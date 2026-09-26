"""The top bar (index.html, <header>; #107): one bar at every width.

Checked in headless Chrome over CDP, against a hub in a thread serving the
pages, with a project that has a PO (Motors) and one that has none (Plain):

* left to right: where you are (home, the project's name and its menu), what
  you can do here (a PO screen's Panels), then search, the PO, the bell, the
  avatar and Create;
* a project page has no status row and no crumbs row; the project's kind and
  key are in its menu; a project without a PO keeps one row of tabs;
* the Workspace and Changes of a project without a PO fit the screen, with no
  page scroll;
* on a phone, home is one row; a project is two: search, PO, bell, avatar and
  Create, then back, the name and Panels; the PO screen's tabs start within
  130px of the top, and nothing is wider than the screen;
* on a phone the Workspace shows one pane at a time: a file open shows the
  file; Files shows the tree and Find, and the file's name there goes back to
  it; the file's viewer is hidden with its pane, and under another panel's
  tab; on a laptop both panes show and neither switch does;
* at 768 (a fine pointer) the bar is the laptop's, in one row.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
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
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")

CDP_JS = r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = fs.mkdtempSync(path.join(A.tmp, 'chrome-'));
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// What the bar shows, left to right, and where.
const BAR = `(() => {
  const ids = ['bar-back', 'bar-home', 'proj-switch', 'bar-here', 'search', 'search-open', 'po-pill', 'notif-btn', 'me-btn', 'new-btn'];
  const on = ids.map(id => document.getElementById(id)).filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== 'hidden'; });
  const at = e => { const b = e.getBoundingClientRect(); return { id: e.id, x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) }; };
  const h = document.querySelector('header').getBoundingClientRect();
  return { items: on.map(at), header: Math.round(h.height), crumbs: !!document.querySelector('.crumbs'), statusbar: !!document.querySelector('.statusbar'),
    ptabs: !!document.querySelector('.ptabs'), panels: !!document.querySelector('#bar-here .pd-panels'), here: document.getElementById('bar-here').innerHTML.trim() !== '',
    name: document.getElementById('proj-switch-name').textContent, scrollW: document.documentElement.scrollWidth, vw: innerWidth };
})()`;
const PANE = `(() => { const p = [...document.querySelectorAll('.wsp')].find(e => e.getBoundingClientRect().height); if (!p) return null;
  const vis = s => { const e = p.querySelector(s); return !!e && getComputedStyle(e).visibility === 'visible' && e.getBoundingClientRect().height > 0; };
  const shown = s => { const e = p.querySelector(s); return !!e && e.getBoundingClientRect().width > 0; };
  const f = p.querySelector('.wsp-frame.on');
  return { side: vis('.wsp-side'), view: vis('.wsp-view'), files: shown('.wsp-files'), tofile: shown('.wsp-tofile'),
    frame: f ? getComputedStyle(f).visibility : null,
    tofileText: (p.querySelector('.wsp-tofile') || {}).textContent || '' }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
    return { evalIn, until, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = (p, proj, tab) => p.evalIn(`(() => { try { localStorage.removeItem('cd-po-dock'); localStorage.removeItem('cd-po-dock-phone'); } catch (e) {}
    SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = ${JSON.stringify(tab || 'tasks')}; SB_DEST = ''; renderRows(); return 0; })()`);
  const poReady = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#bar-here .pd-panels") && [...document.querySelectorAll(".dk-head")].some(e => e.getBoundingClientRect().height)', 30000);
  const openDoc = async (p) => {
    await p.evalIn("pdReveal('workspace'); 0");
    await p.until('[...PD.els.workspace.querySelectorAll(".wse.doc[data-path]")].some(x => x.dataset.path.endsWith("#1 Notes.md"))', 20000);
    await p.evalIn('[...PD.els.workspace.querySelectorAll(".wse.doc[data-path]")].find(x => x.dataset.path.endsWith("#1 Notes.md")).click(); 0');
    await p.until('!!PD.els.workspace.querySelector(".wst-tab.on")', 15000);
    await sleep(300);
  };
  try {
    // ---- a laptop
    const p = await page(1440, 900);
    await p.until('!!document.querySelector("header")');
    out.home = await p.evalIn(BAR);
    await go(p, A.proj); await poReady(p); await sleep(400);
    out.po = await p.evalIn(BAR);
    await p.shot('top-bar-1440-po');
    await p.evalIn('document.getElementById("proj-switch").click(); 0');
    await p.until('!document.getElementById("proj-menu").hidden');
    out.menu = await p.evalIn('[...document.querySelectorAll("#proj-menu .pm-set")].map(b => b.className.replace("pm-item pm-set ", ""))');
    await p.evalIn('closeProjMenu(); 0');
    await openDoc(p);
    out.wideWs = await p.evalIn(PANE);
    for (const tab of ['workspace', 'changes']) {
      await go(p, A.plain, tab);
      await p.until('!!document.querySelector(".wsp, .chp") && [...document.querySelectorAll(".wsp, .chp")].some(e => e.getBoundingClientRect().height)', 20000);
      await sleep(400);
      out['plain_' + tab] = await p.evalIn(`(() => { const b = [...document.querySelectorAll('.wsp, .chp')].find(e => e.getBoundingClientRect().height).getBoundingClientRect();
        return { ...${BAR}, bottom: Math.round(b.bottom), vh: innerHeight, docH: document.documentElement.scrollHeight }; })()`);
      await p.shot('top-bar-1440-plain-' + tab);
    }
    await p.close();
    // ---- a tablet's width, with a fine pointer
    const t = await page(768, 1024);
    await go(t, A.proj); await poReady(t); await sleep(400);
    out.po768 = await t.evalIn(BAR);
    await t.close();
    // ---- a phone
    const q = await page(430, 932, true);
    await q.until('!!document.querySelector("header")');
    out.phoneHome = await q.evalIn(BAR);
    await go(q, A.proj); await poReady(q); await sleep(400);
    out.phonePo = await q.evalIn(`({ ...${BAR}, tabsTop: Math.round(Math.min(...[...document.querySelectorAll('.dk-head')].map(e => e.getBoundingClientRect()).filter(b => b.height).map(b => b.top))) })`);
    await q.shot('top-bar-430-po');
    await openDoc(q);
    out.phoneFile = await q.evalIn(PANE);
    await q.evalIn('[...document.querySelectorAll(".wsp-files")].find(e => e.getBoundingClientRect().width).click(); 0');
    await sleep(200);
    out.phoneTree = await q.evalIn(PANE);
    await q.shot('top-bar-430-tree');
    await q.evalIn('[...document.querySelectorAll(".wsp-tofile")].find(e => e.getBoundingClientRect().width).click(); 0');
    await sleep(200);
    out.phoneBack = await q.evalIn(PANE);
    // Another panel's tab: the Workspace's file does not show through it.
    await q.evalIn("pdReveal('po-chat'); 0");
    await sleep(300);
    out.phoneChat = await q.evalIn(`(() => { const f = PD.els.workspace.querySelector('.wsp-frame.on'); return f ? getComputedStyle(f).visibility : null; })()`);
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheTopBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-bar-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", cls.root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        (home / "ROADMAP.md").write_text("# Motors roadmap\n", encoding="utf-8")
        (home / "Documents").mkdir(exist_ok=True)
        (home / "Documents" / "#1 Notes.md").write_text("# Notes\n\nA document.\n", encoding="utf-8")
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                   {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        ok, plain, _ = dashboard.register_project("Plain")
        assert ok, plain
        cls.plain = plain["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {"chrome": CHROME, "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "plain": cls.plain, "shots": shots}
        script = base / "top_bar_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def order(self, bar):
        return [i["id"] for i in sorted(bar["items"], key=lambda i: (i["y"] // 20, i["x"]))]

    def test_left_to_right_where_you_are_what_you_can_do_then_the_rest(self):
        self.assertEqual(self.order(self.got["po"]),
                         ["bar-home", "proj-switch", "bar-here", "search", "po-pill", "notif-btn", "me-btn", "new-btn"])
        self.assertEqual(self.got["po"]["name"], "Motors")
        self.assertLessEqual(self.got["po"]["header"], 50, "one row on a laptop")
        self.assertEqual(self.order(self.got["po768"]), self.order(self.got["po"]), "the same at 768")
        self.assertLessEqual(self.got["po768"]["header"], 50)
        self.assertLessEqual(self.got["po768"]["scrollW"], self.got["po768"]["vw"])

    def test_home_has_nothing_here(self):
        home = self.got["home"]
        self.assertFalse(home["here"])
        self.assertNotIn("bar-back", [i["id"] for i in home["items"]])

    def test_a_project_page_has_no_status_row_and_no_crumbs(self):
        for k in ("po", "plain_workspace", "plain_changes", "phonePo"):
            self.assertFalse(self.got[k]["crumbs"], k)
            self.assertFalse(self.got[k]["statusbar"], k)

    def test_a_po_screen_has_panels_in_the_bar_and_no_tab_row(self):
        self.assertTrue(self.got["po"]["panels"])
        self.assertFalse(self.got["po"]["ptabs"])

    def test_the_projects_settings_are_in_its_menu(self):
        self.assertIn("kind-btn", self.got["menu"])
        self.assertIn("key-btn", self.got["menu"])

    def test_a_project_without_a_po_keeps_one_tab_row_and_fits_the_screen(self):
        for tab in ("workspace", "changes"):
            g = self.got["plain_" + tab]
            self.assertTrue(g["ptabs"], tab)
            self.assertFalse(g["here"], tab)
            self.assertLessEqual(g["bottom"], g["vh"], tab)
            self.assertLessEqual(g["docH"], g["vh"], f"{tab}: no page scroll")

    def test_home_on_a_phone_is_one_row(self):
        h = self.got["phoneHome"]
        self.assertLess(h["header"], 60)
        self.assertLessEqual(h["scrollW"], h["vw"])

    def test_a_project_on_a_phone_is_two_rows_and_the_panels_start_high(self):
        g = self.got["phonePo"]
        at = {i["id"]: i for i in g["items"]}
        row1 = {k for k in ("search-open", "po-pill", "notif-btn", "me-btn", "new-btn") if k in at}
        self.assertEqual(row1, {"search-open", "po-pill", "notif-btn", "me-btn", "new-btn"})
        for k in ("bar-back", "proj-switch", "bar-here"):
            self.assertIn(k, at, k)
            self.assertGreater(at[k]["y"], at["new-btn"]["y"] + 30, f"{k} is on the second row")
            self.assertGreaterEqual(at[k]["h"], 44, f"{k} is finger-sized")
        self.assertGreater(at["proj-switch"]["w"], 44, "the name shows")
        self.assertLessEqual(g["tabsTop"], 130)
        self.assertLessEqual(g["scrollW"], g["vw"])

    def test_the_workspace_on_a_phone_is_one_pane_at_a_time(self):
        f, t, b = self.got["phoneFile"], self.got["phoneTree"], self.got["phoneBack"]
        self.assertEqual((f["side"], f["view"], f["files"], f["frame"]), (False, True, True, "visible"), "a file open: the file")
        self.assertEqual((t["side"], t["view"], t["tofile"], t["frame"]), (True, False, True, "hidden"), "Files: the tree and Find, the file's viewer hidden too")
        self.assertIn("#1 Notes.md", t["tofileText"])
        self.assertEqual((b["side"], b["view"], b["frame"]), (False, True, "visible"), "the file's name goes back to it")
        self.assertEqual(self.got["phoneChat"], "hidden", "another panel's tab hides the Workspace's file")

    def test_the_workspace_on_a_laptop_shows_both_panes_and_no_switch(self):
        w = self.got["wideWs"]
        self.assertEqual((w["side"], w["view"], w["files"], w["tofile"], w["frame"]), (True, True, False, False, "visible"))


if __name__ == "__main__":
    unittest.main()
