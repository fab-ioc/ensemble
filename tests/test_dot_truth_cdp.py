"""#190 (GitHub issue 13): the dots in headless Chrome over CDP, against a
throwaway hub in a thread (its own state in a temp dir, fake terminals):

* a task whose terminal runs is green;
* a Done task that does not run is not green (it was, before);
* a task whose agent died and was resumed since is not red;
* the same death nobody dealt with is red, so the check above is not empty.

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
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
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
    '--no-default-browser-check', '--disable-gpu', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    const rooms = JSON.stringify(A.rooms);
    // The grouped view holds a project's tasks; Done today unfolded.
    const read = `(() => { try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {}
      if (typeof renderRows !== 'function' || !ALL_ROWS || !ALL_ROWS.length) return null;
      SW_DONE_OPEN = true; renderRows();
      const out = {};
      for (const [k, rid] of Object.entries(${rooms})) {
        const dots = [...document.querySelectorAll('.sw-row[data-room="' + rid + '"] .sw-st')];
        out[k] = dots.map(d => [...d.classList].filter(c => c !== 'sw-st').join(' ') + '|' + d.getAttribute('aria-label'));
      }
      return out; })()`;
    let got = null; const t = Date.now();
    while (Date.now() - t < 30000) {
      try { got = await evalIn(read); } catch (e) { got = null; }
      if (got && got.gone.length && got.gone.every(s => s.startsWith('danger'))) break;   // the attention answer is in
      await sleep(250);
    }
    console.log(JSON.stringify(got));
  } finally { ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _live(rid):
    return types.SimpleNamespace(
        id="pty-live", alive=lambda: True, tail=lambda: "● ok\n❯ \n", last_output=time.time(),
        info=lambda: {"idleSeconds": 30}, last_submit=lambda: 0.0, death=lambda: None, last_input=0.0,
        meta={"room": rid, "identity": "claude"})


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheDotsInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-dot-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        live = {}
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
            mock.patch.object(dashboard, "_read_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: live.get(pid)),
            mock.patch.object(dashboard.ptyrun, "death_for", lambda pid: None),
            mock.patch.object(dashboard.ptyrun, "list_sessions",
                              lambda: [{"id": k, "alive": True, "meta": s.meta} for k, s in live.items()]),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        for cache in (attention._SUMMARY_CACHE, attention._ANALYSIS, attention._FIRST_SEEN):
            cache.clear()
            cls.addClassCleanup(cache.clear)
        cls.addClassCleanup(setattr, attention, "_result", (0.0, None))
        attention._result = (0.0, None)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = str(Path(proj.get("home") or proj["path"]))
        died = time.time() - 600

        def task(title, **fields):
            rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": home}])["id"]
            chatroom.post_message(rid, "user", "Please look at it")
            full = chatroom.get_room(rid, public=False)
            for m in full.get("messages") or []:
                m["ts"] = died - 3600      # written before the death: not a sign of use since
            part = chatroom.participant(full, "claude")
            part["sessionId"] = "s-" + rid
            for k, v in fields.pop("part", {}).items():
                part[k] = v
            full.update(mode="solo", launched=True, **fields)
            chatroom.update_room(full)
            dashboard.assign_session_project(rid, proj["id"])
            return rid

        def gone(pty):
            return {"ptyId": pty, "lastExit": {"ptyId": pty, "exitCode": 1, "killed": False,
                                               "endedAt": died, "tail": "● ok\n", "lastInput": 0.0}}

        rooms = {
            "live": task("Runs now", workflow="inprogress", part={"ptyId": "pty-live"}),
            "done": task("Finished this morning", workflow="done", part={"ptyId": "pty-old"}),
            "resumed": task("Died then resumed", workflow="inprogress",
                            part={**gone("pty-r"), "resumedAt": died + 60}),
            "gone": task("Died, nobody looked", workflow="inprogress", part=gone("pty-g")),
        }
        live["pty-live"] = _live(rooms["live"])
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        script = base / "dot_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{server.server_address[1]}", "rooms": rooms}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=200)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def dots(self, key):
        got = self.got[key]
        self.assertTrue(got, f"{key}: its row is in the list ({self.got})")
        return got

    def test_a_running_terminal_is_green(self):
        self.assertTrue(all(d.startswith("live|running") for d in self.dots("live")), self.got)

    def test_a_done_task_that_does_not_run_is_not_green(self):
        self.assertEqual(set(self.dots("done")), {"off|done, not running"}, self.got)

    def test_a_resumed_once_gone_task_is_not_red(self):
        self.assertFalse(any(d.startswith("danger") for d in self.dots("resumed")), self.got)

    def test_a_death_nobody_dealt_with_is_red(self):
        self.assertTrue(all(d.startswith("danger") for d in self.dots("gone")), self.got)


if __name__ == "__main__":
    unittest.main()
