"""What's new in the page (#339), in headless Chrome over CDP against a hub in a
thread serving the pages, with an update faked on the hub (check_for_update
and cached_update_check patched; the merge subjects go through update_notes.plain_title as the real
check does). Checked:

* no #update-banner anywhere, at 1728x1117, 1440x900 and a phone (390x844);
* the chip on the user button: shown, 12px, on the avatar's top-right corner,
  and the avatar keeps its size (28x28 on a computer, 44x44 on a phone);
* the menu's What's new item opens the dialog; its list has no task number,
  point or hash; "and N more"; the wait note names the working agents;
* Later and Esc close it and keep the chip; Install now closes it and calls
  triggerUpdate (stubbed);
* Skip this version hides the chip, keeps the item as "What's new · skipped",
  and holds over a reload and in a second browser profile (the hub keeps it);
* the page threw nothing meanwhile.

Measures and screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped
without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import re
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
import restart_drain  # noqa: E402
import update_notes  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1728, 1117, False), (1440, 900, False), (390, 844, True)]
SUBJECTS = [
    "Merge #328 (P294, P296): Projects above the Inbox in one left panel, the Inbox follows the pick",
    "Merge #327 (P290): Ensemble runs Dock v0.24.0, which puts a minimised pinned strip panel away itself",
    "Merge #325: An answer opened from the Inbox counts as checked (Checked, Undo)",
    "Merge fix: #326's pinned panel goes back to its strip button (2d8ec38)",
    "Merge #324 (P280, #318): The board tells what is really running",
]
LATEST = "2d8ec38f00c0ffee1234567890abcdef12345678"

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const STATE = `(() => {
  const R = e => { if (!e) return null; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { x: r.left, y: r.top, w: r.width, h: r.height } : null; };
  const btn = document.getElementById('me-btn'), dot = btn && btn.querySelector('.upd-dot'), item = document.getElementById('me-whatsnew');
  const d = document.getElementById('wn-dialog');
  return { banner: !!document.getElementById('update-banner'), btn: R(btn), dot: dot && !dot.hidden ? R(dot) : null,
    label: btn ? btn.getAttribute('aria-label') : '', itemHidden: !item || item.hidden, itemQuiet: !!item && item.classList.contains('quiet'),
    itemText: item ? item.textContent.replace(/\\s+/g, ' ').trim() : '', dialogOpen: !!d && d.open };
})()`;
const DIALOG = `(() => {
  const d = document.getElementById('wn-dialog'), t = id => (document.getElementById(id) || { textContent: '' }).textContent;
  return { open: d.open, head: t('wn-heading'), sub: t('wn-sub'), rows: [...d.querySelectorAll('#wn-box ol > li')].map(li => li.textContent),
    more: t('wn-box').match(/and \\d+ more/) ? t('wn-box').match(/and \\d+ more/)[0] : '', wait: document.getElementById('wn-wait').hidden ? '' : t('wn-wait'),
    skipHidden: document.getElementById('wn-skip').hidden, focus: document.activeElement ? document.activeElement.id : '',
    box: (() => { const r = d.getBoundingClientRect(); return { w: r.width, h: r.height, r: r.right }; })(), W: document.documentElement.clientWidth };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { sizes: {} };
  const page = async (w, h, mobile, ctx) => {
    const { targetId } = await c.send('Target.createTarget', ctx ? { url: 'about:blank', browserContextId: ctx } : { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const key = async (k, code, vk) => { for (const type of ['rawKeyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const load = async () => {
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('window.ensBooted === true && !!window.whatsNew && !!whatsNew.info', 30000);
      await sleep(300);
    };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await load();
    return { evalIn, until, click, key, shot, load, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const openDialog = async (p) => {
    await p.click(`document.getElementById('me-btn')`);
    await p.until(`!document.getElementById('me-menu').hidden`, 5000);
    await p.click(`document.getElementById('me-whatsnew')`);
    await p.until(`document.getElementById('wn-dialog').open && !document.getElementById('wn-wait').hidden`, 8000).catch(() => null);
    await sleep(200);
  };
  try {
    for (const [w, h, mobile] of A.sizes) {
      const p = await page(w, h, mobile);
      const s = { before: await p.evalIn(STATE) };
      await p.shot(`whatsnew-chip-${w}`);
      await openDialog(p);
      s.dialog = await p.evalIn(DIALOG);
      await p.shot(`whatsnew-dialog-${w}`);
      // A row's full text, by a click.
      await p.click(`document.querySelector('#wn-box ol > li')`);
      s.rowOpen = await p.evalIn(`document.querySelector('#wn-box ol > li').getAttribute('aria-expanded')`);
      await p.click(`document.getElementById('wn-later')`);
      await sleep(200);
      s.afterLater = await p.evalIn(STATE);
      s.focusAfterLater = await p.evalIn(`document.activeElement ? document.activeElement.id : ''`);
      out.sizes[w] = s;
      await p.close();
    }
    // ---- Esc, Install, then Skip, at 1440
    const p = await page(1440, 900, false);
    await openDialog(p);
    await p.key('Escape', 'Escape', 27);
    await sleep(200);
    out.afterEsc = await p.evalIn(STATE);
    // The hub restarted since this page's last poll: its cache is cold. The
    // dialog keeps what it shows and the page's ordinary poll runs.
    fs.writeFileSync(path.join(A.tmp, 'cold'), '1');
    await p.evalIn(`window.__polls = 0; { const f = window.checkForUpdate; window.checkForUpdate = () => { window.__polls++; return f(); }; } 0`);
    await openDialog(p);
    await p.until('window.__polls > 0', 5000).catch(() => null);
    await sleep(300);
    out.cold = { polls: await p.evalIn('window.__polls'), dialog: await p.evalIn(DIALOG), state: await p.evalIn(STATE) };
    fs.unlinkSync(path.join(A.tmp, 'cold'));
    await p.click(`document.getElementById('wn-later')`);
    await sleep(200);
    await p.evalIn(`window.__trig = 0; window.triggerUpdate = () => { window.__trig++; }; 0`);
    await openDialog(p);
    await p.click(`document.getElementById('wn-install')`);
    await sleep(200);
    out.install = { trig: await p.evalIn('window.__trig'), state: await p.evalIn(STATE) };
    await openDialog(p);
    await p.click(`document.getElementById('wn-skip')`);
    await p.until(`!document.getElementById('wn-dialog').open`, 8000).catch(() => null);
    await sleep(200);
    out.afterSkip = await p.evalIn(STATE);
    await p.load();
    out.skipReload = await p.evalIn(STATE);
    await p.click(`document.getElementById('me-btn')`);
    await sleep(200);
    await p.shot('whatsnew-menu-skipped');
    await p.click(`document.getElementById('me-whatsnew')`);
    await p.until(`document.getElementById('wn-dialog').open`, 8000).catch(() => null);
    await sleep(300);
    out.skipDialog = await p.evalIn(DIALOG);
    await p.close();
    // A second profile: the hub holds the skip.
    const { browserContextId } = await c.send('Target.createBrowserContext');
    const q = await page(1440, 900, false, browserContextId);
    out.skipOther = await q.evalIn(STATE);
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'whatsnew.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def fake_check(force: bool = False) -> dict:
    day = 86400
    now = int(time.time())
    items = [{"text": update_notes.plain_title(s), "at": now - i * day} for i, s in enumerate(SUBJECTS)]
    return {"available": True, "currentSha": "aceeb60", "latestSha": LATEST[:7], "latestId": LATEST,
            "behind": 31, "latestMessage": SUBJECTS[0], "items": items, "more": 2, "updates": 7,
            "currentDate": now - 9 * day, "latestDate": now, "keepsHost": False}


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class WhatsNewInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-wn-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "EnsembleProjects").mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.settings = state / "settings.json"
        agents = {"a": {"state": "working"}, "b": {"state": "idle", "background": True}, "c": {"state": "idle"}}
        patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", cls.settings),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "check_for_update", fake_check),
            mock.patch.object(dashboard, "cached_update_check",
                              lambda: ({"available": False, "reason": "not_checked"}
                                       if (base / "cold").exists() else fake_check())),
            mock.patch.object(dashboard.app_version, "packaged", lambda: False),
            mock.patch.object(restart_drain, "holding", lambda: False),
            mock.patch.object(restart_drain, "observe_hub", lambda: agents),
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
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "sizes": SIZES, "shots": shots}
        script = base / "whatsnew_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    def test_no_banner(self):
        for w, _, _ in SIZES:
            with self.subTest(w=w):
                self.assertFalse(self.got["sizes"][str(w)]["before"]["banner"])

    def test_the_chip_sits_on_the_avatar_and_leaves_its_size(self):
        for w, _, mobile in SIZES:
            s = self.got["sizes"][str(w)]["before"]
            with self.subTest(w=w):
                side = 44 if mobile else 28
                self.assertEqual((round(s["btn"]["w"]), round(s["btn"]["h"])), (side, side), s["btn"])
                d = s["dot"]
                self.assertIsNotNone(d, "the chip shows")
                self.assertEqual((round(d["w"]), round(d["h"])), (12, 12))
                # Top-right corner: its centre right of and above the button's centre, inside the corner.
                b = s["btn"]
                self.assertGreater(d["x"] + 6, b["x"] + b["w"] / 2)
                self.assertLess(d["y"] + 6, b["y"] + b["h"] / 2)
                self.assertLessEqual(d["x"] + d["w"], b["x"] + b["w"] + 3)
                self.assertGreaterEqual(d["y"], b["y"] - 3)
                self.assertIn("update", s["label"].lower())
                self.assertFalse(s["itemHidden"])
                self.assertIn("What's new", s["itemText"])

    def test_the_dialog_lists_plain_words(self):
        for w, _, _ in SIZES:
            g = self.got["sizes"][str(w)]["dialog"]
            with self.subTest(w=w):
                self.assertTrue(g["open"])
                self.assertEqual(g["head"], "7 updates available")
                self.assertRegex(g["sub"], r"^You have the version from \d+ \w+\. The newest is from \d+ \w+\.$")
                self.assertEqual(len(g["rows"]), len(SUBJECTS))
                for row in g["rows"] + [g["sub"], g["head"]]:
                    self.assertNotRegex(row, r"#\d|\bP\d+\b|\b[0-9a-f]{7,}\b|Merge", row)
                self.assertEqual(g["rows"][0], "Projects above the Inbox in one left panel, the Inbox follows the pick")
                self.assertEqual(g["more"], "and 2 more")
                self.assertTrue(g["wait"].startswith("2 agents are working."), g["wait"])
                self.assertEqual(g["focus"], "wn-install")
                self.assertLessEqual(g["box"]["r"], g["W"], "the dialog fits the screen")
                self.assertEqual(self.got["sizes"][str(w)]["rowOpen"], "true")

    def test_later_and_esc_keep_the_chip(self):
        for w, _, _ in SIZES:
            s = self.got["sizes"][str(w)]
            with self.subTest(w=w):
                self.assertFalse(s["afterLater"]["dialogOpen"])
                self.assertIsNotNone(s["afterLater"]["dot"])
                self.assertEqual(s["focusAfterLater"], "me-btn")
        self.assertFalse(self.got["afterEsc"]["dialogOpen"])
        self.assertIsNotNone(self.got["afterEsc"]["dot"])

    def test_a_cold_hub_cache_keeps_the_open_dialog_and_polls(self):
        g = self.got["cold"]
        self.assertTrue(g["dialog"]["open"], g)
        self.assertEqual(len(g["dialog"]["rows"]), len(SUBJECTS))
        self.assertFalse(g["state"]["itemHidden"])
        self.assertGreaterEqual(g["polls"], 1, "the ordinary poll ran")

    def test_install_calls_trigger_update(self):
        self.assertEqual(self.got["install"]["trig"], 1)
        self.assertFalse(self.got["install"]["state"]["dialogOpen"])

    def test_skip_hides_the_chip_everywhere(self):
        for name in ("afterSkip", "skipReload", "skipOther"):
            s = self.got[name]
            with self.subTest(name):
                self.assertIsNone(s["dot"], s)
                self.assertFalse(s["itemHidden"], "the item stays, quiet")
                self.assertTrue(s["itemQuiet"])
                self.assertEqual(s["itemText"], "What's newskipped", "the item, then its muted tag 'skipped'")
                self.assertNotIn("update", s["label"].lower())
        self.assertTrue(self.got["skipDialog"]["skipHidden"])
        self.assertEqual(json.loads(self.settings.read_text())["updateSkipped"], LATEST)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
