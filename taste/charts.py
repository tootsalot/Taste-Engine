"""What the Reports page draws. Qt-free; reads the report views and core.

The page shows charts only. Every number behind them is in the report views,
which the page exports as CSV (taste.reports).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean, quantiles
from zoneinfo import ZoneInfo

from taste import overview, settings

PERIODS = ("week", "month", "year")
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
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
    periods: dict[str, list[tuple[str, int]]]  # plays per week, month, year (plays_by_period)
    partial: dict[str, bool]  # whether each series ends in the period in progress
    top_artists: list[tuple[str, int]]
    top_tracks: list[tuple[str, str, int]]  # track, artist, plays
    hours: list[int]  # 24, local time
    weekdays: list[tuple[str, int]]  # Sunday first
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
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name)).date()
    periods, partial = plays_by_period(conn, today)
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
        periods=periods,
        partial=partial,
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


def week_start(day: date) -> date:
    """The Sunday a week starts on, like the weekday chart."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _next(period: str, key: str) -> str:
    if period == "week":
        return (date.fromisoformat(key) + timedelta(days=7)).isoformat()
    if period == "month":
        year, month = int(key[:4]), int(key[5:7])
        return f"{year + 1:04d}-01" if month == 12 else f"{year:04d}-{month + 1:02d}"
    return str(int(key) + 1)


def plays_by_period(
    conn: sqlite3.Connection, today: date
) -> tuple[dict[str, list[tuple[str, int]]], dict[str, bool]]:
    """Plays per local week, month, and year, oldest first, gaps as zero.

    Keys are "YYYY-MM-DD" (the Sunday), "YYYY-MM", and "YYYY". The second dict says
    whether each series ends in the period still in progress. The page shows as many
    of the latest periods as fit its width.
    """
    counts: dict[str, dict[str, int]] = {p: {} for p in PERIODS}
    for day_text, plays in conn.execute(
        "SELECT substr(occurred_at_local, 1, 10), COUNT(*) FROM rpt_lastfm_plays_local "
        "GROUP BY substr(occurred_at_local, 1, 10)"
    ):
        day = date.fromisoformat(day_text)
        for period, key in (
            ("week", week_start(day).isoformat()),
            ("month", day_text[:7]),
            ("year", day_text[:4]),
        ):
            counts[period][key] = counts[period].get(key, 0) + plays
    current = {
        "week": week_start(today).isoformat(),
        "month": f"{today:%Y-%m}",
        "year": str(today.year),
    }
    series: dict[str, list[tuple[str, int]]] = {}
    for period in PERIODS:
        found = counts[period]
        filled = []
        if found:
            key, last = min(found), max(found)
            while key <= last:
                filled.append((key, found.get(key, 0)))
                key = _next(period, key)
        series[period] = filled
    partial = {p: bool(series[p]) and series[p][-1][0] == current[p] for p in PERIODS}
    return series, partial


def period_label(period: str, key: str) -> tuple[str, str]:
    """(axis label, hover name): ("Dec 28", "Week of Dec 28, 2025"), ("Oct 26", "Oct 2026")."""
    if period == "week":
        day = date.fromisoformat(key)
        name = f"{MONTH_NAMES[day.month - 1]} {day.day}"
        return name, f"Week of {name}, {day.year}"
    if period == "month":
        month = MONTH_NAMES[int(key[5:7]) - 1]
        return f"{month} {key[2:4]}", f"{month} {key[:4]}"
    return key, key
