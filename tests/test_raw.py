from datetime import datetime, timezone

from taste import raw
from taste.sync_log import SyncRun
from tests.test_lastfm_sync import NOW_1, FakeLastfm, run_sync


def store(conn, run, payload, params=None, page=1):
    return raw.store_page(
        conn,
        sync_run_id=run.run_id,
        source="mal",
        endpoint="/test",
        params=params or {"limit": 1},
        page_number=page,
        http_status=200,
        payload=payload,
    )


def pages(conn):
    return conn.execute("SELECT COUNT(*) FROM raw_api_pages").fetchone()[0]


def test_identical_page_is_not_stored_twice(conn):
    run = SyncRun(conn, "mal", "full")
    first = store(conn, run, {"data": [1, 2]})
    assert store(conn, run, {"data": [1, 2]}) == first
    assert pages(conn) == 1
    # A change, or a different request, is stored.
    assert store(conn, run, {"data": [1, 2, 3]}) != first
    assert store(conn, run, {"data": [1, 2]}, params={"limit": 2}) != first
    assert pages(conn) == 3


def test_lookback_pages_with_nothing_new_are_dropped(conn, make_client):
    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    client, _ = make_client(server)
    run_sync(conn, client, now=NOW_1)
    after_first = pages(conn)
    assert after_first == 1  # one page held every new scrobble

    run_sync(conn, client, now=NOW_1 + 3600)  # same scrobbles again via the lookback
    assert pages(conn) == after_first


def test_prune_keeps_referenced_and_latest_pages(conn):
    run = SyncRun(conn, "mal", "full")
    old_unused = store(conn, run, {"v": 1})
    old_latest = store(conn, run, {"v": 2})  # latest copy of request A
    old_other = store(conn, run, {"v": 3}, params={"limit": 9})  # latest copy of request B
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
    newer = store(conn, run, {"v": 4})  # now request A's latest

    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, last_raw_page_id, loaded_at) "
        "VALUES (1, 'Example', ?, '2020-01-01 00:00:00')",
        (old_latest,),
    )
    deleted = raw.prune(conn, 180, now=datetime(2026, 10, 9, tzinfo=timezone.utc))
    remaining = {r[0] for r in conn.execute("SELECT raw_page_id FROM raw_api_pages")}
    assert deleted == 1
    assert old_unused not in remaining  # old, unused, superseded
    assert old_latest in remaining  # staging still points to it
    assert old_other in remaining  # latest copy of its request
    assert newer in remaining


def test_prune_disabled_with_zero(conn):
    run = SyncRun(conn, "mal", "full")
    store(conn, run, {"v": 1})
    store(conn, run, {"v": 2})
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2000-01-01 00:00:00'")
    assert raw.prune(conn, 0) == 0
    assert pages(conn) == 2


def test_discard_never_deletes_a_page_in_use(conn):
    run = SyncRun(conn, "mal", "full")
    page = store(conn, run, {"v": 1})
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, last_raw_page_id, loaded_at) "
        "VALUES (1, 'Example', ?, '2026-01-01 00:00:00')",
        (page,),
    )
    assert not raw.discard_if_unused(conn, page)
    assert pages(conn) == 1


def test_pruning_and_saving_use_indexes_not_full_scans(conn):
    # A big library made prune take seconds per sync: every old page scanned each
    # staging table. Small test data can't show the time, so check the plans.
    def plan(sql, params=()):
        return " | ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))

    prune = plan(
        f"DELETE FROM raw_api_pages AS p WHERE p.fetched_at < ? AND {raw._unreferenced_sql('p')} "
        "AND p.raw_page_id <> (SELECT MAX(q.raw_page_id) FROM raw_api_pages q "
        "WHERE q.source = p.source AND q.endpoint = p.endpoint "
        "AND q.request_params = p.request_params)",
        ("2026-01-01 00:00:00",),
    )
    assert "SCAN r" not in prune and "SCAN q" not in prune, prune
    latest = plan(
        "SELECT raw_page_id, payload FROM raw_api_pages WHERE source = ? AND endpoint = ? "
        "AND request_params = ? ORDER BY raw_page_id DESC LIMIT 1",
        ("lastfm", "user.getrecenttracks", "{}"),
    )
    assert "ix_raw_api_pages_request" in latest and "TEMP B-TREE" not in latest, latest
