"""A picture an agent names by its path shows in the balloon as a thumbnail,
and a path abbreviated with "..." opens the full path written before it.

The shared link block and mdToHtml of session.html (the same block in
index.html and fileview.html, tests/test_links.py) run in Node and check:

* a picture's path in a code span, in running text, in a link target or in a
  list item is a thumbnail from /api/file with the path's link under it, the
  words after it kept; a non-picture path is its link alone;
* "...\\tail", ".../tail" and "…/tail" open the nearest full path before them
  in the same message whose folders hold their first folder; with none
  before, the text stays as it was written;
* the balloon of the trading PO the CEO pointed at renders its five paths;
* an item of numbered points and a quoted block see the whole message's paths;
* a compact table row ("|...\\x.png|first|") is read cell by cell, as it is drawn;
* a thumbnail the hub had no picture for gives way to the link, titled so,
  and the path is drawn as the link alone from then on;
* the "[image]" thumbnails of a pasted screenshot are as they were;
* the three pages hook mdToHtml and carry the thumbnail's rules.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
PAGES = {n: (ROOT / n).read_text(encoding="utf-8").replace("\r\n", "\n") for n in ("session.html", "index.html", "fileview.html")}
SRC = PAGES["session.html"]
ATTACH = (ROOT / "static" / "attach.js").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def block(begin: str, end: str) -> str:
    i = SRC.index(begin)
    return SRC[i:SRC.index(end, i)]


def js_function(name: str) -> str:
    i = SRC.index(f"\nfunction {name}(") + 1
    return SRC[i:SRC.index("\n}\n", i) + 3]


def js_const(name: str) -> str:
    i = SRC.index(f"\nconst {name} = ") + 1
    j = SRC.index("\n", i)
    while SRC[j + 1:j + 3] == "  ":   # a continuation line (fileHref runs over two)
        j = SRC.index("\n", j + 1)
    return SRC[i:j + 1]


# The balloon of the trading PO (room-3f303104, message df4767051db4), as the hub holds it.
REAL = (
    "If you want to see it before tonight, these are pictures from the test setup, made with the new code:\n"
    "- `C:\\Trading\\dev\\projects\\opTen-microservices\\evidence\\task-59\\1-nothing-selected.png`\n"
    "- `...\\evidence\\task-59\\2-exp-2-oct-fly.png`: the 2 Oct butterfly with the full strike window\n"
    "- `...\\evidence\\task-59\\3-exp-29-sep.png`\n"
    "- `...\\evidence\\task-59\\4-one-account-simulated-2-oct.png`\n"
    "- `...\\evidence\\task-59\\6-exp-25-sep-all-accounts.png`\n\n"
    "The history job's first half (saving each day after the close) goes in tonight too."
)

CASES = {
    "code": "`C:\\x\\shots\\a.png`",
    "text": "see C:\\x\\shots\\a.png now",
    "link": "[the shot](C:\\x\\shots\\a.png)",
    "fileurl": "file:///C:/x/shots/a.png",
    "listtail": "- `C:\\x\\shots\\a.png`: the first shot",
    "notimage": "`C:\\x\\notes.md`",
    "line": "`C:\\x\\shots\\a.png:12`",
    "jpeg": "C:\\x\\shots\\b.JPEG and C:\\x\\shots\\c.webp",
    "real": REAL,
    "alone": "`...\\evidence\\x.png` and ...\\evidence\\y.png and `\u2026/evidence/z.png`",
    "after": "`...\\shots\\x.png` then `C:\\x\\shots\\a.png`",
    "nearest": "`C:\\one\\shots\\a.png` then `C:\\two\\shots\\b.png` then `...\\shots\\c.png` and `C:\\three\\other\\d.png` then `...\\shots\\e.png`",
    "deepest": "`C:\\src\\lib\\src\\a.png` then `...\\src\\b.png`",
    "unix": "/home/f/shots/a.png then \u2026/shots/b.png and .../shots/c.md",
    "folder": "the folder `C:\\x\\shots\\` holds ...\\shots\\a.png",
    "dirbase": "in `C:\\x\\shots\\a.png` then ...\\x\\shots\\deep\\b.png",
    "nofolder": "`C:\\x\\shots\\a.png` then `...\\b.png`",
    "textabbrev": "`C:\\x\\shots\\a.png` then see ...\\shots\\b.png here",
    "points": "## Points (2)\n\n**1.** `C:\\x\\ev\\a.png`\n\n**2.** `...\\ev\\b.png` please",
    "quote": "`C:\\x\\ev\\a.png`\n\n> `...\\ev\\b.png`",
    "image": "words\n[image] C:\\t\\attachments\\room-aaaa0001\\shot.png",
    "gone": "`C:\\x\\ev\\gone.png`",
    # Review 1: each occurrence stands for what was written before it, not the last answer.
    "twice": "`C:\\one\\shots\\a.png` then `...\\shots\\x.png` then `C:\\two\\shots\\a.png` then `...\\shots\\x.png`",
    "firstbare": "`...\\shots\\x.png` then `C:\\one\\shots\\a.png` then `...\\shots\\x.png`",
    "kinds": "`C:\\one\\s\\a.png` ...\\s\\x.png `C:\\two\\s\\a.png` `...\\s\\x.png`",
    "pointstwice": "## Points (3)\n\n**1.** `...\\ev\\b.png`\n\n**2.** `C:\\x\\ev\\a.png`\n\n**3.** `...\\ev\\b.png`",
    "fenced": "`C:\\one\\shots\\a.png`\n\n```\n...\\shots\\f.png\nC:\\two\\shots\\g.png\n```\n\n`...\\shots\\x.png`",
    # Review 1: a full path, then an abbreviation, on one line of running text.
    "sameline": "C:\\one\\shots\\a.png then ...\\shots\\x.png",
    "spaces": "D:\\work\\Ensemble Dashboard\\shots\\a.png then ...\\shots\\x.png",
    # Review 1: a file URL in a code span or a link target is a full path too.
    "fileurlcode": "`file:///C:/one/shots/a.png` then `.../shots/x.png`",
    "fileurllink": "[a](file:///C:/one/shots/a.png) then `.../shots/x.png`",
    # Review 1: every picture kind by its bare name, as written or as a camera writes it.
    "barecode": "`shot.webp` and `shot.bmp` and `shot.PNG` and `shot.JPG` and `shot.svg`",
    "baretext": "shot.webp and shot.bmp and shot.PNG and shot.gif and shot.jpeg",
    # Review 2: a compact table row has its path against the bars; the scanner reads the cells the render draws.
    "cells": "`C:\\one\\shots\\a.png`\n\n|...\\shots\\x.png|first|\n|---|---|\n|...\\shots\\x.png|second|\n\n`C:\\two\\shots\\a.png` then ...\\shots\\x.png",
    "cellbase": "|C:\\one\\shots\\a.png|first|\n|---|---|\n|`...\\shots\\x.png`|second|third ...\\shots\\y.png|\n\n...\\shots\\z.png",
}

JS = r"""
const vm = require('vm');
const { code, cases } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { console, URL, location: new URL('http://hub:8765/session?id=room-aaaa0001'), ROOM: 'room-aaaa0001', ROOM_OBJ: { cwd: 'C:\\t' },
  HUB_PORT: '8765', CHAT_NAMES: {}, refOfUrl: () => null, refChipHtml: () => null, parkTaskRefs: (s, chips) => s,
  REF_A: '\uE004', REF_Z: '\uE005', REF_MARK_RE: /\uE004(\d+)\uE005/g, REF_URL_RE: /https?:\/\/[^\s<>()\[\]{}"'`*|\\]+/gi,
  attOwn: (room, p) => /attachments/.test(p), cases };
vm.createContext(ctx);
vm.runInContext(code + `
const out = {};
for (const [k, t] of Object.entries(cases)) out[k] = mdToHtml(t);
// The hub had no picture at gone.png: the thumbnail gives way to the link.
const srcOf = h => (h.match(/<img src="([^"]*)"/) || [])[1] || '';
const gone = src => {
  const link = { title: '' }, thumb = { removed: false, remove() { this.removed = true; } };
  const wrap = { querySelector: sel => sel === 'a.file-link' ? link : sel === 'a.att-thumb' ? thumb : null };
  pathThumbGone({ closest: sel => sel === '.path-img' ? wrap : null, getAttribute: a => a === 'src' ? src : null });
  return { title: link.title, removed: thumb.removed };
};
out.goneDom = gone(srcOf(out.gone));
out.goneAfter = mdToHtml(cases.gone);
out.goneOther = mdToHtml('\`C:\\\\x\\\\ev\\\\a.png\`');
// A relative path the hub had nothing for in one task is still a picture in another.
out.relA = mdToHtml('\`shots/a.png\`');
out.relADom = gone(srcOf(out.relA));
out.relAAfter = mdToHtml('\`shots/a.png\`');
ROOM = 'room-bbbb0002'; ROOM_OBJ = { cwd: 'C:\\\\u' };
out.relB = mdToHtml('\`shots/a.png\`');
ROOM = 'room-aaaa0001'; ROOM_OBJ = { cwd: 'C:\\\\t' };
globalThis.cases = cases; globalThis.out = out;
`, ctx);
console.log(JSON.stringify(ctx.out));
"""

THUMB = re.compile(r'<span class="path-img"><a class="att-thumb" href="([^"]*)" target="_blank" rel="noopener" title="([^"]*)">'
                   r'<img src="([^"]*)" alt="([^"]*)" loading="lazy" onerror="pathThumbGone\(this\)"></a><a href="([^"]*)" target="_blank" rel="noopener" class="file-link"[^>]*>')


def thumbs(html: str) -> list[dict]:
    """Each thumbnail's request, link and name; path is what the request asks the hub for."""
    return [{"path": unquote(parse_qs(urlparse(m[2]).query).get("path", [""])[0]), "src": m[2], "alt": m[3], "href": m[4], "title": m[1]}
            for m in THUMB.findall(html)]


@unittest.skipUnless(NODE, "node is not installed")
class PathImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = "\n".join([
            ATTACH,
            js_const("esc"),
            block("// ---- Links in rendered text: begin shared block", "// ---- Links in rendered text: end shared block"),
            block("// ---- Numbered points: begin", "// ---- Numbered points: end"),
            js_const("fileBase"), js_const("fileHref"),
            js_function("mdToHtml"), js_function("itemsHtml")])
        r = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code, "cases": CASES}), capture_output=True,
                           text=True, encoding="utf-8", timeout=120)
        assert r.returncode == 0, r.stderr
        cls.out = json.loads(r.stdout)

    A_SRC = "/api/file?path=C%3A%5Cx%5Cshots%5Ca.png&room=room-aaaa0001&cwd=C%3A%5Ct"
    A_HREF = "/fileview?path=C%3A%5Cx%5Cshots%5Ca.png&room=room-aaaa0001&cwd=C%3A%5Ct"

    def one_thumb(self, key: str) -> dict:
        t = thumbs(self.out[key])
        self.assertEqual(len(t), 1, self.out[key])
        return t[0]

    def test_a_pictures_path_is_a_thumbnail_with_its_link_under_it(self):
        for key in ("code", "text", "link", "listtail"):
            t = self.one_thumb(key)
            self.assertEqual(t["src"], self.A_SRC, key)
            self.assertEqual(t["href"], self.A_HREF, key)
            self.assertEqual(t["alt"], "a.png", key)
            self.assertEqual(t["title"], "a.png", key)
            self.assertEqual(t["path"], "C:\\x\\shots\\a.png", key)
        t = self.one_thumb("fileurl")   # a file: URL's path, as the block reads it
        self.assertEqual(t["src"], "/api/file?path=C%3A%2Fx%2Fshots%2Fa.png&room=room-aaaa0001&cwd=C%3A%5Ct")
        self.assertEqual(t["alt"], "a.png")
        self.assertIn('<code class="ic">C:\\x\\shots\\a.png</code></a></span>', self.out["code"], "the path stays as code")
        self.assertRegex(self.out["text"], r'>see <span class="path-img".*a\.png</a></span> now</div>', "the words around it stay")
        self.assertIn('class="file-link">the shot</a>', self.out["link"], "a link keeps its text")
        self.assertRegex(self.out["listtail"], r'<li><span class="path-img".*</span>: the first shot</li>', "the words after it stay in the item")
        jpeg = thumbs(self.out["jpeg"])
        self.assertEqual([t["alt"] for t in jpeg], ["b.JPEG", "c.webp"])

    def test_any_other_path_is_its_link_alone(self):
        for key in ("notimage", "line"):
            self.assertNotIn("path-img", self.out[key], key)
            self.assertNotIn("<img", self.out[key], key)
        self.assertIn('<a href="/fileview?path=C%3A%5Cx%5Cnotes.md&room=room-aaaa0001&cwd=C%3A%5Ct" target="_blank" rel="noopener" class="file-link">', self.out["notimage"])
        self.assertIn("&line=12", self.out["line"], "a picture named with a line is a place in a file")

    def test_the_ceos_balloon(self):
        t = thumbs(self.out["real"])
        base = "C:\\Trading\\dev\\projects\\opTen-microservices\\evidence\\task-59\\"
        self.assertEqual([x["path"] for x in t], [base + n for n in (
            "1-nothing-selected.png", "2-exp-2-oct-fly.png", "3-exp-29-sep.png", "4-one-account-simulated-2-oct.png", "6-exp-25-sep-all-accounts.png")])
        enc = "C%3A%5CTrading%5Cdev%5Cprojects%5CopTen-microservices%5Cevidence%5Ctask-59%5C"
        self.assertTrue(all(x["src"].startswith("/api/file?path=" + enc) for x in t), t)
        self.assertTrue(all(x["href"].startswith("/fileview?path=" + enc) for x in t), t)
        self.assertEqual([x["alt"] for x in t][1], "2-exp-2-oct-fly.png")
        self.assertIn('<code class="ic">...\\evidence\\task-59\\2-exp-2-oct-fly.png</code></a></span>: the 2 Oct butterfly with the full strike window</li>',
                      self.out["real"], "the abbreviation reads as written, its words after it")
        self.assertEqual(self.out["real"].count("<li>"), 5)

    def test_an_abbreviation_with_no_full_path_before_it_stays_text(self):
        h = self.out["alone"]
        self.assertNotIn("<a ", h, h)
        self.assertIn('<code class="ic">...\\evidence\\x.png</code>', h)
        self.assertIn(" and ...\\evidence\\y.png and ", h)
        self.assertIn('<code class="ic">\u2026/evidence/z.png</code>', h)
        self.assertEqual(len(thumbs(self.out["after"])), 1, "a full path after it does not count")
        self.assertIn('<code class="ic">...\\shots\\x.png</code> then', self.out["after"])
        self.assertNotIn("<a ", self.out["nofolder"].split("</span>")[1], "an abbreviation with no folder names nothing")

    def test_an_abbreviation_opens_the_nearest_full_path_holding_its_first_folder(self):
        t = thumbs(self.out["nearest"])
        self.assertEqual([x["path"] for x in t], ["C:\\one\\shots\\a.png", "C:\\two\\shots\\b.png", "C:\\two\\shots\\c.png",
                                                   "C:\\three\\other\\d.png", "C:\\two\\shots\\e.png"])
        self.assertEqual([x["path"] for x in thumbs(self.out["deepest"])], ["C:\\src\\lib\\src\\a.png", "C:\\src\\lib\\src\\b.png"])
        u = thumbs(self.out["unix"])
        self.assertEqual([x["path"] for x in u], ["/home/f/shots/a.png", "/home/f/shots/b.png"])
        self.assertIn('href="/fileview?path=%2Fhome%2Ff%2Fshots%2Fc.md&room=room-aaaa0001&cwd=C%3A%5Ct" target="_blank" rel="noopener" class="file-link">.../shots/c.md</a>', self.out["unix"])
        self.assertEqual([x["path"] for x in thumbs(self.out["folder"])], ["C:\\x\\shots\\a.png"], "a folder written in full counts")
        self.assertEqual([x["path"] for x in thumbs(self.out["dirbase"])], ["C:\\x\\shots\\a.png", "C:\\x\\shots\\deep\\b.png"])
        self.assertEqual([x["path"] for x in thumbs(self.out["textabbrev"])], ["C:\\x\\shots\\a.png", "C:\\x\\shots\\b.png"], "in running text too")
        self.assertIn('class="file-link">...\\shots\\b.png</a></span> here', self.out["textabbrev"])

    def test_an_item_and_a_quote_see_the_whole_message(self):
        self.assertEqual([x["path"] for x in thumbs(self.out["points"])], ["C:\\x\\ev\\a.png", "C:\\x\\ev\\b.png"])
        self.assertIn('<ol class="pt-items">', self.out["points"])
        self.assertIn("</span> please</div></li>", self.out["points"])
        self.assertEqual([x["path"] for x in thumbs(self.out["quote"])], ["C:\\x\\ev\\a.png", "C:\\x\\ev\\b.png"])
        self.assertIn('&gt; <span class="path-img"><a class="att-thumb" href="/api/file?path=C%3A%5Cx%5Cev%5Cb.png', self.out["quote"], "in a quoted block too")

    def test_a_picture_the_hub_has_not_got_is_its_link_titled_so(self):
        self.assertEqual(len(thumbs(self.out["gone"])), 1, "drawn as a thumbnail first")
        self.assertEqual(self.out["goneDom"], {"title": "not found on the hub", "removed": True})
        self.assertNotIn("<img", self.out["goneAfter"])
        self.assertIn('class="file-link" title="not found on the hub"><code class="ic">C:\\x\\ev\\gone.png</code></a>', self.out["goneAfter"])
        self.assertEqual(len(thumbs(self.out["goneOther"])), 1, "only that path")

    def test_each_occurrence_stands_for_what_was_written_before_it(self):
        self.assertEqual([x["path"] for x in thumbs(self.out["twice"])],
                         ["C:\\one\\shots\\a.png", "C:\\one\\shots\\x.png", "C:\\two\\shots\\a.png", "C:\\two\\shots\\x.png"])
        h = self.out["firstbare"]
        self.assertEqual([x["path"] for x in thumbs(h)], ["C:\\one\\shots\\a.png", "C:\\one\\shots\\x.png"])
        self.assertTrue(h.startswith('<div class="ln"><code class="ic">...\\shots\\x.png</code> then'), "the first occurrence stays text")
        self.assertEqual([x["path"] for x in thumbs(self.out["kinds"])], ["C:\\one\\s\\a.png", "C:\\one\\s\\x.png", "C:\\two\\s\\a.png", "C:\\two\\s\\x.png"],
                         "in running text and in code, each in its own order")
        h = self.out["pointstwice"]
        self.assertEqual([x["path"] for x in thumbs(h)], ["C:\\x\\ev\\a.png", "C:\\x\\ev\\b.png"])
        self.assertIn('<li class="pt-item"><div class="ln"><code class="ic">...\\ev\\b.png</code></div></li>', h, "the first item stays text")
        self.assertEqual([x["path"] for x in thumbs(self.out["fenced"])], ["C:\\one\\shots\\a.png", "C:\\one\\shots\\x.png"],
                         "a fenced block neither gives nor takes a path")

    def test_a_full_path_then_an_abbreviation_on_one_line_of_running_text(self):
        self.assertEqual([x["path"] for x in thumbs(self.out["sameline"])], ["C:\\one\\shots\\a.png", "C:\\one\\shots\\x.png"])
        self.assertRegex(self.out["sameline"], r'a\.png</a></span> then <span class="path-img"')
        self.assertEqual([x["path"] for x in thumbs(self.out["spaces"])], ["D:\\work\\Ensemble Dashboard\\shots\\a.png", "D:\\work\\Ensemble Dashboard\\shots\\x.png"],
                         "a name with spaces is still one name")

    def test_a_compact_table_row_is_read_cell_by_cell(self):
        h = self.out["cells"]
        self.assertEqual([x["path"] for x in thumbs(h)],
                         ["C:\\one\\shots\\a.png", "C:\\one\\shots\\x.png", "C:\\one\\shots\\x.png", "C:\\two\\shots\\a.png", "C:\\two\\shots\\x.png"])
        self.assertEqual((h.count("<th>"), h.count("<td>")), (2, 2), h)
        h = self.out["cellbase"]
        self.assertEqual([x["path"] for x in thumbs(h)], ["C:\\one\\shots\\a.png", "C:\\one\\shots\\x.png", "C:\\one\\shots\\z.png"],
                         "a full path in a cell is a base; a cell beyond the head row is not drawn, so it neither gives nor takes")
        self.assertIn("<td>second</td>", h)
        self.assertNotIn("third", h)

    def test_a_file_url_written_in_full_counts(self):
        for key in ("fileurlcode", "fileurllink"):
            self.assertEqual([x["path"] for x in thumbs(self.out[key])], ["C:/one/shots/a.png", "C:/one/shots/x.png"], key)

    def test_every_picture_kind_by_its_bare_name(self):
        self.assertEqual([x["alt"] for x in thumbs(self.out["barecode"])], ["shot.webp", "shot.bmp", "shot.PNG", "shot.JPG", "shot.svg"])
        self.assertEqual([x["alt"] for x in thumbs(self.out["baretext"])], ["shot.webp", "shot.bmp", "shot.PNG", "shot.gif", "shot.jpeg"])

    def test_a_picture_the_hub_has_not_got_is_gone_for_that_task_only(self):
        self.assertEqual(len(thumbs(self.out["relA"])), 1)
        self.assertEqual(self.out["relADom"], {"title": "not found on the hub", "removed": True})
        self.assertNotIn("<img", self.out["relAAfter"])
        self.assertIn('title="not found on the hub"', self.out["relAAfter"])
        t = thumbs(self.out["relB"])
        self.assertEqual(len(t), 1, "the same name in another task is asked for again")
        self.assertIn("room=room-bbbb0002&cwd=C%3A%5Cu", t[0]["src"])

    def test_a_pasted_screenshot_is_as_it_was(self):
        h = self.out["image"]
        self.assertIn('<div class="att-thumbs"><a class="att-thumb" href="/api/room/attachment?room=room-aaaa0001&amp;name=shot.png"', h)
        self.assertNotIn("path-img", h)
        self.assertNotIn("/api/file", h)


class ThreePages(unittest.TestCase):
    def test_every_page_reads_the_whole_message_first_and_styles_the_thumbnail(self):
        for name, src in PAGES.items():
            i = src.index("function mdToHtml(src) {")
            self.assertIn("if (!PATH_ABBR) return withPathAbbrevs(src, () => mdToHtml(src));", src[i:i + 300], name)
            self.assertEqual(src.count("function mdToHtml("), 1, name)
            self.assertRegex(src, r"\.path-img > \.att-thumb \{ width: ?fit-content; margin: ?var\(--s-100\) 0; \}", name)
            self.assertRegex(src, r"\.att-thumb img \{ display: ?block; max-height: ?1[26]0px; max-width: ?100%; \}", name)
            self.assertNotIn("#", re.search(r"\.path-img > \.att-thumb \{[^}]*\}", src).group(0), name)

    def test_the_points_of_a_balloon_are_drawn_in_its_context(self):
        self.assertIn("withPathAbbrevs(m.text, () => itemsHtml(it, bars, md, headBar))", js_function("pointItemsHtml"))
        self.assertIn("let h = inCtx() ? null : MD_CACHE.get(t); if (h == null) h = mdToHtml(t); if (!inCtx()) used.set(t, h);", SRC,
                      "an item of a message with abbreviations is drawn in its context, never from or into the cache")
        self.assertIn("+ '|' + THUMB_GONE.size;", SRC, "a picture gone from the hub redraws as its link")


if __name__ == "__main__":
    unittest.main()
