"""#330: Update now shows one card from the press until the page reloads itself.

In headless Chrome over CDP, against a hub in a thread (its own state in a
temp dir) on a source checkout. The restart itself is stubbed (Linux has no
self-restart, #336): the git fetch is a stub, and the helper is a thread that
moves the hub's own update stage (dashboard.src_update_stage) the way a
helper's calls do. "The hub is down" is the hub closing every API request
without an answer; "the new hub" is /api/config answering with another pid.

* success: Install now in What's new shows the card at once (Fetching the update
  from GitHub), then Checking that the new code starts, Restarting the hub,
  The hub is restarting, back in a moment, and The new code is running ·
  Reloading the page; the time line counts up from 0:0x; the page then
  reloads by itself on the new hub, and the drain banner never shows;
* a helper that gives up while the hub runs on: the card goes, the update
  dialog says Not updated, why, and that this version keeps running; Close;
* a fetch that fails: the dialog says git fetch failed and to check the
  connection, naming restart.log;
* a request that never reaches the hub (the hub up, no update under way):
  the dialog says so within seconds, not after the ten-minute stall;
* the hub away past the limit, then back the same: Install now again works,
  and the first attempt's loop is over (a later new hub reloads nothing);
* the hub not answering before the request: no request, the dialog says so,
  and after every failure with a reason What's new still offers Install now;
* a hung status request ends even without AbortSignal.timeout;
* the app: a helper that gives up after its preflight (the copy) is a
  failure with its reason at once.

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
    // Install now in What's new (#339), pressed as a person would.
    const install = async () => {
      await ev(`(document.getElementById('me-whatsnew').click(), true)`);
      await until(`document.getElementById('wn-dialog').open`, 50);
      await click('#wn-install');
    };
    const offered = `!document.getElementById('me-whatsnew').hidden && !document.getElementById('wn-dialog').open`;
    const open = async () => {
      await c.send('Page.navigate', { url: A.base + '/' }, s);
      await until(`typeof RUNTIME_CFG !== 'undefined' && !!RUNTIME_CFG && !!RUNTIME_CFG.pid && !!window.whatsNew && !!whatsNew.info && !!whatsNew.info.available`, 300);
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
        if (g.failed) { R.failed = true; R.failMs = Date.now() - t0; break; }
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
        await install();
        await watch(R, NAMES);
        await until(`typeof RUNTIME_CFG !== 'undefined' && !!RUNTIME_CFG && !!RUNTIME_CFG.pid`, 300);
        R.pidAfter = await ev('RUNTIME_CFG.pid');
        R.cardAfter = await ev(`document.getElementById('upd-card').hidden`);
      } }
    // 2. The helper gives up while the hub runs on.
    { const R = out.fail = {};
      await open();
      await install();
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
      await install();
      await watch(R, {});
      await sleep(200);
      R.dialog = await ev(`({ title: document.querySelector('#update-overlay h3').textContent, stage: document.getElementById('update-stage').textContent,
        hint: document.getElementById('update-hint').textContent, card: document.getElementById('upd-card').hidden })`);
      await shot('12-fail-fetch'); }
    const dialog = () => ev(`({ title: document.querySelector('#update-overlay h3').textContent, stage: document.getElementById('update-stage').textContent,
        hint: document.getElementById('update-hint').textContent, card: document.getElementById('upd-card').hidden })`);
    // 4. The request gets no answer and the hub, up, never started an update.
    { const R = out.lost = {};
      await open();
      await install();
      await watch(R, {});
      R.dialog = await dialog();
      await click('#update-close');
      R.bannerAfter = await ev(offered); }
    // 5. The hub stays away past the limit (it says so and keeps looking),
    //    comes back the same; Install now again fails; then a new hub answers:
    //    the first attempt's loop is over, so nothing reloads the page.
    { const R = out.retry = {};
      await open();
      await ev('UPD_LIMITS.downS = 1.5; true');
      await install();
      await watch(R, {});
      R.notBack = await dialog();
      R.back = await until(`fetch('/api/config').then(r => r.ok, () => false)`, 150);   // the same hub is back
      await click('#update-close');
      await install();
      const R2 = R.second = {};
      await watch(R2, {});
      R2.dialog = await dialog();
      R.pid = await ev('RUNTIME_CFG.pid');
      await sleep(5000);                  // a new hub answers 3 s after the failure
      R.newPid = await ev(`fetch('/api/config').then(r => r.json()).then(c => c.pid)`);
      R.markAfter = await ev('window.__mark330 || 0'); }
    // 6. The hub does not answer before the request: no request goes out,
    //    What's new opens again and says why Install now did not start.
    { const R = out.noBaseline = {};
      await open();
      await ev(`(() => { const f = window.fetch; window.__posts = 0;
        window.fetch = (u, o) => { const p = String(u);
          if (p.startsWith('/api/update-status')) return Promise.reject(new TypeError('Failed to fetch'));
          if (p === '/api/update' && o && o.method === 'POST') window.__posts++;
          return f(u, o); }; return true; })()`);
      await install();
      await until(`document.getElementById('wn-dialog').open && document.getElementById('wn-msg').textContent === 'The hub could not be reached, try again.'`, 100);
      R.dialogOpen = await ev(`document.getElementById('wn-dialog').open`);
      R.message = await ev(`document.getElementById('wn-msg').textContent`);
      R.posts = await ev('window.__posts');
      await click('#wn-later');
      R.bannerAfter = await ev(offered); }
    // 7. A browser without AbortSignal.timeout still gives up on a hung request.
    { const R = out.hang = {};
      await open();
      R.got = await ev(`(async () => { UPD_LIMITS.fetchS = 1; AbortSignal.timeout = undefined;
        const t = performance.now(); const v = await updJson('/api/__hang'); return { v, ms: performance.now() - t }; })()`); }
    // 7b. An update check in flight when Update now is pressed: its answer
    //     reaches What's new only when no press came meanwhile.
    { const R = out.check = {};
      await open();
      R.got = await ev(`(async () => {
        const f = window.fetch, wn = whatsNew.render; let answer = null, renders = 0;
        whatsNew.render = i => { renders++; return wn(i); };
        window.fetch = (u, o) => String(u).startsWith('/api/update-check')
          ? new Promise(r => { answer = r; }) : f(u, o);
        const reply = body => answer(new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }));
        const settle = () => new Promise(r => setTimeout(r, 50));
        const info = { available: true, kind: 'source', behind: 1, latestSha: 'abc1234', latestMessage: 'x' };
        // A: the answer comes while the update runs (the press as triggerUpdate makes it).
        let p = checkForUpdate(); await settle();
        UPD_ATTEMPT++; UPDATE_IN_PROGRESS = true;
        reply(info); await p; await settle();
        const during = renders === 0;
        // B: the update failed (Update now is back) before the answer, which says nothing is available.
        UPDATE_IN_PROGRESS = false;
        p = checkForUpdate(); await settle();
        UPD_ATTEMPT++; UPDATE_IN_PROGRESS = true;
        UPDATE_IN_PROGRESS = false;      // fail(): What's new still offers Install now
        reply({ available: false }); await p; await settle();
        const after = renders === 0 && !document.getElementById('me-whatsnew').hidden;
        // C: no press meanwhile: the answer is used as before.
        p = checkForUpdate(); await settle();
        reply({ available: false }); await p; await settle();
        const plain = renders === 1 && document.getElementById('me-whatsnew').hidden;
        whatsNew.render = wn;
        window.fetch = f;
        return { during, after, plain };
      })()`); }
    // 8. The app: the helper gives up after the preflight (the copy).
    { const R = out.app = {};
      await open();
      R.packaged = await ev('!!RUNTIME_CFG.packaged');
      await install();
      await watch(R, {});
      R.dialog = await dialog(); }
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
        self.lost_until = 0.0

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
        if mode in ("fail", "late"):
            dashboard._drop_restart_lease(lease)
            dashboard.src_update_stage("failed", lease, error="The new code did not start: "
                                       "PREFLIGHT FAILED - the new code did not serve on port 9000")
            if mode == "late":
                time.sleep(3.0)
                self.gen += 1                 # some later restart: no page loop of before may react
            return
        dashboard.src_update_stage("restarting", lease)   # its snapshot call, just before the stop
        time.sleep(1.5)
        self.down = True
        time.sleep(3.0 if mode == "ok" else 4.0)
        dashboard._drop_restart_lease(lease)
        if mode == "ok":
            self.gen += 1                     # a new process answers
        self.down = False                     # "away": the same one is back

    def packaged(self):
        return self.mode == "app" or (self.modes[:1] == ["app"])

    def start_app_update(self):
        self.mode = self.modes.pop(0)
        with dashboard._APP_UPDATE_LOCK:
            dashboard._APP_UPDATE.update(stage="downloading", to="9.9.9", error="", at=time.time())

        def helper():
            time.sleep(1.0)
            with dashboard._APP_UPDATE_LOCK:
                dashboard._APP_UPDATE.update(stage="restarting")
            time.sleep(1.5)                   # past its preflight, the copy fails; the hub runs on
            dashboard.UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            (dashboard.UPDATE_DIR / "last.json").write_text(json.dumps({
                "at": time.time(), "from": "0.9.4", "to": "9.9.9", "ok": False, "stage": "copy",
                "reason": "could not copy the new version next to the app; nothing was changed: disk full"}))
        threading.Thread(target=helper, daemon=True).start()
        return {"started": True, "version": "9.9.9", "pid": os.getpid()}


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
        hub = cls.hub = FakeHub(["ok", "fail", "fetch", "lost", "away", "late", "app"])
        real_get, real_post, real_json = dashboard.Handler.do_GET, dashboard.Handler.do_POST, dashboard.Handler._send_json

        def away(h) -> bool:
            if hub.down and h.path.startswith("/api/"):
                h.close_connection = True     # no answer at all
                return True
            return False

        def do_get(h):
            if h.path.startswith("/api/__hang"):
                time.sleep(4)                 # a request that hangs
            if not away(h):
                real_get(h)

        def do_post(h):
            if h.path.split("?")[0] == "/api/update" and (hub.modes[:1] == ["lost"] or time.time() < hub.lost_until):
                if hub.modes[:1] == ["lost"]:
                    hub.mode = hub.modes.pop(0)
                    hub.lost_until = time.time() + 2   # Chrome sends it again at once on a fresh socket
                h.close_connection = True     # the request never reaches the hub
                return
            if not away(h):
                real_post(h)

        def send_json(h, code, payload):
            if h.path.split("?")[0] == "/api/config" and isinstance(payload, dict) and "pid" in payload:
                payload = {**payload, "pid": payload["pid"] + 1000 * hub.gen}
            return real_json(h, code, payload)
        info = {"available": True, "kind": "source", "behind": 2, "latestSha": "abc1234",
                "latestMessage": "Merge #330: the Update now card"}
        app_info = {"available": True, "kind": "release", "latestVersion": "9.9.9", "currentVersion": "0.9.4",
                    "release": {"version": "9.9.9"}}
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
            mock.patch.object(dashboard, "check_for_update", lambda *a, **k: app_info if hub.packaged() else info),
            mock.patch.object(dashboard.app_version, "packaged", hub.packaged),
            mock.patch.object(dashboard, "start_app_update", hub.start_app_update),
            mock.patch.object(dashboard, "UPDATE_DIR", state / "update"),
            mock.patch.dict(dashboard._APP_UPDATE, {"stage": "idle"}, clear=True),
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

    def test_a_request_that_never_arrived_says_so(self):
        g = self.got["lost"]
        self.assertTrue(g.get("failed"), g)
        self.assertLess(g["failMs"], 15000)
        self.assertEqual(g["dialog"]["title"], "Not updated")
        self.assertEqual(g["dialog"]["stage"], "The hub did not get the update request")
        self.assertIn("Check the connection, then Install now again.", g["dialog"]["hint"])

    def test_after_a_failure_update_now_is_there_again(self):
        self.assertTrue(self.got["lost"]["bannerAfter"])
        self.assertTrue(self.got["noBaseline"]["bannerAfter"])

    def test_a_hub_that_does_not_answer_first_gets_no_request(self):
        g = self.got["noBaseline"]
        self.assertTrue(g["dialogOpen"], g)
        self.assertEqual(g["message"], "The hub could not be reached, try again.")
        self.assertEqual(g["posts"], 0)

    def test_a_hung_request_ends_without_abortsignal_timeout(self):
        g = self.got["hang"]["got"]
        self.assertIsNone(g["v"])
        self.assertLess(g["ms"], 2500)

    def test_after_not_back_a_new_press_ends_the_old_loop(self):
        g = self.got["retry"]
        self.assertTrue(g.get("failed"), g)
        self.assertRegex(g["notBack"]["stage"], r"^Waited ")
        self.assertTrue(g["back"], g)
        s = g["second"]
        self.assertTrue(s.get("failed"), s)
        self.assertEqual(s["dialog"]["stage"],
                         "The new code did not start: PREFLIGHT FAILED - the new code did not serve on port 9000")
        self.assertNotEqual(g["newPid"], g["pid"], "a new hub answers")
        self.assertEqual(g["markAfter"], 1, "the first attempt's loop did not reload the page")

    def test_an_update_check_in_flight_leaves_what_s_new_to_the_update(self):
        g = self.got["check"]["got"]
        self.assertTrue(g["during"], "the answer is dropped while the update runs")
        self.assertTrue(g["after"], "Install now is still offered after the failure")
        self.assertTrue(g["plain"], "with no press meanwhile the answer still renders")

    def test_an_app_helper_that_fails_after_the_preflight_says_why(self):
        g = self.got["app"]
        self.assertTrue(g["packaged"], g)
        self.assertTrue(g.get("failed"), g)
        self.assertLess(g["failMs"], 15000, "not the ten-minute stall")
        self.assertEqual(g["dialog"]["stage"],
                         "could not copy the new version next to the app; nothing was changed: disk full")
        self.assertEqual(g["steps"][:2], ["Downloading 9.9.9", "Checking that 9.9.9 starts"], g)

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
        self.assertTrue(d["hint"].startswith("This version keeps running. Check the connection, then Install now again. More in "), d)
        self.assertTrue(d["hint"].endswith("restart.log."), d)
        self.assertTrue(d["card"], d)


if __name__ == "__main__":
    unittest.main()
