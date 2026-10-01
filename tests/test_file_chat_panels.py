"""#150: each open file, and as many chats as you like, each a Dock panel of
its own (the CEO's P105 and P110).

On a PO screen a file opened from the Files tree is a closable panel of the
dock (``file:<path>``, titled with the file's name, its path the tooltip, the
path bar over its viewer): the first beside the conversation, the next ones
tabs of the file panel last shown; the Files pane keeps the tree alone. A
task's chat opens as a panel of its own (``chat:<room>``, "#12 Title") from
the middle's ⋯ (Open in new panel, which moves the task out of the middle),
from a Ctrl/⌘ click or a middle click on its row; its ⋯ holds the task's
actions. Both kinds survive a reload in their kept places (``cd-ws-panels``,
``cd-chat-panels``), can be split, moved and closed as any panel, and on a
phone are tabs of the one column. A chat panel polls its room every 10 s
while nobody types in it, every 30 s while it is off screen (session.html).

In headless Chrome over CDP, against a hub in a thread serving the pages,
with a project that has a PO (Motors), four tasks and three files in its
folder:

* a desktop (1440×900): three files from the tree → three file panels, the
  first beside the conversation, the others its stack-mates; the Files pane
  has no tab row and no viewer; one split under the conversation stays there
  (the middle keeps only the tools out); × on a tab closes it; a reload
  brings the two back where they were;
* the middle's ⋯ › Open in new panel moves the task out into a chat panel;
  Ctrl+click and a middle click on rows open two more; each frame is its
  room's chat (a message said in it reaches its room and shows in it);
  a plain click on another row takes the middle, the panels stay; a plain
  click on a panelled task reveals its panel;
* requests per minute (/api/room, /api/ptys, /api/live) with one chat and
  with four: the three chat panels nobody types in poll at a fifth of the
  rate (the numbers go in the report);
* a phone (390×844): the panels are tabs of the one column; no sideways scroll.
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
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8")
ACTIONS = (ROOT / "static" / "actions.js").read_text(encoding="utf-8")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// The runtime panels: each one's kind, place in the layout (the path of
// child indexes down the root to its stack), box and tab; the Files pane;
// the file panels' bars; the middle.
const STATE = `(() => {
  const d = PD.dock, L = d.layout();
  const where = {};
  (function walk(n, p) { if (!n) return; if (n.t === 'stack') { n.panels.forEach(id => { where[id] = p.join('/') || 'root'; }); return; } (n.kids || []).forEach((k, i) => walk(k, p.concat(i))); })(L.root, []);
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) }; };
  const rt = [...PD.rt.entries()].map(([id, e]) => ({ id, kind: e.kind, path: e.path || null, room: e.room || null, box: box(e.el), front: d.frontOf(id) === id, visible: d.isVisible(id), where: where[id] || null, mode: d.viewMode(id) }));
  const tabs = [...document.querySelectorAll('#po-dock .dk-tab[data-dk-tab]')].map(t => ({ id: t.dataset.dkTab, text: t.textContent, title: t.title, x: !!t.parentElement.querySelector('.dk-tab-x') }));
  const ws = PD.els.workspace.querySelector('.wsp');
  const files = ws ? { panelsCls: ws.classList.contains('ws-panels'), strip: !!ws.querySelector('.wst') && !ws.querySelector('.wst').hidden, viewers: ws.querySelectorAll('iframe.wsp-frame').length,
    side: box(ws.querySelector('.wsp-side')), wsp: box(ws), tree: ws.querySelectorAll('.wsp-tree .wse.file').length } : null;
  const panels = [...document.querySelectorAll('#po-dock .wfp')].map(el => ({ bar: !!el.querySelector('.wsp-bar'), crumbs: [...el.querySelectorAll('.wsc-list li')].map(li => li.textContent),
    frame: !!el.querySelector('iframe.wsp-frame.on'), note: el.querySelector('.wsp-note').textContent, own: (el.querySelector('.wsp-own') || {}).href || '' }));
  const mid = PD.els['po-chat'].querySelector('iframe.dp-session');
  return { rt, tabs, files, panels, chat: box(PD.els['po-chat']), where, sid: SELECTED_SID || null, task: pdTask(), middleRoom: mid ? mid.dataset.room : null,
    poFrame: !!pdChatFrame() && PO_PANEL.parentNode === PD.els['po-chat'], saved: { ws: localStorage.getItem('cd-ws-panels'), chats: localStorage.getItem('cd-chat-panels') },
    vw: innerWidth, scrollW: document.documentElement.scrollWidth, narrow: d.narrow(), fly: d.flyOpen() };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    // A real click, as a person's: left, with modifier bits (2 = Ctrl), or the middle button.
    const click = async (sel, o = {}) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: o.button || 'left', clickCount: 1, modifiers: o.modifiers || 0 }, sessionId);
    };
    const ready = () => until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.tasks[0]) + ')', 30000);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await ready();
    return { evalIn, until, shot, click, ready, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  // The project's PO screen; `keep`: the browser's layout and panels as saved (a reload).
  const go = (p, proj, keep) => p.evalIn(`(() => { if (!${!!keep}) { try { ['cd-list-dock', 'cd-list-dock-axis', 'cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k));
      Object.keys(localStorage).filter(k => k.startsWith('cd-ws:')).forEach(k => localStorage.removeItem(k)); } catch (e) {} }
    SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
  const pdReady = p => p.until('!!PD.dock && document.body.classList.contains("po-dock") && !!SW_EL.querySelector(".sw-row") && PD.restored && !!PD.els.workspace.querySelector(".wsp-tree .wse.file[data-path]")', 30000);
  const row = r => `.sw-row[data-room="${r}"]`;
  const tool = id => `#po-dock .dk-strip-btn[data-dk-auto="${id}"]`;
  const fid = name => `[...PD.rt.keys()].find(k => k.startsWith('file:') && k.endsWith(${JSON.stringify(name.toLowerCase())}))`;
  // The file's panel, its viewer loaded.
  const FILE_ON = name => `(() => { const id = ${fid(name)}; const e = id && PD.rt.get(id); const f = e && e.el.querySelector('iframe.wsp-frame.on'); try { return !!f && f.contentDocument.readyState === 'complete' && f.contentWindow.location.pathname === '/fileview'; } catch (x) { return false; } })()`;
  const openFile = async (p, name) => {
    await p.evalIn(`[...PD.els.workspace.querySelectorAll('.wsp-tree .wse.file[data-path]')].find(x => x.dataset.path.endsWith(${JSON.stringify(name)})).click(); 0`);
    await p.until(FILE_ON(name), 20000); await sleep(300);
  };
  const HEAD = '#po-dock .dk-stack:has(> .dk-body > .pd-chat:not(.pd-chat-x))';
  const MENU = `(() => { const m = document.querySelector('.dk-menu.dk-options'); if (!m) return null;
    return { app: [...m.querySelectorAll('[data-dk-app]')].map(b => b.textContent), items: [...m.querySelectorAll('[data-dk-menu]')].map(b => b.dataset.dkMenu) }; })()`;
  const PICK = label => `(() => { const b = [...document.querySelectorAll('.dk-menu.dk-options [data-dk-app]')].find(x => x.textContent.startsWith(${JSON.stringify(label)})); if (!b) return false; b.click(); return true; })()`;
  const chatFrame = r => `(() => { const e = PD.rt.get('chat:' + ${JSON.stringify(r)}); return e && e.el.querySelector('iframe.dp-session'); })()`;
  const chatDrawn = r => `(() => { const f = ${chatFrame(r)}; try { return !!f && f.contentWindow.eval('typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN') === true; } catch (e) { return false; } })()`;
  const [TA, TB, TC, TD] = A.tasks;
  try {
    const p = await page(1440, 900);
    await go(p, A.proj); await pdReady(p); await sleep(800);
    // One chat (the PO's) for 12 s: the requests it makes.
    out.rate = { one: [Date.now()] };
    await sleep(12000);
    out.rate.one.push(Date.now());
    // Three files from the Files tree.
    await p.click(tool('workspace')); await p.until('PD.dock.flyOpen() === "workspace"', 5000); await sleep(300);
    for (const f of ['a.md', 'b.py', 'c.txt']) await openFile(p, f);
    out.three = await p.evalIn(STATE);
    await p.shot('150-three-files-1440');
    // One split under the conversation: it stays (only the tools are kept out of the middle).
    await p.evalIn(`(() => { const find = n => !n ? null : n.t === 'stack' ? (n.panels.includes('po-chat') ? n : null) : (n.kids || []).map(find).find(Boolean) || null;
      PD.dock.moveTo(${fid('b.py')}, { kind: 'stack', stack: find(PD.dock.layout().root) }, 'bottom'); return 0; })()`); await sleep(500);
    out.split = await p.evalIn(STATE);
    await p.shot('150-split-1440');
    // × on a tab closes the file (a real click on the front tab's ×).
    await p.evalIn(`PD.dock.activate(${fid('c.txt')}); 0`); await sleep(300);
    const cid = await p.evalIn(fid('c.txt'));
    await p.click(`#po-dock .dk-tab-x[data-dk-close="${cid}"]`); await sleep(500);
    out.closed = await p.evalIn(STATE);
    out.closed.wsSaved = await p.evalIn(`(() => { const s = JSON.parse(localStorage.getItem('cd-ws:project:' + ${JSON.stringify(A.proj)}) || '{}'); return (s.tabs || []).map(t => t.path.split(/[\\\\/]/).pop()); })()`);
    // A reload: the two come back where they were.
    try { await p.evalIn('location.reload(); 0'); } catch (e) {}
    await sleep(1500); await p.ready(); await go(p, A.proj, true); await pdReady(p);
    await p.until(FILE_ON('a.md') + ' && ' + FILE_ON('b.py'), 20000); await sleep(800);
    out.reloaded = await p.evalIn(STATE);
    await p.shot('150-reloaded-1440');
    // The task in the middle, then its ⋯ › Open in new panel: out it goes, as a panel.
    await p.click(row(TA));
    await p.until('!!SELECTED_SID && pdTask() && !!PD.els["po-chat"].querySelector("iframe.dp-session")', 15000); await sleep(600);
    await p.click(`${HEAD} [data-dk-act="menu"]`); await sleep(300);
    out.menu = await p.evalIn(MENU);
    out.picked = await p.evalIn(PICK('Open in new panel'));
    await p.until(`PD.rt.has('chat:' + ${JSON.stringify(TA)}) && !SELECTED_SID`, 10000); await sleep(600);
    out.chatA = await p.evalIn(STATE);
    await p.shot('150-chat-panel-1440');
    // Ctrl+click and a middle click on rows: two more.
    await p.click(row(TB), { modifiers: 2 });
    await p.until(`PD.rt.has('chat:' + ${JSON.stringify(TB)})`, 10000); await sleep(400);
    await p.click(row(TD), { button: 'middle' });
    await p.until(`PD.rt.has('chat:' + ${JSON.stringify(TD)})`, 10000); await sleep(400);
    out.chats = await p.evalIn(STATE);
    // The chat panel's ⋯: the task's actions, no Open in new panel.
    await p.click(`#po-dock .dk-stack:has(> .dk-body > .pd-chat-x[data-room="${TB}"]) [data-dk-act="menu"]`); await sleep(300);
    out.chatMenu = await p.evalIn(MENU);
    await p.evalIn('document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); 0'); await sleep(200);
    // A plain click on a fourth row takes the middle; the panels stay.
    await p.click(row(TC));
    await p.until(`!!SELECTED_SID && pdTask() && (PD.els["po-chat"].querySelector("iframe.dp-session") || {}).dataset?.room === ${JSON.stringify(TC)}`, 15000); await sleep(600);
    out.third = await p.evalIn(STATE);
    await p.shot('150-four-chats-1440');
    // Each panel is its room's chat: said in it, a line reaches its room and shows in it.
    for (const r of [TA, TB, TD]) await p.until(chatDrawn(r), 20000);
    out.said = await p.evalIn(`(async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms)), res = {}, rooms = ${JSON.stringify([TA, TB, TD])};
      const frameOf = r => { const e = PD.rt.get('chat:' + r); return e && e.el.querySelector('iframe.dp-session'); };
      const says = (r, text) => { try { return frameOf(r).contentDocument.body.innerText.includes(text); } catch (e) { return false; } };
      for (const r of rooms) {
        const f = frameOf(r), d = f.contentDocument, text = 'Said in the panel of ' + r;
        d.querySelector('#input').value = text;
        await f.contentWindow.fetch('/api/room/say', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ roomId: r, text: d.querySelector('#input').value }) });
        res[r] = { typed: d.querySelector('#input').value === text };
      }
      for (const r of rooms) {
        const text = 'Said in the panel of ' + r;
        const room = await fetch('/api/room?id=' + encodeURIComponent(r)).then(x => x.json());
        res[r].inRoom = (room.messages || []).some(m => m.text === text);
        let shown = false;
        for (let i = 0; i < 160 && !shown; i++) { shown = says(r, text); if (!shown) await sleep(250); }
        res[r].shown = shown;
        res[r].others = rooms.filter(o => o !== r).every(o => !says(o, text));
      }
      return res; })()`);
    // Four chats for 12 s: the requests they make.
    out.rate.four = [Date.now()];
    await sleep(12000);
    out.rate.four.push(Date.now());
    // A plain click on a panelled task reveals its panel; the middle keeps its task.
    await p.evalIn(`PD.dock.activate('chat:' + ${JSON.stringify(TB)}); 0`); await sleep(200);
    await p.click(row(TA)); await sleep(600);
    out.revealA = await p.evalIn(STATE);
    // Hidden behind a tab: the chat there polls every 30 s (its frame knows from the host's class).
    out.offScreen = await p.evalIn(`(() => { const f = ${chatFrame(TB)}; try { return { off: f.contentWindow.chatOffScreen(), slow: f.contentWindow.chatSlow(), cls: !!f.closest('.dk-off') }; } catch (e) { return String(e); } })()`);
    await p.close();
    // A phone: the panels are tabs of the one column.
    const ph = await page(390, 844, true);
    await go(ph, A.proj, true); await pdReady(ph); await sleep(1200);
    out.phone = await ph.evalIn(STATE);
    await ph.shot('150-phone-390');
    await ph.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

REQS: list = []


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class FilesAndChatsAsPanels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-fcp-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        orig_get = dashboard.Handler.do_GET

        def counted(self):
            REQS.append((time.time() * 1000, self.path))
            return orig_get(self)

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
            mock.patch.object(dashboard.Handler, "do_GET", counted),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        for name, text in (("a.md", "# A\n\nThe first file.\n"), ("b.py", "print('b')\n" * 40), ("c.txt", "c\n" * 10)):
            (home / name).write_text(text, encoding="utf-8")
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        chatroom.post_message(po["id"], "user", "Hello PO", to="claude")
        cls.tasks = []
        for title in ("Brakes that squeal", "Wipers that smear", "A horn that honks twice", "Lights that flicker"):
            t = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
            chatroom.post_message(t["id"], "user", "About: " + title)
            dashboard.assign_session_project(t["id"], cls.proj)
            cls.tasks.append(t["id"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "tasks": cls.tasks, "shots": shots}
        script = base / "fcp_cdp.js"
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

    # The requests of a kind within a window, per minute.
    @classmethod
    def per_minute(cls, window, prefix):
        t0, t1 = window
        n = sum(1 for t, p in REQS if t0 <= t <= t1 and p.startswith(prefix))
        return n * 60000 / max(1, t1 - t0)

    def files(self, g):
        return [e for e in g["rt"] if e["kind"] == "file"]

    def test_three_files_are_three_panels_beside_the_conversation(self):
        g = self.got["three"]
        f = self.files(g)
        self.assertEqual([e["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for e in f], ["a.md", "b.py", "c.txt"])
        self.assertTrue(all(e["id"].startswith("file:") and e["visible"] for e in f), f)
        self.assertEqual(len({e["where"] for e in f}), 1, "the later ones are tabs of the first's stack")
        self.assertNotEqual(f[0]["where"], g["where"]["po-chat"], "beside the conversation, not in its stack")
        self.assertGreater(f[0]["box"]["x"], g["chat"]["x"] + g["chat"]["w"] - 1, "at its right")
        self.assertEqual([e["front"] for e in f], [False, False, True], "the last opened in front")
        tabs = {t["id"]: t for t in g["tabs"] if t["id"].startswith("file:")}
        self.assertEqual(sorted(t["text"] for t in tabs.values()), ["a.md", "b.py", "c.txt"], "a tab is the file's name")
        for e in f:
            self.assertIn(e["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1], tabs[e["id"]]["title"], "its tooltip is the path")
            self.assertNotEqual(tabs[e["id"]]["title"], tabs[e["id"]]["text"])
        self.assertTrue(tabs[f[2]["id"]]["x"], "the front tab has its ×")
        self.assertEqual(len(g["panels"]), 3)
        for pnl in g["panels"]:
            self.assertTrue(pnl["bar"] and pnl["frame"] and not pnl["note"], pnl)
            self.assertTrue(pnl["crumbs"] and pnl["own"].find("/fileview?") > 0, pnl)
        fs = g["files"]
        self.assertTrue(fs["panelsCls"], "the Files pane is the tree alone")
        self.assertFalse(fs["strip"], "no tab row in Files")
        self.assertEqual(fs["viewers"], 0, "no viewer in Files")
        self.assertGreaterEqual(fs["side"]["w"], fs["wsp"]["w"] - 2, "the tree takes the whole pane")
        self.assertLessEqual(g["scrollW"], g["vw"])
        self.assertIn('"kind":"project"', g["saved"]["ws"] or "", "the Workspace is remembered as having panels")

    def test_a_file_split_under_the_conversation_stays_there(self):
        g = self.got["split"]
        b = next(e for e in self.files(g) if e["path"].endswith("b.py"))
        self.assertNotEqual(b["where"], g["where"]["po-chat"])
        self.assertGreaterEqual(b["box"]["y"], g["chat"]["y"] + g["chat"]["h"] - 6, "under the conversation")
        self.assertLess(abs(b["box"]["x"] - g["chat"]["x"]), 6, "in its column")

    def test_the_tabs_close_closes_the_file_and_a_reload_brings_the_rest_back(self):
        g = self.got["closed"]
        names = [e["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for e in self.files(g)]
        self.assertEqual(sorted(names), ["a.md", "b.py"], "c.txt closed")
        self.assertEqual(sorted(g["wsSaved"]), ["a.md", "b.py"], "its tab went from the Workspace's saved tabs")
        r = self.got["reloaded"]
        back = {e["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]: e for e in self.files(r)}
        self.assertEqual(sorted(back), ["a.md", "b.py"])
        for e in self.files(g):
            name = e["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            self.assertEqual(back[name]["where"], e["where"], f"{name} is back in its place")
            self.assertEqual(back[name]["box"]["y"] > g["chat"]["y"] + 20, e["box"]["y"] > g["chat"]["y"] + 20, name)
        self.assertEqual(len(r["panels"]), 2)
        self.assertTrue(all(pnl["frame"] for pnl in r["panels"]), "each viewer loaded again")

    def test_open_in_new_panel_moves_the_task_out_of_the_middle(self):
        m = self.got["menu"]
        self.assertTrue(m and m["app"] and m["app"][0] == "Open in new panel", m)
        self.assertTrue(self.got["picked"])
        g = self.got["chatA"]
        chats = [e for e in g["rt"] if e["kind"] == "chat"]
        self.assertEqual([e["room"] for e in chats], [self.tasks[0]])
        self.assertIsNone(g["sid"], "the middle has no task")
        self.assertTrue(g["poFrame"], "the middle shows the PO's chat again")
        self.assertNotEqual(chats[0]["where"], g["where"]["po-chat"], "beside the conversation")
        self.assertGreater(chats[0]["box"]["x"], g["chat"]["x"] + g["chat"]["w"] - 1, "at its right")
        tab = next(t for t in g["tabs"] if t["id"] == chats[0]["id"])
        self.assertEqual(tab["text"], "Brakes that squeal", "named after the task (a numbered task says #12 first)")
        self.assertTrue(tab["x"], "closable")

    def test_ctrl_click_and_a_middle_click_open_more_and_their_menu_is_the_tasks(self):
        g = self.got["chats"]
        chats = [e for e in g["rt"] if e["kind"] == "chat"]
        self.assertEqual([e["room"] for e in chats], [self.tasks[0], self.tasks[1], self.tasks[3]])
        self.assertIsNone(g["sid"], "a Ctrl+click and a middle click open no task in the middle")
        a, b, d = chats
        self.assertGreaterEqual(b["box"]["x"], a["box"]["x"] + a["box"]["w"] - 6, "each new chat beside the chat panel last shown")
        self.assertGreaterEqual(d["box"]["x"], b["box"]["x"] + b["box"]["w"] - 6)
        self.assertEqual(json.loads(g["saved"]["chats"]), [self.tasks[0], self.tasks[1], self.tasks[3]], "remembered per browser")
        m = self.got["chatMenu"]
        self.assertTrue(m and m["app"], m)
        self.assertNotIn("Open in new panel", m["app"], "a chat panel is one already")
        for label in ("Rename…", "Delete task…", "Agents and models…"):
            self.assertIn(label, m["app"])
        self.assertIn("close", m["items"], "Dock's own Close")

    def test_a_plain_click_takes_the_middle_and_a_panelled_task_reveals_its_panel(self):
        g = self.got["third"]
        self.assertEqual(g["middleRoom"], self.tasks[2])
        self.assertTrue(g["task"])
        self.assertEqual(sorted(e["room"] for e in g["rt"] if e["kind"] == "chat"), sorted([self.tasks[0], self.tasks[1], self.tasks[3]]), "the panels stay")
        r = self.got["revealA"]
        self.assertEqual(r["middleRoom"], self.tasks[2], "the middle keeps its task")
        a = next(e for e in r["rt"] if e["room"] == self.tasks[0])
        self.assertTrue(a["front"] and a["visible"], "the panel came to the front")

    def test_each_panel_is_its_rooms_chat(self):
        s = self.got["said"]
        for r in (self.tasks[0], self.tasks[1], self.tasks[3]):
            self.assertEqual(s[r], {"typed": True, "inRoom": True, "shown": True, "others": True}, r)

    def test_a_chat_behind_a_tab_knows_it_is_off_screen(self):
        o = self.got["offScreen"]
        self.assertEqual(o, {"off": True, "slow": True, "cls": True}, o)

    def test_chat_panels_poll_less(self):
        one, four = self.got["rate"]["one"], self.got["rate"]["four"]
        rooms = lambda w: self.per_minute(w, "/api/room?")
        total = lambda w: sum(self.per_minute(w, p) for p in ("/api/room?", "/api/ptys", "/api/live"))
        self.assertGreater(rooms(one), 15, f"the one chat polls its room every 2 s: {rooms(one):.0f}/min")
        mid = self.per_minute(four, "/api/room?id=" + self.tasks[2])
        self.assertGreater(mid, 15, f"the middle's chat polls every 2 s: {mid:.0f}/min")
        for r in (self.tasks[0], self.tasks[1], self.tasks[3]):
            rate = self.per_minute(four, "/api/room?id=" + r)
            self.assertLessEqual(rate, 12, f"a chat panel nobody types in polls every 10 s: {rate:.0f}/min")
            self.assertGreater(rate, 0, "but it polls")
        self.assertLess(total(four), total(one) * 2.6, f"four chats cost less than twice one: {total(one):.0f} → {total(four):.0f} requests/min")
        print(f"\n[#150] requests/min: one chat {total(one):.0f} (room {rooms(one):.0f}); four chats {total(four):.0f} (rooms {rooms(four):.0f})")

    def test_a_phone_has_the_panels_as_tabs(self):
        g = self.got["phone"]
        self.assertTrue(g["narrow"])
        rt = g["rt"]
        self.assertEqual(sorted(e["kind"] for e in rt), ["chat", "chat", "chat", "file", "file"])
        self.assertEqual({e["where"] for e in rt} | {g["where"]["po-chat"]}, {"root"}, "every panel a tab of the one column")
        self.assertEqual(g["scrollW"], g["vw"], "no sideways scroll")

    def test_the_static_wiring(self):
        self.assertIn("closest('.panel-btn')", INDEX)
        self.assertIn("id: 'panel'", ACTIONS)
        self.assertIn("data-chat-slow", SESSION)
        self.assertIn(".dk-off, .dk-parking, .pd-off", SESSION)
        self.assertIn("'file:' + wsNorm(path)", INDEX)
        self.assertIn("dock-removed", INDEX)


if __name__ == "__main__":
    unittest.main()
