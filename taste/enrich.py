"""Enrichment: the extra API data recommendations and art need.

Everything here is cached in staging and refreshed slowly, so after the first
run each refresh is a few dozen calls:
- MAL show details (community recommendations, related shows, genres, studios)
  for shows I scored above my own average, then for the strongest candidates.
- Last.fm similar artists and top tags for the artists I play most.
- Deezer photos for suggested artists that still have no picture.

Every response is stored in the raw layer like any sync, and each item commits
on its own, so an interrupted run picks up where it stopped.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from taste import core
from taste.config import LastfmSettings, MalSettings
from taste.db import TIMESTAMP_FORMAT, transaction, utc_now
from taste.http_client import RETRYABLE_HTTP_STATUSES, ApiError, HttpClient
from taste.raw import store_page
from taste.sources import deezer, lastfm, mal
from taste.sync_log import SyncRun

REFRESH_DAYS = 30
MAL_MIN_INTERVAL = 0.5  # 2 requests a second; backoff slows it further if MAL pushes back
MAL_DETAIL_FIELDS = ",".join([*mal.ANIME_FIELDS, "recommendations", "related_anime"])
# Relations worth suggesting. Summaries, character crossovers, and "other" aren't.
RELATED_KINDS = {
    "sequel",
    "prequel",
    "side_story",
    "spin_off",
    "alternative_version",
    "alternative_setting",
    "parent_story",
    "full_story",
}
HALF_LIFE_DAYS = 90  # a play from 90 days ago counts half as much as one today
ARTIST_NOT_FOUND = 6  # Last.fm error code


def _cutoff(now: datetime) -> str:
    return (now - timedelta(days=REFRESH_DAYS)).strftime(TIMESTAMP_FORMAT)


def _stamp(now: datetime) -> str:
    return now.strftime(TIMESTAMP_FORMAT)


def refresh_slice(total: int) -> int:
    """How many stale entries to refresh per run, so all of them turn over in about a month."""
    return max(10, math.ceil(total / REFRESH_DAYS))


# ---------------------------------------------------------------------------
# MyAnimeList
# ---------------------------------------------------------------------------


def my_mean_score(conn: sqlite3.Connection) -> float | None:
    return conn.execute(
        "SELECT AVG(score) FROM stg_mal_list_entries WHERE removed_at IS NULL AND score IS NOT NULL"
    ).fetchone()[0]


def seed_ids(conn: sqlite3.Connection) -> list[int]:
    """Shows I scored above my own average, best first. Their recommendations drive mine."""
    mean = my_mean_score(conn)
    if mean is None:
        return []
    rows = conn.execute(
        "SELECT mal_anime_id FROM stg_mal_list_entries WHERE removed_at IS NULL "
        "AND score > ? ORDER BY score DESC, mal_anime_id",
        (mean,),
    ).fetchall()
    return [r[0] for r in rows]


def candidate_support(conn: sqlite3.Connection) -> dict[int, float]:
    """Shows not on my list, scored by how strongly they connect to shows I liked.

    Each liked show adds (my score - my average) * log(1 + users recommending), and
    a related show (a sequel, say) adds (my score - my average).
    """
    mean = my_mean_score(conn)
    if mean is None:
        return {}
    on_list = {
        r[0]
        for r in conn.execute(
            "SELECT mal_anime_id FROM stg_mal_list_entries WHERE removed_at IS NULL"
        )
    }
    support: dict[int, float] = {}
    for row in conn.execute(
        "SELECT r.recommended_id, r.num_recommendations, l.score "
        "FROM stg_mal_anime_recommendations r JOIN stg_mal_list_entries l "
        "ON l.mal_anime_id = r.mal_anime_id WHERE l.removed_at IS NULL AND l.score > ?",
        (mean,),
    ):
        if row[0] not in on_list:
            support[row[0]] = support.get(row[0], 0.0) + (row[2] - mean) * math.log1p(row[1])
    for row in conn.execute(
        "SELECT r.related_id, r.relation_type, l.score "
        "FROM stg_mal_related_anime r JOIN stg_mal_list_entries l "
        "ON l.mal_anime_id = r.mal_anime_id WHERE l.removed_at IS NULL AND l.score > ?",
        (mean,),
    ):
        if row[0] not in on_list and row[1] in RELATED_KINDS:
            support[row[0]] = support.get(row[0], 0.0) + (row[2] - mean)
    return support


def _needs_details(conn: sqlite3.Connection, ids: list[int], now: datetime) -> list[int]:
    """Ids never fetched (in the given order), then a slice of the stalest ones."""
    fetched = dict(
        conn.execute("SELECT mal_anime_id, fetched_at FROM stg_mal_anime_details").fetchall()
    )
    missing = [i for i in ids if i not in fetched]
    cutoff = _cutoff(now)
    stale = sorted((fetched[i], i) for i in ids if i in fetched and fetched[i] < cutoff)
    return missing + [i for _, i in stale[: refresh_slice(len(ids))]]


def _check_detail(status: int, data: Any) -> None:
    if status in RETRYABLE_HTTP_STATUSES:
        raise ApiError(f"MAL HTTP {status}", retryable=True, status=status)
    if status >= 400:
        raise ApiError(f"MAL HTTP {status}", retryable=False, status=status)
    if not isinstance(data, dict) or "id" not in data:
        raise ApiError("MAL details response is missing 'id'", retryable=True, status=status)


def _minimal_item(conn: sqlite3.Connection, node: dict[str, Any]) -> int:
    """A core item for a show we only know by name and poster (no details yet)."""
    if conn.execute("SELECT 1 FROM stg_mal_anime WHERE mal_anime_id = ?", (node["id"],)).fetchone():
        return mal.load_anime_core(conn, node["id"])
    item_id = core.upsert_item(
        conn,
        source=mal.SOURCE,
        id_type="mal_anime_id",
        external_id=str(node["id"]),
        media_type="anime",
        title=node.get("title") or f"MAL #{node['id']}",
        refresh=False,
    )
    poster = mal.poster_url(node)
    if poster:
        core.set_item_image(conn, item_id, mal.SOURCE, "poster", poster)
    return item_id


def save_details(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    raw_id: int | None,
    fetched_at: str | None = None,
) -> None:
    """Load one details response into staging and core."""
    anime_id = data["id"]
    fetched_at = fetched_at or utc_now()
    mal.upsert_stg_anime(conn, data, raw_id, fetched_at)
    conn.execute("DELETE FROM stg_mal_anime_recommendations WHERE mal_anime_id = ?", (anime_id,))
    for rec in data.get("recommendations") or []:
        node = rec["node"]
        conn.execute(
            "INSERT INTO stg_mal_anime_recommendations (mal_anime_id, recommended_id, "
            "recommended_title, num_recommendations) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (mal_anime_id, recommended_id) DO NOTHING",
            (anime_id, node["id"], node.get("title", ""), rec.get("num_recommendations") or 0),
        )
    conn.execute("DELETE FROM stg_mal_related_anime WHERE mal_anime_id = ?", (anime_id,))
    for rel in data.get("related_anime") or []:
        node = rel["node"]
        conn.execute(
            "INSERT INTO stg_mal_related_anime (mal_anime_id, related_id, related_title, "
            "relation_type) VALUES (?, ?, ?, ?) ON CONFLICT (mal_anime_id, related_id) DO NOTHING",
            (anime_id, node["id"], node.get("title", ""), rel.get("relation_type") or "other"),
        )
    conn.execute(
        "INSERT INTO stg_mal_anime_details (mal_anime_id, fetched_at, raw_page_id) "
        "VALUES (?, ?, ?) ON CONFLICT (mal_anime_id) DO UPDATE SET "
        "fetched_at = excluded.fetched_at, raw_page_id = excluded.raw_page_id",
        (anime_id, fetched_at, raw_id),
    )

    item_id = mal.load_anime_core(conn, anime_id)
    conn.execute(
        "DELETE FROM core_item_similarity WHERE item_id = ? AND source = ?", (item_id, mal.SOURCE)
    )
    for rec in data.get("recommendations") or []:
        other = _minimal_item(conn, rec["node"])
        core.set_similarity(
            conn,
            item_id,
            other,
            mal.SOURCE,
            "user_recommended",
            rec.get("num_recommendations") or 0,
        )
    for rel in data.get("related_anime") or []:
        other = _minimal_item(conn, rel["node"])
        kind = f"related_{rel.get('relation_type') or 'other'}"
        core.set_similarity(conn, item_id, other, mal.SOURCE, kind, 1.0)


def _fetch_details(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: MalSettings,
    run: SyncRun,
    ids: list[int],
    label: str,
    out: Callable[[str], None],
    now: datetime,
) -> int:
    fields = {"fields": MAL_DETAIL_FIELDS}
    for i, anime_id in enumerate(ids, 1):
        try:
            response = client.get_json(
                f"{mal.API_BASE}/anime/{anime_id}",
                params=fields,
                headers={"X-MAL-CLIENT-ID": cfg.client_id},
                checker=_check_detail,
            )
        except ApiError as exc:
            if exc.status == 404:
                # Removed from MAL. Note it so we don't ask again every run.
                conn.execute(
                    "INSERT INTO stg_mal_anime_details (mal_anime_id, fetched_at) VALUES (?, ?) "
                    "ON CONFLICT (mal_anime_id) DO UPDATE SET fetched_at = excluded.fetched_at",
                    (anime_id, _stamp(now)),
                )
                continue
            raise
        with transaction(conn):
            raw_id = store_page(
                conn,
                sync_run_id=run.run_id,
                source=mal.SOURCE,
                endpoint="/anime/{id}",
                params={"id": anime_id, **fields},
                page_number=None,
                http_status=response.status,
                payload=response.data,
            )
            save_details(conn, response.data, raw_id, _stamp(now))
            run.pages_fetched += 1
            run.rows_fetched += 1
            run.save()
        if i % 10 == 0 or i == len(ids):
            out(f"Recommendations: details for {label}, {i} of {len(ids)}")
    return len(ids)


def enrich_mal(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: MalSettings,
    out: Callable[[str], None] = print,
    *,
    now: datetime | None = None,
    candidate_limit: int = 200,
) -> int:
    """Fetch what the anime recommender needs. Returns how many details were fetched."""
    now = now or datetime.now(timezone.utc)
    client.min_interval = max(client.min_interval, MAL_MIN_INTERVAL)
    run = SyncRun(conn, mal.SOURCE, "enrich")
    try:
        seeds = seed_ids(conn)
        fetched = _fetch_details(
            conn, client, cfg, run, _needs_details(conn, seeds, now), "shows you liked", out, now
        )
        support = candidate_support(conn)
        top = sorted(support, key=lambda i: (-support[i], i))[:candidate_limit]
        fetched += _fetch_details(
            conn, client, cfg, run, _needs_details(conn, top, now), "candidates", out, now
        )
        run.finish("success")
        return fetched
    except ApiError as exc:
        run.finish("failed", str(exc))
        out(f"Recommendations: MyAnimeList details stopped, will continue next time: {exc}")
        return 0
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------


def artist_key(name: str) -> str:
    return name.lower()


def artist_weights(
    conn: sqlite3.Connection, now_unix: int, days: int = 365
) -> dict[str, tuple[str, float, int, int]]:
    """{artist_key: (display name, recency-weighted plays, plays, last play unix)}.

    Plays within `days` count, each weighted by 0.5 ** (age / 90 days).
    """
    since = now_unix - days * 86400
    result: dict[str, list] = {}
    for name, played in conn.execute(
        "SELECT c.name, e.occurred_at_unix FROM core_behavior_events e "
        "JOIN core_item_creators ic ON ic.item_id = e.item_id AND ic.role = 'artist' "
        "AND ic.source = 'lastfm' JOIN core_creators c ON c.creator_id = ic.creator_id "
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' AND e.occurred_at_unix >= ?",
        (since,),
    ):
        entry = result.setdefault(artist_key(name), [name, 0.0, 0, 0])
        age_days = max(now_unix - played, 0) / 86400
        entry[1] += 0.5 ** (age_days / HALF_LIFE_DAYS)
        entry[2] += 1
        entry[3] = max(entry[3], played)
    return {k: tuple(v) for k, v in result.items()}


def _fresh(conn: sqlite3.Connection, key: str, method: str, now: datetime) -> bool:
    row = conn.execute(
        "SELECT fetched_at FROM stg_lastfm_artist_fetches WHERE artist_key = ? AND method = ?",
        (key, method),
    ).fetchone()
    return row is not None and row[0] >= _cutoff(now)


def _mark_fetched(conn: sqlite3.Connection, key: str, method: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO stg_lastfm_artist_fetches (artist_key, method, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT (artist_key, method) DO UPDATE SET fetched_at = excluded.fetched_at",
        (key, method, _stamp(now)),
    )


def _lastfm_call(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: LastfmSettings,
    run: SyncRun,
    method: str,
    artist: str,
    extra: dict[str, Any],
) -> dict[str, Any] | None:
    """Call one artist.* method, store the raw page. None if Last.fm doesn't know the artist."""
    params = {"method": method, "artist": artist, "api_key": cfg.api_key, "format": "json"}
    params.update(extra)
    try:
        response = client.get_json(lastfm.API_URL, params=params, checker=lastfm.check_error)
    except ApiError as exc:
        if str(exc).startswith(f"Last.fm error {ARTIST_NOT_FOUND}:"):
            return None
        raise  # a bad key or an outage stops the run instead of caching "no data" for a month
    store_page(
        conn,
        sync_run_id=run.run_id,
        source=lastfm.SOURCE,
        endpoint=method,
        params=params,
        page_number=None,
        http_status=response.status,
        payload=response.data,
    )
    run.pages_fetched += 1
    return response.data


def enrich_lastfm(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: LastfmSettings,
    out: Callable[[str], None] = print,
    *,
    now: datetime | None = None,
    seed_count: int = 50,
) -> int:
    """Similar artists and top tags for my top artists. Returns how many were fetched."""
    now = now or datetime.now(timezone.utc)
    run = SyncRun(conn, lastfm.SOURCE, "enrich")
    fetched = 0
    try:
        with transaction(conn):
            lastfm.backfill_images(conn)
        weights = artist_weights(conn, int(now.timestamp()))
        seeds = sorted(weights, key=lambda k: -weights[k][1])[:seed_count]
        todo = [
            k
            for k in seeds
            if not (
                _fresh(conn, k, "artist.getsimilar", now)
                and _fresh(conn, k, "artist.gettoptags", now)
            )
        ]
        for i, key in enumerate(todo, 1):
            name = weights[key][0]
            with transaction(conn):
                similar = _lastfm_call(
                    conn, client, cfg, run, "artist.getsimilar", name, {"limit": 30}
                )
                conn.execute("DELETE FROM stg_lastfm_similar_artists WHERE artist_key = ?", (key,))
                for a in ((similar or {}).get("similarartists") or {}).get("artist") or []:
                    conn.execute(
                        "INSERT INTO stg_lastfm_similar_artists (artist_key, similar_key, "
                        "similar_name, similar_mbid, match) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT (artist_key, similar_key) DO NOTHING",
                        (
                            key,
                            artist_key(a["name"]),
                            a["name"],
                            a.get("mbid") or "",
                            float(a.get("match") or 0),
                        ),
                    )
                _mark_fetched(conn, key, "artist.getsimilar", now)
                tags = _lastfm_call(conn, client, cfg, run, "artist.gettoptags", name, {})
                conn.execute("DELETE FROM stg_lastfm_artist_tags WHERE artist_key = ?", (key,))
                for t in (((tags or {}).get("toptags") or {}).get("tag") or [])[:10]:
                    conn.execute(
                        "INSERT INTO stg_lastfm_artist_tags (artist_key, tag, tag_count) "
                        "VALUES (?, ?, ?) ON CONFLICT (artist_key, tag) DO NOTHING",
                        (key, t["name"].lower(), int(t.get("count") or 0)),
                    )
                _mark_fetched(conn, key, "artist.gettoptags", now)
                run.rows_fetched += 1
                run.save()
            fetched += 1
            if i % 10 == 0 or i == len(todo):
                out(f"Recommendations: similar artists, {i} of {len(todo)}")
        run.finish("success")
    except ApiError as exc:
        run.finish("failed", str(exc))
        out(f"Recommendations: Last.fm details stopped, will continue next time: {exc}")
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
    return fetched


def fetch_artist_covers(
    conn: sqlite3.Connection,
    client: HttpClient,
    cfg: LastfmSettings,
    names: Iterable[str],
    now: datetime | None = None,
) -> int:
    """Top album cover for artists I've never played (recommended ones). Cached 30 days."""
    now = now or datetime.now(timezone.utc)
    cutoff = _cutoff(now)
    fresh = {
        r[0]
        for r in conn.execute(
            "SELECT artist_key FROM stg_lastfm_artist_top_album WHERE fetched_at >= ?", (cutoff,)
        )
    }
    todo = [n for n in names if artist_key(n) not in fresh]
    if not todo:
        return 0
    run = SyncRun(conn, lastfm.SOURCE, "enrich")
    count = 0
    try:
        for name in todo:
            key = artist_key(name)
            with transaction(conn):
                data = _lastfm_call(
                    conn, client, cfg, run, "artist.gettopalbums", name, {"limit": 1}
                )
                albums = ((data or {}).get("topalbums") or {}).get("album") or []
                album = albums[0] if albums else {}
                conn.execute(
                    "INSERT INTO stg_lastfm_artist_top_album (artist_key, album_name, image_url, "
                    "fetched_at) VALUES (?, ?, ?, ?) ON CONFLICT (artist_key) DO UPDATE SET "
                    "album_name = excluded.album_name, image_url = excluded.image_url, "
                    "fetched_at = excluded.fetched_at",
                    (
                        key,
                        album.get("name") or "",
                        lastfm.image_url(album.get("image")),
                        _stamp(now),
                    ),
                )
            count += 1
        run.finish("success")
    except ApiError as exc:
        run.finish("failed", str(exc))
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
    return count


def fetch_deezer_pictures(
    conn: sqlite3.Connection,
    client: HttpClient,
    names: Iterable[str],
    now: datetime | None = None,
) -> int:
    """Deezer photos for artists with no Last.fm picture. Cached 30 days, misses too.

    Returns how many artists were looked up. An error (a quota, an outage) stops the
    run quietly: whatever was stored stays, and the rest is tried next time.
    """
    now = now or datetime.now(timezone.utc)
    fresh = {
        r[0]
        for r in conn.execute(
            "SELECT artist_key FROM stg_deezer_artists WHERE fetched_at >= ?", (_cutoff(now),)
        )
    }
    todo = [n for n in dict.fromkeys(names) if artist_key(n) not in fresh]
    if not todo:
        return 0
    run = SyncRun(conn, deezer.SOURCE, "enrich")
    count = 0
    try:
        for name in todo:
            params = {"q": name, "limit": 5}
            with transaction(conn):
                response = client.get_json(
                    deezer.SEARCH_URL, params=params, checker=deezer.check_error
                )
                store_page(
                    conn,
                    sync_run_id=run.run_id,
                    source=deezer.SOURCE,
                    endpoint="search/artist",
                    params=params,
                    page_number=None,
                    http_status=response.status,
                    payload=response.data,
                )
                run.pages_fetched += 1
                match = deezer.best_match(name, response.data)
                conn.execute(
                    "INSERT INTO stg_deezer_artists (artist_key, deezer_id, name, picture_url, "
                    "fetched_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (artist_key) DO UPDATE SET "
                    "deezer_id = excluded.deezer_id, name = excluded.name, "
                    "picture_url = excluded.picture_url, fetched_at = excluded.fetched_at",
                    (
                        artist_key(name),
                        match.get("id") if match else None,
                        (match.get("name") or "") if match else "",
                        deezer.picture_url(match),
                        _stamp(now),
                    ),
                )
                run.rows_fetched += 1
                run.save()
            count += 1
        run.finish("success")
    except ApiError as exc:
        run.finish("failed", str(exc))
    except Exception as exc:
        run.finish("failed", client.redact(f"{type(exc).__name__}: {exc}"))
        raise
    return count
