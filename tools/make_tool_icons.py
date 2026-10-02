"""Switches the tool icons' style (index.html, #166) and draws the three styles
side by side.

    py tools/make_tool_icons.py                 # write the shipped style into index.html
    py tools/make_tool_icons.py tile            # write another one
    py tools/make_tool_icons.py --preview DIR   # every style in every theme: compare.html, PNGs, contrast table

The drawings and the renderer live in index.html (TOOL_GLYPH, toolIcon), so
the page needs no build step; this script only rewrites the one line between
its markers that names the style, and for --preview runs that same code in a
page of its own, with each theme's tokens read from index.html.

* glyph: the drawing in the tool's colour (--tool-*), a tinted fill under it.
* tile:  a tile in the tool's colour, the drawing on it in --tool-ink, as the
         app icon is a tile.
* dot:   the drawing in the button's colour, a dot in the tool's colour.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.make_wordmark import THEMES, ratio, shot, theme_tokens  # noqa: E402

PAGE = ROOT / "index.html"
SHIPPED = "glyph"
STYLES = ("glyph", "tile", "dot")
BEGIN, END = "// tool-icons: tools/make_tool_icons.py", "// /tool-icons"
TOOLS = (("points", "Your asks"), ("changes", "Changes"), ("workspace", "Files"), ("board", "Board"), ("spec", "Spec"),
         ("list", "Tasks (left list)"))


def ship(style: str) -> None:
    text = PAGE.read_bytes().decode("utf-8")   # its line ends as they are
    pat = re.compile(re.escape(BEGIN) + r"(\r?\n).*?" + re.escape(END), re.S)
    m = pat.search(text)
    if not m:
        raise SystemExit(f"no {BEGIN} … {END} block in {PAGE}")
    nl = m.group(1)
    block = f"{BEGIN}{nl}const TOOL_ICON_STYLE = '{style}';{nl}{END}"
    PAGE.write_text(text[:m.start()] + block + text[m.end():], encoding="utf-8", newline="")
    print("shipped", style, "->", PAGE.name)


def page_code() -> str:
    """TOOL_GLYPH and toolIcon, as index.html has them."""
    text = PAGE.read_text(encoding="utf-8")
    m = re.search(re.escape(BEGIN) + r".*?(const TOOL_GLYPH = .*?)\nconst PD_ICON", text, re.S)
    if not m:
        raise SystemExit("no TOOL_GLYPH … toolIcon in index.html")
    return "const TOOL_ICON_STYLE = 'glyph';\n" + m.group(1)


# The strip as index.html draws it: a 44px --surface-sunken strip, 36px
# --surface buttons with a --border, the icon 55% of the strip; the open one
# --selected-bg with --selected-fg.
CSS = """
body{margin:0;font:14px/20px system-ui,sans-serif;background:#8A8F98;color:#172B4D}
h1{font-size:20px;margin:16px} p.k{margin:0 16px 16px;max-width:900px}
.grid{display:grid;grid-template-columns:repeat(3,max-content);gap:12px;margin:0 16px 16px}
.th{padding:10px 12px;background:var(--bg);color:var(--fg);border-radius:8px}
.th h2{font-size:12px;font-weight:600;margin:0 0 8px;color:var(--fg-muted);text-transform:uppercase;letter-spacing:.04em}
.row{display:flex;gap:16px;align-items:flex-start}
.strip{display:flex;flex-direction:column;gap:4px;padding:4px;width:36px;background:var(--surface-sunken);border:1px solid var(--border)}
.btn{position:relative;box-sizing:border-box;width:36px;height:36px;display:flex;align-items:center;justify-content:center;
 border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--fg-subtle)}
.btn.hov{background:var(--hover);border-color:var(--border-strong);color:var(--fg)}
.btn.on{background:var(--selected-bg);border-color:var(--border-strong);color:var(--selected-fg)}
.btn svg{width:24px;height:24px}
.sizes{display:flex;flex-direction:column;gap:6px}
.sizes div{display:flex;gap:8px;align-items:center;color:var(--fg-subtle)}
.sizes span{font-size:11px;width:28px;color:var(--fg-muted)}
.big{display:flex;flex-wrap:wrap;gap:10px;width:168px}
.big svg{width:48px;height:48px}
.lab{font-size:11px;color:var(--fg-muted)}
.ti{--ti:currentColor;--ti-ink:var(--tool-ink);--ti-ring:var(--surface);color:var(--ti)}
.ti-points{--ti:var(--tool-points)}.ti-changes{--ti:var(--tool-changes)}.ti-workspace{--ti:var(--tool-workspace)}
.ti-board{--ti:var(--tool-board)}.ti-spec{--ti:var(--tool-spec)}.ti-list{--ti:var(--tool-list)}
.ti-s-dot{color:inherit}
.btn.on .ti{--ti:currentColor;--ti-ink:var(--selected-bg);--ti-ring:var(--selected-bg)}
"""

SCRIPT = """
const STYLES = %s, TOOLS = %s, THEMES = %s;
const g = document.getElementById('grid');
const NAMES = { glyph: 'A · Glyph (shipped)', tile: 'B · Tile', dot: 'C · Outline + dot' };
for (const t of THEMES) for (const s of STYLES) {
  const tools = TOOLS.filter(([id]) => id !== 'list');
  const btn = (id, cls = '') => `<div class="btn ${cls}" title="${id}">${toolIcon(id, s)}</div>`;
  g.insertAdjacentHTML('beforeend', `<div class="th t-${t}"><h2>${t} · ${NAMES[s]}</h2><div class="row">
    <div><div class="lab">strip</div><div class="strip">${tools.map(([id]) => btn(id)).join('')}</div></div>
    <div><div class="lab">left</div><div class="strip">${btn('list')}</div>
      <div class="lab" style="margin-top:8px">open · hover</div><div class="strip">${btn('board', 'on')}${btn('changes', 'hov')}</div></div>
    <div class="sizes"><div class="lab">16 · 20 px</div>
      ${[16, 20].map(px => `<div><span>${px}</span>${TOOLS.map(([id]) => toolIcon(id, s).replace('<svg ', `<svg width="${px}" height="${px}" `)).join('')}</div>`).join('')}
      <div class="big">${TOOLS.map(([id]) => toolIcon(id, s)).join('')}</div></div>
  </div></div>`);
}
"""


def preview(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    tok = theme_tokens()
    themes_css = "".join(f".t-{t}{{" + ";".join(f"{k}:{v}" for k, v in tok[t].items()) + "}" for t in THEMES)
    rows = []
    for t in THEMES:
        k = tok[t]
        for id_, name in TOOLS:
            c = k[f"--tool-{id_}"].strip()
            rows.append((t, name, c, ratio(c, k["--surface"]), ratio(c, k["--surface-sunken"]), ratio(k["--tool-ink"], c)))
    html = ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>Tool icons</title><style>{CSS}{themes_css}</style></head><body>"
            "<h1>Tool icons (#166): three styles at strip size</h1>"
            "<p class=k>Each row: the right-hand tool strip (Your asks, Changes, Files, Board, Spec), the left list's "
            "button, the open and hover states, every icon at 16 and 20 px, and 2× for detail. "
            "Generated by <code>py tools/make_tool_icons.py --preview</code> from index.html's own drawing code.</p>"
            f"<div class=grid id=grid></div><script>{page_code()}\n"
            + SCRIPT % (json.dumps(STYLES), json.dumps(TOOLS), json.dumps(THEMES)) + "</script></body></html>")
    page = dest / "compare.html"
    page.write_text(html, encoding="utf-8")
    shot(page, dest / "compare.png", 1180, 1560)
    for half, ts in (("light", ("light", "paper", "contrast")), ("dark", ("dark", "dim", "fjord"))):
        one = dest / f"compare-{half}.html"
        one.write_text(html.replace(json.dumps(THEMES), json.dumps(ts)), encoding="utf-8")
        shot(one, dest / f"compare-{half}.png", 1180, 800)
        one.unlink()
    lines = ["| Theme | Tool | Colour | on its button (`--surface`) | on the strip (`--surface-sunken`) | tile's ink on it |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {t} | {n} | `{c}` | {a:.2f} | {b:.2f} | {i:.2f} |" for t, n, c, a, b, i in rows]
    (dest / "contrast.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("worst:", min(min(r[3], r[4]) for r in rows))
    print("preview ->", dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("style", nargs="?", default=SHIPPED, choices=STYLES)
    ap.add_argument("--preview", type=Path)
    a = ap.parse_args()
    if a.preview:
        preview(a.preview)
    else:
        ship(a.style)


if __name__ == "__main__":
    main()
