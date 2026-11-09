"""Tasks and the Inbox share one left strip (#322). On a computer both are
panels of the list dock, its left strip holding Tasks then Inbox, as the
tools sit on the PO dock's right one, on every screen. In headless Chrome over
CDP, against a hub in a thread serving the pages.

Three projects: Motors (a PO with an answer to check, so one Inbox row),
Wheels (a PO) and Alpha (none), and a task in no project. Checked:

* at 1440x900 and 1728x1117 a fresh browser shows Tasks then Inbox on the left
  strip; the Inbox pinned open at the left of the page, the list closed on its
  strip; the PO dock has no Inbox; the bar's Inbox button gives way; the strip
  button carries the count;
* the Tasks button slides the list out, a click elsewhere or Esc puts it back;
  a list row opens its conversation;
* the Inbox on another project's PO screen: All shows Motors' row, and a click
  opens Motors' PO;
* the Inbox pinned, on its strip, floating and in its window; on the other
  side and first on the strip;
* Panels ▾ › Tasks and › Inbox; a reload keeps the layout; Reset layout puts
  the default back;
* a layout saved before #322 (the list hidden, the Inbox in the PO dock,
  docked or on its strip) opens with both on the left strip, the Inbox as the
  person kept it;
* 800x600 overflows nothing; a phone (390x844) has no list dock and keeps the
  Inbox as a tab of the PO dock;
* the page threw nothing meanwhile.

Measures go to $ENSEMBLE_SHOTS/left-strip.json and screenshots there too when
it is set. Skipped without Node or Chrome.
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
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
WIDTHS = [(1440, 900), (1728, 1117)]
# Layouts saved by main at 1bcc2bd (before #322) at 1440x900: the list dock's
# #306 default (the list hidden) and the PO dock with its Inbox docked, on its
# strip and floating.
OLD = {
 "ld": "{\"v\":1,\"root\":{\"t\":\"stack\",\"panels\":[\"page\"],\"active\":\"page\"},\"floats\":[],\"auto\":[],\"hidden\":[{\"id\":\"list\",\"was\":{\"kind\":\"dock\",\"home\":null}}],\"out\":{}}",
 "pinned": "{\"v\":1,\"root\":{\"t\":\"split\",\"dir\":\"row\",\"kids\":[{\"t\":\"stack\",\"panels\":[\"inbox\"],\"active\":\"inbox\",\"size\":280},{\"t\":\"stack\",\"panels\":[\"po-chat\"],\"active\":\"po-chat\"}]},\"floats\":[],\"auto\":[{\"id\":\"changes\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"workspace\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"board\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"spec\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":480}],\"hidden\":[],\"out\":{}}",
 "unpinned": "{\"v\":1,\"root\":{\"t\":\"stack\",\"panels\":[\"po-chat\"],\"active\":\"po-chat\"},\"floats\":[],\"auto\":[{\"id\":\"changes\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"workspace\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"board\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"spec\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":480},{\"id\":\"inbox\",\"edge\":\"left\",\"size\":280,\"home\":{\"peers\":[],\"index\":0,\"near\":\"po-chat\",\"side\":\"left\",\"size\":280}}],\"hidden\":[],\"out\":{}}",
 "float": "{\"v\":1,\"root\":{\"t\":\"stack\",\"panels\":[\"po-chat\"],\"active\":\"po-chat\"},\"floats\":[{\"stack\":{\"t\":\"stack\",\"panels\":[\"inbox\"],\"active\":\"inbox\"},\"x\":460,\"y\":107,\"w\":520,\"h\":420,\"home\":{\"peers\":[],\"index\":0,\"near\":\"po-chat\",\"side\":\"left\",\"size\":280},\"strip\":{\"id\":\"inbox\",\"edge\":\"left\",\"size\":280,\"peers\":[],\"index\":0}}],\"auto\":[{\"id\":\"changes\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"workspace\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"board\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"spec\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":480}],\"hidden\":[],\"out\":{}}"
}

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
// Where things are on the screen.
const GEOM = `(() => {
  const R = e => { if (!e || !e.isConnected || e.ownerDocument !== document) return null; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden' ? { x: Math.round(r.left), r: Math.round(r.right), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } : null; };
  const d = window.LD && LD.dock;
  const strip = s => [...document.querySelectorAll('#list-dock .dk-strip-' + s + ' .dk-strip-btn[data-dk-auto]')].map(b => b.dataset.dkAuto);
  const sb = id => document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="' + id + '"]');
  const wideEls = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('[hidden], .dk-off') && !(e.closest('#detail-panel') && !document.body.classList.contains('detail-open')); });
  const wide = wideEls.length, wideWhat = wideEls.slice(0, 4).map(e => e.tagName + '#' + e.id + '.' + String(e.className).slice(0, 40) + '@' + Math.round(e.getBoundingClientRect().right));
  const get = k => { try { return localStorage.getItem(k); } catch (e) { return 'x'; } };
  return { W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth, wide, wideWhat,
    ld: !!d, left: d ? strip('left') : [], right: d ? strip('right') : [],
    listMode: d ? d.viewMode('list') : '', inboxMode: d ? d.viewMode('inbox') : '', fly: d ? d.flyOpen() : null,
    listOn: d ? ldOnScreen('list') : false, inboxOn: d ? ldOnScreen('inbox') : false,
    list: R(SW_EL), ldInbox: R(document.getElementById('ld-inbox')), inboxOut: !!d && d.isOut('inbox'),
    pdInbox: !!PD.dock && PD.dock.title('inbox') != null, pdInboxEl: R(PD.els.inbox),
    pdInboxTab: !!document.querySelector('#po-dock [data-dk-tab="inbox"], #po-dock .dk-strip-btn[data-dk-auto="inbox"]'),
    bar: R(document.getElementById('inbox-btn')), ldIbx: document.body.classList.contains('ld-ibx'),
    badge: ((sb('inbox') || document).querySelector('.dk-strip-badge') || { textContent: '' }).textContent.trim(),
    rows: document.querySelectorAll('#ld-inbox a.ibx-link[data-room]').length,
    po: document.body.classList.contains('po-dock'), pid: (PD && PD.pid) || '', narrow: !!PD.dock && PD.dock.narrow(),
    chat: R(PD.els['po-chat']), a5: get('cd-list-dock-a5'), stored: get('cd-list-dock'), pdStored: get('cd-tool-strip') };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  // A page; seed: localStorage keys set (a string) or removed (null) before the page's first script, once.
  const page = async (w, h, mobile, seed) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const clickAt = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
      await clickAt(x, y);
    };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const s = Object.assign({ 'cd-list-dock': null, 'cd-list-dock-a4': null, 'cd-list-dock-a5': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-phone-tabs': null, 'cd-last-conv': null }, seed || {});
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 2 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, clickAt, key, shot, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = async (p, pid) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock && PD.pid === ' + JSON.stringify(pid), 30000);
    await sleep(500);
  };
  const G = p => p.evalIn(GEOM);
  const P = A.ids;
  const LBTN = id => `document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="${id}"]')`;
  try {
    // ---- the default, at each width, a fresh browser
    out.widths = {};
    for (const [w, h] of A.widths) {
      const p = await page(w, h, false);
      await toPo(p, P.Motors);
      await p.until('document.querySelectorAll("#ld-inbox a.ibx-link[data-room]").length > 0', 15000).catch(() => null);
      out.widths[w] = await G(p);
      await p.shot(`left-strip-${w}x${h}`);
      await p.close();
    }
    const p = await page(1440, 900, false);
    await toPo(p, P.Motors);
    await p.until('document.querySelectorAll("#ld-inbox a.ibx-link[data-room]").length > 0', 15000).catch(() => null);
    out.fresh = await G(p);
    // ---- Tasks slides out, a click elsewhere puts it back; Esc too
    await p.click(LBTN('list')); await sleep(500);
    out.listOut = await G(p);
    await p.shot('left-strip-1440x900-tasks-out');
    await p.click('document.querySelector("#ld-inbox")'); await sleep(400);
    out.listBack = await G(p);
    await p.click(LBTN('list')); await sleep(500);
    out.listOut2 = (await G(p)).fly;
    await p.key('Escape'); await sleep(400);
    out.listEsc = await G(p);
    // ---- a Tasks row opens its conversation
    await p.click(LBTN('list')); await sleep(500);
    out.rowRoom = await p.evalIn(`(() => { const r = SW_EL.querySelector('#sw-list .sw-row[data-room], #sw-list .sw-row'); return r ? (r.dataset.room || r.getAttribute('data-id') || r.textContent.trim().slice(0, 60)) : null; })()`);
    await p.click(`SW_EL.querySelector('#sw-list .sw-row[data-room], #sw-list .sw-row')`); await sleep(900);
    out.rowOpened = await p.evalIn(`({ hash: location.hash, po: document.body.classList.contains('po-dock'), pid: PD.pid || '', detail: document.body.classList.contains('detail-open'), fly: LD.dock.flyOpen(), frames: [...document.querySelectorAll('iframe')].filter(f => f.getBoundingClientRect().width > 0).map(f => f.dataset.room || f.src.slice(-60)) })`);
    // ---- the Inbox on Wheels' PO: All has Motors' row, its click opens Motors
    await toPo(p, P.Wheels);
    out.wheels = await G(p);
    await p.click(`document.querySelector('#ld-inbox [data-ibx-scope="all"]')`); await sleep(500);
    out.wheelsAll = await G(p);
    await p.click(`document.querySelector('#ld-inbox a.ibx-link[data-room]')`);
    await p.until('PD.pid === ' + JSON.stringify(P.Motors), 15000).catch(() => null);
    await sleep(500);
    out.ibxOpened = await G(p);
    await p.evalIn(`(() => { const b = document.querySelector('#ld-inbox [data-ibx-scope="project"]'); if (b) b.click(); return 0; })()`); await sleep(300);
    // ---- the modes
    const L = 'LD.dock';
    await p.evalIn(`${L}.setViewMode('inbox', 'unpinned'); 0`); await sleep(400);
    out.unpinned = await G(p);
    await p.click(LBTN('inbox')); await sleep(500);
    out.inboxFly = await G(p);
    await p.shot('left-strip-1440x900-inbox-out');
    await p.key('Escape'); await sleep(300);
    await p.evalIn(`${L}.setViewMode('inbox', 'float'); 0`); await sleep(400);
    out.float = await G(p);
    await p.evalIn(`${L}.setViewMode('inbox', 'pinned'); 0`); await sleep(400);
    out.pinned = await G(p);
    await p.evalIn(`${L}.popOut('inbox'); 0`);
    await p.until(`!!${L}.popWindow('inbox')`, 15000).catch(() => null); await sleep(600);
    out.window = await G(p);
    await p.evalIn(`${L}.popIn('inbox'); 0`); await sleep(500);
    out.popIn = await G(p);
    await p.evalIn(`${L}.setViewMode('inbox', 'unpinned'); ${L}.moveSide('inbox', 'right'); 0`); await sleep(400);
    out.right = await G(p);
    await p.evalIn(`${L}.moveSide('inbox', 'left'); ${L}.moveStrip('inbox', 0, 'left'); 0`); await sleep(400);
    out.first = await G(p);
    // ---- a reload keeps it
    await p.evalIn('location.reload(); 0'); await sleep(1500);
    await p.until('window.ensBooted === true && !!LD.dock', 30000);
    await toPo(p, P.Motors);
    out.reloaded = await G(p);
    // ---- Panels ▾ › Tasks and › Inbox
    const menu = async () => { await p.click('document.querySelector("#bar-here .pd-panels")'); await sleep(250); };
    const rows = () => p.evalIn(`[...document.querySelectorAll('.pd-menu [data-pd-ld]')].map(b => ({ id: b.dataset.pdLd, checked: b.getAttribute('aria-checked') === 'true', disabled: b.disabled, text: b.textContent.trim() }))`);
    await menu(); out.menu1 = await rows();
    await p.click('document.querySelector(".pd-menu [data-pd-ld=list]")'); await sleep(500);
    out.menuList = await G(p); out.menuOpenAfterList = await p.evalIn('!!document.querySelector(".pd-menu")');
    await p.key('Escape'); await sleep(300);
    await menu();
    await p.click('document.querySelector(".pd-menu [data-pd-ld=inbox]")'); await sleep(500);
    out.menuInbox = await G(p);
    await p.key('Escape'); await sleep(300);
    // the Inbox pinned: its row is checked and kept
    await p.evalIn(`${L}.pin('inbox'); 0`); await sleep(300);
    await menu(); out.menu2 = await rows();
    await p.evalIn('pdMenuClose(false); 0');
    // ---- Reset layout
    await p.evalIn(`${L}.setViewMode('inbox', 'float'); ${L}.moveSide('list', 'right'); 0`); await sleep(300);
    out.beforeReset = await G(p);
    await menu();
    await p.click('document.querySelector(".pd-menu [data-pd-reset]")'); await sleep(700);
    out.reset = await G(p);
    // the old layouts, built from this one's own parts
    const L0 = JSON.parse(out.reset.stored || '{}');
    out.layoutSample = L0;
    await p.close();
    // ---- a layout saved before #322: the list hidden (#306), the PO dock's Inbox docked / on its strip
    out.migr = {};
    for (const was of ['pinned', 'unpinned', 'float']) {
      const seeds = { ld: A.old.ld, pd: A.old[was] };
      const m = await page(1440, 900, false, { 'cd-list-dock': seeds.ld, 'cd-list-dock-a4': '1', 'cd-tool-strip': seeds.pd });
      await toPo(m, P.Motors);
      out.migr[was] = await G(m);
      await m.shot(`left-strip-migrated-${was}`);
      await m.close();
    }
    // ---- 800x600: nothing wider than the screen
    const sm = await page(800, 600, false);
    await toPo(sm, P.Motors);
    out.small = await G(sm);
    await sm.shot('left-strip-800x600');
    await sm.close();
    // ---- a phone: no list dock, the PO dock keeps its Inbox tab
    const ph = await page(390, 844, true);
    await toPo(ph, P.Motors).catch(() => null);
    await sleep(600);
    out.phone = await ph.evalIn(`(() => ({ ld: !!LD.dock, narrow: !!PD.dock && PD.dock.narrow(), pdInbox: !!PD.dock && PD.dock.title('inbox') != null, W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth }))()`);
    await ph.shot('left-strip-phone');
    await ph.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'left-strip.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class LeftStripInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ls-", ignore_cleanup_errors=True)
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
        cls.ids = {}
        cls.rooms = {}
        for name in ("Motors", "Wheels", "Alpha"):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
            if name in ("Motors", "Wheels"):
                home = Path(proj.get("home") or proj["path"])
                po = chatroom.create_room(f"{name} PO", members(home))
                dashboard.assign_session_project(po["id"], proj["id"])
                ok, why = dashboard.set_project_po(proj["id"], po["id"])
                assert ok, why
                cls.rooms[name] = po["id"]
                if name == "Motors":
                    cls.po = po["id"]
                    task = chatroom.create_room("Gearbox noise", members(home))
                    dashboard.assign_session_project(task["id"], proj["id"])
                    cls.task = task["id"]
                    text, ids = points.take(chatroom.get_room(po["id"], public=False), "Wider board", to="claude", key="k1")
                    assert ids == ["P1"], ids
                    chatroom.post_message(po["id"], "user", text, to="claude")
                    chatroom.post_message(po["id"], "claude", "Re P1: it is wider now.", to="user")
                    points.sync(po["id"], force=True)
        chatroom.create_room("Loose carriage inspection", members(base))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "po": cls.po, "widths": WIDTHS, "shots": shots, "old": OLD}
        script = base / "left_strip_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=480)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_LS_DUMP"):
            Path(os.environ["ENSEMBLE_LS_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def g(self, k):
        return self.got[k]

    def assertNoOverflow(self, g):
        self.assertLessEqual(g["scrollW"], g["W"])
        self.assertEqual(g["wide"], 0, g["wideWhat"])

    def test_the_default_both_on_the_left_strip(self):
        for w, _ in WIDTHS:
            g = self.got["widths"][str(w)]
            with self.subTest(w=w):
                self.assertEqual(g["left"], ["list", "inbox"], "Tasks first, then Inbox")
                self.assertEqual(g["right"], [])
                self.assertEqual(g["listMode"], "unpinned")
                self.assertIsNone(g["list"], "the list is closed on its strip")
                self.assertEqual(g["inboxMode"], "pinned")
                self.assertEqual(g["ldInbox"]["x"], 44, "docked right of the strip")
                self.assertEqual(g["ldInbox"]["w"], 280)
                self.assertFalse(g["pdInbox"], "the PO dock has no Inbox on a computer")
                self.assertIsNone(g["pdInboxEl"])
                self.assertFalse(g["pdInboxTab"])
                self.assertIsNone(g["bar"], "the bar's Inbox gives way")
                self.assertEqual(g["badge"], "1", "the strip button carries the count")
                self.assertGreater(g["rows"], 0)
                self.assertGreaterEqual(g["chat"]["x"], g["ldInbox"]["r"])
                self.assertLessEqual(g["chat"]["x"] - g["ldInbox"]["r"], 8)
                self.assertNoOverflow(g)

    def test_tasks_slides_out_and_back(self):
        o = self.g("listOut")
        self.assertEqual(o["fly"], "list")
        self.assertEqual(o["list"]["x"], 45, "beside the strip")
        self.assertGreaterEqual(o["chat"]["w"], 360)
        self.assertEqual(o["inboxMode"], "pinned")
        self.assertNoOverflow(o)
        for k in ("listBack", "listEsc"):
            with self.subTest(k=k):
                self.assertIsNone(self.g(k)["fly"])
                self.assertIsNone(self.g(k)["list"])
        self.assertEqual(self.g("listOut2"), "list")

    def test_a_list_row_opens_its_conversation(self):
        o = self.g("rowOpened")
        self.assertTrue(o["detail"], o)
        self.assertIn(self.g("rowRoom"), o["frames"])
        self.assertIsNone(o["fly"], "the list slides back in")

    def test_the_inbox_on_another_projects_po(self):
        w = self.g("wheels")
        self.assertEqual(w["pid"], self.ids["Wheels"])
        self.assertEqual(w["rows"], 0, "nothing waits in Wheels")
        self.assertEqual(w["badge"], "")
        a = self.g("wheelsAll")
        self.assertGreater(a["rows"], 0, "All: Motors' row")
        o = self.g("ibxOpened")
        self.assertEqual(o["pid"], self.ids["Motors"])
        self.assertTrue(o["po"])

    def test_the_modes(self):
        u = self.g("unpinned")
        self.assertEqual(u["inboxMode"], "unpinned")
        self.assertEqual(u["left"], ["list", "inbox"])
        f = self.g("inboxFly")
        self.assertIsNone(f["ldInbox"], "back on its strip")
        self.assertEqual(f["chat"]["x"], 44, "the conversation from the strip")
        self.assertIsNone(f["bar"], "the strip still has it")
        fl = self.g("float")
        self.assertEqual(fl["inboxMode"], "float")
        self.assertIsNotNone(fl["ldInbox"])
        self.assertEqual(fl["chat"]["x"], 44)
        pi = self.g("pinned")
        self.assertEqual(pi["inboxMode"], "pinned")
        self.assertEqual(pi["ldInbox"]["x"], 44)
        wn = self.g("window")
        self.assertEqual(wn["inboxMode"], "window")
        self.assertTrue(wn["inboxOut"])
        self.assertIsNone(wn["ldInbox"])
        self.assertIsNotNone(wn["bar"], "in its window: the bar's Inbox is back")
        self.assertEqual(self.g("popIn")["inboxMode"], "pinned")
        r = self.g("right")
        self.assertEqual((r["left"], r["right"]), (["list"], ["inbox"]))
        self.assertGreater(r["ldInbox"]["x"], r["chat"]["x"])
        self.assertEqual(self.g("first")["left"], ["inbox", "list"])
        for k in ("unpinned", "inboxFly", "float", "pinned", "window", "popIn", "right", "first"):
            with self.subTest(k=k):
                self.assertNoOverflow(self.g(k))

    def test_a_reload_keeps_the_layout(self):
        r = self.g("reloaded")
        self.assertEqual(r["left"], ["inbox", "list"])
        self.assertEqual((r["inboxMode"], r["listMode"]), ("unpinned", "unpinned"))
        self.assertIsNone(r["ldInbox"])
        self.assertEqual(r["chat"]["x"], 44)

    def test_the_panels_menu(self):
        self.assertEqual([(m["id"], m["text"], m["checked"]) for m in self.g("menu1")],
                         [("list", "Tasks", False), ("inbox", "Inbox", False)])
        ml = self.g("menuList")
        self.assertEqual(ml["fly"], "list", "Tasks slides the list out")
        self.assertFalse(self.g("menuOpenAfterList"), "and the menu closes")
        self.assertEqual(self.g("menuInbox")["fly"], "inbox")
        m2 = {m["id"]: m for m in self.g("menu2")}
        self.assertTrue(m2["inbox"]["checked"] and m2["inbox"]["disabled"], "a docked Inbox stays")
        self.assertFalse(m2["list"]["disabled"])

    def test_reset_layout(self):
        b = self.g("beforeReset")
        self.assertEqual((b["left"], b["right"], b["inboxMode"]), (["inbox"], ["list"], "float"))
        r = self.g("reset")
        self.assertEqual(r["left"], ["list", "inbox"])
        self.assertEqual((r["listMode"], r["inboxMode"]), ("unpinned", "pinned"))
        self.assertEqual(r["ldInbox"]["x"], 44)
        self.assertEqual(r["chat"]["x"], self.got["fresh"]["chat"]["x"])

    def test_a_layout_saved_before_moves_onto_the_strip(self):
        want = {"pinned": "pinned", "unpinned": "unpinned", "float": "float"}
        for was, mode in want.items():
            g = self.got["migr"][was]
            with self.subTest(was=was):
                self.assertEqual(g["left"], ["list", "inbox"])
                self.assertEqual(g["listMode"], "unpinned", "the list from hidden onto its strip")
                self.assertEqual(g["inboxMode"], mode, "the Inbox as the PO dock kept it")
                self.assertFalse(g["pdInbox"])
                self.assertEqual(g["a5"], "1")
                self.assertNoOverflow(g)

    def test_800_wide(self):
        g = self.g("small")
        self.assertEqual(g["left"], ["list", "inbox"])
        self.assertGreaterEqual(g["chat"]["w"], 360)
        self.assertNoOverflow(g)

    def test_a_phone_keeps_the_inbox_tab(self):
        g = self.g("phone")
        self.assertFalse(g["ld"], "no list dock on a phone")
        self.assertTrue(g["narrow"])
        self.assertTrue(g["pdInbox"], "the PO dock's Inbox tab")
        self.assertLessEqual(g["scrollW"], g["W"])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
