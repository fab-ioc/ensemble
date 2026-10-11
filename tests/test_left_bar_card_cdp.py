"""#201: a task number under a PO in the left bar opens the task card on rest.

In headless Chrome over CDP, against a hub in a thread (its own state in a temp
dir), at 1728x1117 with a mouse and 390x844 as a touch phone. Motors has a PO
and a task in review, so the PO's row in the list names it (#7):

* no tooltip on the number or on anything of the line under the PO;
* resting the pointer on the number opens the task card after about 500 ms,
  beside the list (never over the number), with the title and column right;
* the list drawn again (forced, then the 2 s poll) keeps the same card in the
  same place, on the number drawn in its place; a change to the task shows in
  it at the next redraw;
* Esc closes it; keyboard focus on the number opens it after the same moment,
  and Esc then leaves the focus there without opening it again;
* a click opens the task and no card, also when the pointer rests on after;
* the card's title opens the task;
* on the phone a long press opens the card and not the task, a tap elsewhere
  closes it, a tap opens the task.

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

HEAD = test_middle.CDP_JS[:test_middle.CDP_JS.index("// Where the middle is")]
TITLE = "Brakes that squeal on a long descent after rain"

CDP_JS = HEAD + r"""
async function main() {
  const { ch, ws } = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [width, height, touch] of [[1728, 1117, false], [390, 844, true]]) {
      const R = out[width] = {};
      const { browserContextId } = await c.send('Target.createBrowserContext');
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank', browserContextId });
      const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, s);
      await c.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: touch }, s);
      await c.send('Emulation.setTouchEmulationEnabled', { enabled: touch, maxTouchPoints: 5 }, s);
      if (touch) await c.send('Emulation.setEmitTouchEventsForMouse', { enabled: true, configuration: 'mobile' }, s);
      const ev = async expression => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
      const until = async (expression, n = 180) => { for (let i = 0; i < n; i++) { try { if (await ev(expression)) return true; } catch {} await sleep(100); } return false; };
      const shot = async name => { if (!A.shots) return; const { data } = await c.send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, `left-bar-card-${width}-${name}.png`), Buffer.from(data, 'base64')); };
      await c.send('Page.navigate', { url: A.base + '/' }, s);
      const TK = `document.querySelector('#sw-list .sw-tk[data-room="${A.task}"]')`;
      R.listed = await until(`!!${TK}`);
      if (!R.listed) { R.list = await ev(`document.querySelector('#sw-list').innerHTML.slice(0, 3000)`); continue; }
      await sleep(300);
      const box = e => `(() => { const b = (${e}).getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2, l: b.left, t: b.top, r: b.right, b: b.bottom }; })()`;
      const CARD = `document.querySelector('.task-card')`;
      const card = () => ev(`(() => { const k = ${CARD}; if (!k) return null; const b = k.getBoundingClientRect(), q = x => (k.querySelector(x) || {}).textContent || '';
        return { no: q('.tc-no'), title: q('.tc-title'), state: q('.tc-state'), why: q('.tc-why'), proj: q('.tc-proj'), mark: k.__mark || '', beside: k.classList.contains('tc-beside'),
                 href: k.querySelector('.tc-title').getAttribute('href'), dataTask: k.querySelector('.tc-title').hasAttribute('data-task'),
                 rect: { l: Math.round(b.left), t: Math.round(b.top), r: Math.round(b.right), b: Math.round(b.bottom) } }; })()`);
      const opened = () => ev(`SELECTED_SID || ''`);
      const shut = () => ev(`closeDetail ? (closeDetail(), 0) : 0`).catch(() => 0);
      // What the line under the PO carries: no title anywhere on it.
      R.titles = await ev(`(() => { const t = ${TK}.closest('.sw-tasks'); return [...t.querySelectorAll('[title]')].map(e => e.outerHTML.slice(0, 80)).concat(t.hasAttribute('title') ? ['sw-tasks'] : []); })()`);
      R.tkTitle = await ev(`${TK}.hasAttribute('title')`);
      const tk = await ev(box(TK));
      R.tk = tk;
      R.list = await ev(box(`document.getElementById('switcher')`));

      if (!touch) {
        const mv = (x, y) => c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, s);
        const click = async (x, y) => { await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, s); await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, s); };
        const away = { x: Math.round(width * 0.6), y: Math.round(height * 0.6) };
        await mv(away.x, away.y); await sleep(200);
        // Rest on the number: the card after about 500 ms.
        await mv(tk.x, tk.y);
        const t0 = Date.now();
        R.at300 = !!(await sleep(300), await card());
        await until(`!!${CARD}`, 40);
        R.after = Date.now() - t0;
        R.card = await card();
        R.expanded = await ev(`${TK}.getAttribute('aria-expanded')`);
        await shot('rest');
        // The list drawn again (forced): the same card, in the same place, on the new number.
        await ev(`${CARD}.__mark = 'kept'; const b = document.querySelector('#sw-list'); b._sig = ''; swRender(); 0`);
        R.redrawn = { card: await card(), newTk: await ev(`!${TK}.__seen && ${TK}.getAttribute('aria-expanded')`) };
        // Then the page's own poll (every 2 s) with the pointer still on it.
        await ev(`${TK}.__seen = 1; 0`);
        await sleep(4500);
        R.polled = await card();
        // The task changes: its card says so at the next redraw.
        await ev(`(() => { const r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.task)}); r.label = 'Renamed while open'; swRender(); })(); 0`);
        R.live = await card();
        await ev(`(() => { const r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.task)}); r.label = ${JSON.stringify(A.title)}; swRender(); })(); 0`);
        // Esc closes it.
        await c.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, s);
        await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, s);
        await sleep(100);
        R.esc = await card();
        // A click opens the task, and no card comes after it.
        await mv(away.x, away.y); await sleep(200);
        await mv(tk.x, tk.y); await sleep(150);
        await click(tk.x, tk.y);
        await sleep(900);
        R.click = { opened: await opened(), card: await card() };
        await shut(); await until(`!SELECTED_SID`, 30);
        await sleep(300);
        // The card's title opens the task.
        await mv(away.x, away.y); await sleep(200);
        const tk2 = await ev(box(TK));
        await mv(tk2.x, tk2.y);
        await until(`!!${CARD}`, 30);
        const ti = await ev(box(`${CARD}.querySelector('.tc-title')`));
        await mv(ti.x, ti.y); await sleep(150);
        R.onTitle = !!(await card());
        await click(ti.x, ti.y);
        await sleep(600);
        R.titleClick = { opened: await opened(), card: await card() };
        await shut(); await until(`!SELECTED_SID`, 30);
        await mv(away.x, away.y); await sleep(400);
        // Keyboard: focus on the number after a key, the card after the same moment; Esc keeps the focus there.
        await c.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Shift', code: 'ShiftLeft', windowsVirtualKeyCode: 16 }, s);
        await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Shift', code: 'ShiftLeft', windowsVirtualKeyCode: 16 }, s);
        await ev(`${TK}.focus(); 0`);
        R.keyAt200 = !!(await sleep(200), await card());
        R.keyCard = (await until(`!!${CARD}`, 20)) ? await card() : null;
        await c.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, s);
        await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, s);
        await sleep(900);
        R.keyEsc = { card: await card(), focus: await ev(`document.activeElement === ${TK}`) };
      } else {
        const touchAt = (type, x, y) => c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] }, s);
        // A long press: the card, not the task.
        await touchAt('touchStart', tk.x, tk.y);
        await sleep(800);
        await touchAt('touchEnd', tk.x, tk.y);
        await sleep(400);
        R.long = { card: await card(), opened: await opened() };
        await shot('long-press');
        // A tap elsewhere closes it (on the list's empty head, nothing that opens).
        const blank = await ev(box(`document.querySelector('#sw-list .sw-ghead')`));
        await touchAt('touchStart', blank.x, blank.y); await sleep(60); await touchAt('touchEnd', blank.x, blank.y);
        await sleep(300);
        R.tapAway = { card: await card(), opened: await opened() };
        // A tap opens the task.
        await touchAt('touchStart', tk.x, tk.y); await sleep(60); await touchAt('touchEnd', tk.x, tk.y);
        await sleep(900);
        R.tap = { card: await card(), opened: await opened() };
      }
      await c.send('Target.closeTarget', { targetId });
      await c.send('Target.disposeBrowserContext', { browserContextId });
    }
  } finally { ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class LeftBarCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-lbc-", ignore_cleanup_errors=True)
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
        task = chatroom.create_room(TITLE, [dict(m) for m in members])
        chatroom.post_message(task["id"], "user", "Why do the brakes squeal?")
        dashboard.assign_session_project(task["id"], cls.proj)
        chatroom.set_task_number(task["id"], cls.proj, 7)
        full = chatroom.get_room(task["id"], public=False)
        full["projectId"] = cls.proj
        full["workflow"] = "inreview"
        chatroom.update_room(full)
        cls.task = task["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**test_middle.chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task, "title": TITLE, "shots": shots}
        script = base / "left_bar_card_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def g(self, w):
        g = self.got[w]
        self.assertTrue(g["listed"], g)
        return g

    def test_no_tooltip_on_the_line_under_the_po(self):
        for w in ("1728", "390"):
            with self.subTest(w=w):
                g = self.g(w)
                self.assertFalse(g["tkTitle"], g)
                self.assertEqual(g["titles"], [], g)

    def test_rest_opens_the_card_after_a_moment_beside_the_list(self):
        g = self.g("1728")
        self.assertFalse(g["at300"], g)
        self.assertGreaterEqual(g["after"], 450, g)
        self.assertLess(g["after"], 1100, g)
        k = g["card"]
        self.assertEqual((k["no"], k["title"]), ("#7", TITLE), k)
        self.assertTrue(k["state"].startswith("In review"), k)
        self.assertEqual(k["proj"], "", k)          # the PO's row already names the project
        self.assertEqual(g["expanded"], "true", g)
        # Beside the list, never over the number.
        self.assertTrue(k["beside"], k)
        self.assertGreaterEqual(k["rect"]["l"], round(g["list"]["r"]), g)
        tk = g["tk"]
        self.assertTrue(k["rect"]["l"] >= tk["r"] or k["rect"]["t"] >= tk["b"] or k["rect"]["b"] <= tk["t"], g)
        # Its title opens the task the way the list does, not the page's link handler.
        self.assertFalse(k["dataTask"], k)

    def test_the_card_survives_a_redraw_and_follows_the_task(self):
        g = self.g("1728")
        r = g["redrawn"]
        self.assertEqual(r["card"]["mark"], "kept", g)
        self.assertEqual(r["card"]["rect"], g["card"]["rect"], g)
        self.assertEqual(r["newTk"], "true", g)
        self.assertEqual(g["polled"]["mark"], "kept", g)
        self.assertEqual(g["polled"]["rect"], g["card"]["rect"], g)
        self.assertEqual(g["live"]["title"], "Renamed while open", g)
        self.assertEqual(g["live"]["mark"], "kept", g)

    def test_esc_closes_it(self):
        g = self.g("1728")
        self.assertIsNone(g["esc"], g)

    def test_a_click_opens_the_task_not_the_card(self):
        g = self.g("1728")
        self.assertTrue(g["click"]["opened"], g)
        self.assertIsNone(g["click"]["card"], g)

    def test_the_cards_title_opens_the_task(self):
        g = self.g("1728")
        self.assertTrue(g["onTitle"], g)
        self.assertTrue(g["titleClick"]["opened"], g)
        self.assertIsNone(g["titleClick"]["card"], g)

    def test_keyboard_focus_opens_it_and_esc_keeps_the_focus(self):
        g = self.g("1728")
        self.assertFalse(g["keyAt200"], g)
        self.assertIsNotNone(g["keyCard"], g)
        self.assertEqual(g["keyCard"]["title"], TITLE, g)
        self.assertIsNone(g["keyEsc"]["card"], g)
        self.assertTrue(g["keyEsc"]["focus"], g)

    def test_touch_long_press_tap_away_and_tap(self):
        g = self.g("390")
        self.assertIsNotNone(g["long"]["card"], g)
        self.assertEqual(g["long"]["card"]["title"], TITLE, g)
        self.assertEqual(g["long"]["opened"], "", g)
        self.assertIsNone(g["tapAway"]["card"], g)
        self.assertEqual(g["tapAway"]["opened"], "", g)
        self.assertIsNone(g["tap"]["card"], g)
        self.assertTrue(g["tap"]["opened"], g)
        # Never over the number it is for.
        k, tk = g["long"]["card"]["rect"], g["tk"]
        self.assertTrue(k["t"] >= tk["b"] or k["b"] <= tk["t"] or k["l"] >= tk["r"], g)


if __name__ == "__main__":
    unittest.main()
