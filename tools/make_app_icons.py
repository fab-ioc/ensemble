"""Draws the app icons the web manifest names (static/icons/), from the logo in
the top bar of index.html: three circles joined as a triangle, in the default
accent, the joining lines at 55% (here mixed with the ground, as it shows).

    py tools/make_app_icons.py

* icon-192.png, icon-512.png ("any"): the mark on a white tile with rounded
  corners and a hairline border, transparent outside the tile;
* icon-maskable-512.png ("maskable"): the white ground to every edge, the mark
  well inside the middle 80% circle, so any mask the platform cuts keeps it;
* apple-touch-icon.png (180): the same as the maskable one, since iOS rounds
  the corners itself and shows transparency as black.

Needs Pillow. Drawn at 4x and scaled down, for smooth edges.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
ACCENT = (0x0C, 0x66, 0xE4)   # --accent, the default
GROUND = (0xFF, 0xFF, 0xFF)   # --surface in Light
EDGE = (0xDC, 0xDF, 0xE4)     # --border in Light
LINE = tuple(round(a * 0.55 + g * 0.45) for a, g in zip(ACCENT, GROUND))
SS = 4
# The logo's own units (a 24 box): circle centres, radius, line width; the
# mark's middle is (12, 11.5) and it spans 18.4 units.
NODES = [(12, 5), (5.5, 18), (18.5, 18)]
R, W, MID, SPAN = 2.7, 1.6, (12, 11.5), 18.4


def mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, span: float) -> None:
    k = span / SPAN
    at = lambda x, y: (cx + (x - MID[0]) * k, cy + (y - MID[1]) * k)
    pts = [at(*n) for n in NODES]
    w = max(1, round(W * k))
    for a, b in ((0, 1), (0, 2), (1, 2)):
        draw.line([pts[a], pts[b]], fill=LINE, width=w)
    for x, y in pts:
        draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=LINE)   # round caps
    for x, y in pts:
        draw.ellipse([x - R * k, y - R * k, x + R * k, y + R * k], fill=ACCENT)


def tile(size: int) -> Image.Image:
    s = size * SS
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = round(s * 0.06)
    d.rounded_rectangle([pad, pad, s - pad - 1, s - pad - 1], radius=round(s * 0.2), fill=GROUND,
                        outline=EDGE, width=max(SS, round(s * 0.008)))
    mark(d, s / 2, s / 2, s * 0.56)
    return im.resize((size, size), Image.LANCZOS)


def full(size: int) -> Image.Image:
    s = size * SS
    im = Image.new("RGB", (s, s), GROUND)
    mark(ImageDraw.Draw(im), s / 2, s / 2, s * 0.50)
    return im.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tile(192).save(OUT / "icon-192.png", optimize=True)
    tile(512).save(OUT / "icon-512.png", optimize=True)
    full(512).save(OUT / "icon-maskable-512.png", optimize=True)
    full(180).save(OUT / "apple-touch-icon.png", optimize=True)
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
