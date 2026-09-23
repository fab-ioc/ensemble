"""task_tool_hook.py: a big text file Read whole by a task agent comes back as
its first lines and a note; rtk's warning line stays out of Bash results; and
nothing the hook does can stand between the agent and its tool.

* PreToolUse Read: over the cap and read whole -> ``limit`` added; the agent's
  own ``offset``/``limit``, an image or PDF, a missing or small file, a cap of
  0 -> untouched;
* PostToolUse Read: a note only after a read this hook cut, with the exact
  lines and the offset to go on from;
* PostToolUse Bash: the nag line dropped only when it is there, stdout and
  stderr alike, everything else of the result kept;
* the script itself: broken input, no input, a path with spaces, a relative
  path: exit 0, and nothing printed unless there is something to say.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import task_tool_hook as hook  # noqa: E402

NAG = "[rtk] /!\\ No hook installed — run `rtk init -g` for automatic token savings"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def pre(tool_input: dict, cwd: str = "") -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": tool_input,
            "cwd": cwd, "session_id": "s", "tool_use_id": "t1"}


def post_read(tool_input: dict, total: int, got: int, start: int = 1, cwd: str = "") -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": tool_input,
            "cwd": cwd,
            "tool_response": {"type": "text", "file": {
                "filePath": tool_input.get("file_path", ""), "content": "x" * got,
                "numLines": got, "startLine": start, "totalLines": total}}}


def post_bash(stdout: str, stderr: str = "") -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": "rtk ls"},
            "tool_response": {"stdout": stdout, "stderr": stderr, "interrupted": False,
                              "isImage": False, "noOutputExpected": False}}


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "with space"
        self.dir.mkdir()
        self.big = self.dir / "big.py"
        self.big.write_text("\n".join(f"line {i} " + "é" * 30 for i in range(800)) + "\n",
                            encoding="utf-8")
        self.assertGreater(self.big.stat().st_size, hook.CAP_BYTES_DEFAULT)
        self.small = self.dir / "small.txt"
        self.small.write_text("hello\n", encoding="utf-8")
        self.image = self.dir / "shot.PNG"
        self.image.write_bytes(b"\x89PNG" + b"\0" * (hook.CAP_BYTES_DEFAULT + 1))
        self.pdf = self.dir / "doc.pdf"
        self.pdf.write_bytes(b"%PDF" + b"\0" * (hook.CAP_BYTES_DEFAULT + 1))


class ReadCap(Files):
    def test_a_big_text_file_read_whole_gets_the_line_limit(self):
        out = hook.handle(pre({"file_path": str(self.big)}))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"],
                         {"file_path": str(self.big), "limit": hook.CAP_LINES_DEFAULT})
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])

    def test_the_agents_own_window_is_kept(self):
        for tool_input in ({"file_path": str(self.big), "limit": 50},
                           {"file_path": str(self.big), "offset": 300},
                           {"file_path": str(self.big), "offset": 1, "limit": 2000}):
            self.assertIsNone(hook.handle(pre(tool_input)), tool_input)

    def test_a_null_limit_counts_as_none_given(self):
        out = hook.handle(pre({"file_path": str(self.big), "limit": None}))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["limit"], hook.CAP_LINES_DEFAULT)

    def test_images_pdfs_and_notebooks_are_not_text(self):
        for path in (self.image, self.pdf, self.dir / "nb.ipynb"):
            path.touch()
            with path.open("ab") as f:
                f.write(b"\0" * (hook.CAP_BYTES_DEFAULT + 1))
            self.assertIsNone(hook.handle(pre({"file_path": str(path)})), path)

    def test_a_missing_or_small_file_is_untouched(self):
        self.assertIsNone(hook.handle(pre({"file_path": str(self.dir / "nope.txt")})))
        self.assertIsNone(hook.handle(pre({"file_path": str(self.small)})))
        self.assertIsNone(hook.handle(pre({"file_path": str(self.dir)})))
        self.assertIsNone(hook.handle(pre({"file_path": ""})))
        self.assertIsNone(hook.handle(pre({"file_path": 12})))
        self.assertIsNone(hook.handle(pre({})))

    def test_the_caps_are_settings(self):
        self.assertIsNone(hook.handle(pre({"file_path": str(self.big)}), cap_bytes=0))
        self.assertIsNone(hook.handle(pre({"file_path": str(self.big)}), cap_lines=0))
        out = hook.handle(pre({"file_path": str(self.small)}), cap_bytes=2, cap_lines=1)
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["limit"], 1)

    def test_a_relative_path_is_read_against_the_agents_cwd(self):
        out = hook.handle(pre({"file_path": "big.py"}, cwd=str(self.dir)))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["limit"], hook.CAP_LINES_DEFAULT)
        self.assertIsNone(hook.handle(pre({"file_path": "big.py"}, cwd=str(self.tmp.name))))

    def test_other_events_and_tools_say_nothing(self):
        self.assertIsNone(hook.handle({**pre({"file_path": str(self.big)}), "tool_name": "Grep"}))
        self.assertIsNone(hook.handle({**pre({"file_path": str(self.big)}),
                                       "hook_event_name": "PostToolUse"}))
        self.assertIsNone(hook.handle(["not", "a", "dict"]))
        self.assertIsNone(hook.handle(None))


class ReadNote(Files):
    def test_a_cut_read_gets_one_line_with_the_offset_to_go_on_from(self):
        out = hook.handle(post_read({"file_path": str(self.big), "limit": 400}, total=800, got=400))
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("big.py: lines 1-400 of 800 shown", note)
        self.assertIn("offset=401", note)
        self.assertIn("16 KB", note)
        self.assertNotIn("\n", note)
        self.assertNotIn("updatedToolOutput", out["hookSpecificOutput"])

    def test_no_note_for_a_read_that_was_not_cut(self):
        big = str(self.big)
        for data in (
                post_read({"file_path": big, "limit": 400}, total=400, got=400),   # whole file
                post_read({"file_path": big, "limit": 50}, total=800, got=50),     # the agent's window
                post_read({"file_path": big, "limit": 400, "offset": 401}, total=800, got=400),
                post_read({"file_path": big}, total=800, got=800),
                post_read({"file_path": str(self.small), "limit": 400}, total=800, got=400),
        ):
            self.assertIsNone(hook.handle(data), data["tool_input"])

    def test_a_broken_response_is_left_alone(self):
        data = post_read({"file_path": str(self.big), "limit": 400}, total=800, got=400)
        for response in (None, "text", {"type": "text"}, {"file": "x"},
                         {"file": {"numLines": "many", "totalLines": 800}}):
            self.assertIsNone(hook.handle({**data, "tool_response": response}), response)


class NagLine(unittest.TestCase):
    def test_the_nag_is_dropped_only_when_it_is_there(self):
        out = hook.handle(post_bash(NAG + "\nbig.txt  61.6K\nsmall.txt  6B"))
        new = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(new["stdout"], "big.txt  61.6K\nsmall.txt  6B")
        self.assertEqual((new["stderr"], new["interrupted"], new["isImage"], new["noOutputExpected"]),
                         ("", False, False, False))
        self.assertIsNone(hook.handle(post_bash("big.txt  61.6K\nsmall.txt  6B")))
        self.assertIsNone(hook.handle(post_bash("")))

    def test_in_the_middle_in_stderr_and_with_a_broken_dash(self):
        out = hook.handle(post_bash("a\n" + NAG + "\nb\n", stderr=NAG.replace("—", "�") + "\n"))
        new = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual((new["stdout"], new["stderr"]), ("a\nb\n", ""))
        out = hook.handle(post_bash("[rtk] /!\\ Hook outdated — run `rtk init -g` to update\nx"))
        self.assertEqual(out["hookSpecificOutput"]["updatedToolOutput"]["stdout"], "x")

    def test_a_line_that_only_mentions_rtk_stays(self):
        self.assertIsNone(hook.handle(post_bash("[rtk] 12 lines hidden\nrun `rtk init -g` yourself")))
        self.assertIsNone(hook.handle({**post_bash(NAG), "tool_response": "plain text"}))
        self.assertIsNone(hook.handle({**post_bash(NAG), "tool_name": "PowerShell"}))


class Script(Files):
    """The hook as Claude Code runs it: a process fed JSON on stdin."""

    def hook(self, stdin: bytes, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "task_tool_hook.py"), *args],
                              input=stdin, capture_output=True, timeout=30,
                              creationflags=NO_WINDOW)

    def test_a_real_read_of_a_path_with_spaces(self):
        res = self.hook(json.dumps(pre({"file_path": str(self.big)})).encode("utf-8"),
                       "--bytes", "1024", "--lines", "25")
        self.assertEqual((res.returncode, res.stderr), (0, b""))
        out = json.loads(res.stdout.decode("utf-8"))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"],
                         {"file_path": str(self.big), "limit": 25})

    def test_the_note_survives_the_pipe_in_utf8(self):
        naughty = self.dir / "ré sumé.md"
        naughty.write_text("x\n" * 20000, encoding="utf-8")
        data = post_read({"file_path": str(naughty), "limit": 400}, total=20000, got=400)
        res = self.hook(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(res.returncode, 0)
        note = json.loads(res.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ré sumé.md: lines 1-400 of 20000", note)

    def test_broken_input_exits_0_with_no_output(self):
        for stdin in (b"", b"{", b"[1, 2]", b"\xff\xfe garbage", b"null",
                      json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read",
                                  "tool_input": "not a dict"}).encode("utf-8")):
            res = self.hook(stdin)
            self.assertEqual((res.returncode, res.stdout, res.stderr), (0, b"", b""), stdin)
        res = self.hook(json.dumps(pre({"file_path": str(self.big)})).encode("utf-8"),
                       "--bytes", "lots", "--lines")
        self.assertEqual(res.returncode, 0)          # bad arguments: the defaults
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["updatedInput"]["limit"],
                         hook.CAP_LINES_DEFAULT)


if __name__ == "__main__":
    unittest.main()
