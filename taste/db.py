"""SQLite connection, schema setup, and time helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent / "sql"

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a profile database, creating the file, tables, settings, and views if needed.

    The connection runs in autocommit mode. Use `transaction()` to group writes.
    """
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Lets the web app read while a sync thread writes.
    conn.execute("PRAGMA busy_timeout = 5000")
    apply_schema(conn)
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    # Local imports: both modules import this one.
    from taste import local_time, settings

    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    settings.ensure_defaults(conn)
    settings.apply_source_notes(conn)
    conn.executescript((SQL_DIR / "views.sql").read_text(encoding="utf-8"))
    local_time.refresh(conn)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block of writes as one transaction. Rolls back on any exception."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def unix_to_utc(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(TIMESTAMP_FORMAT)


def iso_to_utc(value: str) -> str:
    """Convert an ISO 8601 timestamp with an offset (as MAL sends) to our UTC text format."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def utc_to_unix(value: str) -> int:
    """Convert our UTC text format back to Unix seconds."""
    parsed = datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())
