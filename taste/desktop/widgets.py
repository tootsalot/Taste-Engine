"""Small reusable widgets: cards, stat tiles, charts, and the report table model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

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


class RowsModel(QAbstractTableModel):
    """Read-only table over query rows. Sorting goes through a QSortFilterProxyModel."""

    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        super().__init__()
        self.columns = list(columns)
        self.rows = [tuple(r) for r in rows]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if value is None else value
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(value, (int, float)):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> Any:  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section].replace("_", " ")
        return None
