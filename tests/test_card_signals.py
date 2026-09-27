"""Two signals on a task's card and row, from the hub's task rows
(dashboard.py):

* ``newsAt``: when the task's chat last got something that is not the
  person's own and not a landmark; in a one-agent chat, the agent's last text,
  which the cost pass over its transcript notes on the way (no read of its own);
* ``changes``: a worktree task's ``+N −N`` against main, counted per pair of
  heads read from the git files, by a background thread on a poll.

The page's side (the dot, the chip) is tests/test_card_signals_page.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dashboard  # noqa: E402
from test_session_window import Bench  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def epoch(stamp: str) -> float:
    return dashboard._turn_epoch(stamp)


def git(cwd, *args) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                          "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                         creationflags=NO_WINDOW, check=True)
    return out.stdout.strip()


class SaidAt(unittest.TestCase):
    """The cost pass notes when a conversation last said something."""

    def test_claude_last_text_not_a_tool_call(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s1.jsonl"
            lines = [
                {"type": "user", "timestamp": "2026-09-27T10:00:00Z", "message": {"role": "user", "content": "go"}},
                {"type": "assistant", "timestamp": "2026-09-27T10:00:05Z", "requestId": "a",
                 "message": {"model": "m", "content": [{"type": "text", "text": "Done, here it is."}],
                             "usage": {"input_tokens": 1, "output_tokens": 1}}},
                # Later lines that say nothing to read: a tool call, an empty text.
                {"type": "assistant", "timestamp": "2026-09-27T10:01:00Z", "requestId": "b",
                 "message": {"model": "m", "content": [{"type": "tool_use", "name": "Bash", "input": {}}],
                             "usage": {"input_tokens": 1, "output_tokens": 1}}},
                {"type": "assistant", "timestamp": "2026-09-27T10:02:00Z",
                 "message": {"model": "m", "content": [{"type": "text", "text": "  "}]}},
            ]
            p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
            dashboard.compute_session_cost(p)
            self.assertEqual(dashboard._SAID_AT[p], epoch("2026-09-27T10:00:05Z"))
            # Nothing said: 0.
            q = Path(td) / "s2.jsonl"
            q.write_text(json.dumps(lines[0]) + "\n", encoding="utf-8")
            dashboard.compute_session_cost(q)
            self.assertEqual(dashboard._SAID_AT[q], 0.0)

    def test_codex_last_assistant_message(self):
        with tempfile.TemporaryDirectory() as td:
            day = Path(td) / "sessions" / "2026" / "09" / "27"
            day.mkdir(parents=True)
            sid = "01a0dcc7-afd0-7612-8d0f-5eba8182d2f2"
            recs = [
                {"timestamp": "2026-09-27T08:00:00Z", "type": "response_item",
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]}},
                {"timestamp": "2026-09-27T08:00:09Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Looks right."}]}},
                {"timestamp": "2026-09-27T08:03:00Z", "type": "response_item",
                 "payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
            ]
            (day / f"rollout-2026-09-27T08-00-00-{sid}.jsonl").write_text(
                "\n".join(json.dumps(r, separators=(",", ":")) for r in recs) + "\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": td}):
                dashboard._CODEX_ROLLOUT_PATHS.pop(sid, None)
                dashboard._CODEX_COST_CACHE.pop(sid, None)
                dashboard.compute_codex_session_cost(sid)
                dashboard._CODEX_ROLLOUT_PATHS.pop(sid, None)
            self.assertEqual(dashboard._CODEX_SAID_AT[sid], epoch("2026-09-27T08:00:09Z"))


class NewsAt(unittest.TestCase):
    def duo(self, messages):
        return {"id": "room-d", "mode": "collab", "messages": messages, "participants": [
            {"kind": "user", "identity": "user"},
            {"kind": "agent", "agent": "claude", "identity": "claude", "sessionId": "c1"},
            {"kind": "agent", "agent": "codex", "identity": "codex", "sessionId": "x1"}]}

    def test_a_chat_of_agents_is_its_last_message_not_the_persons(self):
        rm = self.duo([
            {"from": "claude", "to": "codex", "text": "Review a1b2.", "ts": 100.0},
            {"from": "codex", "to": "claude", "text": "Looks right.", "ts": 110.0},
            {"from": "user", "to": "all", "text": "Ship it.", "ts": 120.0},
            {"from": "ensemble", "kind": "notice", "noticeKind": "restart", "text": "The hub restarted.", "ts": 130.0},
            {"divider": {"n": 1}, "ts": 140.0},
            {"from": "claude", "kind": "notice", "rotation": {"n": 2}, "text": "Handed over.", "ts": 150.0},
        ])
        self.assertEqual(dashboard.room_news_at(rm), 110.0)
        # Hub input in the person's name (a report, a digest) is news; their own words are not.
        rm["messages"].append({"from": "user", "kind": "report", "text": "[report] done", "ts": 160.0})
        self.assertEqual(dashboard.room_news_at(rm), 160.0)
        rm["messages"].append({"from": "user", "kind": "human", "text": "Thanks.", "ts": 170.0})
        self.assertEqual(dashboard.room_news_at(rm), 160.0)
        self.assertEqual(dashboard.room_news_at(self.duo([])), 0.0)

    def test_a_one_agent_chat_is_its_transcript(self):
        path = Path("C:/nowhere/solo-1.jsonl")
        rm = {"id": "room-s", "mode": "solo", "participants": [
            {"kind": "agent", "agent": "claude", "identity": "claude", "sessionId": "solo-1"}],
              # Its room holds the person's sends and reports typed into it: not its chat.
              "messages": [{"from": "claude", "text": "not shown", "ts": 900.0},
                           {"from": "po", "kind": "pomsg", "text": "From the other PO.", "ts": 300.0}]}
        with mock.patch.dict(dashboard._TRANSCRIPT_PATHS, {"solo-1": path}), \
                mock.patch.dict(dashboard._SAID_AT, {path: 250.0}):
            self.assertEqual(dashboard.room_news_at(rm), 300.0)
            rm["messages"].pop()
            self.assertEqual(dashboard.room_news_at(rm), 250.0)
        # A Codex seat, and a transcript not found yet.
        rm["participants"][0]["agent"] = "codex"
        with mock.patch.dict(dashboard._CODEX_SAID_AT, {"solo-1": 42.0}):
            self.assertEqual(dashboard.room_news_at(rm), 42.0)
        self.assertEqual(dashboard.room_news_at({**rm, "participants": [
            {"kind": "agent", "agent": "claude", "sessionId": "unknown"}]}), 0.0)


class BranchChanges(unittest.TestCase):
    """A worktree branch's committed change against main."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        base = Path(td.name)
        self.main = base / "proj"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        (self.main / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        git(self.main, "add", ".")
        git(self.main, "commit", "-q", "-m", "first")
        self.wt = base / "task" / "repo"
        git(self.main, "worktree", "add", "-q", str(self.wt), "-b", "sess/t")

    def commit(self):
        (self.wt / "a.txt").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
        (self.wt / "b.txt").write_text("new\n", encoding="utf-8")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "work")

    def test_counted_per_pair_of_heads(self):
        root = str(self.wt)
        # A branch with nothing committed: +0 −0 (the page shows nothing).
        self.assertEqual(dashboard.branch_changes(root, background=False), {"add": 0, "del": 0, "files": 0})
        self.commit()
        # Uncommitted work is not counted: the key is the heads.
        (self.wt / "a.txt").write_text("changed\n", encoding="utf-8")
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(dashboard.branch_heads(root), (head, git(self.main, "rev-parse", "main")))
        self.assertEqual(dashboard.branch_changes(root, background=False), {"add": 3, "del": 1, "files": 2})
        # The same heads are not counted again.
        with mock.patch.object(dashboard, "_count_branch", side_effect=AssertionError("counted again")):
            self.assertEqual(dashboard.branch_changes(root, background=False)["add"], 3)
        # Packed refs are read too.
        git(self.main, "pack-refs", "--all")
        self.assertEqual(dashboard.branch_heads(root)[0], head)
        # Merged into main: nothing left against it.
        git(self.main, "merge", "-q", "--ff-only", "sess/t")
        self.assertEqual(dashboard.branch_changes(root, background=False), {"add": 0, "del": 0, "files": 0})

    def test_no_branch_of_its_own(self):
        self.assertIsNone(dashboard.branch_heads(str(self.main)))          # on main itself
        self.assertIsNone(dashboard.branch_changes(str(self.main), background=False))
        self.assertIsNone(dashboard.branch_changes(str(self.wt.parent), background=False))   # not a checkout
        self.assertIsNone(dashboard.branch_changes(""))
        git(self.wt, "checkout", "-q", "--detach")
        self.assertIsNone(dashboard.branch_heads(str(self.wt)))

    def test_a_poll_never_waits_for_git(self):
        self.commit()
        root = str(self.wt)
        # Not counted yet: nothing now, the count comes from the background.
        self.assertIsNone(dashboard.branch_changes(root))
        deadline = time.time() + 20
        got = None
        while time.time() < deadline and got is None:
            time.sleep(0.05)
            got = dashboard.branch_changes(root)
        self.assertEqual(got, {"add": 3, "del": 1, "files": 2})

    def test_on_the_task_row(self):
        self.commit()
        root = str(self.wt)
        dashboard.branch_changes(root, background=False)
        (self.wt.parent.parent / "bench").mkdir()
        b = Bench(self.wt.parent.parent / "bench")
        b.room("room-wt", "claude", root, workspace={"mode": "worktree", "branch": "sess/t"},
               messages=[{"from": "codex", "to": "claude", "text": "ok", "ts": 77.0}])
        b.rooms[0]["participants"].append({"kind": "agent", "agent": "codex", "identity": "codex", "cwd": root})
        b.room("room-copy", "claude", root, workspace={"mode": "copy"})
        for rm in b.rooms:
            rm["cwd"] = root
        rows = {r["roomId"]: r for r in b.load(50) if r.get("headless")}
        self.assertEqual(rows["room-wt"]["changes"], {"add": 3, "del": 1, "files": 2})
        self.assertEqual(rows["room-wt"]["newsAt"], 77.0)
        self.assertIsNone(rows["room-copy"]["changes"])
        self.assertEqual(rows["room-copy"]["newsAt"], 0.0)


if __name__ == "__main__":
    unittest.main()
