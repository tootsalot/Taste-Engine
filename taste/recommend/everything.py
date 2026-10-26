"""Experimental suggestions from the existing pools, with cross-media theme reasons."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from taste import settings, themes
from taste.recommend.anime import _anime_facts
from taste.recommend.books import _book_facts
from taste.recommend.model import _dismissed
from taste.recommend.screen import _screen_facts

POOLS = ("anime", "film", "tv", "book", "music_discover", "music_rediscover")


def everything_recommendations(
    conn: sqlite3.Connection, saved: dict[str, tuple[list, dict]]
) -> tuple[list, dict]:
    """Rerank eligible picks by within-list rank and a small theme adjustment.

    `saved` holds each pool's latest (picks, metrics), the lists a refresh has just saved,
    so nothing is predicted twice.
    """
    pools = [(list_kind, saved.get(list_kind, ([], {}))[0]) for list_kind in POOLS]
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

    def holdout(list_kind: str) -> dict:
        list_metrics = saved.get(list_kind, ([], {}))[1]
        model = list_metrics.get("selected_model")
        if model is not None:
            # The mix still reports plain Usual's baseline, already saved by that list.
            return {
                "count": model["count"] if model["usual_mae"] is not None else 0,
                "mae": model["usual_mae"],
            }
        return list_metrics.get("experiments", {}).get("recency", {}).get("off", {})

    metrics = {
        "experimental": True,
        "visibility": themes.visibility(conn),
        "theme_evidence": taste,
        "holdouts": {
            "anime": holdout("anime"),
            "film": holdout("film"),
            "tv": holdout("tv"),
            "book": holdout("book"),
            "music": {"count": 0, "mae": None, "note": "Plays, no personal ratings."},
        },
    }
    return ranked[: settings.get(conn, "rec_count")], metrics
