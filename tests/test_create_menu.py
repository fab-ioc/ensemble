"""Create is a menu (#165): what each item opens, with the board prefilled.

A hub in a thread serves the page with one board, Motors, holding a few tasks
and no PO. Headless Chrome (tests/chrome_profile.py) opens Motors with one of
its tasks open, for each word for a board (the default, Project, and
Initiative), at 1280x900 and on a 390px phone, in every theme (photographed
in Light and Dark),
and reads from the DOM:

* the menu's items, in order: New task in Motors (the default, Enter), Sub-task
  of the open task, New task…, New <word>…, each in the configured word;
* that the menu fits the screen (nothing outside the viewport, nothing cut,
  the page not wider than the screen) and every tile's glyph has 4.5:1 or
  better on its tile;
* what each item opens: New task in Motors and Sub-task the new-task dialog
  with Motors fixed (the sub-task's spec saying whose follow-up it is); New
  task… the dialog with the chooser focused on Motors; New <word>… the setup
  dialog;
* the keyboard: ArrowDown on Create opens it on its first item, arrows move,
  Escape closes it and gives Create the focus back.

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
from tests import chrome_profile  # noqa: E402
from tests.test_project_noun_page import CDP_JS as NOUN_JS  # noqa: E402

NODE = shutil.which("node")
CHROME = chrome_profile.CHROME

# The launcher and the CDP client of the noun test, then this test's run.
CDP_JS = NOUN_JS[:NOUN_JS.index("// Every visible")] + r"""
const MEASURE = `(() => {
  const m = document.getElementById('create-menu');
  const rgb = s => (s.match(/[\\d.]+/g) || []).slice(0, 3).map(Number);
  const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const r = m.getBoundingClientRect();
  const items = [...m.querySelectorAll('.cm-item')].map(b => {
    const t = b.querySelector('.cm-tile'), cs = getComputedStyle(t), n = b.querySelector('.cm-name'), s = b.querySelector('.cm-sub');
    return { act: b.dataset.create, name: n.textContent, sub: s.textContent, def: !!b.querySelector('.cm-key'),
             h: Math.round(b.getBoundingClientRect().height),
             cut: n.scrollWidth > n.clientWidth + 1 || s.scrollWidth > s.clientWidth + 1,
             tile: Math.round(ratio(rgb(cs.color), rgb(cs.backgroundColor)) * 100) / 100 };
  });
  return { open: !m.hidden, items, focused: document.activeElement && document.activeElement.dataset.create || '',
           inView: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
           noScroll: m.scrollWidth <= m.clientWidth + 1, pageFits: document.documentElement.scrollWidth <= innerWidth + 1,
           theme: document.documentElement.dataset.theme || '', w: Math.round(r.width), expanded: document.getElementById('new-btn').getAttribute('aria-expanded') };
})()`;
const DIALOG = `(() => {
  const d = document.querySelector('dialog[open]');
  if (!d) return null;
  const sel = d.querySelector('select#ns-proj');
  return { id: d.id, h3: (d.querySelector('h3') || {}).textContent || '',
           fixed: (d.querySelector('#ns-proj-fixed') || {}).textContent || '', proj: (d.querySelector('#ns-proj') || {}).value,
           chooser: !!sel, chooserFocused: !!sel && document.activeElement === sel,
           spec: (d.querySelector('#ns-task') || {}).value || '' };
})()`;
const CLOSE = `(() => { document.querySelectorAll('dialog[open]').forEach(d => d.close()); if (typeof SETUP_FLOW !== 'undefined') SETUP_FLOW++; return 0; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, userGesture: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const key = async (k, code) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: code || k, windowsVirtualKeyCode: { ArrowDown: 40, ArrowUp: 38, Escape: 27, Enter: 13 }[k] || 0 }, sessionId); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && !!PREFS', 30000);
    await sleep(800);
    return { evalIn, until, shot, key, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const openMenu = async p => { await p.evalIn(`(closeCreateMenu(), document.getElementById('new-btn').click(), 0)`); await p.until(`!document.getElementById('create-menu').hidden`); };
  try {
    for (const word of A.words) {
      // The word is the hub's: set it there, then load the page afresh.
      const set = await page(800, 600, false);
      await set.evalIn(`fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ projectNoun: ${JSON.stringify(word)} }) }).then(r => r.status)`);
      await set.close();
      for (const [size, w, h, mobile] of [['1280', 1280, 900, false], ['390', 390, 844, true]]) {
        const p = await page(w, h, mobile);
        await p.until(`noun('one') === ${JSON.stringify(word.one.toLowerCase())}`);
        await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); syncProjSwitch(); return 0; })()`);
        await sleep(400);
        await p.evalIn(`openDetail(${JSON.stringify(A.task)}), 0`);
        await p.until(`!!crumbTask()`);
        await sleep(600);
        const res = { themes: {}, opens: {} };
        // Every theme is measured; Light and Dark are photographed.
        for (const theme of ['light', 'dark', 'dim', 'paper', 'contrast', 'fjord']) {
          await p.evalIn(`setAppearance(${JSON.stringify(theme)}), 0`);
          await sleep(300);
          await openMenu(p);
          await sleep(200);
          res.themes[theme] = await p.evalIn(MEASURE);
          if (theme === 'light' || theme === 'dark') await p.shot(`create-menu-${word.one.toLowerCase()}-${size}-${theme}`);
        }
        await p.evalIn(`setAppearance('light'), 0`);
        for (const act of ['here', 'sub', 'pick', 'project']) {
          await openMenu(p);
          await p.evalIn(`document.querySelector('#create-menu [data-create="${act}"]').click(), 0`);
          await p.until(`!!document.querySelector('dialog[open]')`);
          await sleep(300);
          res.opens[act] = await p.evalIn(DIALOG);
          res.opens[act].menuClosed = await p.evalIn(`document.getElementById('create-menu').hidden`);
          if (act === 'sub') await p.shot(`create-sub-${word.one.toLowerCase()}-${size}`);
          await p.evalIn(CLOSE);
          await sleep(200);
        }
        // The keyboard: ArrowDown on Create, arrows, Escape.
        await p.evalIn(`(closeCreateMenu(), document.getElementById('new-btn').focus(), 0)`);
        await p.key('ArrowDown');
        await sleep(100);
        const k = { first: await p.evalIn(`document.activeElement.dataset.create || ''`) };
        await p.key('ArrowDown');
        k.second = await p.evalIn(`document.activeElement.dataset.create || ''`);
        await p.key('ArrowUp'); await p.key('ArrowUp');
        k.wrapped = await p.evalIn(`document.activeElement.dataset.create || ''`);
        await p.key('Escape');
        k.closed = await p.evalIn(`document.getElementById('create-menu').hidden`);
        k.back = await p.evalIn(`document.activeElement.id`);
        k.taskStillOpen = await p.evalIn(`!!crumbTask()`);
        await openMenu(p);
        await p.evalIn(`document.querySelector('main').click(), document.body.click(), 0`);
        k.clickAway = await p.evalIn(`document.getElementById('create-menu').hidden`);
        // One of the bar's menus at a time, whichever opens second.
        const OPEN = `[...['create-menu', 'me-menu', 'proj-menu']].filter(i => !document.getElementById(i).hidden).join(',')`;
        await openMenu(p);
        await p.evalIn(`document.getElementById('me-btn').click(), 0`);
        k.thenAvatar = await p.evalIn(OPEN);
        await p.evalIn(`document.getElementById('new-btn').click(), 0`);
        k.thenCreate = await p.evalIn(OPEN);
        await p.evalIn(`document.getElementById('proj-switch').click(), 0`);
        k.thenBoard = await p.evalIn(OPEN);
        await p.evalIn(`document.getElementById('new-btn').click(), 0`);
        k.boardThenCreate = await p.evalIn(OPEN);
        // Focus left on the page (a click on the menu's padding): Esc closes
        // the menu, not the task under it.
        await p.evalIn(`document.activeElement.blur(), 0`);
        await p.key('Escape');
        k.escFromPage = await p.evalIn(`document.getElementById('create-menu').hidden && !!crumbTask()`);
        res.keys = k;
        out[word.one + '-' + size] = res;
        await p.close();
      }
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

WORDS = [{"one": "Project", "many": "Projects"}, {"one": "Initiative", "many": "Initiatives"}]


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class CreateMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-create-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "Boards"
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
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        members = [{"identity": "claude", "agent": "claude", "model": "claude-opus-5-5", "cwd": str(home)}]
        rooms = []
        for title in ("Brakes", "Gearbox"):
            r = chatroom.create_room(title, members)
            dashboard.assign_session_project(r["id"], cls.proj)
            dashboard.assign_task_number(r["id"], cls.proj)
            rooms.append(r)
        cls.task = rooms[0]["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "words": WORDS, "task": cls.task,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "proj": cls.proj, "shots": shots}
        script = base / "create_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def runs(self):
        for word in WORDS:
            for size in ("1280", "390"):
                yield word, size, self.got[f"{word['one']}-{size}"]

    def test_the_items_in_order_in_the_word(self):
        for word, size, g in self.runs():
            for theme, m in g["themes"].items():
                with self.subTest(word=word["one"], size=size, theme=theme):
                    self.assertTrue(m["open"])
                    self.assertEqual(m["expanded"], "true")
                    self.assertEqual(m["theme"], theme)
                    self.assertEqual([i["act"] for i in m["items"]], ["here", "sub", "pick", "project"])
                    names = [i["name"] for i in m["items"]]
                    self.assertEqual(names[0], "New task in Motors")
                    self.assertRegex(names[1], r"^Sub-task of #\d+$")
                    self.assertEqual(names[2], "New task…")
                    self.assertEqual(names[3], f"New {word['one'].lower()}…")
                    self.assertEqual(m["focused"], "here")
                    self.assertEqual([i["def"] for i in m["items"]], [True, False, False, False])
                    if word["one"] != "Project":
                        self.assertNotRegex(" ".join(i["name"] + i["sub"] for i in m["items"]), r"(?i)project")

    def test_it_fits_and_the_tiles_read(self):
        for word, size, g in self.runs():
            for theme, m in g["themes"].items():
                with self.subTest(word=word["one"], size=size, theme=theme):
                    self.assertTrue(m["inView"] and m["noScroll"] and m["pageFits"], m)
                    for i in m["items"]:
                        self.assertFalse(i["cut"], i)
                        self.assertGreaterEqual(i["tile"], 4.5, i)
                        if size == "390":
                            self.assertGreaterEqual(i["h"], 44, i)

    def test_each_item_opens_its_dialog_with_the_board(self):
        for word, size, g in self.runs():
            o = g["opens"]
            with self.subTest(word=word["one"], size=size):
                for act in ("here", "sub", "pick", "project"):
                    self.assertTrue(o[act]["menuClosed"], act)
                self.assertEqual(o["here"]["id"], "modal")
                self.assertEqual(o["here"]["h3"], "New task")
                self.assertEqual((o["here"]["fixed"], o["here"]["proj"]), ("Motors", self.proj))
                self.assertFalse(o["here"]["chooser"])
                self.assertRegex(o["sub"]["h3"], r"^Sub-task of ")
                self.assertEqual((o["sub"]["fixed"], o["sub"]["proj"]), ("Motors", self.proj))
                self.assertRegex(o["sub"]["spec"], r"^Follow-up of #\d+ \(Brakes\)\.\n\n$")
                self.assertTrue(o["pick"]["chooser"] and o["pick"]["chooserFocused"], o["pick"])
                self.assertEqual(o["pick"]["proj"], self.proj)
                self.assertEqual(o["project"]["id"], "setup-modal")
                self.assertEqual(o["project"]["h3"], f"New {word['one'].lower()}")

    def test_the_keyboard(self):
        for word, size, g in self.runs():
            with self.subTest(word=word["one"], size=size):
                self.assertEqual(g["keys"], {"first": "here", "second": "sub", "wrapped": "project", "closed": True,
                                             "back": "new-btn", "taskStillOpen": True, "clickAway": True,
                                             "thenAvatar": "me-menu", "thenCreate": "create-menu",
                                             "thenBoard": "proj-menu", "boardThenCreate": "create-menu",
                                             "escFromPage": True})


if __name__ == "__main__":
    unittest.main()
