"""The claude.ai guard (#425, P469; rule P171).

* ``asks_for_web_artifact``: the CEO's text names claude.ai, Claude Docs or an
  artifact and does not negate it; the CEO's own P469 sentence does not pass;
* ``handle``: other tools and read-only calls pass without asking; anything
  else is denied unless the hub says yes;
* ``main`` fails closed (no hub environment → deny, exit 0);
* ``ask_hub`` takes only a 200 with ``allow: true`` as a yes;
* the matcher catches the guarded tools and not the everyday ones;
* the wiring: the task file and the PO file have the guard, the adopted
  sessions' file does not;
* ``_artifact_request_allowed``: the terminal, the seat, the room kind, and
  the latest message the input journal recorded as typed by the CEO.
"""
from __future__ import annotations

import http.server
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import artifact_guard  # noqa: E402
import dashboard  # noqa: E402
import input_provenance  # noqa: E402
import rotation  # noqa: E402
from backends import ptyrun  # noqa: E402

P469 = ("i see that ensemble still creates artifacts on the web, please don't allow claude "
        "to create anything outside ensemble, never create an artifact outside Ensemble")


class AsksForWebArtifact(unittest.TestCase):
    def test_explicit_asks_pass(self):
        for text in ("Make it a claude.ai artifact.",
                     "please publish the plan as an artifact",
                     "Can you put it in Claude Docs?",
                     "I want you to share it on claude.ai",
                     "Write the roadmap. Then make a claude.ai artifact of it.",
                     "could you make a Claude artifact of this",
                     "Don't write a file. Publish it to claude.ai.",   # the no is an earlier sentence
                     # review 2: a later, unrelated no does not take the ask back
                     "Please publish the roadmap on claude.ai. Do not include secrets.",
                     "Publish it on claude.ai. Don't forget the chart.",
                     "Publish the Documents page to claude.ai"):
            self.assertTrue(artifact_guard.asks_for_web_artifact(text), text)

    def test_negations_and_other_text_do_not(self):
        for text in (P469,
                     "never create an artifact outside Ensemble",
                     "don't publish to claude.ai",
                     "Don’t publish to claude.ai",                 # a curly apostrophe
                     "no artifacts, files only",
                     "write it as a file instead of an artifact",
                     "do it without claude docs",
                     "make a web page and share it online",       # no synonyms
                     "", "   ", None, 42):
            self.assertFalse(artifact_guard.asks_for_web_artifact(text), text)

    def test_mentions_and_withdrawn_asks_do_not(self):
        for text in ("These documents are known as artifacts.",            # review 1
                     "The instructions say to create an artifact in the project folder.",
                     "Make a claude.ai artifact? No, save it in Documents.",
                     "publish it on claude.ai, not as a file",
                     "No artifacts last time. This time, publish it as an artifact.",
                     "ensemble still creates artifacts on the web",
                     "why are there artifacts on claude.ai?",
                     "make it an artifact",                                # bare: may be local
                     "save the CI artifacts",
                     "an artifact, please",
                     # review 2
                     "Publish the artifact in the project Documents folder.",   # a local place
                     'Please share the phrase "publish it on claude.ai" in a local report.',
                     "Please share the phrase \u201cpublish it on claude.ai\u201d in a report.",
                     "Please share the phrase 'publish it on claude.ai' in a report.",
                     "Publish it on claude.ai. Actually, don't.",               # withdrawn
                     "Publish it on claude.ai. No.",
                     "Publish it on claude.ai. Never mind.",
                     "Publish it on claude.ai. Don't publish the secrets."):
            self.assertFalse(artifact_guard.asks_for_web_artifact(text), text)


class Handle(unittest.TestCase):
    def event(self, tool, **tool_input):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                "session_id": "s1"}

    def test_other_tools_pass_without_asking(self):
        ask = mock.Mock(return_value=False)
        for tool in ("Read", "Bash", "Agent", "Write", "mcp__ensemble__chat_send"):
            self.assertIsNone(artifact_guard.handle(self.event(tool), {}, ask))
        ask.assert_not_called()

    def test_read_only_calls_pass_without_asking(self):
        ask = mock.Mock(return_value=False)
        for data in (self.event("Artifact", action="read", url="u"),
                     self.event("Artifact", action="List"),
                     self.event("mcp__claude_ai_Claude_Docs__read"),
                     self.event("mcp__claude_ai_Claude_Docs__query")):
            self.assertIsNone(artifact_guard.handle(data, {}, ask), data)
        ask.assert_not_called()

    def test_the_rest_asks_the_hub_and_only_a_yes_passes(self):
        for data in (self.event("Artifact", file_path="x.html"),          # publish is the default
                     self.event("Artifact", action="publish"),
                     self.event("Artifact", action="delete"),
                     self.event("ArtifactData", action="set"),
                     self.event("ArtifactComments"),
                     self.event("mcp__claude_ai_Claude_Docs__batch"),
                     self.event("mcp__claude_ai_Claude_Docs__create"),
                     self.event("mcp__claude-artifacts__publish")):
            out = artifact_guard.handle(data, {}, mock.Mock(return_value=False))
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", data)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"],
                             artifact_guard.DENY_REASON)
            ask = mock.Mock(return_value=True)
            self.assertIsNone(artifact_guard.handle(data, {"E": "1"}, ask), data)
            ask.assert_called_once_with({"E": "1"}, data)

    def test_the_matcher_catches_the_guarded_tools_only(self):
        rx = re.compile(artifact_guard.MATCHER)
        for tool in ("Artifact", "ArtifactComments", "ArtifactData", "ArtifactCheck",
                     "mcp__claude_ai_Claude_Docs__batch", "mcp__claude-artifacts__publish"):
            self.assertTrue(rx.fullmatch(tool), tool)
            self.assertTrue(artifact_guard.guarded(tool), tool)
        for tool in ("Read", "Bash", "Agent", "Write", "Edit", "mcp__ensemble__ensemble_report"):
            self.assertFalse(rx.fullmatch(tool), tool)
            self.assertFalse(artifact_guard.guarded(tool), tool)


class HandleEvent(unittest.TestCase):
    def test_a_missing_or_other_event_is_denied_even_when_the_hub_says_yes(self):
        yes = mock.Mock(return_value=True)
        for data in ({"tool_name": "Artifact"}, {"hook_event_name": None, "tool_name": "Artifact"},
                     {"hook_event_name": "PostToolUse", "tool_name": "Artifact"}):
            self.assertEqual(artifact_guard.handle(data, env={}, ask=yes), artifact_guard.deny(), data)
        yes.assert_not_called()
        self.assertIsNone(artifact_guard.handle({"hook_event_name": "PreToolUse",
                                                 "tool_name": "Artifact"}, env={}, ask=yes))


class Main(unittest.TestCase):
    def run_script(self, data, env):
        return subprocess.run([sys.executable, str(ROOT / "artifact_guard.py")],
                              input=json.dumps(data).encode(), env=env, capture_output=True,
                              timeout=30)

    def test_without_the_hub_environment_it_denies_and_exits_0(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENSEMBLE_")}
        res = self.run_script({"hook_event_name": "PreToolUse", "tool_name": "Artifact",
                               "tool_input": {"file_path": "x.html"}}, env)
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_another_tool_prints_nothing_and_bad_input_is_denied(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENSEMBLE_")}
        res = self.run_script({"hook_event_name": "PreToolUse", "tool_name": "Read"}, env)
        self.assertEqual((res.returncode, res.stdout), (0, b""))
        for raw in (b"{not json", b"", b"[1]", b"[" * 5000 + b"]" * 5000,
                    b'{"hook_event_name": "PreToolUse"}',
                    b'{"hook_event_name": "PreToolUse", "tool_name": 3}',
                    b'{"hook_event_name": "Other", "tool_name": "Artifact"}',
                    b'{"tool_name": "Artifact"}',                    # review 2: no event
                    b'{"hook_event_name": null, "tool_name": "Artifact"}'):
            res = subprocess.run([sys.executable, str(ROOT / "artifact_guard.py")], input=raw,
                                 env=env, capture_output=True, timeout=30)
            self.assertEqual(res.returncode, 0, raw[:40])
            self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"],
                             "deny", raw[:40])


class AskHub(unittest.TestCase):
    def serve(self, status, body):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                seen.append((self.path, json.loads(self.rfile.read(ln))))
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = {"ENSEMBLE_HOOK_URL": f"http://127.0.0.1:{srv.server_port}/api/agent/hook",
               "ENSEMBLE_HOOK_ROOM": "room-t", "ENSEMBLE_HOOK_IDENTITY": "claude",
               "ENSEMBLE_PTY_ID": "pty-1"}
        return env, seen

    DATA = {"tool_name": "Artifact", "tool_input": {"action": "publish"}, "session_id": "s1"}

    def test_a_200_with_allow_true_is_a_yes(self):
        env, seen = self.serve(200, {"allow": True, "why": "asked"})
        self.assertIs(artifact_guard.ask_hub(env, self.DATA, timeout=5), True)
        path, body = seen[0]
        self.assertEqual(path, artifact_guard.CHECK_PATH)
        self.assertEqual(body, {"room": "room-t", "identity": "claude", "ptyId": "pty-1",
                                "sessionId": "s1", "tool": "Artifact", "action": "publish"})

    def test_anything_else_is_a_no(self):
        for status, body in ((200, {"allow": False}), (200, {"allow": "true"}), (200, [True]),
                             (503, {"allow": True}), (404, {})):
            env, _ = self.serve(status, body)
            self.assertIs(artifact_guard.ask_hub(env, self.DATA, timeout=5), False, (status, body))

    def test_no_hub_or_no_environment_is_a_no(self):
        env, _ = self.serve(200, {"allow": True})
        for drop in ("ENSEMBLE_HOOK_URL", "ENSEMBLE_HOOK_ROOM", "ENSEMBLE_HOOK_IDENTITY",
                     "ENSEMBLE_PTY_ID"):
            self.assertIs(artifact_guard.ask_hub({k: v for k, v in env.items() if k != drop},
                                                 self.DATA, timeout=5), False, drop)
        env["ENSEMBLE_HOOK_URL"] = "http://127.0.0.1:1/api/agent/hook"        # nothing listens
        self.assertIs(artifact_guard.ask_hub(env, self.DATA, timeout=2), False)


class Wiring(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, value in (("TASK_CLAUDE_SETTINGS", tmp / "task.json"),
                            ("RTK_CLAUDE_SETTINGS", tmp / "rtk.json"),
                            ("USAGE_CLAUDE_SETTINGS", tmp / "usage.json"),
                            ("PO_CLAUDE_SETTINGS", tmp / "po.json")):
            p = mock.patch.object(dashboard, name, value)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def guards(path: Path) -> list[dict]:
        pre = json.loads(path.read_text(encoding="utf-8"))["hooks"].get("PreToolUse", [])
        return [e for e in pre for h in e["hooks"] if "artifact_guard.py" in h["command"]]

    def test_task_and_po_files_have_the_guard_first_the_adopted_file_not(self):
        for path in (dashboard._task_claude_settings(False), dashboard._task_claude_settings(True),
                     dashboard._usage_claude_settings(guard=True)):
            entries = self.guards(path)
            self.assertEqual(len(entries), 1, path)
            self.assertEqual(entries[0]["matcher"], artifact_guard.MATCHER)
            pre = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
            self.assertEqual(pre[0], entries[0])                 # first of all
            self.assertFalse(entries[0]["hooks"][0].get("async"), "a deny must wait for it")
        self.assertEqual(dashboard._usage_claude_settings(), dashboard.USAGE_CLAUDE_SETTINGS)
        self.assertEqual(self.guards(dashboard.USAGE_CLAUDE_SETTINGS), [])

    def test_po_rooms_get_the_po_file_adopted_sessions_the_usage_file(self):
        with mock.patch.object(dashboard, "load_projects", return_value=[{"id": "p", "poRoomId": "room-po"}]), \
                mock.patch.object(dashboard, "load_settings",
                                  return_value={**dashboard._SETTINGS_DEFAULTS, "cheapReader": False}):
            args, _, _ = dashboard._rtk_task_wiring({"id": "room-po", "launched": True}, "claude")
            self.assertEqual(args, ["--settings", str(dashboard.PO_CLAUDE_SETTINGS)])
            args, _, _ = dashboard._rtk_task_wiring({"id": "room-po", "adopted": True}, "claude")
            self.assertEqual(args, ["--settings", str(dashboard.USAGE_CLAUDE_SETTINGS)])


class RequestAllowed(unittest.TestCase):
    PAYLOAD = {"room": "room-t", "identity": "claude", "ptyId": "pty-1", "sessionId": "s1",
               "tool": "Artifact", "action": "publish"}

    def setUp(self):
        self.turns = []
        self.room = {"id": "room-t", "launched": True}
        for p in (mock.patch.object(dashboard, "_session_input_context",
                                    side_effect=lambda sid: ("room-t", "claude") if sid == "s1" else ("", "")),
                  mock.patch.object(dashboard.chatroom, "get_room", side_effect=lambda rid, public=False: self.room),
                  mock.patch.object(dashboard, "_hub_task_room", side_effect=lambda r: not r.get("adopted")),
                  mock.patch.object(dashboard, "_po_room", return_value=False),
                  mock.patch.object(dashboard, "find_transcript", return_value=Path("t.jsonl")),
                  mock.patch.object(dashboard, "claude_turns_classified", side_effect=lambda p: self.turns)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def owner(pty_id):
        return ("room-t", "claude") if pty_id == "pty-1" else None

    @staticmethod
    def human(text, provenance="record"):
        return {"role": "user", "kind": "human", "provenance": provenance, "text": text}

    def allowed(self, payload=None, owner=None):
        return dashboard._artifact_request_allowed(self.PAYLOAD if payload is None else payload,
                                                   owner or self.owner)

    def test_the_latest_typed_message_that_asks_allows(self):
        self.turns = [self.human("hi"), {"role": "assistant", "text": "ok"},
                      self.human("publish it as a claude.ai artifact")]
        self.assertEqual(self.allowed(), (True, "asked"))

    def test_only_the_latest_typed_message_counts(self):
        self.turns = [self.human("publish it as an artifact"), self.human("now fix the tests")]
        self.assertEqual(self.allowed(), (False, "not_asked"))
        self.turns = [self.human(P469)]
        self.assertEqual(self.allowed(), (False, "not_asked"))

    def test_a_hub_turn_or_a_guessed_human_turn_is_ignored(self):
        self.turns = [self.human("fix the tests"),
                      {"role": "user", "kind": "hub", "provenance": "record",
                       "text": "make it a claude.ai artifact"},
                      self.human("make it a claude.ai artifact", provenance="guess")]
        self.assertEqual(self.allowed(), (False, "not_asked"))
        self.turns = [{"role": "user", "kind": "hub", "provenance": "record", "text": "an artifact"}]
        self.assertEqual(self.allowed(), (False, "no_ceo_message"))

    def test_who_is_asking_and_where(self):
        self.turns = [self.human("publish it as an artifact")]
        self.assertEqual(self.allowed(owner=lambda pty: None), (False, "unknown_agent"))
        self.assertEqual(self.allowed(owner=lambda pty: ("room-t", "codex")), (False, "unknown_agent"))
        self.assertEqual(self.allowed(owner=mock.Mock(side_effect=RuntimeError)), (False, "unknown_agent"))
        self.assertEqual(self.allowed({**self.PAYLOAD, "sessionId": "s2"}), (False, "not_this_seat"))
        for bad in (None, [], {**self.PAYLOAD, "ptyId": ""}, {**self.PAYLOAD, "sessionId": 3}):
            self.assertEqual(dashboard._artifact_request_allowed(bad, self.owner),
                             (False, "bad_payload"), bad)
        self.room = {"id": "room-t", "adopted": True}
        self.assertEqual(self.allowed(), (False, "not_guarded_room"))
        self.room = None
        self.assertEqual(self.allowed(), (False, "not_guarded_room"))


class AgentCannotTypeTheAsk(unittest.TestCase):
    """Review 1: an agent posting to the hub over loopback cannot type the
    request the guard needs; its write is journalled as the hub's."""

    class Session:
        meta = {"room": "room-t", "identity": "claude"}
        hub_line_typed = False
        hub_line_text = ""
        person_line_text = ""

        def __init__(self):
            self.writes = []

        def alive(self):
            return True

        def write(self, data):
            self.writes.append(data)
            return True

    def handler(self, peer=""):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h._agent_peer = mock.Mock(return_value=peer)
        return h

    def post(self, sess, data, peer):
        body = json.dumps({"id": "pty-x", "data": data}).encode()
        h = self.handler(peer)
        h.path, h.command, h.request_version = "/api/pty/input", "POST", "HTTP/1.1"
        h.requestline = "POST /api/pty/input HTTP/1.1"
        h.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json",
                     "Host": "127.0.0.1"}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        with mock.patch.object(ptyrun, "get", lambda _id: sess), \
                mock.patch.object(dashboard.Handler, "_pty_input_by_person", return_value=True), \
                mock.patch.object(rotation, "room_rotating", return_value=False), \
                mock.patch.object(dashboard, "note_answer"), \
                mock.patch.object(dashboard, "reopen_done"):
            h.do_POST()
        return h

    def kinds(self, chunks, peer):
        sess = self.Session()
        with mock.patch.object(dashboard, "_record_typed_input") as record:
            handlers = [self.post(sess, c, peer) for c in chunks]
        return [c.args[2]["kind"] for c in record.call_args_list], handlers

    def test_the_helper_looks_the_sender_up_only_for_an_ask(self):
        h = self.handler("an agent's process")
        self.assertFalse(h._agent_asks_as_ceo("fix the tests"))
        h._agent_peer.assert_not_called()
        self.assertTrue(h._agent_asks_as_ceo("publish it on claude.ai"))
        self.assertFalse(self.handler("")._agent_asks_as_ceo("publish it on claude.ai"))
        h = self.handler()
        h._agent_peer.side_effect = RuntimeError
        self.assertTrue(h._agent_asks_as_ceo("publish it on claude.ai"))   # cannot tell: not the person
        self.assertFalse(h._agent_asks_as_ceo(None))

    def test_an_agents_ask_in_its_terminal_is_the_hubs(self):
        self.assertEqual(self.kinds(["publish it on claude.ai\r"], "an agent")[0], ["hub"])
        self.assertEqual(self.kinds(["publish it on claude.ai", "\r"], "an agent")[0], ["hub"])

    def test_a_persons_ask_and_an_agents_other_line_stay_as_they_were(self):
        self.assertEqual(self.kinds(["publish it on claude.ai", "\r"], "")[0], ["human"])
        kinds, handlers = self.kinds(["fix the tests\r"], "an agent")
        self.assertEqual(kinds, ["human"])
        handlers[0]._agent_peer.assert_not_called()

    def test_an_agent_cannot_edit_a_line_into_an_ask(self):
        # Review 2: the raw bytes are not the ask, the edited line is.
        for chunks in (["zzz\b\b\bPublish it on claude.ai\r"],
                       ["zzz\x7f\x7f\x7fPublish it on claude.ai\r"],
                       ["Publish it on claude.ai", "x\x7f\r"],
                       ["claude.ai", "\x1b[H", "Publish it on \r"],
                       ["Publish it on claude.a", "\x15Publish it on claude.ai\r"]):
            self.assertEqual(self.kinds(chunks, "an agent")[0], ["hub"], chunks)
        # a person's edited line is still the person's (the sender is looked up)
        kinds, handlers = self.kinds(["Publish it on claude.a", "x\x7f\x7fai\r"], "")
        self.assertEqual(kinds, ["human"])

    def test_an_edited_hub_line_never_becomes_a_typed_turn(self):
        # The transcript side: the editing fallback pairs only explicit-human
        # rows whose hash differs, so the agent's edited line (now journalled
        # as the hub's) leaves the submitted ask without provenance.
        raw = "zzz\b\b\bPublish it on claude.ai"
        for kind, paired in (("hub", False), ("human", True)):
            row = {"hash": input_provenance.text_hash(raw), "kind": kind, "at": 100.0,
                   "_order": 0}
            got = input_provenance.assign({row["hash"]: [row]},
                                          [(1, "Publish it on claude.ai", 100.5)])
            self.assertEqual(1 in got, paired, kind)

    def test_every_person_write_boundary_checks_it(self):
        src = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertIn('if text and self._agent_asks_as_ceo(text):\n            extra["origin"] = "hub"', src)
        self.assertIn("as_hub = bool(text and submit) and self._agent_asks_as_ceo(text)", src)
        self.assertIn("self._agent_asks_as_ceo(line_now)", src)
        self.assertIn("submit_text != line_now and _could_be_ask(submit_text))", src)


if __name__ == "__main__":
    unittest.main()
