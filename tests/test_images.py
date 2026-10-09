"""Image cache: known hosts only, download once, size caps, LRU eviction, background loading."""

import os
import threading
import time

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmapCache

from taste import images
from taste.config import data_dir
from taste.desktop.art import ArtLoader, ArtTile, decode
from tests.fake_images import GIF_BYTES, FakeImageSession, image_bytes

POSTER = "https://cdn.myanimelist.net/images/anime/1/101.jpg"
WEBP = "https://cdn.myanimelist.net/images/anime/2/202.webp"
COVER = "https://lastfm.freetls.fastly.net/i/u/300x300/cover.png"


@pytest.fixture(autouse=True)
def empty_memory_cache():
    QPixmapCache.clear()  # it's process-wide; each test starts with nothing decoded
    yield
    QPixmapCache.clear()


@pytest.fixture
def session():
    s = FakeImageSession()
    s.add(POSTER, image_bytes(fmt="JPEG"), "image/jpeg")
    s.add(COVER, image_bytes(30, 30), "image/png")
    return s


@pytest.fixture
def cache(tmp_path, session):
    return images.ImageCache(tmp_path / "images", session=session)


def set_last_used(path, seconds_ago):
    stamp = time.time() - seconds_ago
    os.utime(path, (stamp, stamp))


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        POSTER,
        COVER,
        "https://cdn.myanimelist.net:443/images/x.jpg",
        # The host real Last.fm responses use for covers (seen in a live check).
        "https://lastfm-img.freetls.fastly.net/i/u/300x300/0123456789abcdef.jpg",
    ],
)
def test_known_image_hosts_are_allowed(url):
    assert images.allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://cdn.myanimelist.net/images/anime/1/101.jpg",  # not https
        "https://myanimelist.net/images/anime/1/101.jpg",
        "https://cdn.myanimelist.net.example.com/x.jpg",
        "https://example.com/cdn.myanimelist.net/x.jpg",
        "https://user@cdn.myanimelist.net/x.jpg",
        "https://cdn.myanimelist.net:8443/x.jpg",
        "file:///C:/Windows/win.ini",
        "https://lastfm.freetls.fastly.net:notaport/x.png",
        "https://lastfm-img.freetls.fastly.net.example.com/x.png",
        "https://lastfm-img2.freetls.fastly.net/x.png",
    ],
)
def test_everything_else_is_refused(url, cache, session):
    assert not images.allowed(url)
    assert cache.get(url) is None
    assert session.calls == []


def test_downloads_once_then_reuses(cache, session):
    path = cache.get(POSTER)
    assert path is not None and path.suffix == ".jpg"
    assert path.read_bytes() == session.files[POSTER][2]
    assert session.calls == [{"url": POSTER, "allow_redirects": False, "stream": True}]
    assert cache.get(POSTER) == path
    assert cache.cached(POSTER) == path
    assert len(session.calls) == 1  # no second download


def test_default_location_is_in_the_data_folder():
    assert images.default_root() == data_dir() / "cache" / "images"
    assert "taste-data" in str(images.default_root())  # the test data folder, not real data


@pytest.mark.parametrize(
    "status, content_type, body",
    [
        (404, "text/html", b"not found"),
        (302, "image/jpeg", b""),  # a redirect with nowhere to go
        (200, "text/html", b"<html>"),
        (200, "image/png", b""),
    ],
)
def test_bad_responses_are_not_cached(cache, session, status, content_type, body):
    session.files[POSTER] = (status, content_type, body)
    assert cache.get(POSTER) is None
    assert cache.get(POSTER) is None
    assert len(session.calls) == 1  # failures aren't retried in the same session
    assert cache.size() == 0
    cache.clear_failures()
    session.add(POSTER, image_bytes(), "image/jpeg")
    assert cache.get(POSTER) is not None


GIF_COVER = "https://lastfm-img.freetls.fastly.net/i/u/300x300/0123456789abcdef.gif"
PNG_COVER = "https://lastfm-img.freetls.fastly.net/i/u/300x300/0123456789abcdef.png"


def test_a_redirect_on_the_same_host_is_followed_once(cache, session):
    # Last.fm answers some .png covers with a 301 to the same picture as .gif.
    session.redirect(PNG_COVER, GIF_COVER)
    session.add(GIF_COVER, GIF_BYTES, "image/gif")
    path = cache.get(PNG_COVER)
    assert path is not None and path == cache.path_for(PNG_COVER)  # found by the URL asked for
    assert session.urls() == [PNG_COVER, GIF_COVER]
    assert all(not c["allow_redirects"] for c in session.calls)  # never left to requests
    image = decode(str(path), QSize(30, 30))  # GIF bytes in a .png-named file still decode
    assert not image.isNull()


def test_relative_redirects_resolve_on_the_same_host(cache, session):
    session.redirect(PNG_COVER, "/i/u/300x300/0123456789abcdef.gif")
    session.add(GIF_COVER, GIF_BYTES, "image/gif")
    assert cache.get(PNG_COVER) is not None


@pytest.mark.parametrize(
    "location",
    [
        "https://example.com/x.gif",  # another host
        "https://cdn.myanimelist.net/images/x.jpg",  # allowed, but not the same host
        "http://lastfm-img.freetls.fastly.net/i/u/300x300/0123456789abcdef.gif",  # not https
    ],
)
def test_redirects_off_the_host_are_refused(cache, session, location):
    session.redirect(PNG_COVER, location)
    session.add(location, image_bytes(), "image/png")
    assert cache.get(PNG_COVER) is None
    assert session.urls() == [PNG_COVER]


def test_only_one_redirect_is_followed(cache, session):
    session.redirect(PNG_COVER, GIF_COVER)
    session.redirect(GIF_COVER, PNG_COVER)  # a loop
    assert cache.get(PNG_COVER) is None
    assert session.urls() == [PNG_COVER, GIF_COVER]


def test_oversized_images_are_refused(cache, session, monkeypatch):
    monkeypatch.setattr(images, "MAX_FILE_BYTES", 100)
    session.add(POSTER, b"x" * 101, "image/jpeg")
    assert cache.get(POSTER) is None
    assert cache.size() == 0


def test_least_recently_used_go_first_when_over_the_cap(tmp_path):
    session = FakeImageSession()
    urls = [f"https://cdn.myanimelist.net/images/anime/1/{i}.jpg" for i in range(4)]
    for url in urls:
        session.add(url, b"x" * 100, "image/jpeg")
    cache = images.ImageCache(tmp_path / "images", max_bytes=300, session=session)
    paths = [cache.get(u) for u in urls[:3]]
    set_last_used(paths[0], 300)
    set_last_used(paths[1], 200)
    set_last_used(paths[2], 100)
    cache.cached(urls[0])  # looking at the oldest makes it the newest

    cache.get(urls[3])  # 400 bytes > 300: one file has to go
    assert not paths[1].exists()  # least recently used
    assert paths[0].exists() and paths[2].exists() and cache.path_for(urls[3]).exists()
    assert cache.size() == 300


def test_file_names_are_hashes_with_the_image_extension():
    assert images.file_name(WEBP).endswith(".webp")
    assert images.file_name("https://cdn.myanimelist.net/x").endswith(".img")
    assert images.file_name(POSTER) != images.file_name(COVER)
    assert "/" not in images.file_name(POSTER) and ".." not in images.file_name(POSTER)


# ---------------------------------------------------------------------------
# Loading on screen
# ---------------------------------------------------------------------------


def test_tile_loads_its_picture_in_the_background(qtbot, cache):
    loader = ArtLoader(cache)
    tile = ArtTile(loader, QSize(60, 90))
    qtbot.addWidget(tile)
    tile.set_art(POSTER, "Example Drama A")
    assert not tile.has_picture()  # placeholder first
    qtbot.waitUntil(tile.has_picture, timeout=5000)
    assert tile.grab().size() == QSize(60, 90)
    loader.shutdown()


def test_webp_posters_decode(qtbot, tmp_path):
    session = FakeImageSession()
    session.add(WEBP, image_bytes(225, 320, fmt="WEBP"), "image/webp")
    loader = ArtLoader(images.ImageCache(tmp_path / "images", session=session))
    tile = ArtTile(loader, QSize(75, 106))
    qtbot.addWidget(tile)
    tile.set_art(WEBP, "Example WebP Show")
    qtbot.waitUntil(tile.has_picture, timeout=5000)
    loader.shutdown()


def test_images_are_decoded_at_display_size(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(image_bytes(600, 900))
    image = decode(str(path), QSize(100, 150))
    assert (image.width(), image.height()) == (100, 150)
    small = decode(str(path), QSize(1200, 1800))  # never scaled up
    assert (small.width(), small.height()) == (600, 900)


def test_unknown_hosts_keep_the_placeholder(qtbot, cache, session):
    loader = ArtLoader(cache)
    tile = ArtTile(loader, QSize(60, 60))
    qtbot.addWidget(tile)
    tile.set_art("https://example.com/x.png", "Example")
    assert loader.is_idle() and not tile.has_picture()
    assert tile.letter == "E"
    assert session.calls == []


def test_missing_picture_keeps_the_placeholder_and_is_not_retried(qtbot, cache, session):
    loader = ArtLoader(cache)
    tile = ArtTile(loader, QSize(60, 60))
    qtbot.addWidget(tile)
    url = "https://lastfm.freetls.fastly.net/i/u/300x300/missing.png"
    tile.set_art(url, "Missing")
    qtbot.waitUntil(loader.is_idle, timeout=5000)
    assert not tile.has_picture()
    tile.set_art(url, "Missing")
    assert loader.is_idle()
    assert session.urls() == [url]


def test_a_tile_falls_back_when_its_picture_cant_be_had(qtbot, cache, session):
    # Some Last.fm covers are animated GIFs over the size cap; the artist photo stands in.
    loader = ArtLoader(cache)
    tile = ArtTile(loader, QSize(60, 60))
    qtbot.addWidget(tile)
    missing = "https://lastfm-img.freetls.fastly.net/i/u/300x300/missing.png"
    photo = "https://cdn-images.dzcdn.net/images/artist/abc/250x250-000000-80-0-0.jpg"
    session.add(photo, image_bytes(fmt="JPEG"), "image/jpeg")
    tile.set_art(missing, "Example Artist", fallback=photo)
    qtbot.waitUntil(tile.has_picture, timeout=5000)
    assert session.urls() == [missing, photo]
    loader.shutdown()


def test_slow_downloads_never_block_the_ui_thread(qtbot, cache, session):
    session.gate = threading.Event()  # every download hangs until released
    loader = ArtLoader(cache)
    tiles = []
    started = time.perf_counter()
    for url in (POSTER, COVER):
        tile = ArtTile(loader, QSize(60, 60))
        qtbot.addWidget(tile)
        tile.set_art(url, "Example")
        tiles.append(tile)
    assert time.perf_counter() - started < 0.5  # returned right away, nothing waited
    assert not any(t.has_picture() for t in tiles)
    session.gate.set()
    qtbot.waitUntil(lambda: all(t.has_picture() for t in tiles), timeout=5000)
    loader.shutdown()


def test_two_tiles_share_one_download(qtbot, cache, session):
    loader = ArtLoader(cache)
    a, b = ArtTile(loader, QSize(60, 90)), ArtTile(loader, QSize(60, 90))
    for tile in (a, b):
        qtbot.addWidget(tile)
        tile.set_art(POSTER, "Example")
    qtbot.waitUntil(lambda: a.has_picture() and b.has_picture(), timeout=5000)
    assert session.urls() == [POSTER]
    loader.shutdown()
