"""Non-ASCII text in an agent's first prompt reaches the agent unchanged.

Every launch the hub makes (fresh, rotation, failover, resume with a seed)
goes through ``BACKEND.headless_launch``, which writes a one-shot .ps1 holding
the prompt in a here-string. Windows PowerShell 5.1 reads a BOM-less script in
the ANSI code page, so "…" reached the agent as "â€¦". These tests run the
real script through PowerShell (plain, inside a PTY, and through a .cmd
wrapper the way npm's codex.cmd is reached) into a receiver that records the
argv it was given.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEXT = "A \u2026 B \u00b7 C \u2014 D \u00e9 \u00fc \u4e2d \"q\" E"
ON_WINDOWS = sys.platform == "win32"

RECEIVER = (
    "import sys\n"
    "open(sys.argv[1], 'w', encoding='utf-8').write('\\x00'.join(sys.argv[2:]))\n"
)


@unittest.skipUnless(ON_WINDOWS, "Windows launch scripts")
class LaunchScriptEncoding(unittest.TestCase):
    def setUp(self):
        from backends import windows
        self.windows = windows
        self.tmp = Path(tempfile.mkdtemp(prefix="enc-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.object(windows, "LAUNCH_DIR", self.tmp / "_launch")
        p.start()
        self.addCleanup(p.stop)
        self.backend = windows.WindowsBackend.__new__(windows.WindowsBackend)
        (self.tmp / "recv.py").write_text(RECEIVER, encoding="utf-8")
        self.out = self.tmp / "out.txt"

    def receiver_argv(self):
        return [sys.executable, str(self.tmp / "recv.py"), str(self.out)]

    def received(self) -> list[str]:
        self.assertTrue(self.out.exists(), "the receiver never ran")
        return self.out.read_text(encoding="utf-8").split("\x00")

    def test_scripts_carry_a_utf8_bom(self):
        cmd = self.backend.headless_launch(str(self.tmp), ["claude"], TEXT)
        raw = Path(cmd[-1]).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("\u2026".encode("utf-8"), raw)
        path = self.backend._write_launch_script(str(self.tmp), ["claude"], TEXT,
                                                 agent="codex", identity="c")
        self.assertTrue(Path(path).read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_prompt_reaches_the_program_unchanged(self):
        cmd = self.backend.headless_launch(str(self.tmp), self.receiver_argv(), TEXT)
        subprocess.run(cmd, cwd=self.tmp, timeout=60, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(self.received(), [TEXT])

    def test_prompt_reaches_a_cmd_wrapped_program_unchanged(self):
        # npm installs codex as codex.cmd; cmd.exe sits between PowerShell and node.
        wrapper = self.tmp / "agent.cmd"
        wrapper.write_text(f'@"{sys.executable}" "%~dp0recv.py" %*\r\n', encoding="ascii")
        cmd = self.backend.headless_launch(str(self.tmp), [str(wrapper), str(self.out)], TEXT)
        subprocess.run(cmd, cwd=self.tmp, timeout=60, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(self.received(), [TEXT])

    def test_non_ascii_cwd_is_entered(self):
        cwd = self.tmp / "d\u00e9j\u00e0 \u2014 \u4e2d"
        cwd.mkdir()
        recv = ("import os, sys\n"
                "open(sys.argv[1], 'w', encoding='utf-8').write(os.getcwd())\n")
        (self.tmp / "recv.py").write_text(recv, encoding="utf-8")
        cmd = self.backend.headless_launch(str(cwd), self.receiver_argv(), "")
        subprocess.run(cmd, cwd=self.tmp, timeout=60, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(self.out.read_text(encoding="utf-8"), str(cwd))

    def test_prompt_reaches_the_program_unchanged_in_a_pty(self):
        try:
            from winpty import PtyProcess
        except ImportError:
            self.skipTest("pywinpty not installed")
        cmd = self.backend.headless_launch(str(self.tmp), self.receiver_argv(), TEXT)
        proc = PtyProcess.spawn(cmd, cwd=str(self.tmp))
        self.addCleanup(proc.close, True)
        deadline = time.time() + 60
        while proc.isalive() and time.time() < deadline:
            try:
                proc.read(4096)
            except EOFError:
                break
            time.sleep(0.05)
        self.assertEqual(self.received(), [TEXT])


def _hub_seats():
    from tests.test_agent_models import _LaunchSeats, dashboard

    @unittest.skipUnless(ON_WINDOWS, "Windows launch scripts")
    class HubSeatsReceiveTheText(_LaunchSeats):
        """Each seat the hub starts — owner, reviewer, PO, a codex resume with a
        seed, an owner handover and a PO switch (rotation and failover start
        these) — runs its real launch script; the program receives the text."""

        def setUp(self):
            super().setUp()
            from backends import windows
            p = mock.patch.object(windows, "LAUNCH_DIR", self.tmp / "_launch")
            p.start()
            self.addCleanup(p.stop)
            (self.tmp / "recv.py").write_text(RECEIVER, encoding="utf-8")
            self.got: list[str] = []
            real = windows.WindowsBackend.__new__(windows.WindowsBackend)

            def launch(cwd, argv, prompt):
                out = self.tmp / "out.txt"
                out.unlink(missing_ok=True)
                cmd = real.headless_launch(
                    cwd, [sys.executable, str(self.tmp / "recv.py"), str(out)], prompt)
                subprocess.run(cmd, timeout=60, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.got.append(out.read_text(encoding="utf-8") if out.exists() else "")
                return argv
            p = mock.patch.object(dashboard.BACKEND, "headless_launch", side_effect=launch)
            p.start()
            self.addCleanup(p.stop)

        def test_every_seat_gets_the_text_unchanged(self):
            h = self.handler
            for kind in ("claude", "codex"):
                self.got.clear()
                room, part = self.room(kind)
                h._launch_room_agent_pty(room, part, TEXT, collab=True)
                room, part = self.room(kind, role="reviewer")
                h._launch_room_agent_pty(room, part, "", collab=True, prompt=TEXT)
                room, part = self.room(kind, "room-po", "ProductOwner")
                h._launch_room_agent_pty(room, part, TEXT, collab=False)
                room, part = self.room(kind)
                h._launch_room_agent_pty(room, {**part, "sessionId": ""}, "", collab=True,
                                         prompt=TEXT, cwd=str(self.tmp))
                room, part = self.room(kind, "room-po", "ProductOwner")
                h._launch_room_agent_pty(room, {**part, "sessionId": ""}, "", collab=False,
                                         prompt=TEXT, cwd=str(self.tmp))
                if kind == "codex":
                    room, part = self.room(kind)
                    h._resume_room_agent_pty(room, part, collab=True, seed=TEXT)
                self.assertEqual(len(self.got), 6 if kind == "codex" else 5)
                for i, text in enumerate(self.got):
                    with self.subTest(kind=kind, seat=i):
                        self.assertIn(TEXT, text)
                        self.assertNotIn("â€", text)

    return HubSeatsReceiveTheText


HubSeatsReceiveTheText = _hub_seats()


class EveryLaunchGoesThroughOneWriter(unittest.TestCase):
    def test_no_ps1_is_written_without_the_bom(self):
        src = (Path(__file__).resolve().parent.parent / "backends" / "windows.py").read_text(
            encoding="utf-8")
        self.assertNotIn('script_path.write_text(body, encoding="utf-8")', src)
        self.assertGreaterEqual(src.count("_write_ps1(script_path, body)"), 3)


if __name__ == "__main__":
    unittest.main()
