"""Raw layer: every API response page, stored exactly as fetched.

Three rules keep it from growing without bound:
- A page identical to the latest stored page for the same request isn't stored
  again; the existing copy is reused.
- Callers drop pages that brought nothing new (`discard_if_unused`).
- `prune` removes pages past the retention period, except pages that staging
  still points to and the latest copy of each request, so everything can still
  be rebuilt from raw.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from taste.db import TIMESTAMP_FORMAT, utc_now

# Request parameters that must never be stored.
SECRET_PARAMS = {"api_key", "api_sig", "sk", "token", "client_id"}

# Staging columns that point at raw pages. A referenced page is never deleted.
REFERENCES = [
    ("stg_lastfm_scrobbles", "raw_page_id"),
    ("stg_mal_anime", "last_raw_page_id"),
    ("stg_mal_list_entries", "last_raw_page_id"),
]


def safe_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if k.lower() not in SECRET_PARAMS}


def store_page(
    conn: sqlite3.Connection,
    *,
    sync_run_id: int,
    source: str,
    endpoint: str,
    params: dict[str, Any],
    page_number: int | None,
    http_status: int,
    payload: Any,
) -> int:
    """Store a page and return its id, or the id of an identical latest copy."""
    request_params = json.dumps(safe_params(params), sort_keys=True)
    body = json.dumps(payload, ensure_ascii=False)
    latest = conn.execute(
        "SELECT raw_page_id, payload FROM raw_api_pages "
        "WHERE source = ? AND endpoint = ? AND request_params = ? "
        "ORDER BY raw_page_id DESC LIMIT 1",
        (source, endpoint, request_params),
    ).fetchone()
    if latest is not None and latest[1] == body:
        return latest[0]
    cursor = conn.execute(
        "INSERT INTO raw_api_pages (sync_run_id, source, endpoint, request_params, page_number, "
        "http_status, fetched_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sync_run_id,
            source,
            endpoint,
            request_params,
            page_number,
            http_status,
            utc_now(),
            body,
        ),
    )
    return cursor.lastrowid


def _unreferenced_sql(alias: str) -> str:
    return " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {table} r WHERE r.{column} = {alias}.raw_page_id)"
        for table, column in REFERENCES
    )


def discard_if_unused(conn: sqlite3.Connection, raw_page_id: int) -> bool:
    """Delete a page nothing points to (for example a page that brought nothing new)."""
    cursor = conn.execute(
        f"DELETE FROM raw_api_pages AS p WHERE p.raw_page_id = ? AND {_unreferenced_sql('p')}",
        (raw_page_id,),
    )
    return cursor.rowcount > 0


def prune(conn: sqlite3.Connection, retention_days: int, now: datetime | None = None) -> int:
    """Delete pages older than the retention period. Returns how many were deleted.

    Kept no matter how old: pages staging points to, and the latest copy of each
    request (same source, endpoint, and parameters). `retention_days` 0 keeps all.
    """
    if retention_days <= 0:
        return 0
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).strftime(TIMESTAMP_FORMAT)
    cursor = conn.execute(
        "DELETE FROM raw_api_pages AS p WHERE p.fetched_at < ? "
        f"AND {_unreferenced_sql('p')} "
        "AND p.raw_page_id <> ("
        "  SELECT MAX(q.raw_page_id) FROM raw_api_pages q "
        "  WHERE q.source = p.source AND q.endpoint = p.endpoint "
        "  AND q.request_params = p.request_params)",
        (cutoff,),
    )
    return cursor.rowcount
