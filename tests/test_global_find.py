"""#161 (GitHub issue 6): search from wherever you are, and the list's filter.

index.html's "Global find" block runs in Node: the drop-down's groups and
counts, its status line (what is still being searched, why there are fewer),
the order the arrows walk, and the list filter's reading of a row.

Then headless Chrome over CDP, against a hub in a thread with a project (its
PO and two tasks), a session in no project, and a Claude transcript no task
holds:

* typed in the top bar, on a PO screen and at home alike, the results drop
  down under the box: Tasks (by title, and by spec), Past sessions (found in
  a transcript), Messages, each with its count; the board is not narrowed;
* the arrows move the chosen result, Enter opens it, Esc closes the drop-down
  and keeps the query; a click on a past session opens it, a message opens
  its chat;
* typing fast keeps one request in flight;
* the list's filter narrows its rows as you type, says so, and Clear brings
  them back;
* on a phone the results take the screen under the bar, each a finger's height.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
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
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import global_search  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


UNIT_JS = r"""
%s
const taskNoText = r => (r && r.no ? 'ED-' + r.no : '');
const log = {};
const res = {
  q: 'brakes', conversations: 'done',
  tasks: { count: 3, items: [
    { roomId: 'room-a', no: 7, title: 'Brakes that squeal', project: 'Motors', where: 'title', hits: 0, snippet: '' },
    { roomId: 'room-b', no: 8, title: 'Paint', project: 'Motors', where: 'spec', hits: 0, snippet: 'check the brakes first' },
  ] },
  sessions: { count: 1, items: [{ sessionId: 's-1', agent: 'codex', cwd: 'C:\\work\\garage', title: '', hits: 4, snippet: 'brakes <b>' }] },
  messages: { count: 1, items: [{ roomId: 'room-po', msgId: 'm1', from: 'user', to: 'claude',
    fromLabel: 'sam', toLabel: 'PO', title: 'Motors · PO', po: true, snippet: 'the brakes' }] },
};
log.items = findItems(res).map(findKey);
log.none = findItems(null);
log.status = [
  findStatus('b', null, ''), findStatus('brakes', null, 'quick'), findStatus('brakes', res, 'deep'),
  findStatus('brakes', res, 'done'), findStatus('brakes', { ...res, conversations: 'partial' }, 'done'),
  findStatus('zz', { tasks: { count: 0 }, sessions: { count: 0 }, messages: { count: 0 } }, 'done'),
  findStatus('zz', { tasks: { count: 0 }, sessions: { count: 0 }, messages: { count: 0 } }, 'deep'),
  findStatus('zz', null, 'error'),
];
log.html = findHtml('brakes', res, 'done', 1);
log.htmlDeep = findHtml('brakes', res, 'deep', -1);
// The list's filter.
const e = (key, row, more) => ({ key, row, it: null, project: 'Motors', ...more });
const g = {
  needs: [e('room-a', { roomId: 'room-a', no: 7, label: 'Brakes that squeal', cwd: 'C:\\p\\brakes_that_squeal\\repo' })],
  running: [e('room-b', { roomId: 'room-b', no: 8, label: 'Paint the doors', cwd: 'C:\\p\\paint\\repo' })],
  projects: [e('room-po', { roomId: 'room-po', label: 'PO talk' }, { po: true })],
  unassigned: [e('s-1', { sessionId: 's-1', first: 'torque the wheel nuts', cwd: 'C:\\garage' }, { project: '' })],
  done: [],
};
const keys = gg => Object.fromEntries(Object.entries(gg).map(([k, l]) => [k, l.map(x => x.key)]));
log.f = {
  same: swFilterGroups(g, '  ') === g,
  title: keys(swFilterGroups(g, 'brakes')),
  num: keys(swFilterGroups(g, '#8')),
  ref: keys(swFilterGroups(g, 'ED-7')),
  proj: keys(swFilterGroups(g, 'motors')),
  po: keys(swFilterGroups(g, 'motors PO')),
  folder: keys(swFilterGroups(g, 'garage')),
  or: keys(swFilterGroups(g, 'paint OR torque')),
  count: swCount(swFilterGroups(g, 'motors')),
};
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class TheDropDownsInside(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Global find: begin")
        src = "\n".join([re.search(r"^const esc = .*$", INDEX, re.M).group(0), fn(INDEX, "function parseSearchQuery("),
                         fn(INDEX, "function haystackMatches("), fn(INDEX, "function highlight("),
                         fn(INDEX, "function rowHaystack("), INDEX[i:INDEX.index("// ---- Global find: end", i)]])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "find.cjs"
            script.write_text(UNIT_JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_order_the_arrows_walk(self):
        self.assertEqual(self.r["items"], ["tasks:room-a", "tasks:room-b", "sessions:s-1", "messages:room-po/m1"])
        self.assertEqual(self.r["none"], [])

    def test_the_status_line_says_why(self):
        self.assertEqual(self.r["status"], [
            "Type two letters or more.", "Searching…", "Searching conversations…", "",
            "Conversations: stopped at the time limit, some were not read.",
            "No results for “zz”.", "Nothing in titles, specs or chats yet. Searching conversations…",
            "Search failed. Type again to retry.",
        ])

    def test_groups_with_counts_and_the_chosen_row(self):
        h = self.r["html"]
        self.assertIn('<div class="fd-status" role="status" hidden></div>', h, "nothing to say once done")
        for k, name, n in (("tasks", "Tasks", 3), ("sessions", "Past sessions", 1), ("messages", "Messages", 1)):
            self.assertIn(f'id="fd-h-{k}"><span>{name}</span><span class="fd-n">{n}</span>', h)
        self.assertIn("Showing the first 2 of 3", h, "a group cut short says so")
        self.assertIn('<div class="fd-row on" role="option" id="fd-1" data-i="1" aria-selected="true">', h)
        self.assertEqual(h.count('aria-selected="true"'), 1)
        self.assertIn('<span class="tno">#7</span> <mark class="match">Brakes</mark> that squeal', h)
        self.assertIn("Motors · in its spec", h)
        self.assertIn('check the <mark class="match">brakes</mark> first', h)
        self.assertIn("Codex · garage · 4 matches", h)
        self.assertIn("&lt;b&gt;", h, "a snippet is text, never markup")
        self.assertIn("sam → PO", h)
        self.assertIn("Searching conversations…", self.r["htmlDeep"])

    def test_the_list_filter_reads_what_a_row_shows(self):
        f = self.r["f"]
        self.assertTrue(f["same"], "no filter: the groups as they were")
        self.assertEqual(f["title"]["needs"], ["room-a"])
        self.assertEqual(f["title"]["running"], [])
        self.assertEqual(f["num"]["running"], ["room-b"])
        self.assertEqual(f["ref"]["needs"], ["room-a"])
        self.assertEqual(f["proj"], {"needs": ["room-a"], "running": ["room-b"], "projects": ["room-po"],
                                     "unassigned": [], "done": []})
        self.assertEqual(f["po"]["projects"], ["room-po"])
        self.assertEqual(f["folder"]["unassigned"], ["s-1"])
        self.assertEqual((f["or"]["running"], f["or"]["unassigned"]), (["room-b"], ["s-1"]))
        self.assertEqual(f["count"], 3)

    def test_the_page_wires_it(self):
        self.assertIn('<div id="find-pop" role="listbox"', INDEX)
        self.assertIn('role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="find-pop"', INDEX)
        self.assertIn('<input id="sw-filter" type="search"', INDEX)
        self.assertIn("FIND.timer = setTimeout(findRun, 250);", INDEX, "typing is debounced")
        self.assertIn("swFilterGroups(all, q)", fn(INDEX, "function swRender("))


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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.events = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown') this.events.push(m.params.exceptionDetails);
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const KEYS = { ArrowDown: 40, ArrowUp: 38, Enter: 13, Escape: 27 };
// What the drop-down shows.
const POP = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), b: Math.round(b.bottom), r: Math.round(b.right) }; };
  const pop = document.getElementById('find-pop'), s = document.getElementById('search');
  return { shown: !pop.hidden, pop: box(pop), box: box(document.querySelector('.search-wrap')), bar: box(document.querySelector('header')),
    expanded: s.getAttribute('aria-expanded'), active: s.getAttribute('aria-activedescendant'), value: s.value, phase: FIND.phase,
    status: (pop.querySelector('.fd-status:not([hidden])') || {}).textContent || '',
    heads: [...pop.querySelectorAll('.fd-head')].map(h => h.textContent),
    rows: [...pop.querySelectorAll('.fd-row')].map(r => ({ id: r.id, on: r.classList.contains('on'), t: r.querySelector('.fd-t').textContent, h: Math.round(r.getBoundingClientRect().height) })),
    sid: SELECTED_SID || null, open: document.body.classList.contains('detail-open'), project: SELECTED_PROJECT || '',
    boardCards: [...document.querySelectorAll('#po-dock-host .card, #view .card')].length,
    vw: innerWidth, scrollW: document.documentElement.scrollWidth };
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
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(120); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      if (mobile) {
        for (const type of ['touchStart', 'touchEnd']) await c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] }, sessionId);
      } else for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: KEYS[k] || 0 }, sessionId); };
    const type = async (sel, text) => {
      await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.focus(); e.select && e.select(); return 0; })()`);
      for (const ch of text) { await c.send('Input.insertText', { text: ch }, sessionId); await sleep(40); }
    };
    const clear = sel => evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.value = ''; e.dispatchEvent(new Event('input')); return 0; })()`);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    return { evalIn, until, shot, click, key, type, clear, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const done = (p, q) => p.until(`FIND.phase === 'done' && !FIND.busy && !!FIND.res && FIND.res.q === ${JSON.stringify(q)}`, 30000);
  try {
    const p = await page(1440, 900);
    // Count the requests in flight, all the time.
    await p.evalIn(`(() => { window.__fl = { now: 0, max: 0, asks: [] }; const f = window.fetch;
      window.fetch = (u, o) => { const s = String(u); if (!s.startsWith('/api/find?') || s.includes('cancel=1')) return f(u, o);
        __fl.now++; __fl.max = Math.max(__fl.max, __fl.now); __fl.asks.push(s); return f(u, o).finally(() => { __fl.now--; }); }; return 0; })()`);
    // A PO screen: the project's.
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('!!document.querySelector(".card")', 20000).catch(() => null);
    out.cardsBefore = (await p.evalIn(POP)).boardCards;
    await p.type('#search', 'squeal');
    await done(p, 'squeal');
    await p.until('document.querySelectorAll("#find-pop .fd-head").length >= 2', 5000).catch(() => null);
    out.po = await p.evalIn(POP);
    out.flight = await p.evalIn('({ max: __fl.max, asks: __fl.asks.length, deep: __fl.asks.filter(s => s.includes("deep=1")).length })');
    await p.shot('find-1440-po');
    await p.key('ArrowDown'); out.down = await p.evalIn(POP);
    await p.key('ArrowUp'); out.up = await p.evalIn(POP);
    await p.key('Escape'); out.esc = await p.evalIn(POP);
    await p.key('ArrowDown'); out.reopen = await p.evalIn(POP);
    await p.key('Enter');
    await p.until('!!SELECTED_SID', 10000).catch(() => null); await sleep(300);
    out.enter = await p.evalIn(POP);
    // Home (no project, no task): the same.
    await p.evalIn('if (SELECTED_SID) closeDetail(); SELECTED_PROJECT = ""; renderRows(); 0'); await sleep(300);
    await p.clear('#search');
    await p.type('#search', 'torque');
    await done(p, 'torque');
    out.home = await p.evalIn(POP);
    if (!out.home.heads.some(h => h.startsWith('Past sessions'))) console.error('HOME ' + JSON.stringify(out.home) + ' ' + JSON.stringify(await p.evalIn('FIND')));
    await p.shot('find-1440-home');
    await p.click('#find-pop .fd-group:has(#fd-h-sessions) .fd-row');
    await p.until('!!SELECTED_SID', 10000).catch(() => null); await sleep(300);
    out.session = await p.evalIn(POP);
    // A message opens its chat.
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(200);
    await p.clear('#search');
    await p.type('#search', 'thinking');
    await done(p, 'thinking');
    out.msgPop = await p.evalIn(POP);
    await p.evalIn('window.__om = []; const _om = openMsgLink; openMsgLink = (...a) => { __om.push(a); return _om(...a); }; 0');
    await p.click('#find-pop .fd-group:has(#fd-h-messages) .fd-row');
    await sleep(500);
    out.msg = await p.evalIn('({ sid: SELECTED_SID || null, asked: __om, pop: !document.getElementById("find-pop").hidden })');
    // Typing fast: one request at a time.
    await p.evalIn('if (SELECTED_SID) closeDetail(); __fl.max = 0; 0');
    await p.clear('#search');
    for (const word of ['br', 'bra', 'brak', 'brakes', 'brakes sq']) { await p.clear('#search'); await p.type('#search', word); await sleep(320); }
    await done(p, 'brakes sq');
    out.fast = await p.evalIn('({ max: __fl.max, value: FIND.q, heads: [...document.querySelectorAll("#find-pop .fd-head")].map(h => h.textContent) })');
    // Enter at once after typing a new query opens that query's result, not one of the last query's.
    await p.evalIn('window.__od = []; const _od = openDetail; openDetail = (...a) => { __od.push(a[0]); return _od(...a); }; ' +
                   'window.__ot = []; const _ot = openTaskLink; openTaskLink = (...a) => { __ot.push(a[0]); return _ot(...a); }; 0');
    await p.type('#search', 'torque');    // typed over 'brakes sq', whose results are still there
    await p.key('Enter');
    await p.until('__od.length + __ot.length > 0', 30000).catch(() => null); await sleep(300);
    out.enterFresh = await p.evalIn('({ details: __od, tasks: __ot })');
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(200);
    await p.key('Escape');
    // The list's filter.
    await p.type('#sw-filter', 'brakes');
    await sleep(200);
    const LIST = `(() => ({ rows: [...document.querySelectorAll('#sw-list .sw-row')].map(r => r.dataset.room), note: document.getElementById('sw-filtered').hidden ? '' : document.getElementById('sw-filtered').textContent.trim(),
      h: Math.round(document.getElementById('sw-filter').getBoundingClientRect().height) }))()`;
    out.filter = await p.evalIn(LIST);
    await p.shot('list-filter-1440');
    await p.click('#sw-filtered .sw-clear'); await sleep(200);
    out.cleared = await p.evalIn(LIST);
    await p.type('#sw-filter', 'loose'); await sleep(200);
    out.filterUn = await p.evalIn(LIST);
    out.errors = c.events.map(e => (e.exception && e.exception.description || e.text || '').slice(0, 300));
    await p.close();
    // A phone.
    const q = await page(430, 932, true);
    await q.evalIn('SELECTED_PROJECT = ""; renderRows(); 0'); await sleep(500);
    await q.click('#search-open'); await sleep(200);
    await q.type('#search', 'squeal');
    await done(q, 'squeal');
    out.phone = await q.evalIn(POP);
    await q.shot('find-430-phone');
    await q.key('Enter');
    await q.until('!!SELECTED_SID', 10000).catch(() => null); await sleep(300);
    out.phoneOpen = await q.evalIn(`({ ...${POP}, searchOpen: document.body.classList.contains('search-open') })`);
    await q.evalIn('closeDetail(); SELECTED_PROJECT = ""; renderRows(); 0'); await sleep(400);
    out.phoneFilter = await q.evalIn(`(() => { const b = document.getElementById('sw-filter').getBoundingClientRect(); return { h: Math.round(b.height), w: Math.round(b.width), font: getComputedStyle(document.getElementById('sw-filter')).fontSize }; })()`);
    out.phoneErrors = c.events.map(e => (e.exception && e.exception.description || e.text || '').slice(0, 300));
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class FindInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-gf-", ignore_cleanup_errors=True)
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
            mock.patch.dict(dashboard._FIND_DEEP_CACHE, clear=True),
            mock.patch.dict(global_search._ROOM_CACHE, clear=True),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        task = chatroom.create_room("Brakes that squeal", [{"identity": "claude", "agent": "claude", "cwd": str(home / "brakes")}])
        chatroom.post_message(task["id"], "user", "Look at the front calipers")
        dashboard.assign_session_project(task["id"], cls.proj)
        cls.task = task["id"]
        paint = chatroom.create_room("Paint the doors", [{"identity": "claude", "agent": "claude", "cwd": str(home / "paint")}])
        room = chatroom.get_room(paint["id"], public=False)
        room["spec"] = "Mask the brakes before you paint; they squeal when sprayed."
        chatroom.update_room(room)
        dashboard.assign_session_project(paint["id"], cls.proj)
        cls.paint = paint["id"]
        loose = chatroom.create_room("A loose idea", [{"identity": "claude", "agent": "claude", "cwd": str(base)}])
        chatroom.post_message(loose["id"], "user", "Just thinking aloud")
        cls.loose = loose["id"]
        # A conversation of your own, in no task, that only its transcript holds the word of.
        slug = base / "transcripts" / "C--garage"
        slug.mkdir()
        cls.own = "0f0f0f0f-1111-2222-3333-444444444444"
        line = {"type": "user", "cwd": "C:\\garage", "sessionId": cls.own,
                "message": {"role": "user", "content": "Which torque for the wheel nuts?"}}
        (slug / f"{cls.own}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task, "shots": shots}
        script = base / "gf_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_results_drop_down_under_the_box_on_a_po_screen(self):
        g = self.got["po"]
        self.assertTrue(g["shown"])
        self.assertEqual(g["expanded"], "true")
        self.assertEqual(g["heads"][0], "Tasks2", "by its title and by another's spec")
        titles = [r["t"] for r in g["rows"]]
        self.assertTrue(titles[0].endswith("Brakes that squeal"), titles)
        self.assertTrue(titles[1].endswith("Paint the doors"), titles)
        self.assertGreaterEqual(g["pop"]["y"], g["box"]["b"], "under the box")
        self.assertLessEqual(g["pop"]["r"], g["vw"])
        self.assertLessEqual(g["scrollW"], g["vw"], "no sideways scroll")
        self.assertEqual(g["rows"][0]["on"], True, "the first result is the one Enter opens")
        self.assertEqual(g["active"], g["rows"][0]["id"])
        self.assertEqual(self.got["cardsBefore"], g["boardCards"], "the board is not narrowed")

    def test_both_halves_one_at_a_time(self):
        f = self.got["flight"]
        self.assertEqual(f["max"], 1, "one request in flight")
        self.assertGreaterEqual(f["deep"], 1, "the deep half ran")
        self.assertEqual(self.got["fast"]["max"], 1, "typing fast keeps one request in flight")
        self.assertEqual(self.got["fast"]["value"], "brakes sq")

    def test_keys(self):
        self.assertTrue(self.got["down"]["rows"][1]["on"])
        self.assertTrue(self.got["up"]["rows"][0]["on"])
        esc = self.got["esc"]
        self.assertFalse(esc["shown"])
        self.assertEqual((esc["expanded"], esc["value"]), ("false", "squeal"), "Esc closes; the query stays")
        self.assertTrue(self.got["reopen"]["shown"], "an arrow brings it back")
        e = self.got["enter"]
        self.assertEqual(e["sid"], self.task, "Enter opens the first result")
        self.assertTrue(e["open"])
        self.assertFalse(e["shown"])
        self.assertEqual(self.got["enterFresh"], {"details": [self.own], "tasks": []},
                         "Enter right after typing waits for the new query's result")

    def test_from_home_a_past_session_found_in_its_transcript(self):
        h = self.got["home"]
        self.assertTrue(h["shown"])
        self.assertEqual(h["project"], "")
        self.assertIn("Past sessions1", h["heads"])
        self.assertEqual(self.got["session"]["sid"], self.own, "a click opens it")
        self.assertTrue(self.got["session"]["open"])

    def test_a_message_opens_its_chat(self):
        self.assertIn("Messages1", self.got["msgPop"]["heads"])
        m = self.got["msg"]
        self.assertEqual(m["sid"], self.loose)
        self.assertEqual([a[0] for a in m["asked"]], [self.loose], "its chat, at the message")
        self.assertTrue(all(a[1] for a in m["asked"]))
        self.assertFalse(m["pop"])

    def test_the_list_filter(self):
        f = self.got["filter"]
        self.assertEqual(f["rows"], [self.task])
        self.assertRegex(f["note"], r"^Filtered: 1 of \d+ shown Clear$")
        self.assertEqual(f["h"], 32)
        self.assertGreater(len(self.got["cleared"]["rows"]), 1, "Clear brings every row back")
        self.assertEqual(self.got["cleared"]["note"], "")
        self.assertEqual(set(self.got["filterUn"]["rows"]), {self.loose}, "Unassigned opens for its match")

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])
        self.assertEqual(self.got["phoneErrors"], [])

    def test_a_phone(self):
        g = self.got["phone"]
        self.assertTrue(g["shown"])
        self.assertEqual((g["pop"]["x"], g["pop"]["w"]), (0, 430), "the whole width")
        self.assertGreaterEqual(g["pop"]["y"], g["bar"]["b"] - 1, "under the bar")
        self.assertGreaterEqual(g["pop"]["b"], 931, "to the bottom")
        self.assertTrue(all(r["h"] >= 44 for r in g["rows"]), g["rows"])
        o = self.got["phoneOpen"]
        self.assertEqual(o["sid"], self.task)
        self.assertFalse(o["shown"] or o["searchOpen"])
        self.assertEqual(self.got["phoneFilter"]["h"], 44)
        self.assertEqual(self.got["phoneFilter"]["font"], "16px")


if __name__ == "__main__":
    unittest.main()
