"""The After Hours brand: colors, fonts, and the Qt stylesheet.

Dark-first by design. The app uses this palette whatever the Windows theme is.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPalette
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
ON_ACCENT = "#120F1F"  # text on lilac or coral
DANGER = "#FF8A8A"
GOOD = "#86E0A8"

HEADING_FAMILY = "Space Grotesk"
BODY_FAMILY = "Inter"


def load_fonts() -> None:
    for name in ("SpaceGrotesk.ttf", "Inter.ttf"):
        QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / name))


def app_icon() -> QIcon:
    return QIcon(str(ASSETS / "logo.svg"))


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

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="scope"] {{ color: {CORAL}; }}
QLabel[role="error"] {{ color: {DANGER}; }}
QLabel[role="ok"] {{ color: {GOOD}; }}
QLabel[role="big"] {{ font-size: 30px; font-weight: 800; }}

#TopBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
#Sidebar {{ background: {PANEL}; border: none; border-right: 1px solid {LINE};
            padding: 12px 8px; outline: 0; }}
#Sidebar::item {{ padding-left: 12px; border-radius: 8px; color: {MUTED}; }}
#Sidebar::item:selected {{ background: {RAISED}; color: {TEXT}; font-weight: 700; }}
#Sidebar::item:hover:!selected {{ background: {SURFACE}; }}

QFrame[role="card"] {{ background: {SURFACE}; border-radius: 14px; }}
QLabel[role="badge"] {{ background: {RAISED}; color: {LILAC}; border-radius: 9px;
                        padding: 2px 10px; font-size: 12px; font-weight: 700; }}
QLabel[role="reason"] {{ color: {TEXT}; font-size: 13px; }}
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
QPushButton[role="primary"] {{ background: {LILAC}; color: {ON_ACCENT}; border: none;
                               font-weight: 700; }}
QPushButton[role="primary"]:hover {{ background: #C7B3FF; }}
QPushButton[role="primary"]:focus {{ border: 2px solid {TEXT}; padding: 6px 14px; }}
QPushButton[role="primary"]:disabled {{ background: {RAISED}; color: {MUTED}; }}
QPushButton[role="danger"] {{ background: transparent; color: {DANGER}; border-color: {DANGER}; }}

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

QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; margin-right: 2px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {LILAC}; }}

QTableView {{ background: {SURFACE}; alternate-background-color: #1F1D2F; border: none;
              gridline-color: {LINE}; selection-background-color: {RAISED};
              selection-color: {TEXT}; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none;
                        border-bottom: 1px solid {LINE}; padding: 6px 8px; font-weight: 700; }}

QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QStatusBar {{ background: {PANEL}; color: {MUTED}; border-top: 1px solid {LINE}; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; }}
QMessageBox {{ background: {BG}; }}
"""
