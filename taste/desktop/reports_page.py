"""Reports: charts only. The numbers behind them are one click away as CSV files.

The data comes from taste.charts (Qt-free); this page only lays it out. Every
report view can be exported, one at a time or all at once, from the Export menu.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taste import charts, reports
from taste.desktop import theme
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BandChart,
    BarChart,
    Card,
    GenreCard,
    RankList,
    heading,
    label,
    section,
)

DROP_LABELS = [f"{i * 10}%" for i in range(10)]


class StatTile(Card):
    def __init__(self) -> None:
        super().__init__()
        self.value = label("-")
        self.value.setFont(theme.number_font(26))
        self.body.addWidget(self.value)
        self.caption = label("", role="muted")
        self.body.addWidget(self.caption)

    def set(self, tile: charts.Tile) -> None:
        self.value.setText(tile.value)
        self.caption.setText(tile.caption)
        color = theme.LILAC if tile.kind == "anime" else theme.CORAL
        self.value.setStyleSheet(f"color: {color};")


def chart_card(title: str, chart: QWidget, note: str = "") -> Card:
    card = Card()
    card.body.addWidget(section(title))
    card.note = label(note, role="muted", wrap=True)
    card.body.addWidget(card.note)
    card.note.setVisible(bool(note))  # after it has a parent, or it opens as its own window
    card.body.addWidget(chart, 1)
    return card


class ReportsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(heading("Reports", 28))
        top.addStretch()
        self.export_button = QToolButton()
        self.export_button.setText("Export CSV")
        self.export_button.setObjectName("ExportButton")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_menu = QMenu(self.export_button)
        self.export_menu.addAction("All reports…", lambda: self.export_all())
        self.export_menu.addSeparator()
        for report in reports.REPORTS:
            self.export_menu.addAction(
                f"{report.title}…", lambda v=report.view: self.export_report(v)
            )
        self.export_button.setMenu(self.export_menu)
        top.addWidget(self.export_button)
        layout.addLayout(top)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles = [StatTile() for _ in range(4)]
        for tile in self.tiles:
            tiles.addWidget(tile)
        layout.addLayout(tiles)

        # Anime
        layout.addWidget(section("Anime"))
        anime = QGridLayout()
        anime.setSpacing(12)
        self.scores = BandChart()
        anime.addWidget(
            chart_card("Your score vs MAL", self.scores, "Your average for shows MAL rates "
                       "about the same. Bars cover the middle half of your scores."),
            0,
            0,
        )  # fmt: skip
        self.drop_chart = BarChart(theme.LILAC, empty_text="No dropped shows", value_labels=True)
        self.drop_chart.setMinimumHeight(200)
        anime.addWidget(
            chart_card("Where you drop shows", self.drop_chart,
                       "How far into a show you were when you dropped it. Each bar spans ten "
                       "points: 20% means 20 to 29% watched."),
            0,
            1,
        )  # fmt: skip
        self.genres = GenreCard(columns=2)
        anime.addWidget(self.genres, 1, 0, 1, 2)
        anime.setColumnStretch(0, 1)
        anime.setColumnStretch(1, 1)
        layout.addLayout(anime)

        # Music
        layout.addWidget(section("Music"))
        self.scope = label("", role="scope", wrap=True)
        layout.addWidget(self.scope)
        music = QGridLayout()
        music.setSpacing(12)
        self.month_chart = BarChart(theme.CORAL, scale=True)
        self.month_chart.setMinimumHeight(170)
        music.addWidget(chart_card("Plays per month", self.month_chart), 0, 0, 1, 2)
        self.top_artists = RankList(empty_text="No plays yet")
        music.addWidget(chart_card("Top artists", self.top_artists), 1, 0)
        self.top_tracks = RankList(empty_text="No plays yet")
        music.addWidget(chart_card("Top tracks", self.top_tracks), 1, 1)
        self.hour_chart = BarChart(theme.CORAL, scale=True)
        self.hour_card = chart_card("Plays by hour", self.hour_chart, "In your time zone.")
        music.addWidget(self.hour_card, 2, 0)
        self.day_chart = BarChart(theme.CORAL, value_labels=True)
        music.addWidget(
            chart_card(
                "Plays by day of week", self.day_chart, "Days start at midnight, your time."
            ),
            2,
            1,
        )
        music.setColumnStretch(0, 1)
        music.setColumnStretch(1, 1)
        layout.addLayout(music)

        self.status = label("", role="muted")
        layout.addWidget(self.status)
        layout.addStretch(1)

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

        for tile, value in zip(self.tiles, data.tiles, strict=True):
            tile.set(value)
        self.scores.set_bands(data.bands)
        if any(data.drops):
            self.drop_chart.set_data(data.drops, DROP_LABELS, unit="shows")
        else:
            self.drop_chart.set_data([], [])
        self.genres.set_genres(
            data.generous,
            data.harsh,
            "Sync MyAnimeList to see which genres you rate above or below the crowd.",
            usual=data.usual,
        )
        mixed = (
            " Plays were captured under more than one scope setting." if data.mixed_scopes else ""
        )
        self.scope.setText(f"Last.fm: {data.scope_note}{mixed}")
        self.month_chart.set_data(
            [p for _, p in data.months],
            [month_label(m) for m, _ in data.months],
            unit="plays",
            partial_last=data.partial_month,
        )
        self.top_artists.set_rows(data.top_artists)
        self.top_tracks.set_rows(
            [(track, plays) for track, _, plays in data.top_tracks],
            tips=[f"{track} by {artist}" for track, artist, _ in data.top_tracks],
        )
        hour_labels = [f"{h % 12 or 12}{'a' if h < 12 else 'p'}" for h in range(24)]
        self.hour_chart.set_data(data.hours if any(data.hours) else [], hour_labels, unit="plays")
        self.day_chart.set_data(
            [p for _, p in data.weekdays] if any(p for _, p in data.weekdays) else [],
            [d for d, _ in data.weekdays],
            unit="plays",
        )
        self.hour_card.note.setText(f"In your time zone, {data.tz_name}.")

    # -- export -------------------------------------------------------------

    def _ask_file(self, suggested: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, "Export report", suggested, "CSV (*.csv)")
        return path

    def _ask_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Export all reports to")

    def export_report(self, view: str, path: str | None = None) -> Path | None:
        """One report view as CSV. `path` skips the file dialog (tests)."""
        if not self.profile_id:
            return None
        report = next(r for r in reports.REPORTS if r.view == view)
        target = path or self._ask_file(f"{self.profile_id}-{report.csv_name}")
        if not target:
            return None
        conn = self.ctx.connect(self.profile_id)
        try:
            reports.write_csv(conn, report, Path(target))
        finally:
            conn.close()
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


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_label(month: str) -> str:
    """'2026-10' as 'Oct 26'."""
    return f"{MONTH_NAMES[int(month[5:7]) - 1]} {month[2:4]}"
