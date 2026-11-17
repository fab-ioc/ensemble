"""#386 (P376), review 2: a project keeps at most 8 tabs open, the PO's tab among them.

The PO's tab counts and never closes; a floating panel is no tab (it neither counts nor closes); a pinned tab of
another project that comes along with a switch counts, and the switch closes this project's least recently shown
unpinned tab to make room. Counted in the dock itself: the tabs drawn in #po-dock.

In headless Chrome over CDP, against a hub in a thread: Big (a PO and 10 tasks) and Other (a PO and one task).
Skipped without Node or Chrome.
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
from tests import chrome_profile  # noqa: E402
from tests.test_task_tabs_cdp import CDP_JS as TT_JS, CHROME, NODE  # noqa: E402

# The launch and CDP helpers of the task-tab suite, then this one's run.
CDP_JS = TT_JS.split("// What the PO's stack holds")[0] + r"""
// The tabs drawn in the dock (the PO's, chats', files'), apart from floating
// ones; which panels are open; which float.
const ST = `(() => { const d = PD.dock, ok = id => id === 'po-chat' || id.startsWith('chat:') || id.startsWith('file:');
  const fl = id => { try { return d.viewMode(id) === 'float'; } catch (e) { return false; } };
  const drawn = [...document.querySelectorAll('#po-dock .dk-tab[data-dk-tab]')].map(t => t.dataset.dkTab)
    .filter(id => ok(id) && !fl(id) && !d.isOut(id));
  return { pid: PD.pid, tabs: [...new Set(drawn)], open: [...PD.rt.keys()].filter(ok), floats: [...PD.rt.keys()].filter(fl),
           po: d.isVisible('po-chat') }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const T = A.T, P = A.P, sid = room => `ALL_ROWS.find(x => x.roomId === ${JSON.stringify(room)}).sessionId`;
  const step = async (name, js, ms = 900) => { await evalIn(js + '; 0'); await sleep(ms); out[name] = await evalIn(ST); };
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.length >= 13', 30000);
    await evalIn(`(() => { SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(P.Big)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    await until(`!!PD.dock && document.body.classList.contains('po-dock') && PD.restored && PD.pid === ${JSON.stringify(P.Big)}`, 30000);
    await sleep(600);
    // Seven tasks' tabs and the PO's: 8. The eighth task closes the first.
    for (let i = 1; i <= 7; i++) await step('t' + i, `openDetail(${sid(T['t' + i])})`, 700);
    await step('t8', `openDetail(${sid(T.t8)})`, 900);
    // A floating panel is no tab: t2 floats, t9 opens (8 tabs again, none closes);
    // t1 again then closes t3, never the float.
    await step('float', `PD.dock.setViewMode('chat:${T.t2}', 'float')`, 700);
    await step('t9', `openDetail(${sid(T.t9)})`, 900);
    await step('t1', `openDetail(${sid(T.t1)})`, 900);
    // A pinned tab of Other comes along to Big: Big's least recently shown goes (t4).
    await step('other', `openProjectPage(${JSON.stringify(P.Other)})`, 1500);
    await step('o1', `openDetail(${sid(T.o1)})`, 900);
    await evalIn(`PD.dock.activate('po-chat'); 0`);
    await sleep(400);
    await evalIn(`document.querySelector('#po-dock [data-pd-pin="chat:${T.o1}"]').click(); 0`);
    await sleep(400);
    out.pinned = await evalIn(`PdWorkspace.isPinned(PD_WS, 'chat:${T.o1}')`);
    await step('big', `openProjectPage(${JSON.stringify(P.Big)})`, 2000);
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
class TabCap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-cap-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard, "push_label_to_iterm", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        cls.ids, cls.tasks = {}, {}

        def room(title, home):
            t = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                             {"identity": "codex", "agent": "codex", "cwd": str(home)}])
            chatroom.post_message(t["id"], "user", "About " + title)
            return t["id"]
        for name, tasks in (("Big", [f"t{i}" for i in range(1, 11)]), ("Other", ["o1"])):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            cls.ids[name] = proj["id"]
            for t in tasks:
                cls.tasks[t] = room(t, home)
                dashboard.assign_session_project(cls.tasks[t], proj["id"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "P": cls.ids, "T": cls.tasks}
        script = base / "cap_cdp.js"
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

    def c(self, t):
        return "chat:" + self.tasks[t]

    def test_seven_tasks_and_the_po_are_eight_tabs(self):
        s = self.got["t7"]
        self.assertEqual(len(s["tabs"]), 8, s)
        self.assertIn("po-chat", s["tabs"])

    def test_an_eighth_task_closes_the_least_recently_shown_not_the_po(self):
        s = self.got["t8"]
        self.assertEqual(len(s["tabs"]), 8, s)
        self.assertIn("po-chat", s["tabs"])
        self.assertIn(self.c("t8"), s["tabs"])
        self.assertNotIn(self.c("t1"), s["open"], s)

    def test_a_floating_panel_neither_counts_nor_closes(self):
        g = self.got
        self.assertIn(self.c("t2"), g["float"]["floats"], g["float"])
        s = g["t9"]
        self.assertEqual(len(s["tabs"]), 8, s)
        self.assertIn(self.c("t2"), s["open"], "a float does not close for a tab")
        self.assertIn(self.c("t3"), s["open"], "with the float not counted, nothing closes")
        s = g["t1"]
        self.assertEqual(len(s["tabs"]), 8, s)
        self.assertIn(self.c("t2"), s["open"], s)
        self.assertNotIn(self.c("t3"), s["open"], s)

    def test_a_pinned_tab_brought_along_counts_and_the_switch_makes_room(self):
        g = self.got
        self.assertTrue(g["pinned"])
        s = g["big"]
        self.assertEqual(s["pid"], self.ids["Big"])
        self.assertLessEqual(len(s["tabs"]), 8, s)
        self.assertIn("po-chat", s["tabs"])
        self.assertIn(self.c("o1"), s["tabs"], "the pinned tab stays")
        self.assertNotIn(self.c("t4"), s["open"], "Big's least recently shown made room")

    def test_the_page_threw_no_exception(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
