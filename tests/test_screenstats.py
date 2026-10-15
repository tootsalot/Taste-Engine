"""The Films and TV page's numbers (taste.screenstats), on the made-up TMDB account."""

from taste import screenstats
from taste.sources import tmdb
from tests.fake_tmdb import FakeTmdb
from tests.test_tmdb import CFG, client_for


def synced_account(conn, make_client):
    client, _ = client_for(make_client, FakeTmdb())
    tmdb.sync(conn, client, CFG, out=lambda m: None)


def test_films_and_shows_counted_apart_and_genres_together(conn, make_client):
    synced_account(conn, make_client)
    s = screenstats.load(conn)
    assert (s.films, s.shows) == (4, 1)
    assert s.film_hours == round(4 * 110 / 60, 1)
    assert s.film_average == round((9.0 + 6.5 + 8.0 + 5.0) / 4, 2)
    assert s.rated[0].day >= s.rated[-1].day  # newest first
    assert sum(s.film_spread) == 4 and sum(s.tv_spread) == 1
    assert s.genres[0].name == "Drama"
    assert [p.name for p in s.people] == ["Ida Director"]  # the only one with 2+ films
    assert s.people[0].films == 2 and s.people[0].ratings == [9.0, 6.5]
    one_year = screenstats.load(conn, "2025")
    assert (one_year.films, one_year.shows) == (1, 0)
    assert screenstats.years(conn) == ["2026", "2025"]
