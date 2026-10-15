"""The Films and TV page's numbers, from my TMDB ratings. No Qt.

TMDB keeps when I rated something, not when I watched it, so a year here is the
year I rated it. Films and shows are counted apart where it matters (how many, my
average against TMDB's), and together for genres. Hours are films only: a show's
length depends on how much of it I watched, which TMDB doesn't know.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

from taste.bookstats import Share
from taste.sources import tmdb

TOP_GENRES = 8
PEOPLE_SHOWN = 6
PERSON_MIN = 2  # a director counts as a favorite from this many rated films
PERSON_POSTERS = 6


@dataclass(frozen=True)
class Rated:
    kind: str  # "movie" or "tv"
    tmdb_id: int
    title: str
    rating: float  # mine, 0.5 to 10
    average: float | None  # TMDB's
    day: str  # when I rated it, UTC day
    year: int | None  # released or first aired
    minutes: int | None  # films only
    genres: tuple[str, ...]
    people: tuple[str, ...]  # directors, or a show's creators
    poster: str | None


@dataclass
class Person:
    name: str
    films: int
    average: float
    posters: list[Rated]  # my highest rated first
    ratings: list[float]  # in the order I rated them


@dataclass
class ScreenStats:
    span: str
    years: list[str]
    films: int
    shows: int
    film_hours: float
    film_average: float | None
    film_diff: float | None  # my films against TMDB's average
    tv_average: float | None
    tv_diff: float | None
    top_decade: str | None
    rated: list[Rated]  # newest first
    film_spread: list[int]  # 0.5 to 10 in halves: twenty counts
    tv_spread: list[int]
    genres: list[Share]
    decades: list[tuple[str, int]]
    per_year: list[tuple[str, int]]  # rated per year, every year from first to last
    people: list[Person] = field(default_factory=list)


def _all(conn: sqlite3.Connection) -> list[Rated]:
    found: list[Rated] = []
    for row in conn.execute(
        "SELECT a.media_type, a.tmdb_id, a.rating, a.rated_at, a.title, m.title, m.vote_average, "
        "m.release_date, m.runtime, m.genres_json, m.directors_json, m.poster_path, "
        "t.name, t.vote_average, t.first_air_date, t.genres_json, t.creators_json, "
        "t.poster_path FROM stg_tmdb_account_ratings a "
        "LEFT JOIN stg_tmdb_movies m ON a.media_type = 'movie' AND m.tmdb_id = a.tmdb_id "
        "LEFT JOIN stg_tmdb_tv t ON a.media_type = 'tv' AND t.tmdb_id = a.tmdb_id "
        "WHERE a.removed_at IS NULL AND a.season = 0 AND a.episode = 0"
    ):
        movie = row[0] == "movie"
        released = (row[7] if movie else row[14]) or ""
        people = json.loads((row[10] if movie else row[16]) or "[]")
        found.append(
            Rated(
                kind=row[0],
                tmdb_id=row[1],
                title=(row[5] if movie else row[12]) or row[4],
                rating=row[2],
                average=row[6] if movie else row[13],
                day=(row[3] or "")[:10],
                year=int(released[:4]) if released[:4].isdigit() else None,
                minutes=row[8] if movie else None,
                genres=tuple(json.loads((row[9] if movie else row[15]) or "[]")),
                people=tuple(p.get("name") for p in people if p.get("name")),
                poster=tmdb.poster_url(row[11] if movie else row[17]),
            )
        )
    return sorted(found, key=lambda r: (r.day, r.title), reverse=True)


def _spread(items: list[Rated]) -> list[int]:
    counts = [0] * 20
    for r in items:
        counts[min(max(int(round(r.rating * 2)) - 1, 0), 19)] += 1
    return counts


def _average(items: list[Rated]) -> tuple[float | None, float | None]:
    if not items:
        return None, None
    against = [r.rating - r.average for r in items if r.average]
    return (
        round(mean(r.rating for r in items), 2),
        round(mean(against), 2) if against else None,
    )


def years(conn: sqlite3.Connection) -> list[str]:
    """Years I rated something in, newest first."""
    return sorted({r.day[:4] for r in _all(conn) if r.day}, reverse=True)


def load(conn: sqlite3.Connection, span: str = "all") -> ScreenStats:
    every = _all(conn)
    rated = every if span == "all" else [r for r in every if r.day[:4] == span]
    films = [r for r in rated if r.kind == "movie"]
    shows = [r for r in rated if r.kind == "tv"]
    film_average, film_diff = _average(films)
    tv_average, tv_diff = _average(shows)
    decades = Counter(f"{r.year // 10 * 10}s" for r in rated if r.year)
    genres = Counter(g for r in rated for g in r.genres)
    per_year = Counter(r.day[:4] for r in every if r.day)
    span_years = sorted(per_year)
    year_list = (
        [str(y) for y in range(int(span_years[0]), int(span_years[-1]) + 1)] if span_years else []
    )
    by_person: dict[str, list[Rated]] = {}
    for r in films:
        for name in r.people:
            by_person.setdefault(name, []).append(r)
    people = [
        Person(
            name=name,
            films=len(items),
            average=round(mean(r.rating for r in items), 2),
            posters=sorted(items, key=lambda r: (-r.rating, r.title))[:PERSON_POSTERS],
            ratings=[r.rating for r in sorted(items, key=lambda r: r.day)],
        )
        for name, items in by_person.items()
        if len(items) >= PERSON_MIN
    ]
    people.sort(key=lambda p: (-p.films, -p.average, p.name))
    return ScreenStats(
        span=span,
        years=sorted(per_year, reverse=True),
        films=len(films),
        shows=len(shows),
        film_hours=round(sum(r.minutes or 0 for r in films) / 60, 1),
        film_average=film_average,
        film_diff=film_diff,
        tv_average=tv_average,
        tv_diff=tv_diff,
        top_decade=decades.most_common(1)[0][0] if decades else None,
        rated=rated,
        film_spread=_spread(films),
        tv_spread=_spread(shows),
        genres=[
            Share(name, n, n / len(rated))
            for name, n in sorted(genres.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_GENRES]
        ],
        decades=sorted(decades.items()),
        per_year=[(y, per_year.get(y, 0)) for y in year_list],
        people=people[:PEOPLE_SHOWN],
    )
