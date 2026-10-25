"""Settings' Sources card: one row per source, the same columns in every row. Real widgets."""

import pytest

from taste import credentials, settings
from taste.desktop.context import AppContext
from taste.desktop.main_window import PAGES, MainWindow
from taste.http_client import HttpClient
from taste.secrets_store import SecretStore
from tests.conftest import FakeKeyring, FakeTransport
from tests.fake_hardcover import FakeHardcover
from tests.fake_recs import FakeDeezer, FakeLastfmApi, FakeMal
from tests.fake_tmdb import READ_TOKEN, FakeTmdb
from tests.test_desktop import MAL_KEY, set_up_profile
from tests.test_for_you import NOW, NOW_DT, images, listening_history  # noqa: F401
from tests.test_lastfm_sync import FakeLastfm


@pytest.fixture
def window(qtbot, images):  # noqa: F811
    """Every source faked: MAL, Last.fm (and Deezer), TMDB, and Hardcover."""
    recent = FakeLastfm()
    recent.scrobbles = listening_history()
    fakes = {
        "myanimelist": FakeMal(),
        "deezer": FakeDeezer(),
        "themoviedb": FakeTmdb(),
        "hardcover": FakeHardcover(),
    }
    music = FakeLastfmApi(recent)

    def handler(url, params):
        return next((f for host, f in fakes.items() if host in url), music)(url, params)

    def client_factory(secrets):
        return HttpClient(
            FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    ctx = AppContext(
        store=SecretStore(backend=FakeKeyring()),
        client_factory=client_factory,
        sync_kwargs={"now": lambda: NOW + 1},
        recs_kwargs={"now": NOW_DT},
        image_session=images,
        clock=lambda: NOW_DT,
    )
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    yield win
    ctx.shutdown()


def shown(win, qtbot):
    win.resize(1600, 1000)
    win.show()
    qtbot.waitExposed(win)
    win.sidebar.setCurrentRow(PAGES.index("Settings"))


def test_a_new_profile_shows_every_source_and_what_it_needs(window, qtbot):
    window.new_profile(("me", "Me"))
    shown(window, qtbot)
    card = window.settings.sources
    assert list(card.rows) == ["mal", "lastfm", "tmdb", "hardcover"]
    states = {source: row.state.text() for source, row in card.rows.items()}
    assert states == {
        "mal": "Add your username and client ID",
        "lastfm": "Add your username and API key",
        "tmdb": "Save a read access token, then connect your account",
        "hardcover": "Not connected",
    }
    # Nothing connected: no Test or Sync now yet, and nothing sits disabled. No row has
    # anything in its menu, so no menu button either; Save waits for typing (2.4).
    for row in card.rows.values():
        assert not row.test.isVisible() and row.sync.isHidden() and row.more.isHidden()
        assert row.save is None or row.save.isHidden()
    card.usernames["mal"].setText("example_user")
    assert card.rows["mal"].save.isVisible()
    card.usernames["mal"].setText("")
    assert card.rows["mal"].save.isHidden()
    assert card.rows["hardcover"].connect.isVisible() and card.rows["hardcover"].connect.isEnabled()
    assert card.rows["tmdb"].connect.isHidden()  # needs the token first
    assert card.rows["hardcover"].no_key.isVisible()


def test_saving_a_row_keeps_the_username_and_the_key(window, qtbot):
    window.new_profile(("me", "Me"))
    shown(window, qtbot)
    card = window.settings.sources
    card.usernames["mal"].setText("example_user")
    card.key_inputs["MAL_CLIENT_ID"].setText(MAL_KEY)
    card.save_source("mal")
    conn = window.ctx.connect("me")
    assert settings.get(conn, "mal_username") == "example_user"
    conn.close()
    assert window.ctx.store.get("me", "MAL_CLIENT_ID") == MAL_KEY
    assert card.key_inputs["MAL_CLIENT_ID"].text() == ""  # never left in the window
    assert card.message.text() == "Saved your MyAnimeList username and client ID."
    row = card.rows["mal"]
    assert row.state.text() == "Connected · never synced"
    assert row.test.isVisible() and row.sync.isVisible() and row.remove.text() == "Remove"
    assert row.more.isVisible() and row.test in row.more.menu().actions()
    assert row.save.isHidden()  # everything typed is saved
    # The Dashboard's menu follows at once.
    assert [a.text() for a in window.dashboard.sync_menu.actions()] == ["MyAnimeList"]


def test_every_row_lines_up(window, qtbot):
    set_up_profile(window)
    card = window.settings.sources
    window.ctx.store.set("me", "TMDB_READ_TOKEN", READ_TOKEN)
    window.ctx.store.set("me", "TMDB_USER_TOKEN", "fake-user-token")
    window.ctx.store.set("me", "TMDB_ACCOUNT_ID", "fake-account")
    card.refresh_status()
    shown(window, qtbot)
    qtbot.wait(50)

    def lefts(part):
        return {s: card.mapFromGlobal(getattr(r, part).mapToGlobal(r.state.pos())).x()
                for s, r in card.rows.items() if getattr(r, part) is not None
                and getattr(r, part).isVisible()}  # fmt: skip

    for part in ("sync", "more"):
        found = lefts(part)
        assert len(found) >= 3 and len(set(found.values())) == 1, (part, found)
    widths = {r.sync.width() for r in card.rows.values() if r.sync.isVisible()}
    assert len(widths) == 1
    # Save appearing while typing never moves Sync now (its column keeps the room).
    before = lefts("sync")
    card.usernames["mal"].setText("someone_else")
    qtbot.wait(20)
    assert card.rows["mal"].save.isVisible() and lefts("sync") == before


def test_a_connected_source_never_shows_a_disabled_connect(window, qtbot):
    window.new_profile(("me", "Me"))
    shown(window, qtbot)
    card = window.settings.sources
    card.key_inputs["TMDB_READ_TOKEN"].setText(READ_TOKEN)
    card.save_source("tmdb")
    tmdb = card.rows["tmdb"]
    assert tmdb.state.text() == "Connect your account"
    assert tmdb.connect.isVisible() and tmdb.connect.isEnabled()
    assert tmdb.remove.text() == "Remove"  # the token
    card.connect_tmdb(approve=lambda url: True)
    qtbot.waitUntil(lambda: credentials.tmdb_connected("me", window.ctx.store), timeout=15000)
    qtbot.waitUntil(lambda: not card.tmdb_worker.isRunning(), timeout=5000)
    qtbot.wait(50)
    assert tmdb.connect.isHidden() and tmdb.account.isVisible()
    assert tmdb.remove.text() == "Disconnect" and tmdb.remove.isVisible()
    assert tmdb.state.text().startswith("Connected")


def test_sync_now_runs_that_source_on_the_dashboard(window, qtbot):
    set_up_profile(window)
    card = window.settings.sources
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000) as blocker:
        card.rows["lastfm"].sync.click()
        assert not card.rows["mal"].sync.isEnabled()  # one sync at a time
    assert blocker.args[0] == {"lastfm": True}
    assert card.rows["mal"].sync.isEnabled()
    assert card.rows["lastfm"].state.text().startswith("Connected · synced ")
    assert "plays" in card.rows["lastfm"].state.text()


def test_test_connection_reports_under_the_card(window, qtbot):
    set_up_profile(window)
    card = window.settings.sources
    worker = card.test_connection("mal")
    # Closing the app mid-test waits for it: short jobs go through ctx.track.
    assert worker in window.ctx.threads
    with qtbot.waitSignal(worker.done, timeout=5000):
        pass
    qtbot.waitUntil(lambda: "connected as example_user" in card.message.text())
    assert card.message.text() == "MyAnimeList: connected as example_user."
    assert MAL_KEY not in card.message.text()
