"""Hardcover GraphQL queries, library sync, and suggestion enrichment."""

from __future__ import annotations

import html
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from taste import __version__, core, local_time
from taste.db import TIMESTAMP_FORMAT, transaction, utc_now
from taste.http_client import ApiError, HttpClient
from taste.raw import store_page
from taste.sync_log import SyncRun

SOURCE = "hardcover"
API_URL = "https://api.hardcover.app/v1/graphql"
USER_AGENT = f"TasteEngine/{__version__} (https://github.com/tootsalot/Taste-Engine)"
CAPTURE_SCOPE = "self_reported"
PAGE_SIZE = 100
MAX_PAGES = 200  # a safety stop: 20,000 books
MIN_INTERVAL = 1.1  # Hardcover allows 60 requests a minute
TAGS_KEPT = 5  # genres and moods kept per book, most tagged first
SIMILAR_KEPT = 20  # similar books kept per book in my library
CANDIDATE_DETAILS = 120  # details for this many of the strongest suggestions
DETAILS_BATCH = 50  # books asked for in one query
REFRESH_DAYS = 30
STARS_MIN, STARS_MAX = 0.5, 5.0
STATUS = {
    1: "want_to_read",
    2: "reading",
    3: "read",
    4: "paused",
    5: "did_not_finish",
    6: "ignored",
}


@dataclass
class SyncResult:
    source: str
    status: str
    run_id: int
    books: int
    new: int
    removed: int
    error: str | None = None


def client_for(client: HttpClient) -> HttpClient:
    """Hardcover's pace: about one request a second."""
    client.min_interval = max(client.min_interval, MIN_INTERVAL)
    return client


# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------


def check_response(status: int, data: Any) -> None:
    if status == 401 or status == 403:
        raise ApiError(
            "Hardcover says the sign-in has expired. Connect your account again in Settings.",
            retryable=False,
            status=status,
        )
    if status == 429 or status >= 500:
        raise ApiError(f"HTTP {status}", retryable=True, status=status)
    if status != 200:
        raise ApiError(f"HTTP {status}", retryable=False, status=status)
    if isinstance(data, dict) and data.get("errors"):
        first = data["errors"][0] if isinstance(data["errors"], list) else data["errors"]
        message = first.get("message") if isinstance(first, dict) else str(first)
        # An expired token comes back as a GraphQL error rather than a 401.
        if "JWT" in str(message) or "expired" in str(message).lower():
            raise ApiError(
                "Hardcover says the sign-in has expired. Connect your account again in Settings.",
                retryable=False,
            )
        raise ApiError(f"Hardcover: {str(message)[:200]}", retryable=False)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ApiError("Hardcover sent an answer without data", retryable=True, status=status)


def query(
    client: HttpClient, access_token: str, text: str, variables: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """Run one GraphQL query. Returns (HTTP status, the whole answer)."""
    response = client.request_json(
        "POST",
        API_URL,
        body={"query": text, "variables": variables or {}},
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT},
        checker=check_response,
    )
    return response.status, response.data


ME = "query Me { me { id username } }"

LIBRARY_FIELDS = """    id
    status_id
    rating
    date_added
    first_started_reading_date
    first_read_date
    last_read_date
    read_count
    owned
    edition { id pages audio_seconds reading_format_id }
    user_book_reads(order_by: {id: asc}) { id started_at finished_at edition_id }
    book {
      id
      title
      subtitle
      slug
      pages
      audio_seconds
      release_year
      rating
      ratings_count
      literary_type_id
      cached_tags
      cached_similar_book_ids
      image { url }
      contributions { contribution author { id name } }
    }
"""
LIBRARY_END = """  }
}
"""
LIBRARY = (
    """
query Library($uid: Int!, $limit: Int!, $offset: Int!) {
  user_books(where: {user_id: {_eq: $uid}}, order_by: {id: asc}, limit: $limit, offset: $offset) {
"""
    + LIBRARY_FIELDS
    + LIBRARY_END
)
# Only what changed since the last sync. Not yet checked against the live API (2026-10-16):
# if Hardcover refuses the filter, the sync reads the whole library instead.
LIBRARY_SINCE = (
    """
query Library($uid: Int!, $since: timestamptz!, $limit: Int!, $offset: Int!) {
  user_books(where: {user_id: {_eq: $uid}, updated_at: {_gt: $since}},
             order_by: {id: asc}, limit: $limit, offset: $offset) {
"""
    + LIBRARY_FIELDS
    + LIBRARY_END
)
FULL_EVERY = timedelta(days=7)  # a full read finds removals and new community numbers
SINCE_MARGIN = timedelta(hours=1)  # clocks needn't agree to the second


def me(client: HttpClient, access_token: str) -> tuple[int, str]:
    """(user id, username) for the signed-in account."""
    _, data = query(client, access_token, ME)
    found = data["data"].get("me")
    if isinstance(found, list):
        found = found[0] if found else None
    if not isinstance(found, dict) or found.get("id") is None:
        raise ApiError("Hardcover didn't say whose account this is", retryable=False)
    return int(found["id"]), str(found.get("username") or "")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _day(value: Any) -> str | None:
    """'2024-03-05' from a date or a timestamp, or None."""
    text = str(value or "")[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def tag_names(cached: Any, category: str) -> list[str]:
    """The top tags in one category ("Genre", "Mood") of a book's cached_tags.

    Hardcover keeps them as {"Genre": [{"tag": "Fantasy", "count": 120}, ...], ...};
    the shape isn't documented, so anything else is read as no tags.
    """
    if isinstance(cached, str):
        try:
            cached = json.loads(cached)
        except ValueError:
            return []
    if not isinstance(cached, dict):
        return []
    entries = next((v for k, v in cached.items() if str(k).lower() == category.lower()), None)
    if not isinstance(entries, list):
        return []
    names = []
    for entry in entries:
        name = entry.get("tag") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return names[:TAGS_KEPT]


def author_list(book: dict[str, Any]) -> list[tuple[int, str]]:
    """(author id, name) credited as author, in Hardcover's order."""
    found: list[tuple[int, str]] = []
    for contribution in book.get("contributions") or []:
        if not isinstance(contribution, dict):
            continue
        role = (contribution.get("contribution") or "Author").strip().lower()
        author = contribution.get("author") or {}
        if role != "author" or not author.get("id") or not author.get("name"):
            continue
        if all(a[0] != int(author["id"]) for a in found):
            found.append((int(author["id"]), str(author["name"]).strip()))
    return found


def changed_since(conn: sqlite3.Connection, now: datetime) -> str | None:
    """Where a changes-only read starts (Hardcover's timestamp form), or None when it's
    time for a full read: none yet, or the last one was FULL_EVERY ago."""
    full, last = conn.execute(
        "SELECT MAX(CASE WHEN mode = 'full' THEN ended_at END), MAX(started_at) "
        "FROM sync_runs WHERE source = ? AND status = 'success' AND mode IN ('full', "
        "'incremental')",
        (SOURCE,),
    ).fetchone()
    if not full or not last:
        return None
    full_at = datetime.strptime(full, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if now - full_at >= FULL_EVERY:
        return None
    start = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (start - SINCE_MARGIN).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def sync(
    conn: sqlite3.Connection,
    client: HttpClient,
    access_token: str,
    out: Callable[[str], None] = print,
    *,
    full: bool = False,
    now: datetime | None = None,
) -> SyncResult:
    """Read my library into staging and core. Only the books changed since the last sync,
    with a full read when a week has passed (or `full`): reading every book each time
    took the longest of any source for no change at all."""
    since = None if full else changed_since(conn, now or datetime.now(timezone.utc))
    run = SyncRun(conn, SOURCE, "incremental" if since else "full")
    client_for(client)
    stamp = utc_now()
    try:
        user_id, _ = me(client, access_token)
        try:
            pages = _read_library(conn, client, access_token, run, user_id, since, out)
        except ApiError as exc:
            if since is None or exc.retryable or "expired" in str(exc):
                raise
            out(f"Hardcover: no changes-only read ({exc}); reading the whole library")
            since = None
            run.set_mode("full")
            pages = _read_library(conn, client, access_token, run, user_id, None, out)
        with transaction(conn):
            for raw_id, rows in pages:
                for row in rows:
                    if not row.get("id") or not isinstance(row.get("book"), dict):
                        continue
                    run.rows_fetched += 1
                    change = _upsert_staging(conn, row, run.run_id, raw_id, stamp)
                    run.rows_inserted += change == "inserted"
                    run.rows_updated += change == "updated"
            # Only a full read can tell what's gone from the library.
            removed = 0 if since else _apply_removals(conn, run.run_id, stamp)
            for (user_book_id,) in conn.execute(
                "SELECT user_book_id FROM stg_hardcover_user_books WHERE last_seen_run_id = ?",
                (run.run_id,),
            ).fetchall():
                _load_core(conn, user_book_id)
            local_time.refresh(conn)
            run.save()
        run.finish("success")
        out(
            f"Hardcover: {run.rows_fetched} books, {run.rows_inserted} new, "
            f"{run.rows_updated} changed, {removed} removed"
        )
        return SyncResult(
            SOURCE, "success", run.run_id, run.rows_fetched, run.rows_inserted, removed
        )
    except ApiError as exc:
        run.finish("failed", str(exc))
        out(f"Hardcover: sync failed: {exc}")
        return SyncResult(SOURCE, "failed", run.run_id, run.rows_fetched, 0, 0, str(exc))
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise


def _read_library(
    conn: sqlite3.Connection,
    client: HttpClient,
    access_token: str,
    run: SyncRun,
    user_id: int,
    since: str | None,
    out: Callable[[str], None],
) -> list[tuple[int, list[dict[str, Any]]]]:
    """Every page of the library (or of the books changed since `since`), each stored raw."""
    pages: list[tuple[int, list[dict[str, Any]]]] = []
    text = LIBRARY_SINCE if since else LIBRARY
    for page in range(MAX_PAGES):
        params: dict[str, Any] = {"offset": page * PAGE_SIZE, "limit": PAGE_SIZE}
        if since:
            params["since"] = since
        status, data = query(client, access_token, text, {"uid": user_id, **params})
        rows = [r for r in data["data"].get("user_books") or [] if isinstance(r, dict)]
        with transaction(conn):
            raw_id = store_page(
                conn,
                sync_run_id=run.run_id,
                source=SOURCE,
                endpoint="graphql:Library",
                params=params,
                page_number=page + 1,
                http_status=status,
                payload=data,
            )
            run.pages_fetched += 1
            run.save()
        pages.append((raw_id, rows))
        changed = " changed" if since else ""
        out(f"Hardcover: {sum(len(r) for _, r in pages)}{changed} books so far")
        if len(rows) < PAGE_SIZE:
            break
    return pages


_USER_BOOK = [
    "book_id",
    "status_id",
    "rating",
    "date_added",
    "first_started_date",
    "first_read_date",
    "last_read_date",
    "read_count",
    "owned",
    "edition_id",
    "reading_format_id",
    "edition_pages",
    "edition_seconds",
]


def user_book_values(row: dict[str, Any]) -> dict[str, Any]:
    edition = row.get("edition") if isinstance(row.get("edition"), dict) else {}
    rating = _number(row.get("rating"))
    return {
        "book_id": int(row["book"]["id"]),
        "status_id": _int(row.get("status_id")) or 0,
        "rating": rating if rating else None,  # 0 means not rated
        "date_added": _day(row.get("date_added")),
        "first_started_date": _day(row.get("first_started_reading_date")),
        "first_read_date": _day(row.get("first_read_date")),
        "last_read_date": _day(row.get("last_read_date")),
        "read_count": _int(row.get("read_count")) or 0,
        "owned": int(bool(row.get("owned"))),
        "edition_id": _int(edition.get("id")),
        "reading_format_id": _int(edition.get("reading_format_id")),
        "edition_pages": _int(edition.get("pages")) or None,
        "edition_seconds": _int(edition.get("audio_seconds")) or None,
    }


def book_values(book: dict[str, Any]) -> dict[str, Any]:
    image = book.get("image") if isinstance(book.get("image"), dict) else {}
    tags = book.get("cached_tags")
    authors = [{"id": a, "name": n} for a, n in author_list(book)]
    return {
        "title": str(book.get("title") or f"Hardcover book {book['id']}").strip(),
        "subtitle": (book.get("subtitle") or "").strip() or None,
        "slug": book.get("slug") or None,
        "pages": _int(book.get("pages")) or None,
        "audio_seconds": _int(book.get("audio_seconds")) or None,
        "release_year": _int(book.get("release_year")),
        "rating": _number(book.get("rating")) or None,
        "ratings_count": _int(book.get("ratings_count")),
        "literary_type_id": _int(book.get("literary_type_id")),
        "image_url": image.get("url") or None,
        "authors_json": json.dumps(authors, ensure_ascii=False),
        "genres_json": json.dumps(tag_names(tags, "Genre"), ensure_ascii=False),
        "moods_json": json.dumps(tag_names(tags, "Mood"), ensure_ascii=False),
        "similar_json": json.dumps(similar_ids(book.get("cached_similar_book_ids"))),
    }


def similar_ids(cached: Any) -> list[int]:
    """The first SIMILAR_KEPT of Hardcover's similar books, most similar first. The field
    is a JSON list of ids (or of objects with an id); anything else reads as none."""
    if isinstance(cached, str):
        try:
            cached = json.loads(cached)
        except ValueError:
            return []
    if not isinstance(cached, list):
        return []
    found: list[int] = []
    for entry in cached:
        value = entry.get("id") if isinstance(entry, dict) else entry
        number = _int(value)
        if number is not None and number not in found:
            found.append(number)
        if len(found) == SIMILAR_KEPT:
            break
    return found


def _upsert_staging(
    conn: sqlite3.Connection, row: dict[str, Any], run_id: int, raw_id: int, now: str
) -> str:
    user_book_id = int(row["id"])
    values = user_book_values(row)
    existing = conn.execute(
        f"SELECT {', '.join(_USER_BOOK)}, removed_at FROM stg_hardcover_user_books "
        "WHERE user_book_id = ?",
        (user_book_id,),
    ).fetchone()
    if existing is None:
        cols = [
            "user_book_id",
            *_USER_BOOK,
            "first_seen_at",
            "last_seen_run_id",
            "last_raw_page_id",
        ]
        conn.execute(
            f"INSERT INTO stg_hardcover_user_books ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            (user_book_id, *values.values(), now, run_id, raw_id),
        )
        change = "inserted"
    else:
        moved = existing["removed_at"] is not None or any(
            existing[c] != values[c] for c in _USER_BOOK
        )
        conn.execute(
            f"UPDATE stg_hardcover_user_books SET {', '.join(f'{c} = ?' for c in _USER_BOOK)}, "
            "last_seen_run_id = ?, last_raw_page_id = ?, removed_at = NULL "
            "WHERE user_book_id = ?",
            (*values.values(), run_id, raw_id, user_book_id),
        )
        change = "updated" if moved else "unchanged"

    book = book_values(row["book"])
    conn.execute(
        f"INSERT INTO stg_hardcover_books (book_id, {', '.join(book)}, last_raw_page_id, "
        f"updated_at) VALUES (?, {', '.join('?' for _ in book)}, ?, ?) "
        f"ON CONFLICT (book_id) DO UPDATE SET "
        f"{', '.join(f'{c} = excluded.{c}' for c in book)}, "
        "last_raw_page_id = excluded.last_raw_page_id, updated_at = excluded.updated_at",
        (values["book_id"], *book.values(), raw_id, now),
    )

    seen_reads = []
    for read in row.get("user_book_reads") or []:
        if not isinstance(read, dict) or read.get("id") is None:
            continue
        seen_reads.append(int(read["id"]))
        conn.execute(
            "INSERT INTO stg_hardcover_reads (read_id, user_book_id, started_at, finished_at, "
            "edition_id, first_seen_at, last_seen_run_id, last_raw_page_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (read_id) DO UPDATE SET "
            "user_book_id = excluded.user_book_id, started_at = excluded.started_at, "
            "finished_at = excluded.finished_at, edition_id = excluded.edition_id, "
            "last_seen_run_id = excluded.last_seen_run_id, "
            "last_raw_page_id = excluded.last_raw_page_id, removed_at = NULL",
            (
                int(read["id"]),
                user_book_id,
                _day(read.get("started_at")),
                _day(read.get("finished_at")),
                _int(read.get("edition_id")),
                now,
                run_id,
                raw_id,
            ),
        )
    # The book came back with all its read-throughs, so one missing from it was deleted on
    # Hardcover. Settled here even on a changes-only read, which can't see removed books.
    # Only against a reads list that came back: a row without one says nothing about reads.
    if not isinstance(row.get("user_book_reads"), list):
        return change
    marks = ", ".join("?" for _ in seen_reads)
    conn.execute(
        "UPDATE stg_hardcover_reads SET removed_at = ? WHERE user_book_id = ? "
        "AND removed_at IS NULL" + (f" AND read_id NOT IN ({marks})" if seen_reads else ""),
        (now, user_book_id, *seen_reads),
    )
    return change


def _load_core(conn: sqlite3.Connection, user_book_id: int) -> None:
    mine = conn.execute(
        "SELECT * FROM stg_hardcover_user_books WHERE user_book_id = ?", (user_book_id,)
    ).fetchone()
    book = conn.execute(
        "SELECT * FROM stg_hardcover_books WHERE book_id = ?", (mine["book_id"],)
    ).fetchone()
    item_id = core.upsert_item(
        conn,
        source=SOURCE,
        id_type="hardcover_book_id",
        external_id=str(book["book_id"]),
        media_type="book",
        title=book["title"],
        release_year=book["release_year"],
    )
    creators = [
        core.upsert_creator(
            conn,
            source=SOURCE,
            id_type="hardcover_author_id",
            external_id=str(a["id"]),
            name=a["name"],
        )  # fmt: skip
        for a in json.loads(book["authors_json"])
    ]
    core.set_item_creators(conn, item_id, SOURCE, "author", creators)
    if mine["rating"] is not None:
        core.upsert_rating(
            conn,
            item_id=item_id,
            source=SOURCE,
            raw_score=mine["rating"],
            scale_min=STARS_MIN,
            scale_max=STARS_MAX,
            source_updated_at=None,
        )
    else:
        core.delete_rating(conn, item_id, SOURCE)
    if book["rating"]:
        core.upsert_community_rating(
            conn,
            item_id=item_id,
            source=SOURCE,
            mean_score=book["rating"],
            scale_min=0.0,
            scale_max=STARS_MAX,
            num_raters=book["ratings_count"],
        )
    else:
        core.delete_community_rating(conn, item_id, SOURCE)

    reads = conn.execute(
        "SELECT read_id, finished_at FROM stg_hardcover_reads "
        "WHERE user_book_id = ? AND removed_at IS NULL ORDER BY read_id",
        (user_book_id,),
    ).fetchall()
    finished = [(f"{user_book_id}:read:{r[0]}", r[1]) for r in reads if r[1]]
    if not finished and STATUS.get(mine["status_id"]) == "read" and mine["last_read_date"]:
        finished = [(f"{user_book_id}:read", mine["last_read_date"])]
    pages = mine["edition_pages"] or book["pages"]
    status = STATUS.get(mine["status_id"], "unknown")
    core.upsert_curation(
        conn,
        item_id=item_id,
        source=SOURCE,
        curation_type="list_status",
        value=status,
        progress=pages if status == "read" else None,
        progress_total=pages,
        is_repeat=max(len(finished), mine["read_count"]) > 1,
        repeat_count=max(len(finished), mine["read_count"]) or None,
    )
    if mine["owned"]:
        core.upsert_curation(conn, item_id=item_id, source=SOURCE, curation_type="owned", value="1")
    else:
        conn.execute(
            "DELETE FROM core_curation WHERE item_id = ? AND source = ? "
            "AND curation_type = 'owned'",
            (item_id, SOURCE),
        )
    tags = [core.tag_id(conn, name, "genre") for name in json.loads(book["genres_json"])]
    tags += [core.tag_id(conn, name, "mood") for name in json.loads(book["moods_json"])]
    core.set_item_tags(conn, item_id, SOURCE, tags)
    if book["image_url"]:
        core.set_item_image(conn, item_id, SOURCE, "cover", book["image_url"])
    else:
        conn.execute(
            "DELETE FROM core_item_images WHERE item_id = ? AND source = ? AND kind = 'cover'",
            (item_id, SOURCE),
        )

    keep = {key for key, _ in finished}
    for key, day in finished:
        core.upsert_event(
            conn,
            item_id=item_id,
            source=SOURCE,
            event_type="read_finished",
            occurred_at_utc=f"{day} 00:00:00",
            occurred_at_unix=None,
            time_precision="day",
            time_basis="user_entered",
            capture_scope=CAPTURE_SCOPE,
            source_event_key=key,
        )
    prefix = f"{user_book_id}:"
    for (key,) in conn.execute(
        "SELECT source_event_key FROM core_behavior_events WHERE source = ? "
        "AND substr(source_event_key, 1, ?) = ?",
        (SOURCE, len(prefix), prefix),
    ).fetchall():
        if key not in keep:
            core.delete_event(conn, SOURCE, key)


def _apply_removals(conn: sqlite3.Connection, run_id: int, now: str) -> int:
    """Books gone from my library, and read-throughs gone from a book still in it."""
    conn.execute(
        "UPDATE stg_hardcover_reads SET removed_at = ? "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (now, run_id),
    )
    gone = conn.execute(
        "SELECT user_book_id, book_id FROM stg_hardcover_user_books "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (run_id,),
    ).fetchall()
    for user_book_id, book_id in gone:
        conn.execute(
            "UPDATE stg_hardcover_user_books SET removed_at = ? WHERE user_book_id = ?",
            (now, user_book_id),
        )
        item_id = core.find_item(conn, SOURCE, "hardcover_book_id", str(book_id))
        if item_id is not None:
            core.delete_rating(conn, item_id, SOURCE)
            core.delete_curation(conn, item_id, SOURCE)
            core.delete_events_with_prefix(conn, SOURCE, f"{user_book_id}:")
    return len(gone)


# ---------------------------------------------------------------------------
# Suggestions: Hardcover's similar books for the ones I rated highest
# ---------------------------------------------------------------------------

BOOKS = """
query Books($ids: [Int!]) {
  books(where: {id: {_in: $ids}}) {
    id
    title
    subtitle
    slug
    pages
    audio_seconds
    release_year
    rating
    ratings_count
    users_count
    users_read_count
    literary_type_id
    description
    cached_tags
    image { url }
    contributions { contribution author { id name } }
  }
}
"""


def my_mean_rating(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT AVG(rating) FROM stg_hardcover_user_books WHERE removed_at IS NULL "
        "AND rating IS NOT NULL"
    ).fetchone()
    return row[0]


def candidate_support(conn: sqlite3.Connection) -> dict[int, float]:
    """How strongly my favorites point at each book I don't have: for every book I rated
    above my own average, its similar books, the closest ones counting most."""
    mine = my_mean_rating(conn)
    if mine is None:
        return {}
    shelved = {
        r[0]
        for r in conn.execute(
            "SELECT book_id FROM stg_hardcover_user_books WHERE removed_at IS NULL "
            "AND status_id <> 1"
        )
    }
    support: dict[int, float] = {}
    for rating, similar in conn.execute(
        "SELECT ub.rating, b.similar_json FROM stg_hardcover_user_books ub "
        "JOIN stg_hardcover_books b ON b.book_id = ub.book_id "
        "WHERE ub.removed_at IS NULL AND ub.rating > ?",
        (mine,),
    ):
        for position, book_id in enumerate(json.loads(similar or "[]")):
            if book_id in shelved:
                continue
            support[book_id] = support.get(book_id, 0.0) + (rating - mine) / (1 + position / 10)
    return support


def clean_description(text: Any) -> str | None:
    """A book's description as plain text (some carry a little HTML)."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(html.unescape(text).split())
    return text or None


def save_book(conn: sqlite3.Connection, book: dict[str, Any], raw_id: int, now: str) -> None:
    """A book from a details lookup: everything the library sync keeps, plus its
    description and how many readers it has."""
    values = book_values(book)
    values.pop("similar_json")  # not asked for here; keep what the library sync found
    extra = {
        "description": clean_description(book.get("description")),
        "users_count": _int(book.get("users_count")),
        "users_read_count": _int(book.get("users_read_count")),
        "details_at": now,
    }
    columns = {**values, **extra}
    conn.execute(
        f"INSERT INTO stg_hardcover_books (book_id, {', '.join(columns)}, last_raw_page_id, "
        f"updated_at) VALUES (?, {', '.join('?' for _ in columns)}, ?, ?) "
        f"ON CONFLICT (book_id) DO UPDATE SET "
        f"{', '.join(f'{c} = excluded.{c}' for c in columns)}, "
        "last_raw_page_id = excluded.last_raw_page_id, updated_at = excluded.updated_at",
        (int(book["id"]), *columns.values(), raw_id, now),
    )


def enrich_recommendations(
    conn: sqlite3.Connection,
    client: HttpClient,
    access_token: str,
    out: Callable[[str], None] = print,
    *,
    now: datetime | None = None,
) -> int:
    """Details for the strongest suggestions (and my Want to Read), cached 30 days.
    Returns how many books were looked up."""
    client_for(client)
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime(TIMESTAMP_FORMAT)
    cutoff = (now - timedelta(days=REFRESH_DAYS)).strftime(TIMESTAMP_FORMAT)
    support = candidate_support(conn)
    wanted = [
        r[0]
        for r in conn.execute(
            "SELECT book_id FROM stg_hardcover_user_books WHERE removed_at IS NULL "
            "AND status_id = 1"
        )
    ]
    strongest = sorted(support, key=lambda i: (-support[i], i))[:CANDIDATE_DETAILS]
    fresh = {
        r[0]
        for r in conn.execute(
            "SELECT book_id FROM stg_hardcover_books WHERE details_at >= ?", (cutoff,)
        )
    }
    todo = [i for i in dict.fromkeys(strongest + wanted) if i not in fresh]
    if not todo:
        return 0
    run = SyncRun(conn, SOURCE, "enrich")
    try:
        for start in range(0, len(todo), DETAILS_BATCH):
            ids = todo[start : start + DETAILS_BATCH]
            status, data = query(client, access_token, BOOKS, {"ids": ids})
            with transaction(conn):
                raw_id = store_page(
                    conn,
                    sync_run_id=run.run_id,
                    source=SOURCE,
                    endpoint="graphql:Books",
                    params={"ids": ids},
                    page_number=start // DETAILS_BATCH + 1,
                    http_status=status,
                    payload=data,
                )
                found = set()
                for book in data["data"].get("books") or []:
                    if isinstance(book, dict) and book.get("id") is not None:
                        save_book(conn, book, raw_id, stamp)
                        found.add(int(book["id"]))
                # Asked for but not sent back (removed, merged): don't ask again for a month.
                # Most aren't in staging at all, so a placeholder row (no average, so never
                # suggested) holds the note; a library sync fills it in if it ever shows up.
                for missing in set(ids) - found:
                    conn.execute(
                        "INSERT INTO stg_hardcover_books (book_id, title, updated_at, details_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT (book_id) DO UPDATE SET "
                        "details_at = excluded.details_at",
                        (missing, f"Hardcover book {missing}", stamp, stamp),
                    )
                run.pages_fetched += 1
                run.rows_fetched += len(found)
                run.save()
            out(f"Recommendations: Hardcover books, {min(start + DETAILS_BATCH, len(todo))} "
                f"of {len(todo)}")  # fmt: skip
        run.finish("success")
        return len(todo)
    except ApiError as exc:
        run.finish("failed", str(exc))
        out(f"Recommendations: Hardcover details stopped, will continue next time: {exc}")
        return 0
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
