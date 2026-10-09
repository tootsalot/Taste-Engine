"""Run syncs for a profile. Shared by the CLI and the desktop app."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from taste import credentials, enrich, raw, recommend, settings
from taste.config import ConfigError
from taste.db import transaction
from taste.http_client import ApiError, HttpClient
from taste.secrets_store import SecretStore
from taste.sources import lastfm, mal

SOURCES = ("mal", "lastfm")
SOURCE_LABELS = {"mal": "MyAnimeList", "lastfm": "Last.fm"}

ClientFactory = Callable[[list[str]], HttpClient]


def _default_client(secrets: list[str]) -> HttpClient:
    return HttpClient(secrets=secrets)


def sync_sources(
    conn: sqlite3.Connection,
    profile_id: str,
    source: str,
    *,
    store: SecretStore | None = None,
    out: Callable[[str], None] = print,
    client_factory: ClientFactory = _default_client,
    **sync_kwargs: Any,
) -> dict[str, bool]:
    """Sync one source or 'all'. Returns {source: succeeded}.

    In 'all', a missing key or a failure in one source doesn't stop the other.
    """
    store = store or SecretStore()
    targets = list(SOURCES) if source == "all" else [source]
    results: dict[str, bool] = {}
    for name in targets:
        try:
            if name == "mal":
                cfg = credentials.mal(conn, profile_id, store)
                client = client_factory([cfg.client_id])
                results[name] = mal.sync(conn, client, cfg, out=out).status == "success"
            else:
                cfg = credentials.lastfm(conn, profile_id, store)
                client = client_factory([cfg.api_key])
                result = lastfm.sync(conn, client, cfg, out=out, **sync_kwargs)
                results[name] = result.status == "success"
        except ConfigError as exc:
            out(f"{SOURCE_LABELS[name]}: {exc}")
            results[name] = False
    pruned = raw.prune(conn, settings.get(conn, "raw_retention_days"))
    if pruned:
        out(f"Cleaned up {pruned} old raw API pages.")
    return results


def check_connection(
    conn: sqlite3.Connection,
    profile_id: str,
    source: str,
    *,
    store: SecretStore | None = None,
    client_factory: ClientFactory = _default_client,
) -> tuple[bool, str]:
    """Make one cheap API call to check the key and username. Returns (ok, message)."""
    store = store or SecretStore()
    label = SOURCE_LABELS[source]
    try:
        if source == "mal":
            cfg = credentials.mal(conn, profile_id, store)
            client = client_factory([cfg.client_id])
            client.max_retries = 1
            client.get_json(
                f"{mal.API_BASE}/users/{quote(cfg.username)}/animelist",
                params={"limit": 1},
                headers={"X-MAL-CLIENT-ID": cfg.client_id},
                checker=mal.check_response,
            )
        else:
            cfg = credentials.lastfm(conn, profile_id, store)
            client = client_factory([cfg.api_key])
            client.max_retries = 1
            client.get_json(
                lastfm.API_URL,
                params={
                    "method": "user.getinfo",
                    "user": cfg.username,
                    "api_key": cfg.api_key,
                    "format": "json",
                },
                checker=lastfm.check_error,
            )
    except (ConfigError, ApiError) as exc:
        return False, f"{label}: {exc}"
    return True, f"{label}: connected as {cfg.username}."


def refresh_recommendations(
    conn: sqlite3.Connection,
    profile_id: str,
    *,
    store: SecretStore | None = None,
    out: Callable[[str], None] = print,
    client_factory: ClientFactory = _default_client,
    fetch: bool = True,
) -> dict[str, int]:
    """Fetch what's missing (unless fetch=False), then recompute every list.

    A source without a key or username is skipped; its list just uses what's cached.
    """
    store = store or SecretStore()
    lastfm_cfg = None
    if fetch:
        try:
            mal_cfg = credentials.mal(conn, profile_id, store)
            enrich.enrich_mal(conn, client_factory([mal_cfg.client_id]), mal_cfg, out)
        except ConfigError:
            out("Recommendations: skipping MyAnimeList (no key or username).")
        try:
            lastfm_cfg = credentials.lastfm(conn, profile_id, store)
            enrich.enrich_lastfm(
                conn,
                client_factory([lastfm_cfg.api_key]),
                lastfm_cfg,
                out,
                seed_count=settings.get(conn, "rec_seed_artists"),
            )
        except ConfigError:
            out("Recommendations: skipping Last.fm (no key or username).")
    with transaction(conn):
        counts = recommend.compute_all(conn)
    if lastfm_cfg is not None:
        # Covers for suggested artists I've never played, then recompute so they show.
        discover, _, _ = recommend.latest(conn, "music_discover")
        missing = [r.title for r in discover if not r.image_url]
        if missing and enrich.fetch_artist_covers(
            conn, client_factory([lastfm_cfg.api_key]), lastfm_cfg, missing
        ):
            with transaction(conn):
                counts = recommend.compute_all(conn)
    out(
        f"Recommendations ready: {counts['anime']} anime, {counts['music_discover']} new "
        f"artists, {counts['music_rediscover']} to rediscover."
    )
    return counts
