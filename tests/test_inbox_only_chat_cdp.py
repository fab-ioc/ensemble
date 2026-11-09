"""Inbox-only step 2 (#311): the PO chat drops Open questions, Needs you and
the threads bar; a phone gets Chat | Inbox tabs. In headless Chrome over CDP,
against a hub in a thread serving the pages.

One project, Motors, whose PO has answered a point (one Inbox row to check).
Checked:

* at 1440x900 and 1728x1117 the PO chat has no Open questions box, no points
  line, no catch-up line and no threads bar; Needs you is not a panel nor on
  the tool strip; the messages start high and get the height (#309 measures:
  top 157, height 609 at 1440x900, 826 at 1728x1117);
* at 390x844 (a phone) the bar is one row (logo, project, search, avatar,
  Create), the tabs start Chat | Inbox, the chat shows one strip naming what
  waits ("1 to check") and its Inbox › brings the Inbox tab forward; the
  messages get the height (#309: 586);
* nothing is wider than the screen at 360, 390, 440, 800, 1440 and 1728
  (the tab row scrolls, with its overflow menu, as Dock draws it);
* the page threw nothing meanwhile.

Measures go to $ENSEMBLE_SHOTS/inbox-only-chat.json and screenshots there too
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
DESKTOP = [(1440, 900), (1728, 1117), (800, 600)]
PHONES = [(390, 844), (360, 780), (440, 900)]

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
// What is on the screen: the page's bar and tabs, the chat frame's parts.
const GEOM = `(() => {
  const R = e => { if (!e) return null; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } : null; };
  const f = pdChatFrame(), d = f.contentDocument, fr = f.getBoundingClientRect();
  const inF = e => { const r = R(e); return r && { ...r, y: r.y + Math.round(fr.top) }; };
  const shown = e => !!e && !e.hidden && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().height > 0;
  const W = document.documentElement.clientWidth;
  const wide = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > W + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('[hidden], .dk-off, .dk-flyout, .dk-parking, .dk-head, dialog'); }).map(e => e.tagName + '.' + e.className).slice(0, 5);
  const fW = d.documentElement.clientWidth;
  const fWide = [...d.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > fW + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('[hidden]'); }).map(e => e.tagName + '.' + e.className).slice(0, 5);
  const bar = ['bar-home', 'proj-switch', 'bar-back', 'bar-here', 'search-open', 'me-btn', 'new-btn'].map(id => [id, R(document.getElementById(id))]).filter(x => x[1]);
  const strip = d.getElementById('inbox-strip');
  return { W, scrollW: document.documentElement.scrollWidth, wide, fWide, fScrollW: d.documentElement.scrollWidth, fW,
    phone: !!PD.dock && PD.dock.narrow(), header: R(document.querySelector('header')), bar: Object.fromEntries(bar),
    tabs: [...document.querySelectorAll('#po-dock .dk-tab')].filter(t => t.offsetWidth).map(t => (t.querySelector('.dk-tab-t, .dk-title') || t).textContent.trim()),
    tabIds: [...document.querySelectorAll('#po-dock .dk-tab')].filter(t => t.offsetWidth).map(t => t.dataset.dkTab),
    ids: PD_IDS.slice(), toolStrip: [...document.querySelectorAll('#po-dock .dk-strip-btn')].map(b => b.dataset.dkAuto),
    leftStrip: [...document.querySelectorAll('#list-dock .dk-strip-btn')].map(b => b.dataset.dkAuto),
    msgs: inF(d.getElementById('msgs')),
    gone: ['open-questions', 'points-line', 'thread-catchup'].filter(id => !!d.getElementById(id)),
    chatbar: shown(d.getElementById('chatbar')), strip: shown(strip), stripText: strip ? strip.textContent.replace(/\s+/g, ' ').trim() : '',
    stripBox: strip && shown(strip) ? inF(strip) : null, front: PD_IDS.filter(id => PD.dock.frontOf(id) === id) };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const tap = async (x, y) => {
      if (mobile) { for (const type of ['touchStart', 'touchEnd']) await c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] }, sessionId); }
      else for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-last-conv", "cd-list-dock", "cd-list-dock-a4"].forEach(k => localStorage.removeItem(k)); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    await evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!PD.dock && (() => { const f = pdChatFrame(); return !!(f && f.contentWindow && f.contentWindow.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN")); })()', 30000);
    // The Inbox's counts reach the chat frame.
    await until('(() => { const f = pdChatFrame(); return !!f && /"check":1/.test(f.getAttribute("data-inbox-wait") || ""); })()', 20000);
    await sleep(600);
    return { evalIn, until, shot, tap, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  try {
    out.desktop = {};
    for (const [w, h] of A.desktop) {
      const p = await page(w, h, false);
      out.desktop[w] = await p.evalIn(GEOM);
      await p.shot(`inbox-only-${w}x${h}`);
      await p.close();
    }
    out.phones = {};
    for (const [w, h] of A.phones) {
      const p = await page(w, h, true);
      await p.until('PD.dock.narrow()', 10000); await sleep(300);
      out.phones[w] = await p.evalIn(GEOM);
      await p.shot(`inbox-only-${w}x${h}`);
      if (w === 390) {
        // Inbox ›, tapped: the Inbox tab comes forward.
        const [x, y] = await p.evalIn(`(() => { const f = pdChatFrame(), a = f.contentDocument.querySelector('#inbox-strip .is-go'), r = a.getBoundingClientRect(), fr = f.getBoundingClientRect(); return [fr.left + r.left + r.width / 2, fr.top + r.top + r.height / 2]; })()`);
        await p.tap(x, y);
        await p.until('PD.dock.frontOf("inbox") === "inbox"', 5000).catch(() => null);
        await sleep(300);
        out.tapInbox = await p.evalIn('({ front: PD.dock.frontOf("inbox"), rows: PD.els.inbox.querySelectorAll("[data-ibx-key], .ibx-row").length, scrollW: document.documentElement.scrollWidth - innerWidth })');
        await p.shot('inbox-only-390-inbox-tab');
      }
      await p.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'inbox-only-chat.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class InboxOnlyChatInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-io-", ignore_cleanup_errors=True)
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
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                {"identity": "codex", "agent": "codex", "cwd": str(home)}])
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        cls.po = po["id"]
        text, ids = points.take(chatroom.get_room(po["id"], public=False), "Wider board", to="claude", key="k1")
        assert ids == ["P1"], ids
        chatroom.post_message(po["id"], "user", text, to="claude")
        chatroom.post_message(po["id"], "claude", "Re P1: it is wider now.", to="user")
        for i in range(30):   # enough talk that the messages fill their box
            chatroom.post_message(po["id"], "claude" if i % 2 else "user", f"Status note {i}: the listing is drafted and the buyer asked about Saturday.",
                                  to="user" if i % 2 else "claude")
        points.sync(po["id"], force=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "po": cls.po, "desktop": DESKTOP, "phones": PHONES, "shots": shots}
        script = base / "inbox_only_chat_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=420)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_chat_has_no_open_questions_points_line_or_threads_bar(self):
        for k, g in [*(("d" + str(w), self.got["desktop"][str(w)]) for w, _ in DESKTOP),
                     *(("p" + str(w), self.got["phones"][str(w)]) for w, _ in PHONES)]:
            with self.subTest(k):
                self.assertEqual(g["gone"], [], "Open questions, the points line and the catch-up line are gone")
                self.assertFalse(g["chatbar"], "no threads bar in the PO chat")

    def test_needs_you_is_not_a_panel(self):
        g = self.got["desktop"]["1440"]
        self.assertNotIn("points", g["ids"])
        self.assertEqual(g["toolStrip"], ["changes", "workspace", "board", "spec"])
        # #322: on a computer the Inbox is the list dock's, beside Tasks on the left strip.
        self.assertEqual(g["leftStrip"], ["list", "inbox"])
        self.assertEqual(g["front"], ["po-chat"], "the PO dock has no Inbox on a computer")
        self.assertFalse(g["strip"], "the Inbox strip is a phone's")

    def test_the_messages_get_the_height_on_a_computer(self):
        for w, want in ((1440, 609), (1728, 826)):
            g = self.got["desktop"][str(w)]
            with self.subTest(w=w):
                self.assertIsNotNone(g["msgs"], g)
                self.assertLessEqual(g["msgs"]["y"], 160, g["msgs"])
                self.assertGreaterEqual(g["msgs"]["h"], want - 4, g["msgs"])

    def test_a_phone_bar_is_one_row(self):
        g = self.got["phones"]["390"]
        self.assertTrue(g["phone"])
        self.assertEqual(sorted(g["bar"]), sorted(["bar-home", "proj-switch", "search-open", "me-btn", "new-btn"]), g["bar"])
        ys = {b["y"] for b in g["bar"].values()}
        self.assertLessEqual(max(ys) - min(ys), 4, "one row")
        self.assertLessEqual(g["header"]["h"], 50)

    def test_a_phone_has_chat_and_inbox_tabs(self):
        for w, _ in PHONES:
            g = self.got["phones"][str(w)]
            with self.subTest(w=w):
                self.assertEqual(g["tabIds"][:2], ["po-chat", "inbox"])
                self.assertEqual(g["tabs"][0], "Chat")
                self.assertTrue(g["tabs"][1].startswith("Inbox"), g["tabs"])
                self.assertNotIn("points", g["tabIds"])

    def test_a_phone_chat_names_what_waits_and_opens_the_inbox(self):
        g = self.got["phones"]["390"]
        self.assertTrue(g["strip"], g)
        self.assertIn("1 to check", g["stripText"])
        self.assertTrue(g["stripText"].endswith("Inbox ›"), g["stripText"])
        self.assertEqual(g["stripBox"]["h"], 36)
        t = self.got["tapInbox"]
        self.assertEqual(t["front"], "inbox", "Inbox › brings the Inbox tab forward")
        self.assertGreaterEqual(t["rows"], 1)
        self.assertEqual(t["scrollW"], 0)

    def test_the_messages_get_the_height_on_a_phone(self):
        g = self.got["phones"]["390"]
        self.assertIsNotNone(g["msgs"], g)
        # #309's target, 586, was drawn without the PO's head line; the strip takes 36 of it.
        self.assertGreaterEqual(g["msgs"]["h"], 500, g["msgs"])

    def test_nothing_is_wider_than_the_screen(self):
        for k, g in [*(("d" + str(w), self.got["desktop"][str(w)]) for w, _ in DESKTOP),
                     *(("p" + str(w), self.got["phones"][str(w)]) for w, _ in PHONES)]:
            with self.subTest(k):
                self.assertLessEqual(g["scrollW"], g["W"])
                self.assertEqual(g["wide"], [])
                self.assertLessEqual(g["fScrollW"], g["fW"])
                self.assertEqual(g["fWide"], [])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
