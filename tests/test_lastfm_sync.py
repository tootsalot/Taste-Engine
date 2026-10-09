import math

from taste import settings
from taste.config import LastfmSettings
from taste.sources import lastfm
from tests.conftest import FAKE_LASTFM_KEY, FakeResponse, load_fixture

SETTINGS = LastfmSettings(api_key=FAKE_LASTFM_KEY, username="example_user")
NOW_1 = 1791500000  # "now" for the first run
NOW_2 = 1791700000  # "now" for the second run
TABLES = [
    "stg_lastfm_scrobbles",
    "core_items",
    "core_item_external_ids",
    "core_item_links",
    "core_creators",
    "core_creator_external_ids",
    "core_item_creators",
    "core_behavior_events",
]


class FakeLastfm:
    """A tiny Last.fm stand-in built from fixture tracks.

    Honors from (inclusive), to (exclusive), page, and limit like the real API,
    and puts any now-playing track at the top of page 1.
    """

    def __init__(self, *fixtures):
        self.now_playing = []
        self.scrobbles = []
        for name in fixtures:
            self.add(name)
        self.fail = None  # optional callable(params) -> FakeResponse or None

    def add(self, fixture):
        seen = {self._key(t) for t in self.scrobbles}
        for track in load_fixture(fixture)["recenttracks"]["track"]:
            if "date" not in track:
                self.now_playing.append(track)
            elif self._key(track) not in seen:
                self.scrobbles.append(track)

    @staticmethod
    def _key(track):
        return (track["date"]["uts"], track["artist"]["#text"], track["name"])

    def __call__(self, url, params):
        if self.fail:
            response = self.fail(params)
            if response is not None:
                return response
        lo, hi = int(params["from"]), int(params["to"])
        limit, page = int(params["limit"]), int(params["page"])
        matching = sorted(
            (t for t in self.scrobbles if lo <= int(t["date"]["uts"]) < hi),
            key=lambda t: int(t["date"]["uts"]),
            reverse=True,
        )
        total_pages = math.ceil(len(matching) / limit)
        chunk = matching[(page - 1) * limit : page * limit]
        if page == 1:
            chunk = self.now_playing + chunk
        attr = {
            "user": "example_user",
            "page": str(page),
            "perPage": str(limit),
            "totalPages": str(total_pages),
            "total": str(len(matching)),
        }
        return FakeResponse(200, {"recenttracks": {"track": chunk, "@attr": attr}})


def run_sync(conn, client, now=NOW_1, **kwargs):
    return lastfm.sync(conn, client, SETTINGS, out=lambda msg: None, now=lambda: now, **kwargs)


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def recorded_pages_handler(url, params):
    name = "lastfm_recent_page1.json" if params["page"] == 1 else "lastfm_recent_page2.json"
    return FakeResponse(200, load_fixture(name))


def test_pagination_follows_total_pages(conn, make_client):
    client, transport = make_client(recorded_pages_handler)
    result = run_sync(conn, client)
    assert result.status == "success"
    assert result.mode == "full"
    assert [c["params"]["page"] for c in transport.calls] == [1, 2]
    for call in transport.calls:
        params = call["params"]
        assert params["method"] == "user.getrecenttracks"
        assert params["limit"] == 200
        assert params["format"] == "json"
        assert params["from"] == 0
        assert params["to"] == NOW_1  # fixed for the whole window
    assert result.pages_fetched == 2
    assert result.rows_inserted == 5
    window = conn.execute("SELECT * FROM sync_lastfm_windows").fetchone()
    assert window["status"] == "complete"
    assert window["cursor_unix"] == 1791400000


def test_now_playing_is_skipped(conn, make_client):
    client, _ = make_client(recorded_pages_handler)
    result = run_sync(conn, client)
    assert result.rows_fetched == 5  # page 1 had 4 tracks, one of them now-playing
    names = [r[0] for r in conn.execute("SELECT track_name FROM stg_lastfm_scrobbles")]
    assert "Slow Fade" not in names


def test_single_track_object_is_handled():
    track = load_fixture("lastfm_recent_page2.json")["recenttracks"]["track"][0]
    assert len(lastfm.parse_tracks(track)) == 1


def test_idempotent_resync(conn, make_client):
    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    client, _ = make_client(server)
    run_sync(conn, client, now=NOW_1)
    before = counts(conn)
    result = run_sync(conn, client, now=NOW_1 + 60)
    assert counts(conn) == before
    assert result.rows_inserted == 0
    assert result.events_loaded == 0


def test_incremental_sync_only_adds_newer_scrobbles(conn, make_client):
    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    client, transport = make_client(server)
    run_sync(conn, client, now=NOW_1)

    server.add("lastfm_recent_newer.json")
    transport.calls.clear()
    result = run_sync(conn, client, now=NOW_2)

    assert result.mode == "incremental"
    first = transport.calls[0]["params"]
    assert first["from"] == NOW_1 - 14 * lastfm.DAY_SECONDS  # default lookback setting
    assert first["to"] == NOW_2
    assert result.rows_inserted == 2  # Neon Rain again (new time) and Undertow
    assert conn.execute("SELECT COUNT(*) FROM stg_lastfm_scrobbles").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM core_behavior_events").fetchone()[0] == 7


def test_lookback_catches_late_scrobbles(conn, make_client):
    server = FakeLastfm("lastfm_recent_page2.json")
    client, _ = make_client(server)
    run_sync(conn, client, now=NOW_1)
    # Page 1's scrobbles are older than NOW_1 but arrive late, inside the lookback.
    server.add("lastfm_recent_page1.json")
    result = run_sync(conn, client, now=NOW_2)
    assert result.rows_inserted == 3


def test_interrupted_sync_resumes_without_gaps_or_duplicates(conn, make_client):
    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    server.fail = lambda params: FakeResponse(503, {}) if params["page"] == 2 else None
    client, transport = make_client(server)

    first = run_sync(conn, client, now=NOW_1, page_limit=2)
    assert first.status == "failed"
    assert "gave up" in first.error
    window = conn.execute("SELECT * FROM sync_lastfm_windows").fetchone()
    assert window["status"] == "open"
    assert window["cursor_unix"] == 1791400200  # oldest scrobble on the committed page
    assert conn.execute("SELECT COUNT(*) FROM stg_lastfm_scrobbles").fetchone()[0] == 2

    server.fail = None
    transport.calls.clear()
    second = run_sync(conn, client, now=NOW_2, page_limit=2)
    assert second.status == "success"
    assert second.mode == "resume"
    resume_call = transport.calls[0]["params"]
    assert resume_call["from"] == 0
    assert resume_call["to"] == 1791400201  # cursor + 1, because `to` is exclusive

    stored = conn.execute(
        "SELECT played_at_unix FROM stg_lastfm_scrobbles ORDER BY played_at_unix"
    ).fetchall()
    assert [r[0] for r in stored] == [1791400000, 1791400050, 1791400100, 1791400200, 1791400300]
    statuses = [r[0] for r in conn.execute("SELECT status FROM sync_lastfm_windows")]
    assert statuses == ["complete", "complete"]
    assert conn.execute("SELECT COUNT(*) FROM core_behavior_events").fetchone()[0] == 5


def test_backs_off_on_rate_limit_error(conn, make_client, sleeper):
    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    hits = []

    def rate_limit_once(params):
        if not hits:
            hits.append(1)
            return FakeResponse(200, load_fixture("lastfm_error_29.json"))
        return None

    server.fail = rate_limit_once
    client, transport = make_client(server)
    result = run_sync(conn, client)
    assert result.status == "success"
    assert 2.0 in sleeper.delays
    assert len(transport.calls) == 2


def test_gives_up_and_logs_after_repeated_rate_limits(conn, make_client, sleeper):
    client, transport = make_client(
        lambda url, params: FakeResponse(429, load_fixture("lastfm_error_29.json"))
    )
    result = run_sync(conn, client)
    assert result.status == "failed"
    assert len(transport.calls) == 5
    assert [d for d in sleeper.delays if d >= 1] == [2.0, 4.0, 8.0, 16.0]
    row = conn.execute(
        "SELECT status, error_message FROM sync_runs ORDER BY sync_run_id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "failed"
    assert "gave up after 4 retries" in row["error_message"]
    assert "Last.fm error 29" in row["error_message"]
    assert FAKE_LASTFM_KEY not in row["error_message"]


def test_invalid_key_fails_without_retry(conn, make_client):
    client, transport = make_client(
        lambda url, params: FakeResponse(403, {"error": 10, "message": "Invalid API key"})
    )
    result = run_sync(conn, client)
    assert result.status == "failed"
    assert len(transport.calls) == 1


def test_raw_pages_never_store_the_api_key(conn, make_client):
    client, _ = make_client(recorded_pages_handler)
    run_sync(conn, client)
    rows = conn.execute("SELECT request_params, payload FROM raw_api_pages").fetchall()
    assert len(rows) == 2
    for row in rows:
        assert FAKE_LASTFM_KEY not in row["request_params"]
        assert "api_key" not in row["request_params"]


def test_core_tracks_artists_albums_and_scope(conn, make_client):
    settings.set_value(conn, "lastfm_capture_scope", "desktop_only")
    client, _ = make_client(recorded_pages_handler)
    run_sync(conn, client)

    # "Signal" and "signal" by "The Example Band" / "the example band" are one track and one
    # artist. The oldest scrobble's spelling is the display name, so it never flips later.
    tracks = conn.execute("SELECT title FROM core_items WHERE media_type = 'track'").fetchall()
    assert sorted(r[0] for r in tracks) == ["Neon Rain", "Static", "Untitled Demo", "signal"]
    artists = conn.execute("SELECT name FROM core_creators ORDER BY name").fetchall()
    assert [r[0] for r in artists] == ["Night Driver", "Some Indie Artist", "the example band"]
    plays = conn.execute(
        "SELECT COUNT(*) FROM core_behavior_events e JOIN core_items i ON i.item_id = e.item_id "
        "WHERE i.title = 'signal'"
    ).fetchone()[0]
    assert plays == 2

    # Empty album name: no album item and no link.
    albums = conn.execute("SELECT title FROM core_items WHERE media_type = 'album'").fetchall()
    assert sorted(r[0] for r in albums) == ["After Hours Drive", "First Light"]
    assert conn.execute("SELECT COUNT(*) FROM core_item_links").fetchone()[0] == 3

    # MBIDs are extra IDs, not the identity.
    mbids = conn.execute(
        "SELECT id_type, COUNT(*) FROM core_item_external_ids "
        "WHERE id_type LIKE 'musicbrainz%' GROUP BY id_type ORDER BY id_type"
    ).fetchall()
    assert [tuple(r) for r in mbids] == [("musicbrainz_recording", 1), ("musicbrainz_release", 1)]

    scopes = conn.execute(
        "SELECT DISTINCT capture_scope, time_basis, event_type FROM core_behavior_events"
    ).fetchall()
    assert [tuple(r) for r in scopes] == [("desktop_only", "source_timestamp", "play")]


def test_capture_scope_applies_to_new_scrobbles_only(conn, make_client):
    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    client, _ = make_client(server)
    settings.set_value(conn, "lastfm_capture_scope", "desktop_only")
    run_sync(conn, client, now=NOW_1)
    settings.set_value(conn, "lastfm_capture_scope", "all_devices")
    server.add("lastfm_recent_newer.json")
    run_sync(conn, client, now=NOW_2)
    scopes = conn.execute(
        "SELECT capture_scope, COUNT(*) FROM core_behavior_events GROUP BY capture_scope "
        "ORDER BY capture_scope"
    ).fetchall()
    assert [tuple(r) for r in scopes] == [("all_devices", 2), ("desktop_only", 5)]
    assert conn.execute("SELECT capture_scopes FROM rpt_lastfm_scope").fetchone()[0] == "mixed"


def test_lookback_days_setting_is_used(conn, make_client):
    server = FakeLastfm("lastfm_recent_page1.json")
    client, transport = make_client(server)
    run_sync(conn, client, now=NOW_1)
    settings.set_value(conn, "lastfm_lookback_days", 3)
    transport.calls.clear()
    run_sync(conn, client, now=NOW_2)
    assert transport.calls[0]["params"]["from"] == NOW_1 - 3 * lastfm.DAY_SECONDS
