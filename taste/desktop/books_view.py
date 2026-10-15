"""The Books report page: my Hardcover reading for all time or one year.

Laid out like the approved mockup (2026-10-15), after Hardcover's own stats page:
six big numbers, the covers I read beside books per year, genres, moods, kind and
format, my stars and book lengths, then the authors I read most. The numbers come
from taste.bookstats (Qt-free); this file only draws them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from taste import bookstats, charts
from taste.bookstats import BookStats, Cover, Share
from taste.desktop import motion, theme
from taste.desktop.art import ArtTile
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BarChart,
    Card,
    LeanBar,
    Segmented,
    Sparkline,
    StatTile,
    chart_card,
    elided,
    label,
    plain_tip,
    tile_row,
)

YEARS_ON_SWITCH = 5  # the newest years get a button; older ones are in a list beside it
COVER = QSize(84, 126)
COVER_GAP = 10
WALL_GROW = 1.5  # how much bigger than its base size a wall's picture may get
AUTHOR_COVER = QSize(44, 66)
AUTHOR_COLUMNS = 3
STAR_LABELS = ["½", "1", "", "2", "", "3", "", "4", "", "5"]
FORMAT_WORDS = {"Physical": "on paper", "Ebook": "as ebooks", "Audio": "as audiobooks"}
SHADES = (theme.AMBER, "#C99A4A", "#8F6E37", theme.LINE)  # the first share is brightest


def times_word(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


@dataclass(frozen=True)
class WallItem:
    """One picture on a wall or a person card: what it is, and what hovering it says."""

    title: str
    url: str | None
    tip: str
    badge: str = ""  # a small label in the corner ("2×", "8.5"); none when empty


def book_tip(cover: Cover) -> str:
    lines = [cover.title]
    if cover.author:
        lines.append(cover.author)
    if cover.my_rating:
        lines.append(f"You gave it {cover.my_rating:g}★")
    if cover.times > 1:
        lines.append(f"Read {times_word(cover.times)}")
    return "\n".join(lines)


def book_item(cover: Cover) -> WallItem:
    return WallItem(
        cover.title, cover.url, book_tip(cover), f"{cover.times}×" if cover.times > 1 else ""
    )


class CoverWall(QWidget):
    """As many pictures as fit, newest first, spread to fill each row. A wider or taller
    window shows more. Books mark rereads ("2×"); films and TV show my rating."""

    def __init__(
        self, ctx: AppContext, size: QSize = COVER, accent: str = theme.AMBER, badge: str = "books"
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.size_ = QSize(size)
        self.accent = accent
        self.badge_accent = badge
        self.items: list[WallItem] = []
        self.tiles: list[ArtTile] = []
        self.shown = 0
        self.setMinimumHeight(size.height())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_covers(self, covers: Sequence[Cover]) -> None:
        self.set_items([book_item(c) for c in covers])

    def set_items(self, items: Sequence[WallItem]) -> None:
        self.items = list(items)
        for tile in self.tiles:
            tile.setVisible(False)
        self._place()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._place()

    def _cell(self) -> QSize:
        """The picture size: the base size, grown (up to WALL_GROW times) so that whole
        rows fill the wall's height instead of leaving a strip of empty card under them."""
        base = self.size_
        rows = max(1, (self.height() + COVER_GAP) // (base.height() + COVER_GAP))
        room = (self.height() - (rows - 1) * COVER_GAP) / rows
        scale = min(max(room / base.height(), 1.0), WALL_GROW)
        return QSize(round(base.width() * scale), round(base.height() * scale))

    def _columns(self, cell: QSize) -> int:
        return max(1, (self.width() + COVER_GAP) // (cell.width() + COVER_GAP))

    def fits(self) -> int:
        cell = self._cell()
        rows = max(1, (self.height() + COVER_GAP) // (cell.height() + COVER_GAP))
        return self._columns(cell) * rows

    def _tile(self, i: int) -> ArtTile:
        while len(self.tiles) <= i:
            tile = ArtTile(self.ctx.art, self.size_, radius=6, accent=self.accent, parent=self)
            tile.badge = label("", role="badge")
            tile.badge.setProperty("accent", self.badge_accent)
            tile.badge.setParent(tile)
            self.tiles.append(tile)
        return self.tiles[i]

    def _place(self) -> None:
        cell = self._cell()
        columns = self._columns(cell)
        w, h = cell.width(), cell.height()
        self.shown = min(len(self.items), self.fits())
        spare = self.width() - columns * w
        gap = spare / (columns - 1) if columns > 1 else 0
        for i, item in enumerate(self.items[: self.shown]):
            tile = self._tile(i)
            tile.set_size(cell)
            if tile.url != item.url or tile.toolTip() != plain_tip(item.tip):
                tile.set_art(item.url, item.title)
                tile.setToolTip(plain_tip(item.tip))
            row, column = divmod(i, columns)
            tile.move(round(column * (w + gap)), row * (h + COVER_GAP))
            tile.badge.setText(item.badge)
            tile.badge.adjustSize()
            tile.badge.move(4, h - tile.badge.height() - 4)
            tile.badge.setVisible(bool(item.badge))
            tile.setVisible(True)
        for tile in self.tiles[self.shown :]:
            tile.setVisible(False)


class SpanBar(QWidget):
    """All time, the newest years as buttons, and older years in a list beside them."""

    changed = Signal(str)  # "all" or a year

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.years: list[str] = []
        self.switch: Segmented | None = None
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(10)
        self.older = QComboBox()
        self.older.setToolTip("Earlier years")
        self.older.activated.connect(lambda i: self.changed.emit(self.older.itemData(i) or "all"))
        self.row.addWidget(self.older)
        self.older.setVisible(False)

    def set_years(self, years: list[str]) -> None:
        if years == self.years and self.switch is not None:
            return
        self.years = list(years)
        if self.switch is not None:
            self.row.removeWidget(self.switch)
            self.switch.deleteLater()
        options = [("all", "All time"), *[(y, y) for y in years[:YEARS_ON_SWITCH]]]
        self.switch = Segmented(options, accent=self.accent)
        self.switch.changed.connect(self.changed)
        self.row.insertWidget(0, self.switch)
        self.older.clear()
        self.older.addItem("Earlier…", None)
        for year in years[YEARS_ON_SWITCH:]:
            self.older.addItem(year, year)
        self.older.setVisible(len(years) > YEARS_ON_SWITCH)

    def show_span(self, span: str) -> None:
        if self.switch is None:
            return
        if span in self.switch.buttons:
            self.switch.set_value(span)
            self.older.setCurrentIndex(0)
            return
        # An older year: no button is lit (the group has to let go of the lit one first).
        self.switch.group.setExclusive(False)
        for button in self.switch.buttons.values():
            button.setChecked(False)
        self.switch.group.setExclusive(True)
        self.switch.pill.hide()
        self.older.setCurrentIndex(max(self.older.findData(span), 0))


class ShareList(QWidget):
    """Name, a bar from zero, and its share: genres and moods."""

    NAME_W = 120

    def __init__(self, empty_text: str, color: str = theme.AMBER) -> None:
        super().__init__()
        self.empty_text = empty_text
        self.color = color
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(5)
        self.grid.setColumnStretch(1, 1)
        self._rows: list[tuple[str, str]] = []

    def rows(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def set_shares(self, shares: Sequence[Share]) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        if not shares:
            self.grid.addWidget(label(self.empty_text, role="muted"), 0, 0, 1, 3)
            return
        top = max(s.share for s in shares) or 1.0
        for line, share in enumerate(shares):
            name = label()
            name.setFixedWidth(self.NAME_W)
            text = share.name[:1].upper() + share.name[1:]
            name.setText(elided(text, name.font(), self.NAME_W))
            name.setToolTip(
                plain_tip(f"{text}: {share.books:,} {'book' if share.books == 1 else 'books'}")
            )
            bar = LeanBar(share.share, 0.0, top, self.color)
            number = label(f"{share.share:.0%}")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            number.setFixedWidth(44)
            for column, widget in enumerate((name, bar, number)):
                self.grid.addWidget(widget, line, column)
            self._rows.append((text, f"{share.share:.0%}"))
        self.grid.setRowStretch(len(shares), 1)


class SplitBar(QWidget):
    """One bar split into shares (fiction against nonfiction), brightest first."""

    def __init__(self) -> None:
        super().__init__()
        self.shares: list[Share] = []
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_shares(self, shares: Sequence[Share]) -> None:
        # "Not set" always goes last and grey, whatever its size.
        self.shares = sorted(shares, key=lambda s: s.name == "Not set")
        self.setToolTip(
            plain_tip("\n".join(f"{s.name}: {s.books:,} ({s.share:.0%})" for s in self.shares))
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.RAISED))
        painter.drawRoundedRect(QRectF(self.rect()), 5, 5)
        x = 0.0
        painter.setClipRect(self.rect())
        for i, share in enumerate(self.shares):
            width = share.share * self.width()
            color = theme.LINE if share.name == "Not set" else SHADES[min(i, len(SHADES) - 2)]
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(x, 0, width, self.height()))
            x += width


def legend(shares: Sequence[Share]) -> str:
    ordered = sorted(shares, key=lambda s: s.name == "Not set")
    return " · ".join(f"{s.name} {s.share:.0%}" for s in ordered)


class PersonCard(Card):
    """Someone I keep coming back to (an author, a director): how many, my average, their
    best covers, and my ratings of their work in the order I got to it."""

    COVERS = 6

    def __init__(
        self, ctx: AppContext, accent: str = theme.AMBER, cover: QSize = AUTHOR_COVER
    ) -> None:
        super().__init__()
        self.setProperty("raised", True)
        self.name = label("")
        self.name.setFont(theme.heading_font(15))
        self.body.addWidget(self.name)
        self.caption = label("", role="muted")
        self.body.addWidget(self.caption)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.covers = [ArtTile(ctx.art, cover, radius=4, accent=accent) for _ in range(self.COVERS)]
        for tile in self.covers:
            row.addWidget(tile)
        row.addStretch()
        self.body.addLayout(row)
        self.line = Sparkline(accent)
        self.line.setFixedSize(220, 34)
        self.body.addWidget(self.line)
        self.body.addStretch(1)

    def set_person(
        self, name: str, caption: str, items: Sequence[WallItem], ratings: Sequence[float],
        line_tip: str,
    ) -> None:  # fmt: skip
        self.name.setText(name)
        self.caption.setText(caption)
        for i, tile in enumerate(self.covers):
            item = items[i] if i < len(items) else None
            if item is not None:
                tile.set_art(item.url, item.title)
                tile.setToolTip(plain_tip(item.tip))
            tile.setVisible(item is not None)
        self.line.set_values(ratings)
        self.line.setToolTip(line_tip)

    def set_author(self, author: bookstats.AuthorStat) -> None:
        books = f"{author.books:,} {'book' if author.books == 1 else 'books'}"
        stars = f" · {author.average:.1f}★ from you" if author.average is not None else ""
        self.set_person(
            author.name,
            books + stars,
            [book_item(c) for c in author.covers],
            author.ratings,
            "Your stars for their books, in the order you read them.",
        )


AuthorCard = PersonCard  # the Books page's name for it


class BooksView(QWidget):
    """Everything on the Books page under its title."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.span = "all"
        self.stats: BookStats | None = None
        self.years: list[str] = []

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)

        # The span: All time, the newest years, and older years in a list.
        top = QHBoxLayout()
        top.setSpacing(10)
        self.span_bar = SpanBar("books")
        self.span_bar.changed.connect(self.set_span)
        top.addWidget(self.span_bar)
        top.addStretch()
        self.span_note = label("", role="muted")
        top.addWidget(self.span_note)
        grid.addLayout(top, 0, 0, 1, 6)

        self.tiles = [StatTile() for _ in range(6)]
        grid.addLayout(tile_row(self.tiles), 1, 0, 1, 6)

        self.wall = CoverWall(ctx)
        self.wall_card = chart_card("Read", self.wall, " ")
        grid.addWidget(self.wall_card, 2, 0, 1, 4)
        self.per_year = BarChart(theme.AMBER, empty_text="No read dates yet", value_labels=True)
        self.year_card = chart_card("Each year", self.per_year, " ")
        grid.addWidget(self.year_card, 2, 4, 1, 2)

        self.genres = ShareList("No genres yet")
        self.genres_card = chart_card("Genres", self.genres, " ")
        self.genres_missing = label("", role="caption", wrap=True)
        self.genres_card.body.addWidget(self.genres_missing)
        grid.addWidget(self.genres_card, 3, 0, 1, 2)
        self.moods = ShareList("No moods yet")
        self.moods_card = chart_card("Moods", self.moods, " ")
        self.moods_missing = label("", role="caption", wrap=True)
        self.moods_card.body.addWidget(self.moods_missing)
        grid.addWidget(self.moods_card, 3, 2, 1, 2)
        kind_box = QWidget()
        kind_layout = QVBoxLayout(kind_box)
        kind_layout.setContentsMargins(0, 6, 0, 0)
        kind_layout.setSpacing(8)
        self.kind_bar = SplitBar()
        self.kind_legend = label("", role="muted", wrap=True)
        self.format_bar = SplitBar()
        self.format_legend = label("", role="muted", wrap=True)
        for widget in (self.kind_bar, self.kind_legend):
            kind_layout.addWidget(widget)
        kind_layout.addSpacing(10)
        for widget in (self.format_bar, self.format_legend):
            kind_layout.addWidget(widget)
        kind_layout.addStretch(1)
        self.kind_card = chart_card("Kind and format", kind_box, " ")
        grid.addWidget(self.kind_card, 3, 4, 1, 2)

        self.stars = BarChart(theme.AMBER, empty_text="No stars yet", value_labels=True)
        self.stars_card = chart_card("Your stars", self.stars, " ")
        grid.addWidget(self.stars_card, 4, 0, 1, 3)
        self.lengths = BarChart(theme.AMBER, empty_text="No page counts yet", value_labels=True)
        self.lengths_card = chart_card("Length", self.lengths, " ")
        grid.addWidget(self.lengths_card, 4, 3, 1, 3)

        authors_box = QWidget()
        self.author_grid = QGridLayout(authors_box)
        self.author_grid.setContentsMargins(0, 4, 0, 0)
        self.author_grid.setSpacing(10)
        self.author_cards = [AuthorCard(ctx) for _ in range(bookstats.AUTHORS_SHOWN)]
        for i, card in enumerate(self.author_cards):
            self.author_grid.addWidget(card, i // AUTHOR_COLUMNS, i % AUTHOR_COLUMNS)
        for column in range(AUTHOR_COLUMNS):
            self.author_grid.setColumnStretch(column, 1)
        self.authors_card = chart_card("Most read authors", authors_box, " ")
        grid.addWidget(self.authors_card, 5, 0, 1, 6)

        for row, weight in enumerate((0, 0, 4, 2, 2, 2)):
            grid.setRowStretch(row, weight)
        for column in range(6):
            grid.setColumnStretch(column, 1)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.span = "all"
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            years = bookstats.years(conn)
            if self.span != "all" and self.span not in years:
                self.span = "all"
            stats = bookstats.load(conn, self.span)
        finally:
            conn.close()
        self.years = years
        self.span_bar.set_years(years)
        self.show_stats(stats)

    def set_span(self, span: str) -> None:
        """All time ("all") or one year. Everything on the page follows."""
        if not self.profile_id:
            return
        self.span = span
        conn = self.ctx.connect(self.profile_id)
        try:
            stats = bookstats.load(conn, span)
        finally:
            conn.close()

        def switch() -> None:
            self.show_stats(stats)

        if self.isVisible():
            motion.crossfade(self, switch)
        else:
            switch()

    def show_stats(self, s: BookStats) -> None:
        self.stats = s
        self.span_bar.show_span(s.span)
        when = "in all" if s.span == "all" else f"in {s.span}"
        self.span_note.setText(
            f"{s.undated:,} read without a date count in All time only."
            if s.span == "all" and s.undated
            else ""
        )
        self._tiles(s)

        self.wall.set_covers(s.covers)
        self.wall_card.title.setText(f"Read {when}".replace("in all", "of all time"))
        self.wall_card.note.setText(
            f"{s.unique:,} {'book' if s.unique == 1 else 'books'}, newest first. "
            "Rereads are marked."
            if s.unique
            else "Nothing read in this span."
        )
        self.per_year.set_data(
            [n for _, n in s.per_year],
            [y for y, _ in s.per_year],
            unit="books",
            partial_last=bool(s.per_year) and s.per_year[-1][0] == str(self.ctx.now().year),
        )
        best = max(s.per_year, key=lambda kv: (kv[1], kv[0]), default=None)
        self.year_card.note.setText(
            f"Most in {best[0]}: {best[1]:,} books, by the day you finished them."
            if best and best[1]
            else "By the day you finished them."
        )

        self.genres.set_shares(s.genres)
        self.genres_card.note.setText(
            f"Mostly {s.genres[0].name.lower()}" if s.genres else "What readers tagged them"
        )
        self.genres_missing.setText(
            f"{s.genres_missing:,} without a genre on Hardcover." if s.genres_missing else ""
        )
        self.moods.set_shares(s.moods)
        if len(s.moods) > 1:
            mood_line = f"{s.moods[0].name.capitalize()}, then {s.moods[1].name.lower()}"
        else:
            mood_line = s.moods[0].name.capitalize() if s.moods else "How readers felt"
        self.moods_card.note.setText(mood_line)
        self.moods_missing.setText(
            f"{s.moods_missing:,} without a mood on Hardcover." if s.moods_missing else ""
        )
        self.kind_bar.set_shares(s.kinds)
        self.kind_legend.setText(legend(s.kinds))
        self.format_bar.set_shares(s.formats)
        self.format_legend.setText(legend(s.formats))
        kind = next((k.name.lower() for k in s.kinds if k.name != "Not set"), None)
        form = next((FORMAT_WORDS[f.name] for f in s.formats if f.name in FORMAT_WORDS), None)
        self.kind_card.note.setText(
            ", ".join(p for p in (f"Mostly {kind}" if kind else "", form or "") if p)
            or "Fiction or not, and paper, ebook, or audio"
        )

        tips = [f"{(i + 1) / 2:g}★: {n:,} books" for i, n in enumerate(s.stars)]
        self.stars.set_data(s.stars if any(s.stars) else [], STAR_LABELS, tips=tips)
        most = max(range(10), key=lambda i: (s.stars[i], i)) if any(s.stars) else None
        parts = [f"Most often {(most + 1) / 2:g} stars"] if most is not None else []
        if s.my_average is not None:
            parts.append(f"yours {s.my_average:.1f}")
        if s.hardcover_average is not None:
            parts.append(f"Hardcover {s.hardcover_average:.1f}")
        self.stars_card.note.setText(" · ".join(parts) or "Half a star to five")
        counts = [n for _, n in s.lengths]
        self.lengths.set_data(counts if any(counts) else [], [n for n, _ in s.lengths],
                              unit="books")  # fmt: skip
        common = max(s.lengths, key=lambda kv: kv[1]) if any(counts) else None
        self.lengths_card.note.setText(
            f"Most between {common[0].replace('-', ' and ')} pages · {s.average_pages:,} on average"
            if common and s.average_pages and "-" in common[0]
            else (f"{s.average_pages:,} pages on average" if s.average_pages else "Pages per book")
        )

        for i, card in enumerate(self.author_cards):
            author = s.top_authors[i] if i < len(s.top_authors) else None
            if author is not None:
                card.set_author(author)
            card.setVisible(author is not None)
        lead = s.top_authors[0] if s.top_authors else None
        self.authors_card.note.setText(
            f"{lead.name} leads with {lead.books:,} {'book' if lead.books == 1 else 'books'}"
            if lead
            else "Nobody yet"
        )

    def _tiles(self, s: BookStats) -> None:
        def tile(value: str, caption: str, detail: str = "") -> charts.Tile:
            return charts.Tile(value, caption, "books", detail=detail)

        unique = f"{s.unique:,} unique" if s.unique != s.reads else "no rereads"
        per_book = s.pages // s.pages_known if s.pages_known else 0
        if s.new_authors is not None:
            authors_detail = f"{s.new_authors:,} new this year"
        elif s.top_authors:
            authors_detail = f"most: {s.top_authors[0].name}"
        else:
            authors_detail = ""
        values = [
            tile(f"{s.reads:,}", "books read", unique),
            tile(f"{s.pages:,}", "pages read", f"about {per_book:,} a book" if per_book else ""),
            tile(
                f"{s.audio_hours:,.0f}",
                "hours listened",
                f"{s.audiobooks:,} audiobook{'s' if s.audiobooks != 1 else ''}"
                if s.audiobooks
                else "no audiobooks",
            ),
            tile(f"{s.authors:,}", "authors", authors_detail),
            tile(
                f"{s.my_average:.1f}★" if s.my_average is not None else "-",
                "your average",
                f"Hardcover {s.hardcover_average:.1f}" if s.hardcover_average is not None else "",
            ),
            tile(
                f"{s.rereads:,}",
                "rereads",
                f"most: {s.most_reread[0]} ×{s.most_reread[1]}" if s.most_reread else "",
            ),
        ]
        for widget, value in zip(self.tiles, values, strict=True):
            widget.set(value)
            # A long title would push the tile wider than its share of the row.
            widget.detail.setText(
                value.detail if len(value.detail) <= 30 else value.detail[:29] + "…"
            )
            widget.detail.setToolTip(plain_tip(value.detail))
