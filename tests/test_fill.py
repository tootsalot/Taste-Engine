"""Every page uses the whole window: little bare background, at full screen and smaller.

A made-up profile the size of a real library (tests/fill_world.py) goes in through
the real sync code, then each page is drawn offscreen at 2560 x 1380 (full screen
on a 2560 x 1440 monitor, after the title bar and taskbar) and 1920 x 1040, and
the share of bare background is measured in 40 px squares. Since 2.1 empty space
inside cards counts too (a card stretched over nothing used to pass), outside the
charts, whose open plot area is part of reading them.
"""

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QScrollBar

from taste.desktop.books_view import CoverWall
from taste.desktop.cards import RankList
from taste.desktop.dashboard_cards import SyncStrip
from taste.desktop.main_window import PAGES
from taste.desktop.plots import BandChart, BarChart, LeanBar
from tests.fake_images import FakeImageSession
from tests.fill_world import World, bare_share, empty_share
from tests.test_desktop import set_up_profile

SIZES = ((2560, 1380), (1920, 1040))
# 2.4 widened the gaps between cards from 12 to 18 px (approved from a mockup,
# 2026-10-24), and gaps are bare background, so the Reports limit moved from 12% to 14%.
LIMITS = {"For You": 0.10, "Anime": 0.14, "Music": 0.14, "Books": 0.14, "Films and TV": 0.14}
LIMIT = 0.15
# Bare background or empty card surface, outside charts (2.1). Set just above what the
# made-up library measures after the cleanup (2026-10-16), so no page quietly gets
# emptier. For You's 30 picks fill three rows at full screen and can't grow without a
# fourth; Settings in Simple mode has less to show than a full screen holds.
# The 2.4 spacing (gaps, card padding) moved For You from 33% to 37% and the Dashboard
# from 13% to 16% on Windows; Linux's font rendering measures the Dashboard about a point
# higher (16.5% to 17% in CI), so its limit is 18%.
EMPTY_LIMITS = {"For You": 0.37, "Rev": 0.30, "Settings": 0.55, "Dashboard": 0.18}
EMPTY_LIMIT = 0.27  # the four Reports pages (0.25 before the 2.4 spacing)
CHARTS = (BarChart, BandChart, LeanBar, RankList, CoverWall)


def pump(ms: int = 300) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def make_library(qtbot, monkeypatch, tmp_path, extras: bool):
    world = World()
    world.install(monkeypatch)
    win = world.window(FakeImageSession())
    qtbot.addWidget(win)
    set_up_profile(win)
    if extras:
        world.install_extras(win, tmp_path)  # books and a connected TMDB account
    win.resize(1280, 860)
    win.show()
    qtbot.waitExposed(win)
    with qtbot.waitSignal(win.dashboard.sync_finished, timeout=120_000):
        win.dashboard.start_sync("all")
    return win


@pytest.fixture
def library(qtbot, monkeypatch, tmp_path):
    """Anime, music, books, films, and TV."""
    win = make_library(qtbot, monkeypatch, tmp_path, extras=True)
    yield win
    win.ctx.shutdown()


@pytest.fixture
def anime_and_music(qtbot, monkeypatch, tmp_path):
    """Only MAL and Last.fm: the Dashboard has no extra shelves, so its rows fit the window."""
    win = make_library(qtbot, monkeypatch, tmp_path, extras=False)
    yield win
    win.ctx.shutdown()


def test_every_page_fills_the_window(library):
    win = library
    found, empty, scrolls = {}, {}, {}
    for width, height in SIZES:
        win.resize(width, height)
        pump()
        for row, page in enumerate(PAGES):
            win.sidebar.setCurrentRow(row)
            pump()
            if page == "Rev":
                for _ in range(60):  # the cards draw in the background
                    if win.recap.deck.cards:
                        break
                    pump(100)
                pump()
            found[(page, width)] = bare_share(win.stack.currentWidget())
            empty[(page, width)] = empty_share(win.stack.currentWidget(), ignore=CHARTS)
            if page == "Dashboard" and width >= 2560:
                # A full-screen Dashboard fits without scrolling, all four media and Your
                # taste included. (1920 x 1040 can't hold every shelf at its smallest.)
                scrolls[width] = win.dashboard.scroll.verticalScrollBar().maximum()
    too_bare = {
        key: f"{share:.0%}" for key, share in found.items() if share > LIMITS.get(key[0], LIMIT)
    }
    assert not too_bare, f"pages with too much bare background: {too_bare}"
    too_empty = {
        key: f"{share:.0%}"
        for key, share in empty.items()
        if share > EMPTY_LIMITS.get(key[0], EMPTY_LIMIT)
    }
    assert not too_empty, f"pages with too much empty space, cards included: {too_empty}"
    assert not any(scrolls.values()), f"the Dashboard scrolls by {scrolls} px"


def test_a_sync_never_makes_the_dashboard_scroll(library):
    win = library
    dash = win.dashboard
    win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    win.resize(2560, 1380)
    pump()
    dash.strip.start()  # a sync running: the status line and its progress bar
    dash.strip.message("Last.fm: page 3 of 40")
    pump()
    running = dash.scroll.verticalScrollBar().maximum()
    dash.strip.finish(False)  # it had problems
    pump()
    problems = dash.scroll.verticalScrollBar().maximum()
    dash.banner.show_text("Your October recap is ready.")  # made in the background
    pump()
    banner = dash.scroll.verticalScrollBar().maximum()
    assert dash.strip.log.isHidden() and not dash.banner.isHidden()
    assert (running, problems, banner) == (0, 0, 0), "the Dashboard scrolls during a sync"
    # 2.3: the past recaps offer joins them on the same line, then shows its progress. It
    # adds nothing to the page's height (at 1920 x 1040 a full library scrolls anyway).
    for width, height in ((2560, 1380), (1920, 1040)):
        win.resize(width, height)
        dash.past.hide_notice()
        dash.strip.start()
        dash.strip.message("Last.fm: page 3 of 40")
        pump()
        without = dash.scroll.verticalScrollBar().maximum()
        dash.strip.start()
        dash.strip.message("Last.fm: page 3 of 40")
        dash.past.offer("Make recaps for your past months? 31 months and 3 years, about a minute.")
        pump()
        offered = dash.scroll.verticalScrollBar().maximum()
        dash.past.making(7, 34, "March 2025")
        pump()
        making = dash.scroll.verticalScrollBar().maximum()
        assert (offered, making) == (without, without), f"the notice adds height at {width}"
        assert width != 2560 or without == 0
        dash.strip.finish(True)
    dash.past.hide_notice()
    win.resize(2560, 1380)
    pump()
    dash.banner.hide_banner()
    # Asked for, the log opens at full height in the page, even if that scrolls a little.
    dash.strip.show_log(True)
    pump()
    assert dash.strip.log.isVisible() and dash.strip.log.height() == SyncStrip.LOG_HEIGHT
    dash.strip.show_log(False)
    pump()
    assert dash.scroll.verticalScrollBar().maximum() == 0


def test_a_big_window_gets_bigger_art_and_more_genres(anime_and_music):
    win = anime_and_music
    dash = win.dashboard
    win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    win.resize(1280, 860)
    pump()
    small_poster = dash.recent.art_size.height()
    small_genres = len(dash.genres.shown_rows("harsh")) + len(dash.genres.shown_rows("generous"))
    win.resize(2560, 1380)
    pump()
    assert dash.wide
    assert dash.recent.art_size.height() > small_poster
    shown = len(dash.genres.shown_rows("harsh")) + len(dash.genres.shown_rows("generous"))
    assert shown > small_genres
    assert len(dash.genres.shown_rows("generous")) <= 10  # up to 10 each side
    # Shrinking again gives the room back: nothing holds the cards at their big size.
    win.resize(1280, 860)
    pump()
    assert dash.recent.art_size.height() == small_poster
    assert not dash.wide


def test_settings_flow_into_columns(library):
    win = library
    page = win.settings
    win.sidebar.setCurrentRow(PAGES.index("Settings"))
    win.resize(1280, 860)
    pump()
    assert page.layout_key == (1, 1)
    # One column: the cards take the page's whole width (the empty right column had a share).
    assert page.sources_card.width() >= page.scroll.viewport().width() - 2 * 28 - 2
    win.resize(2560, 1380)
    pump()
    assert page.layout_key == (2, 1)  # sources and data left, settings right
    # Appearance sits under the profile settings, so Simple mode's right side isn't bare.
    assert page.right.indexOf(page.appearance_card) > page.right.indexOf(page.form_card) >= 0
    page.set_mode("advanced")
    pump()
    assert page.layout_key == (2, 2)  # and Advanced splits the settings in two
    assert page.group_split is not None
    field = page.inputs["display_name"]
    assert field.width() <= 600  # fields keep a readable width
    page.set_mode("simple")
    pump()
    assert page.layout_key == (2, 1)


@pytest.mark.parametrize("size", [(1920, 1040), (1280, 860)])
def test_advanced_settings_never_scroll_sideways(library, size):
    # Dashboard cards' eleven long names, four to a row, once pushed the form wider than
    # a 1920 window and the Simple | Advanced switch off the edge.
    win = library
    page = win.settings
    win.sidebar.setCurrentRow(PAGES.index("Settings"))
    win.resize(*size)
    page.set_mode("advanced")
    pump()
    assert page.scroll.horizontalScrollBar().maximum() == 0
    boxes = page.inputs["dashboard_cards"]
    assert boxes.width() <= 600
    edge = page.scroll.viewport().width()
    for box in boxes.boxes.values():  # every checkbox fully inside the page, none clipped
        assert box.mapTo(page.scroll.viewport(), box.rect().topRight()).x() < edge
        assert box.width() >= box.sizeHint().width()


def test_each_medium_has_its_own_report_page(library):
    win = library
    page = win.reports
    for name, key in (("Anime", "anime"), ("Music", "music"), ("Books", "books"),
                      ("Films and TV", "screen")):  # fmt: skip
        win.sidebar.setCurrentRow(PAGES.index(name))
        pump()
        assert win.stack.currentWidget() is page
        assert page.stack.currentWidget() is page.pages[key]
        assert page.title.text() == name
    # One skeleton for every medium: six tiles across the top of each page.
    for view in (page.anime, page.music, page.books, page.screen):
        assert len(view.tiles) == 6 and all(t.value.text() for t in view.tiles)
    vs_mal = next(t for t in page.anime.tiles if t.caption.text() == "vs MAL")
    assert not vs_mal.trend.isHidden()  # points vs MAL by year
    assert page.books.tiles[0].caption.text() == "books read"
    assert page.screen.tiles[0].caption.text() == "films rated"


def test_books_films_and_tv_show_up_everywhere(library):
    win = library
    dash = win.dashboard
    # The sync ran while Settings was in front: each page caught up when opened.
    win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    assert dash.extra_shown == [dash.books, dash.films, dash.tv]
    # Your taste, across the top: a headline and traits that each carry a number.
    assert not dash.taste.isHidden() and dash.taste.headline.text().startswith("The ")
    assert len(dash.taste.traits) >= 2
    assert all(any(c.isdigit() for c in evidence) for _, evidence in dash.taste.traits)
    assert dash.taste.based_on.text().startswith("From ")
    assert dash.books.items and "Hardcover" in dash.books.items[0].caption.text()
    assert int(dash.month.numbers["books"][0].text()) >= 3  # at least three read this month
    assert not dash.month.numbers["films"][0].isHidden()
    # One Sync everything: the four sources are named once, and each on hover.
    assert dash.strip.text.text().startswith("Everything synced ")
    assert "TMDB synced" in dash.strip.text.toolTip()
    assert "Hardcover synced" in dash.strip.text.toolTip()
    reports = win.reports
    win.sidebar.setCurrentRow(PAGES.index("Books"))
    assert reports.books_section.isVisible()
    books = reports.books.stats
    assert books.unique == 130 and books.reads == 136  # six read twice
    assert sum(reports.books.stars.values) == 130
    assert books.audiobooks > 0 and books.genres and books.moods
    assert len(books.top_authors) == 6
    win.sidebar.setCurrentRow(PAGES.index("Films and TV"))
    assert reports.screen_section.isVisible()
    screen = reports.screen.stats
    assert (screen.films, screen.shows) == (120, 30)
    assert sum(reports.screen.spread.values) == 120  # Films first
    reports.screen.set_kind("tv")
    assert sum(reports.screen.spread.values) == 30
    # Directors grouped by name, each with two or more films, most first.
    names = [p.name for p in screen.people]
    assert len(names) == len(set(names)) and all(p.films >= 2 for p in screen.people)
    win.sidebar.setCurrentRow(PAGES.index("For You"))
    assert len(win.for_you.lists["film"].recs) > 0  # film suggestions from the TMDB account
    conn = win.ctx.connect(win.current_profile())
    try:
        from taste import recap
        from taste.activity import Period

        october = recap.build(conn, Period("month", "2026-10"), win.ctx.now())
    finally:
        conn.close()
    assert {"books", "films"} <= set(october.cards)
    assert dict((c, n) for n, c in october.cover_numbers)["books read"] == (
        dash.month.numbers["books"][0].text()
    )


def test_dashboard_cards_can_be_hidden_and_the_rest_share_the_room(library):
    from taste import settings as registry

    win = library
    dash = win.dashboard
    win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    win.resize(2560, 1380)
    pump()
    conn = win.ctx.connect(win.current_profile())
    keep = [k for k in registry.DASHBOARD_CARDS if k not in ("genres", "scores", "tv")]
    registry.set_value(conn, "dashboard_cards", keep)
    conn.close()
    dash.refresh()
    pump()
    assert dash.genres.isHidden() and dash.scores.isHidden() and dash.tv.isHidden()
    assert not dash.crowd.isHidden() and not dash.recent.isHidden()
    page = dash.scroll.viewport().width()
    # Without the genres down the right, Recently finished takes the whole width.
    assert dash.recent.width() > page * 0.9
    # Crowd, This month, and Airing now share the first row between them.
    assert dash.crowd.width() + dash.month.width() + dash.airing.width() > page * 0.9
    assert dash.films.width() > page * 0.4  # books and films share their row
    assert bare_share(win.stack.currentWidget()) < LIMIT


def test_tv_suggestions_fill_the_window(library):
    win = library
    win.sidebar.setCurrentRow(PAGES.index("For You"))
    page = win.for_you
    page.tabs.setCurrentWidget(page.lists["tv"])
    assert page.lists["tv"].recs
    for width, height in SIZES:
        win.resize(width, height)
        pump()
        assert bare_share(page) <= LIMITS["For You"]
        assert empty_share(page, ignore=CHARTS) <= EMPTY_LIMITS["For You"]


def test_airing_card_fits_beside_this_month_without_moving_the_page(library):
    win = library
    win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    dash = win.dashboard
    assert dash.airing.items and not dash.airing.isHidden()
    for width, height in SIZES:
        win.resize(width, height)
        pump()
        air_row = dash.grid.getItemPosition(dash.grid.indexOf(dash.airing))[0]
        month_row = dash.grid.getItemPosition(dash.grid.indexOf(dash.month))[0]
        assert air_row == month_row
        assert bare_share(dash) <= LIMIT
        assert empty_share(dash, ignore=CHARTS) <= EMPTY_LIMITS["Dashboard"]
        assert dash.scroll.horizontalScrollBar().maximum() == 0
        if width == 2560:
            assert dash.scroll.verticalScrollBar().maximum() == 0
        for state in ("sync", "error", "banner"):
            before = dash.scroll.verticalScrollBar().maximum()
            if state == "sync":
                dash.strip.start()
            elif state == "error":
                dash.strip.finish(False)
            else:
                dash.banner.show_text("Your October recap is ready.")
            pump()
            assert dash.scroll.verticalScrollBar().maximum() == before
        dash.strip.clear()
        dash.banner.hide_banner()
    conn = win.ctx.connect(win.current_profile())
    conn.execute("UPDATE stg_mal_anime SET airing_status = 'finished_airing'")
    conn.close()
    dash.refresh()
    assert dash.airing.isHidden() and dash.grid.indexOf(dash.airing) == -1
    for width, height in SIZES:
        win.resize(width, height)
        pump()
        assert bare_share(dash) <= LIMIT
        assert empty_share(dash, ignore=CHARTS) <= EMPTY_LIMITS["Dashboard"]
        assert dash.scroll.horizontalScrollBar().maximum() == 0
        if width == 2560:
            assert dash.scroll.verticalScrollBar().maximum() == 0


def test_a_small_window_never_scrolls_sideways(library):
    """2.5: at the app's opening size the status line's notices used to push the
    Dashboard wider than the window, cutting off every card's right edge."""
    win = library
    win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    dash = win.dashboard
    dash.banner.show_text("Your September recap is ready.")
    dash.past.offer("Make recaps for your past months? 49 months and 4 years, about a minute.")
    for size in ((1180, 820), (1280, 800)):
        win.resize(*size)
        pump()
        assert dash.scroll.horizontalScrollBar().maximum() == 0, size
        for row in range(len(PAGES)):
            win.sidebar.setCurrentRow(row)
            pump(150)
            page = win.stack.currentWidget()
            for bar in page.findChildren(QScrollBar):
                if bar.orientation() == Qt.Orientation.Horizontal and bar.isVisible():
                    assert bar.maximum() == 0, (PAGES[row], size)
        win.sidebar.setCurrentRow(PAGES.index("Dashboard"))
