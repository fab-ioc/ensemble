"""An initiative's icon drawn by an AI model (#335): Generate on its settings.

The hub asks an agent CLI it already runs for four small SVG icons from the
project's name (and its roadmap's title, when it has one): ``claude -p`` on
haiku with no tools, else ``codex exec`` in a read-only sandbox in an empty
folder. No new key, no new service; neither present, the page says so.

The answer is untrusted. Each SVG is parsed (no DTD, no entities), kept only
when every element and attribute is on the allow-list below, and written out
again from the parsed tree, so nothing the model typed reaches the page as
text. The picked one is then saved through #329's own path
(project_icon.accept), which checks it once more."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

TIMEOUT_S = 60
COUNT = 4
MAX_SVG_BYTES = 8 * 1024
MAX_ELEMENTS = 200
MAX_DEPTH = 8
MAX_OUTPUT = 256 * 1024
CLAUDE_MODEL = "haiku"
SVG_NS = "http://www.w3.org/2000/svg"

ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
GEOMETRY = {"x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2",
            "d", "points", "viewbox"}
PAINT = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
         "stroke-dasharray", "stroke-dashoffset", "fill-rule", "clip-rule", "opacity", "fill-opacity",
         "stroke-opacity", "transform"}
ATTRS = GEOMETRY | PAINT
# A value may hold numbers, words, colours (#hex, rgb()), path letters and the
# transform functions; never a quote, an angle bracket, a backslash, a colon
# (javascript:, data:) or a semicolon.
_VALUE_RE = re.compile(r"^[A-Za-z0-9#.,%()+\-\s]*$")
_BAD_VALUE_RE = re.compile(r"url\s*\(|expression\s*\(|var\s*\(|attr\s*\(", re.I)
_FUNC_RE = re.compile(r"([A-Za-z-]+)\s*\(")
_FUNCS = {"rgb", "rgba", "hsl", "hsla", "matrix", "translate", "scale", "rotate", "skewx", "skewy"}

REFUSALS = {
    "no_cli": "Generating needs claude or codex on the hub, and neither is installed there.",
    "timeout": "did not answer within 60 s.",
    "failed": "stopped with an error.",
    "no_icons": "answered with no icon that could be used.",
    "busy": "Icons are already being drawn on the hub; try again in a minute.",
    "no_such_project": "No such project.",
}

_BUSY: set[str] = set()
_BUSY_LOCK = threading.Lock()
MAX_RUNS = 2   # hub-wide: more at once answer "busy"

# Windows: the CLI must not open a console window (as digest.py).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_d = None  # the dashboard module, set by bind(): its _claude_bin() finds claude


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


class Unsafe(Exception):
    pass


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _ok_value(v: str) -> bool:
    if len(v) > 4000 or not _VALUE_RE.match(v) or _BAD_VALUE_RE.search(v):
        return False
    return all(f.lower() in _FUNCS for f in _FUNC_RE.findall(v))


def sanitize(text: str) -> str:
    """One SVG written out again from its parsed tree, every element and
    attribute on the allow-list; Unsafe when anything is not."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SVG_BYTES:
        raise Unsafe("size")
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low or "<?" in low or "<![cdata[" in low:
        raise Unsafe("dtd")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise Unsafe("parse")
    if _local(root.tag) != "svg" or (root.tag.startswith("{") and not root.tag.startswith("{" + SVG_NS + "}")):
        raise Unsafe("root")
    count = [0]

    def walk(el, depth: int) -> str:
        count[0] += 1
        if count[0] > MAX_ELEMENTS or depth > MAX_DEPTH:
            raise Unsafe("too_many")
        if not isinstance(el.tag, str):
            raise Unsafe("node")
        if el.tag.startswith("{") and not el.tag.startswith("{" + SVG_NS + "}"):
            raise Unsafe("namespace")
        tag = _local(el.tag)
        if tag not in ELEMENTS or (tag == "svg" and depth):
            raise Unsafe("element " + tag)
        if (el.text or "").strip() or (depth and (el.tail or "").strip()):
            raise Unsafe("text")
        attrs = []
        for k, v in el.attrib.items():
            if k.startswith("{"):
                raise Unsafe("namespaced attribute")
            name = k.lower()
            if name in ("xmlns", "version"):
                continue
            if name not in ATTRS or not _ok_value(v):
                raise Unsafe("attribute " + name)
            if tag == "svg" and name in ("width", "height", "x", "y"):
                continue
            if name == "viewbox" and any(k == "viewBox" for k, _ in attrs):
                continue   # viewBox and VIEWBOX both: keep the first, or the XML is malformed
            attrs.append((k if name != "viewbox" else "viewBox", " ".join(v.split())))
        if tag == "svg":
            if not any(k == "viewBox" for k, _ in attrs):
                raise Unsafe("viewBox")
            attrs = [("xmlns", SVG_NS), ("width", "48"), ("height", "48")] + attrs
        inner = "".join(walk(c, depth + 1) for c in el)
        a = "".join(f" {k}={quoteattr(v)}" for k, v in attrs)
        return f"<{tag}{a}>{inner}</{tag}>" if inner else f"<{tag}{a}/>"

    return walk(root, 0)


MAX_CANDIDATES = 32
_SVG_CLOSE = re.compile(r"\s*>")
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _svg_blocks(text: str):
    """Each `<svg ...>...</svg>` in text, the shortest from each opener, in one
    pass (a regex's lazy match is quadratic on many openers with no closer)."""
    low = text.translate(_ASCII_FOLD)   # str.lower() can lengthen ("İ" -> "i̇"), shifting the offsets
    i = 0
    while True:
        a = low.find("<svg", i)
        if a < 0:
            return
        nxt = low[a + 4:a + 5]
        if nxt and (nxt.isalnum() or nxt == "_"):
            i = a + 4
            continue
        b = low.find("</svg", a + 4)
        while b >= 0:
            m = _SVG_CLOSE.match(low, b + 5)
            if m:
                break
            b = low.find("</svg", b + 5)
        if b < 0:
            return   # no closer after this opener, so none after a later one
        yield text[a:m.end()]
        i = m.end()


def parse(output: str) -> list[str]:
    """The usable icons in a model's answer, sanitised, at most COUNT, no two
    alike; at most MAX_CANDIDATES are tried."""
    out: list[str] = []
    for n, block in enumerate(_svg_blocks((output or "")[:MAX_OUTPUT])):
        if n >= MAX_CANDIDATES:
            break
        try:
            s = sanitize(block)
        except Unsafe:
            continue
        if s not in out:
            out.append(s)
        if len(out) >= COUNT:
            break
    return out


def about_text(roadmap_text: str) -> str:
    """What the project is about, from its roadmap: its first line of prose
    (its title is mostly "<name> — roadmap", and its first note often who
    owns it), cut at its first sentence, at most 200 characters."""
    for line in (roadmap_text or "").splitlines()[:80]:
        t = line.strip()
        if not t or t.startswith(("#", "|", "<", "```", "---")) or t.lower().startswith("owned by"):
            continue
        t = re.sub(r"[*_`]+", "", t.lstrip("-* >").strip())
        t = " ".join(t.split())
        m = re.match(r"(.{20,}?[.!?])(\s|$)", t)
        if m:
            t = m.group(1)
        return t if len(t) <= 200 else t[:199].rstrip() + "…"
    return ""


def prompt(name: str, about: str, colour: str = "") -> str:
    name = " ".join((name or "").split())[:120]
    about = " ".join((about or "").split())[:200]
    what = f'a project called "{name}"' + (f', about: "{about}"' if about else "")
    c = norm_hex(colour)
    paint = (f"its main colour is {c}: either the ground is {c} with a white or {DARK} symbol, or the symbol is {c} "
             f"on a white or {DARK} ground; any other colour is white, {DARK}, or a lighter or darker shade of {c}; "
             "every colour as hex; " if c else "at most 3 colours as hex; ")
    return (f"Draw {COUNT} different icons for {what}. Each is one SVG: "
            'viewBox="0 0 48 48"; its first element a rect x="0" y="0" width="48" height="48" rx="10" as the ground; '
            f"one bold symbol readable at 16 px; {paint}only the elements "
            "svg, g, path, rect, circle, ellipse, line, polyline, polygon; "
            "no text, no gradients, no style, no scripts, no links, no external references. "
            f"Output only the {COUNT} SVGs, one per line, nothing else.")


# ---- The project's colour (P362) ----
# Every generated icon is drawn in the project's PO label colour: the ground or
# the symbol is that colour, and any other colour a neutral (white, near-black,
# grey) or a lighter/darker shade of it. The prompt asks for it; recolour()
# checks each icon after and maps what the model got wrong, never drops it.

DARK = "#1D2125"          # the PO label's dark ink (static/po-colour.js)
_HEX_RE = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.I)
_NUM = r"\s*([+-]?(?:\d+\.?\d*|\.\d+))(%?)\s*"
_RGB_RE = re.compile(r"^rgba?\(" + _NUM + r"[,\s]" + _NUM + r"[,\s]" + _NUM + r"(?:[,/].*)?\)$", re.I)
_HSL_RE = re.compile(r"^hsla?\(" + _NUM + r"[,\s]" + _NUM + r"[,\s]" + _NUM + r"(?:[,/].*)?\)$", re.I)
NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128), "grey": (128, 128, 128),
         "silver": (192, 192, 192), "red": (255, 0, 0), "green": (0, 128, 0), "lime": (0, 255, 0),
         "blue": (0, 0, 255), "navy": (0, 0, 128), "teal": (0, 128, 128), "aqua": (0, 255, 255),
         "cyan": (0, 255, 255), "yellow": (255, 255, 0), "orange": (255, 165, 0), "gold": (255, 215, 0),
         "purple": (128, 0, 128), "magenta": (255, 0, 255), "fuchsia": (255, 0, 255), "pink": (255, 192, 203),
         "brown": (165, 42, 42), "maroon": (128, 0, 0), "olive": (128, 128, 0), "indigo": (75, 0, 130),
         "violet": (238, 130, 238), "crimson": (220, 20, 60), "tomato": (255, 99, 71), "coral": (255, 127, 80),
         "salmon": (250, 128, 114), "skyblue": (135, 206, 235), "royalblue": (65, 105, 225),
         "dodgerblue": (30, 144, 255), "steelblue": (70, 130, 180), "forestgreen": (34, 139, 34),
         "seagreen": (46, 139, 87), "darkgreen": (0, 100, 0), "darkblue": (0, 0, 139), "darkred": (139, 0, 0),
         "darkorange": (255, 140, 0), "turquoise": (64, 224, 208), "slategray": (112, 128, 144),
         "darkgray": (169, 169, 169), "lightgray": (211, 211, 211), "whitesmoke": (245, 245, 245),
         "currentcolor": (0, 0, 0)}   # an <img> has no colour to give: black
NO_PAINT = {"none", "transparent"}
HUE_SPAN = 18      # degrees: a colour this near the label's hue is a tint or shade of it
NEAR = 40          # RGB distance at which a colour counts as the label colour itself


def norm_hex(s: str) -> str:
    """'#rgb' or '#rrggbb' as '#rrggbb' in lower case; '' when it is neither."""
    s = (s or "").strip().lower() if isinstance(s, str) else ""
    if re.fullmatch(r"#[0-9a-f]{6}", s):
        return s
    if re.fullmatch(r"#[0-9a-f]{3}", s):
        return "#" + "".join(ch * 2 for ch in s[1:])
    return ""


def _chan(v: str, pct: str, full: float) -> float:
    x = float(v) * (full / 100 if pct else 1)
    return max(0.0, min(full, x))


def parse_colour(v) -> tuple[int, int, int] | None:
    """A paint value as (r, g, b); None for no paint or a value not understood."""
    v = (v or "").strip().lower() if isinstance(v, str) else ""
    if not v or v in NO_PAINT:
        return None
    m = _HEX_RE.match(v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h[:3])
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = _RGB_RE.match(v)
    if m:
        g = m.groups()
        return tuple(round(_chan(g[i], g[i + 1], 255)) for i in (0, 2, 4))
    m = _HSL_RE.match(v)
    if m:
        import colorsys
        g = m.groups()
        r, gg, b = colorsys.hls_to_rgb((float(g[0]) % 360) / 360, _chan(g[4], "%", 100) / 100, _chan(g[2], "%", 100) / 100)
        return (round(r * 255), round(gg * 255), round(b * 255))
    return NAMED.get(v)


def _hex(c) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(x)))):02x}" for x in c)


def _hls(c) -> tuple[float, float, float]:
    import colorsys
    return colorsys.rgb_to_hls(*(x / 255 for x in c))


def _neutral(c) -> bool:
    """White, near-black or a grey: no hue of its own."""
    h, l, s = _hls(c)
    return s < 0.15 or l > 0.95 or l < 0.08 or (max(c) - min(c)) < 24


def _hue_gap(a, b) -> float:
    d = abs(_hls(a)[0] - _hls(b)[0]) * 360
    return min(d, 360 - d)


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _lum(c) -> float:
    lin = [(x / 255) / 12.92 if x / 255 <= 0.04045 else ((x / 255 + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _shade_of(label, c):
    """c turned into a tint or shade of label: label's hue and saturation, c's lightness."""
    import colorsys
    h, _, s = _hls(label)
    l = min(0.9, max(0.15, _hls(c)[1]))
    return tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, l, s))


def _style(el) -> dict:
    out = {}
    for part in (el.get("style") or "").split(";"):
        k, sep, v = part.partition(":")
        if sep:
            out[k.strip().lower()] = v.strip()
    return out


def _paint(el, prop: str):
    """An element's own fill or stroke: its style's, else its attribute's; None when unset."""
    st = _style(el)
    if prop in st:
        return st[prop]
    return el.get(prop)


_PATH_TOK = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ARGS = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7, "z": 0}


def _path_points(d: str) -> list[tuple[float, float]]:
    """Every point a path's commands name (control points included), absolute."""
    pts, x, y, sx, sy, cmd = [], 0.0, 0.0, 0.0, 0.0, None
    toks = _PATH_TOK.findall(d or "")[:4000]
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                x, y = sx, sy
            continue
        if cmd is None or cmd in "Zz":
            return pts
        n = _ARGS[cmd.lower()]
        try:
            a = [float(v) for v in toks[i:i + n]]
        except ValueError:
            return pts
        if len(a) < n:
            return pts
        i += n
        rel, c = cmd.islower(), cmd.lower()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "h":
            x = a[0] + (x if rel else 0)
        elif c == "v":
            y = a[0] + (y if rel else 0)
        elif c == "a":
            x, y = a[5] + ox, a[6] + oy
        else:
            for j in range(0, n - 2, 2):
                pts.append((a[j] + ox, a[j + 1] + oy))
            x, y = a[n - 2] + ox, a[n - 1] + oy
        pts.append((x, y))
        if c == "m":
            sx, sy = x, y
            cmd = "l" if rel else "L"
    return pts


def _num(el, k: str, default: float = 0.0) -> float:
    try:
        return float(re.match(r"\s*([+-]?(?:\d+\.?\d*|\.\d+))", el.get(k) or "").group(1))
    except (AttributeError, ValueError):
        return default


def _box(el, tag: str) -> tuple[float, float]:
    """An element's rough size (width, height) in the viewBox's units, for weighing colours."""
    if tag == "rect":
        return _num(el, "width"), _num(el, "height")
    if tag == "circle":
        return 2 * _num(el, "r"), 2 * _num(el, "r")
    if tag == "ellipse":
        return 2 * _num(el, "rx"), 2 * _num(el, "ry")
    if tag == "line":
        return abs(_num(el, "x2") - _num(el, "x1")), abs(_num(el, "y2") - _num(el, "y1"))
    if tag == "path":
        pts = _path_points(el.get("d") or "")
    elif tag in ("polygon", "polyline"):
        ns = [float(v) for v in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)", el.get("points") or "")[:4000]]
        pts = list(zip(ns[0::2], ns[1::2]))
    else:
        return 0.0, 0.0
    if not pts:
        return 0.0, 0.0
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def _paints(root) -> list[dict]:
    """Each painted fill and stroke of an icon's shapes: {el, src, prop, rgb, raw, area,
    ground}; src is the element that sets it. Fill and stroke are inherited from the
    <g> and <svg> around a shape; an unset fill is black, as SVG draws it. area is a
    rough weight (a shape's box; a stroke's length times its width). The ground is
    the first shape when it covers the icon."""
    out, first = [], [True]

    def walk(el, fill, stroke, sw):
        tag = _local(el.tag)
        f, s_ = _paint(el, "fill"), _paint(el, "stroke")
        fill = (f, el) if f is not None else fill
        stroke = (s_, el) if s_ is not None else stroke
        sw = _num(el, "stroke-width", sw) if (_style(el).get("stroke-width") or el.get("stroke-width")) else sw
        if tag in ("svg", "g"):
            for c in el:
                walk(c, fill, stroke, sw)
            return
        w, h = _box(el, tag)
        ground = first[0] and tag == "rect" and w >= 40 and h >= 40
        first[0] = False
        if tag != "line":   # a line has no inside; every other shape fills, black when unset
            raw, src = fill if fill is not None else ("black", el)
            rgb = parse_colour(raw)
            if rgb is not None or raw.strip().lower() not in NO_PAINT:
                out.append({"el": el, "src": src, "prop": "fill", "rgb": rgb, "raw": raw, "area": w * h, "ground": ground})
        if stroke is not None and stroke[0].strip().lower() not in NO_PAINT:
            out.append({"el": el, "src": stroke[1], "prop": "stroke", "rgb": parse_colour(stroke[0]), "raw": stroke[0],
                        "area": (w + h) * max(sw, 0.5), "ground": False})

    walk(root, None, None, 1.0)
    return out


def _write(el, depth: int = 0) -> str:
    tag = _local(el.tag)
    attrs = ([("xmlns", SVG_NS)] if depth == 0 else []) + list(el.attrib.items())
    inner = "".join(_write(c, depth + 1) for c in el)
    a = "".join(f" {k}={quoteattr(v)}" for k, v in attrs)
    return f"<{tag}{a}>{inner}</{tag}>" if inner else f"<{tag}{a}/>"


def _key(p):
    return p["rgb"] if p["rgb"] is not None else p["raw"]


def _dominant(ps: list[dict]):
    """The colour with the most area among paints (an (r, g, b), or a raw value not understood)."""
    weight: dict = {}
    for p in ps:
        weight[_key(p)] = weight.get(_key(p), 0) + p["area"]
    return max(weight, key=weight.get) if weight else None


def _foreign(k, label) -> bool:
    """A colour that is neither neutral nor the label's hue (nor near the label colour)."""
    return not isinstance(k, tuple) or (not _neutral(k) and _hue_gap(k, label) > HUE_SPAN and _dist(k, label) > NEAR)


def check(svg: str, colour: str) -> dict:
    """How an icon stands to the label colour: {ok, dominant, foreign}. ok when its
    main colour (the hued one with the most area: the ground's or the symbol's) is
    the label colour and every other colour a neutral or a tint or shade of it."""
    label = parse_colour(norm_hex(colour))
    if label is None:
        return {"ok": True, "dominant": None, "foreign": []}
    ps = _paints(ET.fromstring(svg))
    hued = [p for p in ps if p["rgb"] is None or not _neutral(p["rgb"])]
    dom = _dominant(hued)
    foreign = sorted({_hex(k) if isinstance(k, tuple) else k for k in map(_key, hued) if _foreign(k, label)})
    dom_ok = isinstance(dom, tuple) and _dist(dom, label) <= NEAR
    return {"ok": dom_ok and not foreign, "dominant": _hex(dom) if isinstance(dom, tuple) else dom, "foreign": foreign}


def _apart(label, c, on=None):
    """A tint or shade of label for another hue c: c's lightness, kept apart from the
    label's, and reading at 3:1 on `on` (the ground) when one is given."""
    import colorsys
    h, ll, s = _hls(label)
    l = _hls(c)[1]
    if abs(l - ll) < 0.22:
        l = ll + 0.3 if ll < 0.6 else ll - 0.3
    l = min(0.9, max(0.12, l))
    rgb = lambda l: tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, l, s))
    if on is not None and _contrast(rgb(l), on) < 3:
        step = 0.02 if _lum(on) < 0.18 else -0.02   # lighter on a dark ground, darker on a light one
        while 0.04 < l < 0.96 and _contrast(rgb(l), on) < 3:
            l += step
    return rgb(min(0.96, max(0.04, l)))


def _ink(on):
    """White or near-black, whichever reads better on `on`."""
    dark = parse_colour(DARK)
    return (255, 255, 255) if _contrast((255, 255, 255), on) >= _contrast(dark, on) else dark


def recolour(svg: str, colour: str) -> str:
    """An icon (sanitised) in the label colour: the very same string when check()
    passes it; else its main colour becomes the label colour, each other hue a tint
    or shade of it, and a neutral that would then read too faintly on the ground
    white or near-black. All neutral (black on white), the symbol's main colour
    becomes the label colour. Each change is made where the colour is set (a <g>
    for the shapes in it)."""
    label = parse_colour(norm_hex(colour))
    if label is None or check(svg, colour)["ok"]:
        return svg
    root = ET.fromstring(svg)
    ps = _paints(root)
    hued = [p for p in ps if p["rgb"] is None or not _neutral(p["rgb"])]
    if not hued:
        hued = [p for p in ps if not p["ground"]] or ps
    dom = _dominant(hued)
    new = {id(p): label for p in hued if _key(p) == dom}
    # What is on the ground must still read on it (3:1).
    ground = next((p for p in ps if p["ground"]), None)
    on = None
    if ground is not None and ground["rgb"] is not None:
        if id(ground) in new:
            for p in ps:
                if p is not ground and p["rgb"] is not None and _neutral(p["rgb"]) and _contrast(p["rgb"], label) < 3:
                    new[id(p)] = _ink(label)
        elif _contrast(ground["rgb"], label) < 3 and new:
            new[id(ground)] = _ink(label)
        on = new.get(id(ground), ground["rgb"])
    for p in hued:
        k = _key(p)
        if id(p) not in new and _foreign(k, label):
            new[id(p)] = _apart(label, k, on) if isinstance(k, tuple) else label
    for p in ps:
        c = new.get(id(p))
        if c is None:
            continue
        el, prop, v = p["src"], p["prop"], _hex(c)
        st = _style(el)
        if prop in st:
            st[prop] = v
            el.set("style", ";".join(f"{k}:{x}" for k, x in st.items()))
        else:
            el.set(prop, v)
    try:
        return sanitize(_write(root))
    except Unsafe:
        return svg


def which_cli() -> dict | None:
    """The CLI Generate runs: {agent, model, bin}, or None."""
    claude = _d._claude_bin() if _d is not None else shutil.which("claude")
    if claude:
        return {"agent": "claude", "model": CLAUDE_MODEL, "bin": claude}
    codex = shutil.which("codex")
    if codex:
        return {"agent": "codex", "model": "", "bin": codex}
    return None


def _workspace() -> str | None:
    """The hub's folder for one-shot model calls (digest.py's), or None."""
    if _d is None:
        return None
    w = _d.DASHBOARD_DIR / "_rename_workspace"
    try:
        w.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return str(w)


def _head(path: Path, limit: int = MAX_OUTPUT) -> str:
    """The first `limit` bytes of a file, as text ("" when it is missing)."""
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _run(cli: dict, text: str, timeout: float) -> str:
    """The CLI's answer, at most MAX_OUTPUT bytes of it (raises
    subprocess.TimeoutExpired, RuntimeError, OSError).

    The prompt, which holds the typed name and the roadmap's words, goes in on
    stdin, never in argv: on Windows the CLI is often a .cmd shim, and cmd.exe
    would parse its arguments as shell text. Its answer goes to files, read
    only up to MAX_OUTPUT, so a runaway answer cannot fill the hub's memory."""
    with tempfile.TemporaryDirectory(prefix="ens-icon-") as tmp:
        t = Path(tmp)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE",)}
        if cli["agent"] == "claude":
            # No tools, no MCP servers (a roadmap line could steer the model to the
            # user's own), no session file left in ~/.claude/projects: as digest.py.
            cmd = [cli["bin"], "-p", "--model", cli["model"], "--no-session-persistence",
                   "--tools", "", "--strict-mcp-config"]
            out_file = None
            # claude still makes ~/.claude/projects/<its cwd>/ for every new cwd: run it
            # where the hub's other one-shot calls run (digest.py), not in a fresh folder.
            cwd = _workspace() or tmp
        else:
            # codex has no flag that keeps the MCP servers in the user's config.toml
            # off; the read-only sandbox in an empty folder and --ephemeral are what
            # bound it. claude, when installed, is always preferred.
            out_file = t / "answer.txt"
            cmd = [cli["bin"], "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                   "-C", tmp, "-o", str(out_file), "-"]
            cwd = tmp
        # errors="replace": a lone surrogate (from JSON's \\ud800) cannot be encoded.
        (t / "prompt.txt").write_text(text, encoding="utf-8", errors="replace")
        with open(t / "prompt.txt", "rb") as fin, open(t / "stdout.txt", "wb") as fout, \
                open(t / "stderr.txt", "wb") as ferr:
            p = subprocess.run(cmd, cwd=cwd, env=env, stdin=fin, stdout=fout, stderr=ferr, timeout=timeout,
                               creationflags=_NO_WINDOW)
        if p.returncode:
            raise RuntimeError((_head(t / "stderr.txt", 4096) or _head(t / "stdout.txt", 4096))[-300:])
        if out_file is not None and out_file.exists():
            return _head(out_file)
        return _head(t / "stdout.txt")


def generate(key: str, name: str, about: str, timeout: float = TIMEOUT_S,
             colour: str = "") -> tuple[bool, str, dict]:
    """(ok, code, result): result {agent, model, svgs, ms} on success, and with a
    label colour (P362) {colour, recoloured}: how many icons had to be mapped to it.
    code one of REFUSALS."""
    colour = norm_hex(colour)
    cli = which_cli()
    if cli is None:
        return False, "no_cli", {}
    meta = {"agent": cli["agent"], "model": cli["model"]}
    with _BUSY_LOCK:
        if key in _BUSY or len(_BUSY) >= MAX_RUNS:
            return False, "busy", meta
        _BUSY.add(key)
    t0 = time.monotonic()
    try:
        try:
            answer = _run(cli, prompt(name, about, colour), timeout)
        except subprocess.TimeoutExpired:
            return False, "timeout", meta
        except (RuntimeError, OSError, ValueError) as e:
            print(f"[icon-gen] {cli['agent']} failed: {e!r}"[:400], flush=True)
            return False, "failed", meta
        svgs = parse(answer)
        if not svgs:
            return False, "no_icons", meta
        extra = {}
        if colour:
            out, n = [], 0
            for s in svgs:
                r = recolour(s, colour)
                n += r != s
                if r not in out:
                    out.append(r)
            svgs, extra = out, {"colour": colour, "recoloured": n}
        return True, "ok", {**meta, "svgs": svgs, **extra, "ms": int((time.monotonic() - t0) * 1000)}
    finally:
        with _BUSY_LOCK:
            _BUSY.discard(key)
