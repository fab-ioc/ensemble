"""Layout A step 4 (#306): on a computer the left task list is not shown by
default; its width goes to the conversation and the Inbox. In headless Chrome
over CDP, against a hub in a thread serving the pages.

Three projects: Motors (a PO with an answer to check, so one Inbox row),
Wheels (a PO) and Alpha (none), and a task in no project. Checked:

* at 1728x1117, 1440x900, 1280x800, 1024x768 and 800x600 a fresh browser
  shows no list (the list dock holds it hidden, --list-w is 0), the
  conversation is at least 360px wide, the Inbox is a column or folded to its
  strip, and nothing is wider than the screen;
* routes: the Inbox button, the wordmark and #needs open the Inbox on a PO
  screen; the switcher opens another project's PO; Unassigned (the tasks in no
  project) is in the switcher and its page lists that task; the empty middle
  points at the bar and the Inbox, not at a list that is not there;
* the list is still a tool: Panels ▾ › Tasks shows it, docked at the left;
* a layout saved before this step (the list docked) opens once in the new
  default; the same layout saved after it keeps the list; a list popped out
  into its window opens again in its window;
* a phone (390x844) still has its list as home;
* the page threw nothing meanwhile.

Measures go to $ENSEMBLE_SHOTS/desktop-layout.json and screenshots there too
when it is set. Skipped without Node or Chrome.
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
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
WIDTHS = [(1728, 1117), (1440, 900), (1280, 800), (1024, 768), (800, 600)]

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// Where things are on the screen.
const GEOM = `(() => {
  const R = e => { if (!e || !e.isConnected || e.ownerDocument !== document) return null; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { x: Math.round(r.left), r: Math.round(r.right), w: Math.round(r.width), h: Math.round(r.height) } : null; };
  const d = LD.dock;
  const wide = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('[hidden], .dk-off'); }).length;
  return { W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth, wide,
    ld: !!d, listOn: !!d && d.isVisible('list'), listMode: d ? d.viewMode('list') : '', list: R(SW_EL), listOut: SW_EL.ownerDocument !== document,
    listW: getComputedStyle(document.body).getPropertyValue('--list-w').trim(),
    po: document.body.classList.contains('po-dock'), pid: (PD && PD.pid) || '', proj: SELECTED_PROJECT,
    chat: R(PD.els['po-chat']), inbox: R(PD.els.inbox), inboxMode: PD.dock ? PD.dock.viewMode('inbox') : '',
    inboxStrip: !!document.querySelector('#po-dock .dk-strip-btn[data-dk-auto="inbox"]'),
    flag: (() => { try { return localStorage.getItem('cd-list-dock-a4'); } catch (e) { return 'x'; } })(),
    stored: (() => { try { return localStorage.getItem('cd-list-dock'); } catch (e) { return null; } })(),
    phoneList: R(document.getElementById('switcher')) };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  // A page; seed: localStorage keys set (a string) or removed (null) before the page's first script, once.
  const page = async (w, h, mobile, seed) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const s = Object.assign({ 'cd-list-dock': null, 'cd-list-dock-a4': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-phone-tabs': null, 'cd-last-conv': null }, seed || {});
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 2 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, shot, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = async (p, pid) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock && PD.pid === ' + JSON.stringify(pid), 30000);
    await sleep(500);
  };
  const P = A.ids;
  try {
    // ---- each computer width, a fresh browser
    out.widths = {};
    for (const [w, h] of A.widths) {
      const p = await page(w, h, false);
      await toPo(p, P.Motors);
      out.widths[w] = await p.evalIn(GEOM);
      await p.shot(`desktop-${w}x${h}`);
      await p.close();
    }
    // ---- routes, at 1440
    const p = await page(1440, 900, false);
    await p.until('!!LD.dock', 30000);
    await sleep(300);
    out.empty = await p.evalIn(`({ text: (document.querySelector('.home-empty .empty') || { textContent: '' }).textContent, po: document.body.classList.contains('po-dock') })`);
    await p.click('document.getElementById("inbox-btn")');
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.isShown("inbox")', 15000).catch(() => null);
    await sleep(400);
    out.inboxBtn = await p.evalIn(GEOM);
    out.inboxShown = await p.evalIn('!!PD.dock && PD.dock.isShown("inbox")');
    // An Inbox row opens its conversation (Motors' PO, the answer).
    out.inboxRows = await p.evalIn(`PD.els.inbox.querySelectorAll('[data-ibx-key], .ibx-row').length`);
    // The switcher: another project's PO.
    await p.click('document.getElementById("proj-switch")'); await sleep(200);
    await p.click(`document.querySelector('#proj-menu .pm-any[data-proj="${P.Wheels}"]')`);
    await p.until('document.body.classList.contains("po-dock") && PD.pid === ' + JSON.stringify(P.Wheels), 15000).catch(() => null);
    await sleep(300);
    out.switchPo = await p.evalIn(GEOM);
    // The switcher: Unassigned, the tasks in no project.
    await p.click('document.getElementById("proj-switch")'); await sleep(200);
    out.unassignedItem = await p.evalIn(`!!document.querySelector('#proj-menu .pm-any[data-proj="__unassigned__"]')`);
    if (out.unassignedItem) {
      await p.click(`document.querySelector('#proj-menu .pm-any[data-proj="__unassigned__"]')`); await sleep(600);
      out.unassigned = await p.evalIn(`({ proj: SELECTED_PROJECT, text: (document.getElementById('main') || document.body).innerText.includes(${JSON.stringify(A.looseTitle)}) || document.body.innerText.includes(${JSON.stringify(A.looseTitle)}) })`);
    } else { await p.evalIn('closeProjMenu(); 0'); }
    // The wordmark: home is the Inbox.
    await p.evalIn('midLeave(); SELECTED_PROJECT = null; renderRows(); 0'); await sleep(300);
    await p.click('document.getElementById("bar-home")');
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.isShown("inbox")', 15000).catch(() => null);
    await sleep(300);
    out.home = await p.evalIn(GEOM);
    out.homeInbox = await p.evalIn('!!PD.dock && PD.dock.isShown("inbox")');
    // #needs: the Inbox too.
    await p.evalIn('midLeave(); SELECTED_PROJECT = null; renderRows(); location.hash = "#needs"; 0');
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.isShown("inbox")', 15000).catch(() => null);
    out.needsHash = await p.evalIn('({ po: document.body.classList.contains("po-dock"), inbox: !!PD.dock && PD.dock.isShown("inbox"), hash: location.hash })');
    // Panels ▾ › Tasks: the list, docked at the left.
    await p.click('document.querySelector("#bar-here .pd-panels")'); await sleep(250);
    out.panelsItem = await p.evalIn(`(() => { const b = document.querySelector('[data-pd-list]'); return b ? { checked: !!(b.checked || b.getAttribute('aria-checked') === 'true'), text: (b.closest('label') || b).textContent.trim() } : null; })()`);
    await p.click('document.querySelector("[data-pd-list]")'); await sleep(600);
    out.listShown = await p.evalIn(GEOM);
    await p.evalIn('document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); 0');
    await p.shot('desktop-1440x900-list-shown');
    const docked = out.listShown.stored;
    // Popped out into a window.
    await p.evalIn('LD.dock.popOut("list"); 0');
    await p.until('!!LD.dock.popWindow("list") && SW_EL.ownerDocument !== document', 15000).catch(() => null);
    await sleep(500);
    out.popped = await p.evalIn(GEOM);
    const poppedLayout = out.popped.stored;
    await p.evalIn('LD.dock.popIn("list"); 0'); await sleep(400);
    await p.close();
    // ---- a layout saved before this step: the list docked, no flag
    const o = await page(1440, 900, false, { 'cd-list-dock': docked });
    await toPo(o, P.Motors);
    out.oldLayout = await o.evalIn(GEOM);
    // Reloaded: it stays as the person leaves it (hidden), the flag kept.
    await o.evalIn('LD.dock.setVisible("list", true); 0'); await sleep(300);
    await o.evalIn('location.reload(); 0'); await sleep(1500);
    await o.until('window.ensBooted === true && !!LD.dock', 30000);
    await toPo(o, P.Motors);
    out.oldReloaded = await o.evalIn(GEOM);
    await o.close();
    // ---- the same layout saved after it (the flag set): the list stays
    const n = await page(1440, 900, false, { 'cd-list-dock': docked, 'cd-list-dock-a4': '1' });
    await toPo(n, P.Motors);
    out.newLayout = await n.evalIn(GEOM);
    await n.close();
    // ---- the list popped out, before and after this step
    for (const [k, flag] of [['outOld', null], ['outNew', '1']]) {
      const q = await page(1440, 900, false, { 'cd-list-dock': poppedLayout, 'cd-list-dock-a4': flag });
      await toPo(q, P.Motors);
      await q.until('LD.dock.viewMode("list") === "window" && (!!LD.dock.popWindow("list") || LD.dock.isOut("list"))', 15000).catch(() => null);
      await sleep(600);
      out[k] = await q.evalIn(GEOM);
      await q.evalIn('try { const w = LD.dock.popWindow("list"); if (w) LD.dock.popIn("list"); } catch (e) {} 0'); await sleep(300);
      await q.close();
    }
    // ---- a phone: its list is home, as before
    const ph = await page(390, 844, true);
    await sleep(800);
    out.phone = await ph.evalIn(GEOM);
    await ph.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'desktop-layout.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class DesktopLayoutInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-dl-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
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
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        cls.ids = {}
        for name in ("Motors", "Wheels", "Alpha"):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
            if name in ("Motors", "Wheels"):
                home = Path(proj.get("home") or proj["path"])
                po = chatroom.create_room(f"{name} PO", members(home))
                dashboard.assign_session_project(po["id"], proj["id"])
                ok, why = dashboard.set_project_po(proj["id"], po["id"])
                assert ok, why
                if name == "Motors":
                    cls.po = po["id"]
                    text, ids = points.take(chatroom.get_room(po["id"], public=False), "Wider board", to="claude", key="k1")
                    assert ids == ["P1"], ids
                    chatroom.post_message(po["id"], "user", text, to="claude")
                    chatroom.post_message(po["id"], "claude", "Re P1: it is wider now.", to="user")
                    points.sync(po["id"], force=True)
        cls.loose_title = "Loose carriage inspection"
        chatroom.create_room(cls.loose_title, members(base))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "po": cls.po, "looseTitle": cls.loose_title, "widths": WIDTHS, "shots": shots}
        script = base / "desktop_layout_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=420)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_no_list_by_default_at_every_computer_width(self):
        for w, _ in WIDTHS:
            g = self.got["widths"][str(w)]
            with self.subTest(w=w):
                self.assertTrue(g["ld"], "the list dock is there")
                self.assertFalse(g["listOn"], g)
                self.assertEqual(g["listMode"], "hidden")
                self.assertIsNone(g["list"], "nothing of the list on the screen")
                self.assertEqual(g["listW"], "0px")

    def test_the_conversation_takes_the_width(self):
        for w, _ in WIDTHS:
            g = self.got["widths"][str(w)]
            with self.subTest(w=w):
                self.assertTrue(g["po"])
                self.assertIsNotNone(g["chat"], g)
                self.assertGreaterEqual(g["chat"]["w"], 360)
                # the Inbox: a column or folded to its strip
                self.assertTrue(g["inbox"] is not None or g["inboxStrip"], g)
                self.assertLessEqual(g["scrollW"], g["W"])
                self.assertEqual(g["wide"], 0, "nothing wider than the screen")
                # the conversation starts at the left edge or right of the Inbox column, not 300px in
                left = g["inbox"]["r"] if g["inbox"] else 0
                self.assertLessEqual(g["chat"]["x"] - left, 50, g)

    def test_the_empty_middle_points_at_the_bar_and_the_inbox(self):
        t = self.got["empty"]["text"]
        if t:   # the page opened nothing yet
            self.assertNotIn("in the list", t)
            self.assertIn("Inbox", t)

    def test_the_inbox_button_opens_the_inbox(self):
        g = self.got["inboxBtn"]
        self.assertTrue(g["po"])
        self.assertEqual(g["pid"], self.ids["Motors"], "the project whose PO has something waiting")
        self.assertTrue(self.got["inboxShown"])

    def test_the_switcher_opens_another_po(self):
        g = self.got["switchPo"]
        self.assertTrue(g["po"])
        self.assertEqual(g["pid"], self.ids["Wheels"])
        self.assertFalse(g["listOn"])

    def test_tasks_in_no_project_are_in_the_switcher(self):
        self.assertTrue(self.got["unassignedItem"], "Unassigned is in the switcher")
        self.assertEqual(self.got["unassigned"], {"proj": "__unassigned__", "text": True})

    def test_the_wordmark_and_needs_open_the_inbox(self):
        self.assertTrue(self.got["home"]["po"])
        self.assertTrue(self.got["homeInbox"])
        self.assertTrue(self.got["needsHash"]["po"] and self.got["needsHash"]["inbox"], self.got["needsHash"])
        self.assertFalse(self.got["home"]["listOn"])

    def test_panels_can_open_the_list(self):
        self.assertIsNotNone(self.got["panelsItem"])
        self.assertFalse(self.got["panelsItem"]["checked"])
        g = self.got["listShown"]
        self.assertTrue(g["listOn"])
        self.assertEqual(g["listMode"], "pinned")
        self.assertIsNotNone(g["list"])
        self.assertLessEqual(g["list"]["x"], 1)
        self.assertGreaterEqual(int(g["listW"].rstrip("px")), 220)
        self.assertGreaterEqual(g["chat"]["w"], 360)
        self.assertLessEqual(g["scrollW"], g["W"])
        p = self.got["popped"]
        self.assertTrue(p["listOut"] and p["listMode"] == "window", p)
        self.assertEqual(p["listW"], "0px")

    def test_a_layout_saved_before_opens_in_the_new_default_once(self):
        g = self.got["oldLayout"]
        self.assertFalse(g["listOn"], g)
        self.assertEqual(g["flag"], "1")
        self.assertEqual(g["listW"], "0px")
        r = self.got["oldReloaded"]
        self.assertTrue(r["listOn"], "once: shown again, it stays")
        self.assertEqual(r["listMode"], "pinned")

    def test_a_layout_saved_after_keeps_the_list(self):
        g = self.got["newLayout"]
        self.assertTrue(g["listOn"])
        self.assertEqual(g["listMode"], "pinned")
        self.assertIsNotNone(g["list"])
        self.assertGreaterEqual(g["chat"]["w"], 360)

    def test_a_popped_out_list_opens_in_its_window(self):
        for k in ("outOld", "outNew"):
            g = self.got[k]
            with self.subTest(k=k):
                self.assertEqual(g["listMode"], "window", g)
                self.assertIsNone(g["list"], "not on the page")
                self.assertGreaterEqual(g["chat"]["w"], 360)
                self.assertLessEqual(g["scrollW"], g["W"])

    def test_a_phone_keeps_its_list(self):
        g = self.got["phone"]
        self.assertFalse(g["ld"], "no list dock on a phone")
        self.assertIsNotNone(g["phoneList"], g)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
