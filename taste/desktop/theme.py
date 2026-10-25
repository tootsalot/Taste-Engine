"""The After Hours brand: colors, fonts, and the Qt stylesheet.

Dark-first by design. The app uses this palette whatever the Windows theme is.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImageReader,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parent / "assets"

BG = "#0E0D16"  # window ground
PANEL = "#14131F"  # sidebar and top bar
SURFACE = "#1B1929"  # cards
RAISED = "#232036"  # inputs, hovered rows
LINE = "#2E2B45"  # borders
EDGE = "#66618F"  # input edges: 3:1 against cards, so fields are findable
TEXT = "#EDEAF7"
MUTED = "#B4B0CC"
LILAC = "#B69CFF"  # anime and primary actions
CORAL = "#FF7A59"  # music
TEAL = "#6FD3C6"  # films and TV (lilac and coral stay anime and music only)
AMBER = "#F2C46D"  # books
SOURCE_COLORS = {"mal": LILAC, "lastfm": CORAL, "tmdb": TEAL, "hardcover": AMBER}
ON_ACCENT = "#120F1F"  # text on lilac or coral
DANGER = "#FF8A8A"
GOOD = "#86E0A8"

HEADING_FAMILY = "Space Grotesk"
BODY_FAMILY = "Inter"


def load_fonts() -> None:
    for name in ("SpaceGrotesk.ttf", "Inter.ttf"):
        QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / name))


def source_dot(source: str, size: int = 10) -> QIcon:
    """A small dot in a source's color, for the sync menu and the Sources rows."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SOURCE_COLORS[source]))
        painter.drawEllipse(size // 2, size // 2, size, size)
    finally:
        painter.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    return QIcon(str(ASSETS / "logo.svg"))


def _svg_pixmap(name: str, color: str, size: int) -> QPixmap:
    """A line icon from assets/icons, drawn in `color` (the SVGs use currentColor)."""
    text = (ASSETS / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    buffer = QBuffer()
    buffer.setData(QByteArray(text.replace("currentColor", color).encode("utf-8")))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"svg")
    reader.setScaledSize(QSize(size, size))
    return QPixmap.fromImage(reader.read())


def icon(name: str, color: str = MUTED, selected: str = LILAC) -> QIcon:
    """Muted normally, lilac on the current page's sidebar button (checked, focused or
    not). Drawn at 2x for sharp edges."""
    result = QIcon()
    result.addPixmap(_svg_pixmap(name, color, 48), QIcon.Mode.Normal)
    result.addPixmap(_svg_pixmap(name, TEXT, 48), QIcon.Mode.Active)
    lit = _svg_pixmap(name, selected, 48)
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
        result.addPixmap(lit, mode, QIcon.State.On)
    result.addPixmap(lit, QIcon.Mode.Selected)
    return result


def tmdb_logo(height: int) -> QPixmap:
    """TMDB's official logo (assets/tmdb.svg, from themoviedb.org/about/logos-attribution),
    drawn at `height` px and 2x for sharp edges."""
    reader = QImageReader(str(ASSETS / "tmdb.svg"))
    size = reader.size()
    width = round(size.width() * height / size.height()) if size.height() > 0 else height * 8
    reader.setScaledSize(QSize(width * 2, height * 2))
    pixmap = QPixmap.fromImage(reader.read())
    pixmap.setDevicePixelRatio(2)
    return pixmap


def number_font(size: int) -> QFont:
    """Big numbers use Inter: its figures are clearer than Space Grotesk's at this size."""
    font = QFont(BODY_FAMILY)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.ExtraBold)
    return font


def title_font(size: int) -> QFont:
    """Card titles: bold Inter, which fits long names in a narrow column."""
    return number_font(size)


def heading_font(size: int, weight: QFont.Weight = QFont.Weight.Bold) -> QFont:
    font = QFont(HEADING_FAMILY)
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def apply(app: QApplication) -> None:
    """Fonts, palette, and stylesheet for the whole app."""
    load_fonts()
    app.setWindowIcon(app_icon())
    body = QFont(BODY_FAMILY)
    body.setPixelSize(14)
    app.setFont(body)

    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, RAISED),
        (QPalette.ColorRole.AlternateBase, SURFACE),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, SURFACE),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, LILAC),
        (QPalette.ColorRole.HighlightedText, ON_ACCENT),
        (QPalette.ColorRole.ToolTipBase, SURFACE),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


CHEVRON_UP = (ASSETS / "chevron-up.svg").as_posix()
CHEVRON_DOWN = (ASSETS / "chevron-down.svg").as_posix()
CHEVRON_DOWN_DARK = (ASSETS / "chevron-down-dark.svg").as_posix()  # on lilac
ON_ACCENT_LINE = "rgba(18, 15, 31, 0.35)"  # the split in a lilac split button

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="error"] {{ color: {DANGER}; }}
QLabel[role="ok"] {{ color: {GOOD}; }}
QLabel[role="strong"] {{ font-weight: 700; }}
QFrame#SourceLine {{ background: {LINE}; }}
QToolButton#CardThumb {{ background: transparent; border: 2px solid transparent;
    border-radius: 8px; padding: 2px; }}
QToolButton#CardThumb:hover {{ border-color: {EDGE}; }}
QToolButton#CardThumb:checked, QToolButton#CardThumb:focus {{ border-color: {LILAC}; }}

#TopBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
#NavPanel {{ background: {PANEL}; border: none; border-right: 1px solid {LINE}; }}
QPushButton[role="nav"] {{ background: transparent; border: 1px solid transparent;
                           border-radius: 8px; padding: 0 0 0 9px; text-align: left;
                           color: {MUTED}; min-height: 0; }}
QLabel[role="nav-heading"] {{ color: {MUTED}; font-size: 11px; font-weight: 700;
                              letter-spacing: 1.5px; padding: 0 0 4px 11px; }}
QFrame[role="nav-line"] {{ background: {LINE}; margin: 6px 10px; }}
QPushButton[role="nav"]:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton[role="nav"]:checked {{ background: {RAISED}; color: {TEXT}; font-weight: 700; }}
QPushButton[role="nav"]:focus {{ border-color: {LILAC}; }}
QFrame[role="segmented"] {{ background: {BG}; border: 1px solid {LINE}; border-radius: 15px; }}
QPushButton[role="segment"] {{ background: transparent; border: 1px solid transparent;
                               border-radius: 13px; padding: 3px 14px; color: {MUTED};
                               min-height: 0; }}
QPushButton[role="segment"]:hover {{ color: {TEXT}; }}
QPushButton[role="segment"]:checked {{ background: transparent; color: {ON_ACCENT};
                                       font-weight: 700; }}
QPushButton[role="segment"]:focus {{ border-color: {TEXT}; }}

QFrame[role="card"] {{ background: {SURFACE}; border-radius: 14px; }}
QFrame[role="card"][raised="true"] {{ background: {RAISED}; border-radius: 10px; }}
QFrame[role="tile"] {{ background: transparent; border: 2px solid transparent;
                       border-radius: 14px; }}
QFrame[role="tile"]:hover {{ background: {SURFACE}; }}
QFrame[role="tile"]:focus {{ border-color: {EDGE}; }}
QFrame[role="tile"][selected="true"] {{ background: {SURFACE}; border-color: {LILAC}; }}
QFrame[role="tile"][selected="true"][accent="music"] {{ border-color: {CORAL}; }}
QFrame[role="tile"][selected="true"][accent="film"] {{ border-color: {TEAL}; }}
QLabel[role="tile-score"] {{ color: {LILAC}; font-weight: 800; font-size: 14px; }}
QLabel[role="tile-score"][accent="music"] {{ color: {CORAL}; }}
QLabel[role="tile-score"][accent="film"] {{ color: {TEAL}; }}
QFrame[role="step"] {{ background: {RAISED}; border-radius: 12px; }}
QFrame[role="trait"] {{ background: {RAISED}; border-radius: 10px; }}
QFrame[role="banner"] {{ background: {RAISED}; border: 1px solid {LILAC}; border-radius: 10px; }}
QLabel[role="badge"] {{ background: {RAISED}; color: {LILAC}; border-radius: 9px;
                        padding: 2px 10px; font-size: 12px; font-weight: 700; }}
QLabel[role="badge"][accent="books"] {{ background: {ON_ACCENT}; color: {AMBER};
                                       padding: 1px 6px; font-size: 11px; }}
QLabel[role="badge"][accent="screen"] {{ background: {ON_ACCENT}; color: {TEAL};
                                        padding: 1px 6px; font-size: 11px; }}
QLabel[role="badge"][accent="anime"] {{ background: {ON_ACCENT}; color: {LILAC};
                                       padding: 1px 6px; font-size: 11px; }}
QLabel[role="badge"][accent="music"] {{ background: {ON_ACCENT}; color: {CORAL};
                                       padding: 1px 6px; font-size: 11px; }}
QLabel[role="reason"] {{ color: {TEXT}; font-size: 13px; }}
QLabel[role="chip"] {{ background: {RAISED}; color: {TEXT}; border-radius: 8px;
                       padding: 5px 10px; font-size: 12px; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 12px; font-weight: 700;
                          letter-spacing: 1px; }}
QPushButton[role="link"] {{ background: transparent; border: none; color: {LILAC};
                            padding: 4px 0; font-weight: 700; }}
QPushButton[role="link"]:hover {{ color: #C7B3FF; text-decoration: underline; }}
QPushButton[role="link"]:focus {{ color: #C7B3FF; text-decoration: underline; }}
QPushButton[role="link"]:disabled {{ color: {MUTED}; }}

QPushButton {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 18px;
               padding: 8px 16px; min-height: 20px; }}
QPushButton:hover {{ border-color: {LILAC}; }}
QPushButton:focus {{ border-color: {LILAC}; background: {RAISED}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {RAISED}; }}
QPushButton[role="deck-arrow"] {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE};
                                  border-radius: 24px; min-width: 48px; max-width: 48px;
                                  min-height: 48px; max-height: 48px; padding: 0;
                                  font-size: 24px; }}
QPushButton[role="deck-arrow"]:hover {{ border-color: {LILAC}; }}
QPushButton[role="deck-arrow"]:focus {{ border: 2px solid {TEXT}; }}
QPushButton[role="deck-arrow"]:disabled {{ background: transparent; color: {LINE}; }}
QPushButton[role="primary"] {{ background: {LILAC}; color: {ON_ACCENT}; border: none;
                               font-weight: 700; }}
QPushButton[role="primary"]:hover {{ background: #C7B3FF; }}
QPushButton[role="primary"]:focus {{ border: 2px solid {TEXT}; padding: 6px 14px; }}
QPushButton[role="primary"]:disabled {{ background: {RAISED}; color: {MUTED}; }}
QPushButton[compact="true"] {{ padding: 3px 12px; min-height: 18px; border-radius: 13px; }}
QPushButton[role="primary"][compact="true"]:focus {{ padding: 1px 10px; }}
QPushButton[role="danger"] {{ background: transparent; color: {DANGER}; border-color: {DANGER}; }}
QToolButton#SyncButton {{ background: {LILAC}; color: {ON_ACCENT}; border: none;
    border-radius: 18px; padding: 8px 44px 8px 16px; min-height: 20px; font-weight: 700; }}
QToolButton#SyncButton:hover {{ background: #C7B3FF; }}
QToolButton#SyncButton:focus {{ border: 2px solid {TEXT}; padding: 6px 42px 6px 14px; }}
QToolButton#SyncButton:disabled {{ background: {RAISED}; color: {MUTED}; }}
QToolButton#SyncButton::menu-button {{ border: none; border-left: 1px solid {ON_ACCENT_LINE};
    width: 32px; border-top-right-radius: 18px; border-bottom-right-radius: 18px; }}
QToolButton#SyncButton::menu-button:hover {{ background: #D4C5FF; }}
QToolButton#SyncButton::menu-arrow {{ image: url("{CHEVRON_DOWN_DARK}");
    width: 10px; height: 10px; }}
QToolButton#SyncButton::menu-arrow:disabled {{ image: url("{CHEVRON_DOWN}"); }}
QToolButton#ExportButton {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 18px;
                            padding: 8px 32px 8px 16px; min-height: 20px; }}
QToolButton#ExportButton:hover {{ border-color: {LILAC}; }}
QToolButton#ExportButton:focus, QToolButton#ExportButton:open {{
    border-color: {LILAC}; background: {RAISED}; }}
QToolButton#ExportButton::menu-indicator {{ image: url("{CHEVRON_DOWN}"); width: 10px; height: 10px;
    subcontrol-origin: padding; subcontrol-position: center right; right: 14px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; padding: 6px 0; }}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 12px; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {RAISED}; border: 1px solid {EDGE}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {LILAC}; selection-color: {ON_ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus {{ border-color: {LILAC}; }}
QSpinBox, QDoubleSpinBox {{ padding-left: 8px; padding-right: 24px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 22px;
    border: none; border-left: 1px solid {LINE}; border-top-right-radius: 8px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 22px;
    border: none; border-left: 1px solid {LINE}; border-bottom-right-radius: 8px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {LINE}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{CHEVRON_UP}");
                                                width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{CHEVRON_DOWN}");
                                                    width: 10px; height: 10px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px;
                        border: none; border-left: 1px solid {LINE}; }}
QComboBox::down-arrow {{ image: url("{CHEVRON_DOWN}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {LINE};
                               selection-background-color: {RAISED}; selection-color: {TEXT}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px;
                        border: 1px solid {EDGE}; background: {RAISED}; }}
QCheckBox::indicator:checked {{ background: {LILAC}; border-color: {LILAC}; }}
QCheckBox:focus {{ color: {LILAC}; }}
QCheckBox::indicator:focus {{ border: 2px solid {TEXT}; }}

QFrame[role="strip"] {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#StatusDot {{ background: {MUTED}; border-radius: 4px; }}
QFrame#StatusDot[state="running"] {{ background: {LILAC}; }}
QFrame#StatusDot[state="ok"] {{ background: {GOOD}; }}
QFrame#StatusDot[state="error"] {{ background: {DANGER}; }}
QLabel[role="caption"] {{ color: {MUTED}; font-size: 12px; }}

QProgressBar {{ background: {RAISED}; border: none; border-radius: 4px; height: 8px;
                text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {CORAL}; border-radius: 4px; }}
QProgressBar#RecapProgress::chunk {{ background: {LILAC}; }}

QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; margin-right: 2px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {LILAC}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; border: none; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 3px; min-height: 32px;
                               margin: 0 2px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {EDGE}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 4px 2px 4px;
                         border: none; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 3px; min-width: 32px;
                                 margin: 2px 0; }}
QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {EDGE}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none;
                                              background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: transparent; }}

QStatusBar {{ background: {PANEL}; color: {MUTED}; border-top: 1px solid {LINE}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; }}
QMessageBox {{ background: {BG}; }}
"""
