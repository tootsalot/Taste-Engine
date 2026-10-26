"""Saved model comparisons appear in For You and model settings refresh from cache."""

import re

import pytest
from PySide6.QtCore import Qt

from taste import recommend, settings
from taste.desktop import for_you
from taste.desktop.for_you import accuracy_text, accuracy_tip, model_note
from taste.desktop.widgets import plain_tip
from taste.recommend import Rec
from tests import test_for_you
from tests.test_for_you import refreshed, synced

images = test_for_you.images
window = test_for_you.window


@pytest.mark.parametrize("kind", ("anime", "film", "tv"))
@pytest.mark.parametrize("name", ("details", "similar", "blend"))
def test_model_note_names_the_model_and_both_comparable_errors(kind, name):
    metrics = {
        "selected_model": {
            "name": name,
            "count": 300,
            "mae": 1.10,
            "usual_mae": 1.17,
            "whole_mae": 1.20,
            "usual_whole_mae": 1.30,
        }
    }
    assert model_note(kind, metrics) == (
        f"{settings.REC_MODELS[name]} (experimental): off by "
        f"{1.20 if kind == 'anime' else 1.10:.2f} on average on "
        f"300 held-out ratings, Usual {1.30 if kind == 'anime' else 1.17:.2f}."
    )


def test_book_model_note_converts_doubled_errors_to_explicit_stars():
    metrics = {
        "selected_model": {
            "name": "similar",
            "count": 130,
            "mae": 2.20,
            "usual_mae": 2.34,
            "whole_mae": 2.10,
            "usual_whole_mae": 2.30,
        }
    }
    assert model_note("book", metrics) == (
        "Similar titles (experimental): off by 1.10 stars on average on "
        "130 held-out ratings, Usual 1.17 stars."
    )


@pytest.mark.parametrize("kind", ("anime", "book"))
@pytest.mark.parametrize("count", (0, 1, 19))
def test_sparse_model_note_reports_only_the_count(kind, count):
    metrics = {
        "selected_model": {
            "name": "blend",
            "count": count,
            "mae": None,
            "usual_mae": None,
            "whole_mae": None,
            "usual_whole_mae": None,
        }
    }
    text = model_note(kind, metrics)
    assert text.startswith("Blend (experimental):")
    assert re.findall(r"\d+(?:\.\d+)?", text) == [str(count)]
    assert "not enough ratings" in text
    assert "off by" not in text
    assert "Usual" not in text


def test_usual_notes_keep_the_existing_scalar_metrics_and_wording():
    metrics = {"held_out": 87, "community": 1.43, "overall": 1.40, "model": 1.35}
    assert model_note("anime", metrics) is None
    assert model_note("film", {"rated": 120}) is None
    assert accuracy_text(metrics) == (
        "Accuracy: off by 1.35 points on average on 87 shows it hadn't seen (MAL alone: 1.43)."
    )
    assert "MAL score plus your usual difference by 1.40" in accuracy_tip(metrics)


def test_anime_accuracy_helpers_accept_new_blocks_without_treating_them_as_numbers():
    metrics = {
        "selected_model": {
            "name": "similar",
            "count": 300,
            "mae": 1.10,
            "usual_mae": 1.17,
            "whole_mae": 1.20,
            "usual_whole_mae": 1.30,
        }
    }
    assert accuracy_text(metrics) == model_note("anime", metrics)
    assert "same titles" in accuracy_tip(metrics)
    metrics["selected_model"].update(
        count=19, mae=None, usual_mae=None, whole_mae=None, usual_whole_mae=None
    )
    assert "not enough ratings" in accuracy_text(metrics)
    assert "not enough ratings" in accuracy_tip(metrics)


def test_page_uses_saved_model_notes_for_every_rated_medium(window):
    window.new_profile(("example_user", "Example"))
    conn = window.ctx.connect("example_user")
    try:
        for kind in ("anime", "film", "tv", "book"):
            recommend.save_run(
                conn,
                kind,
                [Rec(kind, "1", f"Example {kind}", score=4.0 if kind == "book" else 8.0)],
                {
                    "selected_model": {
                        "name": "similar",
                        "count": 300,
                        "mae": 2.20,
                        "usual_mae": 2.34,
                        "whole_mae": 2.10,
                        "usual_whole_mae": 2.30,
                    }
                },
            )
    finally:
        conn.close()
    window.for_you.reload()
    for kind in ("anime", "film", "tv", "book"):
        note = window.for_you.lists[kind].note
        assert note.text().startswith("Similar titles (experimental):")
        assert note.textFormat() == Qt.TextFormat.PlainText
        assert "300 held-out ratings" in note.text()
        assert "Usual" in note.text()
        assert "Predicted from your" not in note.text()
    assert "stars" in window.for_you.lists["book"].note.text()
    assert window.for_you.lists["anime"].note.toolTip() == plain_tip(
        accuracy_tip(
            {
                "selected_model": {
                    "name": "similar",
                    "count": 300,
                    "mae": 2.20,
                    "usual_mae": 2.34,
                    "whole_mae": 2.10,
                    "usual_whole_mae": 2.30,
                }
            }
        )
    )


def test_model_setting_recomputes_in_existing_worker_from_cache_and_changes_scores(
    window, qtbot, monkeypatch
):
    synced(window, qtbot)
    refreshed(window, qtbot)
    page = window.for_you
    conn = window.ctx.connect(window.current_profile())
    try:
        usual = {rec.item_key: rec.score for rec in recommend.latest(conn, "anime")[0]}
    finally:
        conn.close()
    usual_note = page.lists["anime"].note.text()
    original = for_you.RecsWorker
    workers = []
    unexpected_clients = []

    def capture(profile_id, store, client_factory, recs_kwargs):
        assert recs_kwargs["fetch"] is False
        worker = original(profile_id, store, client_factory, recs_kwargs)
        workers.append(worker)
        return worker

    def refuse_client(_secrets):
        unexpected_clients.append(True)
        raise AssertionError("A model setting change must recompute from cache without API clients")

    monkeypatch.setattr(for_you, "RecsWorker", capture)
    monkeypatch.setattr(window.ctx, "client_factory", refuse_client)
    window.settings.set_mode("advanced", remember=False)
    combo = window.settings.inputs["rec_model_anime"]
    assert combo.currentData() == "usual"
    combo.setCurrentIndex(combo.findData("similar"))
    with qtbot.waitSignal(page.refresh_finished, timeout=15000):
        assert window.settings.save_settings()
    assert len(workers) == 1
    assert workers[0].isFinished()
    assert not page.fetching
    assert not unexpected_clients
    conn = window.ctx.connect(window.current_profile())
    try:
        recs, metrics, _ = recommend.latest(conn, "anime")
        assert settings.get(conn, "rec_model_anime") == "similar"
    finally:
        conn.close()
    assert {rec.item_key: rec.score for rec in recs} != usual
    assert metrics["selected_model"]["name"] == "similar"
    assert page.lists["anime"].note.text().startswith("Similar titles (experimental):")
    assert any("Predicted by Similar titles, experimental." in rec.facts["details"] for rec in recs)

    combo.setCurrentIndex(combo.findData("usual"))
    with qtbot.waitSignal(page.refresh_finished, timeout=15000):
        assert window.settings.save_settings()
    assert len(workers) == 2
    conn = window.ctx.connect(window.current_profile())
    try:
        recs, metrics, _ = recommend.latest(conn, "anime")
    finally:
        conn.close()
    assert {rec.item_key: rec.score for rec in recs} == usual
    assert "selected_model" not in metrics
    assert page.lists["anime"].note.text() == usual_note


@pytest.mark.parametrize("kind", ("anime", "film", "tv", "book"))
def test_old_saved_model_blocks_are_normalized_once_when_read(conn, kind):
    old = {"name": "similar", "count": 300, "mae": 1.10, "usual_mae": 1.17}
    recommend.save_run(conn, kind, [], {"model": old})
    _, metrics, _ = recommend.latest(conn, kind)
    assert metrics == {"selected_model": old}
    assert model_note(kind, metrics)
    if kind == "anime":
        assert "recompute" in accuracy_text(metrics).lower()
        assert "whole" in accuracy_tip(metrics)


def test_latest_keeps_old_numeric_anime_model_error(conn):
    original = {"held_out": 87, "community": 1.43, "overall": 1.40, "model": 1.35}
    recommend.save_run(conn, "anime", [], original)
    _, metrics, _ = recommend.latest(conn, "anime")
    assert metrics == original
    assert "off by 1.35" in accuracy_text(metrics)


def test_anime_selected_note_and_tooltip_use_whole_score_errors():
    metrics = {
        "selected_model": {
            "name": "details",
            "count": 300,
            "mae": 0.91,
            "usual_mae": 1.03,
            "whole_mae": 1.10,
            "usual_whole_mae": 1.20,
        }
    }
    assert "off by 1.10" in accuracy_text(metrics)
    assert "Usual 1.20" in accuracy_text(metrics)
    assert "0.91" not in accuracy_tip(metrics)
    assert "whole score" in accuracy_tip(metrics)
