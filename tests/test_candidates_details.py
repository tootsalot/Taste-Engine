"""Ridge details learn metadata corrections from training ratings only."""

import math
from dataclasses import replace
from datetime import date

import pytest

from taste.recommend import candidates
from taste.recommend.model import build_model


@pytest.mark.parametrize(
    "medium,fact,categories,numeric",
    [
        (
            "anime",
            {
                "genres": ["Drama", "School", "Shounen"],
                "studios": ["Studio Alpha"],
                "media_type": "tv",
                "source_material": "manga",
                "start_season_year": 2017,
                "num_episodes": 13,
                "num_list_users": 99,
                "popularity_rank": 780,
            },
            {
                ("genre", "Drama"),
                ("genre", "School"),
                ("genre", "Shounen"),
                ("studio", "Studio Alpha"),
                ("media_type", "tv"),
                ("source_material", "manga"),
                ("five_year", "2015"),
                ("episodes", "<=26"),
                ("popularity", "<=1000"),
            },
            {"log_members": math.log(100)},
        ),
        (
            "film",
            {
                "genres": ["Drama"],
                "studios": ["Ida Director"],
                "original_language": "fr",
                "release_date": "1999-01-01",
                "runtime": 180,
                "vote_count": 9,
            },
            {
                ("genre", "Drama"),
                ("director", "Ida Director"),
                ("language", "fr"),
                ("decade", "1990"),
                ("runtime", "<=180"),
            },
            {"log_votes": math.log(10)},
        ),
        (
            "tv",
            {
                "genres": ["Drama"],
                "studios": ["Ola Creator"],
                "original_language": "ko",
                "first_air_date": "2007-01-01",
                "seasons": 2,
                "vote_count": 99,
                "networks_json": '["Example Network"]',
            },
            {
                ("genre", "Drama"),
                ("creator", "Ola Creator"),
                ("language", "ko"),
                ("decade", "2000"),
                ("seasons", "<=3"),
                ("network", "Example Network"),
            },
            {"log_votes": math.log(100)},
        ),
        (
            "book",
            {
                "genres": ["History"],
                "studios": ["Ada Author"],
                "literary_type_id": 2,
                "release_year": 2001,
                "pages": 301,
                "ratings_count": 99,
                "moods_json": '["hopeful", "tense"]',
            },
            {
                ("genre", "History"),
                ("author", "Ada Author"),
                ("literary_type", "2"),
                ("decade", "2000"),
                ("pages", "<=500"),
                ("mood", "hopeful"),
                ("mood", "tense"),
            },
            {"log_ratings": math.log(100)},
        ),
    ],
)
def test_details_use_every_requested_metadata_field(medium, fact, categories, numeric):
    actual_categories, actual_numeric = candidates.detail_features(fact, medium)
    assert actual_categories == categories
    assert actual_numeric == pytest.approx(numeric)


def test_encoder_learns_vocabulary_and_scaling_from_distinct_training_titles_only():
    rows = [(1, 8, 7), (1, 8, 7), (1, 8, 7), (2, 7, 7), (3, 6, 7)]
    raw = {
        1: ({("genre", "Shared"), ("genre", "Rare")}, {"number": 1, "constant": 7}),
        2: ({("genre", "Shared")}, {"number": 3, "constant": 7}),
        3: ({("genre", "Shared")}, {"number": 5, "constant": 7}),
        4: ({("genre", "Rare"), ("genre", "Held out")}, {"number": 1000, "constant": 100}),
        5: ({("genre", "Held out")}, {}),
    }
    encode, vocabulary, numbers = candidates._detail_encoder(rows, raw)
    assert vocabulary == {("genre", "Shared"): 0}
    assert set(numbers) == {"number"}
    _, center, spread = numbers["number"]
    assert center == 3
    assert spread == pytest.approx(math.sqrt(8 / 3))
    assert encode(5) == []
    assert dict(encode(4)) == {1: pytest.approx((1000 - 3) / spread)}


def test_ridge_solver_recovers_known_weights_and_keeps_inputs_unchanged():
    # X has rows (1, 0), (1, 1), (1, 2), (1, 3), (1, 4).
    # The response is exactly 2 + 3 x, so X'y is (40, 110).
    gram = [[5.0, 10.0], [10.0, 30.0]]
    rhs = [40.0, 110.0]
    assert candidates.ridge_weights(gram, rhs, 0) == pytest.approx([2, 3])
    assert gram == [[5.0, 10.0], [10.0, 30.0]]
    assert rhs == [40.0, 110.0]


def test_ridge_solver_handles_redundant_constant_and_zero_columns():
    gram = [[4, 4, 0], [4, 4, 0], [0, 0, 0]]
    rhs = [8, 8, 0]
    weights = candidates.ridge_weights(gram, rhs, 1)
    assert weights == pytest.approx([8 / 9, 8 / 9, 0])
    assert candidates.ridge_weights(gram, rhs, 1) == weights


def test_sparse_design_cross_terms_match_a_dense_reference():
    ratings = [8.0, 6.0, 9.0, 3.0, 4.0, 6.0]
    rows = [(key, mine, 7.0) for key, mine in enumerate(ratings, 1)]
    raw = {
        key: ({("genre", "A" if key <= 3 else "B"), ("genre", "Shared")}, {"number": key})
        for key in range(1, 7)
    }
    spread = math.sqrt(35 / 12)
    dense = [[float(key <= 3), float(key > 3), 1.0, (key - 3.5) / spread] for key in range(1, 7)]
    model, encode, gram, rhs = candidates._details_design(rows, {}, raw)
    assert model.base(7.0) == 6.0
    for key, vector in enumerate(dense, 1):
        assert [dict(encode(key)).get(column, 0.0) for column in range(4)] == pytest.approx(vector)
    for column in range(4):
        assert rhs[column] == pytest.approx(
            sum(vector[column] * (mine - 6) for vector, mine in zip(dense, ratings, strict=True))
        )
        for other in range(column + 1):
            assert gram[column][other] == pytest.approx(
                sum(vector[column] * vector[other] for vector in dense)
            )


def test_details_learn_new_information_and_fall_back_to_the_line(conn):
    rows = [(key, 9.0 if key % 2 else 3.0, 6.0) for key in range(1, 61)]
    facts = {key: {"media_type": "tv" if key % 2 else "movie"} for key, _, _ in rows}
    facts.update(
        {
            101: {"media_type": "tv"},
            102: {"media_type": "movie"},
            103: {},
            104: {"media_type": "ona"},
        }
    )
    context = candidates.Context(conn, "anime", dict.fromkeys(facts, 6.0), date(2026, 1, 1))
    predict = candidates.fit_details(rows, facts, context)
    assert predict(101) > predict(102)
    line = build_model(rows, facts).base(6.0)
    assert predict(103) == line
    assert predict(104) == line
    assert predict.penalty == 1
    again = candidates.fit_details(rows, facts, context)
    assert again.penalty == predict.penalty
    assert [again(key) for key in facts] == [predict(key) for key in facts]


def test_details_tuning_refits_the_line_and_encoder_inside_each_training_fold(conn, monkeypatch):
    rows = [(key, 9.0 if key % 2 else 3.0, 6.0) for key in range(1, 31)]
    facts = {key: {"media_type": "tv" if key % 2 else "movie"} for key, _, _ in rows}
    context = candidates.Context(conn, "anime", dict.fromkeys(facts, 6.0), date(2026, 1, 1))
    seen = []
    original = candidates._details_design

    def capture(train, item_facts, raw):
        seen.append({key for key, _, _ in train})
        return original(train, item_facts, raw)

    monkeypatch.setattr(candidates, "_details_design", capture)
    candidates.fit_details(rows, facts, context)
    assert seen[:-1] == [{key for key, _, _ in rows if key % 5 != fold} for fold in range(5)]
    assert seen[-1] == {key for key, _, _ in rows}


@pytest.mark.parametrize("medium", ("anime", "film", "tv", "book"))
def test_missing_metadata_contains_no_invented_features(medium):
    assert candidates.detail_features({"title": "Example Only", "community_mean": 7}, medium) == (
        set(),
        {},
    )


def test_details_prepare_only_training_and_requested_metadata_and_load_later_titles(
    conn, monkeypatch
):
    rows = [(key, 9.0 if key % 2 else 3.0, 6.0) for key in range(1, 41)]
    facts = {
        key: {"example_id": key, "media_type": "tv" if key % 2 else "movie"}
        for key in range(1, 141)
    }
    ctx = candidates.Context(conn, "anime", dict.fromkeys(facts, 6.0), date(2026, 1, 1))
    full = candidates.fit_details(rows, facts, ctx)
    features = candidates.detail_features
    seen = []

    def capture(fact, medium):
        seen.append(fact["example_id"])
        return features(fact, medium)

    monkeypatch.setattr(candidates, "detail_features", capture)
    predict = candidates.fit_details(rows, facts, replace(ctx, prediction_ids=(101, 102)))
    expected_ids = {key for key, _, _ in rows} | {101, 102}
    assert set(seen) == expected_ids
    assert len(seen) == len(expected_ids)
    assert predict.penalty == full.penalty
    assert [predict(key) for key in (101, 102, 103)] == [full(key) for key in (101, 102, 103)]
    assert seen[-1] == 103
    assert predict(103) == full(103)
    assert seen.count(103) == 1
