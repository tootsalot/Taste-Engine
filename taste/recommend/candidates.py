"""Experimental rating fits, with all learning restricted to the supplied training rows.

Details and Similar correct the usual crowd line. Blend combines their clamped
predictions with Usual. Inner folds always use source id % 5, including the
component tuning inside Blend. This module reads item metadata only from SQLite.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from taste.recommend.model import MIN_RESIDUALS, TasteModel, build_model

Rows = list[tuple[int, float, float]]
Facts = dict[int, dict[str, Any]]


@dataclass(frozen=True)
class Context:
    conn: sqlite3.Connection
    medium: str
    crowd: dict[int, float]
    # The lab freezes recency to stored data; new models do not use rating dates.
    now: date


def holdout_predictions(
    rows: Rows,
    facts: Facts,
    context: Context,
    fit: Callable[[Rows, Facts, Context], Callable[[int], float]],
) -> list[tuple[int, float, float]]:
    """Predict every rating once outside its source-id fold, shared by lab and lists."""
    if len(rows) < MIN_RESIDUALS:
        return []
    result = []
    for fold in range(5):
        train = [row for row in rows if row[0] % 5 != fold]
        predict = fit(train, facts, context)
        for key, mine, _ in rows:
            if key % 5 == fold:
                result.append((key, mine, predict(key)))
    return result


def _clamp(value: float) -> float:
    return min(max(value, 1.0), 10.0)


def _usual_predictor(train_rows: Rows, facts: Facts, context: Context) -> Callable[[int], float]:
    model = build_model(train_rows, facts)

    def predict(title_id: int) -> float:
        fact = facts.get(title_id, {})
        return model.predict(
            context.crowd[title_id], fact.get("genres", []), fact.get("studios", [])
        )

    return predict


# Band upper bounds are inclusive. Zero or missing lengths/ranks have no band.
DETAIL_EPISODE_BANDS = (1, 12, 26, 52, 100)
DETAIL_POPULARITY_BANDS = (100, 500, 1000, 5000, 10000)
DETAIL_RUNTIME_BANDS = (60, 90, 120, 150, 180)
DETAIL_SEASON_BANDS = (1, 3, 6, 10)
DETAIL_PAGE_BANDS = (150, 300, 500, 800)
DETAIL_PENALTIES = (1, 3, 10, 30, 100)
DETAIL_MIN_TITLES = 3

DetailRaw = dict[int, tuple[set[tuple[str, str]], dict[str, float]]]


def _detail_band(value: int | None, bounds: tuple[int, ...]) -> str | None:
    if value is None or value <= 0:
        return None
    for bound in bounds:
        if value <= bound:
            return f"<={bound}"
    return f">{bounds[-1]}"


def _detail_json_names(value: str | list | None) -> list[str]:
    values = json.loads(value or "[]") if isinstance(value, str) or value is None else value
    names = [v.get("name") if isinstance(v, dict) else v for v in values]
    return [name for name in names if isinstance(name, str) and name]


def detail_features(fact: dict, medium: str) -> tuple[set[tuple[str, str]], dict[str, float]]:
    """Raw metadata only; the encoder later learns the vocabulary from training titles."""
    categories = {("genre", str(name)) for name in fact.get("genres", []) if name}
    people = {"anime": "studio", "film": "director", "tv": "creator", "book": "author"}[medium]
    categories.update((people, str(name)) for name in fact.get("studios", []) if name)
    numeric = {}
    fields = []
    if medium == "anime":
        fields = [
            ("media_type", fact.get("media_type")),
            ("source_material", fact.get("source_material")),
            ("episodes", _detail_band(fact.get("num_episodes"), DETAIL_EPISODE_BANDS)),
            ("popularity", _detail_band(fact.get("popularity_rank"), DETAIL_POPULARITY_BANDS)),
        ]
        year = fact.get("start_season_year")
        if year and year > 0:
            fields.append(("five_year", year // 5 * 5))
        count_name, count = "log_members", fact.get("num_list_users")
    elif medium in ("film", "tv"):
        fields = [("language", fact.get("original_language"))]
        stamp = fact.get("release_date") or fact.get("first_air_date") or ""
        if stamp[:4].isdigit() and int(stamp[:4]) > 0:
            fields.append(("decade", int(stamp[:4]) // 10 * 10))
        if medium == "film":
            fields.append(("runtime", _detail_band(fact.get("runtime"), DETAIL_RUNTIME_BANDS)))
        else:
            fields.append(("seasons", _detail_band(fact.get("seasons"), DETAIL_SEASON_BANDS)))
            fields.extend(
                ("network", name) for name in _detail_json_names(fact.get("networks_json"))
            )
        count_name, count = "log_votes", fact.get("vote_count")
    else:
        fields = [
            ("literary_type", fact.get("literary_type_id")),
            ("pages", _detail_band(fact.get("pages"), DETAIL_PAGE_BANDS)),
        ]
        year = fact.get("release_year")
        if year and year > 0:
            fields.append(("decade", year // 10 * 10))
        fields.extend(("mood", name) for name in _detail_json_names(fact.get("moods_json")))
        count_name, count = "log_ratings", fact.get("ratings_count")
    categories.update(
        (name, str(value)) for name, value in fields if value is not None and value != ""
    )
    if count is not None and count >= 0 and math.isfinite(count):
        numeric[count_name] = math.log1p(count)
    return categories, numeric


def _detail_encoder(train_rows: Rows, raw: DetailRaw):
    """Keep categories seen on at least three distinct training titles and scale numbers."""
    counts = {}
    numbers = {}
    for key in sorted({key for key, _, _ in train_rows}):
        categories, numeric = raw.get(key, (set(), {}))
        for token in categories:
            counts[token] = counts.get(token, 0) + 1
        for name, value in numeric.items():
            numbers.setdefault(name, []).append(value)
    vocabulary = {
        token: index
        for index, token in enumerate(
            sorted(token for token, count in counts.items() if count >= DETAIL_MIN_TITLES)
        )
    }
    numeric_stats = {}
    for name, values in sorted(numbers.items()):
        center = sum(values) / len(values)
        spread = math.sqrt(sum((value - center) ** 2 for value in values) / len(values))
        if spread > 1e-12:
            numeric_stats[name] = (len(vocabulary) + len(numeric_stats), center, spread)

    def encode(key: int) -> list[tuple[int, float]]:
        categories, numeric = raw.get(key, (set(), {}))
        values = sorted((vocabulary[token], 1.0) for token in categories if token in vocabulary)
        values.extend(
            (index, (numeric[name] - center) / spread)
            for name, (index, center, spread) in numeric_stats.items()
            if name in numeric
        )
        return [(index, value) for index, value in values if value != 0]

    return encode, vocabulary, numeric_stats


def ridge_weights(gram: list[list[float]], rhs: list[float], penalty: float) -> list[float]:
    """Solve (X'X + penalty I) weights = X'y by Cholesky, without mutating the inputs."""
    size = len(rhs)
    lower = [[0.0] * size for _ in range(size)]
    for i in range(size):
        row = lower[i]
        for j in range(i):
            other = lower[j]
            value = gram[i][j]
            for k in range(j):
                value -= row[k] * other[k]
            row[j] = value / other[j]
        diagonal = gram[i][i] + penalty
        for k in range(i):
            diagonal -= row[k] * row[k]
        if diagonal <= 0:
            raise ValueError("The ridge normal matrix must be positive definite.")
        row[i] = math.sqrt(diagonal)
    forward = [0.0] * size
    for i in range(size):
        value = rhs[i]
        for j in range(i):
            value -= lower[i][j] * forward[j]
        forward[i] = value / lower[i][i]
    weights = [0.0] * size
    for i in range(size - 1, -1, -1):
        value = forward[i]
        for j in range(i + 1, size):
            value -= lower[j][i] * weights[j]
        weights[i] = value / lower[i][i]
    return weights


def _details_design(train_rows: Rows, facts: Facts, raw: DetailRaw):
    model = build_model(train_rows, facts)
    encode, vocabulary, numeric_stats = _detail_encoder(train_rows, raw)
    size = len(vocabulary) + len(numeric_stats)
    # Cholesky reads the lower triangle, so the upper half needs no accumulation.
    gram = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for key, mine, crowd in train_rows:
        vector = encode(key)
        residual = mine - model.base(crowd)
        for offset, (i, value) in enumerate(vector):
            rhs[i] += value * residual
            row = gram[i]
            for j, other in vector[: offset + 1]:
                row[j] += value * other
    return model, encode, gram, rhs


def _details_fixed(
    train_rows: Rows, facts: Facts, context: Context, penalty: float, raw: DetailRaw | None = None
):
    raw = (
        raw
        if raw is not None
        else {key: detail_features(f, context.medium) for key, f in facts.items()}
    )
    model, encode, gram, rhs = _details_design(train_rows, facts, raw)
    weights = ridge_weights(gram, rhs, penalty)

    def predict(title_id: int) -> float:
        correction = sum(weights[index] * value for index, value in encode(title_id))
        return _clamp(model.base(context.crowd[title_id]) + correction)

    predict.penalty = penalty
    return predict


def fit_details(train_rows: Rows, facts: Facts, context: Context):
    """Ridge corrections to the usual line, with penalty chosen inside the training ratings.

    Each inner fold learns its own line, vocabulary and numeric scaling. Its Gram
    matrix is shared across penalty values. Equal errors favor stronger shrinkage.
    """
    raw = {key: detail_features(f, context.medium) for key, f in facts.items()}
    errors = [0.0] * len(DETAIL_PENALTIES)
    for fold in range(5):
        train = [row for row in train_rows if row[0] % 5 != fold]
        held_out = [row for row in train_rows if row[0] % 5 == fold]
        if not held_out:
            continue
        model, encode, gram, rhs = _details_design(train, facts, raw)
        validation = [(mine, model.base(crowd), encode(key)) for key, mine, crowd in held_out]
        for index, penalty in enumerate(DETAIL_PENALTIES):
            weights = ridge_weights(gram, rhs, penalty)
            errors[index] += sum(
                abs(mine - _clamp(base + sum(weights[i] * value for i, value in vector)))
                for mine, base, vector in validation
            )
    penalty = min(
        zip(errors, DETAIL_PENALTIES, strict=True), key=lambda choice: (choice[0], -choice[1])
    )[1]
    return _details_fixed(train_rows, facts, context, penalty, raw)


SIMILAR_PRIORS = (1.0, 3.0, 10.0)
SIMILAR_NEIGHBORS = 20


def _similarity_graph(
    train_rows: Rows, facts: Facts, context: Context
) -> dict[int, tuple[tuple[float, int], ...]]:
    """Metadata-only neighbor order, reusable inside this fit's nested folds.

    Weighted Jaccard gives genres weight 1 and people weight 0.5. MAL's
    recommendation votes add a bounded link strength votes / (votes + 10);
    a Hardcover similar-book link has strength 1. Links work in either direction.
    A link combines with overlap as 1 - (1 - overlap) * (1 - link).
    """
    genre_names = sorted({g for f in facts.values() for g in f.get("genres", [])})
    people_names = sorted({p for f in facts.values() for p in f.get("studios", [])})
    genres = {name: 1 << index for index, name in enumerate(genre_names)}
    people = {name: 1 << index for index, name in enumerate(people_names)}
    signatures = {
        key: (
            sum(genres[g] for g in set(f.get("genres", []))),
            sum(people[p] for p in set(f.get("studios", []))),
        )
        for key, f in facts.items()
    }
    links: dict[int, dict[int, float]] = {}
    if context.medium == "anime":
        for source, target, votes in context.conn.execute(
            "SELECT mal_anime_id, recommended_id, num_recommendations "
            "FROM stg_mal_anime_recommendations"
        ):
            if votes > 0:
                strength = votes / (votes + 10)
                for left, right in ((source, target), (target, source)):
                    neighbors = links.setdefault(left, {})
                    neighbors[right] = max(neighbors.get(right, 0.0), strength)
    elif context.medium == "book":
        for key, f in facts.items():
            for target in json.loads(f.get("similar_json") or "[]"):
                if isinstance(target, int):
                    links.setdefault(key, {})[target] = 1.0
                    links.setdefault(target, {})[key] = 1.0

    train_ids = sorted({key for key, _, _ in train_rows})
    train_signatures = []
    for key in train_ids:
        g, p = signatures.get(key, (0, 0))
        train_signatures.append((key, g, p, g.bit_count() + 0.5 * p.bit_count()))
    graph = {}
    for key in context.crowd:
        g, p = signatures.get(key, (0, 0))
        size = g.bit_count() + 0.5 * p.bit_count()
        row_links = links.get(key, {})
        neighbors = []
        for other, other_g, other_p, other_size in train_signatures:
            if key == other:
                continue
            intersection = (g & other_g).bit_count() + 0.5 * (p & other_p).bit_count()
            link = row_links.get(other, 0.0)
            if not intersection and not link:
                continue
            overlap = intersection / (size + other_size - intersection) if intersection else 0.0
            neighbors.append((overlap + link - overlap * link, other))
        neighbors.sort(key=lambda pair: (-pair[0], pair[1]))
        graph[key] = tuple(neighbors)
    return graph


def _neighbor_leftovers(
    rows: Rows,
    model: TasteModel,
    graph: dict[int, tuple[tuple[float, int], ...]],
    keys: Sequence[int],
) -> dict[int, tuple[float, float]]:
    """Similarity-weighted residual sums and support, using this split's rows only."""
    leftovers = {key: mine - model.base(crowd) for key, mine, crowd in rows}
    return {key: _neighbor_sums(leftovers, graph.get(key, ())) for key in keys}


def _neighbor_sums(
    leftovers: dict[int, float], ranked: tuple[tuple[float, int], ...]
) -> tuple[float, float]:
    total = support = 0.0
    count = 0
    for weight, neighbor in ranked:
        if neighbor not in leftovers:
            continue
        total += weight * leftovers[neighbor]
        support += weight
        count += 1
        if count == SIMILAR_NEIGHBORS:
            break
    return total, support


def _fit_similar_with_graph(
    train_rows: Rows,
    facts: Facts,
    context: Context,
    graph: dict[int, tuple[tuple[float, int], ...]],
) -> Callable[[int], float]:
    errors = [0.0] * len(SIMILAR_PRIORS)
    for fold in range(5):
        train = [row for row in train_rows if row[0] % 5 != fold]
        held_out = [row for row in train_rows if row[0] % 5 == fold]
        if not held_out:
            continue
        # Only the crowd line supplies the base; unused leans need no fitting.
        model = build_model(train, {})
        neighbors = _neighbor_leftovers(train, model, graph, [key for key, _, _ in held_out])
        for key, mine, crowd in held_out:
            total, support = neighbors[key]
            base = model.base(crowd)
            for index, prior in enumerate(SIMILAR_PRIORS):
                errors[index] += abs(mine - _clamp(base + total / (support + prior)))
    # Prefer more shrinkage when validation error is exactly tied.
    index = min(range(len(SIMILAR_PRIORS)), key=lambda i: (errors[i], -SIMILAR_PRIORS[i]))
    prior = SIMILAR_PRIORS[index]
    model = build_model(train_rows, {})
    leftovers = {key: mine - model.base(crowd) for key, mine, crowd in train_rows}
    neighbors = {}

    def predict(title_id: int) -> float:
        if title_id not in neighbors:
            neighbors[title_id] = _neighbor_sums(leftovers, graph.get(title_id, ()))
        total, support = neighbors[title_id]
        return _clamp(model.base(context.crowd[title_id]) + total / (support + prior))

    predict.prior = prior
    return predict


def fit_similar(train_rows: Rows, facts: Facts, context: Context) -> Callable[[int], float]:
    """The closest 20 training titles' base-line leftovers, with inner-fold shrinkage."""
    graph = _similarity_graph(train_rows, facts, context)
    return _fit_similar_with_graph(train_rows, facts, context, graph)


def _blend_weights(
    predictions: Sequence[tuple[float, float, float, float]],
) -> tuple[float, ...]:
    """Choose tenth-step simplex weights from training-only out-of-fold predictions.

    Tuple fields are actual rating, Usual, Details, Similar. MAE ties at twelve
    decimal places prefer Usual, then Details, avoiding floating-point tie noise.
    """
    best = (float("inf"), 0, 0)
    weights = (1.0, 0.0, 0.0)
    for usual in range(10, -1, -1):
        for details in range(10 - usual, -1, -1):
            similar = 10 - usual - details
            error = sum(
                abs(mine - (usual * u + details * d + similar * s) / 10)
                for mine, u, d, s in predictions
            )
            rank = (round(error / max(len(predictions), 1), 12), -usual, -details)
            if rank < best:
                best = rank
                weights = (usual / 10, details / 10, similar / 10)
    return weights


def _blend_predictions(
    train_rows: Rows,
    facts: Facts,
    context: Context,
    graph: dict[int, tuple[tuple[float, int], ...]],
) -> list[tuple[float, float, float, float]]:
    """Refit and retune both components inside each blend training fold."""
    predictions = []
    for fold in range(5):
        train = [row for row in train_rows if row[0] % 5 != fold]
        held_out = [row for row in train_rows if row[0] % 5 == fold]
        if not held_out:
            continue
        usual = _usual_predictor(train, facts, context)
        details = fit_details(train, facts, context)
        similar = _fit_similar_with_graph(train, facts, context, graph)
        predictions.extend(
            (mine, usual(key), details(key), similar(key)) for key, mine, _ in held_out
        )
    return predictions


def fit_blend(train_rows: Rows, facts: Facts, context: Context) -> Callable[[int], float]:
    """Blend Usual, Details, and Similar with weights learned inside the training part."""
    graph = _similarity_graph(train_rows, facts, context)
    weights = _blend_weights(_blend_predictions(train_rows, facts, context, graph))
    # A zero-weight component needs no final fit. Its validation predictions were
    # still fitted and tuned on each inner training part before selecting weights.
    predictors = (
        _usual_predictor(train_rows, facts, context) if weights[0] else None,
        fit_details(train_rows, facts, context) if weights[1] else None,
        _fit_similar_with_graph(train_rows, facts, context, graph) if weights[2] else None,
    )

    def predict(title_id: int) -> float:
        values = [
            (weight, model(title_id))
            for weight, model in zip(weights, predictors, strict=True)
            if model
        ]
        first = values[0][1]
        if all(value == first for _, value in values):
            return first
        return _clamp(sum(weight * value for weight, value in values))

    predict.weights = weights
    return predict
