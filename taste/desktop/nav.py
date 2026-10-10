"""The sidebar: page buttons, Settings pinned to the bottom, and when data last synced.

It offers the bits of QListWidget the app used (count, item, currentRow,
setCurrentRow, currentRowChanged), built from buttons so Settings can sit at the
bottom with the sync line above it.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from taste.desktop import theme
from taste.desktop.widgets import label

ICON = QSize(20, 20)


def nav_button(icon: str, text: str = "") -> QPushButton:
    """A full-width sidebar row: icon 10 px in, then the page name."""
    button = QPushButton(text)
    button.setProperty("role", "nav")
    button.setIcon(theme.icon(icon))
    button.setIconSize(ICON)
    button.setFixedHeight(40)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class NavList(QWidget):
    currentRowChanged = Signal(int)

    def __init__(self, pages: Sequence[tuple[str, str]], bottom: int) -> None:
        """`pages` are (name, icon). Pages from index `bottom` on sit at the bottom."""
        super().__init__()
        self.names = [name for name, _ in pages]
        self.collapsed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []

        self.status = QWidget()
        status_row = QHBoxLayout(self.status)
        # The dot's center lines up with the icons' (10 px padding + half of 20 px).
        status_row.setContentsMargins(16, 6, 4, 6)
        status_row.setSpacing(8)
        self.status_dot = QFrame()
        self.status_dot.setObjectName("StatusDot")
        self.status_dot.setFixedSize(8, 8)
        self.status_dot.setProperty("state", "idle")
        # Left-aligned, or the row centers it once the words hide in the collapsed rail.
        status_row.addWidget(
            self.status_dot, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.status_text = label("Not synced yet", role="caption", wrap=True)
        status_row.addWidget(self.status_text, 1)

        for index, (name, icon) in enumerate(pages):
            if index == bottom:
                layout.addStretch(1)
                layout.addWidget(self.status)
            button = nav_button(icon, name)
            button.setCheckable(True)
            button.installEventFilter(self)
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        self.group.idToggled.connect(self._on_toggled)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        """Up and Down move between pages, like the list this replaced."""
        if event.type() == QEvent.Type.KeyPress and obj in self.buttons:
            step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(event.key())
            if step:
                row = (self.buttons.index(obj) + step) % len(self.buttons)
                self.setCurrentRow(row)
                self.buttons[row].setFocus()
                return True
        return super().eventFilter(obj, event)

    # -- the QListWidget-like part -------------------------------------------

    def count(self) -> int:
        return len(self.buttons)

    def item(self, row: int) -> QPushButton:
        return self.buttons[row]

    def currentRow(self) -> int:  # noqa: N802 (Qt naming, like QListWidget)
        return self.group.checkedId()

    def setCurrentRow(self, row: int) -> None:  # noqa: N802 (Qt naming, like QListWidget)
        if 0 <= row < len(self.buttons):
            self.buttons[row].setChecked(True)

    def _on_toggled(self, row: int, checked: bool) -> None:
        if checked:
            self.currentRowChanged.emit(row)

    def focus_current(self) -> None:
        row = max(self.currentRow(), 0)
        self.buttons[row].setFocus()

    # -- looks -----------------------------------------------------------------

    def set_collapsed(self, collapsed: bool) -> None:
        """Icons only, with the names as tooltips, or icons and names."""
        self.collapsed = collapsed
        for button, name in zip(self.buttons, self.names, strict=True):
            button.setText("" if collapsed else name)
            button.setToolTip(name if collapsed else "")
        self.status_text.setVisible(not collapsed)

    def set_status(self, text: str, state: str, details: str = "") -> None:
        """The last sync, under the pages. Collapsed, only the dot shows; hover for words."""
        self.status_text.setText(text)
        self.status_dot.setToolTip("\n".join(filter(None, (text, details))))
        self.status_text.setToolTip(details)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)
