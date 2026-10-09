"""Dashboard: counts, the critic number, sync buttons, and live sync progress."""

from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from taste import settings
from taste.desktop import theme
from taste.desktop.context import AppContext
from taste.desktop.widgets import Card, StatTile, heading, label
from taste.desktop.workers import SyncWorker

PAGE_PROGRESS = re.compile(r"page (\d+) of (\d+)")


class DashboardPage(QWidget):
    sync_finished = Signal(dict)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.worker: SyncWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        top = QHBoxLayout()
        self.title = heading("Dashboard", 28)
        top.addWidget(self.title)
        top.addStretch()
        self.sync_buttons: dict[str, QPushButton] = {}
        for source, text in (
            ("mal", "MyAnimeList"),
            ("lastfm", "Last.fm"),
            ("all", "Sync everything"),
        ):
            button = QPushButton(text)
            if source == "all":
                button.setProperty("role", "primary")
            button.clicked.connect(lambda _=False, s=source: self.start_sync(s))
            self.sync_buttons[source] = button
            top.addWidget(button)
        layout.addLayout(top)

        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.tile_entries = StatTile("MAL entries")
        self.tile_scored = StatTile("Scored anime")
        self.tile_plays = StatTile("Last.fm plays", theme.CORAL)
        for i, tile in enumerate((self.tile_entries, self.tile_scored, self.tile_plays)):
            tiles.addWidget(tile, 0, i)
        layout.addLayout(tiles)

        critic = Card()
        row = QHBoxLayout()
        self.critic_value = label("-")
        self.critic_value.setFont(theme.number_font(44))
        self.critic_value.setStyleSheet(f"color: {theme.LILAC};")
        self.critic_text = label("", wrap=True)
        row.addWidget(self.critic_value)
        row.addSpacing(12)
        row.addWidget(self.critic_text, 1)
        critic.body.addLayout(row)
        layout.addWidget(critic)

        sync = Card()
        sync.body.addWidget(label("Sync", role="muted"))
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        sync.body.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Sync messages show up here.")
        sync.body.addWidget(self.log, 1)
        self.last_sync = label("", role="muted", wrap=True)
        sync.body.addWidget(self.last_sync)
        layout.addWidget(sync, 1)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.log.clear()
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            name = settings.get(conn, "display_name") or self.profile_id
            count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            self.tile_entries.set_value(
                count("SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NULL")
            )
            self.tile_scored.set_value(
                count("SELECT COUNT(*) FROM core_ratings WHERE source='mal'")
            )
            self.tile_plays.set_value(
                count("SELECT COUNT(*) FROM core_behavior_events WHERE source='lastfm'")
            )
            summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
            lines = []
            for source, label_text in (("mal", "MyAnimeList"), ("lastfm", "Last.fm")):
                row = conn.execute(
                    "SELECT ended_at FROM sync_runs WHERE source = ? AND status = 'success' "
                    "ORDER BY sync_run_id DESC LIMIT 1",
                    (source,),
                ).fetchone()
                lines.append(f"{label_text}: {row[0] + ' UTC' if row else 'never synced'}")
            interrupted = count("SELECT COUNT(*) FROM sync_lastfm_windows WHERE status = 'open'")
        finally:
            conn.close()
        self.title.setText(name)
        if summary and summary["shows_scored"]:
            diff = summary["avg_diff"] + 0.0
            self.critic_value.setText(f"{diff:+.2f}")
            direction = "below" if diff < 0 else "above"
            self.critic_text.setText(
                f"Points {direction} the MAL community on average, across "
                f"{summary['shows_scored']:,} scored shows. You score lower on "
                f"{summary['share_scored_below']:.0%} of them."
            )
        else:
            self.critic_value.setText("-")
            self.critic_text.setText("Sync MyAnimeList to see how critical you are.")
        note = "  A Last.fm sync was interrupted; the next one resumes it." if interrupted else ""
        self.last_sync.setText("Last successful sync: " + "   ".join(lines) + note)

    # -- syncing ------------------------------------------------------------

    def is_syncing(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start_sync(self, source: str) -> None:
        if not self.profile_id or self.is_syncing():
            return
        self.log.clear()
        self.progress.setRange(0, 0)  # busy until a page count arrives
        self.progress.setVisible(True)
        for button in self.sync_buttons.values():
            button.setEnabled(False)
        self.worker = SyncWorker(
            self.profile_id, source, self.ctx.store, self.ctx.client_factory, self.ctx.sync_kwargs
        )
        self.worker.message.connect(self._on_message)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_message(self, message: str) -> None:
        self.log.appendPlainText(message)
        match = PAGE_PROGRESS.search(message)
        if match:
            page, total = int(match.group(1)), int(match.group(2))
            self.progress.setRange(0, total)
            self.progress.setValue(page)

    def _on_done(self, results: dict) -> None:
        self.worker.wait()
        self.progress.setVisible(False)
        for button in self.sync_buttons.values():
            button.setEnabled(True)
        ok = bool(results) and all(results.values())
        self.log.appendPlainText("Done." if ok else "Finished with problems. See above.")
        self.refresh()
        self.sync_finished.emit(results)
