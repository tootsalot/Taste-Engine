"""Each source's state for the Settings rows and the Dashboard's status line. Made up."""

from datetime import datetime, timezone

from taste import settings, source_status
from taste.config import MalSettings
from taste.secrets_store import SecretStore
from taste.sources import mal
from tests.conftest import FAKE_MAL_CLIENT_ID, FakeKeyring
from tests.fake_recs import FakeMal, anime, list_entry

# 11:30 PM on Oct 15 in Phoenix, the default time zone.
NOW = datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc)
TODAY_1122 = "2026-10-16 06:22:00"  # 11:22 PM Phoenix
TODAY_1115 = "2026-10-16 06:15:00"  # 11:15 PM
YESTERDAY_905 = "2026-10-15 04:05:00"  # 9:05 PM the day before


def store_with(**keys) -> SecretStore:
    store = SecretStore(backend=FakeKeyring())
    for name, value in keys.items():
        store.set("me", name, value)
    return store


def synced(conn, source, ended_at, mode="full", status="success"):
    conn.execute(
        "INSERT INTO sync_runs (source, mode, started_at, ended_at, status) VALUES (?, ?, ?, ?, ?)",
        (source, mode, ended_at, ended_at, status),
    )


def everything_connected(conn) -> SecretStore:
    settings.set_value(conn, "mal_username", "example_user")
    settings.set_value(conn, "lastfm_username", "example_user")
    return store_with(
        MAL_CLIENT_ID="fake-mal",
        LASTFM_API_KEY="fake-lastfm",
        TMDB_READ_TOKEN="fake-read",
        TMDB_USER_TOKEN="fake-user",
        TMDB_ACCOUNT_ID="fake-account",
        HARDCOVER_ACCESS_TOKEN="fake-access",
        HARDCOVER_REFRESH_TOKEN="fake-refresh",
    )


def by_source(conn, store):
    return {s.source: s for s in source_status.load(conn, "me", store)}


def test_a_new_profile_has_nothing_connected(conn):
    found = by_source(conn, store_with())
    assert list(found) == ["mal", "lastfm", "tmdb", "hardcover"]
    assert not any(s.connected for s in found.values())
    assert found["mal"].needs == "Add your username and client ID"
    assert found["lastfm"].needs == "Add your username and API key"
    assert found["tmdb"].needs == "Save a read access token, then connect your account"
    assert found["hardcover"].needs == "Not connected"
    assert source_status.state_line(found["mal"], "America/Phoenix", NOW) == (
        "Add your username and client ID"
    )
    line = source_status.synced_line(list(found.values()), "America/Phoenix", NOW)
    assert line.text == "Nothing synced yet. Add your sources in Settings."


def test_half_set_up_sources_say_what_is_missing(conn):
    settings.set_value(conn, "mal_username", "example_user")
    found = by_source(conn, store_with(LASTFM_API_KEY="fake", TMDB_READ_TOKEN="fake"))
    assert found["mal"].needs == "Add your client ID"
    assert found["lastfm"].needs == "Add your username"
    assert found["tmdb"].needs == "Connect your account"
    assert not found["tmdb"].connected


def test_connected_sources_show_their_last_sync_and_what_they_hold(conn, make_client):
    store = everything_connected(conn)
    shows = [anime(1, "Example One", 7.0, ["Drama"], ["Studio Alpha"]),
             anime(2, "Example Two", 7.5, ["Comedy"], ["Studio Alpha"])]  # fmt: skip
    fake = FakeMal(entries=[list_entry(shows[0], "completed", 8), list_entry(shows[1], "watching")])
    client, _ = make_client(fake)
    mal.sync(conn, client, MalSettings(FAKE_MAL_CLIENT_ID, "example_user"), out=lambda m: None)
    conn.execute("UPDATE sync_runs SET ended_at = ? WHERE source = 'mal'", (TODAY_1122,))
    synced(conn, "mal", "2026-10-16 06:29:00", mode="enrich")  # suggestions, not a sync
    found = by_source(conn, store)
    assert all(s.connected for s in found.values())
    row = found["mal"]
    assert row.last_sync == TODAY_1122
    assert row.items == "2 anime on your list"
    assert source_status.state_line(row, "America/Phoenix", NOW) == (
        "Connected · synced today at 11:22 PM · 2 anime on your list"
    )
    assert source_status.state_line(found["lastfm"], "America/Phoenix", NOW) == (
        "Connected · never synced"
    )
    assert found["lastfm"].items == "0 plays"


def test_syncs_ending_together_are_said_once(conn):
    store = everything_connected(conn)
    for source, when in (("mal", TODAY_1115), ("lastfm", TODAY_1122), ("tmdb", TODAY_1122),
                         ("hardcover", TODAY_1122)):  # fmt: skip
        synced(conn, source, when)
    line = source_status.synced_line(source_status.load(conn, "me", store), "America/Phoenix", NOW)
    assert line.text == "Everything synced today at 11:22 PM."
    assert line.latest == TODAY_1122
    # Hovering says when each one finished.
    assert line.tip.splitlines() == [
        "MyAnimeList synced today at 11:15 PM",
        "Last.fm synced today at 11:22 PM",
        "TMDB synced today at 11:22 PM",
        "Hardcover synced today at 11:22 PM",
    ]


def test_syncs_far_apart_are_grouped_by_when(conn):
    store = everything_connected(conn)
    for source, when in (("mal", TODAY_1122), ("lastfm", TODAY_1115), ("tmdb", TODAY_1122),
                         ("hardcover", YESTERDAY_905)):  # fmt: skip
        synced(conn, source, when)
    line = source_status.synced_line(source_status.load(conn, "me", store), "America/Phoenix", NOW)
    assert line.text == (
        "MyAnimeList, Last.fm, and TMDB synced today at 11:22 PM. "
        "Hardcover synced yesterday at 9:05 PM."
    )


def test_a_lone_source_or_one_never_synced_is_named(conn):
    settings.set_value(conn, "mal_username", "example_user")
    store = store_with(MAL_CLIENT_ID="fake-mal")
    synced(conn, "mal", TODAY_1122)
    statuses = source_status.load(conn, "me", store)
    line = source_status.synced_line(statuses, "America/Phoenix", NOW)
    assert line.text == "MyAnimeList synced today at 11:22 PM."
    settings.set_value(conn, "lastfm_username", "example_user")
    store.set("me", "LASTFM_API_KEY", "fake-lastfm")
    line = source_status.synced_line(source_status.load(conn, "me", store), "America/Phoenix", NOW)
    assert line.text == "MyAnimeList synced today at 11:22 PM. Last.fm: never synced."
    assert [s.source for s in source_status.load(conn, "me", store) if s.connected] == [
        "mal",
        "lastfm",
    ]


class LockedKeyring(FakeKeyring):
    """A credential store that refuses every read (a locked session, a policy)."""

    def get_password(self, service, entry):
        raise OSError("locked")


def test_a_credential_store_that_fails_reads_as_not_connected(conn):
    store = SecretStore(backend=LockedKeyring())
    found = {s.source: s for s in source_status.load(conn, "me", store)}
    assert not any(s.connected for s in found.values())  # used to raise on the UI thread
    assert found["hardcover"].needs == "Couldn't read the saved keys"


def test_sync_everything_goes_on_past_a_credential_store_error(conn):
    from taste import runner

    lines = []
    store = SecretStore(backend=LockedKeyring())
    results = runner.sync_sources(conn, "me", "mal", store=store, out=lines.append)
    assert results == {"mal": False}
    assert any("credential store" in line for line in lines)
