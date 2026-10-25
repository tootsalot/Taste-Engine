"""Run syncs for a profile. Shared by the CLI and the desktop app."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from taste import activity, credentials, enrich, raw, recommend, settings
from taste.config import ConfigError
from taste.db import transaction
from taste.http_client import ApiError, HttpClient
from taste.secrets_store import SecretStore, SecretStoreError
from taste.sources import hardcover, hardcover_auth, lastfm, mal, tmdb

SOURCES = ("mal", "lastfm", "tmdb", "hardcover")
SOURCE_LABELS = {
    "mal": "MyAnimeList",
    "lastfm": "Last.fm",
    "tmdb": "TMDB",
    "hardcover": "Hardcover",
}

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
    if source == "all" and not _connected(credentials.tmdb_connected, profile_id, store):
        targets.remove("tmdb")  # never set up: not a failure, just not part of "all" yet
    if source == "all" and not _connected(credentials.hardcover_connected, profile_id, store):
        targets.remove("hardcover")
    results: dict[str, bool] = {}
    for name in targets:
        try:
            if name == "hardcover":
                tokens = hardcover_tokens(profile_id, store, client_factory, sync_kwargs.get("now"))
                client = client_factory([tokens.access, tokens.refresh])
                results[name] = hardcover.sync(conn, client, tokens.access, out).status == "success"
            elif name == "tmdb":
                cfg = credentials.tmdb(conn, profile_id, store)
                if not cfg.user_token or not cfg.account_id:
                    raise ConfigError("connect your TMDB account on the Settings page first.")
                client = client_factory([cfg.read_token, cfg.user_token])
                results[name] = tmdb.sync(conn, client, cfg, out=out).status == "success"
            elif name == "mal":
                cfg = credentials.mal(conn, profile_id, store)
                client = client_factory([cfg.client_id])
                results[name] = mal.sync(conn, client, cfg, out=out).status == "success"
            else:
                cfg = credentials.lastfm(conn, profile_id, store)
                client = client_factory([cfg.api_key])
                result = lastfm.sync(conn, client, cfg, out=out, **sync_kwargs)
                results[name] = result.status == "success"
                if results[name]:
                    # Lengths for "about how many hours": the recaps still open first,
                    # then all time until enough plays have one.
                    clock = sync_kwargs.get("now")
                    now = datetime.fromtimestamp(clock(), timezone.utc) if clock else None
                    enrich.fetch_track_lengths(
                        conn, client, cfg, activity.length_spans(conn, now), out, now=now
                    )
        except ConfigError as exc:
            out(f"{SOURCE_LABELS[name]}: {exc}")
            results[name] = False
        except ApiError as exc:  # only Hardcover's sign-in refresh gets here
            out(f"{SOURCE_LABELS[name]}: {exc}")
            results[name] = False
        except SecretStoreError as exc:  # the credential store failed: the others still run
            out(f"{SOURCE_LABELS[name]}: {exc}")
            results[name] = False
    pruned = raw.prune(conn, settings.get(conn, "raw_retention_days"))
    if pruned:
        out(f"Cleaned up {pruned} old raw API pages.")
    return results


def _connected(check: Callable[[str, SecretStore], bool], profile_id: str, store) -> bool:
    """Whether an account is connected. A credential store that can't be read counts as
    connected, so the sync tries it and says what went wrong instead of leaving it out."""
    try:
        return check(profile_id, store)
    except SecretStoreError:
        return True


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
        if source == "hardcover":
            tokens = hardcover_tokens(profile_id, store, client_factory)
            client = client_factory([tokens.access, tokens.refresh])
            client.max_retries = 1
            name = hardcover.me(client, tokens.access)[1]
            return True, f"{label}: connected as {name}." if name else f"{label}: connected."
        if source == "tmdb":
            cfg = credentials.tmdb(conn, profile_id, store)
            client = client_factory([cfg.read_token])
            client.max_retries = 1
            tmdb.check_token(client, cfg.read_token)
            account = " Your account is connected." if cfg.user_token else ""
            return True, f"{label}: the read access token works.{account}"
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
    now: datetime | None = None,
    on_update: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Fetch what's missing (unless fetch=False), then recompute every list.

    A source without a key or username is skipped; its list just uses what's cached.
    The anime list is saved as soon as the MAL part is done, so it can be shown
    while Last.fm is still fetching. `on_update` is called after each save.
    """

    def compute(*kinds: str) -> dict[str, int]:
        with transaction(conn):
            result = recommend.compute_all(conn, now, kinds)
        if on_update is not None:
            on_update()
        return result

    store = store or SecretStore()
    lastfm_cfg = None
    pending = ["anime", "music", "film", "tv", "book"]
    if fetch:
        try:
            mal_cfg = credentials.mal(conn, profile_id, store)
        except ConfigError:
            out("Recommendations: skipping MyAnimeList (no key or username).")
        else:
            mal_client = client_factory([mal_cfg.client_id])
            enrich.enrich_mal(conn, mal_client, mal_cfg, out, now=now)
            compute(pending.pop(0))
            # Synopses for the shows on the list whose details came before synopses did.
            shown, _, _ = recommend.latest(conn, "anime")
            missing = [
                int(r.item_key) for r in shown if not r.facts.get("about") and r.item_key.isdigit()
            ]
            if enrich.fetch_synopses(conn, mal_client, mal_cfg, missing, out, now=now):
                compute("anime")
        try:
            lastfm_cfg = credentials.lastfm(conn, profile_id, store)
            enrich.enrich_lastfm(
                conn,
                client_factory([lastfm_cfg.api_key]),
                lastfm_cfg,
                out,
                now=now,
                seed_count=settings.get(conn, "rec_seed_artists"),
            )
        except ConfigError:
            out("Recommendations: skipping Last.fm (no key or username).")
    if fetch and credentials.tmdb_connected(profile_id, store):
        tmdb_cfg = credentials.tmdb(conn, profile_id, store)
        tmdb.enrich_recommendations(
            conn, client_factory([tmdb_cfg.read_token]), tmdb_cfg, out, now=now
        )
    if fetch and credentials.hardcover_connected(profile_id, store):
        try:
            clock = (lambda: now.timestamp()) if now else None
            tokens = hardcover_tokens(profile_id, store, client_factory, clock)
            hardcover.enrich_recommendations(
                conn, client_factory([tokens.access, tokens.refresh]), tokens.access, out, now=now
            )
        except (ConfigError, ApiError) as exc:
            out(f"Recommendations: skipping Hardcover ({exc}).")
    counts = compute(*pending)
    if lastfm_cfg is not None:
        # Covers for suggested artists I've never played, then recompute so they show.
        discover, _, _ = recommend.latest(conn, "music_discover")
        missing = [r.title for r in discover if not r.image_url]
        if missing and enrich.fetch_artist_covers(
            conn, client_factory([lastfm_cfg.api_key]), lastfm_cfg, missing, now
        ):
            counts = compute("music")
    if fetch:
        # A Deezer photo for each suggested artist (no key; exact name only): the picture
        # for artists Last.fm has none for, and a backup for covers that can't be
        # downloaded. Cached 30 days, so usually nothing is fetched here.
        discover, _, _ = recommend.latest(conn, "music_discover")
        rediscover, _, _ = recommend.latest(conn, "music_rediscover")
        names = [r.title for r in discover + rediscover]
        if names and enrich.fetch_deezer_pictures(conn, client_factory([]), names, now):
            counts = compute("music")
    if fetch and lastfm_cfg is not None:
        # Who each suggested artist is, and how many people listen (cached 30 days).
        discover, _, _ = recommend.latest(conn, "music_discover")
        rediscover, _, _ = recommend.latest(conn, "music_rediscover")
        names = [r.title for r in discover + rediscover]
        if names and enrich.fetch_artist_info(
            conn, client_factory([lastfm_cfg.api_key]), lastfm_cfg, names, now
        ):
            counts = compute("music")
    out(
        f"Recommendations ready: {counts['anime']} anime, {counts['music_discover']} new "
        f"artists, {counts['music_rediscover']} to rediscover, {counts['film']} films, "
        f"{counts['tv']} TV shows, {counts['book']} books."
    )
    return counts


def hardcover_tokens(
    profile_id: str,
    store: SecretStore,
    client_factory: ClientFactory = _default_client,
    clock: Callable[[], float] | None = None,
) -> hardcover_auth.Tokens:
    """The stored Hardcover tokens, refreshed first when the access token is close to
    running out. The new pair is saved before it's used: the old refresh token is dead."""
    tokens = credentials.hardcover(profile_id, store)
    now = datetime.fromtimestamp(clock(), timezone.utc) if clock else datetime.now(timezone.utc)
    if tokens.due(now):
        client = client_factory([tokens.access, tokens.refresh])
        tokens = hardcover_auth.refresh(client, tokens.refresh, now)
        credentials.save_hardcover(profile_id, store, tokens)
    return tokens
