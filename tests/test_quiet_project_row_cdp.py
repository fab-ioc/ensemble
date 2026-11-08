"""#317: a project with no PO and no tasks still has a row in the desktop list.

In headless Chrome over CDP, against a hub in a thread (its own state in a temp
dir), at 1728x1117 and 1280x800 with a mouse. Inheritance is a documents
project with no PO and no tasks; Motors has a PO, so the list is not empty:

* the list shows a folded "Other projects" group with a count of 1;
* a click on its head unfolds it, and Inheritance is a row there, inside the
  list, saying it has no PO;
* a click on the row opens the project; the unfolded state is kept after a
  reload (per browser).

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

CDP_JS = HEAD + r"""
async function main() {
  const { ch, ws } = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [width, height] of [[1728, 1117], [1280, 800]]) {
      const R = out[width] = {};
      const { browserContextId } = await c.send('Target.createBrowserContext');
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank', browserContextId });
      const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, s);
      await c.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, s);
      const ev = async expression => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
      const until = async (expression, n = 180) => { for (let i = 0; i < n; i++) { try { if (await ev(expression)) return true; } catch {} await sleep(100); } return false; };
      const shot = async name => { if (!A.shots) return; const { data } = await c.send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, `quiet-project-${width}-${name}.png`), Buffer.from(data, 'base64')); };
      const click = async (x, y) => { await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, s); await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, s); await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, s); };
      const box = e => `(() => { const k = ${e}; if (!k) return null; const b = k.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2, l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; })()`;
      await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensListDocked = true;' }, s);   // the task list shown, as Panels does
      await c.send('Page.navigate', { url: A.base + '/' }, s);
      const GRP = `document.querySelector('#sw-list details[data-group="quiet"]')`;
      const ROW = `document.querySelector('#sw-list details[data-group="quiet"] [data-proj="${A.quiet}"]')`;
      R.group = await until(`!!${GRP}`);
      if (!R.group) { R.list = await ev(`(document.querySelector('#sw-list') || {}).innerHTML || 'no list'`); continue; }
      await sleep(300);
      R.folded = await ev(`!${GRP}.open`);
      R.head = await ev(`[...${GRP}.querySelectorAll('summary > span')].map(x => x.textContent.trim()).join(' ')`);
      R.inPoList = await ev(`!!document.querySelector('#sw-list [data-po="${A.quiet}"]')`);
      R.list = await ev(box(`document.querySelector('#sw-list')`));
      const sum = await ev(box(`${GRP}.querySelector('summary')`));
      R.summary = sum;
      await click(sum.x, sum.y);
      await until(`${GRP}.open`, 20);
      await ev(`${ROW} && ${ROW}.scrollIntoView({ block: 'nearest' }), 0`);
      await sleep(200);
      R.row = await ev(box(ROW));
      R.rowText = await ev(`${ROW} ? ${ROW}.innerText : ''`);
      R.list2 = await ev(box(`document.querySelector('#sw-list')`));
      await shot('unfolded');
      if (R.row) {
        await click(R.row.x, R.row.y);
        R.opened = (await until(`SELECTED_PROJECT === ${JSON.stringify(A.quiet)}`, 30)) ? await ev(`SELECTED_PROJECT`) : await ev(`SELECTED_PROJECT || ''`);
        await sleep(300);
        await shot('opened');
      }
      // The fold is kept per browser.
      await c.send('Page.navigate', { url: A.base + '/' }, s);
      await until(`!!${GRP}`);
      await sleep(300);
      R.keptOpen = await ev(`${GRP}.open`);
      await c.send('Target.closeTarget', { targetId });
      await c.send('Target.disposeBrowserContext', { browserContextId });
    }
  } finally { ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class AQuietProjectRow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-qp-", ignore_cleanup_errors=True)
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
            cls.addClassCleanup(p.stop)
        ok, motors, _ = dashboard.register_project("Motors")
        assert ok, motors
        home = Path(motors.get("home") or motors["path"])
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], motors["id"])
        ok, why = dashboard.set_project_po(motors["id"], po["id"])
        assert ok, why
        ok, quiet, _ = dashboard.register_project("Inheritance")
        assert ok, quiet
        ok, why = dashboard.set_project_kind(quiet["id"], "documents")
        assert ok, why
        (Path(quiet.get("home") or quiet["path"]) / "estate.csv").write_text("a,b\n", encoding="utf-8")
        cls.quiet = quiet["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**test_middle.chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "quiet": cls.quiet, "shots": shots}
        script = base / "quiet_project_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_quiet_project_has_a_row_and_it_opens_the_project(self):
        for w in ("1728", "1280"):
            with self.subTest(w=w):
                g = self.got[w]
                self.assertTrue(g["group"], g)
                self.assertTrue(g["folded"], g)
                self.assertEqual(g["head"], "Other projects 1", g)
                self.assertFalse(g["inPoList"], g)
                row, lst = g["row"], g["list2"]
                self.assertIsNotNone(row, g)
                self.assertGreater(row["h"], 0, g)
                self.assertGreaterEqual(row["l"], lst["l"] - 1, g)
                self.assertLessEqual(row["r"], lst["r"] + 1, g)
                self.assertGreaterEqual(row["t"], lst["t"] - 1, g)
                self.assertLessEqual(row["b"], lst["b"] + 1, g)
                self.assertIn("Inheritance", g["rowText"], g)
                self.assertIn("with no PO", g["rowText"], g)
                self.assertEqual(g["opened"], self.quiet, g)
                self.assertTrue(g["keptOpen"], g)


if __name__ == "__main__":
    unittest.main()
