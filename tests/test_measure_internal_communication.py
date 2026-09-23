"""tools/measure_internal_communication.py on a small fixture home.

One project with a PO room (adopted, as a made PO is) and one task room with
a Claude owner and a Codex reviewer.  The Claude owner's transcript holds a
spec, hub lines of several kinds, a CEO line, a system reminder, reads of a
handover and of the review log, an Ensemble tool result, a chat_send, a
report, an ordinary Bash result, narration, a reply, thinking, a queued
command and a compaction.  The PO's transcript holds a [rotation] prompt, a
[report] and a [digest].  The Codex reviewer's rollout holds the recorded
base instructions, a developer message, the environment context, the review
brief, reasoning, commentary, a Get-Content of SKILL.md, an Ensemble batch
and the final answer.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_task_tool_results as base  # noqa: E402
import measure_internal_communication as m  # noqa: E402

END = base._timestamp("2026-09-21T00:00:00+00:00")
START = END - base.timedelta(days=7)
IN = "2026-09-20T10:00:{:02d}.000Z"
OLD = "2026-09-01T10:00:00.000Z"


def _lines(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _claude(kind: str, content, ts: str, **extra) -> dict:
    row = {"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}}
    row.update(extra)
    return row


def _assistant(content, ts: str, rid: str, stop: str = "tool_use", usage: dict | None = None) -> dict:
    row = _claude("assistant", content, ts, requestId=rid)
    row["message"]["stop_reason"] = stop
    if usage:
        row["message"]["usage"] = usage
    return row


def _use(tid: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _result(tid: str, content) -> dict:
    return {"type": "tool_result", "tool_use_id": tid, "content": content}


def _item(kind: str, payload: dict, ts: str) -> dict:
    return {"type": kind, "timestamp": ts, "payload": payload}


def _msg(role: str, text: str, ts: str, phase: str | None = None) -> dict:
    payload = {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}
    if phase:
        payload["phase"] = phase
    return _item("response_item", payload, ts)


SPEC = "You are 'claude', the engineer on a small software team.\n\nTASK:\n# Do the thing\n" + "x" * 400
REPORT_LINE = ("[report] completed from task 'Do the thing' (#7, claude): all done, see commit abc " + "r" * 300)
DIGEST_LINE = "[digest] Proj: #7 (Do the thing) has new commits. " + "d" * 200
FROM_PO = "[from the PO] Please also add a test for the empty case. " + "p" * 100
CEO = "Alex here: why did you choose that? " + "c" * 100
REMINDER = "<system-reminder>\nThe memory index says ...\n</system-reminder>"
HANDOVER_TEXT = "# Task handover\n\nState: half done. " + "h" * 2000
HANDOVER_EDIT = "State: done, review pending. " + "e" * 300
REVIEW_LOG_TEXT = "## Review 1 (changes requested)\n\n" + "l" * 1000
GET_TASK = json.dumps({"taskNo": 7, "spec": SPEC, "messages": []})
CHAT_MSG = "## Commit `abc` ready for review\n\n@codex please review. " + "m" * 500
REPORT_TEXT = "## Completed\n\n- Re P3: added the test.\n- Commit `abc`.\n" + "t" * 800
NARRATION = "Reading the handover first."
REPLY = "Re P3: done, the empty case is covered by a test now."
THINKING = "Let me think about this carefully." * 10
REVIEW_BRIEF = ("You are 'codex', the reviewer on the task #7 \"Do the thing\" (room-aaaa). This is review 1 of "
                "this task.\n\nYou are a fresh session started for this ONE review. " + "b" * 600)
BASE_INSTRUCTIONS = "You are Codex, an agent based on GPT-5. " + "i" * 3000
DEVELOPER = "<skills_instructions>\n## Skills\n" + "s" * 500
ENV = "<environment_context>\n  <cwd>x</cwd>\n</environment_context>"
SKILL_TEXT = "---\nname: ensemble\ndescription: Work with the board\n---\n# Ensemble\n" + "k" * 5000
VERDICT = "Review 1 recorded: **changes requested**."


def build_home(root: Path) -> Path:
    home_dir = root / "EnsembleProjects" / "Proj"
    task_dir = home_dir / "do_the_thing"
    (root / ".ensemble" / "rooms").mkdir(parents=True)
    (root / ".ensemble" / "projects.json").write_text("[]", encoding="utf-8")
    home_dir.mkdir(parents=True)
    (home_dir / "project.json").write_text(json.dumps({
        "id": "proj-1", "name": "Proj", "poRoomId": "room-pppp", "home": str(home_dir)}), encoding="utf-8")
    (root / ".ensemble" / "rooms" / "room-pppp.json").write_text(json.dumps({
        "id": "room-pppp", "title": "Proj PO", "adopted": True, "mode": "solo", "cwd": str(home_dir),
        "participants": [{"kind": "agent", "agent": "claude", "sessionId": "po-2", "cwd": str(home_dir),
                          "rotations": [{"n": 1, "fromSessionId": "po-1", "toSessionId": "po-2"}]}],
    }), encoding="utf-8")
    (root / ".ensemble" / "rooms" / "room-aaaa.json").write_text(json.dumps({
        "id": "room-aaaa", "launched": True, "no": 7, "title": "Do the thing", "projectId": "proj-1",
        "taskDir": str(task_dir), "cwd": str(task_dir / "repo"),
        "participants": [
            {"kind": "agent", "agent": "claude", "role": "engineer", "sessionId": "claude-1",
             "cwd": str(task_dir / "repo")},
            {"kind": "agent", "agent": "codex", "role": "reviewer", "sessionId": "codex-0",
             "cwd": str(task_dir / "repo"),
             "reviews": [{"n": 1, "sessionId": "codex-1", "verdict": "changes_requested"}]},
        ],
    }), encoding="utf-8")
    # An ad-hoc room whose session must stay out.
    (root / ".ensemble" / "rooms" / "room-zzzz.json").write_text(json.dumps({
        "id": "room-zzzz", "title": "ad hoc", "mode": "solo",
        "participants": [{"kind": "agent", "agent": "claude", "sessionId": "adhoc-1"}],
    }), encoding="utf-8")

    proj = root / ".claude" / "projects" / "C--proj"
    handover = str(task_dir / "TASK-HANDOVER.md")
    notes = str(task_dir / "notes.txt")
    usage1 = {"input_tokens": 10, "cache_creation_input_tokens": 40_000, "cache_read_input_tokens": 0}
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 41_000}
    _lines(proj / "claude-1.jsonl", [
        _claude("user", [{"type": "text", "text": REMINDER}, {"type": "text", "text": SPEC}], IN.format(0)),
        _assistant([{"type": "thinking", "thinking": THINKING}], IN.format(1), "r1", usage=usage1),
        _assistant([{"type": "text", "text": NARRATION}], IN.format(1), "r1"),
        _assistant([_use("t1", "Read", {"file_path": handover})], IN.format(1), "r1"),
        _claude("user", [_result("t1", HANDOVER_TEXT)], IN.format(2)),
        _assistant([_use("t2", "Bash", {"command": f"cat \"{task_dir / 'REVIEW-LOG.md'}\""})], IN.format(3), "r2", usage=usage),
        _claude("user", [_result("t2", REVIEW_LOG_TEXT)], IN.format(4)),
        _assistant([_use("t3", "mcp__ensemble__ensemble_get_task", {"taskId": "room-aaaa"})], IN.format(5), "r3", usage=usage),
        _claude("user", [_result("t3", [{"type": "text", "text": GET_TASK}])], IN.format(6)),
        _assistant([_use("t4", "Bash", {"command": "git status"})], IN.format(7), "r4", usage=usage),
        _claude("user", [_result("t4", "On branch sess/x\nnothing to commit")], IN.format(8)),
        _assistant([_use("t5", "Read", {"file_path": notes})], IN.format(9), "r5", usage=usage),
        _claude("user", [_result("t5", "some notes")], IN.format(10)),
        _assistant([_use("t6", "Edit", {"file_path": str(task_dir / "repo" / "a.py"), "old_string": "a", "new_string": "b"}),
                    _use("t10", "Edit", {"file_path": handover, "old_string": "half done", "new_string": HANDOVER_EDIT}),
                    _use("t11", "Bash", {"command": "Set-Content -Path ../notes.txt -Value 'n'"}),
                    _use("t12", "Bash", {"command": f"cat \"{handover}\" && sed -n 1,20p src/a.py"})],
                   IN.format(11), "r6", usage=usage),
        _claude("user", [_result("t6", "edited"), _result("t10", "edited"), _result("t11", ""),
                         _result("t12", HANDOVER_TEXT + "\nimport os\n")], IN.format(12)),
        _assistant([_use("t7", "mcp__ensemble__chat_send", {"to": "codex", "message": CHAT_MSG})], IN.format(13), "r7", usage=usage),
        _claude("user", [_result("t7", "sent")], IN.format(14)),
        _claude("user", "[relay] New message from 'codex' in your shared room.", IN.format(15)),
        _claude("user", '<pasted_content id="1">\n' + FROM_PO + '\n</pasted_content id="1">', IN.format(16)),
        _claude("user", CEO, IN.format(17)),
        {"type": "attachment", "timestamp": IN.format(18),
         "attachment": {"type": "queued_command", "prompt": REPORT_LINE, "timestamp": IN.format(18)}},
        _assistant([_use("t8", "mcp__ensemble__ensemble_report", {"kind": "completed", "text": REPORT_TEXT})], IN.format(19), "r8", usage=usage),
        _claude("user", [_result("t8", "reported")], IN.format(20)),
        _assistant([{"type": "text", "text": REPLY}], IN.format(21), "r9", stop="end_turn", usage=usage),
        _claude("user", [{"type": "text", "text": "summary of what came before"}], IN.format(22), isCompactSummary=True),
        _assistant([_use("t9", "Bash", {"command": "git log -1"})], IN.format(23), "r10", usage=usage),
        _claude("user", [_result("t9", "abc the commit")], IN.format(24)),
        _assistant([{"type": "text", "text": "After the compaction."}], IN.format(25), "r11", stop="end_turn", usage=usage),
    ])
    _lines(proj / "po-2.jsonl", [
        _claude("user", "[rotation] You are the product owner (PO) of the project 'Proj', taking over. " + "o" * 300, IN.format(0)),
        _assistant([{"type": "text", "text": "Fresh PO here."}], IN.format(1), "p1", stop="end_turn", usage=usage1),
        _claude("user", REPORT_LINE, IN.format(2)),
        _assistant([{"type": "text", "text": "Noted."}], IN.format(3), "p2", stop="end_turn", usage=usage),
        _claude("user", DIGEST_LINE, IN.format(4)),
        _assistant([{"type": "text", "text": "Noted again."}], IN.format(5), "p3", stop="end_turn", usage=usage),
    ])
    _lines(proj / "adhoc-1.jsonl", [
        _claude("user", "hello", IN.format(0)),
        _assistant([{"type": "text", "text": "hi"}], IN.format(1), "a1", stop="end_turn"),
    ])
    # Out of the window: nothing counted from it.
    _lines(proj / "po-1.jsonl", [
        _claude("user", "[rotation] older", OLD),
        _assistant([{"type": "text", "text": "old"}], OLD, "q1", stop="end_turn"),
    ])

    rollout = root / ".codex" / "sessions" / "2026" / "09" / "20" / "rollout-2026-09-20T10-00-00-codex-1.jsonl"
    skill = "D:\\\\home\\\\x\\\\.codex\\\\skills\\\\ensemble\\\\SKILL.md"
    _lines(rollout, [
        _item("session_meta", {"id": "codex-1", "cwd": str(task_dir / "repo"),
                               "base_instructions": {"text": BASE_INSTRUCTIONS}}, IN.format(0)),
        _msg("developer", DEVELOPER, IN.format(0)),
        _msg("user", ENV, IN.format(0)),
        _msg("user", REVIEW_BRIEF, IN.format(0)),
        _item("response_item", {"type": "reasoning", "summary": [], "encrypted_content": "e" * 500}, IN.format(1)),
        _msg("assistant", "Reviewing now.", IN.format(1), phase="commentary"),
        _item("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                                "input": "const r = await tools.exec_command({\"cmd\":\"Get-Content -Raw '" + skill + "'\"});\ntext(r.output);\n"}, IN.format(1)),
        {"type": "token_usage_record", "timestamp": IN.format(1), "payload": {"usage": {"input_tokens": 8000}}},
        _item("response_item", {"type": "custom_tool_call_output", "call_id": "c1",
                                "output": [{"type": "input_text", "text": SKILL_TEXT}]}, IN.format(2)),
        _item("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "c2",
                                "input": "const a = await tools.mcp__ensemble__ensemble_whoami({});\nconst b = await tools.mcp__ensemble__ensemble_get_task({taskId:'room-aaaa'});\ntext(JSON.stringify([a,b]));\n"}, IN.format(3)),
        {"type": "token_usage_record", "timestamp": IN.format(3), "payload": {"usage": {"input_tokens": 10000}}},
        _item("response_item", {"type": "custom_tool_call_output", "call_id": "c2",
                                "output": [{"type": "input_text", "text": GET_TASK}]}, IN.format(4)),
        _item("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "c3",
                                "input": "await tools.mcp__ensemble__review_done({verdict:'changes_requested', findings:'" + "f" * 300 + "'});\n"}, IN.format(5)),
        {"type": "token_usage_record", "timestamp": IN.format(5), "payload": {"usage": {"input_tokens": 12000}}},
        _item("response_item", {"type": "custom_tool_call_output", "call_id": "c3",
                                "output": [{"type": "input_text", "text": "recorded"}]}, IN.format(6)),
        _msg("assistant", VERDICT, IN.format(7), phase="final_answer"),
        {"type": "token_usage_record", "timestamp": IN.format(7), "payload": {"usage": {"input_tokens": 12500}}},
    ])
    return task_dir


def _find(records: list[dict], **match) -> list[dict]:
    return [r for r in records if all(r.get(k) == v for k, v in match.items())]


class ClassifierTests(unittest.TestCase):
    def test_user_text_kinds(self):
        self.assertEqual(m.classify_user_text(SPEC, False)[:2], ("first", "spec"))
        self.assertEqual(m.classify_user_text(REVIEW_BRIEF, False)[:2], ("first", "review brief"))
        self.assertEqual(m.classify_user_text("[rotation] You are the PO", False)[:2], ("first", "rotation"))
        self.assertEqual(m.classify_user_text("[product owner] You are now", False)[:2], ("first", "madepo"))
        # The same lines after the first prompt are hub lines, not a first prompt.
        self.assertEqual(m.classify_user_text("[rotation] You are the PO", True)[:2], ("hub", "rotation"))
        cat, kind, extra = m.classify_user_text(REPORT_LINE, True)
        self.assertEqual((cat, kind), ("hub", "report"))
        self.assertEqual(extra, {"reportKind": "completed", "taskId": "#7", "reporter": "claude"})
        _, _, extra = m.classify_user_text("[report] review 3 (changes requested) from task 'T' (#9, codex): x", True)
        self.assertEqual(extra["reportKind"], "review (changes requested)")
        self.assertEqual(m.classify_user_text(DIGEST_LINE, True)[:2], ("hub", "digest"))
        self.assertEqual(m.classify_user_text("[from the restart helper, not the CEO] back", True)[:2], ("hub", "helper"))
        self.assertEqual(m.classify_user_text(FROM_PO, True)[:2], ("hub", m.FROM_PO_KIND))
        self.assertEqual(m.classify_user_text('<pasted_content id="1">\n' + FROM_PO + '\n</pasted_content id="1">', True)[:2],
                         ("hub", m.FROM_PO_KIND))
        self.assertEqual(m.classify_user_text(CEO, True)[:2], ("ceo", "message"))
        self.assertEqual(m.classify_user_text(REMINDER, False)[:2], ("harness", "system-reminder"))
        self.assertEqual(m.classify_user_text("<task-notification>x</task-notification>", True)[:2],
                         ("harness", "task-notification"))
        self.assertEqual(m.classify_user_text("Caveat: the messages below", True)[:2], ("harness", "tagged"))
        self.assertEqual(m.classify_user_text("anything", True, meta=True)[:2], ("harness", "meta"))

    def test_doc_kind(self):
        dirs = ["d:/home/x/ensembleprojects/proj/do_the_thing"]
        self.assertEqual(m.doc_kind("D:\\home\\x\\EnsembleProjects\\Proj\\PO-HANDOVER.md"), "PO-HANDOVER")
        self.assertEqual(m.doc_kind("PO-HANDOVER-2026-09-10.md"), "PO-HANDOVER")
        self.assertEqual(m.doc_kind("sed -n 1,40p REVIEW-LOG.md"), "REVIEW-LOG")
        self.assertEqual(m.doc_kind("TOKEN-REPORT.md"), "REPORT")
        self.assertEqual(m.doc_kind("~/.ensemble/rooms/room-8d56cd21.json"), "room json")
        self.assertEqual(m.doc_kind("D:/home/x/EnsembleProjects/Proj/do_the_thing/notes.txt", dirs), "task folder")
        self.assertEqual(m.doc_kind("D:\\\\home\\\\x\\\\EnsembleProjects\\\\Proj\\\\do_the_thing\\\\notes.txt", dirs), "task folder")
        self.assertEqual(m.doc_kind("D:/home/x/EnsembleProjects/Proj/do_the_thing/repo/a.py", dirs), "")
        self.assertEqual(m.doc_kind("cat src/report.md"), "")
        self.assertEqual(m.doc_kind(""), "")
        # A named document inside the task folder is that document, once.
        self.assertEqual(m.doc_kinds("D:/home/x/EnsembleProjects/Proj/do_the_thing/REVIEW-LOG.md", dirs), ["REVIEW-LOG"])
        self.assertEqual(m.doc_kinds("Get-Content SKILL.md; Get-Content TASK-HANDOVER.md"), ["SKILL", "TASK-HANDOVER"])
        # Relative to the call's working directory: the repo checkout or the task folder.
        repo = "D:/home/x/EnsembleProjects/Proj/do_the_thing/repo"
        self.assertEqual(m.doc_kind("../notes.txt", dirs, cwd=repo), "task folder")
        self.assertEqual(m.doc_kind("Get-Content ../notes.txt", dirs, cwd=repo), "task folder")
        self.assertEqual(m.doc_kind("cat ../repo/a.py", dirs, cwd=repo), "")
        self.assertEqual(m.doc_kind("cat docs/a.md", dirs, cwd=repo), "")
        self.assertEqual(m.doc_kind("type notes.txt", dirs, cwd=dirs[0]), "task folder")
        self.assertEqual(m.doc_kind("cat ../notes.txt", dirs, cwd="D:/elsewhere/repo"), "")
        self.assertEqual(m.doc_kind("cat ../notes.txt", dirs), "")
        # Reading segments of shell commands: one per &&, ||, ; or line; a write is not a read.
        self.assertEqual(m.read_kinds(["cat TASK-HANDOVER.md && sed -n 1,50p src/a.py"], dirs), ["TASK-HANDOVER", "other"])
        self.assertEqual(m.read_kinds(["git log -3 | head -5; cat ROADMAP.md"], dirs), ["other", "ROADMAP"])
        self.assertEqual(m.read_kinds(["cat SKILL.md\ncat > x.md <<EOF\nq\nEOF"], dirs), ["SKILL"])
        self.assertEqual(m.read_kinds(["cat a.py | head", "cat b.py"], dirs), ["other"])

    def test_results_and_inputs(self):
        dirs = ["d:/home/x/ensembleprojects/proj/do_the_thing"]
        self.assertEqual(m.classify_result("mcp__ensemble__chat_read", {}, "x", dirs), ("ensemble", "chat_read"))
        self.assertEqual(m.classify_result("Read", {"file_path": "C:/x/TASK-HANDOVER.md"}, "x", dirs), ("docread", "TASK-HANDOVER"))
        self.assertEqual(m.classify_result("Read", {"file_path": "C:/x/repo/a.py"}, "x", dirs), ("otherresult", "text"))
        self.assertEqual(m.classify_result("Bash", {"command": "cat REVIEW-LOG.md"}, "x", dirs), ("docread", "REVIEW-LOG"))
        self.assertEqual(m.classify_result("PowerShell", {"command": "Get-Content ROADMAP.md"}, "x", dirs), ("docread", "ROADMAP"))
        # Naming the document is not reading it: a grep, a git command, a write.
        self.assertEqual(m.classify_result("Bash", {"command": "grep -n due REVIEW-LOG.md"}, "x", dirs), ("otherresult", "text"))
        self.assertEqual(m.classify_result("Bash", {"command": "cat > REVIEW-LOG.md <<EOF\nx\nEOF"}, "x", dirs), ("otherresult", "text"))
        self.assertEqual(m.classify_result("Read", {"file_path": "C:/x/a.png"},
                                           [{"type": "image", "source": {}}], dirs), ("otherresult", "image"))
        # One output holding a document and something else is mixed: no single kind owns its bytes.
        self.assertEqual(m.classify_result("Bash", {"command": "cat TASK-HANDOVER.md && sed -n 1,50p src/a.py"}, "x", dirs),
                         ("docread", "mixed: TASK-HANDOVER+other"))
        self.assertEqual(m.classify_result("exec_command", None, "x", dirs,
                                           commands=["Get-Content SKILL.md", "Get-Content TASK-HANDOVER.md"]),
                         ("docread", "mixed: SKILL+TASK-HANDOVER"))
        self.assertEqual(m.classify_result("exec_command", None, "x", dirs,
                                           commands=["Get-Content SKILL.md", "Get-Content src/a.py"]),
                         ("docread", "mixed: SKILL+other"))
        self.assertEqual(m.classify_result("Bash", {"command": "cat TASK-HANDOVER.md && git status"}, "x", dirs),
                         ("docread", "TASK-HANDOVER"))            # git status reads no file
        self.assertEqual(m.classify_result("Bash", {"command": "cat a.py && cat b.py"}, "x", dirs), ("otherresult", "text"))
        # Relative paths resolve against the call's working directory.
        repo = "D:/home/x/EnsembleProjects/Proj/do_the_thing/repo"
        self.assertEqual(m.classify_result("Read", {"file_path": "../notes.txt"}, "x", dirs, cwd=repo), ("docread", "task folder"))
        self.assertEqual(m.classify_result("Bash", {"command": "cat ../notes.txt"}, "x", dirs, cwd=repo), ("docread", "task folder"))
        self.assertEqual(m.classify_result("Bash", {"command": "cat ../notes.txt"}, "x", dirs), ("otherresult", "text"))
        self.assertEqual(m.classify_input("Edit", {"file_path": "../notes.txt", "new_string": "n"}, dirs, cwd=repo),
                         ("owninternal", "write task folder"))
        self.assertEqual(m.classify_input("Bash", {"command": "Set-Content -Path ../notes.txt -Value n"}, dirs, cwd=repo),
                         ("owninternal", "write task folder"))
        self.assertEqual(m.classify_input("Edit", {"file_path": "../repo/a.py", "new_string": "n"}, dirs, cwd=repo),
                         ("owninput", "edit"))
        self.assertEqual(m.classify_result("exec_command", None, "x", dirs, ["cat SKILL.md"]), ("docread", "SKILL"))
        self.assertEqual(m.classify_input("mcp__ensemble__chat_send", {"message": "m"}, dirs), ("owninternal", "chat_send"))
        self.assertEqual(m.classify_input("mcp__ensemble__ensemble_whoami", {}, dirs), ("owninput", "other"))
        self.assertEqual(m.classify_input("Write", {"file_path": "C:/x/TASK-HANDOVER.md", "content": "c"}, dirs),
                         ("owninternal", "write TASK-HANDOVER"))
        self.assertEqual(m.classify_input("Write", {"file_path": "C:/x/repo/a.py", "content": "c"}, dirs), ("owninput", "edit"))
        self.assertEqual(m.classify_input("Bash", {"command": "cat > REVIEW-LOG.md <<EOF\nx\nEOF"}, dirs),
                         ("owninternal", "write REVIEW-LOG"))
        self.assertEqual(m.classify_input("Bash", {"command": "git status"}, dirs), ("owninput", "shell"))
        self.assertEqual(m.classify_input("Grep", {"pattern": "x"}, dirs), ("owninput", "read/search"))


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        cls.task_dir = build_home(cls.home)
        cls.report = m.measure(cls.home, START, END, top=5, per_kind=2)
        cls.records = cls.report.pop("topRecords")
        cls.per_kind = cls.report.pop("perKindRecords")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_scope_and_roles(self):
        scope = m.conversation_scope(self.home)
        self.assertEqual(scope["rooms"], {"po": 1, "task": 1})
        self.assertEqual(scope["sessions"]["claude-1"]["role"], "owner")
        self.assertEqual(scope["sessions"]["codex-1"]["role"], "reviewer")
        self.assertEqual(scope["sessions"]["po-2"]["role"], "po")
        self.assertEqual(scope["sessions"]["po-1"]["role"], "po")
        self.assertIn("adhoc-1", scope["excluded"])
        self.assertEqual(scope["task_dirs"], [m._norm_dir(str(self.task_dir))])
        self.assertEqual(self.report["agents"]["claude"]["sessions"], {"po": 1, "owner": 1, "reviewer": 0})
        self.assertEqual(self.report["agents"]["codex"]["sessions"], {"po": 0, "owner": 0, "reviewer": 1})

    def _owner(self) -> dict:
        return self.report["agents"]["claude"]["roles"]["owner"]

    def test_claude_owner_categories(self):
        g = self._owner()
        kinds = g["kinds"]
        self.assertEqual(kinds["first"]["spec"]["count"], 1)
        self.assertEqual(kinds["first"]["spec"]["bytes"], len(SPEC.encode()))
        self.assertEqual(set(kinds["hub"]), {"relay", m.FROM_PO_KIND, "report"})
        self.assertEqual(kinds["hub"]["report"]["count"], 1)         # the queued command
        # Bytes are what the model was sent: the paste wrapper included.
        wrapped = '<pasted_content id="1">\n' + FROM_PO + '\n</pasted_content id="1">'
        self.assertEqual(kinds["hub"][m.FROM_PO_KIND]["bytes"], len(wrapped.encode()))
        self.assertEqual(g["categories"]["ceo"]["count"], 1)
        # A tool's acknowledgement ("sent", "reported") is an Ensemble result too.
        self.assertEqual(set(kinds["ensemble"]), {"ensemble_get_task", "chat_send", "ensemble_report"})
        self.assertEqual(kinds["ensemble"]["ensemble_get_task"]["bytes"],
                         base._payload_bytes([{"type": "text", "text": GET_TASK}]))
        self.assertEqual({k: v["count"] for k, v in kinds["docread"].items()},
                         {"TASK-HANDOVER": 1, "REVIEW-LOG": 1, "task folder": 1,
                          "mixed: TASK-HANDOVER+other": 1})      # a handover and source in one output
        self.assertEqual(g["categories"]["ownnarr"]["count"], 1)
        self.assertEqual(g["categories"]["ownreply"]["count"], 2)
        self.assertEqual({k: v["count"] for k, v in kinds["owninternal"].items()},
                         {"chat_send": 1, "ensemble_report": 1, "write TASK-HANDOVER": 1,
                          "write task folder": 1})                  # Set-Content ../notes.txt from repo/
        # Inputs: Read x2, Bash x4, get_task, Edit a.py; results: git status, 3 edits/writes, git log.
        self.assertEqual(g["categories"]["owninput"]["count"], 8)
        self.assertEqual(g["categories"]["otherresult"]["count"], 5)
        self.assertEqual({k: v["count"] for k, v in kinds["harness"].items()},
                         {"system-reminder": 1, "compact summary": 1})
        self.assertEqual(g["thinking"]["count"], 1)
        self.assertEqual(g["thinking"]["bytes"], len(THINKING.encode()))
        total = g["total"]
        self.assertEqual(total["count"], sum(c["count"] for c in g["categories"].values()))
        self.assertEqual(g["internal"]["count"],
                         sum(g["categories"][c]["count"] for c in m.INTERNAL))
        self.assertAlmostEqual(g["internalSharePayload"], round(100 * g["internal"]["bytes"] / total["bytes"], 1))

    def test_reread_arithmetic(self):
        recs = [r for r in self.records if r["session"] == "claude-1"] or []
        # topRecords holds only the largest; use the full scan for the arithmetic.
        session = m.scan_claude(self.home / ".claude" / "projects" / "C--proj" / "claude-1.jsonl",
                                START, END, "owner", {"room": "room-aaaa", "task": 7}, [m._norm_dir(str(self.task_dir))])
        spec = _find(session.records, cat="first")[0]
        # 11 request ids in the file; the compaction drops the 9 before it.
        self.assertEqual(spec["remainingTurns"], 9)
        self.assertEqual(spec["rereadBytes"], spec["bytes"] * 9)
        narration = _find(session.records, cat="ownnarr")[0]
        self.assertEqual(narration["remainingTurns"], 8)      # its own call does not count
        handover = _find(session.records, kind="TASK-HANDOVER")[0]
        self.assertEqual(handover["remainingTurns"], 8)
        after = _find(session.records, cat="ownreply")
        self.assertEqual([r["remainingTurns"] for r in after], [0, 0])
        summary = _find(session.records, kind="compact summary")[0]
        self.assertEqual(summary["remainingTurns"], 2)
        # Usage: 11 calls; the first call's fixed prompt = 40,010 - (reminder + spec) / 4.
        self.assertEqual(session.calls, 11)
        self.assertEqual(session.input_tokens, 40_010 + 10 * 41_110)
        self.assertEqual(session.visible_first_call, len(REMINDER.encode()) + len(SPEC.encode()))
        fixed = self.report["agents"]["claude"]["fixedPromptFromUsage"]
        self.assertEqual(fixed["sessions"], 2)
        self.assertEqual(fixed["minTokens"], 40_010 - m.math.ceil(session.visible_first_call / 4))

    def test_po_room(self):
        g = self.report["agents"]["claude"]["roles"]["po"]
        self.assertEqual(g["kinds"]["first"], {"rotation": g["kinds"]["first"]["rotation"]})
        self.assertEqual({k: v["count"] for k, v in g["kinds"]["hub"].items()}, {"report": 1, "digest": 1})
        self.assertEqual(g["categories"]["ownreply"]["count"], 3)
        self.assertEqual(g["conversations"], 1)          # po-1 is out of the window

    def test_codex_reviewer(self):
        g = self.report["agents"]["codex"]["roles"]["reviewer"]
        kinds = g["kinds"]
        self.assertEqual(kinds["first"], {"review brief": kinds["first"]["review brief"]})
        self.assertEqual({k: v["count"] for k, v in kinds["fixed"].items()}, {"base instructions": 1, "developer": 1})
        self.assertEqual({k: v["count"] for k, v in kinds["harness"].items()}, {"environment_context": 1})
        self.assertEqual({k: v["count"] for k, v in kinds["docread"].items()}, {"SKILL": 1})
        self.assertEqual({k: v["count"] for k, v in kinds["ensemble"].items()},
                         {"batch: ensemble_whoami+ensemble_get_task": 1, "review_done": 1})
        self.assertEqual({k: v["count"] for k, v in kinds["owninternal"].items()}, {"review_done": 1})
        self.assertEqual(g["categories"]["owninput"]["count"], 2)   # the Get-Content script, the whoami batch
        self.assertEqual(g["categories"]["ownnarr"]["count"], 1)
        self.assertEqual(g["categories"]["ownreply"]["count"], 1)
        self.assertEqual(g["thinking"]["count"], 1)
        usage = self.report["agents"]["codex"]["usage"]
        self.assertEqual(usage, {"calls": 4, "inputTokens": 8000 + 10000 + 12000 + 12500})
        fixed = self.report["agents"]["codex"]["fixedPromptFromUsage"]
        visible = sum(len(t.encode()) for t in (BASE_INSTRUCTIONS, DEVELOPER, ENV, REVIEW_BRIEF))
        self.assertEqual(fixed["medianTokens"], 8000 - m.math.ceil(visible / 4))
        rollout = next(base._glob(self.home / ".codex" / "sessions", "**/rollout-*.jsonl"))
        session = m.scan_codex(rollout, START, END, "reviewer", {"room": "room-aaaa", "task": 7},
                               [m._norm_dir(str(self.task_dir))])
        brief = _find(session.records, cat="first")[0]
        self.assertEqual(brief["remainingTurns"], 4)
        skill = _find(session.records, kind="SKILL")[0]
        self.assertEqual(skill["remainingTurns"], 3)
        self.assertEqual(_find(session.records, cat="ownreply")[0]["remainingTurns"], 0)

    def test_signals_top_and_dump(self):
        sig = self.report["agents"]["claude"]["signals"]
        self.assertEqual(sig["reportsByKind"], {"completed": 2})      # PO room + the owner's queued line
        self.assertEqual(sig["tasksReportingCompleted"], 1)
        self.assertEqual(sig["fromPoLines"], 1)
        self.assertEqual(sig["rePointAnswers"], 2)                    # the reply and the report text
        top = self.report["top"]
        self.assertEqual(len(top), 5)
        self.assertEqual([r["rereadBytes"] for r in top], sorted((r["rereadBytes"] for r in top), reverse=True))
        self.assertTrue(all(r["cat"] in m.INTERNAL and r["cat"] != "ownnarr" for r in top))
        self.assertTrue(all("text" in r for r in self.records))
        with tempfile.TemporaryDirectory() as out:
            names = m.dump_texts(self.records, Path(out))
            self.assertEqual(len(names), 5)
            first = (Path(out) / names[0]).read_text(encoding="utf-8")
            self.assertTrue(first.startswith("<!-- "))
            self.assertIn(self.records[0]["text"][:40], first)

    def test_written_text_is_kept_and_dumped(self):
        session = m.scan_claude(self.home / ".claude" / "projects" / "C--proj" / "claude-1.jsonl", START, END, "owner",
                                {"room": "room-aaaa", "task": 7, "cwd": str(self.task_dir / "repo")},
                                [m._norm_dir(str(self.task_dir))])
        edit = _find(session.records, kind="write TASK-HANDOVER")[0]
        self.assertEqual(edit["text"], HANDOVER_EDIT)                # the new text, not the message field
        self.assertEqual(edit["textBytes"], len(HANDOVER_EDIT.encode()))
        self.assertLess(edit["textBytes"], edit["bytes"])            # bytes = the whole input
        shell = _find(session.records, kind="write task folder")[0]
        self.assertEqual(shell["text"], "Set-Content -Path ../notes.txt -Value 'n'")
        report = _find(session.records, kind="ensemble_report", cat="owninternal")[0]
        self.assertEqual(report["text"], REPORT_TEXT)
        with tempfile.TemporaryDirectory() as out:
            names = m.dump_texts([edit, shell], Path(out))
            first = (Path(out) / names[0]).read_text(encoding="utf-8")
            self.assertIn(f"bytes {edit['bytes']} text-bytes {edit['textBytes']}", first)
            self.assertTrue(first.endswith(HANDOVER_EDIT))
            self.assertTrue((Path(out) / names[1]).read_text(encoding="utf-8").endswith("-Value 'n'"))

    def test_signals_count_tasks_per_project(self):
        def report(project, task):
            return {"cat": "hub", "kind": "report", "reportKind": "completed", "taskId": task,
                    "project": project, "room": "room-" + project, "text": ""}
        two_projects = m._signals([report("p1", "#7"), report("p2", "#7")])
        self.assertEqual(two_projects["tasksReportingCompleted"], 2)
        self.assertEqual(two_projects["tasksReportingCompletedMoreThanOnce"], 0)
        twice = m._signals([report("p1", "#7"), report("p1", "#7")])
        self.assertEqual(twice["tasksReportingCompleted"], 1)
        self.assertEqual(twice["extraCompletedReports"], 1)
        po = _find(self.per_kind, cat="hub", kind="report")
        self.assertTrue(po and all(r["project"] == "proj-1" for r in po))   # the PO room's project from project.json

    def test_top_per_kind(self):
        two = self.per_kind                                             # measure(per_kind=2)
        keys = [(r["cat"], r["kind"]) for r in two]
        self.assertEqual(keys, sorted(keys))                            # grouped by kind, kinds sorted
        self.assertLessEqual(max(keys.count(k) for k in keys), 2)
        self.assertIn(("hub", "report"), keys)
        self.assertIn(("hub", "digest"), keys)
        self.assertIn(("first", "spec"), keys)
        self.assertIn(("ownreply", "ownreply"), keys)
        self.assertNotIn("ownnarr", [c for c, _ in keys])
        for key in set(keys):
            same = [r["rereadBytes"] for r in two if (r["cat"], r["kind"]) == key]
            self.assertEqual(same, sorted(same, reverse=True))
        one = m.top_per_kind(two, 1)
        self.assertEqual(len(one), len(set(keys)))
        with tempfile.TemporaryDirectory() as out:
            names = m.dump_texts(one, Path(out), prefix="kind-")
            self.assertTrue(all(n.startswith("kind-") for n in names))

    def test_markdown_and_files(self):
        text = m._markdown(self.report)
        self.assertIn("## Claude:", text)
        self.assertIn("### Claude owner:", text)
        self.assertIn("### Codex reviewer:", text)
        self.assertIn("| **Internal communication", text)
        self.assertIn("largest internal texts", text)
        items = {row["item"] for row in self.report["filesOnDisk"]}
        self.assertIn("Ensemble MCP tool definitions: owner (common tools)", items)


if __name__ == "__main__":
    unittest.main()
