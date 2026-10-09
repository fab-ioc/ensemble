"""Dock v0.14.1 and Ensemble's IntelliJ Dark theme contract."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
from tools.check_intellij_theme import measure  # noqa: E402


class IntelliJDarkTheme(unittest.TestCase):
    def test_every_page_knows_the_theme(self):
        for name in ("index.html", "session.html", "fileview.html"):
            page = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertIn("'intellij-dark': 'dark'", page)
                self.assertEqual(page.count(':root[data-theme="intellij-dark"]'), 1)

    def test_the_account_menu_has_the_requested_label_and_swatch(self):
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("'intellij-dark': ['IntelliJ Dark', '#1E1F22', '#78A4FA']", page)

    def test_the_hub_accepts_the_saved_value(self):
        self.assertIn("intellij-dark", dashboard._SETTINGS_ALLOWED_VALUES["theme"])

    def test_the_vendored_theme_set_is_dock_v0_14_0(self):
        version = (ROOT / "static" / "dock" / "VERSION").read_text(encoding="utf-8")
        self.assertEqual(
            version.strip(),
            "fab-ioc/dock v0.14.1 9a26d6dc49cb4ccc8d678849762d297bc25b7abb (tag v0.14.1, 2026-10-09)",
        )
        theme_js = (ROOT / "static" / "dock" / "src" / "theme.js").read_text(encoding="utf-8")
        self.assertIn("['intellij-dark', 'IntelliJ Dark', 'dark', 'extra']", theme_js)

    def test_the_measured_palette_passes(self):
        result = measure()
        self.assertEqual(result["ground"], "#1E1F22")
        self.assertEqual(result["panels"], "#2B2D30")
        self.assertEqual(result["drift"], {})
        self.assertEqual(result["failed"], [])
        self.assertGreaterEqual(result["minimum_ratio"], 4.5)
        self.assertEqual(set(result["states"]), {"working", "waiting", "blocked", "new"})
        self.assertEqual(result["states"]["working"]["selector"], ".sw-st.working")
        self.assertEqual(result["states"]["working"]["token"], "--run-working")
        self.assertEqual(len({row["colour"] for row in result["states"].values()}), 4)
        self.assertGreaterEqual(min(row["on_surface"] for row in result["states"].values()), 3)
        separation = result["diff_ground_separation"]
        self.assertTrue(separation["passed"])
        self.assertGreaterEqual(separation["rgb_distance"], separation["minimum"])

    def test_working_is_green_and_idle_is_not(self):
        # GitHub issue 13, point P157: green is an agent working now, and a
        # terminal running idle is a calm neutral, never green.
        for name in ("index.html", "session.html"):
            page = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertIn("--run-working: var(--c-success-bold);", page)
                self.assertIn("--run-idle: var(--c-neutral-bold);", page)
                self.assertNotIn("--run-live", page)


if __name__ == "__main__":
    unittest.main()
