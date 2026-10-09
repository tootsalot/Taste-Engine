"""The For You page: real widgets, offscreen Qt, fake APIs and images, temp data folder."""

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QPixmapCache
from PySide6.QtWidgets import QWidget

from taste import recommend, settings
from taste.desktop.context import AppContext
from taste.desktop.for_you import (
    RecCard,
    accuracy_text,
    accuracy_tip,
    chance_text,
    safe_link,
    score_text,
)
from taste.desktop.main_window import PAGES, MainWindow
from taste.http_client import HttpClient
from taste.recommend import Rec
from taste.secrets_store import SecretStore
from tests.conftest import FakeKeyring, FakeTransport
from tests.fake_images import FakeImageSession, image_bytes
from tests.fake_recs import (
    LASTFM_CDN,
    NOW,
    FakeDeezer,
    FakeLastfmApi,
    FakeMal,
    listening_history,
)
from tests.test_desktop import set_up_profile
from tests.test_enrich import NOW_DT
from tests.test_lastfm_sync import FakeLastfm


@pytest.fixture
def images():
    QPixmapCache.clear()
    session = FakeImageSession()
    session.add(
        "https://cdn.myanimelist.net/images/anime/1/201.jpg", image_bytes(fmt="JPEG"), "image/jpeg"
    )
    yield session
    QPixmapCache.clear()


class WindowWatcher(QObject):
    """Records every widget shown as its own window, apart from the ones meant to be.

    A widget shown before it has a parent becomes a real top-level window: on Windows
    it flashes up as an empty little window with a title bar until it's reparented.
    """

    EXPECTED = ("MainWindow", "QMenu", "QTipLabel", "QMessageBox")

    def __init__(self) -> None:
        super().__init__()
        self.stray: list[str] = []

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, QWidget)
            and obj.isWindow()
            and type(obj).__name__ not in self.EXPECTED
        ):
            text = obj.text() if hasattr(obj, "text") else None
            self.stray.append(type(obj).__name__ + (f" {text!r}" if text is not None else ""))
        return False


@pytest.fixture
def stray_windows(qapp):
    watcher = WindowWatcher()
    qapp.installEventFilter(watcher)
    yield watcher.stray
    qapp.removeEventFilter(watcher)


@pytest.fixture
def window(qtbot, images):
    mal = FakeMal()
    recent = FakeLastfm()
    recent.scrobbles = listening_history()
    music = FakeLastfmApi(recent)
    photos = FakeDeezer()  # knows nobody: suggested artists keep their placeholder

    def handler(url, params):
        if "deezer" in url:
            return photos(url, params)
        return mal(url, params) if "myanimelist" in url else music(url, params)

    def client_factory(secrets):
        return HttpClient(
            FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    ctx = AppContext(
        store=SecretStore(backend=FakeKeyring()),
        client_factory=client_factory,
        sync_kwargs={"now": lambda: NOW + 1},
        recs_kwargs={"now": NOW_DT},
        image_session=images,
        clock=lambda: NOW_DT,
    )
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    yield win
    ctx.shutdown()


def synced(win, qtbot):
    set_up_profile(win)
    with qtbot.waitSignal(win.dashboard.sync_finished, timeout=15000):
        win.dashboard.start_sync("all")
    return win


def refreshed(win, qtbot):
    page = win.for_you
    with qtbot.waitSignal(page.refresh_finished, timeout=15000) as blocker:
        assert page.start_refresh() is not None
    return blocker.args[0]


def cards(win, kind):
    return win.for_you.lists[kind].cards


def test_for_you_is_second_in_the_sidebar(window):
    assert PAGES.index("For You") == 1
    window.new_profile(("me", "Me"))
    window.sidebar.setCurrentRow(1)
    assert window.stack.currentWidget() is window.for_you
    page = window.for_you
    assert page.updated.text() == "Never refreshed"
    assert page.status.isHidden()  # no empty band between the heading and the tabs
    assert "Sync MyAnimeList, then press Refresh" in page.lists["anime"].empty.text()
    assert page.lists["anime"].note.text() == (
        "Accuracy: not enough scored shows yet to check (it needs about 25)."
    )


def test_refresh_runs_in_the_background_and_fills_the_cards(window, qtbot):
    synced(window, qtbot)
    window.sidebar.setCurrentRow(PAGES.index("For You"))
    page = window.for_you
    with qtbot.waitSignal(page.refresh_finished, timeout=15000) as blocker:
        page.start_refresh()
        # While it runs: the button and the sync buttons wait, the progress bar shows.
        assert not page.refresh_button.isEnabled()
        assert not window.dashboard.sync_buttons["all"].isEnabled()
        assert not page.progress.isHidden()
        assert not window.profile_picker.isEnabled()
    assert blocker.args[0] == {"anime": 4, "music_discover": 4, "music_rediscover": 1}
    assert page.refresh_button.isEnabled()
    assert window.dashboard.sync_buttons["all"].isEnabled()
    assert page.progress.isHidden()
    assert "Recommendations ready" in page.status.text()
    assert not page.status.isHidden()
    assert page.updated.text().startswith("Updated ")
    assert "UTC" not in page.updated.text()  # in the profile's time zone, in plain words

    anime = cards(window, "anime")
    assert [c.rec.item_key for c in anime] == ["201", "203", "104", "206"]
    first = anime[0]
    assert first.title.text() == "Example Candidate One"
    assert first.score.text() == "8.2"
    assert first.caption.text() == "for you · MAL 8.20"
    assert first.chance.isHidden()  # four scored shows are too few to estimate it
    assert first.open_button.text() == "Open on MyAnimeList"
    reasons = [
        w.text() for w in first.findChildren(type(first.title)) if w.property("role") == "reason"
    ]
    assert reasons == [
        "Because you loved Example Drama A and really liked Example Action F, and you tend "
        "to enjoy drama anime."
    ]
    assert "20 MAL users who liked Example Drama A recommend this." in first.toolTip()
    badges = [
        w.text() for w in anime[2].findChildren(type(first.title)) if w.property("role") == "badge"
    ]
    assert badges == ["On your Plan to Watch"]
    assert page.tabs.tabText(0) == "Anime (4)"


def test_music_tabs_build_when_shown_and_carry_the_scope_note(window, qtbot):
    synced(window, qtbot)
    refreshed(window, qtbot)
    page = window.for_you
    discover = page.lists["music_discover"]
    assert discover.dirty and discover.cards == []  # not built until someone looks
    page.tabs.setCurrentWidget(discover)
    assert [c.rec.title for c in discover.cards][:2] == ["Example New X", "Example Dismissed Z"]
    assert discover.cards[0].score.text() == "Strong match"
    assert "Match strength 0.95" in discover.cards[0].toolTip()
    assert discover.cards[0].open_button.text() == "Open on Last.fm"
    assert "Desktop listening only" in discover.note.text()
    page.tabs.setCurrentWidget(page.lists["music_rediscover"])
    rediscover = page.lists["music_rediscover"]
    assert [c.rec.title for c in rediscover.cards] == ["Example Old Favorite"]
    assert rediscover.cards[0].score.text() == "20"
    assert "Desktop listening only" in rediscover.note.text()


def test_posters_load_in_the_background(window, qtbot, images):
    synced(window, qtbot)
    refreshed(window, qtbot)
    first = cards(window, "anime")[0]
    qtbot.waitUntil(first.art.has_picture, timeout=5000)
    # Cards without a downloadable picture keep their placeholder letter.
    qtbot.waitUntil(window.ctx.art.is_idle, timeout=5000)
    assert not cards(window, "anime")[1].art.has_picture()
    # Only known hosts (the Dashboard asks for album covers too), and nothing for
    # the music tabs, which nobody has opened yet.
    urls = images.urls()
    hosts = ("https://cdn.myanimelist.net/", "https://lastfm.freetls.fastly.net/")
    assert urls and all(url.startswith(hosts) for url in urls)
    assert LASTFM_CDN.format("newx") not in urls


def test_not_interested_hides_a_card_for_good(window, qtbot):
    synced(window, qtbot)
    refreshed(window, qtbot)
    page = window.for_you
    target = next(c for c in cards(window, "anime") if c.rec.item_key == "203")
    qtbot.mouseClick(target.dismiss_button, Qt.MouseButton.LeftButton)
    assert [c.rec.item_key for c in cards(window, "anime")] == ["201", "104", "206"]
    assert page.tabs.tabText(0) == "Anime (3)"
    assert "Hidden Example Drama A Season 2" in page.status.text()
    refreshed(window, qtbot)  # and it stays hidden after a refresh
    assert "203" not in [c.rec.item_key for c in cards(window, "anime")]


def test_open_link_goes_to_the_browser(window, qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "taste.desktop.for_you.QDesktopServices.openUrl", lambda url: opened.append(url.toString())
    )
    synced(window, qtbot)
    refreshed(window, qtbot)
    qtbot.mouseClick(cards(window, "anime")[0].open_button, Qt.MouseButton.LeftButton)
    assert opened == ["https://myanimelist.net/anime/201"]
    assert not window.for_you.open_link("https://example.com/phish")
    assert opened == ["https://myanimelist.net/anime/201"]


def test_changing_a_rec_setting_recomputes_without_the_network(window, qtbot):
    synced(window, qtbot)
    refreshed(window, qtbot)
    page = window.for_you
    window.settings.inputs["rec_include_plan_to_watch"].setChecked(False)
    with qtbot.waitSignal(page.refresh_finished, timeout=15000) as blocker:
        assert window.settings.save_settings()
    assert blocker.args[0]["anime"] == 3
    assert "104" not in [c.rec.item_key for c in cards(window, "anime")]

    # Other settings don't trigger a recompute.
    window.settings.inputs["top_n_all_time"].setValue(40)
    assert window.settings.save_settings()
    assert not page.is_refreshing()


def test_rec_settings_sit_under_their_own_heading(window):
    window.new_profile(("me", "Me"))
    page = window.settings
    assert page.headings == settings.GROUPS
    assert all(key in page.inputs for key in recommend.REC_SETTINGS)
    rec_keys = [k for k in recommend.REC_SETTINGS if k.startswith("rec_")]
    assert {page.group_of[k] for k in rec_keys} == {"Recommendations"}
    types = page.inputs["rec_media_types"]
    assert [box.text() for box in types.boxes.values()] == [
        "TV",
        "Movie",
        "ONA",
        "OVA",
        "Special",
        "TV special",
        "Music",
    ]
    assert types.value() == ["tv", "movie", "ona", "ova"]  # the default


def test_unticking_an_anime_type_saves_and_recomputes(window, qtbot):
    synced(window, qtbot)
    refreshed(window, qtbot)
    page = window.for_you
    window.settings.inputs["rec_media_types"].boxes["movie"].setChecked(False)
    with qtbot.waitSignal(page.refresh_finished, timeout=15000):
        assert window.settings.save_settings()
    conn = window.ctx.connect("me")
    assert settings.get(conn, "rec_media_types") == "tv,ona,ova"
    conn.close()

    # Unticking everything is refused instead of quietly emptying the anime list.
    for box in window.settings.inputs["rec_media_types"].boxes.values():
        box.setChecked(False)
    assert not window.settings.save_settings()
    assert "at least one" in window.settings.settings_message.text()


def test_sync_blocks_refresh(window, qtbot):
    set_up_profile(window)
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000):
        window.dashboard.start_sync("all")
        assert not window.for_you.refresh_button.isEnabled()
        assert window.for_you.start_refresh() is None
    assert window.for_you.refresh_button.isEnabled()


def test_card_text_is_never_html(window, qtbot):
    synced(window, qtbot)
    refreshed(window, qtbot)
    conn = window.ctx.connect("me")
    conn.execute("UPDATE stg_mal_anime SET title = '<b>bold</b>' WHERE mal_anime_id = 201")
    conn.close()
    with qtbot.waitSignal(window.for_you.refresh_finished, timeout=15000):
        window.for_you.start_refresh(fetch=False)
    title = cards(window, "anime")[0].title
    assert title.textFormat() == Qt.TextFormat.PlainText
    assert title.text() == "<b>bold</b>"


def test_helpers():
    assert safe_link("https://www.last.fm/music/Example") == "https://www.last.fm/music/Example"
    assert safe_link("http://myanimelist.net/anime/1") is None
    assert safe_link("https://myanimelist.net.evil.example/x") is None
    assert safe_link(None) is None
    anime = Rec("anime", "1", "T", score=7.675, facts={"mal_mean": 8.26})
    assert score_text("anime", anime) == ("7.7", "for you · MAL 8.26")
    older = Rec("anime", "1", "T", score=7.675)  # saved before facts existed
    assert score_text("anime", older) == ("7.7", "predicted for you")
    label = {"match_label": "Strong match"}
    assert score_text("music_discover", Rec("artist", "x", "X", score=0.95, facts=label)) == (
        "Strong match",
        "",
    )
    assert score_text("music_discover", Rec("artist", "x", "X", score=0.95)) == ("0.95", "match")
    assert score_text("music_rediscover", Rec("artist", "x", "X", score=1234.0)) == (
        "1,234",
        "plays",
    )
    assert score_text("anime", Rec("anime", "1", "T")) == ("-", "")
    assert chance_text(0.684) == "68% chance you'd give it an 8 or more"
    metrics = {"held_out": 87, "community": 1.43, "overall": 1.40, "model": 1.35}
    # One line on the page; the full comparison is in its tooltip.
    assert accuracy_text(metrics) == (
        "Accuracy: off by 1.35 points on average on 87 shows it hadn't seen (MAL alone: 1.43)."
    )
    assert "MAL score plus your usual difference by 1.40" in accuracy_tip(metrics)
    assert settings.BY_KEY["rec_count"].default == 30


def test_sync_everything_also_refreshes_recommendations(window, qtbot):
    set_up_profile(window)
    dash = window.dashboard
    progress = []
    dash.progress.valueChanged.connect(progress.append)
    with qtbot.waitSignal(dash.sync_finished, timeout=15000) as blocker:
        dash.start_sync("all")
    assert blocker.args[0] == {"mal": True, "lastfm": True}
    log = dash.log.toPlainText()
    assert "Recommendations: details for shows you liked, 2 of 2" in log
    assert "Recommendations ready: 4 anime, 4 new artists, 1 to rediscover." in log
    assert window.for_you.tabs.tabText(0) == "Anime (4)"  # For You picked them up
    assert len(cards(window, "anime")) == 4
    assert progress  # the bar followed the recommendation steps too


def test_no_stray_windows_flash_up(stray_windows, window, qtbot):
    # Building the window, syncing (which refreshes recommendations), refreshing again,
    # and opening every page and tab must never show a widget as its own window.
    synced(window, qtbot)
    refreshed(window, qtbot)
    for row in range(len(PAGES)):
        window.sidebar.setCurrentRow(row)
    for index in range(window.for_you.tabs.count()):
        window.for_you.tabs.setCurrentIndex(index)
    window.reports.refresh()
    assert stray_windows == [], "\n".join(sorted(set(stray_windows)))


def test_a_card_with_every_line_opens_no_window(stray_windows, qtbot, images):
    rec = Rec(
        "anime", "1", "Example Show", "TV · 2020 · 12 eps", 8.1, badge="On your Plan to Watch",
        reasons=["Because you loved Example Drama A."],
        facts={"mal_mean": 8.0, "chance_8_plus": 0.55, "details": ["a number"]},
    )  # fmt: skip
    ctx = AppContext(image_session=images)
    card = RecCard(ctx, "anime", rec)
    qtbot.addWidget(card)
    card.show()  # the card itself is a window here; nothing inside it may be one
    assert not card.chance.isHidden() and not card.caption.isHidden()
    assert stray_windows == ["RecCard"]
    ctx.shutdown()


def test_syncing_one_source_leaves_recommendations_alone(window, qtbot):
    set_up_profile(window)
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000):
        window.dashboard.start_sync("mal")
    assert "Recommendations" not in window.dashboard.log.toPlainText()
    assert window.for_you.updated.text() == "Never refreshed"
