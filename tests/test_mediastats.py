"""The Anime and Music report pages' numbers (animestats, musicstats), made-up data."""

from taste import animestats, musicstats, recap
from taste.activity import Period
from tests.test_recap import OCT_14, world  # noqa: F401 (the shared made-up world)


def test_anime_for_all_time_and_one_year(world):  # noqa: F811
    conn = world["conn"]
    everything = animestats.load(conn)
    september = recap.build(conn, Period("month", "2026-09"), OCT_14).anime
    assert len(everything.finished) >= september.finished
    assert everything.years and everything.years[0] == "2026"
    one_year = animestats.load(conn, "2026")
    assert {f.title for f in one_year.finished} <= {f.title for f in everything.finished}
    assert sum(everything.scores) >= sum(1 for f in one_year.finished if f.my_score)
    assert everything.finished == sorted(
        everything.finished, key=lambda f: (f.day, f.title), reverse=True
    )
    assert animestats.load(conn, "1999").finished == []


def test_music_for_all_time_and_one_year(world):  # noqa: F811
    conn = world["conn"]
    everything = musicstats.load(conn)
    year = musicstats.load(conn, "2026")
    assert everything.plays >= year.plays > 0
    assert sum(everything.by_hour) == everything.plays
    assert sum(p for _, p in everything.by_weekday) == everything.plays
    assert everything.new_artists is None and year.new_artists is not None
    assert everything.people[0].name == "Example Band"
    assert sum(everything.people[0].months) == everything.people[0].plays


def test_every_tile_has_a_number_behind_it(world):  # noqa: F811
    conn = world["conn"]
    anime = animestats.load(conn)
    assert anime.episodes == sum(f.episodes or 0 for f in anime.finished) > 0
    assert anime.top_genre == "Drama" and anime.top_genre_shows == 3  # of the 3 dated shows
    music = musicstats.load(conn)
    year = musicstats.load(conn, "2026")
    # All time has no "new" artists, so the tile shows the newest year's.
    assert music.newest_year == ("2026", year.new_artists)
    busiest = max(p for _, p in music.by_weekday)
    assert music.busiest_day_share == busiest / music.plays
    # No track lengths looked up yet: no hours, and nothing known to count.
    assert music.hours.hours is None and music.hours.known == 0


def test_weeks_can_start_on_monday(world):  # noqa: F811
    from datetime import date

    from taste import charts, settings

    conn = world["conn"]
    assert charts.week_start(date(2026, 10, 14)) == date(2026, 10, 11)  # a Sunday
    assert charts.week_start(date(2026, 10, 14), monday=True) == date(2026, 10, 12)
    sunday_first = [d for d, _ in musicstats.load(conn).by_weekday]
    settings.set_value(conn, "week_start", "monday")
    music = musicstats.load(conn)
    assert sunday_first[0] == "Sunday" and music.by_weekday[0][0] == "Monday"
    assert dict(music.by_weekday) == dict(musicstats.load(conn).by_weekday)  # same counts
    weeks, _ = charts.plays_by_period(conn, date(2026, 10, 14))
    assert all(date.fromisoformat(k).weekday() == 0 for k, _ in weeks["week"])  # Mondays
    assert recap.build(conn, Period("month", "2026-09"), OCT_14).monday_first
