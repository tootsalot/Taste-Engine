"""Local-time copies of event timestamps, in the profile's time zone.

SQLite has no time zone rules, so Python converts with zoneinfo (which handles
daylight saving) and stores the parts the reports group by. This replaces the
fixed UTC-7 offset from Phase 1, and keeps every SQLite date function out of
the report views.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from taste import settings


def refresh(conn: sqlite3.Connection) -> int:
    """Bring core_event_local_times up to date. Returns how many rows were (re)computed.

    Rows in a different time zone, or whose event time changed, are recomputed.
    Events without an exact time (date-only MAL events) are skipped.
    """
    tz_name = settings.get(conn, "timezone")
    conn.execute(
        "DELETE FROM core_event_local_times WHERE tz_name <> ? OR event_id IN ("
        "  SELECT l.event_id FROM core_event_local_times l JOIN core_behavior_events e "
        "  ON e.event_id = l.event_id "
        "  WHERE e.occurred_at_unix IS NULL OR e.occurred_at_unix <> l.occurred_at_unix)",
        (tz_name,),
    )
    missing = conn.execute(
        "SELECT e.event_id, e.occurred_at_unix FROM core_behavior_events e "
        "WHERE e.occurred_at_unix IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM core_event_local_times l WHERE l.event_id = e.event_id)"
    ).fetchall()
    if not missing:
        return 0
    zone = ZoneInfo(tz_name)
    rows = []
    for event_id, unix in missing:
        local = datetime.fromtimestamp(unix, timezone.utc).astimezone(zone)
        rows.append(
            (
                event_id,
                unix,
                tz_name,
                local.strftime("%Y-%m-%d %H:%M:%S"),
                local.strftime("%Y-%m-%d"),
                local.year,
                local.strftime("%Y-%m"),
                local.hour,
                (local.weekday() + 1) % 7,  # Python: Monday = 0. Reports: Sunday = 0.
            )
        )
    conn.executemany(
        "INSERT INTO core_event_local_times (event_id, occurred_at_unix, tz_name, local_ts, "
        "local_date, local_year, local_month, local_hour, local_weekday_num) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def describe(utc_text: str, tz_name: str, now: datetime | None = None) -> str:
    """A stored UTC time in plain words, in the profile's time zone.

    "today at 10:52 PM", "yesterday at 9:05 AM", "Oct 1 at 12:05 PM", or
    "Dec 31, 2025" for another year.
    """
    zone = ZoneInfo(tz_name)
    moment = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    local = moment.astimezone(zone)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    day = f"{local.strftime('%b')} {local.day}"
    if local.date() == today:
        return f"today at {clock}"
    if (today - local.date()).days == 1:
        return f"yesterday at {clock}"
    if local.year == today.year:
        return f"{day} at {clock}"
    return f"{day}, {local.year}"
