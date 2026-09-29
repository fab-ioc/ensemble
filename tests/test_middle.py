"""Layout A step 2 (#124): one conversation in the middle, and the breadcrumb.

In Node, the breadcrumb's levels (index.html crumbsOf, crumbsHtml): project,
the conversation (a task, or the PO), the tool and the file, the last one
where you are.

In headless Chrome over CDP, against a hub in a thread serving the pages, with
a project that has a PO (Motors) and a task in it, and one that has none
(Plain):

* at 1280, 1440 and 1728 the PO's conversation and a task's open full height
  in the middle, their text in a centred column no wider than --conv-w, with
  no page scroll;
* the breadcrumb goes up level by level: the task's tool closes and the
  conversation stays, a PO screen's tool goes back to the PO chat, the project
  goes to its PO's conversation and closes the task, a project without a PO
  goes back to its board;
* the PO pill over an open task gives the middle back to the PO;
* the page reloaded for an update comes back on the same task and tab;
* a phone keeps its own layout: no middle, no breadcrumb.

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
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


def crumbs_block() -> str:
    i = INDEX.index("// ---- The breadcrumb: begin")
    return INDEX[i:INDEX.index("// What the page shows now, in crumbsOf's terms.", i)]


NODE_JS = r"""
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
BLOCK
const pj = { name: 'Motors' };
const task = { no: '#18', title: 'Brakes' };
const out = {
  home: crumbsOf({ project: null }),
  po: crumbsOf({ project: pj, po: true }),
  poTool: crumbsOf({ project: pj, po: true, tool: { k: 'board', name: 'Board' } }),
  poFile: crumbsOf({ project: pj, po: true, tool: { k: 'changes', name: 'Changes' }, file: 'src\\app\\main.py' }),
  plain: crumbsOf({ project: pj, po: false }),
  plainWs: crumbsOf({ project: pj, po: false, tool: { k: 'workspace', name: 'Workspace' }, file: 'C:/p/Motors/README.md' }),
  task: crumbsOf({ project: pj, po: true, task }),
  taskTool: crumbsOf({ project: pj, po: true, task, tool: { k: 'changes', name: 'Changes' } }),
  taskFile: crumbsOf({ project: pj, po: true, task, tool: { k: 'changes', name: 'Changes' }, file: 'a/b.py' }),
  loose: crumbsOf({ project: null, task }),
  toolNoFile: crumbsOf({ project: pj, po: false, tool: { k: 'changes', name: 'Changes' }, file: '' }),
};
out.html = crumbsHtml(out.taskFile);
out.htmlEvil = crumbsHtml(crumbsOf({ project: pj, task: { no: '', title: '<b>x</b>' } }));
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs Node")
class TheCrumbs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, "-e", NODE_JS.replace("BLOCK", crumbs_block())],
                             capture_output=True, encoding="utf-8", timeout=60)
        assert out.returncode == 0, out.stderr
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    def levels(self, key):
        return [(c["level"], c["text"], bool(c.get("current"))) for c in self.got[key]]

    def test_home_has_none(self):
        self.assertEqual(self.got["home"], [])

    def test_a_po_screen_is_project_then_po(self):
        self.assertEqual(self.levels("po"), [("project", "Motors", False), ("po", "PO", True)])
        self.assertEqual(self.levels("poTool"), [("project", "Motors", False), ("po", "PO", False), ("tool", "Board", True)])
        self.assertEqual(self.levels("poFile"), [("project", "Motors", False), ("po", "PO", False),
                                                 ("tool", "Changes", False), ("file", "main.py", True)])
        self.assertEqual(self.got["poFile"][-1]["title"], "src\\app\\main.py", "the whole path in the tooltip")
        self.assertIn("Back to the list", self.got["poFile"][2]["title"])

    def test_a_project_without_a_po_is_where_you_are(self):
        self.assertEqual(self.levels("plain"), [("project", "Motors", True)])
        self.assertEqual(self.levels("plainWs"), [("project", "Motors", False), ("tool", "Workspace", False), ("file", "README.md", True)])
        self.assertEqual(self.levels("toolNoFile"), [("project", "Motors", False), ("tool", "Changes", True)])

    def test_a_task_replaces_the_po(self):
        self.assertEqual(self.levels("task"), [("project", "Motors", False), ("task", "#18 Brakes", True)])
        self.assertEqual(self.levels("taskTool"), [("project", "Motors", False), ("task", "#18 Brakes", False), ("tool", "Changes", True)])
        self.assertIn("closes Changes", self.got["taskTool"][1]["title"])
        self.assertEqual(self.levels("taskFile")[-1], ("file", "b.py", True))
        self.assertEqual(self.levels("loose"), [("task", "#18 Brakes", True)], "a task in no project")

    def test_every_level_but_the_last_is_a_button(self):
        h = self.got["html"]
        self.assertNotIn('data-crumb="project"', h, "the project is #proj-go")
        for level in ("task", "tool"):
            self.assertIn(f'<button type="button" class="bar-btn bar-crumb" data-crumb="{level}"', h)
        self.assertIn('<span class="crumb-here" data-crumb="file" aria-current="location"', h)
        self.assertEqual(h.count("crumb-sep"), 3)
        self.assertNotIn("<b>", self.got["htmlEvil"], "a title is text")


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
// Where the middle is, what is in it and what the bar says.
const MID = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom) }; };
  const vis = e => !!e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden';
  const hd = document.querySelector('header').getBoundingClientRect();
  const open = document.body.classList.contains('detail-open');
  const dp = document.getElementById('detail-panel');
  const chat = open ? dp.querySelector('iframe.dp-session') : document.querySelector('#po-panel iframe.po-session:not([hidden])');
  return { mid: document.body.classList.contains('mid'), open, vw: innerWidth, vh: innerHeight, top: Math.round(hd.bottom),
    panel: document.body.classList.contains('po-dock') && document.body.classList.contains('mid') ? box(PD.els['po-chat'])
      : open ? box(dp) : box(document.getElementById('po-dock-host')), chat: vis(chat) ? box(chat) : null,
    listR: (() => { const l = document.getElementById('switcher'); return l && !l.hidden ? Math.round(l.getBoundingClientRect().right) : 0; })(),
    convW: parseFloat(getComputedStyle(document.body).getPropertyValue('--conv-w')),
    project: vis(document.getElementById('proj-go')) ? document.querySelector('#proj-go .proj-go-name').textContent : null,
    trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].filter(vis).map(e => [e.dataset.crumb, e.textContent, e.tagName === 'BUTTON']),
    crumbsShown: vis(document.getElementById('bar-crumbs')) || vis(document.getElementById('proj-go')),
    header: Math.round(hd.height), scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight,
    itab: SELECTED_SID ? ISSUE_TAB : '', front: PD.dock ? PD.dock.frontOf('po-chat') : '', proj: SELECTED_PROJECT, ptab: PROJECT_TAB,
    fly: PD.dock ? PD.dock.flyOpen() : null,
    flyW: (() => { const f = document.querySelector('#po-dock .dk-flyout.open'); return f && document.body.classList.contains('po-dock') ? Math.round(f.getBoundingClientRect().width) : 0; })(),
    strip: (() => { const s = document.querySelector('#po-dock .dk-strip-right'); return vis(s) && document.body.classList.contains('po-dock') ? Math.round(s.getBoundingClientRect().width) : 0; })() };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const ready = () => until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await ready();
    return { evalIn, until, shot, ready, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = (p, proj, tab) => p.evalIn(`(() => { try { ['cd-tool-strip', 'cd-tool-open', 'cd-po-dock-phone'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = ${JSON.stringify(tab || 'tasks')}; SB_DEST = ''; renderRows(); return 0; })()`);
  const poReady = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && [...document.querySelectorAll(".dk-head, #po-dock .dk-strip-btn")].some(e => e.getBoundingClientRect().height) && !!document.querySelector("#po-panel iframe.po-session:not([hidden])")', 30000);
  const sid = `ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.task)}).sessionId`;
  const openTask = async (p) => {
    await p.evalIn(`openDetail(${sid}); 0`);
    await p.until('!!document.querySelector("#detail-panel iframe.dp-session")');
    await sleep(400);
  };
  const click = (p, sel) => p.evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); e.click(); return 0; })()`);
  try {
    for (const [w, h] of [[1280, 800], [1440, 900], [1728, 1117]]) {
      const p = await page(w, h);
      await go(p, A.proj); await poReady(p); await sleep(500);
      out['po' + w] = await p.evalIn(MID);
      await p.shot(`middle-${w}-po`);
      await openTask(p);
      out['task' + w] = await p.evalIn(MID);
      await p.shot(`middle-${w}-task`);
      if (w !== 1440) { await p.close(); continue; }
      // A task's tool, and the task's crumb that closes it.
      await click(p, '#po-dock .dk-strip-btn[data-dk-auto="changes"]');
      await sleep(300);
      out.taskTool = await p.evalIn(MID);
      await p.shot('middle-1440-task-changes');
      await click(p, '#bar-crumbs button[data-crumb="task"]');
      await sleep(300);
      out.taskUp = await p.evalIn(MID);
      // The page reloaded for an update comes back on the task and its tab.
      await click(p, '#po-dock .dk-strip-btn[data-dk-auto="spec"]');
      await sleep(200);
      await p.evalIn('ensUpd.reload(); 0');
      await sleep(1500);
      await p.ready();
      await p.until('document.body.classList.contains("detail-open") && !!SELECTED_SID', 20000);
      await sleep(400);
      out.reloaded = await p.evalIn(MID);
      // The project's crumb: its PO's conversation, the task closed.
      await click(p, '#proj-go');
      await poReady(p); await sleep(400);
      out.projectUp = await p.evalIn(MID);
      // A PO screen's tool, and the PO crumb that goes back to the chat.
      await p.evalIn("pdReveal('board'); 0");
      await sleep(400);
      out.poTool = await p.evalIn(MID);
      await p.shot('middle-1440-po-board');
      await click(p, '#bar-crumbs button[data-crumb="po"]');
      await sleep(400);
      out.poUp = await p.evalIn(MID);
      // The pill over an open task: the PO's conversation takes the middle.
      await openTask(p);
      await click(p, '#po-pill');
      await sleep(500);
      out.pill = await p.evalIn(MID);
      // A project without a PO: Workspace, and back to its board.
      await go(p, A.plain, 'workspace');
      await p.until('!!document.querySelector(".wsp") && document.querySelector(".wsp").getBoundingClientRect().height > 0');
      await sleep(300);
      out.plainWs = await p.evalIn(MID);
      await click(p, '#proj-go');
      await sleep(300);
      out.plainUp = await p.evalIn(MID);
      // Six themes: the crumbs keep their contrast (colours read from the page).
      out.themes = {};
      await go(p, A.proj); await poReady(p);
      await p.evalIn("pdReveal('board'); 0"); await sleep(300);
      for (const t of ['light', 'dark', 'dim', 'paper', 'contrast', 'fjord']) {
        await p.evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(t)}; 0`);
        await sleep(120);
        out.themes[t] = await p.evalIn(`(() => { const hd = getComputedStyle(document.querySelector('header')).backgroundColor;
          return ['#proj-go', '#bar-crumbs .bar-crumb', '#bar-crumbs .crumb-here', '#bar-crumbs .crumb-sep'].map(s => [s, getComputedStyle(document.querySelector(s)).color, hd]); })()`);
      }
      await p.evalIn('document.documentElement.dataset.theme = "light"; 0');
      // Going somewhere else with a task open: the task gives way (R1-F1).
      await openTask(p);
      await click(p, '#proj-switch');
      await click(p, `#proj-menu .pm-item[data-proj="${A.plain}"]`);
      await sleep(400);
      out.menuAway = await p.evalIn(MID);
      await openTask(p);
      await click(p, '#bar-home');
      await sleep(400);
      out.homeAway = await p.evalIn(MID);
      // A task opened from Home: the caret beside its project is that project's.
      await openTask(p);
      await click(p, '#proj-switch');
      out.caretMenu = await p.evalIn(`(() => { const m = document.getElementById('proj-menu');
        return { head: (m.querySelector('.pm-head') || {}).textContent || '', on: (m.querySelector('.pm-item.on') || { dataset: {} }).dataset.proj || '' }; })()`);
      await click(p, '#proj-switch');
      // The pill from Home with a task open, and a PO opened from Needs you and
      // from Home: the PO takes the middle, no drawer (R1-F2).
      await click(p, '#po-pill');
      await poReady(p); await sleep(500);
      out.pillHome = { ...(await p.evalIn(MID)), peek: await p.evalIn('PO_PEEK') };
      await p.evalIn("SELECTED_PROJECT = null; SB_DEST = 'needsyou'; renderRows(); 0");
      await sleep(300);
      await click(p, '#po-pill');
      await poReady(p); await sleep(400);
      out.pillNeeds = { ...(await p.evalIn(MID)), peek: await p.evalIn('PO_PEEK') };
      await p.evalIn('goHome(); 0');
      await sleep(300);
      await p.evalIn(`openPoOf(projectById(${JSON.stringify(A.proj)})); 0`);
      await poReady(p); await sleep(400);
      out.openPoHome = { ...(await p.evalIn(MID)), peek: await p.evalIn('PO_PEEK') };
      // A Workspace's file, searched and in Recent: its tool crumb goes back to
      // the tree, the file shown in it (R1-F3). The project's, then the task's.
      let wsView = '', readme = '';
      const wsUp = async (key, open, kind) => {
        wsView = `[...WS_VIEWS.values()].find(x => x.el && x.el.isConnected && x.ctx.kind === ${JSON.stringify(kind)})`;
        readme = (kind === 'task' ? '.pd-ws > .dp-pane ' : '') + '.wsp .wsp-tree .wse.file[data-path$="README.md"]';
        await open();
        await p.until(`!!document.querySelector(${JSON.stringify(readme)})`);
        await click(p, readme);
        await sleep(500);
        out[key + 'File'] = await p.evalIn(MID);
        for (const mode of ['search', 'recent']) {
          await p.evalIn(`(() => { const v = ${wsView}; wsOpenTab(v, v.sel);
            if (${JSON.stringify(mode)} === 'search') { v.find.q = 'README'; wsfSchedule(v); } else v.find.recent = true;
            wsfPaint(v); return 0; })()`);
          await sleep(500);
          const before = await p.evalIn(`(() => ({ hidden: ${wsView}.el.querySelector('.wsp-tree').hidden,
            trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].map(e => e.dataset.crumb) }))()`);
          await click(p, '#bar-crumbs button[data-crumb="tool"]');
          await sleep(700);
          out[key + 'Up' + mode] = { ...(await p.evalIn(MID)), before, ...(await p.evalIn(`(() => { const v = ${wsView};
            const el = v.el.querySelector(${JSON.stringify(readme)});
            return { pane: v.pane, q: v.find.q, recent: v.find.recent, treeHidden: v.el.querySelector('.wsp-tree').hidden,
              mark: v.mark || '', shown: !!el && el.getBoundingClientRect().height > 0 }; })()`)) };
        }
      };
      await wsUp('plainWs', async () => { await go(p, A.plain, 'workspace'); }, 'project');
      await wsUp('taskWs', async () => { await go(p, A.proj); await poReady(p); await openTask(p); await click(p, '#po-dock .dk-strip-btn[data-dk-auto="workspace"]'); }, 'task');
      await p.close();
    }
    // ---- a tool hidden on a phone is a tab again on a desktop, and hidden again on the phone (R1-F4)
    {
      const r = await page(430, 932, true);
      await go(r, A.proj); await poReady(r); await sleep(400);
      await r.evalIn("PD.dock.setVisible('workspace', false); 0");
      await sleep(200);
      const tabs = `[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.textContent.trim())`;
      out.bpPhone = { mid: await r.evalIn("document.body.classList.contains('mid')"), ws: await r.evalIn("PD.dock.isVisible('workspace')") };
      await c.send('Emulation.setTouchEmulationEnabled', { enabled: false }, r.sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, r.sessionId);
      await r.until("document.body.classList.contains('mid')");
      await sleep(500);
      out.bpDesk = { ws: await r.evalIn("PD.dock.isVisible('workspace')"), tabs: await r.evalIn(tabs),
        strip: await r.evalIn("[...document.querySelectorAll('#po-dock .dk-strip-btn')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.dataset.dkAuto)") };
      await c.send('Emulation.setDeviceMetricsOverride', { width: 430, height: 932, deviceScaleFactor: 3, mobile: true }, r.sessionId);
      await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, r.sessionId);
      await r.until("!document.body.classList.contains('mid')");
      await sleep(500);
      out.bpBack = { ws: await r.evalIn("PD.dock.isVisible('workspace')") };
      await r.close();
    }
    // ---- a phone keeps its own layout
    const q = await page(430, 932, true);
    await go(q, A.proj); await poReady(q); await sleep(400);
    await openTask(q);
    out.phone = await q.evalIn(MID);
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _rgb(s):
    import re
    m = re.findall(r"[\d.]+", s)
    return tuple(float(x) for x in m[:3])


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(_rgb(a)), _lum(_rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheMiddle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-mid-", ignore_cleanup_errors=True)
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
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                   {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        task = chatroom.create_room("Brakes that squeal on a long descent after rain", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
        chatroom.post_message(task["id"], "user", "Why do the brakes squeal?")
        dashboard.assign_session_project(task["id"], cls.proj)
        cls.task = task["id"]
        (home / "README.md").write_text("# Motors\n", encoding="utf-8")
        ok, plain, _ = dashboard.register_project("Plain")
        assert ok, plain
        cls.plain = plain["id"]
        (Path(plain.get("home") or plain["path"]) / "README.md").write_text("# Plain\n", encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "plain": cls.plain, "task": cls.task, "shots": shots}
        script = base / "middle_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def full_height(self, g, what):
        self.assertTrue(g["mid"], what)
        self.assertLessEqual(abs(g["panel"]["y"] - g["top"]), 1, f"{what}: starts under the bar")
        self.assertLessEqual(abs(g["panel"]["b"] - g["vh"]), 1, f"{what}: reaches the bottom")
        self.assertEqual(g["panel"]["x"], g["listR"], f"{what}: from the list's right edge")
        self.assertGreater(g["listR"], 0, f"{what}: the task list is there")
        self.assertEqual(g["panel"]["r"], g["vw"] - g["strip"] - g["flyW"], f"{what}: to the tool strip (or the tool open beside it)")
        self.assertEqual(g["strip"], 44 if g["open"] or g["front"] else 0, f"{what}: the strip is 44px")
        self.assertIsNotNone(g["chat"], f"{what}: the conversation shows")
        self.assertLessEqual(g["chat"]["w"], g["convW"] + 1, f"{what}: a column")
        left, right = g["chat"]["x"] - g["panel"]["x"], g["panel"]["r"] - g["chat"]["r"]
        self.assertLessEqual(abs(left - right), 2, f"{what}: centred")
        self.assertGreater(g["chat"]["h"], g["vh"] * 0.6, f"{what}: the chat takes the height")
        self.assertLessEqual(g["scrollW"], g["vw"], f"{what}: no sideways scroll")
        self.assertLessEqual(g["scrollH"], g["vh"], f"{what}: no page scroll")
        self.assertLessEqual(g["header"], 50, f"{what}: the bar is one row")

    def test_the_po_and_a_task_open_full_height_in_the_middle(self):
        for w in (1280, 1440, 1728):
            with self.subTest(w=w):
                self.full_height(self.got[f"po{w}"], f"PO at {w}")
                self.full_height(self.got[f"task{w}"], f"task at {w}")

    def test_the_breadcrumb_names_where_you_are(self):
        po, task = self.got["po1440"], self.got["task1440"]
        self.assertEqual(po["project"], "Motors")
        self.assertEqual(po["trail"], [["po", "PO", False]])
        self.assertEqual(task["project"], "Motors")
        self.assertEqual(len(task["trail"]), 1)
        level, text, button = task["trail"][0]
        self.assertEqual((level, button), ("task", False))
        self.assertIn("Brakes", text)
        self.assertEqual(self.got["taskTool"]["trail"][-1], ["tool", "Changes", False])
        self.assertTrue(self.got["taskTool"]["trail"][0][2], "the task goes up")

    def test_the_task_crumb_closes_the_tool_and_keeps_the_conversation(self):
        self.assertEqual(self.got["taskTool"]["fly"], "changes")
        g = self.got["taskUp"]
        self.assertTrue(g["open"])
        self.assertIsNone(g["fly"], "the tool went back to the strip")
        self.assertEqual([t[0] for t in g["trail"]], ["task"])

    def test_the_project_crumb_goes_to_its_pos_conversation(self):
        g = self.got["projectUp"]
        self.assertFalse(g["open"], "the task gave the middle back")
        self.assertEqual(g["front"], "po-chat")
        self.assertEqual(g["trail"], [["po", "PO", False]])

    def test_a_po_tool_goes_back_to_the_po_chat(self):
        self.assertEqual(self.got["poTool"]["fly"], "board")
        self.assertEqual(self.got["poTool"]["trail"], [["po", "PO", True], ["tool", "Board", False]])
        self.assertIsNone(self.got["poUp"]["fly"])
        self.assertEqual(self.got["poUp"]["front"], "po-chat")
        self.assertEqual(self.got["poUp"]["trail"], [["po", "PO", False]])

    def test_the_pill_over_a_task_gives_the_middle_to_the_po(self):
        g = self.got["pill"]
        self.assertFalse(g["open"])
        self.assertEqual(g["front"], "po-chat")
        self.full_height(g, "PO from the pill")

    def test_a_project_without_a_po(self):
        g = self.got["plainWs"]
        self.assertEqual(g["project"], "Plain")
        self.assertEqual(g["trail"], [["tool", "Workspace", False]])
        u = self.got["plainUp"]
        self.assertEqual((u["ptab"], u["trail"]), ("tasks", []), "back to its board")

    def test_a_reload_comes_back_on_the_task_and_its_tool(self):
        g = self.got["reloaded"]
        self.assertTrue(g["open"])
        self.assertEqual(g["fly"], "spec")
        self.assertEqual(g["proj"], self.proj)

    def test_the_crumbs_are_legible_in_every_theme(self):
        for theme, rows in self.got["themes"].items():
            for sel, fg, bg in rows:
                with self.subTest(theme=theme, sel=sel):
                    need = 3.0 if sel.endswith("crumb-sep") else 4.5
                    self.assertGreaterEqual(contrast(fg, bg), need, f"{fg} on {bg}")

    def test_going_elsewhere_with_a_task_open_closes_it(self):
        g = self.got["menuAway"]
        self.assertFalse(g["open"], "the project menu")
        self.assertEqual((g["proj"], g["project"]), (self.plain, "Plain"))
        self.assertFalse(self.got["homeAway"]["open"], "the wordmark")
        self.assertIsNone(self.got["homeAway"]["proj"])

    def test_the_caret_is_the_named_projects(self):
        self.assertEqual(self.got["caretMenu"], {"head": "Motors", "on": self.proj})

    def test_a_po_opens_in_the_middle_from_anywhere(self):
        for key in ("pillHome", "pillNeeds", "openPoHome"):
            with self.subTest(key=key):
                g = self.got[key]
                self.assertFalse(g["open"])
                self.assertFalse(g["peek"], "no drawer")
                self.assertEqual((g["proj"], g["front"]), (self.proj, "po-chat"))
                self.full_height(g, key)

    def test_a_workspace_tool_crumb_goes_back_to_the_tree(self):
        for key in ("plainWs", "taskWs"):
            f = self.got[key + "File"]
            with self.subTest(key=key):
                self.assertEqual(f["trail"][-1][0], "file", "the file is a crumb")
                self.assertEqual((f["trail"][-2][0], f["trail"][-2][2]), ("tool", True), "its tool goes up")
            for mode in ("search", "recent"):
                g = self.got[key + "Up" + mode]
                with self.subTest(key=key, mode=mode):
                    self.assertTrue(g["before"]["hidden"], "the search hid the tree")
                    self.assertIn("file", g["before"]["trail"])
                    self.assertEqual((g["pane"], g["q"], g["recent"], g["treeHidden"]), ("tree", "", False, False))
                    self.assertNotIn("file", [t[0] for t in g["trail"]])
                    self.assertTrue(g["shown"], "the file is shown in the tree")
                    self.assertTrue(g["mark"].endswith("README.md"), g["mark"])

    def test_a_tool_hidden_on_a_phone_is_on_the_strip_on_a_desktop(self):
        self.assertEqual(self.got["bpPhone"], {"mid": False, "ws": False})
        self.assertTrue(self.got["bpDesk"]["ws"])
        self.assertEqual(self.got["bpDesk"]["tabs"], [], "no tabs on a desktop")
        self.assertEqual(self.got["bpDesk"]["strip"], ["points", "changes", "workspace", "board", "spec"])
        self.assertFalse(self.got["bpBack"]["ws"], "the phone keeps what it hid")

    def test_a_phone_keeps_its_layout(self):
        g = self.got["phone"]
        self.assertFalse(g["mid"])
        self.assertFalse(g["crumbsShown"])
        self.assertTrue(g["open"])


if __name__ == "__main__":
    unittest.main()
