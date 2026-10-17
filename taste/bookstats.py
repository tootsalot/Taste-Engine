"""The Books page's numbers, from my Hardcover library. No Qt, so it's tested on its own.

A "read" is one finished read-through: a book read twice counts twice in "books
read" and once in "unique". A book marked read with no date at all counts in the
all-time view only, since there's no year to put it in.

Everything else (genres, moods, kinds, formats, stars, lengths, authors) counts each
book once, among the books read in the chosen span.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

from taste.bands import Band, score_bands

READ = 3  # Hardcover's status id for "Read"
TOP_SHOWN = 8  # genres and moods listed
AUTHORS_SHOWN = 6
AUTHOR_COVERS = 6
STAR_BAND = 0.25  # Hardcover averages sit close together: quarter-star bands
FORMATS = {1: "Physical", 2: "Audio", 3: "Physical", 4: "Ebook"}
KINDS = {1: "Fiction", 2: "Nonfiction"}


@dataclass(frozen=True)
class Cover:
    title: str
    author: str
    url: str | None
    times: int  # read-throughs in the span (a badge from 2)
    my_rating: float | None


@dataclass(frozen=True)
class Share:
    name: str
    books: int
    share: float  # of the books read in the span, 0 to 1


@dataclass
class AuthorStat:
    name: str
    books: int  # read-throughs in the span
    average: float | None  # my stars for their books
    covers: list[Cover]  # my highest rated first
    ratings: list[float]  # my stars in the order I read them, for the little line


@dataclass
class BookStats:
    span: str  # "all" or a year like "2026"
    years: list[str]  # years with a dated read, newest first
    reads: int
    unique: int
    undated: int  # reads with no date (all time only)
    pages: int  # pages of the reads with a known length
    pages_known: int  # how many reads that is
    audio_hours: float | None  # of the audiobooks with a known length; None if none has one
    audiobooks: int
    audio_unknown: int  # audiobooks Hardcover has no length for
    authors: int
    new_authors: int | None  # authors first read in this year (None for all time)
    my_average: float | None
    hardcover_average: float | None  # for the same rated books
    rereads: int  # books read more than once
    most_reread: tuple[str, int] | None
    covers: list[Cover]  # newest first
    per_year: list[tuple[str, int]]  # every year from the first read to the last
    genres: list[Share]
    genres_missing: int
    moods: list[Share]
    moods_missing: int
    kinds: list[Share]  # Fiction, Nonfiction, Not set
    formats: list[Share]  # Physical, Ebook, Audio, Not set
    starred: list[Share]  # Starred, Not starred
    stars: list[int]  # 0.5 to 5 in halves: ten counts
    bands: list[Band]  # my stars per quarter star of Hardcover's average
    top_authors: list[AuthorStat] = field(default_factory=list)


@dataclass(frozen=True)
class _Book:
    user_book_id: int
    title: str
    authors: tuple[str, ...]
    my_rating: float | None
    average: float | None
    pages: int | None
    seconds: int | None
    format_id: int | None
    kind_id: int | None
    cover: str | None
    genres: tuple[str, ...]
    moods: tuple[str, ...]


def _library(conn: sqlite3.Connection) -> tuple[dict[int, _Book], dict[int, int]]:
    """My books by user_book_id, and each one's status."""
    books, status = {}, {}
    for row in conn.execute(
        "SELECT ub.user_book_id, ub.status_id, ub.rating, ub.edition_pages, "
        "ub.edition_seconds, ub.reading_format_id, b.title, b.pages, b.audio_seconds, "
        "b.rating, b.literary_type_id, b.image_url, b.authors_json, b.genres_json, "
        "b.moods_json FROM stg_hardcover_user_books ub "
        "JOIN stg_hardcover_books b ON b.book_id = ub.book_id WHERE ub.removed_at IS NULL"
    ):
        books[row[0]] = _Book(
            user_book_id=row[0],
            title=row[6],
            authors=tuple(a["name"] for a in json.loads(row[12])),
            my_rating=row[2],
            average=row[9],
            pages=row[3] or row[7],
            seconds=row[4] or row[8],
            format_id=row[5],
            kind_id=row[10],
            cover=row[11],
            genres=tuple(json.loads(row[13])),
            moods=tuple(json.loads(row[14])),
        )
        status[row[0]] = row[1]
    return books, status


def _reads(conn: sqlite3.Connection, status: dict[int, int]) -> list[tuple[int, str | None]]:
    """(user_book_id, day or None) for every finished read-through, oldest first."""
    found: list[tuple[int, str | None]] = []
    for key, day in conn.execute(
        "SELECT source_event_key, substr(occurred_at_utc, 1, 10) FROM core_behavior_events "
        "WHERE source = 'hardcover' AND event_type = 'read_finished' "
        "ORDER BY occurred_at_utc, event_id"
    ):
        user_book_id = int(key.split(":", 1)[0])
        if user_book_id in status:
            found.append((user_book_id, day))
    dated = {b for b, _ in found}
    found += [(b, None) for b, s in sorted(status.items()) if s == READ and b not in dated]
    return found


def _shares(counts: Counter, total: int, limit: int | None = None) -> list[Share]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if limit is not None:
        ranked = ranked[:limit]
    return [Share(name, n, n / total if total else 0.0) for name, n in ranked]


def years(conn: sqlite3.Connection) -> list[str]:
    """Years with a finished read, newest first (for the span switch)."""
    rows = conn.execute(
        "SELECT DISTINCT substr(occurred_at_utc, 1, 4) FROM core_behavior_events "
        "WHERE source = 'hardcover' AND event_type = 'read_finished' ORDER BY 1 DESC"
    ).fetchall()
    return [r[0] for r in rows]


def load(conn: sqlite3.Connection, span: str = "all") -> BookStats:
    books, status = _library(conn)
    every = _reads(conn, status)
    reads = every if span == "all" else [r for r in every if r[1] and r[1][:4] == span]
    read_ids = list(dict.fromkeys(b for b, _ in reads))  # unique, oldest first
    times = Counter(b for b, _ in reads)
    unique = [books[b] for b in read_ids]

    pages = [books[b].pages for b, _ in reads if books[b].pages]
    audio = [books[b] for b, _ in reads if books[b].format_id == 2]
    timed = [b for b in audio if b.seconds]  # a length Hardcover doesn't know isn't 0 hours
    authors = Counter(a for b, _ in reads for a in books[b].authors)
    new_authors = None
    if span != "all":
        before = {a for b, d in every if d and d[:4] < span for a in books[b].authors}
        new_authors = len(set(authors) - before)
    rated = [b for b in unique if b.my_rating]
    both = [b for b in rated if b.average]
    # A reread: read twice in the span, or (in a year) read again after an earlier year.
    earlier = {b for b, d in every if span != "all" and d and d[:4] < span}
    reread = {b: n + (b in earlier) for b, n in times.items() if n + (b in earlier) > 1}
    most = max(reread.items(), key=lambda kv: (kv[1], -kv[0]), default=None)

    per_year: Counter = Counter(d[:4] for _, d in every if d)
    first, last = (min(per_year), max(per_year)) if per_year else (None, None)
    year_list = [str(y) for y in range(int(first), int(last) + 1)] if first else []

    genres = Counter(g for b in unique for g in b.genres)
    moods = Counter(m for b in unique for m in b.moods)
    kinds = Counter(KINDS.get(b.kind_id, "Not set") for b in unique)
    formats = Counter(FORMATS.get(b.format_id, "Not set") for b in unique)
    stars = [0] * 10
    for b in rated:
        stars[min(max(round(b.my_rating * 2) - 1, 0), 9)] += 1

    top = []
    for name, count in sorted(authors.items(), key=lambda kv: (-kv[1], kv[0]))[:AUTHORS_SHOWN]:
        theirs = [b for b in unique if name in b.authors]
        scored = [b for b in theirs if b.my_rating]
        best = sorted(theirs, key=lambda b: (-(b.my_rating or 0), b.title))[:AUTHOR_COVERS]
        top.append(
            AuthorStat(
                name=name,
                books=count,
                average=round(mean(b.my_rating for b in scored), 2) if scored else None,
                covers=[
                    Cover(b.title, name, b.cover, times[b.user_book_id], b.my_rating) for b in best
                ],  # fmt: skip
                ratings=[
                    books[b].my_rating
                    for b, _ in reads
                    if name in books[b].authors and books[b].my_rating
                ],  # fmt: skip
            )
        )

    return BookStats(
        span=span,
        years=years(conn),
        reads=len(reads),
        unique=len(unique),
        undated=sum(1 for _, d in reads if d is None),
        pages=sum(pages),
        pages_known=len(pages),
        audio_hours=round(sum(b.seconds for b in timed) / 3600, 1) if timed else None,
        audiobooks=len({b.user_book_id for b in audio}),
        audio_unknown=len({b.user_book_id for b in audio if not b.seconds}),
        authors=len(authors),
        new_authors=new_authors,
        my_average=round(mean(b.my_rating for b in rated), 2) if rated else None,
        hardcover_average=round(mean(b.average for b in both), 2) if both else None,
        rereads=len(reread),
        most_reread=(books[most[0]].title, most[1]) if most else None,
        covers=[
            Cover(
                books[b].title,
                ", ".join(books[b].authors),
                books[b].cover,
                times[b],
                books[b].my_rating,
            )
            for b in reversed(read_ids)
        ],  # fmt: skip
        per_year=[(y, per_year.get(y, 0)) for y in year_list],
        genres=_shares(genres, len(unique), TOP_SHOWN),
        genres_missing=sum(1 for b in unique if not b.genres),
        moods=_shares(moods, len(unique), TOP_SHOWN),
        moods_missing=sum(1 for b in unique if not b.moods),
        kinds=_shares(kinds, len(unique)),
        formats=_shares(formats, len(unique)),
        starred=_shares(
            Counter("Starred" if b.my_rating else "Not starred" for b in unique), len(unique)
        ),
        stars=stars,
        bands=score_bands([(b.average, b.my_rating) for b in both], width=STAR_BAND),
        top_authors=top,
    )
