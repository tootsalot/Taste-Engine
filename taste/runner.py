"""Run syncs for a profile. Shared by the CLI and the web app."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from taste import credentials
from taste.config import ConfigError
from taste.http_client import HttpClient
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
    return results
