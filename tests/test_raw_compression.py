"""Old raw pages remain readable and deduplicate after compression."""

import base64
import json
import zlib
from datetime import datetime, timezone

import pytest

from taste import raw
from taste.sync_log import SyncRun
from tests.test_raw import store

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_mixed_pages_round_trip_identical_check_and_second_pass(conn):
    run = SyncRun(conn, "mal", "full")
    body = {"data": ["Example 日本語" * 100]}
    old = store(conn, run, body)
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
    new = store(conn, run, {"data": ["New example"]}, params={"limit": 2})
    assert raw.compress_old_pages(conn, now=NOW, batch_size=1) == 1
    rows = conn.execute("SELECT * FROM raw_api_pages ORDER BY raw_page_id").fetchall()
    assert [r["payload_encoding"] for r in rows] == ["zlib-b64", ""]
    assert raw.read_page(rows[0]["payload"], rows[0]["payload_encoding"]) == body
    assert raw.read_page(rows[1]["payload"], rows[1]["payload_encoding"]) == {
        "data": ["New example"]
    }
    assert store(conn, run, body) == old
    assert raw.compress_old_pages(conn, now=NOW) == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_api_pages").fetchone()[0] == 2
    assert len(rows[0]["payload"]) < len(json.dumps(body)) / 2
    assert rows[1]["raw_page_id"] == new


def test_compact_gives_freed_space_back_to_the_disk(conn):
    from pathlib import Path

    path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    run = SyncRun(conn, "mal", "full")
    for n in range(200):
        store(conn, run, {"data": [f"Example title {i}" for i in range(500)], "n": n}, page=n)
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = path.stat().st_size
    assert not raw.compact(conn)  # nothing free yet: no rewrite
    assert raw.compress_old_pages(conn, now=NOW) == 200
    assert path.stat().st_size >= before  # compressing alone never shrinks the file
    assert raw.compact(conn)
    assert path.stat().st_size < before / 2
    assert not raw.compact(conn)  # once is enough
    page = conn.execute("SELECT payload, payload_encoding FROM raw_api_pages").fetchone()
    assert raw.read_page(*page)["data"][0] == "Example title 0"


def test_reader_rejects_unknown_or_damaged_encoding():
    with pytest.raises(ValueError):
        raw.read_page("{}", "unknown")
    with pytest.raises(ValueError):
        raw.read_page("invalid", "zlib-b64")
    packed = base64.b64encode(zlib.compress(b'{"example": true}')).decode("ascii")
    assert raw.read_page(packed, "zlib-b64") == {"example": True}


def test_upgrade_adds_encoding_without_changing_payload(conn):
    from taste import db

    conn.execute("ALTER TABLE raw_api_pages DROP COLUMN payload_encoding")
    db.apply_schema(conn)
    assert "payload_encoding" in {r[1] for r in conn.execute("PRAGMA table_info(raw_api_pages)")}


def test_tmdb_backfills_read_compressed_pages(conn, make_client):
    from taste.sources import tmdb
    from tests import fake_tmdb
    from tests.test_tmdb import CFG

    api = fake_tmdb.FakeTmdb()
    client, _ = make_client(api)
    tmdb.sync(conn, client, CFG, out=lambda message: None)
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
    assert raw.compress_old_pages(conn, now=NOW) > 0
    conn.execute(
        "UPDATE stg_tmdb_tv SET original_language = NULL, "
        "origin_country_json = NULL, overview = NULL"
    )
    conn.execute("UPDATE stg_tmdb_movies SET overview = NULL, popularity = NULL")
    assert tmdb.backfill_origins(conn) > 0
    tmdb.backfill_overviews(conn)
    assert (
        conn.execute("SELECT COUNT(*) FROM stg_tmdb_tv WHERE original_language IS NULL").fetchone()[
            0
        ]
        == 0
    )


def test_lastfm_backfill_reads_compressed_pages(conn, make_client):
    from taste.sources import lastfm
    from tests.test_lastfm_sync import NOW_1, FakeLastfm, run_sync

    server = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")
    for track in server.scrobbles:
        track["image"] = [
            {
                "size": "extralarge",
                "#text": "https://lastfm.freetls.fastly.net/i/u/300x300/example.png",
            }
        ]
    client, _ = make_client(server)
    run_sync(conn, client, now=NOW_1)
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
    raw.compress_old_pages(conn, now=NOW)
    conn.execute("UPDATE stg_lastfm_scrobbles SET image_url = NULL")
    lastfm.backfill_images(conn)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM stg_lastfm_scrobbles WHERE image_url IS NOT NULL"
        ).fetchone()[0]
        > 0
    )


def test_full_library_payload_size_drops(conn, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from taste import runner, settings
    from taste.secrets_store import SecretStore
    from tests.conftest import FakeKeyring
    from tests.fill_world import NOW, NOW_DT, World

    world = World()
    world.install(monkeypatch)
    store = SecretStore(backend=FakeKeyring())
    for key in ("MAL_CLIENT_ID", "LASTFM_API_KEY"):
        store.set("example_user", key, "invented-key")
    for key in ("mal_username", "lastfm_username"):
        settings.set_value(conn, key, "example_user")
    world.install_extras(
        SimpleNamespace(ctx=SimpleNamespace(store=store), current_profile=lambda: "example_user"),
        tmp_path,
    )
    runner.sync_sources(
        conn,
        "example_user",
        "all",
        store=store,
        client_factory=world.client_factory,
        now=lambda: NOW,
        page_limit=1000,
        out=lambda message: None,
    )
    runner.refresh_recommendations(
        conn,
        "example_user",
        store=store,
        client_factory=world.client_factory,
        now=NOW_DT,
        out=lambda message: None,
    )
    size_sql = "SELECT SUM(length(CAST(payload AS BLOB))) FROM raw_api_pages"
    before = conn.execute(size_sql).fetchone()[0]
    conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
    assert raw.compress_old_pages(conn, now=NOW_DT) > 0
    after = conn.execute(size_sql).fetchone()[0]
    assert after < before / 2
    print(f"Invented library raw payload bytes: {before} -> {after}")
