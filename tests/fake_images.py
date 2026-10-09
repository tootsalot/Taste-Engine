"""A fake image download session and sample images made with Qt. No network."""

from __future__ import annotations

import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

# Qt reads GIF but can't write it, so this one is written out by hand: a 1x1 GIF89a.
GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def image_bytes(width: int = 60, height: int = 90, fmt: str = "PNG", color: str = "#B69CFF"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt), f"Qt can't write {fmt}"
    buffer.close()
    return bytes(data)


class FakeImageResponse:
    def __init__(
        self, status: int, content_type: str, body: bytes, location: str | None = None
    ) -> None:
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size: int):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i : i + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeImageSession:
    """Stands in for requests.Session in the image cache.

    `files` maps URL -> (status, content type, body). Unknown URLs answer 404.
    Set `gate` to a threading.Event to hold every download until it's set.
    """

    def __init__(self, files: dict[str, tuple[int, str, bytes]] | None = None) -> None:
        self.files = dict(files or {})
        self.redirects: dict[str, str] = {}  # URL -> Location of a 301
        self.calls: list[dict] = []
        self.gate: threading.Event | None = None
        self._lock = threading.Lock()

    def add(self, url: str, body: bytes, content_type: str = "image/png", status: int = 200):
        self.files[url] = (status, content_type, body)

    def redirect(self, url: str, location: str) -> None:
        """Answer `url` with 301 to `location`, the way Last.fm sends some .png to .gif."""
        self.redirects[url] = location

    def get(self, url, timeout=None, stream=False, allow_redirects=True):
        with self._lock:
            self.calls.append({"url": url, "allow_redirects": allow_redirects, "stream": stream})
        if self.gate is not None:
            self.gate.wait(10)
        if url in self.redirects:
            return FakeImageResponse(301, "image/png", b"", location=self.redirects[url])
        status, content_type, body = self.files.get(url, (404, "text/html", b"not found"))
        return FakeImageResponse(status, content_type, body)

    def urls(self) -> list[str]:
        with self._lock:
            return [c["url"] for c in self.calls]
