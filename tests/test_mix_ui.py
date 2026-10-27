"""Cross-media filters and evidence use saved picks without recomputing."""

import json
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea

from taste.config import data_dir
from taste.desktop import ui_state
from taste.desktop.context import AppContext
from taste.desktop.for_you import EMPTY, EMPTY_AFTER_RUN, MixedList, RecCard, mixed_note, score_text
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
                "medium": "book",
                "outside_usual": True,
                "source_item_key": "1",
                "surprise_reason": "Books have the fewest ratings among these picks (1 rating).",
                "usual_comparison": "Above your usual for books",
            },
        ),
        Rec(
            kind="film",
            item_key="film:2",
            title="Invented <b>film</b>",
            score=8.5,
            reasons=["Like Invented <b>Show</b>: both share <b>time travel</b> and found family."],
            facts={
                "list_kind": "film",
                "medium": "film",
                "source_item_key": "2",
                "usual_comparison": "Above your usual for films",
                "cross_seeds": [
                    {
                        "medium": "anime",
                        "item_key": "3",
                        "title": "Invented <b>Show</b>",
                        "my_score": 9,
                    },
                    {"medium": "book", "item_key": "4", "title": "Invented Book", "my_score": 4.5},
                ],
                "shared_terms": ["<b>time travel</b>", "found family", "friendship"],
                "cross_support": 0.63,
                "cross_compared": 6,
                "usual_mean": 7,
                "details": [
                    "Cross support: 0.63 from 2 liked titles in other media.",
                    "Compared 6 liked titles in other media.",
                    "Predicted 8.5 points versus your usual 7 for films.",
                ],
            },
        ),
        Rec(
            kind="anime",
            item_key="anime:5",
            title="Invented Anime",
            score=8.1,
            facts={"list_kind": "anime", "medium": "anime", "source_item_key": "5"},
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
    tab.filters.buttons["anime"].click()
    assert [rec.kind for rec in tab.recs] == ["anime"]
    tab.set_profile("one")
    tab.set_recs(original, True)
    assert tab.filters.value() == "film"
    assert ui_state.mix_filter("two") == "anime"
    assert tuple(tab.filters.buttons) == ui_state.MIX_FILTERS


def test_surprise_is_separate_plain_and_dismisses_like_a_cross_pick(qtbot):
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


def test_cross_detail_shows_plain_seeds_terms_and_own_model_score(qtbot):
    ctx = AppContext(store=SecretStore(backend=FakeKeyring()), image_session=FakeImageSession())
    card = RecCard(ctx, "everything", picks()[1])
    qtbot.addWidget(card)
    try:
        assert card.score.text() == "8.5"
        assert [seed.text() for seed in card.seeds] == [
            "Anime · Invented <b>Show</b> · you gave it 9",
            "Book · Invented Book · you gave it 4.5★",
        ]
        assert [chip.text() for chip in card.shared_terms] == picks()[1].facts["shared_terms"]
        assert all(chip.property("role") == "chip" for chip in card.shared_terms)
        assert all(
            text.textFormat() == Qt.TextFormat.PlainText for text in card.findChildren(QLabel)
        )
        assert "Cross support: 0.63" in card.toolTip()
        assert "Compared 6 liked titles in other media" in card.toolTip()
        assert "Predicted 8.5 points versus your usual 7" in card.toolTip()
        evidence = [
            card.body.itemAt(i)
            for i in range(card.body.count())
            if card.body.itemAt(i).layout() is not None
            and card.body.itemAt(i).layout().itemAt(0).widget() is card.shared_terms[0]
        ]
        assert len(evidence) == 1
        chips = evidence[0].layout()
        assert [chips.getItemPosition(i)[:2] for i in range(chips.count())] == [
            (0, 0),
            (0, 1),
            (1, 0),
        ]
        chip_index = next(
            i for i in range(card.body.count()) if card.body.itemAt(i).layout() is chips
        )
        assert card.body.indexOf(card.seeds[-1]) < chip_index

    finally:
        ctx.shutdown()


def test_saved_music_filter_defaults_to_all_and_preserves_window_preferences():
    ui_state.save(nav_collapsed=True, for_you_mix_filter={"one": "music"})
    assert ui_state.mix_filter("one") == "all"
    ui_state.set_mix_filter("one", "book")
    assert ui_state.mix_filter("one") == "book"
    ui_state.set_mix_filter("one", "music")
    assert ui_state.mix_filter("one") == "book"
    assert ui_state.load()["nav_collapsed"] is True
    state = json.loads((data_dir() / "ui_state.json").read_text())
    assert state["for_you_mix_filter"] == {"one": "book"}
    ui_state.save(for_you_mix_filter=["bad"])
    assert ui_state.mix_filter("one") == "all"


@pytest.mark.parametrize("kind,score", [("anime", 8.1), ("film", 8.5), ("tv", 7.5), ("book", 4.5)])
def test_cross_score_matches_its_own_tab(kind, score):
    rec = Rec(
        kind=kind, item_key=f"{kind}:1", title="Invented", score=score, facts={"list_kind": kind}
    )
    assert score_text("everything", rec) == score_text(kind, rec)
    assert rec.score == score


def test_cross_note_and_empty_text_explain_the_list():
    assert "Refresh recommendations" in mixed_note({"experimental": True})
    note = mixed_note({"cross_media": 1, "balance": "balanced"})
    assert "other media" in note and "own model" in note
    assert "Music" not in note
    for text in (EMPTY["everything"], EMPTY_AFTER_RUN["everything"]):
        assert "at least two" in text and "Refresh recommendations" in text
        assert all(medium in text for medium in ("Anime", "Films", "TV", "Books"))


def test_real_page_filters_without_recomputing_and_dismisses_the_surprise(
    qtbot, monkeypatch, tmp_path
):
    from taste import recommend
    from tests.test_fill import make_library

    win = make_library(qtbot, monkeypatch, tmp_path, extras=True)
    conn = win.ctx.connect(win.current_profile())
    try:
        saved = picks()
        for rec in saved:
            recommend.save_run(
                conn, rec.kind, [replace(rec, item_key=rec.facts["source_item_key"])]
            )
        recommend.save_run(conn, "everything", saved, {"cross_media": 1, "balance": "balanced"})
        conn.commit()
        win.show_page("For You")
        page = win.for_you
        page.reload()
        page.switch.buttons["everything"].click()
        tab = page.lists["everything"]
        original = list(tab.all_recs)
        assert tab.surprise_card is not None
        runs = conn.execute("SELECT COUNT(*) FROM rec_runs").fetchone()[0]

        def refuse(*args, **kwargs):
            raise AssertionError("A cross-media filter must not start recomputing")

        monkeypatch.setattr(page, "start_refresh", refuse)
        for choice in ui_state.MIX_FILTERS:
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
        win.ctx.shutdown()
