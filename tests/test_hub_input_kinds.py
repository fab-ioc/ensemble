"""What the hub typed into a session is told apart from what a person typed.

A PO's transcript records a progress check, a task's report or a handover
request as the user's turn, just like a person's line. /api/session/<sid>?full=1
classifies each user turn by how it starts (HUB_INPUT_KINDS) and says, on each
assistant turn, what it answers, so the chat page can fold hub traffic and tag
every reply. Checked here with a temporary Claude transcript and a Codex
session served through the same handler:

* human, digest, report, resumed, handover, rotation, relay and restart-helper
  turns get their kind; a person's "[note to self] …" stays human;
* a report carries its kind, task title, task id and reporter;
* every assistant turn after a user turn says what it answers;
* a resume note typed together with the held messages is two turns.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard

REPORT = ("[report] completed from task 'Docs: projects (v2)' (docs_projects, claude): "
          "merged and tested — read it in full with ensemble_get_task taskId=docs_projects messages=0.")
TURNS = [
    ("user", "Can you tell me where the docs task stands?"),
    ("assistant", "It is in review."),
    ("user", "[digest] Ensemble Dashboard: 1 task moved to Done — details with ensemble_list_tasks / ensemble_get_task."),
    ("assistant", "Noted the move."),
    ("user", REPORT),
    ("assistant", "Merged and live."),
    ("assistant", "Decision needed: ship now or wait?"),
    ("user", dashboard.RESUME_NOTE),
    ("user", "[handover] Your conversation has reached 210k tokens (the limit is 200k), so …"),
    ("assistant", "Handover written."),
    ("user", "[rotation] You are the product owner (PO) of the project 'Ensemble', taking over …"),
    ("assistant", "Where things stand: …"),
    ("user", "[from the restart helper, not sam] The hub restarted on the code already on disk …"),
    ("user", "[relay] New message from 'codex' in your shared room. Use the chat_read tool …"),
    ("user", "[note to self] check the backup"),
    ("assistant", "Backup checked."),
    ("user", "[digest]almost"),     # not the prefix: no space after the bracket
]


def transcript(path: Path, turns) -> None:
    with path.open("w", encoding="utf-8") as f:
        for i, (role, text) in enumerate(turns):
            content = text if role == "user" else [{"type": "text", "text": text}]
            f.write(json.dumps({"type": role, "timestamp": f"2026-09-14T10:00:{i:02d}Z",
                                "message": {"role": role, "content": content}}) + "\n")


def get(path: str) -> dict:
    h = dashboard.Handler.__new__(dashboard.Handler)
    h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
    h.requestline = f"GET {path} HTTP/1.1"
    h.headers = {"Host": "127.0.0.1"}
    h.rfile, h.wfile = io.BytesIO(), io.BytesIO()
    h.client_address = ("127.0.0.1", 50000)
    h.log_message = lambda *a: None
    h.do_GET()
    raw = h.wfile.getvalue()
    head, body = raw.split(b"\r\n\r\n", 1)
    assert head.split(b" ", 2)[1] == b"200", head
    return json.loads(body.decode("utf-8"))


class ClassifiedTurns(unittest.TestCase):
    def check(self, turns):
        users = [t for t in turns if t["role"] == "user"]
        self.assertEqual([t["kind"] for t in users],
                         ["human", "digest", "report", "resumed", "handover", "rotation",
                          "helper", "relay", "human", "human"])
        rep = users[2]
        self.assertEqual((rep["reportKind"], rep["taskTitle"], rep["taskId"], rep["reporter"]),
                         ("completed", "Docs: projects (v2)", "docs_projects", "claude"))
        answers = [(t["text"], t["answers"]["kind"]) for t in turns if t["role"] == "assistant"]
        self.assertEqual(answers, [
            ("It is in review.", "human"), ("Noted the move.", "digest"),
            ("Merged and live.", "report"), ("Decision needed: ship now or wait?", "report"),
            ("Handover written.", "handover"), ("Where things stand: …", "rotation"),
            ("Backup checked.", "human")])
        merged = next(t for t in turns if t["text"] == "Merged and live.")
        self.assertEqual(merged["answers"], {"kind": "report", "reportKind": "completed",
                                             "taskTitle": "Docs: projects (v2)",
                                             "taskId": "docs_projects", "reporter": "claude"})

    def test_a_claude_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s1.jsonl"
            transcript(p, TURNS)
            with mock.patch.object(dashboard, "find_transcript", lambda sid: p), \
                    mock.patch.object(dashboard, "load_labels", lambda: {}):
                self.check(get("/api/session/s1?full=1")["turns"])
                # The plain list of user turns is unchanged.
                self.assertNotIn("kind", get("/api/session/s1")["turns"][0])

    def test_a_codex_session(self):
        class Codex:
            def session_stat(self, sid):
                return {"size": 1, "mtime": 1}

            def read_turns(self, sid):
                return [{"timestamp": "", "role": r, "text": t} for r, t in TURNS]

            def cwd_for_session(self, sid):
                return ""

        with mock.patch.object(dashboard, "find_transcript", lambda sid: None), \
                mock.patch.object(dashboard.agents, "get_agent", lambda k: Codex()), \
                mock.patch.object(dashboard, "load_labels", lambda: {}):
            self.check(get("/api/session/c1?full=1")["turns"])

    def test_a_resume_note_with_held_messages_is_two_turns(self):
        turns = dashboard.classify_turns([
            {"role": "user", "text": dashboard.RESUME_NOTE + "\n\nplease continue with step 2"},
            {"role": "assistant", "text": "Continuing."}])
        self.assertEqual([(t["kind"], t["text"]) for t in turns[:2]],
                         [("resumed", dashboard.RESUME_NOTE), ("human", "please continue with step 2")])
        self.assertEqual(turns[2]["answers"], {"kind": "human"})

    def test_a_report_header_that_does_not_parse_is_still_a_report(self):
        self.assertEqual(dashboard.hub_input_kind("[report] something odd"), {"kind": "report"})
        self.assertEqual(dashboard.hub_input_kind("  [digest] x")["kind"], "digest")
        self.assertEqual(dashboard.hub_input_kind("## Review comments (2)")["kind"], "human")

    def test_a_review_verdict_report(self):
        info = dashboard.hub_input_kind(
            "[report] review 1 (changes requested) from task 'Docs (v2)' (room-cc9bd746, codex): Restore is unsafe.")
        self.assertEqual(info, {"kind": "report", "reportKind": "review 1 (changes requested)",
                                "taskTitle": "Docs (v2)", "taskId": "room-cc9bd746", "reporter": "codex"})

    def test_every_prefix_the_hub_types_is_listed(self):
        # The lines as the hub writes them start with a listed prefix.
        root = Path(dashboard.__file__).resolve().parent
        sources = "".join((root / f).read_text(encoding="utf-8") for f in ("dashboard.py", "digest.py", "rotation.py"))
        for prefix, _ in dashboard.HUB_INPUT_KINDS:
            self.assertIn('"' + prefix, sources, f"nothing types {prefix!r} any more")
        self.assertTrue(dashboard.RESUME_NOTE.startswith("[resumed] "))


if __name__ == "__main__":
    unittest.main()
