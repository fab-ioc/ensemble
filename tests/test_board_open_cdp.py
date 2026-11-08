"""#316: a board card opens in place by its own small button, in headless
Chrome over CDP against a throwaway hub in a thread (its own state in a temp
dir), at 1728x1117, 1280x800 and 390x844:

* the toggle is a real button with aria-expanded; a click opens the card, a
  second closes it; Enter on the focused toggle does the same;
* the open card shows the whole title, the goal, where the task stands (who,
  when, and "before the last N commits" when the branch moved since), the
  facts line and a link to the latest report; a task with no goal shows its
  spec's first sentence, marked as from the spec;
* nothing in the open card runs out of it, and the card stays in its column;
* the toggle opens no task and starts no drag;
* the card stays open over a repaint (the same nodes when nothing changed)
  and over a reload.

ENS_SHOTS_DIR, when set, receives a screenshot of the card closed and open at
each width. Skipped without Node or Chrome.
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
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import attention  # noqa: E402
import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import task_brief  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
TITLE = ("Board cards open in place to show the full title, the goal and where the task stands, "
         "with a facts line and the latest report")
GOAL = "Let anyone grasp a project from its board: each card opens to say what the task is for and where it is."
STATE = "The hub side and tools are done; the page part is being built and tested."

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const i = ++id; waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  try {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const R = JSON.stringify(A.rooms), P = JSON.stringify(A.project);
    const card = k => `document.querySelector('.card[data-room="' + ${R}.${k} + '"]')`;
    const show = `(() => { if (typeof renderRows !== 'function' || !PROJECTS || !ALL_ROWS || !ALL_ROWS.length) return false;
      SELECTED_PROJECT = ${P}; PROJECT_TAB = 'tasks'; if (VIEW_MODE !== 'board') setViewMode('board'); else { BOARD_SIG = ''; renderRows(); }
      if (!window.__spy) { window.__spy = 1; window.__opened = 0; openDetail = () => { window.__opened++; }; }
      return Object.values(${R}).every(rid => document.querySelector('.card[data-room="' + rid + '"] .cexp')); })()`;
    const wait = async () => { const t = Date.now(); while (Date.now() - t < 30000) { try { if (await evalIn(show)) return true; } catch (e) {} await sleep(250); } return false; };
    // What a card shows, and whether anything in it runs out of it or its column.
    const read = k => `(() => { const c = ${card(k)}; if (!c) return null;
      c.scrollIntoView({ block: 'center', inline: 'center' });
      const b = c.querySelector('.cexp'), br = c.querySelector('.cbrief'), cr = c.getBoundingClientRect();
      const col = c.closest('.col').getBoundingClientRect(), t = c.querySelector('.ctitle');
      const out = [];
      for (const e of c.querySelectorAll('*')) { const r = e.getBoundingClientRect();
        if (r.width && (r.left < cr.left - 0.5 || r.right > cr.right + 0.5)) out.push(e.className || e.tagName); }
      const line = s => { const e = br && br.querySelector(s); return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null; };
      const a = br && br.querySelector('.cb-rep a.msg-link');
      return { open: c.classList.contains('open'), aria: b.getAttribute('aria-expanded'), tag: b.tagName, type: b.type,
        label: b.getAttribute('aria-label'), brief: !!br, titleWhole: t.scrollHeight <= t.clientHeight + 1,
        title: t.textContent.trim(), goalWhole: (() => { const g = br && br.querySelector('.cb-goal .cb-txt'); return !!g && g.scrollHeight <= g.clientHeight + 1; })(), goal: line('.cb-goal'), state: line('.cb-state'), facts: line('.cb-facts'),
        rep: a ? { text: a.textContent.trim(), href: a.getAttribute('href') } : null,
        outOfCard: out, inCol: cr.left >= col.left - 0.5 && cr.right <= col.right + 0.5,
        btnW: b.getBoundingClientRect().width, inView: cr.left >= 0 && cr.right <= innerWidth, opened: window.__opened,
        stored: (() => { try { return JSON.parse(localStorage.getItem('cd-board-open') || '[]'); } catch (e) { return 'x'; } })() }; })()`;
    const click = async k => {
      const p = await evalIn(`(() => { const b = ${card(k)}.querySelector('.cexp'); b.scrollIntoView({ block: 'center', inline: 'center' });
        const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased'])
        await send('Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: 'left', clickCount: type === 'mouseMoved' ? 0 : 1 }, sessionId);
      await sleep(150);
    };
    const shot = async (k, name) => {
      if (!A.shots) return;
      const r = await evalIn(`(() => { const c = ${card(k)}; c.scrollIntoView({ block: 'center', inline: 'center' });
        const port = boardPort().port; if (port) port.scrollLeft += c.closest('.col').getBoundingClientRect().left - port.getBoundingClientRect().left - 8;
        const r = c.getBoundingClientRect();
        return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height }; })()`);
      const pad = 12, clip = { x: Math.max(0, r.x - pad), y: Math.max(0, r.y - pad), width: r.w + 2 * pad, height: r.h + 2 * pad, scale: 1 };
      const { data } = await send('Page.captureScreenshot', { format: 'png', clip }, sessionId);
      fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(data, 'base64'));
    };
    const got = {};
    const sizes = [['desk', 1728, 1117, false], ['laptop', 1280, 800, false], ['phone', 390, 844, true]];
    await evalIn('1');
    for (const [name, w, h, mobile] of sizes) {
      const g = got[name] = {};
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile }, sessionId);
      await send('Page.navigate', { url: A.base + '/' }, sessionId);
      await sleep(300);
      g.shown = await wait();
      if (!g.shown) continue;
      await evalIn(`(() => { try { localStorage.removeItem('cd-board-open'); } catch (e) {} BOARD_OPEN = null; BOARD_SIG = ''; renderRows(); return 1; })()`);
      await sleep(100);
      g.closed = await evalIn(read('main'));
      await shot('main', `${name}-${w}-closed`);
      await click('main');
      g.open = await evalIn(read('main'));
      g.spec = (await click('spec'), await evalIn(read('spec')));
      await shot('main', `${name}-${w}-open`);
      // A poll that changes nothing keeps the very same nodes; a full repaint keeps it open.
      g.repaint = await evalIn(`(async () => { const c = ${card('main')}; const br = c.querySelector('.cbrief'); br.__mark = 1;
        const goal = br.querySelector('.cb-goal'); goal.__mark = 1;
        renderRows(); await new Promise(r => setTimeout(r, 50));
        const c2 = ${card('main')}, b2 = c2.querySelector('.cbrief');
        const out = { sameBrief: !!(b2 && b2.__mark), sameGoal: !!(b2 && b2.querySelector('.cb-goal').__mark), open: c2.classList.contains('open') };
        BOARD_SIG = ''; renderRows(); await new Promise(r => setTimeout(r, 50));
        const c3 = ${card('main')}; out.afterFull = c3.classList.contains('open') && !!c3.querySelector('.cbrief');
        return out; })()`);
      // No drag from the toggle; a drag from the card body still starts.
      g.drag = await evalIn(`(() => { const c = ${card('main')}, b = c.querySelector('.cexp');
        b.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
        const e1 = new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }); c.dispatchEvent(e1);
        const fromBtn = { prevented: e1.defaultPrevented, room: _dragRoom, dragging: c.classList.contains('dragging') };
        c.querySelector('.crow2').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
        const e2 = new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }); c.dispatchEvent(e2);
        const fromBody = { prevented: e2.defaultPrevented, room: _dragRoom };
        c.dispatchEvent(new DragEvent('dragend', { bubbles: true })); return { fromBtn, fromBody }; })()`);
      await send('Page.reload', {}, sessionId);
      await sleep(300);
      g.reShown = await wait();
      g.reloaded = await evalIn(read('main'));
      await click('main');
      g.closedAgain = await evalIn(read('main'));
      if (name === 'desk') {
        // The keyboard: Enter on the focused toggle opens it, Space closes it.
        const key = async (k, code, text) => {
          await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, windowsVirtualKeyCode: k === 'Enter' ? 13 : 32, text }, sessionId);
          await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: k === 'Enter' ? 13 : 32 }, sessionId);
          await sleep(150);
        };
        await evalIn(`(${card('main')}.querySelector('.cexp').focus(), 1)`);
        await key('Enter', 'Enter', '\r');
        g.keyOpen = await evalIn(`[${card('main')}.classList.contains('open'), document.activeElement && document.activeElement.className]`);
        await key(' ', 'Space', ' ');
        g.keyClosed = await evalIn(`${card('main')}.classList.contains('open')`);
      }
      g.width = await evalIn('innerWidth'); g.scroll = await evalIn('document.documentElement.scrollWidth');
    }
    console.log(JSON.stringify(got));
  } finally { ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class ACardOpensInPlace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-bo-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        now = time.time()
        cls.STATE_AT = now - 3 * 3600
        commits = {"ahead": 5, "times": [int(now - 600), int(now - 1200), int(now - 1800),
                                         int(now - 4 * 3600), int(now - 5 * 3600)]}
        for p in [
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
            mock.patch.object(dashboard, "_read_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: None),
            mock.patch.object(dashboard.ptyrun, "death_for", lambda pid: None),
            mock.patch.object(dashboard.ptyrun, "list_sessions", lambda: []),
            mock.patch.object(dashboard, "room_po_id", lambda room, *a, **k: ""),
            mock.patch.object(dashboard, "branch_commits", lambda root, *a, **k: commits),
            mock.patch.object(dashboard, "branch_changes",
                              lambda root, *a, **k: {"add": 512, "del": 4, "files": 6}),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        for cache in (attention._SUMMARY_CACHE, attention._ANALYSIS, attention._FIRST_SEEN):
            cache.clear()
            cls.addClassCleanup(cache.clear)
        cls.addClassCleanup(setattr, attention, "_result", (0.0, None))
        attention._result = (0.0, None)
        asks.forget()
        cls.addClassCleanup(asks.forget)
        ok, proj, _ = dashboard.register_project("Garage")
        assert ok, proj
        home = str(Path(proj.get("home") or proj["path"]))

        def task(title, spec):
            rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": home}])["id"]
            chatroom.post_message(rid, "user", "Please look at it")
            full = chatroom.get_room(rid, public=False)
            full.update(mode="solo", launched=True, workflow="inprogress", spec=spec,
                        workspace={"mode": "worktree", "branch": "sess/x"}, cwd=home)
            chatroom.update_room(full)
            dashboard.assign_session_project(rid, proj["id"])
            return rid

        rooms = {"main": task(TITLE, "## Ask\nMake the board readable."),
                 "spec": task("Sort the gears", "## Ask\n- **Sort** the gears by size. Then paint them.")}
        task_brief.set_goal(chatroom.get_room(rooms["main"], public=False), GOAL)
        chatroom.record_report(rooms["main"], "claude", "update", "Half done")
        task_brief.set_state(rooms["main"], STATE, "claude", at=cls.STATE_AT)
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        script = base / "open_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        shots = os.environ.get("ENS_SHOTS_DIR", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "project": proj["id"],
                "base": f"http://127.0.0.1:{server.server_address[1]}", "rooms": rooms, "shots": shots}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.rooms = rooms
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def each(self):
        for name, w in (("desk", 1728), ("laptop", 1280), ("phone", 390)):
            g = self.got[name]
            self.assertTrue(g.get("shown"), f"{name}: board shown {g}")
            self.assertEqual(g["width"], w)
            yield name, g

    def test_the_toggle_is_a_button_and_opens_and_closes(self):
        for name, g in self.each():
            c, o, a = g["closed"], g["open"], g["closedAgain"]
            self.assertEqual((c["tag"], c["type"], c["aria"], c["open"], c["brief"]),
                             ("BUTTON", "button", "false", False, False), f"{name}: {c}")
            self.assertTrue(c["label"], c)
            self.assertGreaterEqual(c["btnW"], 24, c)
            self.assertEqual((o["aria"], o["open"], o["brief"]), ("true", True, True), f"{name}: {o}")
            self.assertEqual((a["aria"], a["open"], a["brief"]), ("false", False, False), f"{name}: {a}")
            self.assertNotIn(self.rooms["main"], a["stored"], f"{name}: closed is forgotten: {a}")
            self.assertIn(self.rooms["main"], o["stored"], f"{name}: open is kept: {o}")

    def test_the_open_card_says_it_all(self):
        for name, g in self.each():
            o = g["open"]
            self.assertTrue(o["titleWhole"], f"{name}: {o}")
            self.assertTrue(o["goalWhole"], f"{name}: the goal is shown whole: {o}")
            self.assertIn(TITLE, o["title"])
            self.assertEqual(o["goal"], "Goal: " + GOAL, f"{name}: {o}")
            self.assertTrue(o["state"].startswith("Where we are: " + STATE), o["state"])
            self.assertIn("claude, 3h ago", o["state"])
            self.assertIn("before the last 3 commits", o["state"])
            for bit in ("In progress", "5 commits ahead of main", "+512 −4", "active "):
                self.assertIn(bit, o["facts"], f"{name}: {o['facts']}")
            self.assertEqual(o["rep"]["text"], "Latest report: an update", o)
            self.assertIn("room=" + self.rooms["main"], o["rep"]["href"])
            self.assertIn("&msg=", o["rep"]["href"])

    def test_without_a_goal_the_spec_speaks(self):
        for name, g in self.each():
            s = g["spec"]
            self.assertEqual(s["goal"], "Goal: Sort the gears by size. (from the spec)", f"{name}: {s}")
            self.assertEqual(s["state"], "Where we are: not reported yet", s)
            self.assertIsNone(s["rep"], s)

    def test_nothing_runs_out_of_the_card_or_the_page(self):
        for name, g in self.each():
            for k in ("open", "spec"):
                self.assertEqual(g[k]["outOfCard"], [], f"{name} {k}: {g[k]}")
                self.assertTrue(g[k]["inCol"], f"{name} {k}: {g[k]}")
                self.assertTrue(g[k]["inView"], f"{name} {k}: {g[k]}")
            if name == "phone":
                self.assertLessEqual(g["scroll"], 391, "no page scroll sideways on a phone")

    def test_the_toggle_opens_no_task_and_starts_no_drag(self):
        for name, g in self.each():
            self.assertEqual(g["closedAgain"]["opened"], 0, f"{name}: openDetail ran")
            d = g["drag"]
            self.assertEqual(d["fromBtn"], {"prevented": True, "room": None, "dragging": False}, f"{name}: {d}")
            self.assertEqual(d["fromBody"], {"prevented": False, "room": self.rooms["main"]}, f"{name}: {d}")

    def test_it_stays_open_over_a_repaint_and_a_reload(self):
        for name, g in self.each():
            self.assertEqual(g["repaint"], {"sameBrief": True, "sameGoal": True, "open": True, "afterFull": True},
                             f"{name}: {g['repaint']}")
            self.assertTrue(g["reShown"], name)
            r = g["reloaded"]
            self.assertEqual((r["open"], r["aria"], r["brief"]), (True, "true", True), f"{name}: {r}")

    def test_the_keyboard_opens_and_closes_it(self):
        g = self.got["desk"]
        self.assertEqual(g["keyOpen"], [True, "cexp"], g.get("keyOpen"))
        self.assertFalse(g["keyClosed"])


if __name__ == "__main__":
    unittest.main()
