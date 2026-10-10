"""Profiles: one SQLite database per profile, all in data/profiles/.

A profile holds one person's (or one account's) usernames, settings, and data.
The list of profiles is just the .db files in the folder, so there's no separate
registry to get out of sync. API keys live outside the database (see secrets_store).
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from taste import db, settings
from taste.config import data_dir, standalone_dir

DEFAULT_PROFILE = "default"


def profiles_dir() -> Path:
    return data_dir() / "profiles"


def legacy_db() -> Path:
    """Where Phase 1 kept its single database."""
    return data_dir() / "taste.db"


# Profile IDs become file names and URL segments, so they're strict.
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# The Phase 1 database was built for the original owner, whose Last.fm only
# receives desktop scrobbles. The migrated profile keeps that label and note.
LEGACY_SCOPE = "desktop_only"
LEGACY_NOTE = (
    "Desktop listening only. Last.fm only receives scrobbles from Tidal on my desktop, "
    "not my phone, so this is a sample of my listening, not all of it."
)


class ProfileError(ValueError):
    """Bad profile ID, or a profile that does or doesn't exist when it should."""


@dataclass(frozen=True)
class Profile:
    profile_id: str
    display_name: str
    path: Path


def validate_id(profile_id: str) -> str:
    if not isinstance(profile_id, str) or not _ID_PATTERN.match(profile_id):
        raise ProfileError(
            "Profile IDs use lowercase letters, numbers, '-' and '_', start with a letter "
            "or number, and are at most 40 characters."
        )
    return profile_id


def db_path(profile_id: str, root: Path | None = None) -> Path:
    root = root or profiles_dir()
    return root / f"{validate_id(profile_id)}.db"


def exists(profile_id: str, root: Path | None = None) -> bool:
    root = root or profiles_dir()
    return db_path(profile_id, root).is_file()


def list_profiles(root: Path | None = None) -> list[Profile]:
    root = root or profiles_dir()
    if not root.is_dir():
        return []
    profiles = []
    for path in sorted(root.glob("*.db")):
        if not _ID_PATTERN.match(path.stem):
            continue  # not something this app created
        profiles.append(Profile(path.stem, display_name(path.stem, root), path))
    return profiles


def display_name(profile_id: str, root: Path | None = None) -> str:
    root = root or profiles_dir()
    try:
        with _readonly(db_path(profile_id, root)) as conn:
            row = conn.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = 'display_name'"
            ).fetchone()
    except sqlite3.Error:
        return profile_id
    return (row[0] if row else "") or profile_id


def open_profile(profile_id: str, root: Path | None = None, **kwargs) -> sqlite3.Connection:
    """Open an existing profile's database."""
    root = root or profiles_dir()
    if not exists(profile_id, root):
        raise ProfileError(f"No profile named {profile_id!r}.")
    return db.connect(db_path(profile_id, root), **kwargs)


def create(profile_id: str, name: str = "", root: Path | None = None) -> Profile:
    root = root or profiles_dir()
    path = db_path(profile_id, root)
    if path.exists():
        raise ProfileError(f"A profile named {profile_id!r} already exists.")
    root.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        if name:
            settings.set_value(conn, "display_name", name)
    finally:
        conn.close()
    return Profile(profile_id, name or profile_id, path)


def delete(profile_id: str, root: Path | None = None, store: Any = None) -> None:
    """Delete a profile's database and its stored API keys. Can't be undone.

    `store` is the SecretStore holding the keys. The app passes its own.
    """
    from taste.secrets_store import SecretStore  # local import: secrets_store imports this module

    root = root or profiles_dir()
    path = db_path(profile_id, root)
    if not path.exists():
        raise ProfileError(f"No profile named {profile_id!r}.")
    (store or SecretStore(root)).delete_all(profile_id)
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def delete_all_data(store: Any = None) -> Path | None:
    """For the uninstaller: every profile's saved API keys, then the whole data folder.

    Returns the folder it deleted. Runs only where that folder is the app's own (the
    packaged app, or TASTE_DATA_DIR); from source it would be the project, so it refuses
    and returns None.
    """
    from taste.secrets_store import SecretStore  # local import: secrets_store imports this module

    folder = standalone_dir()
    if folder is None:
        return None
    store = store or SecretStore(profiles_dir())
    for profile in list_profiles():
        store.delete_all(profile.profile_id)
    shutil.rmtree(folder, ignore_errors=True)
    return folder


def migrate_legacy(root: Path | None = None, legacy: Path | None = None) -> bool:
    """Move the Phase 1 data/taste.db into data/profiles/default.db, once.

    Returns True if a migration happened. Nothing is re-synced and nothing is lost.
    """
    root = root or profiles_dir()
    legacy = legacy or legacy_db()
    target = root / f"{DEFAULT_PROFILE}.db"
    if not legacy.is_file() or target.exists():
        return False
    root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy), str(target))
    conn = db.connect(target)
    try:
        settings.set_value(conn, "lastfm_capture_scope", LEGACY_SCOPE)
        settings.set_value(conn, "lastfm_scope_note", LEGACY_NOTE)
    finally:
        conn.close()
    return True


class _readonly:
    """Open a database read-only for quick lookups, without running schema setup."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> sqlite3.Connection:
        # as_uri() gives file:///C:/... on Windows and file:///home/... elsewhere.
        self.conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        return self.conn

    def __exit__(self, *exc) -> None:
        self.conn.close()
