"""What the Reports page draws. Qt-free; reads the report views and core.

The page shows charts only. Every number behind them is in the report views,
which the page exports as CSV (taste.reports).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, quantiles
from zoneinfo import ZoneInfo

from taste import overview, settings

MONTHS_SHOWN = 24
TOP_SHOWN = 10
BAND_WIDTH = 0.5  # MAL mean points per band of the score chart
MIDDLE_MIN_SHOWS = 4  # fewer than this and a band's middle half says nothing


@dataclass(frozen=True)
class Tile:
    value: str
    caption: str
    kind: str  # "anime" or "music": which accent color


@dataclass(frozen=True)
class Band:
    """My scores for shows whose MAL mean falls in [mal_from, mal_to)."""

    mal_from: float
    mal_to: float
    shows: int
    average: float
    middle: tuple[float, float] | None  # 25th to 75th percentile of my scores


@dataclass
class ReportData:
    tiles: list[Tile]
    bands: list[Band]
    usual: float | None  # my average difference from the MAL mean
    generous: list[overview.GenreLean]
    harsh: list[overview.GenreLean]
    drops: list[int]  # dropped shows by how far in I stopped: 0 to 9%, 10 to 19%, ...
    months: list[tuple[str, int]]  # ("YYYY-MM", plays), no gaps
    top_artists: list[tuple[str, int]]
    top_tracks: list[tuple[str, str, int]]  # track, artist, plays
    hours: list[int]  # 24, local time
    weekdays: list[tuple[str, int]]  # Sunday first
    partial_month: bool = False  # the last month is the one in progress
    scope_note: str = ""
    mixed_scopes: bool = False
    tz_name: str = ""


def score_bands(points: list[tuple[float, float]]) -> list[Band]:
    """(MAL mean, my score) pairs, averaged per BAND_WIDTH of the MAL mean."""
    groups: dict[float, list[float]] = {}
    for community, mine in points:
        start = math.floor(community / BAND_WIDTH) * BAND_WIDTH
        groups.setdefault(start, []).append(mine)
    bands = []
    for start in sorted(groups):
        scores = groups[start]
        middle = None
        if len(scores) >= MIDDLE_MIN_SHOWS:
            q1, _, q3 = quantiles(scores, n=4, method="inclusive")
            middle = (round(q1, 2), round(q3, 2))
        bands.append(Band(start, start + BAND_WIDTH, len(scores), round(mean(scores), 2), middle))
    return bands


def load(conn: sqlite3.Connection, now: datetime | None = None) -> ReportData:
    summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
    scored = summary["shows_scored"] if summary else 0
    plays = conn.execute(
        "SELECT COUNT(*) FROM core_behavior_events WHERE source = 'lastfm' AND event_type = 'play'"
    ).fetchone()[0]
    weekdays = [
        (r["weekday"], r["plays"])
        for r in conn.execute(
            "SELECT weekday, plays FROM rpt_lastfm_by_weekday ORDER BY local_weekday_num"
        )
    ]
    busiest = max(weekdays, key=lambda d: d[1], default=("-", 0))
    tiles = [
        Tile(f"{scored:,}", "scored shows", "anime"),
        Tile(
            f"{summary['avg_diff'] + 0.0:+.2f}" if scored else "-",
            "points vs the MAL average",
            "anime",
        ),
        Tile(f"{plays:,}", "Last.fm plays", "music"),
        Tile(busiest[0] if busiest[1] else "-", "busiest day", "music"),
    ]

    drops = [0] * 10
    for (pct,) in conn.execute(
        "SELECT pct_complete FROM rpt_mal_dropped_on_hold "
        "WHERE list_status = 'dropped' AND pct_complete IS NOT NULL"
    ):
        drops[min(int(pct // 10), 9)] += 1

    generous, harsh = overview.genre_lean(conn, n=TOP_SHOWN)
    scope = conn.execute("SELECT * FROM rpt_lastfm_scope").fetchone()
    tz_name = settings.get(conn, "timezone")
    months = _months(conn)
    this_month = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    return ReportData(
        tiles=tiles,
        bands=score_bands(
            [
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT community_mean, my_score FROM rpt_mal_score_vs_community"
                )
            ]
        ),
        usual=summary["avg_diff"] + 0.0 if scored else None,
        generous=generous,
        harsh=harsh,
        drops=drops,
        months=months,
        partial_month=bool(months) and months[-1][0] == f"{this_month:%Y-%m}",
        top_artists=[
            (r[0], r[1])
            for r in conn.execute(
                "SELECT artist, plays FROM rpt_lastfm_top_artists_all_time "
                "ORDER BY play_rank LIMIT ?",
                (TOP_SHOWN,),
            )
        ],
        top_tracks=[
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT track, artist, plays FROM rpt_lastfm_top_tracks_all_time "
                "ORDER BY play_rank LIMIT ?",
                (TOP_SHOWN,),
            )
        ],
        hours=[
            r[0] for r in conn.execute("SELECT plays FROM rpt_lastfm_by_hour ORDER BY local_hour")
        ],
        weekdays=[(d[:3], p) for d, p in weekdays],
        scope_note=settings.lastfm_scope_note(conn),
        mixed_scopes=bool(scope and scope["capture_scopes"] == "mixed"),
        tz_name=tz_name,
    )


def _months(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Plays per local month for the last MONTHS_SHOWN months with any, gaps as zero."""
    counts = dict(
        conn.execute(
            "SELECT local_month, COUNT(*) FROM rpt_lastfm_plays_local GROUP BY local_month"
        ).fetchall()
    )
    if not counts:
        return []
    first, last = min(counts), max(counts)
    year, month = int(first[:4]), int(first[5:7])
    months = []
    while f"{year:04d}-{month:02d}" <= last:
        key = f"{year:04d}-{month:02d}"
        months.append((key, counts.get(key, 0)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months[-MONTHS_SHOWN:]
