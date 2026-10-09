"""Deezer: photos of suggested artists, nothing else.

Last.fm returns the same blank image for every artist, and a few artists have no
album cover either, so the photo is the picture when there's no cover and a backup
when the cover can't be downloaded. Deezer's artist search needs no key. Only an exact name match
(ignoring case) counts, so a wrong photo is never shown; anything else means no
photo. Checked against the live API on 2026-10-09: results are in `data`, an
unknown name gives `{"data": [], "total": 0}`, and photos are on cdn-images.dzcdn.net.
"""

from __future__ import annotations

from typing import Any

from taste.http_client import RETRYABLE_HTTP_STATUSES, ApiError

SOURCE = "deezer"
SEARCH_URL = "https://api.deezer.com/search/artist"
RETRYABLE_ERRORS = {4, 700}  # quota exceeded, service busy


def check_error(status: int, data: Any) -> None:
    """Deezer reports errors in the body, often with HTTP 200."""
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        error = data["error"]
        code = error.get("code")
        raise ApiError(
            f"Deezer error {code}: {error.get('message', '')}",
            retryable=code in RETRYABLE_ERRORS,
            status=status,
        )
    if status in RETRYABLE_HTTP_STATUSES:
        raise ApiError(f"Deezer HTTP {status}", retryable=True, status=status)
    if status >= 400:
        raise ApiError(f"Deezer HTTP {status}", retryable=False, status=status)


def best_match(name: str, data: Any) -> dict[str, Any] | None:
    """The first result whose name is exactly the artist's, ignoring case."""
    wanted = name.strip().casefold()
    for artist in (data or {}).get("data") or []:
        if (artist.get("name") or "").strip().casefold() == wanted:
            return artist
    return None


def picture_url(artist: dict[str, Any] | None) -> str | None:
    """The 250 px photo, or None. Deezer's no-photo URL has an empty hash: /artist//."""
    url = (artist or {}).get("picture_medium") or ""
    return url if url and "/artist//" not in url else None
