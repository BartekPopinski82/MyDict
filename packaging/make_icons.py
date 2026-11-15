"""Generuje ikony programu z logo wektorowego (zasoby/logo.svg).

    python packaging/make_icons.py

Wynik: ``zasoby/ikona.ico`` (Windows), ``zasoby/ikona.icns`` (macOS) i
``zasoby/ikona.png`` (1024 px). Ikony są w repozytorium — skrypt uruchamia się
tylko po zmianie logo. Wymaga PySide6 i Pillow (requirements-build.txt).
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "zasoby"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(renderer: QSvgRenderer, size: int, inset: float = 0.0) -> Image.Image:
    """Rysuje logo w podanym rozmiarze; ``inset`` zostawia margines (wymóg ikon macOS)."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    margin = size * inset
    renderer.render(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 — Qt wymaga obiektu aplikacji
    renderer = QSvgRenderer(str(ASSETS / "logo.svg"))
    if not renderer.isValid():
        sys.stderr.write("Nie można wczytać zasoby/logo.svg\n")
        return 1
    render(renderer, 1024).save(ASSETS / "ikona.png")
    frames = [render(renderer, size) for size in ICO_SIZES]
    frames[-1].save(ASSETS / "ikona.ico", format="ICO", append_images=frames[:-1],
                    sizes=[(size, size) for size in ICO_SIZES])
    # macOS: kształt ikony zajmuje ok. 80% płótna (wytyczne Apple), reszta to margines.
    render(renderer, 1024, inset=0.10).save(ASSETS / "ikona.icns", format="ICNS")
    sys.stdout.write("Zapisano ikona.png, ikona.ico, ikona.icns\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
