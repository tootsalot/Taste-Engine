"""Posters and covers on screen, loaded off the UI thread.

ArtLoader downloads (through taste.images.ImageCache) and decodes each image at
the size it's shown, on a small thread pool. The UI thread only turns the
finished QImage into a QPixmap. Decoded pixmaps are kept in QPixmapCache,
capped at 20 MB.

ArtTile is the widget: a rounded box that shows a placeholder (the title's
first letter) until its picture arrives, and keeps it if the picture can't be had.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPixmap,
    QPixmapCache,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from taste import images
from taste.desktop import theme

MEMORY_CACHE_KB = 20 * 1024
THREADS = 4


def cache_key(url: str, size: QSize) -> str:
    return f"{url}@{size.width()}x{size.height()}"


def decode(path: str, size: QSize) -> QImage:
    """Decode an image already scaled to cover `size` (cropping happens when painting)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    # By content, not file name: a .png address can deliver a GIF after a redirect.
    reader.setDecideFormatFromContent(True)
    full = reader.size()
    if full.isValid() and full.width() > 0 and full.height() > 0:
        scale = max(size.width() / full.width(), size.height() / full.height())
        if scale < 1:
            reader.setScaledSize(
                QSize(max(1, round(full.width() * scale)), max(1, round(full.height() * scale)))
            )
    return reader.read()


class _Job(QRunnable):
    def __init__(self, loader: ArtLoader, url: str, size: QSize, key: str) -> None:
        super().__init__()
        self.loader, self.url, self.size, self.key = loader, url, size, key

    def run(self) -> None:
        image = QImage()
        try:
            path = self.loader.cache.get(self.url)
            if path is not None:
                image = decode(str(path), self.size)
        except Exception:
            traceback.print_exc()  # a bug, not a missing picture: keep it visible in a console
        # Signals emitted here are delivered on the UI thread (the loader lives there).
        self.loader._finished.emit(self.key, image)


class ArtLoader(QObject):
    loaded = Signal(str)  # cache key, once its pixmap is ready
    failed_key = Signal(str)  # cache key of a picture that can't be had
    _finished = Signal(str, QImage)

    def __init__(self, cache: images.ImageCache | None = None) -> None:
        super().__init__()
        self.cache = cache or images.ImageCache()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(THREADS)
        self.pending: set[str] = set()
        self.failed: set[str] = set()
        QPixmapCache.setCacheLimit(MEMORY_CACHE_KB)
        self._finished.connect(self._on_finished)

    def pixmap(self, url: str | None, size: QSize) -> QPixmap | None:
        """The picture if it's ready. Otherwise starts loading it and returns None."""
        if not images.allowed(url):
            return None
        key = cache_key(url, size)
        found = QPixmapCache.find(key)
        if found is not None and not found.isNull():
            return found
        if key not in self.pending and key not in self.failed:
            self.pending.add(key)
            self.pool.start(_Job(self, url, QSize(size), key))
        return None

    def _on_finished(self, key: str, image: QImage) -> None:
        self.pending.discard(key)
        if image.isNull():
            self.failed.add(key)
            self.failed_key.emit(key)
            return
        QPixmapCache.insert(key, QPixmap.fromImage(image))
        self.loaded.emit(key)

    def is_idle(self) -> bool:
        return not self.pending

    def shutdown(self, wait_ms: int = 3000) -> None:
        """Drop queued downloads and give running ones a moment to finish."""
        self.pool.clear()
        self.pool.waitForDone(wait_ms)


class ArtTile(QWidget):
    """A fixed-size rounded picture with a lettered placeholder."""

    def __init__(
        self,
        loader: ArtLoader,
        size: QSize,
        *,
        radius: int = 10,
        accent: str = theme.LILAC,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.loader = loader
        self.art_size = QSize(size)
        self.radius = radius
        self.accent = QColor(accent)
        self.url: str | None = None
        self.fallback: str | None = None
        self.letter = ""
        self._pixmap: QPixmap | None = None
        self.setFixedSize(size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        loader.loaded.connect(self._on_loaded)
        loader.failed_key.connect(self._on_failed)

    def set_art(self, url: str | None, title: str = "", fallback: str | None = None) -> None:
        """`fallback` is tried when `url` can't be had (missing, refused, too big)."""
        self.url = url
        self.fallback = fallback
        self.letter = (title.strip()[:1] or "?").upper()
        self.setToolTip(title)
        self._pixmap = self.loader.pixmap(url, self.art_size)
        if self._pixmap is None and not self._waiting():
            self._use_fallback()
        self.update()

    def has_picture(self) -> bool:
        return self._pixmap is not None

    def _waiting(self) -> bool:
        """True while the current picture is still downloading."""
        return bool(self.url) and cache_key(self.url, self.art_size) in self.loader.pending

    def _use_fallback(self) -> None:
        if self.fallback and self.fallback != self.url:
            self.url, self.fallback = self.fallback, None
            self._pixmap = self.loader.pixmap(self.url, self.art_size)

    def _on_loaded(self, key: str) -> None:
        if self.url and key == cache_key(self.url, self.art_size):
            self._pixmap = self.loader.pixmap(self.url, self.art_size)
            self.update()

    def _on_failed(self, key: str) -> None:
        if self.url and key == cache_key(self.url, self.art_size):
            self._use_fallback()
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, self.radius, self.radius)
        painter.setClipPath(clip)
        if self._pixmap is not None:
            # Fill the box, cropping the overflow evenly (posters and square covers differ).
            pm = self._pixmap
            scale = max(rect.width() / pm.width(), rect.height() / pm.height())
            w, h = pm.width() * scale, pm.height() * scale
            target = QRectF((rect.width() - w) / 2, (rect.height() - h) / 2, w, h)
            painter.drawPixmap(target, pm, QRectF(pm.rect()))
            return
        painter.fillRect(rect, QColor(theme.RAISED))
        painter.setPen(self.accent)
        painter.setFont(theme.heading_font(max(int(min(rect.width(), rect.height()) / 3), 10)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.letter)
