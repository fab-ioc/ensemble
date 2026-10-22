"""#261 (the CEO's P213): the PO's conversation is named after its project.

The Dock tab read "PO chat" and right under it the header read "Ensemble
Dashboard · PO": the project said twice. Now the conversation's panel is called
by the project's name everywhere Dock shows a panel's title (its tab, its
window's ``document.title``, the Panels menu row; on a phone its tab too), and
the header under it keeps the avatar, "PO", the state and the actions, with no
project name. A drawer (the PO over another page) has no tab: it keeps the name.

In headless Chrome over CDP, against a hub in a thread, on two projects with a
PO (one with a short name, one with a long one), at 1728x1117 and 390x844 (and
360 wide): the tab's text is the project's name and its tooltip holds it whole;
the header has no project name; a long name is cut by an ellipsis on the tab;
switching project and renaming the project re-title the tab; the popped-out
window is named after the project once; a phone's tab is cut at 112px. ``ENS_SHOT_DIR`` keeps screenshots. Skipped without Node or Chrome.
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
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
LONG = "Optional Trading Engine for Long Named Initiatives"

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.exceptions = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); }
      if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; this.exceptions.push((d.text || '') + ' ' + (d.exception && d.exception.description || '') + ' @' + (d.url || '') + ':' + d.lineNumber); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// The conversation's names: the dock's title, its tab (text, tooltip, cut or not) here or in its window, the Panels
// menu row, the window's document.title, and the PO header's text.
const NAMES = `(() => {
  const d = PD.dock, w = d.popWindow('po-chat');
  const tab = doc => { const t = doc.querySelector('.dk-tab[data-dk-tab="po-chat"]'); if (!t) return null;
    const r = t.getBoundingClientRect(); return { text: t.textContent, tip: t.title, cut: t.scrollWidth > t.clientWidth + 1, w: Math.round(r.width) }; };
  const menu = document.createElement('div'); menu.innerHTML = pdMenuHtml();
  const row = menu.querySelector('[data-pd-toggle="po-chat"]');
  const head = (w ? w.document : document).querySelector('#po-panel .po-head');
  return { title: d.title('po-chat'), tab: tab(document), menuRow: row ? row.textContent : null,
           head: head ? head.innerText.replace(/\s+/g, ' ').trim() : null, name: head ? (head.querySelector('.po-name') || {}).textContent : null,
           win: w ? { title: w.document.title, tab: tab(w.document), chat: (() => { const f = w.document.querySelector('iframe.pd-own');
             const h = f && f.contentDocument && f.contentDocument.querySelector('header'); return h ? h.innerText.replace(/\s+/g, ' ').trim() : null; })() } : null };
})()`;
const PHONE = `(() => { const tabs = PD_ROOT.querySelector('.dk-tabs'); const R = tabs.getBoundingClientRect();
  const wraps = [...tabs.querySelectorAll('.dk-tab-wrap')];
  return { narrow: PD.dock.narrow(), row: Math.round(tabs.clientWidth), need: Math.round(tabs.scrollWidth),
           rects: wraps.map(w => { const r = w.getBoundingClientRect(); const t = w.querySelector('.dk-tab') || {}; return [t.dataset ? t.dataset.dkTab : '', t.textContent, Math.round(r.left - R.left), Math.round(r.right - R.left)]; }) }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  const size = (width, height, mobile) => c.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: mobile ? 2 : 1, mobile }, sessionId);
  await size(1728, 1117, false);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const shot = async (name, w, h) => { if (!A.shots) return;
    const r = await c.send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width: w, height: h, scale: 1 } }, sessionId);
    fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  const open = async (pid) => {
    await evalIn(`(() => { SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    await until(`!!PD.dock && document.body.classList.contains('po-dock') && PD.restored && PD.pid === ${JSON.stringify(pid)} && !!document.querySelector('#po-panel .po-head .po-name')`, 30000);
    await sleep(600);
  };
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
    await evalIn(`(() => { try { ['cd-list-dock', 'cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {} return 0; })()`);
    // A computer.
    await open(A.short);
    out.short = await evalIn(NAMES);
    await shot('desk-short', 1728, 260);
    await open(A.long);
    out.long = await evalIn(NAMES);
    await shot('desk-long', 1728, 260);
    // Back to the short one: the tab follows the switch.
    await open(A.short);
    out.switched = await evalIn(NAMES);
    // The project renamed (what the hub's projects say next): the tab follows at the next refresh.
    await evalIn(`(async () => { const real = apiProjects; window.__realApiProjects = real;
      apiProjects = async () => { const j = await real(); (j.projects || []).forEach(p => { if (p.id === ${JSON.stringify(A.short)}) p.name = 'Motors Renamed'; }); return j; };
      await refreshProjects(); await refresh({ now: true }); return 0; })()`);
    await until(`PD.dock.title('po-chat') === 'Motors Renamed'`, 15000).catch(() => null);
    await sleep(300);
    out.renamed = await evalIn(NAMES);
    await evalIn(`(async () => { apiProjects = window.__realApiProjects; await refreshProjects(); await refresh({ now: true }); return 0; })()`);
    await until(`PD.dock.title('po-chat') !== 'Motors Renamed'`, 15000).catch(() => null);
    // A window of its own.
    await evalIn('PD.dock.setViewMode("po-chat", "window"); 0');
    await until('!!PD.dock.popWindow("po-chat") && PD.els["po-chat"].ownerDocument !== document', 15000);
    await until(`(() => { const f = PD.dock.popWindow('po-chat').document.querySelector('iframe.pd-own'); return !!f && (() => { try { return f.contentWindow.eval('!!ROOM_OBJ'); } catch (e) { return false; } })(); })()`, 15000);
    await sleep(600);
    out.window = await evalIn(NAMES);
    await evalIn('PD.dock.popWindow("po-chat").close(); 0');
    await until('!PD.dock.isOpenOut("po-chat")', 10000);
    await evalIn('PD.dock.setViewMode("po-chat", "pinned"); 0');
    await until('PD.els["po-chat"].ownerDocument === document && PD.dock.isVisible("po-chat")', 10000);
    // A task in the middle: the tab is the task's; what the task panel shows under it.
    await evalIn(`openDetail(ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.task)}).sessionId); 0`);
    await until(`pdTask() && PD.dock.title('po-chat').includes('Brakes')`, 15000);
    await sleep(800);
    out.task = await evalIn(`(() => { const h = document.querySelector('#detail-panel .dp-head h2'); const t = document.querySelector('.dk-tab[data-dk-tab="po-chat"]');
      return { tab: t ? t.textContent : null, h2: h && h.offsetParent ? h.innerText : null }; })()`);
    await shot('desk-task', 1728, 260);
    await evalIn('closeDetail(); 0');
    await until('!pdTask()', 10000);
    // The task's chat as a panel of its own: what its page shows over the messages.
    await evalIn(`pdChatOpen(${JSON.stringify(A.task)}); 0`);
    await until(`PD.rt.has(('chat:' + ${JSON.stringify(A.task)})) && (() => { const f = PD.rt.get(('chat:' + ${JSON.stringify(A.task)}))?.el.querySelector('iframe'); return !!f && (() => { try { return f.contentWindow.eval('!!ROOM_OBJ'); } catch (e) { return false; } })(); })()`, 15000);
    await sleep(500);
    out.chatPanel = await evalIn(`(() => { const id = ('chat:' + ${JSON.stringify(A.task)}); const f = PD.rt.get(id).el.querySelector('iframe'); const h = f.contentDocument.querySelector('header');
      const t = document.querySelector('.dk-tab[data-dk-tab="' + id + '"]');
      return { tab: t ? t.textContent : null, head: h && h.offsetParent !== null ? h.innerText.replace(/\s+/g, ' ').trim() : null }; })()`);
    await shot('desk-chat-panel', 1728, 260);
    await evalIn(`pdChatUnpanel(${JSON.stringify(A.task)}); 0`);
    // A phone, 390 then 360 wide, each project.
    for (const [w, h] of [[390, 844], [360, 780]]) {
      await size(w, h, true);
      for (const [key, pid] of [['short', A.short], ['long', A.long]]) {
        await open(pid);
        await until('PD.dock.narrow()', 15000);
        await sleep(500);
        out[`phone${w}${key}`] = { ...(await evalIn(NAMES)), ...(await evalIn(PHONE)) };
        await shot(`phone${w}-${key}`, w, 200);
      }
    }
    // A long press on the cut tab (390 wide, the long name): one toast with the whole name, and no Dock tab menu,
    // through a touch held (iOS: no contextmenu) and through Android's contextmenu after it.
    await size(390, 844, true);
    await open(A.long);
    await until('PD.dock.narrow()', 15000);
    await sleep(400);
    out.longPress = await evalIn(`(async () => {
      const wait = ms => new Promise(r => setTimeout(r, ms));
      const tab = document.querySelector('.dk-tab[data-dk-tab="po-chat"]'), r = tab.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const el = _toastEl(); el.hidden = true; let writes = 0;
      new MutationObserver(() => { writes++; }).observe(el, { childList: true });
      const ev = (type, o = {}) => tab.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: 'touch', clientX: x, clientY: y, ...o }));
      const menu = () => !!document.querySelector('.dk-menu:not([hidden])');
      ev('pointerdown'); await wait(700);
      const ctx = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 2 });
      tab.dispatchEvent(ctx); await wait(100);
      const held = { writes, shown: !el.hidden, text: el.querySelector('.msg') && el.querySelector('.msg').textContent, menu: menu(), prevented: ctx.defaultPrevented };
      ev('pointerup');
      // A finger that moves (a sideways scroll of the row) is no long press.
      el.hidden = true; const before = writes;
      ev('pointerdown'); ev('pointermove', { clientX: x + 30 }); await wait(700);
      const moved = { writes: writes - before, shown: !el.hidden };
      ev('pointercancel');
      return { held, moved, menu: menu() }; })()`);
    out.errors = c.exceptions;
    if (A.dump) fs.writeFileSync(A.dump, JSON.stringify(out, null, 1));
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


class TheTaskPanelHeader(unittest.TestCase):
    def test_a_docked_task_hides_its_title_but_not_a_missing_one(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("body.dp-docked #detail-panel .dp-head h2:not(.dp-missing) { display:none; }", index)
        # Its tab cannot say the task is gone (no row): the header still does.
        self.assertIn('<div class="dp-head"><h2 class="dp-missing">Session not found</h2>', index)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class ThePoTabIsTheProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pt-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard, "push_label_to_iterm", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ids = []
        for name in ("Motors", LONG):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            chatroom.post_message(po["id"], "user", "Hello PO", to="claude")
            ids.append(proj["id"])
            if name == "Motors":
                t = chatroom.create_room("Brakes that squeal", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                                {"identity": "codex", "agent": "codex", "cwd": str(home)}])
                chatroom.post_message(t["id"], "user", "About the brakes")
                dashboard.assign_session_project(t["id"], proj["id"])
                cls.task = t["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENS_SHOT_DIR", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "short": ids[0], "long": ids[1], "task": cls.task, "shots": shots, "dump": os.environ.get("ENS_DUMP", "")}
        script = base / "pt_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def named(self, n, name, where):
        self.assertEqual(n["title"], name, where)
        self.assertEqual(n["tab"]["text"], name, f"{where}: the tab")
        self.assertTrue(n["tab"]["tip"].startswith(name), f"{where}: the tab's tooltip has the whole name: {n['tab']['tip']}")
        self.assertEqual(n["menuRow"], name, f"{where}: the Panels menu row")

    def header_without(self, n, name, where, phone=False):
        self.assertEqual(n["name"], "PO", f"{where}: the header says PO")
        self.assertNotIn(name, n["head"], f"{where}: the header has no project name: {n['head']}")
        if not phone:   # (a phone's header line shows the state only, its "PO" hidden since #167)
            self.assertIn("PO", n["head"])

    def test_a_computer_names_the_tab_after_the_project_and_the_header_does_not(self):
        g = self.got
        self.named(g["short"], "Motors", "short")
        self.header_without(g["short"], "Motors", "short")
        self.assertFalse(g["short"]["tab"]["cut"], g["short"]["tab"])

    def test_a_long_name_is_cut_on_the_tab_and_whole_in_its_tooltip(self):
        n = self.got["long"]
        self.named(n, LONG, "long")
        self.header_without(n, LONG, "long")
        self.assertTrue(n["tab"]["cut"], f"the long name is cut with an ellipsis: {n['tab']}")
        self.assertLessEqual(n["tab"]["w"], 200, n["tab"])

    def test_switching_and_renaming_the_project_retitle_the_tab(self):
        self.named(self.got["switched"], "Motors", "switched back")
        self.named(self.got["renamed"], "Motors Renamed", "renamed")
        self.header_without(self.got["renamed"], "Motors Renamed", "renamed")

    def test_an_open_task_is_named_by_its_tab_not_again_by_its_header(self):
        t = self.got["task"]
        self.assertTrue(t["tab"].endswith("Brakes that squeal"), t)
        self.assertIsNone(t["h2"], "the task panel's title is hidden under its tab")
        # A task's chat as a panel of its own already named it only on its tab.
        c = self.got["chatPanel"]
        self.assertEqual(c["tab"], "Brakes that squeal", c)
        self.assertNotIn("Brakes", c["head"] or "", c)

    def test_its_window_is_named_after_the_project_once(self):
        w = self.got["window"]
        self.assertEqual(w["title"], "Motors")
        self.assertEqual(w["win"]["title"], "Motors", "the window's document.title")
        self.assertEqual(w["win"]["tab"]["text"], "Motors")
        # The window holds a chat of its own: what it shows over the messages names no project either.
        self.assertIsNotNone(w["win"]["chat"], w)
        self.assertNotIn("Motors", w["win"]["chat"], w["win"]["chat"])

    def test_a_phone_names_the_tab_after_the_project_cut_short(self):
        for w in (390, 360):
            for key, name in (("short", "Motors"), ("long", LONG)):
                p = self.got[f"phone{w}{key}"]
                where = f"phone {w} {key}"
                self.assertTrue(p["narrow"], where)
                self.assertEqual(p["title"], name, where)
                self.assertEqual(p["tab"]["text"], name, where)
                self.assertTrue(p["tab"]["tip"].startswith(name), where)
                self.header_without(p, name, where, phone=True)
                # The tab row scrolls sideways (Board was off a 390px row before #261 too, since Topics came): the
                # PO's tab is kept short, so it adds at most some 60px to what "Chat" took.
                self.assertLessEqual(p["tab"]["w"], 120, f"{where}: {p['rects']}")
                if key == "long":
                    self.assertTrue(p["tab"]["cut"], f"{where}: cut with an ellipsis: {p['tab']}")

    def test_a_long_press_on_the_cut_tab_shows_the_whole_name_once_and_no_dock_menu(self):
        lp = self.got["longPress"]
        h = lp["held"]
        self.assertEqual((h["writes"], h["shown"], h["text"]), (1, True, LONG), lp)
        self.assertTrue(h["prevented"], "Android's contextmenu is answered by the name, not Dock's tab menu")
        self.assertFalse(h["menu"], lp)
        self.assertEqual(lp["moved"], {"writes": 0, "shown": False}, "a finger that moves is a scroll")
        self.assertFalse(lp["menu"], lp)

    def test_the_page_threw_no_exception(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
