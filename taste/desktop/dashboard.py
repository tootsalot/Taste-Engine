"""Dashboard: how critical I am, what I finished and played lately, and syncing.

The sync log lives in a slim strip that opens while a sync runs and folds away
when it ends (it stays open if something went wrong). The rest is art and a few
numbers, all from taste.overview.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from taste import local_time, overview, settings
from taste.desktop import theme
from taste.desktop.art import ArtTile
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BarChart,
    Card,
    GenreCard,
    elided_lines,
    heading,
    label,
    section,
)
from taste.desktop.workers import SyncWorker

PROGRESS = re.compile(r"(\d+) of (\d+)")  # "page 3 of 40", "details for candidates, 10 of 200"
POSTER = QSize(104, 148)
COVER = QSize(104, 104)
SOURCES = (("mal", "MyAnimeList"), ("lastfm", "Last.fm"))
SCOPE_TIP = "Most played on Last.fm in the last {days} days. {note}"


def polish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class SyncStrip(QFrame):
    """One line when idle. While a sync runs: progress, the current step, and the log."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "strip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.dot = QFrame()
        self.dot.setObjectName("StatusDot")
        self.dot.setFixedSize(8, 8)
        self.dot.setProperty("state", "idle")
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = label("", role="muted", wrap=True)
        row.addWidget(self.text, 1)
        self.toggle = QPushButton("Show log")
        self.toggle.setProperty("role", "link")
        self.toggle.clicked.connect(lambda: self.show_log(self.log.isHidden()))
        self.toggle.setVisible(False)
        row.addWidget(self.toggle)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(170)
        self.log.setVisible(False)
        layout.addWidget(self.log)

    def set_state(self, state: str) -> None:
        self.dot.setProperty("state", state)
        polish(self.dot)

    def show_log(self, shown: bool) -> None:
        self.log.setVisible(shown)
        self.toggle.setText("Hide log" if shown else "Show log")

    def start(self) -> None:
        self.log.clear()
        self.progress.setRange(0, 0)  # busy until a page count arrives
        self.progress.setVisible(True)
        self.toggle.setVisible(True)
        self.show_log(True)
        self.set_state("running")
        self.text.setText("Starting the sync.")

    def message(self, message: str) -> None:
        self.log.appendPlainText(message)
        self.text.setText(message)
        match = PROGRESS.search(message)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def finish(self, ok: bool) -> None:
        self.progress.setVisible(False)
        self.log.appendPlainText("Done." if ok else "Finished with problems. See above.")
        self.set_state("ok" if ok else "error")
        self.show_log(not ok)

    def clear(self) -> None:
        self.log.clear()
        self.show_log(False)
        self.toggle.setVisible(False)
        self.set_state("idle")


class ShelfItem(QWidget):
    """A picture with a title (up to two lines) and a caption under it."""

    def __init__(self, ctx: AppContext, size: QSize, accent: str) -> None:
        super().__init__()
        self.setFixedWidth(size.width())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.art = ArtTile(ctx.art, size, accent=accent)
        layout.addWidget(self.art)
        self.title = label("", wrap=True)
        font = QFont(theme.BODY_FAMILY)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.title.setMaximumHeight(self.title.fontMetrics().lineSpacing() * 2)
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.title)
        self.caption = label("", role="caption", wrap=True)
        layout.addWidget(self.caption)
        layout.addStretch()

    def set(self, url: str | None, title: str, caption: str, tip: str = "") -> None:
        self.art.set_art(url, title)
        # Two lines at most, ending in "…" when cut; the tooltip has the whole title.
        self.title.setText(elided_lines(title, self.title.font(), self.width(), 2))
        self.caption.setText(caption)
        self.setToolTip(tip or f"{title}\n{caption}")


class Shelf(Card):
    """A titled row of pictures. Shows as many as fit the width."""

    def __init__(self, ctx: AppContext, title: str, size: QSize, accent: str) -> None:
        super().__init__()
        self.ctx = ctx
        self.art_size = size
        self.accent = accent
        self.items: list[ShelfItem] = []
        self.body.setSpacing(10)
        self.body.addWidget(section(title))
        # The row's width mustn't follow its pictures, or the card could never get
        # narrow enough for _fit to hide any.
        self.holder = QWidget()
        self.holder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.row = QHBoxLayout(self.holder)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(14)
        self.row.addStretch()
        self.body.addWidget(self.holder)
        self.empty = label("", role="muted", wrap=True)
        self.body.addWidget(self.empty)
        self.body.addStretch()  # a taller neighbor card leaves space below, not above

    def set_items(self, entries: list[tuple[str | None, str, str, str]], empty_text: str) -> None:
        """entries: (picture url, title, caption, tooltip; '' means title and caption)."""
        for item in self.items:
            self.row.removeWidget(item)
            item.deleteLater()
        self.items = []
        for url, title, caption, tip in entries:
            item = ShelfItem(self.ctx, self.art_size, self.accent)
            item.set(url, title, caption, tip)
            self.row.insertWidget(len(self.items), item, 0, Qt.AlignmentFlag.AlignTop)
            self.items.append(item)
        self.empty.setText(empty_text)
        self.empty.setVisible(not entries)
        self.holder.setVisible(bool(entries))
        self._fit()

    def _fit(self) -> None:
        margins = self.body.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        step = self.art_size.width() + self.row.spacing()
        fits = max(1, (room + self.row.spacing()) // step) if room > 0 else len(self.items)
        for i, item in enumerate(self.items):
            item.setVisible(i < fits)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fit()


class DashboardPage(QWidget):
    sync_started = Signal()
    sync_finished = Signal(dict)
    recs_updated = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.worker: SyncWorker | None = None
        self.outcome: bool | None = None  # how the last sync in this session went
        self.is_blocked = lambda: False  # the main window points this at "are recs refreshing?"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self.title = heading("Dashboard", 28)
        top.addWidget(self.title)
        top.addStretch()
        self.sync_buttons: dict[str, QPushButton] = {}
        for source, text in (
            ("mal", "Sync MyAnimeList"),
            ("lastfm", "Sync Last.fm"),
            ("all", "Sync everything"),
        ):
            button = QPushButton(text)
            if source == "all":
                button.setProperty("role", "primary")
            button.clicked.connect(lambda _=False, s=source: self.start_sync(s))
            self.sync_buttons[source] = button
            top.addWidget(button)
        layout.addLayout(top)

        self.strip = SyncStrip()
        self.progress = self.strip.progress
        self.log = self.strip.log
        layout.addWidget(self.strip)

        hero = QHBoxLayout()
        hero.setSpacing(14)
        critic = Card()
        critic.body.addWidget(section("How critical you are"))
        row = QHBoxLayout()
        row.setSpacing(14)
        self.critic_value = label("")
        self.critic_value.setFont(theme.number_font(44))
        self.critic_value.setStyleSheet(f"color: {theme.LILAC};")
        row.addWidget(self.critic_value, 0, Qt.AlignmentFlag.AlignVCenter)
        self.critic_text = label("", wrap=True)
        row.addWidget(self.critic_text, 1, Qt.AlignmentFlag.AlignVCenter)
        critic.body.addLayout(row)
        self.counts = label("", role="muted", wrap=True)
        critic.body.addWidget(self.counts)
        critic.body.addStretch()
        hero.addWidget(critic, 11)
        scores = Card()
        scores.body.addWidget(section("Your scores, 1 to 10"))
        self.score_chart = BarChart(theme.LILAC, empty_text="No scores yet", value_labels=True)
        self.score_chart.setMinimumHeight(80)
        scores.body.addWidget(self.score_chart, 1)
        hero.addWidget(scores, 9)
        layout.addLayout(hero)

        self.recent = Shelf(ctx, "Recently finished", POSTER, theme.LILAC)
        layout.addWidget(self.recent)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.repeat = Shelf(
            ctx, f"On repeat, last {overview.ON_REPEAT_DAYS} days", COVER, theme.CORAL
        )
        bottom.addWidget(self.repeat, 11)
        self.genres = GenreCard()
        bottom.addWidget(self.genres, 9)
        layout.addLayout(bottom)
        layout.addStretch(1)  # spare height goes below the cards, not into them

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.outcome = None
        self.strip.clear()
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        now = self.ctx.now()
        conn = self.ctx.connect(self.profile_id)
        try:
            cfg = settings.get_all(conn)
            count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            entries = count("SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NULL")
            scored = count("SELECT COUNT(*) FROM core_ratings WHERE source='mal'")
            plays = count("SELECT COUNT(*) FROM core_behavior_events WHERE source='lastfm'")
            summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
            synced = {}
            for source, _ in SOURCES:
                row = conn.execute(
                    "SELECT ended_at FROM sync_runs WHERE source = ? AND status = 'success' "
                    "AND mode <> 'enrich' ORDER BY sync_run_id DESC LIMIT 1",
                    (source,),
                ).fetchone()
                synced[source] = row[0] if row else None
            interrupted = count("SELECT COUNT(*) FROM sync_lastfm_windows WHERE status = 'open'")
            distribution = overview.score_distribution(conn)
            finished = overview.recently_finished(conn, limit=8)
            albums = overview.on_repeat(conn, now, limit=6)
            generous, harsh = overview.genre_lean(conn)
            scope_note = settings.lastfm_scope_note(conn)
        finally:
            conn.close()

        self.title.setText(cfg["display_name"] or self.profile_id)
        self.counts.setText(
            f"{entries:,} on your list · {scored:,} scored · {plays:,} Last.fm plays"
        )
        if summary and summary["shows_scored"]:
            diff = summary["avg_diff"] + 0.0
            self.critic_value.setText(f"{diff:+.2f}")
            self.critic_value.setVisible(True)
            direction = "below" if diff < 0 else "above"
            self.critic_text.setText(
                f"Points {direction} the MAL community on average, across "
                f"{summary['shows_scored']:,} scored shows. You score lower on "
                f"{summary['share_scored_below']:.0%} of them."
            )
        else:
            self.critic_value.setVisible(False)  # a lone dash at this size reads as a bar
            self.critic_text.setText("Sync MyAnimeList to see how critical you are.")
        if any(distribution):
            self.score_chart.set_data(distribution, [str(s) for s in range(1, 11)])
            self.score_chart.setToolTip(
                "\n".join(
                    f"{s}: {n} {'show' if n == 1 else 'shows'}"
                    for s, n in enumerate(distribution, 1)
                )
            )
        else:
            self.score_chart.set_data([], [])  # draws "No scores yet"

        self.recent.set_items(
            [(s.poster_url, s.title, finished_caption(s), finished_tip(s)) for s in finished],
            "Sync MyAnimeList, and the shows you finish show up here."
            if not entries
            else "No finished shows on your list yet.",
        )
        self.repeat.set_items(
            [(a.cover_url, a.title, f"{a.artist}\n{a.plays:,} plays", "") for a in albums],
            "Sync Last.fm to see the albums you've played most lately."
            if not plays
            else f"No album plays on Last.fm in the last {overview.ON_REPEAT_DAYS} days.",
        )
        self.repeat.setToolTip(SCOPE_TIP.format(days=overview.ON_REPEAT_DAYS, note=scope_note))
        self.genres.set_genres(
            generous,
            harsh,
            "Sync MyAnimeList to see which genres you rate above or below the crowd."
            if not scored
            else f"No genre has {cfg['genre_min_sample']} scored shows yet. The genre minimum "
            "sample in Settings sets how many it takes.",
            usual=summary["avg_diff"] + 0.0 if summary and summary["shows_scored"] else None,
        )

        if not self.is_syncing():
            parts = []
            for source, name in SOURCES:
                when = synced[source]
                described = local_time.describe(when, cfg["timezone"], now) if when else None
                parts.append(f"{name} synced {described}." if when else f"{name}: never synced.")
            text = " ".join(parts)
            if interrupted:
                text += " A Last.fm sync was interrupted; the next one resumes it."
            if self.outcome is False:
                text = "The last sync had problems; the log says what happened. " + text
            self.strip.text.setText(text)

    # -- syncing ------------------------------------------------------------

    def is_syncing(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def set_blocked(self, blocked: bool) -> None:
        for button in self.sync_buttons.values():
            button.setEnabled(not blocked and not self.is_syncing())
            button.setToolTip("Wait for the recommendations to finish." if blocked else "")

    def start_sync(self, source: str) -> None:
        if not self.profile_id or self.is_syncing() or self.is_blocked():
            return
        self.strip.start()
        for button in self.sync_buttons.values():
            button.setEnabled(False)
        self.worker = SyncWorker(
            self.profile_id,
            source,
            self.ctx.store,
            self.ctx.client_factory,
            self.ctx.sync_kwargs,
            self.ctx.recs_kwargs,
        )
        self.worker.recs_updated.connect(self.recs_updated)
        self.worker.message.connect(self.strip.message)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self.sync_started.emit()

    def _on_done(self, results: dict) -> None:
        self.worker.wait()
        for button in self.sync_buttons.values():
            button.setEnabled(not self.is_blocked())
        self.outcome = bool(results) and all(results.values())
        self.strip.finish(self.outcome)
        self.refresh()
        self.sync_finished.emit(results)


def finished_tip(show: overview.FinishedShow) -> str:
    """The tooltip adds MAL's main title when the card shows the English one."""
    if show.main_title == show.title:
        return ""
    return f"{show.title}\n{show.main_title}\n{finished_caption(show)}"


def finished_caption(show: overview.FinishedShow) -> str:
    """Like "You 8 · MAL 7.60": short enough for one line under a poster."""
    if show.my_score is None:
        return "Not scored"
    mine = f"You {show.my_score:g}"
    return f"{mine} · MAL {show.community_mean:.2f}" if show.community_mean is not None else mine
