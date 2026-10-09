"""What the Dashboard shows: made-up MAL and Last.fm data through the real sync code."""

from datetime import datetime, timedelta, timezone

import pytest

from taste import local_time, overview, settings
from taste.sources import lastfm, mal
from tests.fake_recs import (
    LASTFM_CDN,
    MAL_CDN,
    NOW,
    FakeMal,
    anime,
    list_entry,
    listening_history,
    scrobble,
)
from tests.test_enrich import LASTFM_SETTINGS, MAL_SETTINGS, NOW_DT, quiet
from tests.test_lastfm_sync import FakeLastfm

DAY = 86400


def finished(node, score, finish_date=None, status="completed"):
    entry = list_entry(node, status, score)
    if finish_date:
        entry["list_status"]["finish_date"] = finish_date
    return entry


ENTRIES = [
    finished(anime(101, "Example Drama A", 8.0, ["Drama"], []), 9, "2026-09-30"),
    finished(anime(102, "Example Comedy B", 7.0, ["Comedy"], []), 5, "2025-12-01"),
    finished(anime(103, "Example Mixed C", 7.5, ["Drama", "Comedy"], []), 7, "2026-10-05"),
    finished(anime(106, "Example Action F", 7.6, ["Action"], []), 8),  # no date: last edit
    finished(anime(104, "Example Plan Show", 7.8, ["Drama"], []), 0, status="plan_to_watch"),
    finished(anime(206, "Example Adult Show", 7.5, ["Hentai"], [], nsfw="black"), 0, "2026-10-06"),
]


@pytest.fixture
def anime_world(conn, make_client):
    client, _ = make_client(FakeMal(entries=ENTRIES, details={}))
    assert mal.sync(conn, client, MAL_SETTINGS, out=quiet).status == "success"
    return conn


@pytest.fixture
def music_world(conn, make_client):
    server = FakeLastfm()
    server.scrobbles = listening_history() + [
        scrobble("Example Band", f"Track {i}", "Second Album", NOW - 5 * DAY - i) for i in range(4)
    ]
    client, _ = make_client(server)
    lastfm.sync(conn, client, LASTFM_SETTINGS, out=quiet, now=lambda: NOW + 1)
    return conn


def test_recently_finished_is_newest_first_with_scores_and_posters(anime_world):
    shows = overview.recently_finished(anime_world, limit=5)
    assert [s.title for s in shows] == [
        "Example Adult Show",
        "Example Mixed C",
        "Example Drama A",
        "Example Action F",  # finish date unknown: the last edit (2026-01-01) stands in
        "Example Comedy B",
    ]
    mixed = shows[1]
    assert (mixed.my_score, mixed.community_mean) == (7, 7.5)
    assert mixed.poster_url == MAL_CDN.format(103)
    # Not scored, and MAL rates it NSFW: no score, and the placeholder instead of the poster.
    assert shows[0].my_score is None
    assert shows[0].poster_url is None
    assert len(overview.recently_finished(anime_world, limit=2)) == 2


def test_score_distribution_counts_each_score_from_1_to_10(anime_world):
    assert overview.score_distribution(anime_world) == [0, 0, 0, 0, 1, 0, 1, 1, 1, 0]


def test_score_distribution_is_all_zero_before_a_sync(conn):
    assert overview.score_distribution(conn) == [0] * 10


def test_genre_lean_ranks_against_the_community_without_overlap(anime_world):
    settings.set_value(anime_world, "genre_min_sample", 1)
    # vs the community: Action +0.40, Drama +0.25, Comedy -1.25 (Hentai has no score).
    generous, harsh = overview.genre_lean(anime_world, n=2)
    assert [(g.genre, g.avg_diff) for g in generous] == [("Action", 0.4), ("Drama", 0.25)]
    assert [(g.genre, g.avg_diff) for g in harsh] == [("Comedy", -1.25)]
    assert harsh[0].shows_scored == 2
    # Fewer genres than 2n: the lists split what's there instead of one taking all.
    generous, harsh = overview.genre_lean(anime_world, n=3)
    assert [g.genre for g in generous] == ["Action", "Drama"]
    assert [g.genre for g in harsh] == ["Comedy"]


def test_genre_lean_respects_the_minimum_sample(anime_world):
    assert overview.genre_lean(anime_world) == ([], [])  # default minimum is 5 shows


def test_on_repeat_ranks_albums_by_plays_in_the_window(music_world):
    albums = overview.on_repeat(music_world, NOW_DT + timedelta(seconds=1))
    # Old Days (400 days ago) is outside the 30 days; plays without an album don't count.
    assert [(a.title, a.artist, a.plays, a.cover_url) for a in albums] == [
        ("Seed One LP", "Example Seed One", 10, LASTFM_CDN.format("seedone")),
        ("Second Album", "Example Band", 4, None),
    ]
    assert len(overview.on_repeat(music_world, NOW_DT, limit=1)) == 1
    later = overview.on_repeat(music_world, NOW_DT + timedelta(days=31))
    assert later == []


@pytest.mark.parametrize(
    ("utc_text", "expected"),
    [
        ("2026-10-09 05:52:36", "today at 10:52 PM"),  # Phoenix is UTC-7: Oct 8
        ("2026-10-08 05:00:00", "yesterday at 10:00 PM"),
        ("2026-10-01 19:05:00", "Oct 1 at 12:05 PM"),
        ("2025-12-31 20:00:00", "Dec 31, 2025"),
    ],
)
def test_describe_says_when_in_plain_words(utc_text, expected):
    now = datetime(2026, 10, 9, 6, 30, tzinfo=timezone.utc)  # Oct 8, 11:30 PM in Phoenix
    assert local_time.describe(utc_text, "America/Phoenix", now) == expected
