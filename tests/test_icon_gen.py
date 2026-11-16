"""#335 Generate: an initiative's icon drawn by the hub's CLI.

* sanitize(): anything that could run, load or hide (script, event
  attributes, links, foreignObject, use, image, style, url(), javascript:,
  a DTD or entity, CDATA, a processing instruction, a namespaced attribute,
  text, too big, too many elements, too deep, a foreign root) is refused; a
  plain icon is written out again from its tree, 48x48 with its viewBox, and
  #329's own check (project_icon.accept) takes it;
* parse(): the usable ones from a chatty answer, no two alike, at most four;
  linear on many openers with no closer, at most 32 tried, `</svg >` closes;
* _run(): the prompt goes in on stdin, never in argv (a Windows .cmd shim
  would shell-parse it); a runaway answer is read only up to MAX_OUTPUT;
* about_text(): the roadmap's first line of prose, its first sentence;
* generate() with the CLI stubbed: ok, no CLI, a timeout, an error, no
  usable icon, already busy; the prompt carries the name and what it is about;
* the endpoints: GET says which CLI draws and from what; POST answers the
  icons or the reason with its status; only from the page; the typed name
  is used when given.
* P362 the PO label colour: the prompt names it; colours parse from hex,
  rgb(), hsl(), names, fill and stroke attributes and style; check() finds the
  main hued colour by area, inherited from a <g>; recolour() leaves an icon
  already in the colour untouched (the same string) and maps a wrong main
  colour to the label colour, other hues to tints or shades of it, a neutral
  that would read faintly to white or near-black; generate() and the POST
  pass the page's colour on.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import icon_gen  # noqa: E402
import project_icon  # noqa: E402
from tests.test_project_settings import Hub  # noqa: E402

NS = 'xmlns="http://www.w3.org/2000/svg"'
GOOD = (f'<svg {NS} viewBox="0 0 48 48"><rect x="0" y="0" width="48" height="48" rx="10" fill="#0F766E"/>'
        '<path d="M26 8 14 27h9l-2 13 13-20h-9z" fill="#FFFFFF" stroke="none"/></svg>')
GOOD2 = (f'<svg {NS} viewBox="0 0 48 48" width="200" height="200"><rect width="48" height="48" rx="10" fill="rgb(234, 88, 12)"/>'
         '<g transform="rotate(45 24 24)" opacity="0.9"><circle cx="24" cy="24" r="9" fill="none" stroke="#fff" stroke-width="4"/>'
         '<line x1="24" y1="8" x2="24" y2="40" stroke="#fff" stroke-linecap="round"/></g>'
         '<polygon points="10,10 14,10 12,14" fill="#fde047"/><ellipse cx="38" cy="38" rx="3" ry="2" fill="#fde047"/>'
         '<polyline points="4 44 8 40 12 44" fill="none" stroke="#fde047"/></svg>')


def svg(inner: str, root_attrs: str = 'viewBox="0 0 48 48"') -> str:
    return f'<svg {NS} {root_attrs}>{inner}</svg>'


class Sanitize(unittest.TestCase):
    def refused(self, text: str):
        with self.assertRaises(icon_gen.Unsafe, msg=str(text)[:120]):
            icon_gen.sanitize(text)

    def test_a_plain_icon_comes_out_clean_and_329_takes_it(self):
        for s in (GOOD, GOOD2):
            out = icon_gen.sanitize(s)
            self.assertTrue(out.startswith(f'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">'), out)
            self.assertNotIn('width="200"', out)
            self.assertEqual(icon_gen.sanitize(out), out, "stable when read again")
            raw, ext = project_icon.accept("data:image/svg+xml;base64," + base64.b64encode(out.encode()).decode())
            self.assertEqual(ext, "svg")
            self.assertEqual(raw.decode(), out)

    def test_viewbox_twice_in_two_cases_is_kept_once(self):
        out = icon_gen.sanitize(svg('<rect width="4" height="4"/>', 'viewBox="0 0 48 48" VIEWBOX="0 0 9 9"'))
        self.assertEqual(out.count("viewBox="), 1)
        self.assertIn('viewBox="0 0 48 48"', out)
        ET.fromstring(out)   # well-formed
        self.assertEqual(project_icon.accept("data:image/svg+xml;base64," + base64.b64encode(out.encode()).decode())[1], "svg")

    def test_whatever_runs_or_links_is_refused(self):
        for bad in (
            svg('<script>alert(1)</script>'),
            svg('<rect width="4" height="4" onload="alert(1)"/>'),
            svg('<rect width="4" height="4"/>', 'viewBox="0 0 48 48" onclick="x()"'),
            svg('<a href="https://x.example"><rect width="4" height="4"/></a>'),
            svg('<rect width="4" height="4" href="#x"/>'),
            f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 48 48"><rect xlink:href="https://x.example" width="4" height="4"/></svg>',
            svg('<foreignObject width="4" height="4"><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject>'),
            svg('<use href="#a"/>'),
            svg('<image href="https://x.example/a.png" width="4" height="4"/>'),
            svg('<rect width="4" height="4" style="fill:red"/>'),
            svg('<style>rect{fill:url(https://x.example)}</style>'),
            svg('<rect width="4" height="4" fill="url(#g)"/>'),
            svg('<rect width="4" height="4" fill="url(https://x.example/a)"/>'),
            svg('<rect width="4" height="4" fill="javascript:alert(1)"/>'),
            svg('<rect width="4" height="4" fill="&#106;avascript:alert(1)"/>'),
            svg('<rect width="4" height="4" fill="expression(alert(1))"/>'),
            svg('<rect width="4" height="4" fill="var(--x)"/>'),
            svg('<rect width="4" height="4" transform="evil(1)"/>'),
            svg('<rect width="4" height="4" fill="a;b"/>'),
            svg('<rect width="4" height="4" fill="&quot;&gt;&lt;script&gt;"/>'),
            svg('<rect width="4" height="4" class="x"/>'),
            svg('<rect width="4" height="4" id="x"/>'),
            svg('<animate attributeName="href" to="javascript:alert(1)"/>'),
            svg('<set attributeName="onload" to="alert(1)"/>'),
            svg('<linearGradient id="g"/>'),
            svg('<text x="1" y="9">Motors</text>'),
            svg('<rect width="4" height="4"/>hidden words'),
            svg('<svg viewBox="0 0 4 4"><rect width="4" height="4"/></svg>'),
            svg('<rect width="4" height="4" xml:space="preserve"/>'),
            svg('<x:rect xmlns:x="urn:evil" width="4" height="4"/>'),
            svg('<rect width="4" height="4"/>', 'width="48"'),        # no viewBox
        ):
            self.refused(bad)

    def test_a_dtd_an_entity_cdata_or_a_pi_is_refused(self):
        laughs = ('<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                  '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>' + svg('<rect width="4" height="4" fill="&c;"/>'))
        self.refused(laughs)
        self.refused('<!DOCTYPE svg SYSTEM "file:///etc/passwd">' + svg('<rect width="4" height="4"/>'))
        self.refused(svg('<![CDATA[<script>alert(1)</script>]]><rect width="4" height="4"/>'))
        self.refused(svg('<?xml-stylesheet href="https://x.example/a.css"?><rect width="4" height="4"/>'))
        self.refused('<?xml version="1.0"?>' + GOOD)

    def test_size_count_depth_and_root_are_capped(self):
        self.refused(svg('<path d="' + "M1 1L2 2" * 1200 + '"/>'))            # over 8 KB
        self.refused(svg('<rect width="1" height="1"/>' * 210))
        nested = '<g>' * 9 + '<rect width="1" height="1"/>' + '</g>' * 9
        self.refused(svg(nested))
        icon_gen.sanitize(svg('<g>' * 7 + '<rect width="1" height="1"/>' + '</g>' * 7))
        self.refused('<html><svg viewBox="0 0 4 4"/></html>')
        self.refused('<svg xmlns="urn:not-svg" viewBox="0 0 4 4"><rect width="4" height="4"/></svg>')
        self.refused('<svg viewBox="0 0 4 4"><rect width="4" height="4"')     # cut short
        self.refused(None)


class Parse(unittest.TestCase):
    def test_the_usable_ones_from_a_chatty_answer(self):
        answer = ("Here are your icons:\n```svg\n" + GOOD + "\n" + GOOD + "\n"
                  + svg('<script>x</script>') + "\n" + GOOD2 + "\n```\nEnjoy!")
        got = icon_gen.parse(answer)
        self.assertEqual(got, [icon_gen.sanitize(GOOD), icon_gen.sanitize(GOOD2)])

    def test_text_that_lowercases_longer_does_not_shift_the_cut(self):
        # "İ".lower() is two characters; the offsets must stay the answer's own.
        self.assertEqual(icon_gen.parse("İşte: " + GOOD + "\nİİİ ẞ " + GOOD2),
                         [icon_gen.sanitize(GOOD), icon_gen.sanitize(GOOD2)])

    def test_at_most_four(self):
        many = "\n".join(svg(f'<rect width="{i + 1}" height="4" fill="#123456"/>') for i in range(7))
        got = icon_gen.parse(many)
        self.assertEqual(len(got), 4)
        self.assertIn('width="1"', got[0].split("<rect", 1)[1])

    def test_malformed_answers_parse_in_linear_time(self):
        t = time.monotonic()
        self.assertEqual(icon_gen.parse("<svg " * 60000), [])
        self.assertEqual(icon_gen.parse("<svg>x</svgx " * 20000), [])
        self.assertEqual(icon_gen.parse("<svgx " * 40000 + GOOD), [icon_gen.sanitize(GOOD)])
        self.assertLess(time.monotonic() - t, 1.0)

    def test_at_most_32_are_tried_and_a_spaced_closer_counts(self):
        bad = svg('<script>x</script>')
        self.assertEqual(icon_gen.parse(bad * 32 + GOOD), [])
        self.assertEqual(icon_gen.parse(bad * 31 + GOOD), [icon_gen.sanitize(GOOD)])
        self.assertEqual(icon_gen.parse(GOOD[:-6] + "</svg \n>"), [icon_gen.sanitize(GOOD)])

    def test_nothing_usable(self):
        self.assertEqual(icon_gen.parse(""), [])
        self.assertEqual(icon_gen.parse("I cannot draw."), [])
        self.assertEqual(icon_gen.parse(None), [])


class About(unittest.TestCase):
    def test_the_first_prose_line_cut_at_its_sentence(self):
        text = ("# Motors — roadmap\n\nOwned by the PO.\n\n"
                "Electric drivetrain for the cargo bike, from the motor to the battery. Then the rest.\n- a list item\n")
        self.assertEqual(icon_gen.about_text(text), "Electric drivetrain for the cargo bike, from the motor to the battery.")

    def test_markup_is_dropped_and_long_lines_are_cut(self):
        self.assertEqual(icon_gen.about_text("| a | b |\n> **Bold** _claim_ `here`"), "Bold claim here")
        long = icon_gen.about_text("x" * 500)
        self.assertEqual(len(long), 200)
        self.assertTrue(long.endswith("…"))

    def test_nothing(self):
        self.assertEqual(icon_gen.about_text(""), "")
        self.assertEqual(icon_gen.about_text("# Title only\n\n## And a heading\n"), "")


CLAUDE = {"agent": "claude", "model": "haiku", "bin": "/usr/bin/claude"}


class Generate(unittest.TestCase):
    def run_with(self, answer=None, error=None, cli=CLAUDE):
        seen = {}

        def fake_run(c, text, timeout):
            seen.update(cli=c, text=text, timeout=timeout)
            if error:
                raise error
            return answer
        with mock.patch.object(icon_gen, "which_cli", lambda: cli), mock.patch.object(icon_gen, "_run", fake_run):
            return icon_gen.generate("proj-a", "Motors", "Electric drivetrain."), seen

    def test_ok(self):
        (ok, code, res), seen = self.run_with(GOOD + "\n" + GOOD2)
        self.assertTrue(ok)
        self.assertEqual(code, "ok")
        self.assertEqual((res["agent"], res["model"]), ("claude", "haiku"))
        self.assertEqual(len(res["svgs"]), 2)
        self.assertIsInstance(res["ms"], int)
        self.assertIn('"Motors"', seen["text"])
        self.assertIn('"Electric drivetrain."', seen["text"])
        self.assertEqual(seen["timeout"], 60)

    def test_no_cli(self):
        (ok, code, res), _ = self.run_with(cli=None)
        self.assertEqual((ok, code, res), (False, "no_cli", {}))

    def test_timeout_error_and_nothing_usable(self):
        self.assertEqual(self.run_with(error=subprocess.TimeoutExpired("claude", 60))[0][:2], (False, "timeout"))
        self.assertEqual(self.run_with(error=RuntimeError("signed out"))[0][:2], (False, "failed"))
        self.assertEqual(self.run_with(error=OSError("gone"))[0][:2], (False, "failed"))
        (ok, code, res), _ = self.run_with("Sorry, no.")
        self.assertEqual((ok, code, res["agent"]), (False, "no_icons", "claude"))

    def test_at_most_two_runs_on_the_hub(self):
        go, release = threading.Semaphore(0), threading.Event()

        def slow(c, text, timeout):
            go.release()
            release.wait(5)
            return GOOD
        got = {}
        with mock.patch.object(icon_gen, "which_cli", lambda: CLAUDE), mock.patch.object(icon_gen, "_run", slow):
            ts = [threading.Thread(target=lambda k=k: got.update({k: icon_gen.generate(k, "B", "")})) for k in ("p1", "p2")]
            for t in ts:
                t.start()
            self.assertTrue(go.acquire(timeout=5) and go.acquire(timeout=5))
            self.assertEqual(icon_gen.generate("p3", "C", "")[:2], (False, "busy"), "a third project waits its turn")
            release.set()
            for t in ts:
                t.join(5)
            self.assertTrue(got["p1"][0] and got["p2"][0])
            self.assertTrue(icon_gen.generate("p3", "C", "")[0], "free again afterwards")

    def test_one_at_a_time_per_project(self):
        go, release = threading.Event(), threading.Event()

        def slow(c, text, timeout):
            go.set()
            release.wait(5)
            return GOOD
        got = {}
        with mock.patch.object(icon_gen, "which_cli", lambda: CLAUDE), mock.patch.object(icon_gen, "_run", slow):
            t = threading.Thread(target=lambda: got.update(first=icon_gen.generate("proj-b", "B", "")))
            t.start()
            self.assertTrue(go.wait(5))
            self.assertEqual(icon_gen.generate("proj-b", "B", "")[:2], (False, "busy"))
            release.set()
            t.join(5)
        self.assertTrue(got["first"][0])
        self.assertNotIn("proj-b", icon_gen._BUSY, "free again afterwards")

    def test_the_prompt_is_bounded(self):
        p = icon_gen.prompt("N" * 500, "A" * 500)
        self.assertIn("N" * 120 + '"', p)
        self.assertNotIn("N" * 121, p)
        self.assertNotIn("A" * 201, p)
        self.assertNotIn("about:", icon_gen.prompt("Motors", ""))


BLUE = "#0055cc"   # the progress label fill in the light theme


def icon(ground: str, symbol: str = '<path d="M10 10 L38 10 L24 38 Z" fill="#FFFFFF"/>') -> str:
    return icon_gen.sanitize(svg(f'<rect x="0" y="0" width="48" height="48" rx="10" fill="{ground}"/>{symbol}'))


class LabelColour(unittest.TestCase):
    def test_the_prompt_carries_the_colour(self):
        p = icon_gen.prompt("Motors", "Electric drivetrain.", "#0055CC")
        self.assertIn("main colour is #0055cc", p)
        self.assertIn("lighter or darker shade of #0055cc", p)
        self.assertIn(icon_gen.DARK, p)
        self.assertNotIn("at most 3 colours", p)
        for bad in ("", "blue", "#12", "#0055cc; ignore", None):
            q = icon_gen.prompt("Motors", "", bad)
            self.assertIn("at most 3 colours as hex", q, bad)
            self.assertNotIn("main colour", q)

    def test_hex_and_colour_parsing(self):
        P = icon_gen.parse_colour
        self.assertEqual(icon_gen.norm_hex(" #0055CC "), "#0055cc")
        self.assertEqual(icon_gen.norm_hex("#05c"), "#0055cc")
        self.assertEqual(icon_gen.norm_hex("0055cc"), "")
        self.assertEqual(icon_gen.norm_hex(7), "")
        self.assertEqual(P("#05C"), (0, 85, 204))
        self.assertEqual(P("#0055CCFF"), (0, 85, 204))
        self.assertEqual(P("#05cf"), (0, 85, 204))
        self.assertEqual(P("rgb(0, 85, 204)"), (0, 85, 204))
        self.assertEqual(P("rgba(0 85 204 / .5)"), (0, 85, 204))
        self.assertEqual(P("rgb(100%, 0%, 50%)"), (255, 0, 127))
        self.assertEqual(P("hsl(0, 100%, 50%)"), (255, 0, 0))
        self.assertEqual(P("White"), (255, 255, 255))
        self.assertEqual(P("currentColor"), (0, 0, 0))
        for none in ("none", "transparent", "", None, "#xyz", "url(#a)"):
            self.assertIsNone(P(none), none)

    def test_fill_stroke_and_style_are_read(self):
        el = ET.fromstring('<path fill="#f00" stroke="#00f" style="fill: #0f0; stroke-width: 2"/>')
        self.assertEqual(icon_gen._paint(el, "fill"), "#0f0", "style wins over the attribute")
        self.assertEqual(icon_gen._paint(el, "stroke"), "#00f")
        self.assertIsNone(icon_gen._paint(ET.fromstring("<path/>"), "fill"))
        ps = icon_gen._paints(ET.fromstring(svg(
            '<rect width="48" height="48" fill="#fff"/><g fill="#dc2626" stroke="#fbbf24"><circle cx="24" cy="24" r="10"/></g>'
            '<line x1="0" y1="0" x2="10" y2="0" stroke="#000"/><path d="M0 0h4v4z"/>')))
        got = [(icon_gen._local(p["el"].tag), p["prop"], p["rgb"], p["ground"]) for p in ps]
        self.assertEqual(got, [("rect", "fill", (255, 255, 255), True), ("circle", "fill", (220, 38, 38), False),
                               ("circle", "stroke", (251, 191, 36), False), ("line", "stroke", (0, 0, 0), False),
                               ("path", "fill", (0, 0, 0), False)], "inherited from the <g>; a line has no fill; unset is black")
        self.assertEqual(icon_gen._paints(ET.fromstring(svg('<rect width="48" height="48" fill="#fff"/><g fill="#dc2626">'
                                                            '<circle cx="24" cy="24" r="10"/></g>')))[1]["src"].tag.rsplit("}")[-1], "g")
        # A path's size from its commands, relative ones too.
        self.assertEqual(icon_gen._box(ET.fromstring('<path d="M10 10 l28 0 l-14 28 z"/>'), "path"), (28.0, 28.0))
        self.assertEqual(icon_gen._box(ET.fromstring('<path d="M4 4H44V30"/>'), "path"), (40.0, 26.0))

    def test_an_icon_already_in_the_colour_is_untouched(self):
        for s in (icon(BLUE),                                     # the ground in it, a white symbol
                  icon("#0055CC", '<path d="M10 10h28v28z" fill="#FFFFFF"/><circle cx="9" cy="9" r="2" fill="#85b8ff"/>'),
                  icon("#FFFFFF", f'<path d="M10 10h28v28z" fill="{BLUE}"/>'),   # the symbol in it
                  icon("rgb(0, 85, 204)")):
            self.assertTrue(icon_gen.check(s, BLUE)["ok"], s)
            self.assertIs(icon_gen.recolour(s, BLUE), s)
        orange = icon("#F97316")
        self.assertIs(icon_gen.recolour(orange, ""), orange, "no colour: nothing to check")

    def test_a_wrong_ground_becomes_the_label_colour(self):
        s = icon("#F97316", '<path d="M10 10 L38 10 L24 38 Z" fill="#FFFFFF"/><circle cx="24" cy="20" r="4" fill="#16A34A"/>')
        c = icon_gen.check(s, BLUE)
        self.assertEqual((c["ok"], c["dominant"]), (False, "#f97316"))
        self.assertEqual(c["foreign"], ["#16a34a", "#f97316"])
        r = icon_gen.recolour(s, BLUE)
        self.assertIn('rx="10" fill="#0055cc"', r)
        self.assertIn('fill="#FFFFFF"', r, "white stays")
        self.assertNotIn("#16a34a", r.lower())
        self.assertTrue(icon_gen.check(r, BLUE)["ok"], r)
        self.assertEqual(icon_gen.sanitize(r), r, "written as sanitize writes")
        self.assertEqual(project_icon.accept("data:image/svg+xml;base64," + base64.b64encode(r.encode()).decode())[1], "svg")

    def test_a_wrong_symbol_on_a_dark_ground(self):
        s = icon("#1D2125", '<g fill="rgb(220,38,38)"><circle cx="24" cy="24" r="12"/></g>'
                            '<line x1="5" y1="5" x2="40" y2="40" stroke="#fbbf24" stroke-width="3"/>')
        r = icon_gen.recolour(s, BLUE)
        self.assertIn('<g fill="#0055cc">', r, "changed where it is set")
        self.assertNotIn("#fbbf24", r)
        self.assertIn('fill="#ffffff"', r, "the label colour reads faintly on near-black: the ground turns white")
        self.assertTrue(icon_gen.check(r, BLUE)["ok"], r)

    def test_another_hue_on_the_new_ground_still_reads(self):
        # A live haiku answer from today's prompt: an amber bolt on navy, for a green project.
        s = icon("#1e3a5f", '<path d="M26 6 14 26h9l-3 16 14-22h-9z" fill="#f59e0b"/>')
        r = icon_gen.recolour(s, "#216E4E")
        self.assertIn('rx="10" fill="#216e4e"', r)
        bolt = icon_gen.parse_colour(ET.fromstring(r)[1].get("fill"))
        self.assertLessEqual(icon_gen._hue_gap(bolt, (0x21, 0x6E, 0x4E)), icon_gen.HUE_SPAN, "a tint of the green")
        self.assertGreaterEqual(icon_gen._contrast(bolt, (0x21, 0x6E, 0x4E)), 3, "and 3:1 on it")
        self.assertTrue(icon_gen.check(r, "#216E4E")["ok"], r)

    def test_near_is_not_the_colour(self):
        # Review 1: the main colour must be the label colour itself, not one near it.
        for near in ("#23885f", "#2a7350"):
            s = icon(near)
            self.assertFalse(icon_gen.check(s, "#216E4E")["ok"], near)
            r = icon_gen.recolour(s, "#216E4E")
            self.assertIn('rx="10" fill="#216e4e"', r)
            self.assertTrue(icon_gen.check(r, "#216E4E")["ok"])

    def test_the_symbol_always_reads_on_the_ground(self):
        # Review 1: a symbol already in the label colour on a ground that becomes it.
        s = icon("#f97316", '<path d="M10 10h28v28z" fill="#216e4e"/>')
        r = icon_gen.recolour(s, "#216E4E")
        self.assertIn('rx="10" fill="#216e4e"', r)
        self.assertIn('<path d="M10 10h28v28z" fill="#ffffff"/>', r, "not green on green")
        self.assertTrue(icon_gen.check(r, "#216E4E")["ok"])
        # White on the yellow label colour is 1.98:1: the check fails it, the symbol turns near-black.
        y = icon("#E2B203", '<path d="M10 10h28v28z" fill="#FFFFFF"/>')
        c = icon_gen.check(y, "#E2B203")
        self.assertEqual((c["ok"], c["legible"], c["dominant"]), (False, False, "#e2b203"))
        r = icon_gen.recolour(y, "#E2B203")
        self.assertIn('fill="#1d2125"', r)
        self.assertTrue(icon_gen.check(r, "#E2B203")["ok"])
        # The label colour on a near-black ground under 3:1: the ground turns white.
        d = icon_gen.recolour(icon("#1D2125", f'<path d="M10 10h28v28z" fill="#ff0000"/>'), BLUE)
        self.assertIn('rx="10" fill="#ffffff"', d)
        self.assertIn(f'fill="{BLUE}"', d)

    def test_recolouring_may_outgrow_the_size_cap(self):
        # Review 1: 155 red shapes near MAX_SVG_BYTES; each "red" becomes a 7-character hex.
        paths = "".join(f'<path d="M{i % 40} {i // 40}h2v2h-2v-2h1v1h1v-1h1z" fill="red"/>'
                        for i in range(155))
        s = icon_gen.sanitize(svg('<rect x="0" y="0" width="48" height="48" fill="#FFFFFF"/>' + paths))
        self.assertTrue(icon_gen.MAX_SVG_BYTES - 620 < len(s.encode()) <= icon_gen.MAX_SVG_BYTES)
        r = icon_gen.recolour(s, "#216E4E")
        self.assertGreater(len(r.encode()), icon_gen.MAX_SVG_BYTES, "it outgrew the cap")
        self.assertTrue(icon_gen.check(r, "#216E4E")["ok"])
        self.assertNotIn("red", r)
        raw, ext = project_icon.accept("data:image/svg+xml;base64," + base64.b64encode(r.encode()).decode())
        self.assertEqual(ext, "svg", "#329 still takes it")

    def test_generate_leaves_out_what_recolour_cannot_fix(self):
        cli = {"agent": "claude", "model": "haiku", "bin": "claude"}
        good, bad = icon(BLUE), icon("#F97316")
        with mock.patch.object(icon_gen, "which_cli", lambda: cli), \
                mock.patch.object(icon_gen, "_run", lambda c, t, to: good + bad), \
                mock.patch.object(icon_gen, "recolour", lambda s, c: s):
            ok, code, res = icon_gen.generate("proj-d", "Motors", "", colour=BLUE)
            self.assertEqual((ok, res["svgs"]), (True, [good]), "never shown off-colour")
        with mock.patch.object(icon_gen, "which_cli", lambda: cli), \
                mock.patch.object(icon_gen, "_run", lambda c, t, to: bad), \
                mock.patch.object(icon_gen, "recolour", lambda s, c: s):
            self.assertEqual(icon_gen.generate("proj-d", "Motors", "", colour=BLUE)[:2], (False, "no_icons"))

    def test_black_on_white_takes_the_colour_on_its_symbol(self):
        s = icon("white", '<path d="M10 10 l28 0 l-14 28 z"/>')
        self.assertFalse(icon_gen.check(s, BLUE)["ok"])
        r = icon_gen.recolour(s, BLUE)
        self.assertIn('<path d="M10 10 l28 0 l-14 28 z" fill="#0055cc"/>', r)
        self.assertIn('fill="white"', r)
        self.assertTrue(icon_gen.check(r, BLUE)["ok"])

    def test_a_neutral_that_would_read_faintly_turns_white_or_dark(self):
        r = icon_gen.recolour(icon("#FACC15", '<path d="M10 10H38V38H10Z" fill="#000"/>'), "#1f3a8a")
        self.assertIn('fill="#ffffff"', r, "black on a deep blue ground is under 3:1: white")
        k = icon_gen.recolour(icon("#FACC15", '<path d="M10 10H38V38H10Z" fill="#000"/>'), BLUE)
        self.assertIn('fill="#000"', k, "black on #0055cc is 3:1: it stays")
        y = icon_gen.recolour(icon("#16A34A", '<path d="M10 10H38V38H10Z" fill="#FFFFFF"/>'), "#e2b203")
        self.assertIn('fill="#1d2125"', y, "white on the yellow ground is too faint: near-black")

    def test_style_paint_is_mapped_in_the_style(self):
        root = ET.fromstring(svg('<rect width="48" height="48" style="fill:#F97316"/>'))
        # sanitize() refuses style, so recolour is fed one built by hand.
        with mock.patch.object(icon_gen, "sanitize", lambda t, *a: t):
            r = icon_gen.recolour(icon_gen._write(root), BLUE)
        self.assertIn('style="fill:#0055cc"', r)

    def test_generate_maps_each_icon_and_says_how_many(self):
        cli = {"agent": "claude", "model": "haiku", "bin": "claude"}
        seen = {}
        answer = icon(BLUE) + "\n" + icon("#F97316") + "\n" + icon("#16A34A")

        def fake_run(c, text, timeout):
            seen["text"] = text
            return answer
        with mock.patch.object(icon_gen, "which_cli", lambda: cli), mock.patch.object(icon_gen, "_run", fake_run):
            ok, code, res = icon_gen.generate("proj-c", "Motors", "", colour="#0055CC")
        self.assertIn("main colour is #0055cc", seen["text"])
        self.assertTrue(ok)
        self.assertEqual((res["colour"], res["recoloured"]), (BLUE, 2))
        self.assertEqual(len(res["svgs"]), 1, "two that map to the same icon are one")
        self.assertTrue(all(icon_gen.check(x, BLUE)["ok"] for x in res["svgs"]))


class Run(unittest.TestCase):
    """_run() with subprocess.run caught or a stand-in CLI script."""
    NASTY = 'Harbour" & del /q * | calc ^ %PATH% $(id) `id`'

    def caught(self, cli):
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"] = cmd
            seen["stdin"] = kw["stdin"].read().decode("utf-8")
            self.assertNotIn("input", kw)
            seen["flags"] = kw.get("creationflags")
            seen["cwd"] = kw.get("cwd")
            kw["stdout"].write(GOOD.encode())
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(icon_gen.subprocess, "run", fake), mock.patch.object(icon_gen, "_NO_WINDOW", 0x08000000):
            out = icon_gen._run(cli, icon_gen.prompt(self.NASTY, "about & | ^"), 5)
        return seen, out

    def test_the_prompt_goes_in_on_stdin_never_in_argv(self):
        for cli in (CLAUDE, {"agent": "codex", "model": "", "bin": "C:/npm/codex.cmd"}):
            seen, out = self.caught(cli)
            self.assertIn(self.NASTY, seen["stdin"], cli["agent"])
            self.assertFalse(any("Harbour" in a or "about" in a for a in seen["cmd"]), seen["cmd"])
            self.assertEqual(out, GOOD)
        c = self.caught(CLAUDE)[0]
        # No MCP servers, no session folder left behind, no tools; no console window on Windows.
        self.assertEqual(c["cmd"][1:], ["-p", "--model", "haiku", "--no-session-persistence",
                                        "--tools", "", "--strict-mcp-config"])
        self.assertEqual(c["flags"], 0x08000000)
        # The hub's one workspace, so no ~/.claude/projects/-tmp-ens-icon-* per run.
        self.assertEqual(Path(c["cwd"]), dashboard.DASHBOARD_DIR / "_rename_workspace")
        self.assertEqual(seen["cmd"][-1], "-", "codex exec reads its prompt from stdin")

    def test_a_lone_surrogate_does_not_break_the_run(self):
        seen = {}

        def fake(cmd, **kw):
            seen["stdin"] = kw["stdin"].read().decode("utf-8")
            kw["stdout"].write(GOOD.encode())
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(icon_gen.subprocess, "run", fake):
            self.assertEqual(icon_gen._run(CLAUDE, icon_gen.prompt("A\ud800B", ""), 5), GOOD)
        self.assertIn('"A?B"', seen["stdin"])

    def test_claude_is_found_the_hub_s_way(self):
        fake = mock.Mock(_claude_bin=lambda: "/opt/x/claude")
        with mock.patch.object(icon_gen, "_d", fake):
            self.assertEqual(icon_gen.which_cli()["bin"], "/opt/x/claude")
        self.assertIs(icon_gen._d, dashboard, "the hub binds itself")

    @unittest.skipIf(os.name == "nt", "a POSIX stand-in script")
    def test_a_runaway_answer_is_read_only_up_to_the_cap(self):
        with tempfile.TemporaryDirectory() as d:
            cli = Path(d) / "stand-in"   # a "claude" would be refused by agent_launch in a test
            cli.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
                           "sys.stdout.write('x' * (3 * 1024 * 1024))\n")
            cli.chmod(0o755)
            out = icon_gen._run({"agent": "claude", "model": "haiku", "bin": str(cli)}, "p", 20)
            self.assertEqual(len(out), icon_gen.MAX_OUTPUT)
            cli.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('e' * 100000 + 'signed out')\nsys.exit(1)\n")
            with self.assertRaises(RuntimeError) as e:
                icon_gen._run({"agent": "claude", "model": "haiku", "bin": str(cli)}, "p", 20)
            self.assertLessEqual(len(str(e.exception)), 300)


class Endpoints(Hub):
    def test_get_says_which_cli_and_from_what(self):
        home = self.home(self.ed)
        home.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(dashboard, "read_roadmap", lambda proj: {"text": "# ED\n\nA dashboard for agent teams. More."}), \
                mock.patch.object(icon_gen, "which_cli", lambda: CLAUDE):
            st, _, body = self.call("GET", f"/api/projects/icon-gen?id={self.ed}")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body), {"agent": "claude", "model": "haiku", "name": "Ensemble Dashboard",
                                            "about": "A dashboard for agent teams."})
        with mock.patch.object(icon_gen, "which_cli", lambda: None):
            st, _, body = self.call("GET", f"/api/projects/icon-gen?id={self.ed}")
        self.assertEqual((st, json.loads(body)["agent"]), (200, ""))
        st, _, _ = self.call("GET", "/api/projects/icon-gen?id=proj-nope")
        self.assertEqual(st, 404)

    def post(self, body):
        st, _, raw = self.call("POST", "/api/projects/icon-gen", body)
        return st, json.loads(raw)

    def test_post_answers_the_icons_or_why_not(self):
        seen = {}

        def gen(key, name, about, timeout=60, colour=""):
            seen.update(key=key, name=name, colour=colour)
            return seen.get("answer")
        with mock.patch.object(icon_gen, "generate", gen):
            seen["answer"] = (True, "ok", {"agent": "claude", "model": "haiku", "svgs": [icon_gen.sanitize(GOOD)], "ms": 5})
            st, body = self.post({"projectId": self.ed})
            self.assertEqual((st, body["ok"], len(body["svgs"]), body["agent"]), (200, True, 1, "claude"))
            self.assertEqual((seen["key"], seen["name"], seen["colour"]), (self.ed, "Ensemble Dashboard", ""))
            self.post({"projectId": self.ed, "colour": "#0055CC"})
            self.assertEqual(seen["colour"], "#0055CC", "the page's label colour goes on (icon_gen checks it)")
            self.post({"projectId": self.ed, "colour": 7})
            self.assertEqual(seen["colour"], "")
            st, body = self.post({"projectId": self.ed, "name": "  Harbour   Lights "})
            self.assertEqual(seen["name"], "Harbour Lights", "the name typed in the settings, not saved yet")
            st, body = self.post({"projectId": self.ed, "name": "Harbour \ud800 Lights"})
            self.assertEqual((st, seen["name"]), (200, "Harbour ? Lights"), "a lone surrogate is replaced, not a dropped connection")
            self.post({"projectId": self.ed, "name": 7})
            self.assertEqual(seen["name"], "Ensemble Dashboard")
            # Never 502-504: hub-resilience.js reads those as the hub being down.
            for code, status in (("no_cli", 200), ("timeout", 200), ("busy", 409), ("failed", 200), ("no_icons", 200)):
                seen["answer"] = (False, code, {"agent": "claude", "model": "haiku"} if code != "no_cli" else {})
                st, body = self.post({"projectId": self.ed})
                self.assertEqual((st, body["error"], body["ok"]), (status, code, False))
                self.assertEqual(body["message"], icon_gen.REFUSALS[code])
        st, body = self.post({"projectId": "proj-nope"})
        self.assertEqual((st, body["error"]), (404, "no_such_project"))

    def test_only_from_the_page(self):
        self.page = False
        with mock.patch.object(icon_gen, "generate", side_effect=AssertionError("must not run")):
            st, body = self.post({"projectId": self.ed})
            self.assertEqual((st, body["error"]), (403, "page_only"))
        # The GET has no Origin to go by: the page key and Sec-Fetch-Site gate it.
        for kw in ({"cookie": False}, {"site": "cross-site"}, {"site": "same-site"}):
            st, _, _ = self.call("GET", f"/api/projects/icon-gen?id={self.ed}", **kw)
            self.assertEqual(st, 403, kw)


if __name__ == "__main__":
    unittest.main()
