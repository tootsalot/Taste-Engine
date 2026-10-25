"""Optional rating-model experiments and comparable holdouts. Both switches default off."""

from __future__ import annotations

import calendar
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone

from taste import settings
from taste.recommend.model import (
    GENRE_SHRINK,
    MIN_RESIDUALS,
    SLOPE_MIN_SHOWS,
    STUDIO_SHRINK,
    TasteModel,
    _dismissed,
    build_model,
)

RECENT_WEIGHT = 1.25
DISMISSAL_STRENGTH = 0.3


def _dates(conn: sqlite3.Connection, kind: str) -> dict[int, str]:
    source, id_type, media = {
        "anime": ("mal", "mal_anime_id", "anime"),
        "film": ("tmdb", "tmdb_movie_id", "film"),
        "tv": ("tmdb", "tmdb_tv_id", "tv_show"),
        "book": ("hardcover", "hardcover_book_id", "book"),
    }[kind]
    return {
        int(key): stamp
        for key, stamp in conn.execute(
            "SELECT x.external_id, r.source_updated_at FROM core_ratings r "
            "JOIN core_item_external_ids x ON x.item_id = r.item_id AND x.source = r.source "
            "JOIN core_items i ON i.item_id = r.item_id "
            "WHERE r.source = ? AND x.id_type = ? AND i.media_type = ?",
            (source, id_type, media),
        )
        if key.isdigit() and stamp
    }


def weighted_model(scored: list[tuple], facts: dict, weights: dict[int, float]) -> TasteModel:
    """The same fit, with explicit sample weights and the same shrinkage constants."""
    if not scored:
        return build_model(scored, facts)
    total = sum(weights.get(k, 1) for k, _, _ in scored)

    def average(values):
        return sum(value * weights.get(key, 1) for key, value in values) / total

    overall = average([(key, mine - crowd) for key, mine, crowd in scored])
    model = TasteModel(overall, {}, {}, {})
    if len(scored) >= SLOPE_MIN_SHOWS:
        mx = average([(key, crowd) for key, _, crowd in scored])
        my = average([(key, mine) for key, mine, _ in scored])
        spread = sum(weights.get(k, 1) * (crowd - mx) ** 2 for k, _, crowd in scored)
        if spread:
            model.slope = (
                sum(weights.get(k, 1) * (crowd - mx) * (mine - my) for k, mine, crowd in scored)
                / spread
            )
            model.intercept = my - model.slope * mx
    for slot, shrink in (("genres", GENRE_SHRINK), ("studios", STUDIO_SHRINK)):
        sums, counts = Counter(), Counter()
        for key, mine, crowd in scored:
            for name in facts.get(key, {}).get(slot, []):
                sums[name] += weights.get(key, 1) * (mine - model.base(crowd))
                counts[name] += weights.get(key, 1)
        leans = {name: value / (counts[name] + shrink) for name, value in sums.items()}
        if slot == "genres":
            model.genre = leans
            model.genre_counts = dict(
                Counter(g for key, _, _ in scored for g in facts.get(key, {}).get(slot, []))
            )
        else:
            model.studio = leans
    return model


def apply(
    conn: sqlite3.Connection,
    kind: str,
    model: TasteModel,
    scored: list[tuple],
    facts: dict,
    now: datetime | date | None = None,
    *,
    dismissal: bool | None = None,
    recency: bool | None = None,
) -> TasteModel:
    """Return the original model when off. Never write experimental signals into ratings."""
    dismissal = settings.get(conn, "rec_dismissal_signal") if dismissal is None else dismissal
    recency = settings.get(conn, "rec_recent_ratings") if recency is None else recency
    if recency:
        reference = now or datetime.now(timezone.utc)
        today = reference.date() if isinstance(reference, datetime) else reference
        cutoff = date(
            today.year - 2,
            today.month,
            min(today.day, calendar.monthrange(today.year - 2, today.month)[1]),
        ).isoformat()
        weights = {
            key: RECENT_WEIGHT
            for key, stamp in _dates(conn, kind).items()
            if cutoff <= stamp[:10] <= today.isoformat()
        }
        model = weighted_model(scored, facts, weights)
    if dismissal:
        genre, people = Counter(), Counter()
        for key in _dismissed(conn, kind):
            f = facts.get(int(key), {}) if key.isdigit() else {}
            genre.update(set(f.get("genres", [])))
            people.update(set(f.get("studios", [])))
        model = TasteModel(
            model.overall,
            dict(model.genre),
            dict(model.genre_counts),
            dict(model.studio),
            model.intercept,
            model.slope,
        )
        for counts, leans, shrink in (
            (genre, model.genre, GENRE_SHRINK),
            (people, model.studio, STUDIO_SHRINK),
        ):
            for name, count in counts.items():
                leans[name] = leans.get(name, 0) - DISMISSAL_STRENGTH * count / (count + shrink)
    return model


def residuals(
    conn: sqlite3.Connection,
    kind: str,
    scored: list[tuple],
    facts: dict,
    now: datetime | date | None = None,
    **switches,
) -> list[float]:
    if len(scored) < MIN_RESIDUALS:
        return []
    result = []
    for fold in range(5):
        train = [row for row in scored if row[0] % 5 != fold]
        model = apply(conn, kind, build_model(train, facts), train, facts, now, **switches)
        for key, mine, crowd in scored:
            if key % 5 == fold:
                f = facts.get(key, {})
                result.append(
                    mine - model.predict(crowd, f.get("genres", []), f.get("studios", []))
                )
    return result


def metrics(
    conn: sqlite3.Connection,
    kind: str,
    scored: list[tuple],
    facts: dict,
    now: datetime | date | None = None,
) -> dict:
    def check(dismissal, recency):
        errors = residuals(conn, kind, scored, facts, now, dismissal=dismissal, recency=recency)
        return {
            "count": len(errors),
            "mae": round(sum(map(abs, errors)) / len(errors), 3) if errors else None,
        }

    off = check(False, False)
    return {
        "experiments": {
            "dismissal": {
                "enabled": settings.get(conn, "rec_dismissal_signal"),
                "off": off,
                "on": check(True, False),
            },
            "recency": {
                "enabled": settings.get(conn, "rec_recent_ratings"),
                "off": off,
                "on": check(False, True),
            },
        }
    }
