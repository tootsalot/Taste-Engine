"""What the Reports page draws: made-up data through the real sync code and report views."""

from datetime import datetime, timezone

from taste import charts
from tests.test_reports import add_play, add_scored_anime, seed_lastfm, seed_mal

WEEKDAYS = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}


def test_everything_is_empty_but_drawable_before_a_sync(conn):
    data = charts.load(conn)
    assert [(t.value, t.caption) for t in data.tiles] == [
        ("0", "scored shows"),
        ("-", "points vs the MAL average"),
        ("0", "Last.fm plays"),
        ("-", "busiest day"),
    ]
    assert data.bands == [] and data.months == [] and data.top_artists == []
    assert data.partial_month is False
    assert data.hours == [0] * 24
    assert [d for d, _ in data.weekdays] == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    assert data.drops == [0] * 10


def test_anime_charts(conn, make_client):
    seed_mal(conn, make_client)
    data = charts.load(conn)
    assert data.tiles[0].value == "3"
    assert data.tiles[1].value == "-1.64"
    assert data.usual == -1.64  # where the genre chart marks "your usual"
    # One show in each half-point band of the MAL mean, from the fixture list.
    assert [(b.mal_from, b.shows, b.average) for b in data.bands] == [
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
    bands = charts.load(conn).bands
    assert [(b.mal_from, b.mal_to, b.shows, b.average) for b in bands] == [
        (7.0, 7.5, 4, 7.0),
        (8.5, 9.0, 1, 9.0),
    ]
    # The middle half of my scores in a band, once there are enough to say.
    assert bands[0].middle == (6.75, 7.25)
    assert bands[1].middle is None


def test_the_month_in_progress_is_marked(conn):
    add_play(conn, 1782000000)  # 2026-06 in Phoenix
    assert charts.load(conn, now=datetime(2026, 6, 25, tzinfo=timezone.utc)).partial_month
    assert not charts.load(conn, now=datetime(2026, 8, 1, tzinfo=timezone.utc)).partial_month


def test_genre_lists_take_the_ends_of_the_ranking(conn):
    add_scored_anime(conn, 6, "Drama", my_score=9, community_mean=8)
    add_scored_anime(conn, 5, "Mecha", my_score=6, community_mean=8)
    data = charts.load(conn)
    assert [(g.genre, g.avg_diff) for g in data.generous] == [("Drama", 1.0)]
    assert [(g.genre, g.avg_diff) for g in data.harsh] == [("Mecha", -2.0)]


def test_music_charts(conn, make_client):
    seed_lastfm(conn, make_client)
    data = charts.load(conn)
    assert data.tiles[2].value == "5"
    assert data.tiles[3].value in WEEKDAYS
    assert data.top_artists[0] == ("the example band", 3)
    assert data.top_tracks[0][0] == "signal" and data.top_tracks[0][2] == 2
    assert data.months == [("2026-10", 5)]
    assert sum(data.hours) == 5 and sum(p for _, p in data.weekdays) == 5
    assert "Desktop listening only" in data.scope_note


def test_months_without_plays_still_show_as_zero(conn):
    add_play(conn, 1767225600)  # 2025-12 in Phoenix
    add_play(conn, 1782000000, track="Later")  # 2026-06
    months = charts.load(conn).months
    assert months[0] == ("2025-12", 1) and months[-1] == ("2026-06", 1)
    assert len(months) == 7 and sum(p for _, p in months) == 2
