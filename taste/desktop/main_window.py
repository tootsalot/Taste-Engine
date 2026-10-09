"""The main window: top bar with the profile picker, sidebar, and pages."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from taste import __version__, profiles
from taste.desktop import theme
from taste.desktop.context import AppContext
from taste.desktop.dashboard import DashboardPage
from taste.desktop.dialogs import NewProfileDialog
from taste.desktop.reports_page import ReportsPage
from taste.desktop.settings_page import SettingsPage
from taste.desktop.widgets import heading, label
from taste.profiles import ProfileError
from taste.settings import SettingError

PAGES = ("Dashboard", "Reports", "Settings")


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext | None = None) -> None:
        super().__init__()
        self.ctx = ctx or AppContext()
        self.setWindowTitle("Taste Engine")
        self.setWindowIcon(theme.app_icon())
        self.resize(1180, 820)
        self.setMinimumSize(900, 620)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)

        # Top bar
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(18, 10, 18, 10)
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(QSize(30, 30)))
        bar_layout.addWidget(logo)
        bar_layout.addWidget(heading("Taste Engine", 20))
        bar_layout.addStretch()
        bar_layout.addWidget(label("Profile", role="muted"))
        self.profile_picker = QComboBox()
        self.profile_picker.setMinimumWidth(200)
        self.profile_picker.currentIndexChanged.connect(self._on_profile_changed)
        bar_layout.addWidget(self.profile_picker)
        new_button = QPushButton("New profile")
        new_button.clicked.connect(lambda: self.new_profile())
        bar_layout.addWidget(new_button)
        outer.addWidget(bar)

        # Sidebar and pages
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(190)
        self.sidebar.addItems(PAGES)
        self.sidebar.setSpacing(2)
        for i in range(self.sidebar.count()):
            self.sidebar.item(i).setSizeHint(QSize(0, 40))
        self.sidebar.currentRowChanged.connect(self._on_page_changed)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(self.ctx)
        self.reports = ReportsPage(self.ctx)
        self.settings = SettingsPage(self.ctx)
        self.empty = self._empty_state()
        for page in (self.dashboard, self.reports, self.settings, self.empty):
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self.dashboard.sync_finished.connect(lambda _: self.reports.refresh())
        self.settings.settings_saved.connect(self._after_settings_saved)
        self.settings.profile_deleted.connect(self._after_profile_deleted)
        self.settings.is_busy = self.dashboard.is_syncing

        self.statusBar().showMessage(
            f"Taste Engine {__version__}. Your data stays on this computer."
        )
        self.reload_profiles()

    def _empty_state(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading("Welcome to Taste Engine", 30), 0, Qt.AlignmentFlag.AlignHCenter)
        text = label(
            "Create a profile to get started. Each profile keeps its own API keys, "
            "settings, and data on this computer.",
            role="muted",
            wrap=True,
        )
        text.setMaximumWidth(460)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text, 0, Qt.AlignmentFlag.AlignHCenter)
        button = QPushButton("Create a profile")
        button.setProperty("role", "primary")
        button.clicked.connect(lambda: self.new_profile())
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    # -- profiles -----------------------------------------------------------

    def current_profile(self) -> str | None:
        return self.profile_picker.currentData()

    def reload_profiles(self, select: str | None = None) -> None:
        found = profiles.list_profiles()
        select = select or self.current_profile()
        self.profile_picker.blockSignals(True)
        self.profile_picker.clear()
        for p in found:
            self.profile_picker.addItem(p.display_name, p.profile_id)
        index = self.profile_picker.findData(select)
        self.profile_picker.setCurrentIndex(index if index >= 0 else 0)
        self.profile_picker.blockSignals(False)
        self._on_profile_changed()

    def _on_profile_changed(self, _index: int = 0) -> None:
        pid = self.current_profile()
        has_profile = pid is not None
        self.sidebar.setEnabled(has_profile)
        self.profile_picker.setEnabled(has_profile and not self.dashboard.is_syncing())
        if not has_profile:
            self.stack.setCurrentWidget(self.empty)
            return
        for page in (self.dashboard, self.reports, self.settings):
            page.set_profile(pid)
        if self.sidebar.currentRow() < 0:
            self.sidebar.setCurrentRow(0)
        else:
            self._on_page_changed(self.sidebar.currentRow())

    def _on_page_changed(self, row: int) -> None:
        if self.current_profile() is None or row < 0:
            return
        self.stack.setCurrentIndex(row)

    def _ask_new_profile(self) -> tuple[str, str] | None:
        dialog = NewProfileDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()

    def new_profile(self, values: tuple[str, str] | None = None) -> bool:
        values = values or self._ask_new_profile()
        if not values:
            return False
        profile_id, name = values
        try:
            profiles.create(profile_id, name)
        except (ProfileError, SettingError) as exc:
            QMessageBox.warning(self, "Couldn't create the profile", str(exc))
            return False
        self.reload_profiles(select=profile_id)
        self.sidebar.setCurrentRow(PAGES.index("Settings"))  # first stop: keys and usernames
        self.statusBar().showMessage(f"Created {profile_id}. Add your usernames and API keys.")
        return True

    def _after_settings_saved(self) -> None:
        pid = self.current_profile()
        index = self.profile_picker.currentIndex()
        self.profile_picker.setItemText(index, profiles.display_name(pid))
        self.dashboard.refresh()
        self.reports.refresh()

    def _after_profile_deleted(self, profile_id: str) -> None:
        self.statusBar().showMessage(f"Deleted profile {profile_id}.")
        self.reload_profiles()

    # -- window -------------------------------------------------------------

    def bring_to_front(self) -> None:
        """Called when the app is opened again while already running."""
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def _confirm_quit_during_sync(self) -> bool:
        answer = QMessageBox.question(
            self,
            "A sync is running",
            "Quit anyway? The sync stops, and the next sync picks up where it left off.",
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.dashboard.is_syncing():
            if not self._confirm_quit_during_sync():
                event.ignore()
                return
            # Stops after the page in flight; committed pages stay and the next sync resumes.
            self.dashboard.worker.cancel()
            self.dashboard.worker.wait(15000)
        self.ctx.shutdown()
        event.accept()
