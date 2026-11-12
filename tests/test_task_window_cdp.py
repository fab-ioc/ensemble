"""A task's own window survives a project switch (#346, GitHub issue 42).

A task can be shown outside the main panel in three ways:

* the conversation's own window: the middle (Dock's po-chat panel) popped
  out while a task is open in it (#148, pdPlaceTask);
* a task's chat panel (Open in new panel, #150) popped out into its window;
* a tab of its own (/session?room=..., window.open with noopener: search,
  Inbox and list rows the page does not list).

In headless Chrome over CDP at 1440x900, against a hub in a thread on a spare
port, with two projects with a PO each (Motors, Wheels) and a task in each:
for each way, a Motors task is put in its window, then the main window goes
to Wheels and back to... Wheels again via another route (twice in all: the
left panel's Projects row, the project switcher, and the Inbox). Checked: the
window is still open and shows the same task (its title and latest message);
a docked chat panel still keeps its per-project behaviour (set aside on the
other project's screen, back with its own); the page threw nothing.

The middle's window is followed by its CDP target: after a switch it is the
same window, now the task's own chat panel's (#346, pdTaskKeepOut), and the
conversation's panel is back in the page. Also: a task of the other project
opened meanwhile leaves the window to this one; the person closing the task
in the window (its x) keeps the window the conversation's, as before; and a
reload of the main window after a switch takes the window back. Changes and
Workspace tool actions take that same window back to the middle's conversation,
and a reload after either action leaves the task visible in it.

ENSEMBLE_TW_MEASURE=1 prints what happens instead of checking. Screenshots go
to $ENSEMBLE_SHOTS when it is set (the main window, and the middle's window);
ENSEMBLE_TW_NOFIX=1 with them takes the before pictures (the page without
pdTaskKeepOut). Skipped without Node or Chrome.
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
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
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
    switcher: async (pid) => { await click(`document.getElementById('proj-switch')`); await p.until(`!document.getElementById('proj-menu').hidden`, 5000);
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
  const MID = { target: null };
  const ROUTES = {
    // The middle's conversation in its own window, a task open in it.
    middle: {
      open: async () => {
        await p.evalIn(`openDetail(${J(A.sidA)}); 0`);
        await p.until(`pdTask() && rowBySid(SELECTED_SID).roomId === ${J(A.taskA)}`, 15000);
        await p.evalIn(`PD.dock.popOut('po-chat') && 0`);
        await p.until(`!!PD.dock.popWindow('po-chat') && PD.dock.popWindow('po-chat').document.contains(pdById('detail-panel'))`, 20000);
        await p.until(`(() => { const f = PD.dock.popWindow('po-chat').document.querySelector('iframe.dp-session'); try { return !!f && f.contentDocument.readyState === 'complete' && f.contentDocument.body.innerText.includes(${J(A.lastA)}); } catch (e) { return false; } })()`, 20000);
        // The window it is in: the one followed from here on, by its target (#346: the task keeps this one).
        MID.target = (await popTarget('ensemble-panel-po-chat')).target;
      },
      read: async () => { const ts = await targets(); const t = MID.target && ts.find(x => x.targetId === MID.target.targetId);
        const w = await readWin(t);
        if (t) w.name = await (await attach(t.targetId)).evalIn('window.name').catch(() => '');
        return { ...w, windows: ts.filter(x => /popout/.test(x.url)).length,
        dock: await p.evalIn(`PD.dock ? ({ out: PD.dock.isOut('po-chat'), openOut: PD.dock.isOpenOut('po-chat'), sel: SELECTED_SID, task: pdTask(), chatOut: PD.dock.isOpenOut('chat:' + ${J(A.taskA)}) }) : ({ none: true, sel: SELECTED_SID, pid: PD.pid || '', proj: SELECTED_PROJECT, body: document.body.className, failed: !!PD.failed })`) }; },
      close: async () => { await p.evalIn(`if (PD.rt.has('chat:' + ${J(A.taskA)})) pdChatUnpanel(${J(A.taskA)}); if (PD.dock && PD.dock.isOut('po-chat')) PD.dock.popIn('po-chat'); if (SELECTED_SID) closeDetail(); 0`); await sleep(500); MID.target = null; },
    },
    // The task's chat panel (Open in new panel), popped out.
    chat: {
      open: async () => {
        await p.evalIn(`pdChatOpen(${J(A.taskA)}) && 0`);
        await p.until(`PD.rt.has('chat:' + ${J(A.taskA)})`, 10000);
        await p.evalIn(`PD.dock.popOut('chat:' + ${J(A.taskA)}) && 0`);
        await p.until(`(() => { const w = PD.dock.popWindow('chat:' + ${J(A.taskA)}); const f = w && w.document.querySelector('iframe.dp-session'); try { return !!f && f.contentDocument.body.innerText.includes(${J(A.lastA)}); } catch (e) { return false; } })()`, 20000);
      },
      read: async () => { const { target, all } = await popTarget('ensemble-panel-chat:' + A.taskA); return { ...(await readWin(target)), windows: all,
        dock: await p.evalIn(`({ has: PD.rt.has('chat:' + ${J(A.taskA)}), out: PD.dock.isOut('chat:' + ${J(A.taskA)}), openOut: PD.dock.isOpenOut('chat:' + ${J(A.taskA)}) })`) }; },
      close: async () => { await p.evalIn(`pdChatUnpanel(${J(A.taskA)}); if (PD.dock.isOut('po-chat')) PD.dock.popIn('po-chat'); if (SELECTED_SID) closeDetail(); 0`); await sleep(500); },
    },
    // A tab of its own (window.open, noopener), as a task the page does not list opens.
    tab: {
      open: async () => {
        await p.evalIn(`swOpenRoomTab(${J(A.taskA)}); 0`);
        const t0 = Date.now(); let t = null;
        while (!t && Date.now() - t0 < 15000) { t = (await targets()).find(x => /\/session\?room=/.test(x.url)); if (!t) await sleep(200); }
        const s = await attach(t.targetId);
        await s.until(`document.readyState === 'complete' && document.body.innerText.includes(${J(A.lastA)})`, 20000);
      },
      read: async () => readWin((await targets()).find(x => /\/session\?room=/.test(x.url))),
      close: async () => { const t = (await targets()).find(x => /\/session\?room=/.test(x.url)); if (t) await c.send('Target.closeTarget', { targetId: t.targetId }); await sleep(300); },
    },
  };
  // The middle's window as the person sees it (for the report's pictures).
  const winShot = async (route, file) => { if (!A.shots || route === 'tab') return;
    const t = route === 'middle' ? MID.target : (await popTarget('ensemble-panel-chat:' + A.taskA)).target; if (!t) return;
    const s = await attach(t.targetId).catch(() => null); if (s) await s.shot(file).catch(() => null); };
  const brief = w => ({ open: w.open, title: w.title, name: w.name, url: w.url, rooms: (w.chats || []).map(x => x.room), hasTitle: JSON.stringify(w).includes(A.titleA),
    hasLast: JSON.stringify(w).includes(A.lastA), hasOther: JSON.stringify(w).includes(A.lastB), windows: w.windows, dock: w.dock, error: w.error });
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, p.sessionId);
    await load();
    // ENSEMBLE_TW_NOFIX=1: the page as it was before #346, for the report's before pictures.
    if (A.noFix) await p.evalIn('pdTaskKeepOut = () => false; 0');
    await toProject(A.projA);
    out.routes = {};
    for (const [name, R] of Object.entries(ROUTES)) {
      for (const how of Object.keys(SWITCH)) {
        const key = name + ':' + how;
        await toProject(A.projA);
        await R.open();
        const r = { before: brief(await R.read()) };
        await p.shot(`${key.replace(':', '-')}-0-open`); await winShot(name, `${key.replace(':', '-')}-0-open-window`);
        // Two switches: to Wheels, then back to Motors, both the same way.
        await SWITCH[how](A.projB); await sleep(1500);
        r.here1 = await here();
        r.after1 = brief(await R.read());
        await p.shot(`${key.replace(':', '-')}-1-wheels`); await winShot(name, `${key.replace(':', '-')}-1-wheels-window`);
        await SWITCH[how](A.projA); await sleep(1500);
        r.here2 = await here();
        r.after2 = brief(await R.read());
        await SWITCH[how](A.projB); await sleep(1500);
        r.here3 = await here();
        r.after3 = brief(await R.read());
        await p.shot(`${key.replace(':', '-')}-3-wheels`);
        out.routes[key] = r;
        await R.close();
      }
    }
    // A docked chat panel keeps its per-project behaviour: set aside on
    // Wheels' screen, back with Motors.
    await toProject(A.projA);
    await p.evalIn(`pdChatOpen(${J(A.taskA)}) && 0`);
    await p.until(`PD.rt.has('chat:' + ${J(A.taskA)}) && PD.dock.isVisible('chat:' + ${J(A.taskA)})`, 10000);
    const vis = () => p.evalIn(`({ visible: PD.dock.isVisible('chat:' + ${J(A.taskA)}), out: PD.dock.isOut('chat:' + ${J(A.taskA)}) })`);
    out.docked = { motors: await vis() };
    await SWITCH.switcher(A.projB); await sleep(1200);
    out.docked.wheels = await vis();
    await SWITCH.switcher(A.projA); await sleep(1200);
    out.docked.back = await vis();
    await p.evalIn(`openWorkspaceTab(${J(A.sidA)}); 0`);
    await p.until(`SELECTED_SID === ${J(A.sidA)} && ISSUE_TAB === 'workspace' && pdTask() && !PD.rt.has('chat:' + ${J(A.taskA)})`, 15000);
    out.docked.tool = await p.evalIn(`({ sel: SELECTED_SID, tab: ISSUE_TAB, chat: PD.rt.has('chat:' + ${J(A.taskA)}), out: PD.dock.isOut('po-chat') })`);
    await p.evalIn(`closeDetail(); 0`);
    // A task of the other project opened while this one is in the window:
    // this one keeps the window, the other opens in the middle here.
    await toProject(A.projA);
    await ROUTES.middle.open();
    await p.evalIn(`openDetail(${J(A.taskB)}); 0`);
    await p.until(`!!PD.dock && PD.pid === ${J(A.projB)} && pdTask()`, 20000).catch(() => null); await sleep(1500);
    out.other = { win: brief(await ROUTES.middle.read()), mid: await p.evalIn(`({ sel: SELECTED_SID, task: pdTask(), inPage: document.contains(pdById('detail-panel')) })`) };
    await ROUTES.middle.close();
    // The person closing the task in the window (its ×): the window stays
    // the conversation's, as before (#148).
    await toProject(A.projA);
    await ROUTES.middle.open();
    await p.evalIn(`PD.dock.popWindow('po-chat').document.querySelector('.dp-close').click(); 0`); await sleep(1000);
    out.closed = { win: brief(await ROUTES.middle.read()) };
    await ROUTES.middle.close();
    // A reload of the main window after a switch: the window and its task
    // come back to the page (Dock's adoption, by the window's marks).
    await toProject(A.projA);
    await ROUTES.middle.open();
    await SWITCH.left(A.projB); await sleep(1500);
    out.reload = { before: brief(await ROUTES.middle.read()) };
    await c.send('Page.reload', {}, p.sessionId);
    await load();
    out.reload.home = brief(await ROUTES.middle.read());
    // Back on the project it was on: the page takes the window back.
    await toProject(A.projB);
    await p.until(`!!PD.dock && PD.dock.isOpenOut('chat:' + ${J(A.taskA)})`, 20000).catch(() => null);
    await sleep(1500);
    out.reload.after = brief(await ROUTES.middle.read());
    await p.shot('middle-reload');
    await ROUTES.middle.close();
    // A task tool opened from the retained chat window must not close it.
    // The same window takes the task back into the middle's conversation,
    // while Changes or Workspace opens beside it.
    out.tools = {};
    for (const tool of ['changes', 'workspace']) {
      await toProject(A.projA);
      await ROUTES.middle.open();
      await SWITCH.left(A.projB); await sleep(1000);
      out.tools[tool] = { before: brief(await ROUTES.middle.read()) };
      if (tool === 'changes') {
        await p.evalIn(`PD.dock.popWindow('chat:' + ${J(A.taskA)}).document.querySelector('iframe.dp-session').contentWindow.cpShowDiff(1); 0`);
        await p.until(`SELECTED_SID === ${J(A.sidA)} && ISSUE_TAB === 'changes' && pdTask() && PD.dock.isOpenOut('po-chat')`, 15000);
      } else {
        await p.evalIn(`runTaskAction(${J(A.taskA)}, 'workspace')`);
        await p.until(`SELECTED_SID === ${J(A.sidA)} && ISSUE_TAB === 'workspace' && pdTask() && PD.dock.isOpenOut('po-chat')`, 15000);
      }
      await sleep(700);
      out.tools[tool].after = brief(await ROUTES.middle.read());
      out.tools[tool].state = await p.evalIn(`({ sel: SELECTED_SID, tab: ISSUE_TAB, turn: TCH_VIEWS.get(${J(A.sidA)})?.turn ?? TCH_TURN_WANT.get(${J(A.sidA)}) ?? null, poOut: PD.dock.isOpenOut('po-chat'), chat: PD.rt.has('chat:' + ${J(A.taskA)}), toolVisible: PD.dock.isVisible(${J(tool)}) })`);
      await c.send('Page.reload', {}, p.sessionId);
      await load();
      await toProject(A.projA);
      await sleep(900);
      out.tools[tool].reload = brief(await ROUTES.middle.read());
      await ROUTES.middle.close();
    }
    // Two tasks with windows of their own: opening A's tool must leave B's
    // window attached to B when B currently occupies the conversation.
    await toProject(A.projA);
    await ROUTES.chat.open();
    const aWin = (await popTarget('ensemble-panel-chat:' + A.taskA)).target;
    await p.evalIn(`openDetail(${J(A.taskB)}); 0`);
    await p.until(`SELECTED_SID === ${J(A.taskB)} && pdTask()`, 15000);
    await p.evalIn(`PD.dock.popOut('po-chat'); 0`);
    await p.until(`!!PD.dock.popWindow('po-chat')`, 15000);
    const bWin = (await popTarget('ensemble-panel-po-chat')).target;
    await p.evalIn(`openWorkspaceTab(${J(A.sidA)}); 0`);
    await p.until(`SELECTED_SID === ${J(A.sidA)} && ISSUE_TAB === 'workspace' && PD.dock.isOpenOut('po-chat') && PD.dock.isOpenOut('chat:' + ${J(A.taskB)})`, 15000);
    await p.until(`(() => { const w = PD.dock.popWindow('chat:' + ${J(A.taskB)}); const f = w && w.document.querySelector('iframe.dp-session'); try { return !!f && f.contentDocument.body.innerText.includes(${J(A.lastB)}); } catch (e) { return false; } })()`, 20000);
    const ts = await targets();
    out.twoWindows = { a: brief(await readWin(ts.find(x => x.targetId === aWin.targetId))),
      b: brief(await readWin(ts.find(x => x.targetId === bWin.targetId))), count: ts.filter(x => /popout/.test(x.url)).length };
    await p.evalIn(`closeDetail(); PD.dock.popIn('po-chat'); pdChatUnpanel(${J(A.taskB)}); if (PD.dock.isOut('po-chat')) PD.dock.popIn('po-chat'); 0`);
    // A separate PO window also stays open when a task chat asks for a tool.
    await toProject(A.projA);
    await ROUTES.chat.open();
    const taskWin = (await popTarget('ensemble-panel-chat:' + A.taskA)).target;
    await p.evalIn(`PD.dock.popOut('po-chat'); 0`);
    await p.until(`!!PD.dock.popWindow('po-chat')`, 15000);
    const poWin = (await popTarget('ensemble-panel-po-chat')).target;
    await p.evalIn(`openWorkspaceTab(${J(A.sidA)}); 0`);
    await p.until(`SELECTED_SID === ${J(A.sidA)} && ISSUE_TAB === 'workspace' && PD.dock.isOpenOut('po-chat')`, 15000);
    await (await attach(poWin.targetId)).until(`location.pathname === '/session' && location.search.includes(${J(A.poOf[A.projA])})`, 15000);
    const ts2 = await targets();
    out.poWindow = { task: brief(await readWin(ts2.find(x => x.targetId === taskWin.targetId))),
      po: brief(await readWin(ts2.find(x => x.targetId === poWin.targetId))) };
    await p.evalIn(`closeDetail(); PD.dock.popIn('po-chat'); 0`);
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
class TaskWindowSurvivesProjectSwitch(unittest.TestCase):
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
                "titleA": "Gearbox fix", "lastA": "Latest gearbox note 7731", "lastB": "Latest tyre note 4410", "shots": shots,
                "noFix": os.environ.get("ENSEMBLE_TW_NOFIX") == "1"}
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

    def test_each_switch_goes_where_asked(self):
        for key, r in self.got["routes"].items():
            with self.subTest(key):
                self.assertEqual([r["here1"], r["here2"], r["here3"]], [self.projB, self.projA, self.projB])

    def test_the_window_stays_open_showing_the_same_task(self):
        for key, r in self.got["routes"].items():
            for step in ("before", "after1", "after2", "after3"):
                with self.subTest(key, step=step):
                    w = r[step]
                    self.assertTrue(w["open"], w)
                    self.assertTrue(w["hasLast"], w)
                    self.assertFalse(w["hasOther"], w)
                    self.assertTrue(w["hasTitle"], w)
                    self.assertTrue(self.taskA in w["rooms"] or self.taskA in (w.get("url") or ""), w)
                    if step != "before" and key.startswith(("middle", "chat")):
                        self.assertEqual(w["windows"], 1, w)   # the same window, no second one

    def test_the_middle_window_becomes_the_tasks_own(self):
        # Followed by its CDP target: the same window, now the task's chat
        # panel's (its name), and the conversation's panel back in the page.
        for key, r in self.got["routes"].items():
            if not key.startswith("middle"):
                continue
            for step in ("after1", "after2", "after3"):
                with self.subTest(key, step=step):
                    w = r[step]
                    self.assertEqual(w["name"], "ensemble-panel-chat:" + self.taskA, w)
                    self.assertEqual(w["dock"], {"out": False, "openOut": False, "sel": None, "task": False, "chatOut": True}, w)

    def test_a_task_of_another_project_leaves_the_window_to_this_one(self):
        o = self.got["other"]
        self.assertTrue(o["win"]["open"] and o["win"]["hasLast"] and not o["win"]["hasOther"], o)
        self.assertEqual(o["win"]["name"], "ensemble-panel-chat:" + self.taskA, o)
        self.assertEqual(o["mid"], {"sel": self.taskB, "task": True, "inPage": True}, o)

    def test_closing_the_task_in_the_window_keeps_it_the_conversations(self):
        w = self.got["closed"]["win"]
        self.assertTrue(w["open"], w)
        self.assertEqual(w["name"], "ensemble-panel-po-chat", w)
        self.assertFalse(w["dock"]["chatOut"], w)
        self.assertIsNone(w["dock"]["sel"], w)

    def test_the_window_survives_a_reload_of_the_main_window(self):
        for step in ("before", "home", "after"):
            with self.subTest(step):
                w = self.got["reload"][step]
                self.assertTrue(w["open"] and w["hasLast"] and w["hasTitle"], w)
                self.assertEqual(w["windows"], 1, w)
                if step != "home":   # no project's page: nothing to take it back yet; it waits
                    self.assertTrue(w["dock"]["chatOut"], w)

    def test_a_docked_chat_panel_keeps_its_per_project_place(self):
        d = self.got["docked"]
        self.assertEqual(d["motors"], {"visible": True, "out": False})
        self.assertEqual(d["wheels"], {"visible": False, "out": False})
        self.assertEqual(d["back"], {"visible": True, "out": False})
        self.assertEqual(d["tool"], {"sel": self.taskA, "tab": "workspace", "chat": False, "out": False})

    def test_task_tools_keep_the_window_and_open_beside_it(self):
        for tool, r in self.got["tools"].items():
            with self.subTest(tool):
                self.assertEqual(r["state"]["sel"], self.taskA, r)
                self.assertEqual(r["state"]["tab"], tool, r)
                self.assertTrue(r["state"]["poOut"] and r["state"]["toolVisible"], r)
                self.assertFalse(r["state"]["chat"], r)
                if tool == "changes":
                    self.assertEqual(r["state"]["turn"], 1, r)
                for step in ("before", "after", "reload"):
                    w = r[step]
                    self.assertTrue(w["open"] and w["hasTitle"] and w["hasLast"], w)
                    self.assertEqual(w["windows"], 1, w)
                self.assertEqual(r["after"]["name"], "ensemble-panel-po-chat", r)

    def test_another_tasks_window_also_stays_open(self):
        r = self.got["twoWindows"]
        self.assertEqual(r["count"], 2, r)
        self.assertTrue(r["a"]["open"] and r["a"]["hasLast"] and not r["a"]["hasOther"], r)
        self.assertTrue(r["b"]["open"] and r["b"]["hasOther"] and not r["b"]["hasLast"], r)

    def test_a_separate_po_window_stays_open(self):
        r = self.got["poWindow"]
        self.assertTrue(r["task"]["open"] and r["task"]["hasLast"], r)
        self.assertTrue(r["po"]["open"] and self.poA in r["po"]["url"], r)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
