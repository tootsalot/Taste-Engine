"""Small reusable widgets: cards, labels, and the painted charts."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from taste import charts, overview
from taste.desktop import theme


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    # Never parse text as HTML: titles and artist names come from the internet
    # ("I <3 My Choppa" would otherwise turn into markup).
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def heading(text: str, size: int = 26) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)  # profile names are user input
    widget.setFont(theme.heading_font(size))
    return widget


def section(text: str) -> QLabel:
    """A small spaced-out card title. Shown in capitals; the text itself stays as written."""
    widget = label(text, role="section")
    font = QFont(theme.BODY_FAMILY)
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.Bold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    widget.setFont(font)
    return widget


def elided(text: str, font: QFont, width: int) -> str:
    """`text` on one line of `width` pixels, ending in an ellipsis if it had to be cut."""
    return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, width)


def elided_lines(text: str, font: QFont, width: int, lines: int) -> str:
    """`text` if it fits in `lines` word-wrapped lines of `width`; otherwise cut to fit,
    with the last line ending in an ellipsis and lines joined by newlines (so a
    word-wrapping label shows exactly these).
    """
    metrics = QFontMetrics(font)
    wrapped: list[str] = []
    for word in text.split():
        trial = f"{wrapped[-1]} {word}" if wrapped else word
        if wrapped and metrics.horizontalAdvance(trial) <= width:
            wrapped[-1] = trial
        else:
            wrapped.append(word)
    fits = all(metrics.horizontalAdvance(line) <= width for line in wrapped)
    if len(wrapped) <= lines and fits:
        return text
    if len(wrapped) > lines:
        wrapped = wrapped[: lines - 1] + [" ".join(wrapped[lines - 1 :])]
    return "\n".join(elided(line, font, width) for line in wrapped)


def small_font(widget: QWidget, size: int = 11) -> QFont:
    font = QFont(widget.font())
    font.setPixelSize(size)
    return font


def nice_ticks(top: float) -> list[int]:
    """Two or three round values up to `top`, for faint scale lines: 250 and 500 for 550."""
    if top <= 0:
        return []
    raw = top / 2
    magnitude = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    step = 1
    for multiple in (1, 2, 2.5, 5, 10):
        candidate = multiple * magnitude
        if candidate <= raw and candidate == int(candidate):
            step = int(candidate)
    return list(range(step, int(top) + 1, step))


class Segmented(QFrame):
    """A row of pill buttons where exactly one is on, like Simple | Advanced."""

    changed = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]], accent: str = "anime") -> None:
        """`options` are (key, text). `accent` "music" lights the chosen one coral."""
        super().__init__()
        self.setProperty("role", "segmented")
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            button = QPushButton(text)
            button.setProperty("role", "segment")
            button.setProperty("accent", accent)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(button)
            row.addWidget(button)
            self.buttons[key] = button

    def set_value(self, key: str) -> None:
        self.buttons[key].setChecked(True)

    def value(self) -> str:
        return next((k for k, b in self.buttons.items() if b.isChecked()), "")


class Card(QFrame):
    """A rounded surface panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(8)


class BarChart(QWidget):
    """Vertical bars with a few axis labels. Painted directly, no chart library.

    `value_labels` writes each bar's number above it (for short charts); `scale` draws
    faint lines at round numbers, labeled on the left (for long ones). `min_bar` keeps
    bars at least that wide by showing only the newest ones that fit, so a wider window
    shows more of a long history.
    """

    LABEL_H = 18  # axis labels under the bars
    VALUE_ROOM = 16  # numbers (or "so far") above the bars
    SCALE_ROOM = 34  # scale numbers left of the bars, at least; wider numbers get more
    GAP = 3

    def __init__(
        self,
        color: str = theme.CORAL,
        parent: QWidget | None = None,
        empty_text: str = "No plays yet",
        *,
        value_labels: bool = False,
        scale: bool = False,
        min_bar: float = 0,
    ) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.empty_text = empty_text
        self.value_labels = value_labels
        self.scale = scale
        self.min_bar = min_bar
        self.values: list[float] = []
        self.labels: list[str] = []
        self.tips: list[str] = []
        self.partial_last = False
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(
        self,
        values: Sequence[float],
        labels: Sequence[str],
        *,
        unit: str = "",
        partial_last: bool = False,
        tips: Sequence[str] | None = None,
    ) -> None:
        """`partial_last`: the last bar is still filling up (this month so far).

        Hovering a bar shows its own line: `tips` when given, else "label: value unit".
        """
        self.values = list(values)
        self.labels = list(labels)
        self.partial_last = partial_last and bool(self.values)
        suffix = f" {unit}" if unit else ""
        self.tips = list(tips) if tips is not None else [
            f"{lbl}: {v:,.0f}{suffix}" for lbl, v in zip(self.labels, self.values, strict=False)
        ]  # fmt: skip
        if self.partial_last and self.tips:
            self.tips[-1] += " so far"
        self.update()

    def tip_at(self, x: float) -> str:
        """The hover text for the bar under `x` (its column, so short bars are easy to hit)."""
        tips = self.tips[self.first_shown() :]
        for rect, tip in zip(self.bar_rects(), tips, strict=False):
            if rect.left() - self.GAP / 2 <= x <= rect.right() + self.GAP / 2:
                return tip
        return ""

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            text = self.tip_at(event.pos().x())
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def scale_room(self) -> float:
        """Width left of the bars for the scale numbers: room for the widest one ("12,500")."""
        if not self.scale:
            return 0
        ticks = nice_ticks(max(self.values, default=0))
        widest = QFontMetrics(small_font(self)).horizontalAdvance(f"{ticks[-1]:,}") if ticks else 0
        return max(self.SCALE_ROOM, widest + 8)

    def _plot(self) -> QRectF:
        left = self.scale_room()
        top = 8 if self.scale else 0  # room for the top scale number
        if self.value_labels or self.partial_last:
            top = self.VALUE_ROOM
        height = self.height() - self.LABEL_H - 4 - top
        return QRectF(left, top, max(self.width() - left, 1), max(height, 0))

    def first_shown(self) -> int:
        """The oldest bar on screen: 0, unless `min_bar` leaves room for only the newest."""
        if not self.min_bar or not self.values:
            return 0
        fits = int((self._plot().width() + self.GAP) // (self.min_bar + self.GAP))
        return max(len(self.values) - max(fits, 1), 0)

    def shown(self) -> list[float]:
        return self.values[self.first_shown() :]

    def bar_rects(self) -> list[QRectF]:
        """One rect per bar on screen, oldest first."""
        plot = self._plot()
        values = self.shown()
        n = len(values)
        if n == 0 or plot.height() <= 0:
            return []
        top = max(values) or 1  # the bars on screen fill the height
        bar_w = max((plot.width() - self.GAP * (n - 1)) / n, 1)
        rects = []
        for i, value in enumerate(values):
            h = max(plot.height() * value / top, 1 if value else 0)
            rects.append(QRectF(plot.left() + i * (bar_w + self.GAP), plot.bottom() - h, bar_w, h))
        return rects

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rects = self.bar_rects()
        muted = QColor(theme.MUTED)
        if not rects:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        plot = self._plot()
        first = self.first_shown()
        values = self.values[first:]
        top = max(values) or 1
        painter.setFont(small_font(self))
        if self.scale:
            for tick in nice_ticks(top):
                y = plot.bottom() - plot.height() * tick / top
                painter.fillRect(QRectF(plot.left(), y, plot.width(), 1), QColor(theme.LINE))
                painter.setPen(muted)
                painter.drawText(
                    QRectF(0, y - 8, plot.left() - 6, 16),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{tick:,}",
                )
        last = len(rects) - 1
        for i, rect in enumerate(rects):
            if self.partial_last and i == last:
                faded = QColor(self.color)
                faded.setAlpha(90)
                pen = QPen(self.color, 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(faded)
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, 0), 3, 3)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.color)
                painter.drawRoundedRect(rect, 3, 3)
        above = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
        if self.value_labels:
            painter.setPen(QColor(theme.TEXT))
            for rect, value in zip(rects, values, strict=True):
                if value:
                    painter.drawText(
                        QRectF(rect.center().x() - 30, rect.top() - 16, 60, 14),
                        above,
                        f"{value:,.0f}",
                    )
        if self.partial_last:
            rect = rects[last]
            painter.setPen(muted)
            painter.drawText(
                QRectF(rect.right() - 60, rect.top() - 16, 60, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                "so far",
            )
        painter.setFont(self.font())
        painter.setPen(muted)
        n = len(rects)
        step = max(n // 6, 1)
        # A label for every bar sits under its bar; sparser labels start at theirs.
        align = Qt.AlignmentFlag.AlignHCenter if step == 1 else Qt.AlignmentFlag.AlignLeft
        for i in range(0, n, step):
            rect = rects[i]
            painter.drawText(
                QRectF(rect.left(), plot.bottom() + 4, rect.width() * step, self.LABEL_H),
                align,
                self.labels[first + i],
            )


class LeanBar(QWidget):
    """One horizontal bar from a zero line: negative goes left, positive right.

    Bars in a group share `low` and `high` (the group's range, including 0), so zero
    sits where the data needs it: at the right edge when every value is negative.
    `usual`, when given, is marked with a dashed line (my usual difference from MAL).
    """

    MARGIN = 2

    def __init__(
        self,
        value: float,
        low: float,
        high: float,
        color: str = theme.LILAC,
        usual: float | None = None,
    ) -> None:
        super().__init__()
        self.value = value
        self.usual = usual
        self.low = min(low, 0.0, usual if usual is not None else 0.0)
        self.high = max(high, 0.0, usual if usual is not None else 0.0)
        self.color = QColor(color)
        self.setMinimumWidth(60)
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _x(self, value: float) -> float:
        usable = self.width() - 2 * self.MARGIN
        return self.MARGIN + (value - self.low) / (self.high - self.low) * usable

    def bar_rect(self) -> QRectF:
        if self.high == self.low:
            return QRectF()
        zero, end = self._x(0.0), self._x(self.value)
        return QRectF(min(zero, end), 3, abs(end - zero), self.height() - 6)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.high == self.low:
            return
        edge = QColor(theme.EDGE)
        painter.fillRect(QRectF(self._x(0.0) - 0.5, 0, 1, self.height()), edge)
        if self.usual is not None:
            painter.setPen(QPen(edge, 1, Qt.PenStyle.DashLine))
            x = self._x(self.usual)
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        rect = self.bar_rect()
        if rect.width() >= 1:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color)
            painter.drawRoundedRect(rect, 4, 4)


class LeanAxis(LeanBar):
    """The labels over a column of LeanBars: "MAL" at zero and "usual" at the dashed line."""

    def __init__(self, low: float, high: float, usual: float | None = None) -> None:
        super().__init__(0.0, low, high, usual=usual)

    BOX = 60  # each label's box, in pixels

    def label_boxes(self) -> list[tuple[QRectF, Qt.AlignmentFlag, str]]:
        """Where "MAL" and "usual" go: on their lines, but never past either edge."""
        if self.high == self.low:
            return []
        labels = [(self._x(0.0), "MAL")]
        if self.usual is not None:
            labels.append((self._x(self.usual), "usual"))
        if len(labels) == 2 and abs(labels[0][0] - labels[1][0]) < 44:
            # Too close to center both: each label moves to the outer side of its line.
            (x0, t0), (x1, t1) = sorted(labels)
            labels = [(x0 - self.BOX / 2, t0), (x1 + self.BOX / 2, t1)]
        boxes = []
        for x, text in labels:
            left = min(max(x - self.BOX / 2, 0), self.width() - self.BOX)
            # Text keeps to the side of its box nearest its line (zero is often at an edge).
            offset = x - left
            if offset < self.BOX / 4:
                align = Qt.AlignmentFlag.AlignLeft
            elif offset > self.BOX * 3 / 4:
                align = Qt.AlignmentFlag.AlignRight
            else:
                align = Qt.AlignmentFlag.AlignHCenter
            boxes.append((QRectF(left, 0, self.BOX, 16), align, text))
        return boxes

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setFont(small_font(self))
        painter.setPen(QColor(theme.MUTED))
        for box, align, text in self.label_boxes():
            painter.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, text)


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

    def __init__(self, title: str = "Genres vs the MAL crowd", columns: int = 1) -> None:
        super().__init__()
        self.body.setSpacing(6)
        self.body.addWidget(section(title))
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
        """(genre, value text) as shown, for tests."""
        return list(self._rows[kind])

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
                    widget.setToolTip(tip)
                    grid.addWidget(widget, line, col)
                self._rows[kind].append((g.genre, value))
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
            text.setToolTip(tips[line] if line < len(tips) else name)
            bar = LeanBar(value, 0.0, float(top), self.color)
            number = label(f"{value:,}")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            number.setFixedWidth(44)
            for column, widget in enumerate((text, bar, number)):
                self.grid.addWidget(widget, line, column)
            self._rows.append((name, f"{value:,}"))


def band_tip(band: charts.Band) -> str:
    shows = "1 show" if band.shows == 1 else f"{band.shows} shows"
    text = f"MAL {band.mal_from:.1f} to {band.mal_to:.1f}: {shows}. You average {band.average:.2f}"
    if band.middle:
        low, high = band.middle
        return f"{text}, and the middle half of your scores is {low:g} to {high:g}."
    return text + "."


class BandChart(QWidget):
    """My average score for each band of the MAL mean, against the "same as MAL" line.

    The bar behind each dot covers the middle half of my scores in that band. Dots for
    bands with few shows are smaller. Hovering a band explains it in words.
    """

    LEFT, BOTTOM, TOP, RIGHT = 30, 40, 24, 10
    Y_LO, Y_HI = 0.5, 10.5  # half a point of room so 1 and 10 aren't cut off

    def __init__(self, color: str = theme.LILAC, empty_text: str = "No scores yet") -> None:
        super().__init__()
        self.color = QColor(color)
        self.empty_text = empty_text
        self.bands: list[charts.Band] = []
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_bands(self, bands: Sequence[charts.Band]) -> None:
        self.bands = list(bands)
        self.update()

    def x_range(self) -> tuple[float, float]:
        return min(b.mal_from for b in self.bands), max(b.mal_to for b in self.bands)

    def point(self, mal: float, score: float) -> tuple[float, float]:
        """Widget coordinates for a MAL mean and a score."""
        lo, hi = self.x_range()
        width = max(self.width() - self.LEFT - self.RIGHT, 1)
        height = max(self.height() - self.BOTTOM - self.TOP, 1)
        x = self.LEFT + (mal - lo) / (hi - lo) * width
        y = self.TOP + (self.Y_HI - score) / (self.Y_HI - self.Y_LO) * height
        return x, y

    def _center(self, band: charts.Band) -> tuple[float, float]:
        return self.point((band.mal_from + band.mal_to) / 2, band.average)

    def tip_at(self, x: float) -> str:
        if not self.bands:
            return ""
        return band_tip(min(self.bands, key=lambda b: abs(self._center(b)[0] - x)))

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            text = self.tip_at(event.pos().x())
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = QColor(theme.MUTED)
        if not self.bands:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        lo, hi = self.x_range()
        left, right = self.LEFT, self.width() - self.RIGHT
        top, bottom = self.point(lo, self.Y_HI)[1], self.point(lo, self.Y_LO)[1]
        grid = QColor(theme.LINE)
        painter.setFont(small_font(self, 12))
        for score in (2, 4, 6, 8, 10):
            _, y = self.point(lo, score)
            painter.fillRect(QRectF(left, y, right - left, 1), grid)
            painter.setPen(muted)
            painter.drawText(
                QRectF(0, y - 8, left - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(score),
            )
        for mal in range(math.ceil(lo), math.floor(hi) + 1):
            x, _ = self.point(mal, self.Y_LO)
            painter.setPen(muted)
            painter.drawText(
                QRectF(x - 12, bottom + 4, 24, 16), Qt.AlignmentFlag.AlignHCenter, str(mal)
            )
        painter.drawText(QRectF(0, 0, 200, 16), Qt.AlignmentFlag.AlignLeft, "Your score")
        painter.drawText(
            QRectF(left, self.height() - 16, right - left, 16),
            Qt.AlignmentFlag.AlignHCenter,
            "MAL mean",
        )
        # Where my score would equal MAL's, dashed so it reads as a guide, not data.
        edge = QColor(theme.EDGE)
        painter.setPen(QPen(edge, 1.5, Qt.PenStyle.DashLine))
        start, end = max(lo, self.Y_LO), min(hi, self.Y_HI)
        painter.drawLine(QPointF(*self.point(start, start)), QPointF(*self.point(end, end)))
        painter.setPen(muted)
        ex, ey = self.point(end, end)
        painter.drawText(
            QRectF(ex - 90, max(ey - 18, top - 20), 88, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "same as MAL",
        )
        # The middle half of my scores per band, then my average as a line and dots.
        spread = QColor(self.color)
        spread.setAlpha(110)
        painter.setPen(QPen(spread, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for band in self.bands:
            if band.middle:
                x, _ = self._center(band)
                _, y_low = self.point(lo, band.middle[0])
                _, y_high = self.point(lo, band.middle[1])
                painter.drawLine(QPointF(x, y_low), QPointF(x, y_high))
        centers = [QPointF(*self._center(b)) for b in self.bands]
        painter.setPen(QPen(self.color, 2))
        painter.drawPolyline(QPolygonF(centers))
        painter.setPen(QPen(QColor(theme.SURFACE), 2))
        painter.setBrush(self.color)
        for band, center in zip(self.bands, centers, strict=True):
            radius = 4.5 if band.shows >= charts.MIDDLE_MIN_SHOWS else 3.0
            painter.drawEllipse(center, radius, radius)
