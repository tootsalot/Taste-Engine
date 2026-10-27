"""Saved rating-model choices use the shared plain-language accuracy check."""

import re
from threading import Event

import pytest
from PySide6.QtCore import Qt

from taste import recommend, settings
from taste.desktop import for_you, workers
from taste.desktop.for_you import model_note, model_tip
from taste.desktop.widgets import plain_tip
from taste.recommend import Rec
from tests import test_for_you
from tests.test_for_you import refreshed, synced

images = test_for_you.images
window = test_for_you.window


def summary(count=300):
    return {
        "count": count,
        "within_one": 0.61 if count >= 20 else None,
        "top_hits": 0.5 if count >= 20 else None,
        "verdict": "same" if count >= 20 else None,
        "interval": [-0.1, 0.1] if count >= 20 else None,
    }


@pytest.mark.parametrize("kind", ("anime", "film", "tv", "book"))
@pytest.mark.parametrize("name", ("usual", "details", "similar", "blend"))
def test_model_note_uses_shared_plain_accuracy_and_paired_verdict(kind, name):
    metrics = {} if name == "usual" else {"selected_model": {"name": name}}
    text = model_note(kind, metrics, summary())
    assert text.startswith(settings.REC_MODELS[name])
    assert "61% of the time" in text
    assert "it ranked 5 in 10 near the top" in text
    assert ("within half a star" if kind == "book" else "within 1 point") in text
    assert ("About the same as Usual" in text) is (name != "usual")
    assert "MAE" not in text and "off by" not in text


@pytest.mark.parametrize("kind", ("anime", "book"))
@pytest.mark.parametrize("count", (0, 1, 19))
def test_sparse_model_note_reports_the_count_without_accuracy_numbers(kind, count):
    metrics = {"selected_model": {"name": "blend"}}
    text = model_note(kind, metrics, summary(count))
    assert text.startswith("Blend (experimental):")
    assert re.findall(r"\d+(?:\.\d+)?", text) == [str(count)]
    assert "Not enough ratings to check yet" in text
    assert "Usual" not in text


@pytest.mark.parametrize("kind", ("anime", "film", "tv", "book"))
def test_missing_saved_check_requests_a_recompute_without_showing_old_errors(kind):
    metrics = {"selected_model": {"name": "similar", "mae": 1.10, "usual_mae": 1.17}}
    text = model_note(kind, metrics)
    assert "recompute" in text.lower()
    assert "1.10" not in text and "1.17" not in text
    assert "recompute" in model_tip(kind, metrics).lower()


def test_page_uses_cached_accuracy_for_the_saved_model_in_each_rated_medium(window, monkeypatch):
    window.new_profile(("example_user", "Example"))
    conn = window.ctx.connect("example_user")
    try:
        for kind in ("anime", "film", "tv", "book"):
            recommend.save_run(
                conn,
                kind,
                [Rec(kind, "1", f"Example {kind}", score=4.0 if kind == "book" else 8.0)],
                {"selected_model": {"name": "similar", "mae": 2.20, "usual_mae": 2.34}},
            )
            settings.set_value(conn, f"rec_model_{kind}", "blend")
    finally:
        conn.close()
    seen = []

    def cached(conn, kind, name):
        seen.append((kind, name))
        return summary()

    monkeypatch.setattr(for_you.comparison, "saved_accuracy", cached)
    window.for_you.reload()
    assert seen == [(kind, "similar") for kind in ("anime", "film", "tv", "book")]
    for kind in ("anime", "film", "tv", "book"):
        note = window.for_you.lists[kind].note
        assert note.text().startswith("Similar titles (experimental):")
        assert note.textFormat() == Qt.TextFormat.PlainText
        assert "61%" in note.text() and "5 in 10" in note.text()
        assert "About the same as Usual" in note.text()
        assert note.toolTip() == plain_tip(
            model_tip(kind, {"selected_model": {"name": "similar"}}, summary())
        )
    assert "half a star" in window.for_you.lists["book"].note.text()


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


def test_settings_saved_during_a_recompute_reach_the_lists(window, qtbot, monkeypatch):
    synced(window, qtbot)
    refreshed(window, qtbot)
    page = window.for_you
    entered, release = Event(), Event()
    original = workers.runner.refresh_recommendations

    def held(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            release.wait(5)  # the first recompute read its settings already
        return original(*args, **kwargs)

    monkeypatch.setattr(workers.runner, "refresh_recommendations", held)
    finished = []
    page.refresh_finished.connect(finished.append)
    assert page.start_refresh(fetch=False) is not None
    qtbot.waitUntil(entered.is_set)
    window.settings.set_mode("advanced", remember=False)
    combo = window.settings.inputs["rec_model_anime"]
    combo.setCurrentIndex(combo.findData("similar"))
    assert window.settings.save_settings()
    release.set()
    # Before the fix the change waited for the next refresh: the lists kept Usual.
    qtbot.waitUntil(lambda: len(finished) == 2, timeout=15000)
    conn = window.ctx.connect(window.current_profile())
    try:
        _, metrics, _ = recommend.latest(conn, "anime")
    finally:
        conn.close()
    assert metrics["selected_model"]["name"] == "similar"


@pytest.mark.parametrize("kind", ("anime", "film", "tv", "book"))
def test_old_saved_model_blocks_still_open_without_showing_raw_errors(conn, kind):
    old = {"name": "similar", "count": 300, "mae": 1.10, "usual_mae": 1.17}
    recommend.save_run(conn, kind, [], {"model": old})
    _, metrics, _ = recommend.latest(conn, kind)
    assert metrics == {"selected_model": old}
    assert "recompute" in model_note(kind, metrics).lower()


def test_for_you_opens_runs_saved_in_the_31_layout(window):
    # 3.0.0 crashed at launch on these: a chosen model's block under "model".
    window.new_profile(("example_user", "Example"))
    old = {"name": "similar", "count": 300, "mae": 1.10, "usual_mae": 1.17}
    conn = window.ctx.connect("example_user")
    try:
        for kind in ("anime", "film", "tv", "book"):
            score = 4.0 if kind == "book" else 8.0
            recommend.save_run(
                conn, kind, [Rec(kind, "1", f"Example {kind}", score=score)], {"model": old}
            )
    finally:
        conn.close()
    window.for_you.reload()
    for kind in ("anime", "film", "tv", "book"):
        tab = window.for_you.lists[kind]
        window.for_you.tabs.setCurrentWidget(tab)
        assert tab.note.text().startswith("Similar titles (experimental):")
        assert [tile.rec.title for tile in tab.tiles] == [f"Example {kind}"]
        assert tab.detail is not None


def test_old_numeric_anime_metrics_stay_saved_but_the_note_uses_plain_words(conn):
    original = {"held_out": 87, "community": 1.43, "overall": 1.40, "model": 1.35}
    recommend.save_run(conn, "anime", [], original)
    _, metrics, _ = recommend.latest(conn, "anime")
    assert metrics == original
    assert model_note("anime", metrics).startswith("Usual: recompute")
    assert "off by" not in model_note("anime", metrics)


def test_accuracy_tooltip_explains_the_same_training_only_folds():
    text = model_tip("anime", {"selected_model": {"name": "details"}}, summary())
    assert "61%" in text
    assert "same five folds" in text
    assert "without" in text and "score" in text
    assert "off by" not in text
