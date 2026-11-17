"""P370: the top bar's popovers close on a click anywhere, a chat frame too.

Most of the board is same-origin frames (the PO chat, threads, a task's
session, the docked Send box). A click in one never reaches this page's click
listeners; it moves focus into the frame, so this window gets `blur`, and that
closes the plan tray, the me menu, Create, the project switcher, search help
and the search results. Esc closes the plan tray, focus back on its chip.

In headless Chrome over CDP against a throwaway hub in a thread, at 1728x1117,
with real input events (Input.dispatchMouseEvent / Input.dispatchKeyEvent),
never .click() from script, for every click that matters: the point is the
real focus move into the PO chat's frame. Clicks inside a popover, and the
tray's slider and its "Back to normal", leave it open.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import pacing  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const i = ++id; waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  try {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 10000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await evalIn(expr)) return true; } catch (e) {} await sleep(50); } return false; };
    // The board's real chat frame: the PO's conversation.
    const PO = `document.querySelector('#po-panel iframe.po-session:not([hidden])') || (window.PD && PD.els && PD.els['po-chat'] && PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])'))`;
    const shown = sel => evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && e.getBoundingClientRect().width > 0; })()`);
    // The middle of an element, and whether a press there lands on it.
    const at = sel => evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null;
      const r = e.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, h = document.elementFromPoint(x, y);
      return { x, y, hit: !!h && (h === e || e.contains(h)), w: r.width }; })()`);
    const mouse = (type, p, extra = {}) => send('Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, ...extra }, sessionId);
    const press = async p => {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p.x, y: p.y, buttons: 0 }, sessionId);
      await mouse('mousePressed', p); await mouse('mouseReleased', p); await sleep(200);
    };
    const click = async sel => { const p = await at(sel); if (!p || !p.hit) throw new Error('cannot press ' + sel + ' ' + JSON.stringify(p)); await press(p); return p; };
    const key = async (k, code, vk) => {
      await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk }, sessionId);
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk }, sessionId);
      await sleep(150);
    };
    // A real press in the PO chat's frame (its message list, not a control).
    const frameClick = async () => {
      const p = await evalIn(`(() => { const f = ${PO}; if (!f) return null; const r = f.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + Math.min(160, r.height / 3);
        const inner = f.contentDocument.elementFromPoint(x - r.left, y - r.top);
        return { x, y, hit: document.elementFromPoint(x, y) === f, inner: inner ? inner.tagName + '#' + inner.id + '.' + inner.className : null }; })()`);
      if (!p || !p.hit) throw new Error('no frame to press ' + JSON.stringify(p));
      await press(p);
      await sleep(150);
      const f = await evalIn(`({ top: document.hasFocus(), frame: (${PO}).contentDocument.hasFocus(),
        active: document.activeElement ? document.activeElement.tagName + '.' + document.activeElement.className : null })`);
      return { ...p, ...f };
    };
    // A point inside a popover that is no control (padding, a title, a rule).
    const inside = sel => evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}), r = e.getBoundingClientRect();
      for (let fy = 0.02; fy < 1; fy += 0.04) for (let fx = 0.02; fx < 1; fx += 0.08) {
        const x = r.left + r.width * fx, y = r.top + r.height * fy, h = document.elementFromPoint(x, y);
        if (h && (h === e || e.contains(h)) && !h.closest('button, a, input, select, textarea, label, [role=menuitem], [role=option], [tabindex]:not(#' + e.id + ')'))
          return { x, y, hit: true, what: h.tagName + '.' + h.className };
      }
      return null; })()`);
    const closeAll = () => evalIn(`(() => { document.getElementById('usage-tray').hidden = true; closeMeMenu(); closeCreateMenu(); closeProjMenu();
      document.getElementById('search-help-pop').hidden = true; if (findShown()) findOpen(false); return true; })()`);
    const thumb = async () => {
      await send('DOM.enable', {}, sessionId);
      const { root } = await send('DOM.getDocument', { depth: -1, pierce: true }, sessionId);
      const attr = (n, k) => { const a = n.attributes || []; for (let i = 0; i < a.length; i += 2) if (a[i] === k) return a[i + 1]; return null; };
      const find = (n, ok) => { if (ok(n)) return n; for (const c of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = find(c, ok); if (f) return f; } return null; };
      const input = find(root, n => n.localName === 'input' && attr(n, 'id') === 'usage-pace-slider');
      const t = find(input, n => n !== input && (attr(n, 'pseudo') === '-webkit-slider-thumb' || attr(n, 'id') === 'thumb'));
      const { model } = await send('DOM.getBoxModel', { nodeId: t.nodeId }, sessionId);
      const b = model.border; return { x: (b[0] + b[2]) / 2, y: (b[1] + b[5]) / 2 };
    };
    const got = {};
    await send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    got.rows = await until(`typeof ALL_ROWS !== 'undefined' && !!ALL_ROWS && ALL_ROWS.some(r => r.roomId === ${JSON.stringify(A.po)}) && !!PROJECTS`, 30000);
    await evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.project)}; PROJECT_TAB = 'tasks'; if (VIEW_MODE !== 'board') setViewMode('board'); else { BOARD_SIG = ''; renderRows(); } return 1; })()`);
    got.frame = await until(`(() => { const f = ${PO}; return !!f && !!f.contentDocument && f.contentDocument.readyState === 'complete' && !!f.contentDocument.getElementById('msgs') && f.getBoundingClientRect().height > 200; })()`, 30000);
    got.chip = await until(`(() => { const c = document.getElementById('usage-chip'); return !c.hidden && c.getBoundingClientRect().width > 0 && typeof usageNaturalMargin === 'function' && usageNaturalMargin() === 10; })()`, 15000);
    await sleep(800);

    // 1. Chip opens the tray; a press in the chat frame closes it.
    const c1 = got.c1 = {};
    await closeAll();
    await click('#usage-chip');
    c1.open = await shown('#usage-tray');
    c1.topFocus = await evalIn(`document.hasFocus()`);
    c1.press = await frameClick();
    c1.after = await shown('#usage-tray');

    // 2. Dragging the slider, "Back to normal" and the tray's own body leave it open.
    const c2 = got.c2 = {};
    await closeAll();
    await click('#usage-chip');
    c2.open = await shown('#usage-tray');
    await sleep(300);
    const t0 = await thumb();
    c2.before = await evalIn(`document.getElementById('usage-pace-slider').value`);
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: t0.x, y: t0.y, buttons: 0 }, sessionId);
    await mouse('mousePressed', t0);
    for (let i = 1; i <= 10; i++) { await mouse('mouseMoved', { x: t0.x + i * 8, y: t0.y }); await sleep(20); }
    await mouse('mouseReleased', { x: t0.x + 80, y: t0.y });
    await sleep(200);
    c2.afterDrag = await shown('#usage-tray');
    c2.dragged = await evalIn(`document.getElementById('usage-pace-slider').value`);
    c2.focusAfterDrag = await evalIn(`document.activeElement && document.activeElement.id`);
    c2.resetShown = await until(`(() => { const b = document.getElementById('usage-pace-reset'); return !b.hidden && b.getBoundingClientRect().width > 0; })()`);
    c2.openWithReset = await shown('#usage-tray');
    await click('#usage-pace-reset');
    c2.afterReset = await shown('#usage-tray');
    c2.backToNormal = await until(`USAGE.pace && USAGE.pace.marginPoints === 10 && document.getElementById('usage-pace-slider').value === '10'`);
    await click('#usage-tray .usage-head');
    c2.afterHead = await shown('#usage-tray');
    const body = await inside('#usage-tray');
    c2.bodyPoint = body;
    if (body) await press(body);
    c2.afterBody = await shown('#usage-tray');

    // 3. Esc in the tray (on its slider) closes it, focus on the chip.
    const c3 = got.c3 = {};
    await closeAll();
    await click('#usage-chip');
    c3.open = await shown('#usage-tray');
    await evalIn(`document.getElementById('usage-pace-slider').focus()`);
    c3.focusBefore = await evalIn(`document.activeElement && document.activeElement.id`);
    await key('Escape', 'Escape', 27);
    c3.after = await shown('#usage-tray');
    c3.focus = await evalIn(`document.activeElement && document.activeElement.id`);
    c3.frameStill = await evalIn(`!!(${PO})`);

    // 4. The chip toggles.
    const c4 = got.c4 = {};
    await closeAll();
    await click('#usage-chip');
    c4.first = await shown('#usage-tray');
    await click('#usage-chip');
    c4.second = await shown('#usage-tray');

    // 5 and 6. Each other popover: a press inside keeps it, one in the frame closes it.
    const pops = got.pops = {};
    const one = async (name, open, sel, insideSel) => {
      const o = pops[name] = {};
      await closeAll();
      try { await open(); } catch (e) { o.openError = String(e.message || e); return; }
      o.open = await shown(sel);
      const p = insideSel ? await at(insideSel) : await inside(sel);
      o.insidePoint = p;
      if (p && p.hit) { await press(p); o.afterInside = await shown(sel); }
      o.press = await frameClick();
      o.after = await shown(sel);
    };
    await one('me', () => click('#me-btn'), '#me-menu', '#me-menu .me-rule');
    await one('create', () => click('#new-btn'), '#create-menu', null);
    await one('proj', () => click('#proj-switch'), '#proj-menu', '#proj-menu .pm-find');
    await one('help', async () => {
      if (!(await at('#search-help'))?.hit) await click('#search');
      await click('#search-help');
    }, '#search-help-pop', '#search-help-pop .shp-title');
    await one('find', async () => {
      await click('#search');
      await send('Input.insertText', { text: 'Brakepad' }, sessionId);
      if (!(await until(`!document.getElementById('find-pop').hidden && !!document.querySelector('#find-pop [id^="fd-"]')`)))
        throw new Error('no results: ' + await evalIn(`document.getElementById('find-pop').hidden + ' ' + document.getElementById('find-pop').textContent.slice(0, 200)`));
    }, '#find-pop', null);
    got.findRows = await evalIn(`document.querySelectorAll('#find-pop [id^="fd-"]').length`);
    await evalIn(`(() => { const s = document.getElementById('search'); s.value = ''; s.dispatchEvent(new Event('input', { bubbles: true })); s.blur(); return 1; })()`);

    // The top bar's empty space still closes one (the page's own click listener).
    const bar = got.bar = {};
    await closeAll();
    await click('#me-btn');
    bar.open = await shown('#me-menu');
    bar.point = await evalIn(`(() => { for (const g of document.querySelectorAll('header .bar-gap')) { const r = g.getBoundingClientRect(); if (r.width < 8) continue;
      const x = r.left + r.width / 2, y = r.top + r.height / 2, h = document.elementFromPoint(x, y);
      if (h && h.closest('header') && !h.closest('button, a, input')) return { x, y, hit: true, what: h.tagName + '.' + h.className }; } return null; })()`);
    if (bar.point) await press(bar.point);
    bar.after = await shown('#me-menu');
    console.log(JSON.stringify(got));
  } finally { ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class PopoversCloseOnAFrameClick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pf-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        now = time.time()
        end = now + 100 * 3600

        def source(kind, percent):
            return {"source": kind, "state": "ok", "ageSeconds": 0, "planType": "plan", "windows": [{
                "kind": "seven_day", "percent": percent, "resetsAt": _iso(end), "trusted": True,
                "ageSeconds": 0, "windowMinutes": 10080, "label": "7-day",
                **({"pool": "codex"} if kind == "codex" else {})}]}
        snap = {"state": "ok", "checkedAt": now, "warnPercent": 80, "alarmPercent": 95, "plans": {},
                "sources": [source("claude", 40), source("codex", 30)]}
        for p in [
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
            mock.patch.object(dashboard.usage, "snapshot", lambda *a, **k: snap),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        cls.addClassCleanup(pacing._CACHE.update, path=None, data=None)
        pacing._CACHE.update(path=None, data=None)
        for h in range(30, -1, -1):
            pacing.note("claude", now - h * 3600, 30.0 + (10 - h / 3), end)
            pacing.note("codex:codex", now - h * 3600, 30.0, end)
        ok, proj, _ = dashboard.register_project("Garage")
        assert ok, proj
        home = str(Path(proj.get("home") or proj["path"]))
        po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "role": "Product owner",
                                               "sessionId": "pf-po", "cwd": home}])
        room = chatroom.get_room(po["id"], public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(po["id"], proj["id"])
        dashboard.set_project_po(proj["id"], po["id"])
        task = chatroom.create_room("Brakepad review", [{"identity": "claude", "agent": "claude",
                                                         "sessionId": "pf-task", "cwd": home}])
        dashboard.assign_session_project(task["id"], proj["id"])
        dashboard.assign_task_number(task["id"], proj["id"])
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        script = base / "popover_frame_click_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "project": proj["id"], "po": po["id"],
                "base": f"http://127.0.0.1:{server.server_address[1]}"}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True,
                             encoding="utf-8", timeout=200)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        dump = os.environ.get("POPOVER_FRAME_DUMP", "")
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.assertTrue(self.got["rows"], "the board never loaded")
        self.assertTrue(self.got["frame"], "the PO chat's frame never loaded")
        self.assertTrue(self.got["chip"], "the plan chip never showed")

    def pressed_the_frame(self, press):
        # The press landed on the frame and focus went into it: this window lost it.
        self.assertTrue(press["hit"], press)
        self.assertTrue(press["frame"], press)
        self.assertEqual(press["active"], "IFRAME.po-session", press)

    def test_a_press_in_the_chat_frame_closes_the_tray(self):
        c = self.got["c1"]
        self.assertTrue(c["open"], c)
        self.assertTrue(c["topFocus"], c)
        self.pressed_the_frame(c["press"])
        self.assertFalse(c["after"], c)

    def test_the_slider_reset_and_body_keep_the_tray_open(self):
        c = self.got["c2"]
        self.assertTrue(c["open"], c)
        self.assertTrue(c["afterDrag"], c)
        self.assertNotEqual(c["dragged"], c["before"], c)
        self.assertEqual(c["focusAfterDrag"], "usage-pace-slider", c)
        self.assertTrue(c["resetShown"], c)
        self.assertTrue(c["openWithReset"], c)
        self.assertTrue(c["afterReset"], c)
        self.assertTrue(c["backToNormal"], c)
        self.assertTrue(c["afterHead"], c)
        self.assertIsNotNone(c["bodyPoint"], c)
        self.assertTrue(c["afterBody"], c)

    def test_esc_closes_the_tray_and_focuses_the_chip(self):
        c = self.got["c3"]
        self.assertTrue(c["open"], c)
        self.assertEqual(c["focusBefore"], "usage-pace-slider", c)
        self.assertFalse(c["after"], c)
        self.assertEqual(c["focus"], "usage-chip", c)
        self.assertTrue(c["frameStill"], c)

    def test_the_chip_toggles(self):
        c = self.got["c4"]
        self.assertTrue(c["first"], c)
        self.assertFalse(c["second"], c)

    def test_each_popover_closes_on_a_frame_press_and_not_on_its_own(self):
        names = {"me": "#me-menu", "create": "#create-menu", "proj": "#proj-menu",
                 "help": "#search-help-pop", "find": "#find-pop"}
        for name, sel in names.items():
            with self.subTest(popover=sel):
                o = self.got["pops"][name]
                self.assertNotIn("openError", o, o)
                self.assertTrue(o["open"], o)
                self.assertTrue(o["insidePoint"] and o["insidePoint"]["hit"], o)
                self.assertTrue(o["afterInside"], o)
                self.pressed_the_frame(o["press"])
                self.assertFalse(o["after"], o)
        self.assertGreater(self.got["findRows"], 0)

    def test_the_top_bar_empty_space_still_closes_one(self):
        b = self.got["bar"]
        self.assertTrue(b["open"], b)
        self.assertIsNotNone(b["point"], b)
        self.assertFalse(b["after"], b)


if __name__ == "__main__":
    unittest.main()
