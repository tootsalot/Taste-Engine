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
