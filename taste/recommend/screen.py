"""Film and TV candidates from TMDB."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from typing import Any

from taste import settings, themes
from taste.recommend import experiments
from taste.recommend.model import (
    FILM_PRIOR_VOTES,
    MAX_REASON_SEEDS,
    Rec,
    _because,
    _dismissed,
    _genre_leans,
    about_text,
    build_model,
    chance_at_least,
    holdout_check,
    holdout_residuals,
    short_count,
    shrunk,
)
from taste.sources import tmdb

FILM_MIN_VOTES = 200  # TMDB averages from fewer votes than this are too unsettled


TYPICAL_FALLBACK = 6.5  # before any film with FILM_MIN_VOTES is known


def _typical_tmdb_average(conn: sqlite3.Connection, kind: str = "movie") -> float:
    """The mean of settled TMDB averages, separately for films and TV."""
    table = "stg_tmdb_movies" if kind == "movie" else "stg_tmdb_tv"
    row = conn.execute(
        f"SELECT AVG(vote_average) FROM {table} WHERE vote_average IS NOT NULL AND vote_count >= ?",
        (FILM_MIN_VOTES,),
    ).fetchone()
    return row[0] if row and row[0] is not None else TYPICAL_FALLBACK


def is_anime(show: dict[str, Any]) -> bool:
    """A TMDB show that's anime: animation made in Japan (by language or origin).
    Animation from elsewhere stays a TV pick."""
    if "Animation" not in show.get("genres", []):
        return False
    countries = json.loads(show.get("origin_country_json") or "[]")
    return show.get("original_language") == "ja" or "JP" in countries


def _screen_facts(conn: sqlite3.Connection, kind: str = "movie") -> dict[int, dict[str, Any]]:
    """Staged titles with genres and directors or creators in the model's slots."""
    facts = {}
    table = "stg_tmdb_movies" if kind == "movie" else "stg_tmdb_tv"
    people = "directors_json" if kind == "movie" else "creators_json"
    for row in conn.execute(f"SELECT * FROM {table}"):
        people_names = [d.get("name") for d in json.loads(row[people] or "[]")]
        facts[row["tmdb_id"]] = {
            **dict(row),
            "title": row["title"] if kind == "movie" else row["name"],
            "release_date": row["release_date"] if kind == "movie" else row["first_air_date"],
            "runtime": row["runtime"] if kind == "movie" else row["episode_run_time"],
            "genres": json.loads(row["genres_json"] or "[]"),
            "studios": [name for name in people_names if name],
        }
    return facts


def _screen_scored(
    conn: sqlite3.Connection, typical: float, prior: int = FILM_PRIOR_VOTES, kind: str = "movie"
) -> list[tuple[int, float, float]]:
    """(title id, my rating, its shrunk TMDB average): learn on the same numbers
    it predicts from."""
    table = "stg_tmdb_movies" if kind == "movie" else "stg_tmdb_tv"
    return [
        (r[0], r[1], shrunk(r[2], r[3], typical, prior))
        for r in conn.execute(
            "SELECT a.tmdb_id, a.rating, m.vote_average, m.vote_count "
            "FROM stg_tmdb_account_ratings a "
            f"JOIN {table} m ON m.tmdb_id = a.tmdb_id WHERE a.media_type = ? "
            "AND a.removed_at IS NULL AND m.vote_average IS NOT NULL AND m.vote_count > 0",
            (kind,),
        )
    ]


def _runtime(minutes: int | None) -> str:
    if not minutes:
        return ""
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def film_recommendations(
    conn: sqlite3.Connection, today: date | None = None, prior: int | None = None
) -> tuple[list[Rec], dict[str, Any]]:
    """Film picks using my film ratings, genres, and directors."""
    return _screen_recommendations(conn, today, prior, "movie")


def tv_recommendations(
    conn: sqlite3.Connection, today: date | None = None, prior: int | None = None
) -> tuple[list[Rec], dict[str, Any]]:
    """The film model applied to my TV ratings, with creators instead of directors."""
    return _screen_recommendations(conn, today, prior, "tv")


def _screen_recommendations(
    conn: sqlite3.Connection,
    today: date | None = None,
    prior: int | None = None,
    kind: str = "movie",
) -> tuple[list[Rec], dict[str, Any]]:
    """Apply the existing model separately to films or TV.

    Use each medium's ratings, genres, and directors or creators. Unreleased titles
    stay out, and thin TMDB averages get the same caution as film picks.
    """
    item_kind = "film" if kind == "movie" else "tv"
    noun = "films" if kind == "movie" else "TV shows"
    table = "stg_tmdb_movies" if kind == "movie" else "stg_tmdb_tv"
    rec_table = (
        "stg_tmdb_movie_recommendations" if kind == "movie" else "stg_tmdb_tv_recommendations"
    )
    title_column = "title" if kind == "movie" else "name"
    cfg = settings.get_all(conn)
    prior = cfg["film_prior_votes"] if prior is None else prior  # the profile's setting
    today_text = (today or date.today()).isoformat()
    facts = _screen_facts(conn, kind)
    typical = _typical_tmdb_average(conn, kind)
    scored = _screen_scored(conn, typical, prior, kind)
    raw = {tmdb_id: facts[tmdb_id]["vote_average"] for tmdb_id, _, _ in scored}
    theme_taste = themes.profile(conn) if cfg["rec_theme_transfer"] and kind == "tv" else None
    model = build_model(scored, facts)
    model = experiments.apply(conn, item_kind, model, scored, facts, today)
    mine = tmdb.my_rating_mean(conn, kind)
    support = tmdb.candidate_support(conn, kind)
    dismissed = _dismissed(conn, item_kind)
    watchlist = {
        r[0]
        for r in conn.execute(
            "SELECT tmdb_id FROM stg_tmdb_account_lists WHERE media_type = ? "
            "AND list_name = 'watchlist' AND removed_at IS NULL",
            (kind,),
        )
    }
    rated = {
        r[0]
        for r in conn.execute(
            "SELECT tmdb_id FROM stg_tmdb_account_ratings WHERE media_type = ? "
            "AND removed_at IS NULL",
            (kind,),
        )
    }
    sources: dict[int, list[tuple[float, str, float]]] = {}
    for rec_id, title, rating, position in conn.execute(
        f"SELECT r.recommended_id, m.{title_column}, a.rating, r.position "
        f"FROM {rec_table} r "
        "JOIN stg_tmdb_account_ratings a ON a.tmdb_id = r.tmdb_id AND a.media_type = ? "
        f"JOIN {table} m ON m.tmdb_id = r.tmdb_id "
        "WHERE a.removed_at IS NULL AND a.rating > ?",
        (kind, mine if mine is not None else 11),
    ):
        weight = (rating - (mine or 0)) / (1 + position / 10)
        sources.setdefault(rec_id, []).append((weight, title, rating))
    residuals = sorted(holdout_residuals(scored, facts))  # chance_at_least needs them sorted
    if (
        cfg["rec_dismissal_signal"]
        or cfg["rec_recent_ratings"]
        or (cfg["rec_theme_transfer"] and kind == "tv")
    ):
        residuals = sorted(experiments.residuals(conn, item_kind, scored, facts, today))
    recs: list[Rec] = []
    for tmdb_id in (set(support) | watchlist) - rated:
        f = facts.get(tmdb_id)
        if not f or f["vote_average"] is None or str(tmdb_id) in dismissed:
            continue
        if (f["vote_count"] or 0) < FILM_MIN_VOTES or not f["fetched_at"]:
            continue  # unsettled, or only known from a list so far (no genres yet)
        if (f["release_date"] or "") > today_text:
            continue  # not out yet: its average is anticipation, not a verdict
        if kind == "tv" and is_anime(f):
            continue  # MAL and the Anime tab cover anime
        crowd = shrunk(f["vote_average"], f["vote_count"], typical, prior)
        predicted = model.predict(crowd, f["genres"], f["studios"])
        if theme_taste is not None:
            shared = themes.candidate_themes(conn, item_kind, str(tmdb_id), f)
            theme_shift = themes.transfer(theme_taste, item_kind, shared, len(scored))
            predicted = min(10, max(1, predicted + theme_shift))
        rec = Rec(
            kind=item_kind,
            item_key=str(tmdb_id),
            title=f["title"],
            score=round(predicted, 2),
            support=round(support.get(tmdb_id, 0.0), 3),
            image_url=tmdb.poster_url(f["poster_path"]),
            url=tmdb.TITLE_URL[kind].format(tmdb_id),
        )
        length = _runtime(f["runtime"])
        if kind == "tv" and length:
            length += " episodes"
        parts = [(f["release_date"] or "")[:4], length]
        if f["studios"]:
            parts.append(f["studios"][0])
        rec.subtitle = " · ".join(p for p in parts if p)
        rec.facts["tmdb_mean"] = f["vote_average"]
        rec.facts["about"] = about_text(f.get("overview"))
        rec.facts["popularity"] = (
            [f"{short_count(f['vote_count'])} votes on TMDB"] if f.get("vote_count") else []
        )
        chance = chance_at_least(predicted, residuals, 8)
        if chance is not None:
            rec.facts["chance_8_plus"] = round(chance, 3)
        if tmdb_id in watchlist:
            rec.badge = "On your watchlist"
        best = sorted(sources.get(tmdb_id, []), reverse=True)[:MAX_REASON_SEEDS]
        seeds = [(title, rating) for _, title, rating in best]
        liked, harsh = _genre_leans(model, f["genres"])
        rec.reasons.extend(
            _because(seeds, liked, harsh, noun="films" if kind == "movie" else "shows")
        )
        details = [f"TMDB recommends it alongside {t}, which you rated {r:g}." for t, r in seeds]
        if abs(crowd - f["vote_average"]) >= 0.1:
            details.append(
                f"TMDB's {f['vote_average']:.1f} rests on {short_count(f['vote_count'])} votes, "
                f"so it counts as {crowd:.1f} here."
            )
        for genre in filter(None, (liked, harsh)):
            details.append(f"You rate {genre} {noun} {model.genre[genre]:+.2f} vs your usual.")
        if theme_taste is not None:
            details.append(
                f"Experimental theme adjustment: {theme_shift / 1:+.2f} points, "
                f"shrunk over {len(scored)} own ratings."
            )
        rec.facts["details"] = details
        recs.append(rec)
    recs.sort(key=lambda r: (-(r.score + 0.15 * math.log1p(r.support)), r.title))
    metrics = {
        "model_overall_bias": round(model.overall, 3),
        "rated": len(scored),
        "typical_tmdb_average": round(typical, 2),
        **holdout_check(scored, residuals, raw),
    }
    metrics.update(experiments.metrics(conn, item_kind, scored, facts, today))
    if kind == "tv":
        metrics["theme_transfer"] = experiments.transfer_metrics(
            conn, item_kind, scored, facts, today
        )
    return recs[: cfg["rec_count"]], metrics
