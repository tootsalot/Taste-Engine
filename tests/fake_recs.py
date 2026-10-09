"""Made-up MAL and Last.fm data for the enrichment and recommendation tests.

Every title, artist, and number here is invented. The numbers are picked so the
predicted scores can be worked out by hand (see test_recommend.py).
"""

from __future__ import annotations

import re
from typing import Any

from tests.conftest import FakeResponse

MAL_CDN = "https://cdn.myanimelist.net/images/anime/1/{}.jpg"
LASTFM_CDN = "https://lastfm.freetls.fastly.net/i/u/300x300/{}.png"


_IDS: dict[str, int] = {}


def _id(name: str) -> int:
    """A stable made-up id per genre or studio name."""
    return _IDS.setdefault(name, len(_IDS) + 1)


def anime(
    anime_id: int,
    title: str,
    mean: float | None,
    genres: list[str],
    studios: list[str],
    *,
    media_type: str = "tv",
    raters: int = 100_000,
    year: int = 2020,
    episodes: int = 12,
    nsfw: str = "white",
    recommendations: list[tuple[int, str, int]] = (),
    related: list[tuple[int, str, str]] = (),
) -> dict[str, Any]:
    """One anime node, shaped like a MAL list node or details response."""
    node = {
        "id": anime_id,
        "title": title,
        "alternative_titles": {"synonyms": [], "en": "", "ja": ""},
        "media_type": media_type,
        "num_episodes": episodes,
        "start_season": {"year": year, "season": "fall"},
        "genres": [{"id": _id(g), "name": g} for g in genres],
        "studios": [{"id": _id(s), "name": s} for s in studios],
        "mean": mean,
        "num_scoring_users": raters,
        "status": "finished_airing",
        "main_picture": {"medium": MAL_CDN.format(anime_id), "large": MAL_CDN.format(anime_id)},
        "nsfw": nsfw,
    }
    if recommendations or related:
        node["recommendations"] = [
            {"node": {"id": i, "title": t}, "num_recommendations": votes}
            for i, t, votes in recommendations
        ]
        node["related_anime"] = [
            {"node": {"id": i, "title": t}, "relation_type": kind} for i, t, kind in related
        ]
    return node


def list_entry(node: dict[str, Any], status: str, score: int = 0) -> dict[str, Any]:
    list_node = {k: v for k, v in node.items() if k not in ("recommendations", "related_anime")}
    return {
        "node": list_node,
        "list_status": {
            "status": status,
            "score": score,
            "num_episodes_watched": node["num_episodes"] if status == "completed" else 0,
            "is_rewatching": False,
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    }


# ---------------------------------------------------------------------------
# The small hand-checked anime world
# ---------------------------------------------------------------------------
#
# Scored shows (my average is 7.25):
#   101 Drama A     me 9, MAL 8.0  Drama          Studio Alpha
#   102 Comedy B    me 5, MAL 7.0  Comedy         Studio Beta
#   103 Mixed C     me 7, MAL 7.5  Drama, Comedy  Studio Alpha
#   106 Action F    me 8, MAL 7.6  Action         Studio Beta
# Unscored: 104 Plan Show (plan to watch), 105 Watching Show (watching).
#
# Shows I liked (above 7.25): 101 and 106. Their details point at candidates
# 201 to 206, plus a few shows already on my list.

LIST = [
    list_entry(anime(101, "Example Drama A", 8.0, ["Drama"], ["Studio Alpha"]), "completed", 9),
    list_entry(anime(102, "Example Comedy B", 7.0, ["Comedy"], ["Studio Beta"]), "completed", 5),
    list_entry(
        anime(103, "Example Mixed C", 7.5, ["Drama", "Comedy"], ["Studio Alpha"]), "completed", 7
    ),
    list_entry(anime(104, "Example Plan Show", 7.8, ["Drama"], ["Studio Gamma"]), "plan_to_watch"),
    list_entry(anime(105, "Example Watching Show", 7.0, ["Comedy"], []), "watching"),
    list_entry(anime(106, "Example Action F", 7.6, ["Action"], ["Studio Beta"]), "completed", 8),
]

DETAILS = {
    101: anime(
        101,
        "Example Drama A",
        8.0,
        ["Drama"],
        ["Studio Alpha"],
        recommendations=[
            (201, "Example Candidate One", 20),
            (202, "Example Small Movie", 5),
            (105, "Example Watching Show", 3),
            (104, "Example Plan Show", 4),
        ],
        related=[
            (203, "Example Drama A Season 2", "sequel"),
            (204, "Example Drama A Recap", "summary"),
            (102, "Example Comedy B", "prequel"),
        ],
    ),
    106: anime(
        106,
        "Example Action F",
        7.6,
        ["Action"],
        ["Studio Beta"],
        recommendations=[
            (201, "Example Candidate One", 7),
            (205, "Example Music Video", 2),
            (206, "Example Adult Show", 1),
        ],
    ),
    201: anime(201, "Example Candidate One", 8.2, ["Drama"], ["Studio Alpha"]),
    202: anime(202, "Example Small Movie", 7.9, ["Comedy"], [], media_type="movie", raters=3000),
    203: anime(203, "Example Drama A Season 2", 8.0, ["Drama"], ["Studio Beta"], year=2022),
    204: anime(204, "Example Drama A Recap", 6.0, ["Drama"], []),
    205: anime(205, "Example Music Video", 7.0, ["Music"], [], media_type="music"),
    206: anime(206, "Example Adult Show", 7.5, ["Hentai"], [], nsfw="black"),
}


class FakeMal:
    """MAL list and details endpoints over the data above. Records detail requests."""

    def __init__(self, entries=None, details=None, missing=()):
        self.entries = LIST if entries is None else entries
        self.details = DETAILS if details is None else details
        self.missing = set(missing)  # ids that answer 404
        self.detail_calls: list[int] = []

    def __call__(self, url: str, params: dict[str, Any]) -> FakeResponse:
        if "/animelist" in url:
            return FakeResponse(200, {"data": self.entries, "paging": {}})
        match = re.search(r"/anime/(\d+)$", url)
        if match:
            anime_id = int(match.group(1))
            self.detail_calls.append(anime_id)
            if anime_id in self.missing or anime_id not in self.details:
                return FakeResponse(404, {"error": "not_found"})
            return FakeResponse(200, self.details[anime_id])
        return FakeResponse(404, {"error": "not_found"})


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------

NOW = 1791500000  # "now" in the music tests (October 2026)
DAY = 86400


def scrobble(artist: str, track: str, album: str, uts: int, cover: str | None = None) -> dict:
    image = [{"size": "extralarge", "#text": LASTFM_CDN.format(cover) if cover else ""}]
    return {
        "artist": {"mbid": "", "#text": artist},
        "name": track,
        "album": {"mbid": "", "#text": album},
        "image": image,
        "url": f"https://www.last.fm/music/{artist}/_/{track}",
        "date": {"uts": str(uts), "#text": ""},
    }


def listening_history() -> list[dict]:
    """Plays chosen so the recency weights come out round.

    - Example Seed One: 10 plays right now, weight 10.
    - Example Seed Two: 5 plays exactly 90 days ago, weight 5 * 0.5 = 2.5.
    - Example Old Favorite: 20 plays 400 days ago (outside the 12-month seed window).
    - Example Faded: 14 plays 300 days ago (one short of rediscover's 15).
    - Example Still Around: 19 plays 300 days ago and 1 play 10 days ago.
    """
    plays = []
    plays += [
        scrobble("Example Seed One", f"Song {i}", "Seed One LP", NOW, "seedone") for i in range(10)
    ]
    plays += [scrobble("Example Seed Two", f"Tune {i}", "", NOW - 90 * DAY) for i in range(5)]
    plays += [
        scrobble("Example Old Favorite", f"Classic {i}", "Old Days", NOW - 400 * DAY, "olddays")
        for i in range(20)
    ]
    plays += [scrobble("Example Faded", f"Faded {i}", "", NOW - 300 * DAY) for i in range(14)]
    plays += [
        scrobble("Example Still Around", f"Around {i}", "", NOW - 300 * DAY) for i in range(19)
    ]
    plays.append(scrobble("Example Still Around", "Around Again", "", NOW - 10 * DAY))
    return plays


SIMILAR = {
    "Example Seed One": [
        ("Example New X", 0.8),
        ("Example New Y", 0.5),
        ("Example Seed Two", 0.9),  # already played: never suggested
        ("Example Dismissed Z", 0.7),
    ],
    "Example Seed Two": [("Example New X", 0.6), ("Example New W", 1.0)],
}

TOP_ALBUMS = {"Example New X": ("X Marks", "newx"), "Example New Y": ("Y Not", None)}


DEEZER_PHOTO = "https://cdn-images.dzcdn.net/images/artist/{}/250x250-000000-80-0-0.jpg"
# What Deezer returns for an artist with no photo: the same URL with an empty hash.
DEEZER_NO_PHOTO = "https://cdn-images.dzcdn.net/images/artist//250x250-000000-80-0-0.jpg"


class FakeDeezer:
    """Deezer's artist search. `artists` maps a query to [(name, photo hash or None)]."""

    def __init__(self, artists=None, error=None):
        self.artists = artists or {}
        self.error = error
        self.queries: list[str] = []

    def __call__(self, url: str, params: dict[str, Any]) -> FakeResponse:
        self.queries.append(params.get("q"))
        if self.error:
            return FakeResponse(200, {"error": self.error})
        found = [
            {
                "id": i,
                "name": name,
                "type": "artist",
                "picture_medium": DEEZER_PHOTO.format(photo) if photo else DEEZER_NO_PHOTO,
            }
            for i, (name, photo) in enumerate(self.artists.get(params.get("q"), []), 1)
        ]
        return FakeResponse(200, {"data": found, "total": len(found)})


class FakeLastfmApi:
    """artist.getsimilar, artist.gettoptags, artist.gettopalbums, and scrobbles."""

    def __init__(self, recent=None, bad_key: bool = False):
        self.recent = recent
        self.bad_key = bad_key
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, params: dict[str, Any]) -> FakeResponse:
        method = params.get("method")
        if method == "user.getrecenttracks":
            return self.recent(url, params)
        self.calls.append((method, params.get("artist")))
        if self.bad_key:
            return FakeResponse(403, {"error": 10, "message": "Invalid API key"})
        artist = params["artist"]
        if artist == "Example Unknown":
            return FakeResponse(200, {"error": 6, "message": "The artist could not be found"})
        if method == "artist.getsimilar":
            similar = [{"name": n, "mbid": "", "match": str(m)} for n, m in SIMILAR.get(artist, [])]
            return FakeResponse(200, {"similarartists": {"artist": similar}})
        if method == "artist.gettoptags":
            tags = [{"name": "Example Dream Pop", "count": 100}, {"name": "Night", "count": 40}]
            return FakeResponse(200, {"toptags": {"tag": tags}})
        if method == "artist.gettopalbums":
            name, cover = TOP_ALBUMS.get(artist, ("", None))
            album = {
                "name": name,
                "image": [
                    {"size": "extralarge", "#text": LASTFM_CDN.format(cover) if cover else ""}
                ],
            }
            return FakeResponse(200, {"topalbums": {"album": [album] if name else []}})
        return FakeResponse(400, {"error": 3, "message": "Invalid method"})
