"""The main window: the sidebar (profile switcher on top, then the pages) and the pages.
No top bar and no status bar since 2.4; short messages show as a toast."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from taste import local_time, profiles, recap, recommend, settings, source_status
from taste.activity import Period
from taste.desktop import motion, theme, ui_state
from taste.desktop.context import AppContext
from taste.desktop.dashboard import DashboardPage
from taste.desktop.dialogs import ModelChoiceDialog, NewProfileDialog
from taste.desktop.for_you import ForYouPage
from taste.desktop.nav import NavList, nav_button
from taste.desktop.profile_switcher import ProfileSwitcher, save_picture
from taste.desktop.recap_page import RecapPage
from taste.desktop.reports_page import ReportsPage
from taste.desktop.settings_page import SettingsPage
from taste.desktop.widgets import heading, label
from taste.desktop.workers import ModelCheckWorker, RecapWorker
from taste.profiles import ProfileError
from taste.recommend import comparison
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
STATUS_MS = 8000  # how long a toast message stays
TOAST_WIDTH = 520
LAUNCH_SYNC_DELAY_MS = 1500
NAV_WIDTH = 220
NAV_COLLAPSED_WIDTH = 60  # icons only


class MainWindow(QMainWindow):
    CLOSE_WAIT_MS = 15000  # how long closing waits for a cancelled sync before hiding

    def __init__(self, ctx: AppContext | None = None) -> None:
        super().__init__()
        self.ctx = ctx or AppContext()
        self._closing = False  # the close was confirmed; it finishes once threads end
        self._first_paint = False
        self._launch_sync_decided = False
        self._starting_launch_sync = False
        self._model_generation = 0
        self.model_worker: ModelCheckWorker | None = None
        self.model_dialog: ModelChoiceDialog | None = None
        self._model_offer: tuple[ModelCheckWorker, dict] | None = None
        self._model_seen: set[tuple[Path, tuple[int, int] | None, int]] = set()
        self._pending_model_choice: str | None = None
        self.setWindowTitle("Taste Engine")
        self.setWindowIcon(theme.app_icon())
        self.resize(1180, 820)
        self.setMinimumSize(900, 620)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)

        # Sidebar (collapses to an icon rail) and pages
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QFrame()
        self.nav.setObjectName("NavPanel")
        nav_layout = QVBoxLayout(self.nav)
        nav_layout.setContentsMargins(10, 14, 10, 10)
        nav_layout.setSpacing(8)
        # The profile, switched or added from its menu (2.4: no top bar).
        self.profile_picker = ProfileSwitcher()
        self.profile_picker.currentIndexChanged.connect(self._on_profile_changed)
        self.profile_picker.new_requested.connect(self._new_profile_from_menu)
        self.profile_picker.picture_requested.connect(self._picture_from_menu)
        self.profile_picker.picture_removed.connect(self.remove_picture)
        nav_layout.addWidget(self.profile_picker)
        rule = QFrame()
        rule.setProperty("role", "nav-line")
        rule.setFixedHeight(1)
        nav_layout.addWidget(rule)
        # The toggle is a page row without a page, so its icon lines up with theirs.
        self.nav_toggle = nav_button("sidebar")
        # Ctrl+B does this from anywhere; out of the Tab order, focus never lands (and
        # rings) here when the page list was disabled without a profile (2.4).
        self.nav_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self.past_worker: RecapWorker | None = None  # past recaps, made in one go (2.3)
        self._past_waiting: list[Period] | None = None  # until the automatic check ends
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

        self.dashboard.sync_finished.connect(self._after_sync)
        self.dashboard.banner.opened.connect(self._open_banner_recap)
        self.dashboard.past.accepted.connect(self._make_past_recaps)
        self.dashboard.past.declined.connect(self._decline_past_recaps)
        self.dashboard.past.opened.connect(self._open_rev)
        self.dashboard.recs_updated.connect(self.for_you.refresh_later)
        self.dashboard.status_changed.connect(self.sidebar.set_status)
        self.dashboard.open_page.connect(self.show_page)
        self.settings.settings_saved.connect(self._after_settings_saved)
        self.settings.profile_deleted.connect(self._after_profile_deleted)
        self.settings.profile_restored.connect(self._after_profile_restored)
        self.settings.message.connect(self.say)
        self.settings.transfer_busy.connect(self._update_busy)
        sources = self.settings.sources
        sources.hardcover_connected.connect(lambda: self.dashboard.start_sync("hardcover"))
        sources.sync_requested.connect(self.dashboard.start_sync)
        sources.changed.connect(self.dashboard.refresh_status)  # the sync menu and status
        # A sync and a recommendations refresh never run at the same time: both write
        # the run log, and a new run marks any other running one as interrupted.
        self.settings.is_busy = self.is_busy
        self.dashboard.is_blocked = self._blocks_sync
        self.for_you.is_blocked = self._blocks_recs
        for signal in (
            self.dashboard.sync_started,
            self.dashboard.sync_finished,
            self.for_you.refresh_started,
            self.for_you.refresh_finished,
        ):
            signal.connect(self._update_busy)

        # Short messages ("Created me. ...") float over the bottom of the page for a few
        # seconds. In its parent before it's ever shown, so it never opens as a window.
        self.toast = label("", role="toast", wrap=True)
        self.toast.setParent(root)
        self.toast.setMaximumWidth(TOAST_WIDTH)
        self.toast.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast.hide)
        self.nav_collapsed = False
        self.set_nav_collapsed(bool(ui_state.load().get("nav_collapsed")), remember=False)
        self.reload_profiles()
        # "Sync when the app opens": a moment after the window is up, so it paints first.
        QTimer.singleShot(LAUNCH_SYNC_DELAY_MS, self.sync_on_launch)
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
        self.profile_picker.set_collapsed(collapsed)
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
        self._cancel_model_check()
        pid = self.current_profile()
        has_profile = pid is not None
        self.sidebar.setEnabled(has_profile)
        self.profile_picker.setEnabled(has_profile and not self.is_busy())
        if not has_profile:
            self.stack.setCurrentWidget(self.empty)
            return
        self._apply_preferences(pid)
        if self.past_worker is not None and self.past_worker.isRunning():
            self.past_worker.cancel()  # another profile's: it goes on when that one's back
        self._past_waiting = None
        for page in (self.dashboard, self.for_you, self.reports, self.recap, self.settings):
            page.set_profile(pid)
        self.make_recaps()
        self.offer_past_recaps()
        if self.sidebar.currentRow() < 0:
            self.sidebar.setCurrentRow(0)
        else:
            self._on_page_changed(self.sidebar.currentRow())
        if self.focusWidget() is self.profile_picker and not self.profile_picker.by_keyboard:
            # Without a profile the page list was disabled, so focus fell to the switcher
            # and would sit ringed there; the page list takes it back.
            self.sidebar.focus_current()
        QTimer.singleShot(0, self._maybe_check_rating_model)

    def _on_page_changed(self, row: int) -> None:
        if self.current_profile() is None or row < 0:
            return
        name = PAGES[row]
        target = self.page_for(name)
        if name not in REPORT_ROWS and hasattr(target, "refresh_if_stale"):
            target.refresh_if_stale()  # a sync ran while it was off screen
        if name in REPORT_ROWS:  # show_section catches up, drawing only that section
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

    def _after_sync(self, _results: dict) -> None:
        """Pages off screen redraw when they're next shown, not now (step 5)."""
        for page in (self.reports, self.for_you, self.recap):
            page.refresh_later()
        self.make_recaps()
        self.offer_past_recaps()
        self.settings.refresh_status()

    # -- recaps -------------------------------------------------------------

    def make_recaps(self) -> RecapWorker | None:
        """Make or update the recaps that are due, in the background (taste.recap.due).
        Runs on launch, on switching profiles, and after each sync."""
        pid = self.current_profile()
        if pid is None or self.dashboard.is_syncing():
            return None
        if self.auto_recap is not None and self.auto_recap.isRunning():
            return None
        if self.past_worker is not None and self.past_worker.isRunning():
            return None  # never two drawings of one recap at once; it runs after the batch
        worker = RecapWorker(pid, self.ctx.art.cache, self.ctx.now())
        worker.made.connect(self._on_recaps_made)  # a bound method: Qt drops it with the window
        self.auto_recap = worker
        self.ctx.track(worker)
        worker.start()
        return worker

    def _on_recaps_made(self, _made: list) -> None:
        worker = self.sender()
        if worker is not None:
            self._recaps_made(worker.profile_id)

    def offer_past_recaps(self) -> None:
        """The first launch (or the first sync of a new profile) offers recaps for every
        past month and year (2.3). A batch the app closed in the middle of goes on."""
        pid = self.current_profile()
        if pid is None or (self.past_worker is not None and self.past_worker.isRunning()):
            return
        conn = self.ctx.connect(pid)
        try:
            state = recap.past_state(conn)
            periods = recap.past_periods(conn, self.ctx.now()) if state in ("", "making") else []
            if state == "making" and not periods:
                recap.set_past_state(conn, "done")
        finally:
            conn.close()
        if state == "making" and periods:
            self._start_past(periods)
        elif state == "" and periods:
            self.dashboard.past.offer(recap.offer_text(periods))

    def _make_past_recaps(self) -> None:
        pid = self.current_profile()
        if pid is None:
            return
        conn = self.ctx.connect(pid)
        try:
            recap.set_past_state(conn, "making")
            periods = recap.past_periods(conn, self.ctx.now())
        finally:
            conn.close()
        self._start_past(periods)

    def _decline_past_recaps(self) -> None:
        pid = self.current_profile()
        if pid is None:
            return
        conn = self.ctx.connect(pid)
        try:
            recap.set_past_state(conn, "declined")  # each month is still a click away on Rev
        finally:
            conn.close()

    def _start_past(self, periods: list[Period]) -> None:
        self.dashboard.past.making(0, len(periods))
        if self.auto_recap is not None and self.auto_recap.isRunning():
            # One drawing at a time: start once the automatic check is done.
            self._past_waiting = periods
            self.auto_recap.finished.connect(self._start_waiting_past)
            return
        worker = RecapWorker(
            self.current_profile(), self.ctx.art.cache, self.ctx.now(), batch=periods
        )
        worker.progress.connect(self.dashboard.past.making)
        worker.finished.connect(self._past_finished)
        self.past_worker = worker
        self.ctx.track(worker)
        worker.start()

    def _start_waiting_past(self) -> None:
        periods, self._past_waiting = self._past_waiting, None
        if periods:
            self._start_past(periods)

    def _past_finished(self) -> None:
        worker = self.sender()
        if worker is None or worker.profile_id != self.current_profile():
            return  # another profile's batch, stopped by the switch
        conn = self.ctx.connect(worker.profile_id)
        try:
            done = recap.past_state(conn) == "done"
        finally:
            conn.close()
        if done:
            self.dashboard.past.finished(worker.made_count)
        else:
            self.dashboard.past.hide_notice()  # stopped; it goes on next time
        self.recap.refresh_later()

    def _open_rev(self) -> None:
        self.sidebar.setCurrentRow(PAGES.index("Rev"))

    def _recaps_made(self, profile_id: str) -> None:
        """Announce a recap made for the first time, once."""
        if self._closing or profile_id != self.current_profile() or not profiles.exists(profile_id):
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

    def sync_on_launch(self) -> bool:
        """Start a Sync everything if the profile asks for one on launch: every time, or
        when the last sync was a day ago or more. Returns whether it started."""
        self._starting_launch_sync = True
        try:
            return self._start_launch_sync()
        finally:
            self._starting_launch_sync = False
            self._launch_sync_decided = True
            QTimer.singleShot(0, self._maybe_check_rating_model)

    def _start_launch_sync(self) -> bool:
        pid = self.current_profile()
        if self._closing or pid is None or not profiles.exists(pid) or self.is_busy():
            return False
        conn = self.ctx.connect(pid)
        try:
            choice = settings.get(conn, "sync_on_launch")
            sources = source_status.load(conn, pid, self.ctx.store)
        finally:
            conn.close()
        if choice == "off" or not any(s.connected for s in sources):
            return False
        if choice == "daily":
            latest = max((s.last_sync for s in sources if s.last_sync), default=None)
            if latest:
                when = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - when < timedelta(days=1):
                    return False
        self.dashboard.start_sync("all")
        return self.dashboard.is_syncing()

    def _blocks_sync(self) -> bool:
        return (
            self.for_you.is_refreshing()
            or self.settings.is_transferring()
            or self.model_worker is not None
        )

    def _blocks_recs(self) -> bool:
        return (
            self.dashboard.is_syncing()
            or self.settings.is_transferring()
            or self.model_worker is not None
        )

    def is_busy(self) -> bool:
        return (
            self.dashboard.is_syncing()
            or self.for_you.is_refreshing()
            or self.settings.is_transferring()
            or self.model_worker is not None
        )

    def _update_busy(self, *_args) -> None:
        self.dashboard.set_blocked(self._blocks_sync())
        self.for_you.set_blocked(self._blocks_recs())
        self.settings.sources.set_busy(self.is_busy())
        enabled = self.current_profile() is not None and not self.is_busy()
        had_focus = self.profile_picker.hasFocus()
        self.profile_picker.setEnabled(enabled)
        if had_focus and not enabled:
            # Qt would hand focus to the next control, the sidebar toggle, and ring it.
            self.sidebar.focus_current()
        QTimer.singleShot(0, self._maybe_check_rating_model)

    # -- first-launch rating model choice -----------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        if not self._first_paint:
            self._first_paint = True
            QTimer.singleShot(0, self._maybe_check_rating_model)

    def _dismiss_model_dialog(self) -> None:
        dialog, self.model_dialog = self.model_dialog, None
        if dialog is not None:
            dialog.reject()
            dialog.deleteLater()

    def _cancel_model_check(self) -> None:
        self._model_generation += 1
        worker, self.model_worker = self.model_worker, None
        if worker is not None:
            worker.cancel()
        self._model_offer = None
        self._pending_model_choice = None
        self._dismiss_model_dialog()
        self._update_busy()

    def _current_model_check(self, worker: ModelCheckWorker) -> bool:
        pid = self.current_profile()
        return (
            not self._closing
            and not worker.is_cancelled()
            and worker.generation == self._model_generation
            and pid == worker.profile_id
            and worker.version == recommend.MODEL_SET_VERSION
            and profiles.exists(pid)
            and profiles.db_path(pid).resolve() == worker.path
            and worker.identity() == worker.file_identity
        )

    def _maybe_check_rating_model(self) -> None:
        if (
            self._closing
            or not self._first_paint
            or not self.isVisible()
            or not self._launch_sync_decided
            or self._starting_launch_sync
            or self.is_busy()
            or self.model_dialog is not None
        ):
            return
        if self._model_offer is not None:
            self.ask_rating_model(answer=self._pending_model_choice)
            return
        pid = self.current_profile()
        if pid is None or not profiles.exists(pid):
            return
        path = profiles.db_path(pid).resolve()
        try:
            info = path.stat()
        except FileNotFoundError:
            return
        key = (path, (info.st_dev, info.st_ino), recommend.MODEL_SET_VERSION)
        if key in self._model_seen:
            return
        conn = self.ctx.connect(pid)
        try:
            if comparison.asked_version(conn) >= recommend.MODEL_SET_VERSION:
                return
        finally:
            conn.close()
        worker = ModelCheckWorker(pid)
        worker.generation = self._model_generation
        worker.message.connect(self._model_check_message)
        worker.finished.connect(self._model_check_finished)
        self.model_worker = worker
        self.ctx.track(worker)
        worker.start()
        self._update_busy()

    def _model_check_message(self, text: str) -> None:
        worker = self.sender()
        if worker is not None and worker is self.model_worker and self._current_model_check(worker):
            self.say(text)

    def _model_check_finished(self) -> None:
        worker = self.sender()
        if worker is None or worker is not self.model_worker:
            return
        self.model_worker = None
        if self._current_model_check(worker) and worker.result is not None:
            if worker.result["count"] == 0:
                conn = self.ctx.connect(worker.profile_id)
                try:
                    comparison.mark_asked(conn, version=worker.version)
                finally:
                    conn.close()
                self._model_seen.add((worker.path, worker.file_identity, worker.version))
            else:
                self._model_offer = worker, worker.result
        else:
            # A failed check gets another chance next launch, without a retry loop.
            self._model_seen.add((worker.path, worker.file_identity, worker.version))
        self._update_busy()

    def ask_rating_model(self, answer: str | None = None) -> bool:
        """Open the startup choice; an explicit answer bypasses the dialog for tests."""
        if self._model_offer is None:
            return False
        worker, checks = self._model_offer
        if not self._current_model_check(worker):
            self._cancel_model_check()
            return False
        if answer is not None and answer not in (*settings.REC_MODELS, "later"):
            raise ValueError("Unknown rating model choice")
        if answer is None:
            if self.model_dialog is not None or self.is_busy():
                return False
            conn = self.ctx.connect(worker.profile_id)
            try:
                selected = settings.get(conn, "rec_model_anime")
            finally:
                conn.close()
            dialog = ModelChoiceDialog(checks, selected=selected, parent=self)
            self.model_dialog = dialog
            self._model_seen.add((worker.path, worker.file_identity, worker.version))
            dialog.accepted.connect(self._model_choice_accepted)
            dialog.rejected.connect(self._model_choice_later)
            dialog.open()
            return True
        self._dismiss_model_dialog()
        self._model_seen.add((worker.path, worker.file_identity, worker.version))
        if answer != "later" and self.is_busy():
            self._pending_model_choice = answer
            return True
        self._model_offer = None
        self._pending_model_choice = None
        if answer != "later":
            conn = self.ctx.connect(worker.profile_id)
            try:
                changed = comparison.apply_choice(conn, answer, version=worker.version)
            finally:
                conn.close()
            # Choosing the existing default still refreshes the saved suggestions.
            keys = {f"rec_model_{medium}" for medium in ("anime", "film", "tv", "book")}
            self._after_settings_saved(sorted(changed | keys))
        return True

    def _model_choice_accepted(self) -> None:
        dialog = self.sender()
        if dialog is not None and dialog is self.model_dialog:
            self.ask_rating_model(answer=dialog.choice())

    def _model_choice_later(self) -> None:
        if self.sender() is self.model_dialog:
            self.ask_rating_model(answer="later")

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
        """A short message near the bottom of the page, gone after STATUS_MS."""
        self.toast.setText(text)
        self.toast.adjustSize()
        self._place_toast()
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start(STATUS_MS)

    def _place_toast(self) -> None:
        area = self.stack.geometry()
        self.toast.move(
            area.x() + (area.width() - self.toast.width()) // 2,
            area.bottom() - self.toast.height() - 24,
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        if not self.toast.isHidden():
            self._place_toast()

    def _new_profile_from_menu(self) -> None:
        self.new_profile()

    def _picture_from_menu(self) -> None:
        self.change_picture()

    def change_picture(self, path: str | None = None) -> bool:
        """Use an image as the profile's picture (cropped to a square, kept beside its
        database). `path` skips the file dialog (tests)."""
        pid = self.current_profile()
        if pid is None:
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Choose a profile picture",
                str(Path.home() / "Pictures"),
                "Pictures (*.png *.jpg *.jpeg *.webp *.gif *.bmp)",
            )
        if not path:
            return False
        problem = save_picture(path, profiles.picture_path(pid))
        if problem:
            self.say(problem)
            return False
        self.profile_picker.refresh_picture()
        self.say("Picture saved.")
        return True

    def remove_picture(self) -> None:
        pid = self.current_profile()
        if pid is not None:
            profiles.picture_path(pid).unlink(missing_ok=True)
            self.profile_picker.refresh_picture()

    def _apply_preferences(self, profile_id: str) -> None:
        """Settings that change how everything reads, not what it shows: the clock."""
        conn = self.ctx.connect(profile_id)
        try:
            local_time.use_clock(settings.get(conn, "clock_format"))
        finally:
            conn.close()

    def _after_settings_saved(self, changed: list[str]) -> None:
        # Release the model-check blocker before starting this settings recompute.
        self._cancel_model_check()
        pid = self.current_profile()
        if "clock_format" in changed or "week_start" in changed:
            self._apply_preferences(pid)
            # Times and weeks are on every page: they all redraw, each when it's shown.
            for page in (self.for_you, self.recap):
                page.refresh_later()
        index = self.profile_picker.currentIndex()
        self.profile_picker.setItemText(index, profiles.display_name(pid))
        self.dashboard.refresh_later()
        self.reports.refresh_later()
        if set(changed) & set(recommend.REC_SETTINGS):
            # Plan to Watch, minimum raters, and so on change the lists: recompute from
            # what's cached (no network, about a second, in the background).
            self.for_you.start_refresh(fetch=False)

    def _after_profile_restored(self, profile_id: str) -> None:
        self.ctx.forget(profile_id)
        self.reload_profiles(select=profile_id)
        self.show_page("Settings")

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
        workers = [w for w in (self.dashboard.worker, self.for_you.worker) if w is not None]
        if not self._closing:
            # Only long work is worth asking about; a recompute from the cache takes a second.
            long_work = self.dashboard.is_syncing() or self.for_you.is_fetching()
            if long_work and not self._confirm_quit_during_sync():
                event.ignore()
                return
            self._closing = True
            self._cancel_model_check()
            # Stops after the page in flight; committed pages stay and the next run resumes.
            for worker in workers:
                if worker.isRunning():
                    worker.cancel()
                    worker.wait(self.CLOSE_WAIT_MS)
            for thread in self.ctx.threads:  # past recaps stop before their next period
                if hasattr(thread, "cancel"):
                    thread.cancel()
            for thread in self.ctx.threads:  # short jobs: recap drawing, Test connection
                thread.wait(self.CLOSE_WAIT_MS)
        stuck = [w for w in [*workers, *self.ctx.threads] if w.isRunning()]
        if stuck:
            # Cancel lands at the next progress message, and a request in its timeout or a
            # Retry-After wait can outlast the wait above. Dropping a running thread makes
            # Qt abort, so the window goes now and the close finishes when the thread ends.
            self.hide()
            for worker in stuck:
                worker.finished.connect(self.close, Qt.ConnectionType.UniqueConnection)
            event.ignore()
            return
        self.ctx.shutdown()
        event.accept()
