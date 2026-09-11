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
        # A sentence is not a path: only the relative file in it is a link.
        self.assertEqual(paths(CASES[18]), ["docs\\readme.md"])
        self.assertTrue(r[CASES[18]].startswith("C:\\foo is copied to "), r[CASES[18]])
        self.assertEqual(paths(CASES[24]), ["C:\\temp\\a.md", "C:\\temp\\b.md"])
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
    "page-actions": "its buttons are wired by #cat-rename-btn and #cat-delete-btn",
    "dp-actions": "its buttons carry the action classes wired in the document click handler",
    "actions": "its buttons carry the action classes wired in the document click handler",
    "offer": "its buttons are handled by the offer's own click listener",
    "pref-btn-row": "its buttons are wired by #pref-consolidate and #pref-split",
    "dp-prio": "its buttons are .dp-prio-btn, wired in the document click handler",
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
  set innerHTML(h) {
    this._h = h; this._q = {};
    if (this.id === 'ch-tray') {
      const options = [...h.matchAll(/<option value="([^"]*)"/g)].map(m => ({ value: m[1] }));
      reg.set('ch-to', { options, value: options.length ? options[0].value : '' });
    }
  }
  get innerHTML() { return this._h; }
  remove() { reg.delete(this.id); if (this.id === 'ch-tray') reg.delete('ch-to'); this.isConnected = false; }
  querySelectorAll() { return []; }
  querySelector(s) { return this._q[s] || (this._q[s] = {}); }
}
globalThis.document = {
  getElementById: id => reg.get(id) || null, createElement: () => new El(),
  body: { appendChild(n) { n.isConnected = true; reg.set(n.id, n); } },
  querySelector: () => null, querySelectorAll: () => [],
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const toast = () => {}, wireScopeBar = () => {};
const PROJECTS = {
  pA: { id: 'pA', sessions: [{ roomId: 'room-aaaaaaa1' }, { roomId: 'room-aaaaaaa2' }] },
  pB: { id: 'pB', sessions: [{ roomId: 'room-bbbbbbb1' }] },
  pC: { id: 'pC', sessions: [{ roomId: 'room-ccccccc1' }] },
};
const projectById = id => PROJECTS[id];
let PROJECT_TAB = 'changes', SB_DEST = '', SELECTED_PROJECT = 'pA';
const sent = []; let hold = null;
const api = (url, o) => { sent.push(JSON.parse(o.body)); return hold ? hold.p : Promise.resolve({}); };
%s
const out = {};
const tray = () => document.getElementById('ch-tray');
const say = (text, file) => CH_COMMENTS.push({ file: file || 'a.py', line: 1, side: '+', code: 'x', text });
(async () => {
  chUseContext('pA|/repoA'); say('fix A'); chRenderTray();
  out.aOptions = document.getElementById('ch-to').options.map(o => o.value);
  const to = document.getElementById('ch-to'); to.value = 'room-aaaaaaa2'; to.onchange();
  // Project B selected, before its panel is wired: A's tray is not shown on B.
  SELECTED_PROJECT = 'pB'; chRenderTray(); out.staleTray = !!tray();
  wireChangesPanel(projectById('pB'));            // no #chp-files in this stub: B's empty context
  out.bCtx = CH_CTX; out.bComments = CH_COMMENTS.length; out.bTray = !!tray();
  // The Changes tab left and entered again, back on A: A's batch and its chosen chat.
  PROJECT_TAB = 'tasks'; chRenderTray(); PROJECT_TAB = 'changes';
  SELECTED_PROJECT = 'pA'; chUseContext('pA|/repoA'); chRenderTray();
  out.aBack = CH_COMMENTS.map(c => c.text); out.aTo = document.getElementById('ch-to').value;
  await tray().querySelector('.ch-send').onclick();
  out.sentTo = sent.map(s => s.roomId); out.sentHasA = sent[0].text.includes('fix A');
  out.aAfterSend = CH_BATCHES.get('pA|/repoA').comments.length;
  // Sent from A, moved to C before the hub answered, commented on C.
  say('second A'); chRenderTray();
  let release; hold = { p: new Promise(r => { release = r; }) };
  const sending = tray().querySelector('.ch-send').onclick();
  SELECTED_PROJECT = 'pC'; chUseContext('pC|/repoC'); say('fix C', 'c.py');
  release({}); await sending;
  out.aAfterMove = CH_BATCHES.get('pA|/repoA').comments.length;
  out.cAfterMove = CH_BATCHES.get('pC|/repoC').comments.map(c => c.text);
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ChangesComments(unittest.TestCase):
    """Review comments on the Changes tab stay with their own project and
    repository: switching never sends one project's comments to another's chat."""

    def test_comments_follow_their_project(self):
        src = PAGES["index.html"].replace("\r\n", "\n")
        i = src.index("// ---- Changes tab")
        j = src.index("// ---- Roadmap tab", i)
        out = subprocess.run([NODE, "-e", CHANGES_JS.replace("%s", src[i:j], 1)], capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r["aOptions"], ["room-aaaaaaa1", "room-aaaaaaa2"])
        self.assertFalse(r["staleTray"], "project A's comments shown on project B")
        self.assertEqual((r["bCtx"], r["bComments"], r["bTray"]), ("pB|", 0, False))
        self.assertEqual(r["aBack"], ["fix A"])
        self.assertEqual(r["aTo"], "room-aaaaaaa2", "the chosen chat was lost")
        self.assertEqual(r["sentTo"], ["room-aaaaaaa2"])
        self.assertTrue(r["sentHasA"])
        self.assertEqual(r["aAfterSend"], 0)
        self.assertEqual(r["aAfterMove"], 0, "the batch sent is the one emptied")
        self.assertEqual(r["cAfterMove"], ["fix C"], "C's new comment was dropped")


if __name__ == "__main__":
    unittest.main()
