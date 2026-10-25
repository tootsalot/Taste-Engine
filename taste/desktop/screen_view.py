"""The Films and TV report page: my TMDB ratings for all time or one year.

Built like the Books page (approved 2026-10-15): six big numbers, the posters I
rated beside my ratings (Films | TV), genres and decades, then the directors I keep
coming back to. Numbers from taste.screenstats (Qt-free); this file only draws them.
TMDB keeps when I rated something, not when I watched it, and the page says "rated".
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from taste import charts, screenstats, settings
from taste.desktop import motion, theme
from taste.desktop.books_view import CoverWall, PersonCard, ShareList, SpanBar, WallItem
from taste.desktop.context import AppContext
from taste.desktop.widgets import (
    BandChart,
    BarChart,
    Segmented,
    StatTile,
    chart_card,
    label,
    tile_row,
)
from taste.screenstats import Rated, ScreenStats

POSTER = QSize(84, 126)
PEOPLE_COLUMNS = 3
RATING_LABELS = [f"{(i + 1) // 2}" if i % 2 == 1 else "" for i in range(20)]
KINDS = {"movie": "Film", "tv": "TV show"}


def rated_item(r: Rated) -> WallItem:
    lines = [r.title, f"{KINDS[r.kind]}{f', {r.year}' if r.year else ''}"]
    if r.people:
        lines.append(", ".join(r.people[:2]))
    lines.append(f"You rated it {r.rating:g}" + (f" · TMDB {r.average:.1f}" if r.average else ""))
    return WallItem(r.title, r.poster, "\n".join(lines), f"{r.rating:g}")


class ScreenView(QWidget):
    """Everything on the Films and TV page under its title."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.span = "all"
        self.kind = "movie"  # whose ratings the spread shows
        self.stats: ScreenStats | None = None

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.GAP)

        top = QHBoxLayout()
        self.span_bar = SpanBar("screen")
        self.span_bar.changed.connect(self.set_span)
        top.addWidget(self.span_bar)
        top.addStretch()
        top.addWidget(label("Counted by the day you rated them on TMDB.", role="muted"))
        grid.addLayout(top, 0, 0, 1, 6)

        self.tiles = [StatTile() for _ in range(6)]
        grid.addLayout(tile_row(self.tiles), 1, 0, 1, 6)

        self.wall = CoverWall(ctx, POSTER, theme.TEAL, "screen")
        self.wall_card = chart_card("Rated", self.wall, " ")
        grid.addWidget(self.wall_card, 2, 0, 1, 4)
        self.spread = BarChart(theme.TEAL, empty_text="No ratings yet", value_labels=True)
        self.kind_switch = Segmented([("movie", "Films"), ("tv", "TV")], accent="screen")
        self.kind_switch.changed.connect(self.set_kind)
        self.spread_card = chart_card("Your ratings", self.spread, " ", side=self.kind_switch)
        grid.addWidget(self.spread_card, 2, 4, 1, 2)

        self.genres = ShareList("No genres yet", theme.TEAL)
        self.genres_card = chart_card("Genres you rate most", self.genres, " ")
        grid.addWidget(self.genres_card, 3, 0, 1, 2)
        self.decades = BarChart(theme.TEAL, empty_text="No release years yet", value_labels=True)
        self.decades_card = chart_card("Decades", self.decades, " ")
        grid.addWidget(self.decades_card, 3, 2, 1, 2)
        # The chart Anime has against MAL (approved 2026-10-16; it took "Rated each year"'s
        # place, which was one bar when everything was rated the same year). It follows
        # the Films | TV switch.
        self.vs_crowd = BandChart(
            theme.TEAL, "Nothing rated with a TMDB average yet", crowd="TMDB", noun="film",
            scores="ratings",
        )  # fmt: skip
        self.vs_crowd.setMinimumHeight(140)
        self.vs_crowd_card = chart_card("Your rating vs TMDB", self.vs_crowd, " ")
        grid.addWidget(self.vs_crowd_card, 3, 4, 1, 2)

        people_box = QWidget()
        self.people_grid = QGridLayout(people_box)
        self.people_grid.setContentsMargins(0, 4, 0, 0)
        self.people_grid.setSpacing(10)
        self.people = [PersonCard(ctx, theme.TEAL) for _ in range(screenstats.PEOPLE_SHOWN)]
        for i, card in enumerate(self.people):
            self.people_grid.addWidget(card, i // PEOPLE_COLUMNS, i % PEOPLE_COLUMNS)
        for column in range(PEOPLE_COLUMNS):
            self.people_grid.setColumnStretch(column, 1)
        self.people_card = chart_card("Directors you keep coming back to", people_box, " ")
        grid.addWidget(self.people_card, 4, 0, 1, 6)

        for row, weight in enumerate((0, 0, 4, 2, 2)):
            grid.setRowStretch(row, weight)
        for column in range(6):
            grid.setColumnStretch(column, 1)
        self.kind_switch.set_value("movie")

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.span = None  # "Report pages open on", once its years are known
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            years = screenstats.years(conn)
            if self.span is None:
                latest = settings.get(conn, "report_span") == "year" and years
                self.span = years[0] if latest else "all"
            if self.span != "all" and self.span not in years:
                self.span = "all"
            stats = screenstats.load(conn, self.span)
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
            stats = screenstats.load(conn, span)
        finally:
            conn.close()
        if self.isVisible():
            motion.crossfade(self, lambda: self.show_stats(stats))
        else:
            self.show_stats(stats)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.kind_switch.set_value(kind)
        if self.stats is not None:
            self._show_spread(self.stats)

    def _show_spread(self, s: ScreenStats) -> None:
        spread = s.film_spread if self.kind == "movie" else s.tv_spread
        noun = "films" if self.kind == "movie" else "shows"
        self.vs_crowd.noun = noun[:-1]
        self.vs_crowd.set_bands(s.film_bands if self.kind == "movie" else s.tv_bands)
        self.vs_crowd_card.note.setText(
            f"Your average for {noun} TMDB rates about the same. Bars cover the middle half "
            "of your ratings."
        )
        tips = [f"{(i + 1) / 2:g}: {n:,} {noun}" for i, n in enumerate(spread)]
        self.spread.set_data(spread if any(spread) else [], RATING_LABELS, tips=tips)
        average, diff = (
            (s.film_average, s.film_diff) if self.kind == "movie" else (s.tv_average, s.tv_diff)
        )
        self.spread_card.note.setText(
            f"Your average {average:.1f}"
            + (f", {diff:+.1f} against TMDB" if diff is not None else "")
            if average is not None
            else "Half a point to 10"
        )

    def show_stats(self, s: ScreenStats) -> None:
        self.stats = s
        self.span_bar.show_span(s.span)

        def tile(value: str, caption: str, detail: str = "") -> charts.Tile:
            return charts.Tile(value, caption, "screen", detail=detail)

        when = "all time" if s.span == "all" else f"in {s.span}"  # like Music's plays
        values = [
            tile(f"{s.films:,}", "films rated", when),
            tile(f"{s.shows:,}", "TV shows rated", when),
            tile(
                f"~{s.film_hours:,.0f}" if s.film_hours else "0",
                "hours of film",
                "from TMDB running times",
            ),  # fmt: skip
            tile(
                f"{s.film_average:.1f}" if s.film_average is not None else "-",
                "your film average",
                "out of 10",
            ),  # fmt: skip
            tile(
                f"{s.film_diff:+.1f}" if s.film_diff is not None else "-",
                "vs the TMDB average",
                "your films",
            ),  # fmt: skip
            tile(s.top_decade or "-", "favorite decade", "by release year"),
        ]
        for widget, value in zip(self.tiles, values, strict=True):
            widget.set(value)

        self.wall.set_items([rated_item(r) for r in s.rated])
        self.wall_card.title.setText("Rated" if s.span == "all" else f"Rated in {s.span}")
        count = len(s.rated)
        self.wall_card.note.setText(
            f"{count:,} {'title' if count == 1 else 'titles'}, newest first, with your rating."
            if count
            else "Nothing rated in this span."
        )
        self._show_spread(s)

        self.genres.set_shares(s.genres)
        self.genres_card.note.setText(
            f"Mostly {s.genres[0].name.lower()}" if s.genres else "From TMDB's genres"
        )
        self.decades.set_data(
            [n for _, n in s.decades] if s.decades else [], [d for d, _ in s.decades],
            unit="titles",
        )  # fmt: skip
        self.decades_card.note.setText(
            f"Mostly the {s.top_decade}" if s.top_decade else "By release year"
        )

        for i, card in enumerate(self.people):
            person = s.people[i] if i < len(s.people) else None
            if person is not None:
                films = f"{person.films} films"
                card.set_person(
                    person.name,
                    f"{films} · {person.average:.1f} from you",
                    [rated_item(r) for r in person.posters],
                    person.ratings,
                    "Your ratings for their films, in the order you rated them.",
                )
            card.setVisible(person is not None)
        lead = s.people[0] if s.people else None
        self.people_card.note.setText(
            f"{lead.name} leads with {lead.films} films"
            if lead
            else f"Nobody yet: a director shows up here from {screenstats.PERSON_MIN} rated films."
        )
