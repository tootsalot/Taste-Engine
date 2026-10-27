"""Mixed filters remember the viewer and change presentation without recomputing."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea

from taste.config import data_dir
from taste.desktop import ui_state
from taste.desktop.context import AppContext
from taste.desktop.for_you import MixedList, score_text
from taste.recommend import Rec
from taste.secrets_store import SecretStore
from tests.conftest import FakeKeyring
from tests.fake_images import FakeImageSession


def picks():
    return [
        Rec(
            kind="book",
            item_key="book:1",
            title="Invented <b>surprise</b>",
            score=4,
            facts={
                "list_kind": "book",
                "outside_usual": True,
                "source_item_key": "1",
                "surprise_reason": "You rarely rate mysteries in books (1 rating).",
                "usual_comparison": "Above your usual for books",
            },
        ),
        Rec(
            kind="film",
            item_key="film:2",
            title="Invented film",
            score=8,
            facts={
                "list_kind": "film",
                "source_item_key": "2",
                "usual_comparison": "Above your usual for films",
            },
        ),
        Rec(
            kind="artist",
            item_key="artist:example",
            title="Invented music",
            score=1.73,
            facts={
                "list_kind": "music_discover",
                "source_item_key": "example",
                "pool_rank": 1,
                "pool_count": 30,
            },
        ),
    ]


def test_filters_keep_the_saved_pool_and_remember_each_profile(qtbot):
    tab = MixedList()
    qtbot.addWidget(tab)
    tab.set_profile("one")
    original = picks()
    tab.set_recs(original, True)
    assert tab.surprise == original[0]
    tab.filters.buttons["film"].click()
    assert [rec.kind for rec in tab.recs] == ["film"]
    assert tab.surprise is None
    assert tab.all_recs == original
    assert ui_state.mix_filter("one") == "film"
    tab.set_profile("two")
    tab.set_recs(original, True)
    assert tab.filters.value() == "all"
    tab.filters.buttons["music"].click()
    assert [rec.kind for rec in tab.recs] == ["artist"]
    tab.set_profile("one")
    tab.set_recs(original, True)
    assert tab.filters.value() == "film"
    assert ui_state.mix_filter("two") == "music"


def test_surprise_is_separate_plain_and_dismisses_like_a_mixed_pick(qtbot):
    ctx = AppContext(store=SecretStore(backend=FakeKeyring()), image_session=FakeImageSession())
    tab = MixedList()
    qtbot.addWidget(tab)
    tab.set_profile("example")
    tab.set_recs(picks(), True)
    dismissed = []
    tab.render(ctx, dismissed.append, lambda kind, key: dismissed.append((kind, key)))
    tab.resize(900, 650)
    tab.show()
    qtbot.waitExposed(tab)
    try:
        assert all(not rec.facts.get("outside_usual") for rec in tab.recs)
        assert tab.surprise_card is not None
        text = "\n".join(label.text() for label in tab.surprise_card.findChildren(QLabel))
        assert "Outside your usual" in text
        assert "Invented <b>surprise</b>" in text
        assert all(
            label.textFormat() == Qt.TextFormat.PlainText
            for label in tab.surprise_card.findChildren(QLabel)
        )
        tab.surprise_card.dismiss_button.click()
        assert dismissed == [("everything", "book:1")]
        assert all(
            area.horizontalScrollBar().maximum() == 0 for area in tab.findChildren(QScrollArea)
        )
    finally:
        ctx.shutdown()


def test_bad_filter_state_defaults_safely_and_preserves_other_window_preferences():
    ui_state.save(nav_collapsed=True, for_you_mix_filter=["bad"])
    assert ui_state.mix_filter("one") == "all"
    ui_state.set_mix_filter("one", "music")
    assert ui_state.mix_filter("one") == "music"
    assert ui_state.load()["nav_collapsed"] is True
    state = json.loads((data_dir() / "ui_state.json").read_text())
    assert state["for_you_mix_filter"] == {"one": "music"}


def test_mixed_music_uses_pool_rank_without_changing_its_native_score():
    rec = picks()[-1]
    number, caption = score_text("everything", rec)
    assert number == "#1"
    assert "30" in caption and "Discover" in caption
    assert rec.score == 1.73


def test_old_mixed_runs_request_refresh_instead_of_claiming_the_new_ranking():
    from taste.desktop.for_you import mixed_note

    assert "Recompute" in mixed_note({"experimental": True})
    assert "rank percentiles" in mixed_note({"means_spreads": {}, "balance": "balanced"})


def test_real_page_filters_without_recomputing_and_dismisses_the_surprise(
    qtbot, monkeypatch, tmp_path
):
    from taste import recommend
    from tests.test_fill import make_library

    win = make_library(qtbot, monkeypatch, tmp_path, extras=True)
    try:
        win.show_page("For You")
        page = win.for_you
        page.switch.buttons["everything"].click()
        tab = page.lists["everything"]
        original = list(tab.all_recs)
        assert tab.surprise_card is not None
        conn = win.ctx.connect(win.current_profile())
        try:
            runs = conn.execute("SELECT COUNT(*) FROM rec_runs").fetchone()[0]

            def refuse(*args, **kwargs):
                raise AssertionError("A mixed filter must not start recomputing")

            monkeypatch.setattr(page, "start_refresh", refuse)
            for choice in ("anime", "film", "tv", "book", "music", "all"):
                tab.filters.buttons[choice].click()
                assert tab.all_recs == original
                assert not page.is_refreshing()
            assert conn.execute("SELECT COUNT(*) FROM rec_runs").fetchone()[0] == runs
            surprise = tab.surprise
            tab.surprise_card.dismiss_button.click()
            assert surprise.item_key not in {rec.item_key for rec in tab.all_recs}
            assert surprise.facts["source_item_key"] not in {
                rec.item_key for rec in recommend.latest(conn, surprise.facts["list_kind"])[0]
            }
            assert surprise.title in page.status.text()
        finally:
            conn.close()
    finally:
        win.ctx.shutdown()
