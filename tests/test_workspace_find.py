"""Go to file and search in files, in a Workspace.

The box above a Workspace's tree finds a file by the letters of its name and
searches the text of the Workspace's files:

* the matcher (index.html's "Workspace find" block, run here in Node the way
  tests/test_workspace_tabs.py runs the tabs block; skipped without Node):
  letters in order, best placement first, this repo's files ranked the way a
  person would expect, marks and notes that say why results are fewer;
* the hub's /api/ws/files and /api/ws/search (dashboard.ws_files, ws_search,
  run for real, the search in its child process): only the folder asked for,
  never through a link out of it, never .git, node_modules or what .gitignore
  excludes; the match cap, regex errors, binary and large files skipped,
  UTF-8 and offsets counted the way a browser counts them, cancel and deadline;
* the page wiring: the box, its keys, one tab-opening path, the phone rules.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import workspace_search  # noqa: E402

INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
FILEVIEW = (ROOT / "fileview.html").read_text(encoding="utf-8").replace("\r\n", "\n")
DASHBOARD = (ROOT / "dashboard.py").read_text(encoding="utf-8")
NODE = shutil.which("node")
GIT = shutil.which("git")


def find_block() -> str:
    i = INDEX.index("// ---- Workspace find: begin")
    j = INDEX.index("// ---- Workspace find: end", i)
    esc = re.search(r"^const esc = .*$", INDEX, re.M).group(0)
    return esc + "\n" + INDEX[i:j]


def repo_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, encoding="utf-8")
    files = [f for f in out.stdout.splitlines() if f]
    return files or [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


JS = r"""
%s
const files = %s;
const log = {};
const top = (q, n) => wsFuzzyRank(q, files, n || 3).items.map(x => x.path);
log.tops = Object.fromEntries(['wsptabs', 'hl', 'dash', 'idx', 'readme', 'fileview', 'test_ws', 'sk/SKILL', 'chatroom']
  .map(q => [q, top(q)]));
log.spec = wsFuzzy('wsptabs', 'workspace_tabs.py');
log.none = [wsFuzzy('zq', 'workspace_tabs.py'), wsFuzzy('tabsx', 'tabs'), wsFuzzy('ab', 'b/a')];
log.caseless = [wsFuzzy('README', 'readme.md').hits, wsFuzzy('readme', 'README.md').hits];
log.spaces = wsFuzzy('ws tabs', 'workspace_tabs.py').hits;
log.slashes = [wsFuzzy('skills\\SKILL', 'skills/ensemble/SKILL.md') !== null, wsFuzzy('a/b', 'ab') === null];
log.nameOverFolder = wsFuzzy('tabs', 'tabs/x/tabs.py').hits;
log.hump = wsFuzzy('fvst', 'fileview/fvStartState.js').hits;
log.unicode = wsFuzzy('i', '\u0130x/i.txt').hits;
log.empty = wsFuzzy('  ', 'a.py');
const wide = wsFuzzyRank('ws', files, 500, files.map(wsfLower));
log.narrowed = [wsFuzzyRank('wsp', wide.pool, 10, wide.poolLower).items.map(x => x.path), top('wsp', 10)];

// A large repo: 50,000 paths, every one matching a short query.
const big = [];
for (let k = 0; k < 50000; k++) big.push(`src/module_${k %% 97}/sub${k %% 13}/someFileName_${k}.ts`);
const bigL = big.map(wsfLower);
const timeIt = q => { const t = performance.now(); const r = wsFuzzyRank(q, big, WSF_SHOWN, bigL); return [r.total, performance.now() - t]; };
timeIt('warm');
log.big = ['e', 'smfn', 'module_5/some', 'someFileName_4999'].map(timeIt);

log.goto = ['dashboard.py:120', 'a:1:5', ':12', 'C:\\x', 'x:12345678', '  name  '].map(wsGotoQuery);
log.markAt = [wsMarkAt('a<b', [0, 2], 0), wsMarkAt('bc', [1, 2], 1), wsMarkAt('abc', [], 0)];
log.markRanges = wsMarkRanges('x<y z', [[1, 1], [0, 1], [3, 9], [99, 1], null, [4, 0]]);
log.gotoHtml = wsGotoHtml('u', [{ path: 'a/b<c.py', hits: [2, 0] }, { path: 'top.md', hits: [0] }]);
log.findFrom = [null, 'x', { mode: 'text', q: 'needle', cs: true, rx: 'yes' }, { mode: 'evil', q: 'x'.repeat(501) }, { q: 5 }]
  .map(wsFindFrom);
const res = { root: 'C:\\t', matches: 3, files: [
  { path: 'a/one.py', matches: [{ line: 4, at: 2, text: 'x = <needle>', ranges: [[5, 6]], n: 1, cutStart: false, cutEnd: false },
                                { line: 9, at: 0, text: 'needle needle', ranges: [[0, 6], [7, 6]], n: 2, cutStart: true, cutEnd: true }] },
  { path: 'two.md', matches: [{ line: 1, at: 0, text: 'Needle', ranges: [[0, 6]], n: 1 }] }] };
const rows = wsHitRows(res);
log.hitRows = { kinds: rows.rows.map(r => r.kind), idx: rows.hits.map(h => h.i), counts: rows.rows.filter(r => r.kind === 'file').map(r => r.count) };
log.hitsHtml = wsHitsHtml('u', rows.rows);
const F = o => ({ mode: 'files', q: 'x', cs: false, rx: false, list: null, ranked: null, res: null, ...o });
log.notes = [
  F({ q: '  ' }),
  F({}),
  F({ list: { error: '403 path_not_allowed' } }),
  F({ list: { files: ['a'], truncated: false, max: 50000 }, ranked: { items: [], total: 0 } }),
  F({ list: { files: ['a'], truncated: true, max: 50000 }, ranked: { items: [1, 2], total: 2 } }),
  F({ list: { files: ['a'], truncated: false, max: 50000 }, ranked: { items: new Array(200), total: 1234 } }),
  F({ mode: 'text', q: '' }),
  F({ mode: 'text', q: 'n' }),
  F({ mode: 'text', q: 'ne', res: { busy: true } }),
  F({ mode: 'text', q: '(x', res: { error: 'bad_regex', detail: 'missing ), unterminated subpattern at position 0' } }),
  F({ mode: 'text', q: 'ne', res: { files: [], matches: 0, filesSearched: 48, skipped: {} } }),
  F({ mode: 'text', q: 'ne', res: { files: [1], matches: 1, filesSearched: 48, skipped: {} } }),
  F({ mode: 'text', q: 'ne', res: { files: new Array(8), matches: 1000, limit: 1000, truncated: true, filesSearched: 9,
      skipped: { binary: 2, large: 1, unreadable: 0 }, largeBytes: 2097152, timedOut: true, deadline: 8, listTruncated: true, filesListed: 50000 } }),
  F({ mode: 'text', q: 'ne', res: { error: '504' } }),
].map(wsFindNote);
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class Matcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "find.cjs"
            script.write_text(JS % (find_block(), json.dumps(repo_files())), encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=120)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_letters_of_a_name_find_the_file(self):
        self.assertEqual(self.r["spec"]["hits"], [0, 4, 5, 10, 11, 12, 13], "wsptabs in workspace_tabs.py")
        self.assertEqual(self.r["none"], [None, None, None], "letters missing, too many, or out of order")
        self.assertEqual(self.r["caseless"], [[0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5]])
        self.assertEqual(self.r["spaces"], [0, 4, 10, 11, 12, 13], "spaces are ignored")
        self.assertEqual(self.r["slashes"], [True, True], "either slash is a folder break, and a break must be there")
        self.assertEqual(self.r["empty"], {"score": 0, "hits": []})

    def test_the_best_placement_wins(self):
        self.assertEqual(self.r["nameOverFolder"], [7, 8, 9, 10], "the file's own name, not the folder of the same name")
        self.assertEqual(self.r["hump"], [9, 10, 11, 12], "a camelCase hump is a word start")
        self.assertEqual(self.r["unicode"], [3], "a letter whose lower case is longer keeps the indexes in place")

    def test_this_repos_files_rank_as_a_person_expects(self):
        tops = self.r["tops"]
        self.assertEqual(tops["wsptabs"][0], "tests/test_workspace_tabs.py")
        self.assertEqual(tops["hl"][0], "static/hl.js")
        self.assertEqual(tops["dash"][0], "dashboard.py")
        self.assertEqual(tops["idx"][0], "index.html")
        self.assertEqual(tops["readme"][0], "README.md")
        self.assertEqual(tops["fileview"][0], "fileview.html")
        self.assertEqual(tops["test_ws"][0], "tests/test_workspace_find.py" if "tests/test_workspace_find.py" in repo_files()
                         else "tests/test_workspace_tabs.py")
        self.assertIn(tops["sk/SKILL"][0], ("skills/ensemble/SKILL.md", "skills/ensemble-design/SKILL.md"))
        self.assertEqual(tops["chatroom"][0], "chatroom.py")

    def test_a_longer_query_searches_what_the_shorter_one_found(self):
        narrowed, full = self.r["narrowed"]
        self.assertEqual(narrowed, full)

    def test_fast_on_a_large_repo(self):
        # A regression guard in Node, well above what a page measures: every
        # one of 50,000 paths matching a short query, ranked on each key.
        for total, ms in self.r["big"]:
            self.assertLess(ms, 400, f"{total} paths ranked in {ms:.0f} ms")
        self.assertEqual(self.r["big"][0][0], 50000)

    def test_go_to_a_line(self):
        self.assertEqual(self.r["goto"], [
            {"q": "dashboard.py", "line": 120}, {"q": "a", "line": 1}, {"q": ":12", "line": 0},
            {"q": "C:\\x", "line": 0}, {"q": "x:12345678", "line": 0}, {"q": "name", "line": 0}])

    def test_marks_are_escaped(self):
        self.assertEqual(self.r["markAt"], ['<mark class="match">a</mark>&lt;<mark class="match">b</mark>',
                                            '<mark class="match">bc</mark>', "abc"])
        self.assertEqual(self.r["markRanges"], 'x<mark class="match">&lt;</mark>y<mark class="match"> z</mark>')
        html = self.r["gotoHtml"]
        self.assertIn('<span class="wsr-nm"><mark class="match">b</mark>&lt;c.py</span><span class="wsr-dir"><mark class="match">a</mark></span>', html)
        self.assertIn('title="a/b&lt;c.py"', html)
        self.assertIn('id="u-r1" aria-selected="false" data-i="1" title="top.md"><span class="wsr-nm"><mark class="match">t</mark>op.md</span></div>', html,
                      "a file at the top has no folder")

    def test_the_box_is_remembered_and_junk_dropped(self):
        empty = {"mode": "files", "q": "", "cs": False, "rx": False}
        f = self.r["findFrom"]
        self.assertEqual(f[0], empty)
        self.assertEqual(f[1], empty)
        self.assertEqual(f[2], {"mode": "text", "q": "needle", "cs": True, "rx": False})
        self.assertEqual(f[3], empty)
        self.assertEqual(f[4], empty)

    def test_results_grouped_by_file(self):
        rows = self.r["hitRows"]
        self.assertEqual(rows["kinds"], ["file", "hit", "hit", "file", "hit"])
        self.assertEqual(rows["idx"], [0, 1, 2])
        self.assertEqual(rows["counts"], [3, 1])
        html = self.r["hitsHtml"]
        self.assertEqual(html.count('role="group"'), 2)
        self.assertEqual(html.count('role="option"'), 3)
        self.assertIn('<span class="wsr-ln">4</span><span class="wsr-t">x = &lt;<mark class="match">needle</mark>&gt;</span>', html)
        self.assertIn('<span class="wsr-t">…<mark class="match">needle</mark> <mark class="match">needle</mark>…</span>', html)
        self.assertIn('<span class="wsr-n">3</span>', html)

    def test_the_note_says_why_results_are_fewer(self):
        n = self.r["notes"]
        self.assertEqual(n[0], "")
        self.assertEqual(n[1], "Reading the list of files…")
        self.assertEqual(n[2], "Can't list the files: 403 path_not_allowed")
        self.assertEqual(n[3], "No file matches “x”.")
        self.assertEqual(n[4], "2 files. Only the first 50,000 files are listed.")
        self.assertEqual(n[5], "The best 200 of 1,234 files.")
        self.assertEqual(n[6], "")
        self.assertEqual(n[7], "Type 2 or more characters to search the files.")
        self.assertEqual(n[8], "Searching…")
        self.assertEqual(n[9], "Not a valid regular expression: missing ), unterminated subpattern at position 0")
        self.assertEqual(n[10], "No matches in 48 files.")
        self.assertEqual(n[11], "1 match in 1 file.")
        self.assertEqual(n[12], "The first 1,000 matches, in 8 files; more results not shown. Stopped after 8 s, with what was found by then."
                                " Skipped 2 binary files, 1 file over 2 MB. Only the first 50,000 files were searched.")
        self.assertEqual(n[13], "The search didn't finish: 504")


def _junction(link: Path, target: Path) -> bool:
    try:
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            return r.returncode == 0 and link.exists()
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def _symlink(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False


class Endpoints(unittest.TestCase):
    """The hub's two endpoints, over a folder built for them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wsf"))
        base = cls.tmp
        root, capdir, outside = base / "task", base / "capdir", base / "outside"
        for d in (root, capdir, outside):
            d.mkdir()
        cls.root, cls.capdir, cls.outside = root, capdir, outside
        (outside / "secret.txt").write_text("needle outside\n", encoding="utf-8")
        (root / "notes.md").write_text("Café 😀 needle here\n", encoding="utf-8")
        (root / "task.json").write_text('{"title": "no match"}\n', encoding="utf-8")
        (root / "bin.dat").write_bytes(b"needle\x00\x01\x02")
        (root / "big.txt").write_bytes(b"needle\n" * 400_000)          # 2.8 MB
        (root / "latin.txt").write_bytes(b"needle caf\xe9\r\n")        # not UTF-8
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "index.js").write_text("needle\n", encoding="utf-8")
        (root / ".git").mkdir()                                       # a stray .git folder is never read either
        (root / ".git" / "needle.txt").write_text("needle\n", encoding="utf-8")
        (capdir / "many.txt").write_text("needle needle\n" * 700, encoding="utf-8")   # 1,400 matches
        cls.git = bool(GIT)
        if cls.git:
            repo = root / "repo"
            (repo / "src").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
            (repo / "src" / "app.py").write_text("x = 'needle'\nNEEDLE = 1\n", encoding="utf-8")
            (repo / "src" / "debug.log").write_text("needle\n", encoding="utf-8")
            (repo / "build").mkdir()
            (repo / "build" / "out.js").write_text("needle\n", encoding="utf-8")
        cls.junction = _junction(root / "jn", outside)
        cls.file_link = _symlink(root / "secret-link.txt", outside / "secret.txt")
        cls.inner_link = _symlink(root / "notes-link.md", root / "notes.md")
        allowed = (str(root), str(capdir))
        cls.patch = mock.patch.object(dashboard, "workspace_access_ok",
                                      lambda p: any(dashboard._within(p, a) for a in allowed))
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        if cls.junction and os.name == "nt":
            os.rmdir(cls.root / "jn")                                 # the link, never what it points at
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def files(self, root=None):
        code, d = dashboard.ws_files(str(root or self.root))
        self.assertEqual(code, 200, d)
        return set(d["files"])

    def search(self, q, root=None, **kw):
        return dashboard.ws_search(str(root or self.root), q, **kw)

    def test_lists_the_folder_without_git_node_modules_or_ignored(self):
        want = {"notes.md", "task.json", "bin.dat", "big.txt", "latin.txt"}
        if self.git:
            want |= {"repo/.gitignore", "repo/src/app.py"}
        if self.inner_link:
            want.add("notes-link.md")
        self.assertEqual(self.files(), want)

    @unittest.skipUnless(GIT, "git is not installed")
    def test_a_folder_inside_a_repo_follows_its_ignores(self):
        self.assertEqual(self.files(self.root / "repo" / "src"), {"app.py"})

    def test_no_path_leads_out_of_the_folder(self):
        outside = str(self.outside)
        for root in (outside, str(self.root) + os.sep + ".." + os.sep + "outside", "outside", "",
                     "C:\\Windows" if os.name == "nt" else "/etc"):
            self.assertEqual(dashboard.ws_files(root)[0], 403, root)
            self.assertEqual(dashboard.ws_search(root, "needle")[0], 403, root)
        self.assertEqual(dashboard.ws_files(str(self.root / "nope"))[0], 404)
        # A `..` that stays inside is folded away first.
        code, d = dashboard.ws_files(str(self.root / "repo" / ".."))
        self.assertEqual((code, d.get("root")), (200, str(self.root)))
        found = {f["path"] for f in self.search("needle")[1]["files"]}
        self.assertFalse(any("secret" in p or p.startswith("jn") for p in found), found)
        self.assertFalse(any("secret" in p or p.startswith("jn/") for p in self.files()))

    def test_links_out_are_not_followed(self):
        if not (self.junction or self.file_link):
            self.skipTest("this machine cannot make a junction or a symlink")
        code, d = self.search("outside")
        self.assertEqual(code, 200, d)
        self.assertEqual(d["matches"], 0, d["files"])

    def test_plain_text_ignores_case_and_counts_like_a_browser(self):
        code, d = self.search("NEEDLE")
        self.assertEqual(code, 200, d)
        by = {f["path"]: f["matches"] for f in d["files"]}
        self.assertEqual(by["notes.md"], [{"line": 1, "at": 0, "text": "Café 😀 needle here", "ranges": [[8, 6]], "n": 1,
                                           "cutStart": False, "cutEnd": False}], "😀 is two UTF-16 units")
        self.assertEqual(by["latin.txt"][0]["text"], "needle caf\ufffd", "bytes that are not UTF-8 are replaced, and \\r dropped")
        self.assertNotIn("bin.dat", by)
        self.assertNotIn("big.txt", by)
        self.assertNotIn("node_modules/pkg/index.js", by)
        self.assertFalse(any(p.startswith(".git") for p in by))
        self.assertEqual(d["skipped"], {"binary": 1, "large": 1, "unreadable": 0})
        if self.git:
            self.assertEqual([m["line"] for m in by["repo/src/app.py"]], [1, 2])
            self.assertNotIn("repo/src/debug.log", by)
            self.assertNotIn("repo/build/out.js", by)
        self.assertEqual(d["root"], str(self.root))
        self.assertFalse(d["truncated"])

    @unittest.skipUnless(GIT, "git is not installed")
    def test_match_case_and_regex(self):
        d = self.search("NEEDLE", case=True)[1]
        self.assertEqual([(f["path"], [m["line"] for m in f["matches"]]) for f in d["files"]], [("repo/src/app.py", [2])])
        d = self.search(r"^x\s*=\s*'(\w+)'$", regex=True)[1]
        self.assertEqual([(f["path"], f["matches"][0]["ranges"]) for f in d["files"]], [("repo/src/app.py", [[0, 12]])])
        d = self.search("needle.", regex=False)[1]
        self.assertEqual(d["matches"], 0, "a dot is a dot in plain text")

    def test_bad_regex_and_empty_query(self):
        code, d = self.search("(unclosed", regex=True)
        self.assertEqual(code, 400)
        self.assertEqual(d["error"], "bad_regex")
        self.assertIn("missing )", d["detail"])
        self.assertEqual(self.search("(unclosed")[0], 200, "the same text is fine as plain text")
        self.assertEqual(self.search("")[1]["error"], "empty_query")
        self.assertEqual(self.search("x" * 501)[1]["error"], "query_too_long")

    def test_the_first_1000_matches_and_no_more(self):
        code, d = self.search("needle", root=self.capdir)
        self.assertEqual(code, 200, d)
        self.assertEqual(d["matches"], 1000)
        self.assertTrue(d["truncated"])
        self.assertEqual(d["limit"], 1000)
        lines = d["files"][0]["matches"]
        self.assertEqual(len(lines), 500, "two matches a line")
        self.assertEqual(lines[-1]["ranges"], [[0, 6], [7, 6]])

    def test_a_long_line_is_cut_around_its_match(self):
        line = "    " + "a" * 1000 + "needle" + "b" * 1000
        hit = workspace_search._line_hit(line, [(1004, 1010)])
        self.assertEqual(hit["at"], 1004 - workspace_search.BEFORE_MATCH)
        self.assertEqual(hit["ranges"], [[workspace_search.BEFORE_MATCH, 6]])
        self.assertEqual(len(hit["text"]), workspace_search.LINE_SHOWN)
        self.assertTrue(hit["cutStart"] and hit["cutEnd"])
        short = workspace_search._line_hit("\tneedle", [(1, 7)])
        self.assertEqual((short["at"], short["text"], short["cutStart"]), (1, "needle", False), "indentation dropped, not cut")

    def test_a_deadline_returns_what_was_found(self):
        code, d = self.search("needle", deadline=0)
        self.assertEqual(code, 200, d)
        self.assertTrue(d["timedOut"])

    def test_typing_again_cancels_the_search_before(self):
        slow = [sys.executable, "-c", "import time; time.sleep(30)"]
        first = dashboard._ws_search_start("box:page", 1, slow)
        second = dashboard._ws_search_start("box:page", 3, slow)
        other = dashboard._ws_search_start("other:page", 1, slow)
        # Asked for between the two, but reaching the hub after the newer one.
        late = dashboard._ws_search_start("box:page", 2, slow)
        try:
            first.wait(timeout=10)
            self.assertTrue(first.ws_cancelled)
            self.assertIsNone(late, "an older search arriving late never starts")
            self.assertIsNone(second.poll(), "the newest one runs")
            self.assertIsNone(other.poll(), "another box's search is left alone")
            code, d = dashboard.ws_search(str(self.root), "needle", tag="box:page", seq=2)
            self.assertEqual((code, d), (409, {"error": "cancelled"}))
            self.assertIsNone(second.poll())
        finally:
            for p in (first, second, other):
                if p.poll() is None:
                    p.kill()
                p.communicate()
                dashboard._ws_search_done("box:page", p)
                dashboard._ws_search_done("other:page", p)
        self.assertEqual({k: p for k, (_, p) in dashboard._WS_SEARCHES.items() if k in ("box:page", "other:page")},
                         {"box:page": None, "other:page": None},
                         "finished searches hold no child")
        code, d = dashboard.ws_search(str(self.root), "needle", tag="box:page", seq=4)
        self.assertEqual(code, 200, d)

    def test_a_search_no_longer_wanted_is_stopped(self):
        slow = [sys.executable, "-c", "import time; time.sleep(30)"]
        running = dashboard._ws_search_start("gone:page", 1, slow)
        try:
            self.assertEqual(dashboard.ws_search_cancel("gone:page", 2), (200, {"stopped": True}))
            running.wait(timeout=10)
            self.assertTrue(running.ws_cancelled)
            self.assertIsNone(dashboard._ws_search_start("gone:page", 1, slow), "one asked for before, arriving late")
            self.assertEqual(dashboard.ws_search_cancel("gone:page", 1), (200, {"stopped": False}), "an old cancel changes nothing")
            self.assertEqual(dashboard.ws_search_cancel("", 9)[0], 400)
        finally:
            if running.poll() is None:
                running.kill()
            running.communicate()
        self.assertEqual(dashboard._WS_SEARCHES["gone:page"], (2, None))
        self.assertEqual(dashboard.ws_search(str(self.root), "needle", tag="gone:page", seq=3)[0], 200)

    def test_a_cancel_is_remembered_when_the_registry_is_full(self):
        saved = dict(dashboard._WS_SEARCHES)
        keep = dashboard._WS_SEARCHES_KEEP
        try:
            dashboard._WS_SEARCHES.clear()
            for i in range(keep):
                dashboard._WS_SEARCHES[f"old{i}:page"] = (1, None)
            self.assertEqual(dashboard.ws_search_cancel("victim:page", 2), (200, {"stopped": False}))
            self.assertEqual(dashboard._WS_SEARCHES.get("victim:page"), (2, None), "the cancel just written is kept")
            self.assertEqual(len(dashboard._WS_SEARCHES), keep)
            self.assertNotIn("old0:page", dashboard._WS_SEARCHES, "the box heard from longest ago goes first")
            self.assertIn("old1:page", dashboard._WS_SEARCHES, "and only as many as needed")
            self.assertIsNone(dashboard._ws_search_start("victim:page", 1, [sys.executable, "-c", "pass"]),
                              "a search asked for before the cancel still never starts")
        finally:
            dashboard._WS_SEARCHES.clear()
            dashboard._WS_SEARCHES.update(saved)

    def test_what_a_search_started_ends_with_it(self):
        # A stand-in for a search stopped during its `git ls-files`: it starts a
        # process of its own, says its pid, and is then replaced by a newer search.
        script = ("import subprocess, sys, time; sys.path.insert(0, %r); import workspace_search; "
                  "workspace_search._children_die_with_me(); "
                  "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                  "print(p.pid, flush=True); time.sleep(60)") % str(ROOT)
        first = dashboard._ws_search_start("tree:page", 1, [sys.executable, "-c", script])
        grandchild = int(first.stdout.readline())
        second = dashboard._ws_search_start("tree:page", 2, [sys.executable, "-c", "pass"])
        try:
            first.wait(timeout=10)
            self.assertTrue(_gone_within(grandchild, 10), "the process the search started was left running")
        finally:
            for p in (first, second):
                if p.poll() is None:
                    p.kill()
                p.communicate()
                dashboard._ws_search_done("tree:page", p)

    def test_the_viewer_reaches_the_line_a_search_found(self):
        d = Path(tempfile.mkdtemp(prefix="wsfv", dir=self.tmp))
        big = d / "long.txt"
        big.write_bytes(b"line\n" * 110_000 + b"x needle y\n" + b"tail\n" * 20_000)   # the hit at line 110001, past 512 KB
        with mock.patch.object(dashboard, "workspace_access_ok", lambda p: True):
            hits = workspace_search.search(str(d), "needle")["files"]
            self.assertEqual([m["line"] for m in hits[0]["matches"]], [110_001])
            code, plain = dashboard.read_workspace_file(str(big))
            self.assertEqual(code, 200)
            self.assertTrue(plain["truncated"])
            self.assertNotIn("needle", plain["text"], "without a line, the first 512 KB")
            code, at = dashboard.read_workspace_file(str(big), 110_001)
            self.assertEqual(code, 200)
            self.assertEqual(at["text"].split("\n")[110_000], "x needle y")
            self.assertEqual(len(at["text"].encode("utf-8")), 110_000 * 5 + 11 + dashboard._TEXT_AFTER_LINE)
            self.assertTrue(at["truncated"])
            self.assertEqual(dashboard.read_workspace_file(str(big), 3)[1]["text"], plain["text"], "a line in the first 512 KB reads no more")
            huge = d / "huge.txt"
            huge.write_bytes(b"z\n" * 1_500_000)                                   # 3 MB
            self.assertEqual(len(dashboard.read_workspace_file(str(huge), 1_400_000)[1]["text"]),
                             workspace_search.FILE_BYTES_MAX, "never past the largest file a search reads")

    def test_a_search_of_this_repo_is_quick(self):
        with mock.patch.object(dashboard, "workspace_access_ok", lambda p: True):
            t0 = time.monotonic()
            code, d = dashboard.ws_search(str(ROOT), "wsOpenTabAt")
            ms = (time.monotonic() - t0) * 1000
        self.assertEqual(code, 200, d)
        self.assertGreater(d["matches"], 0)
        self.assertLess(ms, 1000, f"{ms:.0f} ms")


def _gone_within(pid: int, seconds: float) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(0x100000, False, pid)                   # SYNCHRONIZE
        if not h:
            return True
        try:
            return k32.WaitForSingleObject(h, int(seconds * 1000)) == 0
        finally:
            k32.CloseHandle(h)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False


class Swaps(unittest.TestCase):
    """A name listed as an ordinary file or folder, swapped for a link out of
    the folder before it is read: what opens is outside, so it is passed over."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wss"))
        self.root, self.outside = self.tmp / "task", self.tmp / "outside"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "sub" / "victim.txt").write_text("plain\n", encoding="utf-8")
        (self.root / "keep.txt").write_text("needle inside\n", encoding="utf-8")
        self.outside.mkdir()
        (self.outside / "victim.txt").write_text("needle outside\n", encoding="utf-8")
        (self.outside / "secret.txt").write_text("needle secret\n", encoding="utf-8")
        self.links: list[Path] = []

    def tearDown(self):
        for link in self.links:
            with contextlib.suppress(OSError):
                if link.is_dir() and os.name == "nt":
                    os.rmdir(link)
                else:
                    link.unlink()
        shutil.rmtree("\\\\?\\" + str(self.tmp) if os.name == "nt" else self.tmp, ignore_errors=True)

    def swap_sub_for_junction(self):
        sub = self.root / "sub"
        shutil.rmtree(sub)
        if not _junction(sub, self.outside):
            self.skipTest("this machine cannot make a junction")
        self.links.append(sub)

    def found(self, res):
        return {(f["path"], m["text"]) for f in res["files"] for m in f["matches"]}

    def test_a_folder_swapped_after_it_was_listed_is_not_walked(self):
        real_scan = workspace_search._scan

        def scan(d, real_root, ignores_rel):
            if os.path.basename(d) == "sub" and not self.links:
                self.swap_sub_for_junction()             # queued as a folder, a junction by the time it is opened
            return real_scan(d, real_root, ignores_rel)
        with mock.patch.object(workspace_search, "_scan", scan):
            res = workspace_search.search(str(self.root), "needle")
        self.assertTrue(self.links, "the swap happened")
        self.assertEqual(res["filesListed"], 1, "nothing listed from the folder it now leads to")
        self.assertEqual(self.found(res), {("keep.txt", "needle inside")})

    @unittest.skipUnless(os.name == "nt", "a Windows folder is listed by the path its handle reports")
    def test_a_junction_repointed_after_its_target_was_checked(self):
        inner, sub = self.root / "inner", self.root / "sub"
        inner.mkdir()
        (inner / "inside.txt").write_text("in\n", encoding="utf-8")
        (inner / ".git").mkdir()                         # so git is asked about it too
        real_scan, real_final = workspace_search._scan, workspace_search._handle_final
        step = [0]
        git_asked = []

        def scan(d, real_root, ignores_rel):
            if os.path.basename(d) == "sub" and step[0] == 0:
                shutil.rmtree(sub)                       # queued as a folder, now a junction to a folder inside
                if not _junction(sub, inner):
                    self.skipTest("this machine cannot make a junction")
                self.links.append(sub)
                step[0] = 1
            return real_scan(d, real_root, ignores_rel)

        def ignored(d, rel):
            git_asked.append((rel, workspace_search._plain(os.path.realpath(d))))
            return set()

        def final(h):
            p = real_final(h)
            if step[0] == 1 and p.lower().endswith("\\inner"):
                os.rmdir(sub)                            # the junction only: approved, then pointed outside
                self.assertTrue(_junction(sub, self.outside))
                step[0] = 2
            return p
        with mock.patch.object(workspace_search, "_scan", scan), mock.patch.object(workspace_search, "_handle_final", final), \
                mock.patch.object(workspace_search, "_git_ignored", ignored):
            files, _ = workspace_search.list_files(str(self.root))
        self.assertEqual(step[0], 2, "both swaps happened")
        self.assertEqual(set(files), {"inner/inside.txt", "keep.txt", "sub/inside.txt"},
                         "the folder that was checked is the one listed")
        want = workspace_search._plain(os.path.realpath(inner))
        self.assertEqual(git_asked, [("inner/", want), ("sub/", want)], "git is asked about the folder that was checked")

    @unittest.skipUnless(os.name == "nt", "Windows' 260-character path limit")
    def test_a_file_deeper_than_260_characters(self):
        parts = []
        while len(str(self.root)) + sum(len(p) + 1 for p in parts) < 320:
            parts.append("d" * 40)
        deep = os.path.join("\\\\?\\" + str(self.root), *parts)
        os.makedirs(deep)
        with open(os.path.join(deep, "long-name.txt"), "w", encoding="utf-8") as f:
            f.write("needle deep\n")
        want = "/".join(parts + ["long-name.txt"])
        self.assertGreater(len(str(self.root)) + len(want), 300)
        files, _ = workspace_search.list_files(str(self.root))
        self.assertIn(want, files)
        self.assertIn((want, "needle deep"), self.found(workspace_search.search(str(self.root), "needle")))

    def test_a_parent_folder_swapped_between_listing_and_reading(self):
        real_list = workspace_search.list_files

        def listed(root, enclosing_ignores=False, limit=workspace_search.FILES_MAX):
            out = real_list(root, enclosing_ignores, limit)
            self.assertIn("sub/victim.txt", out[0])
            self.swap_sub_for_junction()
            return out
        with mock.patch.object(workspace_search, "list_files", listed):
            res = workspace_search.search(str(self.root), "needle")
        self.assertEqual(self.found(res), {("keep.txt", "needle inside")})

    def test_a_file_swapped_for_a_link_between_listing_and_reading(self):
        real_list = workspace_search.list_files
        victim = self.root / "sub" / "victim.txt"

        def listed(root, enclosing_ignores=False, limit=workspace_search.FILES_MAX):
            out = real_list(root, enclosing_ignores, limit)
            victim.unlink()
            if not _symlink(victim, self.outside / "victim.txt"):
                self.skipTest("this machine cannot make a symlink")
            return out
        with mock.patch.object(workspace_search, "list_files", listed):
            res = workspace_search.search(str(self.root), "needle")
        self.assertEqual(self.found(res), {("keep.txt", "needle inside")})

    def test_a_nul_anywhere_makes_a_file_binary(self):
        (self.root / "late.dat").write_bytes(b"a" * 9000 + b"\x00needle\n")
        res = workspace_search.search(str(self.root), "needle")
        self.assertEqual(self.found(res), {("keep.txt", "needle inside")})
        self.assertEqual(res["skipped"]["binary"], 1)


class Job(unittest.TestCase):
    """A search's git calls end with it, or the search does not run."""

    @unittest.skipUnless(os.name == "nt", "the job is how Windows does it")
    def test_a_job_that_cannot_be_made_or_joined_is_an_error(self):
        for fails in ("CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"):
            k = mock.MagicMock()
            k.CreateJobObjectW.return_value = 1234
            k.SetInformationJobObject.return_value = 1
            k.AssignProcessToJobObject.return_value = 1
            getattr(k, fails).return_value = 0
            with self.subTest(fails), mock.patch.object(workspace_search, "_k32", k), \
                    mock.patch.object(workspace_search, "_JOB", None):
                with self.assertRaises(OSError):
                    workspace_search._children_die_with_me()
                self.assertIsNone(workspace_search._JOB)
                if fails != "CreateJobObjectW":
                    k.CloseHandle.assert_called_once_with(1234)

    def test_the_search_does_not_run_without_it(self):
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({"root": str(ROOT), "q": "x"}).encode("utf-8")))
        stdout = io.TextIOWrapper(io.BytesIO())
        with mock.patch.object(workspace_search, "_children_die_with_me", side_effect=OSError("no job")), \
                mock.patch.object(workspace_search, "search") as search, \
                mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
            code = workspace_search.main()
        search.assert_not_called()
        self.assertEqual(code, 1)
        out = json.loads(stdout.buffer.getvalue().decode("utf-8"))
        self.assertEqual(out["error"], "search_failed")
        self.assertIn("no job", out["detail"])
        self.assertIn('(500 if res["error"] == "search_failed" else 400)', DASHBOARD, "the hub passes it on as a failure")


def _fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


class PageWiring(unittest.TestCase):
    def test_the_box_is_a_combobox_over_a_listbox(self):
        panel = _fn(INDEX, "function wsPanelHtml(")
        self.assertIn('class="wsf-q" type="text" role="combobox"', panel)
        self.assertIn('class="wsf-res" role="listbox"', panel)
        self.assertIn('class="wsf-state" aria-live="polite"', panel)
        self.assertLess(panel.index('class="wsf"'), panel.index('class="wsp-tree"'), "the box sits above the tree")

    def test_keys_act_on_the_box_only(self):
        mount = _fn(INDEX, "function wsfMount(")
        keys = mount[mount.index("input.onkeydown"):]
        keys = keys[:keys.index("\n  };\n")]
        for k in ("'ArrowDown'", "'ArrowUp'", "'Enter'", "'Escape'"):
            self.assertIn(k, keys)
        self.assertNotIn("document.addEventListener('keydown'", mount)
        self.assertNotIn("window.addEventListener('keydown'", mount)

    def test_results_open_through_the_one_tab_path(self):
        opener = _fn(INDEX, "function wsfOpen(")
        self.assertEqual(opener.count("wsOpenTabAt(v, "), 3, "a file, a text match and a recent file")
        self.assertNotIn("iframe", opener)
        self.assertEqual(INDEX.count("f.className = 'wsp-frame';"), 1, "one place makes a viewer")
        at = _fn(INDEX, "function wsOpenTabAt(")
        self.assertIn("hit: hit ? { line, ...hit } : null", at)

    def test_a_search_is_cancelled_by_typing_again(self):
        search = _fn(INDEX, "async function wsfSearch(")
        self.assertIn("new AbortController()", search)
        self.assertIn("tag: v.uid + ':' + WSF_PAGE", search)
        self.assertIn("if (gen !== f.gen) return;", search, "an answer to an older query is dropped")
        schedule = _fn(INDEX, "function wsfSchedule(")
        self.assertIn("f.ctl.abort()", schedule)
        self.assertLess(schedule.index("f.gen++;"), schedule.index("if (sent) wsfCancel(v);"), "the hub stops it too, with the newer count")
        self.assertIn("cancel: '1', tag: v.uid + ':' + WSF_PAGE, n: String(v.find.gen)", _fn(INDEX, "function wsfCancel("))
        self.assertIn('if q.get("cancel", [""])[0] == "1":', DASHBOARD)

    def test_the_query_is_remembered_per_workspace(self):
        self.assertIn("find: { mode: f.mode, q: f.q, cs: f.cs, rx: f.rx }", _fn(INDEX, "function wsPersist("))
        self.assertIn("wsFindFrom(s && s.find)", _fn(INDEX, "function wsView("))
        sync = INDEX[INDEX.index("async function wsSync("):INDEX.index("\nfunction wsMount(")]
        self.assertIn("wsfEnsure(v);", sync)

    def test_phone_rules(self):
        phone = INDEX[INDEX.index("Find: the box and a few rows fit above the file"):]
        phone = phone[:phone.index("\n\n")]
        self.assertIn(".wsf-q { height: var(--touch-min); font-size: var(--fs-400); }", phone)
        self.assertIn(".wsf-mode button, .wsf-opt { height: var(--touch-min); min-width: var(--touch-min); }", phone)
        self.assertIn(".wsr { min-height: var(--touch-min); align-items: center; }", phone)
        self.assertIn(".wsp.wsf-on, .wsp.wsf-text { grid-template-columns: minmax(0, 1fr);", phone)

    def test_the_viewer_marks_the_match(self):
        self.assertIn("if (view === 'source' && hitNow()) markHit(box, hitNow());", FILEVIEW)
        self.assertIn("mark.fv-hit {", FILEVIEW)
        self.assertIn("border-radius:var(--r-100); }", FILEVIEW[FILEVIEW.index("mark.fv-hit {"):FILEVIEW.index("mark.fv-hit {") + 160])
        self.assertIn("(AT_LINE ? '&line=' + AT_LINE : '')", _fn(FILEVIEW, "async function loadText("))
        self.assertIn("range.extractContents()", _fn(FILEVIEW, "function markHit("))

    def test_the_hub_serves_both_endpoints(self):
        self.assertIn('if p == "/api/ws/files":', DASHBOARD)
        self.assertIn('if p == "/api/ws/search":', DASHBOARD)
        self.assertIn('[sys.executable, "-X", "utf8", str(Path(workspace_search.__file__).resolve())]', DASHBOARD)


if __name__ == "__main__":
    unittest.main()
