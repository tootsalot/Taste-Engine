"""Explicit genre maps and profile-wide evidence, without matching item titles."""

from __future__ import annotations

import hashlib
import json
import math
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
# The existing transfer experiment retains MAP. The broader vocabulary is used
# explicitly by the mixed list and its coverage command.
MIXED_MAP = {source: dict(terms) for source, terms in MAP.items()}
EXPANDED = {
    "shounen": ("teen audience",),
    "shoujo": ("teen audience",),
    "young adult": ("teen audience",),
    "children's": ("child audience",),
    "children's fiction": ("child audience",),
    "middle grade": ("child audience",),
    "seinen": ("adult audience",),
    "josei": ("adult audience",),
    "new adult": ("adult audience",),
    "kids": ("child audience",),
    "ecchi": ("sexual content",),
    "erotica": ("sexual content",),
    "hentai": ("sexual content",),
    "avant garde": ("experimental",),
    "boys love": ("romance",),
    "girls love": ("romance",),
    "gourmet": ("food",),
    "adult cast": ("adult life",),
    "anthropomorphic": ("anthropomorphic",),
    "cgdct": ("everyday life",),
    "childcare": ("caregiving",),
    "combat sports": ("competition", "martial arts"),
    "crossdressing": ("gender expression",),
    "delinquents": ("rebellion",),
    "detective": ("mystery",),
    "educational": ("learning",),
    "gag humor": ("humor",),
    "gore": ("graphic violence",),
    "harem": ("romance",),
    "high stakes game": ("competition", "suspense"),
    "idols (female)": ("performance",),
    "idols (male)": ("performance",),
    "isekai": ("other worlds",),
    "iyashikei": ("restful",),
    "love polygon": ("romance",),
    "love status quo": ("romance",),
    "magical sex shift": ("transformation",),
    "mahou shoujo": ("magic",),
    "martial arts": ("martial arts",),
    "medical": ("medicine",),
    "mythology": ("mythology",),
    "organized crime": ("crime",),
    "otaku culture": ("fandom",),
    "parody": ("humor",),
    "performing arts": ("performance",),
    "pets": ("animals",),
    "racing": ("competition",),
    "reincarnation": ("rebirth",),
    "reverse harem": ("romance",),
    "romantic subtext": ("romance",),
    "samurai": ("samurai",),
    "school": ("school life",),
    "showbiz": ("performance",),
    "strategy game": ("competition",),
    "super power": ("superpowers",),
    "survival": ("survival",),
    "team sports": ("competition", "teamwork"),
    "time travel": ("time travel",),
    "urban fantasy": ("magic", "urban life"),
    "vampire": ("vampires",),
    "video game": ("gaming",),
    "villainess": ("villain protagonist",),
    "visual arts": ("art",),
    "workplace": ("work",),
    "action & adventure": ("action", "adventure"),
    "sci-fi & fantasy": ("speculative",),
    "war & politics": ("conflict", "politics"),
    "news": ("current affairs",),
    "reality": ("unscripted",),
    "soap": ("character drama",),
    "talk": ("conversation",),
    "adventure fiction": ("adventure",),
    "science fiction & fantasy": ("speculative",),
    "sci fi": ("speculative",),
    "speculative fiction": ("speculative",),
    "high fantasy": ("magic",),
    "epic fantasy": ("magic", "adventure"),
    "magical realism": ("magic", "everyday life"),
    "dystopian": ("oppression",),
    "dystopia": ("oppression",),
    "post-apocalyptic": ("survival",),
    "apocalyptic": ("survival",),
    "space opera": ("space", "adventure"),
    "cyberpunk": ("technology", "urban life"),
    "steampunk": ("technology",),
    "superheroes": ("superpowers",),
    "historical fiction": ("history",),
    "historical romance": ("history", "romance"),
    "romantic comedy": ("romance", "humor"),
    "romantasy": ("romance", "magic"),
    "lgbtq+ romance": ("romance",),
    "cozy mystery": ("mystery",),
    "murder mystery": ("mystery", "crime"),
    "true crime": ("crime", "real life"),
    "crime fiction": ("crime",),
    "psychological thriller": ("psychological", "suspense"),
    "suspenseful": ("suspense",),
    "gothic": ("dark",),
    "coming of age": ("growing up",),
    "coming-of-age": ("growing up",),
    "family saga": ("family",),
    "memoir": ("real life",),
    "autobiography": ("real life",),
    "biography & autobiography": ("real life",),
    "non-fiction": ("real life",),
    "essays": ("reflection",),
    "philosophy": ("reflection",),
    "politics": ("politics",),
    "social issues": ("society",),
    "science": ("science",),
    "technology": ("technology",),
    "nature": ("nature",),
    "animals": ("animals",),
    "travel": ("travel",),
    "food": ("food",),
    "cooking": ("food",),
    "art": ("art",),
    "poetry": ("poetry",),
    "short stories": ("short form",),
    "comics": ("sequential art",),
    "graphic novels": ("sequential art",),
    "manga": ("sequential art",),
    "lighthearted": ("lighthearted",),
    "light-hearted": ("lighthearted",),
    "reflective": ("reflection",),
    "contemplative": ("reflection",),
    "sad": ("melancholy",),
    "melancholic": ("melancholy",),
    "heartwarming": ("warmth",),
    "uplifting": ("hope",),
    "inspiring": ("hope",),
    "inspirational": ("hope",),
    "romantic": ("romance",),
    "informative": ("learning",),
    "humorous": ("humor",),
    "witty": ("humor",),
    "silly": ("humor",),
    "relaxed": ("restful",),
    "bleak": ("dark",),
    "bittersweet": ("bittersweet",),
    "optimistic": ("hope",),
    "fun": ("lighthearted",),
    "adventurous": ("adventure",),
    "mysterious": ("mystery",),
    "scary": ("dark",),
    "relaxing": ("restful",),
    "cozy": ("restful",),
    "dreamlike": ("dreamlike",),
}
for _source in ("mal", "tmdb", "hardcover"):
    MIXED_MAP[_source].update(EXPANDED)
# Audience terms retain literal audience labels, never inferred plots. Accolades
# and distribution formats supply no shared story evidence.
UNMAPPED = frozenset(
    {
        "award winning",
        "tv movie",
        "adult",
        "fiction",
        "contemporary",
        "fast-paced",
        "medium-paced",
        "slow-paced",
    }
)
for _terms in MIXED_MAP.values():
    for _term in UNMAPPED:
        _terms.pop(_term, None)
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


def mapped(source: str, tags: list[str], *, mixed: bool = False) -> list[str]:
    """Map known genres, themes, moods, or tags. Unknown terms are left out."""
    return sorted(
        {
            theme
            for tag in tags
            if isinstance(tag, str)
            for theme in (MIXED_MAP if mixed else MAP)
            .get(source, {})
            .get(tag.strip().casefold(), ())
        }
    )


def candidate_themes(
    conn: sqlite3.Connection, kind: str, key: str, facts: dict, *, mixed: bool = False
) -> list[str]:
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
    return mapped(SOURCES[kind], tags, mixed=mixed)


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


def load(conn: sqlite3.Connection) -> dict:
    """What `profile` reads, read once: a holdout builds a profile per fold from it."""
    item_tags: dict[tuple[int, str], list[str]] = defaultdict(list)
    for item, source, tag in conn.execute(
        "SELECT it.item_id, it.source, t.name FROM core_item_tags it "
        "JOIN core_tags t ON t.tag_id = it.tag_id "
        "WHERE t.tag_kind IN ('genre', 'theme', 'mood')"
    ):
        item_tags[item, source].append(tag)
    ratings = conn.execute(
        "SELECT r.item_id, r.source, i.media_type, r.raw_score, cr.mean_score FROM core_ratings r "
        "JOIN core_items i ON i.item_id = r.item_id LEFT JOIN core_community_ratings cr "
        "ON cr.item_id = r.item_id AND cr.source = r.source"
    ).fetchall()
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
    identities = defaultdict(set)
    kinds = {identity: kind for kind, identity in IDS.items()}
    for item, source, id_type in conn.execute(
        "SELECT item_id, source, id_type FROM core_item_external_ids"
    ):
        kind = kinds.get((source, id_type))
        if kind:
            identities[item, source].add(kind)
    return {
        "item_tags": item_tags,
        "ratings": ratings,
        "plays": plays,
        "artist_tags": tags,
        "identities": identities,
    }


def rated_rows(data: dict) -> list[tuple]:
    """Personal ratings by recorded source identity, even without crowd scores."""
    rows = []
    for item, source, media, mine, crowd in data["ratings"]:
        options = data["identities"].get((item, source), set())
        kind = next(iter(options)) if len(options) == 1 else MEDIA.get(media)
        if kind not in IDS or source != SOURCES[kind]:
            continue
        if not isinstance(mine, (float, int)) or not math.isfinite(mine):
            continue
        if not 0 < mine <= (5 if kind == "book" else 10):
            continue
        rows.append((item, source, kind, mine, crowd))
    return rows


def coverage(conn: sqlite3.Connection, data: dict | None = None) -> dict:
    """Counts and taxonomy terms only, with no titles or credited people."""
    data = load(conn) if data is None else data
    media = {
        kind: dict.fromkeys(("titles", "mapped_titles", "tags", "mapped_tags"), 0)
        for kind in (*IDS, "music")
    }
    unmapped = Counter()
    entries = [
        (kind, source, data["item_tags"].get((item, source), []))
        for item, source, kind, _, _ in rated_rows(data)
    ]
    entries += [
        ("music", "lastfm", data["artist_tags"].get(artist, []))
        for artist, count in data["plays"].items()
        if count > 0
    ]
    for kind, source, tags in entries:
        block = media[kind]
        block["titles"] += 1
        matches = 0
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                continue
            block["tags"] += 1
            if mapped(source, [tag], mixed=True):
                matches += 1
            else:
                unmapped[tag.strip().casefold()] += 1
        block["mapped_tags"] += matches
        block["mapped_titles"] += bool(matches)
    totals = {
        key: sum(block[key] for block in media.values()) for key in next(iter(media.values()))
    }
    return {
        "media": media,
        "totals": totals,
        "unmapped": sorted(unmapped.items(), key=lambda row: (-row[1], row[0]))[:20],
    }


def profile(
    conn: sqlite3.Connection,
    exclude: set[int] | None = None,
    data: dict | None = None,
    *,
    mixed: bool = False,
) -> dict:
    """Theme evidence by medium, keeping ratings and music behavior distinct."""
    exclude = exclude or set()
    data = data or load(conn)
    item_tags = data["item_tags"]
    samples, all_scores = defaultdict(list), defaultdict(list)
    rows = (
        rated_rows(data)
        if mixed
        else (
            (item, source, MEDIA.get(media), mine, crowd)
            for item, source, media, mine, crowd in data["ratings"]
        )
    )
    for item, source, kind, mine, crowd in rows:
        if item in exclude or not kind or source != SOURCES[kind] or crowd is None:
            continue
        diff = (mine - crowd) / (5 if kind == "book" else 10)
        all_scores[kind].append(diff)
        for theme in mapped(source, item_tags[item, source], mixed=mixed):
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
    plays, tags = data["plays"], data["artist_tags"]
    total = sum(plays.values())
    counts = Counter()
    for artist, count in plays.items():
        for theme in mapped("lastfm", tags[artist], mixed=mixed):
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
