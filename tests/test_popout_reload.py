"""#204 (P170): a panel popped out into its own window survives a reload of the main page.

In headless Chrome over CDP, against a hub in a thread, with a project that has
a PO (Motors), a document and a task:

* a file panel popped out, then the main page reloaded: the same window stays
  open, is adopted again by the reloaded page (same panel, same file, same
  scroll place), and no notice is shown;
* the same through Ensemble's own reload after a merge (the hub's page stamps
  change), and across a hub restart (the hub stops, a new one starts on the
  same port, then the page reloads);
* a reload just after a window opened, before its page first looked at the
  main page: it is still taken back, not left open and unowned;
* every kind of panel does the same: the conversation, a tool (Your asks), a
  task's chat and the task list;
* the main tab closed: its windows close with it;
* a panel out whose window was closed before the reload: no notice over the
  conversation at 1728x1117 and 390x844; its Panels menu row says so and
  opens it again with one click.

ENSEMBLE_POP_MEASURE=1 prints what happens with the windows (the measurements
in the task's report) instead of checking. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
MEASURE = os.environ.get("ENSEMBLE_POP_MEASURE") == "1"

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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const ctl = what => fetch(A.ctl + '/' + what).then(r => r.text());
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const attach = async (targetId) => {
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); let last = null; while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) { last = e; } if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr + (last ? ' :: ' + last.message : '')); };
    return { sessionId, evalIn, until };
  };
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const p = await attach(targetId);
    p.targetId = targetId;
    p.close = () => c.send('Target.closeTarget', { targetId });
    await c.send('Page.enable', {}, p.sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, p.sessionId);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, p.sessionId);
    p.shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, p.sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    p.load = async () => {
      await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
      await p.until('window.ensBooted === true', 30000);
      await p.evalIn(`(() => { try { localStorage.setItem('cd-view', 'board'); } catch (e) {} VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
      await p.until('document.body.classList.contains("po-dock") && !!PD.dock && (!!LD.dock || isPhone())', 30000);
    };
    await c.send('Page.navigate', { url: A.base + '/' }, p.sessionId);
    await p.load();
    return p;
  };
  const pops = async () => (await c.send('Target.getTargets')).targetInfos.filter(t => t.type === 'page' && /popout/.test(t.url));
  // What the window shows, read in the window itself (its own realm, whatever became of the page that opened it).
  const POP_STATE = `(() => { const o = window.opener; let openerUrl = null, openerHasDock = null, openerSame = null;
    try { openerUrl = o && o.location.href; openerHasDock = !!(o && o.PD && o.PD.dock); openerSame = o ? o.document === window.__measureDoc : null; } catch (e) { openerUrl = 'denied'; }
    const f = document.querySelector('#dk-pop-root iframe:not([hidden])'); let frame = null;
    try { frame = f ? { url: f.contentWindow.location.pathname + f.contentWindow.location.search, ready: f.contentDocument.readyState, text: (f.contentDocument.body || {}).innerText ? f.contentDocument.body.innerText.slice(0, 80) : '' } : null; } catch (e) { frame = 'denied'; }
    return { opener: !!o, openerClosed: o ? o.closed : null, openerUrl, openerHasDock, openerSame, title: document.title,
      root: (document.getElementById('dk-pop-root') || {}).innerText ? document.getElementById('dk-pop-root').innerText.slice(0, 120) : '',
      panel: !!document.querySelector('#dk-pop-root .dk-panel'), frame }; })()`;
  const popState = async (t) => { const s = await attach(t.targetId); try { return await s.evalIn(POP_STATE); } catch (e) { return 'error: ' + e.message.slice(0, 200); } };
  const FILE_ID = '[...PD.rt.keys()].find(k => k.startsWith("file:"))';
  const fileFrameOf = w => `(() => { const f = ${w}.document.querySelector('#dk-pop-root .wfp iframe.wsp-frame.on'); try { return !!f && f.contentDocument.readyState === 'complete' && f.contentWindow.location.pathname === '/fileview' && !!f.contentDocument.querySelector('#main .md, #main pre, #main .code, #main'); } catch (e) { return false; } })()`;
  const noteShown = `(() => { const n = document.querySelector('.dk-outnote'); if (!n) return false; const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(n).visibility !== 'hidden'; })()`;
  try {
    const p = await page(1440, 900);
    // ---- a file panel, from the Files tool
    await p.evalIn('document.querySelector(\'.dk-strip-btn[data-dk-auto="workspace"]\').click(); 0');
    await p.until('[...PD.els.workspace.querySelectorAll(".wse.doc[data-path]")].some(x => x.dataset.path.endsWith("#1 Notes.md"))', 20000);
    await p.evalIn('[...PD.els.workspace.querySelectorAll(".wse.doc[data-path]")].find(x => x.dataset.path.endsWith("#1 Notes.md")).click(); 0');
    await p.until('!!' + FILE_ID + ' && !!document.querySelector("#po-dock .wfp iframe.wsp-frame.on")', 20000);
    const fid = await p.evalIn(FILE_ID);
    out.fid = fid;
    const popOut = async (id) => {
      await p.evalIn(`PD.dock.popOut(${JSON.stringify(id)}) && 0`);
      await p.until(`!!PD.dock.popWindow(${JSON.stringify(id)}) && ${fileFrameOf(`PD.dock.popWindow(${JSON.stringify(id)})`)}`, 20000);
    };
    await popOut(fid);
    // Scrolled down in the window, to see the place kept.
    await p.evalIn(`(() => { const f = PD.dock.popWindow(${JSON.stringify(fid)}).document.querySelector('.wfp iframe.wsp-frame.on'); const s = f.contentWindow.fvScrollOwner(f.contentDocument.getElementById('main')); s.scrollTop = 600; return s.scrollTop; })()`);
    await sleep(400);
    out.scrolledTo = await p.evalIn(`(() => { const f = PD.dock.popWindow(${JSON.stringify(fid)}).document.querySelector('.wfp iframe.wsp-frame.on'); return Math.round(f.contentWindow.fvScrollOwner(f.contentDocument.getElementById('main')).scrollTop); })()`);
    const reload = async (how) => {
      await p.evalIn('window.__before = 1');
      if (how === 'merge') { await ctl('bump'); await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = 0; ensUpd.poll(); 0'); }
      else if (how === 'restart') { await ctl('restart'); await p.evalIn('location.reload(); 0'); }
      else await p.evalIn('location.reload(); 0');
      await p.until('!window.__before', 40000);
      await p.load();
    };
    if (A.measure) {
      const m = out.measure = {};
      // As shipped: the reload.
      m.before = await pops();
      await reload('plain'); await sleep(2500);
      m.afterReload = { windows: (await pops()).length, isOut: await p.evalIn(`PD.dock.isOut(${JSON.stringify(fid)})`), open: await p.evalIn(`PD.dock.isOpenOut(${JSON.stringify(fid)})`), notice: await p.evalIn(noteShown),
        noticeText: await p.evalIn('(document.querySelector(".dk-outnote") || {}).innerText || ""') };
      await p.shot('measure-notice');
      // The window kept by force: Dock's close at pagehide made a no-op, and its page's own check for a
      // reloaded opener (its key) taken away. What does the window hold after the reload?
      await p.evalIn(`PD.dock.popOut(${JSON.stringify(fid)}) && 0`);
      await p.until(`!!PD.dock.popWindow(${JSON.stringify(fid)}) && ${fileFrameOf(`PD.dock.popWindow(${JSON.stringify(fid)})`)}`, 20000);
      const keep = `(() => { const w = PD.dock.popWindow(${JSON.stringify(fid)}); w.close = () => {}; delete w.__dockPopKey; w.__measureDoc = document; return 0; })()`;
      await p.evalIn(keep);
      await reload('plain'); await sleep(1500);
      let ts = await pops(); m.keptPlain = { windows: ts.length, state: ts[0] ? await popState(ts[0]) : null };
      // A click on the window's own controls (its title bar's ⋯): handled by the page that is gone?
      if (ts[0]) { const s = await attach(ts[0].targetId); m.keptClick = await s.evalIn(`(() => { const b = document.querySelector('[data-dk-act="menu"]'); if (!b) return 'no button'; b.click(); return !!document.querySelector('.dk-menu.dk-options'); })()`).catch(e => 'error ' + e.message.slice(0, 100)); }
      // Only the library's close at pagehide taken away (its page's key check kept): what does its page do?
      for (const t of ts) await c.send('Target.closeTarget', { targetId: t.targetId });
      await p.evalIn(`PD.dock.popIn(${JSON.stringify(fid)}); PD.dock.popOut(${JSON.stringify(fid)}) && 0`);
      await p.until(`!!PD.dock.popWindow(${JSON.stringify(fid)}) && ${fileFrameOf(`PD.dock.popWindow(${JSON.stringify(fid)})`)}`, 20000);
      await p.evalIn(`(() => { const w = PD.dock.popWindow(${JSON.stringify(fid)}); const shut = w.close.bind(w); w.__shutCalls = 0; w.close = () => { w.__shutCalls++; }; return 0; })()`);
      await reload('plain'); await sleep(1500);
      ts = await pops();
      m.keyOnly = { windows: ts.length, state: ts[0] ? await popState(ts[0]) : null };
      if (ts[0]) { const s = await attach(ts[0].targetId); m.keyOnly.closeCalls = await s.evalIn('window.__shutCalls'); }
    } else {
      // ---- the outcome: the window kept and taken back, after each kind of reload
      const keptAfter = async (id, how, check) => {
        const before = (await pops()).map(t => t.targetId);
        await reload(how);
        const J = JSON.stringify(id);
        const took = await p.until(`PD.dock && (PD.dock.isOpenOut(${J}) || (LD.dock && LD.dock.isOpenOut(${J})))`, 20000).then(() => true, () => false);
        await sleep(1200);
        const after = (await pops()).map(t => t.targetId);
        const r = await p.evalIn(`(() => { const d = PD.dock.panels().some(x => x.id === ${J}) ? PD.dock : LD.dock; const w = d.popWindow(${J});
          const el = d.panels().find(x => x.id === ${J}).el;
          return { took: ${took}, open: d.isOpenOut(${J}), inWindow: !!w && el.ownerDocument === w.document && w.document.contains(el),
            notice: ${noteShown}, keptWindow: ${JSON.stringify(before)}.length === 1 && ${JSON.stringify(before)}[0] === ${JSON.stringify(after[0] || '')}, windows: ${after.length},
            leaving: window.ensPopLeaving }; })()`);
        if (check) Object.assign(r, await p.evalIn(check));
        // Its own controls answer again: the title bar's ⋯ opens its menu in the window.
        r.menu = await p.evalIn(`(async () => { const d = PD.dock.panels().some(x => x.id === ${J}) ? PD.dock : LD.dock; const w = d.popWindow(${J}); if (!w) return false;
          const b = w.document.querySelector('.dk-pop .dk-head [data-dk-act="menu"]'); if (!b) return 'no button'; b.click(); await new Promise(r => setTimeout(r, 200));
          const m = !!w.document.querySelector('.dk-menu.dk-options'); w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return m; })()`);
        return r;
      };
      const FILE_CHECK = `(() => { const w = PD.dock.popWindow(${JSON.stringify(fid)}); const f = w && w.document.querySelector('.wfp iframe.wsp-frame.on');
        let top = null; try { top = Math.round(f.contentWindow.fvScrollOwner(f.contentDocument.getElementById('main')).scrollTop); } catch (e) {}
        return { file: !!f && new URL(f.src).searchParams.get('path').endsWith('#1 Notes.md'), top }; })()`;
      const fileTop = async () => { for (let i = 0; i < 40; i++) { const r = await p.evalIn(FILE_CHECK); if (r.top && r.top > 0) return r; await sleep(150); } return p.evalIn(FILE_CHECK); };
      out.file = {};
      for (const how of ['plain', 'merge', 'restart']) {
        out.file[how] = await keptAfter(fid, how);
        Object.assign(out.file[how], await fileTop());
      }
      // ---- a reload right after the window opened, before its page's first look at this one (review 1)
      await p.evalIn(`(async () => { PD.dock.popOut('points'); const t = Date.now();
        for (;;) { const w = PD.dock.popWindow('points'); let ready = false; try { ready = !!w && w.location.pathname === '/static/popout.html' && !!w.document.getElementById('dk-pop-root'); } catch (e) {}
          if (ready || Date.now() - t > 10000) break; await new Promise(r => setTimeout(r, 5)); }
        window.__before = 1; location.reload(); return 0; })()`);
      await p.until('!window.__before', 20000); await p.load();
      out.early = { took: await p.until(`PD.dock.isOpenOut('points')`, 20000).then(() => true, () => false) };
      await sleep(600);
      out.early.windows = (await pops()).length;
      out.early.notice = await p.evalIn(noteShown);
      await p.evalIn(`PD.dock.popIn('points'); 0`);
      await p.until('PD.dock && !PD.dock.isOut("points")', 10000);
      // ---- every other kind of panel, each in its window, through one reload
      const chatId = 'chat:' + A.task;
      await p.evalIn(`pdChatOpen(${JSON.stringify(A.task)}) && 0`);
      await p.until(`PD.rt.has(${JSON.stringify(chatId)})`);
      const others = [['pd', 'points'], ['pd', 'board'], ['pd', 'po-chat'], ['pd', chatId], ['ld', 'list']];
      for (const [k, id] of others) {
        const D = k === 'ld' ? 'LD.dock' : 'PD.dock';
        await p.evalIn(`${D}.popOut(${JSON.stringify(id)}) && 0`);
        await p.until(`!!${D}.popWindow(${JSON.stringify(id)}) && !!${D}.popWindow(${JSON.stringify(id)}).document.querySelector('#dk-pop-root .dk-panel')`, 20000);
      }
      await sleep(800);
      // The list scrolled in its window, to see its place kept.
      // A box scrolled in a window (the board's), to see its place kept.
      const SCROLLER = `(() => { const el = PD.els.board; const v = el.ownerDocument.defaultView;
        return [el, ...el.querySelectorAll('*')].find(x => x.scrollHeight > x.clientHeight + 40 && /auto|scroll/.test(v.getComputedStyle(x).overflowY)) || null; })()`;
      out.boardScrolled = await p.evalIn(`(() => { const s = ${SCROLLER}; if (!s) return null; s.scrollTop = 120; return s.scrollTop; })()`);
      const allPops = (await pops()).length;
      await reload('plain');
      await p.until(`${JSON.stringify(others)}.every(([k, id]) => (k === 'ld' ? LD.dock : PD.dock).isOpenOut(id))`, 20000).catch(() => null);
      await sleep(1000);
      out.others = await p.evalIn(`(() => { const r = {}; for (const [k, id] of ${JSON.stringify(others)}) { const d = k === 'ld' ? LD.dock : PD.dock; const w = d.popWindow(id); const el = d.panels().find(x => x.id === id).el;
        r[id] = { open: d.isOpenOut(id), inWindow: !!w && w.document.contains(el), frames: [...el.querySelectorAll('iframe')].filter(f => !f.hidden).map(f => { try { return f.contentDocument.readyState; } catch (e) { return 'denied'; } }) }; }
        const s = ${SCROLLER};
        return { panels: r, boardTop: s ? s.scrollTop : null, notice: ${noteShown} }; })()`);
      out.others.windowsBefore = allPops;
      out.others.windowsAfter = (await pops()).length;
      // ---- a window closed while the page reloads: no notice, a quiet mark, one click opens it again
      const fileWin = (await pops()).find(t => /Notes/.test(t.title));
      // Closed while the page reloads, before the next one takes it back: its
      // window stops asking (its timers cleared), and is closed once the page is back.
      const closeWhileAway = async (t) => {
        if (t) { const s = await attach(t.targetId); await s.evalIn('(() => { for (let i = 0; i < 5000; i++) clearInterval(i); return 0; })()'); }
        await p.evalIn('window.__before = 1; location.reload(); 0');
        await p.until('!window.__before', 20000);
        await p.load();
        if (t) await c.send('Target.closeTarget', { targetId: t.targetId });
      };
      await closeWhileAway(fileWin);
      await sleep(3800);   // past the page's first seconds, when windows still open are taken back
      out.closed = await p.evalIn(`(() => { const id = ${JSON.stringify(fid)};
        return { out: PD.dock.isOut(id), open: PD.dock.isOpenOut(id), notice: ${noteShown}, panelsMark: !!document.querySelector('.pd-panels.pd-out-closed'),
          noticeEls: [...document.querySelectorAll('.dk-outnote')].map(n => getComputedStyle(n).display) }; })()`);
      await p.shot('closed-1440');
      // Panels lists it first; one click opens its window again.
      await p.evalIn('document.querySelector(".pd-panels").click(); 0');
      out.closedMenu = await p.evalIn(`[...document.querySelectorAll('.pd-menu .pd-out-row [data-pd-pop]')].map(b => b.dataset.pdPop + ':' + b.textContent.trim())`);
      await p.evalIn('document.querySelector(\'.pd-menu .pd-out-row [data-pd-pop="open"]\').click(); 0');
      await p.until(`PD.dock.isOpenOut(${JSON.stringify(fid)})`, 10000).catch(() => null);
      out.reopened = await p.evalIn(`({ open: PD.dock.isOpenOut(${JSON.stringify(fid)}), mark: !!document.querySelector('.pd-panels.pd-out-closed') })`);
      // A tool closed the same way: the ring on its strip button, whose click opens it.
      const ptsWin = (await pops()).find(t => /Your asks/.test(t.title));
      await closeWhileAway(ptsWin);
      await sleep(3800);
      out.toolClosed = await p.evalIn(`({ out: PD.dock.isOut('points'), open: PD.dock.isOpenOut('points'), ring: !!document.querySelector('.dk-strip-btn.pd-out-closed[data-dk-auto="points"]'), notice: ${noteShown} })`);
      await p.evalIn('document.querySelector(\'.dk-strip-btn[data-dk-auto="points"]\').click(); 0');
      await p.until(`PD.dock.isOpenOut('points')`, 10000).catch(() => null);
      out.toolReopened = await p.evalIn(`({ open: PD.dock.isOpenOut('points'), ring: !!document.querySelector('.dk-strip-btn.pd-out-closed') })`);
      // ---- no notice over the conversation at 1728x1117 and 390x844, a window closed before the reload
      out.sizes = {};
      for (const [w, h, mobile] of [[1728, 1117, false], [390, 844, true]]) {
        const q = await page(w, h, mobile);
        // Only the file out, its window not open here (the first page holds it).
        await q.evalIn(`(() => { for (const d of [PD.dock, LD.dock]) if (d) for (const x of d.panels()) if (x.id !== ${JSON.stringify(fid)} && d.isOut(x.id)) d.popIn(x.id); return 0; })()`);
        await q.evalIn('window.__before = 1; location.reload(); 0');
        await q.until('!window.__before', 20000); await q.load();
        await sleep(3800);
        out.sizes[w + 'x' + h] = await q.evalIn(`(() => { const chat = PD.els['po-chat'].getBoundingClientRect();
          const over = [...document.querySelectorAll('.dk-outnote, .dk-note, [role="status"]')].filter(n => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(n).visibility !== 'hidden'
            && r.left < chat.right && r.right > chat.left && r.top < chat.bottom && r.bottom > chat.top && !PD.els['po-chat'].contains(n); }).map(n => n.className || n.id);
          return { notice: ${noteShown}, over, narrow: PD.dock.narrow() }; })()`);
        await q.shot('no-notice-' + w + 'x' + h);
        await q.close();
      }
      // ---- the main tab closed: its windows close with it
      const left = (await pops()).length;
      await c.send('Target.closeTarget', { targetId: p.targetId });
      await sleep(1500);
      out.tabClosed = { before: left, after: (await pops()).length };
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


class _Ctl(BaseHTTPRequestHandler):
    owner = None

    def do_GET(self):
        self.owner.control(self.path.strip("/"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class PopOutReload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pop-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.bumped = False
        real_stamps = dashboard.page_stamps

        def stamps():
            s = real_stamps()
            if cls.bumped and s.get("index.html"):
                s["index.html"] += "-merged"
            return s
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
            mock.patch.object(dashboard, "page_stamps", stamps),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        (home / "Documents").mkdir(exist_ok=True)
        (home / "Documents" / "#1 Notes.md").write_text(
            "# Notes\n\n" + "".join(f"## Part {i}\n\n" + "A line of the notes. " * 30 + "\n\n" for i in range(60)), encoding="utf-8")
        members = lambda: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                           {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        task = chatroom.create_room("Wider board", members())
        cls.task = task["id"]
        dashboard.assign_session_project(cls.task, cls.proj)
        po = chatroom.create_room("PO talk", members())
        cls.po = po["id"]
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        for i in range(12):
            chatroom.post_message(cls.po, "codex", f"note {i}\n\n" + "words " * 40, to="claude")
        # Enough tasks that the list scrolls in its window.
        for i in range(40):
            dashboard.assign_session_project(chatroom.create_room(f"Task {i}", members())["id"], cls.proj)
        cls.server = cls._serve(0)
        cls.port = cls.server.server_address[1]
        _Ctl.owner = cls
        cls.ctl = ThreadingHTTPServer(("127.0.0.1", 0), _Ctl)
        threading.Thread(target=cls.ctl.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.port}",
                "ctl": f"http://127.0.0.1:{cls.ctl.server_address[1]}", "proj": cls.proj, "po": cls.po,
                "task": cls.task, "shots": shots, "measure": MEASURE}
        script = base / "popout_reload_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if MEASURE:
            print(json.dumps(cls.got, indent=1))

    @classmethod
    def _serve(cls, port):
        s = ThreadingHTTPServer(("127.0.0.1", port), dashboard.Handler)
        s.daemon_threads = True
        s.handle_error = lambda *a: None
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s

    @classmethod
    def control(cls, what):
        if what == "bump":
            cls.bumped = True
        elif what == "restart":
            # The hub stops, and a new one starts on the same port a moment later.
            cls.server.shutdown()
            cls.server.server_close()
            time.sleep(2)
            cls.server = cls._serve(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.ctl.shutdown()
        cls.ctl.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        if MEASURE:
            self.skipTest("measuring")

    def test_a_file_window_is_kept_and_taken_back_after_each_kind_of_reload(self):
        self.assertEqual(self.got["scrolledTo"], 600)
        for how in ("plain", "merge", "restart"):
            r = self.got["file"][how]
            with self.subTest(how=how):
                self.assertTrue(r["keptWindow"], "the same window, never closed")
                self.assertEqual(r["windows"], 1)
                self.assertTrue(r["took"] and r["open"] and r["inWindow"], "the reloaded page draws the panel in it")
                self.assertTrue(r["file"], "the same file")
                self.assertEqual(r["top"], 600, "at the same place")
                self.assertTrue(r["menu"], "its controls answer again")
                self.assertFalse(r["notice"], "nothing asked")
                self.assertFalse(r["leaving"])

    def test_a_reload_right_after_the_window_opened_keeps_it(self):
        # Review 1: the window's first look found the reloaded page and took it for its own.
        e = self.got["early"]
        self.assertTrue(e["took"], "taken back by the reloaded page")
        self.assertEqual(e["windows"], 2, "the file's window and this one")
        self.assertFalse(e["notice"])

    def test_every_kind_of_panel_is_kept(self):
        o = self.got["others"]
        self.assertEqual(o["windowsBefore"], o["windowsAfter"])
        self.assertEqual(set(o["panels"]), {"points", "board", "po-chat", f"chat:{self.task}", "list"})
        for pid, r in o["panels"].items():
            with self.subTest(panel=pid):
                self.assertTrue(r["open"] and r["inWindow"], r)
                self.assertTrue(all(f == "complete" for f in r["frames"]), r)
        self.assertEqual(self.got["boardScrolled"], 120)
        self.assertEqual(o["boardTop"], 120, "a scrolled box keeps its place")
        self.assertFalse(o["notice"])

    def test_a_window_closed_meanwhile_is_a_quiet_mark_not_a_notice(self):
        c = self.got["closed"]
        self.assertTrue(c["out"] and not c["open"])
        self.assertFalse(c["notice"])
        self.assertEqual(c["noticeEls"], ["none"], "the library's notice is never shown")
        self.assertTrue(c["panelsMark"], "Panels carries the mark")
        self.assertEqual(self.got["closedMenu"], ["open:#1 Notes.md window closed", "back:Bring back here"])
        self.assertEqual(self.got["reopened"], {"open": True, "mark": False}, "one click opens it again")
        t = self.got["toolClosed"]
        self.assertTrue(t["out"] and not t["open"] and t["ring"] and not t["notice"], t)
        self.assertEqual(self.got["toolReopened"], {"open": True, "ring": False}, "its strip button opens it again")

    def test_no_notice_over_the_conversation(self):
        for size, r in self.got["sizes"].items():
            with self.subTest(size=size):
                self.assertFalse(r["notice"])
                self.assertEqual(r["over"], [])
        self.assertTrue(self.got["sizes"]["390x844"]["narrow"])

    def test_the_main_tab_closed_takes_its_windows(self):
        self.assertEqual(self.got["tabClosed"]["before"], 6)
        self.assertEqual(self.got["tabClosed"]["after"], 0)


class ThePage(unittest.TestCase):
    """The page's wiring, read from the files."""

    def test_both_docks_open_ensembles_page_through_the_glue(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertEqual(index.count("popUrl: '/static/popout.html',"), 2)
        self.assertEqual(index.count("openWindow: ensPopOpen,"), 2)
        self.assertNotIn("popUrl: '/static/dock/src/popout.html'", index)
        self.assertIn(".dk-outnote { display: none !important; }", index)

    def test_the_pop_out_page_waits_for_the_next_page(self):
        page = (ROOT / "static" / "popout.html").read_text(encoding="utf-8")
        for tok in ('id="dk-pop-root"', 'class="dk-popwin"', "ensPopLeaving", "ensPopAdopt", "o.closed"):
            self.assertIn(tok, page)
        self.assertNotIn("__dockPopKey", page, "not closed by a new page's key")


if __name__ == "__main__":
    unittest.main()
