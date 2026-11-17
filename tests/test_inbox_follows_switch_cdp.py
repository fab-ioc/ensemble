"""GitHub issue 56 (#390): the Inbox's project filter follows the project switcher.

On a project switch (the switcher, the left list, opening a task of another project):
a filter on one project becomes the new project; All stays All. Also in the Inbox's
popped-out window, and after a reload. Picking All by hand in the Inbox never switches
the project.

In headless Chrome over CDP, against a hub in a thread: Big and Other, each with a PO and
two tasks. Skipped without Node or Chrome.
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

CDP_JS = TT_JS.split("// What the PO's stack holds")[0] + r"""
// The Inbox as the page has it: its scope, the list's pick, the heading of the
// list dock's Inbox ("Inbox · Big · 0"), the row the list marks, the project.
const ST = `(() => { const h = document.querySelector('#ld-ibx-body .ibx-title');
  const cur = document.querySelector('#ld-projs .ldp-row[data-cur]');
  const w = LD.dock && LD.dock.isOut('inbox') ? LD.dock.popWindow('inbox') : null;
  const wh = w && !w.closed ? w.document.querySelector('#ld-ibx-body .ibx-title') : null;
  const bh = LD_INBOX.querySelector('#ld-ibx-body .ibx-title');
  return { inDoc: document.contains(LD_INBOX), boxHead: bh ? bh.textContent : null, scope: IBX.scope, pick: ibxLdPid(), head: h ? h.textContent : null, cur: cur ? (cur.dataset.ldpProj || cur.dataset.ldp || '') : null,
           here: ldHere(), proj: SELECTED_PROJECT, winHead: wh ? wh.textContent : null,
           stored: localStorage.getItem('cd-inbox-scope'), storedPick: localStorage.getItem('cd-ld-sel') }; })()`;
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
  const T = A.T, P = A.P, J = JSON.stringify, sid = room => `ALL_ROWS.find(x => x.roomId === ${J(room)}).sessionId`;
  const step = async (name, js, ms = 1200) => { await evalIn(js + '; 0'); await sleep(ms); out[name] = await evalIn(ST); };
  const load = async () => {
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.length >= 6 && !!window.LD && !!LD.dock', 30000);
    await until('!!document.querySelector("#ld-projs .ldp-row")', 20000);
  };
  // The switcher's own menu: open it, click the project's row.
  const viaSwitcher = pid => `(() => { openProjMenu(); const b = document.querySelector('#proj-menu .pm-item[data-proj="${pid}"]'); if (b) { b.click(); return 1; } projPick(${J(pid)}); return 2; })()`;
  // The left list's project row (a pick there switches the project too).
  const viaList = pid => `document.querySelector('#ld-projs .ldp-row[data-ldp-proj="${pid}"]').click()`;
  const allRow = `document.querySelector('#ld-projs .ldp-row[data-ldp="all"]').click()`;
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await load();
    out.loaded = await evalIn(`({ inDoc: document.contains(LD_INBOX), mode: LD.dock.viewMode('inbox'), vis: LD.dock.isVisible('inbox') })`);
    await step('start', `projPick(${J(P.Big)})`, 2500);
    // 1. Inbox on Big (the list's row), then the switcher to Other: Other.
    await step('onBig', viaList(P.Big));
    await step('switchOther', viaSwitcher(P.Other), 2000);
    out.switcherClicked = await evalIn(`!!document.querySelector('#proj-menu') && 1`);
    // 2. The left list to Big: Big.
    await step('listBig', viaList(P.Big), 2000);
    // 3. A task of Other opened: Other.
    await step('taskOther', `openDetail(${sid(T.o1)})`, 2000);
    await step('backBig', `closeDetail(); projPick(${J(P.Big)})`, 2000);
    // 4. All by hand: the project stays; a switch keeps All; so does a task.
    await step('all', allRow, 800);
    await step('allSwitch', viaSwitcher(P.Other), 2000);
    await step('allTask', `openDetail(${sid(T.b1)})`, 2000);
    await step('allBack', `closeDetail(); projPick(${J(P.Other)})`, 2000);
    // 5. A reload keeps All.
    await c.send('Page.reload', {}, sessionId);
    await sleep(500);
    await load(); await sleep(2500);
    out.allReload = await evalIn(ST);
    // 6. Back to one project (Other's row), then the popped-out Inbox follows a switch.
    await step('pickOther', viaList(P.Other));
    await evalIn(`LD.dock.popOut('inbox') && 0`);
    await until(`(() => { const w = LD.dock.popWindow('inbox'); return !!w && !w.closed && !!w.document.querySelector('#ld-ibx-body .ibx-title'); })()`, 15000).catch(() => null);
    await step('winBefore', '0', 600);
    await step('winSwitch', `projPick(${J(P.Big)})`, 2000);
    await step('winAll', `IBX.scope = 'all'; try { localStorage.setItem('cd-inbox-scope', 'all'); } catch (e) {} ibxRepaint()`, 600);
    await step('winAllSwitch', `projPick(${J(P.Other)})`, 2000);
    await step('winPick', `ldpScope('project'); ldSelSet(${J(P.Other)}); ibxRepaint()`, 600);
    try { await evalIn(`LD.dock.popIn('inbox'); 0`); } catch (e) {}
    await sleep(800);
    // 7. The Inbox put away (its − : back to its strip button) follows too.
    await step('awayPick', viaList(P.Other), 1500);
    await evalIn(`document.querySelector('#list-dock .dk-stack:has([data-dk-tab="inbox"]) .dk-head [data-dk-act="hide"]').click()`);
    await sleep(800);
    await step('awaySwitch', `projPick(${J(P.Big)})`, 2000);
    await step('awayBack', `document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="inbox"]').click()`, 1200);
    // 8. One project survives a reload; then a switch moves it on.
    await step('pickBig', viaList(P.Big), 2000);
    await c.send('Page.reload', {}, sessionId);
    await sleep(500);
    await load(); await sleep(2500);
    out.pickReload = await evalIn(ST);
    await step('pickReloadSwitch', `projPick(${J(P.Other)})`, 2000);
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
class InboxFollowsSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibxsw-", ignore_cleanup_errors=True)
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
        for name, tasks in (("Big", ["b1", "b2"]), ("Other", ["o1", "o2"])):
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
        script = base / "ibxsw_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("IBXSW_DUMP"):
            Path(os.environ["IBXSW_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def on(self, key, name):
        s = self.got[key]
        self.assertEqual(s["scope"], "project", f"{key}: {s}")
        self.assertEqual(s["pick"], self.ids[name], f"{key}: {s}")
        self.assertEqual(s["cur"], self.ids[name], f"{key}: {s}")
        self.assertTrue((s["head"] or "").startswith(f"Inbox · {name}"), f"{key}: {s}")

    def all(self, key):
        s = self.got[key]
        self.assertEqual(s["scope"], "all", f"{key}: {s}")
        self.assertEqual(s["cur"], "all", f"{key}: {s}")
        self.assertTrue((s["head"] or "").startswith("Inbox · All"), f"{key}: {s}")

    def test_one_project_follows_the_switcher(self):
        self.on("onBig", "Big")
        self.on("switchOther", "Other")

    def test_one_project_follows_the_left_list_and_a_task_opened(self):
        self.on("listBig", "Big")
        self.on("taskOther", "Other")
        self.on("backBig", "Big")

    def test_all_by_hand_keeps_the_project(self):
        self.all("all")
        self.assertEqual(self.got["all"]["proj"], self.got["backBig"]["proj"])
        self.assertEqual(self.got["all"]["here"], self.ids["Big"])

    def test_all_stays_all_across_switches(self):
        for k in ("allSwitch", "allTask", "allBack"):
            self.all(k)
        self.assertEqual(self.got["allSwitch"]["here"], self.ids["Other"])

    def test_a_reload_keeps_all(self):
        self.all("allReload")
        self.assertEqual(self.got["allReload"]["stored"], "all")

    def test_the_popped_out_inbox_follows(self):
        g = self.got
        self.assertTrue((g["winBefore"]["winHead"] or "").startswith("Inbox · Other"), g["winBefore"])
        self.assertTrue((g["winSwitch"]["winHead"] or "").startswith("Inbox · Big"), g["winSwitch"])
        self.assertTrue((g["winAllSwitch"]["winHead"] or "").startswith("Inbox · All"), g["winAllSwitch"])
        self.assertEqual(g["winAllSwitch"]["scope"], "all")

    def test_the_put_away_inbox_follows(self):
        g = self.got
        self.assertTrue((g["awaySwitch"]["boxHead"] or "").startswith("Inbox · Big"), g["awaySwitch"])
        self.assertEqual(g["awaySwitch"]["pick"], self.ids["Big"])
        self.assertTrue((g["awayBack"]["head"] or "").startswith("Inbox · Big"), g["awayBack"])

    def test_a_reload_keeps_one_project_and_it_follows_after(self):
        self.on("pickReload", "Big")
        self.on("pickReloadSwitch", "Other")

    def test_the_page_threw_no_exception(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
