"""Shared writes into the source-agnostic core_* tables.

Every source module calls these instead of writing core SQL itself, so the
upsert logic (the part that doesn't port directly to SQL Server) lives in one place.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from taste.db import utc_now

SOURCE_NATIVE = "source_native"


def normalize(raw: float, scale_min: float, scale_max: float) -> float:
    """Min-max normalize a score to 0..1, so the bottom of any scale is 0 and the top is 1."""
    return (raw - scale_min) / (scale_max - scale_min)


# ---------------------------------------------------------------------------
# Items and creators
# ---------------------------------------------------------------------------


def find_item(conn: sqlite3.Connection, source: str, id_type: str, external_id: str) -> int | None:
    row = conn.execute(
        "SELECT item_id FROM core_item_external_ids "
        "WHERE source = ? AND id_type = ? AND external_id = ?",
        (source, id_type, external_id),
    ).fetchone()
    return row[0] if row else None


def upsert_item(
    conn: sqlite3.Connection,
    *,
    source: str,
    id_type: str,
    external_id: str,
    media_type: str,
    title: str,
    title_alt: str | None = None,
    release_year: int | None = None,
    refresh: bool = True,
) -> int:
    """Return the item for a source ID, creating it if needed.

    With `refresh`, an existing item's descriptive fields are updated to the
    source's latest values (MAL titles do get corrected sometimes).
    """
    item_id = find_item(conn, source, id_type, external_id)
    now = utc_now()
    if item_id is None:
        item_id = conn.execute(
            "INSERT INTO core_items (media_type, title, title_alt, release_year, source, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (media_type, title, title_alt, release_year, source, now, now),
        ).lastrowid
        add_item_external_id(conn, item_id, source, id_type, external_id)
    elif refresh:
        conn.execute(
            "UPDATE core_items SET title = ?, title_alt = ?, release_year = ?, updated_at = ? "
            "WHERE item_id = ? AND (title IS NOT ? OR title_alt IS NOT ? OR release_year IS NOT ?)",
            (title, title_alt, release_year, now, item_id, title, title_alt, release_year),
        )
    return item_id


def add_item_external_id(
    conn: sqlite3.Connection, item_id: int, source: str, id_type: str, external_id: str
) -> None:
    """Map another source ID to an item. An ID already mapped elsewhere is left alone."""
    conn.execute(
        "INSERT INTO core_item_external_ids (source, id_type, external_id, item_id, match_method) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (source, id_type, external_id) DO NOTHING",
        (source, id_type, external_id, item_id, SOURCE_NATIVE),
    )


def upsert_creator(
    conn: sqlite3.Connection, *, source: str, id_type: str, external_id: str, name: str
) -> int:
    row = conn.execute(
        "SELECT creator_id FROM core_creator_external_ids "
        "WHERE source = ? AND id_type = ? AND external_id = ?",
        (source, id_type, external_id),
    ).fetchone()
    if row:
        return row[0]
    creator_id = conn.execute(
        "INSERT INTO core_creators (name, source, created_at) VALUES (?, ?, ?)",
        (name, source, utc_now()),
    ).lastrowid
    add_creator_external_id(conn, creator_id, source, id_type, external_id)
    return creator_id


def add_creator_external_id(
    conn: sqlite3.Connection, creator_id: int, source: str, id_type: str, external_id: str
) -> None:
    conn.execute(
        "INSERT INTO core_creator_external_ids "
        "(source, id_type, external_id, creator_id, match_method) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (source, id_type, external_id) DO NOTHING",
        (source, id_type, external_id, creator_id, SOURCE_NATIVE),
    )


def set_item_creators(
    conn: sqlite3.Connection, item_id: int, source: str, role: str, creator_ids: Iterable[int]
) -> None:
    """Replace this source's creators in one role for an item."""
    conn.execute(
        "DELETE FROM core_item_creators WHERE item_id = ? AND source = ? AND role = ?",
        (item_id, source, role),
    )
    for creator_id in creator_ids:
        link_item_creator(conn, item_id, creator_id, role, source)


def link_item_creator(
    conn: sqlite3.Connection, item_id: int, creator_id: int, role: str, source: str
) -> None:
    conn.execute(
        "INSERT INTO core_item_creators (item_id, creator_id, role, source) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (item_id, creator_id, role) DO NOTHING",
        (item_id, creator_id, role, source),
    )


def link_items(
    conn: sqlite3.Connection, parent_item_id: int, child_item_id: int, link_type: str, source: str
) -> None:
    conn.execute(
        "INSERT INTO core_item_links (parent_item_id, child_item_id, link_type, source) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (parent_item_id, child_item_id, link_type) DO NOTHING",
        (parent_item_id, child_item_id, link_type, source),
    )


def tag_id(conn: sqlite3.Connection, name: str, tag_kind: str) -> int:
    conn.execute(
        "INSERT INTO core_tags (name, tag_kind) VALUES (?, ?) "
        "ON CONFLICT (tag_kind, name) DO NOTHING",
        (name, tag_kind),
    )
    return conn.execute(
        "SELECT tag_id FROM core_tags WHERE tag_kind = ? AND name = ?", (tag_kind, name)
    ).fetchone()[0]


def set_item_tags(
    conn: sqlite3.Connection, item_id: int, source: str, tag_ids: Iterable[int]
) -> None:
    """Replace this source's tags for an item."""
    conn.execute("DELETE FROM core_item_tags WHERE item_id = ? AND source = ?", (item_id, source))
    for tid in tag_ids:
        conn.execute(
            "INSERT INTO core_item_tags (item_id, tag_id, source) VALUES (?, ?, ?) "
            "ON CONFLICT (item_id, tag_id, source) DO NOTHING",
            (item_id, tid, source),
        )


def set_item_image(
    conn: sqlite3.Connection, item_id: int, source: str, kind: str, url: str
) -> None:
    # Syncs set every poster and cover again; an unchanged one isn't rewritten.
    conn.execute(
        "INSERT INTO core_item_images (item_id, source, kind, url) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (item_id, source, kind) DO UPDATE SET url = excluded.url "
        "WHERE core_item_images.url IS NOT excluded.url",
        (item_id, source, kind, url),
    )


def set_similarity(
    conn: sqlite3.Connection,
    item_id: int,
    similar_item_id: int,
    source: str,
    kind: str,
    score: float,
) -> None:
    conn.execute(
        "INSERT INTO core_item_similarity (item_id, similar_item_id, source, kind, score) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (item_id, similar_item_id, source, kind) "
        "DO UPDATE SET score = excluded.score",
        (item_id, similar_item_id, source, kind, score),
    )


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def upsert_rating(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    source: str,
    raw_score: float,
    scale_min: float,
    scale_max: float,
    source_updated_at: str | None,
) -> None:
    conn.execute(
        "INSERT INTO core_ratings (item_id, source, raw_score, scale_min, scale_max, "
        "normalized_score, source_updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source, item_id) DO UPDATE SET raw_score = excluded.raw_score, "
        "scale_min = excluded.scale_min, scale_max = excluded.scale_max, "
        "normalized_score = excluded.normalized_score, "
        "source_updated_at = excluded.source_updated_at",
        (
            item_id,
            source,
            raw_score,
            scale_min,
            scale_max,
            normalize(raw_score, scale_min, scale_max),
            source_updated_at,
        ),
    )


def delete_rating(conn: sqlite3.Connection, item_id: int, source: str) -> None:
    conn.execute("DELETE FROM core_ratings WHERE item_id = ? AND source = ?", (item_id, source))


def upsert_community_rating(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    source: str,
    mean_score: float,
    scale_min: float,
    scale_max: float,
    num_raters: int | None,
) -> None:
    conn.execute(
        "INSERT INTO core_community_ratings (item_id, source, mean_score, scale_min, scale_max, "
        "normalized_mean, num_raters, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source, item_id) DO UPDATE SET mean_score = excluded.mean_score, "
        "scale_min = excluded.scale_min, scale_max = excluded.scale_max, "
        "normalized_mean = excluded.normalized_mean, num_raters = excluded.num_raters, "
        "observed_at = excluded.observed_at",
        (
            item_id,
            source,
            mean_score,
            scale_min,
            scale_max,
            normalize(mean_score, scale_min, scale_max),
            num_raters,
            utc_now(),
        ),
    )


def delete_community_rating(conn: sqlite3.Connection, item_id: int, source: str) -> None:
    conn.execute(
        "DELETE FROM core_community_ratings WHERE item_id = ? AND source = ?", (item_id, source)
    )


def upsert_event(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    source: str,
    event_type: str,
    occurred_at_utc: str,
    occurred_at_unix: int | None,
    time_precision: str,
    time_basis: str,
    capture_scope: str,
    source_event_key: str,
) -> bool:
    """Insert or update an event. Returns True if a new row was inserted."""
    exists = conn.execute(
        "SELECT 1 FROM core_behavior_events WHERE source = ? AND source_event_key = ?",
        (source, source_event_key),
    ).fetchone()
    conn.execute(
        "INSERT INTO core_behavior_events (item_id, source, event_type, occurred_at_utc, "
        "occurred_at_unix, time_precision, time_basis, capture_scope, source_event_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source, source_event_key) DO UPDATE SET item_id = excluded.item_id, "
        "event_type = excluded.event_type, occurred_at_utc = excluded.occurred_at_utc, "
        "occurred_at_unix = excluded.occurred_at_unix, "
        "time_precision = excluded.time_precision, time_basis = excluded.time_basis, "
        "capture_scope = excluded.capture_scope",
        (
            item_id,
            source,
            event_type,
            occurred_at_utc,
            occurred_at_unix,
            time_precision,
            time_basis,
            capture_scope,
            source_event_key,
        ),
    )
    return exists is None


def delete_event(conn: sqlite3.Connection, source: str, source_event_key: str) -> None:
    conn.execute(
        "DELETE FROM core_behavior_events WHERE source = ? AND source_event_key = ?",
        (source, source_event_key),
    )


def upsert_curation(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    source: str,
    curation_type: str,
    value: str | None,
    list_name: str = "",
    progress: int | None = None,
    progress_total: int | None = None,
    is_repeat: bool = False,
    repeat_count: int | None = None,
    source_updated_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO core_curation (item_id, source, curation_type, list_name, value, progress, "
        "progress_total, is_repeat, repeat_count, source_updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source, item_id, curation_type, list_name) DO UPDATE SET "
        "value = excluded.value, progress = excluded.progress, "
        "progress_total = excluded.progress_total, is_repeat = excluded.is_repeat, "
        "repeat_count = excluded.repeat_count, source_updated_at = excluded.source_updated_at",
        (
            item_id,
            source,
            curation_type,
            list_name,
            value,
            progress,
            progress_total,
            int(is_repeat),
            repeat_count,
            source_updated_at,
        ),
    )


def delete_curation(conn: sqlite3.Connection, item_id: int, source: str) -> None:
    conn.execute("DELETE FROM core_curation WHERE item_id = ? AND source = ?", (item_id, source))
