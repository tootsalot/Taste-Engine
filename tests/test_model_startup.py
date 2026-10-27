"""Startup model choice waits for launch work and belongs to one profile session."""

import time
from threading import Event

import pytest
from PySide6.QtWidgets import QDialog

from taste import profiles, recommend, settings
from taste.desktop import main_window
from taste.desktop.context import AppContext
from taste.desktop.main_window import MainWindow
from taste.desktop.workers import ModelCheckWorker
from taste.recommend import comparison
from taste.secrets_store import SecretStore
from tests.conftest import FakeKeyring


def checks(count=24):
    return {
        "count": count,
        "models": {name: {} for name in ("usual", "details", "similar", "blend")},
    }


class ChoiceDialog(QDialog):
    opened = []

    def __init__(self, checks, selected="usual", parent=None):
        super().__init__(parent)
        self.selected = selected
        self.checks = checks
        self.opened.append(self)

    def choice(self):
        return self.selected if self.result() == QDialog.DialogCode.Accepted else None


@pytest.fixture
def startup(qtbot, monkeypatch):
    monkeypatch.setattr(main_window, "LAUNCH_SYNC_DELAY_MS", 1000000)
    monkeypatch.setattr(main_window, "ModelChoiceDialog", ChoiceDialog)
    monkeypatch.setattr(MainWindow, "make_recaps", lambda self: None)
    monkeypatch.setattr(MainWindow, "offer_past_recaps", lambda self: None)
    ChoiceDialog.opened = []
    calls = []

    def check(conn, cancelled=None, progress=None):
        calls.append(conn.execute("SELECT 1").fetchone()[0])
        if progress:
            progress("Checking each model against your ratings")
        return checks()

    monkeypatch.setattr(comparison, "check_models", check)

    def make(pid="me", create=True):
        if create:
            profiles.create(pid, pid)
        win = MainWindow(AppContext(store=SecretStore(backend=FakeKeyring())))
        qtbot.addWidget(win)
        return win

    return make, calls


def show_after_launch(win, qtbot):
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(lambda: bool(ChoiceDialog.opened))


def asked(pid="me"):
    conn = profiles.open_profile(pid)
    try:
        return comparison.asked_version(conn)
    finally:
        conn.close()


def replace_after_check(worker, release):
    """Put a new database at the checked profile's path before the window hears the result.

    Windows refuses to rename a file that is open, so the check ends and closes its
    connection first. Its finished signal waits in the event queue until after the swap.
    """
    release.set()
    worker.wait()
    path = profiles.db_path("me")
    path.rename(path.with_suffix(".old"))
    profiles.create("me", "New me")


def test_waits_for_first_paint_and_launch_sync_decision(startup, qtbot):
    make, calls = startup
    win = make()
    qtbot.wait(20)
    assert not calls
    win.show()
    qtbot.waitExposed(win)
    qtbot.wait(20)
    assert not calls
    win.sync_on_launch()
    qtbot.waitUntil(lambda: len(ChoiceDialog.opened) == 1)
    assert calls == [1]


def test_waits_until_launch_sync_finishes_and_does_not_offer_twice(startup, qtbot, monkeypatch):
    make, calls = startup
    win = make()
    busy = [True]
    monkeypatch.setattr(win.dashboard, "is_syncing", lambda: busy[0])
    win.sync_on_launch()
    win.show()
    qtbot.wait(30)
    assert not calls
    busy[0] = False
    win.dashboard.sync_finished.emit({})
    qtbot.waitUntil(lambda: len(ChoiceDialog.opened) == 1)
    win.dashboard.sync_finished.emit({})
    qtbot.wait(30)
    assert len(ChoiceDialog.opened) == 1


def test_no_offer_while_launch_sync_is_starting(startup, qtbot, monkeypatch):
    make, calls = startup
    win = make()
    win.show()
    qtbot.waitExposed(win)
    busy = [False]
    monkeypatch.setattr(win.dashboard, "is_syncing", lambda: busy[0])

    def start():
        win._maybe_check_rating_model()
        qtbot.wait(10)
        assert not calls
        busy[0] = True
        win.dashboard.sync_started.emit()
        return True

    monkeypatch.setattr(win, "_start_launch_sync", start)
    assert win.sync_on_launch()
    qtbot.wait(20)
    assert not calls
    busy[0] = False
    win.dashboard.sync_finished.emit({})
    qtbot.waitUntil(lambda: len(ChoiceDialog.opened) == 1)


def test_choice_sets_every_medium_and_recomputes_cached_lists(startup, qtbot, monkeypatch):
    make, _ = startup
    win = make()
    refreshed = []
    monkeypatch.setattr(win.for_you, "start_refresh", lambda fetch: refreshed.append(fetch))
    show_after_launch(win, qtbot)
    assert win.ask_rating_model(answer="similar")
    conn = profiles.open_profile("me")
    try:
        assert {
            settings.get(conn, f"rec_model_{medium}") for medium in ("anime", "book", "film", "tv")
        } == {"similar"}
    finally:
        conn.close()
    assert asked() == recommend.MODEL_SET_VERSION
    assert refreshed == [False]
    assert not ChoiceDialog.opened[0].isVisible()


def test_choosing_current_usual_still_recomputes(startup, qtbot, monkeypatch):
    make, _ = startup
    win = make()
    refreshed = []
    monkeypatch.setattr(win.for_you, "start_refresh", lambda fetch: refreshed.append(fetch))
    show_after_launch(win, qtbot)
    win.ask_rating_model(answer="usual")
    assert refreshed == [False]


@pytest.mark.parametrize("use", [True, False])
def test_bound_dialog_signals_apply_choice_or_decide_later(startup, qtbot, monkeypatch, use):
    make, _ = startup
    win = make()
    monkeypatch.setattr(win.for_you, "start_refresh", lambda fetch: None)
    show_after_launch(win, qtbot)
    dialog = ChoiceDialog.opened[0]
    dialog.selected = "details"
    dialog.accept() if use else dialog.reject()
    assert asked() == (recommend.MODEL_SET_VERSION if use else 0)
    assert win.model_dialog is None


def test_real_dialog_use_button_applies_selected_model(startup, qtbot, monkeypatch):
    from taste.desktop.dialogs import ModelChoiceDialog

    make, _ = startup
    win = make()
    monkeypatch.setattr(main_window, "ModelChoiceDialog", ModelChoiceDialog)
    monkeypatch.setattr(win.for_you, "start_refresh", lambda fetch: None)
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(lambda: win.model_dialog is not None)
    dialog = win.model_dialog
    dialog.choices["similar"].setChecked(True)
    dialog.use_button.click()
    assert asked() == recommend.MODEL_SET_VERSION
    conn = profiles.open_profile("me")
    try:
        assert settings.get(conn, "rec_model_anime") == "similar"
    finally:
        conn.close()


@pytest.mark.parametrize("change", ["switch", "close", "delete"])
def test_profile_change_or_close_dismisses_dialog_and_rejects_old_answer(startup, qtbot, change):
    make, _ = startup
    win = make()
    show_after_launch(win, qtbot)
    dialog = ChoiceDialog.opened[0]
    profiles.create("other", "Other")
    if change == "close":
        win.close()
    elif change == "delete":
        profiles.delete("me", store=win.ctx.store)
        win._after_profile_deleted("me")
    else:
        win.reload_profiles(select="other")
    assert not dialog.isVisible()
    assert not win.ask_rating_model(answer="similar")
    assert asked("other") == 0
    if change == "delete":
        assert not profiles.exists("me")
    else:
        assert asked("me") == 0


@pytest.mark.parametrize("setting", ["film_prior_votes", "clock_format"])
def test_settings_save_cancels_check_before_recompute(startup, qtbot, monkeypatch, setting):
    make, _ = startup
    entered, release = Event(), Event()

    def check(conn, cancelled=None, progress=None):
        entered.set()
        release.wait(3)
        return None

    monkeypatch.setattr(comparison, "check_models", check)
    win = make()
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(entered.is_set)
    worker = win.model_worker
    blocked = []
    monkeypatch.setattr(
        win.for_you, "start_refresh", lambda fetch: blocked.append(win._blocks_recs())
    )
    win._after_settings_saved([setting])
    assert blocked == ([False] if setting == "film_prior_votes" else [])
    assert win.profile_picker.isEnabled()
    assert worker.is_cancelled()
    release.set()
    qtbot.waitUntil(lambda: not worker.isRunning())
    assert not ChoiceDialog.opened


def test_delete_waits_for_a_cancelled_check_to_close_the_database(startup, qtbot, monkeypatch):
    make, _ = startup
    entered = Event()

    def check(conn, cancelled=None, progress=None):
        entered.set()
        while not cancelled():
            time.sleep(0.01)
        time.sleep(0.3)  # the rest of a medium, with the database still open
        return None

    monkeypatch.setattr(comparison, "check_models", check)
    win = make()
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(entered.is_set)
    worker = win.model_worker
    win._after_settings_saved(["clock_format"])
    assert win.model_worker is None and worker.isRunning()
    # Windows refused to delete the file while the check had it open.
    assert win.settings.delete_profile(confirmed=True)
    assert not worker.isRunning()
    assert not profiles.exists("me")


def test_answered_version_is_per_profile_and_version_bump_asks_again(startup, qtbot, monkeypatch):
    make, calls = startup
    win = make()
    monkeypatch.setattr(win.for_you, "start_refresh", lambda fetch: None)
    show_after_launch(win, qtbot)
    win.ask_rating_model(answer="blend")
    win.close()
    again = make(create=False)
    again.sync_on_launch()
    again.show()
    qtbot.wait(30)
    assert len(calls) == 1
    monkeypatch.setattr(recommend, "MODEL_SET_VERSION", recommend.MODEL_SET_VERSION + 1)
    again._maybe_check_rating_model()
    qtbot.waitUntil(lambda: len(ChoiceDialog.opened) == 2)
    assert again.ask_rating_model(answer="later")
    profiles.create("other", "Other")
    again.reload_profiles(select="other")
    qtbot.waitUntil(lambda: len(ChoiceDialog.opened) == 3)
    assert asked("other") == 0


def test_decide_later_waits_for_next_launch(startup, qtbot):
    make, calls = startup
    win = make()
    show_after_launch(win, qtbot)
    assert win.ask_rating_model(answer="later")
    assert asked() == 0
    win.dashboard.sync_finished.emit({})
    win._maybe_check_rating_model()
    qtbot.wait(30)
    assert len(calls) == 1
    win.close()
    again = make(create=False)
    show_after_launch(again, qtbot)
    qtbot.waitUntil(lambda: len(ChoiceDialog.opened) == 2)


def test_small_profile_is_marked_silently(startup, qtbot, monkeypatch):
    make, _ = startup
    monkeypatch.setattr(comparison, "check_models", lambda *a, **kw: checks(0))
    win = make()
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(lambda: asked() == recommend.MODEL_SET_VERSION)
    assert not ChoiceDialog.opened
    assert win.toast.text() != "Checking each model against your ratings"


@pytest.mark.parametrize("change", ["switch", "recreate", "version", "close"])
def test_stale_worker_cannot_offer_or_mark(startup, qtbot, monkeypatch, change):
    make, _ = startup
    entered, release = Event(), Event()
    jobs = []

    def check(conn, cancelled=None, progress=None):
        jobs.append(1)
        first = len(jobs) == 1
        entered.set()
        release.wait(3)
        return checks(0) if first else None

    monkeypatch.setattr(comparison, "check_models", check)
    win = make()
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(entered.is_set)
    worker = win.model_worker
    if change == "switch":
        profiles.create("other", "Other")
        win.reload_profiles(select="other")
        win.reload_profiles(select="me")
    elif change == "recreate":
        replace_after_check(worker, release)
    elif change == "version":
        monkeypatch.setattr(recommend, "MODEL_SET_VERSION", recommend.MODEL_SET_VERSION + 1)
    else:
        win.CLOSE_WAIT_MS = 1
        win.close()
    # Prevent a new valid check from hiding a stale result's incorrect write.
    monkeypatch.setattr(win, "_maybe_check_rating_model", lambda: None)
    release.set()
    qtbot.waitUntil(lambda: not worker.isRunning())
    qtbot.wait(30)
    assert asked() == 0
    assert not ChoiceDialog.opened


def test_worker_keeps_original_path_and_never_recreates_a_deleted_database(
    startup, monkeypatch, tmp_path
):
    make, calls = startup
    make()
    worker = ModelCheckWorker("me")
    original = profiles.db_path("me")
    monkeypatch.setenv("TASTE_DATA_DIR", str(tmp_path / "moved"))
    worker.run()
    assert calls == [1]
    assert not profiles.db_path("me").exists()
    original.unlink()
    calls.clear()
    worker.run()
    assert not calls
    assert not original.exists()


def test_worker_cancellation_avoids_checking(startup):
    make, calls = startup
    make()
    worker = ModelCheckWorker("me")
    worker.cancel()
    worker.run()
    assert calls == []


def test_recreated_profile_gets_its_own_fresh_check(startup, qtbot, monkeypatch):
    make, _ = startup
    entered, release = Event(), Event()
    calls = []

    def check(conn, cancelled=None, progress=None):
        calls.append(1)
        entered.set()
        release.wait(3)
        return checks(0)

    monkeypatch.setattr(comparison, "check_models", check)
    win = make()
    win.sync_on_launch()
    win.show()
    qtbot.waitUntil(entered.is_set)
    replace_after_check(win.model_worker, release)
    qtbot.waitUntil(lambda: asked() == recommend.MODEL_SET_VERSION)
    assert len(calls) == 2


def test_worker_error_does_not_print_private_exception_values(startup, monkeypatch, capsys):
    make, _ = startup
    make()

    def fail(*args, **kwargs):
        raise ValueError("private-made-up-token")

    monkeypatch.setattr(comparison, "check_models", fail)
    worker = ModelCheckWorker("me")
    messages = []
    worker.message.connect(messages.append)
    worker.run()
    assert messages == ["The rating check stopped on an unexpected error (ValueError)."]
    assert "private-made-up-token" not in str(capsys.readouterr())
