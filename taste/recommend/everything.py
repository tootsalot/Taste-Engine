"""Cross-media similarity from tags, keeping each medium's own prediction."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from statistics import mean, pstdev

from taste import activity, settings, themes
from taste.recommend.anime import _anime_facts
from taste.recommend.books import _book_facts
from taste.recommend.model import Rec, _dismissed
from taste.recommend.screen import _screen_facts

POOLS = ("anime", "film", "tv", "book")
MIN_SIMILARITY = 0.12
MAX_SEEDS = 3
WITHIN_REACH = 1.0
MEDIUM_ORDER = {kind: index for index, kind in enumerate(POOLS)}
# Format and adaptation labels describe delivery, not a shared story.
STOP_TERMS = frozenset(
    {
        "animation",
        "anime",
        "animated film",
        "animated movie",
        "animated series",
        "based on manga",
        "based on a manga",
        "based on anime",
        "based on a novel",
        "based on novel",
        "based on novel or book",
        "based on a book",
        "based on book",
        "based on comic",
        "based on a comic",
        "based on comic book",
        "manga",
        "comics",
        "graphic novels",
        "sequential art",
        "tv movie",
        "television",
        "film",
        "movie",
    }
)
# Specific raw terms such as time travel and found family still count even when
# the broad map also knows them. Generic genres and mapped umbrellas do not.
BROAD_TERMS = frozenset(
    {
        "action",
        "adventure",
        "action & adventure",
        "comedy",
        "humor",
        "drama",
        "character drama",
        "fantasy",
        "science fiction",
        "sci-fi",
        "sci fi",
        "sci-fi & fantasy",
        "science fiction & fantasy",
        "speculative",
        "speculative fiction",
        "mystery",
        "thriller",
        "suspense",
        "romance",
        "horror",
        "dark",
        "family",
        "crime",
        "history",
        "historical",
        "documentary",
        "real life",
        "everyday life",
        "competition",
        "conflict",
        "growing up",
        "adult audience",
        "teen audience",
        "child audience",
        "fiction",
        "nonfiction",
        "non-fiction",
        "literary fiction",
        "emotional",
        "funny",
        "tense",
        "romantic",
        "suspenseful",
        "humorous",
        "witty",
        "silly",
        "scary",
        "bleak",
        "mysterious",
        "adventurous",
    }
)


def normalize(term: str) -> str:
    """Casefold and collapse API whitespace without interpreting markup."""
    return " ".join(term.casefold().split())


@dataclass(frozen=True)
class CrossTitle:
    medium: str
    item_key: str
    title: str
    my_score: float | None
    terms: frozenset[str]
    specific_terms: frozenset[str]
    item_id: int | None = None


def _terms(source: str, tags: list[str]) -> tuple[frozenset[str], frozenset[str]]:
    raw = {normalize(tag) for tag in tags if isinstance(tag, str) and tag.strip()} - STOP_TERMS
    mapped = set(themes.mapped(source, list(raw), mixed=True)) - STOP_TERMS
    return frozenset(raw | mapped), frozenset(raw - BROAD_TERMS)


def source_items(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Only recorded source identities establish that two source IDs are one item."""
    kinds = {identity: kind for kind, identity in themes.IDS.items()}
    return {
        (kinds[source, id_type], key): item
        for item, source, id_type, key in conn.execute(
            "SELECT item_id, source, id_type, external_id FROM core_item_external_ids"
        )
        if (source, id_type) in kinds
    }


def dismissed_keys(conn: sqlite3.Connection, identities: dict) -> set[tuple[str, str]]:
    """A dismissal covers recorded aliases, never a related adaptation."""
    dismissed = {tuple(row) for row in conn.execute("SELECT kind, item_key FROM rec_dismissed")}
    items = {identities[key] for key in dismissed if key in identities}
    return dismissed | {key for key, item in identities.items() if item in items}


def load_cross_data(
    conn: sqlite3.Connection, candidates: dict[str, list[Rec]] | None = None
) -> list[CrossTitle]:
    """Read tags and native ratings without changing the model's genre-only inputs.

    During recommendations the IDF corpus contains rated titles and the full
    eligible scored pools. A read-only lab uses all stored four-media titles.
    """
    identities = source_items(conn)
    names = dict(conn.execute("SELECT item_id, title FROM core_items"))
    tags = defaultdict(list)
    for item, source, name in conn.execute(
        "SELECT it.item_id, it.source, t.name FROM core_item_tags it "
        "JOIN core_tags t ON t.tag_id = it.tag_id "
        "WHERE t.tag_kind IN ('genre', 'theme', 'mood', 'keyword')"
    ):
        tags[item, source].append(name)
    ratings = {
        (item, source, kind): score
        for item, source, kind, score, _ in themes.rated_rows(themes.load(conn))
    }
    staged = {
        "anime": _anime_facts(conn),
        "film": _screen_facts(conn),
        "tv": _screen_facts(conn, "tv"),
        "book": _book_facts(conn),
    }
    staged_names, staged_tags = {}, {}
    for kind, facts in staged.items():
        for key, facts_row in facts.items():
            key = (kind, str(key))
            staged_names[key] = (
                facts_row.get("title_en") or facts_row.get("title") or facts_row.get("name") or ""
            )
            raw = list(facts_row.get("genres", []))
            raw += json.loads(facts_row.get("moods_json") or "[]")
            raw += json.loads(facts_row.get("keywords_json") or "[]")
            staged_tags[key] = raw
    keys = set(identities) | set(staged_names)
    candidate_keys = (
        {(kind, rec.item_key) for kind, recs in candidates.items() for rec in recs}
        if candidates is not None
        else None
    )
    result = []
    for kind, key in sorted(keys):
        item = identities.get((kind, key))
        source = themes.SOURCES[kind]
        score = ratings.get((item, source, kind))
        if candidate_keys is not None and score is None and (kind, key) not in candidate_keys:
            continue
        raw = tags[item, source] + staged_tags.get((kind, key), [])
        terms, specific = _terms(source, raw)
        result.append(
            CrossTitle(
                kind,
                key,
                staged_names.get((kind, key)) or names.get(item, ""),
                score,
                terms,
                specific,
                item,
            )
        )
    return result


class CrossIndex:
    """IDF weighted Jaccard, using a term index instead of all seed/candidate pairs.

    Jaccard rewards shared evidence while penalizing unrelated extra tags. Binary
    term sets avoid a source repeating a keyword to increase its influence.
    """

    def __init__(self, titles: list[CrossTitle], hidden: set[int] | None = None):
        self.titles = titles
        hidden = hidden or set()
        scores = defaultdict(list)
        for title in titles:
            if title.my_score is not None:
                scores[title.medium].append(title.my_score)
        self.baselines = {
            kind: {
                "count": len(scores[kind]),
                "mean": mean(scores[kind]) if scores[kind] else None,
                "spread": max(0.5 if kind == "book" else 1.0, pstdev(scores[kind]))
                if scores[kind]
                else None,
            }
            for kind in POOLS
        }
        counts = Counter(term for title in titles for term in title.terms)
        self.idf = {
            term: math.log((len(titles) + 1) / (count + 1)) + 1 for term, count in counts.items()
        }
        self.seeds = []
        self.index = defaultdict(set)
        self.weights = []
        self.totals = {}
        for title in titles:
            baseline = self.baselines[title.medium]
            if (
                title.my_score is None
                or title.item_id in hidden
                or title.my_score <= baseline["mean"]
            ):
                continue
            seed_id = len(self.seeds)
            self.seeds.append(title)
            self.weights.append((title.my_score - baseline["mean"]) / baseline["spread"])
            for term in title.specific_terms:
                self.index[term].add(seed_id)
        for title in titles:
            self.totals[title.medium, title.item_key] = sum(self.idf[t] for t in title.terms)

    def support(self, title: CrossTitle) -> dict:
        """The strongest three visible liked titles in other media, strongest first."""
        possible = set()
        # Two specific raw terms are required, so broad-only matches can never
        # qualify. Index only specific terms before computing full Jaccard.
        for term in title.specific_terms:
            possible.update(self.index.get(term, ()))
        found = []
        compared = 0
        for seed_id in possible:
            seed = self.seeds[seed_id]
            if seed.medium == title.medium or (
                title.item_id is not None and seed.item_id == title.item_id
            ):
                continue
            compared += 1
            shared = title.terms & seed.terms
            if len(title.specific_terms & seed.specific_terms) < 2:
                continue
            overlap = sum(self.idf[t] for t in shared)
            union = (
                self.totals[title.medium, title.item_key]
                + self.totals[seed.medium, seed.item_key]
                - overlap
            )
            similarity = overlap / union if union else 0
            if similarity < MIN_SIMILARITY:
                continue
            found.append((self.weights[seed_id] * similarity, seed, shared))
        found.sort(key=lambda row: (-row[0], MEDIUM_ORDER[row[1].medium], row[1].item_key))
        best = found[:MAX_SEEDS]
        shared = set().union(*(row[2] for row in best)) if best else set()
        return {
            "cross_support": sum(row[0] for row in best),
            "cross_seeds": [
                {
                    "medium": seed.medium,
                    "item_key": seed.item_key,
                    "title": seed.title,
                    "my_score": seed.my_score,
                }
                for _, seed, _ in best
            ],
            "shared_terms": sorted(shared, key=lambda t: (-self.idf[t], t))[:5],
            "cross_compared": compared,
        }


def _order(rec: Rec) -> tuple:
    return (
        -rec.facts["cross_support"],
        -rec.facts.get("relative_score", 0),
        MEDIUM_ORDER[rec.kind],
        rec.item_key,
    )


def balance(picks: list[Rec], mode: str, prefix: tuple[Rec, ...] = ()) -> list[Rec]:
    """Count the separate surprise card in the first ten; Best first opts out."""
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
                and rec.facts["cross_support"] >= remaining[0].facts["cross_support"] - WITHIN_REACH
            }
            if len(result) < 10 and 10 - len(result) <= len(reachable):
                chosen = next(rec for rec in allowed if rec.kind in reachable)
            else:
                chosen = allowed[0]
        result.append(chosen)
        remaining.remove(chosen)
    return result


def _surprise(picks: list[Rec], baselines: dict) -> Rec | None:
    """Best supported pick in the least-rated available medium, with ranking ties.

    Keep it separate only when another pick remains in the regular list. Its score
    still has to reach the same personal average as every other cross-media pick.
    """
    if len(picks) < 2:
        return None
    rec = min(picks, key=lambda r: (baselines[r.kind]["count"], *_order(r)))
    count = baselines[rec.kind]["count"]
    noun = "rating" if count == 1 else "ratings"
    reason = (
        f"Among these picks, you have rated {themes.LABELS[rec.kind]} least "
        f"({count} {noun}). This pick shares specific terms with liked titles "
        "in other media and is predicted at or above your usual."
    )
    return replace(rec, facts={**rec.facts, "outside_usual": True, "surprise_reason": reason})


def _join(words: list[str]) -> str:
    return " and ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " and " + words[-1]


def everything_recommendations(
    conn: sqlite3.Connection, saved: dict[str, tuple[list, dict]]
) -> tuple[list, dict]:
    """Cross-media picks from whole eligible pools, preserving their native scores."""
    pools = {kind: saved.get(kind, ([], {}))[0] for kind in POOLS}
    data = load_cross_data(conn, pools)
    hidden = activity.hidden_items(conn)
    index = CrossIndex(data, hidden)
    titles = {(title.medium, title.item_key): title for title in data}
    identities = source_items(conn)
    blocked = dismissed_keys(conn, identities)
    dismissed = _dismissed(conn, "everything")
    ranked = []
    seen = set()
    for kind, recs in pools.items():
        baseline = index.baselines[kind]
        for rec in recs:
            key = f"{kind}:{rec.item_key}"
            title = titles.get((kind, rec.item_key))
            if (
                title is None
                or title.item_id in hidden
                or (kind, rec.item_key) in blocked
                or key in dismissed
                or rec.score is None
                or not math.isfinite(rec.score)
                or baseline["mean"] is None
                or rec.score < baseline["mean"]
            ):
                continue
            identity = ("core", title.item_id) if title.item_id is not None else ("source", key)
            if identity in seen or title.my_score is not None:
                continue
            support = index.support(title)
            if not support["cross_seeds"]:
                continue
            seen.add(identity)
            relative = (rec.score - baseline["mean"]) / baseline["spread"]
            named = [
                f"{seed['title']} ({seed['medium']}, you gave it {seed['my_score']:g})"
                for seed in support["cross_seeds"]
            ]
            reason = f"Like {_join(named)}: shares {_join(support['shared_terms'])}."
            unit = "stars" if kind == "book" else "points"
            details = [
                f"Cross support: {support['cross_support']:.3f}.",
                f"Compared {support['cross_compared']} liked titles in other media.",
                f"Predicted {rec.score:g} {unit} versus your usual "
                f"{baseline['mean']:.2f}, from {baseline['count']} ratings.",
            ]
            ranked.append(
                replace(
                    rec,
                    item_key=key,
                    reasons=[reason],
                    facts={
                        **rec.facts,
                        **support,
                        "medium": kind,
                        "source_item_key": rec.item_key,
                        "list_kind": kind,
                        "usual_mean": baseline["mean"],
                        "relative_score": relative,
                        "usual_comparison": f"At or above your usual for {themes.LABELS[kind]}",
                        "details": details,
                    },
                )
            )
    ranked.sort(key=_order)
    outside = _surprise(ranked, index.baselines)
    regular = [rec for rec in ranked if outside is None or rec.item_key != outside.item_key]
    mode = settings.get(conn, "mix_balance")
    ordered = balance(regular, mode, (outside,) if outside else ())
    metrics = {
        "cross_media": 1,
        "experimental": True,
        "visibility": themes.visibility(conn),
        "means_spreads": index.baselines,
        "balance": mode,
        "minimum_similarity": MIN_SIMILARITY,
        "max_seeds": MAX_SEEDS,
    }
    return ordered[: settings.get(conn, "rec_count")], metrics
