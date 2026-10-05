"""Dock v0.13.0 and Ensemble's IntelliJ Dark theme contract."""
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

    def test_the_vendored_theme_set_is_dock_v0_13_0(self):
        version = (ROOT / "static" / "dock" / "VERSION").read_text(encoding="utf-8")
        self.assertEqual(
            version.strip(),
            "fab-ioc/dock v0.13.0 23363ebfa934c5c1d83346a018e9cdd9d6e65604 (tag v0.13.0, 2026-10-05)",
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
        self.assertEqual(set(result["states"]), {"running", "waiting", "blocked", "done"})
        self.assertEqual(len({row["colour"] for row in result["states"].values()}), 4)
        self.assertGreaterEqual(min(row["on_surface"] for row in result["states"].values()), 3)


if __name__ == "__main__":
    unittest.main()
