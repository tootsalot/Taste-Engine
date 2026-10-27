"""A made-up TMDB account and catalog, shaped like the v3 and v4 API. No network.

Every title, person, and number here is invented.
"""

from __future__ import annotations

import re
from typing import Any

from tests.conftest import FakeResponse

READ_TOKEN = "fake-tmdb-read-token-0123456789"
USER_TOKEN = "fake-tmdb-user-token-9876543210"
ACCOUNT = "fake-account-1"


def movie(
    tmdb_id,
    title,
    vote,
    genres,
    director,
    year=2021,
    runtime=110,
    recs=(),
    keywords=("clockwork harbor", "borrowed memories"),
):
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": title,
        "release_date": f"{year}-05-01",
        "runtime": runtime,
        "vote_average": vote,
        "vote_count": 1200,
        "poster_path": f"/poster{tmdb_id}.jpg",
        "original_language": "en",
        "imdb_id": f"tt{tmdb_id:07d}",
        "genres": [{"id": i, "name": g} for i, g in enumerate(genres, 1)],
        "credits": {"crew": [{"id": 900 + tmdb_id, "name": director, "job": "Director"}]},
        "keywords": {"keywords": [{"id": i, "name": k} for i, k in enumerate(keywords, 1)]},
        "_recs": list(recs),
    }


def tv(
    tmdb_id,
    name,
    vote,
    genres,
    creator,
    year=2019,
    keywords=("clockwork harbor", "borrowed memories"),
):
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": name,
        "first_air_date": f"{year}-01-10",
        "last_air_date": f"{year + 2}-03-01",
        "status": "Ended",
        "number_of_seasons": 3,
        "number_of_episodes": 30,
        "episode_run_time": [45],
        "vote_average": vote,
        "vote_count": 800,
        "poster_path": f"/tv{tmdb_id}.jpg",
        "networks": [{"name": "Example Network"}],
        "genres": [{"id": i, "name": g} for i, g in enumerate(genres, 1)],
        "created_by": [{"id": 700 + tmdb_id, "name": creator}],
        "keywords": {"results": [{"id": i, "name": k} for i, k in enumerate(keywords, 1)]},
    }


CATALOG = {
    ("movie", 11): movie(11, "Example Night Train", 7.2, ["Drama"], "Ida Director", recs=[21, 22]),
    ("movie", 12): movie(12, "Example Paper Moon", 6.8, ["Comedy"], "Ida Director", recs=[22, 23]),
    ("movie", 13): movie(13, "Example Glass Sky", 7.9, ["Drama", "Mystery"], "Jo Filmmaker"),
    ("movie", 21): movie(21, "Example Candidate Film", 7.6, ["Drama"], "Ida Director", 2022),
    ("movie", 22): movie(22, "Example Second Pick", 7.0, ["Comedy", "Drama"], "Kay Maker", 2023),
    ("movie", 23): movie(23, "Example Rated Already", 6.0, ["Comedy"], "Kay Maker", 2020),
    ("tv", 31): tv(31, "Example Harbor Series", 8.1, ["Drama"], "Lu Writer"),
    ("tv", 32): tv(32, "Example Watchlist Series", 7.4, ["Comedy"], "Mo Writer"),
}

# My ratings: (kind, id, rating, rated at)
RATINGS = [
    ("movie", 11, 9.0, "2026-09-12T20:15:00.000Z"),
    ("movie", 12, 6.5, "2026-09-25T18:00:00.000Z"),
    ("movie", 13, 8.0, "2026-08-03T21:00:00.000Z"),
    ("movie", 23, 5.0, "2025-12-01T21:00:00.000Z"),
    ("tv", 31, 8.5, "2026-09-20T02:00:00.000Z"),
]
WATCHLIST = [("tv", 32), ("movie", 21)]
FAVORITES = [("movie", 11)]


def list_item(kind: str, tmdb_id: int, catalog=None) -> dict[str, Any]:
    full = (catalog or CATALOG)[(kind, tmdb_id)]
    keys = ("id", "title", "name", "original_title", "original_name", "release_date",
            "first_air_date", "vote_average", "vote_count", "poster_path")  # fmt: skip
    return {k: full[k] for k in keys if k in full}


class FakeTmdb:
    """Sign-in, my account's lists (20 a page, like the real one), and details."""

    def __init__(
        self, ratings=None, page_size: int = 20, catalog=None, watchlist=None, favorites=None
    ) -> None:
        self.ratings = list(RATINGS if ratings is None else ratings)
        self.catalog = CATALOG if catalog is None else catalog
        self.watchlist = WATCHLIST if watchlist is None else watchlist
        self.favorites = FAVORITES if favorites is None else favorites
        self.page_size = page_size
        self.approved = True
        self.calls: list[tuple[str, str]] = []
        self.gone: set[tuple[str, int]] = set()  # titles whose details answer 404

    def __call__(self, url: str, params: dict[str, Any]) -> FakeResponse:
        method = params.get("_method", "GET")
        path = url.split("themoviedb.org", 1)[-1]
        self.calls.append((method, path))
        if path == "/3/authentication":
            return FakeResponse(200, {"success": True})
        if path == "/4/auth/request_token":
            return FakeResponse(200, {"success": True, "request_token": "req-token-1"})
        if path == "/4/auth/access_token" and method == "POST":
            if not self.approved or params.get("request_token") != "req-token-1":
                return FakeResponse(401, {"success": False, "status_message": "Not approved."})
            return FakeResponse(
                200, {"success": True, "access_token": USER_TOKEN, "account_id": ACCOUNT}
            )
        if path == "/4/auth/access_token" and method == "DELETE":
            return FakeResponse(200, {"success": True})
        match = re.match(r"/4/account/([^/]+)/(movie|tv)/(rated|watchlist|favorites)$", path)
        if match:
            _, kind, which = match.groups()
            if which == "rated":
                items = [
                    {
                        **list_item(k, i, self.catalog),
                        "account_rating": {"value": v, "created_at": at},
                    }
                    for k, i, v, at in self.ratings
                    if k == kind
                ]
            else:
                chosen = self.watchlist if which == "watchlist" else self.favorites
                items = [list_item(k, i, self.catalog) for k, i in chosen if k == kind]
            page = int(params.get("page", 1))
            chunk = items[(page - 1) * self.page_size : page * self.page_size]
            pages = max(1, -(-len(items) // self.page_size))
            return FakeResponse(
                200,
                {"page": page, "results": chunk, "total_pages": pages, "total_results": len(items)},
            )
        match = re.match(r"/3/(movie|tv)/(\d+)/recommendations$", path)
        if match:
            kind, tmdb_id = match.groups()
            recs = self.catalog.get((kind, int(tmdb_id)), {}).get("_recs", [])
            results = [list_item(kind, r, self.catalog) for r in recs]
            return FakeResponse(200, {"page": 1, "results": results, "total_pages": 1})
        match = re.match(r"/3/(movie|tv)/(\d+)$", path)
        if match:
            found = self.catalog.get((match.group(1), int(match.group(2))))
            if found is None or (match.group(1), int(match.group(2))) in self.gone:
                return FakeResponse(404, {"success": False, "status_message": "Not found."})
            appended = set((params.get("append_to_response") or "").split(","))
            return FakeResponse(
                200,
                {
                    k: v
                    for k, v in found.items()
                    if not k.startswith("_") and (k not in ("keywords", "credits") or k in appended)
                },
            )
        return FakeResponse(404, {"success": False, "status_message": f"No route {path}"})
