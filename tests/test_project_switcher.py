"""The project switcher in the top bar (#303, Layout A step 2), in headless
Chrome over CDP, against a hub in a thread serving the pages.

Seven projects: Motors and Wheels with a PO (Motors' PO chat holds an answer
to check, so Motors has one Inbox row), Alpha, Beta, Gamma, Delta and Epsilon
without one. Checked:

* the pure parts: projRecent (the newest of the list's recent and the
  projects this browser opened, three at most, no Unassigned, no stale
  project) and projMatches (name or key, by name, Unassigned last);
* at 1728x1117 the bar's project name is the switcher; a click opens Recent
  (the projects opened last, newest first) then All projects with its count
  and a filter that has the keyboard, every project, the Inbox count on
  Motors, this project's settings and New project; the menu is under its
  button and on the screen;
* typing finds a project not in Recent and puts Recent and the settings
  aside; Enter opens it; a project with a PO opens its PO's conversation;
* Escape clears the filter, then closes and gives the keyboard back to the
  name; ArrowDown on the name opens it on the first project, ArrowDown moves;
* New project starts the new-project flow;
* on a phone (390x844, 360 and 430) the menu fits the screen, its rows are
  finger-sized, the filter does not raise the keyboard on its own and is
  16px (no zoom), and All projects as cards is its cards page;
* the page threw nothing meanwhile.

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
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")

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
// What the open menu shows and where.
const MENU = `(() => {
  const m = document.getElementById('proj-menu'), b = document.getElementById('proj-switch');
  const vis = e => !!e && e.offsetParent !== null && e.getBoundingClientRect().height > 0;
  const R = e => { const r = e.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) }; };
  const name = e => (e.querySelector('.pm-name') || e).textContent.trim();
  const ae = document.activeElement;
  return { open: !m.hidden, expanded: b.getAttribute('aria-expanded'), box: m.hidden ? null : R(m), btn: R(b),
    W: document.documentElement.clientWidth, H: innerHeight, scrollW: document.documentElement.scrollWidth,
    heads: [...m.querySelectorAll('.pm-head')].filter(vis).map(e => e.textContent.trim()),
    recent: [...m.querySelectorAll('.pm-rec')].filter(vis).map(name),
    all: [...m.querySelectorAll('.pm-any')].filter(vis).map(name),
    on: [...m.querySelectorAll('.pm-item.on')].map(e => e.dataset.proj || ''),
    needs: Object.fromEntries([...m.querySelectorAll('.pm-any')].map(e => [name(e), (e.querySelector('.pm-needs') || { textContent: '' }).textContent])),
    sets: [...m.querySelectorAll('.pm-set')].filter(vis).map(e => [...e.classList].filter(c => c !== 'pm-item' && c !== 'pm-set').join(' ')),
    newBtn: vis(m.querySelector('.pm-new')), cards: vis(m.querySelector('.pm-cards')), none: vis(m.querySelector('.pm-none')),
    finding: m.classList.contains('pm-finding'),
    items: [...m.querySelectorAll('.pm-item, .pm-find')].filter(vis).map(e => ({ cls: e.className, ...R(e) })),
    findFont: m.querySelector('.pm-find') ? getComputedStyle(m.querySelector('.pm-find')).fontSize : '',
    focus: ae === m ? 'menu' : ae && ae.matches('.pm-find') ? 'find' : ae && ae.closest('#proj-menu') ? 'item:' + name(ae) : ae ? ae.id || ae.tagName : '',
    switchName: document.getElementById('proj-switch-name').textContent, proj: SELECTED_PROJECT,
    po: document.body.classList.contains('po-dock'), pid: (typeof PD !== 'undefined' && PD.pid) || '' };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile, opened) => {
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
    const key = async (k, code, vk) => { for (const type of ['rawKeyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }, sessionId); };
    const type = t => c.send('Input.insertText', { text: t }, sessionId);
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs"].forEach(k => localStorage.removeItem(k)); localStorage.setItem("cd-proj-opened", ' + JSON.stringify(JSON.stringify(opened || {})) + '); } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 6 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, key, type, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toProj = (p, pid) => p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const P = A.ids;
  // Far in the future: newer than anything the list's own recent holds.
  const later = Date.now() + 1e9;
  const opened = { [P.Gamma]: later + 3000, [P.Beta]: later + 2000, [P.Wheels]: later + 1000 };
  try {
    // ---- 1728 x 1117
    const p = await page(1728, 1117, false, opened);
    // The pure parts, run in the page.
    out.unit = await p.evalIn(`(() => {
      const U = UNASSIGNED_ID, pj = (id, name, more) => ({ id, name, registered: true, ...more });
      const list = [pj('a', 'Alpha', { key: 'XQ' }), pj('b', 'beta'), pj('c', 'Gamma'), pj('d', 'Delta'), pj(U, 'Unassigned'), pj('s', 'Stale', { registered: false })];
      const ids = l => l.map(x => x.id);
      return {
        none: ids(projRecent(list, [], {})),
        opened: ids(projRecent(list, [], { a: 1000, c: 3000, d: 2000, b: 500 })),
        two: ids(projRecent(list, [], { a: 1000, c: 3000, d: 2000, b: 500 }, 2)),
        mixed: ids(projRecent(list, [{ pid: 'b', po: 's', at: 10 }, { pid: 'd', proj: 1, at: 1 }, { pid: 'a', at: 99 }], { c: 5000 })),
        skips: ids(projRecent(list, [{ pid: U, po: 'x', at: 9 }, { pid: 's', po: 'x', at: 9 }], { [U]: 9e9, s: 9e9 })),
        all: ids(projMatches(list, '')), q: ids(projMatches(list, 'TA')), key: ids(projMatches(list, 'xq')), blank: ids(projMatches(list, '  ')),
      }; })()`);
    await toProj(p, P.Motors);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!IBX.data', 30000);
    await p.until('!!IBX.data && IBX.data.rows.some(r => r.projectId === ' + JSON.stringify(P.Motors) + ')', 15000).catch(() => null);
    await sleep(300);
    await p.click('document.getElementById("proj-switch")');
    await p.until('!document.getElementById("proj-menu").hidden');
    await sleep(150);
    out.desk = await p.evalIn(MENU);
    await p.shot('switcher-1728x1117');
    // Find a project that is not in Recent; Enter opens it.
    await p.type('elt');
    await sleep(150);
    out.found = await p.evalIn(MENU);
    await p.shot('switcher-1728x1117-find');
    await p.key('Enter', 'Enter', 13);
    await sleep(400);
    out.picked = await p.evalIn(MENU);
    // Nothing found.
    await p.click('document.getElementById("proj-switch")'); await sleep(150);
    await p.type('zzz'); await sleep(100);
    out.nothing = await p.evalIn(MENU);
    // Escape: the filter first, then the menu, the keyboard back on the name.
    await p.key('Escape', 'Escape', 27); await sleep(100);
    out.esc1 = await p.evalIn(MENU);
    await p.key('Escape', 'Escape', 27); await sleep(100);
    out.esc2 = await p.evalIn(MENU);
    // ArrowDown on the name: open on the first project; ArrowDown moves on.
    await p.key('ArrowDown', 'ArrowDown', 40); await sleep(150);
    out.down1 = await p.evalIn(MENU);
    await p.key('ArrowDown', 'ArrowDown', 40); await sleep(100);
    out.down2 = await p.evalIn(MENU);
    await p.key('Escape', 'Escape', 27); await sleep(100);
    // A project with a PO: its PO's conversation.
    await p.click('document.getElementById("proj-switch")'); await sleep(150);
    await p.click(`document.querySelector('#proj-menu .pm-any[data-proj="${P.Wheels}"]')`);
    await p.until('document.body.classList.contains("po-dock") && PD.pid === ' + JSON.stringify(P.Wheels), 15000).catch(() => null);
    await sleep(300);
    out.poPick = await p.evalIn(MENU);
    // A click outside closes it.
    await p.click('document.getElementById("proj-switch")'); await sleep(150);
    await p.click('document.querySelector("header")'); await sleep(100);
    out.outside = await p.evalIn(MENU);
    // New project: the new-project flow.
    await p.evalIn('window.__setup = 0; window.projectSetupFlow = () => { window.__setup++; }; 0');
    await p.click('document.getElementById("proj-switch")'); await sleep(150);
    await p.click('document.querySelector("#proj-menu .pm-new")'); await sleep(150);
    out.newFlow = await p.evalIn('({ setup: window.__setup, open: !document.getElementById("proj-menu").hidden })');
    await p.close();
    // ---- phones
    out.phone = {};
    for (const [w, h] of [[390, 844], [360, 780], [430, 932]]) {
      const q = await page(w, h, true, opened);
      await toProj(q, P.Motors);
      await q.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.narrow()', 30000);
      await sleep(300);
      await q.click('document.getElementById("proj-switch")');
      await q.until('!document.getElementById("proj-menu").hidden');
      await sleep(150);
      const m = await q.evalIn(MENU);
      await q.shot(`switcher-phone-${w}x${h}`);
      await q.click('document.querySelector("#proj-menu .pm-cards")'); await sleep(400);
      m.cardsPage = await q.evalIn('({ cards: PH_CARDS, proj: SELECTED_PROJECT, open: !document.getElementById("proj-menu").hidden })');
      out.phone[w] = m;
      await q.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class ProjectSwitcherInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-psw-", ignore_cleanup_errors=True)
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
        for name in ("Motors", "Wheels", "Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
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
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "po": cls.po, "shots": shots}
        script = base / "switcher_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_recent_and_matches(self):
        u = self.got["unit"]
        self.assertEqual(u["none"], [], "a project neither listed nor opened is not recent")
        self.assertEqual(u["opened"], ["c", "d", "a"], "newest first, three at most")
        self.assertEqual(u["two"], ["c", "d"])
        self.assertEqual(u["mixed"], ["b", "c", "d"], "the newest of both; an entry that is no project's PO or page is not counted")
        self.assertEqual(u["skips"], [], "never Unassigned or a stale project")
        self.assertEqual(u["all"], ["a", "b", "d", "c", "s", "__unassigned__"], "by name, Unassigned last")
        self.assertEqual(u["q"], ["b", "d", "s"], "name, any case; a stale project is found too")
        self.assertEqual(u["key"], ["a"], "or key")
        self.assertEqual(u["blank"], u["all"])

    def test_the_name_opens_recent_then_all_projects(self):
        d = self.got["desk"]
        self.assertEqual(d["switchName"], "Motors")
        self.assertTrue(d["open"] and d["expanded"] == "true", d)
        self.assertEqual(d["recent"], ["Gamma", "Beta", "Wheels"], "the projects opened last, newest first")
        self.assertEqual(d["heads"][0], "Recent")
        self.assertEqual(d["heads"][1], "All projects 7")
        self.assertEqual(d["all"], ["Alpha", "Beta", "Delta", "Epsilon", "Gamma", "Motors", "Wheels"])
        self.assertEqual(d["on"], [self.ids["Motors"]], "this project is marked")
        self.assertEqual(d["focus"], "find", "the filter has the keyboard")
        self.assertFalse(d["cards"], "a desktop has no cards page")

    def test_the_inbox_count_is_on_the_projects_row(self):
        d = self.got["desk"]
        self.assertEqual(d["needs"]["Motors"], "1", d["needs"])
        self.assertEqual(d["needs"]["Wheels"], "", d["needs"])

    def test_every_old_item_is_still_there(self):
        d = self.got["desk"]
        for k in ("task-tools-btn", "kind-btn", "key-btn"):
            self.assertIn(k, d["sets"], d["sets"])
        self.assertTrue(d["newBtn"], "New project")

    def test_the_menu_is_under_its_button_and_on_the_screen(self):
        d = self.got["desk"]
        b, m = d["box"], d["btn"]
        self.assertGreaterEqual(b["y"], m["b"])
        self.assertLess(b["y"] - m["b"], 16)
        self.assertGreaterEqual(b["x"], 0)
        self.assertLessEqual(b["r"], d["W"])
        self.assertLessEqual(b["b"], d["H"])
        self.assertLessEqual(d["scrollW"], d["W"])

    def test_typing_finds_a_project_and_enter_opens_it(self):
        f = self.got["found"]
        self.assertEqual(f["all"], ["Delta"])
        self.assertTrue(f["finding"])
        self.assertEqual((f["recent"], f["sets"], f["newBtn"]), ([], [], False), "Recent and the settings step aside")
        g = self.got["picked"]
        self.assertFalse(g["open"])
        self.assertEqual((g["proj"], g["switchName"]), (self.ids["Delta"], "Delta"))
        n = self.got["nothing"]
        self.assertTrue(n["none"] and n["all"] == [], n)

    def test_escape_clears_then_closes(self):
        e1, e2 = self.got["esc1"], self.got["esc2"]
        self.assertTrue(e1["open"] and not e1["finding"], e1)
        self.assertEqual(len(e1["all"]), 7)
        self.assertFalse(e2["open"])
        self.assertEqual(e2["expanded"], "false")
        self.assertEqual(e2["focus"], "proj-switch", "the keyboard back on the name")

    def test_arrow_down_opens_on_the_first_project(self):
        d1, d2 = self.got["down1"], self.got["down2"]
        self.assertTrue(d1["open"])
        self.assertEqual(d1["focus"], "item:Gamma")
        self.assertEqual(d2["focus"], "item:Beta")

    def test_a_project_with_a_po_opens_its_conversation(self):
        g = self.got["poPick"]
        self.assertFalse(g["open"])
        self.assertTrue(g["po"])
        self.assertEqual(g["pid"], self.ids["Wheels"])
        self.assertEqual(g["switchName"], "Wheels")
        self.assertFalse(self.got["outside"]["open"], "a click outside closes it")

    def test_new_project_starts_its_flow(self):
        self.assertEqual(self.got["newFlow"], {"setup": 1, "open": False})

    def test_on_a_phone_it_fits_and_is_finger_sized(self):
        for w, m in self.got["phone"].items():
            with self.subTest(w=w):
                self.assertTrue(m["open"])
                self.assertEqual(m["switchName"], "Motors")
                self.assertGreaterEqual(m["box"]["x"], 0)
                self.assertLessEqual(m["box"]["r"], int(w))
                self.assertLessEqual(m["box"]["b"], m["H"])
                self.assertLessEqual(m["scrollW"], m["W"])
                small = [i for i in m["items"] if i["h"] < 44]
                self.assertEqual(small, [], "finger-sized")
                self.assertEqual(m["findFont"], "16px", "no zoom on focus")
                self.assertNotEqual(m["focus"], "find", "no keyboard springing up")
                self.assertTrue(m["cards"])
                self.assertEqual(m["recent"], ["Gamma", "Beta", "Wheels"])
                self.assertEqual(m["cardsPage"], {"cards": True, "proj": None, "open": False}, "All projects as cards")

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
