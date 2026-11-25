"""First-run setup uses Settings' persisted keys and only opens on a new hub."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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
