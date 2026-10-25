"""Shared rating model and reason helpers."""

from __future__ import annotations

import bisect
import math
import sqlite3
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

GENRE_SHRINK = 5  # a genre's bias counts fully only after several shows


STUDIO_SHRINK = 3


STUDIO_WEIGHT = 0.5  # studios overlap with genres, so they count half


SLOPE_MIN_SHOWS = 30  # fewer scored shows than this and a fitted slope swings wildly


MAX_REASON_SEEDS = 2


GENRE_MENTION = 0.15  # a genre lean this big is worth a word on the card


GENRE_WARNING = -0.3


MIN_RESIDUALS = 20  # fewer past predictions than this and the chance of an 8+ isn't shown


@dataclass
class Rec:
    kind: str
    item_key: str
    title: str
    subtitle: str = ""
    score: float | None = None
    support: float = 0.0
    badge: str = ""
    image_url: str | None = None
    url: str | None = None
    reasons: list[str] = field(default_factory=list)  # plain sentences for the card
    # mal_mean, chance_8_plus, match_label, and details (the numbers, for a tooltip)
    facts: dict[str, Any] = field(default_factory=dict)


def short_count(n: int) -> str:
    """1,234 -> "1.2k", 3,400,000 -> "3.4M"; small numbers as they are."""
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= size:
            value = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{value}{suffix}"
    return f"{n:,}"


ABOUT_MAX = 900  # characters of a synopsis or bio kept for the panel


def about_text(text: str | None) -> str:
    """A summary trimmed for the panel: at most ABOUT_MAX characters, cut at a sentence
    when there's one, and without MAL's "[Written by MAL Rewrite]" credit line."""
    if not text:
        return ""
    text = " ".join(text.replace("[Written by MAL Rewrite]", "").split())
    if len(text) <= ABOUT_MAX:
        return text
    cut = text[:ABOUT_MAX]
    stop = cut.rfind(". ")
    return cut[: stop + 1] if stop > ABOUT_MAX // 2 else cut.rstrip() + "…"


@dataclass
class TasteModel:
    overall: float  # my average (score - community mean), "my usual difference"
    genre: dict[str, float]  # shrunk extra bias per genre
    genre_counts: dict[str, int]
    studio: dict[str, float]
    # Base prediction: intercept + slope x community mean. A plain shift (slope 1,
    # intercept = overall) until there are enough scored shows to fit a slope.
    intercept: float | None = None
    slope: float = 1.0

    def base(self, community_mean: float) -> float:
        intercept = self.overall if self.intercept is None else self.intercept
        return intercept + self.slope * community_mean

    def adjustment(self, genres: list[str], studios: list[str]) -> float:
        # Plain sums: statistics.mean is exact but slow, and this runs for every candidate.
        g = sum(self.genre.get(x, 0.0) for x in genres) / len(genres) if genres else 0.0
        s = sum(self.studio.get(x, 0.0) for x in studios) / len(studios) if studios else 0.0
        return g + STUDIO_WEIGHT * s

    def predict(self, community_mean: float, genres: list[str], studios: list[str]) -> float:
        value = self.base(community_mean) + self.adjustment(genres, studios)
        return min(max(value, 1.0), 10.0)


def build_model(scored: list[tuple[int, float, float]], facts: dict[int, dict]) -> TasteModel:
    """My score from the MAL mean, then shrunk genre and studio leans on what's left.

    With enough scored shows the MAL mean is scaled, not just shifted: a harsh critic
    is often harsher on weak shows than on strong ones (checked on held-out shows
    before adopting it). Below SLOPE_MIN_SHOWS it's a plain shift.
    """
    if not scored:
        return TasteModel(0.0, {}, {}, {})
    overall = mean(s - c for _, s, c in scored)
    model = TasteModel(overall, {}, {}, {})
    if len(scored) >= SLOPE_MIN_SHOWS:
        xs = [c for _, _, c in scored]
        ys = [s for _, s, _ in scored]
        mx, my = mean(xs), mean(ys)
        spread = sum((x - mx) ** 2 for x in xs)
        if spread > 0:
            model.slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / spread
            model.intercept = my - model.slope * mx
    sums: dict[str, list[float]] = {}
    studio_sums: dict[str, list[float]] = {}
    for anime_id, score, community in scored:
        extra = score - model.base(community)
        for g in facts.get(anime_id, {}).get("genres", []):
            sums.setdefault(g, []).append(extra)
        for s in facts.get(anime_id, {}).get("studios", []):
            studio_sums.setdefault(s, []).append(extra)
    model.genre = {g: sum(v) / (len(v) + GENRE_SHRINK) for g, v in sums.items()}
    model.genre_counts = {g: len(v) for g, v in sums.items()}
    model.studio = {s: sum(v) / (len(v) + STUDIO_SHRINK) for s, v in studio_sums.items()}
    return model


def whole_score(predicted: float) -> int:
    """The whole score a prediction points to, as the card shows it: 7.5 or more is an 8.

    Rounded to the one decimal the card also shows first, so it never reads "7 (7.5)".
    """
    return math.floor(round(predicted, 1) + 0.5)


def _dismissed(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT item_key FROM rec_dismissed WHERE kind = ?", (kind,))
    }


def _a(score: int) -> str:
    """'a 9', 'an 8'."""
    return f"an {score}" if score in (8, 11, 18) else f"a {score}"


def _genre_leans(model: TasteModel, genres: list[str]) -> tuple[str | None, str | None]:
    """(a genre of this show I lean toward, one I'm usually hard on), when worth a mention."""
    # Rounded as shown, so a lean that is exactly at a cutoff doesn't depend on
    # floating point noise in the last digit.
    scored = [(round(model.genre[g], 2), g) for g in genres if g in model.genre]
    best = max(scored, default=None)
    worst = min(scored, default=None)
    return (
        best[1] if best and best[0] >= GENRE_MENTION else None,
        worst[1] if worst and worst[0] <= GENRE_WARNING else None,
    )


def _loved(score: float) -> str:
    return "loved" if score >= 9 else "really liked" if score >= 8 else "liked"


def _because(
    seeds: list[tuple[str, float]],
    liked_genre: str | None,
    harsh_genre: str | None,
    noun: str = "anime",
) -> list[str]:
    """The card's reasons in plain words, no numbers.

    "Because you loved A and really liked B, and you tend to enjoy samurai anime."
    """
    sentences = []
    clauses = []  # "loved A", "B", "really liked C": a verb only when it changes
    last_verb = None
    for title, score in seeds:
        verb = _loved(score)
        clauses.append(title if verb == last_verb else f"{verb} {title}")
        last_verb = verb
    genre = f"you tend to enjoy {liked_genre.lower()} {noun}" if liked_genre else ""
    if clauses:
        sentence = "Because you " + " and ".join(clauses)
        sentences.append(sentence + (f", and {genre}." if genre else "."))
    elif genre:
        sentences.append(genre[0].upper() + genre[1:] + ".")
    if harsh_genre:
        sentences.append(f"Heads up: you're usually tougher on {harsh_genre.lower()} {noun}.")
    return sentences


def holdout_residuals(
    scored: list[tuple[int, float, float]], facts: dict[int, dict]
) -> list[float]:
    """How far my real scores landed from predictions made without them (5 folds).

    Every scored show is predicted once by a model that never saw it. Used to say how
    likely a predicted score is to end up an 8 or more.
    """
    if len(scored) < MIN_RESIDUALS:
        return []
    residuals = []
    for fold in range(5):
        train = [x for x in scored if x[0] % 5 != fold]
        model = build_model(train, facts)
        for anime_id, score, community in scored:
            if anime_id % 5 == fold:
                f = facts.get(anime_id, {})
                guess = model.predict(community, f.get("genres", []), f.get("studios", []))
                residuals.append(score - guess)
    return residuals


def chance_at_least(predicted: float, residuals: list[float], at_least: int) -> float | None:
    """Share of past misses that would put this show at `at_least` or more once rounded.

    `residuals` must be sorted. A binary search finds the first miss that gets there
    (the same comparison as checking each one, in log time).
    """
    if len(residuals) < MIN_RESIDUALS:
        return None
    first = bisect.bisect_left(residuals, at_least - 0.5, key=lambda r: predicted + r)
    return (len(residuals) - first) / len(residuals)


# How much a TMDB average is trusted: from this many votes it counts half, the other half
# being TMDB's typical average; fewer votes, less (approved 2026-10-15: brand-new films
# with a high average from few votes led the list on early hype).
FILM_PRIOR_VOTES = 1000  # the default of the film_prior_votes setting


def shrunk(average: float, votes: int, typical: float, prior: int = FILM_PRIOR_VOTES) -> float:
    """A TMDB average pulled toward the typical one, by how few votes it rests on."""
    votes = max(votes or 0, 0)
    if votes + prior == 0:
        return average
    return (votes * average + prior * typical) / (votes + prior)


def holdout_check(scored: list[tuple[int, float, float]], residuals: list[float],
                  raw: dict[int, float]) -> dict[str, Any]:  # fmt: skip
    """How far predictions landed from my real ratings on titles they never saw, next to
    TMDB's own average alone (both mean absolute errors)."""
    if not residuals:
        return {}
    return {
        "holdout_mae": round(sum(abs(r) for r in residuals) / len(residuals), 3),
        "holdout_count": len(residuals),
        "tmdb_alone_mae": round(
            sum(abs(score - raw[tmdb_id]) for tmdb_id, score, _ in scored) / len(scored), 3
        ),
    }
