"""Bands: my average score per slice of a crowd's average (MAL on Anime, Hardcover on
Books), with the middle half of my scores. Shared by the report pages; no Qt."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, quantiles

BAND_WIDTH = 0.5  # MAL mean points per band of the score chart
MIDDLE_MIN_SHOWS = 4  # fewer than this and a band's middle half says nothing


@dataclass(frozen=True)
class Band:
    """My scores for shows whose MAL mean falls in [mal_from, mal_to)."""

    mal_from: float
    mal_to: float
    shows: int
    average: float
    middle: tuple[float, float] | None  # 25th to 75th percentile of my scores


def score_bands(points: list[tuple[float, float]], width: float = BAND_WIDTH) -> list[Band]:
    """(crowd average, my score) pairs, averaged per `width` of the crowd's average."""
    groups: dict[float, list[float]] = {}
    for community, mine in points:
        start = math.floor(community / width) * width
        groups.setdefault(start, []).append(mine)
    bands = []
    for start in sorted(groups):
        scores = groups[start]
        middle = None
        if len(scores) >= MIDDLE_MIN_SHOWS:
            q1, _, q3 = quantiles(scores, n=4, method="inclusive")
            middle = (round(q1, 2), round(q3, 2))
        bands.append(Band(start, start + width, len(scores), round(mean(scores), 2), middle))
    return bands
