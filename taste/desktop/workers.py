"""Background threads, so syncing or testing a connection never freezes the window.

Each worker opens its own database connection: SQLite connections must stay in
the thread that made them.
"""

from __future__ import annotations

import re
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QThread, Signal

from taste import backup, db, profiles, recap, recommend, runner
from taste.desktop import recap_cards
from taste.desktop.art import decode
from taste.recommend import comparison
from taste.secrets_store import SecretStore


class SyncCancelled(Exception):
    """Raised inside the sync when the user quits mid-sync."""


class ModelCheckWorker(QThread):
    """Compare rating models using a connection owned by this short background job."""

    message = Signal(str)

    def __init__(self, profile_id: str) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.path = profiles.db_path(profile_id).resolve()
        self.file_identity = self.identity()
        self.version = recommend.MODEL_SET_VERSION
        self.generation = 0  # assigned by the window that starts this job
        self.result: dict | None = None
        self._cancelled = False

    def identity(self) -> tuple[int, int] | None:
        try:
            info = self.path.stat()
            return info.st_dev, info.st_ino
        except FileNotFoundError:
            return None

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        self.result = None
        if self._cancelled or not self.path.is_file() or self.identity() != self.file_identity:
            return
        conn = None
        try:
            conn = db.connect(self.path, setup=False)
            result = comparison.check_models(
                conn, cancelled=self.is_cancelled, progress=self.message.emit
            )
            if not self._cancelled:
                self.result = result
        except Exception as exc:
            self.message.emit(
                f"The rating check stopped on an unexpected error ({type(exc).__name__})."
            )
        finally:
            if conn is not None:
                conn.close()


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
        quick: bool = False,
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        # Resolved now, on the UI thread: the data folder can't change under a running thread.
        self.path = profiles.db_path(profile_id)
        self.source = source
        self.quick = quick
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
        if not self.path.is_file():  # deleted meanwhile: connecting would make an empty one
            self.done.emit({self.source: False})
            return
        try:
            conn = db.connect(self.path)
            kwargs: dict[str, Any] = dict(self.sync_kwargs)
            if self.client_factory is not None:
                kwargs["client_factory"] = self.client_factory
            results = runner.sync_sources(
                conn,
                self.profile_id,
                self.source,
                store=self.store,
                out=self._out,
                quick=self.quick,
                **kwargs,
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
        if self.quick:
            kwargs["fetch"] = False  # recompute from what's saved, no network
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
        if not self.path.is_file():  # deleted meanwhile: connecting would make an empty one
            self.done.emit(counts)
            return
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
    """Run one call off the UI thread: TMDB's and Hardcover's sign-in, which touch no
    database (what they need is read before, the result saved after), and CSV exports,
    which open their own connection."""

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
    `record` set when that's the recap's own folder). `retry` asks again for pictures
    that failed earlier in the session.
    """

    ready = Signal(str, str, object, list)  # kind, key, RecapData, [(card name, QImage)]
    made = Signal(list)  # [(kind, key)] recaps made for the first time
    failed = Signal(str)
    progress = Signal(int, int, str)  # a batch: periods done, of how many, the latest's name

    def __init__(
        self,
        profile_id: str,
        cache: Any,
        now: Any,
        period: Any = None,
        save_to: Any = None,
        record: bool = False,
        retry: bool = False,
        replace_in: Any = None,
        batch: list | None = None,
    ) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.path = profiles.db_path(profile_id)
        self.cache = cache
        self.now = now
        self.period = period
        self.save_to = save_to
        self.record = record
        self.retry = retry
        self.replace_in = replace_in  # Rebuild: the save folder, whose saved cards are redone
        self.replaced = 0  # how many saved cards that replaced, read once `ready` arrives
        self.missing = 0  # pictures drawn as placeholders in the latest drawing
        self.old_names: list[str] | None = None  # card names in save_to before this drawing
        self.batch = batch  # past recaps to make in one go, oldest first (2.3)
        self.made_count = 0  # of the batch, the ones with something to recap
        self._cancelled = False

    def cancel(self) -> None:
        """Stop a batch before its next period (the app is closing); what's made stays."""
        self._cancelled = True

    def pictures(self, url: str | None):
        path = self.cache.get(url, retry=self.retry) if url else None
        image = decode(str(path), QSize(700, 700)) if path is not None else None
        if url and (image is None or image.isNull()) and not self.cache.failed_for_good(url):
            self.missing += 1  # a placeholder that may load later: worth drawing again
        return image

    def run(self) -> None:
        conn = None
        try:
            if not self.path.is_file():
                return  # the profile was deleted meanwhile; never create an empty one
            conn = db.connect(self.path, setup=False)
            if self.batch is not None:
                self._make_batch(conn)
                return
            if self.period is None:
                made = []
                for period in recap.due(conn, self.now):
                    data = recap.build(conn, period, self.now)
                    if not recap.has_data(data):
                        continue
                    same = data.fingerprint == recap.stored_fingerprint(conn, period)
                    if same and not recap.pictures_missing(conn, period):
                        recap.record(conn, data, self.now)  # may turn it final
                        continue
                    # New numbers, or the same ones last drawn with placeholders.
                    self.missing = 0
                    cards = recap_cards.render(data, self.pictures)
                    self._write(data, cards, recap.folder(self.profile_id, period))
                    if recap.record(conn, data, self.now, missing=self.missing):
                        made.append((period.kind, period.key))
                self.made.emit(made)
                return
            data = recap.build(conn, self.period, self.now)
            if self.save_to is not None:
                # Which card was which when it was last made: read before it's rewritten.
                self.old_names = recap.saved_card_names(Path(self.save_to))
            self.missing = 0
            cards = recap_cards.render(data, self.pictures)
            if self.save_to is not None:
                self._write(data, cards, self.save_to)
                if self.record:  # made by hand
                    recap.record(conn, data, self.now, announced=True, missing=self.missing)
            if self.replace_in is not None and recap.has_data(data):
                self.replaced = self._replace_saved(
                    data, cards, Path(self.replace_in), self.old_names
                )
                if self.replaced and self.save_to is not None:
                    recap.remember_saved_names(Path(self.save_to), [n for n, _ in cards])
            self.ready.emit(self.period.kind, self.period.key, data, cards)
        except Exception as exc:
            self.failed.emit(f"The recap stopped on an unexpected error ({type(exc).__name__}).")
            traceback.print_exc()
        finally:
            if conn is not None:
                conn.close()

    def _make_batch(self, conn) -> None:
        """Past recaps, oldest first, each final and already announced (no banner for each
        one). A period made meanwhile (by the automatic check) is left as it is; one with
        nothing to recap is recorded too, so it isn't offered again. Stopped by cancel(),
        it goes on next launch: the state stays 'making' until the last one is done."""
        total = len(self.batch)
        for done, period in enumerate(self.batch, 1):
            if self._cancelled:
                return
            if recap.stored_fingerprint(conn, period) is None:
                data = recap.build(conn, period, self.now)
                missing = 0
                if recap.has_data(data):
                    self.missing = 0
                    cards = recap_cards.render(data, self.pictures)
                    self._write(data, cards, recap.folder(self.profile_id, period))
                    missing = self.missing
                    self.made_count += 1
                recap.record(conn, data, self.now, announced=True, missing=missing)
            self.progress.emit(done, total, period.label)
        recap.set_past_state(conn, "done")

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

    @staticmethod
    def _replace_saved(data, cards, folder: Path, old_names: list[str] | None) -> int:
        """The cards of this recap already saved in `folder` (by Save this card or Save
        all), replaced with the ones just drawn; cards never saved stay unsaved (the
        user's call, 2026-10-19). A saved "(2 of 5)" is found by which card it was:
        `old_names` are the card names when it was made (from its recap.json), so after a
        new card arrives it's replaced and renamed "(3 of 6)". Without them, only a deck
        of the same size can be matched, by position; otherwise saved cards are left as
        they are. Returns how many files were written."""
        label = re.escape(data.period.label)
        pattern = re.compile(rf"Taste Engine recap {label} \((\d+) of (\d+)\)\.png")
        new_names = [name for name, _ in cards]
        targets: dict[Path, int] = {}  # saved file -> the new card that replaces it
        if folder.is_dir():
            for path in folder.glob("Taste Engine recap *.png"):
                match = pattern.fullmatch(path.name)
                if not match:
                    continue
                number, total = int(match[1]), int(match[2])
                names = old_names if old_names and len(old_names) == total else None
                if names is None and total == len(cards):
                    names = new_names  # same size, nothing better to go on: by position
                if names is None or not 1 <= number <= total or names[number - 1] not in new_names:
                    continue  # can't tell which card it was, or that card is gone
                targets[path] = new_names.index(names[number - 1])
        # Old names first, then the new files, so a rename never removes a new card.
        new_paths = {folder / data.save_name(i) for i in targets.values()}
        for path in targets:
            if path not in new_paths:
                path.unlink(missing_ok=True)
        written = 0
        for i in sorted(set(targets.values())):
            written += bool(cards[i][1].save(str(folder / data.save_name(i))))
        return written


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
        if not self.path.is_file():  # deleted meanwhile: connecting would make an empty one
            self.done.emit(False, "The profile was deleted.")
            return
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


class BackupWorker(QThread):
    """Back up or restore a profile without blocking the window."""

    done = Signal(str, str)

    def __init__(self, operation: str, path: Path, profile_id: str | None) -> None:
        super().__init__()
        self.operation = operation
        self.target = Path(path)
        self.profile_id = profile_id
        self.root = profiles.profiles_dir()
        self.recaps = backup.data_dir() / "recaps"
        self.database = profiles.db_path(profile_id, self.root) if operation == "backup" else None
        self.picture = (
            profiles.picture_path(profile_id, self.root) if operation == "backup" else None
        )

    def run(self) -> None:
        try:
            if self.operation == "backup":
                if not self.database.is_file():
                    raise profiles.ProfileError("The profile was deleted.")
                backup.create(
                    self.profile_id,
                    self.target,
                    database=self.database,
                    recap_root=self.recaps / self.profile_id,
                    picture=self.picture,
                )
                result = self.profile_id
            else:
                result = backup.restore(
                    self.target, self.profile_id, root=self.root, recap_root=self.recaps
                ).profile_id
            self.done.emit(result, "")
        except (backup.BackupError, profiles.ProfileError) as exc:
            self.done.emit("", str(exc))
        except Exception as exc:
            self.done.emit("", f"The profile {self.operation} failed ({type(exc).__name__}).")
