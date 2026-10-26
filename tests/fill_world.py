"""A made-up profile the size of a real library, and a way to measure empty space.

Everything is invented: titles, artists, scores, and plays come from a seeded
random generator, so every run builds the same world. The data goes in through the
real sync and recommendation code over fake APIs, like the other desktop tests.

`bare_share` is the "fill every page" check: the share of a page, in 40 px squares,
that is nothing but window background.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from taste import credentials
from taste.desktop import theme
from taste.desktop.context import AppContext
from taste.desktop.main_window import MainWindow
from taste.http_client import HttpClient
from taste.secrets_store import SecretStore
from taste.sources.hardcover_auth import Tokens
from tests import fake_hardcover, fake_recs, fake_tmdb
from tests.conftest import FakeKeyring, FakeTransport
from tests.fake_recs import FakeDeezer, FakeLastfmApi, FakeMal, anime, scrobble
from tests.test_lastfm_sync import FakeLastfm

NOW_DT = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
NOW = int(NOW_DT.timestamp())
DAY = 86400
SQUARE = 40

WORDS_A = [
    "Starlit",
    "Iron",
    "Paper",
    "Velvet",
    "Hollow",
    "Crimson",
    "Quiet",
    "Neon",
    "Glass",
    "Summer",
    "Winter",
    "Silver",
    "Lunar",
    "Copper",
    "Midnight",
    "Electric",
    "Distant",
    "Gentle",
    "Amber",
    "Cobalt",
    "Ashen",
    "Golden",
]
WORDS_B = [
    "Garden",
    "Frontier",
    "Requiem",
    "Academy",
    "Signal",
    "Harbor",
    "Orbit",
    "Lantern",
    "Tide",
    "Circuit",
    "Kingdom",
    "Parade",
    "Atlas",
    "Rain",
    "Compass",
    "Engine",
    "Letters",
    "Season",
    "Choir",
    "Voyage",
    "Archive",
]
GENRES = [
    "Action", "Adventure", "Comedy", "Drama", "Fantasy", "Mystery", "Psychological",
    "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Mecha", "Suspense",
    "Music", "Horror", "Historical", "School", "Military", "Ecchi",
]  # fmt: skip
STUDIOS = ["Studio Alpha", "Studio Beta", "Studio Gamma", "Studio Delta", "Studio Epsilon"]


class World:
    """The fake MAL and Last.fm behind one made-up library."""

    def __init__(self, seed: int = 11, shows: int = 400, plays: int = 20_000) -> None:
        rng = random.Random(seed)
        self.rng = rng
        bias = {g: rng.uniform(-1.3, 0.7) for g in GENRES}
        names = [f"{a} {b}{part}" for a in WORDS_A for b in WORDS_B for part in ("", " II")]
        titles = rng.sample(names, shows + 150)
        entries, details = [], {}
        for i in range(shows):
            node = self._node(i + 1, titles[i])
            status = (
                "completed" if i < 360 else rng.choice(("watching", "plan_to_watch", "dropped"))
            )
            if status == "watching":
                node["status"] = "currently_airing"
                node["broadcast"] = {"day_of_the_week": "saturday", "start_time": "17:30"}
            score = 0
            if i < 343:  # 343 of the 400 are scored
                genre_bias = sum(bias[g["name"]] for g in node["genres"]) / len(node["genres"])
                score = max(1, min(10, round(node["mean"] - 0.6 + genre_bias + rng.gauss(0, 0.9))))
            finished = NOW_DT - timedelta(days=rng.randint(1, 1500))
            if i < 12:  # a few this month, for the "this month" numbers
                finished = NOW_DT - timedelta(days=rng.randint(0, 12))
            status_fields = {
                "status": status,
                "score": score,
                "num_episodes_watched": node["num_episodes"] if status == "completed" else 3,
                "is_rewatching": False,
                "updated_at": finished.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            }
            if status == "completed":
                status_fields["finish_date"] = finished.strftime("%Y-%m-%d")
            entries.append({"node": dict(node), "list_status": status_fields})
            details[node["id"]] = node
        scored = [e for e in entries if e["list_status"]["score"]]
        average = sum(e["list_status"]["score"] for e in scored) / len(scored)
        candidates = list(range(2001, 2151))
        for offset, cid in enumerate(candidates):
            details[cid] = self._node(cid, titles[shows + offset])
        for e in scored:
            if e["list_status"]["score"] > average:
                node = dict(details[e["node"]["id"]])
                node["recommendations"] = [
                    {"node": {"id": c, "title": details[c]["title"]},
                     "num_recommendations": rng.randint(1, 60)}
                    for c in rng.sample(candidates, rng.randint(3, 8))
                ]  # fmt: skip
                node["related_anime"] = []
                details[node["id"]] = node
        self.mal = FakeMal(entries, details)

        art_a = [
            "Night",
            "Paper",
            "Velvet",
            "Static",
            "Glass",
            "Hollow",
            "Coral",
            "Lilac",
            "Neon",
            "Distant",
            "Summer",
        ]
        art_b = [
            "Ferry",
            "Satellites",
            "Arcade",
            "Bloom",
            "Choir",
            "Harbor",
            "Motel",
            "Garden",
            "Signal",
            "Tapes",
            "Club",
        ]
        artists = rng.sample([f"{a} {b}" for a in art_a for b in art_b], 80)
        played, fresh = artists[:50], artists[50:]
        albums = {a: [f"{rng.choice(WORDS_A)} Hours {n}" for n in range(1, 4)] for a in played}
        weights = [1 / (r + 1) ** 0.9 for r in range(len(played))]
        seen, history = set(), []
        while len(history) < plays:
            artist = rng.choices(played, weights)[0]
            stamp = NOW - rng.randint(60, 900 * DAY)
            track = f"{rng.choice(WORDS_B)} {rng.randint(1, 14)}"
            if (stamp, artist, track) in seen:
                continue
            seen.add((stamp, artist, track))
            history.append(scrobble(artist, track, rng.choice(albums[artist]), stamp, "c"))
        self.recent = FakeLastfm()
        self.recent.scrobbles = history
        self._similar = {
            a: [(s, round(rng.uniform(0.3, 1.0), 2)) for s in rng.sample(fresh, 6)] for a in played
        }
        self._top_albums = {f: (f"{f} Weather", None) for f in fresh}
        self.music = FakeLastfmApi(self.recent)
        self.photos = FakeDeezer()
        self._books()
        self._films()

    def _books(self) -> None:
        """150 made-up books in a Hardcover library: 130 read (a few twice), the rest
        wanted, with half-star ratings, genres, moods, and some audiobooks."""
        rng = self.rng
        authors = [(20 + i, f"{a} Example") for i, a in enumerate(
            ("Ada", "Ben", "Cy", "Dee", "Eve", "Fay", "Gus"))]  # fmt: skip
        genres = ["Fantasy", "Science Fiction", "Mystery", "Young Adult", "History", "Romance"]
        moods = ["adventurous", "emotional", "dark", "funny", "hopeful", "tense"]
        library = []
        for i in range(150):
            read = i < 130
            days = rng.randint(0, 10) if i < 3 else rng.randint(0, 1400)
            day = (NOW_DT - timedelta(days=days)).strftime("%Y-%m-%d")
            audio = i % 11 == 0
            the_book = fake_hardcover.book(
                5000 + i,
                f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} Book {i}",
                rng.choice(authors),
                pages=rng.randint(120, 640),
                year=rng.randint(1990, 2025),
                rating=round(rng.uniform(3.2, 4.5), 2),
                fiction=i % 9 != 0,
                genres=rng.sample(genres, 2),
                moods=rng.sample(moods, 2),
                audio_seconds=rng.randint(6, 20) * 3600 if audio else None,
                similar=rng.sample(range(8000, 8040), 5),
            )
            reads = [(90000 + i, None, day)] if read else []
            if read and i % 25 == 0:  # a few rereads
                again = (NOW_DT - timedelta(days=rng.randint(0, 300))).strftime("%Y-%m-%d")
                reads.append((95000 + i, None, again))
            edition = {"id": 7000 + i, "pages": None, "audio_seconds": None,
                       "reading_format_id": 2 if audio else 1}  # fmt: skip
            library.append(
                fake_hardcover.entry(
                    6000 + i,
                    the_book,
                    status=3 if read else 1,
                    rating=rng.randint(1, 10) / 2 if read else None,
                    reads=reads,
                    edition=edition,
                )
            )
        self.hardcover = fake_hardcover.FakeHardcover(library)
        # Books I don't have, that the ones I read point at (For You's Books tab).
        self.hardcover.catalog = {
            8000 + i: fake_hardcover.book(
                8000 + i,
                f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} Pick {i}",
                rng.choice(authors),
                rating=round(rng.uniform(3.3, 4.6), 2),
                genres=rng.sample(genres, 2),
                moods=rng.sample(moods, 2),
            )
            for i in range(40)
        }

    def _films(self) -> None:
        """120 rated films and 30 rated shows on a made-up TMDB account."""
        rng = self.rng
        genres = ["Drama", "Comedy", "Thriller", "Science Fiction", "Romance", "Animation"]
        directors = [f"{n} Director" for n in ("Ida", "Jo", "Kay", "Lu", "Mo", "Ned")]
        catalog, ratings = {}, []
        for i in range(120):
            fid = 100 + i
            recs = rng.sample(range(400, 460), 4)
            catalog[("movie", fid)] = fake_tmdb.movie(
                fid, f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} Film {i}",
                round(rng.uniform(5.5, 8.4), 1), rng.sample(genres, 2), rng.choice(directors),
                year=rng.randint(1975, 2026), recs=recs,
            )  # fmt: skip
            when = NOW_DT - timedelta(days=rng.randint(0, 900) if i >= 4 else rng.randint(0, 10))
            stamp = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            ratings.append(("movie", fid, rng.randint(2, 20) / 2, stamp))
        for i in range(60):
            cid = 400 + i
            catalog[("movie", cid)] = fake_tmdb.movie(
                cid, f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} Pick {i}",
                round(rng.uniform(6.0, 8.6), 1), rng.sample(genres, 2), rng.choice(directors),
                year=rng.randint(1990, 2026),
            )  # fmt: skip
        for i in range(30):
            tid = 700 + i
            catalog[("tv", tid)] = fake_tmdb.tv(
                tid, f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} Series {i}",
                round(rng.uniform(6.0, 8.8), 1), rng.sample(genres, 2), "Ola Writer",
            )  # fmt: skip
            when = NOW_DT - timedelta(days=rng.randint(0, 900))
            stamp = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            ratings.append(("tv", tid, rng.randint(4, 20) / 2, stamp))
        # TV candidates use invented details without changing the random library above.
        for i in range(700, 730):
            catalog[("tv", i)]["_recs"] = list(range(800, 830))
        for i in range(800, 830):
            catalog[("tv", i)] = fake_tmdb.tv(
                i, f"Example Suggested Series {i}", 7.5, ["Drama"], "Ola Writer"
            )
        # Cover anime films in both my ratings and suggestions, without shifting the RNG.
        for fid in (104, 401, 404, 406):
            catalog[("movie", fid)]["original_language"] = "ja"
        self.tmdb = fake_tmdb.FakeTmdb(
            ratings, catalog=catalog, watchlist=[("movie", 401)], favorites=[]
        )

    def install_extras(self, win, folder) -> None:
        """Connect the fake TMDB and Hardcover accounts, before the first sync."""
        store, pid = win.ctx.store, win.current_profile()
        store.set(pid, "TMDB_READ_TOKEN", fake_tmdb.READ_TOKEN)
        store.set(pid, "TMDB_USER_TOKEN", fake_tmdb.USER_TOKEN)
        store.set(pid, "TMDB_ACCOUNT_ID", fake_tmdb.ACCOUNT)
        credentials.save_hardcover(
            pid,
            store,
            Tokens(fake_hardcover.ACCESS, fake_hardcover.REFRESH, "2099-01-01 00:00:00"),
        )

    def _node(self, anime_id: int, title: str) -> dict:
        rng = self.rng
        return anime(
            anime_id,
            title,
            round(rng.uniform(6.3, 8.9), 2),
            rng.sample(GENRES, rng.choice((1, 2, 2, 3))),
            [rng.choice(STUDIOS)],
            raters=rng.randint(8_000, 900_000),
            year=rng.randint(2006, 2026),
            episodes=rng.choice((12, 12, 13, 24, 25)),
        )

    def handler(self, url, params):
        if "themoviedb" in url:
            return self.tmdb(url, params)
        if "hardcover" in url:
            return self.hardcover(url, params)
        if "deezer" in url:
            return self.photos(url, params)
        if "myanimelist" in url:
            return self.mal(url, params)
        return self.music(url, params)

    def client_factory(self, secrets):
        return HttpClient(
            FakeTransport(self.handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    def install(self, monkeypatch) -> None:
        """Point the shared fake Last.fm similar-artist tables at this world."""
        monkeypatch.setattr(fake_recs, "SIMILAR", self._similar)
        monkeypatch.setattr(fake_recs, "TOP_ALBUMS", self._top_albums)

    def window(self, image_session) -> MainWindow:
        ctx = AppContext(
            store=SecretStore(backend=FakeKeyring()),
            client_factory=self.client_factory,
            sync_kwargs={"now": lambda: NOW, "page_limit": 1000},
            recs_kwargs={"now": NOW_DT},
            image_session=image_session,
            clock=lambda: NOW_DT,
        )
        return MainWindow(ctx)


def bare_share(widget: QWidget, background: str = theme.BG) -> float:
    """The share of `widget`, in SQUARE px squares, that is only background color.

    Squares cut off at the right or bottom edge count by their area.
    """
    return empty_share(widget, (background,))


CHART_MARK = "#FF00FF"  # never a color the app draws


def empty_share(
    widget: QWidget,
    colors: tuple[str, ...] = (theme.BG, theme.SURFACE),
    ignore: tuple[type, ...] = (),
) -> float:
    """The share of `widget`, in SQUARE px squares, that is one flat color from `colors`:
    by default bare window background or the inside of a card with nothing in it.

    Until 2.1 the test counted only bare background, so a card stretched over empty
    space passed. Squares cut off at the right or bottom edge count by their area.
    """
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    width, height = image.width(), image.height()
    if not width or not height:
        return 0.0
    if ignore:
        # A chart's own open space (above short bars, between points) is part of reading
        # it, not empty layout: its area is painted over so it never counts.
        painter = QPainter(image)
        try:
            for kind in ignore:
                for child in widget.findChildren(kind):
                    if child.isVisible() and child.window() is widget.window():
                        top_left = child.mapTo(widget, QPoint(0, 0))
                        painter.fillRect(QRect(top_left, child.size()), QColor(CHART_MARK))
        finally:
            painter.end()
    solids = []
    for color in colors:
        solid = QImage(SQUARE, SQUARE, QImage.Format.Format_RGB32)
        solid.fill(QColor(color))
        solids.append(solid)
    empty = 0
    for top in range(0, height, SQUARE):
        for left in range(0, width, SQUARE):
            piece = image.copy(left, top, SQUARE, SQUARE)
            w, h = min(SQUARE, width - left), min(SQUARE, height - top)
            for solid in solids:
                if w == SQUARE and h == SQUARE:
                    same = piece == solid
                else:
                    same = piece.copy(0, 0, w, h) == solid.copy(0, 0, w, h)
                if same:
                    empty += w * h
                    break
    return empty / (width * height)
