"""An uploaded icon's prevailing colour (P366): the project's own colour.

``prevailing(raw, ext)`` is the hue that covers most of the icon, as #rrggbb, or
None when it has no real hue (every covered pixel white, near-black or a grey):
the project then keeps its palette pick (static/po-colour.js). Transparent
pixels and neutrals do not count. Among the covered pixels of the winning hue
the most common colour is the answer, so it is a colour the icon really has,
not the mean of its edges.

A raster (PNG, JPEG, WebP) is read with Pillow when the hub's Python has it;
without it a PNG is decoded here (the core is standard-library only) and a
JPEG or WebP gives None. An SVG is measured by covered area: a grid over its
viewBox, each point the colour of the topmost paint covering it (icon_gen's
shape outlines, transforms and even-odd fill), a gradient as its stops' mean.

``shades(hex)`` is that colour as the PO label paints it in a light and in a
dark theme: the same hue, its lightness moved only as far as the label's text
needs for 4.5:1 (white or dark ink in a light theme; dark ink on a lighter
fill in a dark one, as the palette's dark shades are).
"""
from __future__ import annotations

import base64
import colorsys
import io
import struct
import xml.etree.ElementTree as ET
import zlib

import icon_gen

WHITE = (255, 255, 255)
DARK = icon_gen.parse_colour(icon_gen.DARK)   # the PO label's dark ink
MIN_TEXT = 4.5
_AIM = 4.6          # a little over, so the hex rounding never drops below 4.5
HUE_BINS = 24       # 15 degrees each
MIN_SHARE = 0.01    # of the covered area: a few stray pixels are not the icon's hue
SAMPLE = 64         # a raster is read at most this many pixels a side
GRID = 48           # points a side over an SVG's viewBox
PNG_MAX_BYTES = 16 * 1024 * 1024  # decoded scanlines the stdlib PNG reader takes on (2048² RGBA)


# ---- the hue that covers most ------------------------------------------------

def from_samples(samples) -> str | None:
    """The prevailing colour of ((r, g, b), weight) samples, weight its covered area."""
    total = 0.0
    bins = [0.0] * HUE_BINS
    cells: dict = {}
    for c, w in samples:
        if w <= 0:
            continue
        c = tuple(int(x) for x in c[:3])
        total += w
        if icon_gen._neutral(c):
            continue
        b = int(icon_gen._hls(c)[0] * HUE_BINS) % HUE_BINS
        bins[b] += w
        k = (b, c[0] >> 3, c[1] >> 3, c[2] >> 3)
        acc = cells.setdefault(k, [0.0, 0.0, 0.0, 0.0])
        acc[0] += w
        acc[1] += w * c[0]
        acc[2] += w * c[1]
        acc[3] += w * c[2]
    if total <= 0:
        return None
    # A hue split over a bin's edge is one hue: each bin scores with its neighbours.
    score = [bins[i - 1] + bins[i] + bins[(i + 1) % HUE_BINS] for i in range(HUE_BINS)]
    best = max(range(HUE_BINS), key=lambda i: (score[i], bins[i]))
    if bins[best] <= 0 or score[best] < MIN_SHARE * total:
        return None
    near = {(best - 1) % HUE_BINS, best, (best + 1) % HUE_BINS}
    top = max((acc for k, acc in cells.items() if k[0] in near), key=lambda a: a[0])
    return icon_gen._hex((top[1] / top[0], top[2] / top[0], top[3] / top[0]))


def prevailing(raw: bytes, ext: str) -> str | None:
    """The icon's prevailing colour as #rrggbb; None when it has no real hue or cannot be read."""
    try:
        if ext == "svg":
            return from_samples(svg_samples(raw.decode("utf-8-sig")))
        return from_samples(raster_samples(raw, ext))
    except Exception:   # a picture the hub cannot read keeps the palette pick
        return None


SAMPLE_MAX = 48 * 1024   # a page-made thumbnail's data URL, at most
SAMPLE_SIDE = 128        # and its side


def from_thumbnail(data_url) -> str | None:
    """The prevailing colour of a small PNG the page drew from an upload
    (``data:image/png;base64,...``, at most SAMPLE_SIDE a side): how a JPEG or
    WebP gets its colour on a hub without Pillow, read by the stdlib PNG reader."""
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix) or len(data_url) > SAMPLE_MAX:
        return None
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        if raw[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", raw[16:24])
        if not 0 < w <= SAMPLE_SIDE or not 0 < h <= SAMPLE_SIDE:
            return None
        return from_samples(png_samples(raw))
    except Exception:
        return None


# ---- rasters -----------------------------------------------------------------

def raster_samples(raw: bytes, ext: str) -> list:
    """((r, g, b), alpha 0..1) of a raster's pixels, read at most SAMPLE a side."""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        im = Image.open(io.BytesIO(raw))
        if im.width * im.height > 64_000_000:
            return []
        if ext == "jpg":
            im.draft("RGB", (SAMPLE * 2, SAMPLE * 2))
        im = im.convert("RGBA")
        im.thumbnail((SAMPLE, SAMPLE))
        get = getattr(im, "get_flattened_data", None) or im.getdata
        return [((p[0], p[1], p[2]), p[3] / 255) for p in get() if p[3] >= 16]
    if ext == "png":
        return png_samples(raw)
    return []


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def png_samples(raw: bytes) -> list:
    """A PNG's pixels without Pillow: every colour type and bit depth, not
    interlaced. The scanlines are inflated and unfiltered one at a time (only
    the previous one is kept), up to PNG_MAX_BYTES of them; a pixel whose value
    is the image's tRNS key is transparent."""
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return []
    pos, ihdr, plte, trns, idat = 8, None, b"", b"", []
    while pos + 8 <= len(raw):
        n, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body[:13]) if len(body) >= 13 else None
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if not ihdr:
        return []
    w, h, depth, ctype, _, _, interlace = ihdr
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if not chans or interlace or depth not in (1, 2, 4, 8, 16) or not w or not h:
        return []
    bpp_bits = chans * depth
    stride = (w * bpp_bits + 7) // 8
    if stride * h > PNG_MAX_BYTES:
        return []
    # The transparent key of a grey or RGB image: its samples at full depth.
    key = None
    if ctype == 0 and len(trns) >= 2:
        key = (struct.unpack(">H", trns[:2])[0],)
    elif ctype == 2 and len(trns) >= 6:
        key = struct.unpack(">HHH", trns[:6])
    bpp = max(1, bpp_bits // 8)
    step_y, step_x = max(1, h // SAMPLE), max(1, w // SAMPLE)
    maxv = (1 << depth) - 1
    z, chunks = zlib.decompressobj(), iter(idat)
    buf, prev, out = bytearray(), bytearray(stride), []
    for y in range(h):
        # Inflate no more than the next scanline needs (a bomb stays a bomb on disk).
        while len(buf) < stride + 1:
            src = z.unconsumed_tail or next(chunks, b"")
            if not src:
                break
            buf += z.decompress(src, stride + 1 - len(buf) + 65536)
        if len(buf) < stride + 1:
            break
        f = buf[0]
        line = buf[1:stride + 1]
        del buf[:stride + 1]
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif f == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 255
        elif f:
            return []
        prev = line
        if y % step_y:
            continue
        for x in range(0, w, step_x):
            if depth < 8:
                bit = x * depth
                full = [(line[bit // 8] >> (8 - depth - bit % 8)) & maxv]
                vals = full
            elif depth == 8:
                full = vals = list(line[x * chans:(x + 1) * chans])
            else:
                full = [line[(x * chans + k) * 2] << 8 | line[(x * chans + k) * 2 + 1] for k in range(chans)]
                vals = [v >> 8 for v in full]   # the high byte
            if ctype == 3:
                i = vals[0]
                if 3 * i + 3 > len(plte):
                    continue
                rgb = tuple(plte[3 * i:3 * i + 3])
                a = trns[i] if i < len(trns) else 255
            elif ctype in (0, 4):
                g = vals[0] * 255 // maxv if depth < 8 else vals[0]
                rgb = (g, g, g)
                a = vals[1] if ctype == 4 else (0 if key and full[0] == key[0] else 255)
            else:
                rgb = tuple(vals[:3])
                a = vals[3] if ctype == 6 else (0 if key and tuple(full[:3]) == key else 255)
            if a >= 16:
                out.append((rgb, a / 255))
    return out


# ---- SVG: covered area ---------------------------------------------------------

def _stops(el) -> tuple | None:
    """A gradient's colour: the mean of its stops."""
    cs = []
    for s in el.iter():
        if icon_gen._local(s.tag) == "stop":
            c = icon_gen.parse_colour(icon_gen._style(s).get("stop-color") or s.get("stop-color") or "black")
            if c is not None:
                cs.append(c)
    return tuple(round(sum(c[i] for c in cs) / len(cs)) for i in range(3)) if cs else None


def _opacity(el) -> float:
    v = 1.0
    for k in ("opacity", "fill-opacity"):
        raw = icon_gen._style(el).get(k) or el.get(k)
        try:
            v *= max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            pass
    return v


def svg_samples(text: str) -> list:
    """((r, g, b), weight) on a grid over an SVG: each point its topmost paint."""
    root = ET.fromstring(text)
    grads = {el.get("id"): _stops(el) for el in root.iter()
             if icon_gen._local(el.tag) in ("lineargradient", "radialgradient") and el.get("id")}
    paints = []
    for p in icon_gen._paints(root):
        if not p["geo"] or not p["geo"]["box"]:
            continue
        c = p["rgb"]
        raw = (p["raw"] or "").strip()
        if c is None and raw.lower().startswith("url("):
            c = grads.get(raw[4:].strip(" )'\"").lstrip("#"))
        if c is None:
            continue
        paints.append((p["geo"], tuple(c), _opacity(p["el"])))
    if not paints:
        return []
    vb = [float(v) for v in (root.get("viewBox") or "").replace(",", " ").split()[:4]] if root.get("viewBox") else []
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        x0, y0, x1, y1 = vb[0], vb[1], vb[0] + vb[2], vb[1] + vb[3]
    else:
        boxes = [g["box"] for g, _, _ in paints]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    n = GRID if sum(len(s) for g, _, _ in paints for s in g["lines"]) < 20000 else GRID // 2
    out = []
    for j in range(n):
        y = y0 + (j + 0.5) * (y1 - y0) / n
        for i in range(n):
            x = x0 + (i + 0.5) * (x1 - x0) / n
            for geo, c, a in reversed(paints):
                if a > 0 and icon_gen._covers(geo, (x, y)):
                    out.append((c, a))
                    break
    return out


# ---- the colour in each theme --------------------------------------------------

def _with_l(c, l: float):
    h, _, s = colorsys.rgb_to_hls(*(x / 255 for x in c))
    return tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s))


def _move(c, ink, up: bool):
    """c with its lightness moved (up: lighter) just far enough for 4.5:1 with ink; None if it cannot."""
    l0 = colorsys.rgb_to_hls(*(x / 255 for x in c))[1]
    lo, hi = (l0, 1.0) if up else (0.0, l0)
    end = _with_l(c, hi if up else lo)
    if icon_gen._contrast(end, ink) < _AIM:
        return None
    for _ in range(30):
        mid = (lo + hi) / 2
        ok = icon_gen._contrast(_with_l(c, mid), ink) >= _AIM
        if up:
            hi = mid if ok else hi
            lo = lo if ok else mid
        else:
            lo = mid if ok else lo
            hi = hi if ok else mid
    return _with_l(c, hi if up else lo)


def _readable(c, ink) -> bool:
    return icon_gen._contrast(c, ink) >= MIN_TEXT


def shades(hex_: str) -> dict | None:
    """{'hex', 'light', 'dark'}: the stored colour and the PO label's fill in a light
    and a dark theme, each with white or dark ink at 4.5:1 or more."""
    c = icon_gen.parse_colour(icon_gen.norm_hex(hex_))
    if c is None:
        return None
    if _readable(c, WHITE) or _readable(c, DARK):
        light = c
    else:
        l0 = colorsys.rgb_to_hls(*(x / 255 for x in c))[1]
        ways = [m for m in (_move(c, WHITE, False), _move(c, DARK, True)) if m is not None]
        light = min(ways, key=lambda m: abs(colorsys.rgb_to_hls(*(x / 255 for x in m))[1] - l0)) if ways else c
    dark = c if _readable(c, DARK) else (_move(c, DARK, True) or c)
    return {"hex": icon_gen._hex(c), "light": icon_gen._hex(light), "dark": icon_gen._hex(dark)}


def ink(fill_hex: str) -> str:
    """The PO label's ink on a fill: white when it reads at 4.5:1, else the dark ink (po-colour.js)."""
    c = icon_gen.parse_colour(fill_hex)
    return "#ffffff" if c is not None and _readable(c, WHITE) else icon_gen.DARK
