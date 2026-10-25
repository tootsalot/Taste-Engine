"""Dashboard cards, sync notices, shelves, and their display helpers."""

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

from taste import (
    activity,
    airing,
    overview,
)
from taste.desktop import motion, theme
from taste.desktop.art import ArtTile
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    Card,
    ElidedLabel,
    card_title,
    elided_lines,
    heading,
    label,
    plain_tip,
    section,
)

PROGRESS = re.compile(r"(\d+) of (\d+)")  # "page 3 of 40", "details for candidates, 10 of 200"
ART_GROWTH = 2.2  # pictures grow with the room they get, up to this many times the base
ART_STEP = 8  # sizes move in steps, so resizing the window doesn't decode every pixel
RECAP_NAME = "Rev"  # the recap page's name in the sidebar
NOTICE_HEIGHT = 32  # compact buttons, padding, and border on the single sync line


def polish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class SyncStrip(QFrame):
    """One line: when everything last synced or, during a sync, the current step and a
    progress bar. The log stays folded until Show log, so a sync never moves the cards
    (the user's choice, 2026-10-16). A sync with problems turns the line red."""

    LOG_HEIGHT = 170
    PROGRESS_WIDTH = 260

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "strip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 0, 0)  # no box since 2.4: lined up with the title
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.addStrut(NOTICE_HEIGHT)
        row.setSpacing(10)
        self.row = row  # the Dashboard puts its recap notice on this line too
        self.dot = QFrame()
        self.dot.setObjectName("StatusDot")
        self.dot.setFixedSize(8, 8)
        self.dot.setProperty("state", "idle")
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        # One line, shortened with "..." when the notices beside it need the room (2.5:
        # wrapped, it stood four lines tall in an 1180 px window).
        self.text = ElidedLabel(role="muted", min_width=80)
        row.addWidget(self.text, 1)
        # On the status line rather than a row of its own, so it costs the cards nothing.
        self.progress = QProgressBar()
        self.progress.setFixedWidth(self.PROGRESS_WIDTH)
        self.progress.setVisible(False)
        row.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignVCenter)
        self.toggle = QPushButton("Show log")
        self.toggle.setProperty("compact", True)
        self.toggle.setProperty("role", "link")
        self.toggle.clicked.connect(lambda: self.show_log(self.log.isHidden()))
        self.toggle.setVisible(False)
        row.addWidget(self.toggle)
        layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(self.LOG_HEIGHT)
        self.log.setVisible(False)
        layout.addWidget(self.log)

    def set_state(self, state: str) -> None:
        self.dot.setProperty("state", state)
        polish(self.dot)
        self.text.setProperty("role", "error" if state == "error" else "muted")
        polish(self.text)

    def show_log(self, shown: bool) -> None:
        if shown != motion.is_open(self.log):
            motion.reveal(self.log, shown)
        self.toggle.setText("Hide log" if shown else "Show log")

    def start(self) -> None:
        self.log.clear()
        self.progress.setRange(0, 0)  # busy until a page count arrives
        self.progress.setVisible(True)
        self.toggle.setVisible(True)
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
        self.entries: list[tuple] = []
        self.body.setSpacing(10)
        self.title_label = card_title(title)
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
        if entries == self.entries and self.items:
            return  # unchanged: rebuilding 30 pictures a shelf took most of a refresh
        self.entries = list(entries)
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


AIRING_POSTER = QSize(44, 62)  # MAL's poster shape, big enough to recognize


class AiringRow(QWidget):
    """A poster, the title, when it airs (in lilac), and my next episode, all plain text,
    drawn with the shared background art loader."""

    def __init__(self, ctx: AppContext, show: airing.AiringShow) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.art = ArtTile(ctx.art, AIRING_POSTER, accent=theme.LILAC)
        row.addWidget(self.art, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addStretch()
        self.title = label(show.title, role="strong", wrap=True)
        self.when = label(show.when, role="airing-time")
        self.caption = label(show.progress, role="caption", wrap=True)
        for widget in (self.title, self.when, self.caption):
            text.addWidget(widget)
        text.addStretch()
        row.addLayout(text, 1)
        self.setToolTip(plain_tip(f"{show.title}\n{show.caption}"))
        self.art.set_art(show.poster_url, show.title)


class AiringCard(Card):
    """Weekly broadcast slots. The list scrolls inside the card when it has many shows."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.shows: list[airing.AiringShow] = []
        self.items: list[AiringRow] = []
        self.body.addWidget(card_title("Airing now"))
        self.setToolTip(
            plain_tip(
                "MAL's weekly broadcast time, in your profile's time zone. "
                "Episode numbers are next for you, not a claim about which episode airs."
            )
        )
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 4, 0)
        self.rows.setSpacing(10)
        self.rows.addStretch()
        self.scroll.setWidget(holder)
        # Both would fill with the window background, a dark box inside the card.
        self.scroll.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)
        self.body.addWidget(self.scroll, 1)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        # The scroll area's contents may be long. They must not make the whole
        # Dashboard taller; the other cards in this row decide its height.
        return QSize(super().sizeHint().width(), self.minimumSizeHint().height())

    def set_shows(self, shows: list[airing.AiringShow]) -> None:
        if shows == self.shows:
            return
        self.shows = shows
        for item in self.items:
            self.rows.removeWidget(item)
            item.hide()
            item.deleteLater()
        self.items = []
        for show in shows:
            item = AiringRow(self.ctx, show)
            self.rows.insertWidget(len(self.items), item)
            self.items.append(item)


class MonthCard(Card):
    """This month so far: plays, hours, shows finished, and the top artist and show.

    A live preview of the month's recap, with a link to it.
    """

    open_recap = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.body.setSpacing(8)
        self.title = card_title("This month so far")
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
        values = {"plays": (f"{glance.plays:,}", "plays" if glance.plays != 1 else "play")}
        hours = glance.hours.hours
        if hours is not None and glance.plays:
            values["hours"] = (f"~{hours:,.0f}" if hours >= 1 else "<1", "hours of music")
        elif glance.hours.known >= 1:
            values["hours"] = (f"{glance.hours.known:,.0f}+", "hours of music")
        # Zeros are left out ("0 books read" says nothing), plays always stay.
        for key, count, one, many in (
            ("shows", glance.shows_finished, "show finished", "shows finished"),
            ("books", glance.books, "book read", "books read"),
            ("films", glance.films, "film rated", "films rated"),
        ):
            if count:
                values[key] = (f"{count}", one if count == 1 else many)
        for key, (value, caption) in values.items():
            self.numbers[key][0].setText(value)
            self.numbers[key][1].setText(caption)
        for key in ("hours", "shows", "books", "films"):
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
    "critic": theme.TEXT,  # across media, so no one medium's color
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


class CrowdCard(Card):
    """You vs the crowd: a number per source, side by side (approved 2026-10-16). A
    source with nothing synced drops out and the rest share the row."""

    COLORS = {"anime": theme.LILAC, "screen": theme.TEAL, "books": theme.AMBER,
              "music": theme.CORAL}  # fmt: skip

    def __init__(self) -> None:
        super().__init__()
        self.body.addWidget(card_title("You vs the crowd"))
        self.row = QHBoxLayout()
        self.row.setSpacing(18)
        self.body.addLayout(self.row)
        self.empty = label(
            "Sync a source to see how your scores compare with its crowd.", role="muted", wrap=True
        )
        self.body.addWidget(self.empty)
        self.body.addStretch()
        self.cells: dict[str, QWidget] = {}
        self.numbers: list[overview.CrowdNumber] = []

    def set_numbers(self, numbers: list[overview.CrowdNumber]) -> None:
        self.numbers = numbers
        for cell in self.cells.values():
            self.row.removeWidget(cell)
            cell.deleteLater()
        self.cells = {}
        for number in numbers:
            cell = QWidget()
            box = QVBoxLayout(cell)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            value = label(number.value)
            value.setFont(theme.number_font(36))
            value.setStyleSheet(f"color: {self.COLORS[number.kind]};")
            box.addWidget(value)
            box.addWidget(label(number.caption, role="caption", wrap=True))
            cell.setToolTip(plain_tip(number.tip))
            self.row.addWidget(cell, 1)  # in its layout before it's ever shown
            self.cells[number.kind] = cell
        self.empty.setVisible(not numbers)

    def shown(self) -> dict[str, tuple[str, str]]:
        """{kind: (number, caption)} as on screen, for tests."""
        return {n.kind: (n.value, n.caption) for n in self.numbers}


class Banner(QFrame):
    """A one-line notice on the status line, like "Your October recap is ready. Open"."""

    opened = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(NOTICE_HEIGHT)
        self.setProperty("role", "banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 2, 6, 2)
        row.setSpacing(4)
        # Keeps its short sentence whole; the longer past-recaps offer shortens first.
        self.text = ElidedLabel(min_width=210)
        row.addWidget(self.text)
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("role", "link")
        self.open_button.setProperty("compact", True)
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.open_button)
        close = QPushButton("Dismiss")
        close.setProperty("compact", True)
        close.setProperty("role", "link")
        close.clicked.connect(self.hide_banner)
        row.addWidget(close)

    def show_text(self, text: str) -> None:
        self.text.setText(text)
        if self.isHidden():
            self.setVisible(True)  # already in the strip's layout
            motion.fade_in([self], stagger=False)

    def hide_banner(self) -> None:
        self.setVisible(False)

    def _open(self) -> None:
        self.hide_banner()
        self.opened.emit()


class PastRecaps(QFrame):
    """The offer to make recaps for past months, then their progress, then done: all on
    the status line, so it never moves the cards (2.3, mockup A approved 2026-10-19)."""

    accepted = Signal()
    declined = Signal()
    opened = Signal()
    PROGRESS_WIDTH = 140

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(NOTICE_HEIGHT)
        self.setProperty("role", "banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 2, 6, 2)
        row.setSpacing(8)
        self.text = ElidedLabel()  # gives up width before the page scrolls sideways
        row.addWidget(self.text)
        self.progress = QProgressBar()
        self.progress.setObjectName("RecapProgress")
        self.progress.setFixedWidth(self.PROGRESS_WIDTH)
        self.progress.setTextVisible(False)
        row.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignVCenter)
        self.make_button = QPushButton("Make them")
        self.make_button.setProperty("role", "primary")
        self.make_button.clicked.connect(self._accept)
        row.addWidget(self.make_button)
        self.later_button = QPushButton("Not now")
        self.later_button.clicked.connect(self._decline)
        row.addWidget(self.later_button)
        for button in (self.make_button, self.later_button):
            button.setProperty("compact", True)  # no taller than the status line
        self.open_button = QPushButton("Open Rev")
        self.open_button.setProperty("role", "link")
        self.open_button.setProperty("compact", True)
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.open_button)
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.setProperty("compact", True)
        self.dismiss_button.setProperty("role", "link")
        self.dismiss_button.clicked.connect(self.hide_notice)
        row.addWidget(self.dismiss_button)

    def _show(self, text: str, *visible: QWidget) -> None:
        self.text.setText(text)
        parts = (
            self.progress, self.make_button, self.later_button, self.open_button,
            self.dismiss_button,
        )  # fmt: skip
        for widget in parts:
            widget.setVisible(widget in visible)  # each is in this row's layout already
        if self.isHidden():
            self.setVisible(True)  # already in the strip's layout
            motion.fade_in([self], stagger=False)

    def offer(self, text: str) -> None:
        self._show(text, self.make_button, self.later_button)

    def making(self, done: int, total: int, name: str = "") -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        where = f" ({name})" if name else ""
        self._show(f"Making recaps: {done} of {total}{where}", self.progress)

    def finished(self, made: int) -> None:
        if made:
            text = f"Made {made} past {'recap' if made == 1 else 'recaps'}."
            self._show(text, self.open_button, self.dismiss_button)
        else:
            self._show("Your past months had nothing to recap.", self.dismiss_button)

    def hide_notice(self) -> None:
        self.setVisible(False)

    def _accept(self) -> None:
        self.accepted.emit()

    def _decline(self) -> None:
        self.hide_notice()
        self.declined.emit()

    def _open(self) -> None:
        self.hide_notice()
        self.opened.emit()


def book_caption(book: activity.Book) -> str:
    """Like "You 4.5★ · Hardcover 3.8" (one decimal, like TMDB's, so it fits one line)."""
    mine = f"You {book.my_rating:g}★" if book.my_rating else "Not rated"
    return f"{mine} · Hardcover {book.average:.1f}" if book.average else mine


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
