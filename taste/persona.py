"""What kind of person the data says I am: the Dashboard's "Your taste" card. No Qt.

Fixed rules over my own numbers, no AI service: each trait has a threshold, and
every trait shown carries the number behind it, so the card never claims more than
the data does. The headline strings the strongest few together ("The night-owl,
tough-grading anime fan with a soft spot for fantasy").

A trait needs enough data to mean anything (MIN_* below); a medium without that much
is left out rather than guessed at.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from taste import bookstats, local_time, overview

MIN_SHOWS = 10  # scored shows before saying anything about how I score
MIN_PLAYS = 300
MIN_BOOKS = 5
MIN_FILMS = 10
LEAN = 0.5  # points from the crowd before I count as tough or generous
NIGHT = {22, 23, 0, 1, 2, 3}
MORNING = {5, 6, 7, 8, 9}
POPULAR_TOP = 1000  # "MAL's top 1,000 by popularity"


@dataclass(frozen=True)
class Trait:
    title: str  # "Night owl"
    evidence: str  # "41% of plays after 10 PM"
    kind: str  # anime, music, books, screen: the color it's drawn in
    strength: float  # how far past its threshold, for ordering and the headline
    adjective: str = ""  # "night-owl", for the headline; empty when it doesn't read as one
    lean: float = 0.0  # against the crowd, for critic traits: below is negative
    part: str = ""  # "0.9 below MAL": the lean in words, shared by every medium


@dataclass
class Persona:
    headline: str
    based_on: str  # "From 751 shows, 48,000 plays, 298 books, and 150 films"
    traits: list[Trait] = field(default_factory=list)


def critic(
    title: str,
    diff: float,
    crowd: str,
    kind: str,
    adjective: str = "",
    unit: str = "",
    strength: float | None = None,
) -> Trait:
    """A trait about scoring above or below a crowd, in the one wording every medium
    shares: "0.9 below MAL on average"."""
    part = f"{abs(diff):.1f}{unit} {'below' if diff < 0 else 'above'} {crowd}"
    return Trait(
        title,
        f"{part} on average",
        kind,
        abs(diff) if strength is None else strength,
        adjective,
        diff,
        part,
    )


def merge_critics(traits: list[Trait]) -> list[Trait]:
    """Two or more media below the crowd make one "Tough critic" with each number (two
    or more above, one "Generous critic"); a lone one keeps its own trait."""
    merged = list(traits)
    for below, title, adjective in (
        (True, "Tough critic", "tough-grading"),
        (False, "Generous critic", "generous"),
    ):
        group = [t for t in merged if t.lean and (t.lean < 0) == below]
        if len(group) < 2:
            continue
        first = merged.index(group[0])
        merged = [t for t in merged if t not in group]
        merged.insert(
            first,
            Trait(
                title,
                " · ".join(t.part for t in group),
                "critic",
                max(t.strength for t in group),
                adjective,
                sum(t.lean for t in group) / len(group),
            ),
        )
    return merged


def _anime(conn: sqlite3.Connection) -> tuple[list[Trait], int, str | None]:
    """Traits from MAL, how many shows they rest on, and my top genre there."""
    summary = conn.execute("SELECT shows_scored, avg_diff FROM rpt_mal_critic_summary").fetchone()
    shows = conn.execute(
        "SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NULL "
        "AND status IN ('completed', 'watching', 'dropped', 'on_hold')"
    ).fetchone()[0]
    traits: list[Trait] = []
    if summary and summary[0] and summary[0] >= MIN_SHOWS and summary[1] is not None:
        diff = summary[1]
        if diff <= -LEAN:
            traits.append(critic("Tough grader", diff, "MAL", "anime", "tough-grading"))
        elif diff >= LEAN:
            traits.append(critic("Generous scorer", diff, "MAL", "anime", "generous"))
        else:
            traits.append(Trait("In step with MAL", f"within {LEAN} of MAL on average",
                                "anime", 0.1))  # fmt: skip
    ranks = [
        r[0]
        for r in conn.execute(
            "SELECT a.popularity_rank FROM stg_mal_list_entries l JOIN stg_mal_anime a "
            "ON a.mal_anime_id = l.mal_anime_id WHERE l.removed_at IS NULL "
            "AND l.status = 'completed' AND a.popularity_rank IS NOT NULL"
        )
    ]
    if len(ranks) >= MIN_SHOWS:
        outside = sum(1 for r in ranks if r > POPULAR_TOP) / len(ranks)
        if outside >= 0.4:
            traits.append(Trait("Off the beaten path",
                                f"{outside:.0%} of your shows are outside MAL's top 1,000",
                                "anime", outside, "adventurous"))  # fmt: skip
        elif outside <= 0.15:
            traits.append(Trait("Crowd favorites",
                                f"{1 - outside:.0%} of your shows are in MAL's top 1,000",
                                "anime", 1 - outside))  # fmt: skip
    genres = Counter(
        r[0]
        for r in conn.execute(
            "SELECT g.genre_name FROM stg_mal_list_entries l JOIN stg_mal_anime_genres g "
            "ON g.mal_anime_id = l.mal_anime_id WHERE l.removed_at IS NULL "
            "AND l.status = 'completed'"
        )
    )
    top = genres.most_common(1)[0][0] if genres and shows >= MIN_SHOWS else None
    return traits, shows, top


def _music(conn: sqlite3.Connection, counts: list[int] | None = None) -> tuple[list[Trait], int]:
    hours = dict(conn.execute("SELECT local_hour, plays FROM rpt_lastfm_by_hour").fetchall())
    plays = sum(hours.values())
    traits: list[Trait] = []
    if plays < MIN_PLAYS:
        return traits, plays
    night = sum(n for h, n in hours.items() if h in NIGHT) / plays
    morning = sum(n for h, n in hours.items() if h in MORNING) / plays
    hour = local_time.hour_text
    if night >= 0.3:
        evidence = f"{night:.0%} of plays between {hour(22)} and {hour(4)}"
        traits.append(Trait("Night owl", evidence, "music", night, "night-owl"))
    elif morning >= 0.3:
        evidence = f"{morning:.0%} of plays between {hour(5)} and {hour(10)}"
        traits.append(Trait("Early riser", evidence, "music", morning, "early-rising"))
    artists = overview.artist_play_counts(conn) if counts is None else counts
    top_share = sum(artists[:10]) / plays if artists else 0.0
    if top_share >= 0.4:
        traits.append(Trait("Loyal listener", f"top 10 artists are {top_share:.0%} of plays",
                            "music", top_share, "loyal"))  # fmt: skip
    elif top_share <= 0.2 and len(artists) >= 100:
        traits.append(Trait("Explorer", f"{len(artists):,} artists, and no ten of them make "
                            f"up more than {top_share:.0%} of plays", "music", 0.5,
                            "curious"))  # fmt: skip
    return traits, plays


def _books(
    conn: sqlite3.Connection, stats: bookstats.BookStats | None = None
) -> tuple[list[Trait], int, str | None]:
    stats = stats or bookstats.load(conn)
    traits: list[Trait] = []
    if stats.unique < MIN_BOOKS:
        return traits, stats.unique, None
    top = stats.genres[0] if stats.genres else None
    if top and top.share >= 0.3:
        traits.append(Trait(f"{top.name[:1].upper()}{top.name[1:]} reader",
                            f"{top.share:.0%} of your books", "books", top.share))  # fmt: skip
    full_years = [n for y, n in stats.per_year[:-1]][-3:]  # the year in progress left out
    if full_years and max(full_years) >= 24:
        traits.append(Trait("Bookworm", f"up to {max(full_years)} books a year",
                            "books", max(full_years) / 24, "bookish"))  # fmt: skip
    if stats.my_average is not None and stats.hardcover_average is not None:
        diff = stats.my_average - stats.hardcover_average
        if diff <= -LEAN / 2:
            traits.append(critic("Hard to impress on the page", diff, "Hardcover", "books",
                                 unit="★", strength=abs(diff) * 2))  # fmt: skip
    return traits, stats.unique, top.name if top else None


def _films(conn: sqlite3.Connection) -> tuple[list[Trait], int]:
    rows = conn.execute(
        "SELECT rating_diff FROM rpt_tmdb_ratings WHERE media_type = 'film'"
    ).fetchall()
    traits: list[Trait] = []
    if len(rows) < MIN_FILMS:
        return traits, len(rows)
    diffs = [r[0] for r in rows if r[0] is not None]
    if diffs:
        diff = sum(diffs) / len(diffs)
        if diff >= LEAN:
            traits.append(critic("Easy to please at the movies", diff, "TMDB", "screen"))
        elif diff <= -LEAN:
            traits.append(critic("Tough on films", diff, "TMDB", "screen", "hard-to-please"))
    genres = Counter(
        g
        for (raw,) in conn.execute(
            "SELECT m.genres_json FROM stg_tmdb_account_ratings a JOIN stg_tmdb_movies m "
            "ON m.tmdb_id = a.tmdb_id WHERE a.media_type = 'movie' AND a.removed_at IS NULL"
        )
        for g in json.loads(raw or "[]")
    )
    if genres:
        name, count = genres.most_common(1)[0]
        traits.append(Trait(f"{name} first", f"{count / len(rows):.0%} of the films you rated",
                            "screen", 0.3))  # fmt: skip
    return traits, len(rows)


NOUNS = {"anime": "anime fan", "music": "listener", "books": "reader", "screen": "film buff"}


def load(
    conn: sqlite3.Connection,
    *,
    books: bookstats.BookStats | None = None,
    counts: list[int] | None = None,
) -> Persona | None:
    """The card's contents, or None until there's enough data for at least two traits.
    `books` and `counts` (overview.artist_play_counts) save loading them twice."""
    anime, shows, anime_genre = _anime(conn)
    music, plays = _music(conn, counts)
    books, read, book_genre = _books(conn, books)
    films, rated = _films(conn)
    # The crowds in a fixed order (MAL, TMDB, Hardcover) before the merge, then strongest first.
    traits = merge_critics(anime + films + books + music)
    traits.sort(key=lambda t: -t.strength)
    if len(traits) < 2:
        return None
    # The noun: the medium I spend the most with, roughly (an hour of plays is a lot of
    # plays, so music is weighed down).
    weights = {"anime": shows, "music": plays / 40, "books": read, "screen": rated}
    noun = NOUNS[max(weights, key=lambda k: weights[k])]
    adjectives = [t.adjective for t in traits if t.adjective][:2]
    soft_spot = anime_genre if noun == "anime fan" else book_genre or anime_genre
    headline = "The " + " ".join(filter(None, [", ".join(adjectives), noun]))
    if soft_spot:
        headline += f" with a soft spot for {soft_spot.lower()}"
    parts = [
        # Each says what it counts: other pages count lists, scores, and reads differently.
        f"{shows:,} shows watched" if shows else "",
        f"{plays:,} plays" if plays else "",
        f"{read:,} books read" if read else "",
        f"{rated:,} films rated" if rated else "",
    ]
    parts = [p for p in parts if p]
    based_on = "From " + (
        ", ".join(parts[:-1]) + f", and {parts[-1]}" if len(parts) > 2 else " and ".join(parts)
    )
    return Persona(headline, based_on, traits[:6])
