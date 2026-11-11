"""#330: Update now shows one card from the press until the page reloads itself.

In headless Chrome over CDP, against a hub in a thread (its own state in a
temp dir) on a source checkout. The restart itself is stubbed (Linux has no
self-restart, #336): the git fetch is a stub, and the helper is a thread that
moves the hub's own update stage (dashboard.src_update_stage) the way a
helper's calls do. "The hub is down" is the hub closing every API request
without an answer; "the new hub" is /api/config answering with another pid.

* success: a click on Update now shows the card at once (Fetching the update
  from GitHub), then Checking that the new code starts, Restarting the hub,
  The hub is restarting, back in a moment, and The new code is running ·
  Reloading the page; the time line counts up from 0:0x; the page then
  reloads by itself on the new hub, and the drain banner never shows;
* a helper that gives up while the hub runs on: the card goes, the update
  dialog says Not updated, why, and that this version keeps running; Close;
* a fetch that fails: the dialog says git fetch failed and to check the
  connection, naming restart.log.

Screenshots of every step go to $ENSEMBLE_SHOTS when it is set (the #330
drawing-vs-built comparison). Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
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
import restart_drain  # noqa: E402
from tests import test_middle  # noqa: E402
from tests.test_middle import CHROME, NODE  # noqa: E402

HEAD = test_middle.CDP_JS[:test_middle.CDP_JS.index("// Where the middle is")]

CDP_JS = HEAD + r"""
async function main() {
  const { ch, ws } = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { browserContextId } = await c.send('Target.createBrowserContext');
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank', browserContextId });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s);
    const [W, H] = A.size || [1440, 900];
    await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: W < 600 ? 2 : 1, mobile: W < 600 }, s);
    const ev = async expression => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
    const until = async (expression, n = 200) => { for (let i = 0; i < n; i++) { try { if (await ev(expression)) return true; } catch {} await sleep(100); } return false; };
    const shot = async name => { if (!A.shots) return; const { data } = await c.send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, `${name}-${W}-${A.theme || 'light'}.png`), Buffer.from(data, 'base64')); };
    const click = async sel => {
      const b = await ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: b.x, y: b.y }, s);
      await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: b.x, y: b.y, button: 'left', clickCount: 1 }, s);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: b.x, y: b.y, button: 'left', clickCount: 1 }, s);
    };
    const open = async () => {
      await c.send('Page.navigate', { url: A.base + '/' }, s);
      await until(`typeof RUNTIME_CFG !== 'undefined' && !!RUNTIME_CFG && !!RUNTIME_CFG.pid && !document.getElementById('update-banner').hidden`, 300);
      if (A.theme) await ev(`document.documentElement.setAttribute('data-theme', ${JSON.stringify(A.theme)}); true`);
      await ev(`(document.getElementById('agents-banner') || {}).hidden = true; window.__mark330 = 1; true`);
      await sleep(400);
    };
    // Watch the card: every step it says, the time beside it, whether the
    // drain banner showed, until the page reloads (the mark is gone) or a
    // dialog says it failed.
    const watch = async (R, names) => {
      R.steps = []; R.times = []; R.drainBanner = false; R.card = [];
      const t0 = Date.now();
      while (Date.now() - t0 < 60000) {
        let g = null;
        try { g = await ev(`(() => ({ mark: window.__mark330 || 0, up: !document.getElementById('upd-card').hidden,
          step: document.getElementById('upd-card-step').textContent, time: document.getElementById('upd-card-time').textContent,
          drain: !document.getElementById('drain-banner').hidden, failed: !document.getElementById('update-overlay').hidden,
          box: (() => { const r = document.querySelector('#upd-card .upd-box').getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); })() }))()`); } catch { await sleep(50); continue; }
        if (!g.mark) { R.reloaded = true; R.reloadMs = Date.now() - t0; break; }
        if (g.failed) { R.failed = true; break; }
        if (g.up && g.step && R.steps[R.steps.length - 1] !== g.step) {
          R.steps.push(g.step); R.times.push(g.time); R.card.push(g.box);
          const name = names && names[g.step]; if (name) await shot(name);
        }
        if (g.drain) R.drainBanner = true;
        await sleep(80);
      }
    };
    const NAMES = { 'Fetching the update from GitHub': '03-src-fetching', 'Checking that the new code starts': '04-src-checking',
      'Restarting the hub': '06-restarting', 'The hub is restarting, back in a moment': '07-hub-down',
      'The new code is running · Reloading the page': '08-reloading' };
    // 1. The whole update, to the reload on the new hub.
    { const R = out.ok = {};
      await open();
      if (!A.only || A.only === 'ok') {
        R.pidBefore = await ev('RUNTIME_CFG.pid');
        R.cardHiddenBefore = await ev(`document.getElementById('upd-card').hidden`);
        await click('#update-banner-go');
        await watch(R, NAMES);
        await until(`typeof RUNTIME_CFG !== 'undefined' && !!RUNTIME_CFG && !!RUNTIME_CFG.pid`, 300);
        R.pidAfter = await ev('RUNTIME_CFG.pid');
        R.cardAfter = await ev(`document.getElementById('upd-card').hidden`);
      } }
    // 2. The helper gives up while the hub runs on.
    { const R = out.fail = {};
      await open();
      await click('#update-banner-go');
      await watch(R, {});
      await sleep(200);
      R.dialog = await ev(`({ title: document.querySelector('#update-overlay h3').textContent, stage: document.getElementById('update-stage').textContent,
        hint: document.getElementById('update-hint').textContent, close: !document.getElementById('update-close').hidden,
        card: document.getElementById('upd-card').hidden, focused: document.activeElement && document.activeElement.id })`);
      await shot('11-fail-helper');
      await click('#update-close');
      R.closed = await ev(`document.getElementById('update-overlay').hidden`); }
    // 3. The fetch fails.
    { const R = out.fetch = {};
      await open();
      await click('#update-banner-go');
      await watch(R, {});
      await sleep(200);
      R.dialog = await ev(`({ title: document.querySelector('#update-overlay h3').textContent, stage: document.getElementById('update-stage').textContent,
        hint: document.getElementById('update-hint').textContent, card: document.getElementById('upd-card').hidden })`);
      await shot('12-fail-fetch'); }
  } finally { ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


class FakeHub:
    """The parts of an update the test plays: the fetch, the helper, the hub
    going away and a new one coming back."""

    def __init__(self, modes):
        self.modes = list(modes)
        self.mode = ""
        self.down = False
        self.gen = 0
        self.log = []

    def pull_checkout(self):
        self.mode = self.modes.pop(0)
        time.sleep(0.6)                       # git fetch from GitHub measured 0.4 s
        if self.mode == "fetch":
            return "git fetch failed: Could not resolve host: github.com"
        return ""

    def self_restart(self, plan):
        self.log.append({"grace": plan["graceSeconds"], "mode": self.mode})
        threading.Thread(target=self.helper, args=(plan["leaseId"], self.mode), daemon=True).start()
        return {"started": True}

    def helper(self, lease, mode):
        time.sleep(1.5)                       # the new code on a spare port
        if mode == "fail":
            dashboard._drop_restart_lease(lease)
            dashboard.src_update_stage("failed", lease, error="The new code did not start: "
                                       "PREFLIGHT FAILED - the new code did not serve on port 9000")
            return
        dashboard.src_update_stage("restarting", lease)   # its snapshot call, just before the stop
        time.sleep(1.5)
        self.down = True
        time.sleep(3.0)
        dashboard._drop_restart_lease(lease)
        self.gen += 1                         # a new process answers
        self.down = False


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class UpdateCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-upd330-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        hub = cls.hub = FakeHub(["ok", "fail", "fetch"])
        real_get, real_post, real_json = dashboard.Handler.do_GET, dashboard.Handler.do_POST, dashboard.Handler._send_json

        def away(h) -> bool:
            if hub.down and h.path.startswith("/api/"):
                h.close_connection = True     # no answer at all
                return True
            return False

        def do_get(h):
            if not away(h):
                real_get(h)

        def do_post(h):
            if not away(h):
                real_post(h)

        def send_json(h, code, payload):
            if h.path.split("?")[0] == "/api/config" and isinstance(payload, dict) and "pid" in payload:
                payload = {**payload, "pid": payload["pid"] + 1000 * hub.gen}
            return real_json(h, code, payload)
        info = {"available": True, "kind": "source", "behind": 2, "latestSha": "abc1234",
                "latestMessage": "Merge #330: the Update now card"}
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
            mock.patch.object(dashboard.Handler, "do_GET", do_get),
            mock.patch.object(dashboard.Handler, "do_POST", do_post),
            mock.patch.object(dashboard.Handler, "_send_json", send_json),
            mock.patch.object(dashboard, "check_for_update", lambda *a, **k: info),
            mock.patch.object(dashboard.app_version, "packaged", lambda: False),
            mock.patch.object(dashboard, "source_update_keeps_host", lambda: True),
            mock.patch.object(dashboard, "pull_checkout", hub.pull_checkout),
            mock.patch.object(dashboard.BACKEND, "self_restart", hub.self_restart),
            mock.patch.object(dashboard, "take_restart_snapshot", lambda *a, **k: {"rooms": []}),
            mock.patch.object(dashboard, "pty_host_compatible", lambda: False),
            mock.patch.object(restart_drain, "holding", lambda: False),
            mock.patch.dict(dashboard._SRC_UPDATE, {"stage": "idle"}, clear=True),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        env = {k: v for k, v in os.environ.items() if k not in ("ENSEMBLE_RESTART_DRY_RUN", "ENSEMBLE_UPDATE_DRY_RUN")}
        cls.patches.append(mock.patch.dict(os.environ, env, clear=True))
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, motors, _ = dashboard.register_project("Motors")
        assert ok, motors
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**test_middle.chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "shots": shots,
                "theme": os.environ.get("ENSEMBLE_SHOTS_THEME", ""),
                "size": json.loads(os.environ.get("ENSEMBLE_SHOTS_SIZE", "[1440, 900]"))}
        script = base / "update_card_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        cls.state = state
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.size = args["size"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_one_card_from_the_press_to_the_reload_on_the_new_hub(self):
        g = self.got["ok"]
        self.assertTrue(g["cardHiddenBefore"], g)
        self.assertEqual(g["steps"], ["Fetching the update from GitHub", "Checking that the new code starts",
                                      "Restarting the hub", "The hub is restarting, back in a moment",
                                      "The new code is running · Reloading the page"], g)
        self.assertTrue(g.get("reloaded"), g)
        self.assertFalse(g.get("failed"), g)
        self.assertNotEqual(g["pidAfter"], g["pidBefore"], "the page that loaded is the new hub's")
        self.assertTrue(g["cardAfter"], "the reloaded page shows no card")
        self.assertFalse(g["drainBanner"], g)
        self.assertRegex(g["times"][0], r"^ · 0:0[0-2]$", "the time starts at the press")
        secs = [int(t.split(":")[-1]) + 60 * int(t.split(":")[0].split(" ")[-1]) for t in g["times"]]
        self.assertEqual(secs, sorted(secs), "the time only counts up")
        # One card, centred, the same place throughout.
        # At phone width the last line may wrap; the card keeps its centre.
        self.assertEqual(len({(b[0], b[2], b[1] + b[3] / 2) for b in g["card"]}), 1, g["card"])
        left, top, width, height = g["card"][0]
        vw = self.size[0]
        self.assertEqual(width, min(440, vw - 32))
        self.assertLessEqual(abs(left + width / 2 - vw / 2), 1)

    def test_the_page_update_waits_no_grace(self):
        self.assertEqual(self.hub.log[0], {"grace": 0, "mode": "ok"})

    def test_a_helper_that_gives_up_says_not_updated(self):
        g = self.got["fail"]
        self.assertEqual(g["steps"][:2], ["Fetching the update from GitHub", "Checking that the new code starts"], g)
        self.assertTrue(g.get("failed"), g)
        d = g["dialog"]
        self.assertEqual(d["title"], "Not updated")
        self.assertEqual(d["stage"], "The new code did not start: PREFLIGHT FAILED - the new code did not serve on port 9000")
        self.assertTrue(d["hint"].startswith("This version keeps running. More in "), d)
        self.assertTrue(d["hint"].endswith("restart.log."), d)
        self.assertTrue(d["close"] and d["card"], d)
        self.assertEqual(d["focused"], "update-close")
        self.assertTrue(g["closed"])

    def test_a_failed_fetch_says_check_the_connection(self):
        g = self.got["fetch"]
        self.assertEqual(g["steps"], ["Fetching the update from GitHub"], g)
        d = g["dialog"]
        self.assertEqual(d["title"], "Not updated")
        self.assertEqual(d["stage"], "git fetch failed: Could not resolve host: github.com")
        self.assertTrue(d["hint"].startswith("This version keeps running. Check the connection, then Update now again. More in "), d)
        self.assertTrue(d["hint"].endswith("restart.log."), d)
        self.assertTrue(d["card"], d)


if __name__ == "__main__":
    unittest.main()
