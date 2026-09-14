"""The hub never opens a console window for a program it only reads.

On Windows a console program (git, tailscale, taskkill, claude -p) started by
a host that has no console of its own -- pythonw.exe under the scheduled task
-- gets a brand-new console window, which takes the keyboard focus for the
life of the call. A `git status` per projects poll made the whole desktop
flicker until the hub was killed (2026-09-14). Two guards, both checked here:
the hub attaches a hidden console to itself at startup, before its first
spawn, and every subprocess.run of a console program asks for no window."""
from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
from backends import ptyrun  # noqa: E402

# Every module the hub runs console programs from. backends/macos.py and
# linux.py never run under a console-less Windows host.
FILES = ["dashboard.py", "backup.py", "digest.py", "workspace_search.py",
         "backends/windows.py", "backends/ptyrun.py"]


def _spawns(rel: str):
    """(lineno, keyword names) of every subprocess.run / Popen call in a file,
    minus the one inside dashboard._run, which forwards **kw and is checked by
    behaviour below."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run":
            skip.update(range(node.lineno, node.end_lineno + 1))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in ("run", "check_output", "check_call", "call", "Popen")):
            continue
        if node.lineno in skip:
            continue
        out.append((node.lineno, node.func.attr, {k.arg for k in node.keywords}))
    return out


class NoConsoleWindow(unittest.TestCase):
    def test_every_console_spawn_asks_for_no_window(self):
        bare = []
        for rel in FILES:
            for lineno, kind, kws in _spawns(rel):
                # Popen in the Windows backend opens things the user asked to
                # see (a terminal, an editor, Explorer): windows on purpose.
                if kind == "Popen" and rel == "backends/windows.py":
                    continue
                if "creationflags" not in kws:
                    bare.append(f"{rel}:{lineno} subprocess.{kind}")
        self.assertEqual(bare, [], "console spawns without creationflags: use "
                                   "dashboard._run or pass creationflags=_NO_WINDOW")

    def test_dashboard_runs_console_programs_through_run(self):
        # No `subprocess.run(` left in dashboard.py outside the helper itself.
        self.assertEqual([s for s in _spawns("dashboard.py") if s[1] == "run"], [])

    def test_run_never_shows_a_window(self):
        with mock.patch("subprocess.run") as run:
            dashboard._run(["git", "--version"], capture_output=True, timeout=3)
        self.assertEqual(run.call_args.kwargs["creationflags"], dashboard._NO_WINDOW)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        if sys.platform == "win32":
            self.assertEqual(dashboard._NO_WINDOW, subprocess.CREATE_NO_WINDOW)
        else:
            self.assertEqual(dashboard._NO_WINDOW, 0)

    def test_hub_takes_a_hidden_console_before_its_first_spawn(self):
        src = inspect.getsource(dashboard.main)
        at = src.index("ptyrun.ensure_windows_console()")
        self.assertLess(at, src.index("_detect_tailscale_ip()"))
        self.assertLess(at, src.index("backup.ensure_repo("))

    @unittest.skipUnless(sys.platform == "win32", "Windows console")
    def test_ensure_windows_console_attaches_one_and_is_idempotent(self):
        import ctypes
        ptyrun.ensure_windows_console()
        ptyrun.ensure_windows_console()
        # A console is attached (GetConsoleCP is 0 without one). Its window may
        # not exist: a host started with CREATE_NO_WINDOW has a windowless one.
        self.assertTrue(ctypes.windll.kernel32.GetConsoleCP())

    @unittest.skipUnless(sys.platform == "win32", "pythonw.exe")
    def test_a_windowless_host_gets_a_hidden_console(self):
        # The production case: pythonw.exe (the scheduled task) has no console
        # at all. After the call it has one, and its window is hidden.
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("no pythonw.exe next to the interpreter")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            prog = "; ".join([
                "import ctypes, sys",
                "sys.path.insert(0, %r)" % str(ROOT),
                "from backends import ptyrun",
                "k = ctypes.windll.kernel32",
                "u = ctypes.windll.user32",
                "before = k.GetConsoleWindow()",
                "ptyrun.ensure_windows_console()",
                "h = k.GetConsoleWindow()",
                "vis = u.IsWindowVisible(h) if h else -1",
                "open(%r, 'w').write('%%d %%d %%d' %% (before, h, vis))" % str(out),
            ])
            subprocess.run([str(pythonw), "-c", prog], timeout=30, check=True)
            before, hwnd, visible = (int(x) for x in out.read_text().split())
        self.assertEqual(before, 0)
        self.assertNotEqual(hwnd, 0)
        self.assertEqual(visible, 0)


if __name__ == "__main__":
    unittest.main()
