"""The Music report page's numbers, for all time or one year. No Qt.

Plays are the ones that reached Last.fm (never all of my listening; the page says
which capture scope applies), counted on my local day. Hours come from Last.fm track
lengths, looked up for recent months only, so all time often has too few to say.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from taste import activity, settings

ALBUMS_SHOWN = 60
TRACKS_SHOWN = 10
PEOPLE_SHOWN = 6
PERSON_COVERS = 6
WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


@dataclass
class Artist:
    name: str
    plays: int
    albums: list[tuple[str, int, str | None]]  # (album, plays, cover), most played first
    months: list[int]  # plays per month across the span, oldest first


@dataclass
class MusicStats:
    span: str
    years: list[str]
    plays: int
    hours: activity.Hours
    artists: int
    new_artists: int | None  # first played in this year; None for all time
    busiest_hour: int | None
    busiest_day: str | None
    busiest_day_share: float  # of the plays
    newest_year: tuple[str, int] | None  # all time: (the newest year, its new artists)
    albums: list[tuple[str, str, int, str | None]]  # (album, artist, plays, cover)
    tracks: list[tuple[str, str, int]]  # (track, artist, plays)
    by_hour: list[int]  # 24, local time
    by_weekday: list[tuple[str, int]]  # Sunday first
    people: list[Artist] = field(default_factory=list)


def years(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(l.local_date, 1, 4) FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' ORDER BY 1 DESC"
    ).fetchall()
    return [r[0] for r in rows]


def _months(conn: sqlite3.Connection, names: list[str], start: str, end: str) -> dict:
    """Plays per month for these artists: {artist: {'YYYY-MM': plays}}."""
    if not names:
        return {}
    marks = ",".join("?" for _ in names)
    found: dict[str, dict[str, int]] = {}
    for name, month, plays in conn.execute(
        "SELECT c.name, substr(l.local_date, 1, 7), COUNT(*) FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        f"{activity.ARTIST_JOIN}"
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' "
        f"AND l.local_date >= ? AND l.local_date < ? AND c.name IN ({marks}) "
        "GROUP BY c.name, substr(l.local_date, 1, 7)",
        (start, end, *names),
    ):
        found.setdefault(name, {})[month] = plays
    return found


def _newest_year(conn: sqlite3.Connection, found: list[str]) -> tuple[str, int] | None:
    """The newest year with plays and how many artists were new in it."""
    if not found:
        return None
    year = found[0]
    return year, len(activity.new_artists(conn, f"{year}-01-01", f"{int(year) + 1}-01-01"))


def _from_week_start(conn: sqlite3.Connection, days: list[tuple[str, int]]) -> list:
    """Sunday first, or Monday first with the "Weeks start on" setting."""
    return days[1:] + days[:1] if settings.get(conn, "week_start") == "monday" else days


def load(
    conn: sqlite3.Connection, span: str = "all", found_years: list[str] | None = None
) -> MusicStats:
    """`found_years`: `years(conn)` when the caller already has it (it reads every play)."""
    start, end = activity.ALL_TIME if span == "all" else (
        f"{span}-01-01", f"{int(span) + 1}-01-01"
    )  # fmt: skip
    plays = activity.play_count(conn, start, end)
    artists = activity.top_artists(conn, start, end, n=1_000_000)
    by_hour = activity.plays_by_hour(conn, start, end)
    weekdays = [0] * 7
    for day, count in activity.plays_by_day(conn, start, end).items():
        weekdays[(date.fromisoformat(day).weekday() + 1) % 7] += count  # Sunday first
    albums = activity.top_albums(conn, start, end, n=ALBUMS_SHOWN * 4)
    top = artists[:PEOPLE_SHOWN]
    months = _months(conn, [name for name, _ in top], start, end)
    all_months = sorted({m for per in months.values() for m in per})
    people = [
        Artist(
            name=name,
            plays=count,
            albums=[(a, p, cover) for a, artist, p, cover in albums if artist == name][
                :PERSON_COVERS
            ],
            months=[months.get(name, {}).get(m, 0) for m in all_months],
        )
        for name, count in top
    ]
    busiest_hour = max(range(24), key=lambda h: (by_hour[h], -h)) if any(by_hour) else None
    busiest_day = (
        WEEKDAYS[max(range(7), key=lambda d: (weekdays[d], -d))] if any(weekdays) else None
    )
    found_years = years(conn) if found_years is None else found_years
    return MusicStats(
        span=span,
        years=found_years,
        plays=plays,
        hours=activity.music_hours(conn, start, end),
        artists=len(artists),
        new_artists=None if span == "all" else len(activity.new_artists(conn, start, end)),
        busiest_hour=busiest_hour,
        busiest_day=busiest_day,
        busiest_day_share=max(weekdays) / plays if plays else 0.0,
        newest_year=_newest_year(conn, found_years) if span == "all" else None,
        albums=albums[:ALBUMS_SHOWN],
        tracks=activity.top_tracks(conn, start, end, n=TRACKS_SHOWN),
        by_hour=by_hour,
        by_weekday=_from_week_start(conn, list(zip(WEEKDAYS, weekdays, strict=True))),
        people=people,
    )
