"""TV suggestions from an invented account, never from live APIs."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from taste import recommend, runner, settings
from taste.sources import tmdb
from tests.fake_tmdb import CATALOG, RATINGS, FakeTmdb, tv
from tests.test_tmdb import CFG, client_for

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


@pytest.fixture
def library(conn, make_client):
    catalog = deepcopy(CATALOG)
    catalog[("tv", 31)]["_recs"] = [32, 41, 42, 43, 44]
    catalog[("tv", 33)] = tv(33, "Example Lowland", 6.0, ["Comedy"], "Mo Writer")
    for i, name in (
        (41, "Example River"),
        (42, "Example Future"),
        (43, "Example Thin Average"),
        (44, "Example Unsettled"),
    ):
        catalog[("tv", i)] = tv(i, name, 7.8, ["Drama"], "Lu Writer")
        catalog[("tv", i)]["overview"] = "An invented series about a river."
    catalog[("tv", 42)]["first_air_date"] = "2027-01-10"
    catalog[("tv", 43)].update(vote_average=9.5, vote_count=300)
    catalog[("tv", 44)]["vote_count"] = 12
    fake = FakeTmdb(
        catalog=catalog,
        ratings=[*RATINGS, ("tv", 33, 5.5, "2026-09-21T02:00:00.000Z")],
    )
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None, now=NOW)
    return fake, client


def test_tv_enrichment_keeps_raw_lists_caches_and_fetches_details(conn, library):
    fake, client = library
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None, now=NOW)
    assert ("GET", "/3/tv/31/recommendations") in fake.calls
    assert ("GET", "/3/tv/33/recommendations") not in fake.calls
    assert ("GET", "/3/tv/41") in fake.calls
    assert conn.execute("SELECT COUNT(*) FROM stg_tmdb_tv_recommendations").fetchone()[0] == 5
    assert (
        conn.execute(
            "SELECT source FROM raw_api_pages WHERE endpoint = '/3/tv/{id}/recommendations'"
        ).fetchone()[0]
        == "tmdb"
    )
    assert (
        conn.execute(
            "SELECT fetched_at FROM stg_tmdb_fetches WHERE tmdb_id = 31 "
            "AND method = 'tv_recommendations'"
        ).fetchone()[0]
        == "2026-10-24 00:00:00"
    )
    calls = len(fake.calls)
    assert tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None, now=NOW) == 0
    assert len(fake.calls) == calls
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None, now=NOW + timedelta(days=31))
    assert fake.calls.count(("GET", "/3/tv/31/recommendations")) == 2
    assert conn.execute("SELECT COUNT(*) FROM stg_tmdb_tv_recommendations").fetchone()[0] == 5


def test_tv_list_uses_tv_ratings_caution_watchlist_and_dismissal(conn, library, monkeypatch):
    _, client = library
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None, now=NOW)
    models = []
    original = recommend.build_model

    def record_model(scored, facts):
        model = original(scored, facts)
        models.append(model)
        return model

    monkeypatch.setattr(recommend, "build_model", record_model)
    recs, metrics = recommend.tv_recommendations(conn, date(2026, 10, 24))
    assert models[0].slope == 1 and models[0].intercept is None
    assert set(models[0].studio) == {"Lu Writer", "Mo Writer"}
    conn.execute("UPDATE stg_tmdb_account_ratings SET rating = 1 WHERE media_type = 'movie'")
    assert recommend.tv_recommendations(conn, NOW.date()) == (recs, metrics)
    assert runner.refresh_recommendations(conn, "me", fetch=False, now=NOW, out=lambda m: None)[
        "tv"
    ] == len(recs)
    picks = {r.item_key: r for r in recs}
    assert set(picks) == {"32", "41", "43"}
    assert metrics["rated"] == 2
    assert all(r.kind == "tv" and "chance_8_plus" not in r.facts for r in recs)
    assert picks["32"].badge == "On your watchlist"
    assert picks["41"].url == "https://www.themoviedb.org/tv/41"
    assert "Lu Writer" in picks["41"].subtitle
    assert picks["41"].facts["about"] == "An invented series about a river."
    assert "TMDB recommends it alongside Example Harbor Series, which you rated 8.5." in (
        picks["41"].reasons
    )
    settings.set_value(conn, "film_prior_votes", 0)
    uncautious = {r.item_key: r for r in recommend.tv_recommendations(conn, NOW.date())[0]}
    assert uncautious["43"].score > picks["43"].score
    recommend.compute_all(conn, NOW, ("tv",))
    assert "tv" in recommend.LIST_KINDS
    recommend.dismiss(conn, "tv", "41")
    assert "41" not in {r.item_key for r in recommend.latest(conn, "tv")[0]}
    assert "41" not in {r.item_key for r in recommend.tv_recommendations(conn)[0]}
    assert all(r.kind == "tv" for r in recommend.latest(conn, "tv")[0])


def test_tv_fetch_limit_and_movie_cache_do_not_hide_tv(conn, make_client):
    catalog = {
        ("tv", 31): tv(31, "Example Seed", 8.0, ["Drama"], "Lu Writer"),
        ("tv", 33): tv(33, "Example Lower Rating", 6.0, ["Comedy"], "Mo Writer"),
    }
    catalog[("tv", 31)]["_recs"] = list(range(100, 225))
    for i in range(100, 225):
        catalog[("tv", i)] = tv(i, f"Example Candidate {i}", 7.0, ["Drama"], "Lu Writer")
    fake = FakeTmdb(
        catalog=catalog,
        watchlist=[],
        favorites=[],
        ratings=[
            ("tv", 31, 8.0, "2026-09-20T02:00:00.000Z"),
            ("tv", 33, 5.0, "2026-09-21T02:00:00.000Z"),
        ],
    )
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None, now=NOW)
    fake.calls.clear()
    conn.execute(
        "INSERT INTO stg_tmdb_fetches VALUES (31, 'recommendations', '2026-10-24 00:00:00')"
    )
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None, now=NOW)
    details = [
        path
        for _, path in fake.calls
        if path.startswith("/3/tv/") and not path.endswith("/recommendations")
    ]
    assert len(details) == 120
    assert "/3/tv/100" in details and "/3/tv/219" in details
    assert "/3/tv/220" not in details
    assert conn.execute("SELECT COUNT(*) FROM stg_tmdb_tv_recommendations").fetchone()[0] == 125


def test_tv_holdout_on_invented_library(conn, make_client, capsys):
    catalog, ratings = {}, []
    for i in range(40):
        vote = 5.0 + (i % 10) * 0.3
        catalog[("tv", 100 + i)] = tv(
            100 + i, f"Example Rated Series {i}", vote, ["Drama"], "Lu Writer"
        )
        rating = round((vote - 0.8 + (-0.5, 0, 0.5)[i % 3]) * 2) / 2
        ratings.append(("tv", 100 + i, rating, "2026-09-20T02:00:00.000Z"))
    catalog[("tv", 100)]["_recs"] = [200]
    catalog[("tv", 200)] = tv(200, "Example New Series", 7.5, ["Drama"], "Lu Writer")
    fake = FakeTmdb(catalog=catalog, ratings=ratings, watchlist=[("tv", 200)], favorites=[])
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None, now=NOW)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None, now=NOW)
    recs, metrics = recommend.tv_recommendations(conn, NOW.date())
    assert metrics["holdout_count"] == 40
    assert metrics["holdout_mae"] < metrics["tmdb_alone_mae"]
    assert "chance_8_plus" in recs[0].facts
    with capsys.disabled():
        print(f"Invented TV holdout: {metrics}")


def test_tv_upgrade_adds_overview_and_recovers_cached_synopsis(conn, library):
    fake, _ = library
    # The fixture synced the raw details before a pre-2.3 database's upgrade.
    conn.execute("ALTER TABLE stg_tmdb_tv DROP COLUMN overview")
    from taste.db import apply_schema

    apply_schema(conn)
    assert "overview" in {r[1] for r in conn.execute("PRAGMA table_info(stg_tmdb_tv)")}
    # Put an invented synopsis in its stored raw response, as an older app would retain it.
    import json

    row = conn.execute("SELECT raw_page_id FROM stg_tmdb_tv WHERE tmdb_id = 31").fetchone()
    payload = dict(fake.catalog[("tv", 31)], overview="An invented harbor story.")
    conn.execute(
        "UPDATE raw_api_pages SET payload = ? WHERE raw_page_id = ?", (json.dumps(payload), row[0])
    )
    tmdb.backfill_overviews(conn)
    assert conn.execute("SELECT overview FROM stg_tmdb_tv WHERE tmdb_id = 31").fetchone()[0] == (
        "An invented harbor story."
    )
