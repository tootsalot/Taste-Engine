"""Fixed year-to-year comparisons from recorded activity, without Qt or prediction."""

from __future__ import annotations

import sqlite3
from collections import Counter
from statistics import mean

from taste import activity, settings
from taste.activity import Period

MEDIA = {
    "anime": ("Anime", "mal"),
    "film": ("Films", "tmdb"),
    "tv": ("TV", "tmdb"),
    "book": ("Books", "hardcover"),
}


def _genres(conn: sqlite3.Connection, source: str) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for item, genre in conn.execute(
        "SELECT it.item_id, t.name FROM core_item_tags it JOIN core_tags t ON t.tag_id = it.tag_id "
        "WHERE it.source = ? AND t.tag_kind = 'genre' ORDER BY t.name",
        (source,),
    ):
        found.setdefault(item, []).append(genre)
    return found


def _items(conn: sqlite3.Connection, period: Period, kind: str) -> list[tuple]:
    if kind == "anime":
        return [
            (s.item_id, s.my_score, s.community_mean)
            for s in activity.finished_shows(conn, period.start, period.end)
        ]
    if kind == "book":
        return [
            (b.item_id, b.my_rating, b.average)
            for b in activity.books_read(conn, period.start, period.end)
        ]
    return [
        (r.item_id, r.my_rating, r.average)
        for r in activity.rated(conn, "movie" if kind == "film" else "tv", period.start, period.end)
    ]


def compare(conn: sqlite3.Connection, year: int | str) -> dict[str, dict]:
    """Compare a calendar year with its predecessor. Sparse media produce no claims.

    Ratings are the current saved ratings for items finished or rated in each year,
    not snapshots of what the ratings used to be. Adult shows count without being named.
    """
    period = Period("year", str(int(year)))
    previous = period.previous()
    minimum = settings.get(conn, "drift_min_titles")
    result = {}
    for kind, (label, source) in MEDIA.items():
        now = _items(conn, period, kind)
        before = _items(conn, previous, kind)
        if min(len(now), len(before)) < minimum:
            continue
        genres = _genres(conn, source)
        shares = [
            Counter(g for item, _, _ in rows for g in set(genres.get(item, [])))
            for rows in (now, before)
        ]
        changes = [
            (g, shares[0][g] / len(now), shares[1][g] / len(before))
            for g in shares[0].keys() | shares[1].keys()
        ]
        changes.sort(key=lambda x: (-(x[1] - x[2]), x[0]))
        growing = next((c for c in changes if c[1] - c[2] >= 0.1), None)
        fading = next((c for c in reversed(changes) if c[2] - c[1] >= 0.1), None)
        pairs = [
            [mine - crowd for _, mine, crowd in rows if mine is not None and crowd is not None]
            for rows in (now, before)
        ]
        lines = []
        numbers = {
            "count": len(now),
            "previous_count": len(before),
            "growing": growing,
            "fading": fading,
        }
        if min(map(len, pairs)) >= minimum:
            current, old = mean(pairs[0]), mean(pairs[1])
            unit = "stars" if kind == "book" else "points"
            lines.append(
                f"Against the crowd: {current:+.2f} {unit}, previously {old:+.2f} "
                f"({current - old:+.2f}; {len(pairs[0])} vs {len(pairs[1])} ratings)."
            )
            numbers.update(difference=current, previous_difference=old, movement=current - old)
        for change, verb in ((growing, "grew"), (fading, "faded")):
            if change:
                genre, current, old = change
                lines.append(
                    f"{genre} {verb}: {current:.0%} vs {old:.0%} "
                    f"({(current - old) * 100:+.0f} percentage points; "
                    f"{len(now)} vs {len(before)} titles)."
                )
        if lines:
            result[kind] = {"label": label, "lines": lines, **numbers}
    plays = [activity.play_count(conn, p.start, p.end) for p in (period, previous)]
    if min(plays) >= settings.get(conn, "drift_min_plays"):
        new = [
            sum(n for _, n in activity.new_artists(conn, p.start, p.end))
            for p in (period, previous)
        ]
        tops = [activity.top_artists(conn, p.start, p.end, 5) for p in (period, previous)]
        retained = len({a for a, _ in tops[0]} & {a for a, _ in tops[1]})
        shares = [n / total for n, total in zip(new, plays, strict=True)]
        lines = [
            f"Plays from artists first heard that year: {shares[0]:.0%} vs {shares[1]:.0%} "
            f"({new[0]:,}/{plays[0]:,} vs {new[1]:,}/{plays[1]:,} recorded plays).",
            f"Top artists retained: {retained} of {len(tops[0])} "
            f"from last year's top {len(tops[1])}.",
        ]
        result["music"] = {
            "label": "Music",
            "lines": lines,
            "plays": plays,
            "new_artist_plays": new,
            "new_share": shares,
            "top_artists": tops,
            "retained_top_artists": retained,
        }
    return result
