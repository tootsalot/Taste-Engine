"""Reports: summary, listening charts, every report as a sortable table, CSV export."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from taste import reports
from taste.desktop import theme
from taste.desktop.context import AppContext
from taste.desktop.widgets import BarChart, Card, RowsModel, heading, label

HIDDEN_COLUMNS = {"data_scope", "item_id"}


class ReportsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.tables: dict[str, tuple[list[str], list[tuple]]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        top = QHBoxLayout()
        top.addWidget(heading("Reports", 28))
        top.addStretch()
        self.export_one = QPushButton("Export this report")
        self.export_one.clicked.connect(lambda: self.export_current())
        self.export_all_button = QPushButton("Export all as CSV")
        self.export_all_button.setProperty("role", "primary")
        self.export_all_button.clicked.connect(lambda: self.export_all())
        top.addWidget(self.export_one)
        top.addWidget(self.export_all_button)
        layout.addLayout(top)

        self.summary = label("", wrap=True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_card = Card()
        summary_card.body.addWidget(self.summary)
        layout.addWidget(summary_card)

        charts = QHBoxLayout()
        charts.setSpacing(12)
        self.hour_card, self.hour_chart = self._chart_card("Plays by hour")
        self.day_card, self.day_chart = self._chart_card("Plays by day of week")
        charts.addWidget(self.hour_card, 3)
        charts.addWidget(self.day_card, 2)
        layout.addLayout(charts)
        self.scope = label("", role="scope", wrap=True)
        layout.addWidget(self.scope)

        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(420)
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs, 1)
        self.status = label("", role="muted")
        layout.addWidget(self.status)

    def _chart_card(self, title: str) -> tuple[Card, BarChart]:
        card = Card()
        title_label = label(title, role="muted")
        chart = BarChart(theme.CORAL)
        card.body.addWidget(title_label)
        card.body.addWidget(chart)
        card.title_label = title_label
        return card, chart

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            # The CLI indents detail lines; the card doesn't need that.
            self.summary.setText("\n".join(line.strip() for line in reports.summary_lines(conn)))
            hours = conn.execute(
                "SELECT local_hour, plays FROM rpt_lastfm_by_hour ORDER BY local_hour"
            ).fetchall()
            days = conn.execute(
                "SELECT weekday, plays FROM rpt_lastfm_by_weekday ORDER BY local_weekday_num"
            ).fetchall()
            scope = conn.execute("SELECT * FROM rpt_lastfm_scope").fetchone()
            tz_name = conn.execute("SELECT timezone FROM rpt_settings").fetchone()[0]
            self.tables = {}
            for report in reports.REPORTS:
                cursor = conn.execute(f"SELECT * FROM {report.view} ORDER BY {report.order_by}")
                self.tables[report.view] = (
                    [d[0] for d in cursor.description],
                    [tuple(r) for r in cursor.fetchall()],
                )
        finally:
            conn.close()

        hour_labels = [f"{h % 12 or 12}{'a' if h < 12 else 'p'}" for h, _ in hours]
        self.hour_chart.set_data([p for _, p in hours], hour_labels)
        self.day_chart.set_data([p for _, p in days], [d[:3] for d, _ in days])
        self.hour_card.title_label.setText(f"Plays by hour ({tz_name})")
        mixed = scope["capture_scopes"] == "mixed"
        self.scope.setText(
            f"Last.fm: {scope['data_scope']}"
            + (" Plays were captured under more than one scope setting." if mixed else "")
        )

        current = self.tabs.currentIndex()
        self.tabs.clear()
        for report in reports.REPORTS:
            columns, rows = self.tables[report.view]
            self.tabs.addTab(self._table(columns, rows), report.title)
        self.tabs.setCurrentIndex(max(current, 0))

    def _table(self, columns: list[str], rows: list[tuple]) -> QTableView:
        view = QTableView()
        model = RowsModel(columns, rows)
        proxy = QSortFilterProxyModel(view)
        proxy.setSourceModel(model)
        view.setModel(proxy)
        view.setSortingEnabled(True)
        view.sortByColumn(-1, Qt.SortOrder.AscendingOrder)  # keep the report's own order
        view.setAlternatingRowColors(True)
        view.verticalHeader().setVisible(False)
        view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        view.horizontalHeader().setStretchLastSection(True)
        for i, column in enumerate(columns):
            if column in HIDDEN_COLUMNS:
                view.setColumnHidden(i, True)
        view.resizeColumnsToContents()
        return view

    # -- export -------------------------------------------------------------

    def _ask_file(self, suggested: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, "Export report", suggested, "CSV (*.csv)")
        return path

    def _ask_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Export all reports to")

    def export_current(self, path: str | None = None) -> Path | None:
        if not self.tables:
            return None
        report = reports.REPORTS[self.tabs.currentIndex()]
        target = path or self._ask_file(f"{self.profile_id}-{report.csv_name}")
        if not target:
            return None
        columns, rows = self.tables[report.view]
        # utf-8-sig so Excel shows non-English titles correctly.
        with open(target, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        self.status.setText(f"Saved {target}")
        return Path(target)

    def export_all(self, folder: str | None = None) -> Path | None:
        if not self.profile_id:
            return None
        target = folder or self._ask_folder()
        if not target:
            return None
        out = Path(target) / f"taste-engine-{self.profile_id}"
        conn = self.ctx.connect(self.profile_id)
        try:
            written = reports.write_csvs(conn, out)
        finally:
            conn.close()
        self.status.setText(f"Saved {len(written)} CSV files to {out}")
        return out
