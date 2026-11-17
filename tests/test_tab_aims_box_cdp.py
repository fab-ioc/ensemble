"""GitHub issue 57: the workspace's message box sends to the chat the person brought to the front.

On a computer the workspace has one Send box with a To: line (#271). Since #386 a task of a project with a PO
is a tab of its own after the PO's tab. Whatever way a tab comes to the front by the person (a click on it,
a task opened from the list, the tab in front closed, Esc, a project switch bringing the tabs back, the arrow
keys on the tabs), the To: line names that chat and a message sent goes to its room. Each target keeps its
own draft. A chat that opens by itself never takes the box (#271).

In headless Chrome over CDP, against a hub in a thread: Motors (a PO, Brakes and Wipers) and Trading (a PO,
Stops). Clicks and keys are real input events (Input.dispatch*). The send is intercepted in the box's page
(postOk): nothing is posted. ``ENS_SHOT_DIR`` keeps screenshots. Skipped without Node or Chrome.
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
// Where the box is aimed, what the To: line says, which chat is in front.
const ST = `(() => { const f = document.getElementById('workspace-compose-frame');
  return { front: PD.dock.frontOf('po-chat'), sel: SELECTED_SID ? (rowBySid(SELECTED_SID) || {}).roomId || null : null,
    target: WC.target ? WC.target.kind + ':' + WC.target.room : null, to: document.getElementById('workspace-compose-link').textContent,
    page: (() => { try { const m = (f.contentDocument.URL || '').match(/[?&]id=([^&]+)/); return m ? decodeURIComponent(m[1]) : null; } catch (e) { return null; } })(),
    pid: PD.pid }; })()`;
// The box's Send pressed with a probe text, the request caught in the box's page (nothing is posted).
const PROBE = name => `(async () => { const f = document.getElementById('workspace-compose-frame');
  for (let n = 0; n < 60; n++) { const d = f.contentDocument; if (d && d.readyState === 'complete' && d.getElementById('send') && !d.getElementById('send').disabled && f.contentWindow.postOk && WC.base && d.URL.includes(WC.base)) break; await new Promise(r => setTimeout(r, 100)); }
  const d = f.contentDocument, w = f.contentWindow, old = w.postOk; let body = null;
  w.postOk = async (p, b) => { if (p === '/api/room/resume') { body = b; return {}; } return old(p, b); };
  try { const i = d.getElementById('input'); i.value = 'probe ' + ${JSON.stringify(name)}; i.dispatchEvent(new Event('input', { bubbles: true })); d.getElementById('send').click();
    for (let n = 0; n < 60 && !body; n++) await new Promise(r => setTimeout(r, 50));
    return body ? body.roomId : null; } finally { w.postOk = old; } })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { steps: {} };
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, userGesture: false }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const shot = async (name) => { if (!A.shots) return;
    const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
    fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  const T = A.T, P = A.P, sid = room => `ALL_ROWS.find(x => x.roomId === ${JSON.stringify(room)}).sessionId`;
  // A real click at the middle of an element of the page.
  const clickAt = async (sel) => {
    const r = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; })()`);
    if (!r) throw new Error('no element ' + sel);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: r[0], y: r[1], button: 'left', clickCount: 1 }, sessionId);
  };
  const key = async (k, code, vk) => { for (const type of ['rawKeyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }, sessionId); };
  const tab = id => `#po-dock .dk-tab[data-dk-tab="${id}"]`;
  // A step: the state settled, then the To: line and the room a message really goes to.
  const step = async (name, act, ms = 1200) => { await act(); await sleep(ms); const s = await evalIn(ST); s.sent = await evalIn(PROBE(name)); out.steps[name] = s; await shot(name); };
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.length >= 7', 30000);
    await evalIn(`(() => { SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(P.Motors)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    await until(`!!PD.dock && document.body.classList.contains('po-dock') && PD.restored && PD.pid === ${JSON.stringify(P.Motors)}`, 30000);
    await sleep(800);
    await step('start', async () => {}, 300);
    // A task opened from the list: its tab, in front, has the box.
    await step('openBrakes', () => evalIn(`openDetail(${sid(T.Brakes)}); 0`));
    await step('poTab', () => clickAt(tab('po-chat')));
    await step('brakesTab', () => clickAt(tab('chat:' + T.Brakes)));
    await step('poTab2', () => clickAt(tab('po-chat')));
    await step('openWipers', () => evalIn(`openDetail(${sid(T.Wipers)}); 0`));
    await step('brakesFromWipers', () => clickAt(tab('chat:' + T.Brakes)));
    await step('wipersTab', () => clickAt(tab('chat:' + T.Wipers)));
    // The keyboard on the tabs: the arrow to the left brings Brakes, then the PO.
    await evalIn(`document.querySelector(${JSON.stringify(tab('chat:' + T.Wipers))}).focus(); 0`);
    await step('keyLeft', () => key('ArrowLeft', 'ArrowLeft', 37));
    await step('keyLeft2', () => key('ArrowLeft', 'ArrowLeft', 37));
    await step('keyRight', () => key('ArrowRight', 'ArrowRight', 39));
    // Drafts: each target keeps its own through the switches.
    await evalIn(`(() => { const f = document.getElementById('workspace-compose-frame'), i = f.contentDocument.getElementById('input'); i.value = 'draft brakes'; i.dispatchEvent(new Event('input', { bubbles: true })); f.contentWindow.edStoreNow(); return 0; })()`);
    await clickAt(tab('po-chat')); await sleep(1500);
    out.draftPo = await evalIn(`document.getElementById('workspace-compose-frame').contentDocument.getElementById('input').value`);
    await clickAt(tab('chat:' + T.Brakes)); await sleep(1500);
    out.draftBrakes = await evalIn(`document.getElementById('workspace-compose-frame').contentDocument.getElementById('input').value`);
    await evalIn(`(() => { const f = document.getElementById('workspace-compose-frame'), i = f.contentDocument.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); f.contentWindow.edStoreNow(); return 0; })()`);
    // The tab in front closed (its ×): the one shown next has the box.
    await clickAt(tab('chat:' + T.Wipers)); await sleep(1200);
    await step('closeWipers', () => clickAt(`#po-dock .dk-tab-x[data-dk-close="chat:${T.Wipers}"]`));
    // Esc: back to the PO's tab.
    await step('esc', () => key('Escape', 'Escape', 27));
    // A project switch and back brings the tabs, the task in front open.
    await clickAt(tab('chat:' + T.Brakes)); await sleep(1200);
    await step('trading', () => evalIn(`openProjectPage(${JSON.stringify(P.Trading)}); 0`), 2000);
    await step('motors', () => evalIn(`openProjectPage(${JSON.stringify(P.Motors)}); 0`), 2500);
    // Focus left in the PO's chat (a click in it), then the task's tab: the window losing and
    // regaining focus (another app, a browser window) gives focus back inside the PO's frame.
    await clickAt(tab('po-chat')); await sleep(1500);
    const PO_MSGS = `(() => { const f = PO_PANEL.querySelector('iframe.po-session:not([hidden])'); const b = f.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 3]; })()`;
    const at = await evalIn(PO_MSGS);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: at[0], y: at[1], button: 'left', clickCount: 1 }, sessionId);
    await sleep(800);
    out.focusAfterPoClick = await evalIn(`(() => { const a = document.activeElement; return a ? a.tagName + '.' + a.className : null; })()`);
    await step('brakesAfterPoFocus', () => clickAt(tab('chat:' + T.Brakes)));
    out.focusAfterTab = await evalIn(`(() => { const a = document.activeElement; return a ? a.tagName + '.' + a.className + ' ' + (a.dataset.dkTab || a.dataset.room || '') : null; })()`);
    const other = await c.send('Target.createTarget', { url: 'about:blank', newWindow: true });
    await sleep(500);
    await c.send('Page.bringToFront', {}, sessionId);
    await evalIn(`(() => { window.dispatchEvent(new Event('blur')); const a = document.activeElement; if (a && a.tagName === 'IFRAME') { try { const d = a.contentDocument, x = d.activeElement || d.body; x.dispatchEvent(new FocusEvent('focusin', { bubbles: true })); } catch (e) {} } return 0; })()`);
    await step('refocused', async () => {});
    try { await c.send('Target.closeTarget', { targetId: other.targetId }); } catch (e) {}
    // Two stacks side by side: Brakes's tab moved right of the PO's stack.
    await evalIn(`(() => { const find = n => !n ? null : n.t === 'stack' ? (n.panels.includes('po-chat') ? n : null) : (n.kids || []).map(find).find(Boolean) || null;
      PD.dock.moveTo('chat:${T.Brakes}', { kind: 'stack', stack: find(PD.dock.layout().root) }, 'right'); return 0; })()`);
    await sleep(1500);
    const chatAt = sel => `(() => { const f = ${sel}; if (!f) return null; const b = f.getBoundingClientRect(); return b.width ? [b.x + b.width / 2, b.y + b.height / 3] : null; })()`;
    const clickXY = async xy => { if (!xy) throw new Error('nothing to click'); for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: xy[0], y: xy[1], button: 'left', clickCount: 1 }, sessionId); };
    out.split = await evalIn(`(() => { const L = PD.dock.layout(); return JSON.stringify(L.root).slice(0, 400); })()`);
    await step('splitPoBody', async () => clickXY(await evalIn(chatAt(`PO_PANEL.querySelector('iframe.po-session:not([hidden])')`))));
    await step('splitBrakesTab', () => clickAt(tab('chat:' + T.Brakes)));
    await step('splitPoTab', () => clickAt(tab('po-chat')));
    await step('splitBrakesBody', async () => clickXY(await evalIn(chatAt(`document.querySelector('iframe.dp-session[data-room="${T.Brakes}"]')`))));
    await step('splitPoTab2', () => clickAt(tab('po-chat')));
    // An Unassigned task's chat as a tab of the PO's stack (Open in new panel).
    await step('loosePanel', () => evalIn(`pdChatOpen(${JSON.stringify(T.Loose)}); 0`), 1500);
    await step('loosePo', () => clickAt(tab('po-chat')));
    await step('looseTab', () => clickAt(tab('chat:' + T.Loose)));
    // A chat that comes by itself (a panel brought back, no press of the person's) never takes the box (#271).
    await sleep(1600);
    await step('byItself', () => evalIn(`pdChatOpen(${JSON.stringify(T.Idle)}, { restore: true }); 0`), 1500);
    out.idleShown = await evalIn(`PD.rt.has('chat:${T.Idle}') && PD.dock.isShown('chat:${T.Idle}')`);
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
class TabAimsBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-tab-box-", ignore_cleanup_errors=True)
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
        cls.ids, cls.tasks, cls.pos = {}, {}, {}

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
            cls.ids[name], cls.pos[name] = proj["id"], po["id"]
            for t in tasks:
                cls.tasks[t] = room(t, home)
                dashboard.assign_session_project(cls.tasks[t], proj["id"])
        cls.tasks["Loose"] = room("Loose", cls.root)
        cls.tasks["Idle"] = room("Idle", cls.root)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENS_SHOT_DIR", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "P": cls.ids, "T": cls.tasks, "shots": shots}
        script = base / "tab_box_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENS_DUMP"):
            print(json.dumps(cls.got, indent=1), file=sys.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def aimed(self, name, kind, room, label):
        s = self.got["steps"][name]
        self.assertEqual(s["target"], f"{kind}:{room}", f"{name}: the box's target ({s})")
        self.assertEqual(s["page"], room, f"{name}: the box's page is the room's ({s})")
        self.assertIn(label, s["to"], f"{name}: the To: line ({s})")
        self.assertEqual(s["sent"], room, f"{name}: a message sent goes to that room ({s})")

    def at_po(self, name, project="Motors"):
        self.aimed(name, "po", self.pos[project], "PO")

    def at_task(self, name, task):
        self.aimed(name, "task", self.tasks[task], task)

    def test_a_task_opened_has_the_box(self):
        self.at_po("start")
        self.at_task("openBrakes", "Brakes")
        self.at_task("openWipers", "Wipers")

    def test_a_click_on_a_tab_aims_the_box_at_its_chat(self):
        self.at_po("poTab")
        self.at_task("brakesTab", "Brakes")
        self.at_po("poTab2")
        self.at_task("brakesFromWipers", "Brakes")
        self.at_task("wipersTab", "Wipers")

    def test_the_arrow_keys_on_the_tabs_aim_the_box(self):
        self.at_task("keyLeft", "Brakes")
        self.at_po("keyLeft2")
        self.at_task("keyRight", "Brakes")

    def test_each_target_keeps_its_own_draft(self):
        self.assertEqual(self.got["draftPo"], "")
        self.assertEqual(self.got["draftBrakes"], "draft brakes")

    def test_the_tab_in_front_closed_gives_the_box_to_the_chat_shown_next(self):
        s = self.got["steps"]["closeWipers"]
        if s["front"] == "po-chat":
            self.at_po("closeWipers")
        else:
            self.assertEqual(s["front"], "chat:" + self.tasks["Brakes"], s)
            self.at_task("closeWipers", "Brakes")

    def test_esc_gives_the_box_to_the_po(self):
        self.at_po("esc")

    def test_a_project_switch_aims_the_box_at_the_chat_in_front(self):
        self.at_po("trading", "Trading")
        self.at_task("motors", "Brakes")

    def test_focus_left_in_the_po_chat_does_not_take_the_box_back(self):
        self.at_task("brakesAfterPoFocus", "Brakes")
        self.at_task("refocused", "Brakes")

    def test_side_by_side_the_stack_last_clicked_has_the_box(self):
        self.assertIn('"chat:' + self.tasks["Brakes"], self.got["split"])
        self.at_po("splitPoBody")
        self.at_task("splitBrakesTab", "Brakes")
        self.at_po("splitPoTab")
        self.at_task("splitBrakesBody", "Brakes")
        self.at_po("splitPoTab2")

    def test_a_chat_panel_that_is_not_a_task_tab_follows_the_same_rule(self):
        self.at_task("loosePanel", "Loose")
        self.at_po("loosePo")
        self.at_task("looseTab", "Loose")

    def test_a_chat_that_comes_by_itself_does_not_take_the_box(self):
        self.assertTrue(self.got["idleShown"], "the chat came to the front")
        self.at_task("byItself", "Loose")

    def test_no_page_errors(self):
        self.assertEqual(self.got.get("errors"), [])


if __name__ == "__main__":
    unittest.main()
