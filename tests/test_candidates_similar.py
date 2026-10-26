"""Nearest-title learning uses training leftovers and source metadata only."""

from dataclasses import replace
from datetime import date

import pytest

from taste.recommend import candidates, lab
from taste.recommend.model import build_model


def context(conn, medium, rows, extras):
    return candidates.Context(
        conn,
        medium,
        {**{key: crowd for key, _, crowd in rows}, **extras},
        date(2026, 1, 1),
    )


def test_similar_puts_a_loved_lookalike_above_a_disliked_one(conn):
    rows = [(key, 9.0 if key <= 20 else 3.0, 7.0) for key in range(1, 41)]
    facts = {
        key: {"genres": ["Drama" if key <= 20 else "Comedy"], "studios": []} for key, _, _ in rows
    }
    facts.update({101: {"genres": ["Drama"]}, 102: {"genres": ["Comedy"]}, 103: {}})
    ctx = context(conn, "film", rows, {101: 7.0, 102: 7.0, 103: 7.0})
    predict = candidates.fit_similar(rows, facts, ctx)
    assert predict(101) > predict(102)
    assert predict(103) == build_model(rows, facts).base(7.0)
    assert predict.prior == 1
    again = candidates.fit_similar(rows, facts, ctx)
    assert [predict(key) for key in ctx.crowd] == [again(key) for key in ctx.crowd]


def test_weighted_jaccard_has_explicit_group_weights_and_no_zero_neighbors(conn):
    rows = [(1, 8.0, 7.0), (2, 6.0, 7.0), (3, 7.0, 7.0)]
    facts = {
        1: {"genres": ["Drama"], "studios": ["Alpha"]},
        2: {"genres": ["Drama"], "studios": ["Beta"]},
        3: {"genres": ["Comedy"], "studios": ["Gamma"]},
        101: {"genres": ["Drama"], "studios": ["Alpha"]},
    }
    ctx = context(conn, "film", rows, {101: 7.0})
    graph = candidates._similarity_graph(rows, facts, ctx)
    assert graph[101] == ((1.0, 1), (0.5, 2))
    assert all(neighbor != 1 for _, neighbor in graph[1])


def test_only_the_closest_twenty_training_titles_supply_leftovers(conn):
    rows = [(key, 9.0 if key <= 20 else 1.0, 7.0) for key in range(1, 26)]
    facts = {key: {"genres": ["Drama"]} for key, _, _ in rows}
    facts[101] = {"genres": ["Drama"]}
    ctx = context(conn, "film", rows, {101: 7.0})
    graph = candidates._similarity_graph(rows, facts, ctx)
    model = build_model(rows, facts)
    total, support = candidates._neighbor_leftovers(rows, model, graph, [101])[101]
    assert support == 20
    assert total == pytest.approx(20 * (9 - model.base(7)))
    # Nested splits may share metadata geometry but must remove excluded rows.
    restricted = rows[20:]
    total, support = candidates._neighbor_leftovers(restricted, model, graph, [101])[101]
    assert support == 5
    assert total == pytest.approx(5 * (1 - model.base(7)))


def test_anime_links_use_vote_counts_in_either_direction_without_overlap(conn):
    rows = [(1, 9.0, 7.0), (2, 1.0, 7.0)]
    facts = {key: {} for key in (1, 2, 101, 102, 103)}
    conn.executemany(
        "INSERT INTO stg_mal_anime_recommendations "
        "(mal_anime_id, recommended_id, recommended_title, num_recommendations) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, 101, "Example linked title", 90),
            (101, 2, "Example linked title", 1),
            (102, 2, "Example linked title", 90),
        ],
    )
    ctx = context(conn, "anime", rows, {101: 7.0, 102: 7.0, 103: 7.0})
    graph = candidates._similarity_graph(rows, facts, ctx)
    assert graph[101][0] == (0.9, 1)
    assert graph[101][1][0] == pytest.approx(1 / 11)
    assert graph[101][1][1] == 2
    assert graph[102] == ((0.9, 2),)
    predict = candidates.fit_similar(rows, facts, ctx)
    assert predict(101) > predict(103) > predict(102)


def test_book_links_work_without_genres_or_authors(conn):
    rows = [(1, 9.0, 7.0), (2, 1.0, 7.0)]
    facts = {1: {"similar_json": "[101]"}, 2: {}, 101: {}, 102: {"similar_json": "[2]"}}
    ctx = context(conn, "book", rows, {101: 7.0, 102: 7.0})
    graph = candidates._similarity_graph(rows, facts, ctx)
    assert graph[101] == ((1.0, 1),)
    assert graph[102] == ((1.0, 2),)
    predict = candidates.fit_similar(rows, facts, ctx)
    assert predict(101) > predict(102)


def test_prior_validation_refits_the_base_using_each_inner_training_part(conn, monkeypatch):
    rows = [(key, float(key % 10 + 1), 7.0) for key in range(1, 21)]
    facts = {key: {"genres": ["Drama"]} for key, _, _ in rows}
    ctx = context(conn, "film", rows, {101: 7.0})
    seen = []

    def capture(train, item_facts):
        seen.append({key for key, _, _ in train})
        return build_model(train, item_facts)

    monkeypatch.setattr(candidates, "build_model", capture)
    candidates.fit_similar(rows, facts, ctx)
    assert seen == [{key for key, _, _ in rows if key % 5 != fold} for fold in range(5)] + [
        {key for key, _, _ in rows}
    ]


def test_graph_omits_unrequested_stored_titles_but_keeps_inner_validation_targets(conn):
    rows = [(key, float(key % 10 + 1), 7.0) for key in range(1, 21)]
    facts = {key: {"genres": ["Drama"]} for key in range(1, 1001)}
    ctx = context(conn, "film", rows, dict.fromkeys(range(21, 1001), 7.0))
    full = candidates._similarity_graph(rows, facts, ctx)
    requested = replace(ctx, prediction_ids=(101, 102))
    graph = candidates._similarity_graph(rows, facts, requested)
    assert set(graph) == {key for key, _, _ in rows} | {101, 102}
    assert graph == {key: full[key] for key in graph}


@pytest.mark.parametrize("fit", (candidates.fit_similar, candidates.fit_blend))
def test_requested_predictions_and_later_predictions_match_the_full_graph(conn, fit):
    rows = [(key, float(key % 10 + 1), 6.0 + key % 3) for key in range(1, 21)]
    facts = {
        key: {"genres": ["Drama" if key % 3 else "Comedy"], "studios": [str(key % 4)]}
        for key in range(1, 121)
    }
    ctx = context(conn, "film", rows, dict.fromkeys(range(21, 121), 7.0))
    full = fit(rows, facts, ctx)
    requested = fit(rows, facts, replace(ctx, prediction_ids=(101, 102)))
    assert [requested(key) for key in ctx.crowd] == [full(key) for key in ctx.crowd]
    attribute = "prior" if fit is candidates.fit_similar else "weights"
    assert getattr(requested, attribute) == getattr(full, attribute)


@pytest.mark.parametrize("medium", lab.MEDIA)
def test_lab_predictions_and_numbers_match_the_original_full_crowd_graph(conn, medium, monkeypatch):
    rows = [(key, float(key % 10 + 1), 6.0 + key % 3) for key in range(1, 21)]
    facts = {
        key: {"genres": ["Drama" if key % 3 else "Comedy"], "studios": [str(key % 4)]}
        for key in range(1, 121)
    }
    ctx = context(conn, medium, rows, dict.fromkeys(range(21, 121), 7.0))
    monkeypatch.setattr(lab, "load_data", lambda *_: (rows, facts, ctx))
    choices = tuple(c for c in lab.DEFAULT_CANDIDATES if c.name in ("similar", "blend"))
    graph_function = candidates._similarity_graph
    built = []

    def capture(train, item_facts, candidate_context, *args):
        graph = graph_function(train, item_facts, candidate_context, *args)
        built.append(set(graph))
        return graph

    monkeypatch.setattr(candidates, "_similarity_graph", capture)
    predictions = {
        choice.name: lab.holdout_predictions(rows, facts, ctx, choice) for choice in choices
    }
    results = lab.evaluate(conn, medium, choices)
    assert built
    assert all(keys == {key for key, _, _ in rows} for keys in built)

    def original_graph(train, item_facts, candidate_context, *args):
        return graph_function(train, item_facts, replace(candidate_context, prediction_ids=None))

    monkeypatch.setattr(candidates, "_similarity_graph", original_graph)
    assert predictions == {
        choice.name: lab.holdout_predictions(rows, facts, ctx, choice) for choice in choices
    }
    original_results = lab.evaluate(conn, medium, choices)
    for result, original in zip(results, original_results, strict=True):
        result.pop("seconds")
        original.pop("seconds")
    assert results == original_results
