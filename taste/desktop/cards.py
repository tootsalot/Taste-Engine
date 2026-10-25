"""Reusable report cards, genre panels, ranked lists, and stat tiles."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QWidget,
)

from taste import charts, overview
from taste.desktop import theme
from taste.desktop.plots import LeanAxis, LeanBar, Sparkline
from taste.desktop.widgets import Card, ElidedLabel, card_title, elided, label, plain_tip


def lean_heading(kind: str, genres: list[overview.GenreLean]) -> str:
    """Headings that match the numbers: a harsh critic's "generous" end may still be below MAL."""
    values = [g.avg_diff for g in genres]
    if kind == "generous":
        return "Furthest above MAL" if all(v > 0 for v in values) else "Closest to MAL"
    return "Furthest below MAL" if all(v < 0 for v in values) else "Lowest against MAL"


class GenreCard(Card):
    """Genres against the MAL community: bars from a MAL line, with my usual marked.

    One column stacks the two lists (the Dashboard); two put them side by side.
    """

    NAME_W = 130
    MIN_ROWS = 3  # genres per list it always has room for, when it fits its height

    def __init__(
        self, title: str = "Genres vs the MAL crowd", columns: int = 1, fit: bool = False
    ) -> None:
        """`fit`: show as many genres as the card's height has room for (the Dashboard,
        where rows share the window height). Otherwise every genre given is shown."""
        super().__init__()
        self.fit = fit
        self._row_widgets: dict[str, list[list[QWidget]]] = {"generous": [], "harsh": []}
        self.body.setSpacing(6)
        self.body.addWidget(card_title(title))
        self.note = label("", role="muted", wrap=True)
        self.body.addWidget(self.note)
        self.note.setVisible(False)
        row = QHBoxLayout()
        row.setSpacing(32)
        self.grids: list[QGridLayout] = []
        for _ in range(columns):
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(3)
            grid.setColumnStretch(1, 1)
            row.addLayout(grid, 1)
            self.grids.append(grid)
        self.body.addLayout(row)
        self.empty = label("", role="muted", wrap=True)
        self.body.addWidget(self.empty)
        self.body.addStretch()
        self._rows: dict[str, list[tuple[str, str]]] = {"generous": [], "harsh": []}
        self._headings: list[str] = []
        self.scale = (0.0, 0.0)
        self.usual: float | None = None

    def rows(self, kind: str) -> list[tuple[str, str]]:
        """(genre, value text) for every genre given, for tests."""
        return list(self._rows[kind])

    def shown_rows(self, kind: str) -> list[tuple[str, str]]:
        """The rows on screen (fewer than `rows` when the card fits its height)."""
        shown = [not widgets[0].isHidden() for widgets in self._row_widgets[kind]]
        return [row for row, visible in zip(self._rows[kind], shown, strict=True) if visible]

    def _row_height(self) -> int:
        return max(self.fontMetrics().height() + 2, 16) + self.grids[0].verticalSpacing()

    def _overhead(self) -> int:
        """Height taken by everything but the genre rows."""
        margins = self.body.contentsMargins()
        height = margins.top() + margins.bottom() + self.body.itemAt(0).widget().sizeHint().height()
        if not self.note.isHidden():
            width = max(self.width() - margins.left() - margins.right(), 100)
            height += self.note.heightForWidth(width) + self.body.spacing()
        return height + self.body.spacing()

    def _fit_rows(self) -> None:
        """Show as many rows as fit, sharing them between the two lists."""
        if not self.fit:
            return
        groups = [k for k in ("generous", "harsh") if self._row_widgets[k]]
        room = (self.height() - self._overhead()) // self._row_height() - len(groups)
        room = max(room, self.MIN_ROWS * len(groups))
        sizes = {k: len(self._row_widgets[k]) for k in groups}
        counts = {k: min(n, room) for k, n in sizes.items()}
        if len(groups) == 2:
            first, second = groups
            # Half each, and a short list hands its spare rows to the other one.
            counts[first] = min(sizes[first], max(room // 2, room - sizes[second]))
            counts[second] = min(sizes[second], room - counts[first])
        for kind in groups:
            for i, widgets in enumerate(self._row_widgets[kind]):
                for widget in widgets:
                    widget.setVisible(i < counts[kind])

    def _base_height(self) -> int:
        groups = [k for k in ("generous", "harsh") if self._row_widgets[k]]
        rows = sum(min(len(self._row_widgets[k]), self.MIN_ROWS) for k in groups) + len(groups)
        return self._overhead() + max(rows, 2) * self._row_height()

    def sizeHint(self):  # noqa: N802 (Qt naming)
        hint = super().sizeHint()
        if self.fit:
            hint.setHeight(self.minimumHeight())
        return hint

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        # Fitting its height, the card mustn't ask its rows how tall to be.
        return not self.fit and super().hasHeightForWidth()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fit_rows()

    def headings(self) -> list[str]:
        return list(self._headings)

    def set_genres(
        self,
        generous: list[overview.GenreLean],
        harsh: list[overview.GenreLean],
        empty: str,
        usual: float | None = None,
    ) -> None:
        for grid in self.grids:
            while grid.count():
                widget = grid.takeAt(0).widget()
                if widget is not None:
                    widget.deleteLater()
        self._rows = {"generous": [], "harsh": []}
        self._row_widgets = {"generous": [], "harsh": []}
        self._headings = []
        self.usual = usual
        ends = [g.avg_diff for g in generous + harsh] + [0.0]
        if usual is not None:
            ends.append(usual)
        low, high = min(ends), max(ends)
        self.scale = (low, high)
        lines = [0] * len(self.grids)
        groups = [(k, g) for k, g in (("generous", generous), ("harsh", harsh)) if g]
        for index, (kind, genres) in enumerate(groups):
            column = min(index, len(self.grids) - 1)
            grid = self.grids[column]
            line = lines[column]
            title = lean_heading(kind, genres)
            self._headings.append(title)
            grid.addWidget(label(title, role="muted"), line, 0)
            if line == 0:  # the MAL and usual labels, once per column
                grid.addWidget(LeanAxis(low, high, usual), line, 1)
            line += 1
            for g in genres:
                value = f"{g.avg_diff:+.2f}"
                tip = (
                    f"{g.genre}: you average {g.my_avg_score:.2f}, the MAL community "
                    f"{g.community_avg_score:.2f}, across {g.shows_scored} scored shows."
                )
                name = label()
                name.setFixedWidth(self.NAME_W)
                name.setText(elided(g.genre, name.font(), self.NAME_W))
                bar = LeanBar(g.avg_diff, low, high, usual=usual)
                number = label(value)
                number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                number.setFixedWidth(48)
                for col, widget in enumerate((name, bar, number)):
                    widget.setToolTip(plain_tip(tip))
                    grid.addWidget(widget, line, col)
                self._rows[kind].append((g.genre, value))
                self._row_widgets[kind].append([name, bar, number])
                line += 1
            lines[column] = line
        if usual is not None:
            self.note.setText(
                f"Points above or below the MAL average. The dashed line is your usual, "
                f"{usual:+.2f}."
            )
        self.note.setVisible(usual is not None and bool(generous or harsh))
        self.empty.setText(empty)
        self.empty.setVisible(not (generous or harsh))
        if self.fit:
            # The layout mustn't size the card from every row, or a tall window's rows
            # would hold it tall after the window shrinks. It fits rows to its height.
            self.setMinimumHeight(self._base_height())
            self._fit_rows()


class RankList(QWidget):
    """A ranked list as bars from zero: name, bar, number. Used for top artists and tracks."""

    NAME_W = 150

    def __init__(self, color: str = theme.CORAL, empty_text: str = "Nothing yet") -> None:
        super().__init__()
        self.color = color
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(1, 1)
        self.empty_text = empty_text
        self._rows: list[tuple[str, str]] = []

    def rows(self) -> list[tuple[str, str]]:
        """(name, number) as shown, for tests. Names are the full ones."""
        return list(self._rows)

    def set_rows(self, rows: Sequence[tuple[str, int]], tips: Sequence[str] = ()) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        if not rows:
            self.grid.addWidget(label(self.empty_text, role="muted"), 0, 0, 1, 3)
            return
        top = max(value for _, value in rows) or 1
        for line, (name, value) in enumerate(rows):
            text = label()
            text.setFixedWidth(self.NAME_W)
            text.setText(elided(name, text.font(), self.NAME_W))
            text.setToolTip(plain_tip(tips[line] if line < len(tips) else name))
            bar = LeanBar(value, 0.0, float(top), self.color)
            number = label(f"{value:,}")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            number.setFixedWidth(44)
            for column, widget in enumerate((text, bar, number)):
                self.grid.addWidget(widget, line, column)
            self._rows.append((name, f"{value:,}"))


# Report pages: a row of big numbers, and charts on cards.


def tile_row(tiles: list[QWidget]) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(theme.GAP)
    for tile in tiles:
        row.addWidget(tile, 1)
    return row


class StatTile(Card):
    """A big number, what it counts, and a second line (sometimes with a tiny trend)."""

    def __init__(self) -> None:
        super().__init__()
        top = QHBoxLayout()
        self.value = label("-")
        self.value.setFont(theme.number_font(26))
        top.addWidget(self.value)
        top.addStretch()
        self.trend = Sparkline()
        top.addWidget(self.trend, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(top)
        # The two lines under the number shorten with "..." in a narrow window rather than
        # push six tiles (and the page) wider than it (2.5).
        self.caption = ElidedLabel(role="muted")
        self.body.addWidget(self.caption)
        self.detail = ElidedLabel(role="caption")
        self.body.addWidget(self.detail)
        self.trend.setVisible(False)

    def set(self, tile: charts.Tile) -> None:
        self.value.setText(tile.value)
        self.caption.setText(tile.caption)
        self.detail.setText(tile.detail)
        color = {"anime": theme.LILAC, "books": theme.AMBER, "screen": theme.TEAL}.get(
            tile.kind, theme.CORAL
        )
        self.value.setStyleSheet(f"color: {color};")
        self.trend.color = QColor(color)
        self.trend.set_values(tile.trend)
        self.trend.setToolTip(tile.trend_tip)


class HeaderRow(QWidget):
    """A card's title with a control (a switch) beside it, or under it when the card is
    too narrow for both on one line, so the control never makes the card, and the page,
    wider than the window (2.5)."""

    def __init__(self, title: QWidget, side: QWidget) -> None:
        super().__init__()
        self.title, self.side = title, side
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(8)
        # The title takes the free width: its size hint leaves out the theme's letter
        # spacing, so held to it the last letter or two were cut off.
        self.box.addWidget(title, 1)
        self.box.addWidget(side, 0, Qt.AlignmentFlag.AlignLeft)

    def needed(self) -> int:
        return self.title.sizeHint().width() + self.side.sizeHint().width() + 8

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        width = max(self.title.minimumSizeHint().width(), self.side.minimumSizeHint().width())
        return QSize(width, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        stacked = self.width() < self.needed()
        direction = (
            QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        )
        if self.box.direction() != direction:
            self.box.setDirection(direction)


def chart_card(title: str, chart: QWidget, note: str = "", side: QWidget | None = None) -> Card:
    """A small title (with `side`, such as a switch, at the right), a note, and a chart."""
    card = Card()
    card.title = card_title(title)
    if side is None:
        card.body.addWidget(card.title)
    else:
        card.header = HeaderRow(card.title, side)
        card.body.addWidget(card.header)
    card.note = label(note, role="muted", wrap=True)
    card.body.addWidget(card.note)
    card.note.setVisible(bool(note))  # after it has a parent, or it opens as its own window
    card.body.addWidget(chart, 1)
    return card
