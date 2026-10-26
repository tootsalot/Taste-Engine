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

import base64
import binascii
import json
import sqlite3
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any

from taste.db import TIMESTAMP_FORMAT, transaction, utc_now

# Request parameters that must never be stored.
SECRET_PARAMS = {
    "api_key", "api_sig", "sk", "token", "client_id", "request_token", "access_token",
    "refresh_token", "code", "code_verifier",
}  # fmt: skip

# Staging columns that point at raw pages. A referenced page is never deleted.
REFERENCES = [
    ("stg_lastfm_scrobbles", "raw_page_id"),
    ("stg_mal_anime", "last_raw_page_id"),
    ("stg_mal_list_entries", "last_raw_page_id"),
    ("stg_mal_anime_details", "raw_page_id"),
    ("stg_hardcover_user_books", "last_raw_page_id"),
    ("stg_hardcover_reads", "last_raw_page_id"),
    ("stg_hardcover_books", "last_raw_page_id"),
    ("stg_lastfm_artist_info", "raw_page_id"),
    ("stg_tmdb_account_ratings", "last_raw_page_id"),
    ("stg_tmdb_movies", "raw_page_id"),
    ("stg_tmdb_tv", "raw_page_id"),
    ("stg_tmdb_tv_recommendations", "raw_page_id"),
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
        "SELECT raw_page_id, payload, payload_encoding FROM raw_api_pages "
        "WHERE source = ? AND endpoint = ? AND request_params = ? "
        "ORDER BY raw_page_id DESC LIMIT 1",
        (source, endpoint, request_params),
    ).fetchone()
    if latest is not None and read_page(latest[1], latest[2]) == payload:
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


MAX_PAGE_BYTES = 64 << 20


def read_page(payload: str, encoding: str = "") -> Any:
    """Read plain JSON or an older page compressed as zlib and base64."""
    if encoding == "zlib-b64":
        try:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(
                base64.b64decode(payload, validate=True), MAX_PAGE_BYTES + 1
            )
            if len(decoded) > MAX_PAGE_BYTES or not decoder.eof or decoder.unused_data:
                raise ValueError("The compressed raw page is too large or damaged.")
            payload = decoded.decode("utf-8")
        except (binascii.Error, zlib.error, UnicodeError) as exc:
            raise ValueError("The compressed raw page is damaged.") from exc
    elif encoding:
        raise ValueError("Unknown raw page encoding.")
    return json.loads(payload)


def compress_old_pages(
    conn: sqlite3.Connection, now: datetime | None = None, batch_size: int = 100
) -> int:
    """Compress pages older than three days in short transactions. Pruning is unchanged."""
    if batch_size < 1:
        raise ValueError("The batch size must be positive.")
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=3)).strftime(TIMESTAMP_FORMAT)
    after, changed = 0, 0
    while True:
        rows = conn.execute(
            "SELECT raw_page_id, payload FROM raw_api_pages WHERE payload_encoding = '' "
            "AND fetched_at < ? AND raw_page_id > ? ORDER BY raw_page_id LIMIT ?",
            (cutoff, after, batch_size),
        ).fetchall()
        if not rows:
            break
        encoded = []
        for page_id, payload in rows:
            body = payload.encode("utf-8")
            if len(body) > MAX_PAGE_BYTES:
                continue  # keep readable plain JSON rather than creating an unreadable page
            packed = base64.b64encode(zlib.compress(body)).decode("ascii")
            if len(packed) < len(body):
                encoded.append((packed, page_id, payload))
        with transaction(conn):
            for packed, page_id, payload in encoded:
                changed += conn.execute(
                    "UPDATE raw_api_pages SET payload = ?, payload_encoding = 'zlib-b64' "
                    "WHERE raw_page_id = ? AND payload_encoding = '' AND payload = ?",
                    (packed, page_id, payload),
                ).rowcount
        after = rows[-1][0]
    return changed


COMPACT_FREE_SHARE = 0.25


def compact(conn: sqlite3.Connection) -> bool:
    """Rewrite the database once a quarter or more of it is free space (pages compressed
    or pruned). SQLite reuses free space but never gives it back to the disk on its own."""
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    if not pages or free / pages < COMPACT_FREE_SHARE:
        return False
    try:
        conn.execute("VACUUM")
        # With WAL the rewrite lands in the -wal file; this copies it back and empties it,
        # or neither file would get smaller.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        return False  # VACUUM needs about the file's size in free disk, or it was busy
    return True
