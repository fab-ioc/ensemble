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
const link = { title: '' }, thumb = { removed: false, remove() { this.removed = true; } };
const wrap = { dataset: { path: 'C:\\\\x\\\\ev\\\\gone.png' }, querySelector: sel => sel === 'a.file-link' ? link : sel === 'a.att-thumb' ? thumb : null };
pathThumbGone({ closest: sel => sel === '.path-img' ? wrap : null });
out.goneDom = { title: link.title, removed: thumb.removed };
out.goneAfter = mdToHtml(cases.gone);
out.goneOther = mdToHtml('\`C:\\\\x\\\\ev\\\\a.png\`');
globalThis.cases = cases; globalThis.out = out;
`, ctx);
console.log(JSON.stringify(ctx.out));
"""

THUMB = re.compile(r'<span class="path-img" data-path="([^"]*)"><a class="att-thumb" href="([^"]*)" target="_blank" rel="noopener" title="([^"]*)">'
                   r'<img src="([^"]*)" alt="([^"]*)" loading="lazy" onerror="pathThumbGone\(this\)"></a><a href="([^"]*)" target="_blank" rel="noopener" class="file-link"[^>]*>')


def thumbs(html: str) -> list[dict]:
    return [{"path": m[0], "src": m[3], "alt": m[4], "href": m[5], "title": m[2]} for m in THUMB.findall(html)]


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
        self.assertIn('&gt; <span class="path-img" data-path="C:\\x\\ev\\b.png">', self.out["quote"], "in a quoted block too")

    def test_a_picture_the_hub_has_not_got_is_its_link_titled_so(self):
        self.assertEqual(len(thumbs(self.out["gone"])), 1, "drawn as a thumbnail first")
        self.assertEqual(self.out["goneDom"], {"title": "not found on the hub", "removed": True})
        self.assertNotIn("<img", self.out["goneAfter"])
        self.assertIn('class="file-link" title="not found on the hub"><code class="ic">C:\\x\\ev\\gone.png</code></a>', self.out["goneAfter"])
        self.assertEqual(len(thumbs(self.out["goneOther"])), 1, "only that path")

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
        self.assertIn("withPathAbbrevs(m.text, () => itemsHtml(it, bars, md))", js_function("pointItemsHtml"))
        self.assertIn("const mdKey = t => PATH_ABBR && PATH_ABBR.size ? t + '\\u0000' + [...PATH_ABBR].join('\\u0000') : t;", SRC,
                      "an item with a resolved path is kept with its paths, not by its words alone")
        self.assertIn("+ '|' + THUMB_GONE.size;", SRC, "a picture gone from the hub redraws as its link")


if __name__ == "__main__":
    unittest.main()
