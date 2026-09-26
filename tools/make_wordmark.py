"""Draws the top bar's wordmark (index.html, #bar-home): the word ENSEMBLE in
the app icon's language, letters built from the "Lanes E"'s rounded bars.

    py tools/make_wordmark.py                  # write the shipped variant into index.html
    py tools/make_wordmark.py lanes            # write another one
    py tools/make_wordmark.py --preview DIR    # every variant in every theme, PNGs + contrast table

Every letter sits on the icon's grid: three bars 76 tall (the lanes, at y 0,
106 and 212) and stems 84 wide, 288 units in all, the size of the E inside the
icon. A shape's role says which lane or stem it is, and a variant colours the
roles:

* tile:     the icon itself is the E, the other letters in the text colour
            (--fg). Reads in every theme by construction.
* lanes:    every E is the icon's, its bars coral, mint and amber, and every
            other letter and each E's spine in --fg.
* gradient: the whole word in the icon's blue-to-violet.

Colours are tokens (--wm-*, in each theme block of index.html), so the SVG is
inline and follows the theme. The page shows the icon alone (img.logo) where
the word does not fit.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
SHIPPED = "tile"
VARIANTS = ("tile", "lanes", "gradient")
CAP = 288            # the letters' height, the icon's E
T, V = 76, 84        # a bar's height, a stem's width
ROWS = (0, 106, 212) # the three lanes
GAP = 48             # between letters
TILE = 400           # the icon's tile around a 288 E (favicon.svg draws its E at 72% of the tile)
BEGIN, END = "<!-- wordmark: tools/make_wordmark.py -->", "<!-- /wordmark -->"


def bar(row, x, w):
    return ("bar%d" % row, ("rect", x, ROWS[row], w, T, T / 2))


def stem(x, y=0, h=CAP, role="stem"):
    return (role, ("rect", x, y, V, h, 30))


def diag(*pts):
    return ("diag", ("poly", pts))


# (width, shapes), drawn in order. A letter's spine goes over its bars, as the
# icon's white spine sits over its bars; the short links that join two bars
# (S, B) go under them, so a lane stays whole.
LETTERS = {
    "E": (200, [bar(0, 40, 160), bar(1, 40, 128), bar(2, 40, 160), stem(0)]),
    "N": (236, [diag((12, 0), (84, 0), (224, 288), (152, 288)), stem(0), stem(152)]),
    "S": (200, [stem(0, 0, 182, "link"), stem(116, 106, 182, "link"), bar(0, 0, 200), bar(1, 0, 200), bar(2, 0, 200)]),
    "M": (300, [diag((12, 0), (84, 0), (192, 236), (108, 236)), diag((288, 0), (216, 0), (108, 236), (192, 236)),
                stem(0), stem(216)]),
    "B": (216, [stem(112, 0, 182, "link"), stem(132, 106, 182, "link"), bar(0, 40, 156), bar(1, 40, 176), bar(2, 40, 176),
                stem(0)]),
    "L": (176, [bar(2, 40, 136), stem(0)]),
}
WORD = "ENSEMBLE"
CLS = {
    "tile": {},
    "lanes": {"bar0": "wm-l1", "bar1": "wm-l2", "bar2": "wm-l3"},
    "gradient": {},
}
INK = {"tile": "wm-ink", "lanes": "wm-ink", "gradient": None}


def shape_svg(s, dx, cls):
    kind = s[0]
    cls = f'class="{cls}"' if cls else 'fill="url(#wm-g)"'
    if kind == "rect":
        _, x, y, w, h, r = s
        return f'<rect {cls} x="{x + dx:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{r:g}"/>'
    pts = " ".join(f"{x + dx:g},{y:g}" for x, y in s[1])
    return f'<polygon {cls} points="{pts}"/>'


def svg(variant: str, cap_px: float) -> str:
    """The inline SVG, cap_px tall letters (the icon's tile is 400/288 of that)."""
    parts, x = [], 0
    for i, ch in enumerate(WORD):
        w, shapes = LETTERS[ch]
        if variant == "tile" and i == 0:
            parts.append(f'<image href="/static/icons/favicon.svg" x="0" y="{-(TILE - CAP) / 2:g}" width="{TILE}" height="{TILE}"/>')
            x += TILE + GAP
            continue
        for role, s in shapes:
            lane = CLS[variant].get(role) if ch == "E" else None   # lanes: each E is the icon's
            parts.append(shape_svg(s, x, lane or INK[variant]))
        x += w + GAP
    width = x - GAP
    top, height = (-(TILE - CAP) / 2, TILE) if variant == "tile" else (0, CAP)
    scale = cap_px / CAP
    defs = ""
    if variant == "gradient":
        defs = ('<defs><linearGradient id="wm-g" gradientUnits="userSpaceOnUse" x1="0" y1="0" '
                f'x2="{width:g}" y2="0"><stop class="wm-from" offset="0"/><stop class="wm-to" offset="1"/>'
                '</linearGradient></defs>')
    return (f'<svg class="bar-word wm-{variant}" viewBox="0 {top:g} {width:g} {height:g}" '
            f'width="{width * scale:.1f}" height="{height * scale:.1f}" aria-hidden="true" focusable="false">'
            f'{defs}{"".join(parts)}</svg>')


# The size in the bar: the icon 22px, as it is on a phone's bar, so the letters
# are 15.84px, the E inside it.
CAP_PX = 22 * CAP / TILE


def ship(variant: str) -> None:
    text = PAGE.read_text(encoding="utf-8")
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pat.search(text):
        raise SystemExit(f"no {BEGIN} … {END} block in {PAGE}")
    block = f"{BEGIN}{svg(variant, CAP_PX)}{END}"
    PAGE.write_text(pat.sub(lambda m: block, text, count=1), encoding="utf-8", newline="")
    print("shipped", variant, "->", PAGE.name)


# ---- preview: every variant on every theme's bar, and the contrast of each colour

THEMES = ("light", "paper", "contrast", "dark", "dim", "fjord")


def theme_tokens() -> dict[str, dict[str, str]]:
    """Each theme's tokens as index.html declares them: comments stripped, the
    last declaration of a name wins, a theme on top of :root."""
    css = re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.S)
    blocks = {"light": re.search(r":root\s*\{(.*?)\}", css, re.S).group(1)}
    for m in re.finditer(r':root\[data-theme="(\w+)"\]\s*\{(.*?)\}', css, re.S):
        blocks.setdefault(m.group(1), m.group(2))
    decl = lambda b: dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", b))
    base = decl(blocks["light"])
    return {t: {**base, **decl(blocks[t])} if t != "light" else base for t in THEMES}


def lum(h: str) -> float:
    h = h.strip().lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def ratio(a: str, b: str) -> float:
    x, y = sorted([lum(a), lum(b)])
    return (y + .05) / (x + .05)


WM_TOKENS = ("--fg", "--wm-lane-1", "--wm-lane-2", "--wm-lane-3", "--wm-from", "--wm-to")


def preview(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    tok = theme_tokens()
    style = ["body{margin:0;font:14px/20px system-ui,sans-serif;background:#888}",
             ".wm-ink{fill:var(--fg)}.wm-l1{fill:var(--wm-lane-1)}.wm-l2{fill:var(--wm-lane-2)}.wm-l3{fill:var(--wm-lane-3)}",
             ".wm-from{stop-color:var(--wm-from)}.wm-to{stop-color:var(--wm-to)}",
             ".bar{display:flex;align-items:center;gap:24px;min-height:48px;padding:8px 16px;background:var(--surface);"
             "color:var(--fg-muted);border-bottom:1px solid var(--border)}",
             ".bar .n{width:72px;font-size:12px}.bar .big{margin-left:auto}"]
    for t in THEMES:
        style.append(f".t-{t}{{" + ";".join(f"{k}:{v}" for k, v in tok[t].items()) + "}")
    for v in VARIANTS:
        rows = []
        for t in THEMES:
            # One gradient id per drawing, as a page has one.
            one = lambda px, k: svg(v, px).replace('id="wm-g"', f'id="wm-g{k}"').replace("url(#wm-g)", f"url(#wm-g{k})")
            rows.append(f'<div class="bar t-{t}"><span class="n">{t}</span>{one(CAP_PX, t + "a")}'
                        f'<img src="{(ROOT / "static/icons/favicon.svg").as_uri()}" width="22" height="22">'
                        f'<span class="big">{one(CAP_PX * 2.5, t + "b")}</span></div>')
        html = (f'<!doctype html><meta charset="utf-8"><style>{"".join(style)}</style>' + "".join(rows))
        html = html.replace('href="/static/icons/favicon.svg"', f'href="{(ROOT / "static/icons/favicon.svg").as_uri()}"')
        page = dest / f"wordmark-{v}.html"
        page.write_text(html, encoding="utf-8")
        shot(page, dest / f"wordmark-{v}.png", 900, 78 * len(THEMES) + 8)
        page.unlink()
    # The contrast of every colour a variant paints, on its theme's bar.
    lines = ["| Theme | " + " | ".join(f"`{k}`" for k in WM_TOKENS) + " |", "|---" * (len(WM_TOKENS) + 1) + "|"]
    for t in THEMES:
        s = tok[t]["--surface"].strip()
        lines.append(f"| {t} | " + " | ".join(f"{ratio(tok[t][k], s):.2f}" for k in WM_TOKENS) + " |")
    (dest / "contrast.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("previews ->", dest)


def shot(page: Path, png: Path, w: int, h: int) -> None:
    b = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
              if Path(p).is_file()), None) or shutil.which("google-chrome") or shutil.which("chromium")
    if not b:
        raise SystemExit("needs Chrome, Edge or Chromium for the preview")
    with tempfile.TemporaryDirectory() as t:
        png.unlink(missing_ok=True)
        subprocess.run([b, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                        f"--window-size={w},{h}", f"--user-data-dir={Path(t) / 'p'}", "--allow-file-access-from-files",
                        f"--screenshot={png}", page.as_uri()],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=60)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", nargs="?", default=SHIPPED, choices=VARIANTS)
    ap.add_argument("--preview", type=Path)
    a = ap.parse_args()
    if a.preview:
        preview(a.preview)
    else:
        ship(a.variant)


if __name__ == "__main__":
    main()
