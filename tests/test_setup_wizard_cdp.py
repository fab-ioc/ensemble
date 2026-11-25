"""Walk all setup screens in Chrome on an empty hub at desktop and phone width."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
from tests import chrome_profile


@unittest.skipUnless(shutil.which("node") and chrome_profile.CHROME, "needs Node and Chrome")
class SetupWizardInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-wizard-cdp-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("transcripts", "cs", "rooms", "root"):
            (base / d).mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "root"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "_SETUP_FRESH_AT_BOOT", True),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.app_setup, "agents_check", return_value={"agents": [], "ready": [], "usable": False, "missing": []}),
            mock.patch.object(dashboard.app_version, "packaged", return_value=True),
            mock.patch.object(dashboard.app_setup, "autostart_status", return_value={"enabled": False, "supported": True}),
            mock.patch.object(dashboard.app_setup, "set_autostart", return_value={"enabled": True, "supported": True}),
            mock.patch.object(dashboard.usage, "remark_codex"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for patch in cls.patches:
            patch.start()
            cls.addClassCleanup(patch.stop)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "root": str(base / "new-projects"), "shots": shots}
        script = base / "wizard_cdp.js"
        script.write_text(chrome_profile.JS + (Path(__file__).with_name("setup_wizard_cdp.js")).read_text(), encoding="utf-8")
        run = subprocess.run([shutil.which("node"), str(script), json.dumps(args)],
                             capture_output=True, text=True, timeout=240)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_every_screen_and_persisted_answers(self):
        self.assertEqual(len(self.got["screens"]), 12)
        self.assertTrue(self.got["deepLinkBypassed"])
        self.assertEqual(self.got["settings"]["projectNoun"], {"one": "Product", "many": "Products"})
        self.assertEqual(self.got["settings"]["claudePlanWeight"], 20)
        self.assertEqual(self.got["settings"]["codexPlanWeight"], 5)
        self.assertEqual(self.got["settings"]["preferAgent"], "codex")
        self.assertFalse(self.got["settings"]["paceWeek"])
        self.assertEqual(self.got["settings"]["projectsRoot"], str(Path(self.tmp.name) / "new-projects"))
        self.assertEqual(self.got["settings"]["theme"], "paper")
        self.assertIn(True, self.got["settings"]["notifyDeviceChoices"].values())
        self.assertTrue(self.got["settings"]["remoteAccessEnabled"])
        self.assertTrue(self.got["settings"]["startAtSignIn"])
        self.assertFalse(self.got["done"]["show"])
        self.assertTrue(self.got["done"]["done"])
        self.assertFalse(self.got["later"]["show"])
        self.assertEqual(self.got["afterPhoneSkip"], "codex")
        self.assertTrue(self.got["projectCreated"])

    def test_phone_geometry_and_touch_targets(self):
        self.assertEqual(len(self.got["phone"]), 12)
        for screen in self.got["phone"]:
            with self.subTest(screen=screen["title"]):
                self.assertLessEqual(screen["scroll"], 390)
                self.assertGreaterEqual(screen["card"]["left"], -1)
                self.assertLessEqual(screen["card"]["right"], 391)
                self.assertEqual(screen["short"], [])


if __name__ == "__main__":
    unittest.main()
