"""What the Dashboard shows: recent shows, albums on repeat, scores, and genre lean.

Qt-free so it can be tested without a window. Everything reads from the core
layer and the report views, except the NSFW rating, which only staging keeps.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

ON_REPEAT_DAYS = 30


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
    fallback the MAL sync already stores on the watch_finished event).
    """
    rows = conn.execute(
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
        "LEFT JOIN stg_mal_anime s ON CAST(s.mal_anime_id AS TEXT) = x.external_id "
        "WHERE e.source = 'mal' AND e.event_type = 'watch_finished' "
        "ORDER BY e.occurred_at_utc DESC, i.title LIMIT ?",
        (limit,),
    ).fetchall()
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
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    until = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT al.title, c.name, COUNT(*) AS plays, img.url "
        "FROM core_behavior_events e "
        "JOIN core_item_links ln ON ln.child_item_id = e.item_id AND ln.link_type = 'appears_on' "
        "JOIN core_items al ON al.item_id = ln.parent_item_id AND al.media_type = 'album' "
        "JOIN core_item_creators ic ON ic.item_id = al.item_id AND ic.role = 'artist' "
        "JOIN core_creators c ON c.creator_id = ic.creator_id "
        "LEFT JOIN core_item_images img ON img.item_id = al.item_id AND img.kind = 'cover' "
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' "
        "  AND e.occurred_at_utc > ? AND e.occurred_at_utc <= ? "
        "GROUP BY al.item_id, al.title, c.name, img.url "
        "ORDER BY plays DESC, al.title LIMIT ?",
        (since, until, limit),
    ).fetchall()
    return [Album(title=r[0], artist=r[1], plays=r[2], cover_url=r[3]) for r in rows]


def genre_lean(conn: sqlite3.Connection, n: int = 3) -> tuple[list[GenreLean], list[GenreLean]]:
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
