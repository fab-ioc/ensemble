"""P366: an uploaded icon's prevailing colour becomes the project's colour.

* icon_colour.prevailing: the hue covering most of a PNG, JPEG, WebP or SVG
  (transparent ground, white ground, a gradient, a symbol over its ground);
  an icon of neutrals only gives none; a PNG reads without Pillow too;
* the hub: an upload stores it beside ``icon``, Replace works it out again, a
  generated icon keeps the colour the project had, Remove clears it, and
  /api/projects serves it as {hex, light, dark}; icons kept before this get
  theirs once (derive_icon_colours);
* icon_colour.shades: in a light and a dark theme the label's text keeps 4.5:1
  on the fill, the hue kept, the lightness moved only when it must.
"""
from __future__ import annotations

import builtins
import colorsys
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import icon_colour  # noqa: E402
import icon_gen  # noqa: E402
from tests.test_project_settings import Hub, data_url  # noqa: E402

try:
    from PIL import Image, ImageDraw
except ImportError:   # the fixtures are drawn with Pillow; the hub does not need it
    Image = None

GREEN, RED, BLUE = (40, 160, 90), (200, 40, 40), (30, 80, 220)


def hue(h: str) -> float:
    return colorsys.rgb_to_hls(*(x / 255 for x in icon_gen.parse_colour(h)))[0] * 360


def near(a: str, b, tol: int = 6) -> bool:
    return icon_gen._dist(icon_gen.parse_colour(a), b) <= tol


def raster(fmt: str, ground, big=GREEN, small=RED, size=120) -> bytes:
    mode = "RGB" if fmt == "JPEG" else "RGBA"
    im = Image.new(mode, (size, size), ground[:3] if mode == "RGB" else ground)
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, size - 10, size - 10), fill=big)        # most of the icon
    d.rectangle((0, 0, size // 6, size // 6), fill=small)      # a corner of another hue
    b = io.BytesIO()
    im.save(b, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return b.getvalue()


NS = 'xmlns="http://www.w3.org/2000/svg"'


@unittest.skipIf(Image is None, "the fixtures are drawn with Pillow")
class Prevailing(unittest.TestCase):
    def test_raster_on_a_transparent_ground(self):
        for fmt, ext in (("PNG", "png"), ("WEBP", "webp")):
            c = icon_colour.prevailing(raster(fmt, (255, 255, 255, 0)), ext)
            self.assertTrue(c and near(c, GREEN), (fmt, c))

    def test_raster_on_a_white_ground(self):
        for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
            c = icon_colour.prevailing(raster(fmt, (255, 255, 255, 255)), ext)
            self.assertTrue(c and near(c, GREEN, 10), (fmt, c))

    def test_a_coloured_ground_covers_most(self):
        # The ground is the icon's colour when it covers more than its symbol.
        raw = raster("PNG", BLUE + (255,), big=(250, 250, 250), size=120)
        im = Image.open(io.BytesIO(raw))
        self.assertTrue(near(icon_colour.prevailing(raw, "png"), BLUE), im.size)

    def test_only_neutrals_have_no_colour(self):
        for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
            raw = raster(fmt, (255, 255, 255, 255), big=(20, 20, 20), small=(128, 128, 128))
            self.assertIsNone(icon_colour.prevailing(raw, ext), fmt)
        self.assertIsNone(icon_colour.prevailing(raster("PNG", (0, 0, 0, 0), big=(0, 0, 0), small=(255, 255, 255)), "png"))

    def test_a_png_reads_without_pillow(self):
        real = builtins.__import__

        def no_pil(name, *a, **k):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError(name)
            return real(name, *a, **k)
        cases = [(raster("PNG", (255, 255, 255, 0)), GREEN), (raster("PNG", (255, 255, 255, 255)), GREEN)]
        im = Image.new("RGB", (60, 60), (255, 255, 255))
        ImageDraw.Draw(im).ellipse((5, 5, 55, 55), fill=RED)
        for pal in (im.convert("P", palette=Image.ADAPTIVE, colors=8),):
            b = io.BytesIO(); pal.save(b, "PNG"); cases.append((b.getvalue(), RED))
        b = io.BytesIO(); Image.new("LA", (20, 20), (90, 255)).save(b, "PNG"); cases.append((b.getvalue(), None))
        with mock.patch.object(builtins, "__import__", no_pil):
            for raw, want in cases:
                c = icon_colour.prevailing(raw, "png")
                self.assertTrue(near(c, want, 12) if want else c is None, (c, want))
            self.assertIsNone(icon_colour.prevailing(raster("JPEG", (255, 255, 255)), "jpg"),
                              "a JPEG without Pillow keeps the palette pick")

    def test_svg_by_covered_area(self):
        # A white ground, a red disc covering most of it, a blue square on a corner.
        svg = (f'<svg {NS} viewBox="0 0 48 48"><rect width="48" height="48" fill="#fff"/>'
               '<circle cx="24" cy="24" r="18" fill="#e03030"/><rect width="10" height="10" fill="#3050e0"/></svg>')
        self.assertEqual(icon_colour.prevailing(svg.encode(), "svg"), "#e03030")
        # A shape on top hides what is under it: the ground's hue is what still shows.
        svg = (f'<svg {NS} viewBox="0 0 48 48"><rect width="48" height="48" fill="#3050e0"/>'
               '<rect x="4" y="4" width="40" height="40" fill="#22a06b"/></svg>')
        self.assertEqual(icon_colour.prevailing(svg.encode(), "svg"), "#22a06b")
        # A transparent ground, a stroked shape in a <g> with a transform, style= paint.
        svg = (f'<svg {NS} viewBox="0 0 48 48"><g transform="translate(4 4)" style="fill:none;stroke:#8367BB">'
               '<path d="M2 2 L38 2 L38 38 Z" stroke-width="6"/></g></svg>')
        self.assertEqual(icon_colour.prevailing(svg.encode(), "svg"), "#8367bb")

    def test_svg_gradient_and_neutrals(self):
        svg = (f'<svg {NS} viewBox="0 0 4 4"><defs><linearGradient id="g"><stop offset="0" stop-color="#ff8000"/>'
               '<stop offset="1" style="stop-color:#ffa040"/></linearGradient></defs><rect width="4" height="4" fill="url(#g)"/></svg>')
        self.assertTrue(abs(hue(icon_colour.prevailing(svg.encode(), "svg")) - 30) < 8)
        svg = f'<svg {NS} viewBox="0 0 4 4"><rect width="4" height="4" fill="#222"/><circle cx="2" cy="2" r="1" fill="white"/></svg>'
        self.assertIsNone(icon_colour.prevailing(svg.encode(), "svg"))
        self.assertIsNone(icon_colour.prevailing(b"<svg><rect></svg>", "svg"), "an unreadable icon has none")


class Shades(unittest.TestCase):
    """The PO label's text on the fill keeps 4.5:1 in a light and a dark theme."""

    def test_every_hue_reads_in_both_themes(self):
        worst = {"light": 99.0, "dark": 99.0}
        for h in range(0, 360, 10):
            for l in (0.15, 0.3, 0.45, 0.55, 0.7, 0.85):
                for s in (0.4, 0.75, 1.0):
                    c = icon_gen._hex(tuple(x * 255 for x in colorsys.hls_to_rgb(h / 360, l, s)))
                    if icon_gen._neutral(icon_gen.parse_colour(c)):
                        continue
                    out = icon_colour.shades(c)
                    for theme in ("light", "dark"):
                        fill = icon_gen.parse_colour(out[theme])
                        ink = icon_gen.parse_colour(icon_colour.ink(out[theme]))
                        r = icon_gen._contrast(fill, ink)
                        worst[theme] = min(worst[theme], r)
                        self.assertGreaterEqual(r, 4.5, (c, theme, out))
                        gap = icon_gen._hue_gap(fill, icon_gen.parse_colour(c))
                        self.assertLess(gap, 6, ("the hue is kept", c, theme, out))
                    # A dark theme's fill is the lighter one, under dark ink (as the palette's).
                    self.assertEqual(icon_colour.ink(out["dark"]), icon_gen.DARK, (c, out))
        print(f"\n[P366] worst label contrast over 700+ colours: light {worst['light']:.2f}:1, dark {worst['dark']:.2f}:1")

    def test_a_readable_colour_is_kept(self):
        self.assertEqual(icon_colour.shades("#8367BB")["light"], "#8367bb")     # white ink, 4.6:1
        self.assertEqual(icon_colour.shades("#E2B203"), {"hex": "#e2b203", "light": "#e2b203", "dark": "#e2b203"})
        out = icon_colour.shades("#102060")                                    # a navy: white ink in light
        self.assertEqual(out["light"], "#102060")
        self.assertGreater(colorsys.rgb_to_hls(*(x / 255 for x in icon_gen.parse_colour(out["dark"])))[1],
                           colorsys.rgb_to_hls(*(x / 255 for x in icon_gen.parse_colour("#102060")))[1],
                           "a dark theme lifts a deep colour off its dark ground")
        self.assertIsNone(icon_colour.shades("not a colour"))


def png(colour, ground=(255, 255, 255, 0)) -> bytes:
    if Image is None:   # a solid 1x1 PNG, written by hand
        import struct
        import zlib
        chunk = lambda k, b: struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(colour))) + chunk(b"IEND", b""))
    return raster("PNG", ground, big=colour, small=colour)


class Stored(Hub):
    def upload(self, raw, mime="image/png", name="logo.png", **extra):
        st, _, body = self.call("POST", "/api/projects/icon",
                                {"projectId": self.ed, "dataUrl": data_url(raw, mime), "name": name, **extra})
        self.assertEqual(st, 200, body)
        return json.loads(body)

    def group(self, pid=None) -> dict:
        return next(g for g in dashboard.build_projects()["projects"] if g["id"] == (pid or self.ed))

    def test_upload_replace_generate_remove(self):
        self.upload(png(GREEN))
        c = self.meta(self.ed)["colour"]
        self.assertTrue(near(c, GREEN), c)
        self.assertEqual(self.meta(self.ed)["colourOf"], self.meta(self.ed)["icon"]["v"])
        self.assertEqual(self.group()["colour"], icon_colour.shades(c))
        self.assertNotIn("colour", self.group(self.ot), "a project without an icon has the palette pick")
        # Replace works it out again.
        self.upload(png(BLUE), name="other.png")
        self.assertTrue(near(self.meta(self.ed)["colour"], BLUE), self.meta(self.ed))
        # A generated icon (drawn in the colour the project has) keeps it, whatever it is drawn in.
        svg = f'<svg {NS} viewBox="0 0 4 4"><rect width="4" height="4" fill="#e03030"/></svg>'.encode()
        self.upload(svg, "image/svg+xml", "generated-icon.svg", generated=True)
        self.assertTrue(near(self.meta(self.ed)["colour"], BLUE), self.meta(self.ed))
        self.assertEqual(self.meta(self.ed)["icon"]["file"], "project-icon.svg")
        # ... and a project with no colour of its own keeps having none.
        st, _, _ = self.call("POST", "/api/projects/icon", {"projectId": self.ot, "dataUrl": data_url(svg, "image/svg+xml"),
                                                           "name": "generated-icon.svg", "generated": True})
        self.assertEqual(st, 200)
        self.assertNotIn("colour", self.meta(self.ot))
        self.assertNotIn("colour", self.group(self.ot))
        # An icon with no real hue: none, the palette pick stays.
        self.upload(png((30, 30, 30)), name="grey.png")
        self.assertNotIn("colour", self.meta(self.ed))
        self.assertNotIn("colour", self.group())
        # Remove: the colour goes with it.
        self.upload(png(RED), name="red.png")
        self.assertIn("colour", self.meta(self.ed))
        st, _, _ = self.call("POST", "/api/projects/icon", {"projectId": self.ed, "dataUrl": None})
        self.assertEqual(st, 200)
        self.assertNotIn("colour", self.meta(self.ed))
        self.assertNotIn("colourOf", self.meta(self.ed))
        self.assertNotIn("colour", self.group())

    def test_icons_kept_before_get_their_colour_once(self):
        self.upload(png(GREEN))
        for k in ("colour", "colourOf"):        # as an icon kept before P366 was
            dashboard._set_project_field(self.ed, k, None)
        self.assertNotIn("colour", self.group())
        self.assertEqual(dashboard.derive_icon_colours(), 1)
        self.assertTrue(near(self.meta(self.ed)["colour"], GREEN))
        self.assertIn("colour", self.group())
        with mock.patch.object(icon_colour, "prevailing", side_effect=AssertionError("worked out again")):
            self.assertEqual(dashboard.derive_icon_colours(), 0, "once per icon")


if __name__ == "__main__":
    unittest.main()
