"""The Board's List view on a phone (index.html; #125): a task row stays on
screen at 360-430px, with no horizontal page scroll.

An HTML table shares one width per column across every row. The Task column
carries a 560px cap (`.topic`) so a title wraps instead of widening the row
— fine on a laptop, but wider than a whole phone screen, and on a real,
~120-task board it took only one row with an unbreakable run (a badge's own
text never wraps) to pin every row's Task column at that width, pushing an
otherwise ordinary row (#115's, in the screenshot that first showed this)
past the right edge with no way to scroll to see the rest.

Checked in headless Chrome over CDP, against a hub in a thread serving the
pages, with one project holding: a few short tasks, one task with a single
pathological unbreakable badge (pins the shared column), and the task
actually measured — two ordinary agents and a long, realistic title.
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
    '--disable-gpu', '--hide-scrollbars', '--window-size=430,932', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// The measured (realistic) row's own extent: itself, its Task cell, and
// each of its agent badges. A pinning row elsewhere in the same table can
// still overflow its own cell (an unbreakable string CSS cannot wrap), but
// must not drag an ordinary row's cell wider than the fix's cap.
const OVERFLOW = `(() => {
  const row = document.querySelector('table tbody tr.row[data-room=${JSON.stringify(A.wideRoom)}]');
  const rr = row ? row.getBoundingClientRect() : null;
  const badges = row ? [...row.querySelectorAll('.badge-agent')].map(b => b.getBoundingClientRect().right) : [];
  const topic = row ? row.querySelector('.topic') : null;
  const tr = topic ? topic.getBoundingClientRect() : null;
  return {
    vw: innerWidth,
    rowRight: rr ? rr.right : null,
    topicWidth: tr ? tr.width : null,
    badgeRights: badges,
    gate: row?.querySelector('.gate-chip')?.textContent,
  };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0', 30000);
    return { evalIn, until, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const openList = async (p) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks';
      localStorage.setItem('cd-view', 'list'); VIEW_MODE = 'list'; renderRows(); return 0; })()`);
    await p.until("!!document.querySelector('table tbody tr.row[data-room=" + JSON.stringify(A.wideRoom) + "]')", 20000);
    await sleep(300);
  };
  try {
    for (const w of [360, 390, 430, 1280, 1440]) {
      const p = await page(w, 800);
      await openList(p);
      const res = {};
      for (const theme of ['light', 'dark', 'dim', 'paper', 'contrast', 'fjord']) {
        await p.evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}; 0`);
        await sleep(120);
        res[theme] = await p.evalIn(OVERFLOW);
        res[theme].card = await p.evalIn(`cardHtml(ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.wideRoom)}), Date.now()/1000)`);
        await p.shot('board-list-' + w + '-' + theme);
      }
      out[w] = res;
      await p.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheBoardListOnAPhone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-board-", ignore_cleanup_errors=True)
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
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        plain_members = [{"identity": "claude", "agent": "claude", "model": "claude-opus-5-5", "cwd": str(home)}]
        for i in range(3):
            r = chatroom.create_room(f"A short task {i}", plain_members)
            dashboard.assign_session_project(r["id"], cls.proj)
        # Pins the shared Task column near the desktop cap: one badge whose
        # own text (`white-space: nowrap`) cannot wrap, wide enough that no
        # column-width fix can make it fit a phone screen either — realistic
        # data never runs this long, so it is not itself asserted on below,
        # only used to reproduce the column-sharing mechanism that pushed
        # #115's ordinary row off screen.
        pin_members = [{"identity": "claude", "agent": "claude", "model": "m" * 90, "cwd": str(home)}]
        pin = chatroom.create_room("A task with one long model name", pin_members)
        dashboard.assign_session_project(pin["id"], cls.proj)
        chatroom.patch_room(pin["id"], no=140)
        # The row actually measured: two ordinary agents and a realistic
        # long title, the shape of #115 in the screenshot that first showed
        # this.
        target_members = [
            {"identity": "claude", "agent": "claude", "model": "claude-opus-5-5", "cwd": str(home)},
            {"identity": "codex", "agent": "codex", "model": "gpt-reserve", "cwd": str(home)},
        ]
        room = chatroom.create_room(
            "How Air organises its screen, and a reorganised Ensemble screen to choose from",
            target_members,
        )
        dashboard.assign_session_project(room["id"], cls.proj)
        chatroom.patch_room(room["id"], launched=False,
                            after=[{"task": pin["id"], "when": "merged"}], onReady="start")
        cls.wide_room = room["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "wideRoom": cls.wide_room, "shots": shots}
        script = base / "board_list_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=180)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_an_ordinary_row_stays_on_screen(self):
        for width in (360, 390, 430):
            for theme in ("light", "dark"):
                g = self.got[str(width)][theme]
                with self.subTest(width=width, theme=theme):
                    self.assertIsNotNone(g["rowRight"], "the task row is shown")
                    self.assertLessEqual(g["rowRight"], g["vw"] + 1, "the row itself stays on screen")
                    self.assertLessEqual(g["topicWidth"], g["vw"], "the Task column fits the screen")
                    for right in g["badgeRights"]:
                        self.assertLessEqual(right, g["vw"] + 1, "an agent badge stays on screen")

    def test_gated_draft_on_card_and_list_across_themes_and_widths(self):
        for width, themes in self.got.items():
            for theme, got in themes.items():
                with self.subTest(width=width, theme=theme):
                    self.assertEqual(got["gate"], "after #140")
                    self.assertIn('class="gate-chip"', got["card"])
                    self.assertIn('after #140', got["card"])


if __name__ == "__main__":
    unittest.main()
