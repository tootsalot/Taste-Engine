"""A small, explicit genre map and profile-wide evidence. It never matches item titles."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from statistics import mean

from taste import settings

# These are broad overlaps, not claims about an individual story or a song's lyrics.
COMMON = {
    "action": ("action",),
    "adventure": ("adventure",),
    "adventurous": ("adventure",),
    "comedy": ("humor",),
    "funny": ("humor",),
    "drama": ("character drama",),
    "emotional": ("character drama",),
    "fantasy": ("speculative",),
    "science fiction": ("speculative",),
    "sci-fi": ("speculative",),
    "mystery": ("mystery",),
    "thriller": ("suspense",),
    "suspense": ("suspense",),
    "tense": ("suspense",),
    "psychological": ("psychological",),
    "romance": ("romance",),
    "slice of life": ("everyday life",),
    "sports": ("competition",),
    "supernatural": ("speculative",),
    "mecha": ("technology",),
    "music": ("music",),
    "horror": ("dark",),
    "dark": ("dark",),
    "historical": ("history",),
    "history": ("history",),
    "school": ("growing up",),
    "young adult": ("growing up",),
    "military": ("conflict",),
    "war": ("conflict",),
    "ecchi": ("romance",),
    "hentai": ("romance",),
    "animation": ("animation",),
    "crime": ("crime",),
    "family": ("family",),
    "found family": ("found family",),
    "space": ("space",),
    "hopeful": ("hope",),
    "literary fiction": ("character drama",),
    "nonfiction": ("real life",),
    "documentary": ("real life",),
    "biography": ("real life",),
    "western": ("frontier",),
}
MAP = {
    "mal": dict(COMMON),
    "tmdb": dict(COMMON),
    "hardcover": dict(COMMON),
    "lastfm": {
        "dream pop": ("dreamlike",),
        "example dream pop": ("dreamlike",),
        "night": ("night",),
        "ambient": ("reflective",),
        "electronic": ("electronic",),
        "rock": ("rock",),
        "pop": ("pop",),
        "indie": ("independent",),
        "jazz": ("jazz",),
        "classical": ("classical",),
        "metal": ("intense",),
        "hip-hop": ("hip hop",),
        "folk": ("folk",),
        "experimental": ("experimental",),
        "psychedelic": ("dreamlike",),
        "space rock": ("space", "rock"),
        "dark ambient": ("dark", "reflective"),
        "soundtrack": ("music",),
        "melancholic": ("reflective",),
        "uplifting": ("hope",),
    },
}
LABELS = {"anime": "anime", "film": "films", "tv": "TV shows", "book": "books", "music": "music"}
SOURCES = {"anime": "mal", "film": "tmdb", "tv": "tmdb", "book": "hardcover", "artist": "lastfm"}
MEDIA = {"anime": "anime", "film": "film", "tv_show": "tv", "book": "book"}
IDS = {
    "anime": ("mal", "mal_anime_id"),
    "film": ("tmdb", "tmdb_movie_id"),
    "tv": ("tmdb", "tmdb_tv_id"),
    "book": ("hardcover", "hardcover_book_id"),
}
SHRINK = 5


def mapped(source: str, tags: list[str]) -> list[str]:
    """Map known genres, themes, moods, or tags. Unknown terms are left out."""
    return sorted(
        {theme for tag in tags for theme in MAP.get(source, {}).get(tag.strip().casefold(), ())}
    )


def candidate_themes(conn: sqlite3.Connection, kind: str, key: str, facts: dict) -> list[str]:
    if kind == "artist":
        tags = [
            r[0]
            for r in conn.execute(
                "SELECT tag FROM stg_lastfm_artist_tags WHERE artist_key = ?", (key,)
            )
        ]
    else:
        tags = list(facts.get("genres", []))
        if kind == "book":
            tags += json.loads(facts.get("moods_json") or "[]")
    return mapped(SOURCES[kind], tags)


def excluded_items(conn: sqlite3.Connection, kind: str, keys: set[int]) -> set[int]:
    source, id_type = IDS[kind]
    return {
        item
        for item, key in conn.execute(
            "SELECT item_id, external_id FROM core_item_external_ids "
            "WHERE source = ? AND id_type = ?",
            (source, id_type),
        )
        if key.isdigit() and int(key) in keys
    }


def profile(conn: sqlite3.Connection, exclude: set[int] | None = None) -> dict:
    """Theme evidence by medium, keeping ratings and music behavior distinct."""
    exclude = exclude or set()
    item_tags: dict[tuple[int, str], list[str]] = defaultdict(list)
    for item, source, tag in conn.execute(
        "SELECT it.item_id, it.source, t.name FROM core_item_tags it "
        "JOIN core_tags t ON t.tag_id = it.tag_id "
        "WHERE t.tag_kind IN ('genre', 'theme', 'mood')"
    ):
        item_tags[item, source].append(tag)
    samples, all_scores = defaultdict(list), defaultdict(list)
    for item, source, media, mine, crowd in conn.execute(
        "SELECT r.item_id, r.source, i.media_type, r.raw_score, cr.mean_score FROM core_ratings r "
        "JOIN core_items i ON i.item_id = r.item_id JOIN core_community_ratings cr "
        "ON cr.item_id = r.item_id AND cr.source = r.source"
    ):
        kind = MEDIA.get(media)
        if item in exclude or not kind or source != SOURCES[kind] or crowd is None:
            continue
        diff = (mine - crowd) / (5 if kind == "book" else 10)
        all_scores[kind].append(diff)
        for theme in mapped(source, item_tags[item, source]):
            samples[theme, kind].append(diff)
    averages = {kind: mean(values) for kind, values in all_scores.items()}
    evidence: dict[str, dict] = defaultdict(dict)
    for (theme, kind), values in samples.items():
        evidence[theme][kind] = {
            "count": len(values),
            "difference": mean(values),
            "lean": sum(value - averages[kind] for value in values) / (len(values) + SHRINK),
            "unit": "rating difference",
        }
    # One artist's plays count once per shared theme, regardless of how many tags map to it.
    plays = dict(
        conn.execute(
            "SELECT lower(c.name), COUNT(*) FROM core_behavior_events e "
            "JOIN core_item_creators ic ON ic.item_id = e.item_id "
            "AND ic.source = 'lastfm' AND ic.role = 'artist' "
            "JOIN core_creators c ON c.creator_id = ic.creator_id "
            "WHERE e.source = 'lastfm' AND e.event_type = 'play' GROUP BY c.creator_id, c.name"
        ).fetchall()
    )
    tags = defaultdict(list)
    for key, tag in conn.execute("SELECT artist_key, tag FROM stg_lastfm_artist_tags"):
        tags[key].append(tag)
    total = sum(plays.values())
    counts = Counter()
    for artist, count in plays.items():
        for theme in mapped("lastfm", tags[artist]):
            counts[theme] += count
    for theme, count in counts.items():
        share = count / total
        evidence[theme]["music"] = {
            "count": count,
            "share": share,
            "difference": None,
            "lean": 0.1 * share * count / (count + 100),
            "unit": "play share",
        }
    counts_by_medium = {kind: len(values) for kind, values in all_scores.items()}
    counts_by_medium["music"] = total
    return {"themes": dict(evidence), "counts": counts_by_medium}


def lean(taste: dict, tags: list[str], medium: str) -> float:
    """Other media contribute at most one shrunk vote each, so a large list cannot dominate."""
    values = []
    for tag in tags:
        parts = taste["themes"].get(tag, {})
        others = [part["lean"] for kind, part in parts.items() if kind != medium]
        if others:
            values.append(mean(others))
    return mean(values) if values else 0.0


def transfer(taste: dict, kind: str, tags: list[str], rated: int) -> float:
    """A small shift on the 1 to 10 scale, fading as the target medium gets its own ratings."""
    return 5 * (10 / (rated + 10)) * lean(taste, tags, kind) if kind in ("tv", "book") else 0.0


def reason(taste: dict, tags: list[str], medium: str) -> str:
    """Describe actual evidence from another medium, never an inferred item match."""
    choices = sorted(tags, key=lambda tag: (-lean(taste, [tag], medium), tag))
    for theme in choices:
        clauses = []
        for kind, part in sorted(taste["themes"].get(theme, {}).items()):
            if kind == medium:
                continue
            if kind == "music" and part["count"] >= 100:
                clauses.append(f"you play {theme} music ({part['share']:.0%} of recorded plays)")
            elif part["count"] >= 3:
                scale, unit = (5, "stars") if kind == "book" else (10, "points")
                if (part["difference"] or 0) > 0:
                    clauses.append(
                        f"you rate {theme} {LABELS[kind]} above the crowd "
                        f"({part['difference'] * scale:+.2f} {unit}, {part['count']} ratings)"
                    )
                elif part["lean"] > 0:
                    clauses.append(
                        f"you rate {theme} {LABELS[kind]} above your usual "
                        "difference from the crowd "
                        f"({part['lean'] * scale:+.2f} {unit}, {part['count']} ratings)"
                    )
        if clauses:
            sentence = " and ".join(clauses[:2])
            return sentence[0].upper() + sentence[1:] + "."
    return "An experimental mix of your existing suggestion lists."


def visibility(conn: sqlite3.Connection) -> str:
    """A policy stamp for cached mixes, including changes to adult classifications."""
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT mal_anime_id FROM stg_mal_anime "
            "WHERE nsfw_rating = 'black' ORDER BY mal_anime_id"
        )
    ]
    text = json.dumps([settings.get(conn, "include_nsfw"), ids])
    return hashlib.sha256(text.encode()).hexdigest()
