"""A task's popup menu shows every item whole (#347, GitHub issue 44).

The left panel's row menu (#334: a task's or a session's ⋯, or a right click
on the row) squeezed its 14 items to 20px each: they were overflow:hidden flex
items in a list with a max-height, so they shrank instead of scrolling. In
headless Chrome over CDP, against a hub in a thread, each task menu is opened
and every item's box is measured:

* the left panel's task row ⋯ from the first, middle and last visible row, a
  right click on the last row, a session row's ⋯ and a project row's ⋯;
* the task's own Dock ⋯ (the task open in the middle) and a right click on its
  tab;

at 1440x900 and 1728x1117 (light and dark) and 390x844 (the tab menu: a phone
has no left panel or Dock ⋯ button). Each item has its natural height, lies inside the window,
inside the menu and inside every clipping ancestor; a menu too tall for the
room under its button opens upwards. In a 1000x420 window the row menu fits
the window and scrolls its list under its head, items still whole.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or
Chrome.
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
// Every item of `menu`: its box against its natural height (a copy laid out
// free), the window, and each ancestor that clips (overflow not visible).
const MEASURE = `((menu) => {
  if (!menu) return null;
  const win = menu.ownerDocument.defaultView;
  const box = r => ({ t: Math.round(r.top * 10) / 10, b: Math.round(r.bottom * 10) / 10, l: Math.round(r.left), r: Math.round(r.right) });
  const clips = el => { const a = []; for (let p = el.parentElement; p; p = p.parentElement) { const cs = win.getComputedStyle(p); if (cs.overflowY !== 'visible' || cs.overflowX !== 'visible') a.push(p); } return a; };
  const items = [...menu.querySelectorAll('button, [role^="menuitem"]')].filter(e => e.getClientRects().length);
  const mr = menu.getBoundingClientRect();
  const bad = [];
  for (const it of items) {
    const r = it.getBoundingClientRect(), cs = win.getComputedStyle(it);
    const c = it.cloneNode(true);
    c.style.cssText = 'position:absolute;visibility:hidden;height:auto;min-height:0;max-height:none;flex:none;width:' + r.width + 'px';
    it.parentElement.appendChild(c); const natural = c.getBoundingClientRect().height; c.remove();
    const why = [];
    if (r.height + 0.5 < natural) why.push('shrunk ' + r.height.toFixed(1) + ' < ' + natural.toFixed(1));
    if (cs.overflowY !== 'visible' && it.scrollHeight > it.clientHeight + 1) why.push('text cut');
    if (r.top < -0.5 || r.bottom > win.innerHeight + 0.5 || r.left < -0.5 || r.right > win.innerWidth + 0.5) why.push('off the window');
    for (const p of clips(it)) {
      const pr = p.getBoundingClientRect(), pc = win.getComputedStyle(p);
      if (r.top < pr.top + (parseFloat(pc.borderTopWidth) || 0) - 0.5 || r.bottom > pr.bottom - (parseFloat(pc.borderBottomWidth) || 0) + 0.5)
        why.push('cut by ' + p.tagName + '.' + String(p.className).split(' ')[0]);
    }
    if (why.length) bad.push({ t: it.textContent.trim().slice(0, 40), why, r: box(r) });
  }
  return { n: items.length, items: items.map(e => e.textContent.trim()), menu: box(mr), W: win.innerWidth, H: win.innerHeight, bad,
    inWin: mr.top >= -0.5 && mr.bottom <= win.innerHeight + 0.5 && mr.left >= -0.5 && mr.right <= win.innerWidth + 0.5,
    theme: document.documentElement.dataset.theme || '' };
})`;
const OPEN = `[...document.querySelectorAll('body > .dcm, .dk-menu')].find(e => !e.hidden && e.getClientRects().length)`;
// The left panel's row ⋯ that are wholly in its list's view.
const VIS = `[...document.querySelectorAll('#ld-projs [data-ldp-menu]')].filter(b => { const r = b.getBoundingClientRect(), c = document.getElementById('ld-projs').getBoundingClientRect(); return r.width && r.top >= c.top && r.bottom <= c.bottom; })`;
// The task's Dock ⋯: the one on screen in the middle (a phone has one).
const TASKDOTS = `[...document.querySelectorAll('[data-dk-act="menu"]')].find(b => { const r = b.getBoundingClientRect(); return r.width && b.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) && (innerWidth < 600 || r.left > 600); })`;
const MORE_LOOSE = `(() => { LDP.moreLoose = true; LDP.sig = ''; ldpPaint(); return 0; })()`;
(async () => {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsu = await new Promise((res, rej) => { let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000); });
  const ws = new WebSocket(wsu); let id = 0; const waits = new Map(); const errors = [];
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails || {}; errors.push(((d.exception && d.exception.description) || d.text || '').slice(0, 600)); }
    if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const page = async (w, h, theme) => {
    const mobile = w < 600;
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId); await send('Runtime.enable', {}, sessionId);
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }, sessionId);
    if (mobile) await send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
      if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const clickAt = async (x, y, button = 'left') => { await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
      for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x, y, button, clickCount: 1 }, sessionId); };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const seed = { 'cd-theme': theme, 'cd-list-dock': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-phone-tabs': null, 'cd-last-conv': null, 'cd-ld-sel': null, 'cd-ld-split': null };
    await send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(seed) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ') && window.ensBooted === true', 30000);
    await evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!PD.dock', 30000);
    await evalIn(`setAppearance(${JSON.stringify(theme)})`);
    await sleep(800);
    return { evalIn, until, clickAt, key, shot, close: () => send('Target.closeTarget', { targetId }) };
  };
  // Open a menu from what `expr` names (a click on it, or a right click), measure it, close it.
  const measure = async (p, name, expr, right) => {
    await p.key('Escape'); await sleep(150);
    const at = await p.evalIn(`(() => { const b = ${expr}; if (!b) return null; b.scrollIntoView({ block: 'nearest' }); const r = b.getBoundingClientRect(); return r.width ? [r.left + r.width / 2, r.top + r.height / 2, r.top, r.bottom] : null; })()`);
    if (!at) return { missing: name };
    await p.clickAt(at[0], at[1], right ? 'right' : 'left'); await sleep(400);
    const m = await p.evalIn(`${MEASURE}(${OPEN})`);
    if (m) m.from = { top: at[2], bottom: at[3] };
    await p.shot(name);
    await p.key('Escape'); await sleep(150);
    await p.evalIn(`(() => { document.querySelectorAll('.dk-menu').forEach(m => m.remove()); ldpMenuClose(); return 0; })()`);
    return m || { missing: name + ' (no menu)' };
  };
  const out = { menus: {} };
  try {
    for (const [w, h] of [[1440, 900], [1728, 1117], [390, 844]]) for (const theme of ['light', 'dark']) {
      const K = `${w}x${h}-${theme}`, p = await page(w, h, theme);
      if (w >= 600) {
        await p.evalIn(MORE_LOOSE); await sleep(300);
        out.menus[K + '-row-first'] = await measure(p, K + '-row-first', `${VIS}[0]`);
        out.menus[K + '-row-middle'] = await measure(p, K + '-row-middle', `(v => v[Math.floor(v.length / 2)])(${VIS})`);
        out.menus[K + '-row-last'] = await measure(p, K + '-row-last', `(v => v[v.length - 1])(${VIS})`);
        out.menus[K + '-row-right-click'] = await measure(p, K + '-row-right-click', `(v => v[v.length - 1].closest('.ldp-item').querySelector('.ldp-row'))(${VIS})`, true);
        out.menus[K + '-task-row'] = await measure(p, K + '-task-row', `document.querySelector('#ld-projs [data-ldp-menu][data-ldp-room=${JSON.stringify(A.looseTask)}]')`);
        out.menus[K + '-session-row'] = await measure(p, K + '-session-row', `document.querySelector('#ld-projs [data-ldp-menu][data-ldp-room=${JSON.stringify(A.looseSession)}]')`);
        out.menus[K + '-project-row'] = await measure(p, K + '-project-row', `document.querySelector('#ld-projs [data-ldp-pmenu]')`);
      }
      await p.evalIn(`(() => { openDetail(ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.task)}).sessionId); return 0; })()`);
      await p.until(`[...document.querySelectorAll('.dk-tab[data-dk-tab="po-chat"]')].some(t => t.getBoundingClientRect().width) && document.body.classList.contains('detail-open')`, 15000);
      await sleep(1200);
      if (w >= 600) out.menus[K + '-task-dots'] = await measure(p, K + '-task-dots', TASKDOTS);
      out.menus[K + '-task-tab-right-click'] = await measure(p, K + '-task-tab-right-click', `[...document.querySelectorAll('.dk-tab[data-dk-tab="po-chat"]')].find(t => t.getBoundingClientRect().width)`, true);
      await p.close();
    }
    // A window too short for the row menu: it fits the window and scrolls its list under the head.
    {
      const p = await page(1000, 420, 'light');
      await p.evalIn(MORE_LOOSE); await sleep(300);
      out.short = await measure(p, 'short-row-last', `(v => v[v.length - 1])(${VIS})`);
      await p.evalIn(MORE_LOOSE);
      const at = await p.evalIn(`(() => { const b = (v => v[v.length - 1])(${VIS}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      await p.clickAt(at[0], at[1]); await sleep(400);
      out.shortScroll = await p.evalIn(`(() => { const m = document.querySelector('body > .dcm'), l = m.querySelector('.dcm-list'), hd = m.querySelector('.dcm-head');
        const mr = m.getBoundingClientRect(), lr = l.getBoundingClientRect(), hr = hd.getBoundingClientRect();
        const before = { sh: l.scrollHeight, ch: l.clientHeight };
        l.scrollTop = l.scrollHeight;
        const last = [...l.querySelectorAll('.dcm-item')].pop().getBoundingClientRect(), lr2 = l.getBoundingClientRect();
        return { ...before, menuTop: mr.top, menuBottom: mr.bottom, H: innerHeight, headInside: hr.top >= mr.top && hr.bottom <= lr.top + 0.5,
          lastInside: last.top >= lr2.top - 0.5 && last.bottom <= lr2.bottom + 0.5 }; })()`);
      await p.shot('short-row-scrolled');
      await p.close();
    }
  } catch (e) { out.ERROR = String(e.stack || e); }
  out.errors = errors;
  console.log(JSON.stringify(out));
  ch.kill('SIGKILL'); process.exit(0);
})();
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class MenuFitInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-mf-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        patches = [
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
            mock.patch.object(points, "mark_seen", lambda *a, **k: []),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("Motors PO", members(home))
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        ok, task, why = dashboard.create_task("Gearbox noise", "Check it.", proj["id"], [{"agent": "claude"}])
        assert ok, why
        task["launched"] = True
        chatroom.update_room(task)
        chatroom.post_message(task["id"], "claude", "Looked at the gearbox.", to="user")
        # tasks in no project: the left panel's rows, enough to fill its list
        ok, loose_task, why = dashboard.create_task("Loose gearbox task", "Check it.", "", [{"agent": "claude"}])
        assert ok, why
        loose_task["launched"] = True
        chatroom.update_room(loose_task)
        loose_session = chatroom.create_room("Loose gearbox session", members(base))
        for i in range(10):
            chatroom.create_room(f"Loose errand {i}", members(base))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "pid": proj["id"], "task": task["id"], "looseTask": loose_task["id"],
                "looseSession": loose_session["id"], "shots": shots}
        script = base / "menu_fit_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=480)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        assert "ERROR" not in cls.got, cls.got["ERROR"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_every_menu_shows_every_item_whole(self):
        menus = self.got["menus"]
        # 2 desktop sizes x 2 themes x 9 menus, and the phone's tab menu x 2
        self.assertEqual(len(menus), 2 * 2 * 9 + 2, sorted(menus))
        for name, m in menus.items():
            with self.subTest(menu=name):
                self.assertNotIn("missing", m, m)
                self.assertGreater(m["n"], 0)
                self.assertEqual(m["theme"], "dark" if name.split("-")[1] == "dark" else "light")
                self.assertTrue(m["inWin"], m["menu"])
                self.assertEqual(m["bad"], [], m["bad"])

    def test_the_task_row_menu_holds_the_tasks_actions(self):
        for name, m in self.got["menus"].items():
            if name.endswith("-task-row"):
                with self.subTest(menu=name):
                    self.assertEqual(m["items"][0], "Open")
                    self.assertIn("Rename…", m["items"])
                    self.assertIn("Delete task…", m["items"])
                    self.assertGreaterEqual(m["n"], 12)

    def test_a_menu_under_a_low_row_opens_upwards(self):
        """From the last row at 1440x900 the menu has no room below: it opens
        above the row (or shifts up to fit), never off the window."""
        for theme in ("light", "dark"):
            m = self.got["menus"][f"1440x900-{theme}-row-last"]
            with self.subTest(theme=theme):
                self.assertLess(m["menu"]["t"], m["from"]["top"], m)
                self.assertLessEqual(m["menu"]["b"], m["H"])

    def test_a_menu_taller_than_the_window_scrolls_its_list(self):
        s, m = self.got["shortScroll"], self.got["short"]
        self.assertTrue(m["inWin"], m)
        self.assertGreaterEqual(s["menuTop"], 0)
        self.assertLessEqual(s["menuBottom"], s["H"])
        self.assertGreater(s["sh"], s["ch"], "the list scrolls")
        self.assertTrue(s["headInside"], s)
        self.assertTrue(s["lastInside"], s)
        # whole items, even while the list scrolls: none is squeezed
        self.assertEqual([b for b in m["bad"] if any(w.startswith(("shrunk", "text cut")) for w in b["why"])], [])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
