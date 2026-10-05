"""Layout A step 3 (#125): the tool strip on the right (Dock v0.5.1).

In headless Chrome over CDP, against a hub in a thread serving the pages, with
a project that has a PO (Motors), a task in it with a folder of its own, and a
project without a PO (Plain):

* a desktop's dock is the conversation in the middle and a 44px strip at the
  right edge: Your asks, Changes, Files, Board, Spec, each an icon whose name
  is its tooltip and aria-label;
* counts on the buttons: Your asks' open asks, Changes' uncommitted files for
  the PO and the branch's "+N" for a task;
* a click opens a tool beside the conversation (which narrows; nothing is
  covered), at its own width; one at a time; a second click closes it;
* the open tool is remembered per browser: a reload opens it again;
* a task opened on a desktop is the middle's conversation with the same
  strip, its own Changes, Files and Spec (with Details) in the tools, and its
  tabs gone; a PO's Spec says why it has none;
* a file open in Files keeps its page when the tool closes and opens again;
* the Board's ⤢ takes the whole width and gives it back;
* a tool opened by a click slides back on a click in the conversation (the
  PO's or an open task's, after a click on the tool's own text or in its file
  view too) or in the task list; typing in it, its ⋯ menu, Go to file, its
  file view and Your asks' arrow keep it out; pinned, it stays (Dock v0.5.1);
* at 1280, 1440 and 1728, nothing wider than the screen; in every theme the
  strip's icons and counts clear 4.5:1;
* a phone keeps its tabs: no strip.

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
import points  # noqa: E402
from tests.test_middle import contrast  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
TOOLS = ["points", "changes", "workspace", "board", "spec"]


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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown' || (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')) this.errors.push(m.params); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// The strip, the tool open beside the middle, and the middle.
const STRIP = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom) }; };
  const strip = document.querySelector('#po-dock .dk-strip-right');
  const fly = document.querySelector('#po-dock .dk-flyout.open');
  const btns = [...document.querySelectorAll('#po-dock .dk-strip-right .dk-strip-btn')].map(b => ({ id: b.dataset.dkAuto, label: b.getAttribute('aria-label'),
    title: b.title, svg: !!b.querySelector('svg'), badge: (b.querySelector('.dk-strip-badge') || {}).textContent || '', on: b.classList.contains('on'),
    expanded: b.getAttribute('aria-expanded'), box: box(b) }));
  const dp = document.getElementById('detail-panel');
  return { vw: innerWidth, vh: innerHeight, strip: box(strip), btns, fly: PD.dock.flyOpen(), flyBox: box(fly), mid: box(PD.els['po-chat']),
    open: document.body.classList.contains('detail-open'), dpIn: dp.parentNode === PD.els['po-chat'], docked: document.body.classList.contains('dp-docked'),
    tabsShown: !!dp.querySelector('.dp-tabs') && getComputedStyle(dp.querySelector('.dp-tabs')).display !== 'none',
    scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight, saved: localStorage.getItem('cd-tool-open'),
    trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].map(e => [e.dataset.crumb, e.textContent]),
    panels: !!document.querySelector('.pd-panels') };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr, userGesture = false) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, userGesture }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    // A real click, as a person's.
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const ready = () => until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    // A desktop opens on the last conversation or the first Needs you entry
    // (#135): each of these pages starts on none, as its checks expect.
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await ready();
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, shot, click, ready, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = (p, proj, keep) => p.evalIn(`(() => { if (!${!!keep}) try { ['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const poReady = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#po-dock .dk-strip-btn") && !!document.querySelector("#po-panel iframe.po-session:not([hidden])")', 30000);
  const sid = `ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.task)}).sessionId`;
  const openTask = async (p) => {
    await p.evalIn(`openDetail(${sid}); 0`);
    await p.until('!!document.querySelector("#detail-panel iframe.dp-session") && document.body.classList.contains("dp-docked")');
    await sleep(400);
  };
  const tool = id => `#po-dock .dk-strip-btn[data-dk-auto="${id}"]`;
  // Dock v0.5.0's title bar: ⋯, then a submenu (View Mode: 'mode', Move To: 'side'), then an item, with real clicks.
  const menuPick = async (p, head, sub, item) => {
    await p.click(`${head} [data-dk-act="menu"]`);
    await p.click(`.dk-menu.dk-options [data-dk-sub="${sub}"]`);
    await p.click(`.dk-menu.dk-submenu [data-dk-menu="${item}"]`);
  };
  try {
    for (const [w, h] of [[1280, 800], [1440, 900], [1728, 1117]]) {
      const p = await page(w, h);
      await go(p, A.proj); await poReady(p); await sleep(500);
      out['po' + w] = await p.evalIn(STRIP);
      // Each tool in turn: opened by a click, beside the conversation.
      out['open' + w] = {};
      for (const id of ${TOOLS}) {
        await p.click(tool(id)); await sleep(350);
        out['open' + w][id] = await p.evalIn(STRIP);
        if (id === 'changes' || id === 'workspace') await p.shot(`strip-${w}-po-${id}`);
      }
      await p.click(tool('spec')); await sleep(300);
      out['closed' + w] = await p.evalIn(STRIP);
      await p.shot(`strip-${w}-po`);
      // A task: the middle, its tools.
      await openTask(p);
      await p.click(tool('changes')); await sleep(600);
      out['task' + w] = await p.evalIn(STRIP);
      await p.shot(`strip-${w}-task-changes`);
      if (w !== 1440) { await p.close(); continue; }
      // The task's panes are in the tools; its branch's count is on Changes.
      out.taskPanes = await p.evalIn(`(() => { const dp = document.getElementById('detail-panel'), P = dp._panes;
        const r = rowBySid(SELECTED_SID); r.changes = { add: 1656, del: 40, files: 7 }; renderDetail();
        return { changes: P.changes.parentNode === PD.els.changes, workspace: P.workspace.parentNode === PD.els.workspace,
          spec: P.spec.parentNode === PD.els.spec, details: P.details.parentNode === PD.els.spec, activity: P.activity.parentNode === dp,
          tch: !!P.changes.querySelector('.tch'), poChanges: getComputedStyle(PD.els.changes.querySelector(':scope > .ch-body') || document.body).display,
          badge: (document.querySelector('${tool('changes')} .dk-strip-badge') || {}).textContent || '',
          badgeTip: (document.querySelector('${tool('changes')} .dk-strip-badge') || {}).title || '',
          badgeFits: (() => { const b = document.querySelector('${tool('changes')}'), x = b && b.querySelector('.dk-strip-badge'); if (!x) return false;
            const r = b.getBoundingClientRect(), q = x.getBoundingClientRect(); return q.left >= r.left && q.right <= r.right + 1; })(),
          poNote: getComputedStyle(PD.els.spec.querySelector('.pd-spec-po')).display }; })()`);
      await p.click(tool('spec')); await sleep(400);
      await p.until('PD.els.spec.textContent.includes("Quiet brakes")', 10000).catch(() => null);
      out.taskSpec = { ...(await p.evalIn(STRIP)), specText: await p.evalIn('PD.els.spec.innerText.slice(0, 400)') };
      await p.shot('strip-1440-task-spec');
      // Files: the task's folder; a file opened is a panel of the dock (#150) and keeps its page when the tool closes and opens again.
      await p.click(tool('workspace'));
      await p.until('!!PD.els.workspace.querySelector(".dp-pane .wsp .wsp-tree .wse.file[data-path$=\\"README.md\\"]")');
      await p.evalIn('PD.els.workspace.querySelector(".dp-pane .wsp .wsp-tree .wse.file[data-path$=\\"README.md\\"]").click(); 0');
      await p.until('(() => { const f = (() => { const id = [...PD.rt.keys()].find(k => k.startsWith("file:") && k.endsWith("/readme.md")); const e = id && PD.rt.get(id); return e ? e.el.querySelector("iframe.wsp-frame.on") : null; })(); try { return !!f && f.contentDocument.readyState === "complete" && f.contentWindow.location.pathname === "/fileview"; } catch (e) { return false; } })()', 20000);
      out.taskFiles = await p.evalIn(STRIP);
      await p.shot('strip-1440-task-files');
      await p.evalIn('(() => { const id = [...PD.rt.keys()].find(k => k.startsWith("file:") && k.endsWith("/readme.md")); const e = id && PD.rt.get(id); return e ? e.el.querySelector("iframe.wsp-frame.on") : null; })().contentWindow.__kept = 1; 0');
      await p.click(tool('workspace')); await sleep(300);
      await p.click(tool('board')); await sleep(300);
      await p.click(tool('workspace')); await sleep(300);
      out.fileKept = await p.evalIn('(() => { const f = (() => { const id = [...PD.rt.keys()].find(k => k.startsWith("file:") && k.endsWith("/readme.md")); const e = id && PD.rt.get(id); return e ? e.el.querySelector("iframe.wsp-frame.on") : null; })(); return !!(f && f.contentWindow && f.contentWindow.__kept); })()');
      // A reload opens the same tool on the same task.
      await p.evalIn('ensUpd.reload(); 0');
      await sleep(1500);
      await p.ready();
      await p.until('document.body.classList.contains("dp-docked") && !!PD.dock && PD.dock.flyOpen() === "workspace"', 20000);
      await sleep(400);
      out.reloaded = await p.evalIn(STRIP);
      // Closing the task: back to its project's PO, its panes back in the task panel, the tool still open.
      await p.evalIn('closeDetail(); 0'); await sleep(500);
      out.closed = await p.evalIn(`(() => ({ ...${STRIP}, dpHome: document.getElementById('detail-panel').parentNode === document.body,
        panesHome: Object.values(document.getElementById('detail-panel')._panes).every(x => x.parentNode === document.getElementById('detail-panel')),
        poWs: getComputedStyle(document.getElementById('ws-panel')).display, poIn: PO_PANEL.parentNode === PD.els['po-chat'] }))()`);
      // The Board's ⤢: the whole width, and back.
      await p.click(tool('board')); await sleep(400);
      await p.click('.pd-board .pd-board-max'); await sleep(500);
      out.boardMax = await p.evalIn(`(() => ({ max: !!PD.dock.maximised(), board: (() => { const b = PD.els.board.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.width)]; })(),
        auto: PD.dock.isAuto('board') }))()`);
      await p.shot('strip-1440-board-max');
      await p.click('.pd-board .pd-board-max'); await sleep(400);
      out.boardBack = await p.evalIn('({ max: !!PD.dock.maximised() })');
      // Every theme: the strip's icons and counts are legible.
      out.themes = {};
      await p.evalIn('PD.dock.reset(); 0'); await sleep(300);
      for (const t of ['light', 'dark', 'dim', 'paper', 'contrast', 'fjord', 'intellij-dark']) {
        await p.evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(t)}; 0`);
        await sleep(150);
        out.themes[t] = await p.evalIn(`(() => { const bg = e => { for (let x = e; x; x = x.parentElement) { const c = getComputedStyle(x).backgroundColor; if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; } return 'rgb(255, 255, 255)'; };
          const b = document.querySelector('${tool('points')}'), badge = b.querySelector('.dk-strip-badge');
          return { icon: [getComputedStyle(b).color, bg(b)], badge: badge ? [getComputedStyle(badge).color, bg(badge)] : null }; })()`);
      }
      await p.evalIn('document.documentElement.dataset.theme = "light"; 0');
      await p.close();
    }
    // ---- a project without a PO: an open task has the strip too
    {
      const p = await page(1440, 900);
      await go(p, A.plain);
      await sleep(600);
      await p.evalIn(`openDetail(ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.plainTask)}).sessionId); 0`);
      await p.until('document.body.classList.contains("dp-docked")', 20000);
      await sleep(400);
      await p.click(tool('board')); await sleep(500);
      out.plainTask = await p.evalIn(`(() => ({ ...${STRIP}, cards: PD.els.board.querySelectorAll('.card, tr.row').length, view: getComputedStyle(document.getElementById('view')).display }))()`);
      await p.evalIn('closeDetail(); 0'); await sleep(500);
      out.plainClosed = await p.evalIn(`({ dock: document.body.classList.contains('po-dock'), view: getComputedStyle(document.getElementById('view')).display,
        dpHome: document.getElementById('detail-panel').parentNode === document.body })`);
      await p.close();
    }
    // ---- Your asks follow the conversation in the middle
    {
      const p = await page(1440, 900);
      await go(p, A.proj); await poReady(p);
      await p.until('!!pdTold() && !!pdTold().points && pdTold().points.items.length > 0', 20000);
      await p.evalIn('window.__motors = pdTold(); (() => { const b = pdChatFrame().contentDocument.getElementById("msgs"); b.scrollTop = b.scrollHeight; })(); 0');
      await openTask(p);
      await p.click(tool('points')); await sleep(400);
      const asks = `({ rows: [...PD.els.points.querySelectorAll('.pdp-row')].map(r => r.dataset.pt), text: PD.els.points.innerText.slice(0, 200),
        badge: (document.querySelector('${tool('points')} .dk-strip-badge') || {}).textContent || '', fly: PD.dock.flyOpen() })`;
      out.asksTask = await p.evalIn(asks);
      // A link from a task: the task closes, the PO's conversation is on screen at that message.
      const link = '#po-dock .pdp-row[data-pt="P1"] a.pdp-link[title^="Go to your message"]';
      await p.evalIn(`window.__mid = document.querySelector(${JSON.stringify(link)}).dataset.mid; 0`);
      await p.click(link);
      await p.until('!document.body.classList.contains("dp-docked")', 10000);
      await p.until('(() => { const f = pdChatFrame(); return !!f && !f.contentWindow.eval("GOTO"); })()', 10000).catch(() => null);
      await sleep(400);
      out.asksLink = await p.evalIn(`(() => { const f = pdChatFrame(), box = f.contentDocument.getElementById('msgs');
        const el = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === window.__mid);
        const er = el.getBoundingClientRect(), br = box.getBoundingClientRect();
        return { open: document.body.classList.contains('detail-open'), shown: f.checkVisibility({ visibilityProperty: true }), poIn: PO_PANEL.parentNode === PD.els['po-chat'],
          inView: er.top >= br.top - 1 && er.top < br.bottom, landed: el.classList.contains('landed') }; })()`);
      // Then a task of a project without a PO, in the same page: nothing of Motors' PO stays, even if its chat tells again.
      await go(p, A.plain, true); await sleep(500);
      await p.evalIn(`openDetail(ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.plainTask)}).sessionId); 0`);
      await p.until('document.body.classList.contains("dp-docked")', 20000); await sleep(400);
      if (await p.evalIn('PD.dock.flyOpen()') !== 'points') { await p.click(tool('points')); await sleep(300); }
      await p.evalIn('(() => { const f = document.querySelector("#po-panel iframe.po-session"); f.dispatchEvent(new CustomEvent("po-points", { detail: window.__motors })); })(); 0');
      await sleep(200);
      out.asksPlain = await p.evalIn(`({ ...${asks}, told: !!pdTold() })`);
      await p.close();
    }
    // ---- widths: a tool opened on a wide window gives way on a narrower one, and a layout saved wide too
    {
      const p = await page(1728, 1117);
      await go(p, A.proj); await poReady(p); await sleep(400);
      await p.click(tool('changes')); await sleep(400);
      out.fit = { 1728: await p.evalIn(STRIP) };
      const size = async (w, h) => { await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false }, p.sessionId); await sleep(600); };
      for (const [w, h] of [[1280, 800], [1024, 768]]) { await size(w, h); out.fit[w] = await p.evalIn(STRIP); }
      await p.evalIn('ensUpd.reload(); 0'); await sleep(1500); await p.ready();
      await go(p, A.proj, true); await poReady(p);
      await p.until('PD.dock.flyOpen() === "changes"', 20000); await sleep(400);
      out.fit.reload = await p.evalIn(STRIP);
      await size(1728, 1117);
      out.fit.wide = await p.evalIn(STRIP);
      await p.close();
    }
    // ---- a pinned tool: all of it on screen, its controls reachable; a tool opened beside it too
    {
      const PINNED = id => `(() => { const box = e => { const b = e.getBoundingClientRect(); return { x: Math.round(b.left), w: Math.round(b.width), r: Math.round(b.right) }; };
        const st = PD.els[${JSON.stringify('${id}')}].closest('.dk-stack'), main = document.querySelector('#po-dock .dk-main'), fly = document.querySelector('#po-dock .dk-flyout.open');
        const hits = head => [...head.querySelectorAll('[data-dk-act]')].map(b => { const r = b.getBoundingClientRect(), h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return { act: b.dataset.dkAct, hit: !!h && b.contains(h) }; });
        const acts = hits(st.querySelector(':scope > .dk-head')), flyActs = fly ? hits(fly.querySelector('.dk-head')) : [];
        return { vw: innerWidth, main: box(main), tool: box(st), chat: box(PD.els['po-chat']), fly: fly ? box(fly) : null, flyOpen: PD.dock.flyOpen(), auto: PD.dock.isAuto(${JSON.stringify('${id}')}), acts, flyActs,
          over: document.getElementById('po-dock').classList.contains('pd-fly-over') }; })()`.split('${id}').join(id);
      const p = await page(1024, 768);
      await go(p, A.proj); await poReady(p); await sleep(400);
      await p.click(tool('changes')); await sleep(400);
      await menuPick(p, '#po-dock .dk-flyout.open', 'mode', 'mode:pinned'); await sleep(600);
      out.pin1024 = await p.evalIn(PINNED('changes'));
      await p.shot('strip-1024-pinned');
      // No room beside both: Files lies over the middle, which keeps its width.
      await p.click(tool('workspace')); await sleep(600);
      out.pin1024Fly = await p.evalIn(PINNED('changes'));
      await p.shot('strip-1024-pinned-files-over');
      // A reload at 1024: the same, measured once the dock shows.
      await p.evalIn('ensUpd.reload(); 0'); await sleep(1500); await p.ready();
      await go(p, A.proj, true); await poReady(p);
      await p.until('PD.dock.flyOpen() === "workspace"', 20000); await sleep(600);
      out.pin1024Reload = await p.evalIn(PINNED('changes'));
      await p.close();
      const q = await page(1728, 1117);
      await go(q, A.proj); await poReady(q); await sleep(400);
      await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, q.sessionId); await sleep(600);
      await q.click(tool('changes')); await sleep(400);
      await menuPick(q, '#po-dock .dk-flyout.open', 'mode', 'mode:pinned'); await sleep(600);
      await q.click(tool('workspace')); await sleep(600);
      out.pin1440 = await q.evalIn(PINNED('changes'));
      await q.shot('strip-1440-pinned-and-files');
      await q.close();
    }
    // ---- each tool, and an open task's Spec, out in a window: closed, it comes back slid out on its side (P8);
    // ⋯ › Move To is what moves it to another side (P9)
    {
      const SIDE = id => `(() => { const b = document.querySelector('${tool(id)}'), s = b && b.closest('.dk-strip'), r = PD.els[${JSON.stringify(id)}].getBoundingClientRect();
        return { visible: PD.dock.isVisible(${JSON.stringify(id)}), notice: !!document.querySelector('#po-dock .dk-outnote'), fly: PD.dock.flyOpen(), mode: PD.dock.viewMode(${JSON.stringify(id)}), side: PD.dock.side(${JSON.stringify(id)}), out: PD.dock.isOut(${JSON.stringify(id)}),
          strip: s ? [...s.classList].filter(c => /^dk-strip-(left|right|top|bottom)$/.test(c)).join('') : null,
          here: PD.els[${JSON.stringify(id)}].ownerDocument === document, shown: r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight, saved: localStorage.getItem('cd-tool-open') }; })()`;
      const p = await page(1440, 900);
      await go(p, A.proj); await poReady(p); await sleep(400);
      out.popBack = {};
      const round = async (id) => {
        if (await p.evalIn('PD.dock.flyOpen()') !== id) await p.click(tool(id));
        await p.until(`PD.dock.flyOpen() === ${JSON.stringify(id)}`, 5000); await sleep(300);
        const r = { before: await p.evalIn(SIDE(id)) };
        await menuPick(p, '#po-dock .dk-flyout.open', 'mode', 'mode:window');
        await p.until(`!!PD.dock.popWindow(${JSON.stringify(id)}) && PD.els[${JSON.stringify(id)}].ownerDocument !== document`, 15000);
        r.out = await p.evalIn(SIDE(id));
        await p.evalIn(`PD.dock.popWindow(${JSON.stringify(id)}).close(); 0`);
        await p.until(`!PD.dock.isOut(${JSON.stringify(id)}) && PD.els[${JSON.stringify(id)}].ownerDocument === document`, 10000); await sleep(500);
        r.back = await p.evalIn(SIDE(id));
        await p.click(tool(id));
        await p.until(`!!PD.dock.popWindow(${JSON.stringify(id)}) && PD.els[${JSON.stringify(id)}].ownerDocument !== document`, 15000);
        r.reopened = await p.evalIn(SIDE(id));
        r.menu = await p.evalIn(`(() => {
          const d = PD.dock.popWindow(${JSON.stringify(id)}).document;
          d.querySelector('[data-dk-act="menu"]').click();
          const result = { screenshot: !!d.querySelector('[data-dk-menu="screenshot"]'), hide: !!d.querySelector('[data-dk-menu="hide"]'), minus: !!d.querySelector('[data-dk-act="hide"]') };
          d.querySelector('[data-dk-sub="mode"]').click();
          d.querySelector('[data-dk-menu="mode:unpinned"]').click();
          return result;
        })()`);
        await p.until(`!PD.dock.isOut(${JSON.stringify(id)}) && PD.dock.flyOpen() === ${JSON.stringify(id)}`, 10000);
        r.docked = await p.evalIn(SIDE(id));
        return r;
      };
      for (const id of ${TOOLS}) out.popBack[id] = await round(id);
      await openTask(p);
      out.popBack.taskSpec = await round('spec');
      out.captureSupport = await p.evalIn(`(() => {
        const btn = () => document.querySelector('#po-dock .dk-flyout.open [data-dk-act="menu"]');
        btn().click();
        const supported = !!document.querySelector('[data-dk-menu="screenshot"]');
        btn().click();
        const crop = window.CropTarget;
        window.CropTarget = undefined;
        btn().click();
        const unsupported = !!document.querySelector('[data-dk-menu="screenshot"]');
        btn().click(); window.CropTarget = crop;
        return { supported, unsupported, secure: isSecureContext };
      })()`);
      await p.shot('strip-1440-spec-back');
      // Move To › Left, then Pinned and Unpinned: the left side throughout; then back to the right.
      out.moveTo = {};
      await p.click('#po-dock .dk-flyout.open [data-dk-act="menu"]');
      await p.click('.dk-menu.dk-options [data-dk-sub="side"]'); await sleep(200);
      await p.shot('strip-1440-spec-move-to');
      await p.click('.dk-menu.dk-submenu [data-dk-menu="side:left"]'); await sleep(500);
      out.moveTo.left = await p.evalIn(SIDE('spec'));
      await menuPick(p, '#po-dock .dk-flyout.open', 'mode', 'mode:pinned'); await sleep(400);
      out.moveTo.pinned = await p.evalIn(SIDE('spec'));
      await p.shot('strip-1440-spec-left-pinned');
      await menuPick(p, '#po-dock .dk-stack > .dk-head:has([data-dk-tab="spec"])', 'mode', 'mode:unpinned'); await sleep(400);
      out.moveTo.unpinned = await p.evalIn(SIDE('spec'));
      if (await p.evalIn('PD.dock.flyOpen()') !== 'spec') { await p.click(tool('spec')); await sleep(400); }
      await menuPick(p, '#po-dock .dk-flyout.open', 'side', 'side:right'); await sleep(500);
      out.moveTo.right = await p.evalIn(SIDE('spec'));
      // Hide in the window, reopen from Panels, and reopen through each API.
      const hidden = () => p.until('!PD.dock.isVisible("spec") && !PD.dock.isOut("spec")');
      const popped = () => p.until('!!PD.dock.popWindow("spec") && PD.els.spec.ownerDocument !== document');
      await menuPick(p, '#po-dock .dk-flyout.open', 'mode', 'mode:window'); await popped();
      await p.evalIn(`PD.dock.popWindow('spec').document.querySelector('[data-dk-act="hide"]').click(); 0`); await hidden();
      await p.click('.pd-panels'); await p.click('[data-pd-toggle="spec"]'); await popped();
      out.panelReopen = await p.evalIn(SIDE('spec'));
      await p.evalIn('pdMenuClose(false); PD.dock.popWindow("spec").close(); 0'); await hidden();
      for (const call of ['showPanel("spec")', 'setVisible("spec", true)', 'reveal("spec")']) {
        await p.evalIn('PD.dock.' + call + '; 0', true); await popped();
        await p.evalIn('PD.dock.popWindow("spec").close(); 0'); await hidden();
      }
      await p.evalIn('location.reload(); 0'); await sleep(1500); await p.ready(); await go(p, A.proj, true); await poReady(p);
      out.hiddenReload = await p.evalIn(SIDE('spec'));
      // Deterministic popup denial: use the actual Dock fallback path.
      out.blocked = await p.evalIn(`(() => {
        const open = window.open;
        try { window.open = () => null; PD.dock.reveal('spec'); }
        finally { window.open = open; }
        return { mode: PD.dock.viewMode('spec'), visible: PD.dock.isVisible('spec'), out: PD.dock.isOut('spec'), note: document.querySelector('.dk-note')?.textContent };
      })()`);
      await p.close();
    }
    // ---- a tool slides back on a click or focus elsewhere (Dock v0.5.1), and stays while it is used or pinned
    {
      const p = await page(1440, 900);
      await go(p, A.proj); await poReady(p); await sleep(400);
      const fly = () => p.evalIn('PD.dock.flyOpen()');
      const open = async (id) => { if (await fly() !== id) await p.click(tool(id)); await p.until(`PD.dock.flyOpen() === ${JSON.stringify(id)}`, 5000); await sleep(300); };
      const key = async (k, code, vk) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }, p.sessionId); };
      const chat = '#po-panel iframe.po-session:not([hidden])';
      const ah = out.autoHide = {};
      // Opened by a click, then a click in the conversation or the task list.
      await open('points'); await p.click(chat); await sleep(400);
      ah.chat = await fly();
      await open('board'); await p.click('#sw-list'); await sleep(400);
      ah.list = await fly();
      // A click on the tool's own text first (nothing in it has focus), then the conversation.
      await open('points'); await p.click('#po-dock .dk-flyout.open .dk-body'); await sleep(300);
      ah.ownText = await fly();
      await p.click(chat); await sleep(400);
      ah.chatAfterText = await fly();
      // Typing in a tool, its ⋯ menu, the file view's frame: it stays; then the conversation.
      await open('workspace');
      await p.until('!!PD.els.workspace.querySelector(".wsf-q")', 10000);
      await p.click('#po-dock .wsf-q'); await sleep(200);
      await c.send('Input.insertText', { text: 'READ' }, p.sessionId); await sleep(600);
      ah.typing = { fly: await fly(), value: await p.evalIn('PD.els.workspace.querySelector(".wsf-q").value') };
      await p.click('#po-dock .dk-flyout.open [data-dk-act="menu"]'); await sleep(300);
      ah.menu = { fly: await fly(), menu: await p.evalIn('!!document.querySelector(".dk-menu.dk-options")') };
      await key('Escape', 'Escape', 27); await sleep(300);
      await p.click('#po-dock .wsf-q'); await sleep(200);
      await key('Enter', 'Enter', 13);
      await p.until('(() => { const f = (() => { const id = [...PD.rt.keys()].find(k => k.startsWith("file:") && k.endsWith("/readme.md")); const e = id && PD.rt.get(id); return e ? e.el.querySelector("iframe.wsp-frame.on") : null; })(); try { return !!f && f.contentDocument.readyState === "complete" && f.contentWindow.location.pathname === "/fileview"; } catch (e) { return false; } })()', 20000);
      await sleep(400);
      ah.goToFile = await fly();
      // The file is a panel of the dock (#150), under the tool at this width: a click in the conversation
      // puts the tool back, then a click in the file's frame focuses it (and opens no tool).
      await p.click(chat); await sleep(400);
      await p.click('#po-dock iframe.wsp-frame.on'); await sleep(400);
      ah.fileFrame = { fly: await fly(), focus: await p.evalIn('document.activeElement.classList.contains("wsp-frame")') };
      await p.click(chat); await sleep(400);
      ah.chatAfterFrame = await fly();
      // Your asks' arrow takes the conversation to the message: the list stays out.
      await open('points');
      await p.click('#po-dock .pdp-row[data-pt="P1"] a.pdp-link[data-mid]'); await sleep(1200);
      ah.arrow = await fly();
      // Pinned (Dock Pinned): a click in the conversation or the task list leaves it.
      await menuPick(p, '#po-dock .dk-flyout.open', 'mode', 'mode:pinned'); await sleep(500);
      await p.click(chat); await sleep(300);
      await p.click('#sw-list'); await sleep(400);
      ah.pinned = await p.evalIn(`({ mode: PD.dock.viewMode('points'), shown: PD.dock.isVisible('points') && PD.els.points.getBoundingClientRect().width > 0 })`);
      // An open task: a click in its conversation puts its Spec back.
      await openTask(p);
      await open('spec');
      await p.click('#detail-panel iframe.dp-session'); await sleep(400);
      ah.taskChat = await fly();
      await p.close();
    }
    // ---- a phone keeps its tabs
    {
      const q = await page(430, 932, true);
      await go(q, A.proj); await q.until('document.body.classList.contains("po-dock") && !!PD.dock', 30000); await sleep(400);
      // The earlier pages saved their file panel (cd-ws-panels) and this page restored it
      // before go() cleared the key: a phone's own tabs are the fixed ones.
      await q.evalIn('[...PD.rt.keys()].forEach(id => PD.dock.removePanel(id)); 0'); await sleep(200);
      out.phone = await q.evalIn(`({ narrow: PD.dock.narrow(), strip: document.querySelectorAll('#po-dock .dk-strip-btn').length,
        tabs: [...document.querySelectorAll('#po-dock .dk-tab')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.dataset.dkTab), panels: !!document.querySelector('.pd-panels') })`);
      await q.evalIn(`openDetail(${sid}); 0`); await sleep(600);
      out.phoneTask = await q.evalIn(`({ docked: document.body.classList.contains('dp-docked'), tabs: [...document.querySelectorAll('#detail-panel .dp-tab')].filter(e => e.getBoundingClientRect().width > 0).length })`);
      await q.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.consoleErrors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
""".replace("${TOOLS}", json.dumps(TOOLS))


class TheWiring(unittest.TestCase):
    """What the page asks of the library (static checks)."""

    def test_the_dock_is_a_tool_strip(self):
        dock = INDEX[INDEX.index("function pdEnsure()"):INDEX.index("// The middle is the conversation alone")]
        for used in ("stripHover: false", "stripOpen: 'beside'", "strip: 44", "icon: PD_ICON[id]", "unpinSize:",
                     "can: (id, a) => (isPhone() && !(id === 'po-chat' && a === 'hide' && pdTask()))\n"
                     "      || (id !== 'po-chat' ? (a !== 'hide' || PD.dock?.viewMode(id) === 'window')\n"
                     "          : (a === 'float' || a === 'pop' || a === 'max' || a === 'min' || (a === 'hide' && PD.dock?.viewMode(id) === 'window')))"):
            self.assertIn(used, dock, used)
        self.assertIn("const PD_TOOLS = ['points', 'changes', 'workspace', 'board', 'spec'];", INDEX)
        self.assertIn("const PD_KEYS = { desk: 'cd-tool-strip', phone: 'cd-phone-tabs' };", INDEX)
        self.assertIn("function pdNarrow() { return isPhone(); }", INDEX)

    def test_the_vendored_library_is_v0_13_0(self):
        self.assertRegex((ROOT / "static" / "dock" / "VERSION").read_text(encoding="utf-8"), r"^fab-ioc/dock v0\.13\.0 23363eb")

    def test_the_title_bar_is_dock_s_default(self):
        dock = INDEX[INDEX.index("function pdEnsure()"):INDEX.index("// The middle is the conversation alone")]
        self.assertNotIn("headButtons", dock, "v0.5.0's ⋯ and −, not the classic buttons")


def start_hub(cls, prefix="ens-strip-"):
    """The hub in a thread, with Motors (a PO, an ask, a task with a spec)
    and Plain (a task, no PO); test_tool_icons.py uses it too."""
    cls.tmp = tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
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
        cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
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
    # One ask to the PO, answered, then enough talk that it is far up.
    text, ids = points.take(chatroom.get_room(po["id"], public=False), "Make the brakes quiet", to="claude", key="k1")
    assert ids == ["P1"], ids
    chatroom.post_message(po["id"], "user", text, to="claude")
    chatroom.post_message(po["id"], "claude", "Re P1: working on it.", to="user")
    for i in range(30):
        chatroom.post_message(po["id"], "codex", f"note {i}\n\n" + "words " * 60, to="claude")
    points.sync(po["id"], force=True)
    task = chatroom.create_room("Brakes that squeal", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
    chatroom.update_room({**chatroom.get_room(task["id"], public=False), "spec": "## Goal\nQuiet brakes on a long descent."})
    chatroom.post_message(task["id"], "user", "Why do the brakes squeal?")
    dashboard.assign_session_project(task["id"], cls.proj)
    cls.task = task["id"]
    (home / "README.md").write_text("# Motors\n", encoding="utf-8")
    ok, plain, _ = dashboard.register_project("Plain")
    assert ok, plain
    cls.plain = plain["id"]
    phome = Path(plain.get("home") or plain["path"])
    (phome / "README.md").write_text("# Plain\n", encoding="utf-8")
    ptask = chatroom.create_room("A plain task", [{"identity": "claude", "agent": "claude", "cwd": str(phome)}])
    chatroom.post_message(ptask["id"], "user", "Hello")
    dashboard.assign_session_project(ptask["id"], cls.plain)
    cls.plain_task = ptask["id"]
    cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    cls.server.daemon_threads = True
    cls.server.handle_error = lambda *a: None   # a page closed mid-answer
    threading.Thread(target=cls.server.serve_forever, daemon=True).start()


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheStrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls)
        base = Path(cls.tmp.name)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "plain": cls.plain, "task": cls.task, "plainTask": cls.plain_task, "shots": shots}
        script = base / "strip_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def no_scroll(self, g, what):
        self.assertLessEqual(g["scrollW"], g["vw"], f"{what}: no sideways scroll")
        self.assertLessEqual(g["scrollH"], g["vh"], f"{what}: no page scroll")

    def test_the_strip_holds_the_five_tools_as_icons(self):
        for w in (1280, 1440, 1728):
            g = self.got[f"po{w}"]
            with self.subTest(w=w):
                self.assertEqual([b["id"] for b in g["btns"]], TOOLS)
                self.assertEqual([b["label"] for b in g["btns"]], ["Your asks", "Changes", "Files", "Board", "Spec"])
                self.assertEqual([b["title"] for b in g["btns"]], [b["label"] for b in g["btns"]], "the name is the tooltip")
                self.assertTrue(all(b["svg"] for b in g["btns"]), "every button is an icon")
                self.assertEqual((g["strip"]["w"], g["strip"]["r"]), (44, g["vw"]), "44px at the right edge")
                self.assertEqual(g["mid"]["r"], g["strip"]["x"], "the conversation reaches the strip")
                self.assertIsNone(g["fly"])
                self.assertTrue(g["panels"], "Panels recovers windows without a former strip button")
                self.no_scroll(g, f"PO at {w}")

    def test_each_tool_opens_beside_the_conversation_at_its_width(self):
        for w in (1280, 1440, 1728):
            for id in TOOLS:
                g = self.got[f"open{w}"][id]
                with self.subTest(w=w, tool=id):
                    self.assertEqual(g["fly"], id)
                    self.assertEqual([b["id"] for b in g["btns"] if b["on"]], [id], "one open at a time")
                    self.assertEqual([b["id"] for b in g["btns"] if b["expanded"] == "true"], [id])
                    f, m = g["flyBox"], g["mid"]
                    self.assertEqual(f["r"], g["strip"]["x"], "between the middle and the strip")
                    self.assertLessEqual(m["r"], f["x"], "beside the conversation: nothing is covered")
                    self.assertGreaterEqual(m["w"], 360, "the conversation keeps its minimum")
                    want = {"points": 400, "spec": 480}.get(id)
                    if want:
                        self.assertEqual(f["w"], want)
                    else:
                        self.assertGreaterEqual(f["w"], 480, "Changes, Files and the Board are wide")
                        self.assertLessEqual(f["w"], 760)
                    self.no_scroll(g, f"{id} at {w}")
            with self.subTest(w=w, step="closed"):
                c = self.got[f"closed{w}"]
                self.assertIsNone(c["fly"], "a second click puts it back")
                self.assertEqual(c["mid"]["r"], c["strip"]["x"])
                self.assertIsNone(c["saved"], "nothing open: nothing remembered")

    def test_a_click_elsewhere_slides_a_tool_back(self):
        g = self.got["autoHide"]
        self.assertIsNone(g["chat"], "a click in the conversation")
        self.assertIsNone(g["list"], "a click in the task list")
        self.assertEqual(g["ownText"], "points", "a click on the tool's own text keeps it")
        self.assertIsNone(g["chatAfterText"], "then the conversation: back (the chat's frame tells the page)")
        self.assertIsNone(g["chatAfterFrame"], "from the file view's frame to the conversation: back")
        self.assertIsNone(g["taskChat"], "an open task's conversation puts its Spec back")

    def test_a_tool_in_use_stays_out(self):
        g = self.got["autoHide"]
        self.assertEqual(g["typing"], {"fly": "workspace", "value": "READ"}, "typing in Go to file")
        self.assertEqual(g["menu"], {"fly": "workspace", "menu": True}, "its ⋯ menu open")
        self.assertEqual(g["goToFile"], "workspace", "Go to file opens the file as a panel; the tool stays")
        self.assertEqual(g["fileFrame"], {"fly": None, "focus": True}, "a click in the file's panel, outside the tool")
        self.assertEqual(g["arrow"], "points", "Your asks' arrow takes the conversation to the message; the list stays")

    def test_a_pinned_tool_stays(self):
        self.assertEqual(self.got["autoHide"]["pinned"], {"mode": "pinned", "shown": True})

    def test_counts_show_on_the_buttons(self):
        g = self.got["taskPanes"]
        self.assertEqual(g["badge"], "+1.7k", "a task's branch: its lines added, short")
        self.assertIn("1,656 lines added, 40 removed", g["badgeTip"], "the tooltip has them all")
        self.assertTrue(g["badgeFits"], "the count stays on its button")

    def test_a_task_is_the_middle_with_its_own_tools(self):
        for w in (1280, 1440, 1728):
            g = self.got[f"task{w}"]
            with self.subTest(w=w):
                self.assertTrue(g["open"] and g["dpIn"] and g["docked"], "the task panel is in the middle")
                self.assertFalse(g["tabsShown"], "its tabs are gone")
                self.assertEqual(g["fly"], "changes")
                self.assertLessEqual(g["mid"]["r"], g["flyBox"]["x"])
                self.assertEqual(g["trail"][-1][0], "tool")
                self.no_scroll(g, f"task at {w}")
        p = self.got["taskPanes"]
        self.assertTrue(p["changes"] and p["workspace"] and p["spec"] and p["details"] and p["activity"], p)
        self.assertTrue(p["tch"], "Changes shows the task's branch")
        self.assertEqual(p["poChanges"], "none", "not the PO's")
        self.assertIn("Quiet brakes on a long descent", self.got["taskSpec"]["specText"], "Spec shows the task's spec")
        self.assertEqual(p["poNote"], "none", "the PO's note steps aside for the task's spec")
        self.assertEqual(self.got["taskSpec"]["fly"], "spec")
        self.assertEqual(self.got["taskFiles"]["fly"], "workspace")
        self.assertEqual(self.got["taskFiles"]["trail"][-1][0], "file", "the file is a crumb")

    def test_a_file_keeps_its_page_when_the_tool_closes(self):
        self.assertTrue(self.got["fileKept"])

    def test_a_reload_opens_the_same_tool(self):
        g = self.got["reloaded"]
        self.assertTrue(g["open"] and g["docked"])
        self.assertEqual((g["fly"], g["saved"]), ("workspace", "workspace"))

    def test_closing_the_task_gives_the_middle_back_to_the_po(self):
        g = self.got["closed"]
        self.assertFalse(g["open"] or g["docked"])
        self.assertTrue(g["dpHome"] and g["panesHome"], "the task panel and its panes are home again")
        self.assertTrue(g["poIn"], "the PO's conversation is in the middle")
        self.assertEqual(g["fly"], "workspace", "the tool stays open, now the PO's")
        self.assertNotEqual(g["poWs"], "none", "the PO's Files show")

    def test_the_boards_expand_takes_the_whole_width(self):
        m = self.got["boardMax"]
        self.assertTrue(m["max"])
        self.assertFalse(m["auto"], "pinned, then maximised")
        self.assertGreater(m["board"][1], 1000)
        self.assertFalse(self.got["boardBack"]["max"])

    def test_the_strip_is_legible_in_every_theme(self):
        self.assertEqual(set(self.got["themes"]), {"light", "dark", "dim", "paper", "contrast", "fjord", "intellij-dark"})
        for theme, g in self.got["themes"].items():
            with self.subTest(theme=theme):
                self.assertGreaterEqual(contrast(*g["icon"]), 4.5, g["icon"])
                if g["badge"]:
                    self.assertGreaterEqual(contrast(*g["badge"]), 4.5, g["badge"])

    def test_a_task_in_a_project_without_a_po_has_the_strip(self):
        g = self.got["plainTask"]
        self.assertTrue(g["docked"])
        self.assertEqual([b["id"] for b in g["btns"]], TOOLS)
        self.assertEqual(g["fly"], "board")
        self.assertGreater(g["cards"], 0, "its project's board")
        self.assertEqual(g["view"], "none")
        c = self.got["plainClosed"]
        self.assertFalse(c["dock"], "closed: the project's own board again")
        self.assertNotEqual(c["view"], "none")
        self.assertTrue(c["dpHome"])

    def test_your_asks_follow_the_conversation_in_the_middle(self):
        t = self.got["asksTask"]
        self.assertEqual((t["fly"], t["rows"], t["badge"]), ("points", ["P1"], "1"), "a task: its project's PO's asks")
        a = self.got["asksLink"]
        self.assertFalse(a["open"], "the task closed")
        self.assertTrue(a["shown"] and a["poIn"], "the PO's conversation is in the middle, on screen")
        self.assertTrue(a["inView"] and a["landed"], a)
        g = self.got["asksPlain"]
        self.assertEqual(g["fly"], "points")
        self.assertEqual((g["rows"], g["badge"], g["told"]), ([], "", False), "nothing of the other project's PO")
        self.assertIn("no PO", g["text"])

    def test_a_tool_gives_way_on_a_narrower_window(self):
        for k in ("1728", "1280", "1024", "reload", "wide"):
            g = self.got["fit"][k]
            with self.subTest(k=k):
                self.assertEqual(g["fly"], "changes")
                self.assertGreaterEqual(g["mid"]["w"], 360, "the conversation keeps its minimum")
                self.assertLessEqual(g["mid"]["r"], g["flyBox"]["x"], "nothing covered")
                self.assertEqual(g["flyBox"]["r"], g["strip"]["x"])
                self.no_scroll(g, k)
        self.assertEqual(self.got["fit"]["1728"]["flyBox"]["w"], 760)
        self.assertEqual(self.got["fit"]["wide"]["flyBox"]["w"], 760, "its width again when there is room")
        self.assertLess(self.got["fit"]["1024"]["flyBox"]["w"], 760)

    def test_a_pinned_tool_is_whole_and_its_controls_reachable(self):
        for k in ("pin1024", "pin1440"):
            g = self.got[k]
            with self.subTest(k=k):
                self.assertFalse(g["auto"], "pinned")
                m, t = g["main"], g["tool"]
                self.assertLessEqual(t["r"], m["r"], "the pinned tool is inside the middle, not clipped")
                self.assertGreaterEqual(g["chat"]["w"], 360, "the conversation keeps its minimum")
                self.assertGreaterEqual(t["w"], 300)
                self.assertTrue(g["acts"] and all(a["hit"] for a in g["acts"]), g["acts"])
        o = self.got["pin1024Fly"]
        self.assertEqual(o["flyOpen"], "workspace")
        self.assertTrue(o["over"], "at 1024 there is no room beside both: it lies over them")
        self.assertEqual((o["main"], o["tool"]), (self.got["pin1024"]["main"], self.got["pin1024"]["tool"]), "the middle keeps its width")
        self.assertEqual(o["fly"]["r"], o["main"]["r"])
        r = self.got["pin1024Reload"]
        self.assertEqual(r["flyOpen"], "workspace", "remembered across the reload")
        self.assertTrue(r["over"], "measured once the dock shows")
        self.assertGreaterEqual(r["fly"]["w"], 300, "a usable width, not a sliver")
        self.assertTrue(r["flyActs"] and all(a["hit"] for a in r["flyActs"]), r["flyActs"])
        self.assertGreaterEqual(r["chat"]["w"], 360)
        g = self.got["pin1440"]
        self.assertEqual(g["flyOpen"], "workspace")
        self.assertFalse(g["over"], "room for Files beside the conversation and Changes")
        self.assertLessEqual(g["main"]["r"], g["fly"]["x"], "Files covers nothing")
        self.assertGreaterEqual(g["fly"]["w"], 300)

    def test_closed_tool_windows_hide_reopen_and_dock_from_their_menu(self):
        for k, g in self.got["popBack"].items():
            id = "spec" if k == "taskSpec" else k
            with self.subTest(tool=k):
                self.assertEqual((g["before"]["fly"], g["before"]["side"], g["before"]["strip"]), (id, "right", "dk-strip-right"), g["before"])
                self.assertEqual((g["out"]["mode"], g["out"]["out"], g["out"]["here"]), ("window", True, False), g["out"])
                b = g["back"]
                self.assertEqual((b["out"], b["here"], b["fly"], b["mode"]), (False, True, None, "window"))
                self.assertFalse(b["visible"] or b["shown"] or b["notice"], b)
                self.assertEqual((b["side"], b["strip"]), ("right", "dk-strip-right"), "on its own side")
                self.assertNotEqual(b["saved"], id, "hidden windows are not restored as flyouts")
                self.assertTrue(g["reopened"]["out"] and g["reopened"]["visible"])
                self.assertTrue(all(g["menu"].values()), g["menu"])
                self.assertEqual((g["docked"]["mode"], g["docked"]["fly"]), ("unpinned", id))

    def test_move_to_is_what_changes_a_side(self):
        m = self.got["moveTo"]
        self.assertEqual((m["left"]["side"], m["left"]["strip"]), ("left", "dk-strip-left"), m["left"])
        self.assertEqual((m["pinned"]["mode"], m["pinned"]["side"]), ("pinned", "left"), "pinning keeps the side")
        self.assertEqual((m["unpinned"]["mode"], m["unpinned"]["side"], m["unpinned"]["strip"]), ("unpinned", "left", "dk-strip-left"), "so does unpinning")
        self.assertEqual((m["right"]["side"], m["right"]["strip"]), ("right", "dk-strip-right"), m["right"])

    def test_hidden_window_recovery_and_capture_support(self):
        self.assertEqual(self.got["consoleErrors"], [])
        # Dock v0.12.0 draws the panel itself, so Take Screenshot shows without Region Capture (CropTarget) too.
        self.assertEqual(self.got["captureSupport"], {"supported": True, "unsupported": True, "secure": True})
        self.assertTrue(self.got["panelReopen"]["out"])
        h = self.got["hiddenReload"]
        self.assertEqual((h["mode"], h["visible"], h["out"], h["notice"]), ("window", False, False, False))
        b = self.got["blocked"]
        self.assertEqual((b["mode"], b["visible"], b["out"]), ("pinned", True, False))
        self.assertIn("did not load; it is back here", b["note"])

    def test_a_phone_keeps_its_tabs(self):
        g = self.got["phone"]
        self.assertTrue(g["narrow"])
        self.assertEqual(g["strip"], 0)
        self.assertEqual(g["tabs"], ["po-chat", "points", "changes", "workspace", "board", "spec"])
        self.assertTrue(g["panels"], "a phone keeps its Panels menu")
        self.assertEqual(self.got["phoneTask"], {"docked": True, "tabs": 0}, "a phone's open task is in the dock too, its tools the dock's tabs (#129)")


if __name__ == "__main__":
    unittest.main()
