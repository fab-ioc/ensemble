"""Draws the app icons the web manifest and the pages name (static/icons/) from
one of the concepts in static/icons/src/, each a 512 SVG with a full-bleed
<g id="ground"> and a <g id="motif"> kept inside the middle 80% circle.

    py tools/make_app_icons.py                  # the shipped concept
    py tools/make_app_icons.py b-chord          # another one
    py tools/make_app_icons.py --preview DIR    # every concept, for comparing

* icon-192.png, icon-512.png ("any"): the ground as a tile with rounded
  corners, transparent outside it, the motif as drawn;
* icon-maskable-512.png ("maskable") and apple-touch-icon.png (180): the
  ground to every edge (a platform cuts its own mask; iOS rounds the corners
  and shows transparency as black), the motif inside the safe zone;
* favicon.svg, favicon.ico (16, 32, 48): the tile to the edges and the motif
  larger, so it still reads in a browser tab and a taskbar.

Renders with headless Chrome (or Edge) at 1024 and scales down with Pillow.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "icons"
SRC = OUT / "src"
SHIPPED = "c-lanes"
RENDER = 1024
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "chromium", "chrome",
]


def browser() -> str:
    for b in BROWSERS:
        if os.path.isfile(b) or shutil.which(b):
            return b
    raise SystemExit("needs Chrome, Edge or Chromium to render the SVGs")


def parts(concept: str) -> dict[str, str]:
    text = (SRC / f"{concept}.svg").read_text(encoding="utf-8")
    pick = lambda pat: (re.search(pat, text, re.S) or [""])[0]
    return {"defs": pick(r"<defs>.*?</defs>"),
            "ground": pick(r'<g id="ground".*?</g>'),
            "motif": pick(r'<g id="motif".*?</g>')}


def compose(concept: str, kind: str) -> str:
    """kind: "full" (maskable, apple-touch), "tile" (any) or "favicon"."""
    p = parts(concept)
    if kind == "full":
        body = p["ground"] + p["motif"]
    else:
        inset, radius, scale = (32, 100, 1) if kind == "tile" else (0, 112, 1.28)
        side = 512 - 2 * inset
        clip = (f'<clipPath id="tile"><rect x="{inset}" y="{inset}" width="{side}" height="{side}" '
                f'rx="{radius}"/></clipPath>')
        p["defs"] = p["defs"].replace("<defs>", "<defs>" + clip, 1) if p["defs"] else f"<defs>{clip}</defs>"
        body = (f'<g clip-path="url(#tile)">{p["ground"]}</g>'
                f'<g transform="translate(256 256) scale({scale}) translate(-256 -256)">{p["motif"]}</g>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{p["defs"]}{body}</svg>\n'


def render(svg: str, work: Path) -> Image.Image:
    src, png = work / "in.svg", work / "out.png"
    src.write_text(svg.replace('viewBox="0 0 512 512"', f'width="{RENDER}" height="{RENDER}" viewBox="0 0 512 512"', 1),
                   encoding="utf-8")
    png.unlink(missing_ok=True)
    subprocess.run([browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--default-background-color=00000000", f"--window-size={RENDER},{RENDER}",
                    f"--user-data-dir={work / 'profile'}", f"--screenshot={png}", src.as_uri()],
                   capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    im = Image.open(png).convert("RGBA")
    im.load()
    return im


def sized(im: Image.Image, px: int) -> Image.Image:
    return im.resize((px, px), Image.LANCZOS)


def ship(concept: str, work: Path) -> None:
    full, tile = render(compose(concept, "full"), work), render(compose(concept, "tile"), work)
    fav_svg = compose(concept, "favicon")
    fav = render(fav_svg, work)
    sized(tile, 192).save(OUT / "icon-192.png", optimize=True)
    sized(tile, 512).save(OUT / "icon-512.png", optimize=True)
    sized(full, 512).convert("RGB").save(OUT / "icon-maskable-512.png", optimize=True)
    sized(full, 180).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    (OUT / "favicon.svg").write_text(fav_svg, encoding="utf-8")
    sized(fav, 48).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("shipped", concept, "->", ", ".join(sorted(p.name for p in OUT.iterdir() if p.is_file())))


def preview(dest: Path, work: Path) -> None:
    """Per concept: 512/64 (tile), 32/16 (favicon) PNGs, and one sheet of them
    all on a light and a dark ground, with the 32 and 16 also shown 4x."""
    dest.mkdir(parents=True, exist_ok=True)
    concepts = sorted(p.stem for p in SRC.glob("*.svg"))
    grounds = [(0xF7, 0xF8, 0xF9, 255), (0x1D, 0x21, 0x25, 255)]
    pad, panel_w, row_h = 24, 512 + 64 + 32 + 16 + 128 + 64 + 8 * 24, 512 + 48
    sheet = Image.new("RGBA", (2 * panel_w, len(concepts) * row_h), (0, 0, 0, 0))
    for r, c in enumerate(concepts):
        tile, fav = render(compose(c, "tile"), work), render(compose(c, "favicon"), work)
        icons = {512: sized(tile, 512), 64: sized(tile, 64), 32: sized(fav, 32), 16: sized(fav, 16)}
        for px, im in icons.items():
            im.save(dest / f"{c}-{px}.png", optimize=True)
        sized(render(compose(c, "full"), work), 512).convert("RGB").save(dest / f"{c}-maskable-512.png", optimize=True)
        zoom = [icons[32].resize((128, 128), Image.NEAREST), icons[16].resize((64, 64), Image.NEAREST)]
        for g, colour in enumerate(grounds):
            x0, y0 = g * panel_w, r * row_h
            sheet.paste(Image.new("RGBA", (panel_w, row_h), colour), (x0, y0))
            x = x0 + pad
            for im in [icons[512], icons[64], icons[32], icons[16], *zoom]:
                sheet.alpha_composite(im, (x, y0 + (row_h - im.height) // 2))
                x += im.width + pad
    sheet.save(dest / "sheet.png", optimize=True)
    print("previews", ", ".join(concepts), "->", dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("concept", nargs="?", default=SHIPPED)
    ap.add_argument("--preview", type=Path)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as t:
        if a.preview:
            preview(a.preview, Path(t))
        else:
            ship(a.concept, Path(t))


if __name__ == "__main__":
    main()
