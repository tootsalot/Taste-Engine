"""Image cache for posters and covers.

Images download the first time they're shown and are kept in
<data folder>/cache/images. Image URLs on these CDNs never change, so a cached
file never needs refreshing.

Rules:
- Only https URLs on the known image hosts (ALLOWED_HOSTS) are fetched. Redirects
  aren't followed, so a redirect can't lead somewhere else.
- Each file is capped at MAX_FILE_BYTES and must say it's an image.
- The whole cache is capped (300 MB by default). When it's over, the least
  recently used files go first. Reading a cached file counts as using it.

No Qt here: the desktop app calls this from worker threads (taste/desktop/art.py).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from taste.config import data_dir

# Real Last.fm responses put covers on lastfm-img.freetls.fastly.net; the other
# Last.fm host is kept in case it serves them too. Deezer is the artist photo fallback.
# Book covers come from Hardcover (originals on assets., resized copies on
# production-img.) and film and TV posters from TMDB.
ALLOWED_HOSTS = frozenset(
    {
        "cdn.myanimelist.net",
        "lastfm-img.freetls.fastly.net",
        "lastfm.freetls.fastly.net",
        "cdn-images.dzcdn.net",
        "assets.hardcover.app",
        "production-img.hardcover.app",
        "image.tmdb.org",
    }
)
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
REDIRECTS = {301, 302, 303, 307, 308}
# 300 MB since 2.1: a full library is about 1,400 posters and covers plus albums, artists,
# and suggestions, and Hardcover covers can be full-size: 100 MB risked walls re-downloading.
MAX_CACHE_BYTES = 300 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = (5, 10)  # connect, read: short, so quitting the app never waits long
# A timeout or a server error is usually passing: tried again after this long. A 404, a
# file that isn't an image, or one over the cap stays failed for the session.
RETRY_TRANSIENT_SECONDS = 600
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
PARTIAL = ".part"


def default_root() -> Path:
    return data_dir() / "cache" / "images"


def allowed(url: str | None) -> bool:
    """True for an https URL on one of the known image hosts, and nothing else."""
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname in ALLOWED_HOSTS
        and parts.username is None
        and port in (None, 443)
    )


def file_name(url: str) -> str:
    """A stable file name for a URL: a hash plus the image's extension."""
    ext = Path(urlsplit(url).path).suffix.lower()
    if ext not in EXTENSIONS:
        ext = ".img"
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + ext


class TransientError(ValueError):
    """A server error or rate limit: worth asking again later."""


class ImageCache:
    """Disk cache shared by every profile. Safe to call from several threads."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = MAX_CACHE_BYTES,
        session: Any = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.root = root or default_root()
        self.max_bytes = max_bytes
        self.session = session if session is not None else requests.Session()
        self.clock = clock
        self._lock = threading.Lock()
        # URLs that failed: when to try again (None: not this session).
        self._failed: dict[str, float | None] = {}
        # Bytes on disk: counted once, then kept up to date, so storing an image doesn't
        # list the whole folder unless the cache may be over its cap.
        self._total: int | None = None

    def path_for(self, url: str) -> Path:
        return self.root / file_name(url)

    def cached(self, url: str) -> Path | None:
        """The cached file for a URL, marked as just used, or None."""
        if not allowed(url):
            return None
        path = self.path_for(url)
        try:
            os.utime(path)  # least recently used is judged by modification time
        except OSError:
            return None
        return path

    def get(self, url: str, retry: bool = False) -> Path | None:
        """The cached file, downloading it first if needed. None if it can't be had.

        A URL that failed is skipped for the rest of the session, unless `retry` (Rev's
        Rebuild: a cover that failed once used to stay a placeholder until a restart)."""
        if not allowed(url) or (self._still_failed(url) and not retry):
            return None
        self._failed.pop(url, None)
        path = self.cached(url)
        if path is not None:
            return path
        try:
            data = self._download(url)
        except (requests.RequestException, ValueError, OSError) as exc:
            # The message isn't kept: nothing here is worth more than "no picture".
            passing = isinstance(exc, (requests.RequestException, TransientError))
            self._failed[url] = self.clock() + RETRY_TRANSIENT_SECONDS if passing else None
            return None
        return self._store(url, data)

    def failed_for_good(self, url: str) -> bool:
        """A 404, a file that isn't an image, or one over the cap: not worth asking again
        this session (a timeout or a server error is)."""
        return url in self._failed and self._failed[url] is None

    def _still_failed(self, url: str) -> bool:
        if url not in self._failed:
            return False
        retry_at = self._failed[url]
        return retry_at is None or self.clock() < retry_at

    def _download(self, url: str, hops: int = 1) -> bytes:
        response = self.session.get(
            url, timeout=TIMEOUT_SECONDS, stream=True, allow_redirects=False
        )
        try:
            if response.status_code in REDIRECTS and hops > 0:
                # Last.fm answers some .png covers with a 301 to the same picture as
                # .gif. Follow one hop, and only on the same allowed host.
                location = response.headers.get("Location") or ""
                target = urljoin(url, location) if location else url
                same_host = urlsplit(target).hostname == urlsplit(url).hostname
                if target != url and allowed(target) and same_host:
                    return self._download(target, hops - 1)
            if response.status_code in TRANSIENT_STATUSES:
                raise TransientError(f"HTTP {response.status_code}")
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            kind = (response.headers.get("Content-Type") or "").lower()
            if not kind.startswith("image/"):
                raise ValueError("not an image")
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError("image too large")
                chunks.append(chunk)
            if size == 0:
                raise ValueError("empty image")
            return b"".join(chunks)
        finally:
            response.close()

    def _store(self, url: str, data: bytes) -> Path | None:
        path = self.path_for(url)
        with self._lock:
            # The same picture can be fetched twice at once (one poster at two sizes).
            # The first copy wins: on Windows the second can't replace it while it's
            # open for decoding, and there's no need to.
            if path.exists():
                return path
            self.root.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + f".{threading.get_ident()}{PARTIAL}")
            try:
                partial.write_bytes(data)
                os.replace(partial, path)  # readers never see a half-written file
            except OSError:
                partial.unlink(missing_ok=True)
                return path if path.exists() else None
            if self._total is not None:
                self._total += len(data)
            if self._total is None or self._total > self.max_bytes:
                self._evict(keep=path)
        return path

    def size(self) -> int:
        return sum(f.stat().st_size for f in self._files())

    def clear(self) -> None:
        """Delete every cached picture. They download again when next shown."""
        with self._lock:
            for f in self._files():
                with contextlib.suppress(OSError):  # in use elsewhere: it goes next time
                    f.unlink()
            self._total = None

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [f for f in self.root.iterdir() if f.is_file() and not f.name.endswith(PARTIAL)]

    def _evict(self, keep: Path) -> None:
        """Delete least recently used files until the cache fits. Never the one just added."""
        entries = []
        for f in self._files():
            try:
                stat = f.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, f, stat.st_size))
        total = sum(size for _, _, size in entries)
        for _, f, size in sorted(entries, key=lambda e: e[0]):
            if total <= self.max_bytes:
                break
            if f == keep:
                continue
            try:
                f.unlink()
                total -= size
            except OSError:
                pass  # in use elsewhere; try again next time
        self._total = total

    def clear_failures(self) -> None:
        self._failed.clear()
