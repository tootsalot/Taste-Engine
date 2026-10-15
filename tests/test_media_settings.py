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
    page = window.settings
    assert page.hardcover_connect.isEnabled() and not page.hardcover_disconnect.isEnabled()
    assert page.hardcover_status.text() == "Not connected yet."
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=20000):
        page.connect_hardcover(browser=approving_browser(window.hardcover))
    assert credentials.hardcover_connected("me", window.ctx.store)
    assert window.ctx.store.get("me", "HARDCOVER_REFRESH_TOKEN") == window.hardcover.refresh
    assert page.hardcover_disconnect.isEnabled() and not page.hardcover_connect.isEnabled()
    assert page.sign_in is None and page.hardcover_stop.isHidden()
    # The sync that followed brought the books in: the Dashboard has a books shelf.
    window.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    assert window.dashboard.books in window.dashboard.extra_shown
    window.reports.refresh()
    assert window.reports.books_section.isVisible()
    assert window.reports.books.stats.unique == 5  # read, with or without a date

    worker = page.disconnect_hardcover()
    qtbot.waitUntil(
        lambda: worker.isFinished() and page.hardcover_connect.isEnabled(), timeout=15000
    )
    assert not credentials.hardcover_connected("me", window.ctx.store)
    assert window.hardcover.revoked  # signed out on Hardcover too
    assert page.hardcover_status.text() == "Not connected yet."


def test_a_refused_hardcover_sign_in_says_so(window, qtbot):
    page = window.settings
    worker = page.connect_hardcover(
        browser=approving_browser(window.hardcover, error="access_denied", code="")
    )
    with qtbot.waitSignal(worker.done, timeout=15000):
        pass
    qtbot.waitUntil(lambda: page.sign_in is None)
    assert "didn't approve" in page.hardcover_status.text()
    assert not credentials.hardcover_connected("me", window.ctx.store)


def test_waiting_for_hardcover_can_be_stopped(window, qtbot):
    page = window.settings
    worker = page.connect_hardcover(browser=lambda url: None)  # the browser never comes back
    assert not page.hardcover_stop.isHidden() and not page.hardcover_copy.isHidden()
    page.copy_hardcover_link()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text().startswith(hardcover.AUTHORIZE_URL)
    with qtbot.waitSignal(worker.done, timeout=15000):
        page.stop_hardcover()
    qtbot.waitUntil(lambda: page.sign_in is None)
    assert page.hardcover_status.text() == "Not connected yet."
    assert page.hardcover_connect.isEnabled()


def test_connecting_and_disconnecting_tmdb(window, qtbot):
    page = window.settings
    assert not page.tmdb_connect.isEnabled()  # no read token yet
    page.key_inputs["TMDB_READ_TOKEN"].setText(READ_TOKEN)
    page.save_key("TMDB_READ_TOKEN")
    assert page.tmdb_connect.isEnabled() and page.tmdb_status.text() == "Not connected yet."
    asked = []
    page.connect_tmdb(approve=lambda url: asked.append(url) or True)
    qtbot.waitUntil(lambda: credentials.tmdb_connected("me", window.ctx.store), timeout=15000)
    qtbot.waitUntil(lambda: not page.tmdb_worker.isRunning(), timeout=5000)
    assert asked == ["https://www.themoviedb.org/auth/access?request_token=req-token-1"]
    assert window.ctx.store.get("me", "TMDB_USER_TOKEN") == USER_TOKEN
    assert window.ctx.store.get("me", "TMDB_ACCOUNT_ID") == ACCOUNT
    assert page.tmdb_status.text().startswith("Connected.")
    assert USER_TOKEN not in page.tmdb_status.text()

    worker = page.disconnect_tmdb()
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)
    assert not credentials.tmdb_connected("me", window.ctx.store)
    assert ("DELETE", "/4/auth/access_token") in window.tmdb.calls
    assert page.tmdb_connect.isEnabled()


def test_stopping_at_the_approval_question_connects_nothing(window, qtbot):
    page = window.settings
    page.key_inputs["TMDB_READ_TOKEN"].setText(READ_TOKEN)
    page.save_key("TMDB_READ_TOKEN")
    worker = page.connect_tmdb(approve=lambda url: False)
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)
    qtbot.wait(100)
    assert not credentials.tmdb_connected("me", window.ctx.store)
    assert page.tmdb_status.text() == "Not connected yet."


def test_about_credits_every_source_and_shows_the_tmdb_logo(window):
    page = window.settings
    assert not page.tmdb_logo.pixmap().isNull()
    assert page.tmdb_logo.pixmap().height() <= 30  # never bigger than the app's own logo
    from taste.desktop.settings_page import CREDITS

    for name in ("MyAnimeList", "Last.fm", "Deezer", "Hardcover", "TMDB"):
        assert name in CREDITS
    assert "not endorsed or certified by TMDB" in CREDITS
