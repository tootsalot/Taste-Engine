"""The Dashboard: art, scores, genre lean, and the sync strip. Real widgets, fake APIs."""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt

from taste.desktop import theme
from taste.desktop.dashboard import finished_tip
from taste.desktop.main_window import PAGES
from taste.desktop.widgets import LeanBar
from taste.overview import FinishedShow
from tests import test_for_you
from tests.fake_images import image_bytes
from tests.fake_recs import MAL_CDN
from tests.test_desktop import set_up_profile
from tests.test_for_you import synced

# The For You world (fake MAL, Last.fm, and images), shared as fixtures.
images = test_for_you.images
window = test_for_you.window


def captions(shelf):
    return [(item.title.text(), item.caption.text()) for item in shelf.items]


def test_before_a_sync_every_card_says_what_fills_it(window):
    window.new_profile(("me", "Me"))
    window.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    dash = window.dashboard
    assert [b.text() for b in dash.sync_buttons.values()] == [
        "Sync MyAnimeList",
        "Sync Last.fm",
        "Sync everything",
    ]
    # No dash rendered as a giant bar: the number hides until there's a score.
    assert dash.critic_value.isHidden()
    assert "Sync MyAnimeList" in dash.critic_text.text()
    assert dash.score_chart.values == [] and dash.score_chart.empty_text == "No scores yet"
    assert dash.recent.items == [] and "Sync MyAnimeList" in dash.recent.empty.text()
    assert dash.repeat.items == [] and "Sync Last.fm" in dash.repeat.empty.text()
    assert "Sync MyAnimeList" in dash.genres.empty.text()
    assert dash.strip.text.text() == "MyAnimeList: never synced. Last.fm: never synced."
    assert dash.log.isHidden() and dash.strip.toggle.isHidden()


def test_a_sync_fills_the_cards(window, qtbot, images):
    images.add(MAL_CDN.format(101), image_bytes(fmt="JPEG"), "image/jpeg")
    synced(window, qtbot)
    window.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    dash = window.dashboard

    assert dash.critic_value.text() == "-0.28"
    assert dash.counts.text() == "6 on your list · 4 scored · 69 Last.fm plays"
    assert dash.score_chart.values == [0, 0, 0, 0, 1, 0, 1, 1, 1, 0]

    # The fake list has no finish dates, so all four share the same last edit time.
    assert captions(dash.recent) == [
        ("Example Action F", "You 8 · MAL 7.60"),
        ("Example Comedy B", "You 5 · MAL 7.00"),
        ("Example Drama A", "You 9 · MAL 8.00"),
        ("Example Mixed C", "You 7 · MAL 7.50"),
    ]
    drama = dash.recent.items[2]
    assert drama.title.textFormat() == Qt.TextFormat.PlainText
    qtbot.waitUntil(drama.art.has_picture, timeout=5000)

    # Only Seed One's album was played in the last 30 days (Old Days was 400 days ago).
    assert captions(dash.repeat) == [("Seed One LP", "Example Seed One\n10 plays")]
    assert "Last.fm" in dash.repeat.toolTip()

    # Same numbers as the Reports page: against the MAL community.
    assert dash.genres.rows("generous") == [("Action", "+0.40"), ("Drama", "+0.25")]
    assert dash.genres.rows("harsh") == [("Comedy", "-1.25")]


def test_the_log_opens_while_syncing_and_folds_away_after(window, qtbot):
    set_up_profile(window)
    dash = window.dashboard
    with qtbot.waitSignal(dash.sync_finished, timeout=15000):
        dash.start_sync("all")
        assert not dash.log.isHidden()
        assert not dash.progress.isHidden()
    assert dash.log.isHidden() and dash.progress.isHidden()
    assert dash.strip.dot.property("state") == "ok"
    text = dash.strip.text.text()
    assert "MyAnimeList synced " in text and "Last.fm synced " in text
    assert "UTC" not in text

    toggle = dash.strip.toggle
    assert not toggle.isHidden() and toggle.text() == "Show log"
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert not dash.log.isHidden() and toggle.text() == "Hide log"
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert dash.log.isHidden()


def test_the_log_stays_open_when_a_sync_has_problems(window, qtbot):
    set_up_profile(window)
    window.settings.remove_key("MAL_CLIENT_ID")
    dash = window.dashboard
    with qtbot.waitSignal(dash.sync_finished, timeout=15000) as blocker:
        dash.start_sync("mal")
    assert blocker.args[0] == {"mal": False}
    assert not dash.log.isHidden()
    assert "MyAnimeList:" in dash.log.toPlainText()
    assert dash.strip.dot.property("state") == "error"
    assert "problems" in dash.strip.text.text()


def test_poster_tooltips_name_the_main_title_when_the_card_shows_english():
    show = FinishedShow("In English", "Romaji Title", "2026-01-01 00:00:00", 8, 7.6, None)
    assert finished_tip(show) == "In English\nRomaji Title\nYou 8 · MAL 7.60"
    assert finished_tip(replace(show, title="Romaji Title")) == ""  # default tooltip


def test_lean_bars_put_zero_where_the_data_needs_it(qtbot):
    # All below the crowd (a harsh critic): zero sits at the right edge, bars grow left.
    harsh = LeanBar(-1.0, low=-2.0, high=0.0)
    qtbot.addWidget(harsh)
    harsh.resize(204, 14)
    rect = harsh.bar_rect()
    assert rect.right() == pytest.approx(202) and rect.width() == pytest.approx(100)
    # Mixed signs: zero in proportion, positive bars grow right.
    mixed = LeanBar(0.5, low=-1.0, high=1.0)
    qtbot.addWidget(mixed)
    mixed.resize(204, 14)
    rect = mixed.bar_rect()
    assert rect.left() == pytest.approx(102) and rect.width() == pytest.approx(50)
    assert LeanBar(0.0, low=0.0, high=0.0).bar_rect().isEmpty()


def test_stylesheet_shows_keyboard_focus_and_draws_control_arrows():
    for rule in (
        "QPushButton:focus",
        'QPushButton[role="link"]:focus',
        "QSpinBox::up-button",
        "QComboBox::drop-down",
    ):
        assert rule in theme.STYLESHEET


def test_scroll_bars_are_thin_with_no_default_groove():
    sheet = theme.STYLESHEET
    # Unstyled, Qt fills the track around the handle with a gray dotted pattern.
    assert "QScrollBar::add-page" in sheet and "QScrollBar::sub-page" in sheet
    assert "QScrollBar::handle:vertical:hover" in sheet
