"""Selected rating models use their own predictions and held-out calibration."""

import math
from datetime import date
from statistics import mean

import pytest

from taste import recommend, settings, themes
from taste.recommend import candidates, experiments, lab, selection
from taste.recommend.everything import everything_recommendations
from taste.recommend.model import chance_at_least, holdout_residuals
from tests.fill_world import NOW_DT
from tests.model_world import fill_model_library


@pytest.fixture
def full_library(conn, tmp_path, monkeypatch):
    return fill_model_library(conn, tmp_path, monkeypatch)


def list_result(conn, medium):
    function, now = {
        "anime": (recommend.anime_recommendations, NOW_DT),
        "film": (recommend.film_recommendations, NOW_DT.date()),
        "tv": (recommend.tv_recommendations, NOW_DT.date()),
        "book": (recommend.book_recommendations, NOW_DT),
    }[medium]
    return function(conn, now)


@pytest.mark.parametrize("medium", lab.MEDIA)
@pytest.mark.parametrize("name", ("details", "similar", "blend"))
def test_selected_scores_chances_and_errors_match_the_lab(full_library, medium, name, monkeypatch):
    conn = full_library
    before, _ = list_result(conn, medium)
    before_by_key = {rec.item_key: rec for rec in before}
    rows, facts, context = lab.load_data(conn, medium)
    candidate = next(c for c in lab.DEFAULT_CANDIDATES if c.name == name)
    predict = candidate.fit(rows, facts, context)
    held_out = lab.holdout_predictions(rows, facts, context, candidate)
    residuals = sorted(mine - guess for _, mine, guess in held_out)
    usual = holdout_residuals(rows, facts)
    expected_check = {
        "name": name,
        "count": len(rows),
        "mae": round(mean(abs(value) for value in residuals), 3),
        "usual_mae": round(mean(abs(value) for value in usual), 3),
    }
    settings.set_value(conn, f"rec_model_{medium}", name)
    recs, metrics = list_result(conn, medium)
    assert metrics["model"] == expected_check
    assert "experiments" not in metrics
    assert "theme_transfer" not in metrics
    assert recs
    if (medium, name) == ("tv", "blend"):
        # This full-data fit chooses Usual, while outer folds may choose other weights.
        assert predict.weights == (1, 0, 0)
        assert [(rec.item_key, rec.score) for rec in recs] == [
            (rec.item_key, rec.score) for rec in before
        ]
        assert residuals != sorted(usual)
    else:
        assert [(rec.item_key, rec.score) for rec in recs] != [
            (rec.item_key, rec.score) for rec in before
        ]
    divisor = 2 if medium == "book" else 1
    chance_key = "chance_4_plus" if medium == "book" else "chance_8_plus"
    for rec in recs:
        guess = predict(int(rec.item_key))
        assert rec.score == round(guess / divisor, 2)
        assert rec.facts[chance_key] == round(chance_at_least(guess, residuals, 8), 3)
        assert rec.facts["details"][-1] == (
            f"Predicted by {settings.REC_MODELS[name]}, experimental."
        )
        if rec.item_key in before_by_key:
            assert rec.reasons == before_by_key[rec.item_key].reasons
    support_weight = 0.08 if medium == "book" else 0.15
    assert recs == sorted(
        recs, key=lambda rec: (-(rec.score + support_weight * math.log1p(rec.support)), rec.title)
    )

    # The 3.0 switches are inactive for each alternate model, including its notes.
    for key in ("rec_dismissal_signal", "rec_recent_ratings", "rec_theme_transfer"):
        settings.set_value(conn, key, True)

    def refuse(*args, **kwargs):
        pytest.fail("Selected models must not apply or evaluate Usual's optional signals")

    for function in ("apply", "residuals", "metrics", "transfer_metrics"):
        monkeypatch.setattr(experiments, function, refuse)
    monkeypatch.setattr(themes, "profile", refuse)
    monkeypatch.setattr(themes, "transfer", refuse)
    assert list_result(conn, medium) == (recs, metrics)


@pytest.mark.parametrize("medium", lab.MEDIA)
def test_small_selected_medium_keeps_its_count_and_suppresses_errors(conn, medium):
    rows = [(key, 7.0, 7.0) for key in range(1, 20)]
    facts = {key: {} for key, _, _ in rows}
    chosen = selection.fit_selected(conn, medium, "details", rows, facts, dict.fromkeys(facts, 7.0))
    assert chosen.metrics == {"name": "details", "count": 19, "mae": None, "usual_mae": None}
    assert chosen.residuals == []
    assert chosen.predict(1) == 7.0


def test_lab_and_selected_models_share_the_fold_implementation(conn, monkeypatch):
    context = candidates.Context(conn, "anime", {}, date(1970, 1, 1))
    sentinel = [(1, 8.0, 7.0)]
    seen = []

    def capture(rows, facts, candidate_context, fit):
        seen.append((rows, facts, candidate_context, fit))
        return sentinel

    monkeypatch.setattr(candidates, "holdout_predictions", capture)
    candidate = lab.DEFAULT_CANDIDATES[0]
    assert lab.holdout_predictions([], {}, context, candidate) is sentinel
    assert seen == [([], {}, context, candidate.fit)]


def test_mixed_list_reranks_saved_selected_picks_without_refitting(full_library, monkeypatch):
    settings.set_value(full_library, "rec_model_film", "details")
    recommend.compute_all(full_library, NOW_DT, ("film",))
    saved = {kind: recommend.latest(full_library, kind)[:2] for kind in recommend.LIST_KINDS}

    def refuse(*args, **kwargs):
        pytest.fail("Across everything must use the saved lists")

    monkeypatch.setattr(selection, "fit_selected", refuse)
    recs, metrics = everything_recommendations(full_library, saved)
    film_check = saved["film"][1]["model"]
    assert metrics["holdouts"]["film"] == {
        "count": film_check["count"],
        "mae": film_check["usual_mae"],
    }
    film_picks = {rec.item_key: rec for rec in saved["film"][0]}
    for rec in recs:
        if rec.kind == "film":
            original = film_picks[rec.facts["source_item_key"]]
            assert rec.score == original.score
            assert rec.facts["details"] == original.facts["details"]
