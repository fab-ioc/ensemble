"""Dock v0.22.2 in Ensemble (#239): a layout file saved by Dock v0.22.1 or
earlier from a smaller window has split fractions that add up to more than 1;
it loads with its panels inside the window, in the proportions it meant.

In headless Chrome over CDP at 1728x1000, against a hub in a thread
(test_tool_strip's): the dock's own export, with Needs you, Board and Files
docked in a column beside the conversation at 0.939 / 0.964 / 0.391 of its
height (the bug report's numbers, 2.29 in all), goes back in through
importLayout (as Dock's Import layout menu does). Every stack is then drawn
inside the dock and the window, the column's three stacks keep the file's
proportions (0.409 / 0.42 / 0.17 of it), and an export now promises the
column at most all of itself.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests.test_page_update import CHROME
from tests.test_tool_strip import NODE, start_hub
from tests import chrome_profile

W, H = 1728, 1000
COLUMN = {"points": 0.939, "board": 0.964, "workspace": 0.391}

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', `--window-size=${A.w},${A.h}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown' || (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')) this.errors.push(m.params); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// Each stack of the dock as drawn (its tabs' ids), the dock's box and the window's.
const BOXES = `(() => { const r = e => { const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; };
  const stacks = [...document.querySelectorAll('#po-dock .dk-stack')].filter(e => e.getBoundingClientRect().width > 0)
    .map(e => ({ ids: [...e.querySelectorAll('[data-dk-tab]')].map(t => t.dataset.dkTab), box: r(e) }));
  const tab = id => document.querySelector('#po-dock [data-dk-tab="' + id + '"]');
  const col = tab('points') && tab('points').closest('.dk-split.dk-col');
  return { dock: r(document.querySelector('#po-dock')), win: { w: innerWidth, h: innerHeight }, stacks, col: col ? r(col) : null }; })()`;
const out = {};
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    const S = (m, p) => c.send(m, p, sessionId);
    await S('Page.enable'); await S('Runtime.enable');
    await S('Emulation.setDeviceMetricsOverride', { width: A.w, height: A.h, deviceScaleFactor: 1, mobile: false });
    const evalIn = async (expr) => { const r = await S('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    await S('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' });
    await S('Page.navigate', { url: A.base + '/' });
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
    await until('window.ensBooted === true', 30000);
    await evalIn(`(() => { ['cd-tool-strip', 'cd-tool-open', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k));
      SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#po-dock .dk-strip-btn") && !!document.querySelector("#po-panel iframe.po-session:not([hidden])")', 30000);
    await sleep(500);
    // The dock's own file, its tools docked in a column at the right whose fractions add up to more than 1.
    const file = JSON.parse(await evalIn('PD.dock.exportLayout()'));
    const ids = Object.keys(A.column);
    const lay = file.docks[0].layout;
    const strip = (xs) => (xs || []).filter(x => !ids.includes(typeof x === 'string' ? x : x && x.id));
    lay.auto = strip(lay.auto); lay.hidden = strip(lay.hidden);
    for (const id of ids) if (lay.out) delete lay.out[id];
    lay.floats = (lay.floats || []).filter(f => !(f.panels || []).some(p => ids.includes(p)));
    lay.root = { t: 'split', dir: 'row', kids: [
      { t: 'stack', panels: ['po-chat'], active: 'po-chat' },
      { t: 'split', dir: 'col', size: 0.3, kids: ids.map(id => ({ t: 'stack', panels: [id], active: id, size: A.column[id] })) },
    ] };
    out.imported = await evalIn(`(() => { const r = PD.dock.importLayout(${JSON.stringify(JSON.stringify(file))}); return { ok: r.ok, errors: r.errors }; })()`);
    await sleep(800);
    out.after = await evalIn(BOXES);
    const back = JSON.parse(await evalIn('PD.dock.exportLayout()'));
    const find = n => !n ? null : n.t === 'split' && n.dir === 'col' && n.kids.some(k => k.t === 'stack' && k.panels.includes('points')) ? n : n.t === 'split' ? n.kids.map(find).find(Boolean) || null : null;
    const col = find(back.docks[0].layout.root);
    out.exported = col ? col.kids.map(k => ({ ids: k.panels, size: k.size === undefined ? null : k.size })) : null;
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.consoleErrors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e, JSON.stringify(out).slice(0, 3000)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class OverfullSplitFractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls, prefix="ens-dk222-")
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "w": W, "h": H, "column": COLUMN}
        script = base / "dk222_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_DK222_PRINT"):
            print(json.dumps(cls.got, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_file_is_taken(self):
        self.assertEqual(self.got["imported"], {"ok": True, "errors": []})

    def test_every_panel_is_drawn_inside_the_dock_and_the_window(self):
        a = self.got["after"]
        drawn = {i for s in a["stacks"] for i in s["ids"]}
        self.assertLessEqual({"po-chat", *COLUMN}, drawn, a["stacks"])
        d, win = a["dock"], a["win"]
        self.assertEqual((win["w"], win["h"]), (W, H))
        for s in a["stacks"]:
            b = s["box"]
            with self.subTest(stack=s["ids"]):
                self.assertGreater(b["h"], 24, "more than its title bar")
                self.assertGreaterEqual(b["t"], d["t"] - 0.5)
                self.assertLessEqual(b["b"], d["b"] + 0.5)
                self.assertGreaterEqual(b["l"], d["l"] - 0.5)
                self.assertLessEqual(b["r"], d["r"] + 0.5)
                self.assertLessEqual(b["b"], H + 0.5)
                self.assertLessEqual(b["r"], W + 0.5)

    def test_the_column_keeps_the_file_s_proportions(self):
        # Scaled back: 0.939 / 0.964 / 0.391 of 2.294 is 0.409 / 0.42 / 0.17. Needs you takes its 0.409;
        # Files its 0.17 or its minimum height (PD_MIN, 200px), whichever is more; Board, the biggest,
        # what is left. (v0.22.1 drew Needs you at most of the column and Board at its minimum.)
        a = self.got["after"]
        self.assertIsNotNone(a["col"])
        col = a["col"]["h"]
        h = {i: s["box"]["h"] for s in a["stacks"] for i in s["ids"] if i in COLUMN}
        self.assertAlmostEqual(h["points"] / col, 0.409, delta=0.01, msg=h)
        self.assertAlmostEqual(h["workspace"], max(0.17 * col, 200), delta=3, msg=h)
        self.assertAlmostEqual(h["board"], col - h["points"] - h["workspace"] - 2 * 5, delta=1, msg=h)
        self.assertGreater(h["board"], h["workspace"], h)

    def test_an_export_now_promises_the_column_at_most_all_of_itself(self):
        col = self.got["exported"]
        self.assertIsNotNone(col)
        self.assertLessEqual(sum(k["size"] or 0 for k in col), 1.0, col)

    def test_no_console_errors(self):
        self.assertEqual(self.got["consoleErrors"], [])


if __name__ == "__main__":
    unittest.main()
