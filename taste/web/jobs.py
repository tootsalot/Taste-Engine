"""Background syncs started from the web app, with progress the browser can poll."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from taste import db, profiles, runner
from taste.db import utc_now
from taste.secrets_store import SecretStore


@dataclass
class Job:
    profile_id: str
    source: str
    started_at: str
    messages: list[str] = field(default_factory=list)
    results: dict[str, bool] = field(default_factory=dict)
    running: bool = True
    finished_at: str | None = None

    def add(self, message: str) -> None:
        self.messages.append(message)

    def snapshot(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "messages": list(self.messages),
            "results": dict(self.results),
            "ok": (not self.running) and bool(self.results) and all(self.results.values()),
        }


class JobManager:
    """One sync at a time per profile, each in its own thread and database connection."""

    def __init__(
        self,
        store: SecretStore,
        client_factory: runner.ClientFactory | None = None,
        sync_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.client_factory = client_factory
        self.sync_kwargs = sync_kwargs or {}
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, profile_id: str) -> Job | None:
        return self._jobs.get(profile_id)

    def is_running(self, profile_id: str) -> bool:
        job = self._jobs.get(profile_id)
        return bool(job and job.running)

    def start(self, profile_id: str, source: str, *, wait: bool = False) -> Job:
        """Start a sync. Raises RuntimeError if one is already running for this profile."""
        with self._lock:
            if self.is_running(profile_id):
                raise RuntimeError("A sync is already running for this profile.")
            job = Job(profile_id, source, utc_now())
            self._jobs[profile_id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        if wait:
            thread.join()
        return job

    def _run(self, job: Job) -> None:
        conn = None
        try:
            conn = db.connect(profiles.db_path(job.profile_id))
            kwargs: dict[str, Any] = dict(self.sync_kwargs)
            if self.client_factory is not None:
                kwargs["client_factory"] = self.client_factory
            job.results = runner.sync_sources(
                conn, job.profile_id, job.source, store=self.store, out=job.add, **kwargs
            )
        except Exception as exc:
            # Sync code already records API failures (redacted) in sync_runs. This is
            # for bugs: show the type here, print the traceback to the console window.
            job.add(f"The sync stopped on an unexpected error ({type(exc).__name__}).")
            traceback.print_exc()
            job.results = job.results or {job.source: False}
        finally:
            if conn is not None:
                conn.close()
            job.finished_at = utc_now()
            job.running = False


JobStarter = Callable[[str, str], Job]
