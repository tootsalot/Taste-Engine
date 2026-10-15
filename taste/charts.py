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

from taste import activity, overview, settings

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
    detail: str = ""  # a second, smaller line: "+4 this month"
    trend: tuple[float, ...] = ()  # a tiny line beside the number, oldest first
    trend_tip: str = ""  # what the tiny line shows, on hover


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
    usual: float | None  # my average difference from the MAL mean
    generous: list[overview.GenreLean]
    harsh: list[overview.GenreLean]
    drops: list[int]  # dropped shows by how far in I stopped: 0 to 9%, 10 to 19%, ...
    periods: dict[str, list[tuple[str, int]]]  # plays per week, month, year (plays_by_period)
    partial: dict[str, bool]  # whether each series ends in the period in progress
    scope_note: str = ""
    mixed_scopes: bool = False
    tz_name: str = ""
    has_books: bool = False  # the Books page loads its own numbers (taste.bookstats)
    has_screen: bool = False  # the Films and TV page loads its own (taste.screenstats)


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
    """What every Reports page shares: the all-time anime parts, plays over time, and the
    Last.fm scope. Each page loads its own span's numbers (animestats, musicstats,
    bookstats, screenstats)."""
    summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
    scored = summary["shows_scored"] if summary else 0
    tz_name = settings.get(conn, "timezone")
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name)).date()

    drops = [0] * 10
    for (pct,) in conn.execute(
        "SELECT pct_complete FROM rpt_mal_dropped_on_hold "
        "WHERE list_status = 'dropped' AND pct_complete IS NOT NULL"
    ):
        drops[min(int(pct // 10), 9)] += 1

    generous, harsh = overview.genre_lean(conn, n=TOP_SHOWN)
    scope = conn.execute("SELECT * FROM rpt_lastfm_scope").fetchone()
    periods, partial = plays_by_period(conn, today)
    return ReportData(
        usual=summary["avg_diff"] + 0.0 if scored else None,
        generous=generous,
        harsh=harsh,
        drops=drops,
        periods=periods,
        partial=partial,
        scope_note=settings.lastfm_scope_note(conn),
        mixed_scopes=bool(scope and scope["capture_scopes"] == "mixed"),
        tz_name=tz_name,
        has_books=activity.has_books(conn),
        has_screen=activity.has_tmdb(conn),
    )


TREND_YEARS = 8
TREND_MIN_SHOWS = 3  # a year with fewer scored finishes than this isn't on the line


def diff_by_year(conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """My average difference from MAL by the year I finished the show, oldest first.

    Uses every finish date, guessed ones included: this is the all-time picture.
    """
    rows = conn.execute(
        "SELECT substr(e.occurred_at_utc, 1, 4) AS year, AVG(sc.score_diff), COUNT(*) "
        "FROM core_behavior_events e JOIN rpt_mal_score_vs_community sc "
        "ON sc.item_id = e.item_id WHERE e.source = 'mal' AND e.event_type = 'watch_finished' "
        "GROUP BY substr(e.occurred_at_utc, 1, 4) ORDER BY year"
    ).fetchall()
    found = [(r[0], round(r[1], 2)) for r in rows if r[2] >= TREND_MIN_SHOWS]
    return found[-TREND_YEARS:]


def hour_name(hour: int) -> str:
    """ "10 PM", "12 AM"."""
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


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
