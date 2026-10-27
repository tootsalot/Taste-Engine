"""Read-only check of cross-media support against five-fold Usual misses."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from statistics import mean

from taste import activity
from taste.recommend import candidates, everything, lab


def _summary(
    medium: str, rated: int, supported: int, observations: list[tuple[float, float]]
) -> dict:
    """Keep zeros separate; low and high are the outer thirds of positive support.

    Rank thirds contain floor(n / 3) observations, or one for a smaller sample.
    Ties retain the deterministic source-id order of the held-out predictions.
    The same observation can occupy both outer thirds when it is the only one.
    Misses use the existing Usual 1 to 10 scale, including doubled book stars.
    """
    positive = sorted((row for row in observations if row[0] > 0), key=lambda row: row[0])
    third = max(1, len(positive) // 3)
    bins = {
        "no_support": [miss for support, miss in observations if support == 0],
        "low_support": [miss for _, miss in positive[:third]],
        "high_support": [miss for _, miss in positive[-third:]],
    }
    return {
        "medium": medium,
        "rated": rated,
        "supported": supported,
        "checked": len(observations),
        "spearman": lab.spearman(
            [support for support, _ in observations], [miss for _, miss in observations]
        ),
        **{name: mean(values) if values else None for name, values in bins.items()},
        "bin_counts": {name: len(values) for name, values in bins.items()},
    }


def evaluate(conn: sqlite3.Connection, media: Sequence[str] = lab.MEDIA) -> list[dict]:
    """Check all rated targets against liked titles in other media, without writes.

    The shared index applies the recommendation thresholds and excludes hidden
    adult anime from seeds while retaining their ratings in the averages. A
    target's own score cannot select or weight seeds from another medium.
    Counts cover every rated title; misses need crowd metadata and the same
    minimum sample as the Usual five-fold calibration. No calibration cache is
    read or written, and no candidate recommendation model is fitted.
    """
    titles = everything.load_cross_data(conn)
    index = everything.CrossIndex(titles, hidden=activity.hidden_items(conn))
    support = {
        (title.medium, title.item_key): index.support(title)["cross_support"]
        for title in titles
        if title.my_score is not None
    }
    results, all_observations = [], []
    total_rated = total_supported = 0
    for medium in media:
        values = [value for (kind, _), value in support.items() if kind == medium]
        rows, facts, context = lab.load_data(conn, medium)
        predictions = candidates.holdout_predictions(rows, facts, context, candidates.fit_usual)
        observations = [
            (support.get((medium, str(key)), 0.0), mine - guess) for key, mine, guess in predictions
        ]
        rated, supported = len(values), sum(value > 0 for value in values)
        results.append(_summary(medium, rated, supported, observations))
        all_observations.extend(observations)
        total_rated += rated
        total_supported += supported
    results.append(_summary("overall", total_rated, total_supported, all_observations))
    return results
