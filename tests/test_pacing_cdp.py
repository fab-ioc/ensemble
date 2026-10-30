"""#291: a task held by the pacing brake says why and until what on its card,
and the plan chip's tray shows the rate and the projected end of each kind,
in headless Chrome over CDP against a throwaway hub in a thread, at 1728x1117
and on a phone (390x844).

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
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const show = `(() => { if (typeof renderRows !== 'function' || !PROJECTS || !ALL_ROWS || !ALL_ROWS.length) return false;
      SELECTED_PROJECT = ${JSON.stringify(A.project)}; PROJECT_TAB = 'tasks'; if (VIEW_MODE !== 'board') setViewMode('board'); else { BOARD_SIG = ''; renderRows(); }
      return !!document.querySelector('.card[data-room="${A.room}"] .chold') && !!(USAGE.pace && USAGE.pace.brake); })()`;
    const read = `(() => { renderUsage(); document.getElementById('usage-tray').hidden = false;
      const hold = document.querySelector('.card[data-room="${A.room}"] .chold');
      const card = hold.closest('.card'), cr = card.getBoundingClientRect(), r = hold.getBoundingClientRect();
      const tray = document.getElementById('usage-tray'), tr = tray.getBoundingClientRect();
      return { hold: hold.textContent, title: hold.title, whole: r.left >= cr.left - 0.5 && r.right <= cr.right + 0.5 && r.bottom <= cr.bottom + 0.5,
        tray: tray.textContent.replace(/\\s+/g, ' '), trayFits: tr.left >= -0.5 && tr.right <= innerWidth + 0.5,
        over: !!tray.querySelector('.usage-pace .ahead'),
        scroll: document.documentElement.scrollWidth, width: innerWidth }; })()`;
    const wait = async () => { const t = Date.now(); while (Date.now() - t < 30000) { try { if (await evalIn(show)) return true; } catch (e) {} await sleep(250); } return false; };
    const got = {};
    await send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    got.shown = await wait();
    got.desk = await evalIn(read);
    await evalIn(`document.getElementById('usage-tray').hidden = true`);
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    await sleep(400);
    got.shownPhone = await wait();
    got.phone = await evalIn(read);
    console.log(JSON.stringify(got));
  } finally { ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheHoldIsVisible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pc-", ignore_cleanup_errors=True)
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
                "sources": [source("claude", 62), source("codex", 40)]}
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
            pacing.note("claude", now - h * 3600, 32.0 + (30 - h), end)
            pacing.note("codex:codex", now - h * 3600, 40.0, end)
        ok, proj, _ = dashboard.register_project("Garage")
        assert ok, proj
        home = str(Path(proj.get("home") or proj["path"]))
        rid = chatroom.create_room("Held by the brake", [{"identity": "claude", "agent": "claude",
                                                          "cwd": home}])["id"]
        full = chatroom.get_room(rid, public=False)
        full.update(mode="solo", launched=False, priority=3, workflow="todo", paceHold={
            "at": now, "by": "agent", "why": "Claude at 62%, 1 points an hour, heading for 150%",
            "until": "the projection falls under 105%, or Tue 14:00 at the latest"})
        chatroom.update_room(full)
        dashboard.assign_session_project(rid, proj["id"])
        pacing._state()["brake"].update(on=True, since=now, episode=1, why="Claude and Codex",
                                        until="the projection falls under 105%")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        script = base / "pacing_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "project": proj["id"], "room": rid,
                "base": f"http://127.0.0.1:{server.server_address[1]}"}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True,
                             encoding="utf-8", timeout=200)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, view):
        self.assertIn("until the projection falls under 105%", view["hold"])
        self.assertIn("heading for 150%", view["title"])
        self.assertTrue(view["whole"], view)
        self.assertIn("Pacing brake on", view["tray"])
        self.assertIn("points an hour", view["tray"])
        self.assertIn("at its reset", view["tray"])
        self.assertTrue(view["over"], view)
        self.assertTrue(view["trayFits"], view)
        self.assertLessEqual(view["scroll"], view["width"], view)

    def test_card_and_tray_on_a_computer(self):
        self.assertTrue(self.got["shown"])
        self.check(self.got["desk"])

    def test_card_and_tray_on_a_phone(self):
        self.assertTrue(self.got["shownPhone"])
        self.check(self.got["phone"])


if __name__ == "__main__":
    unittest.main()
