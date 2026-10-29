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
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backends import base, linux, macos, windows  # noqa: E402


def _fresh(b) -> None:
    """Forget the held look for editors (it holds _EDITOR_FIND_TTL)."""
    b.__dict__.pop("_editor_found", None)


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
        _fresh(self.b)
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(self.t), "pycharm"), "ok")
        self.assertEqual(popen.call_args[0][0][0], str(script))
        self.assertEqual(popen.call_args[1]["creationflags"], windows._CREATE_NO_WINDOW)

    def test_other_command_with_arguments(self):
        # PO 2: a quoted path and flags, or a name and a flag, is a program
        # and its arguments; the arguments come before the folder.
        tool = _touch(self.t / "My Tools" / "subl.exe")
        line = f'"{tool}" -n --wait'
        c = self.b.choose_editor("java", {"editor": "custom", "editorCommand": line})
        self.assertEqual((c["label"], c["path"]), ("subl", str(tool)))
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(self.t), line, command=True), "ok")
        self.assertEqual(popen.call_args[0][0], [str(tool), "-n", "--wait", str(self.t)])
        # An unquoted path with blanks is one program.
        c = self.b.choose_editor("java", {"editor": "custom", "editorCommand": str(tool)})
        self.assertEqual(c["path"], str(tool))
        # A name on PATH with a flag.
        code = str(self.t / "bin" / "code.cmd")
        with mock.patch.object(base.shutil, "which", lambda p, *_a, **_k: code if p == "code" else None):
            c = self.b.choose_editor("java", {"editor": "custom", "editorCommand": "code -n"})
            self.assertEqual((c["label"], c["path"]), ("code", code))

    def test_other_command_is_never_a_catalogue_editor(self):
        # PO 1: "IntelliJ IDEA" typed as a command is that command, not the
        # newest IntelliJ found.
        _touch(self.pf / "JetBrains" / "IntelliJ IDEA 2026.1" / "bin" / "idea64.exe")
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            r = self.b.open_path(str(self.t), "IntelliJ IDEA", command=True)
        self.assertIn("IntelliJ was not found", r)        # its program, "IntelliJ"
        self.assertTrue(popen.call_args[0][0][0].endswith("explorer"))

    def test_a_script_is_never_handed_a_folder_cmd_would_misread(self):
        # PO 3: a .cmd runs through cmd.exe; "a&b" would run "b".
        script = _touch(self.local / "JetBrains" / "Toolbox" / "scripts" / "idea.cmd")
        for name in ("a&b", "100%x", "c^d"):
            with self.subTest(name):
                folder = self.t / name
                folder.mkdir()
                with mock.patch.object(windows.subprocess, "Popen") as popen:
                    r = self.b.open_path(str(folder), "idea")
                self.assertIn("opened in Explorer instead", r)
                self.assertTrue(r.endswith("Settings › Editor."), r)
                self.assertEqual(len(popen.call_args_list), 1)
                self.assertTrue(popen.call_args[0][0][0].endswith("explorer"))
        # A plain folder still opens through it.
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(self.t), "idea"), "ok")
        self.assertEqual(popen.call_args[0][0], [str(script), str(self.t)])

    def test_a_toolbox_script_runs_the_ide_it_names(self):
        # PO 3: the real idea64.exe, not cmd.exe, when the script names it.
        exe = _touch(self.t / "tb" / "IDEA" / "bin" / "idea64.exe")
        script = _touch(self.local / "JetBrains" / "Toolbox" / "scripts" / "idea.cmd")
        script.write_text(f'@echo off\r\nstart "" "{exe}" %*\r\n', encoding="utf-8")
        self.assertEqual(self.b.find_editor("idea"), str(exe))
        folder = self.t / "a&b"
        folder.mkdir()
        with mock.patch.object(windows.subprocess, "Popen") as popen:
            self.assertEqual(self.b.open_path(str(folder), "idea"), "ok")
        self.assertEqual(popen.call_args[0][0], [str(exe), str(folder)])
        self.assertEqual(popen.call_args[1]["creationflags"], 0)

    def test_one_look_lists_the_toolbox_once(self):
        # PO 5: the Toolbox's installs are listed once per look, not per IDE.
        with mock.patch.object(windows, "_toolbox_builds", wraps=windows._toolbox_builds) as tb:
            self.b.installed_editors()
            self.b.find_editor("pycharm")
        self.assertEqual(tb.call_count, 1)

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
        _fresh(self.b)
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

    def test_other_command_runs_the_app_typed(self):
        # PO 1: "IntelliJ IDEA 2023.app" is that app, not the newest IntelliJ.
        self.app(self.apps, "IntelliJ IDEA")
        old = self.app(self.apps, "IntelliJ IDEA 2023")
        ok = mock.Mock(returncode=0, stderr="")
        for line, extra in (("IntelliJ IDEA 2023.app", []), (f'"{old}" -e', ["-e"]),
                            (f"'{old}'", [])):
            with self.subTest(line), mock.patch.object(macos.subprocess, "run", return_value=ok) as run:
                c = self.b.choose_editor("java", {"editor": "custom", "editorCommand": line})
                self.assertEqual(c["path"], str(old))
                self.assertEqual(self.b.open_path(str(self.t), line, command=True), "ok")
                self.assertEqual(run.call_args[0][0], ["open", "-na", str(old), "--args", *extra, str(self.t)])
        # One not found is asked for by its own name, not the catalogue's.
        no = mock.Mock(returncode=1, stderr="Unable to find application")
        with mock.patch.object(macos.subprocess, "run", side_effect=[no, ok]) as run:
            r = self.b.open_path(str(self.t), "IntelliJ IDEA 2019 -e", command=True)
        self.assertEqual(run.call_args_list[0][0][0][:3], ["open", "-na", "IntelliJ"])
        self.assertIn("not found on this Mac", r)

    def test_a_command_on_path_with_arguments(self):
        # PO 2: "code -n" is code, with -n before the folder.
        code = str(self.t / "bin" / "code")
        which = lambda p, *_a, **_k: code if p == "code" else None
        with mock.patch.object(base.shutil, "which", which), \
                mock.patch.object(macos.shutil, "which", which), \
                mock.patch.object(macos.subprocess, "Popen") as popen:
            c = self.b.choose_editor("java", {"editor": "custom", "editorCommand": "code -n"})
            self.assertEqual((c["label"], c["path"]), ("code", code))
            self.assertEqual(self.b.open_path(str(self.t), "code -n", command=True), "ok")
        self.assertEqual(popen.call_args[0][0], [code, "-n", str(self.t)])

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

    def test_no_xdg_open_is_an_error_not_a_crash(self):
        # PO 4: the fallback's Popen raised out of the handler.
        b = linux.LinuxBackend()
        with mock.patch.object(linux.Path, "home", lambda: self.t), \
                mock.patch.object(linux.shutil, "which", lambda *_a, **_k: None), \
                mock.patch.object(linux.subprocess, "Popen", side_effect=FileNotFoundError("xdg-open")):
            self.assertTrue(b.open_path(str(self.t), "pycharm").startswith("error:"))


class SplittingACommand(unittest.TestCase):
    def test_windows_rules(self):
        s = lambda c: base.split_command(c, posix=False)
        self.assertEqual(s(r'"C:\Program Files\x\code.exe" -n'), [r"C:\Program Files\x\code.exe", "-n"])
        self.assertEqual(s(r"C:\tools\subl.exe --wait  a"), [r"C:\tools\subl.exe", "--wait", "a"])
        self.assertEqual(s(r'code "a b" c\d "e\"f" g\\"h i"'), ["code", "a b", r"c\d", 'e"f', "g\\h i"])
        self.assertEqual(s('x ""'), ["x", ""])

    def test_posix_rules(self):
        s = lambda c: base.split_command(c, posix=True)
        self.assertEqual(s("code -n"), ["code", "-n"])
        self.assertEqual(s("'/Applications/IntelliJ IDEA.app' -e"), ["/Applications/IntelliJ IDEA.app", "-e"])
        self.assertEqual(s('"unclosed'), ["unclosed"])


class TheLookForEditors(unittest.TestCase):
    """PO 5: page reads are served the held look; an old one is looked at
    again in the background, never on the request."""

    def test_served_from_the_held_look(self):
        b = linux.LinuxBackend()
        looks = []
        gate = threading.Event()

        def scan(_s):
            looks.append(1)
            if len(looks) > 1:
                gate.wait(5)
            return {"idea": f"/look{len(looks)}"}
        with mock.patch.object(linux.LinuxBackend, "_scan_editors", scan):
            self.assertEqual(b.find_editor("idea"), "/look1")          # the first look is waited for
            self.assertEqual(b.find_editor("idea"), "/look1")
            self.assertEqual(len(looks), 1, "held")
            b.__dict__["_editor_found"]["at"] -= base._EDITOR_FIND_TTL + 1
            t0 = time.monotonic()
            self.assertEqual(b.find_editor("idea"), "/look1", "the old look, at once")
            self.assertEqual(b.find_editor("idea"), "/look1", "one look at a time")
            self.assertLess(time.monotonic() - t0, 1)
            gate.set()
            for _ in range(100):
                if b.find_editor("idea") == "/look2":
                    break
                time.sleep(0.02)
            self.assertEqual(b.find_editor("idea"), "/look2")
            self.assertEqual(len(looks), 2)

    def test_the_hub_looks_at_its_start(self):
        b = linux.LinuxBackend()
        done = threading.Event()
        with mock.patch.object(linux.LinuxBackend, "_scan_editors", lambda _s: done.set() or {}):
            b.warm_editors()
            self.assertTrue(done.wait(5))


class OtherCommandFromThePage(unittest.TestCase):
    def test_the_hub_says_it_is_the_typed_command(self):
        # PO 1: what /api/open-path is sent runs as typed when it is
        # Settings › Editor's command, as a catalogue name otherwise.
        import dashboard
        with mock.patch.object(dashboard, "editor_setting",
                               lambda: {"editor": "custom", "editorCommand": "IntelliJ IDEA 2023.app"}), \
                mock.patch.object(dashboard.BACKEND, "open_path", return_value="ok") as op:
            dashboard.open_path("/x", "IntelliJ IDEA 2023.app")
            self.assertEqual(op.call_args, mock.call("/x", "IntelliJ IDEA 2023.app", command=True))
            dashboard.open_path("/x", "idea")
            self.assertEqual(op.call_args, mock.call("/x", "idea", command=False))


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
