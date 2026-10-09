"""Last.fm sync: scrobbles from user.getrecenttracks.

Last.fm only knows about plays from devices that scrobble to it, which varies by
person. Every play event is stored with the profile's capture scope setting
(desktop_only, mobile_only, all_devices, or unknown) as it was when synced.

Why windows instead of "fetch everything newer than my latest scrobble"?
Last.fm returns newest first. If a first full sync dies on page 5 of 40, only the
newest scrobbles are stored, and a naive "newer than latest" query would never go
back for the older pages. So each run fetches a fixed time window [from, to) and
records a cursor (the oldest scrobble committed so far) in sync_lastfm_windows.
An interrupted window stays 'open' and the next run finishes it first.

API behavior this relies on (checked against the live API): `from` is inclusive,
`to` is exclusive.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from taste import core, local_time
from taste import settings as profile_settings
from taste.config import LastfmSettings
from taste.db import transaction, unix_to_utc, utc_now
from taste.http_client import RETRYABLE_HTTP_STATUSES, ApiError, HttpClient
from taste.raw import store_page
from taste.sync_log import SyncRun

SOURCE = "lastfm"
API_URL = "https://ws.audioscrobbler.com/2.0/"
METHOD = "user.getrecenttracks"
PAGE_LIMIT = 200
# Last.fm accepts scrobbles timestamped up to 14 days in the past, so by default
# each incremental run re-checks that far back (the lastfm_lookback_days setting).
# Dedupe makes the overlap harmless.
DAY_SECONDS = 24 * 60 * 60
KEY_SEPARATOR = "\x1f"  # joins names into identity keys; can't appear in a title

# Last.fm error codes that mean "try again later".
RETRYABLE_ERRORS = {
    8: "operation failed",
    11: "service offline",
    16: "service temporarily unavailable",
    29: "rate limit exceeded",
}


@dataclass
class SyncResult:
    source: str
    status: str
    run_id: int
    mode: str
    pages_fetched: int
    rows_fetched: int
    rows_inserted: int
    events_loaded: int = 0
    error: str | None = None


def check_error(status: int, data: Any) -> None:
    """Raise for Last.fm errors. Shared by every Last.fm call."""
    # Last.fm can return an error body with HTTP 200, so check the body first.
    if isinstance(data, dict) and "error" in data:
        code = data.get("error")
        message = data.get("message", "")
        raise ApiError(
            f"Last.fm error {code}: {message}",
            retryable=code in RETRYABLE_ERRORS,
            status=status,
        )
    if status in RETRYABLE_HTTP_STATUSES:
        raise ApiError(f"Last.fm HTTP {status}", retryable=True, status=status)
    if status >= 400:
        raise ApiError(f"Last.fm HTTP {status}", retryable=False, status=status)


def check_response(status: int, data: Any) -> None:
    check_error(status, data)
    if not isinstance(data, dict) or "recenttracks" not in data:
        # Seen once during API probing: a response without the expected key that
        # worked on the next try.
        raise ApiError("Last.fm response is missing 'recenttracks'", retryable=True, status=status)


def sync(
    conn: sqlite3.Connection,
    client: HttpClient,
    settings: LastfmSettings,
    out: Callable[[str], None] = print,
    *,
    now: Callable[[], int] = lambda: int(time.time()),
    lookback_seconds: int | None = None,
    page_limit: int = PAGE_LIMIT,
) -> SyncResult:
    """Sync scrobbles. `lookback_seconds` overrides the profile setting (for tests)."""
    run = SyncRun(conn, SOURCE, "incremental")
    events_loaded = 0
    scope = profile_settings.get(conn, "lastfm_capture_scope")
    if lookback_seconds is None:
        lookback_seconds = profile_settings.get(conn, "lastfm_lookback_days") * DAY_SECONDS
    try:
        # Catch up on anything a crashed run left in staging but not in core.
        with transaction(conn):
            events_loaded += load_core(conn, scope)

        open_windows = conn.execute(
            "SELECT * FROM sync_lastfm_windows WHERE status = 'open' ORDER BY window_id"
        ).fetchall()
        high_water = conn.execute(
            "SELECT MAX(to_unix) FROM sync_lastfm_windows WHERE status = 'complete'"
        ).fetchone()[0]
        if open_windows:
            run.set_mode("resume")
        elif high_water is None:
            run.set_mode("full")

        for window in open_windows:
            out(f"Last.fm: resuming an interrupted sync (window {window['window_id']})")
            _fetch_window(conn, client, settings, run, window, out, page_limit)

        high_water = conn.execute(
            "SELECT MAX(to_unix) FROM sync_lastfm_windows WHERE status = 'complete'"
        ).fetchone()[0]
        from_unix = 0 if high_water is None else max(0, high_water - lookback_seconds)
        window_id = conn.execute(
            "INSERT INTO sync_lastfm_windows (opened_by_run_id, from_unix, to_unix, status) "
            "VALUES (?, ?, ?, 'open')",
            (run.run_id, from_unix, now()),
        ).lastrowid
        window = conn.execute(
            "SELECT * FROM sync_lastfm_windows WHERE window_id = ?", (window_id,)
        ).fetchone()
        _fetch_window(conn, client, settings, run, window, out, page_limit)

        with transaction(conn):
            events_loaded += load_core(conn, scope)
            local_time.refresh(conn)
        run.finish("success")
        status, error = "success", None
        out(
            f"Last.fm: {run.rows_fetched} scrobbles fetched, {run.rows_inserted} new "
            f"(capture scope: {profile_settings.CAPTURE_SCOPES[scope].lower()})"
        )
    except ApiError as exc:
        status, error = "failed", str(exc)
        run.finish(status, error)
        out(f"Last.fm: sync failed, will resume next run: {error}")
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
    return SyncResult(
        SOURCE,
        status,
        run.run_id,
        run.mode,
        run.pages_fetched,
        run.rows_fetched,
        run.rows_inserted,
        events_loaded,
        error,
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_window(
    conn: sqlite3.Connection,
    client: HttpClient,
    settings: LastfmSettings,
    run: SyncRun,
    window: sqlite3.Row,
    out: Callable[[str], None],
    page_limit: int,
) -> None:
    """Fetch one window newest-first, committing each page with the window cursor."""
    window_id = window["window_id"]
    cursor = window["cursor_unix"]
    # `to` is exclusive, so +1 re-fetches the cursor's own second. Dedupe drops repeats.
    upper = window["to_unix"] if cursor is None else min(window["to_unix"], cursor + 1)
    page = 1
    while True:
        params = {
            "method": METHOD,
            "user": settings.username,
            "api_key": settings.api_key,
            "format": "json",
            "limit": page_limit,
            "from": window["from_unix"],
            "to": upper,
            "page": page,
            "extended": 0,
        }
        response = client.get_json(API_URL, params=params, checker=check_response)
        recent = response.data["recenttracks"]
        total_pages = int((recent.get("@attr") or {}).get("totalPages") or 0)
        scrobbles = parse_tracks(recent.get("track"))

        with transaction(conn):
            raw_id = store_page(
                conn,
                sync_run_id=run.run_id,
                source=SOURCE,
                endpoint=METHOD,
                params=params,
                page_number=page,
                http_status=response.status,
                payload=response.data,
            )
            run.rows_inserted += _insert_scrobbles(conn, scrobbles, raw_id)
            run.pages_fetched += 1
            run.rows_fetched += len(scrobbles)
            if scrobbles:
                oldest = min(s["played_at_unix"] for s in scrobbles)
                cursor = oldest if cursor is None else min(cursor, oldest)
            done = page >= total_pages
            conn.execute(
                "UPDATE sync_lastfm_windows SET cursor_unix = ?, status = ?, completed_at = ? "
                "WHERE window_id = ?",
                (cursor, "complete" if done else "open", utc_now() if done else None, window_id),
            )
            run.save()

        out(
            f"Last.fm: page {page} of {max(total_pages, 1)} "
            f"({run.rows_inserted} new scrobbles so far)"
        )
        if done:
            return
        page += 1


def parse_tracks(tracks: Any) -> list[dict[str, Any]]:
    """Clean scrobbles from a recenttracks page, skipping the now-playing track.

    Now-playing has no timestamp and isn't a finished scrobble.
    """
    if tracks is None:
        return []
    if isinstance(tracks, dict):  # Last.fm sends a bare object when there's only one
        tracks = [tracks]
    scrobbles = []
    for track in tracks:
        if (track.get("@attr") or {}).get("nowplaying") == "true" or "date" not in track:
            continue
        uts = int(track["date"]["uts"])
        artist = track.get("artist") or {}
        album = track.get("album") or {}
        scrobbles.append(
            {
                "played_at_unix": uts,
                "played_at_utc": unix_to_utc(uts),
                "artist_name": artist.get("#text", ""),
                "artist_mbid": artist.get("mbid") or "",
                "track_name": track.get("name", ""),
                "track_mbid": track.get("mbid") or "",
                "album_name": album.get("#text") or "",
                "album_mbid": album.get("mbid") or "",
                "track_url": track.get("url"),
            }
        )
    return scrobbles


def _insert_scrobbles(conn: sqlite3.Connection, scrobbles: list[dict[str, Any]], raw_id: int):
    inserted = 0
    now = utc_now()
    for s in scrobbles:
        cursor = conn.execute(
            "INSERT INTO stg_lastfm_scrobbles (played_at_unix, played_at_utc, artist_name, "
            "artist_mbid, track_name, track_mbid, album_name, album_mbid, track_url, "
            "raw_page_id, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (played_at_unix, artist_name, track_name) DO NOTHING",
            (
                s["played_at_unix"],
                s["played_at_utc"],
                s["artist_name"],
                s["artist_mbid"],
                s["track_name"],
                s["track_mbid"],
                s["album_name"],
                s["album_mbid"],
                s["track_url"],
                raw_id,
                now,
            ),
        )
        inserted += cursor.rowcount
    return inserted


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def event_key(played_at_unix: int, artist_name: str, track_name: str) -> str:
    return f"{played_at_unix}|{artist_name}|{track_name}"


def load_core(conn: sqlite3.Connection, capture_scope: str) -> int:
    """Load staging scrobbles that aren't in core yet. Returns how many events were added.

    New events get `capture_scope`. Events already in core keep the scope they had.
    """
    rows = conn.execute(
        "SELECT s.* FROM stg_lastfm_scrobbles s WHERE NOT EXISTS ("
        "  SELECT 1 FROM core_behavior_events e WHERE e.source = ? AND e.source_event_key = "
        "  CAST(s.played_at_unix AS TEXT) || '|' || s.artist_name || '|' || s.track_name"
        ") ORDER BY s.played_at_unix",
        (SOURCE,),
    ).fetchall()

    artists: dict[str, int] = {}
    tracks: dict[str, int] = {}
    albums: dict[str, int] = {}
    album_links: set[tuple[int, int]] = set()
    added = 0
    for row in rows:
        artist_key = row["artist_name"].lower()
        creator_id = artists.get(artist_key)
        if creator_id is None:
            creator_id = core.upsert_creator(
                conn,
                source=SOURCE,
                id_type="lastfm_artist_key",
                external_id=artist_key,
                name=row["artist_name"],
            )
            artists[artist_key] = creator_id
        if row["artist_mbid"]:
            core.add_creator_external_id(
                conn, creator_id, SOURCE, "musicbrainz_artist", row["artist_mbid"]
            )

        track_key = f"{artist_key}{KEY_SEPARATOR}{row['track_name'].lower()}"
        track_id = tracks.get(track_key)
        if track_id is None:
            # refresh=False: rows load oldest first, so the oldest scrobble's spelling
            # becomes the display title and doesn't flip when a new spelling shows up.
            track_id = core.upsert_item(
                conn,
                source=SOURCE,
                id_type="lastfm_track_key",
                external_id=track_key,
                media_type="track",
                title=row["track_name"],
                refresh=False,
            )
            core.link_item_creator(conn, track_id, creator_id, "artist", SOURCE)
            tracks[track_key] = track_id
        if row["track_mbid"]:
            core.add_item_external_id(
                conn, track_id, SOURCE, "musicbrainz_recording", row["track_mbid"]
            )

        if row["album_name"]:
            album_key = f"{artist_key}{KEY_SEPARATOR}{row['album_name'].lower()}"
            album_id = albums.get(album_key)
            if album_id is None:
                album_id = core.upsert_item(
                    conn,
                    source=SOURCE,
                    id_type="lastfm_album_key",
                    external_id=album_key,
                    media_type="album",
                    title=row["album_name"],
                    refresh=False,
                )
                core.link_item_creator(conn, album_id, creator_id, "artist", SOURCE)
                albums[album_key] = album_id
            if (album_id, track_id) not in album_links:
                core.link_items(conn, album_id, track_id, "appears_on", SOURCE)
                album_links.add((album_id, track_id))
            if row["album_mbid"]:
                core.add_item_external_id(
                    conn, album_id, SOURCE, "musicbrainz_release", row["album_mbid"]
                )

        added += core.upsert_event(
            conn,
            item_id=track_id,
            source=SOURCE,
            event_type="play",
            occurred_at_utc=row["played_at_utc"],
            occurred_at_unix=row["played_at_unix"],
            time_precision="second",
            time_basis="source_timestamp",
            capture_scope=capture_scope,
            source_event_key=event_key(
                row["played_at_unix"], row["artist_name"], row["track_name"]
            ),
        )
    return added
