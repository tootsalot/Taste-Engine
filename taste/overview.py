"""What the Dashboard shows: recent shows, albums on repeat, scores, and genre lean.

Qt-free so it can be tested without a window. Everything reads from the core
layer and the report views, except the NSFW rating, which only staging keeps.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from taste import activity, bookstats, screenstats

ON_REPEAT_DAYS = 30

# The Dashboard runs these on the UI thread, so they must stay index lookups
# (tests/test_overview.py checks the plans).
RECENTLY_FINISHED_SQL = (
    "SELECT i.title, e.occurred_at_utc, r.raw_score, cr.mean_score, img.url, "
    "  s.nsfw_rating, i.title_alt "
    "FROM core_behavior_events e "
    "JOIN core_items i ON i.item_id = e.item_id "
    "LEFT JOIN core_ratings r ON r.item_id = e.item_id AND r.source = e.source "
    "LEFT JOIN core_community_ratings cr ON cr.item_id = e.item_id AND cr.source = e.source "
    "LEFT JOIN core_item_images img "
    "  ON img.item_id = e.item_id AND img.source = e.source AND img.kind = 'poster' "
    "LEFT JOIN core_item_external_ids x "
    "  ON x.item_id = e.item_id AND x.source = e.source AND x.id_type = 'mal_anime_id' "
    "LEFT JOIN stg_mal_anime s ON s.mal_anime_id = CAST(x.external_id AS INTEGER) "
    "WHERE e.source = 'mal' AND e.event_type = 'watch_finished' "
    "  AND (? = 0 OR s.nsfw_rating IS NULL OR s.nsfw_rating <> 'black') "
    "ORDER BY e.occurred_at_utc DESC, i.title LIMIT ?"
)
ON_REPEAT_SQL = (
    "SELECT al.title, c.name, COUNT(*) AS plays, img.url "
    "FROM core_behavior_events e "
    "JOIN core_item_links ln ON ln.child_item_id = e.item_id AND ln.link_type = 'appears_on' "
    "JOIN core_items al ON al.item_id = ln.parent_item_id AND al.media_type = 'album' "
    "JOIN core_item_creators ic ON ic.item_id = al.item_id AND ic.role = 'artist' "
    "JOIN core_creators c ON c.creator_id = ic.creator_id "
    "LEFT JOIN core_item_images img ON img.item_id = al.item_id AND img.kind = 'cover' "
    "WHERE e.source = 'lastfm' AND e.event_type = 'play' "
    "  AND e.occurred_at_unix > ? AND e.occurred_at_unix <= ? "
    "GROUP BY al.item_id, al.title, c.name, img.url "
    "ORDER BY plays DESC, al.title LIMIT ?"
)


@dataclass(frozen=True)
class FinishedShow:
    title: str  # the English title when MAL has one, as on For You
    main_title: str  # MAL's main title (usually romaji)
    finished_at: str  # UTC text; date-only finish dates read as midnight
    my_score: float | None
    community_mean: float | None
    poster_url: str | None  # None for shows MAL rates NSFW


@dataclass(frozen=True)
class Album:
    title: str
    artist: str
    plays: int
    cover_url: str | None


@dataclass(frozen=True)
class GenreLean:
    genre: str
    shows_scored: int
    my_avg_score: float
    community_avg_score: float
    avg_diff: float  # my score minus the community mean, averaged


def recently_finished(conn: sqlite3.Connection, limit: int = 6) -> list[FinishedShow]:
    """Shows I finished, newest first.

    A completed show without a finish date uses its last list edit instead (the
    fallback the MAL sync already stores on the watch_finished event). Adult shows are
    left out while "Include NSFW anime" is off.
    """
    hide = int(activity.hide_nsfw(conn))
    rows = conn.execute(RECENTLY_FINISHED_SQL, (hide, limit)).fetchall()
    return [
        FinishedShow(
            title=row[6] or row[0],
            main_title=row[0],
            finished_at=row[1],
            my_score=row[2],
            community_mean=row[3],
            poster_url=None if row[5] == "black" else row[4],
        )
        for row in rows
    ]


def score_distribution(conn: sqlite3.Connection) -> list[int]:
    """How many shows I gave each score, 1 to 10 (index 0 is a 1)."""
    counts = [0] * 10
    for score, count in conn.execute(
        "SELECT CAST(raw_score AS INTEGER), COUNT(*) FROM core_ratings "
        "WHERE source = 'mal' GROUP BY CAST(raw_score AS INTEGER)"
    ):
        if 1 <= score <= 10:
            counts[score - 1] = count
    return counts


def on_repeat(
    conn: sqlite3.Connection, now: datetime, days: int = ON_REPEAT_DAYS, limit: int = 4
) -> list[Album]:
    """Albums with the most Last.fm plays in the last `days` days.

    A play counts for every album its track is linked to, and plays without an
    album aren't counted. `now` must be timezone-aware.
    """
    until = int(now.timestamp())
    since = int((now - timedelta(days=days)).timestamp())
    rows = conn.execute(ON_REPEAT_SQL, (since, until, limit)).fetchall()
    return [Album(title=r[0], artist=r[1], plays=r[2], cover_url=r[3]) for r in rows]


def genre_lean(conn: sqlite3.Connection, n: int = 10) -> tuple[list[GenreLean], list[GenreLean]]:
    """(most generous, harshest) genres against the MAL community, n of each.

    Same numbers as the Reports page, so only genres with at least the minimum
    sample are in it. A genre never appears in both lists: with fewer than 2n
    genres, the generous list takes the larger half.
    """
    rows = [
        GenreLean(
            genre=r[0],
            shows_scored=r[1],
            my_avg_score=r[2],
            community_avg_score=r[3],
            avg_diff=r[4] + 0.0,  # SQLite's -0.0 becomes 0.0
        )
        for r in conn.execute(
            "SELECT genre, shows_scored, my_avg_score, community_avg_score, avg_diff "
            "FROM rpt_mal_genre_vs_community ORDER BY avg_diff DESC, genre"
        )
    ]
    split = min(n, (len(rows) + 1) // 2)
    return rows[:split], list(reversed(rows[split:]))[:n]


# ---------------------------------------------------------------------------
# You vs the crowd: one number per source, how far I sit from its crowd
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrowdNumber:
    kind: str  # anime, screen, books, music: the color it's drawn in
    value: str  # "-0.88", "-1.1★", "40%"
    caption: str  # "vs MAL · 343 shows"
    tip: str  # what it means, on hover


def artist_play_counts(conn: sqlite3.Connection) -> list[int]:
    """Plays per artist, most first. Your taste and You vs the crowd both read it, so the
    Dashboard counts once and hands it to each."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT COUNT(*) AS n FROM stg_lastfm_scrobbles GROUP BY lower(artist_name) "
            "ORDER BY n DESC"
        )
    ]


def vs_crowd(
    conn: sqlite3.Connection,
    *,
    books: bookstats.BookStats | None = None,
    counts: list[int] | None = None,
) -> list[CrowdNumber]:
    """MAL, TMDB, and Hardcover: my average against the crowd's for the same titles.
    Last.fm has no crowd score, so it's how much I play beyond my ten most played
    artists. A source with nothing synced is left out."""
    found: list[CrowdNumber] = []
    summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
    if summary and summary["shows_scored"]:
        found.append(
            CrowdNumber(
                "anime",
                f"{summary['avg_diff'] + 0.0:+.2f}",
                f"vs MAL · {summary['shows_scored']:,} shows",
                f"Points {'below' if summary['avg_diff'] < 0 else 'above'} the MAL community "
                f"on average. You go lower than MAL on {summary['share_scored_below']:.0%} of "
                f"your scored shows and higher on {summary['share_scored_above']:.0%}. Lower and "
                "higher compare your score with MAL's average rounded to a whole number, the "
                "closest score you could give.",
            )
        )
    screen = screenstats.load(conn)
    if screen.film_diff is not None:
        found.append(
            CrowdNumber(
                "screen",
                f"{screen.film_diff:+.1f}",
                f"vs TMDB · {screen.films:,} films",
                "Your film ratings against TMDB's average for the same films, out of 10.",
            )
        )
    books = books or bookstats.load(conn)
    if books.vs_hardcover is not None:
        found.append(
            CrowdNumber(
                "books",
                f"{books.vs_hardcover:+.1f}★",
                f"vs Hardcover · {books.unique:,} books",
                "Your stars against Hardcover's average for the same books, out of 5.",
            )
        )
    counts = artist_play_counts(conn) if counts is None else counts
    if counts:
        outside = 1 - sum(counts[:10]) / sum(counts)
        found.append(
            CrowdNumber(
                "music",
                f"{outside:.0%}",
                "of plays outside your top 10 artists",
                "Last.fm has no crowd score to compare with, so this is how far your listening "
                "goes beyond your ten most played artists.",
            )
        )
    return found
