"""Background threads, so syncing or testing a connection never freezes the window.

Each worker opens its own database connection: SQLite connections must stay in
the thread that made them.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QThread, Signal

from taste import db, profiles, recap, runner
from taste.desktop import recap_cards
from taste.desktop.art import decode
from taste.secrets_store import SecretStore


class SyncCancelled(Exception):
    """Raised inside the sync when the user quits mid-sync."""


class SyncWorker(QThread):
    """Sync one source or all. Syncing everything also refreshes recommendations after."""

    message = Signal(str)
    recs_updated = Signal()  # a recommendations list was saved
    done = Signal(dict)  # {source: succeeded}

    def __init__(
        self,
        profile_id: str,
        source: str,
        store: SecretStore,
        client_factory: Any = None,
        sync_kwargs: dict[str, Any] | None = None,
        recs_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        # Resolved now, on the UI thread: the data folder can't change under a running thread.
        self.path = profiles.db_path(profile_id)
        self.source = source
        self.store = store
        self.client_factory = client_factory
        self.sync_kwargs = sync_kwargs or {}
        self.recs_kwargs = recs_kwargs or {}
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
            conn = db.connect(self.path)
            kwargs: dict[str, Any] = dict(self.sync_kwargs)
            if self.client_factory is not None:
                kwargs["client_factory"] = self.client_factory
            results = runner.sync_sources(
                conn, self.profile_id, self.source, store=self.store, out=self._out, **kwargs
            )
            if self.source == "all" and any(results.values()):
                self._refresh_recommendations(conn)
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

    def _refresh_recommendations(self, conn) -> None:
        """Top up enrichment and recompute. A failure here doesn't undo the sync."""
        kwargs: dict[str, Any] = dict(self.recs_kwargs)
        if self.client_factory is not None:
            kwargs["client_factory"] = self.client_factory
        try:
            runner.refresh_recommendations(
                conn,
                self.profile_id,
                store=self.store,
                out=self._out,
                on_update=self.recs_updated.emit,
                **kwargs,
            )
        except SyncCancelled:
            raise
        except Exception as exc:
            self.message.emit(
                f"Recommendations stopped on an unexpected error ({type(exc).__name__})."
            )
            traceback.print_exc()


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
        self.path = profiles.db_path(profile_id)
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
            conn = db.connect(self.path)
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


class CallWorker(QThread):
    """Run one network call off the UI thread (TMDB's and Hardcover's sign-in). It touches no
    database: what it needs is read before it starts, and the result is saved after."""

    done = Signal(object, str)  # the call's result, or None and a message

    def __init__(self, call: Any) -> None:
        super().__init__()
        self.call = call

    def run(self) -> None:
        try:
            self.done.emit(self.call(), "")
        except Exception as exc:  # ApiError messages are already redacted
            message = str(exc) if hasattr(exc, "retryable") else type(exc).__name__
            self.done.emit(None, message)


class RecapWorker(QThread):
    """Build and draw recaps off the UI thread.

    `period` None: the automatic check (recap.due), which saves any recap that's new
    or whose numbers changed, and reports the new ones in `made`. With a period: draw
    it for the page (`ready`), and also save it to `save_to` when given (with
    `record` set when that's the recap's own folder).
    """

    ready = Signal(str, str, object, list)  # kind, key, RecapData, [(card name, QImage)]
    made = Signal(list)  # [(kind, key)] recaps made for the first time
    failed = Signal(str)

    def __init__(
        self,
        profile_id: str,
        cache: Any,
        now: Any,
        period: Any = None,
        save_to: Any = None,
        record: bool = False,
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.path = profiles.db_path(profile_id)
        self.cache = cache
        self.now = now
        self.period = period
        self.save_to = save_to
        self.record = record

    def pictures(self, url: str | None):
        path = self.cache.get(url) if url else None
        return decode(str(path), QSize(700, 700)) if path is not None else None

    def run(self) -> None:
        conn = None
        try:
            if not self.path.is_file():
                return  # the profile was deleted meanwhile; never create an empty one
            conn = db.connect(self.path, setup=False)
            if self.period is None:
                made = []
                for period in recap.due(conn, self.now):
                    data = recap.build(conn, period, self.now)
                    if not recap.has_data(data):
                        continue
                    if data.fingerprint == recap.stored_fingerprint(conn, period):
                        recap.record(conn, data, self.now)  # may turn it final
                        continue
                    cards = recap_cards.render(data, self.pictures)
                    self._write(data, cards, recap.folder(self.profile_id, period))
                    if recap.record(conn, data, self.now):
                        made.append((period.kind, period.key))
                self.made.emit(made)
                return
            data = recap.build(conn, self.period, self.now)
            cards = recap_cards.render(data, self.pictures)
            if self.save_to is not None:
                self._write(data, cards, self.save_to)
                if self.record:
                    recap.record(conn, data, self.now, announced=True)  # made by hand
            self.ready.emit(self.period.kind, self.period.key, data, cards)
        except Exception as exc:
            self.failed.emit(f"The recap stopped on an unexpected error ({type(exc).__name__}).")
            traceback.print_exc()
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _write(data, cards, folder) -> Path:
        """The recap's own folder: its cards ("01-cover.png", ...) and recap.json (the
        numbers behind them). Older files are cleared first, so a card that's gone since
        (or the one long image of an earlier version) doesn't linger."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for old in [*folder.glob("[0-9][0-9]-*.png"), *folder.glob("Taste Engine recap *.png")]:
            old.unlink()
        for (_, image), name in zip(cards, data.file_names(), strict=True):
            image.save(str(folder / name))
        recap.write_json(data, folder)
        return folder


class ConnectionTestWorker(QThread):
    done = Signal(bool, str)

    def __init__(
        self, profile_id: str, source: str, store: SecretStore, client_factory: Any = None
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.path = profiles.db_path(profile_id)
        self.source = source
        self.store = store
        self.client_factory = client_factory

    def run(self) -> None:
        conn = None
        try:
            conn = db.connect(self.path, setup=False)
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
