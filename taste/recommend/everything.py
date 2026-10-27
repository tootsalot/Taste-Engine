"""Experimental mix ranked relative to personal baselines, without predicting again."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import replace
from statistics import NormalDist, mean, pstdev

from taste import activity, settings, themes
from taste.recommend import links
from taste.recommend.anime import _anime_facts
from taste.recommend.books import _book_facts
from taste.recommend.model import Rec, _dismissed
from taste.recommend.screen import _screen_facts

POOLS = ("anime", "film", "tv", "book", "music_discover", "music_rediscover")
SPREAD_FLOOR = 1.0
WITHIN_REACH = 1.0
MEDIUM_ORDER = {kind: index for index, kind in enumerate(("anime", "film", "tv", "book", "artist"))}


def _order(rec: Rec) -> tuple:
    return -rec.facts["mix_score"], MEDIUM_ORDER[rec.kind], rec.item_key


def balance(picks: list[Rec], mode: str, prefix: tuple[Rec, ...] = ()) -> list[Rec]:
    """Count a separate surprise card in the first ten; Best first opts out of balancing."""
    remaining = sorted(picks, key=_order)
    result = list(prefix)
    cycle = list(dict.fromkeys(rec.kind for rec in remaining))
    cursor = (
        (cycle.index(prefix[-1].kind) + 1) % len(cycle)
        if prefix and prefix[-1].kind in cycle
        else 0
    )
    while remaining:
        if mode == "best":
            result.extend(remaining)
            break
        if mode == "rotation":
            while not any(rec.kind == cycle[cursor] for rec in remaining):
                cursor = (cursor + 1) % len(cycle)
            chosen = next(rec for rec in remaining if rec.kind == cycle[cursor])
            cursor = (cursor + 1) % len(cycle)
        else:
            allowed = remaining
            if len(result) >= 2 and result[-1].kind == result[-2].kind:
                others = [rec for rec in remaining if rec.kind != result[-1].kind]
                if others:
                    allowed = others
            seen = {rec.kind for rec in result}
            reachable = {
                rec.kind
                for rec in remaining
                if rec.kind not in seen
                and rec.facts["mix_score"] >= remaining[0].facts["mix_score"] - WITHIN_REACH
            }
            if len(result) < 10 and 10 - len(result) <= len(reachable):
                chosen = next(rec for rec in allowed if rec.kind in reachable)
            else:
                chosen = allowed[0]
        result.append(chosen)
        remaining.remove(chosen)
    return result


def _baselines(data: dict) -> dict:
    scores = defaultdict(list)
    for _, _, kind, score, _ in themes.rated_rows(data):
        scores[kind].append(score * (2 if kind == "book" else 1))
    return {
        kind: {
            "count": len(scores[kind]),
            "mean": mean(scores[kind]) if scores[kind] else None,
            "spread": max(SPREAD_FLOOR, pstdev(scores[kind])) if scores[kind] else None,
        }
        for kind in (*themes.IDS, "music")
    }


def _comparison(kind: str, standardized: float, baseline: dict, pool: str) -> str:
    if kind == "artist":
        name = "Discover" if pool == "music_discover" else "Rediscover"
        return f"Placed by its rank in {name} music"
    if baseline["mean"] is None:
        return f"No personal ratings for {themes.LABELS[kind]} yet; placed by its list rank"
    direction = (
        "Well above"
        if standardized >= 1
        else "Above"
        if standardized >= 0.25
        else "Near"
        if standardized >= -0.25
        else "Below"
    )
    return f"{direction} your usual for {themes.LABELS[kind]}"


def _surprise(picks: list[Rec], baselines: dict, data: dict) -> Rec | None:
    if len(picks) < 2:
        return None
    counts = Counter()
    for item, source, kind, _, _ in themes.rated_rows(data):
        for tag in themes.mapped(source, data["item_tags"].get((item, source), []), mixed=True):
            counts[kind, tag] += 1
    choices = []
    for rec in picks[1:]:
        if rec.kind == "artist":
            continue  # Music has no personal rating baseline to exceed.
        baseline = baselines[rec.kind]
        if baseline["mean"] is None or rec.facts["relative_score"] <= 0:
            continue
        options = [(baseline["count"], "")]
        options += [(counts[rec.kind, tag], tag) for tag in rec.facts["themes"]]
        count, tag = min(options)
        choices.append((count, *_order(rec), tag, rec))
    if not choices:
        return None
    count, _, _, _, tag, rec = min(choices, key=lambda row: row[:-1])
    medium = themes.LABELS[rec.kind]
    noun = "rating" if count == 1 else "ratings"
    scope = f"{tag} in {medium}" if tag else medium
    wording = "well above" if rec.facts["relative_score"] >= 1 else "above"
    reason = (
        f"You have fewer ratings for {scope} ({count} {noun}) among the eligible picks; "
        f"this one is predicted {wording} your usual."
    )
    return replace(rec, facts={**rec.facts, "outside_usual": True, "surprise_reason": reason})


def everything_recommendations(
    conn: sqlite3.Connection, saved: dict[str, tuple[list, dict]]
) -> tuple[list, dict]:
    """Rerank saved source picks with a personal baseline and a small theme shift."""
    facts = {
        "anime": _anime_facts(conn),
        "film": _screen_facts(conn),
        "tv": _screen_facts(conn, "tv"),
        "book": _book_facts(conn),
    }
    data = themes.load(conn)
    baselines = _baselines(data)
    taste = themes.profile(conn, data=data, mixed=True)
    identities = links.source_items(conn)
    named = links.load(conn, identities)
    blocked = links.dismissed_keys(conn, identities)
    hidden = activity.hidden_items(conn)
    dismissed = _dismissed(conn, "everything")
    ranked, seen = [], set()
    for list_kind in POOLS:
        recs = saved.get(list_kind, ([], {}))[0]
        for rank, rec in enumerate(recs):
            key = f"{rec.kind}:{rec.item_key}"
            item = identities.get((rec.kind, rec.item_key))
            identity = ("core", item) if item is not None else ("source", key)
            if (
                identity in seen
                or key in dismissed
                or item in hidden
                or (rec.kind, rec.item_key) in blocked
            ):
                continue
            if rec.score is None or not math.isfinite(rec.score):
                continue
            seen.add(identity)
            f = facts.get(rec.kind, {}).get(int(rec.item_key), {}) if rec.kind != "artist" else {}
            tags = themes.candidate_themes(conn, rec.kind, rec.item_key, f, mixed=True)
            medium = "music" if rec.kind == "artist" else rec.kind
            baseline = baselines[medium]
            native = rec.score * (2 if rec.kind == "book" else 1)
            percentile = (len(recs) - rank - 0.5) / len(recs)
            relative = (
                (native - baseline["mean"]) / baseline["spread"]
                if baseline["mean"] is not None
                else NormalDist().inv_cdf(percentile)
            )
            adjustment = 0.15 * themes.lean(taste, tags, medium)
            if baseline["mean"] is not None:
                scale = 2 if rec.kind == "book" else 1
                unit = "stars" if rec.kind == "book" else "points"
                basis = (
                    f"For {themes.LABELS[medium]}, predicted {rec.score:g} {unit} versus your "
                    f"usual {baseline['mean'] / scale:.2f}, from {baseline['count']} ratings."
                )
            else:
                name = (
                    "Discover"
                    if list_kind == "music_discover"
                    else "Rediscover"
                    if rec.kind == "artist"
                    else themes.LABELS[medium]
                )
                basis = (
                    f"Ranked {rank + 1} of {len(recs)} in {name}; placed by its rank percentile."
                )
            reasons = [basis]
            if (rec.kind, rec.item_key) in named:
                reasons.append(named[rec.kind, rec.item_key])
            shared = themes.reason(taste, tags, medium)
            if shared != "An experimental mix of your existing suggestion lists.":
                reasons.append(shared)
            ranked.append(
                replace(
                    rec,
                    item_key=key,
                    reasons=[*reasons, *rec.reasons],
                    facts={
                        **rec.facts,
                        "medium": rec.kind,
                        "source_item_key": rec.item_key,
                        "list_kind": list_kind,
                        "pool_rank": rank + 1,
                        "pool_count": len(recs),
                        "themes": tags,
                        "relative_score": relative,
                        "theme_adjustment": adjustment,
                        "mix_score": relative + adjustment,
                        "usual_comparison": _comparison(rec.kind, relative, baseline, list_kind),
                    },
                )
            )
    ranked.sort(key=_order)
    outside = _surprise(ranked, baselines, data)
    regular = [rec for rec in ranked if outside is None or rec.item_key != outside.item_key]
    mode = settings.get(conn, "mix_balance")
    ordered = balance(regular, mode, (outside,) if outside else ())

    def holdout(list_kind: str) -> dict:
        metrics = saved.get(list_kind, ([], {}))[1]
        model = metrics.get("selected_model")
        if model is not None:
            return {
                "count": model["count"] if model["usual_mae"] is not None else 0,
                "mae": model["usual_mae"],
            }
        return metrics.get("experiments", {}).get("recency", {}).get("off", {})

    metrics = {
        "experimental": True,
        "visibility": themes.visibility(conn),
        "theme_evidence": taste,
        "means_spreads": baselines,
        "spread_floor": SPREAD_FLOOR,
        "balance": mode,
        "within_reach": WITHIN_REACH,
        "map_coverage": themes.coverage(conn, data),
        "holdouts": {kind: holdout(kind) for kind in themes.IDS},
    }
    metrics["holdouts"]["music"] = {"count": 0, "mae": None, "note": "Plays, no personal ratings."}
    return ordered[: settings.get(conn, "rec_count")], metrics
