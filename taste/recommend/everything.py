"""Experimental suggestions from the existing pools, with cross-media theme reasons."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime

from taste import settings, themes
from taste.recommend.anime import _anime_facts, anime_recommendations
from taste.recommend.books import _book_facts, book_recommendations
from taste.recommend.model import _dismissed
from taste.recommend.music import music_recommendations
from taste.recommend.screen import _screen_facts, film_recommendations, tv_recommendations


def everything_recommendations(
    conn: sqlite3.Connection, now: datetime | None = None
) -> tuple[list, dict]:
    """Rerank eligible picks by within-list rank and a small theme adjustment."""
    anime, anime_metrics = anime_recommendations(conn, now)
    films, film_metrics = film_recommendations(conn, now.date() if now else None)
    tv, tv_metrics = tv_recommendations(conn, now.date() if now else None)
    books, book_metrics = book_recommendations(conn, now)
    discover, rediscover = music_recommendations(conn, now)
    pools = (
        ("anime", anime),
        ("film", films),
        ("tv", tv),
        ("book", books),
        ("music_discover", discover),
        ("music_rediscover", rediscover),
    )
    facts = {
        "anime": _anime_facts(conn),
        "film": _screen_facts(conn),
        "tv": _screen_facts(conn, "tv"),
        "book": _book_facts(conn),
    }
    taste = themes.profile(conn)
    dismissed = _dismissed(conn, "everything")
    ranked, seen = [], set()
    for list_kind, recs in pools:
        for rank, rec in enumerate(recs):
            key = f"{rec.kind}:{rec.item_key}"
            if key in seen or key in dismissed:
                continue
            seen.add(key)
            f = facts.get(rec.kind, {}).get(int(rec.item_key), {}) if rec.kind != "artist" else {}
            tags = themes.candidate_themes(conn, rec.kind, rec.item_key, f)
            medium = "music" if rec.kind == "artist" else rec.kind
            # Source score scales differ. Rank within each list provides a common base;
            # native scores stay unchanged for the cards and their explanations.
            mixed = 1 - rank / max(len(recs), 1) + 0.15 * themes.lean(taste, tags, medium)
            pick = replace(
                rec,
                item_key=key,
                reasons=[themes.reason(taste, tags, medium), *rec.reasons],
                facts={
                    **rec.facts,
                    "medium": rec.kind,
                    "source_item_key": rec.item_key,
                    "list_kind": list_kind,
                    "themes": tags,
                    "mix_score": mixed,
                },
            )
            ranked.append(pick)
    ranked.sort(key=lambda rec: (-rec.facts["mix_score"], rec.kind, rec.title, rec.item_key))
    metrics = {
        "experimental": True,
        "visibility": themes.visibility(conn),
        "theme_evidence": taste,
        "holdouts": {
            "anime": anime_metrics.get("experiments", {}).get("recency", {}).get("off", {}),
            "film": film_metrics.get("experiments", {}).get("recency", {}).get("off", {}),
            "tv": tv_metrics.get("experiments", {}).get("recency", {}).get("off", {}),
            "book": book_metrics.get("experiments", {}).get("recency", {}).get("off", {}),
            "music": {"count": 0, "mae": None, "note": "Plays, no personal ratings."},
        },
    }
    return ranked[: settings.get(conn, "rec_count")], metrics
