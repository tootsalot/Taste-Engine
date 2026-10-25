"""Book candidates from Hardcover."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from typing import Any

from taste import settings, themes
from taste.recommend import experiments
from taste.recommend.model import (
    MAX_REASON_SEEDS,
    Rec,
    _because,
    _dismissed,
    _genre_leans,
    about_text,
    build_model,
    chance_at_least,
    holdout_residuals,
    short_count,
)
from taste.sources import hardcover

BOOK_MIN_RATINGS = 20  # Hardcover averages from fewer ratings than this are too unsettled


BOOK_URL = "https://hardcover.app/books/{}"


def _book_facts(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Every book in staging, with genres and authors in the model's slots."""
    facts = {}
    for row in conn.execute("SELECT * FROM stg_hardcover_books"):
        authors = [a.get("name") for a in json.loads(row["authors_json"] or "[]")]
        facts[row["book_id"]] = {
            **dict(row),
            "genres": json.loads(row["genres_json"] or "[]"),
            "studios": [a for a in authors if a],  # authors stand where studios do
        }
    return facts


def _book_scored(conn: sqlite3.Connection) -> list[tuple[int, float, float]]:
    """(book id, my stars x2, Hardcover average x2): stars doubled onto the anime model's
    1 to 10 scale, so the same fit and limits apply."""
    return [
        (r[0], r[1] * 2, r[2] * 2)
        for r in conn.execute(
            "SELECT ub.book_id, ub.rating, b.rating FROM stg_hardcover_user_books ub "
            "JOIN stg_hardcover_books b ON b.book_id = ub.book_id "
            "WHERE ub.removed_at IS NULL AND ub.rating IS NOT NULL AND b.rating IS NOT NULL"
        )
    ]


def book_recommendations(
    conn: sqlite3.Connection, now: datetime | None = None
) -> tuple[list[Rec], dict[str, Any]]:
    """Books I don't have, predicted the anime way on doubled stars: the Hardcover average
    shifted (or scaled) by how I usually differ from it, plus genre and author leans.
    Candidates are Hardcover's similar books for the ones I rated highest, plus my Want
    to Read."""
    cfg = settings.get_all(conn)
    facts = _book_facts(conn)
    scored = _book_scored(conn)
    theme_taste = themes.profile(conn) if cfg["rec_theme_transfer"] else None
    model = build_model(scored, facts)
    model = experiments.apply(conn, "book", model, scored, facts, now)
    mine = hardcover.my_mean_rating(conn)
    support = hardcover.candidate_support(conn)
    dismissed = _dismissed(conn, "book")
    shelf = dict(
        conn.execute(
            "SELECT book_id, status_id FROM stg_hardcover_user_books WHERE removed_at IS NULL"
        ).fetchall()
    )
    wanted = {b for b, status in shelf.items() if status == 1}
    sources: dict[int, list[tuple[float, str, float]]] = {}
    if mine is not None:
        for title, rating, similar in conn.execute(
            "SELECT b.title, ub.rating, b.similar_json FROM stg_hardcover_user_books ub "
            "JOIN stg_hardcover_books b ON b.book_id = ub.book_id "
            "WHERE ub.removed_at IS NULL AND ub.rating > ?",
            (mine,),
        ):
            for position, book_id in enumerate(json.loads(similar or "[]")):
                weight = (rating - mine) / (1 + position / 10)
                sources.setdefault(book_id, []).append((weight, title, rating))
    residuals = sorted(holdout_residuals(scored, facts))  # chance_at_least needs them sorted
    if cfg["rec_dismissal_signal"] or cfg["rec_recent_ratings"] or (cfg["rec_theme_transfer"]):
        residuals = sorted(experiments.residuals(conn, "book", scored, facts, now))
    recs: list[Rec] = []
    for book_id in set(support) | wanted:
        f = facts.get(book_id)
        if not f or not f.get("details_at") or str(book_id) in dismissed:
            continue  # no details yet (fetched on a later refresh), or dismissed
        if book_id in shelf and book_id not in wanted:
            continue  # read, reading, paused, or given up
        if f["rating"] is None or (f["ratings_count"] or 0) < BOOK_MIN_RATINGS:
            continue
        predicted = model.predict(f["rating"] * 2, f["genres"], f["studios"])
        if theme_taste is not None:
            shared = themes.candidate_themes(conn, "book", str(book_id), f)
            theme_shift = themes.transfer(theme_taste, "book", shared, len(scored))
            predicted = min(10, max(1, predicted + theme_shift))
        stars = round(predicted / 2, 2)
        rec = Rec(
            kind="book",
            item_key=str(book_id),
            title=f["title"],
            score=stars,
            support=round(support.get(book_id, 0.0), 3),
            image_url=f["image_url"],
            url=BOOK_URL.format(f["slug"]) if f.get("slug") else None,
        )
        parts = [f["studios"][0] if f["studios"] else "", str(f["release_year"] or ""),
                 f"{f['pages']:,} pages" if f["pages"] else ""]  # fmt: skip
        rec.subtitle = " · ".join(p for p in parts if p)
        rec.facts["hardcover_mean"] = f["rating"]
        chance = chance_at_least(predicted, residuals, 8)
        if chance is not None:
            rec.facts["chance_4_plus"] = round(chance, 3)
        rec.facts["about"] = about_text(f.get("description"))
        rec.facts["popularity"] = [
            text
            for text in (
                f"{short_count(f['users_count'])} readers on Hardcover"
                if f.get("users_count")
                else "",
                f"{short_count(f['ratings_count'])} ratings" if f.get("ratings_count") else "",
                f"{short_count(f['users_read_count'])} have read it"
                if f.get("users_read_count")
                else "",
            )
            if text
        ]
        if book_id in wanted:
            rec.badge = "On your Want to Read"
        best = sorted(sources.get(book_id, []), reverse=True)[:MAX_REASON_SEEDS]
        seeds = [(title, rating * 2) for _, title, rating in best]  # wording on the 10 scale
        liked, harsh = _genre_leans(model, f["genres"])
        rec.reasons.extend(_because(seeds, liked, harsh, noun="books"))
        details = [
            f"Hardcover lists it as similar to {t}, which you gave {r:g}★." for _, t, r in best
        ]
        for genre in filter(None, (liked, harsh)):
            details.append(f"You rate {genre} books {model.genre[genre] / 2:+.2f}★ vs your usual.")
        if theme_taste is not None:
            details.append(
                f"Experimental theme adjustment: {theme_shift / 2:+.2f} stars, "
                f"shrunk over {len(scored)} own ratings."
            )
        rec.facts["details"] = details
        recs.append(rec)
    recs.sort(key=lambda r: (-(r.score + 0.08 * math.log1p(r.support)), r.title))
    metrics = {"model_overall_bias": round(model.overall / 2, 3), "rated": len(scored)}
    metrics.update(experiments.metrics(conn, "book", scored, facts, now))
    metrics["theme_transfer"] = experiments.transfer_metrics(conn, "book", scored, facts, now)
    return recs[: cfg["rec_count"]], metrics
