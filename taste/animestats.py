"""The Anime report page's numbers, for all time or one year. No Qt.

A show counts in the year I finished it (activity.finished_shows: a typed finish
date, or a recent list edit the app saw happen). All time also counts the completed
shows with only an old list edit to go on, since every one of them was finished at
some point. Genre leans and where I drop shows are all-time numbers either way:
they need more shows than a year has.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

from taste import activity, charts
from taste.activity import Finished

PEOPLE_SHOWN = 6
PERSON_MIN = 2  # a studio counts as a favorite from this many finished shows
PERSON_POSTERS = 6


@dataclass
class Studio:
    name: str
    shows: int
    average: float | None  # my scores for their shows
    posters: list[Finished]  # my highest scored first
    scores: list[float]  # in the order I finished them


@dataclass
class AnimeStats:
    span: str
    years: list[str]
    finished: list[Finished]  # newest first
    hours: float  # episodes x their length, for the shows finished
    average: float | None  # my scores: every scored show for all time, else those finished
    vs_mal: float | None  # against the MAL mean, the same shows
    top_genre: str | None
    top_decade: str | None
    bands: list[charts.Band]
    scores: list[int]  # 1 to 10
    trend: list[tuple[str, float]] = field(default_factory=list)  # vs MAL by year, all time
    studios: list[Studio] = field(default_factory=list)


def _years_and_studios(
    conn: sqlite3.Connection, item_ids: list[int]
) -> tuple[dict[int, int], dict[int, list[str]]]:
    """Each show's first-season year and its studios, by core item id."""
    if not item_ids:
        return {}, {}
    marks = ",".join("?" for _ in item_ids)
    years, studios = {}, {}
    for item_id, year, studio in conn.execute(
        "SELECT x.item_id, s.start_season_year, st.studio_name FROM core_item_external_ids x "
        "JOIN stg_mal_anime s ON s.mal_anime_id = CAST(x.external_id AS INTEGER) "
        "LEFT JOIN stg_mal_anime_studios st ON st.mal_anime_id = s.mal_anime_id "
        f"WHERE x.source = 'mal' AND x.id_type = 'mal_anime_id' AND x.item_id IN ({marks})",
        item_ids,
    ):
        if year:
            years[item_id] = year
        if studio:
            studios.setdefault(item_id, []).append(studio)
    return years, studios


def years(conn: sqlite3.Connection) -> list[str]:
    """Years I finished a show in (typed dates and recent guesses), newest first."""
    shows = activity.finished_shows(conn, "0000-01-01", "9999-12-31")
    return sorted({f.day[:4] for f in shows}, reverse=True)


def load(conn: sqlite3.Connection, span: str = "all") -> AnimeStats:
    if span == "all":
        shows = activity.finished_shows(conn, "0000-01-01", "9999-12-31", every_guess=True)
    else:
        shows = activity.finished_shows(conn, f"{span}-01-01", f"{int(span) + 1}-01-01")
    # One entry per show: a rewatch finished again counts once, at its latest finish.
    latest = {f.item_id: f for f in sorted(shows, key=lambda f: f.day)}
    shows = sorted(latest.values(), key=lambda f: (f.day, f.title), reverse=True)
    if span == "all":
        # Every show I scored (watching and dropped ones too), from the same report views
        # as the Dashboard's "How critical you are", so the two never disagree.
        pairs = [
            (r[0], r[1])
            for r in conn.execute("SELECT my_score, community_mean FROM rpt_mal_score_vs_community")
        ]
        unpaired = [
            (r[0], None)
            for r in conn.execute(
                "SELECT raw_score FROM core_ratings WHERE source = 'mal' AND item_id NOT IN "
                "(SELECT item_id FROM rpt_mal_score_vs_community)"
            )
        ]
        pairs += unpaired
    else:
        pairs = [(f.my_score, f.community_mean) for f in shows if f.my_score is not None]
    my_scores = [mine for mine, _ in pairs]
    against = [(mine, crowd) for mine, crowd in pairs if crowd is not None]
    released, studios = _years_and_studios(conn, [f.item_id for f in shows])
    genres = Counter(g for f in shows for g in f.genres)
    decades = Counter(f"{released[f.item_id] // 10 * 10}s" for f in shows if f.item_id in released)
    score_counts = [0] * 10
    for mine in my_scores:
        score_counts[min(max(int(mine), 1), 10) - 1] += 1

    by_studio: dict[str, list[Finished]] = {}
    for f in shows:
        for name in studios.get(f.item_id, []):
            by_studio.setdefault(name, []).append(f)
    people = []
    for name, items in by_studio.items():
        if len(items) < PERSON_MIN:
            continue
        mine = [f.my_score for f in items if f.my_score is not None]
        people.append(
            Studio(
                name=name,
                shows=len(items),
                average=round(mean(mine), 2) if mine else None,
                posters=sorted(items, key=lambda f: (-(f.my_score or 0), f.title))[:PERSON_POSTERS],
                scores=[f.my_score for f in sorted(items, key=lambda f: f.day) if f.my_score],
            )
        )
    people.sort(key=lambda p: (-p.shows, -(p.average or 0), p.name))
    return AnimeStats(
        span=span,
        years=years(conn),
        finished=shows,
        hours=round(sum((f.episodes or 0) * (f.episode_seconds or 0) for f in shows) / 3600, 1),
        average=round(mean(my_scores), 2) if my_scores else None,
        vs_mal=round(mean(mine - crowd for mine, crowd in against), 2) if against else None,
        top_genre=genres.most_common(1)[0][0] if genres else None,
        top_decade=decades.most_common(1)[0][0] if decades else None,
        bands=charts.score_bands([(crowd, mine) for mine, crowd in against]),
        scores=score_counts,
        trend=charts.diff_by_year(conn) if span == "all" else [],
        studios=people[:PEOPLE_SHOWN],
    )
