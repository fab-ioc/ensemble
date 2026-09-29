"""#131 in a real page: New project with a name and an empty code folder.

In headless Chrome over CDP, against a hub in a thread: the code folder's
hint follows the name as it is typed (the folder the hub would make), and
Create project with the folder left empty registers the project in
<root>/My-Day-Job, a new git repository.

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
from tests import chrome_profile  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

# The top bar test's launcher and CDP client, with this test's steps.
CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS', 30000);
    await evalIn('window.GOT = projectSetupFlow().then(p => (window.MADE = p && p.id, p)); 0');
    await until('!!document.querySelector("#setup-modal[open] #su-name")');
    out.before = await evalIn('document.getElementById("su-path").placeholder');
    // Typed one key at a time, as a person would: the hint follows.
    const type = async (text) => { for (const ch of text) await c.send('Input.insertText', { text: ch }, sessionId); };
    await evalIn('document.getElementById("su-name").focus(); 0');
    await type('My Day');
    await until(`document.getElementById("su-path").placeholder.endsWith("My-Day will be created")`);
    out.mid = await evalIn('document.getElementById("su-path").placeholder');
    await type(' Job');
    await until(`document.getElementById("su-path").placeholder.endsWith("My-Day-Job will be created")`);
    out.hint = await evalIn('document.getElementById("su-path").placeholder');
    out.note = await evalIn('document.getElementById("su-folder-note").textContent');
    out.value = await evalIn('document.getElementById("su-path").value');
    await shot('new-project-empty-folder');
    await evalIn('document.querySelector("input[name=su-po][value=none]").click(); 0');
    await evalIn('document.getElementById("su-ok").click(); 0');
    await until('!!window.MADE', 20000);
    out.made = await evalIn('window.MADE');
    out.err = await evalIn('(document.getElementById("su-err") || {}).textContent || ""');
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class NewProjectWithJustAName(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-np-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "shots": shots}
        script = base / "new_project_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_the_hint_follows_the_name(self):
        g = self.got
        self.assertIn("named after the project", g["before"])
        self.assertEqual(g["mid"], f"Leave empty: {self.root / 'My-Day'} will be created")
        self.assertEqual(g["hint"], f"Leave empty: {self.root / 'My-Day-Job'} will be created")
        self.assertIn("as a new git repository", g["note"])
        self.assertEqual(g["value"], "", "a hint, not a value")

    def test_create_with_the_folder_empty(self):
        self.assertEqual(self.got["err"], "")
        proj = dashboard.find_project(self.got["made"])
        self.assertEqual(proj["name"], "My Day Job")
        folder = self.root / "My-Day-Job"
        self.assertEqual(os.path.normcase(proj["path"]), os.path.normcase(str(folder)))
        log = subprocess.run(["git", "-C", str(folder), "log", "--format=%s"], capture_output=True,
                             text=True, encoding="utf-8")
        self.assertEqual(log.stdout.strip(), "Initial commit")


if __name__ == "__main__":
    unittest.main()
