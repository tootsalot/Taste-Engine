"""The transitions with motion on: what made 2.0's page changes, For You's cards, and
Rev's deck janky (said 2026-10-16) stays fixed. Real widgets, offscreen."""

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QStackedWidget

from taste.desktop import motion
from taste.desktop.recap_page import Deck
from tests.test_for_you import images, refreshed, synced, window  # noqa: F401


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def veils(widget):
    return [w for w in widget.findChildren(motion.Veil) if not w.isHidden()]


def test_switching_pages_mid_fade_never_stacks_fades(qtbot, monkeypatch):
    monkeypatch.setattr(motion, "override", "full")
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    pages = [QLabel(f"Page {i}") for i in range(3)]
    for page in pages:
        stack.addWidget(page)
    stack.resize(400, 300)
    stack.show()
    qtbot.waitExposed(stack)
    motion.crossfade(stack, lambda: stack.setCurrentIndex(1))
    pump(60)
    motion.crossfade(stack, lambda: stack.setCurrentIndex(2))
    # One veil fading the new page in; the first one was finished, not stacked under it.
    assert len(veils(stack)) == 1 and stack.currentIndex() == 2
    pump(motion.DURATION + 200)
    assert veils(stack) == []


def test_a_second_slide_starts_from_where_the_deck_is(qtbot, monkeypatch):
    monkeypatch.setattr(motion, "override", "full")
    deck = Deck()
    qtbot.addWidget(deck)
    card = QImage(108, 192, QImage.Format.Format_RGB32)
    card.fill(0x202030)
    deck.set_cards([card] * 5)
    deck.resize(800, 600)
    deck.show()
    qtbot.waitExposed(deck)
    deck.go(1)
    pump(motion.DURATION // 5)
    midway = deck.offset
    assert 0.2 < midway < 1  # still on its way to card 2
    deck.go(2)
    # No jump: card 3 is one card further than where the deck already was.
    assert abs(deck.offset - (midway + 1)) < 0.15
    pump(motion.DURATION + 200)
    assert deck.offset == 0 and deck.index == 2


def test_for_you_only_fades_in_cards_it_hasnt_shown(window, qtbot, monkeypatch):  # noqa: F811
    synced(window, qtbot)
    refreshed(window, qtbot)
    anime = window.for_you.lists["anime"]
    window.for_you.tabs.setCurrentWidget(anime)
    assert anime.tiles
    monkeypatch.setattr(motion, "override", "full")
    anime.render(anime._ctx, anime._on_open, anime._on_dismiss)  # same picks again
    assert not any(t.graphicsEffect() for t in anime.tiles)  # nothing blinks out and back
    anime.recs = anime.recs[1:]  # one hidden: the rest stay put
    anime.render(anime._ctx, anime._on_open, anime._on_dismiss)
    assert not any(t.graphicsEffect() for t in anime.tiles)


def test_settings_mode_switch_fades_once_and_is_remembered(qtbot, monkeypatch):
    from taste.desktop import ui_state
    from taste.desktop.context import AppContext
    from taste.desktop.main_window import PAGES, MainWindow
    from taste.secrets_store import SecretStore
    from tests.conftest import FakeKeyring

    win = MainWindow(AppContext(store=SecretStore(backend=FakeKeyring())))
    qtbot.addWidget(win)
    win.new_profile(("me", "Me"))
    win.resize(1600, 1000)
    win.show()
    qtbot.waitExposed(win)
    win.sidebar.setCurrentRow(PAGES.index("Settings"))
    monkeypatch.setattr(motion, "override", "full")
    page = win.settings
    page.set_mode("advanced")
    assert page.mode == "advanced" and len(veils(page.scroll.viewport())) == 1
    assert ui_state.load()["settings_mode"] == "advanced"
    pump(motion.PAGE_FADE + 150)
    assert veils(page.scroll.viewport()) == []


def test_a_page_deleted_mid_fade_leaves_nothing_behind(qtbot, monkeypatch):
    from shiboken6 import delete

    monkeypatch.setattr(motion, "override", "full")
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    stack.addWidget(QLabel("one"))
    stack.addWidget(QLabel("two"))
    stack.resize(200, 100)
    stack.show()
    qtbot.waitExposed(stack)
    motion.crossfade(stack, lambda: stack.setCurrentIndex(1))
    delete(stack)  # gone mid-fade, veil and animation with it
    other = QStackedWidget()
    qtbot.addWidget(other)
    other.addWidget(QLabel("three"))
    other.show()
    qtbot.waitExposed(other)
    motion.crossfade(other, lambda: other.setCurrentIndex(0))
    assert list(motion._veils) == [other]  # the dead entry was dropped


def test_the_deck_keeps_both_sides_filled_at_its_ends(qtbot):
    deck = Deck()
    qtbot.addWidget(deck)
    card = QImage(108, 192, QImage.Format.Format_RGB32)
    card.fill(0x202030)
    deck.set_cards([card] * 7)
    deck.resize(1750, 1150)  # Rev's deck at 2560 x 1380
    deck.show()
    qtbot.waitExposed(deck)
    first = deck.rect_of(0)
    assert first.left() >= 0 and first.center().x() < deck.width() / 2  # moved over
    assert deck.rect_of(1).center().x() > deck.width() / 2  # a neighbor fills the right
    deck.go(3)
    assert abs(deck.rect_of(3).center().x() - deck.width() / 2) < 1  # the middle: centered
    deck.go(6)
    last = deck.rect_of(6)
    assert last.right() <= deck.width() and last.center().x() > deck.width() / 2
