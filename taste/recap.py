"""Recaps: a month or a year in a short deck of cards ("Rev" in the app). Qt-free.

`build(conn, period)` picks the numbers and items for every card from core and the
report views. It's deterministic: the same data gives the same recap, and its
`fingerprint` changes only when a number on a card does. Drawing the cards is the
desktop app's job (taste/desktop/recap_cards.py).

Recaps make themselves: on launch and after each sync the app asks `due()` which
periods need building. A month's recap is first made on the first launch or sync
after the month ends, then rebuilt during the next LATE_DAYS days whenever its
numbers change (Last.fm takes scrobbles up to 14 days late, and MAL dates get typed
in afterwards). After that it's final; rebuilding by hand still works. Years work
the same way, made in January.

Wording comes from templates, never a language model, and numbers aren't shown more
exactly than they're known ("about 41 hours").
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from taste import activity, recommend, settings
from taste.activity import Period
from taste.config import data_dir
from taste.db import utc_now

LATE_DAYS = activity.LATE_DAYS
POSTERS_SHOWN = 12
TOP_SHOWN = 5


@dataclass
class Music:
    plays: int
    hours: float | None  # None when too few plays have a known length
    top_artists: list[tuple[str, int]]  # (artist, plays)
    top_artist_picture: str | None
    top_track: tuple[str, str, int] | None  # (track, artist, plays)
    top_album: tuple[str, str, int, str | None] | None  # (album, artist, plays, cover)
    new_artists: int
    top_new_artist: tuple[str, int] | None
    busiest_day: tuple[str, int] | None  # ('YYYY-MM-DD', plays)
    hours_by_clock: list[int] = field(default_factory=list)  # 24, local time
    days: list[tuple[str, int]] = field(default_factory=list)  # every day of the period


@dataclass
class Show:
    title: str
    poster_url: str | None
    my_score: float | None
    community_mean: float | None


@dataclass
class Anime:
    finished: int
    posters: list[Show]
    episodes: int
    hours: float | None  # None when episode lengths aren't known
    best: Show | None  # my highest-scored finish
    scored: int
    avg_diff: float | None  # my score minus the MAL mean, averaged
    above: int  # against the MAL mean rounded to a whole point
    level: int
    below: int
    top_genre: tuple[str, int] | None  # (genre, shows)


@dataclass
class Item:
    """A book, film, or show on a card: its title, picture, and my rating."""

    title: str
    picture: str | None
    mine: float | None
    crowd: float | None


@dataclass
class Books:
    read: int
    covers: list[Item]
    pages: int  # pages of the books with a known count
    unknown_pages: int  # books read with no page count
    best: Item | None
    rated: int
    avg_diff: float | None  # my stars minus the Hardcover average
    above: int  # against the Hardcover average rounded to the nearest half star
    level: int
    below: int
    top_author: tuple[str, int] | None


@dataclass
class Screen:
    """Films or TV rated in the period. TMDB has rating dates, not watch dates."""

    rated: int
    posters: list[Item]
    best: Item | None
    avg_diff: float | None  # my rating minus the TMDB average
    spread: list[int]  # how many of each rating, 0.5 to 10 in halves (20 counts)
    top_person: tuple[str, int] | None  # the most rated director (films) or creator (TV)
    top_decade: tuple[str, int] | None


@dataclass
class YearView:
    plays_by_month: list[int]
    shows_by_month: list[int]
    books_by_month: list[int]
    films_by_month: list[int]
    top_artists: list[tuple[str, int]]
    top_shows: list[Show]
    busiest_month: tuple[str, int] | None  # ('October', plays)
    first_finish: Show | None
    last_finish: Show | None


# The parts, grouped onto cards: the listening clock shares a card with the months
# of the year, films with TV, and the comparison with the closing line.
SLIDES = (
    ("cover", ("cover",)),
    ("music", ("music",)),
    ("when", ("clock", "year")),
    ("anime", ("anime",)),
    ("books", ("books",)),
    ("screen", ("films", "tv")),
    ("wrap", ("compare", "closing")),
)


@dataclass
class RecapData:
    period: Period
    profile_name: str
    so_far: bool  # the period isn't over yet
    cover_numbers: list[tuple[str, str]]  # (number, what it counts), in order
    music: Music | None
    anime: Anime | None
    books: Books | None
    films: Screen | None
    tv: Screen | None
    compare: list[str]  # plain sentences against the previous period
    year: YearView | None
    closing: str
    cards: list[str]  # card names in order: "cover", "music", "clock", ...
    monday_first: bool = False  # Rev's calendar, with the "Weeks start on" setting

    def to_json(self) -> dict:
        data = asdict(self)
        data["period"] = {"kind": self.period.kind, "key": self.period.key}
        return data

    @property
    def fingerprint(self) -> str:
        """A hash of everything on the cards, so a rebuild happens only for real changes."""
        text = json.dumps(self.to_json(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def slides(self) -> list[tuple[str, list[str]]]:
        """The cards to swipe through: (card name, the parts drawn on it), at most seven.
        A card none of whose parts has anything to show is left out."""
        return slides_for(self.cards)

    def file_names(self) -> list[str]:
        """The cards in the recap's own folder: "01-cover.png", "02-music.png", ..."""
        return [f"{i:02d}-{name}.png" for i, (name, _) in enumerate(self.slides, 1)]

    def save_name(self, index: int) -> str:
        """What Save names a card: "Taste Engine recap October 2026 (2 of 6).png"."""
        return f"Taste Engine recap {self.period.label} ({index + 1} of {len(self.slides)}).png"


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def _show(f: activity.Finished) -> Show:
    return Show(f.title, f.poster_url, f.my_score, f.community_mean)


def _music(conn: sqlite3.Connection, period: Period) -> Music | None:
    start, end = period.start, period.end
    plays = activity.play_count(conn, start, end)
    if not plays:
        return None
    artists = activity.top_artists(conn, start, end, TOP_SHOWN)
    tracks = activity.top_tracks(conn, start, end, 1)
    albums = activity.top_albums(conn, start, end, 1)
    new = activity.new_artists(conn, start, end)
    by_day = activity.plays_by_day(conn, start, end)
    busiest = max(by_day.items(), key=lambda d: (d[1], d[0])) if by_day else None
    return Music(
        plays=plays,
        hours=activity.music_hours(conn, start, end).hours,
        top_artists=artists,
        top_artist_picture=recommend.artist_picture(conn, artists[0][0]) if artists else None,
        top_track=tracks[0] if tracks else None,
        top_album=albums[0] if albums else None,
        new_artists=len(new),
        top_new_artist=new[0] if new else None,
        busiest_day=busiest,
        hours_by_clock=activity.plays_by_hour(conn, start, end),
        days=[(day, by_day.get(day, 0)) for day in period.days()],
    )


def _anime(conn: sqlite3.Connection, period: Period) -> Anime | None:
    finished = activity.finished_shows(conn, period.start, period.end)
    episodes = activity.episodes_watched(conn, period.start, period.end, finished)
    if not finished and not episodes.count:
        return None
    scored = [f for f in finished if f.my_score is not None]
    against = [f for f in scored if f.community_mean is not None]
    above, level, below = activity.whole_score_split(
        [(f.my_score, f.community_mean) for f in against]
    )
    genres = Counter(g for f in finished for g in f.genres)
    top_genre = min(genres.items(), key=lambda g: (-g[1], g[0])) if genres else None
    # Adult shows count above but are never named or pictured while hidden.
    visible = activity.shown(finished)
    best = min(
        (f for f in visible if f.my_score is not None), key=lambda f: (-f.my_score, f.title),
        default=None,
    )  # fmt: skip
    return Anime(
        finished=len(finished),
        posters=[_show(f) for f in sorted(visible, key=lambda f: (f.day, f.title), reverse=True)][
            :POSTERS_SHOWN
        ],
        episodes=episodes.count,
        hours=episodes.seconds / 3600 if episodes.seconds else None,
        best=_show(best) if best else None,
        scored=len(scored),
        avg_diff=round(sum(f.my_score - f.community_mean for f in against) / len(against), 2)
        if against
        else None,
        above=above,
        level=level,
        below=below,
        top_genre=top_genre,
    )


def _books(conn: sqlite3.Connection, period: Period) -> Books | None:
    read = activity.books_read(conn, period.start, period.end)
    if not read:
        return None
    rated = [b for b in read if b.my_rating is not None]
    against = [b for b in rated if b.average]
    above, level, below = activity.half_star_split([(b.my_rating, b.average) for b in against])
    authors = Counter(b.author for b in read if b.author)
    best = min(rated, key=lambda b: (-b.my_rating, b.title), default=None)
    return Books(
        read=len(read),
        covers=[Item(b.title, b.cover_url, b.my_rating, b.average) for b in reversed(read)][
            :POSTERS_SHOWN
        ],
        pages=sum(b.pages or 0 for b in read),
        unknown_pages=sum(1 for b in read if not b.pages),
        best=Item(best.title, best.cover_url, best.my_rating, best.average) if best else None,
        rated=len(rated),
        avg_diff=round(sum(b.my_rating - b.average for b in against) / len(against), 2)
        if against
        else None,
        above=above,
        level=level,
        below=below,
        top_author=min(authors.items(), key=lambda a: (-a[1], a[0])) if authors else None,
    )


def _screen(conn: sqlite3.Connection, period: Period, kind: str) -> Screen | None:
    found = activity.rated(conn, kind, period.start, period.end)
    if not found:
        return None
    against = [f for f in found if f.average]
    spread = [0] * 20
    for f in found:
        spread[min(max(int(round(f.my_rating * 2)) - 1, 0), 19)] += 1
    people = Counter(p for f in found for p in f.people)
    decades = Counter(f"{f.year // 10 * 10}s" for f in found if f.year)
    best = min(found, key=lambda f: (-f.my_rating, f.title))
    top_person = min(people.items(), key=lambda p: (-p[1], p[0])) if people else None
    return Screen(
        rated=len(found),
        posters=[Item(f.title, f.poster_url, f.my_rating, f.average) for f in reversed(found)][
            :POSTERS_SHOWN
        ],
        best=Item(best.title, best.poster_url, best.my_rating, best.average),
        avg_diff=round(sum(f.my_rating - f.average for f in against) / len(against), 2)
        if against
        else None,
        spread=spread,
        top_person=top_person if top_person and top_person[1] > 1 else None,
        top_decade=min(decades.items(), key=lambda d: (-d[1], d[0])) if decades else None,
    )


def change_sentence(now: float, before: float, what: str, previous: str) -> str | None:
    """'18% more listening than September.' None when there's nothing to compare."""
    if not now and not before:
        return None
    if not before:
        return f"{what.capitalize()}: {now:,.0f}, with none in {previous}."
    change = (now - before) / before
    if abs(change) < 0.05:
        return f"About as much {what} as {previous}."
    if change >= 1:
        times = now / before
        amount = "twice as much" if times < 2.5 else f"{times:.0f} times as much"
        return f"{amount.capitalize()} {what} as {previous}."
    direction = "more" if change > 0 else "less"
    return f"{abs(change):.0%} {direction} {what} than {previous}."


def _compare(conn: sqlite3.Connection, period: Period, music, anime, books, films) -> list[str]:
    previous = period.previous()
    before_plays = activity.play_count(conn, previous.start, previous.end)
    before_shows = len(activity.finished_shows(conn, previous.start, previous.end))
    name = previous.short if period.kind == "month" else f"{previous.key}"
    lines = [
        change_sentence(music.plays if music else 0, before_plays, "listening", name),
        _shows_sentence(anime.finished if anime else 0, before_shows, name),
    ]
    if activity.has_books(conn):
        before = len(activity.books_read(conn, previous.start, previous.end))
        lines.append(_count_sentence(books.read if books else 0, before, "book", "read", name))
    if activity.has_tmdb(conn):
        before = len(activity.rated(conn, "movie", previous.start, previous.end))
        lines.append(_count_sentence(films.rated if films else 0, before, "film", "rated", name))
    return [line for line in lines if line]


def _count_sentence(now: int, before: int, noun: str, verb: str, previous: str) -> str | None:
    """Like "2 more books read than September (5 against 3)."."""
    if not now and not before:
        return None
    if now == before:
        return f"The same number of {noun}s {verb} as {previous}: {now}."
    gap = abs(now - before)
    word = "more" if now > before else "fewer"
    things = noun if gap == 1 else f"{noun}s"
    return f"{gap} {word} {things} {verb} than {previous} ({now} against {before})."


def _shows_sentence(now: int, before: int, previous: str) -> str | None:
    if not now and not before:
        return None
    if now == before:
        return f"The same number of shows finished as {previous}: {now}."
    gap = abs(now - before)
    word = "more" if now > before else "fewer"
    shows = "show" if gap == 1 else "shows"
    return f"{gap} {word} {shows} finished than {previous} ({now} against {before})."


def _year(conn: sqlite3.Connection, period: Period) -> YearView | None:
    if period.kind != "year":
        return None
    months = period.months()
    plays = [activity.play_count(conn, m.start, m.end) for m in months]
    finished = activity.finished_shows(conn, period.start, period.end)
    shows = [sum(1 for f in finished if f.day[:7] == m.key) for m in months]
    read = activity.books_read(conn, period.start, period.end)
    films = activity.rated(conn, "movie", period.start, period.end)
    visible = activity.shown(finished)  # named on the card; every show is counted
    scored = sorted(
        (f for f in visible if f.my_score is not None),
        key=lambda f: (-f.my_score, f.title),
    )
    busiest = max(range(12), key=lambda i: (plays[i], -i)) if any(plays) else None
    return YearView(
        plays_by_month=plays,
        shows_by_month=shows,
        books_by_month=[sum(1 for b in read if b.day[:7] == m.key) for m in months],
        films_by_month=[sum(1 for f in films if f.day[:7] == m.key) for m in months],
        top_artists=activity.top_artists(conn, period.start, period.end, TOP_SHOWN),
        top_shows=[_show(f) for f in scored[:TOP_SHOWN]],
        busiest_month=(activity.MONTH_NAMES[busiest], plays[busiest])
        if busiest is not None
        else None,
        # The real first and last, or nothing: naming a later show "first" isn't true.
        first_finish=_show(finished[0]) if finished and not finished[0].hidden else None,
        last_finish=_show(finished[-1]) if finished and not finished[-1].hidden else None,
    )


def article(word: str) -> str:
    """ "A" or "An" by the first letter: good enough for titles in a sentence."""
    return "An" if word[:1].lower() in "aeiou" else "A"


def closing_sentence(period: Period, music: Music | None, anime: Anime | None) -> str:
    """One plain sentence from the highlights: "A Frieren month, with a lot of Radiohead"."""
    unit = period.kind
    show = anime.best.title if anime and anime.best else None
    artist = music.top_artists[0][0] if music and music.top_artists else None
    if show and artist:
        return f"{article(show)} {show} {unit}, with a lot of {artist}."
    if artist:
        return f"A {unit} with a lot of {artist}."
    if show:
        return f"{article(show)} {show} {unit}."
    return f"A quiet {unit}."


def build(conn: sqlite3.Connection, period: Period, now: datetime | None = None) -> RecapData:
    music = _music(conn, period)
    anime = _anime(conn, period)
    books = _books(conn, period)
    films = _screen(conn, period, "movie")
    tv = _screen(conn, period, "tv")
    so_far = activity.today(conn, now).isoformat() < period.end
    plays = music.plays if music else 0

    def counted(n: int, one: str, many: str) -> list[tuple[str, str]]:
        return [(f"{n:,}", one if n == 1 else many)] if n else []  # no "0 shows finished"

    cover = counted(anime.finished if anime else 0, "show finished", "shows finished")
    if music and music.hours:
        cover.append((f"~{music.hours:,.0f}", "hours of music"))
    cover += counted(plays, "play", "plays")
    cover += counted(books.read if books else 0, "book read", "books read")
    cover += counted(films.rated if films else 0, "film rated", "films rated")
    cover += counted(anime.episodes if anime else 0, "episode", "episodes")
    cover = cover or [("0", "plays")]  # a quiet month still says so
    year = _year(conn, period)
    cards = ["cover"]
    if music:
        cards += ["music", "clock"]
    if anime:
        cards.append("anime")
    for name, part in (("books", books), ("films", films), ("tv", tv)):
        if part:
            cards.append(name)
    compare = _compare(conn, period, music, anime, books, films)
    if compare:
        cards.append("compare")
    if year and any(
        any(v)
        for v in (
            year.plays_by_month,
            year.shows_by_month,
            year.books_by_month,
            year.films_by_month,
        )
    ):
        cards.append("year")
    cards.append("closing")
    return RecapData(
        period=period,
        profile_name=settings.get(conn, "display_name") or "",
        so_far=so_far,
        cover_numbers=cover,
        music=music,
        anime=anime,
        books=books,
        films=films,
        tv=tv,
        compare=compare,
        year=year,
        closing=closing_sentence(period, music, anime),
        monday_first=settings.get(conn, "week_start") == "monday",
        cards=cards,
    )


def has_data(data: RecapData) -> bool:
    parts = (data.music, data.anime, data.books, data.films, data.tv)
    return any(part is not None for part in parts)


# ---------------------------------------------------------------------------
# Making them by themselves
# ---------------------------------------------------------------------------

KIND_SETTINGS = {"month": "recap_monthly", "year": "recap_yearly"}


def folder(profile_id: str, period: Period) -> Path:
    """Where a recap's images and recap.json go: <data folder>/recaps/<profile>/<period>."""
    return data_dir() / "recaps" / profile_id / period.key


def _window_ends(period: Period) -> date:
    return date.fromisoformat(period.end) + timedelta(days=LATE_DAYS)


def due(conn: sqlite3.Connection, now: datetime | None = None) -> list[Period]:
    """The periods to build or check now: the last finished month (and year), unless
    switched off or already final. Past the late-data window a made recap turns final.

    Only the latest finished period is considered, so a first launch doesn't make a
    recap for every month in the history; older ones can be made by hand.
    """
    today = activity.today(conn, now)
    found = []
    for kind, key in KIND_SETTINGS.items():
        if not settings.get(conn, key):
            continue
        period = activity.current(conn, kind, now).previous()
        row = conn.execute(
            "SELECT final FROM recap_runs WHERE period_kind = ? AND period_key = ?",
            (kind, period.key),
        ).fetchone()
        if row and row[0]:
            continue
        if today >= _window_ends(period):
            if row:
                mark_final(conn, period)
                continue
            # Never made (the app wasn't opened in time): make it once, already final,
            # unless more than a month has gone by since it ended. Then it's left for
            # making by hand, so a first launch in October doesn't announce last year.
            if today.isoformat() >= Period("month", period.end[:7]).next().start:
                continue
        found.append(period)
    return found


def record(
    conn: sqlite3.Connection,
    data: RecapData,
    now: datetime | None = None,
    announced: bool = False,
    missing: int | None = None,
) -> bool:
    """Note that a recap was made (or rebuilt). Returns True the first time.

    `announced`: made by hand, so the Dashboard banner shouldn't say it's ready later.
    `missing`: how many pictures were placeholders when it was drawn (None: not drawn
    now, keep the count from the last drawing).
    """
    period = data.period
    new = (
        conn.execute(
            "SELECT 1 FROM recap_runs WHERE period_kind = ? AND period_key = ?",
            (period.kind, period.key),
        ).fetchone()
        is None
    )
    final = activity.today(conn, now) >= _window_ends(period)
    conn.execute(
        "INSERT INTO recap_runs (period_kind, period_key, built_at, fingerprint, cards, final, "
        "announced, pictures_missing) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (period_kind, period_key) DO UPDATE SET "
        "built_at = excluded.built_at, fingerprint = excluded.fingerprint, "
        "cards = excluded.cards, final = excluded.final"
        + ("" if missing is None else ", pictures_missing = excluded.pictures_missing"),
        (
            period.kind,
            period.key,
            utc_now(),
            data.fingerprint,
            len(data.cards),
            int(final),
            int(announced),
            missing or 0,
        ),
    )
    return new


def stored_fingerprint(conn: sqlite3.Connection, period: Period) -> str | None:
    row = conn.execute(
        "SELECT fingerprint FROM recap_runs WHERE period_kind = ? AND period_key = ?",
        (period.kind, period.key),
    ).fetchone()
    return row[0] if row else None


def pictures_missing(conn: sqlite3.Connection, period: Period) -> int:
    """How many pictures were placeholders when the recap was last drawn."""
    row = conn.execute(
        "SELECT pictures_missing FROM recap_runs WHERE period_kind = ? AND period_key = ?",
        (period.kind, period.key),
    ).fetchone()
    return row[0] if row else 0


def mark_final(conn: sqlite3.Connection, period: Period) -> None:
    conn.execute(
        "UPDATE recap_runs SET final = 1 WHERE period_kind = ? AND period_key = ?",
        (period.kind, period.key),
    )


def unannounced(conn: sqlite3.Connection) -> list[Period]:
    """Recaps made by themselves that the Dashboard banner hasn't mentioned yet."""
    return [
        Period(r[0], r[1])
        for r in conn.execute(
            "SELECT period_kind, period_key FROM recap_runs WHERE announced = 0 "
            "ORDER BY period_key DESC"
        )
    ]


def mark_announced(conn: sqlite3.Connection, period: Period) -> None:
    conn.execute(
        "UPDATE recap_runs SET announced = 1 WHERE period_kind = ? AND period_key = ?",
        (period.kind, period.key),
    )


def slides_for(parts: list[str]) -> list[tuple[str, list[str]]]:
    """(card name, its parts) for the parts a recap has, in SLIDES order."""
    found = []
    for name, card_parts in SLIDES:
        on_card = [part for part in card_parts if part in parts]
        if on_card:
            found.append((name, on_card))
    return found


SAVED_NAMES = "saved_cards.json"  # in a recap's own folder: the deck its saved cards are from


def remember_saved_names(folder: Path, names: list[str]) -> None:
    """Note the card names of the deck Save wrote from, so a Rebuild can tell which saved
    card is which (the page's drawing can differ from the one in recap.json)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SAVED_NAMES).write_text(json.dumps({"cards": names}), encoding="utf-8")
    except OSError:
        pass  # only a better guess is lost: Rebuild falls back to recap.json


def saved_card_names(folder: Path) -> list[str] | None:
    """The card names of the deck this recap's saved cards came from: noted at Save, else
    the drawing last written to `folder` (its recap.json). None if neither can be read."""
    try:
        names = json.loads((folder / SAVED_NAMES).read_text(encoding="utf-8"))["cards"]
        if isinstance(names, list) and all(isinstance(n, str) for n in names):
            return names
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        parts = json.loads((folder / "recap.json").read_text(encoding="utf-8"))["cards"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return [name for name, _ in slides_for(parts)] if isinstance(parts, list) else None


def write_json(data: RecapData, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    path = target / "recap.json"
    path.write_text(json.dumps(data.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Past recaps: the first launch offers the months and years before (2.3)
# ---------------------------------------------------------------------------

PAST_STATE = "past_recaps"  # '' never offered, 'making', 'done', or 'declined'
SECONDS_PER_RECAP = 1.3  # drawing one, measured on the made-up library (2026-10-19)


def past_state(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT state_value FROM app_state WHERE state_key = ?", (PAST_STATE,)
    ).fetchone()
    return row[0] if row else ""


def set_past_state(conn: sqlite3.Connection, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (state_key, state_value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (state_key) DO UPDATE SET state_value = excluded.state_value, "
        "updated_at = excluded.updated_at",
        (PAST_STATE, value, utc_now()),
    )


def past_periods(conn: sqlite3.Connection, now: datetime | None = None) -> list[Period]:
    """Every finished month and year with something in it that has no recap yet, oldest
    first (a year after its December). Months and years follow their Recaps settings.
    "Something in it" is any event that month; some turn out to have nothing to recap
    (an old list-edit guess only), and the drawing skips those."""
    months = sorted(
        {
            r[0]
            for r in conn.execute(
                "SELECT local_month FROM core_event_local_times UNION "
                "SELECT substr(occurred_at_utc, 1, 7) FROM core_behavior_events "
                "WHERE occurred_at_unix IS NULL"
            )
            if r[0]
        }
    )
    this_month = activity.current(conn, "month", now)
    this_year = activity.current(conn, "year", now)
    found: list[Period] = []
    if settings.get(conn, "recap_monthly"):
        found += [Period("month", m) for m in months if m < this_month.key]
    if settings.get(conn, "recap_yearly"):
        found += [Period("year", y) for y in sorted({m[:4] for m in months}) if y < this_year.key]
    made = {(r[0], r[1]) for r in conn.execute("SELECT period_kind, period_key FROM recap_runs")}
    found = [p for p in found if (p.kind, p.key) not in made]
    return sorted(found, key=lambda p: (p.end, p.kind == "year"))


def past_offer(conn: sqlite3.Connection, now: datetime | None = None) -> list[Period]:
    """What to offer to make, or [] when it was offered already (or there's nothing).
    Offered once per profile, to profiles that have recaps already too (only the rest)."""
    if past_state(conn):
        return []
    return past_periods(conn, now)


def offer_text(periods: list[Period]) -> str:
    """ "Make recaps for your past months? 31 months and 3 years, about a minute." """
    months = sum(1 for p in periods if p.kind == "month")
    years = len(periods) - months
    parts = [
        f"{n} {word}{'' if n == 1 else 's'}"
        for n, word in ((months, "month"), (years, "year"))
        if n
    ]
    seconds = len(periods) * SECONDS_PER_RECAP
    minutes = round(seconds / 60)
    when = (
        "under a minute"
        if seconds < 60
        else ("about a minute" if minutes <= 1 else f"about {minutes} minutes")
    )
    return f"Make recaps for your past months? {' and '.join(parts)}, {when}."


def banner_text(period: Period) -> str:
    """ "Your October recap is ready." The month's name alone, as people say it."""
    return f"Your {period.short} recap is ready."
