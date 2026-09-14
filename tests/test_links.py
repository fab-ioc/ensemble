"""Links and clickable things in the dashboard pages.

index.html, session.html and fileview.html are used from the hub machine and
from other computers on the tailnet. These checks are cheap and need no hub:

* the link code the three pages share is word for word the same in all three;
* what that code makes of an agent's text never links to a loopback address or
  a file:// URL, which a browser on another computer cannot open, and the hard
  cases (a space in a Windows path, a full stop after a URL, a line number, a
  task id) come out as links that open;
* no markup or script hard-codes a loopback or file:// link;
* every element the stylesheet styles as clickable (cursor: pointer) is wired
  to something in the page's script.

The linkify checks run the shared block in Node and are skipped without it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
PAGES = {n: (ROOT / n).read_text(encoding="utf-8") for n in ("index.html", "session.html", "fileview.html")}
BEGIN = "// ---- Links in rendered text: begin shared block"
END = "// ---- Links in rendered text: end shared block"

LOOPBACK_OR_FILE = re.compile(r"^(?:https?://(?:127\.\d+\.\d+\.\d+|localhost|\[::1\])(?:[:/]|$)|file:)", re.I)


def shared_block(src: str) -> str:
    i = src.index(BEGIN)
    j = src.index("\n", src.index(END, i)) + 1
    return src[i:j].replace("\r\n", "\n")


def hrefs(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]*)"', html)]


def viewer_path(href: str) -> tuple[str, str]:
    """The path= and line= of a /fileview link."""
    q = parse_qs(urlparse(href).query)
    return q.get("path", [""])[0], q.get("line", [""])[0]


class SharedBlock(unittest.TestCase):
    def test_the_three_copies_match(self):
        blocks = {n: shared_block(s) for n, s in PAGES.items()}
        self.assertEqual(blocks["session.html"], blocks["index.html"], "session.html's link block drifted")
        self.assertEqual(blocks["fileview.html"], blocks["index.html"], "fileview.html's link block drifted")


NODE = shutil.which("node")

JS = r"""
globalThis.location = new URL(process.argv[1]);
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
%s
const render = t => {
  if (t.length > 1 && t.startsWith('`') && t.endsWith('`')) return codeSpanHtml(t.slice(1, -1));
  const keep = [];
  return unlinkify(linkify(esc(t), keep), keep);
};
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(Object.fromEntries(cases.map(c => [c, render(c)]))));
"""

CASES = [
    "Open http://127.0.0.1:8765/fileview?path=C%3A%5Cx.md now",
    "the hub is at http://localhost:8765/ today",
    "`http://127.0.0.1:8765/session?id=room-35d21def`",
    "dev server http://127.0.0.1:8797/ is up",
    "`http://127.0.0.1:8797/`",
    "see [plan](file:///C:/Users/ceo/plan.md)",
    "see file:///C:/Users/ceo/plan.md",
    "Docs at https://example.com/a/b.",
    "(see https://example.com/x)",
    "Wrote C:\\Users\\ceo\\Ensemble Dashboard\\task\\LINKS-REVIEW.md for you",
    "`dashboard.py:4620`",
    "and index.html:88 too",
    "Task room-35d21def is done",
    "open [the viewer](/fileview?path=C%3A%5Cx.md)",
    "see [below](#setup)",
    "`C:\\Users\\ceo\\Ensemble Dashboard\\task\\`",
    "[the preview](http://127.0.0.1:8797/)",
    # 17-: the review's adversarial cases
    "Wrote C:\\Users\\ceo\\Ensemble Dashboard\\My File.md today",
    "C:\\foo is copied to docs\\readme.md",
    "see C:\\Users\\me\\a&b\\x.md",
    "[paren](https://en.wikipedia.org/wiki/Foo_(bar))",
    "x \ue0020\ue003 y",
    "see https://a.example/ then \ue0020\ue003",
    "C:\\Users\\ceo\\New folder\\notes.md is there",
    "Saved to C:\\temp\\a.md and C:\\temp\\b.md",
    "[doc](C:\\Users\\ceo\\Ensemble Dashboard\\a (1).md)",
    # 26-: review 2 — names with little words in them, and prose after a drive
    "C:\\Users\\ceo\\Terms and Conditions.md",
    "C:\\Books\\War of the Worlds.md is here",
    "Meme: C:\\x\\This is Fine.md",
    "C:\\foo please check docs\\readme.md",
    "C:\\x\\my notes.md here",
    "C:\\Foo is copied To the docs\\readme.md",
    "Use C:\\temp for scratch and docs\\b.md for notes",
]


@unittest.skipUnless(NODE, "node is not installed")
class GeneratedLinks(unittest.TestCase):
    def render(self, location: str) -> dict[str, str]:
        prog = JS % shared_block(PAGES["index.html"])
        out = subprocess.run([NODE, "-e", prog, location], input=json.dumps(CASES), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_from_another_computer(self):
        r = self.render("http://hub-host:8765/")
        for text, html in r.items():
            for h in hrefs(html):
                self.assertIsNone(LOOPBACK_OR_FILE.match(h), f"{text!r} links to {h}")
        self.assertEqual(hrefs(r[CASES[0]]), ["http://hub-host:8765/fileview?path=C%3A%5Cx.md"])
        self.assertEqual(hrefs(r[CASES[1]]), ["http://hub-host:8765/"])
        self.assertEqual(hrefs(r[CASES[2]]), ["http://hub-host:8765/session?id=room-35d21def"])
        for c in (CASES[3], CASES[4], CASES[16]):          # another port on the hub: said, not linked
            self.assertEqual(hrefs(r[c]), [], c)
            self.assertIn("only on the hub machine", r[c])
        for c in (CASES[5], CASES[6]):
            self.assertEqual(viewer_path(hrefs(r[c])[0])[0], "C:/Users/ceo/plan.md", c)
        self.assertEqual(hrefs(r[CASES[7]]), ["https://example.com/a/b"])
        self.assertEqual(hrefs(r[CASES[8]]), ["https://example.com/x"])
        self.assertEqual(viewer_path(hrefs(r[CASES[9]])[0])[0],
                         "C:\\Users\\ceo\\Ensemble Dashboard\\task\\LINKS-REVIEW.md")
        self.assertEqual(viewer_path(hrefs(r[CASES[10]])[0]), ("dashboard.py", "4620"))
        self.assertEqual(viewer_path(hrefs(r[CASES[11]])[0]), ("index.html", "88"))
        self.assertEqual(hrefs(r[CASES[12]]), ["/?task=room-35d21def"])
        self.assertIn('data-task="room-35d21def"', r[CASES[12]])
        self.assertEqual(hrefs(r[CASES[13]]), ["/fileview?path=C%3A%5Cx.md"])
        self.assertEqual(hrefs(r[CASES[14]]), ["#setup"])
        self.assertNotIn("_blank", r[CASES[14]], "an in-page anchor must not open a new tab")
        self.assertEqual(viewer_path(hrefs(r[CASES[15]])[0])[0], "C:\\Users\\ceo\\Ensemble Dashboard\\task\\")

    def test_paths_and_markers_in_running_text(self):
        r = self.render("http://hub-host:8765/")
        paths = lambda c: [viewer_path(h)[0] for h in hrefs(r[c])]
        # Spaces in the file name as well as the folders.
        self.assertEqual(paths(CASES[17]), ["C:\\Users\\ceo\\Ensemble Dashboard\\My File.md"])
        self.assertEqual(paths(CASES[23]), ["C:\\Users\\ceo\\New folder\\notes.md"])
        self.assertEqual(paths(CASES[24]), ["C:\\temp\\a.md", "C:\\temp\\b.md"])
        # Names with little words in them, linked whole.
        self.assertEqual(paths(CASES[26]), ["C:\\Users\\ceo\\Terms and Conditions.md"])
        self.assertEqual(paths(CASES[27]), ["C:\\Books\\War of the Worlds.md"])
        self.assertEqual(paths(CASES[28]), ["C:\\x\\This is Fine.md"])
        # A sentence after a drive is not a path, and no piece of it is linked
        # on its own either: "notes.md" or "docs\readme.md" alone would open
        # some other file.
        for c in (CASES[18], CASES[29], CASES[30], CASES[31], CASES[32]):
            self.assertEqual(hrefs(r[c]), [], c)
            self.assertEqual(r[c], c.replace("&", "&amp;"), c)
        # An & in a path, which arrives escaped.
        self.assertEqual(paths(CASES[19]), ["C:\\Users\\me\\a&b\\x.md"])
        # Brackets in a markdown target, a URL's and a path's.
        self.assertEqual(hrefs(r[CASES[20]]), ["https://en.wikipedia.org/wiki/Foo_(bar)"])
        self.assertTrue(r[CASES[20]].endswith(">paren</a>"), r[CASES[20]])
        self.assertEqual(paths(CASES[25]), ["C:\\Users\\ceo\\Ensemble Dashboard\\a (1).md"])
        # The text's own marker characters come back as they were, and never
        # stand in for a link.
        self.assertEqual(r[CASES[21]], CASES[21])
        self.assertEqual(hrefs(r[CASES[22]]), ["https://a.example/"])
        self.assertTrue(r[CASES[22]].endswith(" then \ue0020\ue003"), r[CASES[22]])

    def test_on_the_hub_machine(self):
        r = self.render("http://127.0.0.1:8765/")
        self.assertEqual(hrefs(r[CASES[0]]), ["http://127.0.0.1:8765/fileview?path=C%3A%5Cx.md"])
        # Another loopback port does open here, on the machine it runs on.
        self.assertEqual(hrefs(r[CASES[3]]), ["http://127.0.0.1:8797/"])
        for text, html in r.items():
            for h in hrefs(html):
                self.assertFalse(h.lower().startswith("file:"), f"{text!r} links to {h}")


LINEAR_JS = r"""
globalThis.location = new URL('http://hub-host:8765/');
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
%s
const render = t => { const keep = []; return unlinkify(linkify(esc(t), keep), keep); };
const { shapes, cases } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ms = {};
for (const [unit, n] of shapes) {
  // Built here: a 50,000-character line would bloat the JSON on stdin.
  const text = unit.repeat(n);
  const t0 = Date.now();
  render(text);
  ms[unit] = Date.now() - t0;
}
console.log(JSON.stringify({ ms, out: Object.fromEntries(cases.map(c => [c, render(c)])) }));
"""

# 50,000 characters of openers that never close. The expression is tried from
# every character, so any part of it that scans on past the next opener makes
# the whole line quadratic: before this test "[" took 0.9 to 1.8 s and "(/"
# 2.5 s, on every render of a chat bubble or a Markdown file.
LINEAR_SHAPES = ["[", "[a", " [", "[a](b ", "(/", "(~/", "_a/", "_/", "*(/", "a_/", "&(/",
                 "(C:\\a", "_\\\\a", " C:\\a b", "x(y/", "._a/", "room-", " http://"]


@unittest.skipUnless(NODE, "node is not installed")
class LinearLinks(unittest.TestCase):
    def test_a_long_line_of_openers_is_linear_in_all_three_pages(self):
        cases = ["see (/home/me/notes.md) and (docs/a.md)", "b.py_x.md: done", "[a [b](https://example.com/b)"]
        for name, src in PAGES.items():
            with self.subTest(page=name):
                shapes = [[u, 50_000 // len(u)] for u in LINEAR_SHAPES]
                with tempfile.TemporaryDirectory() as tmp:
                    script = Path(tmp) / "linear.cjs"
                    script.write_text(LINEAR_JS % shared_block(src), encoding="utf-8")
                    proc = subprocess.run([NODE, str(script)], input=json.dumps({"shapes": shapes, "cases": cases}),
                                          capture_output=True, text=True, encoding="utf-8", timeout=120)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                r = json.loads(proc.stdout)
                for unit, ms in r["ms"].items():
                    self.assertLess(ms, 250, f"{name}: a 50,000-character line of {unit!r} took {ms} ms")
                out = r["out"]
                # Where a path can start is narrower, and no piece of a name is linked.
                self.assertEqual([viewer_path(h)[0] for h in hrefs(out[cases[0]])], ["/home/me/notes.md", "docs/a.md"])
                self.assertEqual(hrefs(out[cases[1]]), [], "x.md alone would open another file")
                self.assertEqual(hrefs(out[cases[2]]), ["https://example.com/b"])
                self.assertTrue(out[cases[2]].startswith("[a <a "), out[cases[2]])


class NoHardCodedLoopback(unittest.TestCase):
    PATTERN = re.compile(
        r"""(?:href|src|action)\s*=\s*["'`]\s*(?:https?://(?:127\.0\.0\.1|localhost)|file:)"""
        r"""|window\.open\(\s*["'`](?:https?://(?:127\.0\.0\.1|localhost)|file:)""", re.I)

    def test_pages(self):
        for name, src in PAGES.items():
            m = self.PATTERN.search(src)
            self.assertIsNone(m, f"{name}: hard-coded loopback/file link: {m and m.group(0)}")


# ---- Clickable-looking elements are wired ---------------------------------
NATIVE_TAGS = {"a", "label", "summary", "select", "input", "option", "textarea", "details"}
# Containers whose buttons are wired one by one, by their own classes.
ALLOW = {
    "cmt-composer": "its Cancel/Add buttons are wired by .cmt-cancel and .cmt-add",
    "dp-actions": "its buttons carry the action classes wired in the document click handler",
    "actions": "its buttons carry the action classes wired in the document click handler",
    "offer": "its buttons are handled by the offer's own click listener",
    "pref-btn-row": "its buttons are wired by #pref-consolidate and #pref-split",
}


def styles_and_scripts(src: str) -> tuple[str, str]:
    css = "\n".join(re.findall(r"<style>([\s\S]*?)</style>", src))
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", src))
    return re.sub(r"/\*[\s\S]*?\*/", "", css), js


def pointer_selectors(css: str):
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"cursor\s*:\s*pointer", body):
            for s in sel.split(","):
                s = s.strip()
                if s and not s.startswith("@"):
                    yield s


def compound_tokens(selector: str) -> tuple[str, list[str]]:
    """The tag and class/id tokens of the element a selector styles, or of its
    container when the element itself is a bare tag ("button")."""
    s = re.sub(r"::?[\w-]+(?:\([^)]*\))?", "", selector)
    parts = [p for p in re.split(r"\s*[>+~]\s*|\s+", s) if p]
    if not parts:
        return "", []
    tag = (re.match(r"[a-z]+", parts[-1]) or [""])[0]   # the styled element's own tag
    for p in reversed(parts):
        toks = re.findall(r"[.#][\w-]+", p)
        if toks:
            return tag, toks
    return tag, []


def element_attrs(src: str, tok: str) -> list[tuple[str, set[str]]]:
    """(tag, class and id tokens) of every element in the page's markup that
    carries tok — markup in the HTML and in the script's template strings."""
    name = tok[1:]
    out = []
    if tok[0] == ".":
        pat = re.compile(r"<(\w+)\b([^<>]*?)\bclass=\"([^\"]*)\"([^<>]*)>")
        for m in pat.finditer(src):
            classes = m.group(3).split()
            if name in [re.sub(r"\$\{.*", "", c) for c in classes] or re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", m.group(3)):
                rest = m.group(2) + m.group(4)
                ids = re.findall(r"\bid=\"([\w-]+)\"", rest)
                data = re.findall(r"\b(data-[\w-]+)=", rest)
                out.append((m.group(1).lower(), {"." + c for c in re.findall(r"[\w-]+", m.group(3))}
                            | {"#" + i for i in ids} | {"[" + d for d in data}))
        for m in re.finditer(rf"(\w+)\.className\s*=\s*['\"`][^'\"`]*(?<![\w-]){re.escape(name)}(?![\w-])", src):
            out.append(("js:" + m.group(1), {tok}))
    else:
        for m in re.finditer(rf"<(\w+)\b[^<>]*\bid=\"{re.escape(name)}\"[^<>]*>", src):
            cls = re.search(r"\bclass=\"([^\"]*)\"", m.group(0))
            out.append((m.group(1).lower(), {tok} | ({"." + c for c in cls.group(1).split()} if cls else set())))
        for m in re.finditer(rf"(\w+)\.id\s*=\s*['\"]{re.escape(name)}['\"]", src):
            out.append(("js:" + m.group(1), {tok}))
    return out


CLICKISH = re.compile(r"closest\(|\.onclick\b|addEventListener\(\s*['\"](?:click|pointerdown|mousedown)['\"]|\.click\(\)")


def wired(js: str, tokens: set[str], tag: str) -> bool:
    """Whether the script handles a click on the element: a selector string
    naming one of its classes (".x"), its id ("#x", getElementById) or a data
    attribute ("[data-x]"), next to a click handler — closest(), onclick or a
    click listener. A selector used only for dragging, or for finding the
    element to patch it, does not count: a board card once had only drag."""
    pats = []
    for t in tokens:
        name = re.escape(t[1:])
        if t[0] == ".":
            pats.append(rf"['\"`][^'\"`\n]*\.{name}(?![\w-])")
        elif t[0] == "[":
            # Only a selector that is the attribute itself ("button[data-rm]",
            # "[data-po]"): in ".srow[data-sid]" it qualifies another element.
            pats.append(rf"['\"` ](?:[a-z]+)?\[{name}[\]=]")
        else:
            pats += [rf"['\"`][^'\"`\n]*#{name}(?![\w-])", rf"getElementById\(\s*['\"]{name}['\"]\s*\)"]
    for p in pats:
        for m in re.finditer(p, js):
            if CLICKISH.search(js[max(0, m.start() - 80):m.end() + 120]):
                return True
    # const btn = document.getElementById('mode'); ... btn.onclick = ...
    for t in tokens:
        if t[0] != "#":
            continue
        name = re.escape(t[1:])
        for m in re.finditer(rf"\b(\w+)\s*=\s*(?:document\.getElementById\(\s*['\"]{name}['\"]\s*\)|\$\(\s*['\"]#{name}['\"]\s*\))", js):
            var = re.escape(m.group(1))
            if re.search(rf"\b{var}\.(?:onclick\s*=|addEventListener\(\s*['\"](?:click|mousedown|pointerdown))",
                         js[m.end():m.end() + 3000]):
                return True
    if tag.startswith("js:"):                   # created in script, wired on the spot
        var = re.escape(tag[3:])
        if re.search(rf"\b{var}\.(?:onclick\s*=|addEventListener\(\s*['\"](?:click|mousedown|pointerdown))", js):
            return True
    return False


class ClickableHasHandler(unittest.TestCase):
    def test_every_pointer_styled_element_is_wired(self):
        problems = []
        for page, src in PAGES.items():
            css, js = styles_and_scripts(src)
            for sel in pointer_selectors(css):
                tag, toks = compound_tokens(sel)
                if not toks or tag in NATIVE_TAGS or any(t[1:] in ALLOW for t in toks):
                    continue
                # Every rendered element the rule matches — all of the compound's
                # classes — must be wired by one of its own classes, id or data
                # attributes. Never rendered is a dead style: nothing to click.
                elems = [(t, s) for t, s in element_attrs(src, toks[0]) if set(toks) <= s | {toks[0]}]
                for etag, etoks in elems:
                    if etag in NATIVE_TAGS or wired(js, etoks | set(toks), etag):
                        continue
                    problems.append(f"{page}: `{sel}` looks clickable, but no click handler finds "
                                    f"the <{etag}> with {sorted(etoks)}")
                    break
        self.assertEqual(problems, [], "\n".join(problems))


class TokenRedirect(unittest.TestCase):
    """A link shared with ?token= is swapped for a cookie by a redirect, which
    must land on the same URL: a file link's path holds \\, spaces, & and #."""

    def test_redirect_keeps_the_query_intact(self):
        from unittest import mock
        import dashboard

        class H:
            _gate = dashboard.Handler._gate
            _presented_token = dashboard.Handler._presented_token
            _cookie = dashboard.Handler._cookie
            _client_ip = dashboard.Handler._client_ip

            def __init__(self, path):
                self.path, self.command, self.headers = path, "GET", {}
                self.client_address = ("100.100.100.100", 50000)       # a tailnet peer
                self.sent = {}

            def send_response(self, code):
                self.sent["status"] = code

            def send_header(self, k, v):
                self.sent[k] = v

            def end_headers(self):
                pass

        path = "C:\\Users\\me\\Ensemble Projects\\a&b #1+2.md"
        from urllib.parse import quote
        h = H("/fileview?path=" + quote(path, safe="") + "&room=room-35d21def&token=T")
        with mock.patch.object(dashboard, "ACCESS_TOKEN", "T"):
            self.assertFalse(h._gate())
        self.assertEqual(h.sent["status"], 303)
        loc = urlparse(h.sent["Location"])
        self.assertEqual(loc.path, "/fileview")
        self.assertEqual(parse_qs(loc.query), {"path": [path], "room": ["room-35d21def"]})


class HomePaths(unittest.TestCase):
    """A chat link to ~/notes/ or ~/notes/a.md opens on the hub: the folder as
    a listing (/api/dir), the file through the raw reader (/api/file)."""

    def test_home_folder_and_file(self):
        import os
        import tempfile
        from unittest import mock
        import dashboard

        with tempfile.TemporaryDirectory() as home:
            notes = Path(home) / "notes"
            notes.mkdir()
            (notes / "a.md").write_text("hi", encoding="utf-8")
            env = {"USERPROFILE": home, "HOME": home}
            allowed = lambda p: os.path.normcase(p).startswith(os.path.normcase(home))
            with mock.patch.dict(os.environ, env), mock.patch.object(dashboard, "workspace_access_ok", allowed):
                status, d = dashboard.list_dir("~/notes/")
                self.assertEqual(status, 200, d)
                self.assertEqual([e["name"] for e in d["entries"]], ["a.md"])
                self.assertEqual(Path(d["path"]), notes)
                self.assertEqual(dashboard.resolve_file_ref("~/notes/a.md"), notes / "a.md")
            self.assertEqual(dashboard.list_dir("~/notes/")[0], 403, "outside the readable folders")


CHANGES_JS = r"""
const reg = new Map();
class El {
  constructor() { this.dataset = {}; this.isConnected = false; this._q = {}; this._h = ''; }
  set innerHTML(h) { this._h = h; this._q = {}; }
  get innerHTML() { return this._h; }
  querySelectorAll() { return []; }
  querySelector(s) {
    return this._q[s] || (this._q[s] = { value: '', focus() {}, addEventListener() {},
                                         classList: { add() {}, remove() {}, toggle() {} } });
  }
}
globalThis.document = { getElementById: id => reg.get(id) || null, querySelector: () => null, querySelectorAll: () => [],
                        addEventListener() {}, activeElement: null };
globalThis.window = { addEventListener() {}, matchMedia: () => ({ matches: false }), getSelection: () => null };
globalThis.matchMedia = () => ({ matches: false });
const mem = new Map();
globalThis.localStorage = { get length() { return mem.size; }, key: i => [...mem.keys()][i] ?? null,
  getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const toast = () => {}, wireScopeBar = () => {}, writeSlot = () => {}, wsJoin = (a, b) => a + '/' + b;
const PROJECTS = {
  pA: { id: 'pA', sessions: [{ roomId: 'room-aaaaaaa1', isLive: true }, { roomId: 'room-aaaaaaa2', isLive: true }] },
  pB: { id: 'pB', sessions: [{ roomId: 'room-bbbbbbb1', isLive: true }] },
  pC: { id: 'pC', sessions: [{ roomId: 'room-ccccccc1', isLive: true }] },
};
const projectById = id => PROJECTS[id];
const projectTasks = pj => ((pj && pj.sessions) || []);  // no PO here; test_task_search covers it
let PROJECT_TAB = 'changes', SB_DEST = '', SELECTED_PROJECT = 'pA';
// The hub: two repositories in project A's folder; `slow[root]` holds that
// root's answers back, `hold` holds back a send.
const sent = []; let hold = null; const slow = {};
globalThis.fetch = async (url, o) => {
  sent.push(JSON.parse(o.body));
  if (hold) await hold.p;
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
const api = async url => {
  if (url.startsWith('/api/git/roots')) return { roots: [{ path: '/r1', name: 'r1' }, { path: '/r2', name: 'r2' }] };
  const root = decodeURIComponent((url.match(/path=([^&]*)/) || [])[1] || '');
  if (slow[root]) await slow[root];
  if (url.startsWith('/api/git/status')) return { files: [{ path: root === '/r1' ? 'a.py' : 'b.py', status: 'M' }] };
  return { diff: '@@ -1 +1 @@\n+x' + root + '\n' };
};
const tick = () => new Promise(r => setTimeout(r, 0));
const later = () => { let free; const p = new Promise(r => { free = r; }); return [p, free]; };
%s
const out = {};
const put = (rv, note, file) => {
  const q = drRange(drParse('@@ -1 +1 @@\n+x\n'), 1, 1);
  rv.store.put({ cid: rv.store.id(), at: rv.store.now(), root: rv.key.split('|')[1], file: file || 'a.py', rows: q.rows, span: q.span, note, sent: 0 });
};
const unsent = rv => (rv ? rv.store.list.filter(c => !c.sent).map(c => c.note) : null);
(async () => {
  chUseContext('pA', '/repoA');
  const A = CH_RV;
  put(A, 'fix A');
  out.aOptions = A.rooms().map(r => r.id);
  A.to = 'room-aaaaaaa2';
  // Project B's panel: B's own review, empty.
  wireChangesPanel(projectById('pB'));            // no #chp-files in this stub: B's empty context
  out.bKey = CH_RV.key; out.bComments = CH_RV.store.list.length;
  // Back on A: A's comments and its chosen chat.
  chUseContext('pA', '/repoA');
  out.aSame = CH_RV === A; out.aBack = unsent(CH_RV); out.aTo = CH_RV.to;
  await drSubmit(CH_RV);
  out.sentTo = sent.map(s => s.roomId); out.sentHasA = sent[0].text.includes('fix A');
  out.aAfterSend = unsent(A).length;
  // Sent from A, moved to C before the hub answered, commented on C.
  put(A, 'second A');
  let release; hold = { p: new Promise(r => { release = r; }) };
  const sending = drSubmit(A);
  chUseContext('pC', '/repoC'); put(CH_RV, 'fix C', 'c.py');
  release(); await sending; hold = null;
  out.aAfterMove = unsent(A).length;
  out.cAfterMove = unsent(DR_REVIEWS.get('project:pC|/repoC'));

  // Two repositories of project A, through the real panel code.
  SELECTED_PROJECT = 'pA'; CH_ROOT = '';
  const files = new El(); files.id = 'chp-files'; files.isConnected = true;
  files.dataset = { project: 'pA', scope: '/s', root: '' }; reg.set('chp-files', files);
  const diff = new El(); diff.id = 'chp-diff'; diff.isConnected = true; reg.set('chp-diff', diff);
  wireChangesPanel(projectById('pA')); await tick();
  out.r1 = [CH_RV.key, files.dataset.root, files.innerHTML.includes('a.py')];
  // A line of r1's diff taken for a comment, then r2 picked before it is added.
  await chOpenDiff('a.py');
  const v = CH_RV.view;
  v.draft = { a: 1, b: 1, note: 'on r1' };
  files.querySelector('.chp-repo').value = '/r2'; files.querySelector('.chp-repo').onchange();
  out.pickNow = [CH_RV.key, diff.innerHTML.includes('class="drv"'), files.innerHTML.includes('a.py')];
  drSave(v);
  await tick();
  out.r2 = [CH_RV.key, files.dataset.root, files.innerHTML.includes('b.py'), files.innerHTML.includes('a.py')];
  out.r1Batch = unsent(DR_REVIEWS.get('project:pA|/r1'));
  out.r2Batch = unsent(DR_REVIEWS.get('project:pA|/r2'));
  // r1 picked and slow to list its files, then r2: r1's late answer is dropped.
  let freeR1; [slow['/r1'], freeR1] = later();
  chPickRepo('/r1'); await tick(); chPickRepo('/r2'); await tick(); freeR1(); await tick();
  out.outOfOrder = [CH_RV.key, files.dataset.root, files.innerHTML.includes('b.py'), files.innerHTML.includes('a.py')];
  // r2's diff slow to arrive, r1 picked meanwhile: the late diff is dropped.
  let freeR2; [slow['/r2'], freeR2] = later();
  const pend = chOpenDiff('b.py'); chPickRepo('/r1'); await tick(); freeR2(); await pend; await tick();
  out.lateDiff = diff.innerHTML.includes('x/r2');
  delete slow['/r1']; delete slow['/r2'];
  // A send pending on the same review: Submit again sends nothing, and a
  // comment added meanwhile stays unsent.
  const r1 = CH_RV;
  out.r1Key = r1.key;
  put(r1, 'A2');
  const n0 = sent.length; let answer; hold = { p: new Promise(r => { answer = r; }) };
  const first = drSubmit(r1);
  await drSubmit(r1);
  while (sent.length === n0) await tick();      // the first send is on its way
  put(r1, 'C');
  answer(); await first; hold = null;
  out.sendsWhilePending = sent.length - n0;
  out.afterPendingSend = unsent(r1);
  out.sentPending = sent[n0].text.includes('on r1') && sent[n0].text.includes('A2') && !sent[n0].text.includes('\n\nC');
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ChangesComments(unittest.TestCase):
    """Review comments on a project's Changes tab stay with their own project
    and repository: switching never sends one project's comments to another's
    chat, and a late answer never takes over the panel."""

    def test_comments_follow_their_project(self):
        src = PAGES["index.html"].replace("\r\n", "\n")
        i = src.index("// ---- Diff review: begin")
        j = src.index("// ---- Roadmap tab", i)
        store = (ROOT / "static" / "comments.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "changes.cjs"
            script.write_text(CHANGES_JS.replace("%s", store + src[i:j], 1), encoding="utf-8")
            out = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r["aOptions"], ["room-aaaaaaa1", "room-aaaaaaa2"])
        self.assertEqual((r["bKey"], r["bComments"]), ("project:pB|", 0))
        self.assertTrue(r["aSame"])
        self.assertEqual(r["aBack"], ["fix A"])
        self.assertEqual(r["aTo"], "room-aaaaaaa2", "the chosen chat was lost")
        self.assertEqual(r["sentTo"], ["room-aaaaaaa2"])
        self.assertTrue(r["sentHasA"])
        self.assertEqual(r["aAfterSend"], 0)
        # Repositories of one project.
        self.assertEqual(r["r1"], ["project:pA|/r1", "/r1", True])
        self.assertEqual(r["pickNow"], ["project:pA|/r2", False, False], "r1's diff or files still up after picking r2")
        self.assertEqual(r["r2"], ["project:pA|/r2", "/r2", True, False])
        self.assertEqual(r["r1Batch"], ["on r1"], "a comment on r1's diff belongs to r1")
        self.assertEqual(r["r2Batch"], [])
        self.assertEqual(r["outOfOrder"], ["project:pA|/r2", "/r2", True, False], "a late answer took over the panel")
        self.assertFalse(r["lateDiff"], "a late diff was drawn over another repository")
        # A send pending on the same review.
        self.assertEqual(r["r1Key"], "project:pA|/r1")
        self.assertEqual(r["sendsWhilePending"], 1, "a second send went out while the first was pending")
        self.assertEqual(r["afterPendingSend"], ["C"], "a comment added during the send was lost")
        self.assertTrue(r["sentPending"])
        self.assertEqual(r["aAfterMove"], 0, "the comments sent are the ones marked sent")
        self.assertEqual(r["cAfterMove"], ["fix C"], "C's new comment was dropped")


def top_level_function(src: str, name: str) -> str:
    """A top-level function's source: from its declaration to the first
    closing brace at the start of a line."""
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    return src[m.start():src.index("\n}\n", m.start()) + 3]


HUB_ACTION_FUNCTIONS = ("actionsCell", "detailOverflow", "ideAction", "openWorkspaceTab", "terminalAction",
                        "openHeadless", "termsAction", "toggleCapturedTerms", "capturedTermsShown", "waitFor",
                        "showCapturedTerminal")

HUB_ACTIONS_JS = r"""
globalThis.location = new URL(process.argv[1]);
const log = [];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const PLATFORM = { terminalName: 'Windows Terminal', fileManagerName: 'Explorer',
                   features: { focus: true, themes: 'launch-only', send: true } };
const feat = k => !!PLATFORM.features[k];
const T = () => PLATFORM.terminalName, FM = () => PLATFORM.fileManagerName;
const CHAT_SCHEME_ON = {};
const CSS = { escape: s => s };
const toast = (msg) => log.push('toast:' + msg);
const ADOPT_WAIT_MS = 1500;
const closeThemeMenu = () => {};
const closeIjMenu = () => {}, showIjMenu = () => log.push('ij-menu'), openInEditor = async p => log.push('editor:' + p);
const updateRoomsBadge = () => {};
let ALL_ROWS = [], SELECTED_SID = null, ISSUE_TAB = '', ISSUE_TAB_SID = '', NEW_ROOM = null, FRAME = null;
// Refreshes that return the list from before the new task (a poll in flight);
// polls held off (a theme menu open, a hidden tab); a request that never answers.
let STALE_REFRESHES = 0, POLLS_HELD = false, HUNG = false, ADOPT_ID = 'room-0000new1';
const api = async (url) => {
  log.push('api:' + url);
  if (url.startsWith('/api/repos/')) return [{ path: '/code/x', editor: 'code' }];
  if (url === '/api/session/adopt') { await new Promise(r => setTimeout(r, 50)); return { room: { id: ADOPT_ID } }; }
  return { result: 'ok' };
};
const refresh = async ({ now = false } = {}) => {
  if (POLLS_HELD && !now) { log.push('refresh:held'); return; }
  if (HUNG) { log.push('refresh:hung'); return new Promise(() => {}); }
  if (STALE_REFRESHES > 0) { STALE_REFRESHES--; log.push('refresh:stale'); return; }
  if (NEW_ROOM && !ALL_ROWS.includes(NEW_ROOM)) ALL_ROWS.push(NEW_ROOM);
};
const openDetail = sid => { SELECTED_SID = sid; log.push('detail:' + sid + ':' + ISSUE_TAB); };
const renderDetail = () => log.push('render:' + ISSUE_TAB);
const issueTab = () => ISSUE_TAB;
const wsRoots = ctx => { const r = ALL_ROWS.find(x => x.sessionId === ctx.sid); return r && r.taskDir ? [{ path: r.taskDir }] : []; };
const $ = () => ({ dataset: {} });
globalThis.window = { open: (u) => log.push('window:' + u) };
globalThis.document = {
  querySelector: s => {
    if (!s.includes('iframe.dp-session')) return { scrollIntoView() {} };
    if (!FRAME) return null;
    return (!s.includes('data-room') || s.includes(FRAME.room)) ? FRAME.ifr : null;
  },
};
// A /session page: its terminals hidden or shown, its agents, whose terminals
// appear once the page has built them. An agent's dot gets its state (dead or
// idle) at once, or with `late` a while after the agents are built, as the
// page's own status poll does; `pty: ''` is an agent with no terminal.
function session(room, { hidden = true, agents = [{ name: 'claude' }, { name: 'codex' }] } = {}) {
  const classes = set => ({ contains: c => set.has(c), add: c => set.add(c), get length() { return 1 + set.size; } });
  const bodySet = new Set(hidden ? ['term-hidden'] : []);
  let built = !hidden;
  const states = [];
  const els = agents.map(a => {
    const set = new Set(a.open ? ['open'] : []);
    const dotSet = new Set();
    const state = () => dotSet.add(a.dead ? 'dead' : 'idle');
    if (a.late) states.push(state); else state();
    const dot = { classList: classes(dotSet), dataset: { pty: a.pty ?? 'pty-' + a.name } };
    const input = { focus: () => log.push('focus:' + a.name) };
    const el = { classList: classes(set), scrollIntoView: () => log.push('scroll:' + a.name) };
    el.querySelector = s => s === '.ah' ? { click: () => { set.add('open'); log.push('open:' + a.name); } }
      : s === '.adot' ? dot
      : s === '.xterm-helper-textarea' ? (set.has('open') && dot.dataset.pty ? input : null) : null;
    return el;
  });
  const build = () => { built = true; setTimeout(() => states.forEach(f => f()), 400); };
  if (built) build();
  const terms = { click: () => { log.push('terms'); bodySet.delete('term-hidden'); setTimeout(build, 150); } };
  const doc = { readyState: 'complete', body: { classList: classes(bodySet) },
                getElementById: id => id === 'terms' ? terms : null,
                querySelectorAll: s => (s === '#agentcol .agent' && built) ? els : [] };
  return { room, ifr: { contentDocument: doc, contentWindow: { postMessage: m => log.push('post:' + m.ensemble) },
                        scrollIntoView: () => log.push('frame-scroll'), focus: () => log.push('frame-focus') } };
}
%s
const btn = (data) => ({ dataset: data, classList: { add() {}, remove() {} } });
const take = () => log.splice(0);
(async () => {
  const out = {};
  const liveLegacy = { sessionId: 's-live', pid: 42, cwd: 'C:\\code\\x', isLive: true };
  const history = { sessionId: 's-hist', cwd: 'C:\\code\\y', agent: 'claude', label: 'old' };
  const liveRoom = { sessionId: 'room-0000aaa1', roomId: 'room-0000aaa1', headless: true, isLive: true,
                     cwd: 'C:\\t\\repo', taskDir: 'C:\\t', status: 'active' };
  const endedRoom = { sessionId: 'room-0000bbb1', roomId: 'room-0000bbb1', headless: true, isLive: false, cwd: 'C:\\u' };
  ALL_ROWS = [liveLegacy, history, liveRoom, endedRoom];
  out.live = actionsCell(liveLegacy, true);
  out.history = actionsCell(history, false);
  out.menu = detailOverflow(liveRoom, true, true);
  out.endedMenu = detailOverflow(endedRoom, false, true);
  out.forkAnywhere = /fork/i.test(out.live + out.history + out.menu);

  // IDE
  await ideAction(btn({ sid: liveRoom.sessionId })); out.ideRoom = [take(), ISSUE_TAB, ISSUE_TAB_SID];
  await ideAction(btn({ sid: history.sessionId })); out.ideNoFolder = take();

  // Terminal on a history session
  NEW_ROOM = { sessionId: 'room-0000new1', roomId: 'room-0000new1', headless: true, isLive: true };
  // Pressed twice at once, with polls held off; the list answers from before
  // the adoption twice, and the first agent is a reviewer with no terminal
  // whose dot is coloured only after it is built.
  FRAME = session('room-0000new1', { agents: [{ name: 'codex', pty: '', dead: true, late: true },
                                              { name: 'claude', late: true }] });
  SELECTED_SID = null; ISSUE_TAB = ''; STALE_REFRESHES = 2; POLLS_HELD = true;
  const histBtn = () => btn({ sid: history.sessionId, cwd: history.cwd, agent: 'claude', label: 'old' });
  await Promise.all([terminalAction(histBtn()), terminalAction(histBtn())]);
  out.terminalHistory = take(); out.adoptingLeft = ADOPTING.size; POLLS_HELD = false;

  // The task list never answers: the action gives up in time and opens nothing.
  ADOPT_ID = 'room-0000hung'; HUNG = true;
  const t0 = Date.now();
  await terminalAction(btn({ sid: 's-hist2', cwd: 'C:\\code\\z', agent: 'claude' }));
  out.hung = [take(), Date.now() - t0]; HUNG = false;
  // Pressed again once the list answers: the task already made is shown, no
  // second one is started.
  NEW_ROOM = { sessionId: 'room-0000hung', roomId: 'room-0000hung', headless: true, isLive: true };
  FRAME = session('room-0000hung', { agents: [{ name: 'claude' }] });
  await terminalAction(btn({ sid: 's-hist2', cwd: 'C:\\code\\z', agent: 'claude' }));
  out.retry = take(); out.adoptedLeft = ADOPTED.size;

  // The task panel's Terminal: first press shows it, the next brings it to the front.
  FRAME = session(liveRoom.roomId, { agents: [{ name: 'claude', dead: true }, { name: 'codex' }] });
  SELECTED_SID = liveRoom.sessionId; ISSUE_TAB_SID = liveRoom.sessionId; ISSUE_TAB = 'workspace';
  out.shownBefore = capturedTermsShown();
  await termsAction(); out.firstPress = [take(), ISSUE_TAB];
  out.shownAfter = capturedTermsShown();
  await termsAction(); out.secondPress = take();
  // Which agent: never one with no terminal, even when its status failed
  // (every dot dead) or its empty pane is the one open.
  const pick = async agents => {
    FRAME = session(liveRoom.roomId, { agents });
    SELECTED_SID = ISSUE_TAB_SID = liveRoom.sessionId; ISSUE_TAB = 'activity';
    await showCapturedTerminal(liveRoom.roomId);
    return take();
  };
  out.statusFailed = await pick([{ name: 'codex', pty: '', dead: true }, { name: 'claude', dead: true }]);
  out.openEmptyReviewer = await pick([{ name: 'codex', pty: '', dead: true, open: true }, { name: 'claude' }]);
  out.noPty = await pick([{ name: 'codex', pty: '', dead: true }]);
  // Not running: nothing to show.
  await showCapturedTerminal(endedRoom.roomId); out.ended = [take(), SELECTED_SID];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


class HubMachineActions(unittest.TestCase):
    """IDE, Terminal and Focus act on the hub machine's own screen. From another
    computer IDE opens the Workspace tab, Terminal the terminal the hub
    captures (pressed again, to the front), and Focus is not offered. Fork is
    gone everywhere."""

    def test_fork_is_gone(self):
        for name, src in PAGES.items():
            self.assertNotIn("fork-btn", src, name)
            self.assertNotIn("/api/fork", src, name)
        self.assertNotIn('"/api/fork"', (ROOT / "dashboard.py").read_text(encoding="utf-8"))

    def test_session_page_still_has_what_terminal_uses(self):
        src = PAGES["session.html"]
        for hook in ('<button id="terms"', "classList.add('term-hidden')", "classList.toggle('term-hidden')",
                     "$('#agentcol')", "div.className = 'agent'", '<div class="ah">',
                     "div.querySelector('.ah').onclick", "div.classList.toggle('open')", "'adot ' + s.cls"):
            self.assertIn(hook, src, f"session.html no longer has {hook!r}, which the dashboard's Terminal uses")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_on_the_hub_and_from_another_computer(self):
        src = PAGES["index.html"].replace("\r\n", "\n")
        defs = [re.search(r"^const LOOPBACK_HOST_RE = .*$", src, re.M).group(0),
                re.search(r"^const onHubMachine = .*$", src, re.M).group(0),
                re.search(r"^const finderLabel = .*$", src, re.M).group(0),
                re.search(r"^const ADOPTING = .*$", src, re.M).group(0),
                re.search(r"^const ADOPTED = .*$", src, re.M).group(0)]
        prog = HUB_ACTIONS_JS % "\n".join(defs + [top_level_function(src, n) for n in HUB_ACTION_FUNCTIONS])

        def run(location):
            out = subprocess.run([NODE, "-e", prog, location], capture_output=True, text=True,
                                 encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout)

        hub, mac = run("http://127.0.0.1:8765/"), run("http://hub-host:8765/")
        for r in (hub, mac):
            self.assertFalse(r["forkAnywhere"])
            self.assertNotIn("dp-terms", r["endedMenu"], "a task that is not running has no terminal")

        # On the hub machine nothing changes.
        self.assertIn('class="focus-btn"', hub["live"])
        self.assertIn("preferred editor", hub["live"])
        self.assertIn("Open in a real Windows Terminal terminal window", hub["history"])
        self.assertIn('dp-terms-btn">Show terminals<', hub["menu"])
        self.assertNotIn("dp-terms-hide", hub["menu"])
        self.assertIn(">Open in editor<", hub["menu"])
        self.assertEqual(hub["ideRoom"][0], ["api:/api/repos/room-0000aaa1", "editor:/code/x"])
        self.assertIn("api:/api/open", hub["terminalHistory"])
        self.assertNotIn("api:/api/session/adopt", hub["terminalHistory"])
        self.assertEqual(hub["firstPress"][0], ["post:toggleTerms"])

        # From another computer.
        self.assertNotIn("focus-btn", mac["live"])
        self.assertIn('class="ij-btn"', mac["live"])
        self.assertIn("Workspace tab", mac["live"])
        self.assertIn("headless, and show its terminal", mac["history"])
        self.assertIn('dp-terms-btn" title=', mac["menu"])
        self.assertIn(">Terminal<", mac["menu"])
        self.assertIn('dp-terms-hide" hidden>Hide terminal<', mac["menu"])
        self.assertIn(">Show in Workspace<", mac["menu"])
        # IDE: the Workspace tab, no editor; a session with no Workspace folder
        # browses its own folder instead.
        log, tab, tab_sid = mac["ideRoom"]
        self.assertEqual((tab, tab_sid), ("workspace", "room-0000aaa1"))
        self.assertFalse([x for x in log if x.startswith(("api:", "editor:"))], log)
        self.assertEqual(mac["ideNoFolder"], ["window:/fileview?path=C%3A%5Ccode%5Cy"])
        # Terminal on a history session: resumed headless, its terminal shown
        # and focused; no window on the hub.
        t = mac["terminalHistory"]
        self.assertNotIn("api:/api/open", t)
        self.assertEqual([x for x in t if x in ("api:/api/session/adopt", "terms", "open:claude", "focus:claude")],
                         ["api:/api/session/adopt", "terms", "open:claude", "focus:claude"], t)
        self.assertEqual(t.count("api:/api/session/adopt"), 1, "a second press while opening starts no second agent")
        self.assertIn("toast:already opening this session…", t)
        self.assertEqual(mac["adoptingLeft"], 0)
        self.assertEqual(t.count("refresh:stale"), 2, "kept refreshing until the new task was listed")
        self.assertNotIn("refresh:held", t, "a user action refreshes even while polls hold off")
        self.assertNotIn("open:codex", t, "not the reviewer, which has no terminal")
        self.assertFalse([x for x in t if x.startswith("toast:") and "opening" not in x and "headless" not in x], t)
        # The list never answers: given up within the wait, nothing opened.
        log, elapsed = mac["hung"]
        self.assertIn("refresh:hung", log)
        self.assertTrue(any("has not shown it yet" in x for x in log), log)
        self.assertFalse([x for x in log if x.startswith(("detail:", "terms", "open:"))], log)
        self.assertLess(elapsed, 2500)
        # Pressed again: the task already made, not a second adopt.
        retry = mac["retry"]
        self.assertNotIn("api:/api/session/adopt", retry)
        self.assertEqual([x for x in retry if x in ("toast:opened headless", "terms", "open:claude", "focus:claude")],
                         ["toast:opened headless", "terms", "open:claude", "focus:claude"], retry)
        self.assertEqual(mac["adoptedLeft"], 0)
        # Which agent's terminal: one that has a pty, whatever the dots say.
        for case in ("statusFailed", "openEmptyReviewer"):
            self.assertIn("open:claude", mac[case], case)
            self.assertEqual(mac[case][-1], "focus:claude", case)
            self.assertNotIn("open:codex", mac[case], case)
        self.assertIn("toast:No agent in this task has a terminal", mac["noPty"])
        self.assertFalse([x for x in mac["noPty"] if x.startswith(("open:", "focus:"))], mac["noPty"])
        # The task panel's Terminal: shown on the Activity tab, the running
        # agent's terminal opened and focused...
        self.assertFalse(mac["shownBefore"])
        first, tab = mac["firstPress"]
        self.assertEqual(tab, "activity")
        self.assertIn("terms", first)
        self.assertIn("open:codex", first, "the running agent's terminal, not the stopped one's")
        self.assertNotIn("open:claude", first)
        self.assertEqual(first[-1], "focus:codex")
        self.assertTrue(mac["shownAfter"])
        # ...and pressed again, brought to the front: nothing toggled or hidden.
        second = mac["secondPress"]
        self.assertNotIn("terms", second)
        self.assertNotIn("post:toggleTerms", second)
        self.assertEqual([x for x in second if not x.startswith("render:")],
                         ["frame-scroll", "scroll:codex", "frame-focus", "focus:codex"])
        log, selected = mac["ended"]
        self.assertTrue(log and log[0].startswith("toast:This task is not running"), log)
        self.assertEqual(selected, "room-0000aaa1", "an ended task does not take over the panel")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_forced_refresh_gives_up_in_time(self):
        """The real refresh(): a user action's refresh that runs out of time
        lets go of the list, so polling carries on, and waits for a poll that
        never answers only until its own time is up."""
        src = PAGES["index.html"].replace("\r\n", "\n")
        defs = [re.search(r"^let _refreshInFlight = .*$", src, re.M).group(0), top_level_function(src, "refresh")]
        out = subprocess.run([NODE, "-e", REAL_REFRESH_JS % "\n".join(defs)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        took, locked, view = r["gaveUp"]
        self.assertLess(took, 1000)
        self.assertFalse(locked, "the list lock is released")
        self.assertEqual(view, "", "running out of time is not shown as an error in the list")
        self.assertEqual(r["resumed"], ["new"], "the next poll still updates the list")
        self.assertLess(r["waitedBehindHung"], 1000)


REAL_REFRESH_JS = r"""
let ALL_ROWS = [], DISPLAYED_ORDER = [], DISPLAYED_WORKFLOW = new Map(), SELECTED_SID = null;
let _projTs = Date.now() + 1e9, PROJECTS = [], _projFailed = false, SELECTED_PROJECT = null, PROJ_GROUPBY = '';
const selectInteracting = () => false, workflowOf = () => '', renderRows = () => {}, renderDetail = () => {};
const toast = () => {}, esc = s => String(s);
const VIEW = { matches: () => false, innerHTML: '' };
const $ = () => VIEW;
globalThis.document = { hidden: false, getElementById: () => null };
let ANSWER = false;
// The task list: answers only once ANSWER is set; otherwise fails when aborted.
const api = (url, opts) => new Promise((resolve, reject) => {
  if (ANSWER) return resolve([{ sessionId: 'new' }]);
  if (opts && opts.signal) opts.signal.addEventListener('abort', () => reject(opts.signal.reason));
});
%s
// Node does not stay up for AbortSignal.timeout's timer alone; a browser tab does.
setInterval(() => {}, 1000);
(async () => {
  const out = {};
  let t0 = Date.now();
  await refresh({ now: true, until: Date.now() + 300 });
  out.gaveUp = [Date.now() - t0, _refreshInFlight, VIEW.innerHTML];
  ANSWER = true; await refresh(); out.resumed = ALL_ROWS.map(r => r.sessionId);
  ANSWER = false; refresh();          // a poll that never answers
  t0 = Date.now();
  await refresh({ now: true, until: Date.now() + 300 });
  out.waitedBehindHung = Date.now() - t0;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
"""


if __name__ == "__main__":
    unittest.main()
