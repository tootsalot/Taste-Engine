"""Desktop app tests: real widgets, offscreen Qt, fake HTTP, temp data folder."""

import csv

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QTableView

from taste import profiles, settings
from taste.desktop import listen_for_others, notify_running_instance
from taste.desktop.context import AppContext
from taste.desktop.dialogs import ConfirmDeleteDialog, NewProfileDialog
from taste.desktop.main_window import PAGES, MainWindow
from taste.http_client import HttpClient
from taste.secrets_store import SecretStore
from tests.conftest import FakeKeyring, FakeResponse, FakeTransport, load_fixture
from tests.test_lastfm_sync import FakeLastfm

MAL_KEY = "fake-mal-client-id-desktop"
LASTFM_KEY = "fake-lastfm-key-desktop-0123"


def api_handler():
    lastfm = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")

    def handler(url, params):
        if "myanimelist" in url and "/anime/" in url:
            return FakeResponse(404, {"error": "not_found"})  # show details: none in these tests
        if str(params.get("method", "")).startswith("artist."):
            empty = {"similarartists": {"artist": []}, "toptags": {"tag": []}}
            return FakeResponse(200, {**empty, "topalbums": {"album": []}})
        if "myanimelist" in url:
            name = "mal_animelist_page2.json" if "offset=3" in url else "mal_animelist_page1.json"
            return FakeResponse(200, load_fixture(name))
        if params.get("method") == "user.getinfo":
            if params.get("api_key") != LASTFM_KEY:
                return FakeResponse(403, {"error": 10, "message": "Invalid API key"})
            return FakeResponse(200, {"user": {"name": params["user"]}})
        return lastfm(url, params)

    return handler


@pytest.fixture
def window(qtbot):
    handler = api_handler()

    def client_factory(secrets):
        return HttpClient(
            FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    ctx = AppContext(
        store=SecretStore(backend=FakeKeyring()),
        client_factory=client_factory,
        sync_kwargs={"now": lambda: 1791500000},
    )
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    return win


def visible_text(win) -> str:
    """Everything the window shows as text, to check keys never appear."""
    parts = [w.text() for w in win.findChildren(QLabel)]
    parts += [w.text() for w in win.findChildren(QLineEdit)]
    parts += [w.toPlainText() for w in win.findChildren(QPlainTextEdit)]
    return "\n".join(parts)


def set_up_profile(win, pid="me"):
    assert win.new_profile((pid, "Me"))
    page = win.settings
    page.key_inputs["MAL_CLIENT_ID"].setText(MAL_KEY)
    page.save_key("MAL_CLIENT_ID")
    page.key_inputs["LASTFM_API_KEY"].setText(LASTFM_KEY)
    page.save_key("LASTFM_API_KEY")
    page.inputs["mal_username"].setText("example_user")
    page.inputs["lastfm_username"].setText("example_user")
    page.inputs["lastfm_capture_scope"].setCurrentIndex(
        page.inputs["lastfm_capture_scope"].findData("desktop_only")
    )
    page.inputs["genre_min_sample"].setValue(1)
    assert page.save_settings()
    return pid


def test_starts_with_welcome_when_no_profiles(window):
    assert window.stack.currentWidget() is window.empty
    assert not window.sidebar.isEnabled()


def test_create_profile_opens_settings(window):
    assert window.new_profile(("me", "Me"))
    assert profiles.exists("me")
    assert window.current_profile() == "me"
    assert window.profile_picker.currentText() == "Me"
    assert window.stack.currentWidget() is window.settings
    assert window.sidebar.currentRow() == PAGES.index("Settings")


def test_status_messages_fade_and_the_version_stays(window, qtbot, monkeypatch):
    monkeypatch.setattr("taste.desktop.main_window.STATUS_MS", 50)
    window.new_profile(("me", "Me"))
    bar = window.statusBar()
    assert bar.currentMessage().startswith("Created me.")
    qtbot.waitUntil(lambda: bar.currentMessage() == "", timeout=3000)
    assert "Your data stays on this computer" in window.version_label.text()


def test_sidebar_collapses_to_icons_and_remembers(window, qtbot):
    window.new_profile(("me", "Me"))
    nav = window.sidebar
    assert not window.nav_collapsed
    assert [nav.item(i).text() for i in range(nav.count())] == list(PAGES)
    assert all(not nav.item(i).icon().isNull() for i in range(nav.count()))

    qtbot.mouseClick(window.nav_toggle, Qt.MouseButton.LeftButton)
    assert window.nav_collapsed
    assert window.nav.maximumWidth() < 80
    assert [nav.item(i).text() for i in range(nav.count())] == [""] * len(PAGES)
    assert [nav.item(i).toolTip() for i in range(nav.count())] == list(PAGES)
    nav.setCurrentRow(PAGES.index("Reports"))  # still navigates
    assert window.stack.currentWidget() is window.reports

    again = MainWindow(window.ctx)  # the next launch opens the way it was left
    qtbot.addWidget(again)
    assert again.nav_collapsed
    qtbot.mouseClick(again.nav_toggle, Qt.MouseButton.LeftButton)
    assert not again.nav_collapsed and nav.count() == len(PAGES)


def test_bad_profile_id_is_refused(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "taste.desktop.main_window.QMessageBox.warning", lambda *a: warnings.append(a[2])
    )
    assert not window.new_profile(("../evil", ""))
    assert warnings and "Profile IDs use lowercase" in warnings[0]
    assert window.current_profile() is None


def test_new_profile_dialog_only_accepts_safe_ids(qtbot):
    dialog = NewProfileDialog()
    qtbot.addWidget(dialog)
    qtbot.keyClicks(dialog.profile_id, "Me/../x")
    assert dialog.profile_id.text() == "ex"  # validator drops "M", "/" and "."


def test_settings_save_and_validation(window):
    window.new_profile(("me", ""))
    page = window.settings
    page.inputs["timezone"].setCurrentText("Mars/Olympus")
    page.inputs["display_name"].setText("Me Again")
    assert not page.save_settings()
    assert "Unknown time zone" in page.settings_message.text()
    conn = window.ctx.connect("me")
    assert settings.get(conn, "display_name") == "Me Again"  # valid fields still saved
    assert settings.get(conn, "timezone") == "America/Phoenix"
    conn.close()
    assert window.profile_picker.currentText() == "Me Again"


def test_keys_saved_cleared_and_never_shown(window):
    set_up_profile(window)
    ring = window.ctx.store.backend
    assert ring.saved[("taste-engine", "me:LASTFM_API_KEY")] == LASTFM_KEY
    assert window.settings.key_inputs["LASTFM_API_KEY"].text() == ""
    assert (
        "Saved in Windows Credential Manager" in window.settings.key_status["MAL_CLIENT_ID"].text()
    )
    for row in range(len(PAGES)):
        window.sidebar.setCurrentRow(row)
        text = visible_text(window)
        assert LASTFM_KEY not in text and MAL_KEY not in text
    window.settings.remove_key("LASTFM_API_KEY")
    assert ("taste-engine", "me:LASTFM_API_KEY") not in ring.saved
    assert window.settings.key_status["LASTFM_API_KEY"].text() == "Not set"


def test_connection_test_runs_in_background(window, qtbot):
    set_up_profile(window)
    worker = window.settings.test_connection("lastfm")
    with qtbot.waitSignal(worker.done, timeout=5000):
        pass
    qtbot.waitUntil(lambda: "connected as example_user" in window.settings.key_message.text())

    window.settings.key_inputs["LASTFM_API_KEY"].setText("wrong-key")
    window.settings.save_key("LASTFM_API_KEY")
    worker = window.settings.test_connection("lastfm")
    with qtbot.waitSignal(worker.done, timeout=5000):
        pass
    qtbot.waitUntil(lambda: "Invalid API key" in window.settings.key_message.text())
    assert "wrong-key" not in visible_text(window)


def test_full_sync_updates_dashboard_and_reports(window, qtbot):
    set_up_profile(window)
    window.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    dash = window.dashboard
    with qtbot.waitSignal(dash.sync_finished, timeout=15000) as blocker:
        dash.start_sync("all")
        assert not dash.sync_buttons["all"].isEnabled()
    assert blocker.args[0] == {"mal": True, "lastfm": True}
    assert dash.sync_buttons["all"].isEnabled()
    assert "Last.fm: page 1 of 1" in dash.log.toPlainText()
    assert dash.counts.text() == "5 on your list · 3 scored · 5 Last.fm plays"
    assert dash.critic_value.text() == "-1.64"

    reports_page = window.reports
    # Charts only: the raw numbers live in the CSV exports, not in tables on screen.
    assert reports_page.findChildren(QTableView) == []
    assert [t.value.text() for t in reports_page.tiles] == [
        "3",
        "-1.64",
        "5",
        reports_page.tiles[3].value.text(),
    ]
    assert sorted(reports_page.scatter.points) == [(6.52, 4.0), (7.4, 5.0), (8.0, 8.0)]
    assert "Desktop listening only" in reports_page.scope.text()
    assert sum(reports_page.hour_chart.values) == 5
    assert reports_page.hour_card.note.text() == "In your time zone, America/Phoenix."
    assert reports_page.top_artists.rows()[0] == ("the example band", "3")
    menu = [a.text() for a in reports_page.export_menu.actions() if not a.isSeparator()]
    assert menu[0] == "All reports…" and len(menu) == 1 + 12


def test_export_one_and_all(window, qtbot, tmp_path):
    set_up_profile(window)
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000):
        window.dashboard.start_sync("all")
    page = window.reports
    path = page.export_report("rpt_lastfm_by_hour", str(tmp_path / "hour.csv"))
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][:3] == ["local_hour", "plays", "pct_of_plays"]
    assert len(rows) == 25
    folder = page.export_all(str(tmp_path))
    assert len(list(folder.glob("*.csv"))) == 12


def test_delete_profile_needs_confirmation(window, qtbot):
    set_up_profile(window)
    dialog = ConfirmDeleteDialog("me")
    qtbot.addWidget(dialog)
    ok = dialog.buttons.buttons()[0]
    qtbot.keyClicks(dialog.confirm, "m")
    assert not ok.isEnabled()
    qtbot.keyClicks(dialog.confirm, "e")
    assert ok.isEnabled()

    assert not window.settings.delete_profile(confirmed=False)
    assert profiles.exists("me")
    assert window.settings.delete_profile(confirmed=True)
    assert not profiles.exists("me")
    assert window.ctx.store.backend.saved == {}
    assert window.stack.currentWidget() is window.empty


def test_delete_blocked_while_syncing(window):
    set_up_profile(window)
    window.settings.is_busy = lambda: True
    assert not window.settings.delete_profile(confirmed=True)
    assert profiles.exists("me")


def test_second_launch_brings_first_to_front(qtbot):
    shown = []
    server = listen_for_others("taste-engine-test-instance", lambda: shown.append(True))
    try:
        assert notify_running_instance("taste-engine-test-instance")
        qtbot.waitUntil(lambda: shown == [True], timeout=3000)
        assert not notify_running_instance("taste-engine-nobody-listening")
    finally:
        server.close()


def test_labels_never_render_html(window):
    from PySide6.QtCore import Qt

    window.new_profile(("me", "<b>bold</b> <img src=x>"))
    assert window.dashboard.title.textFormat() == Qt.TextFormat.PlainText
    assert window.dashboard.title.text() == "<b>bold</b> <img src=x>"
    assert window.reports.scope.textFormat() == Qt.TextFormat.PlainText


def test_smoke_test_report(qapp, tmp_path):
    from taste.desktop import smoke_test

    path = tmp_path / "smoke.txt"
    assert smoke_test(qapp, str(path)) == 0
    report = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())
    assert report["pages"] == "5"
    assert report["webp"] == "True" and report["jpeg"] == "True"
    assert report["gif"] == "True"  # some Last.fm covers are GIFs


def test_about_credits_and_notices(window, monkeypatch, tmp_path):
    from taste.desktop import settings_page

    window.new_profile(("me", "Me"))
    page = window.settings
    text = visible_text(window)
    assert "MIT License" in text and "LGPL 3.0" in text and "Space Grotesk" in text
    assert not page.notices_button.isEnabled()  # from source there's no notices file

    (tmp_path / "THIRD_PARTY_NOTICES.txt").write_text("notices", encoding="utf-8")
    monkeypatch.setattr(settings_page.sys, "frozen", True, raising=False)
    monkeypatch.setattr(settings_page.sys, "executable", str(tmp_path / "taste-engine.exe"))
    opened = []
    monkeypatch.setattr(
        "taste.desktop.settings_page.QDesktopServices.openUrl", lambda url: opened.append(url)
    )
    assert page.open_notices()
    assert opened[0].toLocalFile().endswith("THIRD_PARTY_NOTICES.txt")
