"""The Airing now card, using the real sync worker and an invented MAL account."""

from PySide6.QtCore import Qt

from taste import settings
from taste.desktop.main_window import PAGES
from taste.http_client import HttpClient
from tests import test_for_you
from tests.conftest import FakeTransport
from tests.fake_recs import FakeDeezer, FakeMal
from tests.test_airing import NOW, airing_entries
from tests.test_desktop import set_up_profile

images = test_for_you.images
window = test_for_you.window


def test_airing_card_refreshes_after_sync_and_settings_without_another_sync(window, qtbot):
    fake, photos = FakeMal(entries=airing_entries(), details={}), FakeDeezer()

    def handler(url, params):
        return photos(url, params) if "deezer" in url else fake(url, params)

    window.ctx.client_factory = lambda secrets: HttpClient(
        FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
    )
    window.ctx.clock = lambda: NOW
    set_up_profile(window)
    conn = window.ctx.connect("me")
    settings.set_value(conn, "timezone", "America/Phoenix")
    settings.set_value(conn, "include_nsfw", False)
    conn.close()
    window.sidebar.setCurrentRow(PAGES.index("Dashboard"))
    with qtbot.waitSignal(window.dashboard.sync_finished, timeout=15000):
        window.dashboard.start_sync("mal")
    dash = window.dashboard
    assert not dash.airing.isHidden()
    assert [row.title.text() for row in dash.airing.items] == [
        "Example Final Show",
        "Example Midnight Crossing",
        "Example No Schedule",
    ]
    # The list sits on the card's own surface, not a box of window background.
    assert not dash.airing.scroll.viewport().autoFillBackground()
    assert not dash.airing.scroll.widget().autoFillBackground()
    row = dash.airing.items[1]
    assert (row.when.text(), row.caption.text()) == ("Sat 8:30 AM", "next for you: ep 7")
    assert row.when.textFormat() == Qt.TextFormat.PlainText
    assert row.title.textFormat() == Qt.TextFormat.PlainText
    assert row.art.loader is window.ctx.art
    conn = window.ctx.connect("me")
    settings.set_value(conn, "clock_format", "24h")
    conn.close()
    window._after_settings_saved(["clock_format"])
    assert dash.airing.items[1].when.text() == "Sat 08:30"
    conn = window.ctx.connect("me")
    settings.set_value(conn, "dashboard_cards", "month,recent")
    conn.close()
    window._after_settings_saved(["dashboard_cards"])
    assert dash.airing.isHidden()
    conn = window.ctx.connect("me")
    settings.set_value(conn, "dashboard_cards", "month,airing")
    conn.close()
    window._after_settings_saved(["dashboard_cards"])
    assert not dash.airing.isHidden()
    fake.entries.clear()
    with qtbot.waitSignal(dash.sync_finished, timeout=15000):
        dash.start_sync("mal")
    assert dash.airing.isHidden()
    assert dash.grid.indexOf(dash.airing) == -1
