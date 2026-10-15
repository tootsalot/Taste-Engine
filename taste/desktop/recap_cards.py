"""Draw a recap as one tall image: a 1080 px wide poster that scrolls.

Each part (music, anime, books, ...) is drawn on its own transparent strip at the
height it needs, then the strips are stacked on one continuous background whose
glow drifts from color to color down the page. Everything is QPainter on QImage,
which works off the UI thread, so a worker draws the whole thing while the window
stays responsive. `pictures(url)` returns a decoded QImage for a poster or cover,
or None for the lettered placeholder.

Lilac is anime, coral music, amber books, and teal films and TV, as everywhere in
the app.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from taste import activity
from taste.desktop import theme
from taste.recap import Item, RecapData

W = 1080
H = 1920  # a phone screen, for sharing as a story
M = 84  # side margin
STRIP_MAX = 2600  # the most any one part may take
GAP = 40  # between parts
Pictures = Callable[[str | None], QImage | None]
WEEKDAY_LETTERS = "SMTWTFS"
LIGHT = {  # a lighter partner for each accent, for gradients
    theme.LILAC: "#E4D9FF",
    theme.CORAL: "#FFC2A8",
    theme.AMBER: "#FFE6B0",
    theme.TEAL: "#C2F2EA",
}
TOP_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
TOP_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
TOP_CENTER = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
CENTER = Qt.AlignmentFlag.AlignCenter


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def font(size: int, heading: bool = False, weight: QFont.Weight = QFont.Weight.Bold) -> QFont:
    f = QFont(theme.HEADING_FAMILY if heading else theme.BODY_FAMILY)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def text(
    p: QPainter,
    rect: QRectF,
    value: str,
    f: QFont,
    color: str = theme.TEXT,
    flags: Qt.AlignmentFlag = TOP_LEFT,
) -> float:
    """Draw wrapped text in `rect`; returns the height it took."""
    p.setFont(f)
    p.setPen(QColor(color))
    wrap = flags | Qt.TextFlag.TextWordWrap
    used = p.boundingRect(rect, wrap, value)
    p.drawText(rect, wrap, value)
    return used.height()


def elide(value: str, f: QFont, width: float) -> str:
    return QFontMetrics(f).elidedText(value, Qt.TextElideMode.ElideRight, int(width))


def fitting(value: str, size: int, width: float, heading: bool = True) -> QFont:
    """The biggest font, up to `size`, that keeps `value` on one line of `width`."""
    f = font(size, heading)
    while size > 28 and QFontMetrics(f).horizontalAdvance(value) > width:
        size -= 4
        f = font(size, heading)
    return f


def gradient_text(
    p: QPainter, x: float, y: float, value: str, f: QFont, start: str, end: str
) -> float:
    """One line of text filled with a gradient, top at `y`. Returns its height."""
    metrics = QFontMetrics(f)
    path = QPainterPath()
    path.addText(x, y + metrics.ascent(), f, value)
    # Heavy weights of Inter draw some digits ("4") with overlapping outlines; the
    # default odd-even fill punched the overlap out.
    path.setFillRule(Qt.FillRule.WindingFill)
    box = path.boundingRect()
    gradient = QLinearGradient(box.topLeft(), box.bottomRight())
    gradient.setColorAt(0, QColor(start))
    gradient.setColorAt(1, QColor(end))
    p.setPen(Qt.PenStyle.NoPen)
    p.fillPath(path, QBrush(gradient))
    return metrics.height()


def glow(p: QPainter, center: QPointF, radius: float, color: str, alpha: int) -> None:
    gradient = QRadialGradient(center, radius)
    start = QColor(color)
    start.setAlpha(alpha)
    end = QColor(color)
    end.setAlpha(0)
    gradient.setColorAt(0, start)
    gradient.setColorAt(1, end)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(gradient))
    p.drawEllipse(center, radius, radius)


def discs(p: QPainter, center: QPointF, radius: float) -> None:
    """The logo: a lilac disc and a coral one, overlapping."""
    p.setPen(Qt.PenStyle.NoPen)
    coral = QColor(theme.CORAL)
    coral.setAlpha(235)
    p.setBrush(QColor(theme.LILAC))
    p.drawEllipse(QPointF(center.x() - radius * 0.45, center.y()), radius, radius)
    p.setBrush(coral)
    p.drawEllipse(QPointF(center.x() + radius * 0.45, center.y()), radius, radius)


def band(p: QPainter, y: float, value: str, accent: str) -> float:
    """A part's heading on a slanted band of its color. Returns the bottom."""
    f = font(30)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
    width = QFontMetrics(f).horizontalAdvance(value) + 70
    left = M - 24
    shape = QPolygonF(
        [QPointF(left, y), QPointF(left + width + 22, y),
         QPointF(left + width, y + 58), QPointF(left - 22, y + 58)]
    )  # fmt: skip
    fill = QColor(accent)
    fill.setAlpha(55)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawPolygon(shape)
    text(p, QRectF(M + 6, y + 12, width, 40), value, f, accent)
    return y + 58


def sticker(p: QPainter, center: QPointF, value: str, color: str, angle: float) -> None:
    """A small tilted label, like a sticker on the page."""
    f = font(26)
    width = QFontMetrics(f).horizontalAdvance(value) + 40
    p.save()
    p.translate(center)
    p.rotate(angle)
    rect = QRectF(-width / 2, -26, width, 52)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(rect, 26, 26)
    text(p, rect, value, f, theme.ON_ACCENT, CENTER)
    p.restore()


def chip(p: QPainter, y: float, value: str, accent: str) -> float:
    """A fact in a rounded box with an accent edge. Returns the bottom."""
    width = W - 2 * M
    f = font(32, weight=QFont.Weight.Normal)
    inner = QRectF(M + 34, y + 22, width - 56, 400)
    p.setFont(f)
    height = p.boundingRect(inner, TOP_LEFT | Qt.TextFlag.TextWordWrap, value).height()
    box = QRectF(M, y, width, height + 44)
    fill = QColor(theme.SURFACE)
    fill.setAlpha(225)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(box, 22, 22)
    p.setBrush(QColor(accent))
    p.drawRoundedRect(QRectF(M, y + 14, 8, height + 16), 4, 4)
    text(p, inner, value, f)
    return box.bottom()


def art(
    p: QPainter,
    rect: QRectF,
    url: str | None,
    title: str,
    accent: str,
    pictures: Pictures,
    angle: float = 0.0,
    circle: bool = False,
) -> None:
    """A poster or cover cropped to fill `rect`, tilted by `angle`, with a soft shadow;
    a circle when `circle`. A lettered placeholder when there's no picture."""
    try:
        image = pictures(url) if url else None
    except Exception:
        image = None  # a picture that can't be had gets the placeholder, like on screen
    p.save()
    p.translate(rect.center())
    p.rotate(angle)
    local = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    if circle:
        p.drawEllipse(local.translated(0, 10))
    else:
        p.drawRoundedRect(local.translated(0, 10), 22, 22)
    clip = QPainterPath()
    if circle:
        clip.addEllipse(local)
    else:
        clip.addRoundedRect(local, 22, 22)
    p.setClipPath(clip)
    if image is not None and not image.isNull():
        scale = max(local.width() / image.width(), local.height() / image.height())
        w, h = image.width() * scale, image.height() * scale
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(-w / 2, -h / 2, w, h), image)
    else:
        p.fillRect(local, QColor(theme.RAISED))
        letter = (title.strip()[:1] or "?").upper()
        size = int(min(local.width(), local.height()) / 2.4)
        text(p, local, letter, font(size, heading=True), accent, CENTER)
    p.restore()


def plays(n: int) -> str:
    return f"{n:,} {'play' if n == 1 else 'plays'}"


def day_name(day: str) -> str:
    d = date.fromisoformat(day)
    weekday = activity.WEEKDAYS[(d.weekday() + 1) % 7]
    return f"{weekday}, {activity.MONTH_NAMES[d.month - 1]} {d.day}"


def _hour(h: int) -> str:
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def headline(p: QPainter, y: float, value: str, accent: str, size: int = 110) -> float:
    """A big gradient headline, shrunk to fit one line. Returns the bottom."""
    f = fitting(value, size, W - 2 * M)
    return y + gradient_text(p, M, y, value, f, accent, LIGHT.get(accent, theme.TEXT))


def subline(p: QPainter, y: float, value: str) -> float:
    f = font(36, weight=QFont.Weight.Normal)
    return y + text(p, QRectF(M, y, W - 2 * M, 200), value, f, theme.MUTED)


TILTS = (-6, 4, -2, 6, -4, 3, -5)
RISES = (0, 26, 8, 30, 4, 22, 12)


def fan(
    p: QPainter, y: float, items: list[Item], accent: str, pictures: Pictures, ratio: float = 1.45
) -> float:
    """Pictures fanned across the page, overlapping a little and tilted. Returns the bottom."""
    shown = items[:7]
    if not shown:
        return y
    width = 250 if len(shown) <= 3 else 210
    height = width * ratio
    step = (W - 2 * M - width) / (len(shown) - 1) if len(shown) > 1 else 0
    left = M if len(shown) > 1 else (W - width) / 2
    for i, item in enumerate(shown):
        angle = TILTS[i % 7] if len(shown) > 1 else -3
        rect = QRectF(left + i * step, y + RISES[i % 7], width, height)
        art(p, rect, item.picture, item.title, accent, pictures, angle)
    return y + height + 70


def bars(p: QPainter, top: float, rows: list[tuple[str, int]], color: str, first: int = 1) -> float:
    """Ranked rows: rank, name, a bar, and the number. Returns the bottom."""
    if not rows:
        return top
    most = max(n for _, n in rows) or 1
    width = W - 2 * M
    name_font = font(34, weight=QFont.Weight.DemiBold)
    y = top
    for rank, (name, n) in enumerate(rows, first):
        text(p, QRectF(M, y, 60, 50), f"{rank}", font(34, heading=True), color)
        text(p, QRectF(M + 64, y, width * 0.5, 50), elide(name, name_font, width * 0.5), name_font)
        bar = QRectF(M + 64 + width * 0.52, y + 12, (width * 0.3) * n / most, 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(bar, 13, 13)
        text(p, QRectF(M, y, width, 50), f"{n:,}", font(32), theme.MUTED, TOP_RIGHT)
        y += 64
    return y


# ---------------------------------------------------------------------------
# The parts. Each draws from y = 0 and returns how tall it was.
# ---------------------------------------------------------------------------


def hero(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    f = font(26)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 7)
    text(p, QRectF(M, 110, W, 40), "TASTE ENGINE  ·  REV", f, theme.MUTED)
    discs(p, QPointF(W - M - 110, 150), 74)
    title = data.period.short if data.period.kind == "month" else data.period.key
    y = 200 + gradient_text(p, M, 200, title, fitting(title, 200, W - 2 * M), theme.LILAC,
                            theme.CORAL)  # fmt: skip
    sub = data.period.key[:4] if data.period.kind == "month" else "Your year"
    y += text(p, QRectF(M, y, W, 90), sub, font(72, heading=True)) + 10
    if data.profile_name:
        y += text(p, QRectF(M, y, W, 50), f"for {data.profile_name}", font(32), theme.MUTED)
    if data.so_far:
        sticker(p, QPointF(W - M - 150, 420), f"{data.period.short} so far", theme.CORAL, -8)
    y += 60
    colors = {
        "shows finished": theme.LILAC,
        "episodes": theme.LILAC,
        "books read": theme.AMBER,
        "book read": theme.AMBER,
        "films rated": theme.TEAL,
        "film rated": theme.TEAL,
    }
    numbers = data.cover_numbers[:6]
    column = (W - 2 * M - 24) / 2
    for i, (value, caption) in enumerate(numbers):
        x = M + (i % 2) * (column + 24)
        top = y + (i // 2) * 230
        fill = QColor(theme.SURFACE)
        fill.setAlpha(210)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(x, top, column, 206), 28, 28)
        color = colors.get(caption, theme.CORAL)
        number_font = fitting(value, 96, column - 60, heading=False)
        number_font.setWeight(QFont.Weight.ExtraBold)
        gradient_text(p, x + 30, top + 24, value, number_font, color, LIGHT.get(color, color))
        text(p, QRectF(x + 30, top + 140, column - 60, 50), caption, font(30), theme.MUTED)
    return y + ((len(numbers) + 1) // 2) * 230 + 20


def music(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    m = data.music
    y = band(p, 0, "MUSIC", theme.CORAL) + 40
    y = headline(p, y, plays(m.plays), theme.CORAL)
    if m.hours is not None:
        y = subline(p, y + 6, f"{activity.about_hours(m.hours).capitalize()} of music")
    y += 60
    if m.top_artists:
        name, n = m.top_artists[0]
        glow(p, QPointF(M + 190, y + 190), 300, theme.CORAL, 120)
        art(p, QRectF(M + 10, y + 10, 360, 360), m.top_artist_picture, name, theme.CORAL,
            pictures, circle=True)  # fmt: skip
        sticker(p, QPointF(M + 330, y + 40), "No. 1", theme.CORAL, 10)
        x = M + 420
        text(p, QRectF(x, y + 60, W - x - M, 40), "YOUR TOP ARTIST", font(26), theme.MUTED)
        used = text(p, QRectF(x, y + 104, W - x - M, 220), name, font(64, heading=True))
        text(p, QRectF(x, y + 120 + used, W - x - M, 50), plays(n), font(36), theme.CORAL)
        y += 420
        if len(m.top_artists) > 1:
            y = bars(p, y, m.top_artists[1:], theme.CORAL, first=2) + 30
    if m.top_album:
        album, artist, n, url = m.top_album
        art(p, QRectF(M, y, 220, 220), url, album, theme.CORAL, pictures, angle=-4)
        x = M + 270
        text(p, QRectF(x, y + 16, W - x - M, 40), "ON REPEAT", font(26), theme.MUTED)
        used = text(p, QRectF(x, y + 56, W - x - M, 120), album, font(46, heading=True))
        text(p, QRectF(x, y + 66 + used, W - x - M, 50), f"{artist} · {plays(n)}", font(30),
             theme.MUTED)  # fmt: skip
        y += 270
    if m.top_track:
        track, artist, n = m.top_track
        y = chip(p, y, f"Top track: {track} by {artist}, {plays(n)}.", theme.CORAL) + 20
    if m.new_artists:
        first = f" The one you played most: {m.top_new_artist[0]}." if m.top_new_artist else ""
        count = "1 artist" if m.new_artists == 1 else f"{m.new_artists} artists"
        y = chip(p, y, f"{count} you'd never played before.{first}", theme.CORAL) + 20
    if m.busiest_day:
        day, n = m.busiest_day
        y = chip(p, y, f"Busiest day: {day_name(day)}, {plays(n)}.", theme.CORAL) + 20
    return y


def clock(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    m = data.music
    hours = m.hours_by_clock or [0] * 24
    peak = max(range(24), key=lambda h: (hours[h], -h))
    y = band(p, 0, "WHEN YOU LISTENED", theme.CORAL) + 40
    y = headline(p, y, f"Around {_hour(peak)}", theme.CORAL, 120) + 30
    center, inner, outer = QPointF(W / 2, y + 400), 140.0, 330.0
    glow(p, center, outer + 80, theme.CORAL, 50)
    most = max(hours) or 1
    p.setPen(QPen(QColor(theme.LINE), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(center, inner, inner)
    p.drawEllipse(center, outer, outer)
    for h, n in enumerate(hours):
        if not n:
            continue
        angle = math.radians(h * 15 - 90)  # midnight at the top, clockwise
        near, far = inner + 20, inner + 20 + max((outer - inner) * n / most - 20, 1)
        color = QColor(theme.CORAL if h == peak else LIGHT[theme.CORAL])
        color.setAlpha(255 if h == peak else 190)
        p.setPen(QPen(color, 30, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        dx, dy = math.cos(angle), math.sin(angle)
        p.drawLine(
            QPointF(center.x() + dx * near, center.y() + dy * near),
            QPointF(center.x() + dx * far, center.y() + dy * far),
        )
    for h, name in ((0, "12 AM"), (6, "6 AM"), (12, "12 PM"), (18, "6 PM")):
        angle = math.radians(h * 15 - 90)
        x = center.x() + math.cos(angle) * (outer + 52)
        ty = center.y() + math.sin(angle) * (outer + 52)
        text(p, QRectF(x - 80, ty - 22, 160, 44), name, font(28), theme.MUTED, CENTER)
    total = QRectF(center.x() - inner, center.y() - 60, 2 * inner, 120)
    text(p, total, f"{sum(hours):,}\nplays", font(44, heading=True), theme.TEXT, CENTER)
    return _day_strip(p, data, y + 860)


def _day_strip(p: QPainter, data: RecapData, top: float) -> float:
    """Plays per day: a calendar for a month, a strip of weeks for a year. Darker is more."""
    days = data.music.days
    if not days:
        return top
    most = max(n for _, n in days) or 1
    first = date.fromisoformat(days[0][0])
    offset = (first.weekday() + 1) % 7  # Sunday first
    text(p, QRectF(M, top, W - 2 * M, 44), "PLAYS PER DAY", font(26), theme.MUTED)
    p.setPen(Qt.PenStyle.NoPen)
    if data.period.kind == "month":
        size, gap = 116.0, 10.0
        y0 = top + 56
        for i, letter in enumerate(WEEKDAY_LETTERS):
            box = QRectF(M + i * (size + gap), y0, size, 34)
            text(p, box, letter, font(24), theme.MUTED, CENTER)
        y0 += 42
        for i, (_, n) in enumerate(days):
            x = M + ((i + offset) % 7) * (size + gap)
            p.setBrush(_heat(n, most))
            p.drawRoundedRect(QRectF(x, y0 + ((i + offset) // 7) * (60 + gap), size, 60), 12, 12)
        weeks = (len(days) + offset + 6) // 7
        return y0 + weeks * (60 + gap) + 20
    weeks = math.ceil((len(days) + offset) / 7)
    size = (W - 2 * M - (weeks - 1) * 3) / weeks
    y0 = top + 60
    for i, (_, n) in enumerate(days):
        x = M + ((i + offset) // 7) * (size + 3)
        p.setBrush(_heat(n, most))
        p.drawRoundedRect(QRectF(x, y0 + ((i + offset) % 7) * (size + 3), size, size), 3, 3)
    return y0 + 7 * (size + 3) + 20


def _heat(n: int, most: int) -> QColor:
    if not n:
        return QColor(theme.RAISED)
    color = QColor(theme.CORAL)
    color.setAlpha(int(60 + 195 * n / most))
    return color


def anime(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    a = data.anime
    y = band(p, 0, "ANIME", theme.LILAC) + 40
    finished = "1 show finished" if a.finished == 1 else f"{a.finished} shows finished"
    y = headline(p, y, finished, theme.LILAC)
    if a.episodes:
        eps = f"{a.episodes:,} {'episode' if a.episodes == 1 else 'episodes'}"
        if a.hours:
            eps += f", {activity.about_hours(a.hours)}"
        y = subline(p, y + 6, eps)
    posters = [Item(s.title, s.poster_url, s.my_score, s.community_mean) for s in a.posters]
    y = fan(p, y + 50, posters, theme.LILAC, pictures)
    if a.best:
        line = f"Your favorite: {a.best.title}, you gave it {a.best.my_score:g}."
        y = chip(p, y, line, theme.LILAC) + 20
    if a.avg_diff is not None:
        direction = "above" if a.avg_diff > 0 else "below"
        level = f", level on {a.level}" if a.level else ""
        line = (
            f"You averaged {abs(a.avg_diff):.2f} points {direction} MAL. Next to the MAL score "
            f"rounded to a whole point, you were higher on {a.above}{level} and lower on "
            f"{a.below}."
        )
        y = chip(p, y, line, theme.LILAC) + 20
    if a.top_genre:
        genre, n = a.top_genre
        line = f"Most watched genre: {genre}, {n} {'show' if n == 1 else 'shows'}."
        y = chip(p, y, line, theme.LILAC) + 20
    return y


def books(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    b = data.books
    y = band(p, 0, "BOOKS", theme.AMBER) + 40
    y = headline(p, y, "1 book read" if b.read == 1 else f"{b.read} books read", theme.AMBER)
    if b.pages:
        pages = f"{b.pages:,} pages"
        if b.unknown_pages:
            pages += f", plus {b.unknown_pages} without a page count"
        y = subline(p, y + 6, pages)
    y = fan(p, y + 50, b.covers, theme.AMBER, pictures, ratio=1.5)
    if b.best:
        y = chip(p, y, f"Your favorite: {b.best.title}, {b.best.mine:g} stars.", theme.AMBER) + 20
    if b.avg_diff is not None:
        direction = "above" if b.avg_diff > 0 else "below"
        line = f"You averaged {abs(b.avg_diff):.2f} stars {direction} everyone else."
        y = chip(p, y, line, theme.AMBER) + 20
    if b.top_author and b.top_author[1] > 1:
        line = f"Most read author: {b.top_author[0]}, {b.top_author[1]} books."
        y = chip(p, y, line, theme.AMBER) + 20
    return y


def _screen(p: QPainter, s, what: str, noun: str, pictures: Pictures) -> float:
    y = band(p, 0, what, theme.TEAL) + 40
    count = f"1 {noun} rated" if s.rated == 1 else f"{s.rated} {noun}s rated"
    y = headline(p, y, count, theme.TEAL)
    y = subline(p, y + 6, "Counted by the day you rated them on TMDB.")
    y = fan(p, y + 50, s.posters, theme.TEAL, pictures, ratio=1.5)
    if s.best:
        line = f"Your favorite: {s.best.title}, you rated it {s.best.mine:g}."
        y = chip(p, y, line, theme.TEAL) + 20
    if s.avg_diff is not None:
        direction = "above" if s.avg_diff > 0 else "below"
        line = f"You averaged {abs(s.avg_diff):.1f} points {direction} TMDB."
        y = chip(p, y, line, theme.TEAL) + 20
    if s.top_person:
        role = "director" if noun == "film" else "creator"
        line = f"Most rated {role}: {s.top_person[0]}, {s.top_person[1]} {noun}s."
        y = chip(p, y, line, theme.TEAL) + 20
    elif s.top_decade and s.top_decade[1] > 1:
        line = f"Most of them from the {s.top_decade[0]}: {s.top_decade[1]}."
        y = chip(p, y, line, theme.TEAL) + 20
    return y


def films(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    return _screen(p, data.films, "FILMS", "film", pictures)


def tv(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    return _screen(p, data.tv, "TV", "show", pictures)


def compare(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    previous = data.period.previous()
    name = previous.short if data.period.kind == "month" else previous.key
    y = band(p, 0, f"AGAINST {name.upper()}", theme.LILAC) + 50
    for line in data.compare:
        up = any(w in line for w in ("more", "twice", "times as much"))
        down = any(w in line for w in ("less", "fewer"))
        arrow = "↑" if up else "↓" if down else "="
        color = theme.CORAL
        for word, accent in (("show", theme.LILAC), ("book", theme.AMBER), ("film", theme.TEAL)):
            if word in line:
                color = accent
        text(p, QRectF(M, y - 8, 90, 90), arrow, font(80, heading=True), color)
        used = text(p, QRectF(M + 110, y, W - 2 * M - 110, 300), line, font(44, heading=True))
        y += max(used, 80) + 40
    return y


def year(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    v = data.year
    y = band(p, 0, "THE YEAR", theme.CORAL) + 40
    y = headline(p, y, "Month by month", theme.CORAL, 100) + 30
    ribbons = [
        ("PLAYS", v.plays_by_month, theme.CORAL),
        ("SHOWS FINISHED", v.shows_by_month, theme.LILAC),
        ("BOOKS READ", v.books_by_month, theme.AMBER),
        ("FILMS RATED", v.films_by_month, theme.TEAL),
    ]
    width = (W - 2 * M - 11 * 14) / 12
    for title, values, color in (r for r in ribbons if any(r[1])):
        text(p, QRectF(M, y, W - 2 * M, 40), title, font(26), theme.MUTED)
        most = max(values) or 1
        for i, n in enumerate(values):
            h = max(130 * n / most, 4)
            x = M + i * (width + 14)
            gradient = QLinearGradient(0, y + 180 - h, 0, y + 180)
            gradient.setColorAt(0, QColor(LIGHT.get(color, color)))
            gradient.setColorAt(1, QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(gradient) if n else QColor(theme.RAISED))
            p.drawRoundedRect(QRectF(x, y + 180 - h, width, h), 8, 8)
            month = activity.MONTH_NAMES[i][0]
            text(p, QRectF(x, y + 188, width, 34), month, font(24), theme.MUTED, CENTER)
        y += 250
    if v.busiest_month:
        month, n = v.busiest_month
        y = chip(p, y, f"You played most in {month}: {plays(n)}.", theme.CORAL) + 30
    if v.top_artists:
        text(p, QRectF(M, y, W - 2 * M, 40), "TOP ARTISTS", font(26), theme.MUTED)
        y = bars(p, y + 50, v.top_artists, theme.CORAL) + 20
    if v.top_shows:
        text(p, QRectF(M, y, W - 2 * M, 40), "TOP SHOWS", font(26), theme.MUTED)
        y = bars(p, y + 50, [(s.title, int(s.my_score)) for s in v.top_shows], theme.LILAC) + 20
    if v.first_finish and v.last_finish and v.first_finish != v.last_finish:
        line = f"First finish of the year: {v.first_finish.title}. Last: {v.last_finish.title}."
        y = chip(p, y, line, theme.LILAC) + 20
    return y


def closing(p: QPainter, data: RecapData, pictures: Pictures) -> float:
    discs(p, QPointF(W / 2, 190), 130)
    y = 390.0
    rect = QRectF(M, y, W - 2 * M, 700)
    big = font(76, heading=True)
    p.setFont(big)
    flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
    used = QRectF(QFontMetrics(big).boundingRect(rect.toRect(), int(flags.value), data.closing))
    gradient = QLinearGradient(used.topLeft(), used.bottomRight())
    gradient.setColorAt(0, QColor(theme.LILAC))
    gradient.setColorAt(1, QColor(theme.CORAL))
    p.setPen(QPen(QBrush(gradient), 1))
    p.drawText(rect, flags, data.closing)
    y += used.height() + 70
    text(p, QRectF(M, y, W - 2 * M, 50), "Made with Taste Engine", font(34), theme.MUTED,
         TOP_CENTER)  # fmt: skip
    line = data.period.label + (f"  ·  {data.profile_name}" if data.profile_name else "")
    text(p, QRectF(M, y + 50, W - 2 * M, 50), line, font(28, weight=QFont.Weight.Normal),
         theme.MUTED, TOP_CENTER)  # fmt: skip
    return y + 170


PAINTERS = {
    "cover": hero,
    "music": music,
    "clock": clock,
    "anime": anime,
    "books": books,
    "films": films,
    "tv": tv,
    "compare": compare,
    "year": year,
    "closing": closing,
}
ACCENT = {
    "cover": theme.LILAC,
    "music": theme.CORAL,
    "clock": theme.CORAL,
    "anime": theme.LILAC,
    "books": theme.AMBER,
    "films": theme.TEAL,
    "tv": theme.TEAL,
    "compare": theme.LILAC,
    "year": theme.CORAL,
    "closing": theme.LILAC,
}


def _strip(name: str, data: RecapData, pictures: Pictures) -> tuple[QImage, float]:
    """One part drawn on a transparent strip, and how tall it came out."""
    image = QImage(W, STRIP_MAX, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        height = PAINTERS[name](painter, data, pictures)
    finally:
        # Always end it: an image dropped with a painter still open takes the app down.
        painter.end()
    return image, min(max(height, 1), STRIP_MAX)


def _card(
    data: RecapData, index: int, count: int, parts: list[str], strips: dict[str, tuple]
) -> QImage:
    """One phone-sized card: its parts stacked and centered (scaled down if they'd
    overflow), on a glow in the card's colors, with the page dots along the bottom."""
    image = QImage(W, H, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(0, 0, W, H, QColor(theme.BG))
        accent = ACCENT[parts[0]]
        other = theme.CORAL if accent != theme.CORAL else theme.LILAC
        glow(painter, QPointF(W * (0.9 if index % 2 == 0 else 0.1), H * 0.22), 900, accent, 85)
        glow(painter, QPointF(W * (0.1 if index % 2 == 0 else 0.9), H * 0.85), 700, other, 45)
        cover = parts[0] == "cover"
        if not cover:
            f = font(24)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
            text(painter, QRectF(M, 64, W, 40), "TASTE ENGINE  ·  REV", f, theme.MUTED)
            text(painter, QRectF(M, 64, W - 2 * M, 40), data.period.label, font(26),
                 theme.MUTED, TOP_RIGHT)  # fmt: skip
        top, bottom = (40, H - 130) if cover else (140, H - 130)
        heights = [strips[part][1] for part in parts]
        total = sum(heights) + GAP * (len(parts) - 1)
        scale = min(1.0, (bottom - top) / total)
        y = top + ((bottom - top) - total * scale) / 2
        x = (W - W * scale) / 2
        for part, height in zip(parts, heights, strict=True):
            strip = strips[part][0]
            painter.drawImage(
                QRectF(x, y, W * scale, height * scale), strip, QRectF(0, 0, W, height)
            )
            y += (height + GAP) * scale
        # Page dots: which card of how many.
        dot, step = 14, 34
        left = (W - (count - 1) * step) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(count):
            color = QColor(accent if i == index else theme.LINE)
            painter.setBrush(color)
            r = dot / 2 + (3 if i == index else 0)
            painter.drawEllipse(QPointF(left + i * step, H - 72), r, r)
        rule = QLinearGradient(0, 0, W, 0)
        rule.setColorAt(0, QColor(theme.LILAC))
        rule.setColorAt(1, QColor(theme.CORAL))
        painter.fillRect(QRectF(0, H - 10, W, 10), QBrush(rule))
    finally:
        painter.end()
    return image


def render(data: RecapData, pictures: Pictures | None = None) -> list[tuple[str, QImage]]:
    """The recap as phone-sized cards (1080 x 1920), in order: [(card name, image)]."""
    pictures = pictures or (lambda url: None)
    strips = {name: _strip(name, data, pictures) for name in data.cards}
    slides = data.slides
    return [
        (name, _card(data, i, len(slides), parts, strips)) for i, (name, parts) in enumerate(slides)
    ]
