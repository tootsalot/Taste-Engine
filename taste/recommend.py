"""Recommendations from my own history.

Anime: predict the score I'd give a show, starting from the MAL community mean
and adding how I usually differ from it, overall and by genre and studio. Those
biases are shrunk toward zero when they rest on few shows, so one show can't
swing a genre. Candidates come from shows MAL users recommend alongside ones I
liked, plus related shows (sequels and so on).

Music: artists I've never played, scored by how similar they are to the ones I
play most (recent plays count more), plus artists I used to play a lot.

Every run is saved in rec_runs / rec_items with its scores and reasons.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Any
from urllib.parse import quote_plus

from taste import enrich, settings
from taste.db import utc_now

GENRE_SHRINK = 5  # a genre's bias counts fully only after several shows
STUDIO_SHRINK = 3
STUDIO_WEIGHT = 0.5  # studios overlap with genres, so they count half
MAX_REASON_SEEDS = 2
GENRE_MENTION = 0.15  # a genre lean this big is worth a word on the card
GENRE_WARNING = -0.3
MIN_RESIDUALS = 20  # fewer past predictions than this and the chance of an 8+ isn't shown
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
REDISCOVER_MIN_PLAYS = 15
REDISCOVER_QUIET_DAYS = 180
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


@dataclass
class Rec:
    kind: str
    item_key: str
    title: str
    subtitle: str = ""
    score: float | None = None
    support: float = 0.0
    badge: str = ""
    image_url: str | None = None
    url: str | None = None
    reasons: list[str] = field(default_factory=list)  # plain sentences for the card
    # mal_mean, chance_8_plus, match_label, and details (the numbers, for a tooltip)
    facts: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# My taste model (anime)
# ---------------------------------------------------------------------------


@dataclass
class TasteModel:
    overall: float  # my average (score - community mean)
    genre: dict[str, float]  # shrunk extra bias per genre
    genre_counts: dict[str, int]
    studio: dict[str, float]

    def adjustment(self, genres: list[str], studios: list[str]) -> float:
        g = mean(self.genre.get(x, 0.0) for x in genres) if genres else 0.0
        s = mean(self.studio.get(x, 0.0) for x in studios) if studios else 0.0
        return g + STUDIO_WEIGHT * s

    def predict(self, community_mean: float, genres: list[str], studios: list[str]) -> float:
        value = community_mean + self.overall + self.adjustment(genres, studios)
        return min(max(value, 1.0), 10.0)


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


def build_model(scored: list[tuple[int, float, float]], facts: dict[int, dict]) -> TasteModel:
    if not scored:
        return TasteModel(0.0, {}, {}, {})
    overall = mean(s - c for _, s, c in scored)
    sums: dict[str, list[float]] = {}
    studio_sums: dict[str, list[float]] = {}
    for anime_id, score, community in scored:
        extra = (score - community) - overall
        for g in facts.get(anime_id, {}).get("genres", []):
            sums.setdefault(g, []).append(extra)
        for s in facts.get(anime_id, {}).get("studios", []):
            studio_sums.setdefault(s, []).append(extra)
    genre = {g: sum(v) / (len(v) + GENRE_SHRINK) for g, v in sums.items()}
    studio = {s: sum(v) / (len(v) + STUDIO_SHRINK) for s, v in studio_sums.items()}
    return TasteModel(overall, genre, {g: len(v) for g, v in sums.items()}, studio)


def evaluate(conn: sqlite3.Connection) -> dict[str, Any]:
    """Hold out every fifth scored show, predict it, and compare errors.

    Mean absolute error (in MAL points) for: the community mean alone, the
    community mean plus my overall bias, and the full model. Lower is better.
    """
    facts = _anime_facts(conn)
    scored = _scored(conn)
    test = [x for x in scored if x[0] % 5 == 0]
    train = [x for x in scored if x[0] % 5 != 0]
    if len(test) < 5 or len(train) < 10:
        return {"held_out": len(test)}
    model = build_model(train, facts)
    errors: dict[str, list[float]] = {"community": [], "overall": [], "model": []}
    for anime_id, score, community in test:
        f = facts[anime_id]
        errors["community"].append(abs(score - community))
        errors["overall"].append(abs(score - min(max(community + model.overall, 1), 10)))
        errors["model"].append(abs(score - model.predict(community, f["genres"], f["studios"])))
    return {"held_out": len(test), **{k: round(mean(v), 3) for k, v in errors.items()}}


def _dismissed(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT item_key FROM rec_dismissed WHERE kind = ?", (kind,))
    }


def anime_recommendations(conn: sqlite3.Connection) -> tuple[list[Rec], dict[str, Any]]:
    cfg = settings.get_all(conn)
    facts = _anime_facts(conn)
    model = build_model(_scored(conn), facts)
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

    candidates = set(support)
    if cfg["rec_include_plan_to_watch"]:
        candidates |= plan_to_watch
    residuals = holdout_residuals(_scored(conn), facts)
    recs: list[Rec] = []
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
        chance = chance_at_least(predicted, residuals, 8)
        if chance is not None:
            rec.facts["chance_8_plus"] = round(chance, 3)
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
    metrics = {"model_overall_bias": round(model.overall, 3), **evaluate(conn)}
    return recs, metrics


def _a(score: int) -> str:
    """'a 9', 'an 8'."""
    return f"an {score}" if score in (8, 11, 18) else f"a {score}"


def _genre_leans(model: TasteModel, genres: list[str]) -> tuple[str | None, str | None]:
    """(a genre of this show I lean toward, one I'm usually hard on), when worth a mention."""
    scored = [(model.genre[g], g) for g in genres if g in model.genre]
    best = max(scored, default=None)
    worst = min(scored, default=None)
    return (
        best[1] if best and best[0] >= GENRE_MENTION else None,
        worst[1] if worst and worst[0] <= GENRE_WARNING else None,
    )


def _loved(score: float) -> str:
    return "loved" if score >= 9 else "really liked" if score >= 8 else "liked"


def _because(
    seeds: list[tuple[str, float]], liked_genre: str | None, harsh_genre: str | None
) -> list[str]:
    """The card's reasons in plain words, no numbers.

    "Because you loved A and really liked B, and you tend to enjoy samurai anime."
    """
    sentences = []
    clauses = []  # "loved A", "B", "really liked C": a verb only when it changes
    last_verb = None
    for title, score in seeds:
        verb = _loved(score)
        clauses.append(title if verb == last_verb else f"{verb} {title}")
        last_verb = verb
    genre = f"you tend to enjoy {liked_genre.lower()} anime" if liked_genre else ""
    if clauses:
        sentence = "Because you " + " and ".join(clauses)
        sentences.append(sentence + (f", and {genre}." if genre else "."))
    elif genre:
        sentences.append(genre[0].upper() + genre[1:] + ".")
    if harsh_genre:
        sentences.append(f"Heads up: you're usually tougher on {harsh_genre.lower()} anime.")
    return sentences


def holdout_residuals(
    scored: list[tuple[int, float, float]], facts: dict[int, dict]
) -> list[float]:
    """How far my real scores landed from predictions made without them (5 folds).

    Every scored show is predicted once by a model that never saw it. Used to say how
    likely a predicted score is to end up an 8 or more.
    """
    if len(scored) < MIN_RESIDUALS:
        return []
    residuals = []
    for fold in range(5):
        train = [x for x in scored if x[0] % 5 != fold]
        model = build_model(train, facts)
        for anime_id, score, community in scored:
            if anime_id % 5 == fold:
                f = facts.get(anime_id, {})
                guess = model.predict(community, f.get("genres", []), f.get("studios", []))
                residuals.append(score - guess)
    return residuals


def chance_at_least(predicted: float, residuals: list[float], at_least: int) -> float | None:
    """Share of past misses that would put this show at `at_least` or more once rounded."""
    if len(residuals) < MIN_RESIDUALS:
        return None
    return mean(1.0 if predicted + r >= at_least - 0.5 else 0.0 for r in residuals)


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------


def lastfm_artist_url(name: str) -> str:
    return f"https://www.last.fm/music/{quote_plus(name)}"


def _artist_cover(conn: sqlite3.Connection, name: str) -> str | None:
    """Cover of the artist's most-played album or EP (decision Q1)."""
    row = conn.execute(
        "SELECT img.url FROM core_creators c "
        "JOIN core_item_creators ic ON ic.creator_id = c.creator_id AND ic.role = 'artist' "
        "JOIN core_items al ON al.item_id = ic.item_id AND al.media_type = 'album' "
        "JOIN core_item_images img ON img.item_id = al.item_id AND img.kind = 'cover' "
        "JOIN core_item_links ln ON ln.parent_item_id = al.item_id "
        "JOIN core_behavior_events e ON e.item_id = ln.child_item_id AND e.event_type = 'play' "
        "WHERE c.name = ? GROUP BY al.item_id, img.url ORDER BY COUNT(*) DESC LIMIT 1",
        (name,),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT image_url FROM stg_lastfm_artist_top_album WHERE artist_key = ?",
        (enrich.artist_key(name),),
    ).fetchone()
    return row[0] if row else None


def _close_to(similar: list[tuple[str, float]]) -> str:
    """'Close to A (84% similar) and B (60%), plus 6 more artists you play.'"""
    shown = similar[:MAX_REASON_SEEDS]
    if len(shown) == 1:
        name, match = shown[0]
        return f"Close to {name} ({match:.0%} similar), an artist you play."
    first, second = shown[0], shown[1]
    text = f"Close to {first[0]} ({first[1]:.0%} similar) and {second[0]} ({second[1]:.0%})"
    more = len(similar) - len(shown)
    if more:
        text += f", plus {more} more {'artist' if more == 1 else 'artists'} you play"
    return text + "."


def _match_label(rank: int, total: int) -> str:
    """The list in thirds: the match score itself (a sum) has no natural scale."""
    if rank <= math.ceil(total / 3):
        return "Strong match"
    if rank <= math.ceil(2 * total / 3):
        return "Good match"
    return "Worth a try"


def music_recommendations(
    conn: sqlite3.Connection, now: datetime | None = None
) -> tuple[list[Rec], list[Rec]]:
    """(discover, rediscover)."""
    cfg = settings.get_all(conn)
    now = now or datetime.now(timezone.utc)
    now_unix = int(now.timestamp())
    weights = enrich.artist_weights(conn, now_unix)
    seeds = sorted(weights, key=lambda k: -weights[k][1])[: cfg["rec_seed_artists"]]
    top = weights[seeds[0]][1] if seeds else 1.0
    known = {
        enrich.artist_key(r[0])
        for r in conn.execute(
            "SELECT c.name FROM core_creators c JOIN core_creator_external_ids x "
            "ON x.creator_id = c.creator_id AND x.id_type = 'lastfm_artist_key'"
        )
    }
    dismissed = _dismissed(conn, "artist")

    scores: dict[str, float] = {}
    names: dict[str, str] = {}
    because: dict[str, list[tuple[float, str, float]]] = {}
    for seed in seeds:
        w = weights[seed][1] / top
        for similar_key, similar_name, match in conn.execute(
            "SELECT similar_key, similar_name, match FROM stg_lastfm_similar_artists "
            "WHERE artist_key = ?",
            (seed,),
        ):
            if similar_key in known or similar_key in dismissed:
                continue
            scores[similar_key] = scores.get(similar_key, 0.0) + w * match
            names[similar_key] = similar_name
            because.setdefault(similar_key, []).append((w * match, weights[seed][0], match))

    picked = sorted(scores, key=lambda k: (-scores[k], k))[: cfg["rec_count"]]
    discover = []
    for rank, key in enumerate(picked, 1):
        seeds_for = sorted(because[key], reverse=True)
        plural = "artist" if len(seeds_for) == 1 else "artists"
        discover.append(
            Rec(
                kind="artist",
                item_key=key,
                title=names[key],
                subtitle=f"Sounds like {len(seeds_for)} {plural} you play",
                score=round(scores[key], 3),
                image_url=_artist_cover(conn, names[key]),
                url=lastfm_artist_url(names[key]),
                reasons=[_close_to([(n, m) for _, n, m in seeds_for])],
                facts={
                    "match_label": _match_label(rank, len(picked)),
                    "details": [
                        f"Match strength {scores[key]:.2f}: how similar it is to artists you "
                        "play, weighted by how much you play them."
                    ],
                },
            )
        )

    all_time = enrich.artist_weights(conn, now_unix, days=36500)
    quiet_since = now_unix - REDISCOVER_QUIET_DAYS * 86400
    rediscover = []
    for key, (name, _, plays, last) in sorted(all_time.items(), key=lambda kv: -kv[1][2]):
        if plays < REDISCOVER_MIN_PLAYS or last >= quiet_since or key in dismissed:
            continue
        last_day = datetime.fromtimestamp(last, timezone.utc)
        rediscover.append(
            Rec(
                kind="artist",
                item_key=key,
                title=name,
                # The play count is the card's big number and its reason already.
                subtitle=f"Last played {last_day:%b} {last_day.day}, {last_day.year}",
                score=float(plays),
                image_url=_artist_cover(conn, name),
                url=lastfm_artist_url(name),
                reasons=[
                    f"You played {name} {plays} times, but not in the last "
                    f"{REDISCOVER_QUIET_DAYS} days."
                ],
            )
        )
        if len(rediscover) >= cfg["rec_count"]:
            break
    return discover, rediscover


# ---------------------------------------------------------------------------
# Saving and reading runs
# ---------------------------------------------------------------------------


def save_run(
    conn: sqlite3.Connection, kind: str, recs: list[Rec], metrics: dict[str, Any] | None = None
) -> int:
    params = {k: settings.get(conn, k) for k in REC_SETTINGS}
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


def latest(conn: sqlite3.Connection, kind: str) -> tuple[list[Rec], dict[str, Any], str | None]:
    """The most recent saved run of a kind, minus anything dismissed since."""
    run = conn.execute(
        "SELECT rec_run_id, created_at, metrics_json FROM rec_runs WHERE kind = ? "
        "ORDER BY rec_run_id DESC LIMIT 1",
        (kind,),
    ).fetchone()
    if run is None:
        return [], {}, None
    item_kind = "anime" if kind == "anime" else "artist"
    dismissed = _dismissed(conn, item_kind)
    recs = [
        Rec(
            kind=item_kind,
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
    return recs, json.loads(run["metrics_json"]), run["created_at"]


def dismiss(conn: sqlite3.Connection, kind: str, item_key: str) -> None:
    conn.execute(
        "INSERT INTO rec_dismissed (kind, item_key, dismissed_at) VALUES (?, ?, ?) "
        "ON CONFLICT (kind, item_key) DO NOTHING",
        (kind, item_key, utc_now()),
    )


REC_SETTINGS = [
    "rec_count",
    "rec_min_raters",
    "rec_media_types",
    "rec_include_plan_to_watch",
    "rec_seed_artists",
    "include_nsfw",
]


def compute_all(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    kinds: tuple[str, ...] = ("anime", "music"),
) -> dict[str, int]:
    """Recompute and save the lists (anime, music, or both). No network.

    Returns counts per list. Lists that weren't recomputed are counted from
    their latest saved run.
    """
    if "anime" in kinds:
        anime, metrics = anime_recommendations(conn)
        save_run(conn, "anime", anime, metrics)
    if "music" in kinds:
        discover, rediscover = music_recommendations(conn, now)
        save_run(conn, "music_discover", discover)
        save_run(conn, "music_rediscover", rediscover)
    return {kind: len(latest(conn, kind)[0]) for kind in LIST_KINDS}


LIST_KINDS = ("anime", "music_discover", "music_rediscover")
