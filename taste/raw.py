"""Raw layer: every API response page, stored exactly as fetched."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from taste.db import utc_now

# Request parameters that must never be stored.
SECRET_PARAMS = {"api_key", "api_sig", "sk", "token", "client_id"}


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
    cursor = conn.execute(
        "INSERT INTO raw_api_pages (sync_run_id, source, endpoint, request_params, page_number, "
        "http_status, fetched_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sync_run_id,
            source,
            endpoint,
            json.dumps(safe_params(params), sort_keys=True),
            page_number,
            http_status,
            utc_now(),
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    return cursor.lastrowid
