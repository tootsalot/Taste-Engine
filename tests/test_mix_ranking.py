"""The mixed list compares personal baselines, with explicit balancing rules."""

from copy import deepcopy

import pytest

from taste import core, recommend, settings
from taste.recommend import Rec
from taste.recommend.everything import balance, everything_recommendations


def ratings(conn, kind, scores):
    source, id_type, media, maximum = {
        "anime": ("mal", "mal_anime_id", "anime", 10),
        "film": ("tmdb", "tmdb_movie_id", "film", 10),
        "tv": ("tmdb", "tmdb_tv_id", "tv_show", 10),
        "book": ("hardcover", "hardcover_book_id", "book", 5),
    }[kind]
    for index, score in enumerate(scores):
        item = core.upsert_item(
            conn,
            source=source,
            id_type=id_type,
            external_id=str(index),
            media_type=media,
            title=f"Invented rated {kind} {index}",
        )
        core.upsert_rating(
            conn,
            item_id=item,
            source=source,
            raw_score=score,
            scale_min=0.5,
            scale_max=maximum,
            source_updated_at=None,
        )


def pick(kind, key, score):
    return Rec(kind=kind, item_key=str(key), title=f"Invented {kind} pick {key}", score=score)


def test_high_raw_film_scores_do_not_outweigh_a_larger_personal_book_gain(conn):
    ratings(conn, "film", [8, 9, 10])
    ratings(conn, "book", [2.5, 3, 3.5])
    settings.set_value(conn, "mix_balance", "best")
    pools = {"film": ([pick("film", 10, 9.2)], {}), "book": ([pick("book", 10, 4.5)], {})}
    original = deepcopy(pools)
    recs, metrics = everything_recommendations(conn, pools)
    normal = [rec for rec in recs if not rec.facts.get("outside_usual")]
    assert normal[0].kind == "book"
    assert metrics["means_spreads"]["film"] == {"count": 3, "mean": 9, "spread": 1.0}
    assert metrics["means_spreads"]["book"] == {"count": 3, "mean": 6, "spread": 1.0}
    assert "Well above your usual for books" in normal[0].facts["usual_comparison"]
    assert pools == original
    assert "map_coverage" in metrics


def test_missing_crowd_and_recorded_anime_film_identity_still_count_in_film_mean(conn):
    ratings(conn, "anime", [8])
    item = conn.execute("SELECT item_id FROM core_items").fetchone()[0]
    core.add_item_external_id(conn, item, "tmdb", "tmdb_movie_id", "1")
    core.upsert_rating(
        conn,
        item_id=item,
        source="tmdb",
        raw_score=9,
        scale_min=0.5,
        scale_max=10,
        source_updated_at=None,
    )
    _, metrics = everything_recommendations(conn, {})
    assert metrics["means_spreads"]["film"] == {"count": 1, "mean": 9, "spread": 1.0}


def ordered_picks():
    return [
        Rec(
            kind=kind,
            item_key=f"{kind}:{i}",
            title="Invented pick",
            score=8,
            facts={"mix_score": score - i / 100, "medium": kind},
        )
        for kind, score in (
            ("anime", 2.0),
            ("film", 1.9),
            ("tv", 1.8),
            ("book", 1.7),
            ("artist", 1.6),
        )
        for i in range(20)
    ]


def test_balanced_reaches_each_medium_in_ten_and_avoids_long_streaks():
    recs = balance(ordered_picks(), "balanced")
    assert {rec.kind for rec in recs[:10]} == {"anime", "film", "tv", "book", "artist"}
    for index in range(len(recs) - 2):
        if len({rec.kind for rec in recs[index:]}) > 1:
            assert len({rec.kind for rec in recs[index : index + 3]}) > 1


def test_best_first_uses_scores_and_rotation_visits_each_available_medium():
    best = balance(ordered_picks(), "best")
    assert [rec.facts["mix_score"] for rec in best] == sorted(
        [rec.facts["mix_score"] for rec in best], reverse=True
    )
    rotation = balance(ordered_picks(), "rotation")
    for start in range(0, len(rotation), 5):
        assert len({rec.kind for rec in rotation[start : start + 5]}) == 5


def test_surprise_counts_toward_balanced_first_ten_and_streak_limit():
    picks = ordered_picks()
    surprise = next(rec for rec in picks if rec.kind == "book")
    recs = balance([rec for rec in picks if rec is not surprise], "balanced", (surprise,))
    assert recs[0] is surprise
    assert len({rec.kind for rec in recs[:10]}) == 5
    assert all(len({rec.kind for rec in recs[i : i + 3]}) > 1 for i in range(10))


def test_balanced_does_not_reserve_a_place_for_a_far_below_usual_pick():
    picks = ordered_picks()[:20]
    picks += [
        Rec(
            kind="book",
            item_key="book:1",
            title="Invented distant",
            score=1,
            facts={"mix_score": -5, "medium": "book"},
        )
    ]
    recs = balance(picks, "balanced")
    # The streak guard still brings a second medium in; reach only controls the
    # first-ten reservation and does not silently drop available picks.
    assert len(recs) == len(picks)
    assert recs[0].kind == "anime"


def test_surprise_is_unique_above_actual_usual_and_does_not_repeat_the_top(conn):
    ratings(conn, "film", [6, 7, 8, 7])
    ratings(conn, "book", [3, 3])
    settings.set_value(conn, "mix_balance", "best")
    pools = {
        "film": ([pick("film", 10, 9), pick("film", 11, 8.5)], {}),
        "book": ([pick("book", 10, 4)], {}),
    }
    recs, _ = everything_recommendations(conn, pools)
    surprise = [rec for rec in recs if rec.facts.get("outside_usual")]
    assert len(surprise) == 1
    assert surprise[0].kind == "book"
    assert "2 ratings" in surprise[0].facts["surprise_reason"]
    assert recs[0] == surprise[0]
    assert len({rec.item_key for rec in recs}) == len(recs)
    assert recs[1].item_key == "film:10"


def test_hidden_adult_aliases_are_removed_but_still_count_in_baselines(conn):
    ratings(conn, "anime", [9])
    item = conn.execute("SELECT item_id FROM core_items").fetchone()[0]
    core.add_item_external_id(conn, item, "hardcover", "hardcover_book_id", "20")
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, nsfw_rating, loaded_at) "
        "VALUES (0, 'Invented Hidden Adult', 'black', '2026-10-14 18:00:00')"
    )
    settings.set_value(conn, "include_nsfw", False)
    recs, metrics = everything_recommendations(
        conn,
        {
            "anime": ([pick("anime", 0, 9.5)], {}),
            "book": ([pick("book", 20, 4.8)], {}),
        },
    )
    assert recs == []
    assert metrics["means_spreads"]["anime"]["count"] == 1
    assert "Invented Hidden Adult" not in str(metrics)


def test_same_recorded_item_is_not_duplicated_across_source_pools(conn):
    ratings(conn, "anime", [7])
    item = conn.execute("SELECT item_id FROM core_items").fetchone()[0]
    core.add_item_external_id(conn, item, "tmdb", "tmdb_movie_id", "20")
    recs, _ = everything_recommendations(
        conn,
        {
            "anime": ([pick("anime", 0, 8)], {}),
            "film": ([pick("film", 20, 8)], {}),
        },
    )
    assert [rec.item_key for rec in recs] == ["anime:0"]


def test_dismissed_mixed_item_cannot_return_through_a_recorded_source_alias(conn):
    item = core.upsert_item(
        conn,
        source="mal",
        id_type="mal_anime_id",
        external_id="100",
        media_type="anime",
        title="Invented shared candidate",
    )
    core.add_item_external_id(conn, item, "tmdb", "tmdb_movie_id", "200")
    recommend.save_run(conn, "anime", [pick("anime", 100, 8)])
    recommend.save_run(conn, "film", [pick("film", 200, 8)])
    recommend.dismiss(conn, "everything", "anime:100")
    recommend.compute_all(conn, kinds=("everything",))
    assert recommend.latest(conn, "everything")[0] == []


def test_old_saved_mixed_aliases_are_hidden_by_the_same_source_dismissal(conn):
    item = core.upsert_item(
        conn,
        source="mal",
        id_type="mal_anime_id",
        external_id="100",
        media_type="anime",
        title="Invented shared candidate",
    )
    core.add_item_external_id(conn, item, "tmdb", "tmdb_movie_id", "200")
    old = [
        Rec(
            kind=kind,
            item_key=f"{kind}:{key}",
            title="Invented old cached pick",
            score=8,
            facts={"medium": kind, "source_item_key": str(key)},
        )
        for kind, key in (("anime", 100), ("film", 200))
    ]
    recommend.save_run(conn, "everything", old)
    recommend.dismiss(conn, "everything", "anime:100")
    assert recommend.latest(conn, "everything")[0] == []


def test_a_dismissal_does_not_hide_a_separate_recorded_adaptation(conn):
    anime = core.upsert_item(
        conn,
        source="mal",
        id_type="mal_anime_id",
        external_id="100",
        media_type="anime",
        title="Invented animated candidate",
    )
    book = core.upsert_item(
        conn,
        source="hardcover",
        id_type="hardcover_book_id",
        external_id="200",
        media_type="book",
        title="Invented book candidate",
    )
    core.link_items(conn, anime, book, "adaptation", "mal")
    recommend.save_run(conn, "anime", [pick("anime", 100, 8)])
    recommend.save_run(conn, "book", [pick("book", 200, 4)])
    recommend.dismiss(conn, "everything", "anime:100")
    recommend.compute_all(conn, kinds=("everything",))
    assert [rec.item_key for rec in recommend.latest(conn, "everything")[0]] == ["book:200"]


@pytest.mark.parametrize("count", (0, 1, 19, 20))
def test_tiny_constant_and_unrated_media_are_finite_and_honest(conn, count):
    ratings(conn, "anime", [7] * count)
    recs, metrics = everything_recommendations(
        conn,
        {
            "anime": ([pick("anime", 100, 8)], {}),
            "music_discover": ([pick("artist", "invented artist", 123)], {}),
        },
    )
    assert metrics["means_spreads"]["anime"]["count"] == count
    assert all(-10 < rec.facts["mix_score"] < 10 for rec in recs)
    artist = next(rec for rec in recs if rec.kind == "artist")
    assert "rank" in artist.facts["usual_comparison"].lower()
    assert not artist.facts.get("outside_usual")
    if count == 0:
        assert not any(rec.facts.get("outside_usual") for rec in recs)
        assert (
            "No personal"
            in next(rec for rec in recs if rec.kind == "anime").facts["usual_comparison"]
        )
