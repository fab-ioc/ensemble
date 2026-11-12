"""Install now in What's new (#345, GitHub issue 43: "The popup closes and
nothing happens"), in headless Chrome over CDP at 1440x900 against a hub in a
thread serving the pages. The hub's update check, its cached answer, the
the page key and POST /api/update's restart are faked through a
mode file the browser script writes per case. Install now either sends the
install and shows the Update card, or keeps the dialog open with a plain line:

* loaded while an update is available (the chip, the menu, What's new): the
  hub checks again, the POST is sent, the card shows;
* left open across a hub restart (no check on the hub, a new page key, the
  old hub's pid): the press checks again, gets today's key, sends the POST,
  and the card shows and stays (the hub that runs is not taken for the new one);
* a check the hub's new one contradicts (the update installed meanwhile):
  "Already up to date, nothing to install", no POST, the dialog stays;
* the update went while the dialog was open (the page's poll): it stays, says so;
* the hub away: "The hub could not be reached, try again";
* GitHub away: the hub could not reach it, Install now stays to try again;
* the hub refuses even with today's key: the dialog comes back with the
  refusal in plain words (no room id), and a later Install now from it, once
  the hub allows it, sends the POST;
* the page threw nothing.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import secrets
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
import restart_drain  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests.test_whatsnew_cdp import fake_check as available  # noqa: E402

NODE = shutil.which("node")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mode = m => fs.writeFileSync(path.join(A.tmp, 'mode.json'), JSON.stringify(m));
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// What the person sees: the dialog, its line, its buttons, the Update card.
const SEEN = `(() => {
  const d = document.getElementById('wn-dialog'), g = id => document.getElementById(id);
  const vis = e => !!e && !e.hidden && e.getClientRects().length > 0;
  const card = g('upd-card');
  return { open: !!d && d.open, msg: vis(g('wn-msg')) ? g('wn-msg').textContent : '', warn: g('wn-msg').classList.contains('warn'),
    install: vis(g('wn-install')), installDisabled: g('wn-install').disabled, later: g('wn-later').textContent,
    skip: vis(g('wn-skip')), text: d ? d.textContent : '', card: !!card && !card.hidden,
    step: card && !card.hidden ? (g('upd-card-step') || {}).textContent : '',
    failed: !g('update-overlay').hidden, chip: !!document.querySelector('#me-btn .upd-dot:not([hidden])') };
})()`;
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async () => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('window.ensBooted === true && !!window.whatsNew && !!whatsNew.info && whatsNew.info.available', 30000);
    await sleep(300);
    await evalIn(`{ const original = window.fetch; window.__updatePosts = [];
      window.fetch = async (...args) => { const r = await original(...args);
        if (String(args[0]) === '/api/update' && args[1]?.method === 'POST') window.__updatePosts.push(r.status);
        return r; }; } 0`);
    return { evalIn, until, click, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  // The chip's menu, then What's new (the route the CEO takes).
  const openDialog = async (p) => {
    await p.click(`document.getElementById('me-btn')`);
    await p.until(`!document.getElementById('me-menu').hidden`, 5000);
    await p.click(`document.getElementById('me-whatsnew')`);
    await p.until(`document.getElementById('wn-dialog').open`, 8000);
    await sleep(400);   // its own ?wait=1 read
  };
  const press = async (p, done, name) => {
    await p.click(`document.getElementById('wn-install')`);
    await p.until(done, 40000).catch(() => null);
    await sleep(400);
    await p.shot(name);
    return p.evalIn(SEEN);
  };
  const OK = { cached: 'avail', check: 'avail' };
  const cardUp = `!document.getElementById('upd-card').hidden && window.__updatePosts.includes(202)`;
  const said = `!document.getElementById('wn-msg').hidden && !/^Checking/.test(document.getElementById('wn-msg').textContent)`;
  try {
    // A: a page loaded while an update is available.
    mode({ ...OK, case: 'A' });
    let p = await page();
    await openDialog(p);
    out.A = await press(p, cardUp, 'install-now-available');
    await p.close();

    // B: the page was left open across a hub restart: the hub has no check
    // yet and drew a new page key.
    mode({ ...OK, case: 'B0' });
    p = await page();
    mode({ cached: 'cold', check: 'avail', rotate: 1, case: 'B' });
    await p.evalIn('RUNTIME_CFG.pid = 1; 0');   // the pid of the hub it loaded from
    await openDialog(p);
    out.B = await press(p, cardUp, 'install-now-after-restart');
    await sleep(2500);   // the update's polls: the hub that runs is not the new one
    out.B2 = await p.evalIn(SEEN);
    await p.close();

    // C: the hub's check said available; asked again, it has nothing newer.
    mode({ ...OK, case: 'C0' });
    p = await page();
    mode({ cached: 'avail', check: 'none', case: 'C' });
    await openDialog(p);
    out.C = await press(p, said, 'install-now-nothing-to-install');
    await p.close();

    // D: the update went while the dialog was open (the page's own poll).
    mode({ ...OK, case: 'D0' });
    p = await page();
    await openDialog(p);
    mode({ cached: 'none', check: 'none', case: 'D' });
    await p.evalIn('checkForUpdate()');
    await p.until(said, 8000).catch(() => null);
    await sleep(300);
    out.D = await p.evalIn(SEEN);
    await p.shot('install-now-gone-while-open');
    await p.close();

    // E: the hub away when Install now is pressed.
    mode({ ...OK, case: 'E' });
    p = await page();
    await openDialog(p);
    await p.evalIn(`{ const f = window.fetch; window.fetch = (u, o) => String(u).startsWith('/api/update-check') ? Promise.reject(new TypeError('Failed to fetch')) : f(u, o); } 0`);
    out.E = await press(p, said, 'install-now-hub-away');
    await p.close();

    // F: GitHub away: the hub could not check.
    mode({ ...OK, case: 'F0' });
    p = await page();
    mode({ cached: 'avail', check: 'fetch_failed', case: 'F' });
    await openDialog(p);
    out.F = await press(p, said, 'install-now-github-away');
    await p.close();

    // G: the hub refuses the page, today's key or not; then allows it, and
    // Install now from the same dialog sends the update.
    mode({ ...OK, refuse: true, case: 'G' });
    p = await page();
    await openDialog(p);
    out.G = await press(p, `document.getElementById('wn-dialog').open && ${said}`, 'install-now-refused');
    mode({ ...OK, case: 'G2' });
    out.G2 = await press(p, cardUp, 'install-now-again');
    await p.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class InstallNowInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-inst-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("EnsembleProjects", "transcripts", "cs"):
            (base / d).mkdir()
        cls.log: list[dict] = []
        rotated = [0]

        def mode() -> dict:
            try:
                return json.loads((base / "mode.json").read_text())
            except (OSError, ValueError):
                return {}

        def answer(kind: str) -> dict:
            if kind == "avail":
                return available()
            if kind == "cold":
                return {"available": False, "reason": "not_checked"}
            if kind == "none":
                return {"available": False, "currentSha": "aceeb60"}
            return {"available": False, "reason": kind}

        def check(force: bool = False) -> dict:
            m = mode()
            cls.log.append({"case": m.get("case"), "check": "fresh" if force else "poll"})
            return answer(m.get("check", "avail"))

        real_refusal = dashboard.Handler._restart_refusal

        def refusal(handler) -> str:
            m = mode()
            if m.get("rotate") and m["rotate"] != rotated[0]:
                rotated[0] = m["rotate"]
                dashboard._UI_KEY = secrets.token_urlsafe(32)   # the hub restarted
            why = dashboard.RESTART_REFUSED if m.get("refuse") else real_refusal(handler)
            cls.log.append({"case": m.get("case"), "refusal": handler.path, "refused": bool(why)})
            return why

        def request_restart(reason, who, kind, stop_foreign=False):
            cls.log.append({"case": mode().get("case"), "post": kind})
            return {"status": 202, "started": True, "draining": False}

        patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
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
            mock.patch.object(dashboard, "check_for_update", check),
            mock.patch.object(dashboard, "cached_update_check", lambda: answer(mode().get("cached", "avail"))),
            mock.patch.object(dashboard, "_UI_KEY", dashboard._UI_KEY),
            mock.patch.object(dashboard.Handler, "_restart_refusal", refusal),
            mock.patch.object(dashboard.app_version, "packaged", lambda: False),
            mock.patch.object(restart_drain, "holding", lambda: False),
            mock.patch.object(restart_drain, "request_restart", request_restart),
            mock.patch.object(restart_drain, "observe_hub", lambda: {}),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "shots": shots}
        script = base / "install_now_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    def of(self, case: str, key: str) -> list[dict]:
        return [e for e in self.log if e.get("case") == case and key in e]

    def posts(self, case: str) -> int:
        return len(self.of(case, "post"))

    def assertBusy(self, seen: dict):
        self.assertFalse(seen["open"], seen)
        self.assertTrue(seen["card"], "the Update card shows")
        self.assertEqual(seen["step"], "Fetching the update from GitHub")
        self.assertFalse(seen["failed"])

    def assertSays(self, seen: dict, text: str):
        self.assertTrue(seen["open"], "the dialog stays open")
        self.assertEqual(seen["msg"], text)
        self.assertFalse(seen["card"])
        self.assertNotRegex(seen["text"], r"room-|#\d|\bP\d+\b|\b[0-9a-f]{7,}\b")

    def test_loaded_while_available_installs(self):
        self.assertBusy(self.got["A"])
        self.assertEqual(self.posts("A"), 1)
        self.assertIn({"case": "A", "check": "fresh"}, self.log, "the hub checks again first")

    def test_left_open_across_a_restart_checks_again_and_installs_with_todays_key(self):
        self.assertBusy(self.got["B"])
        self.assertBusy(self.got["B2"])   # not "Reloading the page" on the hub that ran already
        self.assertEqual(self.posts("B"), 1)
        self.assertIn({"case": "B", "check": "fresh"}, self.log)
        refusals = [e["refused"] for e in self.of("B", "refusal") if e["refusal"] == "/api/update"]
        self.assertEqual(refusals, [True, False], "the old key is refused once, today's key passes")

    def test_an_old_check_the_hub_contradicts_says_nothing_to_install(self):
        seen = self.got["C"]
        self.assertSays(seen, "Already up to date, nothing to install.")
        self.assertFalse(seen["warn"])
        self.assertFalse(seen["install"])
        self.assertFalse(seen["skip"])
        self.assertEqual(seen["later"], "Close")
        self.assertFalse(seen["chip"], "the chip goes too")
        self.assertIn({"case": "C", "check": "fresh"}, self.log)
        self.assertEqual(self.posts("C"), 0)

    def test_the_update_gone_while_open_keeps_the_dialog(self):
        seen = self.got["D"]
        self.assertSays(seen, "Already up to date, nothing to install.")
        self.assertEqual(seen["later"], "Close")
        self.assertEqual(self.posts("D"), 0)

    def test_the_hub_away_says_so(self):
        seen = self.got["E"]
        self.assertSays(seen, "The hub could not be reached, try again.")
        self.assertTrue(seen["warn"])
        self.assertTrue(seen["install"])
        self.assertFalse(seen["installDisabled"], "Install now again")
        self.assertEqual(self.posts("E"), 0)

    def test_github_away_says_so(self):
        seen = self.got["F"]
        self.assertSays(seen, "The hub could not reach GitHub to check for the update, try again.")
        self.assertTrue(seen["install"])
        self.assertEqual(self.posts("F"), 0)

    def test_a_refusal_comes_back_in_plain_words_and_install_now_again_installs(self):
        seen = self.got["G"]
        self.assertSays(seen, "The hub refused the update from this page. Reload the page, then Install now again.")
        self.assertTrue(seen["install"])
        self.assertEqual(self.posts("G"), 0)
        self.assertEqual([e["refused"] for e in self.of("G", "refusal") if e["refusal"] == "/api/update"], [True, True],
                         "once with the page's key, once with today's")
        self.assertBusy(self.got["G2"])
        self.assertEqual(self.posts("G2"), 1)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
