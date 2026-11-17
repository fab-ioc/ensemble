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
// What the PO's stack holds and where the task panel and each room's chat frame are.
const ST = `(() => { const d = PD.dock, L = d.layout();
  const find = n => !n ? null : n.t === 'stack' ? (n.panels.includes('po-chat') ? n : null) : (n.kids || []).map(find).find(Boolean) || null;
  const s = find(L.root), dp = document.getElementById('detail-panel');
  const host = dp && dp.parentNode;
  return { stack: s ? s.panels : null, front: d.frontOf('po-chat'), sel: SELECTED_SID ? rowBySid(SELECTED_SID).roomId : null,
    dpIn: !host ? 'detached' : host === PD.els['po-chat'] ? 'po-chat' : host.classList.contains('pd-chat-x') ? 'chat:' + host.dataset.room : 'home',
    docked: document.body.classList.contains('dp-docked'),
    frames: [...document.querySelectorAll('iframe.dp-session')].map(f => ({ room: f.dataset.room, inPane: !!f.closest('.dp-pane'), lent: f.classList.contains('pd-adopted') })),
    poTitle: d.title('po-chat') }; })()`;
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
    await step('esc', `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`, 700);
    await step('wipersAgain', `openDetail(${sid(T.Wipers)})`);
    await step('trading', `openProjectPage(${JSON.stringify(P.Trading)})`, 1500);
    await step('motors', `openProjectPage(${JSON.stringify(P.Motors)})`, 2000);
    await step('closeWipers', `PD.dock.close('chat:${T.Wipers}')`, 1200);
    await step('loose', `openDetail(${sid(T.Loose)})`);
    await step('brakesBack', `openDetail(${sid(T.Brakes)})`, 1500);
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
                "P": cls.ids, "T": cls.tasks, "shots": shots, "dump": os.environ.get("ENS_DUMP", "")}
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
        self.assertEqual(g["wipers"]["poTitle"], "Motors", "the PO's tab keeps the project's name")

    def test_the_po_tab_closes_the_task_and_its_tab_opens_it_again_on_the_same_page(self):
        g, T = self.got, self.tasks
        self.no_task(g["poTab"], "PO tab")
        self.assertEqual(len(g["poTab"]["stack"]), 3, "the tabs stay")
        self.in_tab(g["brakesTab"], T["Brakes"], "Brakes tab")
        self.assertTrue(g["sameFrame"], "the chat page was kept, not reloaded")

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

    def test_the_page_threw_no_exception(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
