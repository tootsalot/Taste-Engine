"""Dashboard: how critical I am, what I finished and played lately, and syncing.

The cards sit in a grid whose rows share the window's height, so a big window
gets bigger posters and more genres rather than empty space below the cards. A
wide window puts three cards across; a narrow one two.

The sync log lives in a slim strip that opens while a sync runs and folds away
when it ends (it stays open if something went wrong). The numbers come from
taste.overview and taste.activity.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from taste import activity, charts, local_time, overview, persona, settings
from taste.desktop import motion, theme
from taste.desktop.art import ArtTile
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BarChart,
    Card,
    GenreCard,
    ScrollArea,
    elided_lines,
    heading,
    label,
    plain_tip,
    section,
)
from taste.desktop.workers import SyncWorker

PROGRESS = re.compile(r"(\d+) of (\d+)")  # "page 3 of 40", "details for candidates, 10 of 200"
POSTER = QSize(104, 148)
COVER = QSize(104, 104)
ART_GROWTH = 2.2  # pictures grow with the room they get, up to this many times the base
ART_STEP = 8  # sizes move in steps, so resizing the window doesn't decode every pixel
SOURCES = (("mal", "MyAnimeList"), ("lastfm", "Last.fm"))
SCOPE_TIP = "Most played on Last.fm in the last {days} days. {note}"
# Enough for a very wide window. Shelves show as many as fit, and an item downloads
# its picture only once it's on screen.
SHELF_MAX = 30
WIDE = 1500  # page width from which three cards go across instead of two
TRAITS_IN_ONE_ROW = 2100  # page width from which Your taste's traits fit in one row
COLUMNS = 12  # grid columns: thirds are 4, halves 6
WEEKS_MIN_BAR = 18
RECAP_NAME = "Rev"  # the recap page's name in the sidebar


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
        if shown != motion.is_open(self.log):
            motion.reveal(self.log, shown)
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
    """A picture with a title (up to two lines) and a caption under it.

    The picture loads the first time the item is shown, so items a narrow window
    leaves hidden never download theirs.
    """

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
        self.full_title = ""
        self.pending: tuple[str | None, str] | None = None  # picture to load when shown

    def set(self, url: str | None, title: str, caption: str, tip: str = "") -> None:
        self.art.set_art(None, title)  # the lettered placeholder until it's on screen
        self.pending = (url, title)
        self.full_title = title
        if self.isVisible():
            self._load()
        self._elide()
        self.caption.setText(caption)
        self.setToolTip(plain_tip(tip or f"{title}\n{caption}"))

    def set_art_size(self, size: QSize) -> None:
        self.setFixedWidth(size.width())
        self.art.set_size(size)
        self._elide()

    def _elide(self) -> None:
        # Two lines at most, ending in "…" when cut; the tooltip has the whole title.
        self.title.setText(elided_lines(self.full_title, self.title.font(), self.width(), 2))

    def _load(self) -> None:
        if self.pending is not None:
            url, title = self.pending
            self.pending = None
            self.art.set_art(url, title)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._load()


class Shelf(Card):
    """A titled row of pictures. Shows as many as fit the width, and the pictures grow
    with the height the card gets (up to ART_GROWTH times their base size)."""

    def __init__(
        self, ctx: AppContext, title: str, size: QSize, accent: str, caption_lines: int = 1
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.base_size = QSize(size)
        self.art_size = QSize(size)
        self.accent = accent
        self.caption_lines = caption_lines
        self.items: list[ShelfItem] = []
        self.body.setSpacing(10)
        self.title_label = section(title)
        self.body.addWidget(self.title_label)
        # The row mustn't size the card from its pictures, or the card could never get
        # narrow enough for _fit to hide any, nor short enough for them to shrink back.
        self.holder = QWidget()
        self.holder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.row = QHBoxLayout(self.holder)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(14)
        self.row.addStretch()
        self.body.addWidget(self.holder, 1)
        self.empty = label("", role="muted", wrap=True)
        self.body.addWidget(self.empty)
        self.setMinimumHeight(self._height_for(self.base_size.height()))

    def _text_height(self) -> int:
        """Room under each picture: two title lines and the caption."""
        title = QFont(theme.BODY_FAMILY)
        title.setPixelSize(13)
        caption = QFont(theme.BODY_FAMILY)
        caption.setPixelSize(12)
        return (
            8
            + 2 * QFontMetrics(title).lineSpacing()
            + self.caption_lines * QFontMetrics(caption).lineSpacing()
            + 6
        )

    def _height_for(self, art_height: int) -> int:
        margins = self.body.contentsMargins()
        return (
            margins.top()
            + margins.bottom()
            + self.title_label.sizeHint().height()
            + self.body.spacing()
            + art_height
            + self._text_height()
        )

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(super().sizeHint().width(), self.minimumHeight())

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        # Otherwise the page asks the wrapped titles how tall the shelf wants to be at
        # its current art size, and the art could grow but never shrink back.
        return False

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
        self._fit()  # first, so showing the row doesn't show (and load) items that don't fit
        self.holder.setVisible(bool(entries))

    def _grow(self) -> None:
        """Size the pictures to the height the card has."""
        room = self.height() - self._height_for(0)
        base = self.base_size
        height = min(max(room, base.height()), int(base.height() * ART_GROWTH))
        height = max(base.height(), height // ART_STEP * ART_STEP)
        size = QSize(round(base.width() * height / base.height()), height)
        if size != self.art_size:
            self.art_size = size
            for item in self.items:
                item.set_art_size(size)

    def _fit(self) -> None:
        margins = self.body.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        step = self.art_size.width() + self.row.spacing()
        fits = max(1, (room + self.row.spacing()) // step) if room > 0 else len(self.items)
        for i, item in enumerate(self.items):
            item.setVisible(i < fits)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._grow()
        self._fit()


class MonthCard(Card):
    """This month so far: plays, hours, shows finished, and the top artist and show.

    A live preview of the month's recap, with a link to it.
    """

    open_recap = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.body.setSpacing(8)
        self.title = section("This month so far")
        self.body.addWidget(self.title)
        numbers = QHBoxLayout()
        numbers.setSpacing(24)
        self.numbers: dict[str, tuple] = {}
        for key, color in (
            ("plays", theme.CORAL),
            ("hours", theme.CORAL),
            ("shows", theme.LILAC),
            ("books", theme.AMBER),
            ("films", theme.TEAL),
        ):
            box = QVBoxLayout()
            box.setSpacing(0)
            value = label("-")
            value.setFont(theme.number_font(28))
            value.setStyleSheet(f"color: {color};")
            caption = label("", role="muted")
            box.addWidget(value)
            box.addWidget(caption)
            numbers.addLayout(box)
            self.numbers[key] = (value, caption)
        numbers.addStretch()
        self.body.addLayout(numbers)
        self.top_artist = label("", wrap=True)
        self.top_show = label("", wrap=True)
        self.body.addWidget(self.top_artist)
        self.body.addWidget(self.top_show)
        self.body.addStretch()
        for key in ("books", "films"):
            for widget in self.numbers[key]:
                widget.setVisible(False)
        row = QHBoxLayout()
        row.addStretch()
        self.link = QPushButton(f"See the month in {RECAP_NAME}")
        self.link.setProperty("role", "link")
        self.link.clicked.connect(self.open_recap)
        row.addWidget(self.link)
        self.body.addLayout(row)

    def set_glance(self, glance: activity.Glance) -> None:
        self.title.setText(f"This month so far · {glance.period.label}")
        values = {
            "plays": (f"{glance.plays:,}", "plays" if glance.plays != 1 else "play"),
            "shows": (
                f"{glance.shows_finished}",
                "show finished" if glance.shows_finished == 1 else "shows finished",
            ),
        }
        hours = glance.hours.hours
        if hours is None:
            values["hours"] = ("-", "hours: track lengths not fetched yet")
        else:
            values["hours"] = (
                f"~{hours:,.0f}" if hours >= 1 else "<1",
                "hours of music",
            )
        for key, count, one, many in (
            ("books", glance.books, "book read", "books read"),
            ("films", glance.films, "film rated", "films rated"),
        ):
            if count is not None:
                values[key] = (f"{count}", one if count == 1 else many)
        for key, (value, caption) in values.items():
            self.numbers[key][0].setText(value)
            self.numbers[key][1].setText(caption)
        for key in ("books", "films"):
            shown = key in values
            self.numbers[key][0].setVisible(shown)
            self.numbers[key][1].setVisible(shown)
        self.numbers["hours"][0].setToolTip(
            "Estimated from Last.fm track lengths. Tracks with no known length count as the "
            "typical one."
        )
        if glance.top_artist:
            name, plays = glance.top_artist
            self.top_artist.setText(
                f"Top artist: {name}, {plays:,} {'play' if plays == 1 else 'plays'}"
            )
        else:
            self.top_artist.setText("No plays yet this month.")
        if glance.top_show:
            show = glance.top_show
            self.top_show.setText(f"Top show: {show.title}, you gave it {show.my_score:g}")
        else:
            self.top_show.setText("No scored shows finished yet this month.")


class GettingStarted(Card):
    """What a new profile sees instead of a page of empty cards."""

    open_settings = Signal()
    sync_all = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.body.setContentsMargins(40, 36, 40, 36)
        self.body.setSpacing(18)
        self.body.addWidget(heading("Let's fill this page", 30))
        self.body.addWidget(
            label(
                "Taste Engine reads your anime list and your listening history, then shows how "
                "your taste compares with everyone else's and what to try next. Three steps:",
                role="muted",
                wrap=True,
            )
        )
        steps = QHBoxLayout()
        steps.setSpacing(24)
        texts = (
            (
                "1. Add your accounts",
                "On Settings, enter your MyAnimeList and Last.fm usernames and API keys. Each "
                "key has a Test connection button. Links to get keys are on the page.",
                "Open Settings",
                self.open_settings,
            ),
            (
                "2. Sync",
                "Sync everything pulls your list and plays. The first time it also fetches what "
                "suggestions need, which takes a few minutes for a big list.",
                "Sync everything",
                self.sync_all,
            ),
            (
                "3. Look around",
                "This page shows what you finished and played lately and how you score against "
                "the crowd. For You has suggestions, Reports has charts, and "
                f"{RECAP_NAME} makes a recap of every month and year.",
                None,
                None,
            ),
        )
        self.buttons: dict[str, QPushButton] = {}
        for title, text, button_text, signal in texts:
            step = Card()
            step.setProperty("role", "step")
            step.body.setContentsMargins(22, 20, 22, 20)
            step.body.addWidget(heading(title, 20))
            step.body.addWidget(label(text, wrap=True))
            step.body.addStretch()
            if button_text:
                button = QPushButton(button_text)
                if signal is self.sync_all:
                    button.setProperty("role", "primary")
                button.clicked.connect(signal)
                step.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
                self.buttons[button_text] = button
            steps.addWidget(step, 1)
        self.body.addLayout(steps, 1)


TRAIT_COLORS = {
    "anime": theme.LILAC,
    "music": theme.CORAL,
    "books": theme.AMBER,
    "screen": theme.TEAL,
}


class TasteCard(Card):
    """Your taste: a one-line description from all the data, then the traits behind it,
    each with its number (taste.persona)."""

    def __init__(self) -> None:
        super().__init__()
        self.body.setContentsMargins(20, 12, 20, 12)
        self.body.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(section("Your taste"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.headline = label("", wrap=True)
        self.headline.setFont(theme.title_font(22))
        top.addWidget(self.headline, 1)
        self.based_on = label("", role="muted")
        top.addWidget(self.based_on, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(top)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        self.body.addLayout(self.grid)
        self.body.addStretch(1)
        self.traits: list[tuple[str, str]] = []  # (title, evidence) as shown, for tests
        self.chips: list[QFrame] = []
        self.columns = 3

    def set_columns(self, columns: int) -> None:
        """Re-flow the traits: one row of six on a big screen, rows of three otherwise."""
        if columns == self.columns:
            return
        self.columns = columns
        for chip in self.chips:
            self.grid.removeWidget(chip)
        self._place()

    def _place(self) -> None:
        for column in range(6):
            self.grid.setColumnStretch(column, 1 if column < self.columns else 0)
        for i, chip in enumerate(self.chips):
            self.grid.addWidget(chip, i // self.columns, i % self.columns)

    def set_persona(self, persona, columns: int) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.traits = []
        self.chips = []
        self.columns = columns
        if persona is None:
            return
        self.headline.setText(persona.headline)
        self.based_on.setText(persona.based_on)
        for trait in persona.traits:
            chip = QFrame()
            chip.setProperty("role", "trait")
            box = QVBoxLayout(chip)
            box.setContentsMargins(12, 6, 12, 6)
            box.setSpacing(1)
            title = label(trait.title)
            title.setStyleSheet(
                f"color: {TRAIT_COLORS.get(trait.kind, theme.LILAC)}; font-weight: 700;"
            )
            box.addWidget(title)
            box.addWidget(label(trait.evidence, role="caption", wrap=True))
            self.chips.append(chip)
            self.traits.append((trait.title, trait.evidence))
        self._place()


class DashboardPage(QWidget):
    sync_started = Signal()
    sync_finished = Signal(dict)
    recs_updated = Signal()
    status_changed = Signal(str, str, str)  # short line, dot state, details (the sidebar)
    open_page = Signal(str)  # a page name, from a card's link

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.worker: SyncWorker | None = None
        self.outcome: bool | None = None  # how the last sync in this session went
        self.is_blocked = lambda: False  # the main window points this at "are recs refreshing?"
        self.wide: bool | None = None
        self.page_width = 0
        self.has_data = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self.scroll)
        body = QWidget()
        self.scroll.setWidget(body)
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

        self.banner = Banner()
        layout.addWidget(self.banner)
        self.banner.setVisible(False)

        self.welcome = GettingStarted()
        self.welcome.open_settings.connect(lambda: self.open_page.emit("Settings"))
        self.welcome.sync_all.connect(lambda: self.start_sync("all"))
        layout.addWidget(self.welcome, 1)

        self.critic = Card()
        self.critic.body.addWidget(section("How critical you are"))
        row = QHBoxLayout()
        row.setSpacing(14)
        self.critic_value = label("")
        self.critic_value.setFont(theme.number_font(44))
        self.critic_value.setStyleSheet(f"color: {theme.LILAC};")
        row.addWidget(self.critic_value, 0, Qt.AlignmentFlag.AlignVCenter)
        self.critic_text = label("", wrap=True)
        row.addWidget(self.critic_text, 1, Qt.AlignmentFlag.AlignVCenter)
        self.critic.body.addLayout(row)
        self.counts = label("", role="muted", wrap=True)
        self.critic.body.addWidget(self.counts)
        self.critic.body.addStretch()

        self.scores = Card()
        self.scores.body.addWidget(section("Your scores, 1 to 10"))
        self.score_chart = BarChart(theme.LILAC, empty_text="No scores yet", value_labels=True)
        self.score_chart.setMinimumHeight(80)
        self.scores.body.addWidget(self.score_chart, 1)

        self.month = MonthCard()
        self.month.open_recap.connect(lambda: self.open_page.emit(RECAP_NAME))

        self.recent = Shelf(ctx, "Recently finished", POSTER, theme.LILAC)
        self.repeat = Shelf(
            ctx, f"On repeat, last {overview.ON_REPEAT_DAYS} days", COVER, theme.CORAL, 2
        )
        self.weeks = Card()
        self.weeks.body.addWidget(section("Plays per week"))
        self.week_chart = BarChart(theme.CORAL, scale=True, min_bar=WEEKS_MIN_BAR)
        self.week_chart.setMinimumHeight(110)
        self.weeks.body.addWidget(self.week_chart, 1)
        self.genres = GenreCard(fit=True)
        # Books, films, and TV join the grid once they have data (a row of two when wide).
        self.books = Shelf(ctx, "Recently read", POSTER, theme.AMBER, 2)
        self.films = Shelf(ctx, "Films you rated lately", POSTER, theme.TEAL, 2)
        self.tv = Shelf(ctx, "TV you rated lately", POSTER, theme.TEAL, 2)
        self.extra = [self.books, self.films, self.tv]
        self.extra_shown: list[Shelf] = []
        # Across the top once there's enough to say something (taste.persona).
        self.taste = TasteCard()
        self.taste_shown = False

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)
        self.cards = [
            self.critic,
            self.scores,
            self.month,
            self.recent,
            self.repeat,
            self.weeks,
            self.genres,
        ]
        layout.addLayout(self.grid, 1)
        self._arrange(wide=False)
        self.scroll.viewport_resized.connect(self._on_width)

    # -- layout -------------------------------------------------------------

    def _arrange(self, wide: bool, force: bool = False) -> None:
        """Place the cards. Wide: three across with the genres down the right side.
        Narrow: two across. Rows share the height: picture rows get more of it. Shelves
        for books, films, and TV go below, two to a row when wide."""
        if wide == self.wide and not force:
            return
        self.wide = wide
        for card in [self.taste, *self.cards, *self.extra]:
            self.grid.removeWidget(card)
        if wide:
            places = [
                (self.critic, 0, 0, 1, 4),
                (self.scores, 0, 4, 1, 4),
                (self.month, 0, 8, 1, 4),
                (self.recent, 1, 0, 1, 8),
                (self.genres, 1, 8, 2, 4),
                (self.repeat, 2, 0, 1, 5),
                (self.weeks, 2, 5, 1, 3),
            ]
            stretches = (2, 3, 3, 0)
        else:
            places = [
                (self.critic, 0, 0, 1, 6),
                (self.scores, 0, 6, 1, 6),
                (self.month, 1, 0, 1, 6),
                (self.weeks, 1, 6, 1, 6),
                (self.recent, 2, 0, 1, 12),
                (self.repeat, 3, 0, 1, 6),
                (self.genres, 3, 6, 1, 6),
            ]
            stretches = (2, 2, 3, 3)
        stretches = list(stretches)
        if self.taste_shown:
            places = [(card, row + 1, *rest) for card, row, *rest in places]
            places.insert(0, (self.taste, 0, 0, 1, COLUMNS))
            stretches.insert(0, 2)
        first = len(stretches) if stretches[-1] else len(stretches) - 1
        # Wide: books, films, and TV side by side in one row, so a full screen fits
        # everything without scrolling. Narrow: one to a row.
        per_row = max(len(self.extra_shown), 1) if wide else 1
        for i, shelf in enumerate(self.extra_shown):
            row = first + i // per_row
            if i % per_row == 0:
                stretches[row:row] = [3]
            span = COLUMNS // per_row
            places.append((shelf, row, (i % per_row) * span, 1, span))
        for card, row, column, rows, columns in places:
            self.grid.addWidget(card, row, column, rows, columns)
        for row in range(self.grid.rowCount()):
            self.grid.setRowStretch(row, stretches[row] if row < len(stretches) else 0)

    def _on_width(self, width: int) -> None:
        self.page_width = width
        self._arrange(width >= WIDE)
        self.taste.set_columns(self._trait_columns())

    def _trait_columns(self) -> int:
        """Traits in one row of six on a big screen, else rows of three."""
        return 6 if self.page_width >= TRAITS_IN_ONE_ROW else 3

    def show_cards(self, has_data: bool) -> None:
        self.has_data = has_data
        self.welcome.setVisible(not has_data)
        self.taste.setVisible(has_data and self.taste_shown)
        for card in self.cards:
            card.setVisible(has_data)
        for shelf in self.extra:
            shelf.setVisible(has_data and shelf in self.extra_shown)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.outcome = None
        self.strip.clear()
        self.banner.setVisible(False)
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
            finished = overview.recently_finished(conn, limit=SHELF_MAX)
            albums = overview.on_repeat(conn, now, limit=SHELF_MAX)
            generous, harsh = overview.genre_lean(conn)
            scope_note = settings.lastfm_scope_note(conn)
            glance = activity.glance(conn, activity.current(conn, "month", now))
            profile = persona.load(conn)
            books = activity.recently_read(conn, SHELF_MAX)
            films = activity.recently_rated(conn, "movie", SHELF_MAX)
            shows = activity.recently_rated(conn, "tv", SHELF_MAX)
            tmdb_synced = conn.execute(
                "SELECT ended_at FROM sync_runs WHERE source = 'tmdb' AND status = 'success' "
                "AND mode <> 'enrich' ORDER BY sync_run_id DESC LIMIT 1"
            ).fetchone()
            books_synced = conn.execute(
                "SELECT ended_at FROM sync_runs WHERE source = 'hardcover' "
                "AND status = 'success' ORDER BY sync_run_id DESC LIMIT 1"
            ).fetchone()
            weeks, partial = charts.plays_by_period(conn, activity.today(conn, now))
        finally:
            conn.close()

        self.title.setText(cfg["display_name"] or self.profile_id)
        shown = [
            shelf
            for shelf, items in ((self.books, books), (self.films, films), (self.tv, shows))
            if items
        ]
        if shown != self.extra_shown or (profile is not None) != self.taste_shown:
            self.extra_shown = shown
            self.taste_shown = profile is not None
            self._arrange(bool(self.wide), force=True)
        self.taste.set_persona(profile, self._trait_columns())
        self.show_cards(bool(entries or plays or books or films or shows))
        self.books.set_items(
            [(b.cover_url, b.title, book_caption(b), f"{b.title}\n{b.author}") for b in books],
            "",
        )
        self.films.set_items([(f.poster_url, f.title, rated_caption(f), "") for f in films], "")
        self.tv.set_items([(f.poster_url, f.title, rated_caption(f), "") for f in shows], "")
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
                f"{summary['shows_scored']:,} scored shows. You go lower than MAL on "
                f"{summary['share_scored_below']:.0%} of them and higher on "
                f"{summary['share_scored_above']:.0%}."
            )
            self.critic_text.setToolTip(
                "Lower and higher compare your score with MAL's average rounded to a whole "
                "number, the closest score you could give: a 7 for a show MAL rates 7.2 "
                f"matches it. You matched on {summary['share_scored_same']:.0%}."
            )
        else:
            self.critic_value.setVisible(False)  # a lone dash at this size reads as a bar
            self.critic_text.setText("Sync MyAnimeList to see how critical you are.")
            self.critic_text.setToolTip("")
        if any(distribution):
            self.score_chart.set_data(
                distribution,
                [str(s) for s in range(1, 11)],
                tips=[
                    f"{s}: {n} {'show' if n == 1 else 'shows'}"
                    for s, n in enumerate(distribution, 1)
                ],
            )
        else:
            self.score_chart.set_data([], [])  # draws "No scores yet"
        self.month.set_glance(glance)
        series = weeks["week"]
        names = [charts.period_label("week", key) for key, _ in series]
        self.week_chart.set_data(
            [n for _, n in series],
            [axis for axis, _ in names],
            partial_last=partial["week"],
            tips=[
                f"{hover}: {n:,} {'play' if n == 1 else 'plays'}"
                for (_, hover), (_, n) in zip(names, series, strict=True)
            ],
        )

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
        self.repeat.setToolTip(
            plain_tip(SCOPE_TIP.format(days=overview.ON_REPEAT_DAYS, note=scope_note))
        )
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
            if tmdb_synced:
                parts.append(
                    f"TMDB synced {local_time.describe(tmdb_synced[0], cfg['timezone'], now)}."
                )
            if books_synced:
                when = local_time.describe(books_synced[0], cfg["timezone"], now)
                parts.append(f"Hardcover synced {when}.")
            text = " ".join(parts)
            if interrupted:
                text += " A Last.fm sync was interrupted; the next one resumes it."
            if self.outcome is False:
                text = "The last sync had problems; the log says what happened. " + text
            self.strip.text.setText(text)
            latest = max(filter(None, synced.values()), default=None)
            if self.outcome is False:
                short, state = "Last sync had problems", "error"
            elif latest:
                short = f"Synced {local_time.describe(latest, cfg['timezone'], now)}"
                state = "ok"
            else:
                short, state = "Not synced yet", "idle"
            self.status_changed.emit(short, state, text)

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
        self.status_changed.emit("Syncing…", "running", "")

    def _on_done(self, results: dict) -> None:
        self.worker.wait()
        for button in self.sync_buttons.values():
            button.setEnabled(not self.is_blocked())
        self.outcome = bool(results) and all(results.values())
        self.strip.finish(self.outcome)
        self.refresh()
        self.sync_finished.emit(results)


class Banner(QFrame):
    """A one-line notice with a button, like "Your October recap is ready"."""

    opened = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        self.text = label("", wrap=True)
        row.addWidget(self.text, 1)
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("role", "primary")
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.open_button)
        close = QPushButton("Dismiss")
        close.setProperty("role", "link")
        close.clicked.connect(self.hide_banner)
        row.addWidget(close)

    def show_text(self, text: str) -> None:
        self.text.setText(text)
        if not motion.is_open(self):
            motion.reveal(self, True)

    def hide_banner(self) -> None:
        if motion.is_open(self):
            motion.reveal(self, False)

    def _open(self) -> None:
        self.hide_banner()
        self.opened.emit()


def book_caption(book: activity.Book) -> str:
    """Like "You 4.5★ · Hardcover 3.84"."""
    mine = f"You {book.my_rating:g}★" if book.my_rating else "Not rated"
    return f"{mine} · Hardcover {book.average:.2f}" if book.average else mine


def rated_caption(item: activity.Rated) -> str:
    """Like "You 8.5 · TMDB 7.2"."""
    mine = f"You {item.my_rating:g}"
    return f"{mine} · TMDB {item.average:.1f}" if item.average else mine


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
