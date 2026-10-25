"""The Rev page and the recaps the app makes by itself. Real widgets, fake APIs."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from taste import recap
from taste.activity import Period
from taste.desktop.main_window import PAGES
from tests import test_for_you
from tests.fake_images import image_bytes
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


def test_rebuild_redraws_missing_pictures_and_replaces_saved_cards(window, qtbot, images, tmp_path):
    """What the user saw after Rebuild (2026-10-19): a picture that failed once stayed the
    placeholder on the deck, the thumbnails, and the cards already saved."""
    synced(window, qtbot)
    qtbot.waitUntil(lambda: not window.auto_recap.isRunning(), timeout=15000)
    page = window.recap
    cover = "https://lastfm.freetls.fastly.net/i/u/300x300/olddays.png"  # on 2025's music card
    page.show_period(Period("year", "2025"))
    wait_for_cards(qtbot, page)
    assert cover in images.urls()  # asked for, and Last.fm didn't have it (404)
    names = [name for name, _ in page.cards]
    music = names.index("music")
    before = page.cards[music][1].copy()
    thumb_before = page.thumbs[music].icon().pixmap(page.thumbs[music].iconSize()).toImage()
    page.show_card(music)
    folder = tmp_path / "saved"
    folder.mkdir()
    saved = page.save_all(str(folder))
    assert len(saved) == len(names)
    saved_bytes = {p.name: p.read_bytes() for p in saved}
    # Which deck the saved cards came from, for a later Rebuild to match them by card.
    app_folder = recap.folder("me", Period("year", "2025"))
    assert recap.saved_card_names(app_folder) == names

    images.add(cover, image_bytes(color="#FF7A6B"))  # the cover turns up
    images.calls.clear()
    page.rebuild()
    qtbot.waitUntil(lambda: page.status.text().startswith("Rebuilt"), timeout=15000)

    assert images.urls() == [cover]  # tried again, not skipped as failed for the session
    assert [name for name, _ in page.cards] == names and page.deck.index == music
    assert page.cards[music][1] != before
    assert page.deck.cards[music] == page.cards[music][1]
    thumb_after = page.thumbs[music].icon().pixmap(page.thumbs[music].iconSize()).toImage()
    assert thumb_after != thumb_before
    # The saved cards are replaced, the same files, and the status line says so.
    assert sorted(p.name for p in folder.iterdir()) == sorted(saved_bytes)
    music_file = folder / page.data.save_name(music)
    assert music_file.read_bytes() != saved_bytes[music_file.name]
    assert "saved cards" in page.status.text()


def test_rebuild_replaces_only_this_recaps_saved_cards(qapp, tmp_path):
    """Only the cards saved before are written again. When the number of cards changed,
    each saved card is found by which card it is (from the recap's recap.json), replaced,
    and renamed to the new count; nothing else is saved (the user's call, 2026-10-19)."""
    from PySide6.QtGui import QImage

    from taste.desktop.workers import RecapWorker

    def deck(names):
        image = QImage(9, 16, QImage.Format.Format_RGB32)
        image.fill(0)
        return [(name, image) for name in names]

    class Data:
        period = Period("month", "2026-09")

        def __init__(self, cards):
            self.cards = cards

        def save_name(self, i):
            return f"Taste Engine recap September 2026 ({i + 1} of {len(self.cards)}).png"

    def september():
        return sorted(p.name for p in tmp_path.iterdir() if "September" in p.name)

    before = ["cover", "music", "when", "anime", "wrap"]
    other = tmp_path / "Taste Engine recap August 2026 (1 of 4).png"
    other.write_bytes(b"old")
    for name in ("(2 of 5).png", "(4 of 5).png"):  # music and anime
        (tmp_path / f"Taste Engine recap September 2026 {name}").write_bytes(b"old")
    cards = deck(before)
    assert RecapWorker._replace_saved(Data(cards), cards, tmp_path, before) == 2
    assert september() == [
        "Taste Engine recap September 2026 (2 of 5).png",
        "Taste Engine recap September 2026 (4 of 5).png",
    ]
    assert all(p.read_bytes() != b"old" for p in tmp_path.glob("*September*"))

    # A books card arrives: music is still 2nd, anime moves from 4th to 4th of 6, and
    # nothing that wasn't saved before is saved now.
    after = ["cover", "music", "when", "books", "anime", "wrap"]
    cards = deck(after)
    assert RecapWorker._replace_saved(Data(cards), cards, tmp_path, before) == 2
    assert september() == [
        "Taste Engine recap September 2026 (2 of 6).png",
        "Taste Engine recap September 2026 (5 of 6).png",
    ]
    # Which card was which can't be told: the saved ones are left alone.
    assert RecapWorker._replace_saved(Data(deck(before)), deck(before), tmp_path, None) == 0
    assert len(september()) == 2
    assert other.read_bytes() == b"old"  # another month's card is never touched
    assert RecapWorker._replace_saved(Data(cards), cards, tmp_path / "missing", after) == 0


def test_saved_card_names_prefer_the_deck_they_were_saved_from(tmp_path):
    """recap.json holds the last automatic or rebuilt drawing; the page's own drawing (the
    one Save writes) can differ, so Save notes its card names and they come first."""
    import json

    (tmp_path / "recap.json").write_text(json.dumps({"cards": ["cover", "music", "closing"]}))
    assert recap.saved_card_names(tmp_path) == ["cover", "music", "wrap"]
    recap.remember_saved_names(tmp_path, ["cover", "music", "books", "wrap"])
    assert recap.saved_card_names(tmp_path) == ["cover", "music", "books", "wrap"]


def test_the_first_sync_offers_past_recaps_and_makes_them(window, qtbot):
    """2.3, mockup A: the status line offers the months and years before, makes them in
    the background with their progress there, and says when they're done."""
    synced(window, qtbot)
    notice = window.dashboard.past
    qtbot.waitUntil(lambda: not notice.isHidden(), timeout=15000)
    assert notice.text.text().startswith("Make recaps for your past months? ")
    assert not notice.make_button.isHidden() and not notice.later_button.isHidden()
    notice.make_button.click()
    qtbot.waitUntil(lambda: notice.text.text().startswith(("Made ", "Your past")), timeout=60000)
    conn = window.ctx.connect("me")
    try:
        assert recap.past_state(conn) == "done"
        assert recap.past_periods(conn, window.ctx.now()) == []
        assert recap.unannounced(conn) == []  # no banner for each one
    finally:
        conn.close()
    assert notice.make_button.isHidden() and not notice.open_button.isHidden()
    notice.open_button.click()
    assert window.stack.currentWidget() is window.recap
    # Offered once: the next sync doesn't ask again.
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000):
        window.dashboard.start_sync("all")
    assert window.dashboard.past.isHidden()


def test_not_now_is_remembered(window, qtbot):
    synced(window, qtbot)
    notice = window.dashboard.past
    qtbot.waitUntil(lambda: not notice.isHidden(), timeout=15000)
    notice.later_button.click()
    assert notice.isHidden()
    conn = window.ctx.connect("me")
    try:
        assert recap.past_state(conn) == "declined"
    finally:
        conn.close()
    window.reload_profiles(select="me")
    assert window.dashboard.past.isHidden()


def test_past_recaps_left_halfway_go_on_at_the_next_launch(window, qtbot):
    """Closing the app stops the batch at the next period; switching back to the profile
    (as a launch does) carries on without asking again."""
    synced(window, qtbot)
    qtbot.waitUntil(lambda: not window.auto_recap.isRunning(), timeout=15000)
    conn = window.ctx.connect("me")
    try:
        recap.set_past_state(conn, "making")  # as if the app closed mid-batch
    finally:
        conn.close()
    window.reload_profiles(select="me")
    notice = window.dashboard.past
    qtbot.waitUntil(lambda: notice.text.text().startswith(("Made ", "Your past")), timeout=60000)
    conn = window.ctx.connect("me")
    try:
        assert recap.past_state(conn) == "done"
    finally:
        conn.close()
