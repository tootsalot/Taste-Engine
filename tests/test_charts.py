"""What the Reports page draws: made-up data through the real sync code and report views."""

from datetime import datetime, timezone

from taste import animestats, charts, musicstats
from tests.test_reports import add_play, add_scored_anime, seed_lastfm, seed_mal

WEEKDAYS = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}


def test_everything_is_empty_but_drawable_before_a_sync(conn):
    data = charts.load(conn, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert data.usual is None and data.generous == [] and data.harsh == []
    assert data.periods == {"week": [], "month": [], "year": []}
    assert data.partial == {"week": False, "month": False, "year": False}
    assert data.drops == [0] * 10
    assert animestats.load(conn).bands == [] and musicstats.load(conn).people == []


def test_anime_charts(conn, make_client):
    seed_mal(conn, make_client)
    data = charts.load(conn)
    anime = animestats.load(conn)
    assert anime.vs_mal == -1.64
    assert data.usual == -1.64  # where the genre chart marks "your usual"
    # One show in each half-point band of the MAL mean, from the fixture list.
    assert [(b.mal_from, b.shows, b.average) for b in anime.bands] == [
        (6.5, 1, 4.0),
        (7.0, 1, 5.0),
        (8.0, 1, 8.0),
    ]
    # The one dropped show stopped 25% of the way in: the 20 to 29% bucket.
    assert data.drops == [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]


def test_scores_are_averaged_per_half_point_of_the_mal_mean(conn):
    for genre, mine, mal in (("A", 6, 7.1), ("B", 7, 7.2), ("C", 7, 7.3), ("D", 8, 7.4)):
        add_scored_anime(conn, 1, genre, my_score=mine, community_mean=mal)
    add_scored_anime(conn, 1, "E", my_score=9, community_mean=8.6)
    bands = animestats.load(conn).bands
    assert [(b.mal_from, b.mal_to, b.shows, b.average) for b in bands] == [
        (7.0, 7.5, 4, 7.0),
        (8.5, 9.0, 1, 9.0),
    ]
    # The middle half of my scores in a band, once there are enough to say.
    assert bands[0].middle == (6.75, 7.25)
    assert bands[1].middle is None


def test_the_month_in_progress_is_marked(conn):
    add_play(conn, 1782000000)  # 2026-06 in Phoenix
    assert charts.load(conn, now=datetime(2026, 6, 25, tzinfo=timezone.utc)).partial["month"]
    assert not charts.load(conn, now=datetime(2026, 8, 1, tzinfo=timezone.utc)).partial["month"]


def utc(*parts):
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


def test_plays_group_by_week_month_and_year(conn):
    # Phoenix is seven hours behind UTC all year. Weeks start on Sunday.
    add_play(conn, utc(2025, 12, 31, 20))  # Wednesday Dec 31: the week of Sunday Dec 28
    add_play(conn, utc(2026, 1, 4, 3), track="B")  # still Saturday Jan 3 there: same week
    add_play(conn, utc(2026, 1, 20, 12), track="C")  # Tuesday Jan 20: the week of Jan 18
    data = charts.load(conn, now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    assert data.periods["week"] == [
        ("2025-12-28", 2),
        ("2026-01-04", 0),  # weeks without plays still show, as zero
        ("2026-01-11", 0),
        ("2026-01-18", 1),
    ]
    assert data.periods["month"] == [("2025-12", 1), ("2026-01", 2)]
    assert data.periods["year"] == [("2025", 1), ("2026", 2)]
    assert data.partial == {"week": True, "month": True, "year": True}


def test_period_labels_for_the_axis_and_the_hover():
    assert charts.period_label("week", "2025-12-28") == ("Dec 28", "Week of Dec 28, 2025")
    assert charts.period_label("month", "2026-10") == ("Oct 26", "Oct 2026")
    assert charts.period_label("year", "2026") == ("2026", "2026")


def test_genre_lists_take_the_ends_of_the_ranking(conn):
    add_scored_anime(conn, 6, "Drama", my_score=9, community_mean=8)
    add_scored_anime(conn, 5, "Mecha", my_score=6, community_mean=8)
    data = charts.load(conn)
    assert [(g.genre, g.avg_diff) for g in data.generous] == [("Drama", 1.0)]
    assert [(g.genre, g.avg_diff) for g in data.harsh] == [("Mecha", -2.0)]


def test_music_charts(conn, make_client):
    seed_lastfm(conn, make_client)
    data = charts.load(conn)
    music = musicstats.load(conn)
    assert music.plays == 5 and music.busiest_day in WEEKDAYS
    assert (music.people[0].name, music.people[0].plays) == ("the example band", 3)
    assert music.tracks[0][0] == "signal" and music.tracks[0][2] == 2
    assert data.periods["month"] == [("2026-10", 5)]
    assert sum(music.by_hour) == 5 and sum(p for _, p in music.by_weekday) == 5
    assert "Desktop listening only" in data.scope_note


def test_months_without_plays_still_show_as_zero(conn):
    add_play(conn, 1767225600)  # 2025-12 in Phoenix
    add_play(conn, 1782000000, track="Later")  # 2026-06
    months = charts.load(conn).periods["month"]
    assert months[0] == ("2025-12", 1) and months[-1] == ("2026-06", 1)
    assert len(months) == 7 and sum(p for _, p in months) == 2
