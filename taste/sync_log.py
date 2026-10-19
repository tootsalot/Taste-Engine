"""Bookkeeping for sync_runs: one row per sync attempt."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from taste.db import TIMESTAMP_FORMAT, utc_now

INTERRUPTED_MESSAGE = "interrupted (process ended before the run finished)"
# Longer than any sync takes (the slowest, a first MAL enrichment, is about 13 minutes).
STALE_RUN = timedelta(hours=6)


class SyncRun:
    """Tracks one sync attempt and its counters.

    Counters are written to the database with `save()`, which callers run inside
    the same transaction as the data they describe, so the counts never get ahead
    of what was actually committed.
    """

    def __init__(self, conn: sqlite3.Connection, source: str, mode: str) -> None:
        self.conn = conn
        self.source = source
        self.mode = mode
        self.pages_fetched = 0
        self.rows_fetched = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        # A run still marked 'running' long after it started means its process died
        # mid-run. A recent one may still be going in another process (the CLI next to
        # the app), so it's left alone.
        stale = (datetime.now(timezone.utc) - STALE_RUN).strftime(TIMESTAMP_FORMAT)
        conn.execute(
            "UPDATE sync_runs SET status = 'failed', ended_at = ?, error_message = ? "
            "WHERE source = ? AND status = 'running' AND started_at < ?",
            (utc_now(), INTERRUPTED_MESSAGE, source, stale),
        )
        cursor = conn.execute(
            "INSERT INTO sync_runs (source, mode, started_at, status) VALUES (?, ?, ?, 'running')",
            (source, mode, utc_now()),
        )
        self.run_id: int = cursor.lastrowid

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.conn.execute(
            "UPDATE sync_runs SET mode = ? WHERE sync_run_id = ?", (mode, self.run_id)
        )

    def save(self) -> None:
        self.conn.execute(
            "UPDATE sync_runs SET pages_fetched = ?, rows_fetched = ?, rows_inserted = ?, "
            "rows_updated = ? WHERE sync_run_id = ?",
            (
                self.pages_fetched,
                self.rows_fetched,
                self.rows_inserted,
                self.rows_updated,
                self.run_id,
            ),
        )

    def finish(self, status: str, error_message: str | None = None) -> None:
        self.save()
        self.conn.execute(
            "UPDATE sync_runs SET status = ?, ended_at = ?, error_message = ? "
            "WHERE sync_run_id = ?",
            (status, utc_now(), error_message, self.run_id),
        )
