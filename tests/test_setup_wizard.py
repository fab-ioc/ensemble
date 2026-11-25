"""First-run setup uses Settings' persisted keys and only opens on a new hub."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dashboard


class SetupWizardSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ens-wizard-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for patch in (
            mock.patch.object(dashboard, "DASHBOARD_DIR", base),
            mock.patch.object(dashboard, "SETTINGS_FILE", base / "settings.json"),
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "projects"),
            mock.patch.object(dashboard, "_SETUP_FRESH_AT_BOOT", True),
            mock.patch.object(dashboard, "load_projects", return_value=[]),
            mock.patch.object(dashboard.usage, "remark_codex"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_new_projectless_hub_is_pending_once_until_finished(self):
        self.assertEqual(dashboard.setup_wizard_status(), {"show": True, "done": False})
        self.assertTrue(dashboard.load_settings()["setupWizardPending"])
        dashboard.save_settings({"setupWizardDone": True, "setupWizardPending": False})
        self.assertEqual(dashboard.setup_wizard_status(), {"show": False, "done": True})

    def test_pending_survives_a_restart_mid_setup(self):
        self.assertTrue(dashboard.setup_wizard_status()["show"])
        with mock.patch.object(dashboard, "_SETUP_FRESH_AT_BOOT", False):
            self.assertTrue(dashboard.setup_wizard_status()["show"])

    def test_existing_install_never_auto_opens(self):
        with mock.patch.object(dashboard, "_SETUP_FRESH_AT_BOOT", False):
            self.assertFalse(dashboard.setup_wizard_status()["show"])
        with mock.patch.object(dashboard, "load_projects", return_value=[{"id": "old"}]):
            self.assertFalse(dashboard.setup_wizard_status()["show"])

    def test_migration_marks_import_time_freshness_as_existing(self):
        (dashboard.DASHBOARD_DIR / dashboard.legacy_install.MARKER_NAME).touch()
        dashboard._setup_after_migration()
        self.assertFalse(dashboard._SETUP_FRESH_AT_BOOT)
        self.assertFalse(dashboard.setup_wizard_status()["show"])

    def test_tailnet_address_prefers_serve_then_name_then_ip(self):
        with mock.patch.object(dashboard, "HUB_PORT", 8765), \
             mock.patch.object(dashboard.hub_links, "https_address", return_value="hub.example.ts.net") as serve, \
             mock.patch.object(dashboard.hub_links, "origins", return_value={"seen": [], "names": ["host.example.ts.net"]}), \
             mock.patch.object(dashboard, "_detect_tailscale_ip", return_value="100.99.0.2"):
            self.assertEqual(dashboard._setup_tailnet_address(), "https://hub.example.ts.net")
            serve.return_value = ""
            self.assertEqual(dashboard._setup_tailnet_address(), "http://host.example.ts.net:8765")
            with mock.patch.object(dashboard.hub_links, "origins", return_value={"seen": [], "names": []}):
                self.assertEqual(dashboard._setup_tailnet_address(), "http://100.99.0.2:8765")

    def test_sensitive_settings_refuse_agent_even_with_page_cookie(self):
        def put(body, agent):
            raw = json.dumps(body).encode()
            h = dashboard.Handler.__new__(dashboard.Handler)
            h.path, h.command, h.request_version = "/api/settings", "PUT", "HTTP/1.1"
            h.requestline = "PUT /api/settings HTTP/1.1"
            h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                         "Host": "127.0.0.1:8791", "Origin": "http://127.0.0.1:8791",
                         "Sec-Fetch-Site": "same-origin",
                         "Cookie": f"ensemble_ui_8791={dashboard._UI_KEY}"}
            h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
            h.client_address = ("127.0.0.1", 50000)
            h.server = SimpleNamespace(server_address=("127.0.0.1", 8791))
            h.log_message = lambda *a: None
            with mock.patch.object(dashboard.Handler, "_agent_peer", return_value="agent process" if agent else ""), \
                 mock.patch.object(dashboard.app_version, "packaged", return_value=True), \
                 mock.patch.object(dashboard.app_setup, "set_autostart", return_value={"enabled": True}) as autostart:
                h.do_PUT()
            head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
            return int(head.split(b" ", 2)[1]), json.loads(payload), autostart.call_count

        with mock.patch.object(dashboard, "HUB_PORT", 8791):
            for key, value in (("projectsRoot", str(Path(self.tmp.name) / "new-home")),
                               ("remoteAccessEnabled", True), ("startAtSignIn", True)):
                with self.subTest(key=key):
                    status, _, calls = put({key: value, "theme": "dark"}, agent=True)
                    self.assertEqual(status, 403)
                    self.assertEqual(calls, 0)
                    self.assertEqual(dashboard.load_settings()["theme"], "")
                    status, saved, _ = put({key: value}, agent=False)
                    self.assertEqual(status, 200)
                    self.assertEqual(saved[key], value)
            with mock.patch.object(dashboard, "HOME", Path(self.tmp.name)):
                status, saved, _ = put({"projectsRoot": "relative-home"}, agent=False)
            self.assertEqual(status, 200)
            self.assertEqual(saved["projectsRoot"], str(Path(self.tmp.name) / "relative-home"))

    def test_wizard_answers_are_settings_values(self):
        values = {
            "projectNoun": {"one": "Product", "many": "Products"},
            "claudePlanWeight": 20,
            "codexPlanWeight": 5,
            "preferAgent": "codex",
            "paceWeek": False,
            "projectsRoot": str(Path(self.tmp.name) / "new-home"),
            "theme": "paper",
            "notifyDeviceChoices": {"test-device": True},
            "remoteAccessEnabled": True,
            "startAtSignIn": True,
        }
        saved = dashboard.save_settings(values)
        settings_read = dashboard.load_settings()
        for key, value in values.items():
            with self.subTest(key=key):
                self.assertEqual(saved[key], value)
                self.assertEqual(settings_read[key], value)

    def test_projects_root_rejects_file_and_nonempty_folder(self):
        root_file = Path(self.tmp.name) / "file"
        root_file.write_text("occupied", encoding="utf-8")
        occupied = Path(self.tmp.name) / "occupied"
        occupied.mkdir()
        (occupied / "entry").touch()
        self.assertFalse(dashboard.projects_root_change_allowed(str(root_file)))
        self.assertFalse(dashboard.projects_root_change_allowed(str(occupied)))
        self.assertTrue(dashboard.projects_root_change_allowed(str(Path(self.tmp.name) / "fresh")))
        original = dashboard.PROJECTS_ROOT
        self.assertEqual(dashboard.save_settings({"projectsRoot": str(root_file)})["projectsRoot"], "")
        self.assertEqual(dashboard.PROJECTS_ROOT, original)


if __name__ == "__main__":
    unittest.main()
