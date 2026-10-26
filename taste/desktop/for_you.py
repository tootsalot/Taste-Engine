"""For You: suggestions as a grid of posters, with the selected one explained in full.

Lists come from the latest saved run (recommend.latest). "Refresh
recommendations" fetches what's missing and recomputes in a background thread,
like a sync. Tabs build their cards the first time they're shown, so pictures
download only when someone looks.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from taste import local_time, recommend, settings
from taste.desktop import motion, theme
from taste.desktop.art import ArtTile
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    Card,
    LazyPage,
    ScrollArea,
    Segmented,
    elided_lines,
    heading,
    label,
    plain_tip,
    section,
)
from taste.desktop.workers import RecsWorker
from taste.recommend import Rec, comparison, whole_score

LISTS = (
    ("anime", "Anime"),
    ("music_discover", "Discover music"),
    ("music_rediscover", "Rediscover"),
    ("film", "Films"),
    ("tv", "TV"),
    ("book", "Books"),
    ("everything", "Across everything · Experimental"),
)
LINK_HOSTS = {
    "myanimelist.net": "MyAnimeList",
    "www.last.fm": "Last.fm",
    "www.themoviedb.org": "TMDB",
    "hardcover.app": "Hardcover",
}
ACCENTS = {
    "anime": theme.LILAC,
    "film": theme.TEAL,
    "tv": theme.TEAL,
    "book": theme.AMBER,
}  # music is coral
# Lists with posters or covers rather than square artwork.
PORTRAIT = ("anime", "film", "tv", "book")
SWITCH_ACCENTS = {  # the list switch lit in each medium's color
    "anime": "anime",
    "music_discover": "music",
    "music_rediscover": "music",
    "film": "screen",
    "tv": "screen",
    "book": "books",
    "everything": "anime",
}
PROGRESS = re.compile(r"(\d+) of (\d+)")
TILE_POSTER = QSize(150, 214)
TILE_COVER = QSize(150, 150)
TILE_PAD = 8  # around a tile's picture, where the selected outline shows
TILE_GAP = 10
TILE_STEP = 4  # tile widths move in steps, so resizing doesn't redraw every picture
GRID_PAD = 14
DETAIL_POSTER = QSize(168, 240)
DETAIL_COVER = QSize(200, 200)
DETAIL_WIDTH = 440  # the panel that shows the selected suggestion in full

MEDIUM_LABELS = {"anime": "Anime", "film": "Film", "tv": "TV", "book": "Book", "artist": "Music"}


EMPTY = {
    "everything": "No mixed suggestions yet. Refresh recommendations to build "
    "the experimental list.",
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
    "film": (
        "No film suggestions yet. Connect your TMDB account in Settings, sync, then press "
        "Refresh recommendations. They start from the films you rated highest."
    ),
    "tv": (
        "No TV suggestions yet. Connect your TMDB account in Settings, sync, then press "
        "Refresh recommendations. They start from the shows you rated highest."
    ),
    "book": (
        "No book suggestions yet. Connect your Hardcover account in Settings, sync, then press "
        "Refresh recommendations. They start from the books you rated highest."
    ),
}
EMPTY_AFTER_RUN = {
    "everything": "No mixed suggestions with these settings. "
    "Sync a source and refresh recommendations.",
    "anime": (
        "Nothing to suggest with the current settings. Lowering the minimum MAL raters "
        "or adding anime types in Settings widens the pool."
    ),
    "music_discover": "No new artists found among the ones similar to what you play.",
    "music_rediscover": EMPTY["music_rediscover"],
    "tv": "No TV shows to suggest yet. Rating a few more shows on TMDB gives it more to go on.",
    "film": "No films to suggest yet. Rating a few more films on TMDB gives it more to go on.",
    "book": "No books to suggest yet. Rating a few more books on Hardcover gives it more to go on.",
}


def safe_link(url: str | None) -> str | None:
    """Only https links to the services the picks come from are opened."""
    if not url:
        return None
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in LINK_HOSTS else None


def score_text(kind: str, rec: Rec) -> tuple[str, str]:
    """(big number or label, caption) for a card. Runs saved before facts existed still read."""
    if kind == "everything":
        return score_text(rec.facts.get("list_kind", rec.kind), rec)
    if rec.score is None:
        return "-", ""
    if kind == "anime":
        # The whole score you'd most likely give, since MAL scores are whole numbers,
        # with the exact prediction to one decimal (good to about a point, not a hundredth).
        whole, exact = whole_score(rec.score), f"{rec.score:.1f}"
        mal = rec.facts.get("mal_mean")
        caption = f"for you ({exact}) · MAL {mal:.2f}" if mal else f"predicted for you ({exact})"
        return str(whole), caption
    if kind in ("film", "tv"):
        # TMDB ratings come in half points, so one decimal, not a whole number.
        mean = rec.facts.get("tmdb_mean")
        return f"{rec.score:.1f}", f"for you · TMDB {mean:.1f}" if mean else "predicted for you"
    if kind == "book":
        mean = rec.facts.get("hardcover_mean")
        return (
            f"{rec.score:.1f}★",
            f"for you · Hardcover {mean:.2f}" if mean else "predicted for you",
        )
    if kind == "music_discover":
        # Last.fm's similarity, 0 to 1, as a percentage; the words say which third it's in.
        return f"{rec.score:.0%}", rec.facts.get("match_label") or "match"
    return f"{int(rec.score):,}", "plays"


def chance_text(chance: float, kind: str = "anime") -> str:
    if kind == "book":
        return f"{chance:.0%} chance you'd give it 4★ or more"
    return f"{chance:.0%} chance you'd give it an 8 or more"


class PosterTile(QFrame):
    """One suggestion in the grid: its picture, title, and score. Click to see why."""

    clicked = Signal(int)

    def __init__(self, ctx: AppContext, list_kind: str, rec: Rec, index: int) -> None:
        super().__init__()
        self.rec = rec
        visual_kind = (
            rec.facts.get("list_kind", list_kind) if list_kind == "everything" else list_kind
        )
        self.index = index
        self.setProperty("role", "tile")
        self.setProperty("selected", False)
        self.setProperty("accent", visual_kind.split("_")[0])  # anime, music, or film
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.poster = visual_kind in PORTRAIT
        size = TILE_POSTER if self.poster else TILE_COVER
        self.setFixedWidth(size.width() + 2 * TILE_PAD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(TILE_PAD, TILE_PAD, TILE_PAD, TILE_PAD)
        layout.setSpacing(6)
        self.art = ArtTile(ctx.art, size, accent=ACCENTS.get(visual_kind, theme.CORAL))
        self.art.set_art(rec.image_url, rec.title, fallback=rec.facts.get("fallback_image"))
        layout.addWidget(self.art)
        if list_kind == "everything":
            layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            self.medium = label(MEDIUM_LABELS[rec.kind], role="badge")
            self.medium.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self.medium.setProperty("accent", SWITCH_ACCENTS.get(visual_kind, "music"))
            layout.addWidget(self.medium, 0, Qt.AlignmentFlag.AlignLeft)
        # The score sits beside the title (2.4), not under a two-line gap.
        number, caption = score_text(list_kind, rec)
        self.score = label(number if visual_kind != "music_rediscover" else f"{number} plays")
        self.score.setProperty("role", "tile-score")
        self.score.setProperty("accent", visual_kind.split("_")[0])
        self.score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.title = label("", wrap=True)
        font = QFont(theme.BODY_FAMILY)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.picture_width = size.width()
        self.title.setText(elided_lines(rec.title, font, self._title_width(), 2))
        self.title.setFixedHeight(self.title.fontMetrics().lineSpacing() * 2 + 2)
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(self.title, 1)
        line.addWidget(self.score, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(line)
        self.setToolTip(plain_tip(rec.title))

    def set_width(self, width: int) -> None:
        """Grow or shrink with the grid, so every row fills the panel."""
        picture = width - 2 * TILE_PAD
        if picture == self.picture_width:
            return
        self.picture_width = picture
        ratio = TILE_POSTER.height() / TILE_POSTER.width() if self.poster else 1.0
        self.art.set_size(QSize(picture, round(picture * ratio)))
        self.setFixedWidth(width)
        self.title.setText(elided_lines(self.rec.title, self.title.font(), self._title_width(), 2))

    def _title_width(self) -> int:
        """The title's room: the picture's width less the score beside it."""
        score = self.score.fontMetrics().horizontalAdvance(self.score.text()) + 12
        return max(self.picture_width - score - 8, 40)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.index)
        else:
            super().keyPressEvent(event)


class RecCard(Card):
    """The selected suggestion in full: picture, score, the reasons, and what to do."""

    dismissed = Signal(str, str)  # list kind, item key
    opened = Signal(str)  # url

    def __init__(self, ctx: AppContext, list_kind: str, rec: Rec) -> None:
        super().__init__()
        self.rec = rec
        visual_kind = (
            rec.facts.get("list_kind", list_kind) if list_kind == "everything" else list_kind
        )
        self.list_kind = list_kind
        poster = visual_kind in PORTRAIT
        accent = ACCENTS.get(visual_kind, theme.CORAL)
        self.body.setContentsMargins(22, 22, 22, 20)
        self.body.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(16)
        self.art = ArtTile(ctx.art, DETAIL_POSTER if poster else DETAIL_COVER, accent=accent)
        self.art.set_art(rec.image_url, rec.title, fallback=rec.facts.get("fallback_image"))
        top.addWidget(self.art, 0, Qt.AlignmentFlag.AlignTop)
        side = QVBoxLayout()
        side.setSpacing(4)
        number, caption = score_text(list_kind, rec)
        self.score = label(number)
        self.score.setFont(theme.number_font(40))
        self.score.setStyleSheet(f"color: {accent};")
        side.addWidget(self.score, 0, Qt.AlignmentFlag.AlignLeft)
        if list_kind == "everything":
            caption = MEDIUM_LABELS[rec.kind] + " · " + caption
        self.caption = label(caption, role="muted", wrap=True)
        side.addWidget(self.caption)
        chance = rec.facts.get("chance_4_plus" if visual_kind == "book" else "chance_8_plus")
        self.chance = label(
            chance_text(chance, visual_kind) if chance is not None else "", role="muted", wrap=True
        )
        side.addWidget(self.chance)
        if rec.badge:
            side.addWidget(label(rec.badge, role="badge"), 0, Qt.AlignmentFlag.AlignLeft)
        side.addStretch(1)
        top.addLayout(side, 1)
        self.body.addLayout(top)

        self.title = label(rec.title, wrap=True)
        self.title.setFont(theme.title_font(22))
        self.body.addWidget(self.title)
        if rec.subtitle:
            self.body.addWidget(label(rec.subtitle, role="muted", wrap=True))
        for reason in rec.reasons:
            self.body.addWidget(label(reason, role="reason", wrap=True))
        # What it is, and how many people know it: the panel's room is for this.
        about = rec.facts.get("about") or ""
        self.about = label(about, wrap=True)
        if about:
            self.body.addSpacing(4)
            self.body.addWidget(section("About"))
            self.body.addWidget(self.about)
        popularity = rec.facts.get("popularity") or []
        self.popularity = [label(text, role="chip") for text in popularity]
        if popularity:
            self.body.addSpacing(4)
            self.body.addWidget(section("How popular"))
            chips = QGridLayout()  # two to a row, so they never push the panel wider
            chips.setSpacing(6)
            for i, chip in enumerate(self.popularity):
                chips.addWidget(chip, i // 2, i % 2, Qt.AlignmentFlag.AlignLeft)
            chips.setColumnStretch(2, 1)
            self.body.addLayout(chips)
        details = rec.facts.get("details", [])
        if details:
            self.body.addSpacing(4)
            self.body.addWidget(section("Behind the score"))
            for line in details:
                self.body.addWidget(label(line, role="caption", wrap=True))
        # The numbers behind the words, also a hover away.
        self.setToolTip(plain_tip("\n".join(details)))
        # The buttons follow the words, together (2.4); spare height goes under them.
        self.body.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        link = safe_link(rec.url)
        self.open_button = QPushButton(
            f"Open on {LINK_HOSTS[urlsplit(link).hostname]}" if link else "No link"
        )
        self.open_button.setProperty("role", "primary")
        self.open_button.setProperty("accent", SWITCH_ACCENTS.get(visual_kind, "anime"))
        self.open_button.setEnabled(link is not None)
        self.open_button.clicked.connect(lambda: self.opened.emit(link))
        self.dismiss_button = QPushButton("Not interested")
        self.dismiss_button.setToolTip("Hide this for good. It won't be suggested again.")
        self.dismiss_button.clicked.connect(
            lambda: self.dismissed.emit(self.list_kind, self.rec.item_key)
        )
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.dismiss_button)
        buttons.addStretch()
        self.body.addLayout(buttons)
        self.body.addStretch(1)
        # Only now do the labels have a parent. Showing a widget without one opens it
        # as its own window, which flashed up on screen for every card.
        self.caption.setVisible(bool(caption))
        self.chance.setVisible(chance is not None)


def columns_for(width: int) -> int:
    """How many tiles fit side by side in a grid this wide."""
    return max(1, (width + TILE_GAP) // (TILE_POSTER.width() + 2 * TILE_PAD + TILE_GAP))


class RecList(QWidget):
    """One tab: a note line, a grid of suggestions, and the selected one in full."""

    def __init__(self, list_kind: str) -> None:
        super().__init__()
        self.list_kind = list_kind
        self.recs: list[Rec] = []
        self.has_run = False
        self.dirty = True
        self.tiles: list[PosterTile] = []
        self.detail: RecCard | None = None
        self.selected = 0
        self.columns = 1
        self.tile_width = 0
        self._ctx = None
        self._on_open = None
        self._on_dismiss = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        self.note = label("", role="muted", wrap=True)  # every tab's note alike
        layout.addWidget(self.note)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = Card()  # the grid's own panel, beside the one showing the selected
        outer = holder.body
        outer.setContentsMargins(GRID_PAD, GRID_PAD, GRID_PAD, GRID_PAD)
        self.grid = QGridLayout()
        self.grid.setSpacing(TILE_GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        outer.addLayout(self.grid)
        self.empty = label("", role="muted", wrap=True)
        outer.addWidget(self.empty)
        outer.addStretch(1)
        self.scroll.setWidget(holder)
        body.addWidget(self.scroll, 1)
        # The panel scrolls on its own when a suggestion has more to say than fits.
        self.panel = QScrollArea()
        self.panel.setWidgetResizable(True)
        self.panel.setFrameShape(QScrollArea.Shape.NoFrame)
        self.panel.setFixedWidth(DETAIL_WIDTH)
        self.panel_body = QWidget()
        self.panel_layout = QVBoxLayout(self.panel_body)
        self.panel_layout.setContentsMargins(0, 0, 0, 0)
        self.panel.setWidget(self.panel_body)
        body.addWidget(self.panel)
        layout.addLayout(body, 1)
        self.panel.setVisible(False)
        # The width the tiles really get, which also shrinks when a scroll bar appears.
        self.scroll.viewport_resized.connect(self._fit)

    @property
    def cards(self) -> list[RecCard]:
        """The detail card, as a list (older callers looked at a list of cards)."""
        return [self.detail] if self.detail is not None else []

    def set_recs(self, recs: list[Rec], has_run: bool) -> None:
        self.recs = recs
        self.has_run = has_run
        self.dirty = True

    def render(self, ctx: AppContext, on_open, on_dismiss) -> None:
        self._ctx, self._on_open, self._on_dismiss = ctx, on_open, on_dismiss
        # Only cards that weren't on screen fade in: in 2.0 every refresh, hidden pick,
        # and suggestions update faded the whole grid out and back, sometimes twice.
        shown = {t.rec.item_key for t in self.tiles}
        for tile in self.tiles:
            self.grid.removeWidget(tile)
            tile.deleteLater()
        self.tiles = []
        for i, rec in enumerate(self.recs):
            tile = PosterTile(ctx, self.list_kind, rec, i)
            tile.clicked.connect(self.select)
            if self.tile_width:
                tile.set_width(self.tile_width)
            self.tiles.append(tile)
        self._place()
        texts = EMPTY_AFTER_RUN if self.has_run else EMPTY
        self.empty.setText("" if self.recs else texts[self.list_kind])
        self.empty.setVisible(not self.recs)
        self.panel.setVisible(bool(self.recs))
        self.dirty = False
        if self.recs:
            self.select(min(self.selected, len(self.recs) - 1), animate=False)
        else:
            self._clear_detail()
        motion.fade_in([t for t in self.tiles if t.rec.item_key not in shown])

    def select(self, index: int, animate: bool = True) -> None:
        """Show one suggestion in full in the panel."""
        if not 0 <= index < len(self.recs):
            return
        self.selected = index
        for i, tile in enumerate(self.tiles):
            tile.set_selected(i == index)
        self._clear_detail()
        card = RecCard(self._ctx, self.list_kind, self.recs[index])
        card.opened.connect(self._on_open)
        card.dismissed.connect(self._on_dismiss)
        self.panel_layout.addWidget(card)
        self.detail = card
        if animate:
            motion.fade_in([card], stagger=False)

    def tile_for(self, item_key: str) -> PosterTile | None:
        return next((t for t in self.tiles if t.rec.item_key == item_key), None)

    def _clear_detail(self) -> None:
        if self.detail is not None:
            self.panel_layout.removeWidget(self.detail)
            self.detail.deleteLater()
            self.detail = None

    def _place(self) -> None:
        """Lay the tiles out row by row, as many across as fit."""
        for tile in self.tiles:
            self.grid.removeWidget(tile)
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // self.columns, i % self.columns)

    def _fit(self, width: int) -> None:
        """As many tiles across as fit, then widened so the row fills the panel."""
        room = width - 2 * GRID_PAD - 2
        columns = columns_for(room)
        tile = (room - (columns - 1) * TILE_GAP) // columns // TILE_STEP * TILE_STEP
        for t in self.tiles:
            t.set_width(tile)
        self.tile_width = tile
        if columns != self.columns:
            self.columns = columns
            self._place()


class ListSwitch(Segmented):
    """The list pills wrap on a narrow page, keeping the same selection and colors."""

    def __init__(self) -> None:
        super().__init__(LISTS, accents=SWITCH_ACCENTS)
        outer = self.layout()
        while outer.count():
            outer.takeAt(0)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        self.rows.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.rows)
        self.wrap_width = 900
        self._lines = []
        self.reflow(self.wrap_width)

    def minimumSizeHint(self) -> QSize:
        buttons = getattr(self, "buttons", {})
        return QSize(
            max((b.minimumWidth() for b in buttons.values()), default=0) + 4, self.height()
        )

    def sizeHint(self) -> QSize:
        return QSize(
            min(self.wrap_width, sum(b.minimumWidth() for b in self.buttons.values()) + 4),
            self.minimumHeight(),
        )

    def set_text(self, key: str, text: str) -> None:
        super().set_text(key, text)
        self.reflow(self.wrap_width)

    def reflow(self, width: int) -> None:
        self.wrap_width = max(width, 1)
        while self.rows.count():
            old = self.rows.takeAt(0).layout()
            while old.count():
                old.takeAt(0)
            old.deleteLater()
        self._lines = []
        line, used = None, 0
        for button in self.buttons.values():
            size = button.minimumWidth()
            if line is None or used + size > self.wrap_width - 4:
                if line is not None:
                    line.addStretch()
                line = QHBoxLayout()
                line.setSpacing(0)
                line.setContentsMargins(0, 0, 0, 0)
                self.rows.addLayout(line)
                self._lines.append(line)
                used = 0
            line.addWidget(button)
            used += size
        if line is not None:
            line.addStretch()
        height = max(b.sizeHint().height() for b in self.buttons.values())
        self.setFixedHeight(len(self._lines) * height + 4)
        self.setMaximumWidth(self.wrap_width)
        self.layout().activate()
        self.updateGeometry()
        self._move_pill(animate=False)


class ForYouPage(LazyPage, QWidget):
    refresh_started = Signal()
    refresh_finished = Signal(dict)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.worker: RecsWorker | None = None
        self.fetching = False  # True while a refresh calls the APIs (minutes, not a second)
        self.is_blocked = lambda: False  # the main window points this at "is a sync running?"
        self.withheld = False  # a saved list is held back until it's recomputed
        self._rebuilt: set[str] = set()  # profiles whose held-back lists were recomputed

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
        # The lists switch with the app's pill switch (2.4), lit in each medium's color;
        # the tab bar stays hidden and the two follow each other.
        self.tabs.tabBar().hide()
        self.switch = ListSwitch()
        self.switch.changed.connect(self._switch_to)
        self.tabs.currentChanged.connect(self._follow_tab)
        self.switch.set_value(LISTS[0][0])
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.tabs, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.switch.reflow(max(self.width() - 56, 1))

    def _switch_to(self, kind: str) -> None:
        self.tabs.setCurrentWidget(self.lists[kind])

    def _follow_tab(self, index: int) -> None:
        kind = LISTS[index][0] if 0 <= index < len(LISTS) else None
        if kind is not None and self.switch.value() != kind:
            self.switch.set_value(kind)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.say("")
        self.reload()

    def lazy_refresh(self) -> None:  # LazyPage: after a sync, when shown
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
                # "Anime · 30": the count after a dot, like the rest of the app (2.4).
                text = f"{title} · {len(recs)}" if created else title
                self.tabs.setTabText(index, text)
                self.switch.set_text(kind, text)
                if kind in ("anime", "film", "tv", "book"):
                    model = metrics.get("selected_model", {}).get("name", "usual")
                    accuracy = comparison.saved_accuracy(conn, kind, model) if created else None
                    self.lists[kind].note.setText(
                        model_note(kind, metrics, accuracy) if created else ""
                    )
                    self.lists[kind].note.setToolTip(
                        plain_tip(model_tip(kind, metrics, accuracy)) if created else ""
                    )
                if kind == "everything":
                    checks = []
                    for medium, check in metrics.get("holdouts", {}).items():
                        error, count = check.get("mae"), check.get("count", 0)
                        checks.append(
                            f"{MEDIUM_LABELS.get(medium, medium.title())}: "
                            + (
                                f"{error:.2f} points on {count}"
                                if error is not None
                                else "no rating holdout"
                            )
                        )
                    self.lists[kind].note.setText(
                        "Experimental. Genre themes mix the existing pools. "
                        "Baseline holdouts use the 1 to 10 scale. " + "; ".join(checks)
                        if created
                        else ""
                    )
                newest = max(filter(None, (newest, created)), default=None)
            self.withheld = any(recommend.withheld(conn, k) for k in ("anime", "everything"))
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
        self.rebuild_withheld()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self.rebuild_withheld()

    def rebuild_withheld(self) -> None:
        """A list saved before 3.0, or before an adult-visibility change, is held back; it
        comes back from the cache (no network) once, while this page is in front."""
        if (
            self.withheld
            and self.profile_id not in self._rebuilt
            and self.isVisibleTo(self.window())
            and self.start_refresh(fetch=False) is not None
        ):
            self._rebuilt.add(self.profile_id)

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
        """Hide a suggestion for good. Its tile fades out, then the grid closes up."""
        kind = list_kind if list_kind in ("anime", "film", "tv", "book", "everything") else "artist"
        conn = self.ctx.connect(self.profile_id)
        try:
            recommend.dismiss(conn, kind, item_key)
        finally:
            conn.close()
        tab = self.lists[list_kind]
        hidden = next((r.title for r in tab.recs if r.item_key == item_key), "")
        self.say(f"Hidden {hidden}. It won't be suggested again.")
        tile = tab.tile_for(item_key)
        if tile is None:
            self.reload()
        else:
            motion.fade_out(tile, self.reload)

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


def model_note(kind: str, metrics: dict, accuracy: dict | None = None) -> str:
    """Describe the saved model using the same held-out words as the startup question."""
    model = metrics.get("selected_model", {}).get("name", "usual")
    title = settings.REC_MODELS.get(model, "Selected model")
    prefix = title if model == "usual" else f"{title} (experimental)"
    if accuracy is None:
        return f"{prefix}: recompute this list to check its accuracy."
    text = comparison.accuracy_text(kind, accuracy)
    if accuracy.get("within_one") is None:
        count = accuracy.get("count", 0)
        noun = "title" if count == 1 else "titles"
        return f"{prefix}: {count} rated {noun}. {text}"
    verdict = comparison.VERDICT_TEXT.get(accuracy.get("verdict"), "") if model != "usual" else ""
    verdict = verdict[:1].upper() + verdict[1:]
    return f"{prefix}: {text}" + (f" {verdict}." if verdict else "")


def model_tip(kind: str, metrics: dict, accuracy: dict | None = None) -> str:
    note = model_note(kind, metrics, accuracy)
    if accuracy is None:
        return note + " No current held-out check is saved for this model."
    if accuracy.get("within_one") is None:
        return note + " Each list needs at least 20 eligible ratings for this check."
    return (
        note + f" Checked on {accuracy['count']} ratings using the same five folds as the lab. "
        "Each prediction is fitted without that title's score."
    )
