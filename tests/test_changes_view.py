"""The Changes view (#153, GitHub issue fab-ioc/ensemble#4): the changed files
as a tree by folder, a side-by-side diff, and the file list and the diff as two
panes with a splitter between them.

* the hub counts each file's lines added and removed (`add`/`del` on the files
  of /api/git/status, with branch=1, and of /api/git/log; None for a binary
  file; an untracked file's lines all count as added);
* the pure parts, run in Node (skipped without Node): the tree (folders first,
  a folder holding one folder and no file becomes one row such as
  `static/dock/src`, each folder's totals), the rows it draws (a folded folder
  shows only its row), the flat list, and side by side's pairing (removed and
  added lines face each other, the extra ones an empty cell, a context line on
  both sides, a hunk header across both);
* the page, in headless Chrome (skipped without Chrome), at 1280 and on a
  390 phone: a task's Changes shows the tree with `+N −M` on files and folders,
  Folders folds a folder and switches to the flat list and back (remembered in
  the browser); Unified / Side by side switches the diff (remembered), a
  comment started on a removed line's cell in side by side is added under
  its row, counted in the tray and sent with "removed line N"; the splitter
  is dragged, moved with →, put back by a double click, its width remembered
  and shared with the project's Changes; the project's Changes groups each
  commit's files by folder too. On the phone the list and the diff show one
  at a time with "‹ Files" back, the diff is unified however it was set, and
  nothing scrolls sideways.
* the largest diff on main in the week before this task (merge 198f817,
  static/dock/src/dock.js) renders in both modes in well under a second; the
  times are printed for the report.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_middle import CHROME, NODE  # noqa: E402
from tests.test_no_project_files import GIT, git, loose_task, patches, put  # noqa: E402

INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")


def block(a: str, b: str) -> str:
    i = INDEX.index(a)
    return INDEX[i:INDEX.index(b, i)]


def temp_repo(base: Path) -> Path:
    """A checkout with files in folders: main, then a branch with one commit
    and uncommitted edits, so a task's Changes has a tree to show."""
    repo = base / "repo"
    (repo / "src" / "app").mkdir(parents=True)
    (repo / "static" / "dock" / "src").mkdir(parents=True)
    put(repo / "README.md", "# Motors\n\nBrakes.\n")
    put(repo / "src" / "app" / "main.py", "import sys\n\n\ndef main(argv):\n    return 0\n\n\ndef helper(x):\n    return x + 1\n")
    put(repo / "src" / "app" / "util.py", "A = 1\nB = 2\n")
    put(repo / "static" / "dock" / "src" / "dock.js", "export const dock = 1;\nexport const bar = 2;\n")
    put(repo / "pic.png", "\x89PNG\x00\x00bin")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    git(repo, "checkout", "-q", "-b", "sess/ED-1-brakes")
    put(repo / "src" / "app" / "main.py", "import sys\n\n\ndef main(argv):\n    print('squeal')\n    return 0\n\n\ndef helper(x):\n    return x + 2\n")
    put(repo / "static" / "dock" / "src" / "dock.js", "export const dock = 1;\nexport const bar = 3;\nexport const baz = 4;\n")
    put(repo / "pic.png", "\x89PNG\x00\x01bin2")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "work")
    put(repo / "src" / "app" / "util.py", "A = 1\nB = 2\nC = 3\n")
    (repo / "docs").mkdir()
    put(repo / "docs" / "notes.md", "# Notes\n\nline\n")
    return repo


class TheHubCounts(unittest.TestCase):
    """add/del on the files the hub lists."""

    def setUp(self):
        if not GIT:
            self.skipTest("needs git")
        self.tmp = tempfile.TemporaryDirectory(prefix="ens-chv-", ignore_cleanup_errors=True)
        self.repo = temp_repo(Path(self.tmp.name))
        self.allowed = mock.patch.object(dashboard, "workspace_access_ok", lambda p: True)
        self.allowed.start()

    def tearDown(self):
        self.allowed.stop()
        self.tmp.cleanup()

    def by_path(self, files):
        return {f["path"]: (f.get("add"), f.get("del")) for f in files}

    def test_a_branch_s_files_carry_their_lines(self):
        code, res = dashboard.git_status(str(self.repo), True)
        self.assertEqual(code, 200, res)
        got = self.by_path(res["files"])
        self.assertEqual(got["src/app/main.py"], (2, 1))
        self.assertEqual(got["static/dock/src/dock.js"], (2, 1))
        self.assertEqual(got["src/app/util.py"], (1, 0), "an uncommitted edit counts too")
        self.assertEqual(got["docs/notes.md"], (3, 0), "an untracked file: all of its lines added")
        self.assertEqual(got["pic.png"], (None, None), "a binary file has no count")

    def test_the_uncommitted_files_carry_their_lines(self):
        code, res = dashboard.git_status(str(self.repo))
        self.assertEqual(code, 200, res)
        got = self.by_path(res["files"])
        self.assertEqual(got["src/app/util.py"], (1, 0))
        self.assertEqual(got["docs/"], (None, None), "git lists an untracked folder as one entry: no count")

    def test_a_renamed_file_is_listed_under_its_new_path_with_its_lines(self):
        git(self.repo, "mv", "src/app/util.py", "src/app/utils.py")
        git(self.repo, "add", "src/app/utils.py")
        code, res = dashboard.git_status(str(self.repo))
        self.assertEqual(code, 200, res)
        by = {f["path"]: f for f in res["files"]}
        self.assertNotIn("src/app/util.py", by, "the old path is gone")
        self.assertEqual(by["src/app/utils.py"]["status"], "R")
        self.assertEqual((by["src/app/utils.py"]["add"], by["src/app/utils.py"]["del"]), (1, 0), "the edit it carried along")
        code, res = dashboard.git_status(str(self.repo), True)
        self.assertEqual(code, 200, res)
        self.assertEqual(self.by_path(res["files"])["src/app/utils.py"], (1, 0))
        # A rename in the work tree (" R": the new name only intended to be added): the same record, the old path after it.
        git(self.repo, "reset", "-q")
        git(self.repo, "add", "-N", "src/app/utils.py")
        code, res = dashboard.git_status(str(self.repo))
        self.assertEqual(code, 200, res)
        paths = [f["path"] for f in res["files"]]
        self.assertEqual([f["status"] for f in res["files"] if f["path"] == "src/app/utils.py"], ["R"])
        self.assertNotIn("src/app/util.py", paths)
        self.assertTrue(all("/" in p or p in ("README.md", "pic.png", "docs/") for p in paths), f"no phantom row from the old path: {paths}")

    def test_what_landed_carries_its_lines(self):
        git(self.repo, "stash", "-u", "-q")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge #1: brakes", "sess/ED-1-brakes")
        code, res = dashboard.git_log(str(self.repo))
        self.assertEqual(code, 200, res)
        merge = res["commits"][0]
        self.assertEqual(merge["subject"], "Merge #1: brakes")
        got = self.by_path(merge["files"])
        self.assertEqual(got["src/app/main.py"], (2, 1))
        self.assertEqual(got["pic.png"], (None, None))
        self.assertEqual({f["status"] for f in merge["files"]}, {"M"}, "the status still comes with each file")
        first = res["commits"][1]
        self.assertEqual(self.by_path(first["files"])["src/app/util.py"], (2, 0))
        self.assertEqual({f["status"] for f in first["files"]}, {"A"})


PURE_JS = r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const agoSpan = ts => `<span data-ago="${ts}"></span>`;
const CH_FILES_SHOWN = 8;
%(landed)s
%(diff)s
const out = {};
const files = [
  { path: 'src/app/main.py', status: 'M', add: 2, del: 1 }, { path: 'src/app/util.py', status: 'M', add: 1, del: 0 },
  { path: 'static/dock/src/dock.js', status: 'M', add: 2, del: 1 }, { path: 'static/dock/src/layout.js', status: 'A', add: 10, del: 0 },
  { path: 'README.md', status: 'M', add: 1, del: 1 }, { path: 'pic.png', status: 'M', add: null, del: null },
  { path: 'docs/notes.md', status: '?', add: 3, del: 0 },
];
const t = chTree(files);
const flat = (n, d) => [[d, n.name, n.path, n.add, n.del, n.n, n.files.map(f => f.path)]].concat(n.dirs.flatMap(k => flat(k, d + 1)));
out.tree = flat(t, 0);
const closed = new Set(['src/app']);
out.rows = chTreeRowsHtml(t, '', p => closed.has(p), (f, d) => chFileHtml(f, '', f.path === 'README.md', d, true), 0);
out.grouped = chFilesHtml(files, 'abc', { file: 'docs/notes.md', sha: 'abc' }, { tree: true, closed: () => false });
out.flatList = chFilesHtml(files, '', { file: 'README.md', sha: '' }, { tree: false });
out.count = [chCountHtml(2, 1), chCountHtml(null, null), chCountHtml(12345, 0)];
out.folderEntry = chFileHtml({ path: 'docs/', status: '?' }, '', false, 0, true);
// Side by side: the pairs.
const rows = drParse('@@ -1,6 +1,7 @@\n a\n-b\n-c\n+B\n d\n+E\n+F\n-g\n');
out.pairs = drPairs(rows).map(p => p.map(i => (i < 0 ? '-' : rows[i].k + ':' + rows[i].t)));
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs Node")
class ThePureParts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        js = PURE_JS % {"landed": block("// ---- Changes, landed on main: begin", "// ---- Changes, landed on main: end"),
                        "diff": "\n".join(block("function drParse(text) {", "const drContent =").splitlines() + [
                            "const drContent = r => r.k === 'add' || r.k === 'del' || r.k === 'ctx';",
                            block("function drPairs(rows) {", "function drPairHtml(")])}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.js"
            f.write_text(js, encoding="utf-8")
            proc = subprocess.run([NODE, str(f)], capture_output=True, encoding="utf-8", timeout=60)
        assert proc.returncode == 0, proc.stderr
        cls.got = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_tree_groups_by_folder_and_compacts_single_child_folders(self):
        t = {row[2]: row for row in self.got["tree"]}
        self.assertEqual([r[1] for r in self.got["tree"] if r[0] == 1], ["docs", "src/app", "static/dock/src"], "folders first, by name; single-child folders as one row")
        self.assertEqual(t[""][6], ["pic.png", "README.md"], "the root's own files, by name")
        self.assertEqual(t[""][3:6], [19, 3, 7], "the root's totals: every file")
        self.assertEqual(t["static/dock/src"][3:6], [12, 1, 2])
        self.assertEqual(t["src/app"][6], ["src/app/main.py", "src/app/util.py"])
        self.assertEqual(t["docs"][3:6], [3, 0, 1])

    def test_the_rows_indent_fold_and_count(self):
        h = self.got["rows"]
        self.assertIn('<div class="chf dir" data-dir="static/dock/src" style="--d:0" tabindex="-1" aria-expanded="true"', h)
        self.assertIn('<span class="nm">static/dock/src</span><span class="cnt" title="12 added, 1 removed">+12 −1</span>', h)
        self.assertIn('data-dir="src/app" style="--d:0" tabindex="-1" aria-expanded="false"', h, "folded")
        self.assertNotIn("main.py", h, "a folded folder shows only its row")
        self.assertIn('data-file="static/dock/src/dock.js" style="--d:1"', h, "a file one step in")
        self.assertIn('<span class="nm">dock.js</span>', h, "the name, not the path, in a tree")
        self.assertIn('<div class="chf on" data-file="README.md" tabindex="-1" aria-current="true" title="README.md">', h)
        self.assertIn('data-file="pic.png" tabindex="-1" title="pic.png"><span class="chst st-M">M</span><span class="nm">pic.png</span></div>', h, "a binary file has no count")
        self.assertEqual(self.got["count"], ['<span class="cnt" title="2 added, 1 removed">+2 −1</span>', "", '<span class="cnt" title="12,345 added, 0 removed">+12,345 −0</span>'])
        self.assertIn('<span class="nm">docs/</span>', self.got["folderEntry"], "an untracked folder entry keeps its slash in a tree, so it does not read as a file")

    def test_a_commit_s_files_group_too_and_the_flat_list_stays(self):
        g = self.got["grouped"]
        self.assertIn('data-dir="docs" data-commit="abc"', g, "a folder row names its commit, so folding one commit's folder leaves another's alone")
        self.assertIn('<div class="chf on" data-file="docs/notes.md" data-commit="abc" style="--d:1" tabindex="-1" aria-current="true"', g)
        f = self.got["flatList"]
        self.assertNotIn('class="chf dir"', f)
        self.assertIn('<span class="nm">src/app/main.py</span>', f, "the whole path in a flat list")
        self.assertIn('<div class="chf on" data-file="README.md"', f)

    def test_side_by_side_pairs_removed_and_added_lines(self):
        self.assertEqual(self.got["pairs"], [
            ["hunk:@@ -1,6 +1,7 @@"],
            ["ctx:a", "ctx:a"],
            ["del:b", "add:B"], ["del:c", "-"],
            ["ctx:d", "ctx:d"],
            ["-", "add:E"], ["-", "add:F"],
            ["del:g", "-"],
        ])


CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));   // a diff of a few hundred KB rides along
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const PANE = `document.getElementById('detail-panel')._panes.changes`;
// What the task's Changes shows: its rows, its panes, its diff.
const LIST = `(() => {
  const x = ${PANE}, grid = x.querySelector('.tch'), list = x.querySelector('.tch-files'), right = x.querySelector('.tch-right');
  const vis = e => !!e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== 'none';
  const w = e => (e ? Math.round(e.getBoundingClientRect().width) : 0);
  return {
    rows: [...list.querySelectorAll('.chf.dir, .chf[data-file]')].map(e => ({ dir: e.dataset.dir || '', file: e.dataset.file || '', name: e.querySelector('.nm').textContent,
      cnt: (e.querySelector('.cnt') || {}).textContent || '', d: e.style.getPropertyValue('--d') || '0', open: e.getAttribute('aria-expanded'), on: e.classList.contains('on'), tab: e.tabIndex, pad: Math.round(parseFloat(getComputedStyle(e).paddingLeft)) })),
    tree: x.querySelector('.chp-tree').getAttribute('aria-pressed'), head: x.querySelector('.tch-head').textContent.replace(/\\s+/g, ' ').trim(),
    listW: w(list), rightW: w(right), gridW: w(grid), splitVis: vis(grid.querySelector('.ch-split')), listVis: vis(list), rightVis: vis(right),
    showDiff: grid.classList.contains('ch-show-diff'), stacked: grid.classList.contains('ch-narrow'), chW: grid.style.getPropertyValue('--ch-w'), valuenow: grid.querySelector('.ch-split').getAttribute('aria-valuenow'),
    split: !!x.querySelector('.drv.split'), drv: !!x.querySelector('.drv'), seg: (() => { const s = x.querySelector('.drv-mode'); return s ? { hidden: s.hidden, vis: vis(s), on: [...s.querySelectorAll('button')].filter(b => b.classList.contains('active')).map(b => b.dataset.mode) } : null; })(),
    back: vis(x.querySelector('.drv-back')), srs: x.querySelectorAll('.drv .sr').length, drs: x.querySelectorAll('.drv .dr[data-i]').length,
    ms: window.__drRenderMs, scrollW: document.documentElement.scrollWidth, vw: innerWidth, stored: { tree: localStorage.getItem('cd-ch-tree'), mode: localStorage.getItem('cd-diff-mode'), w: localStorage.getItem('cd-ch-w') },
  };
})()`;
const out = {};
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 800)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const at = async (sel) => evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    const mouse = (type, x, y, extra = {}) => c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1, ...extra }, sessionId);
    const click = async (sel) => { const [x, y] = await at(sel); await mouse('mouseMoved', x, y); await mouse('mousePressed', x, y); await mouse('mouseReleased', x, y); };
    const dblclick = async (sel) => { const [x, y] = await at(sel); for (const n of [1, 2]) { await mouse('mousePressed', x, y, { clickCount: n }); await mouse('mouseReleased', x, y, { clickCount: n }); } };
    const drag = async (sel, dx) => { const [x, y] = await at(sel); await mouse('mouseMoved', x, y); await mouse('mousePressed', x, y); for (const s of [0.3, 0.6, 1]) { await mouse('mouseMoved', x + dx * s, y); await sleep(30); } await mouse('mouseReleased', x + dx, y); };
    const dragTo = async (x1, y1, x2, y2) => { await mouse('mouseMoved', x1, y1); await mouse('mousePressed', x1, y1); for (const s of [0.3, 0.6, 1]) { await mouse('mouseMoved', x1 + (x2 - x1) * s, y1 + (y2 - y1) * s); await sleep(30); } await mouse('mouseReleased', x2, y2); };
    const key = async (k, code, vk) => { for (const t of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type: t, key: k, code, windowsVirtualKeyCode: vk }, sessionId); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    try { await evalIn(`['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ch-tree', 'cd-diff-mode', 'cd-ch-w'].forEach(k => localStorage.removeItem(k)); 0`); } catch (e) {}
    return { evalIn, until, shot, click, dblclick, drag, dragTo, key, sessionId, targetId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const tool = id => `#po-dock .dk-strip-btn[data-dk-auto="${id}"]`;
  const tab = name => `[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].find(e => e.textContent.trim().startsWith(${JSON.stringify(name)}))`;
  const open = async (p, sid) => {
    await p.evalIn(`openDetail(${JSON.stringify(sid)}); 0`);
    await p.until(`document.body.classList.contains("dp-docked") && SELECTED_SID === ${JSON.stringify(sid)} && document.getElementById('detail-panel').dataset.shell === ${JSON.stringify(sid)}`, 20000);
    await sleep(300);
  };
  const listed = p => p.until(`${PANE}.querySelectorAll('.tch-files .chf[data-file]').length > 0`, 20000);
  const rowSel = file => `${PANE}.querySelector('.tch-files .chf[data-file=${JSON.stringify(JSON.stringify(file)).slice(1, -1)}]')`;
  const diffShown = p => p.until(`!!${PANE}.querySelector('.tch-diff .drv .dr[data-i]')`, 15000);
  try {
    // ---- a desktop, 1280
    {
      const p = await page(1280, 800);
      await open(p, A.task);
      await p.click(tool('changes'));
      await listed(p); await sleep(300);
      out.tree = await p.evalIn(LIST);
      await p.shot('changes-1280-tree');
      // A folder folded by a click, opened again by →; ↓ moves along the rows.
      await p.evalIn(`${PANE}.querySelector('.tch-files .chf.dir[data-dir="src/app"]').click(); 0`); await sleep(150);
      out.folded = await p.evalIn(LIST);
      out.foldedFocus = await p.evalIn(`document.activeElement.dataset.dir || ''`);
      await p.key('ArrowRight', 'ArrowRight', 39); await sleep(150);
      out.reopened = await p.evalIn(LIST);
      await p.key('ArrowDown', 'ArrowDown', 40); await sleep(100);
      out.downFocus = await p.evalIn(`({ file: document.activeElement.dataset.file || '', dir: document.activeElement.dataset.dir || '', tab: document.activeElement.tabIndex })`);
      // Enter opens the file; the list is redrawn, the keyboard stays on its row: ↓ moves on.
      await p.key('Enter', 'Enter', 13); await diffShown(p); await sleep(200);
      out.enterFocus = await p.evalIn(`({ file: document.activeElement.dataset.file || '', on: ${PANE}.querySelector('.tch-files .chf.on').dataset.file, path: ${PANE}.querySelector('.drv-path').textContent })`);
      await p.key('ArrowDown', 'ArrowDown', 40); await sleep(100);
      out.enterDown = await p.evalIn(`document.activeElement.dataset.file || ''`);
      // Folders off: the flat list, remembered; on again.
      await p.click(`#po-dock .tch-head .chp-tree`); await sleep(200);
      out.flat = await p.evalIn(LIST);
      await p.shot('changes-1280-flat');
      await p.click(`#po-dock .tch-head .chp-tree`); await sleep(200);
      out.treeAgain = await p.evalIn(LIST);
      // A file: unified first, then side by side (remembered).
      await p.evalIn(`${rowSel('src/app/main.py')}.click(); 0`);
      await diffShown(p); await sleep(200);
      out.unified = await p.evalIn(LIST);
      await p.shot('changes-1280-unified');
      // The task list to its strip, the panel docked and maximised (Dock's own
      // controls): the whole width, so side by side fits.
      await p.evalIn(`if (LD.dock) LD.dock.unpin('list'); PD.dock.pin('changes'); 0`); await sleep(300);
      await p.evalIn(`PD.dock.toggleMax('changes'); 0`); await sleep(500);
      await p.until(`${PANE}.querySelector('.tch').getBoundingClientRect().width > 1000`, 10000);
      out.maxed = await p.evalIn(LIST);
      // The splitter: dragged 120px wider, → 16px more, a double click back.
      out.beforeDrag = await p.evalIn(LIST);
      await p.drag(`#po-dock .tch .ch-split`, 120); await sleep(200);
      out.dragged = await p.evalIn(LIST);
      await p.evalIn(`${PANE}.querySelector('.ch-split').focus(); 0`);
      await p.key('ArrowRight', 'ArrowRight', 39); await sleep(150);
      out.keyed = await p.evalIn(LIST);
      await p.shot('changes-1280-wider');
      await p.dblclick(`#po-dock .tch .ch-split`); await sleep(200);
      out.reset = await p.evalIn(LIST);
      await p.click(`#po-dock .drv-mode button[data-mode="split"]`);
      await p.until(`!!${PANE}.querySelector('.drv.split')`, 10000); await sleep(200);
      out.split = await p.evalIn(LIST);
      out.splitRows = await p.evalIn(`[...${PANE}.querySelectorAll('.drv .sr')].map(sr => [...sr.children].map(c => c.classList.contains('k-none') ? '-' : (c.className.match(/k-(\\w+)/) || [])[1] + ':' + (c.dataset.i || '') + ':' + (c.querySelector('.dg') ? [c.querySelector('.dg').dataset.o, c.querySelector('.dg').dataset.n].join('/') : '') + ':' + c.querySelector('.dt').textContent))`);
      out.splitGutters = await p.evalIn(`(() => { const sr = [...${PANE}.querySelectorAll('.drv .sr')].find(s => s.querySelector('.k-del') && s.querySelector('.k-add')); const old = sr.querySelector('.dr.old .dg'), nw = sr.querySelector('.dr.new .dg');
        const txt = (el, pseudo) => getComputedStyle(el, pseudo).content; return { oldBefore: txt(old, '::before'), oldAfter: txt(old, '::after'), newBefore: txt(nw, '::before'), newAfter: txt(nw, '::after'),
          oldW: Math.round(sr.querySelector('.dr.old').getBoundingClientRect().width), newW: Math.round(sr.querySelector('.dr.new').getBoundingClientRect().width), tokens: sr.querySelectorAll('[class^="tk-"], [class*=" tk-"]').length }; })()`);
      await p.shot('changes-1280-split');
      // A drag down the new column, from the context line above the pair to the
      // added line: the selection (what Copy takes) is that column alone.
      const [sx1, sy1, sx2, sy2] = await p.evalIn(`(() => { const srs = [...${PANE}.querySelectorAll('.drv .sr')]; const i = srs.findIndex(s => s.querySelector('.k-del') && s.querySelector('.k-add'));
        const a = srs[i - 1].querySelector('.dr.new .dt').getBoundingClientRect(), b = srs[i].querySelector('.dr.new .dt').getBoundingClientRect(); return [a.left + 2, a.top + a.height / 2, b.right - 2, b.top + b.height / 2]; })()`);
      await p.dragTo(sx1, sy1, sx2, sy2); await sleep(200);
      out.colSel = await p.evalIn(`({ text: getSelection().toString(), cls: ${PANE}.querySelector('.drv').className })`);
      await p.evalIn(`getSelection().removeAllRanges(); 0`); await sleep(100);
      // A comment on the removed line: its box under the row, the card, the tray, the message.
      await p.evalIn(`${PANE}.querySelector('.drv .sr .dr.old.k-del .dg').click(); 0`);
      await p.until(`!!${PANE}.querySelector('.dcx textarea')`, 5000);
      out.composer = await p.evalIn(`(() => { const x = ${PANE}, dcx = x.querySelector('.dcx'); return { loc: dcx.querySelector('.dc-loc').textContent, afterRow: dcx.previousElementSibling.classList.contains('sr'),
        sel: [...x.querySelectorAll('.dr.sel')].map(e => e.className), focused: document.activeElement === dcx.querySelector('textarea') }; })()`);
      await p.evalIn(`(() => { const ta = ${PANE}.querySelector('.dcx textarea'); ta.value = 'Why drop this line?'; ta.dispatchEvent(new Event('input', { bubbles: true })); })(); 0`);
      await p.evalIn(`${PANE}.querySelector('.dcx-add').click(); 0`);
      await p.until(`!!${PANE}.querySelector('.drv .dc')`, 5000); await sleep(200);
      out.card = await p.evalIn(`(() => { const x = ${PANE}, dc = x.querySelector('.drv .dc'); return { loc: dc.querySelector('.dc-loc').textContent, note: dc.querySelector('.dc-note').textContent, afterRow: dc.previousElementSibling.classList.contains('sr'),
        tray: x.querySelector('.cmt-tray').hidden ? '' : x.querySelector('.cmt-tray-head strong').textContent, item: (x.querySelector('.cmt-item-q') || {}).textContent || '', split: !!x.querySelector('.drv.split') }; })()`);
      await p.shot('changes-1280-comment');
      // Submit: what the hub would be sent (the resume call is answered here, so no agent starts).
      await p.evalIn(`window.__sent = []; (() => { const f = window.fetch.bind(window); window.fetch = (u, o) => { if (String(u).startsWith('/api/room/resume')) { window.__sent.push(JSON.parse(o.body)); return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })); } return f(u, o); }; })(); 0`);
      await p.evalIn(`${PANE}.querySelector('.cmt-submit').click(); 0`);
      await p.until(`window.__sent.length > 0 && !!${PANE}.querySelector('.drv .dc.sent')`, 10000);
      out.sent = await p.evalIn(`({ body: window.__sent[0], cards: [...${PANE}.querySelectorAll('.drv .dc')].map(e => e.className), tray: ${PANE}.querySelector('.cmt-tray').hidden })`);
      // A narrow panel (beside the conversation again): side by side gives way
      // to unified by itself, and comes back with the width.
      await p.evalIn(`PD.dock.toggleMax('changes'); 0`);
      await p.until(`!${PANE}.querySelector('.drv.split')`, 10000); await sleep(300);
      out.narrow = await p.evalIn(LIST);
      await p.shot('changes-1280-narrow');
      await p.evalIn(`PD.dock.toggleMax('changes'); 0`);
      await p.until(`!!${PANE}.querySelector('.drv.split')`, 10000); await sleep(300);
      out.wideAgain = await p.evalIn(LIST);
      // The project's Changes: the same tree per commit, the same width.
      await p.drag(`#po-dock .tch .ch-split`, 60); await sleep(200);
      await p.evalIn(`closeDetail && closeDetail(); SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'changes'; renderRows(); 0`);
      await p.until(`(() => { const b = pdById('chp-files'); return !!b && b.querySelectorAll('.chf[data-file]').length > 0; })()`, 20000); await sleep(300);
      out.project = await p.evalIn(`(() => { const b = pdById('chp-files'), grid = b.closest('.chp'), w = e => Math.round(e.getBoundingClientRect().width);
        return { rows: [...b.querySelectorAll('.chf.dir, .chf[data-file]')].map(e => ({ dir: e.dataset.dir || '', file: e.dataset.file || '', sha: (e.dataset.commit || '').slice(0, 7), name: e.querySelector('.nm').textContent, cnt: (e.querySelector('.cnt') || {}).textContent || '', d: e.style.getPropertyValue('--d') || '0' })),
          secs: [...b.querySelectorAll('.chp-sec')].map(e => e.textContent.trim()), tree: b.querySelector('.chp-tree').getAttribute('aria-pressed'), chW: grid.style.getPropertyValue('--ch-w'), listW: w(b), split: !!grid.querySelector('.ch-split'),
          scrollW: document.documentElement.scrollWidth, vw: innerWidth }; })()`);
      await p.evalIn(`pdById('chp-files').querySelector('.chf[data-file][data-commit]').click(); 0`);
      await p.until(`!!pdById('chp-diff').querySelector('.drv .dr[data-i]')`, 15000); await sleep(200);
      out.projectDiff = await p.evalIn(`(() => { const d = pdById('chp-diff'); return { split: !!d.querySelector('.drv.split'), path: d.querySelector('.drv-path').textContent, seg: !!d.querySelector('.drv-mode'), on: pdById('chp-files').querySelector('.chf.on').dataset.file }; })()`);
      await p.shot('changes-1280-project');
      // The largest diff on main in the last week, both ways.
      if (A.big) {
        out.big = await p.evalIn(`(async () => {
          const box = document.createElement('div'); box.style.cssText = 'position:fixed;left:0;top:0;width:1200px;height:700px;overflow:auto;background:#fff'; document.body.appendChild(box);
          const rv = drReview('measure', { open: null, target: () => null });
          const run = async mode => { localStorage.setItem('cd-diff-mode', mode); const t0 = performance.now(); drShow(rv, box, 'root', 'static/dock/src/dock.js', ${JSON.stringify(A.big)}); const js = performance.now() - t0;
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); const rows = box.querySelectorAll('.drv .dr[data-i]').length; return { js: Math.round(js), painted: Math.round(performance.now() - t0), rows, split: !!box.querySelector('.drv.split'), h: box.scrollHeight }; };
          const r = { lines: ${JSON.stringify(A.big)}.split('\\n').length, unified: await run('unified'), split: await run('split') };
          box.remove(); rv.view = null; localStorage.removeItem('cd-diff-mode'); return r; })()`);
        // The whole merge as one diff (every file): well over 5,000 lines.
        out.bigAll = await p.evalIn(`(async () => {
          const box = document.createElement('div'); box.style.cssText = 'position:fixed;left:0;top:0;width:1200px;height:700px;overflow:auto;background:#fff'; document.body.appendChild(box);
          const rv = drReview('measure-all', { open: null, target: () => null });
          const run = async mode => { localStorage.setItem('cd-diff-mode', mode); const t0 = performance.now(); drShow(rv, box, 'root', 'all.diff', ${JSON.stringify(A.bigAll)}); const js = performance.now() - t0;
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); const rows = box.querySelectorAll('.drv .dr[data-i]').length; return { js: Math.round(js), painted: Math.round(performance.now() - t0), rows, split: !!box.querySelector('.drv.split') }; };
          const r = { lines: ${JSON.stringify(A.bigAll)}.split('\\n').length, unified: await run('unified'), split: await run('split') };
          box.remove(); rv.view = null; localStorage.removeItem('cd-diff-mode'); return r; })()`);
      }
      await p.close();
    }
    // ---- a phone, 390
    {
      const p = await page(390, 844, true);
      await p.evalIn(`localStorage.setItem('cd-diff-mode', 'split'); 0`);
      await open(p, A.task);
      await p.evalIn(`${tab('Changes')}.click(); 0`);
      await listed(p); await sleep(400);
      out.phoneList = await p.evalIn(LIST);
      await p.shot('changes-390-list');
      await p.evalIn(`${rowSel('src/app/main.py')}.click(); 0`);
      await diffShown(p); await sleep(300);
      out.phoneDiff = await p.evalIn(LIST);
      await p.shot('changes-390-diff');
      await p.evalIn(`${PANE}.querySelector('.drv-back').click(); 0`); await sleep(300);
      out.phoneBack = await p.evalIn(LIST);
      await p.close();
    }
  } finally {
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || e); console.error('so far: ' + JSON.stringify(out).slice(-3000)); process.exit(1); });
"""


def big_diff(*paths: str) -> str:
    """The largest diff on main in the week before this task: merge 198f817
    (2026-09-24), its static/dock/src/dock.js alone or the whole merge; ''
    where git has no such commit (a shallow or foreign checkout)."""
    if not GIT:
        return ""
    try:
        out = subprocess.run([GIT, "-C", str(ROOT), "show", "--diff-merges=first-parent", "--format=", "--no-color", "198f817", "--", *paths],
                             capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 and out.stdout.count("\n") > 1000 else ""


@unittest.skipUnless(NODE and CHROME and GIT, "needs Node, Chrome and git")
class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-chv-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        cls.patches = patches(base) + [
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in cls.patches:
            p.start()
        dashboard.invalidate_session_listing()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        # The project's folder: a checkout with one merge landed and an uncommitted edit.
        code = Path(proj["path"])
        (code / "src" / "app").mkdir(parents=True)
        put(code / "src" / "app" / "main.py", "print(1)\n")
        put(code / "README.md", "# Motors\n")
        git(code, "init", "-q", "-b", "main")
        git(code, "add", ".")
        git(code, "commit", "-q", "-m", "first")
        git(code, "checkout", "-q", "-b", "sess/ED-1-brakes")
        put(code / "src" / "app" / "main.py", "print(1)\nprint(2)\n")
        (code / "src" / "lib").mkdir()
        put(code / "src" / "lib" / "brakes.py", "PADS = 2\n")
        git(code, "add", ".")
        git(code, "commit", "-q", "-m", "brakes")
        git(code, "checkout", "-q", "main")
        git(code, "merge", "-q", "--no-ff", "-m", "Merge #1: Quiet brakes", "sess/ED-1-brakes")
        put(code / "README.md", "# Motors\n\nQuiet.\n")
        # The task: a checkout on a branch, with files in folders.
        task = loose_task("Quiet brakes")
        cwd = Path(task["cwd"])
        for x in cwd.iterdir():
            shutil.rmtree(x) if x.is_dir() else x.unlink()
        repo = temp_repo(cwd)
        for x in repo.iterdir():
            shutil.move(str(x), str(cwd / x.name))
        repo.rmdir()
        cls.task = task["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "task": cls.task, "proj": cls.proj, "shots": shots, "big": big_diff("static/dock/src/dock.js"), "bigAll": big_diff()}
        script = base / "changes_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        (base / "args.json").write_text(json.dumps(args), encoding="utf-8")
        out = subprocess.run([NODE, str(script), str(base / "args.json")], capture_output=True, encoding="utf-8", timeout=500)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        dump = os.environ.get("ENSEMBLE_DUMP", "")
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def rows(self, g):
        return [(r["dir"] or r["file"], r["name"], r["cnt"], r["d"]) for r in g["rows"]]

    def test_the_files_are_a_tree_by_folder_with_their_lines(self):
        g = self.got["tree"]
        self.assertEqual(g["tree"], "true")
        self.assertEqual(self.rows(g), [
            ("docs", "docs", "+3 −0", "0"), ("docs/notes.md", "notes.md", "+3 −0", "1"),
            ("src/app", "src/app", "+3 −1", "0"), ("src/app/main.py", "main.py", "+2 −1", "1"), ("src/app/util.py", "util.py", "+1 −0", "1"),
            ("static/dock/src", "static/dock/src", "+2 −1", "0"), ("static/dock/src/dock.js", "dock.js", "+2 −1", "1"),
            ("pic.png", "pic.png", "", "0"),
        ])
        self.assertEqual([r["pad"] for r in g["rows"]][:2], [6, 20], "a step of 14px per level")
        self.assertEqual([r["tab"] for r in g["rows"]].count(0), 1, "one row in the tab order")
        self.assertIn("5 files", g["head"], "the head counts the files")
        self.assertTrue(g["listVis"] and g["rightVis"], "both panes")
        self.assertTrue(g["stacked"] and not g["splitVis"], "a tool beside the conversation is too narrow for two panes: the files above the diff, no splitter")
        self.assertLess(g["gridW"], 680)
        self.assertEqual(g["listW"], g["gridW"] - 2, "the list the whole width")
        self.assertLessEqual(g["scrollW"], g["vw"])
        m = self.got["maxed"]
        self.assertTrue(m["splitVis"] and not m["stacked"], "the panel the whole width: two panes and the splitter")
        self.assertEqual(m["listW"], 300, "the default width")

    def test_a_folder_folds_and_the_keys_move(self):
        f = self.got["folded"]
        self.assertEqual([r["open"] for r in f["rows"] if r["dir"] == "src/app"], ["false"])
        self.assertNotIn("src/app/main.py", [r["file"] for r in f["rows"]])
        self.assertEqual(self.got["foldedFocus"], "src/app", "the folder keeps the focus")
        r = self.got["reopened"]
        self.assertIn("src/app/main.py", [x["file"] for x in r["rows"]], "→ opens it again")
        self.assertEqual(self.got["downFocus"], {"file": "src/app/main.py", "dir": "", "tab": 0}, "↓ moves to the next row and makes it the tab stop")
        e = self.got["enterFocus"]
        self.assertEqual((e["on"], e["path"]), ("src/app/main.py", "src/app/main.py"), "Enter opens the row's file")
        self.assertEqual(e["file"], "src/app/main.py", "the redrawn list keeps the keyboard on the row")
        self.assertEqual(self.got["enterDown"], "src/app/util.py", "↓ still moves on")

    def test_a_drag_down_one_column_selects_that_column_alone(self):
        s = self.got["colSel"]
        self.assertIn("sel-new", s["cls"].split())
        self.assertIn("def helper(x):", s["text"])
        self.assertIn("return x + 2", s["text"])
        self.assertNotIn("x + 1", s["text"], "the old column's line between is not selected, so Copy leaves it out")

    def test_folders_switches_to_the_flat_list_and_is_remembered(self):
        f = self.got["flat"]
        self.assertEqual(f["tree"], "false")
        self.assertEqual([r["name"] for r in f["rows"]], ["docs/notes.md", "pic.png", "src/app/main.py", "src/app/util.py", "static/dock/src/dock.js"], "the whole paths, in the hub's order")
        self.assertEqual([r["cnt"] for r in f["rows"]], ["+3 −0", "", "+2 −1", "+1 −0", "+2 −1"])
        self.assertEqual(f["stored"]["tree"], "0")
        t = self.got["treeAgain"]
        self.assertEqual(t["tree"], "true")
        self.assertEqual(t["stored"]["tree"], "1")
        self.assertEqual(self.rows(t), self.rows(self.got["tree"]))

    def test_unified_then_side_by_side_remembered(self):
        u = self.got["maxed"]
        self.assertTrue(u["drv"] and not u["split"])
        self.assertEqual(u["seg"]["on"], ["unified"])
        self.assertFalse(u["seg"]["hidden"])
        s = self.got["split"]
        self.assertTrue(s["split"])
        self.assertEqual(s["seg"]["on"], ["split"])
        self.assertEqual(s["stored"]["mode"], "split")
        self.assertTrue(s["showDiff"])
        rows = self.got["splitRows"]
        # @@ header across both; context on both sides; the removed line facing the added one; the extra added lines facing nothing.
        self.assertEqual(rows[0][0].split(":")[0], "hunk")
        self.assertEqual(len(rows[0]), 1)
        ctx = [r for r in rows if r[0].startswith("ctx:")]
        self.assertTrue(ctx and all(len(r) == 2 and r[0] == r[1] for r in ctx), ctx)
        pair = [r for r in rows if r[0].startswith("del:") and r[1].startswith("add:")]
        self.assertEqual(len(pair), 1, rows)
        self.assertTrue(pair[0][0].endswith("    return x + 1") and pair[0][1].endswith("    return x + 2"), pair)
        self.assertEqual(pair[0][0].split(":")[2].split("/")[1], "", "the old cell carries the old number only")
        alone = [r for r in rows if len(r) == 2 and r[0] == "-" and r[1].startswith("add:")]
        self.assertTrue(alone and alone[0][1].endswith("    print('squeal')"), rows)
        self.assertEqual(alone[0][1].split(":")[2].split("/")[0], "", "the new cell carries the new number only")
        g = self.got["splitGutters"]
        self.assertEqual(g["oldAfter"], "none", "the old side shows no new number")
        self.assertEqual(g["newBefore"], "none", "the new side shows no old number")
        self.assertNotEqual(g["oldBefore"], "none")
        self.assertNotEqual(g["newAfter"], "none")
        self.assertLessEqual(abs(g["oldW"] - g["newW"]), 2, "two halves")
        self.assertGreater(g["tokens"], 0, "still highlighted")

    def test_a_comment_on_a_removed_line_in_side_by_side(self):
        c = self.got["composer"]
        self.assertEqual(c["loc"], "Comment on removed line 9")
        self.assertTrue(c["afterRow"], "the box under the display row")
        self.assertEqual(len(c["sel"]), 1)
        self.assertIn("old", c["sel"][0].split(), "the removed line's cell is the selection")
        self.assertTrue(c["focused"])
        k = self.got["card"]
        self.assertEqual((k["loc"], k["note"], k["afterRow"], k["tray"], k["split"]), ("removed line 9", "Why drop this line?", True, "1 comment", True))
        self.assertEqual(k["item"], "src/app/main.py · removed line 9")
        s = self.got["sent"]
        self.assertEqual(s["body"]["roomId"], self.task)
        self.assertIn("## Review comments (1)", s["body"]["text"])
        self.assertIn("`src/app/main.py` removed line 9", s["body"]["text"])
        self.assertIn("-    return x + 1", s["body"]["text"])
        self.assertIn("Why drop this line?", s["body"]["text"])
        self.assertEqual(s["cards"], ["dc sent"])
        self.assertTrue(s["tray"], "nothing left to send")

    def test_the_splitter_resizes_remembers_and_resets(self):
        b, d, k, r = self.got["beforeDrag"], self.got["dragged"], self.got["keyed"], self.got["reset"]
        self.assertEqual(b["listW"], 300)
        self.assertEqual(d["listW"], 420, "dragged 120px")
        self.assertEqual((d["stored"]["w"], d["chW"], d["valuenow"]), ("420", "420px", "420"))
        self.assertEqual(k["listW"], 436, "→ is 16px")
        self.assertEqual(r["listW"], 300, "a double click puts the default back")
        self.assertIsNone(r["stored"]["w"])
        self.assertEqual(r["chW"], "")
        self.assertTrue(d["drv"] and k["drv"] and r["drv"], "the diff stays")

    def test_at_1280_the_panel_in_the_dock_is_narrow_and_maximised_it_is_not(self):
        u, m = self.got["unified"], self.got["maxed"]
        self.assertLess(u["gridW"], 900, "the Changes panel beside the conversation: unified")
        self.assertTrue(u["seg"]["hidden"] and not u["seg"]["vis"], "and no switch")
        self.assertGreaterEqual(m["gridW"], 900, "the panel the whole width: side by side fits")
        self.assertFalse(m["seg"]["hidden"])

    def test_a_narrow_diff_falls_back_to_unified_and_comes_back(self):
        n, w = self.got["narrow"], self.got["wideAgain"]
        self.assertLess(n["gridW"], 900)
        self.assertFalse(n["split"], "unified under 900px, whatever was chosen")
        self.assertTrue(n["stacked"] and n["listVis"] and n["rightVis"] and not n["splitVis"], "and under 680px the files above the diff")
        self.assertTrue(n["seg"]["hidden"] and not n["seg"]["vis"], "the switch is away while side by side cannot fit")
        self.assertEqual(n["stored"]["mode"], "split", "the choice is kept")
        self.assertTrue(w["split"] and not w["seg"]["hidden"], "side by side again once it fits")

    def test_the_project_s_changes_group_by_folder_and_share_the_width(self):
        g = self.got["project"]
        self.assertEqual(g["tree"], "true")
        self.assertTrue(g["secs"][0].startswith("Uncommitted "), g["secs"])
        self.assertTrue(g["secs"][1].startswith("Landed on main"))
        rows = [(r["dir"] or r["file"], r["name"], r["cnt"], r["d"], bool(r["sha"])) for r in g["rows"]]
        self.assertIn(("README.md", "README.md", "+2 −0", "0", False), rows, "the uncommitted edit, flat at the root")
        merge = [r for r in rows if r[4]][:5]
        # The merge's files: src holds two folders, so it is a row of its own; each folder's files a step deeper.
        self.assertEqual(merge, [("src", "src", "+2 −0", "0", True), ("src/app", "app", "+1 −0", "1", True), ("src/app/main.py", "main.py", "+1 −0", "2", True),
                                 ("src/lib", "lib", "+1 −0", "1", True), ("src/lib/brakes.py", "brakes.py", "+1 −0", "2", True)])
        self.assertEqual((g["chW"], g["listW"]), ("360px", 360), "the width set in the task's Changes")
        self.assertTrue(g["split"])
        self.assertLessEqual(g["scrollW"], g["vw"])
        d = self.got["projectDiff"]
        self.assertTrue(d["split"] and d["seg"], "side by side here too")
        self.assertEqual(d["on"], d["path"])

    def test_the_phone_shows_one_pane_at_a_time(self):
        l, d, b = self.got["phoneList"], self.got["phoneDiff"], self.got["phoneBack"]
        self.assertTrue(l["listVis"] and not l["rightVis"] and not l["splitVis"], "the files alone")
        self.assertGreater(l["listW"], 300, "the whole width, not a stacked row")
        self.assertFalse(l["showDiff"])
        self.assertEqual([r["name"] for r in l["rows"]][:2], ["docs", "notes.md"], "the same tree")
        self.assertTrue(d["rightVis"] and not d["listVis"], "the diff alone")
        self.assertTrue(d["showDiff"] and d["drv"] and d["back"], "with its way back")
        self.assertFalse(d["split"], "unified on a phone, although side by side was chosen")
        self.assertTrue(d["seg"]["hidden"] and not d["seg"]["vis"], "no switch on a phone")
        self.assertEqual(d["stored"]["mode"], "split")
        self.assertTrue(b["listVis"] and not b["rightVis"] and not b["showDiff"], "‹ Files goes back")
        for g in (l, d, b):
            self.assertLessEqual(g["scrollW"], g["vw"], "nothing scrolls sideways")

    def test_the_largest_diff_of_the_week_renders_fast_both_ways(self):
        big = self.got.get("big")
        if not big:
            self.skipTest("merge 198f817 is not in this checkout")
        print(f"\nlargest diff on main (198f817 static/dock/src/dock.js): {big['lines']} lines, {big['unified']['rows']} rows; "
              f"unified {big['unified']['js']} ms (painted {big['unified']['painted']} ms), side by side {big['split']['js']} ms (painted {big['split']['painted']} ms)")
        self.assertTrue(big["split"]["split"] and not big["unified"]["split"])
        self.assertGreater(big["unified"]["rows"], 1000)
        self.assertLess(big["unified"]["painted"], 1500)
        self.assertLess(big["split"]["painted"], 1500)
        a = self.got["bigAll"]
        print(f"the whole merge as one diff: {a['lines']} lines, {a['unified']['rows']} rows; unified {a['unified']['js']} ms (painted {a['unified']['painted']} ms), "
              f"side by side {a['split']['js']} ms (painted {a['split']['painted']} ms)")
        self.assertGreater(a["unified"]["rows"], 5000, "over 5,000 lines")
        self.assertLess(a["unified"]["painted"], 2000)
        self.assertLess(a["split"]["painted"], 2000)


if __name__ == "__main__":
    unittest.main()
