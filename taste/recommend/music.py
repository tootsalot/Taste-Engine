"""Music discovery and rediscovery candidates."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from taste import enrich, settings
from taste.recommend.model import MAX_REASON_SEEDS, Rec, _dismissed, about_text, short_count

REDISCOVER_MIN_PLAYS = 15


REDISCOVER_QUIET_DAYS = 180


def lastfm_artist_url(name: str) -> str:
    return f"https://www.last.fm/music/{quote_plus(name)}"


def _artist_cover(conn: sqlite3.Connection, name: str) -> str | None:
    """The artist's picture: their most-played album's cover, else their top album's
    cover from Last.fm, else a Deezer photo. Last.fm itself has no artist photos."""
    # By the artist's key (lowercase, as Last.fm artists are matched everywhere), which is
    # indexed: matching the display name was case-sensitive and scanned every item.
    row = conn.execute(
        "SELECT img.url FROM core_creator_external_ids x "
        "JOIN core_item_creators ic ON ic.creator_id = x.creator_id AND ic.role = 'artist' "
        "JOIN core_items al ON al.item_id = ic.item_id AND al.media_type = 'album' "
        "JOIN core_item_images img ON img.item_id = al.item_id AND img.kind = 'cover' "
        "JOIN core_item_links ln ON ln.parent_item_id = al.item_id "
        "JOIN core_behavior_events e ON e.item_id = ln.child_item_id AND e.event_type = 'play' "
        "WHERE x.source = 'lastfm' AND x.id_type = 'lastfm_artist_key' AND x.external_id = ? "
        "GROUP BY al.item_id, img.url ORDER BY COUNT(*) DESC LIMIT 1",
        (enrich.artist_key(name),),
    ).fetchone()
    if row:
        return row[0]
    key = enrich.artist_key(name)
    row = conn.execute(
        "SELECT image_url FROM stg_lastfm_artist_top_album WHERE artist_key = ?", (key,)
    ).fetchone()
    if row and row[0]:
        return row[0]
    return _deezer_photo(conn, name)


def artist_picture(conn: sqlite3.Connection, name: str) -> str | None:
    """An artist's picture for anywhere in the app (the recaps): see _artist_cover."""
    return _artist_cover(conn, name)


def _deezer_photo(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT picture_url FROM stg_deezer_artists WHERE artist_key = ?",
        (enrich.artist_key(name),),
    ).fetchone()
    return row[0] if row else None


def _with_fallback(conn: sqlite3.Connection, rec: Rec) -> Rec:
    """Keep the artist's Deezer photo as a backup for a cover that can't be downloaded."""
    photo = _deezer_photo(conn, rec.title)
    if rec.image_url and photo and photo != rec.image_url:
        rec.facts["fallback_image"] = photo
    return rec


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
        last_day = datetime.fromtimestamp(last, ZoneInfo(cfg["timezone"]))  # your calendar
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
    for rec in discover + rediscover:
        _add_artist_info(conn, rec)
    return (
        [_with_fallback(conn, r) for r in discover],
        [_with_fallback(conn, r) for r in rediscover],
    )


def _add_artist_info(conn: sqlite3.Connection, rec: Rec) -> None:
    row = conn.execute(
        "SELECT bio, listeners, playcount FROM stg_lastfm_artist_info WHERE artist_key = ?",
        (rec.item_key,),
    ).fetchone()
    if row is None:
        return
    rec.facts["about"] = about_text(row[0])
    rec.facts["popularity"] = [
        text
        for text in (
            f"{short_count(row[1])} listeners on Last.fm" if row[1] else "",
            f"{short_count(row[2])} plays" if row[2] else "",
        )
        if text
    ]
