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
