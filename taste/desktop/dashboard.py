"""Dashboard page layout, data refresh, and sync actions."""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taste import (
    activity,
    airing,
    bookstats,
    charts,
    local_time,
    overview,
    persona,
    settings,
    source_status,
)
from taste.desktop import motion, theme
from taste.desktop.cards import GenreCard
from taste.desktop.context import AppContext
from taste.desktop.dashboard_cards import (
    RECAP_NAME,
    AiringCard,
    Banner,
    CrowdCard,
    GettingStarted,
    MonthCard,
    PastRecaps,
    Shelf,
    SyncStrip,
    TasteCard,
    book_caption,
    finished_caption,
    finished_tip,
    rated_caption,
)
from taste.desktop.plots import BarChart
from taste.desktop.widgets import (
    Card,
    LazyPage,
    ScrollArea,
    card_title,
    heading,
    plain_tip,
)
from taste.desktop.workers import SyncWorker

POSTER = QSize(104, 148)
COVER = QSize(104, 104)
SCOPE_TIP = "Most played on Last.fm in the last {days} days. {note}"
# Enough for a very wide window. Shelves show as many as fit, and an item downloads
# its picture only once it's on screen.
SHELF_MAX = 30
WIDE = 1500  # page width from which three cards go across instead of two
TRAITS_IN_ONE_ROW = 2100  # page width from which Your taste's traits fit in one row
COLUMNS = 12  # grid columns: thirds are 4, halves 6
WEEKS_MIN_BAR = 18


class DashboardPage(LazyPage, QWidget):
    sync_started = Signal()
    sync_finished = Signal(dict)
    recs_updated = Signal()
    status_changed = Signal(str, str, str)  # short line, dot state, details (the sidebar)
    open_page = Signal(str)  # a page name, from a card's link

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.worker: SyncWorker | None = None
        self.outcome: bool | None = None  # how the last sync in this session went
        self.is_blocked = lambda: False  # the main window points this at "are recs refreshing?"
        self.wide: bool | None = None
        self.page_width = 0
        self.has_data = True
        self.cards_on = set(settings.DASHBOARD_CARDS)  # the profile's "Dashboard cards"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self.scroll)
        body = QWidget()
        self.scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self.title = heading("Dashboard", 28)
        top.addWidget(self.title)
        top.addStretch()
        # One button for everything; its arrow lists each connected source.
        self.sync_button = QToolButton()
        self.sync_button.setObjectName("SyncButton")
        self.sync_button.setText("Sync everything")
        self.sync_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.sync_button.clicked.connect(lambda: self.start_sync("all"))
        self.sync_menu = QMenu(self.sync_button)
        self.sync_menu.aboutToShow.connect(motion.menu_effects)
        self.sync_button.setMenu(self.sync_menu)
        top.addWidget(self.sync_button)
        layout.addLayout(top)

        self.strip = SyncStrip()
        self.progress = self.strip.progress
        self.log = self.strip.log
        layout.addWidget(self.strip)

        # On the status line, not a row of its own: a full library at 2560 x 1380 has
        # about 15 px to spare, and a row of its own (52 px) made the page scroll.
        self.banner = Banner()
        self.strip.row.insertWidget(2, self.banner)
        self.banner.setVisible(False)
        self.past = PastRecaps()
        self.strip.row.insertWidget(3, self.past)
        self.past.setVisible(False)

        self.welcome = GettingStarted()
        self.welcome.open_settings.connect(lambda: self.open_page.emit("Settings"))
        self.welcome.sync_all.connect(lambda: self.start_sync("all"))
        layout.addWidget(self.welcome, 1)

        self.crowd = CrowdCard()

        self.scores = Card()
        self.scores.body.addWidget(card_title("Your scores, 1 to 10"))
        self.score_chart = BarChart(theme.LILAC, empty_text="No scores yet", value_labels=True)
        self.score_chart.setMinimumHeight(80)
        self.scores.body.addWidget(self.score_chart, 1)

        self.airing = AiringCard(ctx)
        self.airing_shown = False
        self.month = MonthCard()
        self.month.open_recap.connect(lambda: self.open_page.emit(RECAP_NAME))

        self.recent = Shelf(ctx, "Recently finished", POSTER, theme.LILAC)
        self.repeat = Shelf(
            ctx, f"On repeat, last {overview.ON_REPEAT_DAYS} days", COVER, theme.CORAL, 2
        )
        self.weeks = Card()
        self.weeks.body.addWidget(card_title("Plays per week"))
        self.week_chart = BarChart(theme.CORAL, scale=True, min_bar=WEEKS_MIN_BAR)
        self.week_chart.setMinimumHeight(110)
        self.weeks.body.addWidget(self.week_chart, 1)
        self.genres = GenreCard(fit=True)
        # Books, films, and TV join the grid once they have data (a row of two when wide).
        self.books = Shelf(ctx, "Recently read", POSTER, theme.AMBER, 2)
        self.films = Shelf(ctx, "Films you rated lately", POSTER, theme.TEAL, 2)
        self.tv = Shelf(ctx, "TV you rated lately", POSTER, theme.TEAL, 2)
        self.extra = [self.books, self.films, self.tv]
        self.extra_shown: list[Shelf] = []
        # Across the top once there's enough to say something (taste.persona).
        self.taste = TasteCard()
        self.taste_shown = False

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(theme.GAP)
        self.grid.setVerticalSpacing(theme.GAP)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)
        self.cards = [
            self.crowd,
            self.scores,
            self.month,
            self.airing,
            self.recent,
            self.repeat,
            self.weeks,
            self.genres,
        ]
        # The "Dashboard cards" setting's keys, and the card each one names.
        self.keyed = {
            "crowd": self.crowd,
            "scores": self.scores,
            "month": self.month,
            "airing": self.airing,
            "recent": self.recent,
            "repeat": self.repeat,
            "weeks": self.weeks,
            "genres": self.genres,
            "books": self.books,
            "films": self.films,
            "tv": self.tv,
        }
        layout.addLayout(self.grid, 1)
        self._arrange(wide=False)
        self.scroll.viewport_resized.connect(self._on_width)

    # -- layout -------------------------------------------------------------

    def _arrange(self, wide: bool, force: bool = False) -> None:
        """Place the cards. Wide: three across with the genres down the right side.
        Narrow: two across. Rows share the height: picture rows get more of it. Shelves
        for books, films, and TV go below, side by side when wide.

        Cards the profile hides (the "Dashboard cards" setting) drop out, and the ones
        left in a row widen to share it; a row with nothing left goes."""
        if wide == self.wide and not force:
            return
        self.wide = wide
        for card in [self.taste, *self.cards, *self.extra]:
            self.grid.removeWidget(card)
        side = self.genres if wide and "genres" in self.cards_on else None
        if wide:
            rows = [
                (
                    [(self.crowd, 3), (self.scores, 2), (self.month, 4), (self.airing, 3)]
                    if self.airing_shown
                    else [(self.crowd, 4), (self.scores, 4), (self.month, 4)],
                    2,
                    COLUMNS,
                ),
                ([(self.recent, 8)], 3, COLUMNS - 4 if side else COLUMNS),
                ([(self.repeat, 5), (self.weeks, 3)], 3, COLUMNS - 4 if side else COLUMNS),
            ]
        else:
            rows = [
                ([(self.crowd, 6), (self.scores, 6)], 2, COLUMNS),
                (
                    # This month's five numbers need about 530 px: 7 of 12 columns keeps
                    # them from pushing the page wider than an 1180 px window (2.5).
                    [(self.month, 7), (self.airing, 5)]
                    if self.airing_shown
                    else [(self.month, 7), (self.weeks, 5)],
                    2,
                    COLUMNS,
                ),
                ([(self.recent, 12)], 3, COLUMNS),
                ([(self.repeat, 6), (self.genres, 6)], 3, COLUMNS),
            ]
            if self.airing_shown:
                rows.append(([(self.weeks, 12)], 2, COLUMNS))
        if self.taste_shown:
            rows.insert(0, ([(self.taste, COLUMNS)], 2, COLUMNS))
        # Wide: books, films, and TV side by side in one row, so a full screen fits
        # everything without scrolling. Narrow: one to a row.
        extras = [(shelf, 1) for shelf in self.extra_shown]
        if extras:
            groups = [extras] if wide else [[e] for e in extras]
            rows += [(group, 3, COLUMNS) for group in groups]
        places, stretches = [], []
        side_rows = []
        for cards, stretch, width in rows:
            cards = [(c, span) for c, span in cards if self._card_on(c)]
            if not cards:
                continue
            row = len(stretches)
            if width < COLUMNS:
                side_rows.append(row)
            total = sum(span for _, span in cards)
            column = 0
            for i, (card, span) in enumerate(cards):
                # Each widens by the same share; the last takes what rounding leaves.
                share = width - column if i == len(cards) - 1 else round(span * width / total)
                places.append((card, row, column, 1, share))
                column += share
            stretches.append(stretch)
        if side is not None:
            if side_rows:
                places.append((side, side_rows[0], COLUMNS - 4, len(side_rows), 4))
            else:  # nothing beside it: a row of its own
                places.append((side, len(stretches), 0, 1, COLUMNS))
                stretches.append(3)
        for card, row, column, row_span, columns in places:
            self.grid.addWidget(card, row, column, row_span, columns)
        for row in range(self.grid.rowCount()):
            self.grid.setRowStretch(row, stretches[row] if row < len(stretches) else 0)

    def _card_on(self, card: QWidget) -> bool:
        """Whether the profile's "Dashboard cards" setting shows this card. Your taste
        is already left out when hidden (taste_shown)."""
        if card is self.airing and not self.airing_shown:
            return False
        return card is self.taste or any(
            card is shown and key in self.cards_on for key, shown in self.keyed.items()
        )

    def _on_width(self, width: int) -> None:
        self.page_width = width
        self._arrange(width >= WIDE)
        self.taste.set_columns(self._trait_columns())

    def _trait_columns(self) -> int:
        """Traits in one row of six on a big screen, else rows of three."""
        return 6 if self.page_width >= TRAITS_IN_ONE_ROW else 3

    def show_cards(self, has_data: bool) -> None:
        self.has_data = has_data
        self.welcome.setVisible(not has_data)
        self.taste.setVisible(has_data and self.taste_shown)
        for card in self.cards:
            card.setVisible(has_data and self._card_on(card))
        for shelf in self.extra:
            shelf.setVisible(has_data and shelf in self.extra_shown)

    # -- data ---------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.outcome = None
        self.strip.clear()
        self.banner.setVisible(False)
        self.past.hide_notice()
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        now = self.ctx.now()
        conn = self.ctx.connect(self.profile_id)
        try:
            cfg = settings.get_all(conn)
            count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            entries = count("SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NULL")
            scored = count("SELECT COUNT(*) FROM core_ratings WHERE source='mal'")
            plays = count("SELECT COUNT(*) FROM core_behavior_events WHERE source='lastfm'")
            summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
            book_stats = bookstats.load(conn)  # loaded once for Your taste and the crowd card
            counts = overview.artist_play_counts(conn)
            crowd = overview.vs_crowd(conn, books=book_stats, counts=counts)
            sources = source_status.load(conn, self.profile_id, self.ctx.store)
            interrupted = count("SELECT COUNT(*) FROM sync_lastfm_windows WHERE status = 'open'")
            distribution = overview.score_distribution(conn)
            broadcasts = airing.load(conn, now)
            finished = overview.recently_finished(conn, limit=SHELF_MAX)
            albums = overview.on_repeat(conn, now, limit=SHELF_MAX)
            generous, harsh = overview.genre_lean(conn)
            scope_note = settings.lastfm_scope_note(conn)
            glance = activity.glance(conn, activity.current(conn, "month", now))
            profile = persona.load(conn, books=book_stats, counts=counts)
            books = activity.recently_read(conn, SHELF_MAX)
            films = activity.recently_rated(conn, "movie", SHELF_MAX)
            shows = activity.recently_rated(conn, "tv", SHELF_MAX)
            weeks, partial = charts.plays_by_period(conn, activity.today(conn, now))
        finally:
            conn.close()

        self._fill_sync_menu([s for s in sources if s.connected])
        cards_on = set(cfg["dashboard_cards"].split(","))
        shown = [
            shelf
            for shelf, items, key in (
                (self.books, books, "books"), (self.films, films, "films"), (self.tv, shows, "tv")
            )
            if items and key in cards_on
        ]  # fmt: skip
        taste = profile is not None and "taste" in cards_on
        airing_shown = bool(broadcasts) and "airing" in cards_on
        if (
            shown != self.extra_shown
            or taste != self.taste_shown
            or cards_on != self.cards_on
            or airing_shown != self.airing_shown
        ):
            self.airing_shown = airing_shown
            self.extra_shown = shown
            self.taste_shown = taste
            self.cards_on = cards_on
            self._arrange(bool(self.wide), force=True)
        self.airing.set_shows(broadcasts if airing_shown else [])
        self.taste.set_persona(profile, self._trait_columns())
        self.show_cards(bool(entries or plays or books or films or shows))
        self.books.set_items(
            [(b.cover_url, b.title, book_caption(b), f"{b.title}\n{b.author}") for b in books],
            "",
        )
        self.films.set_items([(f.poster_url, f.title, rated_caption(f), "") for f in films], "")
        self.tv.set_items([(f.poster_url, f.title, rated_caption(f), "") for f in shows], "")
        self.crowd.set_numbers(crowd)
        if any(distribution):
            self.score_chart.set_data(
                distribution,
                [str(s) for s in range(1, 11)],
                tips=[
                    f"{s}: {n} {'show' if n == 1 else 'shows'}"
                    for s, n in enumerate(distribution, 1)
                ],
            )
        else:
            self.score_chart.set_data([], [])  # draws "No scores yet"
        self.month.set_glance(glance)
        series = weeks["week"]
        names = [charts.period_label("week", key) for key, _ in series]
        self.week_chart.set_data(
            [n for _, n in series],
            [axis for axis, _ in names],
            partial_last=partial["week"],
            tips=[
                f"{hover}: {n:,} {'play' if n == 1 else 'plays'}"
                for (_, hover), (_, n) in zip(names, series, strict=True)
            ],
        )

        self.recent.set_items(
            [(s.poster_url, s.title, finished_caption(s), finished_tip(s)) for s in finished],
            "Sync MyAnimeList, and the shows you finish show up here."
            if not entries
            else "No finished shows on your list yet.",
        )
        self.repeat.set_items(
            [(a.cover_url, a.title, f"{a.artist}\n{a.plays:,} plays", "") for a in albums],
            "Sync Last.fm to see the albums you've played most lately."
            if not plays
            else f"No album plays on Last.fm in the last {overview.ON_REPEAT_DAYS} days.",
        )
        self.repeat.setToolTip(
            plain_tip(SCOPE_TIP.format(days=overview.ON_REPEAT_DAYS, note=scope_note))
        )
        self.genres.set_genres(
            generous,
            harsh,
            "Sync MyAnimeList to see which genres you rate above or below the crowd."
            if not scored
            else f"No genre has {cfg['genre_min_sample']} scored shows yet. The genre minimum "
            "sample in Settings sets how many it takes.",
            usual=summary["avg_diff"] + 0.0 if summary and summary["shows_scored"] else None,
        )

        if not self.is_syncing():
            self._show_status(sources, cfg["timezone"], interrupted, now)

    def refresh_status(self) -> None:
        """Only the sync menu and status line (a key, username, or account changed), not
        the whole page: that took about 0.3 s on a full library."""
        if not self.profile_id or self.is_syncing():
            return
        now = self.ctx.now()
        conn = self.ctx.connect(self.profile_id)
        try:
            sources = source_status.load(conn, self.profile_id, self.ctx.store)
            tz_name = settings.get(conn, "timezone")
            interrupted = conn.execute(
                "SELECT COUNT(*) FROM sync_lastfm_windows WHERE status = 'open'"
            ).fetchone()[0]
        finally:
            conn.close()
        self._fill_sync_menu([s for s in sources if s.connected])
        self._show_status(sources, tz_name, interrupted, now)

    def lazy_refresh(self) -> None:  # LazyPage: after a sync, when shown
        self.refresh()

    def while_hidden(self) -> None:
        self.refresh_status()  # the sidebar's last-sync line stays current

    def _show_status(self, sources, tz_name: str, interrupted: int, now) -> None:
        line = source_status.synced_line(sources, tz_name, now)
        notes = []
        if self.outcome is False:
            notes.append("The last sync had problems. Show log says what happened.")
        if interrupted:
            notes.append("A Last.fm sync was interrupted; the next one resumes it.")
        text = " ".join([*notes[:1], line.text, *notes[1:]])
        self.strip.text.setText(text)
        self.strip.text.setToolTip(plain_tip(line.tip))
        details = "\n".join([*notes, line.tip])  # each source's own time, on hover
        latest = line.latest
        if self.outcome is False:
            short, state = "Last sync had problems", "error"
        elif latest:
            short = f"Synced {local_time.describe(latest, tz_name, now)}"
            state = "ok"
        else:
            short, state = "Not synced yet", "idle"
        self.status_changed.emit(short, state, details)

    # -- syncing ------------------------------------------------------------

    def is_syncing(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def set_blocked(self, blocked: bool) -> None:
        self.sync_button.setEnabled(not blocked and not self.is_syncing())
        self.sync_button.setToolTip("Wait for the recommendations to finish." if blocked else "")

    def _fill_sync_menu(self, connected: list[source_status.SourceStatus]) -> None:
        """The arrow's menu: each connected source, or the way to Settings."""
        self.sync_menu.clear()
        for status in connected:
            action = self.sync_menu.addAction(theme.source_dot(status.source), status.label)
            action.triggered.connect(lambda _=False, s=status.source: self.start_sync(s))
        if not connected:
            action = self.sync_menu.addAction("Add your sources in Settings")
            action.triggered.connect(lambda: self.open_page.emit("Settings"))

    def start_sync(self, source: str) -> None:
        if not self.profile_id or self.is_syncing() or self.is_blocked():
            return
        self.strip.start()
        self.sync_button.setEnabled(False)
        self.worker = SyncWorker(
            self.profile_id,
            source,
            self.ctx.store,
            self.ctx.client_factory,
            self.ctx.sync_kwargs,
            self.ctx.recs_kwargs,
        )
        self.worker.recs_updated.connect(self.recs_updated)
        self.worker.message.connect(self.strip.message)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self.sync_started.emit()
        self.status_changed.emit("Syncing…", "running", "")

    def _on_done(self, results: dict) -> None:
        self.worker.wait()
        self.sync_button.setEnabled(not self.is_blocked())
        self.outcome = bool(results) and all(results.values())
        self.strip.finish(self.outcome)
        self.refresh_later()  # a Sync now from Settings: the rest when the Dashboard shows
        self.sync_finished.emit(results)
