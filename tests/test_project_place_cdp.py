"""A project keeps its place in the left list when you open it or work in it
(#376, GitHub issue 53). In headless Chrome over CDP, against a hub in a thread
serving the pages; nothing patched between the page and the Inbox, so opening a
project's PO chat marks its answer seen, as it does live.

Four projects with a PO each: Bee keeping, Cat shelter and Dog walks with one
answer to check (an Inbox count of 1 each), Abacus notes with none. Checked:

* the unpinned go by Inbox count, then by name: Bee, Cat, Dog, Abacus;
* a click on the top one (Bee) opens its PO chat, which lowers its count to 0,
  and no row moves: the project you are in keeps the count it had;
* then its PO and its task speak, and you write in its PO chat: nothing moves;
* a question from Dog's PO raises Dog's count: Dog goes to the top;
* a click on Cat: nothing moves while the pointer is over the list; once it has
  left (and 2 s passed) Bee, no longer the one you are in, takes its place by
  its count (0, after Abacus by name) and Cat keeps its own;
* the page threw nothing meanwhile.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or
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
import time
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
from tests import test_left_panel_order_cdp as lpo  # noqa: E402

NODE = shutil.which("node")
# name, answers to check; registered and spoken in in this order
SEED = [("Abacus notes", 0), ("Bee keeping", 1), ("Cat shelter", 1), ("Dog walks", 1)]
START = ["Bee keeping", "Cat shelter", "Dog walks", "Abacus notes"]

# The #337 test's launcher, CDP client and row reader (everything before its main).
CDP_JS = lpo.CDP_JS[:lpo.CDP_JS.index("async function main()")] + r"""
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
const lines = []; let wake = null;
rl.on('line', l => { lines.push(l); if (wake) { wake(); wake = null; } });
// the hub side does `what`, then answers on stdin
const hub = async (what) => { console.log('STEP ' + what); while (!lines.length) await new Promise(r => wake = r); return lines.shift(); };
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { steps: {} }; global.OUT = out;
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const mouse = (type, x, y) => c.send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', clickCount: 1 }, sessionId);
  const clickAt = async (x, y) => { await mouse('mouseMoved', x, y); await mouse('mousePressed', x, y); await mouse('mouseReleased', x, y); };
  const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
  await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
  await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 3 && window.ensBooted === true', 30000);
  const P = A.ids, J = JSON.stringify;
  const projRows = async () => (await evalIn(ROWS)).filter(r => r.proj);
  const names = async () => (await projRows()).map(r => r.name);
  const counts = async () => Object.fromEntries((await projRows()).map(r => [r.name, r.n]));
  const cnt = name => `ldpCount(${J(P[name])})`;
  // the pointer away from the list, the hold over (2 s), the polls in
  const settle = async () => { await mouse('mouseMoved', 900, 600); await sleep(4000); };
  const step = async (k) => { await settle(); out.steps[k] = { order: await names(), counts: await counts(), pd: await evalIn('PD.pid') }; await shot('place-' + k); };
  const open = async (name) => {
    const r = (await projRows()).find(x => x.proj === P[name]);
    await clickAt(r.cx, r.cy);
    await until(`PD.pid === ${J(P[name])}`, 15000);
  };
  try {
    await evalIn(`(() => { SELECTED_PROJECT = ${J(P['Abacus notes'])}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until(`document.body.classList.contains("po-dock") && !!LD.dock && PD.pid === ${J(P['Abacus notes'])}`, 30000);
    await until(`${cnt('Bee keeping')} === 1 && ${cnt('Cat shelter')} === 1 && ${cnt('Dog walks')} === 1`, 20000);
    await step('start');
    // ---- open the top one: its PO chat in front marks its answer seen, its count falls
    await open('Bee keeping');
    out.steps.clickedUnder = { order: await names() };
    out.beeSeen = !!(await until(`${cnt('Bee keeping')} === 0`, 20000).catch(() => false));
    await step('opened');
    // ---- work in it: its PO and its task speak, you write
    await hub('bee-agent'); await step('agent');
    await hub('bee-task'); await step('task');
    await hub('bee-you'); await step('you');
    // ---- another project's count rises
    await hub('dog-answer');
    await until(`${cnt('Dog walks')} === 2`, 20000);
    await step('dogUp');
    // ---- open another: under the pointer nothing moves, then Bee takes its place
    await open('Cat shelter');
    await sleep(1500);
    out.steps.catUnder = { order: await names() };
    out.catSeen = !!(await until(`${cnt('Cat shelter')} === 0`, 20000).catch(() => false));
    await step('cat');
  } finally {
    out.errors = c.errors;
    console.log('OUT ' + JSON.stringify(out));
    ch.kill();
  }
  process.exit(0);
}
main().catch(e => { console.error(e && e.stack || e); console.log('OUT ' + JSON.stringify(global.OUT || {})); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class ProjectKeepsItsPlaceInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-place-", ignore_cleanup_errors=True)
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
        cls.ids, cls.po, cls.task = {}, {}, {}
        for name, n in SEED:
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", members(home))
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            cls.po[name] = po["id"]
            task = chatroom.create_room(f"A task in {name}", members(home))
            dashboard.assign_session_project(task["id"], proj["id"])
            cls.task[name] = task["id"]
            if n:
                cls.answer(po["id"], "k0")
            else:
                chatroom.post_message(po["id"], "user", "hello", to="claude")
            time.sleep(1.1)  # the next project's conversation the later one
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**lpo.chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "ids": cls.ids, "shots": shots}
        script = base / "project_place_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        steps = {
            "bee-agent": lambda: chatroom.post_message(cls.po["Bee keeping"], "claude", "Working on the hive list now.", to="user"),
            "bee-task": lambda: chatroom.post_message(cls.task["Bee keeping"], "claude", "Pushed a commit.", to="codex"),
            "bee-you": lambda: chatroom.post_message(cls.po["Bee keeping"], "user", "Good, carry on.", to="claude"),
            "dog-answer": lambda: chatroom.post_message(cls.po["Dog walks"], "claude", "Ask (yes/no): Walk the dogs at noon?", to="user"),
        }
        pr = subprocess.Popen([NODE, str(script), json.dumps(args)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8")
        got = None
        timer = threading.Timer(300, pr.kill)
        timer.start()
        try:
            for line in pr.stdout:
                line = line.strip()
                if line.startswith("STEP "):
                    steps[line[5:]]()
                    pr.stdin.write("go\n")
                    pr.stdin.flush()
                elif line.startswith("OUT "):
                    got = json.loads(line[4:])
            err = pr.stderr.read()
            pr.wait()
        finally:
            timer.cancel()
        assert pr.returncode == 0, (err[-4000:], got)
        cls.got = got

    @staticmethod
    def answer(room_id, key):
        """A point of yours and the PO's answer to it: one more answer to check."""
        text, pids = points.take(chatroom.get_room(room_id, public=False), f"Point {key}", to="claude", key=key)
        chatroom.post_message(room_id, "user", text, to="claude")
        for pid in pids:
            chatroom.post_message(room_id, "claude", f"Re {pid}: done.", to="user")
        points.sync(room_id, force=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def s(self, k):
        return self.got["steps"][k]

    def test_unpinned_by_count_then_name(self):
        self.assertEqual(self.s("start")["order"], START)
        self.assertEqual(self.s("start")["counts"]["Bee keeping"], "1")

    def test_opening_the_top_one_lowers_its_count_and_moves_nothing(self):
        self.assertTrue(self.got["beeSeen"], "its PO chat in front marked its answer seen: count 0")
        self.assertEqual(self.s("opened")["pd"], self.ids["Bee keeping"])
        self.assertEqual(self.s("opened")["counts"]["Bee keeping"], "", "its row shows its real count")
        self.assertEqual(self.got["steps"]["clickedUnder"]["order"], START)
        self.assertEqual(self.s("opened")["order"], START)

    def test_work_in_it_moves_nothing(self):
        for k in ("agent", "task", "you"):
            with self.subTest(k=k):
                self.assertEqual(self.s(k)["order"], START)

    def test_a_count_that_rises_moves_that_project_up(self):
        self.assertEqual(self.s("dogUp")["counts"]["Dog walks"], "2")
        self.assertEqual(self.s("dogUp")["order"], ["Dog walks", "Bee keeping", "Cat shelter", "Abacus notes"])

    def test_once_in_another_the_first_takes_its_place_by_count(self):
        self.assertEqual(self.got["steps"]["catUnder"]["order"], self.s("dogUp")["order"], "nothing moves under the pointer")
        self.assertTrue(self.got["catSeen"])
        self.assertEqual(self.s("cat")["order"], ["Dog walks", "Cat shelter", "Abacus notes", "Bee keeping"])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
