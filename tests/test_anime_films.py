"""Japanese animated films stay in ratings but are suggested from Anime only."""

from copy import deepcopy

import pytest

from taste import core, recommend, settings
from taste.recommend import screen
from taste.sources import tmdb
from tests.fake_recs import DETAILS, FakeMal
from tests.fake_tmdb import CATALOG, RATINGS, FakeTmdb, movie
from tests.test_enrich import NOW_DT, run_enrich_mal, sync_mal
from tests.test_tmdb import CFG, client_for


@pytest.mark.parametrize(
    ("genres", "language", "expected"),
    ((["Animation"], "ja", True), (["Animation"], "en", False), (["Drama"], "ja", False)),
)
def test_film_anime_detection_needs_animation_and_japanese_language(genres, language, expected):
    # Film facts have no origin country, unlike TV facts.
    assert screen.is_anime({"genres": genres, "original_language": language}) is expected


@pytest.fixture
def library(conn, make_client):
    mal_details = deepcopy(DETAILS)
    mal_details[202]["num_scoring_users"] = 100_000
    mal = FakeMal(details=mal_details)
    sync_mal(conn, make_client, mal)
    run_enrich_mal(conn, make_client, mal)
    # The fixture records this identity explicitly before TMDB creates its item.
    # The two sources use different names; no title matching is involved.
    item = core.find_item(conn, "mal", "mal_anime_id", "202")
    core.add_item_external_id(conn, item, "tmdb", "tmdb_movie_id", "21")

    catalog = deepcopy(CATALOG)
    catalog[("movie", 11)].update(
        genres=[{"id": 16, "name": "Animation"}], original_language="ja", _recs=[21, 22, 24]
    )
    catalog[("movie", 21)].update(genres=[{"id": 16, "name": "Animation"}], original_language="ja")
    catalog[("movie", 22)]["genres"] = [{"id": 16, "name": "Animation"}]
    catalog[("movie", 24)] = movie(24, "Example Japanese Drama", 7.5, ["Drama"], "Jo Filmmaker")
    catalog[("movie", 24)]["original_language"] = "ja"
    fake = FakeTmdb(
        ratings=[row for row in RATINGS if row[0] == "movie"],
        catalog=catalog,
        watchlist=[("movie", 21)],
        favorites=[],
    )
    client, _ = client_for(make_client, fake)
    tmdb.sync(conn, client, CFG, out=lambda _: None, now=NOW_DT)
    tmdb.enrich_recommendations(conn, client, CFG, out=lambda _: None, now=NOW_DT)


@pytest.mark.parametrize("model", ("usual", "details", "similar", "blend"))
def test_every_film_model_leaves_anime_to_the_anime_list(conn, library, model):
    settings.set_value(conn, "rec_model_film", model)
    picks, metrics = recommend.film_recommendations(conn, NOW_DT.date())
    assert {pick.item_key for pick in picks} == {"22", "24"}
    assert metrics["rated"] == 4
    assert all(pick.kind == "film" for pick in picks)


def test_rated_anime_films_stay_in_film_training_rows_and_the_report(conn, library):
    typical = screen._typical_tmdb_average(conn)
    prior = settings.get(conn, "film_prior_votes")
    scored = screen._screen_scored(conn, typical, prior)
    report = [tuple(row) for row in conn.execute("SELECT * FROM rpt_tmdb_ratings ORDER BY item_id")]
    assert any(key == 11 and mine == 9.0 for key, mine, _ in scored)
    assert (
        conn.execute(
            "SELECT my_rating FROM rpt_tmdb_ratings WHERE title = 'Example Night Train'"
        ).fetchone()[0]
        == 9.0
    )
    assert core.find_item(conn, "tmdb", "tmdb_movie_id", "11") in {
        row[0] for row in conn.execute("SELECT item_id FROM core_ratings WHERE source = 'tmdb'")
    }
    recommend.compute_all(conn, NOW_DT, ("film", "everything"))
    assert screen._screen_scored(conn, typical, prior) == scored
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM rpt_tmdb_ratings ORDER BY item_id")
    ] == (report)


def test_mixed_list_gets_the_recorded_anime_film_once_from_anime(conn, library):
    assert core.find_item(conn, "mal", "mal_anime_id", "202") == core.find_item(
        conn, "tmdb", "tmdb_movie_id", "21"
    )
    recommend.compute_all(conn, NOW_DT, ("anime", "film", "everything"))
    assert "202" in {pick.item_key for pick in recommend.latest(conn, "anime")[0]}
    picks = recommend.latest(conn, "everything")[0]
    linked = [
        pick
        for pick in picks
        if (pick.kind, pick.facts["source_item_key"]) in (("anime", "202"), ("film", "21"))
    ]
    assert [(pick.kind, pick.facts["source_item_key"]) for pick in linked] == [("anime", "202")]
