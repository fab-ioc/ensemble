"""Layout A on a phone (#129): one screen at a time.

In headless Chrome over CDP (every launch through tests/chrome_profile.py), at
360, 390 and 430 px as a touch phone, against a hub in a thread serving the
pages, with a project that has a PO (Motors, a task in it), one that has none
(Plain, with enough tasks that the list scrolls) and a room in no project
(Unassigned):

* home is the list: the whole screen under a one-row bar, the same groups as
  the desktop's with Unassigned, every row and control 44px, no sideways
  scroll;
* a row opens its conversation full screen under a two-row bar whose second
  row is the breadcrumb (← project ▾ › #n title › tab), the task's tools as
  the dock's tabs (Chat, Your asks, Changes, Files, Board, Spec);
* a tab is a crumb, and the task's crumb goes back to the Chat;
* the keyboard (--vv-h, as fitVisualViewport sets it) shortens the screen, so
  the message box stays above it;
* ← goes back to the list where you were: its scroll, the row you opened;
* the project cards stay reachable (the list's foot, the project menu's All
  projects), with their Unassigned card, and ← goes back to the list.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
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
from tests import test_middle  # noqa: E402
from tests.test_middle import CHROME, NODE  # noqa: E402

INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
# The launcher (through chrome_profile) and the CDP client, as test_middle has them.
HEAD = test_middle.CDP_JS[:test_middle.CDP_JS.index("// Where the middle is")]

CDP_JS = HEAD + r"""
// What is on screen, and what a finger can reach.
const LOOK = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom) }; };
  const vis = e => !!e && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0 && getComputedStyle(e).visibility !== 'hidden';
  const hd = document.querySelector('header').getBoundingClientRect();
  const sw = document.getElementById('switcher'), dp = document.getElementById('detail-panel');
  const open = document.body.classList.contains('detail-open');
  const chat = open ? dp.querySelector('iframe.dp-session') : null;
  const small = sel => [...document.querySelectorAll(sel)].filter(vis).map(e => [sel, (e.id || e.dataset.crumb || e.textContent.trim()).slice(0, 30), Math.round(e.getBoundingClientRect().width), Math.round(e.getBoundingClientRect().height)])
    .filter(([, , w, h]) => w < 44 - 0.5 || h < 44 - 0.5);
  return { vw: innerWidth, vh: innerHeight, header: Math.round(hd.height), top: Math.round(hd.bottom), open,
    list: vis(sw) ? box(sw) : null, main: vis(document.querySelector('main')),
    listTop: document.getElementById('sw-list').scrollTop,
    groups: [...document.querySelectorAll('#sw-list [data-group]')].map(e => e.dataset.group),
    on: [...document.querySelectorAll('#sw-list .sw-row.on')].map(e => e.dataset.room || e.dataset.po || ''),
    docked: document.body.classList.contains('dp-docked'),
    panel: open ? box(dp) : null, chat: vis(chat) ? box(chat) : null,
    tabs: [...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].filter(vis).map(e => e.textContent.trim()),
    tabsB: (() => { const t = [...document.querySelectorAll('#po-dock .dk-stack > .dk-head')].find(vis); return t ? Math.round(t.getBoundingClientRect().bottom) : 0; })(),
    front: PD.dock ? PD.dock.frontOf('po-chat') : '',
    project: vis(document.getElementById('proj-go')) ? document.querySelector('#proj-go .proj-go-name').textContent : null,
    switchName: vis(document.getElementById('proj-switch-name')) ? document.getElementById('proj-switch-name').textContent : null,
    back: vis(document.getElementById('bar-back')),
    trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].filter(vis).map(e => [e.dataset.crumb, e.textContent, e.tagName === 'BUTTON']),
    cards: [...document.querySelectorAll('#view .proj[data-proj]')].filter(vis).map(e => e.querySelector('h3').textContent),
    unCard: vis(document.querySelector('#view .proj.unassigned')),
    proj: SELECTED_PROJECT, cardsPage: PH_CARDS,
    scrollW: document.documentElement.scrollWidth,
    small: [].concat(small('#sw-list .sw-row'), small('#sw-list summary.sw-ghead'), small('#sw-list .sw-more'), small('#sw-cards'),
      small('.sw-pick select'), small('header button'), small('header a'), small('#bar-crumbs .bar-crumb'), small('#po-dock .dk-tab')) };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 3, mobile: true }, sessionId);
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ') && !!document.querySelector("#sw-list .sw-row")', 30000);
    await sleep(300);
    return { evalIn, until, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const click = (p, sel) => p.evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); e.click(); return 0; })()`);
  const row = room => `#sw-list .sw-row[data-room="${room}"]`;
  const tab = name => `[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].find(e => e.textContent.trim().startsWith(${JSON.stringify(name)}))`;
  const opened = p => p.until('document.body.classList.contains("dp-docked") && !!document.querySelector("#detail-panel iframe.dp-session") && document.querySelectorAll("#po-dock .dk-tab").length > 0', 30000);
  try {
    for (const [w, h] of [[360, 740], [390, 844], [430, 932]]) {
      const p = await page(w, h);
      const k = String(w);
      out['home' + k] = await p.evalIn(LOOK);
      await p.shot(`phone-${w}-list`);
      // Done today unfolded and the list scrolled down, then a row: its conversation.
      await p.evalIn(`(() => { const d = document.querySelector('#sw-list details[data-group="done"]'); d.open = true; return 0; })()`);
      await sleep(200);
      await p.evalIn(`document.getElementById('sw-list').scrollTop = 120; 0`);
      await sleep(100);
      out['scrolled' + k] = await p.evalIn(`document.getElementById('sw-list').scrollTop`);
      await click(p, row(A.task));
      await opened(p); await sleep(500);
      out['task' + k] = await p.evalIn(LOOK);
      await p.shot(`phone-${w}-task`);
      // A tab, and the task's crumb that goes back to the Chat.
      await p.evalIn(`${tab('Changes')}.click(); 0`);
      await sleep(400);
      out['tool' + k] = await p.evalIn(LOOK);
      await p.shot(`phone-${w}-task-changes`);
      await click(p, '#bar-crumbs button[data-crumb="task"]');
      await sleep(300);
      out['toolUp' + k] = await p.evalIn(LOOK);
      // The keyboard: the visible viewport 300px shorter.
      await p.evalIn(`document.documentElement.style.setProperty('--vv-h', '${h - 300}px'); 0`);
      await sleep(300);
      out['kbd' + k] = { ...(await p.evalIn(LOOK)), vvh: h - 300 };
      // Its message box comes into view as keepComposerInView brings it.
      await p.evalIn(`document.querySelector('#detail-panel iframe.dp-session').scrollIntoView({ block: 'end' }); 0`);
      await sleep(200);
      out['kbdIn' + k] = await p.evalIn(LOOK);
      await p.evalIn(`document.documentElement.style.removeProperty('--vv-h'); 0`);
      await sleep(200);
      // ← back to the list, where it was.
      await click(p, '#bar-back');
      await sleep(400);
      out['back' + k] = await p.evalIn(LOOK);
      if (w !== 390) { await p.close(); continue; }
      // The project cards: from the list's foot, with the Unassigned card; ← to the list.
      await click(p, '#sw-cards');
      await sleep(400);
      out.cards = await p.evalIn(LOOK);
      await p.shot('phone-390-cards');
      await click(p, '#bar-back');
      await sleep(300);
      out.cardsBack = await p.evalIn(LOOK);
      // A task in a project without a PO; its project menu's All projects is the cards.
      await click(p, row(A.plainTask));
      await opened(p); await sleep(400);
      out.plainTask = await p.evalIn(LOOK);
      // Its Panels menu: Chat cannot be hidden while the task is in it.
      await click(p, '.pd-panels');
      out.panelsTask = await p.evalIn(`[...document.querySelectorAll('.pd-menu [data-pd-toggle]')].map(e => [e.dataset.pdToggle, e.disabled])`);
      await p.evalIn(`pdMenuClose(false); 0`);
      await click(p, '#proj-switch');
      out.menu = await p.evalIn(`[...document.querySelectorAll('#proj-menu .pm-item')].map(e => [e.dataset.proj || '', Math.round(e.getBoundingClientRect().height)])`);
      await click(p, '#proj-menu .pm-item[data-proj=""]');
      await sleep(400);
      out.menuAll = await p.evalIn(LOOK);
      // A card: its project; ← back to the list, not the cards.
      await p.evalIn(`document.querySelector('#view .proj[data-proj="${A.proj}"]').click(); 0`);
      await sleep(600);
      out.cardProj = await p.evalIn(LOOK);
      await click(p, '#bar-back');
      await sleep(300);
      out.cardProjBack = await p.evalIn(LOOK);
      // An Unassigned row, unfolded: its conversation opens the same way.
      await p.evalIn(`(() => { const d = document.querySelector('#sw-list details[data-group="unassigned"]'); if (d) d.open = true; return 0; })()`);
      await sleep(200);
      await click(p, `#sw-list [data-group="unassigned"] .sw-row[data-room="${A.loose}"]`);
      await opened(p); await sleep(400);
      out.loose = await p.evalIn(LOOK);
      await p.shot('phone-390-unassigned-task');
      // Esc closes it the way ← does: back to the list, not a project's screen.
      await p.evalIn(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); 0`);
      await sleep(300);
      out.looseEsc = await p.evalIn(LOOK);
      // A PO row: its project's screen with the PO's Chat in front.
      await click(p, `#sw-list .sw-row[data-po="${A.proj}"]`);
      await p.until('document.body.classList.contains("po-dock") && !!document.querySelector("#po-panel iframe.po-session:not([hidden])")', 30000);
      await sleep(400);
      out.po = await p.evalIn(LOOK);
      await p.shot('phone-390-po');
      // Chat hidden on the PO screen (its Panels menu allows it there), then a task:
      // the Chat comes back with the task in it.
      await p.evalIn(`PD.dock.setVisible('po-chat', false); 0`);
      await sleep(200);
      out.chatHidden = await p.evalIn(`PD.dock.isVisible('po-chat')`);
      await click(p, '#bar-back');
      await sleep(300);
      await click(p, row(A.task));
      await opened(p); await sleep(400);
      out.afterHidden = await p.evalIn(LOOK);
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
class ThePhone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-phone-", ignore_cleanup_errors=True)
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
        task = chatroom.create_room("Brakes that squeal on a long descent after rain", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
        chatroom.post_message(task["id"], "user", "Why do the brakes squeal?")
        dashboard.assign_session_project(task["id"], cls.proj)
        cls.task = task["id"]
        ok, plain, _ = dashboard.register_project("Plain")
        assert ok, plain
        cls.plain = plain["id"]
        phome = Path(plain.get("home") or plain["path"])
        # Enough rows that the list scrolls on the tallest phone.
        for i in range(24):
            t = chatroom.create_room(f"Plain chore {i + 1}", [{"identity": "claude", "agent": "claude", "cwd": str(phome)}])
            chatroom.post_message(t["id"], "user", f"Chore {i + 1}")
            dashboard.assign_session_project(t["id"], cls.plain)
            if i == 0:
                cls.plain_task = t["id"]
        loose = chatroom.create_room("A question in no project", [{"identity": "claude", "agent": "claude", "cwd": str(base)}])
        chatroom.post_message(loose["id"], "user", "Anything?")
        cls.loose = loose["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**test_middle.chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "plain": cls.plain, "task": cls.task, "plainTask": cls.plain_task,
                "loose": cls.loose, "shots": shots}
        script = base / "phone_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    WIDTHS = ("360", "390", "430")

    def fits(self, g, what):
        self.assertLessEqual(g["scrollW"], g["vw"], f"{what}: no sideways scroll")
        self.assertEqual(g["small"], [], f"{what}: every target is 44px")

    def test_home_is_the_list(self):
        for w in self.WIDTHS:
            g = self.got["home" + w]
            with self.subTest(w=w):
                self.assertIsNotNone(g["list"], "the list shows")
                self.assertEqual((g["list"]["x"], g["list"]["w"]), (0, g["vw"]), "the whole width")
                self.assertEqual(g["list"]["y"], g["top"], "under the bar")
                self.assertEqual(g["list"]["b"], g["vh"], "to the bottom")
                self.assertFalse(g["main"], "the page under it is out of the way")
                self.assertLessEqual(g["header"], 50, "the bar is one row")
                self.assertFalse(g["back"])
                for grp in ("needs", "running", "projects", "unassigned", "done"):
                    self.assertIn(grp, g["groups"])
                self.fits(g, "the list")

    def test_a_row_opens_its_conversation_full_screen(self):
        for w in self.WIDTHS:
            g = self.got["task" + w]
            with self.subTest(w=w):
                self.assertTrue(g["open"] and g["docked"])
                self.assertIsNone(g["list"], "the list steps aside")
                self.assertGreater(g["header"], 90, "the bar has its second row")
                self.assertEqual(g["tabsB"], g["top"] + 45, "the tabs under the bar")
                self.assertEqual(g["panel"]["y"], g["tabsB"], "the conversation under the tabs")
                self.assertEqual((g["panel"]["x"], g["panel"]["w"]), (0, g["vw"]))
                self.assertLessEqual(abs(g["panel"]["b"] - g["vh"]), 1, "to the bottom")
                self.assertIsNotNone(g["chat"], "the conversation shows")
                self.assertEqual(g["tabs"], ["Chat", "Your asks", "Changes", "Files", "Board", "Spec"])
                self.assertEqual(g["front"], "po-chat")
                self.assertTrue(g["back"], "← back to the list")
                self.assertEqual(g["project"], "Motors")
                self.assertEqual(len(g["trail"]), 1)
                self.assertEqual(g["trail"][0][0], "task")
                self.assertIn("Brakes", g["trail"][0][1])
                self.fits(g, "a task")

    def test_a_tab_is_a_crumb_and_the_task_goes_back_to_the_chat(self):
        for w in self.WIDTHS:
            with self.subTest(w=w):
                t, u = self.got["tool" + w], self.got["toolUp" + w]
                self.assertEqual(t["front"], "changes")
                self.assertEqual([x[0] for x in t["trail"]], ["task", "tool"])
                self.assertTrue(t["trail"][0][2], "the task goes up")
                self.assertEqual(t["trail"][1], ["tool", "Changes", False])
                self.fits(t, "a tab")
                self.assertEqual(u["front"], "po-chat")
                self.assertEqual([x[0] for x in u["trail"]], ["task"])

    def test_the_keyboard_does_not_cover_the_message_box(self):
        for w in self.WIDTHS:
            with self.subTest(w=w):
                g, i = self.got["kbd" + w], self.got["kbdIn" + w]
                self.assertLessEqual(abs(g["panel"]["b"] - g["vvh"]), 1, "the screen ends at the keyboard")
                self.assertIsNotNone(i["chat"])
                self.assertLessEqual(i["chat"]["b"], g["vvh"] + 1, "the message box above the keyboard")
                self.assertLessEqual(i["scrollW"], i["vw"])

    def test_back_goes_to_the_list_where_you_were(self):
        for w in self.WIDTHS:
            with self.subTest(w=w):
                g = self.got["back" + w]
                self.assertFalse(g["open"])
                self.assertIsNotNone(g["list"])
                self.assertIsNone(g["proj"])
                self.assertGreater(self.got["scrolled" + w], 0, "the list scrolls")
                self.assertEqual(g["listTop"], self.got["scrolled" + w], "at the same scroll")
                self.assertIn(self.task, g["on"], "the row you opened is marked")
                self.assertLessEqual(g["header"], 50)

    def test_the_project_cards_stay_reachable(self):
        g = self.got["cards"]
        self.assertTrue(g["cardsPage"])
        self.assertIsNone(g["list"])
        self.assertIn("Motors", g["cards"])
        self.assertTrue(g["unCard"], "the Unassigned card")
        self.assertTrue(g["back"])
        self.assertEqual(g["switchName"], "All projects")
        self.fits(g, "the cards")
        b = self.got["cardsBack"]
        self.assertIsNotNone(b["list"], "← goes back to the list")
        self.assertFalse(b["cardsPage"])
        self.assertIn(self.task, b["on"], "the row you last opened is still marked")

    def test_the_project_menu(self):
        g = self.got["plainTask"]
        self.assertEqual(g["project"], "Plain")
        self.assertEqual(g["tabs"][0], "Chat", "a project without a PO: the task's tabs all the same")
        self.assertTrue(all(h >= 44 for _, h in self.got["menu"]), self.got["menu"])
        panels = dict(self.got["panelsTask"])
        self.assertTrue(panels["po-chat"], "Chat stays while the task is in it")
        self.assertFalse(panels["changes"], "a tool can still be hidden")
        m = self.got["menuAll"]
        self.assertFalse(m["open"], "the task gives way")
        self.assertTrue(m["cardsPage"] and m["unCard"], "All projects is the cards")
        c = self.got["cardProj"]
        self.assertEqual(c["proj"], self.proj)
        self.assertIsNotNone(self.got["cardProjBack"]["list"], "← from a project is the list")

    def test_an_unassigned_row(self):
        g = self.got["loose"]
        self.assertTrue(g["open"] and g["docked"])
        self.assertIsNotNone(g["chat"])
        self.assertEqual([x[0] for x in g["trail"]], ["task"])
        self.fits(g, "an Unassigned task")
        e = self.got["looseEsc"]
        self.assertFalse(e["open"], "Esc closes the task")
        self.assertIsNotNone(e["list"], "and goes back to the list")
        self.assertIsNone(e["proj"])

    def test_a_po_row_opens_its_projects_screen(self):
        g = self.got["po"]
        self.assertEqual(g["proj"], self.proj)
        self.assertEqual(g["front"], "po-chat")
        self.assertEqual(g["tabs"][0], "Chat")
        self.fits(g, "a PO")

    def test_a_chat_hidden_earlier_comes_back_with_a_task(self):
        self.assertFalse(self.got["chatHidden"], "Chat was hidden on the PO screen")
        g = self.got["afterHidden"]
        self.assertTrue(g["open"] and g["docked"])
        self.assertEqual(g["tabs"][0], "Chat", "the Chat tab is back")
        self.assertIsNotNone(g["chat"], "and the task's conversation shows")


class TheWiring(unittest.TestCase):
    def test_a_phone_opens_a_task_in_the_dock(self):
        self.assertIn("function pdTask() { return pdTaskWanted() && !!PD.dock; }", INDEX)
        self.assertIn("function swOn() { try { return !isPhone() || phList(); }", INDEX)
        self.assertIn("const PD_KEYS = { desk: 'cd-tool-strip', phone: 'cd-phone-tabs' };", INDEX)

    def test_a_task_closing_by_itself_goes_back_to_the_list(self):
        self.assertIn("function closeTask() {\n  if (phoneNow()) goHome(); else closeDetail();\n}", INDEX)
        for line in ("if (SELECTED_SID) closeTask();", "if (archiving && SELECTED_SID === sid) closeTask();",
                     "if (SELECTED_SID === sid) closeTask();", "if (SELECTED_SID === row.sessionId) closeTask();"):
            self.assertIn(line, INDEX)

    def test_a_wider_screen_drops_the_cards_page(self):
        self.assertIn("if (!isPhone()) PH_CARDS = false;", INDEX)

    def test_chat_cannot_be_hidden_under_an_open_task(self):
        self.assertIn("(isPhone() && !(id === 'po-chat' && a === 'hide' && pdTask()))", INDEX)


if __name__ == "__main__":
    unittest.main()
