"""What happened in a stretch of time: plays, finished shows, hours. Qt-free.

The Dashboard's "This month so far" card, the Reports tiles, and the monthly and
yearly recaps all read their numbers from here, so they always agree.

Periods are local calendar days in the profile's time zone: plays use
core_event_local_times, and MAL dates (typed in by hand, no time of day) are used
as written. Days are 'YYYY-MM-DD' text and `end` is exclusive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo

from taste import settings

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip
WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
HOURS_MIN_COVERAGE = 0.5  # fewer plays with a known length than this and hours aren't shown
ALL_TIME = ("0000-01-01", "9999-12-31")  # (start, end) local days that hold every play


@dataclass(frozen=True)
class Period:
    """A calendar month ('2026-10') or year ('2026')."""

    kind: str  # "month" or "year"
    key: str

    @property
    def start(self) -> str:
        return f"{self.key}-01" if self.kind == "month" else f"{self.key}-01-01"

    @property
    def end(self) -> str:
        return self.next().start

    @property
    def label(self) -> str:
        """ "October 2026" or "2026"."""
        if self.kind == "year":
            return self.key
        return f"{MONTH_NAMES[int(self.key[5:7]) - 1]} {self.key[:4]}"

    @property
    def short(self) -> str:
        """ "October" or "2026", for sentences inside a recap."""
        return MONTH_NAMES[int(self.key[5:7]) - 1] if self.kind == "month" else self.key

    def next(self) -> Period:
        if self.kind == "year":
            return Period("year", str(int(self.key) + 1))
        year, month = int(self.key[:4]), int(self.key[5:7])
        return Period(
            "month", f"{year + 1:04d}-01" if month == 12 else f"{year:04d}-{month + 1:02d}"
        )

    def previous(self) -> Period:
        if self.kind == "year":
            return Period("year", str(int(self.key) - 1))
        year, month = int(self.key[:4]), int(self.key[5:7])
        return Period(
            "month", f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"
        )

    def months(self) -> list[Period]:
        """The months in this period (itself, for a month)."""
        if self.kind == "month":
            return [self]
        return [Period("month", f"{self.key}-{m:02d}") for m in range(1, 13)]

    def days(self) -> list[str]:
        first = date.fromisoformat(self.start)
        count = (date.fromisoformat(self.end) - first).days
        return [date.fromordinal(first.toordinal() + i).isoformat() for i in range(count)]


def today(conn: sqlite3.Connection, now: datetime | None = None) -> date:
    """Today in the profile's time zone."""
    zone = ZoneInfo(settings.get(conn, "timezone"))
    return (now or datetime.now(timezone.utc)).astimezone(zone).date()


def current(conn: sqlite3.Connection, kind: str, now: datetime | None = None) -> Period:
    day = today(conn, now)
    return Period(kind, f"{day:%Y-%m}" if kind == "month" else str(day.year))


LATE_DAYS = 14  # Last.fm takes scrobbles up to 14 days late, and MAL dates get typed in later


def open_spans(conn: sqlite3.Connection, now: datetime | None = None) -> list[tuple[str, str]]:
    """(start, end) of the periods whose recap can still change: this month, last
    month, and in January last year."""
    this = current(conn, "month", now)
    spans = [(this.start, this.end), (this.previous().start, this.previous().end)]
    if this.key.endswith("-01"):
        last_year = current(conn, "year", now).previous()
        spans.append((last_year.start, last_year.end))
    return spans


def length_spans(conn: sqlite3.Connection, now: datetime | None = None) -> list[tuple[str, str]]:
    """Where a sync looks up track lengths: the open periods first, then all time while
    too few plays have a length for all-time hours. Most played tracks come first in
    each, so all time crosses HOURS_MIN_COVERAGE in the fewest lookups."""
    spans = open_spans(conn, now)
    if music_hours(conn, *ALL_TIME).hours is None:
        spans.append(ALL_TIME)
    return spans


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------

PLAYS_SQL = (
    "FROM core_behavior_events e JOIN core_event_local_times l ON l.event_id = e.event_id "
    "WHERE e.source = 'lastfm' AND e.event_type = 'play' AND l.local_date >= ? AND l.local_date < ?"
)
ARTIST_JOIN = (
    "JOIN core_item_creators ic ON ic.item_id = e.item_id AND ic.role = 'artist' "
    "AND ic.source = 'lastfm' JOIN core_creators c ON c.creator_id = ic.creator_id "
)


def play_count(conn: sqlite3.Connection, start: str, end: str) -> int:
    return conn.execute(f"SELECT COUNT(*) {PLAYS_SQL}", (start, end)).fetchone()[0]


def top_artists(conn: sqlite3.Connection, start: str, end: str, n: int = 5):
    """[(artist, plays)], most played first."""
    rows = conn.execute(
        "SELECT c.name, COUNT(*) AS plays FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        f"{ARTIST_JOIN}"
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' "
        "AND l.local_date >= ? AND l.local_date < ? "
        "GROUP BY c.creator_id, c.name ORDER BY plays DESC, c.name LIMIT ?",
        (start, end, n),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def top_tracks(conn: sqlite3.Connection, start: str, end: str, n: int = 5):
    """[(track, artist, plays)], most played first."""
    rows = conn.execute(
        "SELECT i.title, c.name, COUNT(*) AS plays FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        "JOIN core_items i ON i.item_id = e.item_id "
        f"{ARTIST_JOIN}"
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' "
        "AND l.local_date >= ? AND l.local_date < ? "
        "GROUP BY e.item_id, i.title, c.name ORDER BY plays DESC, c.name, i.title LIMIT ?",
        (start, end, n),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def top_albums(conn: sqlite3.Connection, start: str, end: str, n: int = 5):
    """[(album, artist, plays, cover url)], most played first."""
    # Plays are counted per track first, then the far fewer tracks are joined to their
    # albums: one join chain per play took about 0.1 s on 20,000 plays (2026-10-16).
    rows = conn.execute(
        "WITH per_track AS (SELECT e.item_id, COUNT(*) AS plays FROM core_behavior_events e "
        "  JOIN core_event_local_times l ON l.event_id = e.event_id "
        "  WHERE e.source = 'lastfm' AND e.event_type = 'play' "
        "  AND l.local_date >= ? AND l.local_date < ? GROUP BY e.item_id) "
        "SELECT al.title, c.name, SUM(t.plays) AS plays, img.url FROM per_track t "
        "JOIN core_item_links ln ON ln.child_item_id = t.item_id AND ln.link_type = 'appears_on' "
        "JOIN core_items al ON al.item_id = ln.parent_item_id AND al.media_type = 'album' "
        "JOIN core_item_creators ic ON ic.item_id = al.item_id AND ic.role = 'artist' "
        "JOIN core_creators c ON c.creator_id = ic.creator_id "
        "LEFT JOIN core_item_images img ON img.item_id = al.item_id AND img.kind = 'cover' "
        "GROUP BY al.item_id, al.title, c.name, img.url ORDER BY plays DESC, al.title LIMIT ?",
        (start, end, n),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def new_artists(conn: sqlite3.Connection, start: str, end: str) -> list[tuple[str, int]]:
    """Artists whose first play ever falls in the period: [(artist, plays in it)]."""
    rows = conn.execute(
        "SELECT c.name, SUM(CASE WHEN l.local_date >= ? THEN 1 ELSE 0 END) AS plays, "
        "MIN(l.local_date) AS first_day FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        f"{ARTIST_JOIN}"
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' AND l.local_date < ? "
        "GROUP BY c.creator_id, c.name HAVING MIN(l.local_date) >= ? "
        "ORDER BY plays DESC, c.name",
        (start, end, start),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def plays_by_hour(conn: sqlite3.Connection, start: str, end: str) -> list[int]:
    hours = [0] * 24
    for hour, plays in conn.execute(
        f"SELECT l.local_hour, COUNT(*) {PLAYS_SQL} GROUP BY l.local_hour", (start, end)
    ):
        hours[hour] = plays
    return hours


def plays_by_day(conn: sqlite3.Connection, start: str, end: str) -> dict[str, int]:
    """{'YYYY-MM-DD': plays} for days with plays."""
    return dict(
        conn.execute(
            f"SELECT l.local_date, COUNT(*) {PLAYS_SQL} GROUP BY l.local_date", (start, end)
        ).fetchall()
    )


@dataclass(frozen=True)
class Hours:
    hours: float | None  # None when too few plays have a known length
    coverage: float  # share of plays with a known track length
    known: float = 0.0  # hours of the plays whose length is known (at least this many)


def music_hours(conn: sqlite3.Connection, start: str, end: str) -> Hours:
    """About how long the period's plays lasted, from Last.fm track lengths.

    Scrobbles have no length, so lengths come from track.getInfo (taste.enrich).
    Plays of tracks without a known length count as the median known length. With
    fewer than HOURS_MIN_COVERAGE of plays covered, it's None rather than a guess.
    """
    rows = conn.execute(
        "SELECT t.duration_ms FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        "LEFT JOIN core_item_external_ids x ON x.item_id = e.item_id AND x.source = 'lastfm' "
        "  AND x.id_type = 'lastfm_track_key' "
        "LEFT JOIN stg_lastfm_track_info t ON t.track_key = x.external_id "
        "WHERE e.source = 'lastfm' AND e.event_type = 'play' "
        "AND l.local_date >= ? AND l.local_date < ?",
        (start, end),
    ).fetchall()
    if not rows:
        return Hours(0.0, 1.0)
    known = [r[0] for r in rows if r[0]]
    coverage = len(known) / len(rows)
    if coverage < HOURS_MIN_COVERAGE:
        return Hours(None, coverage, sum(known) / 3_600_000)
    total_ms = sum(known) + (len(rows) - len(known)) * median(known)
    return Hours(total_ms / 3_600_000, coverage)


# ---------------------------------------------------------------------------
# Anime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finished:
    item_id: int
    title: str  # English when MAL has one
    day: str  # local 'YYYY-MM-DD'
    my_score: float | None
    community_mean: float | None
    poster_url: str | None  # None for shows MAL rates NSFW
    episodes: int | None
    episode_seconds: int | None
    genres: list[str] = field(default_factory=list)
    guessed: bool = False  # the date is the last list edit, not a typed finish date
    hidden: bool = False  # an adult show while "Include NSFW anime" is off: counted, not shown


def hide_nsfw(conn: sqlite3.Connection) -> bool:
    """Whether adult shows (MAL's nsfw "black") are kept off screen. They still count."""
    return not settings.get(conn, "include_nsfw")


def hidden_items(conn: sqlite3.Connection) -> set[int]:
    """Core ids of the shows kept off screen right now (none while NSFW is included)."""
    if not hide_nsfw(conn):
        return set()
    return {
        r[0]
        for r in conn.execute(
            "SELECT x.item_id FROM core_item_external_ids x JOIN stg_mal_anime s "
            "ON s.mal_anime_id = CAST(x.external_id AS INTEGER) "
            "WHERE x.source = 'mal' AND x.id_type = 'mal_anime_id' AND s.nsfw_rating = 'black'"
        )
    }


def shown(shows: list[Finished]) -> list[Finished]:
    """The shows that may be named or pictured (adult ones left out while hidden)."""
    return [f for f in shows if not f.hidden]


def first_sync_at(conn: sqlite3.Connection, source: str) -> str | None:
    row = conn.execute(
        "SELECT MIN(started_at) FROM sync_runs WHERE source = ? AND status = 'success' "
        "AND mode <> 'enrich'",
        (source,),
    ).fetchone()
    return row[0] if row else None


def finished_shows(
    conn: sqlite3.Connection, start: str, end: str, every_guess: bool = False
) -> list[Finished]:
    """Shows finished in [start, end), oldest first.

    A finish date I typed counts on that day. A completed show without one carries
    its last list edit as a guess, and most of those land on old bulk-edit days, so a
    guess counts only when the edit came after my first MAL sync (seen close to when
    it happened). Older guesses still count in all-time totals (`every_guess`).
    """
    first_sync = first_sync_at(conn, "mal") or "9999"
    hide = hide_nsfw(conn)
    rows = conn.execute(
        "SELECT e.item_id, i.title, i.title_alt, e.occurred_at_utc, e.time_basis, l.local_date, "
        "  r.raw_score, cr.mean_score, img.url, s.nsfw_rating, s.num_episodes, "
        "  s.avg_episode_seconds "
        "FROM core_behavior_events e "
        "JOIN core_items i ON i.item_id = e.item_id "
        "LEFT JOIN core_event_local_times l ON l.event_id = e.event_id "
        "LEFT JOIN core_ratings r ON r.item_id = e.item_id AND r.source = e.source "
        "LEFT JOIN core_community_ratings cr ON cr.item_id = e.item_id AND cr.source = e.source "
        "LEFT JOIN core_item_images img "
        "  ON img.item_id = e.item_id AND img.source = e.source AND img.kind = 'poster' "
        "LEFT JOIN core_item_external_ids x "
        "  ON x.item_id = e.item_id AND x.source = e.source AND x.id_type = 'mal_anime_id' "
        "LEFT JOIN stg_mal_anime s ON s.mal_anime_id = CAST(x.external_id AS INTEGER) "
        "WHERE e.source = 'mal' AND e.event_type = 'watch_finished'"
    ).fetchall()
    found = []
    for row in rows:
        guessed = row[4] == "fallback_list_updated_at"
        if guessed and row[3] < first_sync and not every_guess:
            continue
        day = row[5] if guessed and row[5] else row[3][:10]
        if not start <= day < end:
            continue
        found.append(
            Finished(
                item_id=row[0],
                title=row[2] or row[1],
                day=day,
                my_score=row[6],
                community_mean=row[7],
                poster_url=None if row[9] == "black" else row[8],
                episodes=row[10],
                episode_seconds=row[11],
                guessed=guessed,
                hidden=hide and row[9] == "black",
            )
        )
    if found:
        genres: dict[int, list[str]] = {}
        marks = ",".join("?" for _ in found)
        for item_id, name in conn.execute(
            "SELECT it.item_id, t.name FROM core_item_tags it JOIN core_tags t "
            "ON t.tag_id = it.tag_id AND t.tag_kind = 'genre' "
            f"WHERE it.source = 'mal' AND it.item_id IN ({marks}) ORDER BY t.name",
            [f.item_id for f in found],
        ):
            genres.setdefault(item_id, []).append(name)
        found = [Finished(**{**f.__dict__, "genres": genres.get(f.item_id, [])}) for f in found]
    return sorted(found, key=lambda f: (f.day, f.title))


@dataclass(frozen=True)
class Episodes:
    count: int
    seconds: int  # 0 when episode lengths aren't known


def episodes_watched(
    conn: sqlite3.Connection, start: str, end: str, finished: list[Finished] | None = None
) -> Episodes:
    """Episodes watched in the period.

    From the episode events a MAL sync records when it sees my progress go up, plus,
    for shows finished in the period with no such events by the period's end (finished
    before syncing tracked episodes), all of their episodes. Events after the period (a
    rewatch years later) don't change it.
    """
    count = seconds = 0
    tracked: set[int] = set()  # shows with episode events by the end of the period
    for item_id, local_date, ep_seconds in conn.execute(
        "SELECT e.item_id, l.local_date, s.avg_episode_seconds FROM core_behavior_events e "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        "LEFT JOIN core_item_external_ids x "
        "  ON x.item_id = e.item_id AND x.source = e.source AND x.id_type = 'mal_anime_id' "
        "LEFT JOIN stg_mal_anime s ON s.mal_anime_id = CAST(x.external_id AS INTEGER) "
        "WHERE e.source = 'mal' AND e.event_type = 'episode_watched'"
    ):
        if local_date < end:
            tracked.add(item_id)
        if start <= local_date < end:
            count += 1
            seconds += ep_seconds or 0
    for show in finished if finished is not None else finished_shows(conn, start, end):
        if show.item_id not in tracked and show.episodes:
            count += show.episodes
            seconds += show.episodes * (show.episode_seconds or 0)
    return Episodes(count, seconds)


def whole_score_split(pairs: list[tuple[float, float]]) -> tuple[int, int, int]:
    """(above, level, below): my score against the crowd's mean rounded to a whole point.

    MAL scores are whole numbers, so a 7 against a 7.4 mean is "level", not "below".
    Averages elsewhere stay decimal. Book stars come in halves: see `half_star_split`.
    """
    above = level = below = 0
    for mine, crowd in pairs:
        rounded = int(crowd + 0.5)
        if mine > rounded:
            above += 1
        elif mine < rounded:
            below += 1
        else:
            level += 1
    return above, level, below


def half_star_split(pairs: list[tuple[float, float]]) -> tuple[int, int, int]:
    """(above, level, below): my stars against the average rounded to the nearest half
    star, since Hardcover ratings come in halves. 4 against a 3.9 average is level."""
    return whole_score_split([(mine * 2, crowd * 2) for mine, crowd in pairs])


# ---------------------------------------------------------------------------
# Books (Hardcover)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Book:
    item_id: int
    title: str
    author: str  # the first credited author
    day: str  # the day I finished it
    my_rating: float | None  # stars, 0.5 to 5 in halves
    average: float | None  # the Hardcover average
    pages: int | None  # the edition I read, else the book's usual length
    cover_url: str | None


# One row per finished read-through, so a book read twice in a month counts twice.
BOOKS_SQL = (
    "SELECT e.item_id, b.title, "
    "  (SELECT c.name FROM core_item_creators ic JOIN core_creators c "
    "   ON c.creator_id = ic.creator_id WHERE ic.item_id = e.item_id "
    "   AND ic.source = 'hardcover' AND ic.role = 'author' ORDER BY c.creator_id LIMIT 1), "
    "  substr(e.occurred_at_utc, 1, 10) AS day, ub.rating, b.rating, "
    "  COALESCE(ub.edition_pages, b.pages), b.image_url "
    "FROM core_behavior_events e "
    "JOIN core_item_external_ids x ON x.item_id = e.item_id AND x.source = 'hardcover' "
    "  AND x.id_type = 'hardcover_book_id' "
    "JOIN stg_hardcover_books b ON b.book_id = CAST(x.external_id AS INTEGER) "
    "JOIN stg_hardcover_user_books ub ON ub.book_id = b.book_id AND ub.removed_at IS NULL "
    "WHERE e.source = 'hardcover' AND e.event_type = 'read_finished' "
)


def _book(row) -> Book:
    return Book(row[0], row[1], row[2] or "", row[3], row[4], row[5], row[6], row[7])


def books_read(conn: sqlite3.Connection, start: str, end: str) -> list[Book]:
    """Read-throughs finished in [start, end), oldest first. Undated reads don't count."""
    rows = conn.execute(
        BOOKS_SQL + "AND e.occurred_at_utc >= ? AND e.occurred_at_utc < ? ORDER BY day, e.event_id",
        (f"{start} 00:00:00", f"{end} 00:00:00"),
    ).fetchall()
    return [_book(r) for r in rows]


def recently_read(conn: sqlite3.Connection, limit: int = 30) -> list[Book]:
    rows = conn.execute(
        BOOKS_SQL + "ORDER BY day DESC, e.event_id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_book(r) for r in rows]


# ---------------------------------------------------------------------------
# Films and TV (TMDB): rated, never "watched" (TMDB has no diary)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rated:
    item_id: int
    kind: str  # "movie" or "tv"
    title: str
    day: str  # local day I rated it
    my_rating: float  # 0.5 to 10, in halves
    average: float | None  # the TMDB average
    year: int | None
    poster_url: str | None
    people: list[str] = field(default_factory=list)  # directors, or a show's creators


def rated(conn: sqlite3.Connection, kind: str, start: str, end: str) -> list[Rated]:
    """Films ("movie") or shows ("tv") I rated on local days in [start, end), oldest first."""
    media = {"movie": "film", "tv": "tv_show"}[kind]
    rows = conn.execute(
        "SELECT e.item_id, i.title, l.local_date, r.raw_score, cr.mean_score, i.release_year, "
        "  img.url FROM core_behavior_events e "
        "JOIN core_items i ON i.item_id = e.item_id AND i.media_type = ? "
        "JOIN core_event_local_times l ON l.event_id = e.event_id "
        "JOIN core_ratings r ON r.item_id = e.item_id AND r.source = 'tmdb' "
        "LEFT JOIN core_community_ratings cr ON cr.item_id = e.item_id AND cr.source = 'tmdb' "
        "LEFT JOIN core_item_images img ON img.item_id = e.item_id AND img.source = 'tmdb' "
        "  AND img.kind = 'poster' "
        "WHERE e.source = 'tmdb' AND e.event_type = 'rated' "
        "AND l.local_date >= ? AND l.local_date < ? ORDER BY l.local_date, i.title",
        (media, start, end),
    ).fetchall()
    people: dict[int, list[str]] = {}
    if rows:
        marks = ",".join("?" for _ in rows)
        for item_id, name in conn.execute(
            "SELECT ic.item_id, c.name FROM core_item_creators ic JOIN core_creators c "
            "ON c.creator_id = ic.creator_id WHERE ic.source = 'tmdb' "
            f"AND ic.role IN ('director', 'creator') AND ic.item_id IN ({marks}) ORDER BY c.name",
            [r[0] for r in rows],
        ):
            people.setdefault(item_id, []).append(name)
    return [
        Rated(r[0], kind, r[1], r[2], r[3], r[4], r[5], r[6], people.get(r[0], [])) for r in rows
    ]


def recently_rated(conn: sqlite3.Connection, kind: str, limit: int = 30) -> list[Rated]:
    return list(reversed(rated(conn, kind, "0000-01-01", "9999-12-31")))[:limit]


# ---------------------------------------------------------------------------
# One month at a glance (the Dashboard card)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Glance:
    period: Period
    plays: int
    hours: Hours
    shows_finished: int
    top_artist: tuple[str, int] | None
    top_show: Finished | None  # my highest-scored finish
    books: int | None = None  # None when Hardcover was never synced
    films: int | None = None  # films rated; None when TMDB was never synced


def has_books(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM stg_hardcover_user_books LIMIT 1").fetchone() is not None


def has_tmdb(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM stg_tmdb_account_ratings LIMIT 1").fetchone() is not None


def glance(conn: sqlite3.Connection, period: Period) -> Glance:
    start, end = period.start, period.end
    finished = finished_shows(conn, start, end)
    scored = [f for f in shown(finished) if f.my_score is not None]
    artists = top_artists(conn, start, end, 1)
    return Glance(
        period=period,
        plays=play_count(conn, start, end),
        hours=music_hours(conn, start, end),
        shows_finished=len(finished),
        top_artist=artists[0] if artists else None,
        top_show=min(scored, key=lambda f: (-f.my_score, f.title)) if scored else None,
        books=len(books_read(conn, start, end)) if has_books(conn) else None,
        films=len(rated(conn, "movie", start, end)) if has_tmdb(conn) else None,
    )


def about_hours(hours: float) -> str:
    """ "about 41 hours": rounded, since the lengths behind it are partly estimated."""
    if hours < 1:
        return "under an hour"
    value = round(hours) if hours < 100 else round(hours, -1)
    return f"about {value:,.0f} {'hour' if value == 1 else 'hours'}"
