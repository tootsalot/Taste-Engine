import pytest

from taste.config import MalSettings
from taste.sources import mal
from tests.conftest import FAKE_MAL_CLIENT_ID, FakeResponse, load_fixture

SETTINGS = MalSettings(client_id=FAKE_MAL_CLIENT_ID, username="example_user")
TABLES = [
    "stg_mal_anime",
    "stg_mal_anime_genres",
    "stg_mal_anime_studios",
    "stg_mal_list_entries",
    "core_items",
    "core_item_external_ids",
    "core_creators",
    "core_item_creators",
    "core_tags",
    "core_item_tags",
    "core_ratings",
    "core_community_ratings",
    "core_behavior_events",
    "core_curation",
]


def two_page_handler(url, params):
    if "offset=3" in url:
        return FakeResponse(200, load_fixture("mal_animelist_page2.json"))
    return FakeResponse(200, load_fixture("mal_animelist_page1.json"))


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def run_sync(conn, make_client, handler=two_page_handler):
    client, transport = make_client(handler)
    result = mal.sync(conn, client, SETTINGS, out=lambda msg: None)
    return result, transport


def test_pagination_follows_paging_next(conn, make_client):
    result, transport = run_sync(conn, make_client)
    assert result.status == "success"
    assert len(transport.calls) == 2
    first, second = transport.calls
    assert first["url"].endswith("/users/example_user/animelist")
    assert first["params"]["nsfw"] == "true"
    assert first["params"]["limit"] == 1000
    assert first["headers"] == {"X-MAL-CLIENT-ID": FAKE_MAL_CLIENT_ID}
    assert "offset=3" in second["url"]
    assert result.pages_fetched == 2
    assert result.rows_fetched == 5
    assert conn.execute("SELECT COUNT(*) FROM stg_mal_list_entries").fetchone()[0] == 5
    raw = conn.execute("SELECT page_number, request_params FROM raw_api_pages").fetchall()
    assert [r["page_number"] for r in raw] == [1, 2]
    assert all(FAKE_MAL_CLIENT_ID not in r["request_params"] for r in raw)


def test_score_zero_becomes_null(conn, make_client):
    run_sync(conn, make_client)
    score = conn.execute(
        "SELECT score FROM stg_mal_list_entries WHERE mal_anime_id = 10002"
    ).fetchone()[0]
    assert score is None
    item_id = conn.execute(
        "SELECT item_id FROM core_item_external_ids WHERE external_id = '10002'"
    ).fetchone()[0]
    assert (
        conn.execute("SELECT COUNT(*) FROM core_ratings WHERE item_id = ?", (item_id,)).fetchone()[
            0
        ]
        == 0
    )
    # Still on my list, just unscored.
    assert (
        conn.execute("SELECT value FROM core_curation WHERE item_id = ?", (item_id,)).fetchone()[0]
        == "completed"
    )


def test_ratings_are_min_max_normalized(conn, make_client):
    run_sync(conn, make_client)
    rows = conn.execute(
        "SELECT raw_score, normalized_score FROM core_ratings ORDER BY raw_score"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        (4.0, pytest.approx(3 / 9)),
        (5.0, pytest.approx(4 / 9)),
        (8.0, pytest.approx(7 / 9)),
    ]


def test_idempotent_resync(conn, make_client):
    run_sync(conn, make_client)
    before = counts(conn)
    result, _ = run_sync(conn, make_client)
    assert counts(conn) == before
    assert (result.rows_inserted, result.rows_updated, result.rows_removed) == (0, 0, 0)


def test_changed_and_removed_entries(conn, make_client):
    run_sync(conn, make_client)
    result, _ = run_sync(
        conn,
        make_client,
        lambda url, params: FakeResponse(200, load_fixture("mal_animelist_changed.json")),
    )
    assert (result.rows_inserted, result.rows_updated, result.rows_removed) == (0, 1, 1)
    assert (
        conn.execute(
            "SELECT score FROM stg_mal_list_entries WHERE mal_anime_id = 10001"
        ).fetchone()[0]
        == 6
    )
    assert (
        conn.execute(
            "SELECT r.raw_score FROM core_ratings r JOIN core_item_external_ids x "
            "ON x.item_id = r.item_id WHERE x.external_id = '10001'"
        ).fetchone()[0]
        == 6
    )
    removed = conn.execute(
        "SELECT removed_at FROM stg_mal_list_entries WHERE mal_anime_id = 50000"
    ).fetchone()[0]
    assert removed is not None
    assert conn.execute("SELECT COUNT(*) FROM core_curation").fetchone()[0] == 4


def test_failed_page_applies_no_removals(conn, make_client):
    run_sync(conn, make_client)

    def page_two_fails(url, params):
        if "offset=3" in url:
            return FakeResponse(503, {})
        return FakeResponse(200, load_fixture("mal_animelist_page1.json"))

    result, _ = run_sync(conn, make_client, page_two_fails)
    assert result.status == "failed"
    assert "gave up" in result.error
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    row = conn.execute(
        "SELECT status, error_message FROM sync_runs ORDER BY sync_run_id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "failed"
    assert FAKE_MAL_CLIENT_ID not in row["error_message"]


def events_by_anime(conn):
    rows = conn.execute(
        "SELECT source_event_key, event_type, occurred_at_utc, occurred_at_unix, "
        "time_precision, time_basis, capture_scope FROM core_behavior_events"
    ).fetchall()
    return {r["source_event_key"]: dict(r) for r in rows}


def test_watch_date_events_and_fallback(conn, make_client):
    run_sync(conn, make_client)
    events = events_by_anime(conn)

    # Entered finish date, no start date: finish event only.
    assert events["10001:finished"]["time_basis"] == "user_entered"
    assert events["10001:finished"]["occurred_at_utc"] == "2021-01-15 00:00:00"
    assert events["10001:finished"]["time_precision"] == "day"
    assert "10001:started" not in events

    # Completed with no dates: finish falls back to updated_at, no start event.
    fallback = events["10002:finished"]
    assert fallback["time_basis"] == "fallback_list_updated_at"
    assert fallback["occurred_at_utc"] == "2025-06-01 12:00:00"
    assert fallback["occurred_at_unix"] == 1748779200
    assert "10002:started" not in events

    # Dropped: start date used, no finish fallback because it wasn't completed.
    assert events["30000:started"]["time_basis"] == "user_entered"
    assert "30000:finished" not in events

    # Partial start date keeps its real precision.
    assert events["40000:started"]["time_precision"] == "month"
    assert events["40000:started"]["occurred_at_utc"] == "2024-03-01 00:00:00"

    # On hold with no dates: nothing.
    assert not any(k.startswith("50000:") for k in events)
    assert {e["capture_scope"] for e in events.values()} == {"self_reported"}


def test_core_items_creators_and_tags(conn, make_client):
    run_sync(conn, make_client)
    item = conn.execute(
        "SELECT i.* FROM core_items i JOIN core_item_external_ids x ON x.item_id = i.item_id "
        "WHERE x.source = 'mal' AND x.id_type = 'mal_anime_id' AND x.external_id = '30000'"
    ).fetchone()
    assert item["media_type"] == "anime"
    assert item["title_alt"] is None  # blank English title stored as NULL
    assert item["release_year"] == 2019
    studios = conn.execute(
        "SELECT c.name FROM core_item_creators ic JOIN core_creators c "
        "ON c.creator_id = ic.creator_id WHERE ic.item_id = ? AND ic.role = 'studio' "
        "ORDER BY c.name",
        (item["item_id"],),
    ).fetchall()
    assert [s[0] for s in studios] == ["Example Studio", "Second Studio"]
    # "Example Studio" appears on two anime but is one creator.
    assert (
        conn.execute("SELECT COUNT(*) FROM core_creators WHERE name = 'Example Studio'").fetchone()[
            0
        ]
        == 1
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM core_tags WHERE tag_kind = 'genre'").fetchone()[0] == 5
    )
    # Unknown episode count (0 from MAL) is NULL in staging and in curation.
    assert (
        conn.execute(
            "SELECT num_episodes FROM stg_mal_anime WHERE mal_anime_id = 40000"
        ).fetchone()[0]
        is None
    )


def test_unknown_user_fails_without_retry(conn, make_client, sleeper):
    result, transport = run_sync(
        conn,
        make_client,
        lambda url, params: FakeResponse(404, {"message": "", "error": "not_found"}),
    )
    assert result.status == "failed"
    assert len(transport.calls) == 1
