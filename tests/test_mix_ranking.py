"""Cross-media picks require specific shared story evidence and native predictions."""

from copy import deepcopy
from dataclasses import replace

import pytest

from taste import activity, core, recommend, settings, themes
from taste.recommend import Rec
from taste.recommend.everything import (
    MAX_SEEDS,
    MIN_SIMILARITY,
    STOP_TERMS,
    CrossIndex,
    balance,
    dismissed_keys,
    everything_recommendations,
    load_cross_data,
    source_items,
)

SOURCE_IDS = {
    "anime": ("mal", "mal_anime_id", "anime", 10),
    "film": ("tmdb", "tmdb_movie_id", "film", 10),
    "tv": ("tmdb", "tmdb_tv_id", "tv_show", 10),
    "book": ("hardcover", "hardcover_book_id", "book", 5),
}
SPECIFIC = ("Time Travel", "Found Family")


def item(conn, kind, key, score=None, tags=SPECIFIC):
    source, id_type, media, maximum = SOURCE_IDS[kind]
    result = core.upsert_item(
        conn,
        source=source,
        id_type=id_type,
        external_id=str(key),
        media_type=media,
        title=f"Invented {kind} title {key}",
    )
    core.set_item_tags(conn, result, source, [core.tag_id(conn, tag, "genre") for tag in tags])
    if score is not None:
        core.upsert_rating(
            conn,
            item_id=result,
            source=source,
            raw_score=score,
            scale_min=0.5,
            scale_max=maximum,
            source_updated_at=None,
        )
    return result


def ratings(conn, kind, scores=(5, 7, 9), tags=SPECIFIC):
    return [item(conn, kind, i, score, tags) for i, score in enumerate(scores)]


def pick(conn, kind, key, score, tags=SPECIFIC):
    item(conn, kind, key, tags=tags)
    return Rec(kind=kind, item_key=str(key), title=f"Invented {kind} pick {key}", score=score)


def test_cross_picks_keep_predictions_and_native_seed_scores(conn):
    ratings(conn, "film")
    ratings(conn, "book", (2, 3, 4.5))
    pools = {"film": ([pick(conn, "film", 10, 8.5)], {}), "book": ([pick(conn, "book", 10, 4)], {})}
    original = deepcopy(pools)
    recs, metrics = everything_recommendations(conn, pools)
    assert len(recs) == 2 and metrics["cross_media"] == 1
    assert metrics["means_spreads"]["book"]["mean"] == pytest.approx(19 / 6)
    for rec in recs:
        assert rec.score == pools[rec.kind][0][0].score
        assert rec.facts["list_kind"] == rec.kind
        seed = rec.facts["cross_seeds"][0]
        assert seed["medium"] != rec.kind
        assert seed["my_score"] == (4.5 if seed["medium"] == "book" else 9)
        assert rec.facts["cross_support"] > 0
        assert "Like " in rec.reasons[0] and "shares" in rec.reasons[0]
        assert "same" not in rec.reasons[0]
        assert rec.facts["cross_compared"] == 1
        assert len(rec.facts["shared_terms"]) <= 5
        assert "Predicted" in rec.facts["details"][-1]
    assert pools == original


def test_only_other_medium_seeds_and_at_or_above_usual_predictions_count(conn):
    ratings(conn, "anime")
    own = pick(conn, "anime", 10, 9)
    assert everything_recommendations(conn, {"anime": ([own], {})})[0] == []
    ratings(conn, "film")
    low = pick(conn, "anime", 11, 6.99)
    picks, _ = everything_recommendations(conn, {"anime": ([own, low], {})})
    assert [r.item_key for r in picks] == ["anime:10"]
    assert all(seed["medium"] == "film" for seed in picks[0].facts["cross_seeds"])


@pytest.mark.parametrize(
    "tags",
    [
        ("Drama", "Romance"),
        ("Emotional", "Funny"),
        ("Tense", "Romantic"),
        tuple(STOP_TERMS),
        ("Time Travel", "Animation", "anime", "based on manga"),
    ],
)
def test_broad_themes_and_medium_markers_do_not_supply_two_specific_terms(conn, tags):
    ratings(conn, "anime", tags=tags)
    ratings(conn, "film", tags=tags)
    candidate = pick(conn, "film", 10, 8, tags)
    assert everything_recommendations(conn, {"film": ([candidate], {})})[0] == []
    documents = load_cross_data(conn)
    assert all(not (title.terms & STOP_TERMS) for title in documents)


def test_casefolded_trimmed_collapsed_terms_and_minimum_similarity(conn):
    ratings(conn, "anime", tags=("  Time   Travel ", "FOUND FAMILY"))
    ratings(conn, "film", tags=())
    candidate = pick(conn, "film", 10, 8)
    data = load_cross_data(conn)
    title = next(t for t in data if t.medium == "film" and t.item_key == "10")
    assert CrossIndex(data).support(title)["cross_support"] > 0
    noisy = replace(title, terms=title.terms | frozenset(f"Unrelated term {i}" for i in range(100)))
    noisy_data = [noisy if t is title else t for t in data]
    assert CrossIndex(noisy_data).support(noisy)["cross_support"] == 0
    assert 0 < MIN_SIMILARITY < 1
    assert everything_recommendations(conn, {"film": ([candidate], {})})[0]


def test_three_strongest_seeds_and_rarest_shared_terms_are_kept(conn):
    ratings(conn, "anime", (1, 6, 7, 8, 9, 10), tags=(*SPECIFIC, "Rare Clockwork", "Drama"))
    ratings(conn, "film", tags=("Drama",))
    candidate = pick(conn, "film", 10, 8, (*SPECIFIC, "Rare Clockwork", "Drama"))
    recs, _ = everything_recommendations(conn, {"film": ([candidate], {})})
    seeds = recs[0].facts["cross_seeds"]
    assert len(seeds) == MAX_SEEDS == 3
    assert [seed["my_score"] for seed in seeds] == [10, 9, 8]
    assert recs[0].facts["shared_terms"][-1] in {"drama", "character drama"}


def test_hidden_adult_seeds_and_alias_candidates_never_appear_but_count_in_averages(conn):
    anime = ratings(conn, "anime", (5, 7, 10))[-1]
    ratings(conn, "film")
    core.add_item_external_id(conn, anime, "hardcover", "hardcover_book_id", "99")
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, nsfw_rating, loaded_at) "
        "VALUES (2, 'Invented hidden title', 'black', '2026-10-14 18:00:00')"
    )
    settings.set_value(conn, "include_nsfw", False)
    candidate = pick(conn, "film", 10, 8)
    data = load_cross_data(conn)
    index = CrossIndex(data, activity.hidden_items(conn))
    assert index.baselines["anime"]["mean"] == pytest.approx(22 / 3)
    assert all(seed.medium != "anime" for seed in index.seeds)
    recs, metrics = everything_recommendations(conn, {"film": ([candidate], {})})
    assert recs == [] and metrics["means_spreads"]["anime"]["count"] == 3
    settings.set_value(conn, "include_nsfw", True)
    assert everything_recommendations(conn, {"film": ([candidate], {})})[0]


def test_recorded_identity_is_not_similarity_and_dismissal_only_covers_aliases(conn):
    anime = ratings(conn, "anime")[-1]
    ratings(conn, "film")
    core.add_item_external_id(conn, anime, "hardcover", "hardcover_book_id", "99")
    core.set_item_tags(conn, anime, "hardcover", [core.tag_id(conn, t, "genre") for t in SPECIFIC])
    data = load_cross_data(conn)
    candidate = next(t for t in data if t.medium == "book")
    assert CrossIndex(data).support(candidate)["cross_seeds"][0]["medium"] == "film"
    other = item(conn, "book", 100)
    core.link_items(conn, anime, other, "adaptation", "mal")
    recommend.dismiss(conn, "everything", "anime:2")
    blocked = dismissed_keys(conn, source_items(conn))
    assert ("book", "99") in blocked and ("book", "100") not in blocked


def test_same_source_identity_does_not_copy_a_film_rating_into_tv(conn):
    film = item(conn, "film", 1, 9)
    core.add_item_external_id(conn, film, "tmdb", "tmdb_tv_id", "2")
    index = CrossIndex(load_cross_data(conn))
    assert index.baselines["film"]["count"] == 1
    assert index.baselines["tv"]["count"] == 0


def ordered_picks():
    return [
        Rec(
            kind=kind,
            item_key=f"{kind}:{i}",
            title="Invented pick",
            score=8,
            facts={"cross_support": score - i / 100, "relative_score": 1},
        )
        for kind, score in (("anime", 2), ("film", 1.9), ("tv", 1.8), ("book", 1.7))
        for i in range(20)
    ]


def test_balance_modes_and_surprise_prefix():
    picks = ordered_picks()
    best = balance(picks, "best")
    assert [r.facts["cross_support"] for r in best] == sorted(
        [r.facts["cross_support"] for r in best], reverse=True
    )
    rotation = balance(picks, "rotation")
    assert all(len({r.kind for r in rotation[start : start + 4]}) == 4 for start in range(0, 80, 4))
    surprise = next(r for r in picks if r.kind == "book")
    balanced = balance([r for r in picks if r is not surprise], "balanced", (surprise,))
    assert balanced[0] is surprise
    assert len({r.kind for r in balanced[:10]}) == 4
    assert all(len({r.kind for r in balanced[i : i + 3]}) > 1 for i in range(10))


def test_surprise_is_best_pick_in_least_rated_available_medium_and_unique(conn):
    ratings(conn, "film", (5, 6, 7, 9))
    ratings(conn, "book", (2, 4))
    pools = {
        "film": ([pick(conn, "film", 10, 8), pick(conn, "film", 11, 9)], {}),
        "book": ([pick(conn, "book", 10, 4)], {}),
    }
    recs, _ = everything_recommendations(conn, pools)
    assert recs[0].kind == "book" and recs[0].facts["outside_usual"]
    assert "2 ratings" in recs[0].facts["surprise_reason"]
    assert len({r.item_key for r in recs}) == 3
    assert sum(bool(r.facts.get("outside_usual")) for r in recs) == 1


def test_index_is_read_only_and_support_queries_no_sql(conn):
    ratings(conn, "anime")
    ratings(conn, "film")
    item(conn, "film", 10)
    before = conn.total_changes
    index = CrossIndex(load_cross_data(conn))
    queries = []
    conn.set_trace_callback(queries.append)
    try:
        for title in index.titles:
            index.support(title)
        assert queries == [] and conn.total_changes == before
    finally:
        conn.set_trace_callback(None)


@pytest.mark.parametrize("old_params", ({}, {"anime_visibility": "current"}))
def test_30_and_40_dev_runs_are_stale_and_rebuilt_without_errors(conn, old_params):
    import json

    params = dict(old_params)
    if params:
        params["anime_visibility"] = themes.visibility(conn)
    conn.execute(
        "INSERT INTO rec_runs (kind, created_at, params_json, metrics_json) "
        "VALUES ('everything', '2026-10-14 18:00:00', ?, '{}')",
        (json.dumps(params),),
    )
    assert recommend.withheld(conn, "everything")
    assert recommend.latest(conn, "everything") == ([], {}, None)
    recommend.compute_all(conn, kinds=("everything",))
    assert not recommend.withheld(conn, "everything")
    assert recommend.latest(conn, "everything")[1]["cross_media"] == 1
