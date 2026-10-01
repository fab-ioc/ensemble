"""The task chip and its card in the page (#152, the CEO's P111).

In headless Chrome over CDP, against a hub in a thread serving a task's chat
whose messages name tasks by number — this project's #18 and #2, and Dock's
#18 in a message from Dock's PO — at 1280 and at 390 px (touch):

* a chip shows only the number (`#18`, `#2`, `D-18` for the other project's);
* one click (a tap on the phone) opens a one-line card in place: the number,
  the project when it is another's, the full title as a link to the task, its
  column and state, its agents; the card overlaps the balloon under the chip
  and nothing on the page moves;
* Escape closes it and the chip keeps the focus; a press elsewhere closes it;
  Enter on a focused chip opens it;
* on a computer, resting the pointer on a chip opens the card after a moment
  and leaving closes it;
* a double click opens the task (the page goes to it);
* on the phone the card wraps and the title is a finger's height.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node and
Chrome.
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
from tests import chrome_profile  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402

NODE = shutil.which("node")
LONG = "Ensemble needs 15–16: setTitle(id, title) and bodyAttrs mirrored into panel windows"

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// The chips and the card as the page shows them.
const VIEW = `(() => {
  const chips = [...document.querySelectorAll('#msgs a.task-chip')];
  const card = document.querySelector('.task-card');
  const r = e => { const b = e.getBoundingClientRect(); return { left: Math.round(b.left), top: Math.round(b.top), right: Math.round(b.right), bottom: Math.round(b.bottom), width: Math.round(b.width), height: Math.round(b.height) }; };
  return { chips: chips.map(a => ({ text: a.textContent, dot: !!a.querySelector('.tc-dot'), expanded: a.getAttribute('aria-expanded'), task: a.dataset.task, rect: r(a), card: JSON.parse(a.dataset.card) })),
    card: card ? { text: card.textContent, rect: r(card), title: card.querySelector('.tc-title').textContent, href: card.querySelector('.tc-title').getAttribute('href'),
                   titleRect: r(card.querySelector('.tc-title')), proj: (card.querySelector('.tc-proj') || {}).textContent || '', state: (card.querySelector('.tc-state') || {}).textContent || '',
                   agents: (card.querySelector('.tc-agents') || {}).textContent || '', wraps: card.querySelector('.tc-title').getBoundingClientRect().top > card.querySelector('.tc-no').getBoundingClientRect().bottom - 2,
                   z: getComputedStyle(card).zIndex, position: getComputedStyle(card).position } : null,
    msgs: [...document.querySelectorAll('#msgs > *')].filter(m => m.offsetHeight).map(m => r(m)), scrollH: document.documentElement.scrollHeight, scrollX: document.documentElement.scrollWidth - innerWidth,
    focused: document.activeElement && document.activeElement.classList.contains('task-chip') ? document.activeElement.textContent : '',
    coarse: matchMedia('(pointer: coarse)').matches, fine: matchMedia('(hover: hover) and (pointer: fine)').matches, href: location.href };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const go = async (url) => {
      await c.send('Page.navigate', { url }, sessionId);
      await until('typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && document.querySelectorAll("#msgs a.task-chip").length >= 3', 30000);
      await sleep(400);
    };
    const center = async (sel) => evalIn(`(() => { const b = document.querySelector('${sel}').getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; })()`);
    const click = async (sel, count = 1) => {
      const { x, y } = await center(sel);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
      for (let n = 1; n <= count; n++) for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: n }, sessionId);
    };
    const tap = async (sel) => {
      const { x, y } = await center(sel);
      await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }, sessionId);
      await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, sessionId);
    };
    const key = async (k, code, vk) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }, sessionId); };
    return { evalIn, until, shot, go, click, tap, key, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  try {
    for (const [w, h, mob] of [[1280, 800, false], [390, 844, true]]) {
      const p = await page(w, h, mob);
      const o = {};
      await p.go(A.base + '/session?room=' + A.room);
      o.chips = await p.evalIn(VIEW);
      await p.shot(`task-chip-${w}-chips`);
      // One click (a tap on the phone) on #2: the card, in place, over the text under it.
      const two = '#msgs a.task-chip[data-task="' + A.two + '"]';
      if (mob) await p.tap(two); else await p.click(two);
      await sleep(250);
      o.card = await p.evalIn(VIEW);
      await p.shot(`task-chip-${w}-card`);
      // Escape closes it; the chip keeps the focus.
      await p.key('Escape', 'Escape', 27);
      await sleep(150);
      o.escaped = await p.evalIn(VIEW);
      // Enter on the focused chip opens it again; a press elsewhere closes it.
      await p.key('Enter', 'Enter', 13);
      await sleep(150);
      o.entered = await p.evalIn(VIEW);
      await p.evalIn(`document.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: '${mob ? 'touch' : 'mouse'}' })); 0`);
      await sleep(150);
      o.pressedAway = await p.evalIn(VIEW);
      // The other project's chip: its card names the project.
      const dock = '#msgs a.task-chip[data-task="' + A.dock + '"]';
      if (mob) await p.tap(dock); else await p.click(dock);
      await sleep(250);
      o.dock = await p.evalIn(VIEW);
      await p.key('Escape', 'Escape', 27);
      await sleep(150);
      if (!mob) {
        // Resting the pointer on #18 opens its card after a moment; leaving closes it.
        const { x, y } = await p.evalIn(`(() => { const b = document.querySelector('#msgs a.task-chip[data-task="${A.eighteen}"]').getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; })()`);
        await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, undefined).catch(() => {});
        await p.evalIn(`(() => { const a = document.querySelector('#msgs a.task-chip[data-task="${A.eighteen}"]'); a.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' })); return 0; })()`);
        await sleep(300);
        o.hoverEarly = await p.evalIn(VIEW);
        await sleep(500);
        o.hover = await p.evalIn(VIEW);
        await p.evalIn(`(() => { const a = document.querySelector('#msgs a.task-chip[data-task="${A.eighteen}"]'); a.dispatchEvent(new PointerEvent('pointerout', { bubbles: true, pointerType: 'mouse', relatedTarget: document.body })); return 0; })()`);
        await sleep(500);
        o.hoverLeft = await p.evalIn(VIEW);
      }
      // A double click opens the task: the page goes to it.
      await p.click(two, 2);
      await p.until(`location.search.includes('id=${A.two}') || location.search.includes('room=${A.two}')`, 15000);
      o.opened = await p.evalIn('location.pathname + location.search');
      out[w] = o;
      await p.close();
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class ChipAndCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-chip-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        ok, dock, _ = dashboard.register_project("Dock")
        assert ok, dock
        cls.dockProj = dock["id"]
        team = [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer"},
                {"identity": "codex", "agent": "codex", "model": "", "role": "reviewer"}]
        cls.room = cls.task("Chips in chat", cls.proj, team, 18)
        cls.two = cls.task(LONG, cls.proj, team, 2)
        cls.dock = cls.task("Dock's own eighteen", cls.dockProj, team, 18)
        full = chatroom.get_room(cls.two, public=False)
        full["workflow"] = "inprogress"
        chatroom.update_room(full)
        chatroom.post_message(cls.room, "user", "Look at #18 and #2 before you start.")
        chatroom.post_message(cls.room, "claude", "On it, the long one first. This line sits right under the chips so the card has something to overlap.")
        chatroom.post_po_message(cls.room, "claude@room-dockpo", "claude", "Needs 15 and 16 are drafted as Dock #18.",
                                 {"id": "pm-12345678", "name": "Dock's PO", "poKind": "note", "fromProjectId": cls.dockProj,
                                  "fromProjectName": "Dock", "toProjectId": cls.proj, "toProjectName": "Motors", "direction": "received"})
        chatroom.post_message(cls.room, "codex", "Noted. The last balloon, under everything.")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.port}", "room": cls.room,
                "two": cls.two, "eighteen": cls.room, "dock": cls.dock, "shots": shots}
        script = base / "task_chip_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def task(cls, title: str, pid: str, team: list[dict], no: int) -> str:
        rid = chatroom.create_room(title, [dict(m) for m in team])["id"]
        full = chatroom.get_room(rid, public=False)
        full["projectId"] = pid
        chatroom.update_room(full)
        dashboard.assign_session_project(rid, pid)
        chatroom.set_task_number(rid, pid, no)
        return rid

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def _both(self):
        return [(w, self.got[str(w)]) for w in (1280, 390)]

    def test_a_chip_shows_only_the_number(self):
        for w, o in self._both():
            with self.subTest(width=w):
                v = o["chips"]
                self.assertEqual([c["text"] for c in v["chips"]], ["#18", "#2", "D-18"], "this project's by number, Dock's in full")
                self.assertEqual([c["task"] for c in v["chips"]], [self.room, self.two, self.dock])
                self.assertEqual([c["expanded"] for c in v["chips"]], ["false"] * 3)
                self.assertIsNone(v["card"], "no card until a click")
                self.assertEqual(v["chips"][1]["card"]["title"], LONG, "the full title waits in the chip's data")
                self.assertEqual(v["chips"][2]["card"]["project"], "Dock")
                self.assertEqual(v["chips"][0]["card"]["project"], "", "this project's task names no project")
                self.assertFalse(any(c["dot"] for c in v["chips"]), "nothing runs in this hub")
                self.assertLessEqual(v["scrollX"], 0, "no sideways scroll")
                self.assertEqual(v["coarse"], w == 390)

    def test_one_click_opens_the_card_in_place_over_the_text(self):
        for w, o in self._both():
            with self.subTest(width=w):
                before, v = o["chips"], o["card"]
                self.assertIsNotNone(v["card"], "a click opens the card")
                card, chip = v["card"], v["chips"][1]
                self.assertEqual(chip["expanded"], "true")
                self.assertEqual((card["title"], card["href"]), (LONG, f"/session?id={self.two}"), "the title is a link to the task")
                self.assertEqual(card["state"], "In progress")
                self.assertEqual(card["agents"], "claude · codex")
                self.assertEqual(card["proj"], "", "this project's task: no project in the card")
                self.assertTrue(card["text"].startswith("#2"), card["text"])
                self.assertEqual((card["position"], card["z"]), ("fixed", "1200"))
                # In place: just under the chip, left-aligned with it (or pushed in from the edge).
                self.assertLessEqual(abs(card["rect"]["top"] - (chip["rect"]["bottom"] + 4)), 1, (card["rect"], chip["rect"]))
                self.assertLessEqual(card["rect"]["left"], chip["rect"]["left"])
                self.assertGreaterEqual(card["rect"]["left"], 8)
                self.assertLessEqual(card["rect"]["right"], w - 8)
                # Over the text: the balloon under the chip is covered, and nothing moved.
                below = [m for m in v["msgs"] if m["top"] > chip["rect"]["bottom"]]
                self.assertTrue(below, v["msgs"])
                self.assertLess(card["rect"]["top"], below[0]["bottom"])
                self.assertGreater(card["rect"]["bottom"], below[0]["top"])
                self.assertEqual(v["msgs"], before["msgs"], "nothing in the conversation moved")
                self.assertEqual(v["scrollH"], before["scrollH"], "the page did not grow")
                if w == 390:
                    self.assertTrue(card["wraps"], "the phone's card wraps: the title under the number")
                    self.assertGreaterEqual(card["titleRect"]["height"], 44, "a finger's height")
                else:
                    self.assertFalse(card["wraps"], "one line on a computer")
                    self.assertLessEqual(card["rect"]["height"], 40)

    def test_escape_enter_and_a_press_elsewhere(self):
        for w, o in self._both():
            with self.subTest(width=w):
                esc = o["escaped"]
                self.assertIsNone(esc["card"], "Escape closes the card")
                self.assertEqual(esc["chips"][1]["expanded"], "false")
                self.assertEqual(esc["focused"], "#2", "the chip keeps the focus")
                ent = o["entered"]
                self.assertIsNotNone(ent["card"], "Enter on the focused chip opens its card")
                self.assertEqual(ent["card"]["title"], LONG)
                self.assertIsNone(o["pressedAway"]["card"], "a press elsewhere closes it")

    def test_another_projects_chip_names_its_project(self):
        for w, o in self._both():
            with self.subTest(width=w):
                card = o["dock"]["card"]
                self.assertIsNotNone(card)
                self.assertEqual((card["proj"], card["title"], card["href"]), ("Dock", "Dock's own eighteen", f"/session?id={self.dock}"))
                self.assertTrue(card["text"].startswith("D-18Dock"), card["text"])

    def test_hover_opens_the_card_after_a_moment_on_a_computer(self):
        o = self.got["1280"]
        if not o["chips"]["fine"]:
            self.skipTest("this Chrome reports no fine pointer")
        self.assertIsNone(o["hoverEarly"]["card"], "not at once: no flicker on the way past")
        self.assertIsNotNone(o["hover"]["card"], "after half a second")
        self.assertEqual(o["hover"]["card"]["title"], "Chips in chat")
        self.assertIsNone(o["hoverLeft"]["card"], "leaving closes it")

    def test_a_double_click_opens_the_task(self):
        for w, o in self._both():
            with self.subTest(width=w):
                self.assertEqual(o["opened"], f"/session?id={self.two}")


if __name__ == "__main__":
    unittest.main()
