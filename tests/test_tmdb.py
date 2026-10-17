"""TMDB: connecting an account, reading my ratings and lists, and details. Fake API."""

import json

from taste import runner
from taste.sources import tmdb
from taste.sources.tmdb import TmdbSettings
from tests.fake_tmdb import ACCOUNT, READ_TOKEN, USER_TOKEN, FakeTmdb

CFG = TmdbSettings(READ_TOKEN, USER_TOKEN, ACCOUNT)


def client_for(make_client, fake):
    client, transport = make_client(fake)
    client.secrets += [READ_TOKEN, USER_TOKEN]
    return client, transport


def test_connecting_an_account_takes_two_steps(make_client):
    fake = FakeTmdb()
    client, transport = client_for(make_client, fake)
    request = tmdb.request_token(client, READ_TOKEN)
    assert tmdb.APPROVE_URL.format(request).startswith("https://www.themoviedb.org/auth/access")
    assert tmdb.access_token(client, READ_TOKEN, request) == (USER_TOKEN, ACCOUNT)
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {READ_TOKEN}"
    fake.approved = False
    try:
        tmdb.access_token(client, READ_TOKEN, request)
    except tmdb.ApiError as exc:
        assert "Not approved" in str(exc) and READ_TOKEN not in str(exc)
    else:
        raise AssertionError("an unapproved request must fail")


def test_a_sync_reads_ratings_lists_and_details(conn, make_client):
    fake = FakeTmdb(page_size=2)  # several pages, as a bigger account would have
    client, _ = client_for(make_client, fake)
    result = tmdb.sync(conn, client, CFG, out=lambda m: None)
    assert (result.status, result.rated, result.inserted) == ("success", 5, 5)
    ratings = conn.execute(
        "SELECT i.media_type, i.title, r.raw_score, r.scale_min, r.scale_max FROM core_ratings r "
        "JOIN core_items i ON i.item_id = r.item_id WHERE r.source = 'tmdb' ORDER BY i.title"
    ).fetchall()
    assert [tuple(r) for r in ratings][:2] == [
        ("film", "Example Glass Sky", 8.0, 0.5, 10.0),
        ("tv_show", "Example Harbor Series", 8.5, 0.5, 10.0),
    ]
    # "Rated" events at the time TMDB says I rated it, never called watching.
    events = conn.execute(
        "SELECT source_event_key, event_type, occurred_at_utc FROM core_behavior_events "
        "WHERE source = 'tmdb' ORDER BY source_event_key"
    ).fetchall()
    assert ("movie:11:rated", "rated", "2026-09-12 20:15:00") in [tuple(e) for e in events]
    # Details: directors, genres, posters, and the community average.
    night = conn.execute("SELECT * FROM stg_tmdb_movies WHERE tmdb_id = 11").fetchone()
    assert night["runtime"] == 110 and json.loads(night["directors_json"])[0]["name"] == (
        "Ida Director"
    )
    poster = conn.execute(
        "SELECT url FROM core_item_images WHERE source = 'tmdb' AND url LIKE '%poster11%'"
    ).fetchone()
    assert poster[0] == "https://image.tmdb.org/t/p/w342/poster11.jpg"
    lists = conn.execute(
        "SELECT i.title, c.curation_type FROM core_curation c JOIN core_items i "
        "ON i.item_id = c.item_id WHERE c.source = 'tmdb' ORDER BY i.title, c.curation_type"
    ).fetchall()
    assert [tuple(r) for r in lists] == [
        ("Example Candidate Film", "watchlist"),
        ("Example Night Train", "favorite"),
        ("Example Watchlist Series", "watchlist"),
    ]
    # Keys never reach the raw layer.
    raw = " ".join(
        r[0] for r in conn.execute("SELECT request_params || payload FROM raw_api_pages")
    )
    assert READ_TOKEN not in raw and USER_TOKEN not in raw and ACCOUNT not in raw


def test_a_rating_removed_on_tmdb_goes_from_core(conn, make_client):
    fake = FakeTmdb()
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None)
    fake.ratings = [r for r in fake.ratings if r[1] != 12]
    result = tmdb.sync(conn, client, CFG, out=lambda m: None)
    assert result.removed == 1
    titles = [
        r[0]
        for r in conn.execute(
            "SELECT i.title FROM core_ratings r JOIN core_items i ON i.item_id = r.item_id "
            "WHERE r.source = 'tmdb'"
        )
    ]
    assert "Example Paper Moon" not in titles
    assert conn.execute(
        "SELECT removed_at FROM stg_tmdb_account_ratings WHERE tmdb_id = 12"
    ).fetchone()[0]


def test_sync_everything_leaves_tmdb_out_until_it_is_connected(conn, make_client):
    from taste.secrets_store import SecretStore

    store = SecretStore(backend=None)
    lines = []
    results = runner.sync_sources(conn, "me", "all", store=store, out=lines.append)
    assert "tmdb" not in results  # never set up: not a failure
    assert runner.sync_sources(conn, "me", "tmdb", store=store, out=lines.append) == {"tmdb": False}
    assert any("TMDB needs" in line for line in lines)


def test_film_suggestions_start_from_the_films_i_liked(conn, make_client):
    from taste import recommend

    fake = FakeTmdb()
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None)
    # My average is 7.125, so Night Train (9) and Glass Sky (8) are the seeds.
    assert tmdb.film_seeds(conn) == [11, 13]
    assert tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None) == 2
    assert tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None) == 0  # cached
    recs, metrics = recommend.film_recommendations(conn)
    by_title = {r.title: r for r in recs}
    # Rated Already is a recommendation too, but I rated it: never suggested.
    assert set(by_title) == {"Example Candidate Film", "Example Second Pick"}
    pick = by_title["Example Candidate Film"]
    assert pick.badge == "On your watchlist"
    assert pick.url == "https://www.themoviedb.org/movie/21"
    assert pick.image_url == "https://image.tmdb.org/t/p/w342/poster21.jpg"
    assert pick.subtitle == "2022 · 1h 50m · Ida Director"
    assert pick.reasons == [
        "Because you loved Example Night Train, and you tend to enjoy drama films."
    ]
    assert pick.facts["tmdb_mean"] == 7.6 and metrics["rated"] == 4
    recommend.dismiss(conn, "film", "21")
    recs, _ = recommend.film_recommendations(conn)
    assert [r.title for r in recs] == ["Example Second Pick"]


def test_a_sync_fetches_details_only_for_titles_on_my_account(conn, make_client):
    fake = FakeTmdb()
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None)
    # A candidate known only from a recommendations list, never fetched in detail.
    conn.execute("UPDATE stg_tmdb_movies SET fetched_at = '' WHERE tmdb_id = 22")
    fake.calls.clear()
    tmdb.sync(conn, client, CFG, out=lambda m: None)
    assert ("GET", "/3/movie/22") not in fake.calls  # that's the refresh's job, not a sync's


def test_film_picks_skip_unreleased_films_and_doubt_thin_averages(conn, make_client):
    from datetime import date

    from taste import recommend

    fake = FakeTmdb()
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None)
    today = date(2026, 10, 16)
    conn.execute("UPDATE stg_tmdb_movies SET release_date = '2026-12-18' WHERE tmdb_id = 22")
    recs, _ = recommend.film_recommendations(conn, today)
    assert [r.title for r in recs] == ["Example Candidate Film"]  # not out yet: left out

    def predicted(votes: int) -> float:
        conn.execute(
            "UPDATE stg_tmdb_movies SET vote_average = 9.5, vote_count = ? WHERE tmdb_id = 21",
            (votes,),
        )
        recs, _ = recommend.film_recommendations(conn, today)
        return next(r.score for r in recs if r.item_key == "21")

    # 9.5 from 300 early votes counts for much less than 9.5 from 30,000.
    assert predicted(300) < predicted(30_000) - 0.5
    pick = next(r for r in recommend.film_recommendations(conn, today)[0] if r.item_key == "21")
    assert pick.facts["tmdb_mean"] == 9.5  # the card still shows TMDB's own number


def test_a_thin_average_is_pulled_toward_the_typical_one():
    from taste import recommend

    typical = 6.9
    assert recommend.shrunk(9.1, 0, typical) == typical
    assert recommend.shrunk(9.1, recommend.FILM_PRIOR_VOTES, typical) == (9.1 + typical) / 2
    assert abs(recommend.shrunk(9.1, 1_000_000, typical) - 9.1) < 0.01


def test_film_caution_is_a_setting(conn, make_client):
    from datetime import date

    from taste import recommend, settings

    fake = FakeTmdb()
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda m: None)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda m: None)
    conn.execute(
        "UPDATE stg_tmdb_movies SET vote_average = 9.5, vote_count = 300 WHERE tmdb_id = 21"
    )
    today = date(2026, 10, 16)

    def pick() -> float:
        recs, _ = recommend.film_recommendations(conn, today)
        return next(r.score for r in recs if r.item_key == "21")

    cautious = pick()
    settings.set_value(conn, "film_prior_votes", 0)  # trust every average as it is
    assert pick() > cautious + 0.5
    assert "film_prior_votes" in recommend.REC_SETTINGS  # changing it recomputes the lists
