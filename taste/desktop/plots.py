"""Painted charts, axes, and the helpers they use."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QSizePolicy,
    QToolTip,
    QWidget,
)

from taste import charts
from taste.desktop import theme
from taste.desktop.widgets import plain_tip, small_font


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


def top_rounded(rect: QRectF, radius: float = 5) -> QPainterPath:
    """A bar with rounded top corners and a square foot on the baseline."""
    radius = min(radius, rect.width() / 2, rect.height())
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if rect.height() > radius:
        foot = QPainterPath()
        foot.addRect(QRectF(rect.left(), rect.bottom() - radius, rect.width(), radius))
        path = path.united(foot)
    return path


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
    LABEL_GAP = 12  # the least space between two axis labels
    SOFT = 0.42  # how strong the bars other than the lead one are (2.4)

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
                QToolTip.showText(event.globalPos(), plain_tip(text), self)
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
        lead = self.lead_bar()
        soft = QColor(self.color)
        soft.setAlphaF(self.SOFT)
        for i, rect in enumerate(rects):
            if self.partial_last and i == last:
                faded = QColor(self.color)
                faded.setAlpha(60)
                pen = QPen(self.color, 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(faded)
                painter.drawPath(top_rounded(rect.adjusted(0.5, 0.5, -0.5, 0)))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.color if i == lead else soft)
                painter.drawPath(top_rounded(rect))
        for box, align, text in self.top_texts():
            partial = self.partial_last and text.endswith("so far")
            painter.setPen(muted if partial else QColor(theme.TEXT))
            painter.drawText(box, align, text)
        painter.setFont(self.font())
        painter.setPen(muted)
        for box, align, text in self.axis_labels():
            painter.drawText(box, align, text)

    def lead_bar(self) -> int:
        """The one bar drawn at full color (2.4): the highest finished one, the newest
        on a tie. The rest are a soft fill, so the chart isn't a solid wall of color."""
        values = self.shown()
        if self.partial_last:
            values = values[:-1]
        if not values or not max(values):
            return -1
        top = max(values)
        return max(i for i, v in enumerate(values) if v == top)

    def top_texts(self) -> list[tuple[QRectF, Qt.AlignmentFlag, str]]:
        """What goes above the bars: each count (value_labels) and "so far" over a bar
        still filling up."""
        rects = self.bar_rects()
        values = self.shown()
        metrics = QFontMetrics(small_font(self))
        bottom = Qt.AlignmentFlag.AlignBottom
        center = Qt.AlignmentFlag.AlignHCenter | bottom
        right = Qt.AlignmentFlag.AlignRight | bottom

        def box(rect: QRectF, text: str, flush_right: bool = False) -> QRectF:
            width = metrics.horizontalAdvance(text) + 2
            x = rect.right() - width if flush_right else rect.center().x() - width / 2
            x = min(max(x, 0), self.width() - width)
            # Above every bar it reaches over, so a taller neighbor never hides it.
            top = min(r.top() for r in rects if r.right() >= x and r.left() <= x + width)
            return QRectF(x, min(top, rect.top()) - 16, width, 14)

        texts: list[tuple[QRectF, Qt.AlignmentFlag, str]] = []
        last = len(rects) - 1
        for i, (rect, value) in enumerate(zip(rects, values, strict=True)):
            partial = self.partial_last and i == last
            if self.value_labels and value:
                text = f"{value:,.0f}"
                if partial:
                    # "12 so far" when it clears the bar before's number; else the
                    # number alone (the dashed bar and its hover text still say so).
                    joined = box(rect, f"{text} so far")
                    before = texts[-1][0] if texts else None
                    if before is None or joined.left() >= before.right() + self.LABEL_GAP / 2:
                        text += " so far"
                texts.append((box(rect, text), center, text))
            elif partial:
                texts.append((box(rect, "so far", flush_right=True), right, "so far"))
        return texts

    def axis_labels(self) -> list[tuple[QRectF, Qt.AlignmentFlag, str]]:
        """Where the labels under the bars go. Every bar gets one when they fit, centered;
        otherwise every second, third, ... bar, spaced by the widest label so none overlap.
        A label that would run past the right edge is left out rather than cut off.
        """
        rects = self.bar_rects()
        labels = self.labels[self.first_shown() :]
        if not rects or not labels:
            return []
        metrics = QFontMetrics(self.font())
        widths = [metrics.horizontalAdvance(text) for text in labels]
        pitch = rects[1].left() - rects[0].left() if len(rects) > 1 else rects[0].width()
        step = max(1, math.ceil((max(widths) + self.LABEL_GAP) / pitch))
        y = self._plot().bottom() + 4
        if step > 1 and not all(labels):
            # Sparse labels by design (whole stars over half-star bars): place each one
            # under its own bar wherever it fits, instead of every nth bar, which could
            # land only on the blanks.
            boxes, last_right = [], -math.inf
            for rect, text, width in zip(rects, labels, widths, strict=False):
                left = rect.center().x() - width / 2
                if not text or left < last_right + self.LABEL_GAP or left + width > self.width():
                    continue
                boxes.append((QRectF(left, y, width + 1, self.LABEL_H), Qt.AlignmentFlag.AlignLeft,
                              text))  # fmt: skip
                last_right = left + width
            return boxes
        if step == 1:
            center = Qt.AlignmentFlag.AlignHCenter
            return [
                (QRectF(rect.left(), y, rect.width(), self.LABEL_H), center, text)
                for rect, text in zip(rects, labels, strict=False)
            ]
        boxes = []
        for i in range(0, min(len(rects), len(labels)), step):
            left = rects[i].left()
            if left + widths[i] > self.width():
                break
            box = QRectF(left, y, widths[i] + 1, self.LABEL_H)
            boxes.append((box, Qt.AlignmentFlag.AlignLeft, labels[i]))
        return boxes


class Sparkline(QWidget):
    """A tiny line of a few values, oldest first, with a dot on the newest."""

    def __init__(self, color: str = theme.LILAC) -> None:
        super().__init__()
        self.color = QColor(color)
        self.values: list[float] = []
        # 84 px wide where there's room, down to 48 in a narrow tile (2.5).
        self.setMinimumSize(48, 30)
        self.setMaximumSize(84, 30)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(84, 30)

    def set_values(self, values: Sequence[float]) -> None:
        self.values = list(values)
        self.setVisible(len(self.values) > 1)
        self.update()

    def points(self) -> list[QPointF]:
        if len(self.values) < 2:
            return []
        low, high = min(self.values), max(self.values)
        span = (high - low) or 1.0
        width, height = self.width() - 6, self.height() - 6
        step = width / (len(self.values) - 1)
        return [
            QPointF(3 + i * step, 3 + height - (v - low) / span * height)
            for i, v in enumerate(self.values)
        ]

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        points = self.points()
        if not points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPolyline(QPolygonF(points))
        painter.setBrush(self.color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(points[-1], 3, 3)


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
        if type(self) is LeanBar:
            # A faint track the whole width, so a row reads across from its name to its
            # bar even when the bar sits at the far end (2.4).
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.RAISED))
            painter.drawRoundedRect(QRectF(0, 3, self.width(), self.height() - 6), 4, 4)
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


def band_tip(
    band: charts.Band, crowd: str = "MAL", noun: str = "show", scores: str = "scores"
) -> str:
    count = f"1 {noun}" if band.shows == 1 else f"{band.shows} {noun}s"
    places = 1 if band.mal_to - band.mal_from >= 0.5 else 2
    text = (
        f"{crowd} {band.mal_from:.{places}f} to {band.mal_to:.{places}f}: {count}. "
        f"You average {band.average:.2f}"
    )
    if band.middle:
        low, high = band.middle
        return f"{text}, and the middle half of your {scores} is {low:g} to {high:g}."
    return text + "."


class BandChart(QWidget):
    """My average score for each band of the crowd's average, against the "same as" line:
    MAL scores out of 10 on Anime, Hardcover stars out of 5 on Books.

    The bar behind each dot covers the middle half of my scores in that band. Dots for
    bands with few titles are smaller. Hovering a band explains it in words.
    """

    LEFT, BOTTOM, TOP, RIGHT = 30, 40, 24, 10

    def __init__(
        self,
        color: str = theme.LILAC,
        empty_text: str = "No scores yet",
        *,
        crowd: str = "MAL",
        top: int = 10,
        noun: str = "show",
        scores: str = "scores",
    ) -> None:
        super().__init__()
        self.color = QColor(color)
        self.empty_text = empty_text
        self.crowd, self.noun, self.scores = crowd, noun, scores
        self.top = top
        room = top / 20  # so the lowest and highest score aren't cut off
        self.Y_LO, self.Y_HI = (1 if top == 10 else 0.5) - room, top + room
        self.bands: list[charts.Band] = []
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_bands(self, bands: Sequence[charts.Band]) -> None:
        self.bands = list(bands)
        self.update()

    def x_range(self) -> tuple[float, float]:
        return min(b.mal_from for b in self.bands), max(b.mal_to for b in self.bands)

    def y_ticks(self) -> list[int]:
        ticks = list(range(2, 11, 2)) if self.top == 10 else list(range(1, self.top + 1))
        # A short chart keeps every other one, so the numbers never touch.
        gap = (self.height() - self.BOTTOM - self.TOP) / max(len(ticks), 1)
        return ticks[::2] if 0 < gap < 22 else ticks

    def x_ticks(self) -> list[float]:
        lo, hi = self.x_range()
        step = 1.0 if hi - lo >= 2.5 else 0.5
        first = math.ceil(lo / step) * step
        return [first + i * step for i in range(int((hi - first) / step + 1e-9) + 1)]

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
        band = min(self.bands, key=lambda b: abs(self._center(b)[0] - x))
        return band_tip(band, self.crowd, self.noun, self.scores)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            text = self.tip_at(event.pos().x())
            if text:
                QToolTip.showText(event.globalPos(), plain_tip(text), self)
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
        for score in self.y_ticks():
            _, y = self.point(lo, score)
            painter.fillRect(QRectF(left, y, right - left, 1), grid)
            painter.setPen(muted)
            painter.drawText(
                QRectF(0, y - 8, left - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(score),
            )
        for mark in self.x_ticks():
            x, _ = self.point(mark, self.Y_LO)
            painter.setPen(muted)
            painter.drawText(
                QRectF(x - 16, bottom + 4, 32, 16), Qt.AlignmentFlag.AlignHCenter, f"{mark:g}"
            )
        painter.drawText(
            QRectF(0, 0, 200, 16),
            Qt.AlignmentFlag.AlignLeft,
            "Your score" if self.scores == "scores" else f"Your {self.scores}",
        )
        painter.drawText(
            QRectF(left, self.height() - 16, right - left, 16),
            Qt.AlignmentFlag.AlignHCenter,
            "MAL mean" if self.crowd == "MAL" else f"{self.crowd} average",
        )
        # Where my score would equal MAL's, dashed so it reads as a guide, not data.
        edge = QColor(theme.EDGE)
        painter.setPen(QPen(edge, 1.5, Qt.PenStyle.DashLine))
        start, end = max(lo, self.Y_LO), min(hi, self.Y_HI)
        painter.drawLine(QPointF(*self.point(start, start)), QPointF(*self.point(end, end)))
        painter.setPen(muted)
        ex, ey = self.point(end, end)
        painter.drawText(
            QRectF(ex - 140, max(ey - 18, top - 20), 138, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"same as {self.crowd}",
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
