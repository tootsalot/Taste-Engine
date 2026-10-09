"""For You: anime and music recommendations, each card explaining itself.

Lists come from the latest saved run (recommend.latest). "Refresh
recommendations" fetches what's missing and recomputes in a background thread,
like a sync. Tabs build their cards the first time they're shown, so pictures
download only when someone looks.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from taste import local_time, recommend, settings
from taste.desktop import theme
from taste.desktop.art import ArtTile
from taste.desktop.context import AppContext
from taste.desktop.widgets import Card, heading, label
from taste.desktop.workers import RecsWorker
from taste.recommend import Rec

LISTS = (
    ("anime", "Anime"),
    ("music_discover", "Discover music"),
    ("music_rediscover", "Rediscover"),
)
LINK_HOSTS = {"myanimelist.net": "MyAnimeList", "www.last.fm": "Last.fm"}
PROGRESS = re.compile(r"(\d+) of (\d+)")
COLUMNS = 2
POSTER = QSize(96, 136)
COVER = QSize(112, 112)

EMPTY = {
    "anime": (
        "No anime suggestions yet. Sync MyAnimeList, then press Refresh recommendations. "
        "The first refresh takes about five minutes; later ones take seconds."
    ),
    "music_discover": ("No new artists yet. Sync Last.fm, then press Refresh recommendations."),
    "music_rediscover": (
        "Nobody to rediscover yet. This lists artists you played at least "
        f"{recommend.REDISCOVER_MIN_PLAYS} times who have been quiet for "
        f"{recommend.REDISCOVER_QUIET_DAYS} days."
    ),
}
EMPTY_AFTER_RUN = {
    "anime": (
        "Nothing to suggest with the current settings. Lowering the minimum MAL raters "
        "or adding anime types in Settings widens the pool."
    ),
    "music_discover": "No new artists found among the ones similar to what you play.",
    "music_rediscover": EMPTY["music_rediscover"],
}


def safe_link(url: str | None) -> str | None:
    """Only https links to MyAnimeList or Last.fm are opened."""
    if not url:
        return None
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in LINK_HOSTS else None


def score_text(kind: str, score: float | None) -> tuple[str, str]:
    """(big number, caption) for a card."""
    if score is None:
        return "-", ""
    if kind == "anime":
        # One decimal: the model is good to about a point, not a hundredth.
        return f"{score:.1f}", "predicted for you"
    if kind == "music_discover":
        return f"{score:.2f}", "match"
    return f"{int(score):,}", "plays"


class RecCard(Card):
    dismissed = Signal(str, str)  # list kind, item key
    opened = Signal(str)  # url

    def __init__(self, ctx: AppContext, list_kind: str, rec: Rec) -> None:
        super().__init__()
        self.rec = rec
        self.list_kind = list_kind
        anime = list_kind == "anime"
        accent = theme.LILAC if anime else theme.CORAL

        row = QHBoxLayout()
        row.setSpacing(14)
        self.art = ArtTile(ctx.art, POSTER if anime else COVER, accent=accent)
        self.art.set_art(rec.image_url, rec.title)
        row.addWidget(self.art, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(4)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = label(rec.title, wrap=True)
        self.title.setFont(theme.title_font(16))
        titles.addWidget(self.title)
        if rec.subtitle:
            titles.addWidget(label(rec.subtitle, role="muted", wrap=True))
        top.addLayout(titles, 1)
        number, caption = score_text(list_kind, rec.score)
        score_box = QVBoxLayout()
        score_box.setSpacing(0)
        self.score = label(number)
        self.score.setFont(theme.number_font(26))
        self.score.setStyleSheet(f"color: {accent};")
        self.score.setAlignment(Qt.AlignmentFlag.AlignRight)
        score_box.addWidget(self.score)
        cap = label(caption, role="muted")
        cap.setAlignment(Qt.AlignmentFlag.AlignRight)
        score_box.addWidget(cap)
        top.addLayout(score_box)
        text.addLayout(top)

        if rec.badge:
            badge = label(rec.badge, role="badge")
            text.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
        for reason in rec.reasons:
            text.addWidget(label(reason, role="reason", wrap=True))
        text.addStretch()

        buttons = QHBoxLayout()
        link = safe_link(rec.url)
        self.open_button = QPushButton(
            f"Open on {LINK_HOSTS[urlsplit(link).hostname]}" if link else "No link"
        )
        self.open_button.setProperty("role", "link")
        self.open_button.setEnabled(link is not None)
        self.open_button.clicked.connect(lambda: self.opened.emit(link))
        self.dismiss_button = QPushButton("Not interested")
        self.dismiss_button.setProperty("role", "link")
        self.dismiss_button.setToolTip("Hide this for good. It won't be suggested again.")
        self.dismiss_button.clicked.connect(
            lambda: self.dismissed.emit(self.list_kind, self.rec.item_key)
        )
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        buttons.addWidget(self.dismiss_button)
        text.addLayout(buttons)
        row.addLayout(text, 1)
        self.body.addLayout(row)


class RecList(QWidget):
    """One tab: a note line and a two-column grid of cards."""

    def __init__(self, list_kind: str) -> None:
        super().__init__()
        self.list_kind = list_kind
        self.recs: list[Rec] = []
        self.has_run = False
        self.dirty = True
        self.cards: list[RecCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        self.note = label("", role="muted" if list_kind == "anime" else "scope", wrap=True)
        layout.addWidget(self.note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 0, 6, 0)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)
        outer.addLayout(self.grid)
        self.empty = label("", role="muted", wrap=True)
        outer.addWidget(self.empty)
        outer.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

    def set_recs(self, recs: list[Rec], has_run: bool) -> None:
        self.recs = recs
        self.has_run = has_run
        self.dirty = True

    def render(self, ctx: AppContext, on_open, on_dismiss) -> None:
        for card in self.cards:
            self.grid.removeWidget(card)
            card.deleteLater()
        self.cards = []
        for i, rec in enumerate(self.recs):
            card = RecCard(ctx, self.list_kind, rec)
            card.opened.connect(on_open)
            card.dismissed.connect(on_dismiss)
            self.grid.addWidget(card, i // COLUMNS, i % COLUMNS)
            self.cards.append(card)
        texts = EMPTY_AFTER_RUN if self.has_run else EMPTY
        self.empty.setText("" if self.recs else texts[self.list_kind])
        self.empty.setVisible(not self.recs)
        self.dirty = False


class ForYouPage(QWidget):
    refresh_started = Signal()
    refresh_finished = Signal(dict)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.worker: RecsWorker | None = None
        self.fetching = False  # True while a refresh calls the APIs (minutes, not a second)
        self.is_blocked = lambda: False  # the main window points this at "is a sync running?"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(heading("For You", 28))
        top.addStretch()
        self.updated = label("", role="muted")
        top.addWidget(self.updated)
        self.refresh_button = QPushButton("Refresh recommendations")
        self.refresh_button.setProperty("role", "primary")
        self.refresh_button.clicked.connect(lambda: self.start_refresh())
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = label("", role="muted", wrap=True)
        self.status.setVisible(False)  # no empty band above the tabs
        layout.addWidget(self.status)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)  # no frame: the cards are the surface
        self.tabs.tabBar().setDrawBase(False)
        self.lists: dict[str, RecList] = {}
        for kind, title in LISTS:
            tab = RecList(kind)
            self.lists[kind] = tab
            self.tabs.addTab(tab, title)
        self.tabs.currentChanged.connect(lambda _: self._render_current())
        layout.addWidget(self.tabs, 1)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.say("")
        self.reload()

    def reload(self) -> None:
        """Read the latest saved lists. Cards are rebuilt when their tab is shown."""
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            newest = None
            for kind, title in LISTS:
                recs, metrics, created = recommend.latest(conn, kind)
                self.lists[kind].set_recs(recs, created is not None)
                index = self.tabs.indexOf(self.lists[kind])
                self.tabs.setTabText(index, f"{title} ({len(recs)})" if created else title)
                if kind == "anime":
                    self.lists[kind].note.setText(accuracy_text(metrics))
                newest = max(filter(None, (newest, created)), default=None)
            scope = settings.lastfm_scope_note(conn)
            tz_name = settings.get(conn, "timezone")
        finally:
            conn.close()
        for kind in ("music_discover", "music_rediscover"):
            self.lists[kind].note.setText(f"Based on Last.fm plays. {scope}")
        self.updated.setText(
            f"Updated {local_time.describe(newest, tz_name, self.ctx.now())}"
            if newest
            else "Never refreshed"
        )
        self._render_current()

    def say(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _render_current(self) -> None:
        tab = self.tabs.currentWidget()
        if isinstance(tab, RecList) and tab.dirty:
            tab.render(self.ctx, self.open_link, self.dismiss)

    # -- actions ------------------------------------------------------------

    def open_link(self, url: str | None) -> bool:
        link = safe_link(url)
        if link:
            QDesktopServices.openUrl(QUrl(link))
        return link is not None

    def dismiss(self, list_kind: str, item_key: str) -> None:
        kind = "anime" if list_kind == "anime" else "artist"
        conn = self.ctx.connect(self.profile_id)
        try:
            recommend.dismiss(conn, kind, item_key)
        finally:
            conn.close()
        hidden = next((r.title for r in self.lists[list_kind].recs if r.item_key == item_key), "")
        self.say(f"Hidden {hidden}. It won't be suggested again.")
        self.reload()

    # -- refreshing ---------------------------------------------------------

    def is_refreshing(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def is_fetching(self) -> bool:
        return self.is_refreshing() and self.fetching

    def set_blocked(self, blocked: bool) -> None:
        self.refresh_button.setEnabled(not blocked and not self.is_refreshing())
        if blocked and not self.is_refreshing():
            self.refresh_button.setToolTip("Wait for the sync to finish.")
        else:
            self.refresh_button.setToolTip("")

    def start_refresh(self, fetch: bool = True) -> RecsWorker | None:
        if not self.profile_id or self.is_refreshing() or self.is_blocked():
            return None
        self.refresh_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.say(
            "Fetching what recommendations need. The first time takes about five minutes."
            if fetch
            else "Recomputing recommendations."
        )
        kwargs = dict(self.ctx.recs_kwargs)
        kwargs["fetch"] = fetch
        self.fetching = fetch
        self.worker = RecsWorker(self.profile_id, self.ctx.store, self.ctx.client_factory, kwargs)
        self.worker.message.connect(self._on_message)
        self.worker.updated.connect(self.reload)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self.refresh_started.emit()
        return self.worker

    def _on_message(self, message: str) -> None:
        self.say(message.removeprefix("Recommendations: "))
        match = PROGRESS.search(message)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _on_done(self, counts: dict) -> None:
        self.worker.wait()
        self.progress.setVisible(False)
        self.refresh_button.setEnabled(not self.is_blocked())
        if not counts:
            self.say(
                (self.status.text() + " " if self.status.text() else "")
                + "Whatever was fetched is kept; the next refresh continues."
            )
        self.reload()
        self.refresh_finished.emit(counts)


def accuracy_text(metrics: dict) -> str:
    """The holdout check, in plain words (recommend.evaluate)."""
    if metrics.get("model") is None:
        return (
            "Accuracy check: not enough scored shows yet. It holds back about a fifth of "
            "your scored shows and needs at least 5 of them, so about 25 scored shows."
        )
    return (
        f"Accuracy check: on {metrics['held_out']} of your scored shows the model never saw, "
        f"its predictions were off by {metrics['model']:.2f} points on average. The MAL score "
        f"alone was off by {metrics['community']:.2f}, and the MAL score plus your usual "
        f"difference by {metrics['overall']:.2f}. Lower is better."
    )
