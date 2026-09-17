"""A past session no task holds stays in the list, however many transcripts the
hub's own tasks wrote after it: the window counts the rows kept, not the files
looked at."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
from agents.codex import CodexAgent  # noqa: E402

NOW = time.time()
DAY = 86400


class Bench:
    """A throwaway transcripts folder, Codex home and set of rooms."""

    def __init__(self, base: Path):
        self.base = base
        self.transcripts = base / "transcripts"
        self.codex_home = base / "codex"
        self.scratch = base / "cs"
        for d in (self.transcripts, self.codex_home, self.scratch):
            d.mkdir()
        self.rooms: list[dict] = []
        self.labels: dict[str, str] = {}

    def claude(self, sid: str, cwd: str, age_days: float, turns: int = 1,
               folder: str = "proj", text: str = "hello") -> Path:
        d = self.transcripts / folder
        d.mkdir(exist_ok=True)
        p = d / f"{sid}.jsonl"
        lines = [json.dumps({"type": "summary", "cwd": cwd})]
        lines += [json.dumps({"type": "user", "cwd": cwd,
                              "message": {"role": "user", "content": f"{text} {i}"}})
                  for i in range(turns)]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(p, (NOW - age_days * DAY,) * 2)
        return p

    def codex(self, sid: str, cwd: str, age_days: float, turns: int = 1) -> Path:
        d = self.codex_home / "sessions" / "2026" / "09" / "01"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"rollout-2026-09-01T00-00-00-{sid}.jsonl"
        lines = [json.dumps({"type": "session_meta",
                             "payload": {"id": sid, "cwd": cwd, "timestamp": "2026-09-01T00:00:00Z"}})]
        lines += [json.dumps({"type": "event_msg",
                              "payload": {"type": "user_message", "message": f"hello {i}"}})
                  for i in range(turns)]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(p, (NOW - age_days * DAY,) * 2)
        return p

    def room(self, rid: str, agent: str, cwd: str, session_id: str = "") -> None:
        self.rooms.append({
            "id": rid, "title": rid, "createdAt": 1, "updatedAt": 2, "messages": [],
            "participants": [{"kind": "agent", "agent": agent, "identity": agent,
                              "cwd": cwd, "sessionId": session_id}],
        })

    def load(self, n: int, extra=()) -> list[dict]:
        patches = [
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}),
            mock.patch.object(dashboard, "PROJ_DIR", self.transcripts),
            mock.patch.object(dashboard, "CS_ROOT", self.scratch),
            mock.patch.object(dashboard, "load_live", return_value=[]),
            mock.patch.object(dashboard, "load_labels", return_value=dict(self.labels)),
            mock.patch.object(dashboard, "load_parents", return_value={}),
            mock.patch.object(dashboard, "load_archived", return_value=set()),
            mock.patch.object(dashboard, "load_jira_links", return_value={}),
            mock.patch.object(dashboard, "load_jira_unlinks", return_value={}),
            mock.patch.object(dashboard, "_read_agent_session_files", return_value=[]),
            mock.patch.object(dashboard.chatroom, "list_rooms", return_value=self.rooms),
            mock.patch.object(dashboard, "_room_is_live", return_value=False),
            mock.patch.object(dashboard, "compute_room_cost", return_value={"dollars": 0}),
            mock.patch.object(dashboard.attention, "by_room", return_value={}),
            *extra,
        ]
        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return dashboard._load_sessions_uncached(n)


def plain(rows: list[dict]) -> list[str]:
    return [r["sessionId"] for r in rows if not r.get("headless")]


class ClaudeWindow(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.outside = str(self.b.base / "SigmaTrader")
        self.task_cwd = str(self.b.base / "tasks" / "t1" / "repo")

    def many_task_transcripts(self, count: int = 12) -> None:
        """More than n transcripts newer than anything else: reviewers and
        rotations in the task's folder, and one owner known by its session id
        that runs elsewhere."""
        self.b.room("room-t1", "claude", self.task_cwd)
        self.b.room("room-t2", "claude", str(self.b.base / "tasks" / "t2"), session_id="owner-by-id")
        for i in range(count):
            self.b.claude(f"held-{i}", self.task_cwd, age_days=0.001 * (i + 1), folder="task")
        self.b.claude("owner-by-id", str(self.b.base / "moved"), age_days=0.0005, folder="task")

    def test_an_older_session_outside_any_task_is_returned(self):
        self.many_task_transcripts()
        self.b.claude("mine", self.outside, age_days=20)
        rows = self.b.load(5)
        self.assertEqual(plain(rows), ["mine"])
        self.assertEqual(sorted(r["sessionId"] for r in rows if r.get("headless")),
                         ["room-t1", "room-t2"], "the tasks are the same rows as ever")

    def test_the_other_rules_still_hold(self):
        self.many_task_transcripts()
        self.b.claude("mine", self.outside, age_days=20)
        self.b.claude("shell", self.outside, age_days=21, turns=0)
        self.b.claude("named-shell", self.outside, age_days=22, turns=0)
        self.b.labels["named-shell"] = "kept by its name"
        # The same conversation copied into another folder: the newest copy only.
        self.b.claude("mine", str(self.b.base / "copy"), age_days=30, folder="copy", text="older copy")
        self.b.claude("rename-1", str(dashboard.RENAME_WORKSPACE), age_days=23,
                      folder=dashboard._RENAME_PROJ_SLUG)
        self.b.claude("rename-2", self.outside, age_days=24,
                      text=dashboard.RENAME_PROMPT_PREFIX)
        self.b.claude("rename-3", self.outside, age_days=25, folder="x-rename-workspace-x")
        rows = self.b.load(5)
        self.assertEqual(plain(rows), ["mine", "named-shell"])
        mine = next(r for r in rows if r["sessionId"] == "mine")
        self.assertEqual(mine["cwd"], self.outside)
        self.assertTrue(mine["first"].startswith("hello"))

    def test_n_counts_the_rows_kept_newest_first(self):
        self.many_task_transcripts()
        for i in range(4):
            self.b.claude(f"mine-{i}", self.outside, age_days=10 + i)
        self.assertEqual(plain(self.b.load(2)), ["mine-0", "mine-1"])

    def test_past_the_window_nothing_older_than_a_year(self):
        self.many_task_transcripts()
        self.b.claude("recent", self.outside, age_days=300)
        self.b.claude("ancient", self.outside, age_days=400)
        self.assertEqual(plain(self.b.load(5)), ["recent"])
        # Inside the newest n files its age never mattered, and still does not.
        self.assertEqual(plain(self.b.load(50)), ["recent", "ancient"])

    def test_a_tasks_transcripts_are_not_read(self):
        self.many_task_transcripts()
        self.b.claude("mine", self.outside, age_days=20)
        self.b.load(5)      # every file's folder is now remembered
        real_open = Path.open
        opened: list[str] = []

        def spy(path, *a, **kw):
            opened.append(path.name)
            return real_open(path, *a, **kw)
        flu = mock.patch.object(dashboard, "first_last_user", wraps=dashboard.first_last_user)
        with flu as flu_spy:
            rows = self.b.load(5, extra=[mock.patch.object(Path, "open", spy)])
        self.assertEqual(plain(rows), ["mine"])
        self.assertEqual([c.args[0].stem for c in flu_spy.call_args_list], ["mine"])
        self.assertFalse([n for n in opened if n.startswith(("held-", "owner-"))],
                         "a task's transcript is opened on no later load")


class CodexWindow(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.outside = str(self.b.base / "elsewhere")
        self.task_cwd = str(self.b.base / "tasks" / "t1" / "codex")
        self.b.room("room-t1", "codex", self.task_cwd)
        for i in range(12):
            self.b.codex(f"0000000{i:02d}-held", self.task_cwd, age_days=0.001 * (i + 1))
        self.b.codex("shell", self.outside, age_days=5, turns=0)
        self.b.codex("mine", self.outside, age_days=20)
        self.b.codex("ancient", self.outside, age_days=400)

    def test_an_older_codex_session_outside_any_task_is_returned(self):
        self.assertEqual(plain(self.b.load(5)), ["mine"])

    def test_the_reader_alone_keeps_its_window_in_files(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.b.codex_home)}):
            got = CodexAgent().list_sessions(limit=5)
            self.assertEqual(len(got), 5)
            self.assertTrue(all(s.cwd == self.task_cwd for s in got))
            kept = CodexAgent().list_sessions(
                limit=1, dropped=lambda s: s.cwd == self.task_cwd or s.turns == 0,
                max_age=365 * DAY)
            self.assertEqual(kept[-1].session_id, "mine")
            far = CodexAgent().list_sessions(
                limit=5, dropped=lambda s: s.cwd == self.task_cwd, max_age=365 * DAY)
            self.assertNotIn("ancient", [s.session_id for s in far])


if __name__ == "__main__":
    unittest.main()
