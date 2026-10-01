"""Which project's task a chip shows (#156, the CEO's P112), in the page.

In headless Chrome over CDP, against a hub in a thread serving the opTen
PO's chat — the project OPtionTradingENgine, its PO chat titled "opten", and
Dock (key DK), both with a #27 — at 1280 and at 390 px (touch):

* the real balloon, "Dock released #27 as v0.11.0 …", shows Dock's #27: the
  chip reads `DK-27` and its card names Dock;
* `DK-27` written out resolves to Dock's task anywhere;
* "Answered Dock about #27" is opTen's #27 — Dock named earlier in the
  sentence is too weak to move it — and, as Dock has a #27 too, the card says
  the project was assumed;
* a plain #27 is opTen's, its card naming no project.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node and
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
from tests import chrome_profile  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402

NODE = shutil.which("node")
REAL = "Re P230: no wait was needed. Dock released #27 as v0.11.0 while our task was running, so it's in."
OPTEN_27 = "Amend a tranche by hand from the screen: any strategy — limit, stop, legs, size"
DOCK_27 = "Ensemble needs 15–16: setTitle(id, title) and bodyAttrs mirrored into panel windows"

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// The chips in the conversation and the open card.
const VIEW = `(() => {
  const chips = [...document.querySelectorAll('#msgs a.task-chip')];
  const card = document.querySelector('.task-card');
  return { chips: chips.map(a => ({ text: a.textContent, task: a.dataset.task, label: a.getAttribute('aria-label'), card: JSON.parse(a.dataset.card) })),
    card: card ? { text: card.textContent, title: card.querySelector('.tc-title').textContent, proj: (card.querySelector('.tc-proj') || {}).textContent || '',
                   assumed: !!card.querySelector('.tc-proj.tc-assumed'), projTitle: (card.querySelector('.tc-proj') || {}).title || '' } : null,
    scrollX: document.documentElement.scrollWidth - innerWidth, coarse: matchMedia('(pointer: coarse)').matches };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of [[1280, 800, false], [390, 844, true]]) {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
      const press = async (sel) => {
        const { x, y } = await evalIn(`(() => { const b = document.querySelector('${sel}').getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; })()`);
        if (mob) {
          await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }, sessionId);
          await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, sessionId);
        } else {
          await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
          for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
        }
        await sleep(250);
      };
      const key = async (k, code, vk) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }, sessionId); await sleep(150); };
      await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
      // Four chips, and the first two Dock's: the project list has landed and the chat was drawn with it.
      try {
        await until(`typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && [...document.querySelectorAll("#msgs a.task-chip")].filter(a => a.dataset.task === "${A.dock}").length >= 2 && document.querySelectorAll("#msgs a.task-chip").length >= 4`, 30000);
      } catch (e) {
        // What the page shows instead, and what the hub answered, for the failure's message.
        const view = await evalIn(VIEW).catch(() => null);
        const projects = await evalIn(`fetch('/api/task/projects?room=${A.room}').then(r => r.status + ' ' + r.url).then(t => fetch('/api/task/projects?room=${A.room}').then(r => r.text()).then(b => t + ' ' + b))`).catch(err => String(err));
        throw new Error(e.message + '\n' + JSON.stringify(view) + '\n' + projects);
      }
      await sleep(400);
      const o = { chips: await evalIn(VIEW) };
      await shot(`task-ref-project-${w}-chips`);
      const chip = i => `#msgs a.task-chip:nth-of-type(1)`;   // unused: chips are found by order below
      const nth = async (i) => evalIn(`(() => { const a = [...document.querySelectorAll('#msgs a.task-chip')][${i}]; a.id = 'chip-' + ${i}; return '#chip-' + ${i}; })()`);
      await press(await nth(0));
      o.dockCard = await evalIn(VIEW);
      await shot(`task-ref-project-${w}-dock-card`);
      await key('Escape', 'Escape', 27);
      await press(await nth(2));
      o.assumedCard = await evalIn(VIEW);
      await shot(`task-ref-project-${w}-assumed-card`);
      await key('Escape', 'Escape', 27);
      await press(await nth(3));
      o.plainCard = await evalIn(VIEW);
      await key('Escape', 'Escape', 27);
      out[w] = o;
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class WhichProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-refp-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard, "operator_name", return_value="fab"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, opten, _ = dashboard.register_project("OPtionTradingENgine")
        assert ok, opten
        cls.opten = opten["id"]
        ok, dock, _ = dashboard.register_project("Dock")
        assert ok, dock
        cls.dockProj = dock["id"]
        ok, why = dashboard.set_project_key(cls.dockProj, "DK")
        assert ok, why
        team = [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer"},
                {"identity": "codex", "agent": "codex", "model": "", "role": "reviewer"}]
        cls.ours = cls.task(OPTEN_27, cls.opten, team, 27)
        cls.dock = cls.task(DOCK_27, cls.dockProj, team, 27)
        # The opTen PO's chat, titled "opten" as the CEO named it.
        cls.room = chatroom.create_room("opten", [{"identity": "claude", "agent": "claude", "model": "", "role": "ProductOwner"}])["id"]
        full = chatroom.get_room(cls.room, public=False)
        full["projectId"] = cls.opten
        chatroom.update_room(full)
        dashboard.assign_session_project(cls.room, cls.opten)
        ok, why = dashboard.set_project_po(cls.opten, cls.room)
        assert ok, why
        chatroom.post_message(cls.room, "claude", REAL)
        chatroom.post_message(cls.room, "user", "DK-27 is tagged, then.")
        chatroom.post_message(cls.room, "claude", "Answered Dock about #27 this morning.")
        chatroom.post_message(cls.room, "claude", "Plain #27 is ours, and the line under it gives the card room.")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.port}", "room": cls.room,
                "dock": cls.dock, "ours": cls.ours, "shots": shots}
        script = base / "task_ref_project_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def task(cls, title: str, pid: str, team: list[dict], no: int) -> str:
        rid = chatroom.create_room(title, [dict(m) for m in team])["id"]
        full = chatroom.get_room(rid, public=False)
        full["projectId"] = pid
        chatroom.update_room(full)
        dashboard.assign_session_project(rid, pid)
        chatroom.set_task_number(rid, pid, no)
        return rid

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def _both(self):
        return [(w, self.got[str(w)]) for w in (1280, 390)]

    def test_the_real_balloon_shows_docks_task(self):
        for w, o in self._both():
            with self.subTest(width=w):
                v = o["chips"]
                self.assertEqual([c["text"] for c in v["chips"]], ["DK-27", "DK-27", "#27", "#27"], "Dock's twice, then ours twice")
                self.assertEqual([c["task"] for c in v["chips"]], [self.dock, self.dock, self.ours, self.ours])
                self.assertEqual(v["chips"][0]["card"]["title"], DOCK_27)
                self.assertEqual(v["chips"][0]["card"]["project"], "Dock")
                self.assertEqual(v["chips"][1]["card"]["project"], "Dock", "DK-27 written out: Dock's, anywhere")
                self.assertLessEqual(v["scrollX"], 0)
                self.assertEqual(v["coarse"], w == 390)

    def test_the_cards_say_which_project(self):
        for w, o in self._both():
            with self.subTest(width=w):
                dock = o["dockCard"]["card"]
                self.assertEqual((dock["title"], dock["proj"], dock["assumed"]), (DOCK_27, "Dock", False))
                assumed = o["assumedCard"]["card"]
                self.assertEqual(assumed["title"], OPTEN_27, "a name earlier in the sentence does not move the number")
                self.assertEqual(assumed["proj"], "assumed OPtionTradingENgine", "Dock has a #27 too: the card says which project was assumed")
                self.assertTrue(assumed["assumed"])
                self.assertIn("more than one project", assumed["projTitle"])
                self.assertEqual(o["chips"]["chips"][2]["label"], f"Task OP-27: {OPTEN_27} (assumed OPtionTradingENgine)")
                plain = o["plainCard"]["card"]
                self.assertEqual((plain["title"], plain["proj"], plain["assumed"]), (OPTEN_27, "", False), "a plain number: nothing to say")


if __name__ == "__main__":
    unittest.main()
