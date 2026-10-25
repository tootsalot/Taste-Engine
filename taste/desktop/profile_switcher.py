"""The profile switcher at the top of the sidebar: the profile's picture (or its first
letter in a disc), its name, and a menu of every profile, New profile, and the picture.

It offers the bits of QComboBox the app and its tests used (addItem, clear, findData,
setCurrentIndex, currentIndex, currentData, currentText, setItemText, count,
currentIndexChanged), the way the sidebar's NavList stands in for a QListWidget.

The menu opens from the click, not through QPushButton.setMenu: on Windows a push
button with a menu made Qt warn "QFont::setPointSize: Point size <= 0" under the app's
stylesheet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QImage,
    QImageReader,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QPushButton, QSizePolicy

from taste import profiles
from taste.desktop import theme

AVATAR = QSize(30, 30)
PICTURE_PX = 256  # stored pictures are this square
PICTURE_MAX_BYTES = 20 * 1024 * 1024
PICTURE_MAX_SIDE = 12000  # larger images are refused before they're decoded


def save_picture(source: str | Path, target: Path) -> str | None:
    """Make a square profile picture from an image file: the middle square, PICTURE_PX
    wide, saved as PNG at `target`. Returns a reason when the file can't be used."""
    source = Path(source)
    try:
        if source.stat().st_size > PICTURE_MAX_BYTES:
            return "That picture is over 20 MB. Pick a smaller one."
    except OSError:
        return "Couldn't open that file."
    reader = QImageReader(str(source))
    reader.setAutoTransform(True)  # phone photos keep their rotation in EXIF
    size = reader.size()
    if not size.isValid() or not reader.canRead():
        return "That file isn't a picture Taste Engine can read."
    if max(size.width(), size.height()) > PICTURE_MAX_SIDE:
        return "That picture is too large. Pick one under 12,000 pixels a side."
    # Decode at about twice the stored size, not at full camera resolution.
    short = min(size.width(), size.height())
    if short > PICTURE_PX * 2:
        scale = PICTURE_PX * 2 / short
        reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
    image = reader.read()
    if image.isNull():
        return "That file isn't a picture Taste Engine can read."
    side = min(image.width(), image.height())
    square = image.copy(
        (image.width() - side) // 2, (image.height() - side) // 2, side, side
    ).scaled(
        PICTURE_PX,
        PICTURE_PX,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not square.convertToFormat(QImage.Format.Format_ARGB32).save(str(target), "PNG"):
        return "Couldn't save the picture in the data folder."
    return None


def avatar(
    name: str, picture: Path | None = None, size: QSize = AVATAR, ratio: float = 2.0
) -> QIcon:
    """The profile's picture in a circle, or its first letter on the logo's
    lilac-to-coral disc."""
    pixmap = QPixmap(round(size.width() * ratio), round(size.height() * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    image = QImage(str(picture)) if picture is not None and picture.is_file() else QImage()
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0, 0, size.width(), size.height())
        if not image.isNull():
            clip = QPainterPath()
            clip.addEllipse(rect)
            painter.setClipPath(clip)
            painter.drawImage(rect, image)
        else:
            fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
            fill.setColorAt(0, QColor(theme.LILAC))
            fill.setColorAt(1, QColor(theme.CORAL))
            painter.setBrush(fill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect)
            font = QFont(theme.HEADING_FAMILY)
            font.setPixelSize(round(size.height() * 0.46))
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(QColor(theme.ON_ACCENT))
            letter = (name.strip()[:1] or "?").upper()
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)
    finally:
        painter.end()
    return QIcon(pixmap)


class ProfileSwitcher(QPushButton):
    """A push button, not a tool button, so its text lines up left like the page rows."""

    currentIndexChanged = Signal(int)  # noqa: N815 (Qt naming, like QComboBox)
    new_requested = Signal()
    picture_requested = Signal()  # "Change picture…"
    picture_removed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ProfileSwitcher")
        self.items: list[tuple[str, Any]] = []
        self.index = -1
        self.collapsed = False
        self.setIconSize(AVATAR)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Profile")
        # Tab reaches it, with its focus ring; a click doesn't leave the ring behind.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # Painted, not a label in a layout: a layout inside the button overrode its
        # fixed height (it drew 32 px tall, not 48).
        self.chevron = QIcon(theme.CHEVRON_DOWN).pixmap(QSize(10, 10))
        self.menu_ = QMenu(self)
        self.menu_.setObjectName("ProfileMenu")
        # Rounded corners need the menu's window to be see-through outside them.
        self.menu_.setWindowFlags(
            self.menu_.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.menu_.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.menu_.aboutToHide.connect(self._menu_closed)
        self.by_keyboard = False  # how the menu was last opened
        self.group = QActionGroup(self)
        self.group.setExclusive(True)
        self.clicked.connect(self.open_menu)
        self._rebuild()

    HEIGHT = 48

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        # Its own height, not the theme's button height: a fixed height set here lost to
        # the stylesheet's min-height, and the switcher drew 32 px tall.
        return QSize(super().sizeHint().width(), self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(super().minimumSizeHint().width(), self.HEIGHT)

    def open_menu(self) -> None:
        self.menu_.setMinimumWidth(self.width())  # as wide as the button it drops from
        self.menu_.popup(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.by_keyboard = True
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.by_keyboard = False
        super().mousePressEvent(event)

    def _menu_closed(self) -> None:
        # A ring left around the switcher after choosing with the mouse looked like a
        # pending action; from the keyboard it shows where you are, so it stays.
        if not self.by_keyboard and self.hasFocus():
            self.clearFocus()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        if self.collapsed:
            return
        painter = QPainter(self)
        try:
            size = self.chevron.deviceIndependentSize()
            x = self.width() - 10 - size.width()
            painter.drawPixmap(
                QPoint(round(x), round((self.height() - size.height()) / 2)), self.chevron
            )
        finally:
            painter.end()

    # -- the QComboBox-like part ---------------------------------------------

    def addItem(self, text: str, data: Any = None) -> None:  # noqa: N802 (Qt naming)
        self.items.append((text, data))
        if self.index < 0:
            self.index = 0
        self._rebuild()

    def clear(self) -> None:
        self.items = []
        self.index = -1
        self._rebuild()

    def count(self) -> int:
        return len(self.items)

    def findData(self, data: Any) -> int:  # noqa: N802 (Qt naming)
        for i, (_, value) in enumerate(self.items):
            if value == data:
                return i
        return -1

    def currentIndex(self) -> int:  # noqa: N802 (Qt naming)
        return self.index

    def currentData(self) -> Any:  # noqa: N802 (Qt naming)
        return self.items[self.index][1] if 0 <= self.index < len(self.items) else None

    def currentText(self) -> str:  # noqa: N802 (Qt naming)
        return self.items[self.index][0] if 0 <= self.index < len(self.items) else ""

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 (Qt naming)
        index = index if 0 <= index < len(self.items) else (0 if self.items else -1)
        changed = index != self.index
        self.index = index
        self._rebuild()
        if changed and not self.signalsBlocked():
            self.currentIndexChanged.emit(index)

    def setItemText(self, index: int, text: str) -> None:  # noqa: N802 (Qt naming)
        if 0 <= index < len(self.items):
            self.items[index] = (text, self.items[index][1])
            self._rebuild()

    # -- looks -----------------------------------------------------------------

    def set_collapsed(self, collapsed: bool) -> None:
        """The disc alone in the icon rail, with the name as its tooltip."""
        self.collapsed = collapsed
        self._rebuild()

    def refresh_picture(self) -> None:
        """Redraw after the current profile's picture changed."""
        self._rebuild()

    def _picture(self, data: Any) -> Path | None:
        if not data:
            return None
        try:
            path = profiles.picture_path(data)
        except profiles.ProfileError:
            return None
        return path if path.is_file() else None

    def _rebuild(self) -> None:
        name = self.currentText()
        shown = "" if self.collapsed else (name or "No profile")
        self.setText(shown.replace("&", "&&"))  # "&" would mark a shortcut key
        picture = self._picture(self.currentData())
        self.setIcon(avatar(name or "?", picture))
        self.setToolTip(f"Profile: {name}. Click to switch or add one." if name else "")
        self.menu_.clear()
        for action in self.group.actions():
            self.group.removeAction(action)
        for i, (text, data) in enumerate(self.items):
            action = QAction(avatar(text, self._picture(data), QSize(20, 20)),
                             text.replace("&", "&&"), self.menu_)  # fmt: skip
            action.setCheckable(True)
            action.setChecked(i == self.index)
            if i == self.index:
                bold = QFont(action.font())
                bold.setBold(True)
                action.setFont(bold)  # the profile in use stands out in the list
            action.setData(i)
            self.group.addAction(action)
            self.menu_.addAction(action)
            action.triggered.connect(self._picked)
        if self.items:
            self.menu_.addSeparator()
        new = QAction("New profile…", self.menu_)
        new.triggered.connect(self.new_requested)
        self.menu_.addAction(new)
        if self.items:
            change = QAction("Change picture…", self.menu_)
            change.triggered.connect(self.picture_requested)
            self.menu_.addAction(change)
            if picture is not None:
                remove = QAction("Remove picture", self.menu_)
                remove.triggered.connect(self.picture_removed)
                self.menu_.addAction(remove)

    def _picked(self) -> None:
        action = self.sender()
        if isinstance(action, QAction):
            self.setCurrentIndex(int(action.data()))
