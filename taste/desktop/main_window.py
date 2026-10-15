"""The main window: top bar with the profile picker, sidebar, and pages."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from taste import __version__, profiles, recap, recommend
from taste.activity import Period
from taste.desktop import motion, theme, ui_state
from taste.desktop.context import AppContext
from taste.desktop.dashboard import DashboardPage
from taste.desktop.dialogs import NewProfileDialog
from taste.desktop.for_you import ForYouPage
from taste.desktop.nav import NavList, nav_button
from taste.desktop.recap_page import RecapPage
from taste.desktop.reports_page import ReportsPage
from taste.desktop.settings_page import SettingsPage
from taste.desktop.widgets import heading, label
from taste.desktop.workers import RecapWorker
from taste.profiles import ProfileError
from taste.settings import SettingError

# The everyday pages, then the Reports section, then Settings at the bottom.
PAGES = ("Dashboard", "For You", "Rev", "Anime", "Music", "Books", "Films and TV", "Settings")
PAGE_ICONS = {
    "Dashboard": "dashboard",
    "For You": "for-you",
    "Anime": "report-anime",
    "Music": "report-music",
    "Books": "report-books",
    "Films and TV": "report-films",
    "Rev": "recap",
    "Settings": "settings",
}
# The sidebar rows under the Reports heading, and the report page each one shows.
REPORT_ROWS = {"Anime": "anime", "Music": "music", "Books": "books", "Films and TV": "screen"}
STATUS_MS = 8000  # how long a status bar message stays
NAV_WIDTH = 200
NAV_COLLAPSED_WIDTH = 60  # icons only


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

        # Sidebar (collapses to an icon rail) and pages
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QFrame()
        self.nav.setObjectName("NavPanel")
        nav_layout = QVBoxLayout(self.nav)
        nav_layout.setContentsMargins(8, 10, 8, 10)
        nav_layout.setSpacing(8)
        # The toggle is a page row without a page, so its icon lines up with theirs.
        self.nav_toggle = nav_button("sidebar")
        self.nav_toggle.clicked.connect(lambda: self.toggle_nav())
        nav_layout.addWidget(self.nav_toggle)
        self.sidebar = NavList(
            [(page, PAGE_ICONS[page]) for page in PAGES],
            bottom=PAGES.index("Settings"),
            groups={PAGES.index("Anime"): ("Reports", len(REPORT_ROWS))},
        )
        self.sidebar.currentRowChanged.connect(self._on_page_changed)
        nav_layout.addWidget(self.sidebar, 1)
        body.addWidget(self.nav)
        QShortcut(QKeySequence("Ctrl+B"), self, self.toggle_nav)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(self.ctx)
        self.for_you = ForYouPage(self.ctx)
        self.reports = ReportsPage(self.ctx)
        self.recap = RecapPage(self.ctx)
        self.settings = SettingsPage(self.ctx)
        self.empty = self._empty_state()
        self.auto_recap: RecapWorker | None = None
        self.banner_period: Period | None = None
        for page in (
            self.dashboard,
            self.for_you,
            self.reports,
            self.recap,
            self.settings,
            self.empty,
        ):
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self.dashboard.sync_finished.connect(lambda _: self.reports.refresh())
        self.dashboard.sync_finished.connect(lambda _: self.for_you.reload())
        self.dashboard.sync_finished.connect(lambda _: self.recap.refresh())
        self.dashboard.sync_finished.connect(lambda _: self.make_recaps())
        self.dashboard.banner.opened.connect(self._open_banner_recap)
        self.dashboard.recs_updated.connect(self.for_you.reload)
        self.dashboard.status_changed.connect(self.sidebar.set_status)
        self.dashboard.open_page.connect(self.show_page)
        self.settings.settings_saved.connect(self._after_settings_saved)
        self.settings.profile_deleted.connect(self._after_profile_deleted)
        self.settings.hardcover_connected.connect(lambda: self.dashboard.start_sync("hardcover"))
        # A sync and a recommendations refresh never run at the same time: both write
        # the run log, and a new run marks any other running one as interrupted.
        self.settings.is_busy = self.is_busy
        self.dashboard.is_blocked = self.for_you.is_refreshing
        self.for_you.is_blocked = self.dashboard.is_syncing
        for signal in (
            self.dashboard.sync_started,
            self.dashboard.sync_finished,
            self.for_you.refresh_started,
            self.for_you.refresh_finished,
        ):
            signal.connect(self._update_busy)

        # The version line stays; a message takes its place for a few seconds. Qt drew
        # both at once over each other, so the line hides while a message shows.
        self.version_label = label(
            f"Taste Engine {__version__}. Your data stays on this computer.", role="muted"
        )
        self.statusBar().addWidget(self.version_label)
        self.statusBar().messageChanged.connect(
            lambda text: self.version_label.setVisible(not text)
        )
        self.nav_collapsed = False
        self.set_nav_collapsed(bool(ui_state.load().get("nav_collapsed")), remember=False)
        self.reload_profiles()
        # Start keyboard focus on the page list, not on whichever button comes first
        # (a focus ring on "New profile" at launch looks like a pending action).
        self.sidebar.focus_current()

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

    # -- sidebar ------------------------------------------------------------

    def toggle_nav(self) -> None:
        self.set_nav_collapsed(not self.nav_collapsed)

    def set_nav_collapsed(self, collapsed: bool, remember: bool = True) -> None:
        """Icons only (with tooltips) or icons and labels. Ctrl+B toggles it."""
        self.nav_collapsed = collapsed
        motion.resize_width(self.nav, NAV_COLLAPSED_WIDTH if collapsed else NAV_WIDTH)
        self.sidebar.set_collapsed(collapsed)
        self.nav_toggle.setToolTip(
            "Show page names (Ctrl+B)" if collapsed else "Collapse the sidebar (Ctrl+B)"
        )
        if remember:
            ui_state.save(nav_collapsed=collapsed)

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
        self.profile_picker.setEnabled(has_profile and not self.is_busy())
        if not has_profile:
            self.stack.setCurrentWidget(self.empty)
            return
        for page in (self.dashboard, self.for_you, self.reports, self.recap, self.settings):
            page.set_profile(pid)
        self.make_recaps()
        if self.sidebar.currentRow() < 0:
            self.sidebar.setCurrentRow(0)
        else:
            self._on_page_changed(self.sidebar.currentRow())

    def _on_page_changed(self, row: int) -> None:
        if self.current_profile() is None or row < 0:
            return
        name = PAGES[row]
        target = self.page_for(name)
        if name in REPORT_ROWS:
            if self.stack.currentWidget() is self.reports:
                self.reports.show_section(REPORT_ROWS[name])  # fades on its own
                return
            self.reports.show_section(REPORT_ROWS[name])
        if self.stack.currentWidget() is not target:
            motion.crossfade(self.stack, lambda: self.stack.setCurrentWidget(target))

    def page_for(self, name: str) -> QWidget:
        """The page widget a sidebar row shows."""
        if name in REPORT_ROWS:
            return self.reports
        return {
            "Dashboard": self.dashboard,
            "For You": self.for_you,
            "Rev": self.recap,
            "Settings": self.settings,
        }[name]

    # -- recaps -------------------------------------------------------------

    def make_recaps(self) -> RecapWorker | None:
        """Make or update the recaps that are due, in the background (taste.recap.due).
        Runs on launch, on switching profiles, and after each sync."""
        pid = self.current_profile()
        if pid is None or self.dashboard.is_syncing():
            return None
        if self.auto_recap is not None and self.auto_recap.isRunning():
            return None
        worker = RecapWorker(pid, self.ctx.art.cache, self.ctx.now())
        worker.made.connect(lambda made, p=pid: self._recaps_made(p))
        self.auto_recap = worker
        self.ctx.track(worker)
        worker.start()
        return worker

    def _recaps_made(self, profile_id: str) -> None:
        """Announce a recap made for the first time, once."""
        if profile_id != self.current_profile():
            return
        conn = self.ctx.connect(profile_id)
        try:
            waiting = recap.unannounced(conn)
            for period in waiting:
                recap.mark_announced(conn, period)
        finally:
            conn.close()
        if waiting:
            # A year's recap outranks its December's: both come in January.
            self.banner_period = next((p for p in waiting if p.kind == "year"), waiting[0])
            self.dashboard.banner.show_text(recap.banner_text(self.banner_period))
            self.recap.refresh()

    def _open_banner_recap(self) -> None:
        if self.banner_period is not None:
            self.show_page("Rev")
            self.recap.show_period(self.banner_period)

    def show_page(self, name: str) -> None:
        name = "Anime" if name == "Reports" else name
        if name in PAGES and self.current_profile() is not None:
            self.sidebar.setCurrentRow(PAGES.index(name))

    def is_busy(self) -> bool:
        return self.dashboard.is_syncing() or self.for_you.is_refreshing()

    def _update_busy(self, *_args) -> None:
        self.dashboard.set_blocked(self.for_you.is_refreshing())
        self.for_you.set_blocked(self.dashboard.is_syncing())
        self.profile_picker.setEnabled(self.current_profile() is not None and not self.is_busy())

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
        self.say(f"Created {profile_id}. Add your usernames and API keys.")
        return True

    def say(self, text: str) -> None:
        self.statusBar().showMessage(text, STATUS_MS)

    def _after_settings_saved(self, changed: list[str]) -> None:
        pid = self.current_profile()
        index = self.profile_picker.currentIndex()
        self.profile_picker.setItemText(index, profiles.display_name(pid))
        self.dashboard.refresh()
        self.reports.refresh()
        if set(changed) & set(recommend.REC_SETTINGS):
            # Plan to Watch, minimum raters, and so on change the lists: recompute from
            # what's cached (no network, about a second, in the background).
            self.for_you.start_refresh(fetch=False)

    def _after_profile_deleted(self, profile_id: str) -> None:
        self.say(f"Deleted profile {profile_id}.")
        self.reload_profiles()

    # -- window -------------------------------------------------------------

    def bring_to_front(self) -> None:
        """Called when the app is opened again while already running."""
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def _confirm_quit_during_sync(self) -> bool:
        what = "A sync" if self.dashboard.is_syncing() else "A recommendations refresh"
        answer = QMessageBox.question(
            self,
            f"{what} is running",
            "Quit anyway? It stops, and the next one picks up where it left off.",
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # Only long work is worth asking about; a recompute from the cache takes a second.
        long_work = self.dashboard.is_syncing() or self.for_you.is_fetching()
        if long_work and not self._confirm_quit_during_sync():
            event.ignore()
            return
        # Stops after the page in flight; committed pages stay and the next run resumes.
        for worker in (self.dashboard.worker, self.for_you.worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
                worker.wait(15000)
        self.ctx.shutdown()
        event.accept()
