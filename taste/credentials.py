"""Work out the key and username each sync should use for a profile.

Keys come from the profile's secret store (falling back to environment variables),
and usernames from the profile's settings (falling back to MAL_USERNAME /
LASTFM_USERNAME). A missing value raises ConfigError naming what to fill in.
"""

from __future__ import annotations

import os
import sqlite3

from taste import settings
from taste.config import ConfigError, LastfmSettings, MalSettings
from taste.secrets_store import KEY_LABELS, SecretStore
from taste.sources.hardcover import Tokens
from taste.sources.tmdb import TmdbSettings


def _username(conn: sqlite3.Connection, setting_key: str, env_name: str) -> str:
    return settings.get(conn, setting_key) or os.environ.get(env_name, "").strip()


def _missing(source: str, missing: list[str]) -> ConfigError:
    return ConfigError(
        f"{source} needs: {', '.join(missing)}. Add them on the profile's Settings page "
        "in the app, or set the matching environment variables."
    )


def mal(conn: sqlite3.Connection, profile_id: str, store: SecretStore) -> MalSettings:
    client_id = store.get(profile_id, "MAL_CLIENT_ID")
    username = _username(conn, "mal_username", "MAL_USERNAME")
    missing = [
        label
        for label, value in ((KEY_LABELS["MAL_CLIENT_ID"], client_id), ("username", username))
        if not value
    ]
    if missing:
        raise _missing("MyAnimeList", missing)
    return MalSettings(client_id, username)


def lastfm(conn: sqlite3.Connection, profile_id: str, store: SecretStore) -> LastfmSettings:
    api_key = store.get(profile_id, "LASTFM_API_KEY")
    username = _username(conn, "lastfm_username", "LASTFM_USERNAME")
    missing = [
        label
        for label, value in ((KEY_LABELS["LASTFM_API_KEY"], api_key), ("username", username))
        if not value
    ]
    if missing:
        raise _missing("Last.fm", missing)
    return LastfmSettings(api_key, username)


def tmdb(conn: sqlite3.Connection, profile_id: str, store: SecretStore) -> TmdbSettings:
    """The read token, plus the account's token and id when it's connected."""
    read_token = store.get(profile_id, "TMDB_READ_TOKEN")
    if not read_token:
        raise _missing("TMDB", [KEY_LABELS["TMDB_READ_TOKEN"]])
    return TmdbSettings(
        read_token,
        store.get(profile_id, "TMDB_USER_TOKEN"),
        store.get(profile_id, "TMDB_ACCOUNT_ID"),
    )


def tmdb_connected(profile_id: str, store: SecretStore) -> bool:
    """Whether a TMDB account is connected (its token is stored, not just the read token)."""
    return bool(store.has_stored(profile_id, "TMDB_USER_TOKEN"))


HARDCOVER_NAMES = ("HARDCOVER_ACCESS_TOKEN", "HARDCOVER_REFRESH_TOKEN", "HARDCOVER_TOKEN_EXPIRES")


def hardcover_connected(profile_id: str, store: SecretStore) -> bool:
    """Whether a Hardcover account is signed in for this profile."""
    return bool(store.has_stored(profile_id, "HARDCOVER_REFRESH_TOKEN"))


def hardcover(profile_id: str, store: SecretStore) -> Tokens:
    access, refresh, expires = (store.get(profile_id, n) for n in HARDCOVER_NAMES)
    if not access or not refresh:
        raise ConfigError("Hardcover: connect your Hardcover account on the Settings page first.")
    return Tokens(access, refresh, expires or "")


def save_hardcover(profile_id: str, store: SecretStore, tokens: Tokens) -> None:
    """Store a fresh pair. The refresh token rotates, so this runs before anything else."""
    store.set(profile_id, "HARDCOVER_REFRESH_TOKEN", tokens.refresh)
    store.set(profile_id, "HARDCOVER_ACCESS_TOKEN", tokens.access)
    store.set(profile_id, "HARDCOVER_TOKEN_EXPIRES", tokens.expires_at)


def forget_hardcover(profile_id: str, store: SecretStore) -> None:
    for name in HARDCOVER_NAMES:
        store.delete(profile_id, name)
