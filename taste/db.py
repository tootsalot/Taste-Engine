"""SQLite connection, schema setup, and time helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent / "sql"

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def connect(path: str | Path, *, setup: bool = True) -> sqlite3.Connection:
    """Open a profile database, creating the file, tables, settings, and views if needed.

    `setup=False` skips the schema work, for connections opened after it's known
    to be done (the desktop app opens many short-lived ones). The connection runs in
    autocommit mode. Use `transaction()` to group writes.
    """
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait instead of failing when another connection holds the write lock, and use
    # WAL so the app can read while a sync thread writes.
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    if setup:
        apply_schema(conn)
    return conn


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS won't add
# them to an existing database, so they're added here. ALTER TABLE ... ADD ports to
# SQL Server; reading PRAGMA table_info is SQLite-only and stays in Python.
ADDED_COLUMNS = [
    ("stg_mal_anime", "main_picture_url", "TEXT"),
    ("stg_mal_anime", "nsfw_rating", "TEXT"),
    ("stg_lastfm_scrobbles", "image_url", "TEXT"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def apply_schema(conn: sqlite3.Connection) -> None:
    # Local imports: both modules import this one.
    from taste import local_time, settings

    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    _add_missing_columns(conn)
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
