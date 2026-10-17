"""Settings for books, films, and TV: connecting Hardcover and TMDB. Real widgets."""

import threading
import urllib.parse
import urllib.request

import pytest

from taste import credentials
from taste.desktop.context import AppContext
from taste.desktop.main_window import PAGES, MainWindow
from taste.http_client import HttpClient
from taste.secrets_store import SecretStore
from taste.sources import hardcover
from tests.conftest import FakeKeyring, FakeResponse, FakeTransport
from tests.fake_hardcover import FakeHardcover
from tests.fake_images import FakeImageSession
from tests.fake_tmdb import ACCOUNT, READ_TOKEN, USER_TOKEN, FakeTmdb


@pytest.fixture
def window(qtbot):
    tmdb, books = FakeTmdb(), FakeHardcover()

    def handler(url, params):
        if "themoviedb" in url:
            return tmdb(url, params)
        if "hardcover" in url:
            return books(url, params)
        return FakeResponse(404, {})

    def client_factory(secrets):
        return HttpClient(
            FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    ctx = AppContext(
        store=SecretStore(backend=FakeKeyring()),
        client_factory=client_factory,
        image_session=FakeImageSession(),
    )
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.new_profile(("me", "Me"))
    win.tmdb = tmdb
    win.hardcover = books
    yield win
    ctx.shutdown()


def approving_browser(fake, **extra):
    """Stands in for the browser: "approves" on Hardcover and comes back to the app."""

    def browser(url):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        code = fake.issue(query["code_challenge"])
        back = {"code": code, "state": query["state"], "iss": hardcover.ISSUER, **extra}
        target = f"{query['redirect_uri']}?{urllib.parse.urlencode(back)}"
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read()).start()

    return browser


def test_connecting_hardcover_signs_in_and_brings_the_books_in(window, qtbot):
    card = window.settings.sources
    row = card.rows["hardcover"]
    assert not row.connect.isHidden() and row.connect.isEnabled() and row.remove.isHidden()
    assert row.state.text() == "Not connected"
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=20000):
        card.connect_hardcover(browser=approving_browser(window.hardcover))
    assert credentials.hardcover_connected("me", window.ctx.store)
    assert window.ctx.store.get("me", "HARDCOVER_REFRESH_TOKEN") == window.hardcover.refresh
    # Connected: Disconnect, Test, and Sync now; no Connect button at all.
    assert row.connect.isHidden() and not row.account.isHidden()
    assert row.remove.text() == "Disconnect" and not row.remove.isHidden()
    assert not row.test.isHidden() and not row.sync.isHidden()
    assert card.sign_in is None and row.stop.isHidden()
    assert row.state.text().startswith("Connected · synced ")
    assert row.state.text().endswith("books in your library")
    # The sync that followed brought the books in: the Dashboard has a books shelf.
    window.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    assert window.dashboard.books in window.dashboard.extra_shown
    window.sidebar.setCurrentRow(PAGES.index("Books"))
    assert window.reports.books_section.isVisible()
    assert window.reports.books.stats.unique == 5  # read, with or without a date

    worker = card.disconnect_hardcover()
    qtbot.waitUntil(lambda: worker.isFinished() and row.connect.isEnabled(), timeout=15000)
    assert not credentials.hardcover_connected("me", window.ctx.store)
    assert window.hardcover.revoked  # signed out on Hardcover too
    assert row.state.text().startswith("Not connected · last synced ")
    assert not row.connect.isHidden() and row.remove.isHidden()


def test_settings_shows_the_latest_sync_once_a_sync_ends(window, qtbot):
    card = window.settings.sources
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=20000):
        card.connect_hardcover(browser=approving_browser(window.hardcover))
    conn = window.ctx.connect("me")
    with conn:
        conn.execute(
            "UPDATE sync_runs SET ended_at = '2026-01-01 12:00:00' WHERE source = 'hardcover'"
        )
    conn.close()
    window.settings.refresh()
    assert "synced Jan 1" in card.rows["hardcover"].state.text()
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=20000):
        window.dashboard.start_sync("hardcover")
    # Settings isn't on screen, but it still says when the books last synced.
    assert "synced today at" in card.rows["hardcover"].state.text()


def test_a_refused_hardcover_sign_in_says_so(window, qtbot):
    card = window.settings.sources
    worker = card.connect_hardcover(
        browser=approving_browser(window.hardcover, error="access_denied", code="")
    )
    with qtbot.waitSignal(worker.done, timeout=15000):
        pass
    qtbot.waitUntil(lambda: card.sign_in is None)
    assert "didn't approve" in card.message.text()
    assert not credentials.hardcover_connected("me", window.ctx.store)


def test_waiting_for_hardcover_can_be_stopped(window, qtbot):
    card = window.settings.sources
    row = card.rows["hardcover"]
    worker = card.connect_hardcover(browser=lambda url: None)  # the browser never comes back
    assert not row.stop.isHidden() and not row.copy.isHidden() and row.connect.isHidden()
    card.copy_hardcover_link()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text().startswith(hardcover.AUTHORIZE_URL)
    with qtbot.waitSignal(worker.done, timeout=15000):
        card.stop_hardcover()
    qtbot.waitUntil(lambda: card.sign_in is None)
    assert row.state.text() == "Not connected"
    assert not row.connect.isHidden() and row.connect.isEnabled() and row.stop.isHidden()


def test_connecting_and_disconnecting_tmdb(window, qtbot):
    card = window.settings.sources
    row = card.rows["tmdb"]
    assert row.connect.isHidden()  # no read token yet
    assert row.state.text() == "Save a read access token, then connect your account"
    row.key.setText(READ_TOKEN)
    card.save_source("tmdb")
    assert not row.connect.isHidden() and row.connect.isEnabled()
    assert row.state.text() == "Connect your account"
    asked = []
    card.connect_tmdb(approve=lambda url: asked.append(url) or True)
    qtbot.waitUntil(lambda: credentials.tmdb_connected("me", window.ctx.store), timeout=15000)
    qtbot.waitUntil(lambda: not card.tmdb_worker.isRunning(), timeout=5000)
    assert asked == ["https://www.themoviedb.org/auth/access?request_token=req-token-1"]
    assert window.ctx.store.get("me", "TMDB_USER_TOKEN") == USER_TOKEN
    assert window.ctx.store.get("me", "TMDB_ACCOUNT_ID") == ACCOUNT
    assert row.state.text() == "Connected · never synced"
    assert USER_TOKEN not in row.state.text() + card.message.text()
    assert [a.text() for a in window.dashboard.sync_menu.actions()] == ["TMDB"]

    worker = card.disconnect_tmdb()
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)
    assert not credentials.tmdb_connected("me", window.ctx.store)
    assert ("DELETE", "/4/auth/access_token") in window.tmdb.calls
    assert not row.connect.isHidden() and row.connect.isEnabled()
    assert row.remove.text() == "Remove"  # the read token is still saved


def test_stopping_at_the_approval_question_connects_nothing(window, qtbot):
    card = window.settings.sources
    card.rows["tmdb"].key.setText(READ_TOKEN)
    card.save_source("tmdb")
    worker = card.connect_tmdb(approve=lambda url: False)
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)
    qtbot.wait(100)
    assert not credentials.tmdb_connected("me", window.ctx.store)
    assert card.rows["tmdb"].state.text() == "Connect your account"


def test_about_credits_every_source_and_shows_the_tmdb_logo(window):
    page = window.settings
    assert not page.tmdb_logo.pixmap().isNull()
    assert page.tmdb_logo.pixmap().height() <= 30  # never bigger than the app's own logo
    from taste.desktop.settings_page import CREDITS

    for name in ("MyAnimeList", "Last.fm", "Deezer", "Hardcover", "TMDB"):
        assert name in CREDITS
    assert "not endorsed or certified by TMDB" in CREDITS
