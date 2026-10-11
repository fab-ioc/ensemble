"""Every way a chat names a task is a chip in the page (#202, P164).

In headless Chrome over CDP, against a hub in a thread serving a task's chat,
at 1728 x 1117 and at 390 x 844 (touch):

* a room id in prose and as a whole code span is that task's chip (#2, #18);
* "task 18" written as a word: the number is the chip;
* a hub header "report from task #2 *its title*": the chip, and the title a
  link to the same task that opens the same card;
* `#2` and `KEY-2` as a whole code span are chips; `#111` (a colour) and a
  longer code span stay code;
* "#140/#2" is a list (#2 a chip), "Documents/#30" and "S-03" stay text;
* a deleted task's room id: its words as written, no link, Enter opens a card
  saying "(deleted)";
* every chip opens the right task's card; nothing scrolls sideways.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node and
Chrome. The chip and card behaviour itself: tests/test_task_chip_page.py.
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
GONE = "room-0badf00d"
TITLE = "Fast task list after a start"

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
// Each balloon: its chips (in order), title links, gone ids, code spans and text.
const VIEW = `(() => {
  const balloons = [...document.querySelectorAll('#msgs .msg .text')];
  return { balloons: balloons.map(b => ({
      chips: [...b.querySelectorAll('a.task-chip:not(.tc-gone)')].map(a => ({ text: a.textContent, task: a.dataset.task, href: a.getAttribute('href') })),
      titles: [...b.querySelectorAll('a.task-title-ref')].map(a => ({ text: a.textContent, task: a.dataset.task, href: a.getAttribute('href'), color: getComputedStyle(a).color })),
      gone: [...b.querySelectorAll('a.tc-gone')].map(a => ({ text: a.textContent, href: a.getAttribute('href'), tabindex: a.getAttribute('tabindex') })),
      code: [...b.querySelectorAll('code')].map(c => c.textContent),
      links: [...b.querySelectorAll('a')].map(a => ({ text: a.textContent, href: a.getAttribute('href') || '' })),
      text: b.textContent })),
    scrollX: document.documentElement.scrollWidth - innerWidth };
})()`;
const CARD = `(() => { const c = document.querySelector('.task-card'); if (!c) return null;
  const t = c.querySelector('.tc-title');
  return { no: c.querySelector('.tc-no').textContent, title: t ? t.textContent : null, href: t ? t.getAttribute('href') : null,
           state: (c.querySelector('.tc-state') || {}).textContent || '' }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of [[1728, 1117, false], [390, 844, true]]) {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      // A link that wraps is pressed on its first line.
      const center = async (sel) => evalIn(`(() => { const b = document.querySelector('${sel}').getClientRects()[0]; return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; })()`);
      const press = async (sel) => {
        await evalIn(`document.querySelector('${sel}').scrollIntoView({ block: 'center' }); 0`);
        await sleep(100);
        const { x, y } = await center(sel);
        if (mob) {
          await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }, sessionId);
          await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, sessionId);
        } else {
          await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
          for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
        }
        await sleep(300);
      };
      const key = async (k, code, vk) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }, sessionId); await sleep(200); };
      await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
      // Drawn, and every room id answered (the deleted one too).
      await until(`typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && document.querySelector('#msgs a.tc-gone') && document.querySelectorAll('#msgs a.task-title-ref').length >= 1`);
      await sleep(600);
      const o = { view: await evalIn(VIEW), cards: [] };
      if (A.shots) { const r = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }, sessionId); fs.writeFileSync(path.join(A.shots, `task-mentions-${w}.png`), Buffer.from(r.data, 'base64')); }
      // Every chip and title link opens its task's card.
      const n = await evalIn(`document.querySelectorAll('#msgs a.task-chip:not(.tc-gone), #msgs a.task-title-ref').length`);
      for (let i = 0; i < n; i++) {
        await evalIn(`(() => { document.querySelectorAll('#msgs [data-mark]').forEach(e => e.removeAttribute('data-mark')); document.querySelectorAll('#msgs a.task-chip:not(.tc-gone), #msgs a.task-title-ref')[${i}].setAttribute('data-mark', '1'); return 0; })()`);
        await press('#msgs [data-mark="1"]');
        o.cards.push({ task: await evalIn(`document.querySelector('#msgs [data-mark="1"]').dataset.task`), cls: await evalIn(`document.querySelector('#msgs [data-mark="1"]').className`), card: await evalIn(CARD) });
        await key('Escape', 'Escape', 27);
      }
      // The deleted id: focus it, Enter opens its card; a click goes nowhere.
      await evalIn(`document.querySelector('#msgs a.tc-gone').focus(); 0`);
      await key('Enter', 'Enter', 13);
      o.gone = await evalIn(CARD);
      await key('Escape', 'Escape', 27);
      out[w] = o;
      await c.send('Target.closeTarget', { targetId });
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
class TaskMentionsInChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ment-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
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
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.key = dashboard.find_project(proj["id"])["key"]
        team = [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer"},
                {"identity": "codex", "agent": "codex", "model": "", "role": "reviewer"}]
        cls.room = cls.task("Mentions in chat", proj["id"], team, 18)
        cls.two = cls.task(TITLE, proj["id"], team, 2)
        cls.texts = [
            f"Room ids: {cls.two} is merged, and `{cls.room}` is this one; {GONE} was deleted.",
            f"**update** — report from task #2 *{TITLE}* (claude):\n\nDone.",
            f"As task 18 said, see `#2` and `{cls.key}-2`, the list #140/#2, the colour `#111`, "
            f"`git log #2`, Documents/#30 and rule S-03.",
            f"[open the task]({cls.two}) and Documents/#140/#142.",
        ]
        for i, t in enumerate(cls.texts):
            chatroom.post_message(cls.room, "claude" if i % 2 else "user", t)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": cls.room, "shots": shots}
        script = base / "task_mentions_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.addClassCleanup(cls.tmp.cleanup)

    @classmethod
    def task(cls, title: str, pid: str, team: list[dict], no: int) -> str:
        rid = chatroom.create_room(title, [dict(m) for m in team])["id"]
        full = chatroom.get_room(rid, public=False)
        full["projectId"] = pid
        chatroom.update_room(full)
        dashboard.assign_session_project(rid, pid)
        chatroom.set_task_number(rid, pid, no)
        return rid

    def _both(self):
        return [(w, self.got[str(w)]) for w in (1728, 390)]

    def balloon(self, o, i):
        return o["view"]["balloons"][i]

    def test_room_ids_are_chips(self):
        for w, o in self._both():
            with self.subTest(width=w):
                b = self.balloon(o, 0)
                self.assertEqual([(c["text"], c["task"]) for c in b["chips"]], [("#2", self.two), ("#18", self.room)])
                self.assertNotIn(self.two, b["text"], "the room id is read as its number")
                self.assertEqual(b["code"], [], "the code span that was only a room id is the chip")

    def test_a_deleted_room_id(self):
        for w, o in self._both():
            with self.subTest(width=w):
                self.assertEqual(self.balloon(o, 0)["gone"], [{"text": GONE, "href": None, "tabindex": "0"}])
                self.assertEqual(o["gone"]["no"], GONE)
                self.assertEqual((o["gone"]["state"], o["gone"]["title"]), ("(deleted)", None), "no title link to follow")

    def test_the_header_title_links_to_the_task(self):
        for w, o in self._both():
            with self.subTest(width=w):
                b = self.balloon(o, 1)
                self.assertEqual([c["text"] for c in b["chips"]], ["#2"])
                self.assertEqual(len(b["titles"]), 1)
                t = b["titles"][0]
                self.assertEqual((t["text"], t["task"], t["href"]), (TITLE, self.two, b["chips"][0]["href"]))
                self.assertNotIn(f"*{TITLE}*", b["text"])

    def test_words_code_spans_and_what_stays_text(self):
        for w, o in self._both():
            with self.subTest(width=w):
                b = self.balloon(o, 2)
                self.assertEqual([(c["text"], c["task"]) for c in b["chips"]],
                                 [("#18", self.room), ("#2", self.two), (f"{self.key}-2", self.two), ("#2", self.two)])
                self.assertIn("As task #18 said", b["text"])
                self.assertEqual(b["code"], ["#111", "git log #2"], "a colour and a longer span stay code")
                for plain in ("#140/", "Documents/#30", "rule S-03"):
                    self.assertIn(plain, b["text"])

    def test_a_link_target_and_a_list_after_a_path(self):
        for w, o in self._both():
            with self.subTest(width=w):
                b = self.balloon(o, 3)
                self.assertEqual(b["chips"], [], "a Markdown link's target is not a mention; a path stays a path")
                self.assertEqual([lk["text"] for lk in b["links"]], ["open the task"], "the link keeps its words")
                self.assertIn(self.two, b["links"][0]["href"])
                self.assertIn("Documents/#140/#142", b["text"])

    def test_every_chip_opens_its_tasks_card(self):
        titles = {self.two: TITLE, self.room: "Mentions in chat"}
        for w, o in self._both():
            with self.subTest(width=w):
                self.assertGreaterEqual(len(o["cards"]), 7)
                for c in o["cards"]:
                    self.assertIsNotNone(c["card"], c)
                    self.assertEqual((c["card"]["title"], c["card"]["href"]), (titles[c["task"]], f"/session?id={c['task']}"), c)
                self.assertLessEqual(o["view"]["scrollX"], 0, "no sideways scroll")


if __name__ == "__main__":
    unittest.main()
