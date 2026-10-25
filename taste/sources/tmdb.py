"""TMDB: my film and TV ratings, watchlist, and favorites, and details for each title.

Two credentials, both kept in the OS credential store:
- an API read access token, free from my TMDB account's API settings, for details;
- a user access token, which the app gets when I approve it on themoviedb.org
  (TMDB's v4 sign-in: ask for a request token, open the approval page, then swap
  the approved request token for an access token and my account id). It reads only.

A sync reads my rated films and shows, watchlist, and favorites whole (they're
small, like the MAL list), then fetches details for anything new or stale. TMDB
keeps when I rated something, not when I watched it, so everything built on these
dates says "rated", never "watched". There's no diary: no watch dates, no rewatches.

Checked against TMDB's documentation, not yet against a live account: the response
fields read here (results[].account_rating.value and .created_at, total_pages,
account_id) need confirming with a test call before release.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from taste import core, local_time
from taste.db import TIMESTAMP_FORMAT, iso_to_utc, transaction, utc_now, utc_to_unix
from taste.http_client import RETRYABLE_HTTP_STATUSES, ApiError, HttpClient
from taste.raw import store_page
from taste.sync_log import SyncRun

SOURCE = "tmdb"
API = "https://api.themoviedb.org"
APPROVE_URL = "https://www.themoviedb.org/auth/access?request_token={}"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
TITLE_URL = {
    "movie": "https://www.themoviedb.org/movie/{}",
    "tv": "https://www.themoviedb.org/tv/{}",
}
RATING_MIN, RATING_MAX = 0.5, 10.0
COMMUNITY_MIN, COMMUNITY_MAX = 0.0, 10.0
CAPTURE_SCOPE = "self_reported"
MEDIA = {"movie": "film", "tv": "tv_show"}
ID_TYPES = {"movie": "tmdb_movie_id", "tv": "tmdb_tv_id"}
LISTS = {"watchlist": "watchlist", "favorites": "favorite"}
REFRESH_DAYS = 30
MAX_PAGES = 200  # a safety stop; 20 results a page


@dataclass(frozen=True)
class TmdbSettings:
    read_token: str
    user_token: str | None = None
    account_id: str | None = None


@dataclass
class SyncResult:
    source: str
    status: str
    run_id: int
    rated: int
    inserted: int
    removed: int
    error: str | None = None


def check_response(status: int, data: Any) -> None:
    if status in RETRYABLE_HTTP_STATUSES:
        raise ApiError(f"TMDB HTTP {status}", retryable=True, status=status)
    if status >= 400:
        message = data.get("status_message") if isinstance(data, dict) else ""
        raise ApiError(f"TMDB HTTP {status}: {message}", retryable=False, status=status)
    if isinstance(data, dict) and data.get("success") is False:
        raise ApiError(f"TMDB: {data.get('status_message', 'refused')}", retryable=False)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def poster_url(path: str | None) -> str | None:
    return f"{IMAGE_BASE}{path}" if path and path.startswith("/") else None


# ---------------------------------------------------------------------------
# Connecting my account
# ---------------------------------------------------------------------------


def check_token(client: HttpClient, read_token: str) -> None:
    """One cheap call that fails on a bad read token."""
    client.get_json(f"{API}/3/authentication", headers=bearer(read_token), checker=check_response)


def request_token(client: HttpClient, read_token: str) -> str:
    """Step one: a request token to approve. Returns it; open APPROVE_URL with it."""
    response = client.request_json(
        "POST", f"{API}/4/auth/request_token", body={}, headers=bearer(read_token),
        checker=check_response,
    )  # fmt: skip
    token = response.data.get("request_token")
    if not token:
        raise ApiError("TMDB sent no request token", retryable=False)
    return token


def access_token(client: HttpClient, read_token: str, approved: str) -> tuple[str, str]:
    """Step two, after approving on themoviedb.org: (user access token, account id)."""
    response = client.request_json(
        "POST",
        f"{API}/4/auth/access_token",
        body={"request_token": approved},
        headers=bearer(read_token),
        checker=check_response,
    )
    token, account = response.data.get("access_token"), response.data.get("account_id")
    if not token or not account:
        raise ApiError(
            "TMDB didn't return an access token. Was the request approved?", retryable=False
        )
    return token, str(account)


def revoke(client: HttpClient, read_token: str, user_token: str) -> None:
    """Disconnect: ask TMDB to forget the access token (best effort)."""
    client.request_json(
        "DELETE",
        f"{API}/4/auth/access_token",
        body={"access_token": user_token},
        headers=bearer(read_token),
        checker=check_response,
    )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _pages(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: TmdbSettings,
    run: SyncRun,
    path: str,
    out: Callable[[str], None],
) -> list[tuple[int, dict[str, Any]]]:
    """Every page of one account list, each stored raw. [(raw page id, result)]."""
    found: list[tuple[int, dict[str, Any]]] = []
    page, total = 1, 1
    while page <= min(total, MAX_PAGES):
        params = {"page": page}
        response = client.get_json(
            f"{API}/4/account/{cfg.account_id}/{path}",
            params=params,
            headers=bearer(cfg.user_token),
            checker=check_response,
        )
        with transaction(conn):
            raw_id = store_page(
                conn,
                sync_run_id=run.run_id,
                source=SOURCE,
                endpoint=f"/4/account/{{id}}/{path}",
                params=params,
                page_number=page,
                http_status=response.status,
                payload=response.data,
            )
            run.pages_fetched += 1
            run.save()
        results = response.data.get("results") or []
        found += [(raw_id, r) for r in results if isinstance(r, dict) and "id" in r]
        total = int(response.data.get("total_pages") or 1)
        out(f"TMDB: {path.replace('/', ' ')}, page {page} of {max(total, 1)}")
        page += 1
    return found


def sync(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: TmdbSettings,
    out: Callable[[str], None] = print,
    *,
    now: datetime | None = None,
) -> SyncResult:
    run = SyncRun(conn, SOURCE, "full")
    stamp = utc_now()
    try:
        rated = {kind: _pages(conn, client, cfg, run, f"{kind}/rated", out) for kind in MEDIA}
        lists = {
            (kind, name): _pages(conn, client, cfg, run, f"{kind}/{name}", out)
            for kind in MEDIA
            for name in LISTS
        }
        with transaction(conn):
            for kind, rows in rated.items():
                for raw_id, item in rows:
                    run.rows_fetched += 1
                    run.rows_inserted += _upsert_rating(conn, kind, item, run.run_id, raw_id, stamp)
            for (kind, name), rows in lists.items():
                for _, item in rows:
                    _upsert_list(conn, kind, name, item, run.run_id)
            removed = _apply_removals(conn, run.run_id, stamp)
            for kind, rows in rated.items():
                for _, item in rows:
                    _load_core(conn, kind, int(item["id"]))
            for (kind, _), rows in lists.items():
                for _, item in rows:
                    _load_core(conn, kind, int(item["id"]))
            local_time.refresh(conn)
            run.save()
        fetch_details(conn, client, cfg, run, out, now=now)
        run.finish("success")
        out(f"TMDB: {run.rows_fetched} ratings, {run.rows_inserted} new, {removed} removed")
        return SyncResult(
            SOURCE, "success", run.run_id, run.rows_fetched, run.rows_inserted, removed
        )
    except ApiError as exc:
        run.finish("failed", str(exc))
        out(f"TMDB: sync failed: {exc}")
        return SyncResult(SOURCE, "failed", run.run_id, run.rows_fetched, 0, 0, str(exc))
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise


def _title(item: dict[str, Any]) -> str:
    return item.get("title") or item.get("name") or f"TMDB #{item.get('id')}"


def _upsert_rating(
    conn: sqlite3.Connection, kind: str, item: dict, run_id: int, raw_id: int, now: str
) -> int:
    """Returns 1 for a rating seen for the first time."""
    account = item.get("account_rating") or {}
    value = account.get("value", item.get("rating"))
    if value is None:
        return 0
    created = account.get("created_at")
    rated_at = iso_to_utc(created) if created else None
    existing = conn.execute(
        "SELECT 1 FROM stg_tmdb_account_ratings WHERE media_type = ? AND tmdb_id = ? "
        "AND season = 0 AND episode = 0",
        (kind, item["id"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO stg_tmdb_account_ratings (media_type, tmdb_id, season, episode, title, "
        "rating, rated_at, first_seen_at, last_seen_run_id, last_raw_page_id) "
        "VALUES (?, ?, 0, 0, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (media_type, tmdb_id, season, episode) DO UPDATE SET "
        "title = excluded.title, rating = excluded.rating, rated_at = excluded.rated_at, "
        "last_seen_run_id = excluded.last_seen_run_id, "
        "last_raw_page_id = excluded.last_raw_page_id, removed_at = NULL",
        (kind, item["id"], _title(item), float(value), rated_at, now, run_id, raw_id),
    )
    # Until details arrive, what the list said about the title is enough for core.
    _seed_details(conn, kind, item, raw_id)
    return 0 if existing else 1


def _upsert_list(conn: sqlite3.Connection, kind: str, name: str, item: dict, run_id: int) -> None:
    conn.execute(
        "INSERT INTO stg_tmdb_account_lists (media_type, tmdb_id, list_name, title, "
        "last_seen_run_id) VALUES (?, ?, ?, ?, ?) ON CONFLICT (media_type, tmdb_id, list_name) "
        "DO UPDATE SET title = excluded.title, last_seen_run_id = excluded.last_seen_run_id, "
        "removed_at = NULL",
        (kind, item["id"], name, _title(item), run_id),
    )
    _seed_details(conn, kind, item, None)


def _seed_details(conn: sqlite3.Connection, kind: str, item: dict, raw_id: int | None) -> None:
    """A details row from a list entry, kept only until real details are fetched."""
    if kind == "movie":
        conn.execute(
            "INSERT INTO stg_tmdb_movies (tmdb_id, title, original_title, release_date, "
            "vote_average, vote_count, poster_path, original_language, raw_page_id, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '') ON CONFLICT (tmdb_id) DO NOTHING",
            (
                item["id"],
                _title(item),
                item.get("original_title"),
                item.get("release_date") or None,
                item.get("vote_average"),
                item.get("vote_count"),
                item.get("poster_path"),
                item.get("original_language"),
                raw_id,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO stg_tmdb_tv (tmdb_id, name, original_name, first_air_date, "
            "vote_average, vote_count, poster_path, original_language, origin_country_json, "
            "raw_page_id, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '') ON CONFLICT (tmdb_id) DO NOTHING",
            (
                item["id"],
                _title(item),
                item.get("original_name"),
                item.get("first_air_date") or None,
                item.get("vote_average"),
                item.get("vote_count"),
                item.get("poster_path"),
                *_origin(item),
                raw_id,
            ),
        )


def _apply_removals(conn: sqlite3.Connection, run_id: int, now: str) -> int:
    """Ratings and list entries this complete read didn't see were removed on TMDB."""
    gone = conn.execute(
        "SELECT media_type, tmdb_id FROM stg_tmdb_account_ratings "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (run_id,),
    ).fetchall()
    conn.execute(
        "UPDATE stg_tmdb_account_ratings SET removed_at = ? "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (now, run_id),
    )
    for kind, tmdb_id in gone:
        item_id = core.find_item(conn, SOURCE, ID_TYPES[kind], str(tmdb_id))
        if item_id is not None:
            core.delete_rating(conn, item_id, SOURCE)
            core.delete_event(conn, SOURCE, f"{kind}:{tmdb_id}:rated")
    lists_gone = conn.execute(
        "SELECT media_type, tmdb_id, list_name FROM stg_tmdb_account_lists "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (run_id,),
    ).fetchall()
    conn.execute(
        "UPDATE stg_tmdb_account_lists SET removed_at = ? "
        "WHERE removed_at IS NULL AND last_seen_run_id <> ?",
        (now, run_id),
    )
    for kind, tmdb_id, name in lists_gone:
        item_id = core.find_item(conn, SOURCE, ID_TYPES[kind], str(tmdb_id))
        if item_id is not None:
            conn.execute(
                "DELETE FROM core_curation WHERE item_id = ? AND source = ? AND curation_type = ?",
                (item_id, SOURCE, LISTS[name]),
            )
    return len(gone)


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------


def _detail_ids(
    conn: sqlite3.Connection, now: datetime, account_only: bool = True
) -> list[tuple[str, int]]:
    """Titles whose details were never fetched, or are older than REFRESH_DAYS.

    `account_only`: just what's on my account (rated, watchlist, favorites). Suggestion
    candidates are known from lists too, and a sync must not fetch them all; the
    recommendations refresh fetches the strongest ones.
    """
    cutoff = (now - timedelta(days=REFRESH_DAYS)).strftime(TIMESTAMP_FORMAT)
    mine = (
        " AND tmdb_id IN (SELECT tmdb_id FROM stg_tmdb_account_ratings WHERE media_type = ? "
        "AND removed_at IS NULL UNION SELECT tmdb_id FROM stg_tmdb_account_lists "
        "WHERE media_type = ? AND removed_at IS NULL)"
        if account_only
        else ""
    )
    found = []
    for kind, table in (("movie", "stg_tmdb_movies"), ("tv", "stg_tmdb_tv")):
        params = (cutoff, kind, kind) if account_only else (cutoff,)
        # A title that answered 404 lately is gone from TMDB: not asked again for a while.
        gone = (
            " AND tmdb_id NOT IN (SELECT tmdb_id FROM stg_tmdb_fetches WHERE method = ? "
            "AND fetched_at >= ?)"
        )
        found += [
            (kind, r[0])
            for r in conn.execute(
                f"SELECT tmdb_id FROM {table} WHERE fetched_at < ?{mine}{gone} ORDER BY tmdb_id",
                (*params, f"gone_{kind}", cutoff),
            )
        ]
    return found


def fetch_details(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: TmdbSettings,
    run: SyncRun,
    out: Callable[[str], None],
    *,
    now: datetime | None = None,
    ids: list[tuple[str, int]] | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    todo = ids if ids is not None else _detail_ids(conn, now)
    for i, (kind, tmdb_id) in enumerate(todo, 1):
        params = {"append_to_response": "credits"} if kind == "movie" else {}
        try:
            response = client.get_json(
                f"{API}/3/{kind}/{tmdb_id}", params=params, headers=bearer(cfg.read_token),
                checker=check_response,
            )  # fmt: skip
        except ApiError as exc:
            if exc.status == 404:
                # Removed from TMDB; the list's own fields stay. Noted so it isn't asked for
                # on every sync (fetched_at stays as it was, so it never counts as detailed).
                conn.execute(
                    "INSERT INTO stg_tmdb_fetches (tmdb_id, method, fetched_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (tmdb_id, method) DO UPDATE SET fetched_at = excluded.fetched_at",
                    (tmdb_id, f"gone_{kind}", now.strftime(TIMESTAMP_FORMAT)),
                )
                continue
            raise
        with transaction(conn):
            raw_id = store_page(
                conn,
                sync_run_id=run.run_id,
                source=SOURCE,
                endpoint=f"/3/{kind}/{{id}}",
                params={"id": tmdb_id, **params},
                page_number=None,
                http_status=response.status,
                payload=response.data,
            )
            save_details(conn, kind, response.data, raw_id, now.strftime(TIMESTAMP_FORMAT))
            _load_core(conn, kind, tmdb_id)
            run.pages_fetched += 1
            run.save()
        if i % 10 == 0 or i == len(todo):
            out(f"TMDB: details, {i} of {len(todo)}")
    return len(todo)


def save_details(
    conn: sqlite3.Connection, kind: str, data: dict[str, Any], raw_id: int | None, fetched_at: str
) -> None:
    genres = json.dumps([g.get("name") for g in data.get("genres") or []], ensure_ascii=False)
    if kind == "movie":
        crew = (data.get("credits") or {}).get("crew") or []
        directors = [
            {"id": c.get("id"), "name": c.get("name")}
            for c in crew
            if c.get("job") == "Director" and c.get("name")
        ]
        conn.execute(
            "INSERT INTO stg_tmdb_movies (tmdb_id, title, original_title, release_date, runtime, "
            "vote_average, vote_count, poster_path, original_language, imdb_id, genres_json, "
            "directors_json, raw_page_id, fetched_at, overview, popularity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tmdb_id) DO UPDATE SET title = excluded.title, "
            "original_title = excluded.original_title, release_date = excluded.release_date, "
            "runtime = excluded.runtime, vote_average = excluded.vote_average, "
            "vote_count = excluded.vote_count, poster_path = excluded.poster_path, "
            "original_language = excluded.original_language, imdb_id = excluded.imdb_id, "
            "genres_json = excluded.genres_json, directors_json = excluded.directors_json, "
            "raw_page_id = excluded.raw_page_id, fetched_at = excluded.fetched_at, "
            "overview = excluded.overview, popularity = excluded.popularity",
            (
                data["id"],
                _title(data),
                data.get("original_title"),
                data.get("release_date") or None,
                data.get("runtime") or None,
                data.get("vote_average"),
                data.get("vote_count"),
                data.get("poster_path"),
                data.get("original_language"),
                data.get("imdb_id") or None,
                genres,
                json.dumps(directors, ensure_ascii=False),
                raw_id,
                fetched_at,
                (data.get("overview") or "").strip() or None,
                data.get("popularity"),
            ),
        )
        return
    run_time = data.get("episode_run_time") or []
    creators = [{"id": c.get("id"), "name": c.get("name")} for c in data.get("created_by") or []]
    conn.execute(
        "INSERT INTO stg_tmdb_tv (tmdb_id, name, original_name, first_air_date, last_air_date, "
        "status, seasons, episodes, episode_run_time, vote_average, vote_count, poster_path, "
        "networks_json, genres_json, creators_json, raw_page_id, fetched_at, overview, "
        "original_language, origin_country_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (tmdb_id) DO UPDATE SET name = excluded.name, "
        "original_name = excluded.original_name, first_air_date = excluded.first_air_date, "
        "last_air_date = excluded.last_air_date, status = excluded.status, "
        "seasons = excluded.seasons, episodes = excluded.episodes, "
        "episode_run_time = excluded.episode_run_time, vote_average = excluded.vote_average, "
        "vote_count = excluded.vote_count, poster_path = excluded.poster_path, "
        "networks_json = excluded.networks_json, genres_json = excluded.genres_json, "
        "creators_json = excluded.creators_json, overview = excluded.overview, "
        "original_language = excluded.original_language, "
        "origin_country_json = excluded.origin_country_json, "
        "raw_page_id = excluded.raw_page_id, "
        "fetched_at = excluded.fetched_at",
        (
            data["id"],
            _title(data),
            data.get("original_name"),
            data.get("first_air_date") or None,
            data.get("last_air_date") or None,
            data.get("status"),
            data.get("number_of_seasons"),
            data.get("number_of_episodes"),
            run_time[0] if run_time else None,
            data.get("vote_average"),
            data.get("vote_count"),
            data.get("poster_path"),
            json.dumps([n.get("name") for n in data.get("networks") or []], ensure_ascii=False),
            genres,
            json.dumps(creators, ensure_ascii=False),
            raw_id,
            fetched_at,
            (data.get("overview") or "").strip() or None,
            *_origin(data),
        ),
    )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _origin(data: dict[str, Any]) -> tuple[str, str]:
    """A show's original language and countries of origin, '' and '[]' when TMDB has
    none (so a stored row reads as checked, not as unknown)."""
    countries = [c for c in data.get("origin_country") or [] if isinstance(c, str)]
    return data.get("original_language") or "", json.dumps(countries)


def backfill_origins(conn: sqlite3.Connection) -> int:
    """Language and origin for shows fetched before they were kept, read back from the
    stored details response (no network). Returns how many were filled."""
    rows = conn.execute(
        "SELECT t.tmdb_id, p.payload FROM stg_tmdb_tv t "
        "JOIN raw_api_pages p ON p.raw_page_id = t.raw_page_id "
        "WHERE t.original_language IS NULL AND t.fetched_at <> ''"
    ).fetchall()
    for tmdb_id, payload in rows:
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        conn.execute(
            "UPDATE stg_tmdb_tv SET original_language = ?, origin_country_json = ? "
            "WHERE tmdb_id = ?",
            (*_origin(data), tmdb_id),
        )
    return len(rows)


def backfill_overviews(conn: sqlite3.Connection) -> int:
    """Overviews for TV and films, plus film popularity, read back from
    the stored response (no network). Returns how many were filled."""
    rows = conn.execute(
        "SELECT m.tmdb_id, p.payload FROM stg_tmdb_movies m "
        "JOIN raw_api_pages p ON p.raw_page_id = m.raw_page_id "
        "WHERE m.overview IS NULL AND m.popularity IS NULL AND m.fetched_at <> ''"
    ).fetchall()
    filled = 0
    for tmdb_id, payload in rows:
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        overview = (data.get("overview") or "").strip() or None
        if overview is None and data.get("popularity") is None:
            continue
        conn.execute(
            "UPDATE stg_tmdb_movies SET overview = ?, popularity = ? WHERE tmdb_id = ?",
            (overview, data.get("popularity"), tmdb_id),
        )
        filled += 1
    rows = conn.execute(
        "SELECT t.tmdb_id, p.payload FROM stg_tmdb_tv t "
        "JOIN raw_api_pages p ON p.raw_page_id = t.raw_page_id "
        "WHERE t.overview IS NULL AND t.fetched_at <> ''"
    ).fetchall()
    for tmdb_id, payload in rows:
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        overview = (data.get("overview") or "").strip() or None
        if overview:
            conn.execute(
                "UPDATE stg_tmdb_tv SET overview = ? WHERE tmdb_id = ?", (overview, tmdb_id)
            )
            filled += 1
    return filled


def load_title_core(conn: sqlite3.Connection, kind: str, tmdb_id: int) -> int | None:
    """A film or show's own facts (not my opinion of it). Returns its item_id."""
    if kind == "movie":
        row = conn.execute("SELECT * FROM stg_tmdb_movies WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
        if row is None:
            return None
        title, date_text, people, role = (
            row["title"],
            row["release_date"],
            row["directors_json"],
            "director",
        )
    else:
        row = conn.execute("SELECT * FROM stg_tmdb_tv WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
        if row is None:
            return None
        title, date_text, people, role = (
            row["name"],
            row["first_air_date"],
            row["creators_json"],
            "creator",
        )
    original = row["original_title"] if kind == "movie" else row["original_name"]
    item_id = core.upsert_item(
        conn,
        source=SOURCE,
        id_type=ID_TYPES[kind],
        external_id=str(tmdb_id),
        media_type=MEDIA[kind],
        title=title,
        title_alt=original if original and original != title else None,
        release_year=int(date_text[:4]) if date_text and date_text[:4].isdigit() else None,
    )
    if kind == "movie" and row["imdb_id"]:
        core.add_item_external_id(conn, item_id, SOURCE, "imdb_id", row["imdb_id"])
    creator_ids = [
        core.upsert_creator(
            conn, source=SOURCE, id_type="tmdb_person_id", external_id=str(p["id"]), name=p["name"]
        )
        for p in json.loads(people or "[]")
        if p.get("id") and p.get("name")
    ]
    core.set_item_creators(conn, item_id, SOURCE, role, creator_ids)
    genres = json.loads(row["genres_json"] or "[]")
    core.set_item_tags(conn, item_id, SOURCE, [core.tag_id(conn, g, "genre") for g in genres if g])
    if row["vote_average"] and row["vote_count"]:
        core.upsert_community_rating(
            conn,
            item_id=item_id,
            source=SOURCE,
            mean_score=row["vote_average"],
            scale_min=COMMUNITY_MIN,
            scale_max=COMMUNITY_MAX,
            num_raters=row["vote_count"],
        )
    poster = poster_url(row["poster_path"])
    if poster:
        core.set_item_image(conn, item_id, SOURCE, "poster", poster)
    return item_id


def _load_core(conn: sqlite3.Connection, kind: str, tmdb_id: int) -> None:
    item_id = load_title_core(conn, kind, tmdb_id)
    if item_id is None:
        return
    rating = conn.execute(
        "SELECT rating, rated_at FROM stg_tmdb_account_ratings WHERE media_type = ? "
        "AND tmdb_id = ? AND season = 0 AND episode = 0 AND removed_at IS NULL",
        (kind, tmdb_id),
    ).fetchone()
    key = f"{kind}:{tmdb_id}:rated"
    if rating:
        core.upsert_rating(
            conn,
            item_id=item_id,
            source=SOURCE,
            raw_score=rating[0],
            scale_min=RATING_MIN,
            scale_max=RATING_MAX,
            source_updated_at=rating[1],
        )
        if rating[1]:
            core.upsert_event(
                conn,
                item_id=item_id,
                source=SOURCE,
                event_type="rated",
                occurred_at_utc=rating[1],
                occurred_at_unix=utc_to_unix(rating[1]),
                time_precision="second",
                time_basis="rating_created_at",
                capture_scope=CAPTURE_SCOPE,
                source_event_key=key,
            )
    else:
        core.delete_rating(conn, item_id, SOURCE)
        core.delete_event(conn, SOURCE, key)
    for name, curation in LISTS.items():
        on = conn.execute(
            "SELECT 1 FROM stg_tmdb_account_lists WHERE media_type = ? AND tmdb_id = ? "
            "AND list_name = ? AND removed_at IS NULL",
            (kind, tmdb_id, name),
        ).fetchone()
        if on:
            core.upsert_curation(
                conn, item_id=item_id, source=SOURCE, curation_type=curation, value="1"
            )


# ---------------------------------------------------------------------------
# Film and TV suggestion candidates
# ---------------------------------------------------------------------------

CANDIDATE_DETAILS = 120  # details for this many of the strongest candidates


def my_rating_mean(conn: sqlite3.Connection, kind: str = "movie") -> float | None:
    return conn.execute(
        "SELECT AVG(rating) FROM stg_tmdb_account_ratings WHERE media_type = ? "
        "AND removed_at IS NULL",
        (kind,),
    ).fetchone()[0]


def film_seeds(conn: sqlite3.Connection) -> list[int]:
    """Films I rated above my own film average, best first."""
    return recommendation_seeds(conn, "movie")


def recommendation_seeds(conn: sqlite3.Connection, kind: str = "movie") -> list[int]:
    """Films or shows rated above my own average for that medium, best first."""
    mean = my_rating_mean(conn, kind)
    if mean is None:
        return []
    return [
        r[0]
        for r in conn.execute(
            "SELECT tmdb_id FROM stg_tmdb_account_ratings WHERE media_type = ? "
            "AND removed_at IS NULL AND rating > ? ORDER BY rating DESC, tmdb_id",
            (kind, mean),
        )
    ]


def candidate_support(conn: sqlite3.Connection, kind: str = "movie") -> dict[int, float]:
    """Unrated titles, by how strongly liked titles of that medium point at them. Each
    liked title adds (my rating - my average), a little less the further down its list."""
    mean = my_rating_mean(conn, kind)
    if mean is None:
        return {}
    rated = {
        r[0]
        for r in conn.execute(
            "SELECT tmdb_id FROM stg_tmdb_account_ratings WHERE media_type = ? "
            "AND removed_at IS NULL",
            (kind,),
        )
    }
    table = "stg_tmdb_movie_recommendations" if kind == "movie" else "stg_tmdb_tv_recommendations"
    support: dict[int, float] = {}
    for rec_id, position, rating in conn.execute(
        f"SELECT r.recommended_id, r.position, a.rating FROM {table} r "
        "JOIN stg_tmdb_account_ratings a ON a.tmdb_id = r.tmdb_id AND a.media_type = ? "
        "WHERE a.removed_at IS NULL AND a.rating > ?",
        (kind, mean),
    ):
        if rec_id not in rated:
            support[rec_id] = support.get(rec_id, 0.0) + (rating - mean) / (1 + position / 10)
    return support


def enrich_recommendations(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: TmdbSettings,
    out: Callable[[str], None] = print,
    *,
    now: datetime | None = None,
) -> int:
    """Cache film and TV recommendation lists and fetch the strongest 120 of each."""
    with transaction(conn):
        backfill_overviews(conn)
    return sum(
        _enrich_recommendations(conn, client, cfg, out, now=now, kind=kind)
        for kind in ("movie", "tv")
    )


def _enrich_recommendations(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: TmdbSettings,
    out: Callable[[str], None] = print,
    *,
    now: datetime | None = None,
    kind: str = "movie",
) -> int:
    """TMDB's recommendations for liked titles (cached 30 days), then details for
    the strongest candidates. Returns how many lists were fetched."""
    table = "stg_tmdb_movie_recommendations" if kind == "movie" else "stg_tmdb_tv_recommendations"
    method = "recommendations" if kind == "movie" else "tv_recommendations"
    noun = "films" if kind == "movie" else "TV shows"
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=REFRESH_DAYS)).strftime(TIMESTAMP_FORMAT)
    fresh = {
        r[0]
        for r in conn.execute(
            "SELECT tmdb_id FROM stg_tmdb_fetches WHERE method = ? AND fetched_at >= ?",
            (method, cutoff),
        )
    }
    todo = [i for i in recommendation_seeds(conn, kind) if i not in fresh]
    run = SyncRun(conn, SOURCE, "enrich")
    try:
        for i, tmdb_id in enumerate(todo, 1):
            params = {"page": 1}
            response = client.get_json(
                f"{API}/3/{kind}/{tmdb_id}/recommendations",
                params=params,
                headers=bearer(cfg.read_token),
                checker=check_response,
            )
            with transaction(conn):
                raw_id = store_page(
                    conn,
                    sync_run_id=run.run_id,
                    source=SOURCE,
                    endpoint=f"/3/{kind}/{{id}}/recommendations",
                    params={"id": tmdb_id, **params},
                    page_number=1,
                    http_status=response.status,
                    payload=response.data,
                )
                conn.execute(f"DELETE FROM {table} WHERE tmdb_id = ?", (tmdb_id,))
                for position, item in enumerate(response.data.get("results") or []):
                    if not isinstance(item, dict) or "id" not in item:
                        continue
                    columns = "tmdb_id, recommended_id, recommended_title, position"
                    values = (tmdb_id, item["id"], _title(item), position)
                    if kind == "tv":
                        columns += ", raw_page_id"
                        values += (raw_id,)
                    marks = ", ".join("?" for _ in values)
                    conn.execute(
                        f"INSERT INTO {table} ({columns}) VALUES ({marks}) "
                        "ON CONFLICT (tmdb_id, recommended_id) DO NOTHING",
                        values,
                    )
                    _seed_details(conn, kind, item, raw_id)
                conn.execute(
                    "INSERT INTO stg_tmdb_fetches (tmdb_id, method, fetched_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (tmdb_id, method) DO UPDATE SET fetched_at = excluded.fetched_at",
                    (tmdb_id, method, now.strftime(TIMESTAMP_FORMAT)),
                )
                run.pages_fetched += 1
                run.save()
            if i % 10 == 0 or i == len(todo):
                out(f"Recommendations: {noun} like the ones you liked, {i} of {len(todo)}")
        support = candidate_support(conn, kind)
        strongest = sorted(support, key=lambda i: (-support[i], i))[:CANDIDATE_DETAILS]
        stale = {i for media, i in _detail_ids(conn, now, account_only=False) if media == kind}
        fetch_details(
            conn, client, cfg, run, out, now=now,
            ids=[(kind, i) for i in strongest if i in stale],
        )  # fmt: skip
        run.finish("success")
    except ApiError as exc:
        run.finish("failed", str(exc))
        out(f"Recommendations: TMDB stopped, will continue next time: {exc}")
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
    return len(todo)
