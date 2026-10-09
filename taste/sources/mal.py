"""MyAnimeList sync: my full anime list via the official API v2.

MAL has no "changed since" filter and the list is small (under 1,000 entries),
so every sync pulls the whole list and upserts it. Steps:

1. Fetch every page (following paging.next) and store each one in raw_api_pages.
2. In one transaction, upsert staging, detect removed entries, and load core.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

from taste import core, local_time
from taste import settings as profile_settings
from taste.config import MalSettings
from taste.db import iso_to_utc, transaction, utc_now, utc_to_unix
from taste.http_client import (
    RETRYABLE_HTTP_STATUSES,
    ApiError,
    HttpClient,
)
from taste.raw import store_page
from taste.sync_log import SyncRun

SOURCE = "mal"
API_BASE = "https://api.myanimelist.net/v2"
ENDPOINT = "/users/{username}/animelist"
PAGE_LIMIT = 1000  # the API's maximum; 1001 returns HTTP 400
SCORE_MIN, SCORE_MAX = 1.0, 10.0
CAPTURE_SCOPE = "self_reported"

LIST_STATUS_FIELDS = [
    "status",
    "score",
    "num_episodes_watched",
    "is_rewatching",
    "start_date",
    "finish_date",
    "updated_at",
    "num_times_rewatched",
    "priority",
    "rewatch_value",
    "tags",
    "comments",
]
ANIME_FIELDS = [
    "title",
    "alternative_titles",
    "media_type",
    "num_episodes",
    "start_season",
    "genres",
    "studios",
    "mean",
    "num_scoring_users",
    "status",
    "start_date",
    "end_date",
    "rating",
    "source",
    "average_episode_duration",
    "main_picture",
    "nsfw",
]
FIELDS = f"list_status{{{','.join(LIST_STATUS_FIELDS)}}},{','.join(ANIME_FIELDS)}"

_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


@dataclass
class SyncResult:
    source: str
    status: str
    run_id: int
    pages_fetched: int
    rows_fetched: int
    rows_inserted: int
    rows_updated: int
    rows_removed: int = 0
    error: str | None = None


def check_response(status: int, data: Any) -> None:
    if status in RETRYABLE_HTTP_STATUSES:
        raise ApiError(f"MAL HTTP {status}", retryable=True, status=status)
    if status >= 400:
        detail = data.get("message") or data.get("error") if isinstance(data, dict) else ""
        raise ApiError(f"MAL HTTP {status}: {detail}", retryable=False, status=status)
    if not isinstance(data, dict) or "data" not in data:
        raise ApiError("MAL response is missing the 'data' key", retryable=True, status=status)


def sync(
    conn: sqlite3.Connection,
    client: HttpClient,
    settings: MalSettings,
    out: Callable[[str], None] = print,
) -> SyncResult:
    run = SyncRun(conn, SOURCE, "full")
    removed = 0
    try:
        pages = _fetch_all_pages(conn, client, settings, run, out)
        with transaction(conn):
            removed = _load(conn, run, pages)
            local_time.refresh(conn)
            run.save()
        run.finish("success")
        out(
            f"MAL: {run.rows_fetched} entries, {run.rows_inserted} new, "
            f"{run.rows_updated} changed, {removed} removed"
        )
        status, error = "success", None
    except ApiError as exc:
        status, error = "failed", str(exc)
        run.finish(status, error)
        out(f"MAL: sync failed: {error}")
    except Exception as exc:
        # Unexpected bug: record it (redacted) and let the traceback through.
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
    return SyncResult(
        SOURCE,
        status,
        run.run_id,
        run.pages_fetched,
        run.rows_fetched,
        run.rows_inserted,
        run.rows_updated,
        removed,
        error,
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_all_pages(
    conn: sqlite3.Connection,
    client: HttpClient,
    settings: MalSettings,
    run: SyncRun,
    out: Callable[[str], None],
) -> list[tuple[int, dict[str, Any]]]:
    url: str | None = f"{API_BASE}/users/{quote(settings.username)}/animelist"
    params: dict[str, Any] | None = {"limit": PAGE_LIMIT, "fields": FIELDS}
    if profile_settings.get(conn, "include_nsfw"):
        # Without this, MAL silently leaves NSFW entries out of the list.
        params["nsfw"] = "true"
    headers = {"X-MAL-CLIENT-ID": settings.client_id}
    pages: list[tuple[int, dict[str, Any]]] = []
    page_number = 1
    while url:
        response = client.get_json(url, params=params, headers=headers, checker=check_response)
        # paging.next already carries every query parameter, so record those for later pages.
        recorded = params if params is not None else dict(parse_qsl(urlparse(url).query))
        entries = response.data["data"]
        with transaction(conn):
            raw_id = store_page(
                conn,
                sync_run_id=run.run_id,
                source=SOURCE,
                endpoint=ENDPOINT,
                params=recorded,
                page_number=page_number,
                http_status=response.status,
                payload=response.data,
            )
            run.pages_fetched += 1
            run.rows_fetched += len(entries)
            run.save()
        out(f"MAL: page {page_number} fetched ({len(entries)} entries)")
        pages.append((raw_id, response.data))
        url = (response.data.get("paging") or {}).get("next")
        params = None
        page_number += 1
    return pages


# ---------------------------------------------------------------------------
# Staging and core
# ---------------------------------------------------------------------------


def _load(conn: sqlite3.Connection, run: SyncRun, pages: list[tuple[int, dict[str, Any]]]) -> int:
    """Load every fetched entry into staging and core. Returns the number of removals."""
    now = utc_now()
    for raw_id, payload in pages:
        for entry in payload["data"]:
            node, list_status = entry["node"], entry["list_status"]
            upsert_stg_anime(conn, node, raw_id, now)
            change = _upsert_stg_list_entry(conn, node["id"], list_status, run.run_id, raw_id, now)
            if change == "inserted":
                run.rows_inserted += 1
            elif change == "updated":
                run.rows_updated += 1
            _load_core(conn, node["id"])
    # Only reached when every page came back, so a missing entry really was removed.
    return _apply_removals(conn, run.run_id, now)


def _none_if_blank(value: Any) -> Any:
    return value if value not in ("", None) else None


def poster_url(node: dict[str, Any]) -> str | None:
    picture = node.get("main_picture") or {}
    return picture.get("medium") or picture.get("large")


def upsert_stg_anime(conn: sqlite3.Connection, node: dict[str, Any], raw_id: int, now: str):
    """Upsert one anime from a list entry's node or a details response (same shape)."""
    alt = node.get("alternative_titles") or {}
    season = node.get("start_season") or {}
    anime_id = node["id"]
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, title_en, title_ja, synonyms_json, "
        "media_type, num_episodes, start_season_year, start_season, airing_status, start_date, "
        "end_date, content_rating, source_material, avg_episode_seconds, community_mean, "
        "num_scoring_users, main_picture_url, nsfw_rating, last_raw_page_id, loaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (mal_anime_id) DO UPDATE SET title = excluded.title, "
        "title_en = excluded.title_en, title_ja = excluded.title_ja, "
        "synonyms_json = excluded.synonyms_json, media_type = excluded.media_type, "
        "num_episodes = excluded.num_episodes, start_season_year = excluded.start_season_year, "
        "start_season = excluded.start_season, airing_status = excluded.airing_status, "
        "start_date = excluded.start_date, end_date = excluded.end_date, "
        "content_rating = excluded.content_rating, source_material = excluded.source_material, "
        "avg_episode_seconds = excluded.avg_episode_seconds, "
        "community_mean = excluded.community_mean, "
        "num_scoring_users = excluded.num_scoring_users, "
        "main_picture_url = COALESCE(excluded.main_picture_url, stg_mal_anime.main_picture_url), "
        "nsfw_rating = COALESCE(excluded.nsfw_rating, stg_mal_anime.nsfw_rating), "
        "last_raw_page_id = excluded.last_raw_page_id, loaded_at = excluded.loaded_at",
        (
            anime_id,
            node["title"],
            _none_if_blank(alt.get("en")),
            _none_if_blank(alt.get("ja")),
            json.dumps(alt.get("synonyms") or [], ensure_ascii=False),
            node.get("media_type"),
            node.get("num_episodes") or None,  # 0 means unknown
            season.get("year"),
            season.get("season"),
            node.get("status"),
            node.get("start_date"),
            node.get("end_date"),
            node.get("rating"),
            node.get("source"),
            node.get("average_episode_duration") or None,
            node.get("mean"),
            node.get("num_scoring_users"),
            poster_url(node),
            node.get("nsfw"),
            raw_id,
            now,
        ),
    )
    conn.execute("DELETE FROM stg_mal_anime_genres WHERE mal_anime_id = ?", (anime_id,))
    for genre in node.get("genres") or []:
        conn.execute(
            "INSERT INTO stg_mal_anime_genres (mal_anime_id, genre_id, genre_name) "
            "VALUES (?, ?, ?) ON CONFLICT (mal_anime_id, genre_id) DO NOTHING",
            (anime_id, genre["id"], genre["name"]),
        )
    conn.execute("DELETE FROM stg_mal_anime_studios WHERE mal_anime_id = ?", (anime_id,))
    for studio in node.get("studios") or []:
        conn.execute(
            "INSERT INTO stg_mal_anime_studios (mal_anime_id, studio_id, studio_name) "
            "VALUES (?, ?, ?) ON CONFLICT (mal_anime_id, studio_id) DO NOTHING",
            (anime_id, studio["id"], studio["name"]),
        )


# Columns compared to decide whether a list entry changed since the last sync.
_TRACKED = [
    "status",
    "score",
    "num_episodes_watched",
    "is_rewatching",
    "num_times_rewatched",
    "priority",
    "rewatch_value",
    "tags_json",
    "comments",
    "start_date",
    "finish_date",
    "mal_updated_at",
]


def _upsert_stg_list_entry(
    conn: sqlite3.Connection,
    anime_id: int,
    ls: dict[str, Any],
    run_id: int,
    raw_id: int,
    now: str,
) -> str:
    """Upsert one list entry. Returns 'inserted', 'updated', or 'unchanged'."""
    values = {
        "status": ls["status"],
        "score": ls.get("score") or None,  # MAL uses 0 for "not scored"
        "num_episodes_watched": ls.get("num_episodes_watched") or 0,
        "is_rewatching": int(bool(ls.get("is_rewatching"))),
        "num_times_rewatched": ls.get("num_times_rewatched"),
        "priority": ls.get("priority"),
        "rewatch_value": ls.get("rewatch_value"),
        "tags_json": json.dumps(ls.get("tags") or [], ensure_ascii=False),
        "comments": ls.get("comments") or "",
        "start_date": _none_if_blank(ls.get("start_date")),
        "finish_date": _none_if_blank(ls.get("finish_date")),
        "mal_updated_at": iso_to_utc(ls["updated_at"]),
    }
    existing = conn.execute(
        f"SELECT {', '.join(_TRACKED)}, removed_at FROM stg_mal_list_entries "
        "WHERE mal_anime_id = ?",
        (anime_id,),
    ).fetchone()

    if existing is None:
        cols = ["mal_anime_id", *_TRACKED, "first_seen_at", "last_seen_run_id", "last_raw_page_id"]
        conn.execute(
            f"INSERT INTO stg_mal_list_entries ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            (anime_id, *values.values(), now, run_id, raw_id),
        )
        return "inserted"

    changed = existing["removed_at"] is not None or any(
        existing[col] != values[col] for col in _TRACKED
    )
    assignments = ", ".join(f"{col} = ?" for col in _TRACKED)
    conn.execute(
        f"UPDATE stg_mal_list_entries SET {assignments}, last_seen_run_id = ?, "
        "last_raw_page_id = ?, removed_at = NULL WHERE mal_anime_id = ?",
        (*values.values(), run_id, raw_id, anime_id),
    )
    return "updated" if changed else "unchanged"


def date_event_time(value: str | None) -> tuple[str, str] | None:
    """Turn a MAL date (possibly partial) into (occurred_at_utc, precision).

    These are calendar dates I typed in, with no time of day or time zone, so the
    time part is always 00:00:00 and the precision says how much to trust it.
    """
    if not value or not _DATE.match(value):
        return None
    if len(value) == 10:
        return f"{value} 00:00:00", "day"
    if len(value) == 7:
        return f"{value}-01 00:00:00", "month"
    return f"{value}-01-01 00:00:00", "year"


def load_anime_core(conn: sqlite3.Connection, anime_id: int) -> int:
    """Load one anime's own facts (not my opinion of it) into core. Returns its item_id.

    Used for shows on my list and for recommendation candidates that aren't.
    """
    anime = conn.execute(
        "SELECT * FROM stg_mal_anime WHERE mal_anime_id = ?", (anime_id,)
    ).fetchone()
    release_year = anime["start_season_year"]
    if release_year is None and anime["start_date"]:
        release_year = int(anime["start_date"][:4])
    item_id = core.upsert_item(
        conn,
        source=SOURCE,
        id_type="mal_anime_id",
        external_id=str(anime_id),
        media_type="anime",
        title=anime["title"],
        title_alt=anime["title_en"],
        release_year=release_year,
    )

    studios = conn.execute(
        "SELECT studio_id, studio_name FROM stg_mal_anime_studios WHERE mal_anime_id = ?",
        (anime_id,),
    ).fetchall()
    creator_ids = [
        core.upsert_creator(
            conn,
            source=SOURCE,
            id_type="mal_studio_id",
            external_id=str(s["studio_id"]),
            name=s["studio_name"],
        )
        for s in studios
    ]
    core.set_item_creators(conn, item_id, SOURCE, "studio", creator_ids)

    genres = conn.execute(
        "SELECT genre_name FROM stg_mal_anime_genres WHERE mal_anime_id = ?", (anime_id,)
    ).fetchall()
    core.set_item_tags(
        conn, item_id, SOURCE, [core.tag_id(conn, g["genre_name"], "genre") for g in genres]
    )

    if anime["community_mean"] is not None:
        core.upsert_community_rating(
            conn,
            item_id=item_id,
            source=SOURCE,
            mean_score=anime["community_mean"],
            scale_min=SCORE_MIN,
            scale_max=SCORE_MAX,
            num_raters=anime["num_scoring_users"],
        )
    else:
        core.delete_community_rating(conn, item_id, SOURCE)
    if anime["main_picture_url"]:
        core.set_item_image(conn, item_id, SOURCE, "poster", anime["main_picture_url"])
    return item_id


def _load_core(conn: sqlite3.Connection, anime_id: int) -> None:
    """Load one anime and my list entry for it from staging into core."""
    item_id = load_anime_core(conn, anime_id)
    anime = conn.execute(
        "SELECT num_episodes FROM stg_mal_anime WHERE mal_anime_id = ?", (anime_id,)
    ).fetchone()
    entry = conn.execute(
        "SELECT * FROM stg_mal_list_entries WHERE mal_anime_id = ?", (anime_id,)
    ).fetchone()

    if entry["score"] is not None:
        core.upsert_rating(
            conn,
            item_id=item_id,
            source=SOURCE,
            raw_score=entry["score"],
            scale_min=SCORE_MIN,
            scale_max=SCORE_MAX,
            source_updated_at=entry["mal_updated_at"],
        )
    else:
        core.delete_rating(conn, item_id, SOURCE)

    core.upsert_curation(
        conn,
        item_id=item_id,
        source=SOURCE,
        curation_type="list_status",
        value=entry["status"],
        progress=entry["num_episodes_watched"],
        progress_total=anime["num_episodes"],
        is_repeat=bool(entry["is_rewatching"]),
        repeat_count=entry["num_times_rewatched"],
        source_updated_at=entry["mal_updated_at"],
    )

    _load_events(conn, item_id, anime_id, entry)


def _load_events(conn: sqlite3.Connection, item_id: int, anime_id: int, entry: sqlite3.Row) -> None:
    """watch_started / watch_finished events, with the fallback rules from PLAN.md 4.1."""
    started_key = f"{anime_id}:started"
    started = date_event_time(entry["start_date"])
    if started:
        core.upsert_event(
            conn,
            item_id=item_id,
            source=SOURCE,
            event_type="watch_started",
            occurred_at_utc=started[0],
            occurred_at_unix=None,
            time_precision=started[1],
            time_basis="user_entered",
            capture_scope=CAPTURE_SCOPE,
            source_event_key=started_key,
        )
    else:
        # No fallback for start dates: nothing in the data says when I started.
        core.delete_event(conn, SOURCE, started_key)

    finished_key = f"{anime_id}:finished"
    finished = date_event_time(entry["finish_date"])
    if finished:
        occurred_at, unix, precision, basis = finished[0], None, finished[1], "user_entered"
    elif entry["status"] == "completed":
        # Fallback: the last time I edited the entry. An upper bound, and it can
        # drift if I edit the entry again later.
        occurred_at = entry["mal_updated_at"]
        unix = utc_to_unix(occurred_at)
        precision, basis = "second", "fallback_list_updated_at"
    else:
        core.delete_event(conn, SOURCE, finished_key)
        return
    core.upsert_event(
        conn,
        item_id=item_id,
        source=SOURCE,
        event_type="watch_finished",
        occurred_at_utc=occurred_at,
        occurred_at_unix=unix,
        time_precision=precision,
        time_basis=basis,
        capture_scope=CAPTURE_SCOPE,
        source_event_key=finished_key,
    )


def _apply_removals(conn: sqlite3.Connection, run_id: int, now: str) -> int:
    """Entries this complete run didn't see were removed from my list.

    Staging keeps the row (with removed_at set). Core drops my current-state
    signals for it: rating, curation, and watch events.
    """
    gone = conn.execute(
        "SELECT mal_anime_id FROM stg_mal_list_entries "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (run_id,),
    ).fetchall()
    for row in gone:
        anime_id = row["mal_anime_id"]
        conn.execute(
            "UPDATE stg_mal_list_entries SET removed_at = ? WHERE mal_anime_id = ?",
            (now, anime_id),
        )
        item_id = core.find_item(conn, SOURCE, "mal_anime_id", str(anime_id))
        if item_id is not None:
            core.delete_rating(conn, item_id, SOURCE)
            core.delete_curation(conn, item_id, SOURCE)
            core.delete_event(conn, SOURCE, f"{anime_id}:started")
            core.delete_event(conn, SOURCE, f"{anime_id}:finished")
    return len(gone)
