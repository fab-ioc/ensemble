"""The file viewer makes files pleasant to read.

fileview.html highlights code with its own highlighter (no CDN: the hub is read
over a tailnet that may have no route out) and renders Markdown. These checks
run the page's own rendering code in Node, the way tests/test_links.py runs the
shared link block, and are skipped without Node:

* a Python file comes out as numbered rows with keywords, strings, comments and
  numbers coloured, and the rows' text is the file, byte for byte, so comments,
  copies and line marks see exactly what is on disk;
* every language the viewer names gets tokens, and none of them changes the text;
* a Markdown document renders headings with anchors, joined paragraphs, nested
  and task lists, tables, images, links through the viewer, and fenced code
  highlighted in its own language;
* one-line JSON is laid out without touching a number JavaScript cannot hold;
* a 500 KB file is highlighted in well under the time a reader would notice.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "fileview.html").read_text(encoding="utf-8")
# The highlighter the page loads from /static/hl.js, shared with index.html.
HL = (ROOT / "static" / "hl.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def render_code() -> str:
    """The page's script from its first line to the comment layer: the link
    block, the highlighter, the JSON and CSV helpers and mdToHtml."""
    start = PAGE.index("const esc = s =>")
    end = PAGE.index("// ---- In-place review comments")
    return HL + PAGE[start:end]


JS = r"""
globalThis.location = new URL('http://127.0.0.1:8765/fileview?path=' + encodeURIComponent('C:\\p\\docs\\README.md'));
%s
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {};
for (const c of cases) {
  const t0 = Date.now();
  if (c.kind === 'rows') { const r = HL.rows(c.text, c.lang); out[c.name] = { html: r.html, lines: r.lines, ms: Date.now() - t0 }; }
  else if (c.kind === 'md') out[c.name] = { html: mdToHtml(c.text), ms: Date.now() - t0 };
  else if (c.kind === 'pretty') out[c.name] = { text: jsonLayout(c.text, false) };
  else if (c.kind === 'lang') out[c.name] = { lang: HL.langOf(c.text, c.body || '') };
  else if (c.kind === 'slow') {
    // Built here: a 50,000-character line would bloat the JSON on stdin.
    const text = c.head + c.unit.repeat(c.n) + c.tail;
    const t1 = Date.now();
    const r = c.lang ? HL.rows(text, c.lang).html : mdToHtml(text);
    out[c.name] = { ms: Date.now() - t1, whole: !c.lang || r.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&') === text };
  }
}
console.log(JSON.stringify(out));
"""

PYTHON = '''import os

MAX = 0x1F  # the limit


@dataclass
class Backup(Base):
    """Keep a copy."""

    def run(self, path: str = "C:\\\\x") -> bool:
        return len(path) > 3.5e2 and True
'''

MARKDOWN = '''---
name: sample
---
# Workspace *files*

A paragraph that the author
wrapped at a narrow width, with **bold text that
spans lines** and `backup.py:12`.

## Steps

1. First
   - nested one
   - [x] done
2. Second

| Language | Colour |
|---|:-:|
| Python | yes |

> A quote
> over two lines.

![screenshot](img/shot.png)

```python
def hello():
    return "hi"  # greet
```
'''

SAMPLES = {
    "javascript": "const re = /a\\/b[/]/g; let x = a / b / c; // note\nfunction go(n) { return `t ${n}`; }\n",
    "typescript": "export interface Task { id: string }\ntype Id = `room-${string}`;\n",
    "html": '<!doctype html>\n<p class="a">Hi &amp; bye</p>\n<script>\nconst n = 1; // x\n</script>\n<style>.a { color: red; }</style>\n',
    "css": ":root { --accent: #0C66E4; }\n.card:hover > .t { padding: 4px 8px !important; }\n",
    "json": '{"a": 1, "b": [true, null, "s"]}\n',
    "yaml": "hub:\n  port: 8765  # comment\n  bind: \"tailscale\"\nlist:\n  - true\n",
    "toml": "[project]\nname = \"ensemble\"\nversion = 1\n",
    "bash": "#!/usr/bin/env bash\nset -euo pipefail\nfor f in *.plist; do echo \"$f\"; done\n",
    "powershell": "param([int]$Port = 8765)\nif ($Port -gt 0) { Write-Host 'hi' } # c\n",
    "java": "public final class Hello {\n  @Override public String toString() { return \"x\"; }\n}\n",
    "markdown": "# Title\n\n- item `code`\n\n```py\nx = 1\n```\n",
    "diff": "diff --git a/x b/x\n@@ -1,2 +1,2 @@\n-old\n+new\n same\n",
    "log": "2026-09-13 10:00:00,123 ERROR dashboard: boom\nOSError: bad\n",
    "sql": "SELECT id FROM tasks WHERE n > 2; -- c\n",
}


ESCAPES = r'''\[safe](https://example.com)

\![alt](img.png)

\`code\`

`C:\dir\` and \*not em\*

\\[x](https://example.com)

\\![alt](img.png)

\\`y`

\\\[z](https://example.org)
'''

# Lines that make a careless pattern quadratic: long runs of blanks between a
# key and its value, before a heading's end, and openers that never close.
# Before review 1, one line of 50,000 blanks took YAML over 16 seconds.
SLOW_LINES = {
    "blanks_after_colon": {"head": "a:", "unit": " ", "tail": "123\n"},
    "blanks_before_colon": {"head": "a", "unit": " ", "tail": ": 1\n"},
    "indent": {"head": "", "unit": " ", "tail": "- a: 1\n"},
    "blanks_after_dash": {"head": "-", "unit": " ", "tail": "true\n"},
    "blanks_after_equals": {"head": "a =", "unit": "\t", "tail": "true\n"},
    "blanks_after_bracket": {"head": "[", "unit": " ", "tail": "1\n"},
    "blanks_mid_line": {"head": "a", "unit": " ", "tail": "b\nc\n"},
}
SLOW_MARKDOWN = {
    "heading_blanks": {"head": "# a", "unit": " ", "tail": "#b\n"},
    "unclosed_strong": {"head": "", "unit": "**a ", "tail": "\n"},
    "unclosed_del": {"head": "", "unit": "~~a ", "tail": "\n"},
    "unclosed_image": {"head": "", "unit": "![a", "tail": "\n"},
}


def text_of(rows_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", rows_html))


@unittest.skipUnless(NODE, "node is not installed")
class FileViewRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        big = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        big = (big * 3)[: 500 * 1024]
        cases = [
            {"kind": "rows", "name": "python", "lang": "python", "text": PYTHON},
            {"kind": "md", "name": "markdown", "text": MARKDOWN},
            {"kind": "rows", "name": "big", "lang": "python", "text": big},
            {"kind": "pretty", "name": "json", "text": '{"n": 12345678901234567890, "s": "a,b:{c}", "e": [], "o": {"k": [1, 2]}, "pad": "' + "x" * 200 + '"}'},
            {"kind": "lang", "name": "lang_py", "text": "backup.py"},
            {"kind": "lang", "name": "lang_dockerfile", "text": "Dockerfile"},
            {"kind": "lang", "name": "lang_shebang", "text": "run", "body": "#!/usr/bin/env python3\nprint(1)\n"},
            {"kind": "md", "name": "escapes", "text": ESCAPES},
        ] + [{"kind": "rows", "name": "lang:" + k, "lang": k, "text": v} for k, v in SAMPLES.items()] + [
            {"kind": "slow", "name": f"slow:{lang}:{name}", "lang": lang, "n": 50_000, **shape}
            for lang in ("yaml", "toml", "ini", "markdown", "python", "javascript", "css", "bash")
            for name, shape in SLOW_LINES.items()
        ] + [
            {"kind": "slow", "name": f"slow:md:{name}", "lang": "", "n": 50_000 // len(shape["unit"]), **shape}
            for name, shape in {**SLOW_LINES, **SLOW_MARKDOWN}.items()
        ]
        cls.big = big
        cls.cases = {c["name"]: c for c in cases}
        # The script is too long for a Windows command line: run it from a file.
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "render.cjs"
            script.write_text(JS % render_code(), encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], input=json.dumps(cases),
                                  capture_output=True, text=True, encoding="utf-8", timeout=120)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.out = json.loads(proc.stdout)

    def test_python_is_numbered_rows_with_colour(self):
        r = self.out["python"]
        h = r["html"]
        self.assertEqual(r["lines"], PYTHON.count("\n"))
        self.assertIn('<div class="ln-row" data-n="1"><span class="tk-kw">import</span> os\n</div>', h)
        for token in ('<span class="tk-kw">def</span> <span class="tk-fn">run</span>',
                      '<span class="tk-kw">class</span> <span class="tk-ty">Backup</span>',
                      '<span class="tk-str">&quot;&quot;&quot;Keep a copy.&quot;&quot;&quot;</span>'.replace("&quot;", '"'),
                      '<span class="tk-com"># the limit</span>',
                      '<span class="tk-num">0x1F</span>',
                      '<span class="tk-num">MAX</span>',
                      '<span class="tk-meta">@dataclass</span>',
                      '<span class="tk-bi">self</span>',
                      '<span class="tk-num">True</span>'):
            self.assertIn(token, h)
        self.assertEqual(text_of(h), PYTHON, "the rows must hold the file's text exactly")

    def test_every_language_gets_tokens_and_keeps_its_text(self):
        for lang, src in SAMPLES.items():
            with self.subTest(lang=lang):
                h = self.out["lang:" + lang]["html"]
                self.assertRegex(h, r'class="tk-', f"{lang}: nothing was highlighted")
                self.assertEqual(text_of(h), src)
        js = self.out["lang:javascript"]["html"]
        self.assertIn('<span class="tk-str">/a\\/b[/]/g</span>', js, "a regex literal")
        self.assertNotIn('<span class="tk-str">/ b /</span>', js, "a division is not a regex")
        page = self.out["lang:html"]["html"]
        self.assertIn('<span class="tk-kw">const</span>', page, "a page's script is JavaScript")
        self.assertIn('<span class="tk-attr">color</span>', page, "a page's style is CSS")
        diff = self.out["lang:diff"]["html"]
        self.assertIn('class="ln-row r-ins"', diff)
        self.assertIn('class="ln-row r-del"', diff)
        self.assertIn('class="ln-row r-hunk"', diff)
        self.assertIn('<span class="tk-err">ERROR</span>', self.out["lang:log"]["html"])

    def test_markdown_renders_as_a_document(self):
        h = self.out["markdown"]["html"]
        self.assertIn('<pre class="cb fm" data-lang="front matter">', h)
        self.assertIn('<h1 id="workspace-files">Workspace <em>files</em></h1>', h)
        self.assertIn('<h2 id="steps">', h)
        self.assertRegex(h, r"<p>A paragraph that the author\nwrapped at a narrow width, with <strong>bold text that\nspans lines</strong>")
        self.assertRegex(h, r'<a href="/fileview\?path=C%3A%5Cp%5Cdocs%5Cbackup\.py&amp;line=12|<a href="/fileview\?path=C%3A%5Cp%5Cdocs%5Cbackup\.py&line=12')
        self.assertRegex(h, r"<ol><li>First\n<ul><li>nested one</li><li class=\"task\"><input type=\"checkbox\" disabled checked> done</li></ul></li><li>Second</li></ol>")
        self.assertIn('<th style="text-align:center">Colour</th>', h)
        self.assertIn("<blockquote><p>A quote\nover two lines.</p></blockquote>", h)
        self.assertIn('<img class="md-img" src="/api/file?path=C%3A%5Cp%5Cdocs%5Cimg%5Cshot.png"', h)
        self.assertIn('<pre class="cb" data-lang="python"><code><span class="tk-kw">def</span> <span class="tk-fn">hello</span>', h)
        self.assertIn('<span class="tk-com"># greet</span>', h)

    def test_backslash_escapes_start_no_link_image_or_code(self):
        p = self.out["escapes"]["html"].split("\n")
        self.assertEqual(len(p), 8, p)
        # One backslash: the opener is a character.
        self.assertTrue(p[0].startswith("<p>[safe]("), p[0])
        self.assertNotIn(">safe</a>", p[0], "an escaped [ starts no link")
        # What is left is a link, as on GitHub; a picture's path is its thumbnail with the link under it.
        self.assertTrue(p[1].startswith('<p>!<span class="path-img" data-path="img.png"><a class="att-thumb" href="/api/file?path=C%3A%5Cp%5Cdocs%5Cimg.png"'), p[1])
        self.assertIn('<a href="/fileview?path=C%3A%5Cp%5Cdocs%5Cimg.png"', p[1])
        self.assertNotIn('<img class="md-img"', p[1], "an escaped ! starts no image")
        self.assertEqual(p[2], "<p>`code`</p>")
        self.assertIn(r'<code class="ic">C:\dir\</code>', p[3], "a code span keeps its backslashes")
        self.assertTrue(p[3].endswith(" and *not em*</p>"), p[3])
        # Two backslashes are one, and the opener after them still counts.
        self.assertRegex(p[4], r'^<p>\\<a href="https://example\.com"[^>]*>x</a></p>$')
        self.assertTrue(p[5].startswith('<p>\\<img class="md-img"'), p[5])
        self.assertEqual(p[6], '<p>\\<code class="ic">y</code></p>')
        # Three: a backslash, then an escaped [.
        self.assertTrue(p[7].startswith("<p>\\[z]("), p[7])
        self.assertNotIn(">z</a>", p[7])

    def test_long_lines_are_not_quadratic(self):
        for name, r in self.out.items():
            if not name.startswith("slow:"):
                continue
            with self.subTest(case=name):
                self.assertLess(r["ms"], 800, f"{name} took {r['ms']} ms")
                self.assertTrue(r["whole"], "the rows must hold the line exactly")

    def test_one_line_json_is_laid_out_as_written(self):
        t = self.out["json"]["text"]
        self.assertTrue(t.startswith('{\n  "n": 12345678901234567890,\n  "s": "a,b:{c}",\n  "e": [],\n  "o": {\n    "k": [\n      1,\n      2\n    ]\n  },'), t)
        self.assertEqual(json.loads(t), json.loads(self.cases["json"]["text"]))

    def test_language_by_name(self):
        self.assertEqual(self.out["lang_py"]["lang"], "python")
        self.assertEqual(self.out["lang_dockerfile"]["lang"], "bash")
        self.assertEqual(self.out["lang_shebang"]["lang"], "python")

    def test_a_500_kb_file_is_fast_and_whole(self):
        r = self.out["big"]
        self.assertEqual(text_of(r["html"]), self.big)
        self.assertEqual(r["lines"], self.big.count("\n") + (0 if self.big.endswith("\n") else 1))
        self.assertIn('class="ln-chunk"', r["html"])
        self.assertLess(r["ms"], 1500, f"highlighting 500 KB took {r['ms']} ms")


if __name__ == "__main__":
    unittest.main()
