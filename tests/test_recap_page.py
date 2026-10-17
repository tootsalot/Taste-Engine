"""The Rev page and the recaps the app makes by itself. Real widgets, fake APIs."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from taste import recap
from taste.activity import Period
from taste.desktop.main_window import PAGES
from tests import test_for_you
from tests.test_for_you import synced

images = test_for_you.images
window = test_for_you.window


def key(which):
    return QKeyEvent(QEvent.Type.KeyPress, which, Qt.KeyboardModifier.NoModifier)


def wait_for_cards(qtbot, page):
    qtbot.waitUntil(lambda: not page.is_busy() and page.data is not None, timeout=15000)


def test_a_sync_makes_last_months_recap_and_says_so_once(window, qtbot):
    synced(window, qtbot)
    # "Now" is October 2026 in these tests, so September is the month just finished.
    qtbot.waitUntil(lambda: not window.dashboard.banner.isHidden(), timeout=15000)
    assert window.dashboard.banner.text.text() == "Your September recap is ready."
    folder = recap.folder("me", Period("month", "2026-09"))
    assert (folder / "recap.json").is_file()
    assert (folder / "01-cover.png").is_file()
    assert not list(folder.glob("Taste Engine recap *.png"))
    conn = window.ctx.connect("me")
    try:
        assert recap.unannounced(conn) == []  # shown once
    finally:
        conn.close()
    window.dashboard.banner.open_button.click()
    assert window.stack.currentWidget() is window.recap
    assert window.recap.period == Period("month", "2026-09")
    # Another sync with nothing new: no second banner.
    window.dashboard.banner.setVisible(False)
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000):
        window.dashboard.start_sync("all")
    qtbot.waitUntil(lambda: not window.auto_recap.isRunning(), timeout=15000)
    assert window.dashboard.banner.isHidden()


def test_the_page_shows_a_deck_and_swipes_through_it(window, qtbot):
    synced(window, qtbot)
    window.sidebar.setCurrentRow(PAGES.index("Rev"))
    page = window.recap
    window.resize(1600, 1000)
    window.show()
    qtbot.waitExposed(window)
    page.show_period(Period("month", "2026-09"))
    wait_for_cards(qtbot, page)
    assert page.period_label.text() == "September 2026"
    # About these numbers is folded to one line until asked for.
    assert all(note.isHidden() for note in page.notes)
    page.about_toggle.click()
    assert not any(note.isHidden() for note in page.notes)
    count = len(page.data.slides)
    assert 2 <= count <= 7 and len(page.deck.cards) == count
    assert page.position.text() == f"Card 1 of {count}"
    assert not page.prev_card.isEnabled() and page.next_card.isEnabled()
    width, height = page.deck.card_size()
    assert abs(height / width - 1920 / 1080) < 0.01 and height > 600

    page.next_card.click()
    page.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    )
    page.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
    )
    assert page.deck.index == 1 and page.position.text() == f"Card 2 of {count}"
    for _ in range(count + 2):
        page.next_card.click()  # past the end: stays on the last card
    assert page.deck.index == count - 1 and not page.next_card.isEnabled()
    page.deck.picked.emit(count - 2)  # clicking the neighbor to the left
    assert page.deck.index == count - 2
    # Every card as a thumbnail beside the deck, the current one marked; click to go there.
    assert len(page.thumbs) == count and page.thumbs[count - 2].isChecked()
    page.thumbs[1].click()
    assert page.deck.index == 1 and page.thumbs[1].isChecked()
    assert sum(t.isChecked() for t in page.thumbs) == 1


def test_save_asks_once_then_goes_straight_to_that_folder(window, qtbot, tmp_path):
    synced(window, qtbot)
    page = window.recap
    page.show_period(Period("month", "2026-09"))
    wait_for_cards(qtbot, page)
    count = len(page.data.slides)
    assert page.folder() is None
    assert "first save asks" in page.folder_line.text()
    first = tmp_path / "first"
    first.mkdir()
    page.show_card(1)
    path = page.save_card(str(first))  # stands in for the one-time folder question
    assert path == first / f"Taste Engine recap September 2026 (2 of {count}).png"
    assert path.is_file() and page.folder() == first and str(first) in page.folder_line.text()

    # The next save doesn't ask: the folder passed here is ignored.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    saved = page.save_all(str(elsewhere))
    assert len(saved) == count and all(p.parent == first for p in saved)
    assert list(elsewhere.iterdir()) == []

    second = tmp_path / "second"
    second.mkdir()
    page.change_folder(str(second))
    assert page.save_card().parent == second


def test_the_current_month_is_so_far_and_later_ones_arent_offered(window, qtbot):
    synced(window, qtbot)
    page = window.recap
    page.show_period(Period("month", "2027-03"))  # the future: back to the current month
    wait_for_cards(qtbot, page)
    assert page.period == Period("month", "2026-10")
    assert not page.forward.isEnabled()
    assert "so far" in page.so_far_note.text()
    assert page.data.so_far
    page.set_kind("year")
    wait_for_cards(qtbot, page)
    assert page.period == Period("year", "2026")


def test_a_month_without_data_says_so(window, qtbot):
    synced(window, qtbot)
    page = window.recap
    page.show_period(Period("month", "2020-02"))
    qtbot.waitUntil(lambda: page.period == Period("month", "2020-02") and not page.is_busy())
    qtbot.waitUntil(lambda: "Nothing to recap" in page.empty.text(), timeout=15000)
    assert page.cards == [] and not page.save_button.isEnabled()
    assert page.deck.isHidden()
