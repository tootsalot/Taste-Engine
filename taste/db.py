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
    ("stg_mal_anime", "broadcast_day", "TEXT"),
    ("stg_mal_anime", "broadcast_time", "TEXT"),
    ("stg_tmdb_tv", "overview", "TEXT"),
    ("stg_tmdb_tv", "original_language", "TEXT"),
    ("stg_tmdb_tv", "origin_country_json", "TEXT"),
    ("stg_mal_anime", "main_picture_url", "TEXT"),
    ("stg_mal_anime", "nsfw_rating", "TEXT"),
    ("stg_lastfm_scrobbles", "image_url", "TEXT"),
    ("rec_items", "facts_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("stg_mal_anime", "num_list_users", "INTEGER"),
    ("stg_mal_anime", "popularity_rank", "INTEGER"),
    ("stg_mal_anime", "score_rank", "INTEGER"),
    ("stg_mal_anime", "synopsis", "TEXT"),
    ("stg_mal_anime_details", "fields_version", "INTEGER NOT NULL DEFAULT 0"),
    ("stg_tmdb_movies", "overview", "TEXT"),
    ("stg_tmdb_movies", "popularity", "REAL"),
    ("stg_hardcover_books", "similar_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("stg_hardcover_books", "description", "TEXT"),
    ("stg_hardcover_books", "users_count", "INTEGER"),
    ("stg_hardcover_books", "users_read_count", "INTEGER"),
    ("stg_hardcover_books", "details_at", "TEXT"),
    ("recap_runs", "pictures_missing", "INTEGER NOT NULL DEFAULT 0"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    added = set()
    for table, column, decl in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added.add((table, column))
    return added


# Books came from a Goodreads export, with Open Library covers, until Hardcover
# replaced both (2026-10-15). Hardcover holds the same history, so keeping the old rows
# would count every book twice. They're removed once; the export file isn't touched.
RETIRED_SOURCES = ("goodreads", "openlibrary")
RETIRED_TABLES = ("stg_goodreads_books", "stg_openlibrary_books")
_ITEM_TABLES = (
    "core_behavior_events", "core_community_ratings", "core_curation", "core_item_creators",
    "core_item_external_ids", "core_item_images", "core_item_tags", "core_ratings",
)  # fmt: skip


def _retire_old_sources(conn: sqlite3.Connection) -> None:
    marks = ", ".join("?" for _ in RETIRED_SOURCES)
    found = conn.execute(
        f"SELECT 1 FROM core_sources WHERE source IN ({marks})", RETIRED_SOURCES
    ).fetchone()
    if found is None:
        return
    old = RETIRED_SOURCES
    items = f"(SELECT item_id FROM core_items WHERE source IN ({marks}))"
    creators = f"(SELECT creator_id FROM core_creators WHERE source IN ({marks}))"
    with transaction(conn):
        for table in RETIRED_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(
            "DELETE FROM core_event_local_times WHERE event_id IN ("
            f"SELECT event_id FROM core_behavior_events WHERE source IN ({marks}) "
            f"OR item_id IN {items})",
            old + old,
        )
        for table in _ITEM_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE source IN ({marks}) OR item_id IN {items}", old + old
            )
        conn.execute(
            f"DELETE FROM core_item_links WHERE source IN ({marks}) "
            f"OR child_item_id IN {items} OR parent_item_id IN {items}",
            old * 3,
        )
        conn.execute(
            f"DELETE FROM core_item_similarity WHERE source IN ({marks}) "
            f"OR item_id IN {items} OR similar_item_id IN {items}",
            old * 3,
        )
        conn.execute(f"DELETE FROM core_item_creators WHERE creator_id IN {creators}", old)
        conn.execute(
            f"DELETE FROM core_creator_external_ids WHERE source IN ({marks}) "
            f"OR creator_id IN {creators}",
            old + old,
        )
        conn.execute(f"DELETE FROM core_items WHERE source IN ({marks})", old)
        conn.execute(f"DELETE FROM core_creators WHERE source IN ({marks})", old)
        conn.execute(f"DELETE FROM raw_api_pages WHERE source IN ({marks})", old)
        conn.execute(f"DELETE FROM sync_runs WHERE source IN ({marks})", old)
        conn.execute(f"DELETE FROM core_sources WHERE source IN ({marks})", old)


def apply_schema(conn: sqlite3.Connection) -> None:
    # Local imports: both modules import this one.
    from taste import local_time, settings

    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    added = _add_missing_columns(conn)
    _retire_old_sources(conn)
    settings.ensure_defaults(conn)
    if ("stg_mal_anime", "broadcast_day") in added:
        # Enable the new card once on upgrade, preserving every other card choice.
        # Later opens must respect someone turning Airing now off.
        cards = settings.get(conn, "dashboard_cards").split(",")
        if "airing" not in cards:
            settings.set_value(conn, "dashboard_cards", [*cards, "airing"])
    if ("stg_tmdb_tv", "original_language") in added:
        # TV picks leave out anime by origin; shows synced before 2.3 get theirs
        # from the stored responses, once (no network).
        from taste.sources import tmdb

        tmdb.backfill_origins(conn)
    settings.apply_source_notes(conn)
    # One transaction, so another connection reading a report while this one starts
    # sees the old views or the new ones, never a moment with the view missing.
    views = (SQL_DIR / "views.sql").read_text(encoding="utf-8")
    conn.executescript("BEGIN;\n" + views + "\nCOMMIT;")
    local_time.refresh(conn)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block of writes as one transaction. Rolls back on any exception."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        # SQLite may have rolled back by itself (a full disk, an I/O error); a second
        # ROLLBACK would fail and hide the real error.
        if conn.in_transaction:
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
