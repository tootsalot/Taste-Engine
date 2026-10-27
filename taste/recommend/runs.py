"""Stored recommendation runs and dismissals."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from taste import settings, themes
from taste.db import utc_now
from taste.recommend.anime import anime_recommendations
from taste.recommend.books import book_recommendations
from taste.recommend.everything import dismissed_keys, everything_recommendations
from taste.recommend.model import Rec, _dismissed
from taste.recommend.music import music_recommendations
from taste.recommend.screen import film_recommendations, tv_recommendations


def save_run(
    conn: sqlite3.Connection, kind: str, recs: list[Rec], metrics: dict[str, Any] | None = None
) -> int:
    params = {
        k: settings.get(conn, k) for k in REC_SETTINGS if k != "mix_balance" or kind == "everything"
    }
    if kind in ("anime", "everything"):
        params["anime_visibility"] = themes.visibility(conn)
    run_id = conn.execute(
        "INSERT INTO rec_runs (kind, created_at, params_json, metrics_json) VALUES (?, ?, ?, ?)",
        (kind, utc_now(), json.dumps(params), json.dumps(metrics or {})),
    ).lastrowid
    for rank, r in enumerate(recs, 1):
        conn.execute(
            "INSERT INTO rec_items (rec_run_id, rank, item_key, title, subtitle, score, support, "
            "badge, image_url, url, reasons_json, facts_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                rank,
                r.item_key,
                r.title,
                r.subtitle,
                r.score,
                r.support,
                r.badge,
                r.image_url,
                r.url,
                json.dumps(r.reasons, ensure_ascii=False),
                json.dumps(r.facts, ensure_ascii=False),
            ),
        )
    return run_id


def _newest_run(conn: sqlite3.Connection, kind: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT rec_run_id, created_at, metrics_json, params_json FROM rec_runs WHERE kind = ? "
        "ORDER BY rec_run_id DESC LIMIT 1",
        (kind,),
    ).fetchone()


def _hidden_run(conn: sqlite3.Connection, kind: str, run: sqlite3.Row) -> bool:
    # Old reasons may name a seed that has since become hidden. Runs saved before 3.0
    # carry no stamp, so they count as stale too.
    return (kind == "everything" and json.loads(run["metrics_json"]).get("cross_media") != 1) or (
        kind in ("anime", "everything")
        and json.loads(run["params_json"]).get("anime_visibility") != themes.visibility(conn)
    )


def withheld(conn: sqlite3.Connection, kind: str) -> bool:
    """Whether `latest` holds back the newest saved run of a list. A recompute from the
    cache (no network) brings the list back."""
    run = _newest_run(conn, kind)
    return run is not None and _hidden_run(conn, kind, run)


def latest(conn: sqlite3.Connection, kind: str) -> tuple[list[Rec], dict[str, Any], str | None]:
    """The most recent saved run of a kind, minus anything dismissed since."""
    run = _newest_run(conn, kind)
    if run is None or _hidden_run(conn, kind, run):
        return [], {}, None
    item_kind = kind if kind in ("anime", "film", "tv", "book", "everything") else "artist"
    dismissed = _dismissed(conn, item_kind)
    recs = [
        Rec(
            kind=json.loads(r["facts_json"] or "{}").get("medium", "artist")
            if kind == "everything"
            else item_kind,
            item_key=r["item_key"],
            title=r["title"],
            subtitle=r["subtitle"],
            score=r["score"],
            support=r["support"] or 0.0,
            badge=r["badge"],
            image_url=r["image_url"],
            url=r["url"],
            reasons=json.loads(r["reasons_json"]),
            facts=json.loads(r["facts_json"] or "{}"),
        )
        for r in conn.execute(
            "SELECT * FROM rec_items WHERE rec_run_id = ? ORDER BY rank", (run["rec_run_id"],)
        )
        if r["item_key"] not in dismissed
    ]
    if kind == "everything":
        hidden = dismissed_keys(conn)
        recs = [r for r in recs if (r.kind, r.facts.get("source_item_key")) not in hidden]
    metrics = json.loads(run["metrics_json"])
    # 3.1 used model for both a numeric anime error and the selected-model block.
    if isinstance(metrics.get("model"), dict):
        metrics.setdefault("selected_model", metrics.pop("model"))
    return recs, metrics, run["created_at"]


def dismiss(conn: sqlite3.Connection, kind: str, item_key: str) -> None:
    if kind == "everything":
        # Not interested in a mixed pick means the item itself: it's stored under its own
        # list ("anime", "artist", ...), so that list hides it too.
        kind, item_key = item_key.split(":", 1)
    conn.execute(
        "INSERT INTO rec_dismissed (kind, item_key, dismissed_at) VALUES (?, ?, ?) "
        "ON CONFLICT (kind, item_key) DO NOTHING",
        (kind, item_key, utc_now()),
    )


REC_SETTINGS = [
    "mix_balance",
    "rec_theme_transfer",
    "rec_dismissal_signal",
    "rec_recent_ratings",
    "rec_count",
    "rec_min_raters",
    "rec_media_types",
    "rec_include_plan_to_watch",
    "rec_seed_artists",
    "include_nsfw",
    "film_prior_votes",
    "rec_model_anime",
    "rec_model_film",
    "rec_model_tv",
    "rec_model_book",
]


def compute_all(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    kinds: tuple[str, ...] = ("anime", "music"),
    *,
    pool_cache: dict | None = None,
) -> dict[str, int]:
    """Recompute and save the requested anime, music, film, TV, or book lists. No network.

    Returns counts per list. Lists that weren't recomputed are counted from
    their latest saved run.
    """
    pools = {} if pool_cache is None else pool_cache
    cross = "everything" in kinds
    count = settings.get(conn, "rec_count")
    for kind, build, clock in (
        ("anime", anime_recommendations, now),
        ("film", film_recommendations, now.date() if now else None),
        ("tv", tv_recommendations, now.date() if now else None),
        ("book", book_recommendations, now),
    ):
        if kind in kinds or cross:
            if kind not in pools:
                pools[kind] = build(conn, clock, full_pool=cross or pool_cache is not None)
            recs, metrics = pools[kind]
            if kind in kinds:
                save_run(conn, kind, recs[:count], metrics)
    if "music" in kinds:
        discover, rediscover = music_recommendations(conn, now)
        save_run(conn, "music_discover", discover)
        save_run(conn, "music_rediscover", rediscover)
    if cross:
        picks, metrics = everything_recommendations(conn, pools)
        save_run(conn, "everything", picks, metrics)
    return {kind: len(latest(conn, kind)[0]) for kind in LIST_KINDS}


LIST_KINDS = ("anime", "music_discover", "music_rediscover", "film", "tv", "book", "everything")
