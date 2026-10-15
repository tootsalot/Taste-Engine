"""Draw the installer's wizard images into packaging/wizard/ (PNG, one per DPI size).

The side panel comes in light/ and dark/ versions (setup follows Windows' theme); the
corner logo is shared. Run after changing the logo or the colors:
    python scripts/make_wizard_images.py
Needs: pip install -r requirements.txt (Qt draws them; the sizes are Inno Setup's).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "taste" / "desktop" / "assets" / "fonts"
OUT = ROOT / "packaging" / "wizard"
# Inno Setup's image areas at 100% to 250% DPI. It picks the closest file.
SIDE_SIZES = [(202, 386), (269, 515), (336, 643), (430, 824), (534, 1022)]
CORNER_SIZES = [58, 77, 97, 124, 159]
# The app's palette (taste/desktop/theme.py) and logo (taste/desktop/assets/logo.svg).
LILAC, CORAL, OVERLAP = "#B69CFF", "#FF7A59", "#FFE3F1"
LOOKS = {"dark": {"text": "#EDEAF7", "glow": 90}, "light": {"text": "#1B1929", "glow": 60}}


def discs(painter: QPainter, center: QPointF, radius: float) -> None:
    """The logo's two discs, lilac (anime) and coral (music), with the overlap lit."""
    offset = radius * 10 / 22  # as in logo.svg: r 22, centers 20 apart
    left, right = QPainterPath(), QPainterPath()
    left.addEllipse(center + QPointF(-offset, 0), radius, radius)
    right.addEllipse(center + QPointF(offset, 0), radius, radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(left, QColor(LILAC))
    painter.fillPath(right, QColor(CORAL))
    painter.fillPath(left.intersected(right), QColor(OVERLAP))


def glow(painter: QPainter, center: QPointF, reach: float, color: str, alpha: int) -> None:
    """A soft tall oval of color that fades out `reach` px to each side, before the edge."""
    gradient = QRadialGradient(QPointF(0, 0), reach)
    tint = QColor(color)
    tint.setAlpha(alpha)
    gradient.setColorAt(0, tint)
    tint.setAlpha(0)
    gradient.setColorAt(1, tint)
    painter.save()
    painter.translate(center)
    painter.scale(1, 1.7)
    painter.fillRect(QRectF(-reach, -reach, 2 * reach, 2 * reach), gradient)
    painter.restore()


def side_image(width: int, height: int, look: dict) -> QImage:
    """The Welcome and Finished pages' left panel: discs over a soft glow, and the name.

    Transparent, so it sits on whatever background the wizard has in Windows' theme.
    """
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    radius = width * 0.2
    center = QPointF(width / 2, height * 0.40)
    # Each glow fades to nothing at the image's edge, so no seam shows against the page.
    shift = width * 0.1
    glow(painter, center + QPointF(-shift, 0), width / 2 - shift, LILAC, look["glow"])
    glow(painter, center + QPointF(shift, 0), width / 2 - shift, CORAL, look["glow"] * 3 // 4)
    discs(painter, center, radius)

    font = QFont("Space Grotesk")
    font.setPixelSize(round(width * 0.115))
    font.setWeight(QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor(look["text"]))
    text_top = center.y() + radius * 1.45
    painter.drawText(
        QRectF(0, text_top, width, font.pixelSize() * 1.6),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        "Taste Engine",
    )
    painter.end()
    return image


def corner_image(size: int) -> QImage:
    """The top-right logo on the inner pages: just the discs, on transparent."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    discs(painter, QPointF(size / 2, size / 2), size * 0.27)
    painter.end()
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    QGuiApplication([])
    QFontDatabase.addApplicationFont(str(FONTS / "SpaceGrotesk.ttf"))
    for name, look in LOOKS.items():
        folder = args.out / name
        folder.mkdir(parents=True, exist_ok=True)
        for width, height in SIDE_SIZES:
            side_image(width, height, look).save(str(folder / f"side-{width}x{height}.png"))
    for size in CORNER_SIZES:
        corner_image(size).save(str(args.out / f"corner-{size}.png"))
    images = list(args.out.rglob("*.png"))
    total = sum(p.stat().st_size for p in images)
    print(f"Wrote {len(images)} images to {args.out} ({total:,} bytes)")


if __name__ == "__main__":
    main()
