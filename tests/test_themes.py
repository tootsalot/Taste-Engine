"""Shared themes never match item titles or change the usual lists by default."""

from datetime import datetime, timezone

from taste import core, recommend, settings, themes
from taste.recommend import experiments
from tests.test_recommend import anime_world  # noqa: F401

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_map_covers_genres_and_moods_in_the_invented_library():
    from tests.fill_world import GENRES, World

    world = World()
    assert set(g.casefold() for g in GENRES) <= themes.MAP["mal"].keys()
    for entry in world.hardcover.library:
        for kind in ("Genre", "Mood"):
            for tag in entry["book"]["cached_tags"][kind]:
                assert tag["tag"].casefold() in themes.MAP["hardcover"]
    for details in world.tmdb.catalog.values():
        for tag in details.get("genres", []):
            assert tag["name"].casefold() in themes.MAP["tmdb"]
    for tag in ("Dream Pop", "Night"):
        assert themes.mapped("lastfm", [tag])


def test_profile_distinguishes_media_and_never_matches_titles(conn):
    for kind, source, id_type, maximum in (
        ("anime", "mal", "mal_anime_id", 10),
        ("film", "tmdb", "tmdb_movie_id", 10),
        ("book", "hardcover", "hardcover_book_id", 5),
    ):
        for n in range(5):
            item = core.upsert_item(
                conn,
                source=source,
                id_type=id_type,
                external_id=str(n),
                media_type=kind,
                title="Example same title",
            )
            core.upsert_rating(
                conn,
                item_id=item,
                source=source,
                raw_score=maximum,
                scale_min=1,
                scale_max=maximum,
                source_updated_at=None,
            )
            core.upsert_community_rating(
                conn,
                item_id=item,
                source=source,
                mean_score=maximum - 1,
                scale_min=1,
                scale_max=maximum,
                num_raters=100,
            )
            core.set_item_tags(conn, item, source, [core.tag_id(conn, "Psychological", "genre")])
    taste = themes.profile(conn)
    assert set(taste["themes"]["psychological"]) == {"anime", "film", "book"}
    assert all(part["count"] == 5 for part in taste["themes"]["psychological"].values())
    assert conn.execute("SELECT COUNT(*) FROM core_items").fetchone()[0] == 15
    assert themes.lean(taste, ["psychological"], "book") == 0


def test_one_medium_has_no_cross_picks_and_keeps_usual_picks(conn, request):
    request.getfixturevalue("anime_world")
    before = recommend.anime_recommendations(conn, NOW)[0]
    recommend.compute_all(conn, NOW, ("anime", "everything"))
    picks, metrics, _ = recommend.latest(conn, "everything")
    assert picks == [] and metrics["cross_media"] == 1
    assert recommend.latest(conn, "anime")[0] == before


def test_transfer_fades_with_own_ratings_and_records_a_holdout(conn):
    taste = {"themes": {"psychological": {"anime": {"lean": 0.2}}}}
    assert themes.transfer(taste, "tv", ["psychological"], 2) > themes.transfer(
        taste, "tv", ["psychological"], 100
    )
    scored = [(i, 8, 7) for i in range(1, 41)]
    facts = {i: {"genres": ["Psychological"], "studios": []} for i, _, _ in scored}
    metrics = experiments.transfer_metrics(conn, "tv", scored, facts, NOW)
    assert metrics["off"]["count"] == metrics["on"]["count"] == 40
    assert metrics["off"]["mae"] == metrics["on"]["mae"] == 0


def test_transfer_holdout_uses_other_media_without_held_out_ratings(conn):
    for i in range(20):
        item = core.upsert_item(
            conn,
            source="mal",
            id_type="mal_anime_id",
            external_id=str(i),
            media_type="anime",
            title=f"Example training anime {i}",
        )
        core.upsert_rating(
            conn,
            item_id=item,
            source="mal",
            raw_score=9 if i < 10 else 5,
            scale_min=1,
            scale_max=10,
            source_updated_at=None,
        )
        core.upsert_community_rating(
            conn,
            item_id=item,
            source="mal",
            mean_score=7,
            scale_min=1,
            scale_max=10,
            num_raters=100,
        )
        core.set_item_tags(
            conn, item, "mal", [core.tag_id(conn, "Psychological" if i < 10 else "Comedy", "genre")]
        )
    scored = [(i, 8, 7) for i in range(1, 41)]
    facts = {i: {"genres": ["Psychological"], "studios": []} for i, _, _ in scored}
    checks = experiments.transfer_metrics(conn, "tv", scored, facts, NOW)
    assert checks["off"]["mae"] == 0
    assert checks["on"]["mae"] > 0  # a plausible theme can make a wrong prediction
    print(f"Invented transfer holdouts: {checks}")
    excluded = themes.excluded_items(conn, "anime", {0, 1})
    assert themes.profile(conn, excluded)["themes"]["psychological"]["anime"]["count"] == 8


def test_cross_page_shows_medium_colors_and_fits_both_window_sizes(qtbot, monkeypatch, tmp_path):
    from dataclasses import replace

    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QScrollArea

    from taste.desktop import theme
    from taste.desktop.for_you import ACCENTS
    from tests.test_fill import make_library, pump
    from tests.test_mix_ui import picks

    win = make_library(qtbot, monkeypatch, tmp_path, extras=True)
    try:
        conn = win.ctx.connect(win.current_profile())
        try:
            saved = picks()
            saved.append(
                replace(saved[0], item_key="book:6", facts={"list_kind": "book", "medium": "book"})
            )
            saved.append(
                replace(
                    saved[1], kind="tv", item_key="tv:7", facts={"list_kind": "tv", "medium": "tv"}
                )
            )
            recommend.save_run(conn, "everything", saved, {"cross_media": 1, "balance": "rotation"})
            conn.commit()
        finally:
            conn.close()
        win.show_page("For You")
        page = win.for_you
        page.reload()
        page.switch.buttons["everything"].click()
        tab = page.lists["everything"]
        assert {r.kind for r in tab.recs} == {"anime", "film", "tv", "book"}
        assert "Experimental" in tab.note.text()
        assert "other media" in tab.note.text()
        pump()
        for tile in tab.tiles:
            assert tile.medium.text()
            assert tile.medium.height() <= tile.medium.sizeHint().height()
            assert tile.art.accent == QColor(ACCENTS.get(tile.rec.facts["list_kind"], theme.CORAL))
        for width, height in ((2560, 1380), (1920, 1040), (1180, 820)):
            win.resize(width, height)
            pump()
            assert page.switch.width() <= page.width() - 56
            assert all(
                s.horizontalScrollBar().maximum() == 0 for s in page.findChildren(QScrollArea)
            )
        original = tab.recs[0].item_key
        tab.detail.dismiss_button.click()
        assert original not in {r.item_key for r in tab.recs}
    finally:
        win.ctx.shutdown()


def test_cross_recompute_fits_each_scored_pool_once(conn, request, monkeypatch):
    from taste.recommend import runs

    request.getfixturevalue("anime_world")
    calls = []
    original = runs.anime_recommendations

    def capture(*args, **kwargs):
        calls.append(kwargs.get("full_pool"))
        return original(*args, **kwargs)

    monkeypatch.setattr(runs, "anime_recommendations", capture)
    recommend.compute_all(conn, NOW, ("anime", "everything"))
    assert calls == [True]


def test_cross_uses_candidates_beyond_the_medium_saved_limit(conn, request):
    from tests.test_mix_ranking import ratings

    request.getfixturevalue("anime_world")
    ratings(conn, "film")
    candidate = core.find_item(conn, "mal", "mal_anime_id", "203")
    assert candidate is not None
    core.set_item_tags(
        conn,
        candidate,
        "mal",
        [core.tag_id(conn, "Time Travel", "theme"), core.tag_id(conn, "Found Family", "theme")],
    )
    for key in range(250, 255):
        conn.execute(
            "INSERT INTO stg_mal_anime (mal_anime_id, title, media_type, community_mean, "
            "num_scoring_users, loaded_at) VALUES (?, ?, 'tv', 9.5, 100000, ?)",
            (key, f"Invented higher own-medium pick {key}", "2026-10-14 18:00:00"),
        )
        conn.execute(
            "INSERT INTO stg_mal_anime_recommendations VALUES (101, ?, ?, 20)",
            (key, f"Invented higher own-medium pick {key}"),
        )
    settings.set_value(conn, "rec_count", 5)
    recommend.compute_all(conn, NOW, ("anime", "everything"))
    assert "203" not in {rec.item_key for rec in recommend.latest(conn, "anime")[0]}
    assert recommend.latest(conn, "everything")[0][0].item_key == "anime:203"


def test_transfer_holdout_reads_the_theme_evidence_once(conn, monkeypatch):
    loads = []
    original = themes.load
    monkeypatch.setattr(themes, "load", lambda c: loads.append(1) or original(c))
    scored = [(i, 8, 7) for i in range(1, 41)]
    facts = {i: {"genres": ["Psychological"], "studios": []} for i, _, _ in scored}
    experiments.transfer_metrics(conn, "tv", scored, facts, NOW)
    assert len(loads) == 1  # the "on" check, for all five folds; "off" reads none


def test_primary_dismissals_also_hide_saved_cross_picks(conn):
    from taste.recommend import Rec

    pick = Rec(
        kind="anime",
        item_key="anime:123",
        title="Invented pick",
        score=8,
        facts={"medium": "anime", "source_item_key": "123"},
    )
    recommend.save_run(conn, "everything", [pick], {"cross_media": 1})
    recommend.dismiss(conn, "anime", "123")
    assert recommend.latest(conn, "everything")[0] == []


def test_classification_changes_with_nsfw_still_off_withhold_old_seed_reasons(conn, request):
    request.getfixturevalue("anime_world")
    settings.set_value(conn, "include_nsfw", False)
    recommend.compute_all(conn, NOW, ("anime", "everything"))
    assert recommend.latest(conn, "anime")[0]
    conn.execute("UPDATE stg_mal_anime SET nsfw_rating = 'black' WHERE mal_anime_id = 101")
    assert recommend.latest(conn, "anime") == ([], {}, None)
    assert recommend.latest(conn, "everything") == ([], {}, None)
    recommend.compute_all(conn, NOW, ("anime", "everything"))
    for kind in ("anime", "everything"):
        for pick in recommend.latest(conn, kind)[0]:
            assert all(
                "Example Drama A" not in line
                for line in pick.reasons + pick.facts.get("details", [])
            )
