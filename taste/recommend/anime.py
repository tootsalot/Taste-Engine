"""Anime candidates and their holdout check."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from statistics import mean
from typing import Any

from taste import enrich, settings
from taste.recommend import experiments
from taste.recommend.model import (
    MAX_REASON_SEEDS,
    Rec,
    _a,
    _because,
    _dismissed,
    _genre_leans,
    about_text,
    build_model,
    chance_at_least,
    holdout_residuals,
    short_count,
    whole_score,
)

RELATION_PHRASES = {
    "sequel": "the sequel to",
    "prequel": "the prequel to",
    "side_story": "a side story of",
    "spin_off": "a spin-off of",
    "alternative_version": "another version of",
    "alternative_setting": "set in the world of",
    "parent_story": "the main story of",
    "full_story": "the full story of",
}


MEDIA_LABELS = {
    "tv": "TV",
    "movie": "Movie",
    "ona": "ONA",
    "ova": "OVA",
    "special": "Special",
    "tv_special": "TV special",
    "music": "Music",
    "pv": "PV",
}


def _anime_facts(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Every anime in staging with its genres and studios."""
    facts: dict[int, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM stg_mal_anime"):
        facts[row["mal_anime_id"]] = {**dict(row), "genres": [], "studios": []}
    for anime_id, name in conn.execute("SELECT mal_anime_id, genre_name FROM stg_mal_anime_genres"):
        if anime_id in facts:
            facts[anime_id]["genres"].append(name)
    for anime_id, name in conn.execute(
        "SELECT mal_anime_id, studio_name FROM stg_mal_anime_studios"
    ):
        if anime_id in facts:
            facts[anime_id]["studios"].append(name)
    return facts


def _scored(conn: sqlite3.Connection) -> list[tuple[int, float, float]]:
    """(anime id, my score, community mean) for shows I scored that have a community mean."""
    return [
        (r[0], r[1], r[2])
        for r in conn.execute(
            "SELECT l.mal_anime_id, l.score, a.community_mean FROM stg_mal_list_entries l "
            "JOIN stg_mal_anime a ON a.mal_anime_id = l.mal_anime_id "
            "WHERE l.removed_at IS NULL AND l.score IS NOT NULL AND a.community_mean IS NOT NULL"
        )
    ]


def evaluate(
    conn: sqlite3.Connection,
    facts: dict[int, dict] | None = None,
    scored: list[tuple[int, float, float]] | None = None,
) -> dict[str, Any]:
    """Hold out every fifth scored show, predict it, and compare errors.

    Mean absolute error (in MAL points) for: the community mean alone, the
    community mean plus my overall bias, and the full model. Lower is better.
    Each guess is a whole score, like the ones I give (and the card shows): with
    whole-number scores that misses by less than the decimal does.
    """
    facts = _anime_facts(conn) if facts is None else facts
    scored = _scored(conn) if scored is None else scored
    test = [x for x in scored if x[0] % 5 == 0]
    train = [x for x in scored if x[0] % 5 != 0]
    if len(test) < 5 or len(train) < 10:
        return {"held_out": len(test)}
    model = build_model(train, facts)
    errors: dict[str, list[float]] = {"community": [], "overall": [], "model": []}
    for anime_id, score, community in test:
        f = facts[anime_id]
        guesses = {
            "community": community,
            "overall": min(max(community + model.overall, 1), 10),
            "model": model.predict(community, f["genres"], f["studios"]),
        }
        for name, guess in guesses.items():
            errors[name].append(abs(score - whole_score(guess)))
    return {"held_out": len(test), **{k: round(mean(v), 3) for k, v in errors.items()}}


def anime_recommendations(
    conn: sqlite3.Connection, now: datetime | None = None
) -> tuple[list[Rec], dict[str, Any]]:
    cfg = settings.get_all(conn)
    facts = _anime_facts(conn)
    scored = _scored(conn)
    model = build_model(scored, facts)
    model = experiments.apply(conn, "anime", model, scored, facts, now)
    mine = enrich.my_mean_score(conn)
    support = enrich.candidate_support(conn)
    dismissed = _dismissed(conn, "anime")
    allowed_types = {t.strip() for t in cfg["rec_media_types"].split(",") if t.strip()}
    plan_to_watch = {
        r[0]
        for r in conn.execute(
            "SELECT mal_anime_id FROM stg_mal_list_entries WHERE removed_at IS NULL "
            "AND status = 'plan_to_watch'"
        )
    }

    # Who recommends each candidate, for the reasons line. English titles when MAL has one.
    sources: dict[int, list[tuple[float, str, int]]] = {}
    for rec_id, title, score, votes in conn.execute(
        "SELECT r.recommended_id, COALESCE(NULLIF(a.title_en, ''), a.title), l.score, "
        "r.num_recommendations "
        "FROM stg_mal_anime_recommendations r JOIN stg_mal_list_entries l "
        "ON l.mal_anime_id = r.mal_anime_id "
        "JOIN stg_mal_anime a ON a.mal_anime_id = r.mal_anime_id "
        "WHERE l.removed_at IS NULL AND l.score > ?",
        (mine or 10,),
    ):
        sources.setdefault(rec_id, []).append(
            ((score - (mine or 0)) * math.log1p(votes), title, score, votes)
        )
    related: dict[int, tuple[str, str, int]] = {}
    for rel_id, kind, title, score in conn.execute(
        "SELECT r.related_id, r.relation_type, COALESCE(NULLIF(a.title_en, ''), a.title), "
        "l.score FROM stg_mal_related_anime r "
        "JOIN stg_mal_list_entries l ON l.mal_anime_id = r.mal_anime_id "
        "JOIN stg_mal_anime a ON a.mal_anime_id = r.mal_anime_id "
        "WHERE l.removed_at IS NULL AND l.score > ? ORDER BY l.score DESC",
        (mine or 10,),
    ):
        if kind in enrich.RELATED_KINDS and rel_id not in related:
            related[rel_id] = (kind, title, score)

    if not cfg["include_nsfw"]:
        # Adult shows still steer the picks, but are never named on a card.
        adult = {k for k, f in facts.items() if f.get("nsfw_rating") == "black"}
        hidden_titles = {facts[k]["title_en"] or facts[k]["title"] for k in adult}
        sources = {
            rec_id: [s for s in found if s[1] not in hidden_titles]
            for rec_id, found in sources.items()
        }
        related = {k: v for k, v in related.items() if v[1] not in hidden_titles}
    candidates = set(support)
    if cfg["rec_include_plan_to_watch"]:
        candidates |= plan_to_watch
    residuals = sorted(holdout_residuals(scored, facts))
    if cfg["rec_dismissal_signal"] or cfg["rec_recent_ratings"]:
        residuals = sorted(experiments.residuals(conn, "anime", scored, facts, now))
    recs: list[Rec] = []
    predictions: dict[str, float] = {}
    for anime_id in candidates:
        f = facts.get(anime_id)
        if not f or f["community_mean"] is None or str(anime_id) in dismissed:
            continue  # no details yet (fetched on a later refresh), or dismissed
        if f["media_type"] not in allowed_types:
            continue
        if (f["num_scoring_users"] or 0) < cfg["rec_min_raters"]:
            continue
        if not cfg["include_nsfw"] and f.get("nsfw_rating") == "black":
            continue
        predicted = model.predict(f["community_mean"], f["genres"], f["studios"])
        rec = Rec(
            kind="anime",
            item_key=str(anime_id),
            title=f["title_en"] or f["title"],
            score=round(predicted, 2),
            support=round(support.get(anime_id, 0.0), 3),
            image_url=f["main_picture_url"],
            url=f"https://myanimelist.net/anime/{anime_id}",
        )
        parts = [MEDIA_LABELS.get(f["media_type"], f["media_type"] or "")]
        if f["start_season_year"]:
            parts.append(str(f["start_season_year"]))
        if f["num_episodes"]:
            parts.append(f"{f['num_episodes']} ep" if f["num_episodes"] == 1 else
                         f"{f['num_episodes']} eps")  # fmt: skip
        rec.subtitle = " · ".join(p for p in parts if p)
        rec.facts["mal_mean"] = f["community_mean"]
        rec.facts["about"] = about_text(f.get("synopsis"))
        rec.facts["popularity"] = [
            text
            for text in (
                f"{short_count(f['num_list_users'])} members on MAL"
                if f.get("num_list_users")
                else "",
                f"#{f['popularity_rank']:,} most popular" if f.get("popularity_rank") else "",
                f"#{f['score_rank']:,} by score" if f.get("score_rank") else "",
            )
            if text
        ]
        predictions[rec.item_key] = predicted
        if anime_id in plan_to_watch:
            rec.badge = "On your Plan to Watch"

        details = []
        if anime_id in related:
            kind, title, score = related[anime_id]
            phrase = RELATION_PHRASES.get(kind, "related to")
            rec.reasons.append(f"It's {phrase} {title}, which you gave {_a(score)}.")
        seeds = []
        for _, title, score, votes in sorted(sources.get(anime_id, []), reverse=True)[
            :MAX_REASON_SEEDS
        ]:
            seeds.append((title, score))
            who = "1 MAL user who liked" if votes == 1 else f"{votes} MAL users who liked"
            verb = "recommends" if votes == 1 else "recommend"
            details.append(f"{who} {title} {verb} this.")
        liked, harsh = _genre_leans(model, f["genres"])
        for genre in filter(None, (liked, harsh)):
            details.append(f"You rate {genre} shows {model.genre[genre]:+.2f} vs your usual.")
        rec.reasons.extend(_because(seeds, liked, harsh))
        rec.facts["details"] = details
        recs.append(rec)

    # Best predicted first; how strongly my favorites point at it breaks near-ties.
    recs.sort(key=lambda r: (-(r.score + 0.15 * math.log1p(r.support)), r.title))
    recs = recs[: cfg["rec_count"]]
    for rec in recs:  # only for the shows kept: it's the slow part with a long list
        chance = chance_at_least(predictions[rec.item_key], residuals, 8)
        if chance is not None:
            rec.facts["chance_8_plus"] = round(chance, 3)
    metrics = {"model_overall_bias": round(model.overall, 3), **evaluate(conn, facts, scored)}
    metrics.update(experiments.metrics(conn, "anime", scored, facts, now))
    return recs, metrics
