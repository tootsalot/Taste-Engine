"""A made-up Hardcover account, shaped like its OAuth endpoints and GraphQL API.

Every book, author, tag, and number here is invented. No network.
"""

from __future__ import annotations

from typing import Any

from taste.sources import hardcover
from tests.conftest import FakeResponse

ACCESS = "hc_at_fake-access-0123456789"
REFRESH = "hc_rt_fake-refresh-0123456789"
USER_ID = 4242


def tags(genres=(), moods=()) -> dict[str, Any]:
    return {
        "Genre": [{"tag": g, "count": 50 - i} for i, g in enumerate(genres)],
        "Mood": [{"tag": m, "count": 40 - i} for i, m in enumerate(moods)],
        "Content Warning": [{"tag": "Example Warning", "count": 3}],
    }


def book(
    book_id: int,
    title: str,
    author: tuple[int, str],
    *,
    pages: int | None = 320,
    year: int = 2018,
    rating: float = 3.9,
    fiction: bool = True,
    genres=("Fantasy",),
    moods=("adventurous",),
    audio_seconds: int | None = None,
    similar=(),
) -> dict[str, Any]:
    return {
        "id": book_id,
        "title": title,
        "subtitle": None,
        "slug": title.lower().replace(" ", "-"),
        "pages": pages,
        "audio_seconds": audio_seconds,
        "release_year": year,
        "rating": rating,
        "ratings_count": 120 + book_id,
        "literary_type_id": 1 if fiction else 2,
        "cached_tags": tags(genres, moods),
        "cached_similar_book_ids": list(similar),
        "users_count": 900 + book_id,
        "users_read_count": 400 + book_id,
        "description": f"<p>A made-up book called {title}.</p>",
        "image": {"url": f"https://assets.hardcover.app/edition/{book_id}/cover.jpg"},
        "contributions": [
            {"contribution": None, "author": {"id": author[0], "name": author[1]}},
            {"contribution": "Illustrator", "author": {"id": 999, "name": "Example Illustrator"}},
        ],
    }


def entry(
    user_book_id: int,
    the_book: dict[str, Any],
    *,
    status: int = 3,
    rating: float | None = None,
    reads: list[tuple[int, str | None, str | None]] = (),
    last_read: str | None = None,
    edition: dict[str, Any] | None = None,
    owned: bool = False,
) -> dict[str, Any]:
    """One book in my library. reads: (read id, started, finished)."""
    return {
        "id": user_book_id,
        "status_id": status,
        "rating": rating,
        "date_added": "2024-01-02",
        "first_started_reading_date": reads[0][1] if reads else None,
        "first_read_date": next((r[2] for r in reads if r[2]), None),
        "last_read_date": last_read or (reads[-1][2] if reads else None),
        "read_count": sum(1 for r in reads if r[2]),
        "owned": owned,
        "updated_at": "2024-01-02T00:00:00+00:00",  # changed long ago
        "edition": edition,
        "user_book_reads": [
            {"id": r[0], "started_at": r[1], "finished_at": r[2], "edition_id": None} for r in reads
        ],
        "book": the_book,
    }


ANA = (11, "Ana Example")
BEN = (12, "Ben Sample")
CAL = (13, "Cal Placeholder")

# Books I don't have, that my favorites point at.
CATALOG = {
    201: book(201, "Example Tide Keeper", ANA, genres=("Fantasy",), rating=4.2),
    202: book(202, "Example Salt Atlas", BEN, genres=("Fantasy", "Adventure"), rating=3.9),
    203: book(203, "Example Paper Fox", CAL, genres=("Mystery",), rating=3.6),
    204: book(204, "Example Few Ratings", CAL, genres=("Fantasy",), rating=4.8),
}
CATALOG[204]["ratings_count"] = 3  # too few to trust

LIBRARY = [
    entry(
        101,
        book(
            1,
            "Example Lantern Road",
            ANA,
            genres=("Fantasy", "Young Adult"),
            rating=4.1,
            similar=[201, 202, 6, 204],
        ),  # fmt: skip
        rating=4.5,
        reads=[(1001, "2026-08-20", "2026-09-04"), (1002, "2026-09-20", "2026-09-28")],
        owned=True,
    ),
    entry(
        102,
        book(2, "Example Quiet Harbor", ANA, genres=("Fantasy",), moods=("emotional",)),
        rating=3.0,
        reads=[(1003, "2026-09-10", "2026-09-15")],
    ),
    entry(
        103,
        book(3, "Example Field Guide", BEN, fiction=False, genres=("History",), pages=210),
        rating=4.0,
        reads=[(1004, None, "2025-06-01")],
        edition={"id": 77, "pages": None, "audio_seconds": 36000, "reading_format_id": 2},
    ),
    # Read, with a last read date but no read-throughs (imported that way).
    entry(
        104,
        book(4, "Example Old Favorite", CAL, year=1999, similar=[202, 203, 1]),
        rating=5.0,
        last_read="2019-03-03",
    ),
    # Read with no date at all: all-time totals only.
    entry(105, book(5, "Example Undated Read", CAL, pages=None), rating=2.5),
    entry(106, book(6, "Example Want List", BEN), status=1),
    entry(107, book(7, "Example Half Done", BEN), status=2, reads=[(1007, "2026-10-01", None)]),
    entry(108, book(8, "Example Gave Up", ANA), status=5, rating=1.5),
]


class FakeHardcover:
    """OAuth token, refresh, and revoke, plus `me` and my library (pages of `page_size`)."""

    def __init__(self, library=None, page_size: int = hardcover.PAGE_SIZE) -> None:
        self.library = list(LIBRARY if library is None else library)
        self.catalog = dict(CATALOG)
        self.page_size = page_size
        self.access = ACCESS
        self.refresh = REFRESH
        self.refreshes = 0
        self.revoked: list[str] = []
        self.codes: dict[str, str] = {}  # code -> the PKCE challenge it was issued for
        self.calls: list[str] = []
        self.knows_updated_at = True  # False: answer like an API without that filter

    def issue(self, challenge: str) -> str:
        """A code, as if I approved on the consent page."""
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = challenge
        return code

    def _pair(self) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "access_token": self.access,
                "refresh_token": self.refresh,
                "token_type": "Bearer",
                "expires_in": 7 * 24 * 3600,
            },
        )

    def __call__(self, url: str, params: dict[str, Any]) -> FakeResponse:
        if url == hardcover.TOKEN_URL:
            self.calls.append(f"token:{params.get('grant_type')}")
            if params.get("client_id") != hardcover.CLIENT_ID:
                return FakeResponse(401, {"error": "invalid_client"})
            if params.get("grant_type") == "authorization_code":
                expected = self.codes.pop(params.get("code"), None)
                if expected is None or hardcover.challenge(params["code_verifier"]) != expected:
                    return FakeResponse(400, {"error": "invalid_grant"})
                return self._pair()
            if params.get("grant_type") == "refresh_token":
                if params.get("refresh_token") != self.refresh:
                    return FakeResponse(400, {"error": "invalid_grant"})
                self.refreshes += 1
                self.access = f"{ACCESS}-{self.refreshes}"
                self.refresh = f"{REFRESH}-{self.refreshes}"  # rotates, like the real one
                return self._pair()
            return FakeResponse(400, {"error": "unsupported_grant_type"})
        if url == hardcover.REVOKE_URL:
            self.revoked.append(params.get("token"))
            return FakeResponse(200, {})
        if url == hardcover.API_URL:
            text = params.get("query") or ""
            variables = params.get("variables") or {}
            if "query Me" in text:
                self.calls.append("me")
                return FakeResponse(
                    200, {"data": {"me": [{"id": USER_ID, "username": "example_user"}]}}
                )
            if "query Books" in text:
                self.calls.append(f"books:{len(variables['ids'])}")
                found = [
                    self.catalog.get(i) or next((e["book"] for e in self.library
                                                 if e["book"]["id"] == i), None)
                    for i in variables["ids"]
                ]  # fmt: skip
                return FakeResponse(200, {"data": {"books": [b for b in found if b]}})
            if "query Library" in text:
                assert variables["uid"] == USER_ID
                offset, limit = variables["offset"], variables["limit"]
                since = variables.get("since")
                if since and not self.knows_updated_at:
                    message = "field 'updated_at' not found in type: 'user_books_bool_exp'"
                    return FakeResponse(200, {"errors": [{"message": message}]})
                self.calls.append(f"library{'-since' if since else ''}:{offset}")
                rows = [r for r in self.library if not since or r["updated_at"] > since]
                rows = rows[offset : offset + min(limit, self.page_size)]
                return FakeResponse(200, {"data": {"user_books": rows}})
        return FakeResponse(404, {"error": "not found"})
