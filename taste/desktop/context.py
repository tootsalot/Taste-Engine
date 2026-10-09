"""State shared by every page: the key store, test hooks, and database access."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from taste import db, profiles
from taste.secrets_store import SecretStore


@dataclass
class AppContext:
    store: SecretStore = field(default_factory=SecretStore)
    client_factory: Any = None  # tests pass fake HTTP clients
    sync_kwargs: dict[str, Any] = field(default_factory=dict)
    _prepared: set[str] = field(default_factory=set)

    def connect(self, profile_id: str) -> sqlite3.Connection:
        """Open a profile's database on the calling thread. Close it when done."""
        path = profiles.db_path(profile_id)
        conn = db.connect(path, setup=str(path) not in self._prepared)
        self._prepared.add(str(path))
        return conn

    def forget(self, profile_id: str) -> None:
        self._prepared.discard(str(profiles.db_path(profile_id)))
