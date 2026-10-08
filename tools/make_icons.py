"""Draws the Ledgerline app icons (PNG) into web/public/icons. Needs: pip install pillow.
The mark is three ledger rules; the shortest, in brass, is the open item still to do.
"""
from pathlib import Path

from PIL import Image, ImageDraw

TEAL, WHITE, BRASS = "#1F6B67", "#FFFFFF", "#E3B341"
OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "icons"


def mark(size: int, *, rounded: bool, scale: float) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=TEAL)
    else:
        d.rectangle([0, 0, size, size], fill=TEAL)
    unit = size * scale / 64
    ox = oy = size * (1 - scale) / 2
    for x, y, w, color in [(14, 16, 36, WHITE), (14, 29, 28, WHITE), (14, 42, 18, BRASS)]:
        d.rounded_rectangle(
            [ox + x * unit, oy + y * unit, ox + (x + w) * unit, oy + (y + 6) * unit],
            radius=3 * unit, fill=color,
        )
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    mark(192, rounded=True, scale=1.0).save(OUT / "icon-192.png")
    mark(512, rounded=True, scale=1.0).save(OUT / "icon-512.png")
    mark(512, rounded=False, scale=0.8).save(OUT / "icon-maskable-512.png")  # safe zone
    mark(180, rounded=False, scale=1.0).save(OUT / "apple-touch-icon.png")
    print("icons written to", OUT)
