"""The Music report page: my Last.fm plays for all time or one year.

The same skeleton as the other report pages: a span switch, six tiles, the albums I
played most beside plays over time, three charts, then the artists I keep coming back
to. Numbers from taste.musicstats (Qt-free). The capture scope is always on the
page: Last.fm plays are a sample of my listening, never all of it.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from taste import charts, local_time, musicstats, settings
from taste.desktop import motion, theme
from taste.desktop.books_view import CoverWall, PersonCard, SpanBar, WallItem
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BarChart,
    RankList,
    Segmented,
    StatTile,
    chart_card,
    label,
    tile_row,
)
from taste.musicstats import MusicStats

COVER = QSize(100, 100)
ARTIST_COVER = QSize(56, 56)
PEOPLE_COLUMNS = 3
PERIOD_NAMES = {"week": "Week", "month": "Month", "year": "Year"}
PLAYS_MIN_BAR = 18  # narrower than this and the bars stop reading as bars


def hour_name(hour: int | None) -> str:
    if hour is None:
        return "-"
    return local_time.hour_text(hour)


def album_item(album: str, artist: str, plays: int, cover: str | None) -> WallItem:
    return WallItem(
        album, cover, f"{album}\n{artist}\n{plays:,} {'play' if plays == 1 else 'plays'}",
        f"{plays:,}",
    )  # fmt: skip


class MusicView(QWidget):
    """Everything on the Music page under its title."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.span = "all"
        self.stats: MusicStats | None = None

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.GAP)

        top = QHBoxLayout()
        self.span_bar = SpanBar("music")
        self.span_bar.changed.connect(self.set_span)
        top.addWidget(self.span_bar)
        top.addStretch()
        # Muted on the right, like every other page's note (it was coral, on the left).
        self.scope = label("", role="muted", wrap=True)
        self.scope.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.scope, 1)
        grid.addLayout(top, 0, 0, 1, 6)

        self.tiles = [StatTile() for _ in range(6)]
        grid.addLayout(tile_row(self.tiles), 1, 0, 1, 6)

        self.wall = CoverWall(ctx, COVER, theme.CORAL, "music")
        self.wall_card = chart_card("Albums you played most", self.wall, " ")
        grid.addWidget(self.wall_card, 2, 0, 1, 4)
        # Week, month, or year: as many of the latest as fit the width.
        self.plays_chart = BarChart(theme.CORAL, scale=True, min_bar=PLAYS_MIN_BAR)
        self.plays_chart.setMinimumHeight(150)
        self.period_switch = Segmented(list(PERIOD_NAMES.items()), accent="music")
        self.plays_card = chart_card("Plays", self.plays_chart, side=self.period_switch)
        grid.addWidget(self.plays_card, 2, 4, 1, 2)

        self.top_tracks = RankList(empty_text="No plays yet")
        self.tracks_card = chart_card("Top tracks", self.top_tracks)
        grid.addWidget(self.tracks_card, 3, 0, 1, 2)
        self.hour_chart = BarChart(theme.CORAL, scale=True)
        self.hour_card = chart_card("Plays by hour", self.hour_chart, "In your time zone.")
        grid.addWidget(self.hour_card, 3, 2, 1, 2)
        self.day_chart = BarChart(theme.CORAL, value_labels=True)
        self.day_card = chart_card(
            "Plays by day of week", self.day_chart, "Days start at midnight, your time."
        )
        grid.addWidget(self.day_card, 3, 4, 1, 2)

        people_box = QWidget()
        self.people_grid = QGridLayout(people_box)
        self.people_grid.setContentsMargins(0, 4, 0, 0)
        self.people_grid.setSpacing(10)
        self.people = [
            PersonCard(ctx, theme.CORAL, ARTIST_COVER) for _ in range(musicstats.PEOPLE_SHOWN)
        ]
        for i, card in enumerate(self.people):
            self.people_grid.addWidget(card, i // PEOPLE_COLUMNS, i % PEOPLE_COLUMNS)
        for column in range(PEOPLE_COLUMNS):
            self.people_grid.setColumnStretch(column, 1)
        self.people_card = chart_card("Artists you keep coming back to", people_box, " ")
        grid.addWidget(self.people_card, 4, 0, 1, 6)

        for row, weight in enumerate((0, 0, 4, 3, 2)):
            grid.setRowStretch(row, weight)
        for column in range(6):
            grid.setColumnStretch(column, 1)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.span = None  # "Report pages open on", once its years are known

    def refresh(self, report: charts.ReportData) -> None:
        """Redraw. `report` brings the capture scope and time zone."""
        mixed = (
            " Plays were captured under more than one scope setting." if report.mixed_scopes else ""
        )
        self.scope.setText(f"Last.fm: {report.scope_note}{mixed}")
        self.hour_card.note.setText(f"In your time zone, {report.tz_name}.")
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            years = musicstats.years(conn)
            if self.span is None:
                latest = settings.get(conn, "report_span") == "year" and years
                self.span = years[0] if latest else "all"
            if self.span != "all" and self.span not in years:
                self.span = "all"
            stats = musicstats.load(conn, self.span)
        finally:
            conn.close()
        self.span_bar.set_years(years)
        self.show_stats(stats)

    def set_span(self, span: str) -> None:
        if not self.profile_id:
            return
        self.span = span
        conn = self.ctx.connect(self.profile_id)
        try:
            stats = musicstats.load(conn, span)
        finally:
            conn.close()
        if self.isVisible():
            motion.crossfade(self, lambda: self.show_stats(stats))
        else:
            self.show_stats(stats)

    def show_stats(self, s: MusicStats) -> None:
        self.stats = s
        self.span_bar.show_span(s.span)

        def tile(value: str, caption: str, detail: str = "") -> charts.Tile:
            return charts.Tile(value, caption, "music", detail=detail)

        hours = s.hours.hours
        if hours:
            hour_tile = tile(
                f"~{hours:,.0f}", "hours of music", f"lengths known for {s.hours.coverage:.0%}"
            )
        elif s.plays and s.hours.known < 1:
            # No lengths looked up yet: how far along that is, not a dash.
            hour_tile = tile(
                f"{s.hours.coverage:.0%}", "plays with a length", "hours show once half have one"
            )
        elif s.plays:
            # Too few lengths for an estimate: what the known ones add up to, at least.
            hour_tile = tile(
                f"{s.hours.known:,.0f}+",
                "hours of music",
                f"lengths known for {s.hours.coverage:.0%} of plays",
            )
        else:
            hour_tile = tile("0", "hours of music")
        if s.new_artists is not None:
            new = tile(f"{s.new_artists:,}", "new artists", "first played this year")
        elif s.newest_year:
            year, count = s.newest_year
            new = tile(f"{count:,}", "new artists", f"first played in {year}")
        else:
            new = tile("0", "new artists")
        values = [
            tile(
                f"{s.plays:,}", "Last.fm plays", "all time" if s.span == "all" else f"in {s.span}"
            ),
            hour_tile,
            tile(
                f"{s.artists:,}", "artists", f"most: {s.people[0].name}" if s.people else ""
            ),  # fmt: skip
            new,
            tile(hour_name(s.busiest_hour), "busiest hour", "your time"),
            tile(
                s.busiest_day or "-",
                "busiest day",
                f"{s.busiest_day_share:.0%} of plays" if s.busiest_day else "",
            ),  # fmt: skip
        ]
        for widget, value in zip(self.tiles, values, strict=True):
            widget.set(value)

        self.wall.set_items([album_item(*a) for a in s.albums])
        self.wall_card.note.setText(
            "Most played first, with plays on each." if s.albums else "No album plays yet."
        )
        self.top_tracks.set_rows(
            [(track, plays) for track, _, plays in s.tracks],
            tips=[f"{track} by {artist}" for track, artist, _ in s.tracks],
        )
        hour_labels = [local_time.hour_text(h, short=True) for h in range(24)]
        self.hour_chart.set_data(s.by_hour if any(s.by_hour) else [], hour_labels, unit="plays")
        self.day_chart.set_data(
            [p for _, p in s.by_weekday] if any(p for _, p in s.by_weekday) else [],
            [d[:3] for d, _ in s.by_weekday],
            unit="plays",
        )

        for i, card in enumerate(self.people):
            artist = s.people[i] if i < len(s.people) else None
            if artist is not None:
                card.set_person(
                    artist.name,
                    f"{artist.plays:,} plays",
                    [album_item(a, artist.name, p, c) for a, p, c in artist.albums],
                    artist.months,
                    "Your plays of them each month, oldest first.",
                )
            card.setVisible(artist is not None)
        lead = s.people[0] if s.people else None
        self.people_card.note.setText(
            f"{lead.name} leads with {lead.plays:,} plays" if lead else "No plays yet"
        )
