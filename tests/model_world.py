"""Build the full invented library through all four fake syncs and enrichment."""

from taste import credentials, runner, settings
from taste.secrets_store import SecretStore
from taste.sources.hardcover_auth import Tokens
from tests import fake_hardcover, fake_tmdb
from tests.conftest import FakeKeyring
from tests.fill_world import NOW, NOW_DT, World


def fill_model_library(conn, tmp_path, monkeypatch):
    """Use the same full fake sync and enrichment as the original pre-change capture."""
    world = World()
    world.install(monkeypatch)
    pid = "example_user"
    store = SecretStore(tmp_path, backend=FakeKeyring())
    settings.set_value(conn, "mal_username", pid)
    settings.set_value(conn, "lastfm_username", pid)
    settings.set_value(conn, "lastfm_capture_scope", "desktop_only")
    store.set(pid, "MAL_CLIENT_ID", "fake-mal-client-id")
    store.set(pid, "LASTFM_API_KEY", "fake-lastfm-key-0123456789")
    store.set(pid, "TMDB_READ_TOKEN", fake_tmdb.READ_TOKEN)
    store.set(pid, "TMDB_USER_TOKEN", fake_tmdb.USER_TOKEN)
    store.set(pid, "TMDB_ACCOUNT_ID", fake_tmdb.ACCOUNT)
    credentials.save_hardcover(
        pid,
        store,
        Tokens(fake_hardcover.ACCESS, fake_hardcover.REFRESH, "2099-01-01 00:00:00"),
    )
    assert runner.sync_sources(
        conn,
        pid,
        "all",
        store=store,
        client_factory=world.client_factory,
        out=lambda _: None,
        now=lambda: NOW,
        page_limit=1000,
    ) == {source: True for source in ("mal", "lastfm", "tmdb", "hardcover")}
    runner.refresh_recommendations(
        conn,
        pid,
        store=store,
        client_factory=world.client_factory,
        out=lambda _: None,
        now=NOW_DT,
    )

    return conn
