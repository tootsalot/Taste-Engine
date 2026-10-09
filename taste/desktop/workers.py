"""Background threads, so syncing or testing a connection never freezes the window.

Each worker opens its own database connection: SQLite connections must stay in
the thread that made them.
"""

from __future__ import annotations

import traceback
from typing import Any

from PySide6.QtCore import QThread, Signal

from taste import db, profiles, runner
from taste.secrets_store import SecretStore


class SyncCancelled(Exception):
    """Raised inside the sync when the user quits mid-sync."""


class SyncWorker(QThread):
    message = Signal(str)
    done = Signal(dict)  # {source: succeeded}

    def __init__(
        self,
        profile_id: str,
        source: str,
        store: SecretStore,
        client_factory: Any = None,
        sync_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.source = source
        self.store = store
        self.client_factory = client_factory
        self.sync_kwargs = sync_kwargs or {}
        self._cancelled = False

    def cancel(self) -> None:
        """Stop at the next progress message, which always follows a committed page."""
        self._cancelled = True

    def _out(self, message: str) -> None:
        if self._cancelled:
            raise SyncCancelled("Sync cancelled because the app was closed.")
        self.message.emit(message)

    def run(self) -> None:
        results: dict[str, bool] = {}
        conn = None
        try:
            conn = db.connect(profiles.db_path(self.profile_id))
            kwargs: dict[str, Any] = dict(self.sync_kwargs)
            if self.client_factory is not None:
                kwargs["client_factory"] = self.client_factory
            results = runner.sync_sources(
                conn, self.profile_id, self.source, store=self.store, out=self._out, **kwargs
            )
        except SyncCancelled:
            # The sync code recorded the run as failed with this reason; the next one resumes.
            results = {self.source: False}
        except Exception as exc:
            # API failures are already handled and logged (redacted) by the sync code.
            # This is for bugs: show the type, print the traceback for a bug report.
            self.message.emit(f"The sync stopped on an unexpected error ({type(exc).__name__}).")
            traceback.print_exc()
            results = results or {self.source: False}
        finally:
            if conn is not None:
                conn.close()
        self.done.emit(results)


class RecsWorker(QThread):
    """Fetch what recommendations need, then recompute them (runner.refresh_recommendations)."""

    message = Signal(str)
    updated = Signal()  # a list was saved; the page can redraw while the rest runs
    done = Signal(dict)  # counts per list, empty if it failed

    def __init__(
        self,
        profile_id: str,
        store: SecretStore,
        client_factory: Any = None,
        recs_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.store = store
        self.client_factory = client_factory
        self.recs_kwargs = recs_kwargs or {}
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _out(self, message: str) -> None:
        if self._cancelled:
            raise SyncCancelled("Stopped because the app was closed.")
        self.message.emit(message)

    def run(self) -> None:
        counts: dict[str, int] = {}
        conn = None
        try:
            conn = db.connect(profiles.db_path(self.profile_id))
            kwargs: dict[str, Any] = dict(self.recs_kwargs)
            if self.client_factory is not None:
                kwargs["client_factory"] = self.client_factory
            counts = runner.refresh_recommendations(
                conn,
                self.profile_id,
                store=self.store,
                out=self._out,
                on_update=self.updated.emit,
                **kwargs,
            )
        except SyncCancelled:
            pass  # everything fetched so far is committed; the next refresh continues
        except Exception as exc:
            self.message.emit(
                f"Recommendations stopped on an unexpected error ({type(exc).__name__})."
            )
            traceback.print_exc()
        finally:
            if conn is not None:
                conn.close()
        self.done.emit(counts)


class ConnectionTestWorker(QThread):
    done = Signal(bool, str)

    def __init__(
        self, profile_id: str, source: str, store: SecretStore, client_factory: Any = None
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.source = source
        self.store = store
        self.client_factory = client_factory

    def run(self) -> None:
        conn = None
        try:
            conn = db.connect(profiles.db_path(self.profile_id), setup=False)
            kwargs = {"client_factory": self.client_factory} if self.client_factory else {}
            ok, message = runner.check_connection(
                conn, self.profile_id, self.source, store=self.store, **kwargs
            )
        except Exception as exc:
            ok, message = False, f"Test failed ({type(exc).__name__})."
            traceback.print_exc()
        finally:
            if conn is not None:
                conn.close()
        self.done.emit(ok, message)
