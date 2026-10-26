"""Read-only, Qt-free comparisons on the same five held-out rating folds.

Fits receive training ratings only. Facts and crowd scores are item metadata, so
they may cover held-out titles too. A fit must not read rating values through the
connection in Context. Future tuning belongs inside the training part.
"""

from __future__ import annotations

import math
import random
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import mean, quantiles
from time import perf_counter
from typing import Any, NamedTuple

from taste import settings
from taste.recommend import experiments
from taste.recommend.anime import _anime_facts, _scored
from taste.recommend.books import _book_facts, _book_scored
from taste.recommend.model import MIN_RESIDUALS, build_model, shrunk, whole_score
from taste.recommend.screen import _screen_facts, _screen_scored, _typical_tmdb_average

MEDIA = ("anime", "film", "tv", "book")
Rows = list[tuple[int, float, float]]
Facts = dict[int, dict[str, Any]]
Predictions = list[tuple[int, float, float]]  # source id, actual rating, held-out prediction


@dataclass(frozen=True)
class Context:
    conn: sqlite3.Connection
    medium: str
    crowd: dict[int, float]
    # Freeze recency to the latest stored rating date, not the day the lab is run.
    now: date


Fit = Callable[[Rows, Facts, Context], Callable[[int], float]]


class Candidate(NamedTuple):
    name: str
    fit: Fit


def _fit(
    train_rows: Rows,
    facts: Facts,
    context: Context,
    *,
    dismissal: bool = False,
    recency: bool = False,
) -> Callable[[int], float]:
    model = experiments.apply(
        context.conn,
        context.medium,
        build_model(train_rows, facts),
        train_rows,
        facts,
        context.now,
        dismissal=dismissal,
        recency=recency,
    )

    def predict(title_id: int) -> float:
        f = facts.get(title_id, {})
        return model.predict(context.crowd[title_id], f.get("genres", []), f.get("studios", []))

    return predict


def fit_usual(train_rows: Rows, facts: Facts, context: Context) -> Callable[[int], float]:
    return _fit(train_rows, facts, context)


def fit_dismissals(train_rows: Rows, facts: Facts, context: Context) -> Callable[[int], float]:
    return _fit(train_rows, facts, context, dismissal=True)


def fit_recency(train_rows: Rows, facts: Facts, context: Context) -> Callable[[int], float]:
    return _fit(train_rows, facts, context, recency=True)


DEFAULT_CANDIDATES = (
    Candidate("usual", fit_usual),
    Candidate("dismissals", fit_dismissals),
    Candidate("recency", fit_recency),
)


def load_data(conn: sqlite3.Connection, medium: str) -> tuple[Rows, Facts, Context]:
    """Use the lists' own rows and facts, including hidden adult anime in the counts.

    Recency is relative to this medium's latest stored source_updated_at date.
    Without dates, the epoch is a fixed reference and no rating gets extra weight.
    Books retain the existing doubled-star scale. TMDB uses the profile's prior.
    """
    if medium == "anime":
        rows, facts = _scored(conn), _anime_facts(conn)
        crowd = {
            key: f["community_mean"] for key, f in facts.items() if f["community_mean"] is not None
        }
    elif medium == "book":
        rows, facts = _book_scored(conn), _book_facts(conn)
        crowd = {key: f["rating"] * 2 for key, f in facts.items() if f["rating"] is not None}
    elif medium in ("film", "tv"):
        kind = "movie" if medium == "film" else "tv"
        typical = _typical_tmdb_average(conn, kind)
        prior = settings.get(conn, "film_prior_votes")
        rows, facts = _screen_scored(conn, typical, prior, kind), _screen_facts(conn, kind)
        crowd = {
            key: shrunk(f["vote_average"], f["vote_count"], typical, prior)
            for key, f in facts.items()
            if f["vote_average"] is not None
        }
    else:
        raise ValueError("Unknown rating medium.")
    latest = max(
        (stamp[:10] for stamp in experiments._dates(conn, medium).values()), default="1970-01-01"
    )
    return rows, facts, Context(conn, medium, crowd, date.fromisoformat(latest))


def holdout_predictions(
    rows: Rows, facts: Facts, context: Context, candidate: Candidate
) -> Predictions:
    """Predict each rated title once without fitting on its fold (source id % 5).

    Fold-major order matches holdout_residuals exactly. Predictions remain
    available to tests and later callers; the CLI never prints item identifiers.
    """
    if len(rows) < MIN_RESIDUALS:
        return []
    result = []
    for fold in range(5):
        train = [row for row in rows if row[0] % 5 != fold]
        predict = candidate.fit(train, facts, context)
        for key, mine, _ in rows:
            if key % 5 == fold:
                result.append((key, mine, predict(key)))
    return result


def _ranks(values: Sequence[float]) -> list[float]:
    """Ascending ranks, assigning the average rank to every member of a tie."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        rank = (start + 1 + stop) / 2
        for index in order[start:stop]:
            ranks[index] = rank
        start = stop
    return ranks


def spearman(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    """Pearson correlation of average ranks; undefined for a constant vector."""
    left, right = _ranks(actual), _ranks(predicted)
    center = (len(left) + 1) / 2
    left = [value - center for value in left]
    right = [value - center for value in right]
    spread = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
    if not spread:
        return None
    return sum(x * y for x, y in zip(left, right, strict=True)) / spread


def top_hits(predictions: Predictions) -> float:
    """Recall of the top ceil(n / 5), pooling all out-of-fold predictions.

    Source id ascending breaks boundary ties on both lists, keeping exactly k
    titles. Shared tie ordering can favor constant predictions; this is an
    explicit convention, not evidence that a constant model learned preferences.
    """
    count = math.ceil(len(predictions) / 5)
    actual = {key for key, _, _ in sorted(predictions, key=lambda r: (-r[1], r[0]))[:count]}
    predicted = {key for key, _, _ in sorted(predictions, key=lambda r: (-r[2], r[0]))[:count]}
    return len(actual & predicted) / count


def paired_bootstrap(
    errors: Sequence[float], usual_errors: Sequence[float]
) -> tuple[float, tuple[float, float]]:
    """Candidate minus Usual absolute error, with a paired 95% percentile interval.

    Each of the 1,000 resamples draws title-level error differences with
    replacement. Inclusive quantiles interpolate the 2.5th and 97.5th percentiles.
    A fresh local generator makes every comparison reproducible.
    """
    differences = [error - usual for error, usual in zip(errors, usual_errors, strict=True)]
    rng = random.Random(0)
    samples = [mean(rng.choices(differences, k=len(differences))) for _ in range(1000)]
    percentiles = quantiles(samples, n=40, method="inclusive")
    return mean(differences), (percentiles[0], percentiles[-1])


def evaluate(
    conn: sqlite3.Connection,
    medium: str,
    candidates: Sequence[Candidate] = DEFAULT_CANDIDATES,
) -> list[dict[str, Any]]:
    """Compare candidates on identical held-out titles. No setup or writes.

    Below MIN_RESIDUALS every numeric field except count stays empty. Elapsed
    seconds include fitting, prediction, metrics, and the paired bootstrap;
    timing is observational and is the only result expected to vary on reruns.
    """
    rows, facts, context = load_data(conn, medium)
    if len({c.name for c in candidates}) != len(candidates):
        raise ValueError("Candidate names must be unique.")
    if len(rows) < MIN_RESIDUALS:
        return [
            {
                "candidate": c.name,
                "count": len(rows),
                "mae": None,
                "whole_mae": None,
                "spearman": None,
                "top_hits": None,
                "vs_usual": None,
                "interval": None,
                "verdict": None,
                "seconds": None,
            }
            for c in candidates
        ]

    start = perf_counter()
    usual = holdout_predictions(rows, facts, context, DEFAULT_CANDIDATES[0])
    usual_seconds = perf_counter() - start
    usual_errors = [abs(mine - guess) for _, mine, guess in usual]
    results = []
    for candidate in candidates:
        start = perf_counter()
        is_usual = candidate.fit is fit_usual
        predictions = usual if is_usual else holdout_predictions(rows, facts, context, candidate)
        errors = [abs(mine - guess) for _, mine, guess in predictions]
        difference, interval = paired_bootstrap(errors, usual_errors)
        results.append(
            {
                "candidate": candidate.name,
                "count": len(predictions),
                "mae": mean(errors),
                "whole_mae": mean(abs(mine - whole_score(guess)) for _, mine, guess in predictions)
                if medium == "anime"
                else None,
                "spearman": spearman(
                    [mine for _, mine, _ in predictions], [guess for _, _, guess in predictions]
                ),
                "top_hits": top_hits(predictions),
                "vs_usual": difference,
                "interval": interval,
                "verdict": "better" if interval[1] < 0 else "worse" if interval[0] > 0 else "same",
                "seconds": perf_counter() - start + (usual_seconds if is_usual else 0),
            }
        )
    return results
