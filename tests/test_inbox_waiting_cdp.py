"""A waiting card in the Inbox (#352): a task whose start was refused because
its folder is missing ("could not start"). In headless Chrome over CDP,
against a hub in a thread serving the pages.

One project, Motors, with its PO and a task, Screen rewrite, whose seat's
folder is gone (the room's ``folderMissing``, as seat_folders.refuse writes
it). Checked at 1440x900:

* the card says what happened and what to do (Done, or Stop the task),
  without the folder's path, and has Done; the task's actions offer Stop
  although nothing runs (P324);
* a click on it opens the task in the middle; a second click while it is
  open there tells its chat it is shown (so it goes to its latest);
* Done shows "Done · Undo" in its place; let run, the hub keeps it (the
  room's ``dealtAt``) and the card is no longer in /api/inbox;
* the page threw nothing meanwhile.

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
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import inbox  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_inbox_cdp import CDP_JS as _BASE  # noqa: E402

NODE = shutil.which("node")

CDP_JS = _BASE[: _BASE.index("async function main()")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const at = async (expr) => evalIn(`(() => { const e = (${expr}); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
    const click = async (expr) => {
      const [x, y] = await at(expr);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope", "cd-last-conv"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    await evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    const ROW = `LD_INBOX.querySelector('li.ibx-row[data-kind="waiting"]')`;
    await until(`!!IBX.data && !!${ROW}`, 30000);
    await sleep(300);
    const card = () => evalIn(`(() => { const r = ${ROW}; if (!r) return null;
      return { id: r.dataset.ibx, line: (r.querySelector('.ibx-line') || {}).textContent || '', reason: (r.querySelector('.ibx-reason') || {}).textContent || '',
        text: r.textContent, done: [...r.querySelectorAll('[data-ibx-done]')].map(b => b.textContent.trim()),
        other: [...r.querySelectorAll('.ibx-acts .ibx-btn:not([data-ibx-done])')].map(b => b.textContent.trim()) }; })()`);
    out.card = await card();
    // The task's own actions offer Stop although nothing runs (P324).
    out.menu = await evalIn(`(() => { const r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.task)}); const m = SessionActions.sessionActions(actionState(r, !!r.isLive), actionEnv()); return { primary: m.primary && m.primary.label, items: m.groups.flat().map(i => i.label), couldNotStart: !!r.couldNotStart }; })()`);
    { const [x, y] = await at(ROW); await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId); await sleep(200); }
    await shot('waiting-card-1440');
    out.api = await evalIn(`(async () => { const d = await (await fetch('/api/inbox', { cache: 'no-store' })).json(); return d.rows.filter(r => r.kind === 'waiting').map(r => ({ id: r.id, open: r.open, dismiss: r.dismiss, line: r.line })); })()`);
    // A click opens the task in the middle.
    out.before = await evalIn('SELECTED_SID || ""');
    await click(`${ROW}.querySelector('a.ibx-link')`);
    out.opened = await until(`SELECTED_SID === ${JSON.stringify(A.task)} && document.body.classList.contains('detail-open') ? SELECTED_SID : ''`, 15000).catch(() => '');
    // Clicked again while it is open there: its chat is told it is shown.
    const F = `document.querySelector('iframe.dp-session[data-room=${JSON.stringify(A.task)}]')`;
    out.frame = !!(await until(`(() => { const f = ${F}; return !!(f && f.contentWindow && f.contentDocument && f.contentDocument.readyState === 'complete'); })()`, 20000).catch(() => false));
    await evalIn(`(() => { const w = ${F}.contentWindow; w.__shown = 0; w.addEventListener('message', e => { if (e.data && e.data.ensemble === 'shown') w.__shown++; }); return 0; })()`);
    await click(`${ROW}.querySelector('a.ibx-link')`);
    out.shown = await until(`${F}.contentWindow.__shown`, 5000).catch(() => 0);
    out.still = await evalIn('SELECTED_SID || ""');
    await shot('waiting-opened-1440');
    // Done: Done · Undo in its place; let run, the hub keeps it.
    await click(`${ROW}.querySelector('[data-ibx-done]')`);
    await sleep(200);
    out.gone = await evalIn(`[...LD_INBOX.querySelectorAll('li.ibx-gone')].map(r => r.textContent.trim())`);
    await shot('waiting-done-undo-1440');
    await evalIn(`(() => { const g = IBX.gone.get(${JSON.stringify(out.card ? out.card.id : '')}); if (g) { clearTimeout(g.timer); g.fire(); } return 0; })()`);
    out.apiAfter = await until(`(async () => { const d = await (await fetch('/api/inbox', { cache: 'no-store' })).json(); const w = d.rows.filter(r => r.kind === 'waiting'); return w.length ? null : { count: d.count }; })()`, 15000).catch(() => null);
    await sleep(400);
    out.after = await card();
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class WaitingCardInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibw-", ignore_cleanup_errors=True)
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
            # Opening a task may try to resume its agents: nothing may start here.
            mock.patch.object(dashboard.Handler, "_resume_room", lambda *a, **k: {"delivered": 0}),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)       # tests/test_inbox.py leaves it bound to its fake hub
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        members = lambda cwd: [{"identity": "claude", "agent": "claude", "cwd": str(cwd)},
                               {"identity": "codex", "agent": "codex", "cwd": str(cwd)}]
        po = chatroom.create_room("Motors PO", members(home))
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        gone = home / "screen_rewrite_task" / "repo"      # never made: the seat's folder is missing
        tk = chatroom.create_room("Screen rewrite", members(gone))
        dashboard.assign_session_project(tk["id"], proj["id"])
        T = tk["id"]
        chatroom.post_message(T, "user", "Rewrite the screen.", to="claude")
        chatroom.post_message(T, "claude", "Started on the screen rewrite.", to="user")
        chatroom.patch_room(T, cwd=str(gone), no=12, noProjectId=proj["id"], folderMissing={
            "path": str(gone), "identity": "claude", "kind": "missing", "since": time.time() - 120,
            "reason": f"claude's folder {gone} is missing (its branch sess/x is neither local nor on origin): "
                      f"recreate it, or move the task to a folder that exists, then start it again.",
            "roomCwd": str(gone), "seatCwd": str(gone)})
        cls.task, cls.gone_path = T, str(gone)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": proj["id"], "task": T, "shots": shots}
        script = base / "inbox_waiting_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.dealt = float(chatroom.get_room(T, public=False).get("dealtAt") or 0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_card_says_what_to_do_without_the_path_and_has_done(self):
        c = self.got["card"]
        self.assertIsNotNone(c, self.got)
        self.assertRegex(c["line"], r"^(#|[A-Z]+-)12 could not start: its work folder is missing$")
        self.assertIn("Its agent cannot start because its folder was removed. Press Done, or open the task and Stop it.", c["reason"])
        self.assertNotIn(self.gone_path, c["text"])
        self.assertEqual(c["done"], ["Done"], c)
        self.assertNotIn("Done with this", c["other"], "one Done, not two")
        self.assertEqual([(r["open"], r["dismiss"]) for r in self.got["api"]], [("chat", "dealt")])

    def test_the_task_offers_stop_although_nothing_runs(self):
        m = self.got["menu"]
        self.assertTrue(m["couldNotStart"], m)
        self.assertEqual(m["primary"], "Resume")
        self.assertIn("Stop", m["items"])

    def test_a_click_opens_the_task_and_again_takes_its_chat_to_the_latest(self):
        self.assertNotEqual(self.got["before"], self.task)
        self.assertEqual(self.got["opened"], self.task)
        self.assertTrue(self.got["frame"])
        self.assertGreaterEqual(self.got["shown"], 1, "the open chat was told it is shown")
        self.assertEqual(self.got["still"], self.task)

    def test_done_has_undo_then_the_hub_keeps_it(self):
        self.assertTrue(self.got["gone"] and self.got["gone"][0].startswith("Done:"), self.got["gone"])
        self.assertIsNotNone(self.got["apiAfter"], "the card left /api/inbox")
        self.assertIsNone(self.got["after"])
        self.assertGreater(self.dealt, 0)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
