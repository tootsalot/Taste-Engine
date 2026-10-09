"""Small reusable widgets: cards, labels, and the painted charts."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from taste import overview
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


class Card(QFrame):
    """A rounded surface panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(8)


class BarChart(QWidget):
    """Plain vertical bars with a few axis labels. Painted directly, no chart library."""

    def __init__(
        self,
        color: str = theme.CORAL,
        parent: QWidget | None = None,
        empty_text: str = "No plays yet",
    ) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.empty_text = empty_text
        self.values: list[float] = []
        self.labels: list[str] = []
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, values: Sequence[float], labels: Sequence[str]) -> None:
        self.values = list(values)
        self.labels = list(labels)
        tips = [f"{lbl}: {v:,.0f}" for lbl, v in zip(self.labels, self.values, strict=False)]
        self.setToolTip("\n".join(tips))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_h = 18
        chart_h = self.height() - label_h - 4
        n = len(self.values)
        if n == 0 or chart_h <= 0:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        top = max(self.values) or 1
        gap = 3
        bar_w = max((self.width() - gap * (n - 1)) / n, 1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        for i, value in enumerate(self.values):
            h = max(chart_h * value / top, 1 if value else 0)
            x = i * (bar_w + gap)
            painter.drawRoundedRect(QRectF(x, chart_h - h, bar_w, h), 3, 3)
        painter.setPen(QColor(theme.MUTED))
        step = max(n // 6, 1)
        # A label for every bar sits under its bar; sparser labels start at theirs.
        align = Qt.AlignmentFlag.AlignHCenter if step == 1 else Qt.AlignmentFlag.AlignLeft
        for i in range(0, n, step):
            x = i * (bar_w + gap)
            painter.drawText(QRectF(x, chart_h + 4, bar_w * step, label_h), align, self.labels[i])


class LeanBar(QWidget):
    """One horizontal bar from a zero line: negative goes left, positive right.

    Bars in a group share `low` and `high` (the group's range, including 0), so zero
    sits where the data needs it: at the right edge when every value is negative.
    """

    MARGIN = 2

    def __init__(self, value: float, low: float, high: float, color: str = theme.LILAC) -> None:
        super().__init__()
        self.value = value
        self.low = min(low, 0.0)
        self.high = max(high, 0.0)
        self.color = QColor(color)
        self.setMinimumWidth(60)
        self.setFixedHeight(14)
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
        zero = self._x(0.0)
        painter.fillRect(QRectF(zero - 0.5, 0, 1, self.height()), QColor(theme.LINE))
        rect = self.bar_rect()
        if rect.width() >= 1:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color)
            painter.drawRoundedRect(rect, 4, 4)


class GenreCard(Card):
    """Most generous and harshest genres against the MAL community, as bars from zero."""

    def __init__(self, title: str = "Genres vs the MAL crowd") -> None:
        super().__init__()
        self.body.setSpacing(6)
        self.body.addWidget(section(title))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(3)
        self.grid.setColumnStretch(1, 1)
        self.body.addLayout(self.grid)
        self.empty = label("", role="muted", wrap=True)
        self.body.addWidget(self.empty)
        self.body.addStretch()
        self._rows: dict[str, list[tuple[str, str]]] = {"generous": [], "harsh": []}

    def rows(self, kind: str) -> list[tuple[str, str]]:
        """(genre, value text) as shown, for tests."""
        return list(self._rows[kind])

    def set_genres(
        self, generous: list[overview.GenreLean], harsh: list[overview.GenreLean], empty: str
    ) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = {"generous": [], "harsh": []}
        values = [g.avg_diff for g in generous + harsh]
        low, high = min(values, default=0.0), max(values, default=0.0)
        line = 0
        for kind, title, genres in (
            ("generous", "Most generous", generous),
            ("harsh", "Harshest", harsh),
        ):
            if not genres:
                continue
            self.grid.addWidget(label(title, role="muted"), line, 0, 1, 3)
            line += 1
            for g in genres:
                value = f"{g.avg_diff:+.2f}"
                tip = (
                    f"{g.genre}: you average {g.my_avg_score:.2f}, the MAL community "
                    f"{g.community_avg_score:.2f}, across {g.shows_scored} scored shows."
                )
                name = label(g.genre)
                name.setFixedWidth(120)
                bar = LeanBar(g.avg_diff, low, high)
                number = label(value)
                number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                number.setFixedWidth(48)
                for column, widget in enumerate((name, bar, number)):
                    widget.setToolTip(tip)
                    self.grid.addWidget(widget, line, column)
                self._rows[kind].append((g.genre, value))
                line += 1
        self.empty.setText(empty)
        self.empty.setVisible(not (generous or harsh))


class RankList(QWidget):
    """A ranked list as bars from zero: name, bar, number. Used for top artists and tracks."""

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
        """(name, number) as shown, for tests."""
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
            text = label(name)
            text.setFixedWidth(150)
            text.setToolTip(tips[line] if line < len(tips) else name)
            bar = LeanBar(value, 0.0, float(top), self.color)
            number = label(f"{value:,}")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            number.setFixedWidth(44)
            for column, widget in enumerate((text, bar, number)):
                self.grid.addWidget(widget, line, column)
            self._rows.append((name, f"{value:,}"))


class ScatterChart(QWidget):
    """My score against the MAL mean, one dot per scored show, with the 'same as MAL' line."""

    def __init__(self, color: str = theme.LILAC, empty_text: str = "No scores yet") -> None:
        super().__init__()
        self.color = QColor(color)
        self.empty_text = empty_text
        self.points: list[tuple[float, float]] = []
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_points(self, points: Sequence[tuple[float, float]]) -> None:
        self.points = list(points)
        self.update()

    def x_range(self) -> tuple[float, float]:
        """The MAL means shown: the data's range with a little room, never past 1 to 10."""
        x_lo = max(min(x for x, _ in self.points) - 0.25, 1.0)
        x_hi = min(max(x for x, _ in self.points) + 0.25, 10.0)
        return x_lo, max(x_hi, x_lo + 0.5)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = QColor(theme.MUTED)
        if not self.points:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        left, bottom, top_pad, right_pad = 30, 40, 24, 8
        width = max(self.width() - left - right_pad, 1)
        height = max(self.height() - bottom - top_pad, 1)
        x_lo, x_hi = self.x_range()
        y_lo, y_hi = 0.5, 10.5  # half a point of room so dots on 1 and 10 aren't cut off

        def at(x: float, y: float) -> tuple[float, float]:
            px = left + (x - x_lo) / (x_hi - x_lo) * width
            py = top_pad + (y_hi - y) / (y_hi - y_lo) * height
            return px, py

        grid = QColor(theme.LINE)
        right_align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for y in (2, 4, 6, 8, 10):
            _, py = at(x_lo, y)
            painter.setPen(grid)
            painter.drawLine(QPointF(left, py), QPointF(left + width, py))
            painter.setPen(muted)
            painter.drawText(QRectF(0, py - 8, left - 6, 16), right_align, str(y))
        for x in range(math.ceil(x_lo), math.floor(x_hi) + 1):
            px, _ = at(x, y_lo)
            painter.setPen(grid)
            painter.drawLine(QPointF(px, top_pad), QPointF(px, top_pad + height))
            painter.setPen(muted)
            painter.drawText(
                QRectF(px - 12, top_pad + height + 4, 24, 16), Qt.AlignmentFlag.AlignHCenter, str(x)
            )
        painter.drawText(QRectF(0, 0, width, 16), Qt.AlignmentFlag.AlignLeft, "Your score")
        painter.drawText(
            QRectF(left, self.height() - 16, width, 16), Qt.AlignmentFlag.AlignHCenter, "MAL mean"
        )
        # Where my score would equal MAL's.
        painter.setPen(QColor(theme.EDGE))
        x0, y0 = at(x_lo, x_lo)
        x1, y1 = at(x_hi, x_hi)
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        # See-through dots, so shows stacked on the same score read as a brighter spot.
        dot = QColor(self.color)
        dot.setAlpha(130)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        for x, y in self.points:
            px, py = at(x, y)
            painter.drawEllipse(QPointF(px, py), 3.2, 3.2)
