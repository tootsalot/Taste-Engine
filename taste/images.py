"""Image cache for posters and covers.

Images download the first time they're shown and are kept in
<data folder>/cache/images. Image URLs on these CDNs never change, so a cached
file never needs refreshing.

Rules:
- Only https URLs on the MAL and Last.fm image hosts are fetched. Redirects
  aren't followed, so a redirect can't lead somewhere else.
- Each file is capped at MAX_FILE_BYTES and must say it's an image.
- The whole cache is capped (100 MB by default). When it's over, the least
  recently used files go first. Reading a cached file counts as using it.

No Qt here: the desktop app calls this from worker threads (taste/desktop/art.py).
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from taste.config import data_dir

# Real Last.fm responses put covers on lastfm-img.freetls.fastly.net; the other
# Last.fm host is kept in case it serves them too.
ALLOWED_HOSTS = frozenset(
    {"cdn.myanimelist.net", "lastfm-img.freetls.fastly.net", "lastfm.freetls.fastly.net"}
)
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
REDIRECTS = {301, 302, 303, 307, 308}
MAX_CACHE_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = (5, 10)  # connect, read: short, so quitting the app never waits long
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


class ImageCache:
    """Disk cache shared by every profile. Safe to call from several threads."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = MAX_CACHE_BYTES,
        session: Any = None,
    ) -> None:
        self.root = root or default_root()
        self.max_bytes = max_bytes
        self.session = session if session is not None else requests.Session()
        self._lock = threading.Lock()
        self._failed: set[str] = set()  # URLs that failed this session; not retried

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

    def get(self, url: str) -> Path | None:
        """The cached file, downloading it first if needed. None if it can't be had."""
        if not allowed(url) or url in self._failed:
            return None
        path = self.cached(url)
        if path is not None:
            return path
        try:
            data = self._download(url)
        except (requests.RequestException, ValueError, OSError):
            # The message isn't kept: nothing here is worth more than "no picture".
            self._failed.add(url)
            return None
        return self._store(url, data)

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

    def _store(self, url: str, data: bytes) -> Path:
        path = self.path_for(url)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + f".{threading.get_ident()}{PARTIAL}")
            partial.write_bytes(data)
            os.replace(partial, path)  # readers never see a half-written file
            self._evict(keep=path)
        return path

    def size(self) -> int:
        return sum(f.stat().st_size for f in self._files())

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

    def clear_failures(self) -> None:
        self._failed.clear()
