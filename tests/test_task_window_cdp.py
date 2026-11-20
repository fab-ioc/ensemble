"""A task's window goes away with its project and comes back with it (#386 rule 6).

This replaces #346's rule (a task's window survived a project switch): each
project keeps its own workspace, and its panels' windows go away with it and
come back with it unless pinned (P374, P376).

In headless Chrome over CDP at 1440x900, against a hub in a thread on a spare
port, with two projects with a PO each (Motors, Wheels) and a task in each.
A task of a project with a PO is a tab of its own (chat:<room>); popped out,
it is the task's window. Checked:

* by each route (the left panel's Projects row, the project switcher, the
  Inbox): on Wheels' screen the window is closed and kept in Motors' store as
  a window; back on Motors it opens again (a new window) with the task open;
* a pinned window stays open, the same window, on both screens;
* a tab of its own (/session?room=..., noopener) is the browser's: it stays;
* a docked task tab: put away on Wheels' screen, back (task open) on Motors';
* a task of the other project opened meanwhile: the page goes to Wheels, the
  Motors window goes away with Motors and Wheels' task opens in its own tab;
* the window's x closes the task; the window stays with the task's chat
  (like the PO tab or Esc: the task closes, its tab stays);
* a reload on Wheels' screen remembers the put-away window; back on Motors it
  opens again;
* Changes and Workspace asked for from the window: the window stays the
  task's, the tool opens beside it in the page;
* the page threw nothing.

ENSEMBLE_TW_MEASURE=1 prints what happens instead of checking. Screenshots go
to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
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
MEASURE = os.environ.get("ENSEMBLE_TW_MEASURE") == "1"

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(((m.params.exceptionDetails || {}).exception || {}).description || JSON.stringify(m.params.exceptionDetails).slice(0, 300));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const attach = async (targetId) => {
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); let last = null; while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) { last = e; } if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr + (last ? ' :: ' + last.message : '')); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    return { sessionId, evalIn, until, shot, targetId };
  };
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const p = await attach(targetId);
  await c.send('Page.enable', {}, p.sessionId);
  await c.send('Runtime.enable', {}, p.sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, p.sessionId);
  await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, p.sessionId);
  const J = JSON.stringify;
  const load = async () => {
    await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + J(A.taskA) + ')', 30000);
    await p.until('window.ensBooted === true', 30000);
  };
  const toProject = async (pid) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${J(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock && PD.pid === ' + J(pid), 30000);
    await sleep(500);
  };
  const click = async (expr) => {
    const xy = await p.evalIn(`(() => { const e = ${expr}; if (!e) return null; e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    if (!xy) throw new Error('nothing to click: ' + expr + ' :: ' + await p.evalIn(`[...document.querySelectorAll('#list-dock .dk-strip-btn, #list-dock .dk-tab, #list-dock [data-dk-auto]')].map(b => b.className + '|' + JSON.stringify(b.dataset)).join(' ; ') + ' body:' + document.body.className`));
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: xy[0], y: xy[1], button: 'left', clickCount: 1 }, p.sessionId);
  };
  const LBTN = id => `document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="${id}"]')`;
  const SHOWN = id => `(() => { const e = document.getElementById('${id}'); const r = e && e.getBoundingClientRect(); return !!r && r.width > 0 && getComputedStyle(e).visibility !== 'hidden'; })()`;
  // The left strip's button, else its tab (both pinned, as one stack).
  const front = async (id) => { if (!(await p.evalIn(SHOWN('ld-' + id)))) {
    const b = await p.evalIn(`!!${LBTN(id)}`);
    await click(b ? LBTN(id) : `document.querySelector('#list-dock .dk-tab[data-dk-tab="${id}"]')`);
    await p.until(SHOWN('ld-' + id), 5000).catch(() => null); await sleep(300); } };
  // The three ways the main window goes to another project, each by clicks.
  const SWITCH = {
    left: async (pid) => { await front('inbox'); await click(`document.querySelector('#ld-projs .ldp-row[data-ldp-proj="${pid}"]')`); },
    switcher: async (pid) => { await click(`document.querySelector('#proj-switch .caret')`); await p.until(`!document.getElementById('proj-menu').hidden`, 5000);
      await click(`[...document.querySelectorAll('#proj-menu .pm-item[data-proj="${pid}"]')].find(b => !b.classList.contains('pm-set') && b.getBoundingClientRect().width > 0)`); },
    inbox: async (pid) => { await front('inbox'); await click(`document.querySelector('#ld-projs .ldp-row[data-ldp="all"]')`); await sleep(400);
      const L = `LD_INBOX.querySelector('.ibx-row a.ibx-link[data-room="${A.poOf[pid]}"]')`;
      await p.until('!!' + L, 15000); await click(L); },
  };
  const here = () => p.evalIn('PD.pid || SELECTED_PROJECT || ""');
  // What a window shows: its title and every task conversation in it (the
  // room and the text of its latest message), read in its own document.
  const SHOWS = `(() => { const d = document; const fr = [...d.querySelectorAll('iframe')].filter(f => { const r = f.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !f.hidden; });
    const chats = fr.map(f => { let room = f.dataset.room || '', last = '', title = ''; try { const u = new URL(f.contentWindow.location.href); room = room || u.searchParams.get('id') || u.searchParams.get('room') || '';
      const b = f.contentDocument.body; const t = b ? b.innerText : ''; last = t.slice(-400); title = f.contentDocument.title; } catch (e) { last = 'denied'; } return { room, last, title }; });
    const body = d.body ? d.body.innerText : '';
    return { title: d.title, chats, text: body.slice(0, 300), last: body.slice(-400) }; })()`;
  const targets = async () => (await c.send('Target.getTargets')).targetInfos.filter(t => t.type === 'page');
  const readWin = async (t) => { if (!t) return { open: false }; const s = await attach(t.targetId); try { return { open: true, url: t.url, ...(await s.evalIn(SHOWS)) }; } catch (e) { return { open: true, url: t.url, error: e.message.slice(0, 200) }; } };
  // The window of a Dock panel, by its name (popName + id).
  const popTarget = async (name) => {
    const id = await p.evalIn(`(() => { for (const d of [PD.dock, LD.dock]) for (const x of d.panels()) { const w = d.popWindow(x.id); if (w && !w.closed && w.name === ${J(name)}) return true; } return false; })()`);
    const ts = (await targets()).filter(t => /popout/.test(t.url));
    if (!id) return { target: null, all: ts.length };
    // The one whose window.name matches.
    for (const t of ts) { const s = await attach(t.targetId); const n = await s.evalIn('window.name').catch(() => ''); if (n === name) return { target: t, all: ts.length }; }
    return { target: null, all: ts.length };
  };
  const TID = 'chat:' + A.taskA;
  // The task's own window: its tab (#386: a task of a project with a PO is a
  // tab of its own) popped out, the task open in it.
  const openWin = async () => {
    await p.evalIn(`openDetail(${J(A.sidA)}); 0`);
    await p.until(`pdTask() && rowBySid(SELECTED_SID).roomId === ${J(A.taskA)} && PD.rt.has(${J(TID)})`, 15000);
    await p.evalIn(`PD.dock.popOut(${J(TID)}) && 0`);
    await p.until(`(() => { const w = PD.dock.popWindow(${J(TID)}); const f = w && w.document.querySelector('iframe.dp-session'); try { return !!f && f.contentDocument.readyState === 'complete' && f.contentDocument.body.innerText.includes(${J(A.lastA)}); } catch (e) { return false; } })()`, 20000);
    return (await popTarget('ensemble-panel-' + TID)).target;
  };
  const state = () => p.evalIn(`({ pid: PD.pid || '', sel: SELECTED_SID, has: PD.rt.has(${J(TID)}), out: PD.dock.isOut(${J(TID)}), openOut: PD.dock.isOpenOut(${J(TID)}),
    away: JSON.stringify(PdWorkspace.putAway(PD_WS, ${J(A.projA)})), pinned: PdWorkspace.isPinned(PD_WS, ${J(TID)}) })`);
  const win = async () => { const { target, all } = await popTarget('ensemble-panel-' + TID); return { ...brief(await readWin(target)), windows: all, targetId: target ? target.targetId : null }; };
  const reset = async () => { await p.evalIn(`pdWsPin(${J(TID)}, false); if (SELECTED_SID) closeDetail(); for (const id of [${J(TID)}, 'chat:' + ${J(A.taskB)}]) { if (PD.rt.has(id)) PD.dock.removePanel(id); PdWorkspace.drop(PD_WS, id); } pdWsSave(true); 0`); await sleep(500); };
  const brief = w => ({ open: w.open, title: w.title, rooms: (w.chats || []).map(x => x.room), hasTitle: JSON.stringify(w).includes(A.titleA),
    hasLast: JSON.stringify(w).includes(A.lastA), hasOther: JSON.stringify(w).includes(A.lastB), error: w.error });
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, p.sessionId);
    await load();
    await toProject(A.projA);
    // Rule 6: the window goes away with its project and comes back with it,
    // by each of the three routes (all clicks: the click opens it again).
    out.routes = {};
    for (const how of Object.keys(SWITCH)) {
      await toProject(A.projA);
      const t0 = await openWin();
      const r = { before: { ...(await win()), ...(await state()) } };
      await SWITCH[how](A.projB); await sleep(1500);
      r.here1 = await here();
      r.away = { ...(await win()), ...(await state()) };
      await p.shot(`${how}-1-wheels`);
      await SWITCH[how](A.projA); await sleep(2500);
      r.here2 = await here();
      r.back = { ...(await win()), ...(await state()), same: false };
      r.back.same = !!t0 && r.back.targetId === t0.targetId;
      await p.shot(`${how}-2-motors`);
      out.routes[how] = r;
      await reset();
    }
    // A pinned window stays open, the same one, on every project's screen.
    await toProject(A.projA);
    {
      const t0 = await openWin();
      await p.evalIn(`pdWsPin(${J(TID)}, true); 0`);
      await SWITCH.switcher(A.projB); await sleep(1500);
      out.pinned = { wheels: { ...(await win()), ...(await state()) } };
      out.pinned.wheels.same = !!t0 && out.pinned.wheels.targetId === t0.targetId;
      await SWITCH.left(A.projA); await sleep(1500);
      out.pinned.motors = { ...(await win()), ...(await state()) };
      out.pinned.motors.same = !!t0 && out.pinned.motors.targetId === t0.targetId;
      await reset();
    }
    // A tab of its own (window.open, noopener) is the browser's, not the page's: it stays.
    await toProject(A.projA);
    {
      await p.evalIn(`swOpenRoomTab(${J(A.taskA)}); 0`);
      const t0 = Date.now(); let t = null;
      while (!t && Date.now() - t0 < 15000) { t = (await targets()).find(x => /\/session\?room=/.test(x.url)); if (!t) await sleep(200); }
      const s = await attach(t.targetId);
      await s.until(`document.readyState === 'complete' && document.body.innerText.includes(${J(A.lastA)})`, 20000);
      await SWITCH.switcher(A.projB); await sleep(1500);
      out.ownTab = brief(await readWin((await targets()).find(x => /\/session\?room=/.test(x.url))));
      await c.send('Target.closeTarget', { targetId: t.targetId }); await sleep(300);
    }
    // A docked task tab: put away on Wheels' screen, back with Motors.
    await toProject(A.projA);
    await p.evalIn(`openDetail(${J(A.sidA)}); 0`);
    await p.until(`PD.rt.has(${J(TID)}) && PD.dock.isVisible(${J(TID)})`, 10000);
    out.docked = { motors: await state() };
    await SWITCH.switcher(A.projB); await sleep(1200);
    out.docked.wheels = await state();
    await SWITCH.switcher(A.projA); await sleep(1500);
    out.docked.back = await state();
    await reset();
    // A task of the other project opened while this one is in its window:
    // the page goes to Wheels, the window goes away with Motors.
    await toProject(A.projA);
    await openWin();
    await p.evalIn(`openDetail(${J(A.taskB)}); 0`);
    await p.until(`!!PD.dock && PD.pid === ${J(A.projB)} && pdTask()`, 20000).catch(() => null); await sleep(1500);
    out.other = { win: await win(), st: await state(), b: await p.evalIn(`({ sel: SELECTED_SID, tab: PD.rt.has('chat:' + ${J(A.taskB)}), front: PD.dock.frontOf('po-chat') })`) };
    await toProject(A.projA); await sleep(800);
    await reset();
    // The task's actions from its window's ⋯ (review 2: was test_task_dock_panel's):
    // the menu opens in the window, Rename… there runs the page's handler, and
    // Terminal colours… (when the hub offers it) opens its picker in the window, under the ⋯.
    await toProject(A.projA);
    await openWin();
    const WD = `PD.dock.popWindow(${J(TID)}).document`;
    await p.evalIn('window.__prompted = null; window.prompt = (m, c) => { window.__prompted = [m, c]; return null; }; 0');
    out.winMenu = await p.evalIn(`(async () => {
      const w = PD.dock.popWindow(${J(TID)}), d = w.document;
      const btn = [...d.querySelectorAll('[data-dk-act="menu"]')].find(e => e.getBoundingClientRect().width > 0);
      if (!btn) return { btn: false };
      btn.click(); await new Promise(r => setTimeout(r, 300));
      const m = d.querySelector('.dk-menu.dk-options');
      const rows = m ? [...m.querySelectorAll('[data-dk-app]')].map(b => b.textContent.replace('▸', '').trim()) : [];
      const r = m && m.getBoundingClientRect();
      return { btn: true, open: !!m, inPageToo: !!document.querySelector('.dk-menu.dk-options'), rows,
               fits: !!r && r.left >= 0 && r.top >= 0 && r.right <= w.innerWidth + 1 && r.bottom <= w.innerHeight + 1 }; })()`);
    out.winPick = { found: await p.evalIn(`(() => { const b = [...${WD}.querySelectorAll('.dk-menu.dk-options [data-dk-app]')].find(e => e.textContent.trim().startsWith('Rename')); if (!b) return false; b.click(); return true; })()`) };
    await sleep(400);
    Object.assign(out.winPick, await p.evalIn(`({ prompted: window.__prompted, menuOpen: !!${WD}.querySelector('.dk-menu.dk-options') })`));
    out.winColours = await p.evalIn(`(async () => {
      const d = ${WD};
      const mb = [...d.querySelectorAll('[data-dk-act="menu"]')].find(e => e.getBoundingClientRect().width > 0);
      mb.click(); await new Promise(r => setTimeout(r, 300));
      const b = [...d.querySelectorAll('.dk-menu.dk-options [data-dk-app]')].find(x => x.textContent.startsWith('Terminal colours'));
      if (!b) { mb.click(); return { offered: false }; }
      if (b.getAttribute('aria-disabled') === 'true') { mb.click(); return { offered: true, disabled: true }; }
      b.click(); await new Promise(r => setTimeout(r, 400));
      const m = d.querySelector('.theme-dd-menu');
      const res = { offered: true, disabled: false, inWindow: !!m, inPage: !!document.querySelector('.theme-dd-menu'), focusIn: !!m && m.contains(d.activeElement) };
      if (m) { const a = m.getBoundingClientRect(), t = mb.getBoundingClientRect(); res.under = a.top >= t.bottom - 1 && a.right <= d.defaultView.innerWidth + 1 && a.left >= 0; }
      closeThemeMenu();
      return res; })()`);
    await reset();
    // The person closing the task in the window (its ×): the task and its window close.
    await toProject(A.projA);
    await openWin();
    await p.evalIn(`PD.dock.popWindow(${J(TID)}).document.querySelector('.dp-close').click(); 0`); await sleep(1000);
    out.closed = { win: await win(), st: await state() };
    await reset();
    // A reload of the main window on Wheels' screen: the put-away window is
    // remembered; back on Motors it comes back.
    await toProject(A.projA);
    await openWin();
    await SWITCH.left(A.projB); await sleep(1500);
    out.reload = { before: { ...(await win()), ...(await state()) } };
    await c.send('Page.reload', {}, p.sessionId);
    await load();
    await SWITCH.left(A.projA); await sleep(2500);
    out.reload.after = { ...(await win()), ...(await state()) };
    await reset();
    // A task tool asked for from the task's window: the window stays the task's,
    // the tool opens beside it in the page.
    out.tools = {};
    for (const tool of ['changes', 'workspace']) {
      await toProject(A.projA);
      await openWin();
      if (tool === 'changes')
        await p.evalIn(`PD.dock.popWindow(${J(TID)}).document.querySelector('iframe.dp-session').contentWindow.cpShowDiff(1); 0`);
      else await p.evalIn(`runTaskAction(${J(A.taskA)}, 'workspace')`);
      await p.until(`SELECTED_SID === ${J(A.sidA)} && ISSUE_TAB === ${J(tool)}`, 15000).catch(() => null);
      await sleep(900);
      out.tools[tool] = { win: await win(), st: await state(),
        ui: await p.evalIn(`({ sel: SELECTED_SID, tab: ISSUE_TAB, turn: TCH_VIEWS.get(${J(A.sidA)})?.turn ?? TCH_TURN_WANT.get(${J(A.sidA)}) ?? null, toolVisible: PD.dock.isVisible(${J(tool)}) })`) };
      await reset();
    }
  } finally {
    out.errors = c.errors;
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TaskWindowFollowsItsProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-tw-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]

        def project(name, task_title, last):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", members(home))
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            # An open question to the person in each PO chat: an Inbox row that
            # stays while the switches go through it (an answer is seen once its chat opens).
            chatroom.post_message(po["id"], "claude", f"For {name}.\n\n1. **Ask:** Wider columns?\n   - Yes\n   - No\n", to="user")
            task = chatroom.create_room(task_title, members(home))
            dashboard.assign_session_project(task["id"], proj["id"])
            for i in range(4):
                chatroom.post_message(task["id"], "codex", f"step {i} of {task_title}", to="claude")
            chatroom.post_message(task["id"], "claude", last, to="codex")
            return proj["id"], po["id"], task["id"]

        cls.projA, cls.poA, cls.taskA = project("Motors", "Gearbox fix", "Latest gearbox note 7731")
        cls.projB, cls.poB, cls.taskB = project("Wheels", "Tyre swap", "Latest tyre note 4410")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "projA": cls.projA, "projB": cls.projB, "taskA": cls.taskA, "sidA": cls.taskA, "taskB": cls.taskB,
                "poOf": {cls.projA: cls.poA, cls.projB: cls.poB},
                "titleA": "Gearbox fix", "lastA": "Latest gearbox note 7731", "lastB": "Latest tyre note 4410", "shots": shots}
        script = base / "task_window_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if MEASURE:
            print(json.dumps(cls.got, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        if MEASURE:
            self.skipTest("measuring")

    def away_one(self, st):
        away = json.loads(st["away"])
        self.assertEqual(len(away), 1, st)
        self.assertEqual(away[0]["id"], "chat:" + self.taskA, st)
        return away[0]

    def test_each_switch_goes_where_asked(self):
        for key, r in self.got["routes"].items():
            with self.subTest(key):
                self.assertEqual([r["here1"], r["here2"]], [self.projB, self.projA])

    def test_the_window_goes_away_with_its_project_and_comes_back(self):
        for key, r in self.got["routes"].items():
            with self.subTest(key, step="before"):
                w = r["before"]
                self.assertTrue(w["open"] and w["hasTitle"] and w["hasLast"] and not w["hasOther"], w)
                self.assertEqual(w["windows"], 1, w)
                self.assertEqual(w["sel"], self.taskA, w)
                self.assertTrue(w["has"] and w["out"] and w["openOut"], w)
                self.assertEqual(w["away"], "[]", w)
                self.assertFalse(w["pinned"], w)
            with self.subTest(key, step="away"):
                w = r["away"]
                self.assertFalse(w["open"], w)
                self.assertEqual(w["windows"], 0, w)
                self.assertIsNone(w["sel"], w)
                self.assertFalse(w["has"], w)
                self.assertTrue(self.away_one(w).get("out"), w)   # kept as a window
            with self.subTest(key, step="back"):
                w = r["back"]
                self.assertTrue(w["open"] and w["hasTitle"] and w["hasLast"], w)
                self.assertEqual(w["windows"], 1, w)
                self.assertEqual(w["sel"], self.taskA, w)
                self.assertTrue(w["openOut"], w)
                self.assertEqual(w["away"], "[]", w)
                self.assertFalse(w["same"], w)   # a new window, opened by the click

    def test_a_pinned_window_stays_open_on_every_screen(self):
        for step in ("wheels", "motors"):
            with self.subTest(step):
                w = self.got["pinned"][step]
                self.assertTrue(w["open"] and w["hasLast"], w)
                self.assertEqual(w["windows"], 1, w)
                self.assertTrue(w["has"] and w["openOut"] and w["pinned"], w)
                self.assertTrue(w["same"], w)
                self.assertIsNone(w["sel"], w)

    def test_a_tab_of_its_own_stays(self):
        w = self.got["ownTab"]
        self.assertTrue(w["open"] and w["hasTitle"] and w["hasLast"] and not w["hasOther"], w)

    def test_a_docked_task_tab_is_put_away_and_comes_back(self):
        d = self.got["docked"]
        self.assertEqual(d["motors"]["sel"], self.taskA, d)
        self.assertTrue(d["motors"]["has"] and not d["motors"]["out"], d)
        self.assertIsNone(d["wheels"]["sel"], d)
        self.assertFalse(d["wheels"]["has"], d)
        self.assertFalse(self.away_one(d["wheels"]).get("out"), d)
        self.assertEqual(d["back"]["sel"], self.taskA, d)
        self.assertTrue(d["back"]["has"] and not d["back"]["out"], d)

    def test_a_task_of_another_project_takes_the_page_there(self):
        o = self.got["other"]
        self.assertFalse(o["win"]["open"], o)
        self.assertEqual(o["win"]["windows"], 0, o)
        self.assertEqual(o["st"]["pid"], self.projB, o)
        self.assertEqual(o["st"]["sel"], self.taskB, o)
        self.assertFalse(o["st"]["has"], o)
        self.away_one(o["st"])
        self.assertTrue(o["b"]["tab"], o)
        self.assertEqual(o["b"]["front"], "chat:" + self.taskB, o)

    def test_the_tasks_actions_run_from_its_windows_menu(self):
        m = self.got["winMenu"]
        self.assertTrue(m["btn"] and m["open"], f"the window's ⋯ opens its menu in the window: {m}")
        self.assertFalse(m["inPageToo"], m)
        self.assertTrue(m["fits"], f"the menu fits the window: {m}")
        for label in ("Rename…", "Agents and models…", "Delete task…"):
            self.assertIn(label, m["rows"])
        k = self.got["winPick"]
        self.assertTrue(k["found"], k)
        self.assertTrue(k["prompted"] and k["prompted"][0].startswith("Label for this session"), f"Rename… ran the page's handler: {k}")
        self.assertFalse(k["menuOpen"], k)
        c = self.got["winColours"]
        if c.get("offered") and not c.get("disabled"):
            self.assertTrue(c["inWindow"] and not c["inPage"] and c["under"] and c["focusIn"], f"the colour picker opens in the window, under its ⋯: {c}")

    def test_closing_the_task_in_the_window_keeps_its_tab(self):
        c = self.got["closed"]
        self.assertTrue(c["win"]["open"] and c["win"]["hasLast"], c)
        self.assertIsNone(c["st"]["sel"], c)
        self.assertTrue(c["st"]["has"] and c["st"]["openOut"], c)

    def test_a_reload_remembers_the_put_away_window(self):
        r = self.got["reload"]
        self.assertFalse(r["before"]["open"], r)
        self.away_one(r["before"])
        a = r["after"]
        self.assertTrue(a["open"] and a["hasLast"], r)
        self.assertEqual(a["windows"], 1, r)
        self.assertEqual(a["sel"], self.taskA, r)
        self.assertTrue(a["openOut"], r)

    def test_task_tools_keep_the_window_and_open_beside_it(self):
        for tool, r in self.got["tools"].items():
            with self.subTest(tool):
                w = r["win"]
                self.assertTrue(w["open"] and w["hasTitle"] and w["hasLast"], r)
                self.assertEqual(w["windows"], 1, r)
                self.assertTrue(r["st"]["out"] and r["st"]["openOut"], r)
                self.assertEqual(r["ui"]["sel"], self.taskA, r)
                self.assertEqual(r["ui"]["tab"], tool, r)
                self.assertTrue(r["ui"]["toolVisible"], r)
                if tool == "changes":
                    self.assertEqual(r["ui"]["turn"], 1, r)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
