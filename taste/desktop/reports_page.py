"""Reports: a page of charts per medium. The numbers behind them are one click away as CSV.

The data comes from taste.charts (Qt-free); this page only lays it out. Every
report view can be exported, one at a time or all at once, from the Export menu.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taste import charts, db, profiles, reports
from taste.desktop import motion, theme, ui_state
from taste.desktop.anime_view import AnimeView
from taste.desktop.books_view import BooksView
from taste.desktop.context import AppContext
from taste.desktop.music_view import MusicView
from taste.desktop.screen_view import ScreenView
from taste.desktop.widgets import (
    Card,
    LazyPage,
    heading,
    label,
)
from taste.desktop.workers import CallWorker

PERIOD_NOTES = {"week": "Weeks start on Sunday, your time."}


class SectionContent:
    """A page's charts, or a card saying how to fill them, never both."""

    def __init__(self, grid: QGridLayout, empty: Card) -> None:
        self.grid = grid
        self.empty = empty

    def show_data(self, has_data: bool) -> None:
        for i in range(self.grid.count()):
            item = self.grid.itemAt(i)
            widget = item.widget()
            if widget is not None and widget is not self.empty:
                widget.setVisible(has_data)
            elif item.layout() is not None:
                for j in range(item.layout().count()):
                    child = item.layout().itemAt(j).widget()
                    if child is not None:
                        child.setVisible(has_data)
        self.empty.setVisible(not has_data)

    def isVisible(self) -> bool:  # noqa: N802 (Qt naming, like the widget it replaced)
        return not self.empty.isVisible()


def empty_or(grid: QGridLayout, text: str) -> SectionContent:
    card = Card()
    card.body.setContentsMargins(40, 36, 40, 36)
    card.body.addWidget(heading("Nothing here yet", 24))
    card.body.addWidget(label(text, role="muted", wrap=True))
    card.body.addStretch()
    grid.addWidget(card, 0, 0, grid.rowCount(), max(grid.columnCount(), 1))
    content = SectionContent(grid, card)
    content.show_data(False)
    return content


SECTIONS = {
    "anime": "Anime",
    "music": "Music",
    "books": "Books",
    "screen": "Films and TV",
}
EMPTY_SECTION = {
    "books": (
        "No books yet. Connect your Hardcover account on the Settings page, then sync, and "
        "your reading shows up here: books per year, your stars against everyone's, and the "
        "authors you read most."
    ),
    "screen": (
        "No films or TV yet. Connect your TMDB account on the Settings page, then sync, and "
        "your ratings show up here: how you rate, how that compares with TMDB, and the "
        "directors you watch most."
    ),
}


def section_page(layout_parent: QWidget) -> tuple[QScrollArea, QGridLayout]:
    """A scrolling page whose rows share the window's height."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    body = QWidget()
    scroll.setWidget(body)
    grid = QGridLayout(body)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(theme.GAP)
    return scroll, grid


class ReportsPage(LazyPage, QWidget):
    """Reports, one page per medium. The sidebar's Reports group picks the page."""

    exported = Signal(object)  # what a finished export wrote (a file or folder), or None

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.current = "anime"
        self.sections_stale: set[str] = set()  # sections to redraw when next opened

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(12)
        top = QHBoxLayout()
        self.title = heading("Anime", 28)
        top.addWidget(self.title)
        top.addStretch()
        self.export_button = QToolButton()
        self.export_button.setText("Export CSV")
        self.export_button.setObjectName("ExportButton")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_menu = QMenu(self.export_button)
        self.export_menu.aboutToShow.connect(motion.menu_effects)
        self.export_menu.addAction("All reports…", lambda: self.export_all())
        self.export_menu.addSeparator()
        for report in reports.REPORTS:
            self.export_menu.addAction(
                f"{report.title}…", lambda v=report.view: self.export_report(v)
            )
        self.export_button.setMenu(self.export_menu)
        top.addWidget(self.export_button)
        layout.addLayout(top)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.status = label("", role="muted")
        layout.addWidget(self.status)
        self.pages: dict[str, QScrollArea] = {}
        # Every medium on one skeleton (span switch, six tiles, pictures beside the main
        # chart, three charts, then the people I keep coming back to), each its own view.
        scroll, anime = section_page(self)
        self.pages["anime"] = scroll
        self.anime = AnimeView(ctx)
        anime.addWidget(self.anime, 0, 0)
        anime.setRowStretch(0, 1)

        scroll, music = section_page(self)
        self.pages["music"] = scroll
        self.music = MusicView(ctx)
        music.addWidget(self.music, 0, 0)
        music.setRowStretch(0, 1)
        # The charts the page itself drives (plays over time), and short names for tests.
        self.plays_chart = self.music.plays_chart
        self.plays_card = self.music.plays_card
        self.period_switch = self.music.period_switch
        self.period_switch.changed.connect(self.set_period)
        self.scope = self.music.scope
        self.hour_chart, self.hour_card = self.music.hour_chart, self.music.hour_card
        self.scores, self.drop_chart = self.anime.scores, self.anime.drop_chart
        self.genres = self.anime.genres

        # Books: its own view (books_view.py), after Hardcover's stats page.
        scroll, books = section_page(self)
        self.pages["books"] = scroll
        self.books = BooksView(ctx)
        books.addWidget(self.books, 0, 0)
        books.setRowStretch(0, 1)
        self.books_section = empty_or(books, EMPTY_SECTION["books"])

        # Films and TV: its own view (screen_view.py), built like Books.
        scroll, screen = section_page(self)
        self.pages["screen"] = scroll
        self.screen = ScreenView(ctx)
        screen.addWidget(self.screen, 0, 0)
        screen.setRowStretch(0, 1)
        self.screen_section = empty_or(screen, EMPTY_SECTION["screen"])

        for key in SECTIONS:
            self.stack.addWidget(self.pages[key])
        self.data: charts.ReportData | None = None
        self.period = ""
        self.set_period(ui_state.load().get("plays_period", "month"), remember=False)

    def show_section(self, key: str) -> None:
        """One medium's page. The sidebar calls this."""
        key = key if key in SECTIONS else "anime"
        if key == self.current and self.stack.currentWidget() is self.pages[key]:
            self.refresh_if_stale()
            return
        self.current = key
        if self.stale:
            self.refresh_if_stale()  # fresh data, drawn straight into this section
        else:
            self._refresh_section(key)
        self.title.setText(SECTIONS[key])

        def switch() -> None:
            self.stack.setCurrentWidget(self.pages[key])

        if self.isVisible():
            motion.crossfade(self.stack, switch)
        else:
            switch()

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.status.setText("")
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            data = charts.load(conn, now=self.ctx.now())
        finally:
            conn.close()

        self.data = data
        # Only the section on screen now; each other one when it's next opened.
        self.sections_stale = set(SECTIONS)
        self._refresh_section(self.current)

    def _refresh_section(self, key: str) -> None:
        if key not in self.sections_stale or self.data is None:
            return
        self.sections_stale.discard(key)
        data = self.data
        if key in ("anime", "music"):
            view = self.anime if key == "anime" else self.music
            if view.profile_id != self.profile_id:
                view.set_profile(self.profile_id)
            view.refresh(data)
            if key == "music":
                self._show_plays()
            return
        section, view, has = (
            (self.books_section, self.books, data.has_books)
            if key == "books"
            else (self.screen_section, self.screen, data.has_screen)
        )
        section.show_data(has)  # or what fills it before there's data
        if has:
            if view.profile_id != self.profile_id:
                view.set_profile(self.profile_id)
            else:
                view.refresh()

    def lazy_refresh(self) -> None:  # LazyPage: after a sync, when shown
        self.refresh()

    def set_period(self, period: str, remember: bool = True) -> None:
        """Plays per week, month, or year. The choice is kept for every profile."""
        period = period if period in charts.PERIODS else "month"
        self.period = period
        self.period_switch.set_value(period)
        self.plays_card.title.setText(f"Plays per {period}")
        note = PERIOD_NOTES.get(period, "")
        self.plays_card.note.setText(note)
        self.plays_card.note.setVisible(bool(note))
        self._show_plays()
        if remember:
            ui_state.save(plays_period=period)

    def _show_plays(self) -> None:
        series = self.data.periods[self.period] if self.data else []
        names = [charts.period_label(self.period, key) for key, _ in series]
        self.plays_chart.set_data(
            [plays for _, plays in series],
            [axis for axis, _ in names],
            partial_last=bool(self.data) and self.data.partial[self.period],
            tips=[
                f"{hover}: {plays:,} {'play' if plays == 1 else 'plays'}"
                for (_, hover), (_, plays) in zip(names, series, strict=True)
            ],
        )

    # -- export -------------------------------------------------------------

    def _ask_file(self, suggested: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, "Export report", suggested, "CSV (*.csv)")
        return path

    def _ask_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Export all reports to")

    def export_report(self, view: str, path: str | None = None) -> CallWorker | None:
        """One report view as CSV, written off the UI thread; `exported` says when.
        `path` skips the file dialog (tests)."""
        if not self.profile_id:
            return None
        report = next(r for r in reports.REPORTS if r.view == view)
        target = path or self._ask_file(f"{self.profile_id}-{report.csv_name}")
        if not target:
            return None
        return self._export(
            lambda conn: reports.write_csv(conn, report, Path(target)),
            Path(target),
            lambda _rows: f"Saved {target}",
        )

    def export_all(self, folder: str | None = None) -> CallWorker | None:
        if not self.profile_id:
            return None
        target = folder or self._ask_folder()
        if not target:
            return None
        out = Path(target) / f"taste-engine-{self.profile_id}"
        return self._export(
            lambda conn: reports.write_csvs(conn, out),
            out,
            lambda written: f"Saved {len(written)} CSV files to {out}",
        )

    def _export(self, write, target: Path, saved) -> CallWorker:
        """Run `write(conn)` on a worker thread: a big history took seconds, the window
        frozen meanwhile. One export at a time."""
        path = profiles.db_path(self.profile_id)

        def call():
            conn = db.connect(path, setup=False)  # its own connection, on its own thread
            try:
                return write(conn)
            finally:
                conn.close()

        def done(result, error: str) -> None:
            self.export_button.setEnabled(True)
            if error:
                self.status.setText(f"Couldn't export ({error}).")
                self.exported.emit(None)
                return
            self.status.setText(saved(result))
            self.exported.emit(target)

        worker = CallWorker(call)
        worker.done.connect(done)
        self.ctx.track(worker)
        self.export_button.setEnabled(False)
        self.status.setText("Exporting…")
        worker.start()
        return worker
