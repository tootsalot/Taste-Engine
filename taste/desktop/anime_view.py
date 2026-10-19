"""The Anime report page: my MAL list for all time or one year.

The same skeleton as Books and Films and TV (approved 2026-10-15, for one look across
Reports): a span switch, six tiles, the posters beside the main chart, three charts,
then the studios I keep coming back to. Numbers from taste.animestats (Qt-free).
Genre leans and where I drop shows stay all-time: a year has too few shows for them.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from taste import animestats, charts, settings
from taste.activity import Finished
from taste.animestats import AnimeStats
from taste.desktop import motion, theme
from taste.desktop.books_view import CoverWall, PersonCard, SpanBar, WallItem
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BandChart,
    BarChart,
    GenreCard,
    StatTile,
    chart_card,
    label,
    tile_row,
)

POSTER = QSize(84, 120)
PEOPLE_COLUMNS = 3
DROP_LABELS = [f"{i * 10}%" for i in range(10)]


def show_item(f: Finished) -> WallItem:
    lines = [f.title, f"Finished {f.day}" + (" (estimated)" if f.guessed else "")]
    if f.my_score is not None:
        mal = f" · MAL {f.community_mean:.2f}" if f.community_mean else ""
        lines.append(f"You gave it {f.my_score:g}{mal}")
    if f.genres:
        lines.append(", ".join(f.genres[:4]))
    badge = f"{f.my_score:g}" if f.my_score is not None else ""
    return WallItem(f.title, f.poster_url, "\n".join(lines), badge)


class AnimeView(QWidget):
    """Everything on the Anime page under its title."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.span = "all"
        self.stats: AnimeStats | None = None

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)

        top = QHBoxLayout()
        self.span_bar = SpanBar("anime")
        self.span_bar.changed.connect(self.set_span)
        top.addWidget(self.span_bar)
        top.addStretch()
        top.addWidget(label("By the day you finished each show.", role="muted"))
        grid.addLayout(top, 0, 0, 1, 6)

        self.tiles = [StatTile() for _ in range(6)]
        grid.addLayout(tile_row(self.tiles), 1, 0, 1, 6)

        self.wall = CoverWall(ctx, POSTER, theme.LILAC, "anime")
        self.wall_card = chart_card("Finished", self.wall, " ")
        grid.addWidget(self.wall_card, 2, 0, 1, 4)
        self.scores = BandChart()
        self.scores_card = chart_card(
            "Your score vs MAL",
            self.scores,
            "Your average for shows MAL rates about the same. Bars cover the middle half "
            "of your scores.",
        )
        grid.addWidget(self.scores_card, 2, 4, 1, 2)

        self.genres = GenreCard(columns=1)
        grid.addWidget(self.genres, 3, 0, 1, 2)
        self.spread = BarChart(theme.LILAC, empty_text="No scores yet", value_labels=True)
        self.spread_card = chart_card("Your scores, 1 to 10", self.spread, " ")
        grid.addWidget(self.spread_card, 3, 2, 1, 2)
        self.drop_chart = BarChart(theme.LILAC, empty_text="No dropped shows", value_labels=True)
        self.drop_card = chart_card(
            "Where you drop shows",
            self.drop_chart,
            "How far in you were when you dropped a show: 20% means 20 to 29% watched. All time.",
        )
        grid.addWidget(self.drop_card, 3, 4, 1, 2)

        people_box = QWidget()
        self.people_grid = QGridLayout(people_box)
        self.people_grid.setContentsMargins(0, 4, 0, 0)
        self.people_grid.setSpacing(10)
        self.people = [PersonCard(ctx, theme.LILAC) for _ in range(animestats.PEOPLE_SHOWN)]
        for i, card in enumerate(self.people):
            self.people_grid.addWidget(card, i // PEOPLE_COLUMNS, i % PEOPLE_COLUMNS)
        for column in range(PEOPLE_COLUMNS):
            self.people_grid.setColumnStretch(column, 1)
        self.people_card = chart_card("Studios you keep coming back to", people_box, " ")
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
        """Redraw. `report` brings the all-time parts (genre leans, drops)."""
        self.genres.set_genres(
            report.generous,
            report.harsh,
            "Sync MyAnimeList to see which genres you rate above or below the crowd.",
            usual=report.usual,
        )
        if any(report.drops):
            self.drop_chart.set_data(report.drops, DROP_LABELS, unit="shows")
        else:
            self.drop_chart.set_data([], [])
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            years = animestats.years(conn)
            if self.span is None:
                latest = settings.get(conn, "report_span") == "year" and years
                self.span = years[0] if latest else "all"
            if self.span != "all" and self.span not in years:
                self.span = "all"
            stats = animestats.load(conn, self.span)
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
            stats = animestats.load(conn, span)
        finally:
            conn.close()
        if self.isVisible():
            motion.crossfade(self, lambda: self.show_stats(stats))
        else:
            self.show_stats(stats)

    def show_stats(self, s: AnimeStats) -> None:
        self.stats = s
        self.span_bar.show_span(s.span)

        def tile(value: str, caption: str, detail: str = "") -> charts.Tile:
            return charts.Tile(value, caption, "anime", detail=detail)

        count = len(s.finished)
        values = [
            tile(
                f"{count:,}",
                "shows finished",
                "every completed show" if s.span == "all" else f"in {s.span}",
            ),  # fmt: skip
            tile(f"~{s.hours:,.0f}", "hours watched", "episodes x their length")
            if s.hours or not count
            # MAL hasn't said how long the episodes are: count them instead of a dash.
            else tile(f"{s.episodes:,}", "episodes watched", "episode lengths unknown"),
            tile(
                f"{s.average:.1f}" if s.average is not None else "-", "your average", "out of 10"
            ),  # fmt: skip
            charts.Tile(
                f"{s.vs_mal + 0.0:+.2f}" if s.vs_mal is not None else "-",
                "vs MAL",
                "anime",
                detail=f"{s.trend[-1][0]}: {s.trend[-1][1] + 0.0:+.2f}"
                if s.trend
                else "the same shows",
                trend=tuple(v for _, v in s.trend) if len(s.trend) > 1 else (),
                trend_tip="Your average against MAL by the year you finished the show.",
            ),
            tile(
                s.top_genre or "-",
                "most watched genre",
                f"{s.top_genre_shows:,} of your shows" if s.top_genre else "",
            ),  # fmt: skip
            tile(s.top_decade or "-", "favorite decade", "by first season"),
        ]
        for widget, value in zip(self.tiles, values, strict=True):
            widget.set(value)

        self.wall.set_items([show_item(f) for f in s.shown])
        self.wall_card.title.setText("Finished" if s.span == "all" else f"Finished in {s.span}")
        self.wall_card.note.setText(
            f"{count:,} {'show' if count == 1 else 'shows'}, newest first, with your score."
            if count
            else "Nothing finished in this span."
        )
        self.scores.set_bands(s.bands)
        if any(s.scores):
            self.spread.set_data(s.scores, [str(n) for n in range(1, 11)], unit="shows")
        else:
            self.spread.set_data([], [])
        common = max(range(10), key=lambda i: (s.scores[i], i)) + 1 if any(s.scores) else None
        self.spread_card.note.setText(
            f"Most often a {common}" + (f" · average {s.average:.1f}" if s.average else "")
            if common
            else "Scores you gave on MAL"
        )

        for i, card in enumerate(self.people):
            studio = s.studios[i] if i < len(s.studios) else None
            if studio is not None:
                average = f" · {studio.average:.1f} from you" if studio.average else ""
                card.set_person(
                    studio.name,
                    f"{studio.shows} shows{average}",
                    [show_item(f) for f in studio.posters],
                    studio.scores,
                    "Your scores for their shows, in the order you finished them.",
                )
            card.setVisible(studio is not None)
        lead = s.studios[0] if s.studios else None
        self.people_card.note.setText(
            f"{lead.name} leads with {lead.shows} shows"
            if lead
            else f"Nobody yet: a studio shows up here from {animestats.PERSON_MIN} shows."
        )
