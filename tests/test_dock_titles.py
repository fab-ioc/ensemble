"""#154: Dock v0.11.0 vendored; a panel's title and the page's body classes
through the library, not through Ensemble's own code.

Dock v0.11.0 (Ensemble's needs 15 and 16, raised in #148) brings
``setTitle(id, title)`` / ``title(id)`` and the ``bodyAttrs`` option. The page
uses them and its two stopgaps went:

* the conversation panel is named after what it shows (``pdSyncTitles``: the
  open task "#12 The title", else "PO chat"), and a chat panel after its task;
  the library puts the title on the tab, the title bar, the ▾ list, the ⋯ menu's
  name and an open window's ``document.title`` (``popTitle``, asked again),
  and Ensemble's Panels menu reads ``dock.title(id)``. A rename of the task
  follows at the next refresh. ``pdPopTitle`` (the page setting the window's
  ``document.title`` itself) is gone;
* ``bodyAttrs: ['class']`` copies the page's body classes into every panel
  window and keeps them in step, the window's own (``dk-popwin``) kept. The
  page's own MutationObserver mirror in ``onEveryWindow`` is gone.

In headless Chrome over CDP, against a hub in a thread, on a project with a PO
and two tasks: the conversation's tab, its Panels-menu row and its window's
title read "PO chat", then the task's name once a task is in the middle, then
the renamed task after a rename through the hub; a chat panel's tab follows its
task's rename too; a body class added to the page reaches the window and leaves
it again, the window's own class staying; on a phone (the narrow dock) the
conversation's tab keeps reading "Chat" so the tab row needs no scrolling, and
a computer's shows the task again; the page threw no exception meanwhile
(CDP ``Runtime.exceptionThrown``). Skipped without Node or Chrome.
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
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
DOCK_JS = (ROOT / "static" / "dock" / "src" / "dock.js").read_text(encoding="utf-8")
PANELS_MENU_JS = (ROOT / "static" / "dock" / "src" / "panels-menu.js").read_text(encoding="utf-8")
VERSION = (ROOT / "static" / "dock" / "VERSION").read_text(encoding="utf-8")


class TheLibraryDoesIt(unittest.TestCase):
    def test_dock_v0_11_0_is_vendored(self):
        self.assertRegex(VERSION, r"^fab-ioc/dock v0\.11\.0 4e01e8d03db07a93234295fdb53c4022f2a74eea \(tag v0\.11\.0, 2026-10-01\)")
        self.assertIn("setTitle: (id, title) => setTitleNow(id, title),", DOCK_JS)
        self.assertIn("title: (id) => (byId.has(id) ? byId.get(id).title : null),", DOCK_JS)
        self.assertIn("bodyAttrs = [],", DOCK_JS)
        self.assertIn("function syncBody(id, pop) {", DOCK_JS)
        # The upgrade note: mountPanelsMenu names panels by dock.title(id).
        self.assertIn("const title = (dock.title && dock.title(p.id)) || p.title;", PANELS_MENU_JS)

    def test_the_page_uses_set_title_and_body_attrs(self):
        self.assertIn("bodyAttrs: ['class'],", INDEX)
        self.assertIn("popTitle: p => `${p.title} · ${(projectById(PD.pid) || {}).name || 'Ensemble'}`,", INDEX)
        self.assertIn("function pdSyncTitles() {", INDEX)
        self.assertIn("d.setTitle('po-chat', d.narrow() ? PD_TITLES['po-chat'] : pdChatTitle())", INDEX)
        self.assertIn("if (r) { try { d.setTitle(id, pdRowTitle(r)); } catch (err) {} }", INDEX)
        # When: the middle changes, and after each refresh of the rows (a rename).
        self.assertIn("if (PD.dock) pdPopChat(ph.ownerDocument !== document);\n  pdSyncTitles();", INDEX)
        self.assertIn("if (SELECTED_SID) renderDetail();\n    pdSyncTitles();", INDEX)
        # Ensemble's own Panels menu names a panel as the dock does now.
        self.assertIn("${esc(PD.dock.title(id) || PD_TITLES[id])}</button>", INDEX)

    def test_neither_stopgap_is_left(self):
        for gone in ("function pdPopTitle()", "pdPopTitle();", "w.document.title = t", "const mirrored = new Set();",
                     "bodyMo.observe(document.body", "bodyMo.disconnect()", "p.id === 'po-chat' ? pdChatTitle() : p.title",
                     "document.body, { attributes: true, attributeFilter: ['class'] }"):
            self.assertEqual(INDEX.count(gone), 0, gone)


CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.exceptions = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); }
      // The page's own exceptions and unhandled rejections (Runtime.enable), kept for the report.
      if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; this.exceptions.push((d.text || '') + ' ' + (d.exception && d.exception.description || '') + ' @' + (d.url || '') + ':' + d.lineNumber); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// What a panel is called everywhere: the dock's title, its tab (text and tooltip) in this page and in its window, its
// row in Ensemble's Panels menu, and its window's document.title.
const NAMES = id => `(() => {
  const d = PD.dock, w = d.popWindow(${JSON.stringify(id)});
  const tab = doc => { const t = doc.querySelector('.dk-tab[data-dk-tab="${id}"]'); return t ? { text: t.textContent, tip: t.title } : null; };
  const menu = document.createElement('div'); menu.innerHTML = pdMenuHtml();
  const row = menu.querySelector('[data-pd-toggle="${id}"]');
  return { title: d.title(${JSON.stringify(id)}), tab: tab(document), menuRow: row ? row.textContent : null,
           win: w ? { title: w.document.title, tab: tab(w.document), body: w.document.body.className } : null };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const click = async (sel) => {
    const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
  };
  const [TA, TB] = A.tasks;
  const rowTitle = r => `pdRowTitle(ALL_ROWS.find(x => x.roomId === ${JSON.stringify(r)}))`;
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(TA) + ')', 30000);
    await evalIn(`(() => { try { ['cd-list-dock', 'cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
      SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    await until('!!PD.dock && document.body.classList.contains("po-dock") && !!SW_EL.querySelector(".sw-row") && PD.restored', 30000);
    await sleep(500);
    out.poChat = await evalIn(NAMES('po-chat'));
    out.titles = { a: await evalIn(rowTitle(TA)), b: await evalIn(rowTitle(TB)) };
    // The conversation in a window of its own.
    await evalIn('PD.dock.setViewMode("po-chat", "window"); 0');
    await until('!!PD.dock.popWindow("po-chat") && PD.els["po-chat"].ownerDocument !== document && !!PD.els["po-chat"].querySelector("iframe.pd-own")', 15000);
    await sleep(500);
    out.poChatOut = await evalIn(NAMES('po-chat'));
    // A body class of the page reaches the window and leaves it again; the window keeps its own.
    out.bodyCls = await evalIn(`(async () => {
      const w = PD.dock.popWindow('po-chat'), b = w.document.body;
      const was = b.className;
      document.body.classList.add('probe-theme'); await new Promise(r => setTimeout(r, 300));
      const there = b.classList.contains('probe-theme');
      document.body.classList.remove('probe-theme'); await new Promise(r => setTimeout(r, 300));
      const gone = !b.classList.contains('probe-theme');
      return { was, there, gone, own: b.classList.contains('dk-popwin'), mid: b.classList.contains('mid'), after: b.className };
    })()`);
    // A task into the middle: its window is named after it.
    await click(`.sw-row[data-room="${TA}"]`);
    await until(`pdTask() && SELECTED_SID && rowBySid(SELECTED_SID).roomId === ${JSON.stringify(TA)} && PD.dock.title('po-chat') === ${rowTitle(TA)}`, 15000);
    await sleep(500);
    out.task = await evalIn(NAMES('po-chat'));
    // Another task's chat as a panel of its own.
    await evalIn(`pdChatOpen(${JSON.stringify(TB)}); 0`);
    await until(`PD.rt.has('chat:' + ${JSON.stringify(TB)})`, 10000);
    await sleep(300);
    out.chatPanel = await evalIn(NAMES('chat:' + TB));
    // Both renamed through the hub: the next refresh of the rows re-titles their panels.
    await evalIn(`(async () => { for (const [room, label] of [[${JSON.stringify(TA)}, 'Brakes, renamed'], [${JSON.stringify(TB)}, 'Wipers, renamed']]) {
      const r = ALL_ROWS.find(x => x.roomId === room);
      await fetch('/api/label/' + encodeURIComponent(r.sessionId), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label }) }); }
      await refresh({ now: true }); return 0; })()`);
    await until(`PD.dock.title('po-chat').includes('Brakes, renamed') && PD.dock.title('chat:' + ${JSON.stringify(TB)}).includes('Wipers, renamed')`, 20000);
    await sleep(500);
    out.renamed = { po: await evalIn(NAMES('po-chat')), chat: await evalIn(NAMES('chat:' + TB)), a: await evalIn(rowTitle(TA)), b: await evalIn(rowTitle(TB)) };
    // The window closed: the panel keeps its title, hidden (Window mode), and shown again.
    await evalIn('PD.dock.popWindow("po-chat").close(); 0');
    await until('!PD.dock.isOpenOut("po-chat")', 10000);
    await evalIn('PD.dock.setViewMode("po-chat", "pinned"); 0');
    await until('PD.els["po-chat"].ownerDocument === document && PD.dock.isVisible("po-chat")', 10000);
    await sleep(300);
    out.back = await evalIn(NAMES('po-chat'));
    // A phone (the narrow dock, the task still in the middle): the conversation's tab keeps reading "Chat", so
    // the fixed tabs all fit the 390px row without scrolling; a computer again: the task's name is back.
    await evalIn(`pdChatUnpanel(${JSON.stringify(TB)}); 0`);
    await until(`!PD.rt.has('chat:' + ${JSON.stringify(TB)})`, 10000);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 3, mobile: true }, sessionId);
    await until('PD.dock.narrow() && PD.dock.title("po-chat") === "Chat"', 15000);
    await sleep(500);
    out.phone = await evalIn(`(() => { const n = ${NAMES('po-chat')}; const tabs = PD_ROOT.querySelector('.dk-tabs');
      const wraps = [...tabs.querySelectorAll('.dk-tab-wrap')];
      const R = tabs.getBoundingClientRect();
      return { ...n, narrow: PD.dock.narrow(), task: !!pdTask(), tabs: wraps.map(w => (w.querySelector('.dk-tab') || {}).textContent),
               fits: tabs.scrollWidth <= tabs.clientWidth + 1, row: Math.round(tabs.clientWidth), need: Math.round(tabs.scrollWidth),
               rects: wraps.map(w => { const r = w.getBoundingClientRect(); return [(w.querySelector('.dk-tab') || {}).textContent, Math.round(r.left - R.left), Math.round(r.right - R.left)]; }) }; })()`);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    await until(`!PD.dock.narrow() && PD.dock.title('po-chat') === ${rowTitle(TA)}`, 15000);
    await sleep(300);
    out.wideAgain = await evalIn(NAMES('po-chat'));
    out.errors = c.exceptions;
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TitlesAndBodyClassesInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-dt-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()

        def resumed(self, room_full, text="", to="", key="", quiet=False):
            if text:
                chatroom.post_message(room_full["id"], "user", text, to=to or "")
            return {"resumed": False, "queued": 0, "delivered": 1 if text else 0}

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
            mock.patch.object(dashboard.Handler, "_resume_room", resumed),
            mock.patch.object(dashboard, "push_label_to_iterm", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        chatroom.post_message(po["id"], "user", "Hello PO", to="claude")
        cls.tasks = []
        team = [{"identity": "claude", "agent": "claude", "cwd": str(home)}, {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        for title in ("Brakes that squeal", "Wipers that smear"):
            t = chatroom.create_room(title, team)
            chatroom.post_message(t["id"], "user", "About: " + title)
            dashboard.assign_session_project(t["id"], cls.proj)
            cls.tasks.append(t["id"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "tasks": cls.tasks}
        script = base / "dt_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    # The panel's title everywhere: the dock's, its tab (in this page, or in its window while it is out: the
    # page has no tab for it then), its row in Ensemble's Panels menu, and its window's document.title.
    def everywhere(self, names, title, window=True, menu=True):
        self.assertEqual(names["title"], title)
        tab = names["win"]["tab"] if window else names["tab"]
        if window:
            self.assertIsNone(names["tab"], "the panel is in its window, not on the page")
        self.assertEqual(tab["text"], title, "the tab")
        self.assertTrue(tab["tip"].startswith(title), tab["tip"])
        # (Ensemble's Panels menu lists the fixed panels; a chat or file panel has no row in it.)
        self.assertEqual(names["menuRow"], title if menu else None, "Ensemble's Panels menu row")
        if window:
            self.assertEqual(names["win"]["title"], f"{title} · Motors", "the window's document.title")

    def test_the_conversation_is_named_po_chat_until_a_task_is_in_it(self):
        g = self.got
        self.assertIsNone(g["poChat"]["win"])
        self.everywhere(g["poChat"], "PO chat", window=False)
        self.everywhere(g["poChatOut"], "PO chat")

    def test_a_task_in_the_middle_names_the_tab_the_menu_row_and_the_window(self):
        g = self.got
        self.assertTrue(g["titles"]["a"].endswith("Brakes that squeal"), g["titles"])
        self.everywhere(g["task"], g["titles"]["a"])

    def test_a_rename_through_the_hub_follows_at_the_next_refresh(self):
        g = self.got
        self.assertTrue(g["renamed"]["a"].endswith("Brakes, renamed"), g["renamed"])
        self.assertTrue(g["renamed"]["b"].endswith("Wipers, renamed"), g["renamed"])
        self.everywhere(g["renamed"]["po"], g["renamed"]["a"])
        self.everywhere(g["renamed"]["chat"], g["renamed"]["b"], window=False, menu=False)
        # The chat panel was named after its task from the start.
        self.everywhere(g["chatPanel"], g["titles"]["b"], window=False, menu=False)

    def test_the_title_survives_the_window_closing_and_the_panel_coming_back(self):
        g = self.got
        self.assertIsNone(g["back"]["win"])
        self.everywhere(g["back"], g["renamed"]["a"], window=False)

    def test_a_body_class_reaches_the_window_and_leaves_it_again(self):
        b = self.got["bodyCls"]
        self.assertTrue(b["there"], b)
        self.assertTrue(b["gone"], b)
        self.assertTrue(b["own"], "the window keeps dk-popwin")
        self.assertTrue(b["mid"], "the page's own classes (mid) were copied at open")
        self.assertIn("dk-popwin", self.got["poChatOut"]["win"]["body"])
        self.assertIn("po-dock", self.got["poChatOut"]["win"]["body"])

    def test_a_phone_keeps_chat_on_the_conversation_tab_so_the_row_stays_short(self):
        p = self.got["phone"]
        self.assertTrue(p["narrow"], p)
        self.assertTrue(p["task"], "the task is still in the middle on the phone")
        self.everywhere(p, "Chat", window=False)
        for name in ("Chat", "Your asks", "Board", "Spec"):
            self.assertIn(name, p["tabs"], p["tabs"])
        # The tab is "Chat"'s width, not a title's 160px, and Board is on screen without scrolling (the six fixed
        # tabs need some 374px of a 390px phone's 331px row since #129: Spec's end was off it before this too).
        rect = {name: (left, right) for name, left, right in p["rects"]}
        self.assertLessEqual(rect["Chat"][1] - rect["Chat"][0], 60, p["rects"])
        self.assertLessEqual(rect["Board"][1], p["row"] + 1, f"Board off the row: {p['rects']} in {p['row']}px")
        self.assertLess(p["need"], p["row"] + 60, f"the tab row needs {p['need']}px in {p['row']}px, tabs {p['tabs']}")
        # A computer again: the conversation's name is back.
        self.everywhere(self.got["wideAgain"], self.got["renamed"]["a"], window=False)

    def test_the_page_threw_no_exception_meanwhile(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
