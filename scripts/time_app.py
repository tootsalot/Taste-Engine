"""Time the desktop app with the invented full library, never a personal profile."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from taste import db, profiles, raw, runner
from taste.desktop import motion, theme
from taste.desktop.main_window import PAGES
from taste.recommend import lab
from tests.fake_images import FakeImageSession
from tests.fill_world import NOW_DT, World
from tests.test_desktop import set_up_profile


def pump() -> None:
    QApplication.processEvents()


def wait_for(signal, start) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    signal.connect(loop.quit)
    timer.start(120_000)
    start()
    loop.exec()
    signal.disconnect(loop.quit)
    if not timer.isActive():
        raise TimeoutError("The invented sync did not finish.")
    timer.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        action="store_true",
        help="also time each experimental model fit and predictions",
    )
    args = parser.parse_args()
    with (
        tempfile.TemporaryDirectory(prefix="taste-timing-") as folder,
        pytest.MonkeyPatch.context() as patch,
    ):
        patch.setenv("TASTE_DATA_DIR", folder)
        for key in (
            "MAL_CLIENT_ID",
            "MAL_USERNAME",
            "LASTFM_API_KEY",
            "LASTFM_USERNAME",
            "TMDB_READ_TOKEN",
        ):
            patch.delenv(key, raising=False)
        patch.setattr("taste.secrets_store._default_backend", lambda: None)
        patch.setattr("taste.config.load_env", lambda: None)

        def refuse(*args, **kwargs):
            raise RuntimeError("The timing script must not call live APIs.")

        patch.setattr(requests.Session, "request", refuse)
        patch.setattr(motion, "override", "off")
        app = QApplication.instance() or QApplication([])
        theme.apply(app)
        world = World()
        world.install(patch)
        win = world.window(FakeImageSession())
        set_up_profile(win, "example_user")
        world.install_extras(win, folder)
        win.resize(1920, 1040)
        win.show()
        wait_for(win.dashboard.sync_finished, lambda: win.dashboard.start_sync("all"))
        win.ctx.shutdown()
        win.close()
        win.deleteLater()
        pump()
        timings = {}
        start = time.perf_counter()
        win = world.window(FakeImageSession())
        win.resize(1920, 1040)
        win.show()
        pump()
        win.grab()  # force a completed first paint
        timings["Launch to first paint"] = time.perf_counter() - start
        try:
            for page in PAGES:
                start = time.perf_counter()
                win.show_page(page)
                pump()
                win.grab()
                timings[f"Page {page}"] = time.perf_counter() - start
            start = time.perf_counter()
            wait_for(win.dashboard.sync_finished, lambda: win.dashboard.start_sync("all"))
            timings["Sync to updated display"] = time.perf_counter() - start
            win.ctx.shutdown()
            start = time.perf_counter()
            win.settings.save_settings()
            pump()
            timings["Save settings"] = time.perf_counter() - start
            conn = db.connect(profiles.db_path("example_user"))
            try:
                start = time.perf_counter()
                runner.refresh_recommendations(
                    conn,
                    "example_user",
                    store=win.ctx.store,
                    fetch=False,
                    now=NOW_DT,
                    out=lambda message: None,
                )
                timings["Recommendations recompute"] = time.perf_counter() - start
                conn.execute("UPDATE raw_api_pages SET fetched_at = '2020-01-01 00:00:00'")
                start = time.perf_counter()
                if hasattr(raw, "compress_old_pages"):
                    raw.compress_old_pages(conn, now=NOW_DT)
                timings["Raw compression"] = time.perf_counter() - start
                if args.models:
                    for medium in lab.MEDIA:
                        rows, facts, context = lab.load_data(conn, medium)
                        for candidate in lab.DEFAULT_CANDIDATES:
                            if candidate.name not in ("details", "similar", "blend"):
                                continue
                            start = time.perf_counter()
                            predict = candidate.fit(rows, facts, context)
                            for key in sorted(context.crowd):
                                predict(key)
                            title = f"Model {medium}/{candidate.name}"
                            timings[f"{title} (fit + {len(context.crowd)} predictions)"] = (
                                time.perf_counter() - start
                            )
            finally:
                conn.close()
        finally:
            win.ctx.shutdown()
            win.close()
            win.deleteLater()
            pump()
        for name, seconds in timings.items():
            print(f"{name}: {seconds:.3f} s")


if __name__ == "__main__":
    main()
