"""Basic Qt widgets, plain-text labels, and shared layout helpers."""

from __future__ import annotations

import html
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from taste.desktop import motion, theme


def plain_tip(text: str) -> str:
    """Tooltip text shown exactly as written. Qt guesses whether a tooltip is rich text,
    so a title from an API that looks like markup would be drawn as markup; escaping it
    inside <qt> makes the guess irrelevant (the tooltip twin of label()'s plain text)."""
    if not text:
        return ""
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


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


def card_title(text: str) -> QLabel:
    """A card's title: sentence case, semibold, the first thing read in the card (2.4)."""
    widget = label(text, role="card-title")
    font = QFont(theme.HEADING_FAMILY)
    font.setPixelSize(theme.CARD_TITLE_PX)
    font.setWeight(QFont.Weight.DemiBold)
    widget.setFont(font)
    return widget


def section(text: str) -> QLabel:
    """A small spaced-out label inside a card or panel ("About", "Save"). Shown in
    capitals; the text itself stays as written. Card titles use card_title."""
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


class ElidedLabel(QLabel):
    """One line of plain text that gives up width when the row is tight, ending in "…"
    with the whole text in its tooltip, instead of pushing its row (and the page) wider.
    `text()` is always the whole text."""

    MIN_WIDTH = 60

    def __init__(self, text: str = "", role: str | None = None, min_width: int = MIN_WIDTH) -> None:
        """`min_width`: what it keeps however tight the row gets (a short notice keeps
        its whole sentence while a longer one beside it shortens)."""
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)
        if role:
            self.setProperty("role", role)
        self.min_width = min_width
        self.setMinimumWidth(min_width)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.full = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self.full = text
        self.updateGeometry()
        self._fit()

    def text(self) -> str:
        return self.full

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self.full) + 4, hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(self.min_width, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fit()

    def setToolTip(self, tip: str) -> None:  # noqa: N802 (Qt naming)
        """A tooltip set from outside (the status line's per-source times) wins; without
        one, the whole text shows on hover while it's shortened."""
        self.own_tip = tip
        self._fit()

    def _fit(self) -> None:
        shown = elided(self.full, self.font(), max(self.width(), 1))
        QLabel.setText(self, shown)
        tip = getattr(self, "own_tip", "")
        QLabel.setToolTip(self, tip or (plain_tip(self.full) if shown != self.full else ""))


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


ACCENTS = {
    "anime": theme.LILAC,
    "music": theme.CORAL,
    "books": theme.AMBER,
    "screen": theme.TEAL,
}


class _Pill(QWidget):
    """The lit shape behind the chosen segment: a fully rounded bar in the accent color."""

    def __init__(self, parent: QWidget, accent: str) -> None:
        super().__init__(parent)
        self.color = QColor(ACCENTS.get(accent, theme.LILAC))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        radius = self.height() / 2
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)


class Segmented(QFrame):
    """A row of pill buttons where exactly one is on, like Simple | Advanced.

    The lit pill is a separate shape behind the buttons, so it can slide from one
    to the next instead of jumping.
    """

    changed = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        accent: str = "anime",
        accents: dict[str, str] | None = None,
    ) -> None:
        """`options` are (key, text). `accent` lights the chosen one: "anime" lilac,
        "music" coral, "books" amber, "screen" teal. `accents` gives some keys their own
        (For You: each list in its medium's color)."""
        super().__init__()
        self.accents = accents or {}
        self.setProperty("role", "segmented")
        self.pill = _Pill(self, accent)
        self.pill.hide()
        self._slide = None
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
            # Tab reaches it (with its focus ring); a click doesn't leave a ring around
            # the lit pill (2.4).
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._fit(button, text)
            self.group.addButton(button)
            row.addWidget(button)
            self.buttons[key] = button

        self.group.buttonToggled.connect(lambda button, on: on and self._move_pill(True))

    def set_value(self, key: str) -> None:
        self.buttons[key].setChecked(True)
        self._move_pill(animate=False)

    def set_text(self, key: str, text: str) -> None:
        """Change one segment's words ("Anime · 30"); the lit pill follows its new width."""
        button = self.buttons[key]
        button.setText(text)
        self._fit(button, text)
        self._move_pill(animate=False)

    @staticmethod
    def _fit(button: QPushButton, text: str) -> None:
        # The chosen one is bold; leave room for that, or its first letter is cut off.
        bold = QFont(button.font())
        bold.setBold(True)
        button.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(text) + 32)

    def value(self) -> str:
        return next((k for k, b in self.buttons.items() if b.isChecked()), "")

    def _target(self):
        button = self.group.checkedButton()
        return button.geometry() if button is not None else None

    def _move_pill(self, animate: bool) -> None:
        accent = self.accents.get(self.value())
        if accent is not None:
            self.pill.color = QColor(ACCENTS.get(accent, theme.LILAC))
            self.pill.update()
        target = self._target()
        if target is None or target.width() <= 0:
            return
        start = self.pill.geometry()
        self.pill.show()
        self.pill.lower()
        if not animate or not self.isVisible() or start.width() <= 0:
            self.pill.setGeometry(target)
            return

        def step(t: float) -> None:
            self.pill.setGeometry(
                round(start.x() + (target.x() - start.x()) * t),
                target.y(),
                round(start.width() + (target.width() - start.width()) * t),
                target.height(),
            )

        self._slide = motion.tween(0.0, 1.0, step, motion.duration(220), owner=self)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._move_pill(animate=False)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._move_pill(animate=False)


class LazyPage:
    """For a page: refresh now if it's the page in front, else when it's next shown.

    A sync used to redraw every page whether anyone was looking (2.1). The page calls
    `refresh_later()` instead of its refresh; `lazy_refresh()` names what to run, and
    `while_hidden()` what to do at once anyway (the Dashboard's status line).
    """

    stale = False

    def lazy_refresh(self) -> None:
        raise NotImplementedError

    def while_hidden(self) -> None:
        pass

    def refresh_later(self) -> None:
        if self.isVisibleTo(self.window()):  # the page in front, window shown or not
            self.stale = False
            self.lazy_refresh()
        else:
            self.stale = True
            self.while_hidden()

    def refresh_if_stale(self) -> None:
        """Catch up after syncs that ran while the page was off screen."""
        if self.stale:
            self.stale = False
            self.lazy_refresh()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self.refresh_if_stale()


def set_role(widget: QWidget, role: str) -> None:
    """Change a widget's style role and re-apply the stylesheet."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ScrollArea(QScrollArea):
    """A scroll area that says when the width its content really gets changes (the
    viewport, which also narrows when a scroll bar appears).

    Pages used an event filter on the viewport for this, but Qt could still call a
    filter while a closing window was being torn down, after its Python side was gone.
    A signal is dropped as soon as either end is deleted.
    """

    viewport_resized = Signal(int)  # the viewport's new width

    def viewportEvent(self, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.Resize:
            self.viewport_resized.emit(event.size().width())
        return super().viewportEvent(event)


class Card(QFrame):
    """A rounded surface panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(10)
