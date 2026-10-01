"""With the word for a board set to Initiative, the main screens never say
"project" (index.html; #130).

A hub in a thread serves the pages with projectNoun Initiative / Initiatives
and one board, Motors, holding a few tasks and no PO. Headless Chrome (through
tests/chrome_profile.py) opens it with a new, empty profile, so the page first
paints with the default word and has to take the hub's (the case of a browser
that has not seen a change made on another device). Then, at 1440x900 and on a
390px phone, every visible text, tooltip, accessible name, placeholder and
option is read on:

* home: the bar and the list;
* the board: the bar, its menu, the list and the page;
* Create (the new task dialog);
* Settings, but for the word picker itself, whose first choice is "Project".

None may say project, in any case, and the list does say Initiatives.
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
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
CHROME = chrome_profile.CHROME

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// Every visible "project" under a root: text, the attributes a person reads
// (a tooltip, an accessible name, a placeholder) and a shown select's options.
const SCAN = sel => `(() => {
  const root = ${sel ? `document.querySelector(${JSON.stringify(sel)})` : 'document.body'};
  if (!root) return ['no ' + ${JSON.stringify(sel || 'body')}];
  const skip = el => !!el.closest('#pref-noun, script, style, template');
  const vis = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden'; };
  const bad = s => /project/i.test(s || '');
  const hits = [];
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (tw.nextNode()) {
    const n = tw.currentNode, el = n.parentElement;
    if (!el || skip(el) || el.closest('option')) continue;
    if (bad(n.nodeValue) && vis(el)) hits.push('text (' + (el.id || el.className || el.tagName) + '): ' + n.nodeValue.trim().slice(0, 160));
  }
  for (const el of [root, ...root.querySelectorAll('[title],[aria-label],[placeholder]')]) {
    if (skip(el) || !vis(el)) continue;
    for (const a of ['title', 'aria-label', 'placeholder']) {
      const v = el.getAttribute(a);
      // The backup's example remote is a URL, not the word.
      if (bad(v) && !/ensemble-projects\\.git/.test(v)) hits.push(a + ' (' + (el.id || el.className || el.tagName) + '): ' + v.slice(0, 160));
    }
  }
  for (const o of root.querySelectorAll('select option')) if (vis(o.parentElement) && bad(o.textContent)) hits.push('option: ' + o.textContent);
  return hits;
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && !!PREFS', 30000);
    await until('noun("one") === "initiative"', 20000);
    await sleep(800);
    return { evalIn, until, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  try {
    for (const [name, w, h, mobile] of [['desktop', 1440, 900, false], ['phone', 390, 844, true]]) {
      const p = await page(w, h, mobile);
      const res = {};
      res.home = await p.evalIn(SCAN(''));
      res.homeText = await p.evalIn('document.body.innerText');
      await p.shot(`noun-${name}-home`);
      await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); syncProjSwitch(); return 0; })()`);
      await sleep(600);
      res.board = await p.evalIn(SCAN(''));
      await p.shot(`noun-${name}-board`);
      await p.evalIn(`document.getElementById('proj-switch').click(), 0`);
      await sleep(300);
      res.menu = await p.evalIn(SCAN('#proj-menu'));
      await p.shot(`noun-${name}-menu`);
      await p.evalIn(`document.body.click(), document.getElementById('proj-menu').hidden = true, 0`);
      await p.evalIn(`document.getElementById('new-btn').click(), 0`);
      await p.until(`!!document.querySelector('#modal[open] #ns-proj')`);
      await sleep(300);
      res.create = await p.evalIn(SCAN('#modal'));
      res.createText = await p.evalIn(`document.getElementById('modal').innerText`);
      await p.shot(`noun-${name}-create`);
      await p.evalIn(`document.getElementById('modal').close(), 0`);
      await p.evalIn(`openSettingsPanel(document.getElementById('me-btn')), 0`);
      await sleep(300);
      res.settings = await p.evalIn(SCAN('#settings-panel'));
      res.settingsText = await p.evalIn(`document.getElementById('settings-panel').innerText`);
      // The five choices sit in one row inside the panel, none cut off.
      res.fits = await p.evalIn(`(() => { const s = document.getElementById('pref-noun'), bs = [...s.querySelectorAll('button')];
        const r = s.getBoundingClientRect(), pr = document.getElementById('settings-panel').getBoundingClientRect();
        return { inPanel: r.left >= pr.left - 1 && r.right <= pr.right + 1, noOverflow: s.scrollWidth <= s.clientWidth + 1,
                 oneRow: new Set(bs.map(b => Math.round(b.getBoundingClientRect().top))).size === 1,
                 panelLeft: Math.round(pr.left), segW: Math.round(r.width), panelW: Math.round(pr.width) }; })()`);
      res.picked = await p.evalIn(`[...document.querySelectorAll('#pref-noun button.active')].map(b => b.textContent)`);
      await p.shot(`noun-${name}-settings`);
      out[name] = res;
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
class TheMainScreensSayInitiative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-noun-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "Boards"   # a folder name with the word would read as a hit
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
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        dashboard.save_settings({"projectNoun": {"one": "Initiative", "many": "Initiatives"}})
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        members = [{"identity": "claude", "agent": "claude", "model": "claude-opus-5-5", "cwd": str(home)}]
        for title in ("Brakes", "Gearbox", "Paint"):
            r = chatroom.create_room(title, members)
            dashboard.assign_session_project(r["id"], cls.proj)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "proj": cls.proj, "shots": shots}
        script = base / "noun_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_no_screen_says_project(self):
        for size in ("desktop", "phone"):
            for screen in ("home", "board", "menu", "create", "settings"):
                with self.subTest(size=size, screen=screen):
                    self.assertEqual(self.got[size][screen], [])

    def test_the_screens_say_initiative(self):
        for size in ("desktop", "phone"):
            g = self.got[size]
            with self.subTest(size=size):
                self.assertIn("nitiative", g["homeText"])
                self.assertIn("Initiative", g["createText"])
                self.assertIn("initiative", g["settingsText"])
                self.assertEqual(g["picked"], ["Initiative"])
                f = g["fits"]
                self.assertTrue(f["inPanel"] and f["noOverflow"], f)


if __name__ == "__main__":
    unittest.main()
