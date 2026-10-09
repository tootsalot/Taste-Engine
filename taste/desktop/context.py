"""State shared by every page: the key store, images, test hooks, and database access."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from taste import db, images, profiles
from taste.secrets_store import SecretStore


@dataclass
class AppContext:
    store: SecretStore = field(default_factory=SecretStore)
    client_factory: Any = None  # tests pass fake HTTP clients
    sync_kwargs: dict[str, Any] = field(default_factory=dict)
    recs_kwargs: dict[str, Any] = field(default_factory=dict)  # tests pin "now"
    image_session: Any = None  # tests pass a fake download session
    clock: Any = None  # tests pin "now" for the Dashboard
    _prepared: set[str] = field(default_factory=set)
    _art: Any = None

    def now(self) -> datetime:
        return self.clock() if self.clock is not None else datetime.now(timezone.utc)

    @property
    def art(self):
        """The shared poster and cover loader, created on first use (it needs a QApplication)."""
        if self._art is None:
            from taste.desktop.art import ArtLoader  # local import: keeps this module Qt-free

            self._art = ArtLoader(images.ImageCache(session=self.image_session))
        return self._art

    def connect(self, profile_id: str) -> sqlite3.Connection:
        """Open a profile's database on the calling thread. Close it when done."""
        path = profiles.db_path(profile_id)
        conn = db.connect(path, setup=str(path) not in self._prepared)
        self._prepared.add(str(path))
        return conn

    def shutdown(self) -> None:
        """Stop image downloads when the window closes."""
        if self._art is not None:
            self._art.shutdown()

    def forget(self, profile_id: str) -> None:
        self._prepared.discard(str(profiles.db_path(profile_id)))
