"""tools/measure_wakes.py: what woke each session, what each wake cost, and
what came of it (#147)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_wakes as mw  # noqa: E402

TZ = timezone.utc
START = datetime(2026, 9, 24, tzinfo=TZ)
END = datetime(2026, 10, 1, tzinfo=TZ)
ROOM = {"room": "room-1", "task": "#5", "title": "Motor", "project": "ED"}


def _at(minutes: int) -> str:
    return (START + timedelta(hours=1, minutes=minutes)).isoformat().replace("+00:00", "Z")


class WakeKinds(unittest.TestCase):
    def test_hub_lines_and_people(self):
        self.assertEqual(mw.wake_kind("[digest] Project 'ED': ...", "po", True)[0], "digest")
        cause, extra = mw.wake_kind(
            "[report] review 2 (approved) from task 'Motor' (#5, codex): fine — read it", "po", True)
        self.assertEqual((cause, extra["reportKind"], extra["taskId"]), ("report", "review (approved)", "#5"))
        cause, extra = mw.wake_kind("[report] completed from task 'Motor' (#5, claude): done", "po", True)
        self.assertEqual((cause, extra["reportKind"]), ("report", "completed"))
        self.assertEqual(mw.wake_kind("[from the Motors PO] question: when?", "po", True)[0], "pomsg")
        self.assertEqual(mw.wake_kind("[from the PO] carry on", "owner", True)[0], "frompo")
        self.assertEqual(mw.wake_kind("You are 'claude' ... TASK: build it", "owner", False),
                         ("first", {"kind": "spec"}))
        self.assertEqual(mw.wake_kind("[rotation] You are taking over", "owner", False)[0], "first")
        self.assertEqual(mw.wake_kind("[rotation] You are taking over", "owner", True)[0], "rotation")
        self.assertEqual(mw.wake_kind("please fix the title", "po", True)[0], "ceo")
        self.assertEqual(mw.wake_kind("<task-notification>done</task-notification>", "po", True)[0], "harness")
        self.assertEqual(mw.wake_kind("[due] 15:00 check the merge", "po", True)[0], "due")


class ShellReads(unittest.TestCase):
    def test_reading_commands(self):
        self.assertTrue(mw.shell_is_read("git log --oneline -5"))
        self.assertTrue(mw.shell_is_read("rtk git status"))
        self.assertTrue(mw.shell_is_read("git diff main...HEAD | head -50"))
        self.assertTrue(mw.shell_is_read("cat a.py && grep -n foo b.py"))
        self.assertTrue(mw.shell_is_read(""))

    def test_writing_commands(self):
        self.assertFalse(mw.shell_is_read("git commit -m x"))
        self.assertFalse(mw.shell_is_read("cat a && rm b"))
        self.assertFalse(mw.shell_is_read("git stash push -m x"))
        self.assertFalse(mw.shell_is_read("py -m unittest tests"))
        self.assertTrue(mw.shell_is_read("git stash list"))
        self.assertTrue(mw.shell_is_read("bash -lc 'git log --oneline'"))
        self.assertFalse(mw.shell_is_read("bash -lc 'git log && rm x'"))


def _claude_rows() -> list[dict]:
    def user(text, when):
        return {"type": "user", "timestamp": when, "message": {"content": [{"type": "text", "text": text}]}}

    def assistant(when, rid, blocks, usage, stop="end_turn"):
        return {"type": "assistant", "timestamp": when, "requestId": rid,
                "message": {"content": blocks, "usage": usage, "stop_reason": stop}}
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 1000,
             "output_tokens": 5}
    return [
        user("You are 'claude', the PO. TASK: run the project", _at(0)),
        assistant(_at(1), "r1", [{"type": "text", "text": "Starting."}], usage),
        user("[digest] Project 'ED' — changes: #5 new commits", _at(10)),
        # One model call streamed as two rows: counted once.
        assistant(_at(11), "r2", [{"type": "tool_use", "name": "mcp__ensemble__ensemble_list_tasks",
                                  "input": {}}], usage, stop="tool_use"),
        assistant(_at(11), "r2", [{"type": "tool_use", "name": "mcp__ensemble__ensemble_list_tasks",
                                  "input": {}}], usage, stop="tool_use"),
        {"type": "user", "timestamp": _at(11), "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        assistant(_at(12), "r3", [{"type": "text", "text": "Noted."}], usage),
        user("[report] completed from task 'Motor' (#5, claude): done", _at(20)),
        assistant(_at(21), "r4", [{"type": "tool_use", "name": "mcp__ensemble__chat_send",
                                  "input": {"to": "user", "message": "Motor is done."}}], usage, stop="tool_use"),
        {"type": "user", "timestamp": _at(21), "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        assistant(_at(22), "r5", [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "git diff main"}}], usage, stop="tool_use"),
        {"type": "user", "timestamp": _at(22), "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        assistant(_at(23), "r6", [{"type": "text", "text": "Told fab. " * 20}], usage),
        user("[due] later", (END + timedelta(hours=1)).isoformat().replace("+00:00", "Z")),
        assistant(_at(24), "r7", [{"type": "text", "text": "after the window"}], usage),
    ]


class ScanClaude(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "aaaaaaaa-0000-0000-0000-000000000001.jsonl"
        with self.path.open("w", encoding="utf-8") as h:
            for row in _claude_rows():
                h.write(json.dumps(row) + "\n")

    def test_turns_calls_tokens_and_outcomes(self):
        turns = mw.scan_claude(self.path, START, END, "po", ROOM)
        self.assertEqual([t.cause for t in turns], ["first", "digest", "report"])
        first, digest, report = turns
        self.assertEqual(first.extra, {"kind": "spec"})
        self.assertEqual((first.calls, first.tokens["cacheRead"], first.tokens["output"]), (1, 1000, 5))
        self.assertEqual(digest.calls, 2, "a request streamed as two rows is one call")
        self.assertEqual(dict(digest.tools), {"ensemble_list_tasks": 2})
        self.assertEqual((digest.actions, digest.toldUser, digest.replyChars), (0, False, len("Noted.")))
        self.assertTrue(digest.noop())
        self.assertEqual(report.extra["reportKind"], "completed")
        self.assertEqual((report.calls, report.actions, report.toldUser), (3, 1, True))
        self.assertFalse(report.noop())
        self.assertTrue(report.whole_diff())
        self.assertFalse(digest.whole_diff())
        d = report.as_dict()
        self.assertEqual((d["session"], d["task"], d["day"], d["total"]),
                         ("aaaaaaaa-0000-0000-0000-000000000001", "#5", "2026-09-24", 3 * 1035))


def _codex_rows() -> list[dict]:
    def user(text, when):
        return {"type": "response_item", "timestamp": when,
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

    def count(when, total, cached, out):
        return {"type": "event_msg", "timestamp": when,
                "payload": {"type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": total, "cached_input_tokens": cached, "output_tokens": out}}}}
    return [
        {"type": "session_meta", "timestamp": _at(0), "payload": {"id": "codex-sid"}},
        user("<environment_context>cwd: x</environment_context>", _at(0)),
        user("You are 'codex', the reviewer. This is review 2. You are a fresh session started for this ONE review.", _at(0)),
        count(_at(1), 5000, 4000, 50),
        {"type": "response_item", "timestamp": _at(1), "payload": {
            "type": "function_call", "name": "shell", "call_id": "c1",
            "arguments": json.dumps({"command": ["bash", "-lc", "git diff abc1234"]})}},
        count(_at(2), 6000, 5000, 20),
        {"type": "response_item", "timestamp": _at(2), "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Approved."}]}},
        user("[relay] claude: thanks", _at(30)),
        user("<environment_context>cwd: x</environment_context>", _at(30)),
        count(_at(31), 7000, 6900, 10),
    ]


class ScanCodex(unittest.TestCase):
    def test_token_deltas_and_folded_environment_notes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rollout.jsonl"
            with path.open("w", encoding="utf-8") as h:
                for row in _codex_rows():
                    h.write(json.dumps(row) + "\n")
            turns = mw.scan_codex(path, START, END, "reviewer", ROOM)
        self.assertEqual([t.cause for t in turns], ["first", "relay"])
        first, relay = turns
        self.assertEqual(first.extra, {"kind": "review brief"})
        self.assertEqual(first.session, "codex-sid")
        self.assertEqual((first.calls, first.tokens["input"], first.tokens["cacheRead"], first.tokens["output"]),
                         (2, 2000, 9000, 70))
        self.assertEqual(first.commands, ["bash -lc git diff abc1234"])
        self.assertEqual(first.actions, 0, "a Codex shell wrapper around a reading command reads")
        self.assertFalse(first.whole_diff())
        self.assertEqual(first.replyChars, len("Approved."))
        self.assertEqual((relay.calls, relay.tokens["input"]), (1, 100))


class ReviewRounds(unittest.TestCase):
    def test_rounds_per_task_and_whole_diff_detection(self):
        def turn(session, minutes, cmds, agent="codex"):
            t = mw.Turn(agent, "reviewer", ROOM, session, START + timedelta(minutes=minutes), TZ,
                        "first", {"kind": "review brief"}, "")
            t.calls = 1
            t.tokens["cacheRead"] = 100
            t.commands = cmds
            return t
        turns = [turn("s1", 0, ["git diff main"]), turn("s2", 60, ["git diff abc1234"]),
                 turn("s3", 120, ["git log -p main..HEAD"]),
                 turn("s3", 125, []),
                 mw.Turn("claude", "owner", ROOM, "o1", START, TZ, "first", {}, "")]
        out = mw.review_rounds(turns, {})
        self.assertEqual((out["tasks"], out["rounds"], out["laterRounds"], out["laterWholeDiff"]), (1, 3, 2, 1))
        self.assertEqual(out["laterWholeDiffTokens"], 200)
        rounds = out["byTask"][0]["details"]
        self.assertEqual([r["round"] for r in rounds], [1, 2, 3])
        self.assertEqual([r["wholeDiff"] for r in rounds], [True, False, True])
        self.assertTrue(rounds[1]["rangeDiff"])
        self.assertEqual(out["tokensPerRoundByNo"]["3"], {"rounds": 1, "perRound": 200})


class HelperEstimate(unittest.TestCase):
    def test_digests_in_po_rooms(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "EnsembleProjects" / "ED").mkdir(parents=True)
            (home / "EnsembleProjects" / "ED" / "project.json").write_text(
                json.dumps({"id": "ed", "poRoomId": "room-po"}), encoding="utf-8")
            (home / ".ensemble" / "rooms").mkdir(parents=True)
            body = "x" * 400
            msgs = [{"from": "ensemble", "ts": (START + timedelta(hours=2)).timestamp(),
                     "text": "**Progress digest**\n" + body},
                    {"from": "ensemble", "ts": (END + timedelta(hours=2)).timestamp(),
                     "text": "**Progress digest**\nlater"},
                    {"from": "claude", "ts": (START + timedelta(hours=3)).timestamp(), "text": "hi"}]
            (home / ".ensemble" / "rooms" / "room-po.json").write_text(
                json.dumps({"id": "room-po", "messages": msgs}), encoding="utf-8")
            out = mw.helper_estimate(home, START, END)
        self.assertEqual(out["digests"], 1)
        self.assertEqual(out["tokens"], mw.HELPER_PROMPT_TOKENS + 300 + 100)
        self.assertEqual(list(out["perDay"]), ["2026-09-24"])


if __name__ == "__main__":
    unittest.main()
