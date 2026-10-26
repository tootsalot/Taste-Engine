"""Calibration is reused only while its training inputs still match."""

import json
from datetime import date

import pytest

from taste import db, recommend
from taste.recommend import candidates, lab, selection


def inputs():
    rows = [(key, float(5 + key % 5), 6.0 + key % 3 / 2) for key in range(1, 26)]
    facts = {
        key: {"genres": ["Mystery"], "studios": ["Example"], "num_episodes": 12}
        for key, _, _ in rows
    }
    return rows, facts, {key: crowd for key, _, crowd in rows}


@pytest.mark.parametrize("medium", ("anime", "film", "tv", "book"))
@pytest.mark.parametrize("name", ("details", "similar", "blend"))
def test_cache_reuses_exact_calibration_and_refits_a_changed_rating(
    conn, monkeypatch, medium, name
):
    rows, facts, crowd = inputs()
    original = candidates.holdout_predictions
    calls = []

    def capture(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(candidates, "holdout_predictions", capture)
    first = selection.fit_selected(conn, medium, name, rows, facts, crowd)
    assert len(calls) == 2
    again = selection.fit_selected(conn, medium, name, rows, facts, crowd)
    assert len(calls) == 2
    assert again.metrics == first.metrics
    assert again.residuals == first.residuals
    assert [again.predict(key) for key in crowd] == [first.predict(key) for key in crowd]
    rows[0] = (1, 1.0, rows[0][2])
    selection.fit_selected(conn, medium, name, rows, facts, crowd)
    assert len(calls) == 4
    assert conn.execute("SELECT COUNT(*) FROM rec_model_calibrations").fetchone()[0] == 1


@pytest.mark.parametrize(
    "change", ("crowd", "genre", "person", "detail", "anime_link", "book_link")
)
def test_cache_tracks_model_metadata_and_links(conn, monkeypatch, change):
    rows, facts, crowd = inputs()
    medium = "book" if change == "book_link" else "anime"
    selection.fit_selected(conn, medium, "blend", rows, facts, crowd)
    if change == "crowd":
        rows[0] = (1, rows[0][1], 4.0)
        crowd[1] = 4.0
    elif change == "genre":
        facts[1]["genres"] = ["Adventure"]
    elif change == "person":
        facts[1]["studios"] = ["Another example"]
    elif change == "detail":
        facts[1]["num_episodes"] = 100
    elif change == "anime_link":
        conn.execute(
            "INSERT INTO stg_mal_anime_recommendations "
            "(mal_anime_id, recommended_id, recommended_title, num_recommendations) "
            "VALUES (1, 2, 'Example', 7)"
        )
    else:
        facts[1]["similar_json"] = "[2]"
    calls = []
    original = candidates.holdout_predictions

    def capture(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(candidates, "holdout_predictions", capture)
    selection.fit_selected(conn, medium, "blend", rows, facts, crowd)
    assert len(calls) == 2


@pytest.mark.parametrize("damage", ("object", "invalid_json", "nan", "huge_integer", "deep_json"))
def test_cache_survives_reopening_and_checks_payload(conn, monkeypatch, damage):
    rows, facts, crowd = inputs()
    first = selection.fit_selected(conn, "anime", "details", rows, facts, crowd)
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    reopened = db.connect(path)
    try:
        with monkeypatch.context() as patch:

            def refuse(*args):
                pytest.fail("Unchanged calibration should come from the profile database")

            patch.setattr(candidates, "holdout_predictions", refuse)
            again = selection.fit_selected(reopened, "anime", "details", rows, facts, crowd)
            assert again.metrics == first.metrics
            assert again.residuals == first.residuals
        payload = {
            "object": "{}",
            "invalid_json": "broken",
            "deep_json": "[" * 20000 + "]" * 20000,
        }.get(damage)
        if payload is None:
            saved = json.loads(
                reopened.execute("SELECT predictions_json FROM rec_model_calibrations").fetchone()[
                    0
                ]
            )
            saved["selected"][0][2] = float("nan") if damage == "nan" else 10**400
            payload = json.dumps(saved)
        reopened.execute("UPDATE rec_model_calibrations SET predictions_json = ?", (payload,))
        rebuilt = selection.fit_selected(reopened, "anime", "details", rows, facts, crowd)
        assert rebuilt.metrics == first.metrics
        assert rebuilt.residuals == first.residuals
    finally:
        reopened.close()


def test_cache_and_uncached_folds_have_identical_predictions(conn):
    rows, facts, crowd = inputs()
    context = candidates.Context(conn, "anime", crowd, date(1970, 1, 1))
    selected = candidates.holdout_predictions(rows, facts, context, candidates.fit_blend)
    usual = candidates.holdout_predictions(rows, facts, context, candidates.fit_usual)
    selection.fit_selected(conn, "anime", "blend", rows, facts, crowd)
    saved = conn.execute("SELECT predictions_json FROM rec_model_calibrations").fetchone()[0]
    payload = json.loads(saved)
    assert payload.pop("summary")["count"] == len(rows)
    assert len(payload.pop("summary_fingerprint")) == 64
    assert payload == {
        "selected": [list(row) for row in selected],
        "usual": [list(row) for row in usual],
    }


@pytest.mark.parametrize("damage", ("legacy", "count", "boolean", "nan", "false_better"))
def test_summary_damage_rebuilds_accuracy_without_refitting_predictions(conn, monkeypatch, damage):
    rows, facts, _ = inputs()
    first = selection.calibration(conn, "anime", "blend", rows, facts)
    saved = json.loads(
        conn.execute("SELECT predictions_json FROM rec_model_calibrations").fetchone()[0]
    )
    if damage == "legacy":
        saved.pop("summary")
        saved.pop("summary_fingerprint")
    elif damage == "count":
        saved["summary"]["count"] = True
    elif damage == "boolean":
        saved["summary"]["within_one"] = True
    elif damage == "nan":
        saved["summary"]["top_hits"] = float("nan")
    else:
        saved["summary"].update(verdict="better", interval=[-1.0, -0.5])
    conn.execute("UPDATE rec_model_calibrations SET predictions_json = ?", (json.dumps(saved),))
    assert selection.saved_calibration(conn, "anime", "blend", rows, facts)["summary"] is None

    def refuse(*args):
        pytest.fail("A summary upgrade must reuse the existing held-out predictions")

    monkeypatch.setattr(candidates, "holdout_predictions", refuse)
    rebuilt = selection.calibration(conn, "anime", "blend", rows, facts)
    assert rebuilt == first


def test_model_set_version_invalidates_calibration(conn, monkeypatch):
    rows, facts, _ = inputs()
    selection.calibration(conn, "anime", "usual", rows, facts)
    monkeypatch.setattr(recommend, "MODEL_SET_VERSION", recommend.MODEL_SET_VERSION + 1)
    assert selection.saved_calibration(conn, "anime", "usual", rows, facts) is None


def test_plain_usual_calibration_is_cached_without_a_candidate_fit(conn, monkeypatch):
    rows, facts, _ = inputs()
    first = selection.calibration(conn, "anime", "usual", rows, facts)
    assert first["selected"] == first["usual"]
    assert first["summary"]["interval"] == [0.0, 0.0]
    assert first["summary"]["verdict"] == "same"

    def refuse(*args):
        pytest.fail("Warm accuracy must not fit folds or repeat the paired bootstrap")

    monkeypatch.setattr(candidates, "holdout_predictions", refuse)
    monkeypatch.setattr(lab, "paired_bootstrap", refuse)
    assert selection.calibration(conn, "anime", "usual", rows, facts) == first
