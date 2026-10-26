"""Blend weights are selected from predictions of fully nested component fits."""

from datetime import date

import pytest

from taste.recommend import candidates
from taste.recommend.model import build_model


def test_blend_weights_stay_on_the_tenth_step_simplex_and_prefer_usual_ties():
    perfect_usual = [(8.0, 8.0, 3.0, 4.0), (2.0, 2.0, 7.0, 6.0)]
    perfect_details = [(8.0, 3.0, 8.0, 4.0), (2.0, 7.0, 2.0, 6.0)]
    assert candidates._blend_weights(perfect_usual) == (1, 0, 0)
    assert candidates._blend_weights(perfect_details) == (0, 1, 0)
    assert candidates._blend_weights([(7, 7, 7, 7)] * 20) == (1, 0, 0)
    assert candidates._blend_weights([(7, 6.12345, 6.12345, 6.12345)] * 20) == (1, 0, 0)
    weights = candidates._blend_weights([(7, 9, 5, 6), (5, 8, 3, 4), (4, 3, 6, 5)])
    assert sum(weights) == pytest.approx(1)
    assert all(
        weight >= 0 and weight * 10 == pytest.approx(round(weight * 10)) for weight in weights
    )


def test_component_fits_in_blend_validation_never_receive_their_test_rows(conn, monkeypatch):
    rows = [(key, float(key % 10 + 1), 7.0) for key in range(1, 21)]
    ctx = candidates.Context(conn, "film", {key: crowd for key, _, crowd in rows}, date(2026, 1, 1))
    calls = []

    def fit(train, facts, context, *extra):
        keys = {key for key, _, _ in train}
        calls.append(keys)

        def predict(key):
            assert key not in keys
            return 7.0

        return predict

    monkeypatch.setattr(candidates, "_usual_predictor", fit)
    monkeypatch.setattr(candidates, "fit_details", fit)
    monkeypatch.setattr(candidates, "_fit_similar_with_graph", fit)
    predictions = candidates._blend_predictions(rows, {}, ctx, {})
    assert len(predictions) == len(rows)
    expected = [{key for key, _, _ in rows if key % 5 != fold} for fold in range(5)]
    assert calls == [keys for keys in expected for _ in range(3)]


def test_blend_is_deterministic_and_featureless_titles_get_the_usual_line(conn):
    rows = [(key, 9.0 if key <= 20 else 3.0, 7.0) for key in range(1, 41)]
    facts = {key: {"genres": ["Drama" if key <= 20 else "Comedy"]} for key, _, _ in rows}
    facts[101] = {}
    ctx = candidates.Context(
        conn,
        "film",
        {**{key: crowd for key, _, crowd in rows}, 101: 7.5},
        date(2026, 1, 1),
    )
    first = candidates.fit_blend(rows, facts, ctx)
    second = candidates.fit_blend(rows, facts, ctx)
    assert first.weights == second.weights
    assert sum(first.weights) == pytest.approx(1)
    assert first(101) == build_model(rows, facts).base(7.5)
    assert all(1 <= first(key) <= 10 for key in ctx.crowd)
    assert [first(key) for key in ctx.crowd] == [second(key) for key in ctx.crowd]
