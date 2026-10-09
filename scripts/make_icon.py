"""Render the logo SVG into packaging/taste-engine.ico (16 to 256 px).

Run after changing taste/desktop/assets/logo.svg:
    python scripts/make_icon.py
Needs: pip install -r requirements-build.txt
"""

from __future__ import annotations

import io
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "taste" / "desktop" / "assets" / "logo.svg"
ICO = ROOT / "packaging" / "taste-engine.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> Image.Image:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(SVG)).render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def main() -> None:
    QGuiApplication([])
    largest = render(256)
    largest.save(ICO, sizes=[(s, s) for s in SIZES])
    print(f"Wrote {ICO.relative_to(ROOT)} ({ICO.stat().st_size:,} bytes, sizes {SIZES})")


if __name__ == "__main__":
    main()
