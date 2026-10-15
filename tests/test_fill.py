"""Every page uses the whole window: little bare background, at full screen and smaller.

A made-up profile the size of a real library (tests/fill_world.py) goes in through
the real sync code, then each page is drawn offscreen at 2560 x 1380 (full screen
on a 2560 x 1440 monitor, after the title bar and taskbar) and 1920 x 1040, and
the share of bare background is measured in 40 px squares.
"""

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from taste.desktop.main_window import PAGES
from tests.fake_images import FakeImageSession
from tests.fill_world import World, bare_share
from tests.test_desktop import set_up_profile

SIZES = ((2560, 1380), (1920, 1040))
LIMITS = {"For You": 0.10, "Anime": 0.12, "Music": 0.12, "Books": 0.12, "Films and TV": 0.12}
LIMIT = 0.15


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
    found, scrolls = {}, {}
    for width, height in SIZES:
        win.resize(width, height)
        pump()
        for row, page in enumerate(PAGES):
            win.sidebar.setCurrentRow(row)
            pump()
            found[(page, width)] = bare_share(win.stack.currentWidget())
            if page == "Dashboard" and width >= 2560:
                # A full-screen Dashboard fits without scrolling, all four media and Your
                # taste included. (1920 x 1040 can't hold every shelf at its smallest.)
                scrolls[width] = win.dashboard.scroll.verticalScrollBar().maximum()
    too_bare = {
        key: f"{share:.0%}" for key, share in found.items() if share > LIMITS.get(key[0], LIMIT)
    }
    assert not too_bare, f"pages with too much bare background: {too_bare}"
    assert not any(scrolls.values()), f"the Dashboard scrolls by {scrolls} px"


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
    win.resize(2560, 1380)
    pump()
    assert page.layout_key == (2, 1)  # keys and data left, settings right
    page.set_mode("advanced")
    pump()
    assert page.layout_key == (2, 2)  # and Advanced splits the settings in two
    assert page.group_split is not None
    field = page.inputs["display_name"]
    assert field.width() <= 600  # fields keep a readable width
    page.set_mode("simple")
    pump()
    assert page.layout_key == (2, 1)


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
    assert dash.extra_shown == [dash.books, dash.films, dash.tv]
    # Your taste, across the top: a headline and traits that each carry a number.
    assert not dash.taste.isHidden() and dash.taste.headline.text().startswith("The ")
    assert len(dash.taste.traits) >= 2
    assert all(any(c.isdigit() for c in evidence) for _, evidence in dash.taste.traits)
    assert dash.taste.based_on.text().startswith("From ")
    assert dash.books.items and "Hardcover" in dash.books.items[0].caption.text()
    assert int(dash.month.numbers["books"][0].text()) >= 3  # at least three read this month
    assert not dash.month.numbers["films"][0].isHidden()
    assert "TMDB synced" in dash.strip.text.text()
    assert "Hardcover synced" in dash.strip.text.text()
    reports = win.reports
    assert reports.books_section.isVisible() and reports.screen_section.isVisible()
    books = reports.books.stats
    assert books.unique == 130 and books.reads == 136  # six read twice
    assert sum(reports.books.stars.values) == 130
    assert books.audiobooks > 0 and books.genres and books.moods
    assert len(books.top_authors) == 6
    screen = reports.screen.stats
    assert (screen.films, screen.shows) == (120, 30)
    assert sum(reports.screen.spread.values) == 120  # Films first
    reports.screen.set_kind("tv")
    assert sum(reports.screen.spread.values) == 30
    # Directors grouped by name, each with two or more films, most first.
    names = [p.name for p in screen.people]
    assert len(names) == len(set(names)) and all(p.films >= 2 for p in screen.people)
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
