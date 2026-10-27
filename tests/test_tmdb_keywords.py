"""Made-up keyword responses, one-request detail reads, and the one-time backfill."""

import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from taste import db, themes
from taste.sources import tmdb
from tests.fake_tmdb import ACCOUNT, CATALOG, READ_TOKEN, USER_TOKEN, FakeTmdb

CFG = tmdb.TmdbSettings(READ_TOKEN, USER_TOKEN, ACCOUNT)
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_details_append_keywords_without_extra_requests_or_model_terms(conn, make_client):
    client, transport = make_client(FakeTmdb())
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW)
    details = [c for c in transport.calls if "/3/" in c["url"]]
    assert len(details) == 7
    for call in details:
        assert call["params"] == {
            "append_to_response": "credits,keywords" if "/movie/" in call["url"] else "keywords"
        }
        assert not call["url"].endswith("/keywords")
    assert json.loads(
        conn.execute("SELECT keywords_json FROM stg_tmdb_movies WHERE tmdb_id = 11").fetchone()[0]
    ) == ["clockwork harbor", "borrowed memories"]
    assert json.loads(
        conn.execute("SELECT keywords_json FROM stg_tmdb_tv WHERE tmdb_id = 31").fetchone()[0]
    ) == ["clockwork harbor", "borrowed memories"]
    keywords = conn.execute(
        "SELECT DISTINCT t.name FROM core_item_tags it "
        "JOIN core_tags t ON t.tag_id = it.tag_id WHERE t.tag_kind = 'keyword'"
    ).fetchall()
    assert {r[0] for r in keywords} == {
        "clockwork harbor",
        "borrowed memories",
    }
    assert all("clockwork harbor" not in tags for tags in themes.load(conn)["item_tags"].values())


@pytest.mark.parametrize("kind,field", [("movie", "keywords"), ("tv", "results")])
def test_keywords_are_trimmed_deduplicated_validated_and_bounded(conn, kind, field):
    data = deepcopy(CATALOG[(kind, 11 if kind == "movie" else 31)])
    data["keywords"] = {
        field: [
            None,
            7,
            {},
            {"name": None},
            {"name": False},
            {"name": "  "},
            {"name": "  Clockwork Harbor  "},
            {"name": "clockwork harbor"},
            *({"name": f"invented keyword {i}"} for i in range(tmdb.MAX_KEYWORDS + 10)),
        ]
    }
    tmdb.save_details(conn, kind, data, None, "2026-10-20 00:00:00")
    table = "stg_tmdb_movies" if kind == "movie" else "stg_tmdb_tv"
    names = json.loads(conn.execute(f"SELECT keywords_json FROM {table}").fetchone()[0])
    assert len(names) == tmdb.MAX_KEYWORDS
    assert names[:2] == ["Clockwork Harbor", "invented keyword 0"]
    assert len({n.casefold() for n in names}) == len(names)


@pytest.mark.parametrize("kind,field", [("movie", "keywords"), ("tv", "results")])
def test_missing_keyword_data_stays_unknown_and_empty_answer_stays_known(conn, kind, field):
    data = deepcopy(CATALOG[(kind, 11 if kind == "movie" else 31)])
    del data["keywords"]
    table = "stg_tmdb_movies" if kind == "movie" else "stg_tmdb_tv"
    tmdb.save_details(conn, kind, data, None, "2026-10-20 00:00:00")
    assert conn.execute(f"SELECT keywords_json FROM {table}").fetchone()[0] is None
    assert (kind, data["id"]) in tmdb._detail_ids(conn, NOW, account_only=False)
    data["keywords"] = {field: []}
    tmdb.save_details(conn, kind, data, None, "2026-10-20 00:00:00")
    assert conn.execute(f"SELECT keywords_json FROM {table}").fetchone()[0] == "[]"
    assert (kind, data["id"]) not in tmdb._detail_ids(conn, NOW, account_only=False)
    for malformed in (None, [], {field: None}, {field: "not a keyword list"}):
        data["keywords"] = malformed
        tmdb.save_details(conn, kind, data, None, "2026-10-20 00:00:00")
        assert conn.execute(f"SELECT keywords_json FROM {table}").fetchone()[0] == "[]"
        assert (kind, data["id"]) not in tmdb._detail_ids(conn, NOW, account_only=False)


def test_missing_keywords_obey_the_existing_removed_title_retry_window(conn, make_client):
    from datetime import timedelta

    fake = FakeTmdb()
    client, _ = make_client(fake)
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW)
    conn.execute("UPDATE stg_tmdb_movies SET keywords_json = NULL WHERE tmdb_id = 12")
    fake.gone.add(("movie", 12))
    fake.calls.clear()
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW)
    assert ("GET", "/3/movie/12") in fake.calls
    fake.calls.clear()
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW + timedelta(days=1))
    assert ("GET", "/3/movie/12") not in fake.calls
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW + timedelta(days=31))
    assert ("GET", "/3/movie/12") in fake.calls


def test_sync_backfills_account_keywords_once_and_refresh_backfills_candidates(conn, make_client):
    fake = FakeTmdb()
    client, _ = make_client(fake)
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda _: None, now=NOW)
    conn.execute("UPDATE stg_tmdb_movies SET keywords_json = NULL WHERE tmdb_id IN (11, 22)")
    conn.execute("UPDATE stg_tmdb_tv SET keywords_json = NULL WHERE tmdb_id = 31")
    fake.calls.clear()
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW)
    assert ("GET", "/3/movie/11") in fake.calls
    assert ("GET", "/3/tv/31") in fake.calls
    assert ("GET", "/3/movie/22") not in fake.calls
    fake.calls.clear()
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW)
    assert not any(path.startswith("/3/") for _, path in fake.calls)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda _: None, now=NOW)
    assert ("GET", "/3/movie/22") in fake.calls
    fake.calls.clear()
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda _: None, now=NOW)
    assert fake.calls == []


def test_existing_databases_gain_nullable_keyword_columns(conn):
    for table in ("stg_tmdb_movies", "stg_tmdb_tv"):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN keywords_json")
    db.apply_schema(conn)
    for table in ("stg_tmdb_movies", "stg_tmdb_tv"):
        column = next(
            r for r in conn.execute(f"PRAGMA table_info({table})") if r[1] == "keywords_json"
        )
        assert column[2:5] == ("TEXT", 0, None)
