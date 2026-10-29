"""#286 (GitHub issue 30): what Open in editor opens.

Settings › Editor names one editor for every project, or leaves it to
Automatic: the repo's language's editor when it is installed, else the first
editor installed (JetBrains first), else the folder. The editors are found
where they really are: on Windows IntelliJ is often not on PATH (the
installer's folders, the Toolbox's, or wherever a version's ``.home`` file
says); on macOS the .app, Toolbox installs included. Every install here is a
fake under a temp folder; nothing of this computer's editors is read.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backends import base, linux, macos, windows  # noqa: E402


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ens-ed-", ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        # Nothing on PATH, and no editors.json, unless a test says so.
        for p in (mock.patch.object(base.shutil, "which", lambda *_a, **_k: None),
                  mock.patch.object(windows.shutil, "which", lambda *_a, **_k: None),
                  mock.patch.object(base, "EDITOR_MAP_FILE", self.t / "editors.json")):
            p.start()
            self.addCleanup(p.stop)


class WindowsFindsEditors(_Tmp):
    def setUp(self):
        super().setUp()
        self.local = self.t / "Local"
        self.pf = self.t / "PF"
        env = {"LOCALAPPDATA": str(self.local), "ProgramFiles": str(self.pf),
               "ProgramW6432": "", "ProgramFiles(x86)": "", "WINDIR": str(self.t / "win")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.b = windows.WindowsBackend()

    def test_a_home_file_finds_an_install_outside_every_standard_folder(self):
        exe = _touch(self.t / "tools" / "idea" / "bin" / "idea64.exe")
        _touch(self.local / "JetBrains" / "IntelliJIdea2026.1" / ".home").write_text(
            str(exe.parent.parent), encoding="utf-8")
        # An older version whose folder is gone is not offered.
        (self.local / "JetBrains" / "IntelliJIdea2025.1").mkdir(parents=True)
        (self.local / "JetBrains" / "IntelliJIdea2025.1" / ".home").write_text(
            str(self.t / "gone"), encoding="utf-8")
        self.assertEqual(self.b.find_editor("idea"), str(exe))

    def test_an_empty_or_relative_home_file_is_skipped(self):
        # "" was Path("."): the hub's own folder was looked in.
        cwd = self.t / "cwd"
        _touch(cwd / "bin" / "idea64.exe")
        _touch(self.local / "JetBrains" / "IntelliJIdea2026.1" / ".home").write_text("", encoding="utf-8")
        _touch(self.local / "JetBrains" / "IntelliJIdea2026.2" / ".home").write_text(".", encoding="utf-8")
        old = os.getcwd()
        os.chdir(cwd)
        try:
            self.assertIsNone(self.b.find_editor("idea"))
        finally:
            os.chdir(old)

    def test_the_installers_and_the_toolbox_folders(self):
        cases = {
            "programs": self.local / "Programs" / "IntelliJ IDEA Ultimate" / "bin" / "idea64.exe",
            "program files": self.pf / "JetBrains" / "IntelliJ IDEA 2026.1" / "bin" / "idea64.exe",
            "toolbox app": self.local / "JetBrains" / "Toolbox" / "apps" / "IDEA-U" / "ch-0" / "261.1" / "bin" / "idea64.exe",
            "toolbox script": self.local / "JetBrains" / "Toolbox" / "scripts" / "idea.cmd",
        }
        for name, exe in cases.items():
            with self.subTest(name):
                _touch(exe)
                self.assertEqual(windows.WindowsBackend().find_editor("idea"), str(exe))
                exe.unlink()

    def test_vs_code_under_programs(self):
        exe = _touch(self.local / "Programs" / "Microsoft VS Code" / "Code.exe")
        self.assertEqual(self.b.find_editor("code"), str(exe))
        self.assertEqual([e["id"] for e in self.b.installed_editors()], ["code"])

    def test_the_choice(self):
        idea = _touch(self.pf / "JetBrains" / "IntelliJ IDEA 2026.1" / "bin" / "idea64.exe")
        b = self.b
        # Automatic: Java has its editor; Python's (PyCharm) and the default
        # (VS Code) are missing, so the first installed one opens.
        self.assertEqual((b.choose_editor("java")["id"], b.choose_editor("java")["how"]), ("idea", "language"))
        py = b.choose_editor("python")
        self.assertEqual((py["id"], py["label"], py["path"], py["how"]),
                         ("idea", "IntelliJ IDEA", str(idea), "fallback"))
        self.assertEqual(b.choose_editor("default")["id"], "idea")
        self.assertEqual(b.editor_button_label({}), "IntelliJ IDEA")
        # The setting names one: it opens, found or not, and says so.
        pc = b.choose_editor("java", {"editor": "pycharm"})
        self.assertEqual((pc["id"], pc["label"], pc["path"], pc["how"]), ("pycharm", "PyCharm", "", "chosen"))
        # ...but the button does not say it opens: the folder does.
        self.assertEqual(b.editor_button_label({"editor": "pycharm"}), "")
        self.assertEqual(b.editor_button_label({"editor": "custom", "editorCommand": "nowhere"}), "")
        self.assertEqual(b.editor_button_label({"editor": "idea"}), "IntelliJ IDEA")
        # Other command…
        tool = _touch(self.t / "bin" / "subl.exe")
        c = b.choose_editor("java", {"editor": "custom", "editorCommand": str(tool)})
        self.assertEqual((c["id"], c["label"], c["path"]), (str(tool), "subl", str(tool)))
        # Other command… with no command yet is Automatic.
        self.assertEqual(b.choose_editor("python", {"editor": "custom", "editorCommand": ""})["id"], "idea")

    def test_the_language_editor_wins_when_installed(self):
        _touch(self.pf / "JetBrains" / "IntelliJ IDEA 2026.1" / "bin" / "idea64.exe")
        _touch(self.pf / "JetBrains" / "PyCharm 2026.2" / "bin" / "pycharm64.exe")
        self.assertEqual(self.b.choose_editor("python")["id"], "pycharm")
        self.assertEqual(self.b.editor_button_label({}), "", "it depends on the repo")

    def test_jetbrains_comes_before_other_editors_as_the_fallback(self):
        _touch(self.local / "Programs" / "cursor" / "Cursor.exe")
        _touch(self.pf / "JetBrains" / "WebStorm 2026.1" / "bin" / "webstorm64.exe")
        self.assertEqual(self.b.choose_editor("default")["id"], "webstorm")

    def test_nothing_installed_opens_the_folder(self):
        c = self.b.choose_editor("python")
        self.assertEqual((c["id"], c["how"]), ("", "none"))
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(self.t), c["id"] or "default"), "ok")
        self.assertTrue(popen.call_args[0][0][0].endswith("explorer"))

    def test_an_editors_json_still_counts_for_automatic(self):
        _touch(self.pf / "JetBrains" / "IntelliJ IDEA 2026.1" / "bin" / "idea64.exe")
        _touch(self.pf / "JetBrains" / "PyCharm 2026.2" / "bin" / "pycharm64.exe")
        (self.t / "editors.json").write_text(json.dumps({"python": "IntelliJ IDEA"}), encoding="utf-8")
        self.assertEqual(self.b.choose_editor("python")["id"], "idea")

    def test_opening(self):
        idea = _touch(self.pf / "JetBrains" / "IntelliJ IDEA 2026.1" / "bin" / "idea64.exe")
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(self.t), "idea"), "ok")
        self.assertEqual(popen.call_args[0][0], [str(idea), str(self.t)])
        self.assertEqual(popen.call_args[1]["creationflags"], 0)
        # A Toolbox script runs hidden: the hub has no console to flash.
        script = _touch(self.local / "JetBrains" / "Toolbox" / "scripts" / "pycharm.cmd")
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(self.t), "pycharm"), "ok")
        self.assertEqual(popen.call_args[0][0][0], str(script))
        self.assertEqual(popen.call_args[1]["creationflags"], windows._CREATE_NO_WINDOW)

    def test_a_missing_editor_names_itself_and_settings(self):
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            r = self.b.open_path(str(self.t), "code")
        self.assertIn("VS Code was not found", r)
        self.assertIn("Explorer", r)
        self.assertTrue(r.endswith("Settings › Editor."), r)
        self.assertTrue(popen.call_args[0][0][0].endswith("explorer"))


class MacFindsEditors(_Tmp):
    def setUp(self):
        super().setUp()
        self.apps = self.t / "Applications"
        self.user_apps = self.t / "home" / "Applications"
        self.caches = self.t / "home" / "Library" / "Caches" / "JetBrains"
        self.b = macos.MacBackend.__new__(macos.MacBackend)   # no iTerm watcher
        for p in (mock.patch.object(macos.MacBackend, "_app_dirs", lambda _s: [
                      self.apps, self.user_apps, self.user_apps / "JetBrains Toolbox"]),
                  mock.patch.object(macos.MacBackend, "_caches_dir", lambda _s: self.caches),
                  mock.patch.object(macos.shutil, "which", lambda *_a, **_k: None)):
            p.start()
            self.addCleanup(p.stop)

    def app(self, folder: Path, name: str) -> Path:
        a = folder / f"{name}.app"
        (a / "Contents").mkdir(parents=True)
        return a

    def test_apps_by_name_toolbox_included(self):
        ce = self.app(self.user_apps / "JetBrains Toolbox", "IntelliJ IDEA CE")
        self.assertEqual(self.b.find_editor("idea"), str(ce))
        code = self.app(self.apps, "Visual Studio Code")
        self.assertEqual([e["id"] for e in self.b.installed_editors()], ["idea", "code"])
        self.assertEqual(self.b.find_editor("code"), str(code))

    def test_a_home_file_finds_the_app_wherever_it_is(self):
        a = self.app(self.t / "elsewhere", "IntelliJ IDEA")
        _touch(self.caches / "IntelliJIdea2026.1" / ".home").write_text(str(a / "Contents"), encoding="utf-8")
        self.assertEqual(self.b.find_editor("idea"), str(a))

    def test_the_choice(self):
        self.app(self.apps, "IntelliJ IDEA")
        self.assertEqual(self.b.choose_editor("python")["how"], "fallback")
        self.assertEqual(self.b.choose_editor("python")["id"], "idea")
        self.assertEqual(self.b.choose_editor("python", {"editor": "cursor"})["path"], "")
        sub = self.app(self.apps, "Sublime Text")
        c = self.b.choose_editor("java", {"editor": "custom", "editorCommand": "Sublime Text"})
        self.assertEqual((c["label"], c["path"]), ("Sublime Text", str(sub)))
        for p in self.apps.iterdir():
            os.rename(p, self.t / p.name)
        fresh = macos.MacBackend.__new__(macos.MacBackend)    # the last look is held a while
        self.assertEqual(fresh.choose_editor("python")["how"], "none")

    def test_opening(self):
        a = self.app(self.apps, "IntelliJ IDEA")
        ok = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(macos.subprocess, "run", return_value=ok) as run:
            self.assertEqual(self.b.open_path(str(self.t), "idea"), "ok")
        self.assertEqual(run.call_args[0][0], ["open", "-na", str(a), "--args", str(self.t)])

    def test_a_missing_editor_asks_launch_services_then_opens_the_folder(self):
        no = mock.Mock(returncode=1, stderr="Unable to find application named 'Cursor'")
        ok = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(macos.subprocess, "run", side_effect=[no, ok]) as run:
            r = self.b.open_path(str(self.t), "cursor")
        self.assertEqual(run.call_args_list[0][0][0], ["open", "-na", "Cursor", "--args", str(self.t)])
        self.assertEqual(run.call_args_list[1][0][0], ["open", str(self.t)])
        self.assertIn("Cursor was not found on this Mac", r)
        self.assertTrue(r.endswith("Settings › Editor."), r)


class LinuxFindsEditors(_Tmp):
    def test_toolbox_script_and_a_missing_editor(self):
        b = linux.LinuxBackend()
        script = _touch(self.t / ".local" / "share" / "JetBrains" / "Toolbox" / "scripts" / "idea")
        with mock.patch.object(linux.Path, "home", lambda: self.t), \
                mock.patch.object(linux.shutil, "which", lambda *_a, **_k: None):
            self.assertEqual(b.find_editor("idea"), str(script))
            self.assertEqual(b.choose_editor("python")["id"], "idea")
            with mock.patch.object(linux.subprocess, "Popen"):
                self.assertIn("PyCharm was not found", b.open_path(str(self.t), "pycharm"))


class TheSetting(unittest.TestCase):
    def test_saved_values(self):
        import dashboard
        with tempfile.TemporaryDirectory(prefix="ens-eds-", ignore_cleanup_errors=True) as d, \
                mock.patch.object(dashboard, "SETTINGS_FILE", Path(d) / "settings.json"), \
                mock.patch.object(dashboard, "DASHBOARD_DIR", Path(d)):
            self.assertEqual(dashboard.editor_setting(), {"editor": "", "editorCommand": ""})
            self.assertEqual(dashboard.save_settings({"editor": "idea"})["editor"], "idea")
            self.assertEqual(dashboard.save_settings({"editor": "notepad"})["editor"], "idea", "not an editor it knows")
            s = dashboard.save_settings({"editor": "custom", "editorCommand": "  subl  "})
            self.assertEqual((s["editor"], s["editorCommand"]), ("custom", "subl"))
            self.assertEqual(dashboard.save_settings({"editorCommand": "a\nb"})["editorCommand"], "subl")
            self.assertEqual(dashboard.save_settings({"editor": ""})["editor"], "")


if __name__ == "__main__":
    unittest.main()
