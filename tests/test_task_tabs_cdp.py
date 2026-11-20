"""#386 (the CEO's P374): on a computer, a task of a project with a PO opens as a tab of its own.

The PO's tab stays first (titled by the project); each task opened is a new tab after the last one of the PO's
stack, with the task panel (its state line, its tools on the strip) in it, and the tab's own chat page lent to it:
going back to a tab is the same page, not a new one. The PO's tab in front (a click, Esc) closes the task (its tab
stays); its tab closed closes the task. A project switch puts the tabs away and brings them back with the task in
front open again. A task of no project (Unassigned) keeps the middle.

In headless Chrome over CDP, against a hub in a thread: Motors (a PO, Brakes and Wipers), Trading (a PO, Stops) and
an Unassigned task. ``ENS_SHOT_DIR`` keeps screenshots. Skipped without Node or Chrome.
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

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
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
// What the PO's stack holds and where the task panel and each room's chat frame are.
const ST = `(() => { const d = PD.dock, L = d.layout();
  const find = n => !n ? null : n.t === 'stack' ? (n.panels.includes('po-chat') ? n : null) : (n.kids || []).map(find).find(Boolean) || null;
  const s = find(L.root), dp = document.getElementById('detail-panel');
  const host = dp && dp.parentNode;
  return { stack: s ? s.panels : null, front: d.frontOf('po-chat'), sel: SELECTED_SID ? rowBySid(SELECTED_SID).roomId : null,
    dpIn: !host ? 'detached' : host === PD.els['po-chat'] ? 'po-chat' : host.classList.contains('pd-chat-x') ? 'chat:' + host.dataset.room : 'home',
    docked: document.body.classList.contains('dp-docked'),
    frames: [...document.querySelectorAll('iframe.dp-session')].map(f => ({ room: f.dataset.room, inPane: !!f.closest('.dp-pane'), lent: f.classList.contains('pd-adopted') })),
    poTitle: d.title('po-chat'), pid: PD.pid }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const shot = async (name) => { if (!A.shots) return;
    const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
    fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  const T = A.T, P = A.P, sid = room => `ALL_ROWS.find(x => x.roomId === ${JSON.stringify(room)}).sessionId`;
  const step = async (name, js, ms = 900) => { await evalIn(js + '; 0'); await sleep(ms); out[name] = await evalIn(ST); };
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.length >= 6', 30000);
    await evalIn(`(() => { SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(P.Motors)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    await until(`!!PD.dock && document.body.classList.contains('po-dock') && PD.restored && PD.pid === ${JSON.stringify(P.Motors)}`, 30000);
    await sleep(600);
    await step('brakes', `openDetail(${sid(T.Brakes)})`);
    await evalIn(`(() => { const f = document.querySelector('iframe.dp-session[data-room="${T.Brakes}"]'); window.__brakes = f; return 0; })()`);
    await until(`(() => { try { return !!window.__brakes.contentWindow.document.body; } catch (e) { return false; } })()`, 15000);
    await evalIn(`window.__brakes.contentWindow.__mark = 7; 0`);
    await step('wipers', `openDetail(${sid(T.Wipers)})`);
    await shot('task-tabs');
    await step('poTab', `PD.dock.activate('po-chat')`);
    await step('brakesTab', `PD.dock.activate('chat:${T.Brakes}')`);
    out.sameFrame = await evalIn(`(() => { const f = document.querySelector('iframe.dp-session[data-room="${T.Brakes}"]'); return f === window.__brakes && f.contentWindow.__mark === 7; })()`);
    // The task's actions are in its tab's ⋯, above the common tail (was test_task_dock_panel's, #150).
    out.taskMenuOpen = await evalIn(DK_MENU_OPEN()); await sleep(200);
    out.taskMenu = (await evalIn(DK_MENU_ROWS)).map(r => r.text);
    await evalIn(DK_MENU_OPEN()); await sleep(300);
    out.taskMenuClosed = await evalIn(`!document.querySelector('.dk-menu [data-dk-app]')`);
    await step('esc', `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`, 700);
    await step('wipersAgain', `openDetail(${sid(T.Wipers)})`);
    await step('trading', `openProjectPage(${JSON.stringify(P.Trading)})`, 1500);
    await step('motors', `openProjectPage(${JSON.stringify(P.Motors)})`, 2000);
    await step('closeWipers', `PD.dock.close('chat:${T.Wipers}')`, 1200);
    await step('loose', `openDetail(${sid(T.Loose)})`);
    await step('brakesBack', `openDetail(${sid(T.Brakes)})`, 1500);
    // Review 1, finding 1: the tab in front comes back in front, not the last one.
    await step('wipers2', `openDetail(${sid(T.Wipers)})`, 1200);
    await step('brakes2', `openDetail(${sid(T.Brakes)})`, 1200);
    await step('trading2', `openProjectPage(${JSON.stringify(P.Trading)})`, 1500);
    await step('motors2', `openProjectPage(${JSON.stringify(P.Motors)})`, 2000);
    // Finding 3: a phone width and back keeps one chat page for the room, the tab's.
    await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
    await sleep(1500);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
    await sleep(1500);
    out.resized = await evalIn(ST);
    // Finding 4: a task of another project opened beside (middle click) is added on its project's screen.
    await step('trading3', `openProjectPage(${JSON.stringify(P.Trading)})`, 1500);
    out.besideOk = await evalIn(`pdRowPanel(${sid(T.Wipers)})`);
    await sleep(1500);
    out.beside = await evalIn(ST);
    out.besideOrphans = await evalIn(`PD.dock.panels().filter(p => p.id.startsWith('chat:') && !PD.rt.has(p.id)).map(p => p.id)`);
    // The drawing (P374): icons, a pin that keeps a tab across a switch and a reload, the ⋯ tail, no Panels ▾.
    const EXTRAS = `(() => { const w = id => { const t = document.querySelector('#po-dock .dk-tab[data-dk-tab="' + id + '"]'), wr = t && t.closest('.dk-tab-wrap');
        if (!wr) return null; const ico = wr.querySelector('.pd-tab-ico'), pin = wr.querySelector('.pd-pin');
        return { ico: !!ico, cur: !!ico && ico.classList.contains('pd-ico-cur'), shown: !!ico && getComputedStyle(ico).display !== 'none' && ico.getBoundingClientRect().width > 0,
                 pin: !!pin, on: !!pin && pin.classList.contains('on') }; };
      const gi = document.getElementById('proj-switch-ico'), gr = gi && gi.getBoundingClientRect();
      const pt = document.querySelector('#po-dock .dk-tab[data-dk-tab="po-chat"]');
      return { pid: PD.pid, po: w('po-chat'), poTab: !!pt && pt.getBoundingClientRect().width > 0,
               grpIco: !!gi && !!gi.querySelector('.ldp-ico, svg, img, span') && gr.width > 0 && getComputedStyle(gi).display !== 'none',
               brakes: w('chat:${T.Brakes}'), brakesOpen: PD.rt.has('chat:${T.Brakes}') && PD.dock.isVisible('chat:${T.Brakes}'),
               panels: !!document.querySelector('.pd-panels'), wipers: PD.dock.panels().some(p => p.id === 'chat:${T.Wipers}'),
               orphans: PD.dock.panels().filter(p => p.id.startsWith('chat:') && !PD.rt.has(p.id)).map(p => p.id) }; })()`;
    out.extras = await evalIn(EXTRAS);
    await evalIn(`(() => { const t = document.querySelector('#po-dock .dk-tab[data-dk-tab="chat:${T.Brakes}"]'); t && t.closest('.dk-tab-wrap').dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); return 0; })()`);
    await shot('tabs-motors');
    await evalIn(`document.querySelector('#po-dock [data-pd-pin="chat:${T.Brakes}"]').click(); 0`);
    await sleep(400);
    await evalIn(`openProjectPage(${JSON.stringify(P.Trading)}); 0`);
    await sleep(1500);
    out.pinnedAway = await evalIn(EXTRAS);
    await shot('pinned-away');
    await evalIn('location.reload(); 0');
    await sleep(1000);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.length >= 6 && window.ensBooted === true', 30000);
    await evalIn(`(() => { if (SELECTED_PROJECT !== ${JSON.stringify(P.Trading)}) { SELECTED_PROJECT = ${JSON.stringify(P.Trading)}; PROJECT_TAB = 'tasks'; renderRows(); } return 0; })()`);
    await until(`!!PD.dock && PD.restored && PD.pid === ${JSON.stringify(P.Trading)}`, 30000);
    await sleep(1500);
    out.pinnedReload = await evalIn(EXTRAS);
    out.menuOpen = await evalIn(DK_MENU_OPEN());
    out.menuRows = await evalIn(DK_MENU_ROWS);
    await shot('menu-tail');
    await evalIn(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); 0`);
    // The PO's live check against the drawing (P374): the bar's trail with the
    // PO in front, a pinned tab's icon in an Unassigned task, a float's place.
    const TRAIL = `(() => ({ front: PD.dock.frontOf('po-chat'), trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].map(e => [e.dataset.crumb, e.textContent]),
      barTabs: document.body.classList.contains('bar-tabs'), grpOn: document.getElementById('bar-grp').classList.contains('on'),
      tabs: [...document.querySelectorAll('.dk-head.bt-head .dk-tab.on')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.textContent.trim()) }))()`;
    const TABX = id => `(() => { const id = ${JSON.stringify(id)}, t = document.querySelector('#po-dock .dk-tab[data-dk-tab="' + id + '"]'), wr = t && t.closest('.dk-tab-wrap');
        if (!wr) return null; const ico = wr.querySelector('.pd-tab-ico');
        return { ico: !!ico, cur: !!ico && ico.classList.contains('pd-ico-cur'), shown: !!ico && getComputedStyle(ico).display !== 'none' && ico.getBoundingClientRect().width > 0 }; })()`;
    const ADDFILE = (f, where) => `(() => { const v = wsView({ kind: 'project', projectId: ${JSON.stringify(P.Motors)}, docs: false }); const t = wsTabOpen(v, ${JSON.stringify(f)});
        return wsPanelAdd(v, t, ${JSON.stringify(where)}) ? wsPanelId(t.path) : null; })()`;
    await evalIn(`openProjectPage(${JSON.stringify(P.Motors)}); 0`); await sleep(2000);
    out.notesId = await evalIn(ADDFILE(A.files.notes, { beside: 'po-chat', side: 'right' }));
    await sleep(600);
    await evalIn(`PD.dock.activate('po-chat'); 0`); await sleep(600);
    out.trailPo = await evalIn(TRAIL);
    out.planId = await evalIn(ADDFILE(A.files.plan, { kind: 'stack', stack: 'po-chat' }));
    await sleep(600);
    await evalIn(`PD.dock.activate(${JSON.stringify(out.planId)}); 0`); await sleep(600);
    out.trailFile = await evalIn(TRAIL);
    await evalIn(`PD.dock.activate('po-chat'); 0`); await sleep(400);
    await evalIn(`pdWsPin(${JSON.stringify(out.notesId)}, true); 0`); await sleep(400);
    out.notesHome = await evalIn(TABX(out.notesId));
    await evalIn(`openDetail(${sid(T.Loose)}); 0`); await sleep(1500);
    out.notesLoose = Object.assign(await evalIn(TABX(out.notesId)) || {}, { pid: await evalIn('PD.pid'), here: await evalIn('pdWsHere()') });
    await evalIn(`openProjectPage(${JSON.stringify(P.Motors)}); 0`); await sleep(2000);
    // A task's tab floated low on the screen, then a round trip through the
    // Unassigned task, twice: it comes back in the very same place (the dock
    // is shorter for a moment during the switch, and Dock fitted it in).
    await evalIn(`openDetail(${sid(T.Wipers)}); 0`); await sleep(1500);
    const W = 'chat:' + T.Wipers;
    const FLOAT = `(() => { const f = (PD.dock.layout().floats || []).find(x => x.stack.panels.includes(${JSON.stringify(W)}));
        const t = document.querySelector('[data-dk-tab="${W}"]'), box = t && t.closest('.dk-float'), r = box && box.getBoundingClientRect();
        return { mode: PD.dock.viewMode(${JSON.stringify(W)}), rect: f ? [f.x, f.y, f.w, f.h] : null, dom: r ? [r.x, r.y, r.width, r.height].map(Math.round) : null }; })()`;
    out.floatOk = await evalIn(`PD.dock.setViewMode(${JSON.stringify(W)}, 'float')`); await sleep(600);
    out.floatLow = await evalIn(`(() => { const f = (PD.dock.layout().floats || []).find(x => x.stack.panels.includes(${JSON.stringify(W)})); if (!f) return null;
        f.y = Math.round(PD_ROOT.getBoundingClientRect().height - f.h); PD.dock.render(); return f.y; })()`);
    await sleep(400);
    out.floats = [await evalIn(FLOAT)];
    for (let i = 0; i < 2; i++) {
      await evalIn(`openDetail(${sid(T.Loose)}); 0`); await sleep(1500);
      out.floats.push(Object.assign(await evalIn(FLOAT), { away: true }));
      await evalIn(`openProjectPage(${JSON.stringify(P.Motors)}); 0`); await sleep(2000);
      out.floats.push(await evalIn(FLOAT));
    }
    await evalIn(`(() => { pdWsPin(${JSON.stringify(out.notesId)}, false); for (const id of [${JSON.stringify(out.notesId)}, ${JSON.stringify(out.planId)}, ${JSON.stringify(W)}]) { try { PD.dock.close(id); } catch (e) {} } return 0; })()`);
    await sleep(600);
    // A phone (review 2: was test_task_dock_panel's): the task opens in the
    // middle; the tab row's ⋯ holds its actions, nothing of Dock's that moves a
    // panel, and Rename… there runs the page's handler.
    await c.send('Emulation.setDeviceMetricsOverride', { width: 430, height: 932, deviceScaleFactor: 1, mobile: true }, sessionId);
    await sleep(1500);
    await evalIn(`openProjectPage(${JSON.stringify(P.Motors)}); 0`);
    await sleep(1500);
    await evalIn(`openDetail(${sid(T.Brakes)}); 0`);
    await until('!!SELECTED_SID && pdTask()', 15000); await sleep(800);
    await evalIn('window.__prompted = null; window.prompt = (m, c) => { window.__prompted = [m, c]; return null; }; 0');
    out.phoneMenuOpen = await evalIn(DK_MENU_OPEN());
    await sleep(300);
    out.phoneMenu = await evalIn(`(() => { const m = document.querySelector('.dk-menu.dk-options'); if (!m) return null;
      return { subs: [...m.querySelectorAll('[data-dk-sub]')].map(b => b.dataset.dkSub), rows: [...m.querySelectorAll('[data-dk-app]')].map(b => b.textContent.replace('▸', '').trim()),
               scrollW: document.documentElement.scrollWidth, vw: innerWidth }; })()`);
    out.phonePick = { found: await evalIn(DK_MENU_PICK('Rename')) };
    await sleep(400);
    Object.assign(out.phonePick, await evalIn(`({ prompted: window.__prompted, menuOpen: !!document.querySelector('.dk-menu.dk-options') })`));
    out.errors = c.exceptions;
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TaskTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-tt-", ignore_cleanup_errors=True)
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
        cls.ids, cls.tasks = {}, {}
        def room(title, home):
            t = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                             {"identity": "codex", "agent": "codex", "cwd": str(home)}])
            chatroom.post_message(t["id"], "user", "About " + title)
            return t["id"]
        for name, tasks in (("Motors", ("Brakes", "Wipers")), ("Trading", ("Stops",))):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            cls.ids[name] = proj["id"]
            if name == "Motors":
                (home / "NOTES.md").write_text("# Notes\n", encoding="utf-8")
                (home / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
                cls.files = {"notes": str(home / "NOTES.md"), "plan": str(home / "PLAN.md")}
            for t in tasks:
                cls.tasks[t] = room(t, home)
                dashboard.assign_session_project(cls.tasks[t], proj["id"])
        cls.tasks["Loose"] = room("Loose", cls.root)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENS_SHOT_DIR", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "P": cls.ids, "T": cls.tasks, "files": cls.files, "shots": shots, "dump": os.environ.get("ENS_DUMP", "")}
        script = base / "tt_cdp.js"
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

    def in_tab(self, s, room, where):
        self.assertEqual(s["sel"], room, where)
        self.assertEqual(s["dpIn"], "chat:" + room, f"{where}: the task panel is in its tab: {s}")
        self.assertTrue(s["docked"], where)
        self.assertEqual(s["front"], "chat:" + room, f"{where}: its tab is in front: {s}")
        mine = [f for f in s["frames"] if f["room"] == room]
        self.assertEqual(len(mine), 1, f"{where}: one chat page for the room: {s['frames']}")
        self.assertTrue(mine[0]["inPane"] and mine[0]["lent"], f"{where}: the tab's page, lent to the panel: {mine}")

    def no_task(self, s, where):
        self.assertIsNone(s["sel"], where)
        self.assertEqual(s["dpIn"], "home", where)
        self.assertEqual(s["front"], "po-chat", f"{where}: the PO's tab in front: {s}")
        self.assertFalse(any(f["inPane"] for f in s["frames"]), f"{where}: every chat back in its tab: {s['frames']}")

    def test_a_task_opens_as_a_tab_after_the_po_tab(self):
        g, T = self.got, self.tasks
        self.assertEqual(g["brakes"]["stack"], ["po-chat", "chat:" + T["Brakes"]])
        self.in_tab(g["brakes"], T["Brakes"], "Brakes")
        self.assertEqual(g["wipers"]["stack"], ["po-chat", "chat:" + T["Brakes"], "chat:" + T["Wipers"]], "after the last tab")
        self.in_tab(g["wipers"], T["Wipers"], "Wipers")
        self.assertEqual(g["wipers"]["poTitle"], "PO", "the PO's tab is titled PO (the drawing); its icon names the project")

    def test_the_po_tab_closes_the_task_and_its_tab_opens_it_again_on_the_same_page(self):
        g, T = self.got, self.tasks
        self.no_task(g["poTab"], "PO tab")
        self.assertEqual(len(g["poTab"]["stack"]), 3, "the tabs stay")
        self.in_tab(g["brakesTab"], T["Brakes"], "Brakes tab")
        self.assertTrue(g["sameFrame"], "the chat page was kept, not reloaded")

    def test_the_task_tabs_menu_holds_its_actions_then_the_common_tail(self):
        m = self.got["taskMenu"]
        self.assertTrue(self.got["taskMenuOpen"], m)
        for item in ("Rename…", "Agents and models…", "Delete task…"):
            self.assertIn(item, m)
        self.assertLess(m.index("Delete task…"), m.index("Reset layout"), "the task's actions first")
        self.assertEqual(m[-1], "Reset layout")
        self.assertTrue(self.got["taskMenuClosed"], "the ⋯ closes again")

    def test_esc_goes_back_to_the_po_tab(self):
        self.no_task(self.got["esc"], "Esc")
        self.assertEqual(len(self.got["esc"]["stack"]), 3)

    def test_a_project_switch_puts_the_tabs_away_and_back(self):
        g, T = self.got, self.tasks
        self.in_tab(g["wipersAgain"], T["Wipers"], "Wipers again")
        self.assertEqual(g["trading"]["stack"], ["po-chat"], g["trading"])
        self.assertIsNone(g["trading"]["sel"])
        self.assertEqual(g["motors"]["stack"], ["po-chat", "chat:" + T["Brakes"], "chat:" + T["Wipers"]])
        self.in_tab(g["motors"], T["Wipers"], "back on Motors: the task in front is open")

    def test_closing_the_tab_closes_its_task(self):
        g, T = self.got, self.tasks
        s = g["closeWipers"]
        self.assertEqual(s["stack"], ["po-chat", "chat:" + T["Brakes"]])
        self.assertNotEqual(s["sel"], T["Wipers"])
        self.assertNotEqual(s["dpIn"], "detached")
        self.assertFalse(any(f["room"] == T["Wipers"] for f in s["frames"]), s["frames"])

    def test_an_unassigned_task_keeps_the_middle(self):
        g, T = self.got, self.tasks
        s = g["loose"]
        self.assertEqual(s["sel"], T["Loose"])
        self.assertEqual(s["dpIn"], "po-chat", s)
        self.assertEqual(s["stack"], ["po-chat"], s)
        self.in_tab(g["brakesBack"], T["Brakes"], "Brakes from the Unassigned task")

    def test_the_tab_in_front_comes_back_in_front(self):
        g, T = self.got, self.tasks
        self.assertEqual(g["brakes2"]["stack"], ["po-chat", "chat:" + T["Brakes"], "chat:" + T["Wipers"]], g["brakes2"])
        self.in_tab(g["brakes2"], T["Brakes"], "Brakes in front, Wipers last")
        self.assertEqual(g["trading2"]["stack"], ["po-chat"])
        self.in_tab(g["motors2"], T["Brakes"], "back on Motors: Brakes (in front when put away), not Wipers (the last tab)")

    def test_a_phone_width_and_back_keeps_the_tabs_page(self):
        self.in_tab(self.got["resized"], self.tasks["Brakes"], "after 390 px and back")

    def test_a_task_of_another_project_opened_beside_switches_to_its_project(self):
        g, T = self.got, self.tasks
        self.assertEqual(g["trading3"]["pid"], self.ids["Trading"])
        self.assertTrue(g["besideOk"], g["beside"])
        self.assertEqual(g["beside"]["pid"], self.ids["Motors"], g["beside"])
        self.assertIn("chat:" + T["Wipers"], g["beside"]["stack"], g["beside"])
        self.assertEqual(g["besideOrphans"], [], "its tab, brought back by the switch, is the page's (not added twice)")

    def test_the_tabs_carry_icons_and_pins_as_drawn(self):
        g = self.got
        x = g["extras"]
        self.assertEqual(x["pid"], self.ids["Motors"], x)
        # #393: the PO's own tab is not drawn on a computer; the bar's project group is
        # it, with the project's icon (project ▾), and it has no pin.
        self.assertFalse(x["poTab"], f"no PO tab in the bar: {x}")
        self.assertTrue(x["grpIco"], f"the project group shows its project's icon: {x}")
        self.assertFalse(x["po"]["pin"], f"the PO's tab has no pin: {x}")
        self.assertTrue(x["brakes"]["ico"] and x["brakes"]["cur"] and not x["brakes"]["shown"], f"this project's task tab: no icon in the page: {x}")
        self.assertTrue(x["brakes"]["pin"] and not x["brakes"]["on"], x)
        self.assertFalse(x["panels"], "no Panels ▾ on a computer")

    def test_a_pinned_tab_stays_across_a_switch_and_a_reload(self):
        for k in ("pinnedAway", "pinnedReload"):
            x = self.got[k]
            self.assertEqual(x["pid"], self.ids["Trading"], f"{k}: {x}")
            self.assertTrue(x["brakesOpen"], f"{k}: the pinned Brakes is still open on Trading: {x}")
            self.assertTrue(x["brakes"] and x["brakes"]["on"], f"{k}: its pin is on: {x}")
            self.assertTrue(x["brakes"]["ico"] and not x["brakes"]["cur"] and x["brakes"]["shown"], f"{k}: Motors' icon shows: {x}")
            self.assertFalse(x["wipers"], f"{k}: the unpinned Wipers stayed with Motors: {x}")
            self.assertEqual(x["orphans"], [], k)

    def test_every_menu_ends_with_reset_layout(self):
        self.assertTrue(self.got["menuOpen"], self.got["menuRows"])
        texts = [r["text"] for r in self.got["menuRows"]]
        self.assertEqual(texts[-1], "Reset layout", texts)
        self.assertIn("Inbox", texts)

    def test_a_phones_menu_holds_the_tasks_actions_and_they_run(self):
        g = self.got
        self.assertTrue(g["phoneMenuOpen"], g.get("phoneMenu"))
        m = g["phoneMenu"]
        self.assertEqual(m["subs"], [], f"nothing of Dock's that moves a panel on a phone: {m}")
        for label in ("Rename…", "Agents and models…", "Delete task…"):
            self.assertIn(label, m["rows"])
        self.assertNotIn("Reset layout", m["rows"], "no common tail on a phone")
        self.assertLessEqual(m["scrollW"], m["vw"])
        k = g["phonePick"]
        self.assertTrue(k["found"] and not k["menuOpen"] and k["prompted"] and k["prompted"][0].startswith("Label for this session"), k)

    def test_a_computers_bar_has_no_trail_the_tabs_name_it(self):
        # The PO's live check (P374, row 1): a file open in a stack beside the PO's adds no trail.
        # #393: on a computer the trail is the project alone; a file in front of the PO's own
        # stack is named on its tab in the bar, the project's group no longer underlined.
        g = self.got
        self.assertTrue(g["notesId"] and g["planId"], g)
        po, f = g["trailPo"], g["trailFile"]
        self.assertEqual((po["front"], po["trail"], po["barTabs"], po["grpOn"]), ("po-chat", [], True, True), po)
        self.assertEqual(f["front"], g["planId"])
        self.assertEqual(f["trail"], [], f)
        self.assertFalse(f["grpOn"], f)
        self.assertTrue(any("PLAN.md" in t for t in f["tabs"]), f)

    def test_a_pinned_tab_shows_its_projects_icon_in_an_unassigned_task(self):
        # Row 5: the Unassigned task keeps Motors' dock (PD.pid), yet Motors' pinned file is another's there.
        home, loose = self.got["notesHome"], self.got["notesLoose"]
        self.assertTrue(home["ico"] and home["cur"] and not home["shown"], f"on Motors, its own: no icon: {home}")
        self.assertEqual(loose["pid"], self.ids["Motors"], loose)
        self.assertEqual(loose["here"], "task:" + self.tasks["Loose"], loose)
        self.assertTrue(loose["ico"] and not loose["cur"] and loose["shown"], f"Motors' icon shows: {loose}")

    def test_a_floating_task_comes_back_in_the_same_place(self):
        # Row 4: after a round trip through the Unassigned task it came back higher.
        g = self.got
        self.assertTrue(g["floatOk"])
        first = g["floats"][0]
        self.assertEqual(first["mode"], "float", first)
        self.assertEqual(first["rect"][1], g["floatLow"], "put low on the screen")
        for n, f in enumerate(g["floats"][1:], 1):
            with self.subTest(n=n):
                if f.get("away"):
                    self.assertIsNone(f["mode"], f"put away in the Unassigned task: {f}")
                else:
                    self.assertEqual((f["mode"], f["rect"], f["dom"]), ("float", first["rect"], first["dom"]), f)

    def test_the_page_threw_no_exception(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
