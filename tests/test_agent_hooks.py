"""A Claude agent tells the hub what it is doing through its hooks: the launch
settings that register them, the hook command (``agent_hook.py``), the hub's
endpoint and state (``agent_hooks.py``), and ``attention`` preferring what the
agent said over what its screen looks like — with the screen as the fallback."""
from __future__ import annotations

import http.client
import http.server
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agent_hook
import agent_hooks
import attention
import chatroom
import dashboard
import digest
from backends import ptyrun

REPO = Path(__file__).resolve().parent.parent
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Every event name of the Claude Code hooks reference (checked 2026-09-18
# against the docs and the installed CLI, 2.1.276).
KNOWN_EVENTS = {
    "SessionStart", "Setup", "UserPromptSubmit", "UserPromptExpansion", "PreToolUse",
    "PermissionRequest", "PermissionDenied", "PostToolUse", "PostToolUseFailure",
    "PostToolBatch", "Notification", "MessageDisplay", "SubagentStart", "SubagentStop",
    "TaskCreated", "TaskCompleted", "Stop", "StopFailure", "TeammateIdle",
    "InstructionsLoaded", "ConfigChange", "CwdChanged", "DirectoryAdded", "FileChanged",
    "WorktreeCreate", "WorktreeRemove", "PreCompact", "PostCompact", "PreModelSwitch",
    "PostModelSwitch", "Elicitation", "ElicitationResult", "SessionEnd"}


# ---------------------------------------------------------------------------
# The settings file
# ---------------------------------------------------------------------------

class LaunchSettings(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, value in (("RTK_DIR", tmp / "rtk"),
                            ("RTK_CLAUDE_SETTINGS", tmp / "rtk" / "claude-task-settings.json"),
                            ("USAGE_CLAUDE_SETTINGS", tmp / "usage" / "claude-agent-settings.json")):
            patch = mock.patch.object(dashboard, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def settings(self, rtk: bool) -> dict:
        with mock.patch.object(dashboard, "_rtk_task_room", return_value=rtk):
            args, _, _ = dashboard._rtk_task_wiring({"id": "room-x"}, "claude")
        self.assertEqual(args[0], "--settings")
        return json.loads(Path(args[1]).read_text(encoding="utf-8"))     # valid JSON

    def handlers(self, settings: dict, event: str) -> list[dict]:
        return [h for entry in settings["hooks"].get(event, []) for h in entry["hooks"]
                if "agent_hook.py" in h["command"]]

    def test_every_claude_launch_reports_the_moments_attention_needs(self):
        for rtk in (True, False):
            settings = self.settings(rtk)
            for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
                          "Notification", "PostToolUse", "PostToolUseFailure", "Stop",
                          "StopFailure", "SubagentStop", "SessionEnd"):
                self.assertEqual(len(self.handlers(settings, event)), 1, (rtk, event))
            self.assertLessEqual(set(settings["hooks"]), KNOWN_EVENTS)
            self.assertIn("usage_statusline.py", settings["statusLine"]["command"])

    def test_the_rtk_hook_stays_and_runs_first(self):
        pre = self.settings(True)["hooks"]["PreToolUse"]
        self.assertEqual(pre[0]["matcher"], "Bash")
        self.assertIn("hook claude", pre[0]["hooks"][0]["command"])
        self.assertEqual(pre[1]["matcher"], "AskUserQuestion|ExitPlanMode")
        self.assertNotIn("hook claude", json.dumps(self.settings(False)))

    def test_only_the_per_tool_hooks_run_in_the_background(self):
        settings = self.settings(False)
        background = {event for event in settings["hooks"]
                      for h in self.handlers(settings, event) if h.get("async")}
        self.assertEqual(background, {"PostToolUse", "PostToolUseFailure"})
        for event in settings["hooks"]:
            for h in self.handlers(settings, event):
                self.assertEqual(h["type"], "command")
                self.assertLessEqual(h["timeout"], 5)
                self.assertGreater(h["timeout"], agent_hook.TOTAL_TIMEOUT)

    def test_paths_with_spaces_are_quoted(self):
        script = Path(tempfile.mkdtemp()) / "Ensemble Dashboard" / "agent_hook.py"
        python = Path(tempfile.mkdtemp()) / "Program Files" / "python.exe"
        with mock.patch.object(dashboard, "AGENT_HOOK_SCRIPT", script), \
                mock.patch.object(dashboard.sys, "executable", str(python)):
            command = self.handlers(self.settings(False), "Stop")[0]["command"]
        self.assertEqual(shlex.split(command), [python.as_posix(), script.as_posix()])

    def test_who_is_speaking_travels_in_the_environment(self):
        self.assertEqual(dashboard._agent_hook_env(8765, "room-1", "claude-2"), {
            "ENSEMBLE_HOOK_URL": "http://127.0.0.1:8765/api/agent/hook",
            "ENSEMBLE_HOOK_ROOM": "room-1", "ENSEMBLE_HOOK_IDENTITY": "claude-2"})


class LaunchEnvironment(unittest.TestCase):
    """The launch and the resume give a Claude agent the hook environment, and
    codex none; the terminal adds its own id."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.made: list[dict] = []

        def create(cmd, cwd=None, env=None, label="", meta=None, **kw):
            self.made.append({"cmd": cmd, "env": dict(env or {}), "meta": meta})
            return types.SimpleNamespace(id="pty-new")
        for patch in (
                mock.patch.object(dashboard, "DASHBOARD_DIR", self.tmp),
                mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
                mock.patch.object(dashboard.agents, "get_agent", return_value=object()),
                mock.patch.object(dashboard.BACKEND, "headless_launch",
                                  side_effect=lambda cwd, argv, prompt: argv),
                mock.patch.object(dashboard.ptyrun, "create", side_effect=create)):
            patch.start()
            self.addCleanup(patch.stop)
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))

    def room(self, agent: str) -> tuple[dict, dict]:
        part = {"identity": "eng", "agent": agent, "kind": "agent", "sessionId": "s1",
                "cwd": str(self.tmp)}
        return {"id": "room-1", "title": "t", "cwd": str(self.tmp), "sharedCwd": True,
                "tokens": {"tok": "eng"}, "participants": [part]}, part

    def test_a_claude_agent_is_told_where_and_as_whom_to_report(self):
        room, part = self.room("claude")
        self.handler._launch_room_agent_pty(room, part, "do it", collab=False)
        self.handler._resume_room_agent_pty(room, part, collab=False)
        for made in self.made:
            self.assertEqual(made["env"]["ENSEMBLE_HOOK_URL"], "http://127.0.0.1:8791/api/agent/hook")
            self.assertEqual((made["env"]["ENSEMBLE_HOOK_ROOM"], made["env"]["ENSEMBLE_HOOK_IDENTITY"]),
                             ("room-1", "eng"))
        self.assertEqual(len(self.made), 2)

    def test_codex_gets_nothing(self):
        room, part = self.room("codex")
        self.handler._launch_room_agent_pty(room, part, "do it", collab=False)
        self.handler._resume_room_agent_pty(room, part, collab=False)
        for made in self.made:
            self.assertFalse([k for k in made["env"] if k.startswith("ENSEMBLE_HOOK")])

    def test_the_terminal_names_itself(self):
        spawned = {}
        fake = types.SimpleNamespace(PtyProcess=types.SimpleNamespace(
            spawn=lambda argv, cwd=None, env=None, dimensions=None: spawned.update(env=env)))
        sess = ptyrun.PtySession.__new__(ptyrun.PtySession)
        sess.id, sess.argv, sess.cwd, sess.rows, sess.cols = "pty-abc", ["x"], None, 40, 120
        with mock.patch.dict(sys.modules, {"winpty": fake, "ptyprocess": fake}), \
                mock.patch.object(ptyrun, "ensure_windows_console", lambda: None, create=True):
            sess._spawn({"A": "1"})
        self.assertEqual(spawned["env"]["ENSEMBLE_PTY_ID"], "pty-abc")
        self.assertEqual(spawned["env"]["A"], "1")


# ---------------------------------------------------------------------------
# The hook command
# ---------------------------------------------------------------------------

class _Recorder(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.got.append((self.path, dict(self.headers), body))
        self.send_response(self.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class HookCommand(unittest.TestCase):
    def serve(self, status=200) -> http.server.ThreadingHTTPServer:
        handler = type("H", (_Recorder,), {"status": status})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        srv.got = []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def run_hook(self, url: str | None, stdin: str, **env) -> tuple[subprocess.CompletedProcess, float]:
        full = {k: v for k, v in os.environ.items() if not k.startswith("ENSEMBLE_")}
        if url is not None:
            full.update(ENSEMBLE_HOOK_URL=url, ENSEMBLE_HOOK_ROOM="room-1",
                        ENSEMBLE_HOOK_IDENTITY="eng", ENSEMBLE_PTY_ID="pty-1")
        full.update(env)
        started = time.perf_counter()
        out = subprocess.run([sys.executable, str(REPO / "agent_hook.py")], input=stdin,
                             capture_output=True, text=True, encoding="utf-8", env=full,
                             timeout=30, creationflags=NO_WINDOW)
        return out, time.perf_counter() - started

    def assert_silent_success(self, out: subprocess.CompletedProcess) -> None:
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_it_posts_who_when_and_the_few_fields_the_hub_reads(self):
        srv = self.serve()
        before = time.time()
        out, _ = self.run_hook(
            f"http://127.0.0.1:{srv.server_address[1]}/api/agent/hook",
            json.dumps({"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Write",
                        "tool_input": {"content": "x" * 500000}, "tool_response": "y" * 500000,
                        "transcript_path": "C:/t.jsonl", "cwd": "C:/w"}))
        self.assert_silent_success(out)
        (path, headers, body), = srv.got
        sent = json.loads(body)
        self.assertEqual(path, "/api/agent/hook")
        self.assertNotIn("Origin", headers)
        self.assertEqual((sent["room"], sent["identity"], sent["ptyId"]), ("room-1", "eng", "pty-1"))
        self.assertEqual(sent["event"], {"hook_event_name": "PostToolUse", "session_id": "s1",
                                         "tool_name": "Write"})
        self.assertTrue(before - 1 <= sent["at"] <= time.time())
        self.assertLess(len(body), 1000)

    def test_a_stopped_hub_costs_at_most_the_connect_timeout(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        out, took = self.run_hook(f"http://127.0.0.1:{port}/api/agent/hook",
                                  json.dumps({"hook_event_name": "Stop"}))
        self.assert_silent_success(out)
        self.assertLess(took, agent_hook.CONNECT_TIMEOUT + 1.5)

    def test_a_hub_that_never_answers_is_given_up_on_in_time(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(4)
        self.addCleanup(s.close)
        out, took = self.run_hook(f"http://127.0.0.1:{s.getsockname()[1]}/api/agent/hook",
                                  json.dumps({"hook_event_name": "Stop"}))
        self.assert_silent_success(out)
        self.assertLess(took, agent_hook.TOTAL_TIMEOUT + 1.5)

    def test_a_failing_hub_is_not_the_agents_problem(self):
        srv = self.serve(status=500)
        out, _ = self.run_hook(f"http://127.0.0.1:{srv.server_address[1]}/api/agent/hook",
                               json.dumps({"hook_event_name": "Stop"}))
        self.assert_silent_success(out)
        self.assertEqual(len(srv.got), 1)               # one attempt, no retry

    def test_bad_stdin_is_dropped_silently(self):
        srv = self.serve()
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/agent/hook"
        for stdin in ("", "not json", "[1, 2]", "\x00\xff"):
            out, _ = self.run_hook(url, stdin)
            self.assert_silent_success(out)
        self.assertEqual(srv.got, [])

    def test_without_the_hubs_environment_it_does_nothing(self):
        out, took = self.run_hook(None, json.dumps({"hook_event_name": "Stop"}))
        self.assert_silent_success(out)
        self.assertLess(took, 1.5)

    def test_the_limits_are_the_ones_promised(self):
        self.assertLessEqual(agent_hook.CONNECT_TIMEOUT, 1.0)
        self.assertLessEqual(agent_hook.TOTAL_TIMEOUT, 2.0)


# ---------------------------------------------------------------------------
# What an event means, and what the hub keeps
# ---------------------------------------------------------------------------

def _event(name: str, **fields) -> dict:
    return {"hook_event_name": name, "session_id": "s1", **fields}


class EventStates(unittest.TestCase):
    def test_what_each_moment_speaks_of(self):
        for event, state in (
                (_event("UserPromptSubmit"), "working"),
                (_event("PostToolUse", tool_name="Bash"), "working"),
                (_event("PostToolUseFailure", tool_name="Bash"), "working"),
                (_event("PreToolUse", tool_name="Bash"), "working"),
                (_event("PreToolUse", tool_name="AskUserQuestion"), "waiting"),
                (_event("PreToolUse", tool_name="ExitPlanMode"), "waiting"),
                (_event("PermissionRequest", tool_name="Write"), "waiting"),
                (_event("Notification", notification_type="permission_prompt"), "waiting"),
                (_event("Notification", notification_type="elicitation_dialog"), "waiting"),
                (_event("Notification", message="Claude needs your permission to use Bash"), "waiting"),
                (_event("Notification", notification_type="idle_prompt"), "idle"),
                (_event("Notification", message="Claude is waiting for your input"), "idle"),
                (_event("Notification", notification_type="auth_success"), ""),
                (_event("Stop"), "idle"),
                (_event("StopFailure", error_type="rate_limit"), "idle"),
                (_event("SessionStart", source="startup"), "idle"),
                (_event("SessionStart", source="compact"), ""),
                (_event("SessionEnd", reason="other"), "ended"),
                (_event("SubagentStop", agent_id="sub-1"), "idle"),      # that subagent's turn, see HeldState
                (_event("SomethingNew"), "")):
            self.assertEqual(agent_hooks.state_of(event)[0], state, event)


class HeldState(unittest.TestCase):
    def setUp(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        self.clock = 1000.0

    def owner(self, pty_id):
        return {"pty-1": ("room-1", "eng")}.get(pty_id)

    def post(self, name, at=None, **fields):
        """One hook, a second after the last unless ``at`` says when."""
        over = {k: fields.pop(k) for k in ("room", "identity", "ptyId") if k in fields}
        self.clock += 1
        payload = {"room": "room-1", "identity": "eng", "ptyId": "pty-1",
                   "at": self.clock if at is None else at, "event": _event(name, **fields), **over}
        return agent_hooks.record(payload, self.owner, now=self.clock)

    def state(self) -> str:
        return (agent_hooks.state_for("pty-1") or {}).get("state", "")

    def test_the_state_is_kept_per_terminal_with_its_time(self):
        self.assertEqual(self.post("UserPromptSubmit", at=1000.5),
                         {"ok": True, "state": "working", "changed": True})
        held = agent_hooks.state_for("pty-1")
        self.assertEqual((held["state"], held["at"], held["room"], held["identity"], held["sessionId"]),
                         ("working", 1000.5, "room-1", "eng", "s1"))
        self.assertIsNone(agent_hooks.state_for("pty-2"))      # a relaunch starts clean
        self.assertIsNone(agent_hooks.state_for(""))

    def test_a_turn_from_prompt_to_permission_to_the_end(self):
        seen = []
        for name, fields in (("SessionStart", {"source": "startup"}), ("UserPromptSubmit", {}),
                             ("PermissionRequest", {"tool_name": "Write"}),
                             ("Notification", {"notification_type": "permission_prompt"}),
                             ("PostToolUse", {"tool_name": "Write", "tool_use_id": "toolu_1"}),
                             ("Stop", {}), ("Notification", {"notification_type": "idle_prompt"})):
            self.post(name, **fields)
            seen.append(self.state())
        self.assertEqual(seen, ["idle", "working", "waiting", "waiting", "working", "idle", "idle"])

    def test_an_event_from_before_the_one_held_changes_nothing(self):
        self.post("Stop", at=999.0)
        res = self.post("PostToolUse", at=998.0, tool_name="Bash")      # a background hook, late
        self.assertEqual((res["changed"], self.state()), (False, "idle"))

    def test_news_and_unknown_events_change_nothing(self):
        self.post("PermissionRequest", tool_name="Write")
        for name, fields in (("Notification", {"notification_type": "auth_success"}), ("Brand New", {})):
            self.assertEqual(self.post(name, **fields), {"ok": True, "state": "waiting", "changed": False})

    # --- an ask is over when ITS answer comes, not when anything happens ---

    def test_a_subagents_tool_call_does_not_answer_the_agents_question(self):
        self.post("UserPromptSubmit")
        self.post("PreToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.post("PostToolUse", tool_name="Read", tool_use_id="toolu_r", agent_id="sub-1")
        self.post("PostToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_other", agent_id="sub-1")
        self.assertEqual(self.state(), "waiting")
        self.post("PostToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.assertEqual(self.state(), "working")

    def test_a_call_made_side_by_side_does_not_answer_a_permission_prompt(self):
        self.post("UserPromptSubmit")
        self.post("PermissionRequest", tool_name="Write")                 # names no call id
        self.post("PostToolUse", tool_name="Read", tool_use_id="toolu_r")
        self.assertEqual(self.state(), "waiting")
        self.post("PostToolUse", tool_name="Write", tool_use_id="toolu_w")
        self.assertEqual(self.state(), "working")

    def test_the_question_and_its_permission_request_are_one_ask(self):
        self.post("PreToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.post("PermissionRequest", tool_name="AskUserQuestion")
        self.assertEqual(agent_hooks.state_for("pty-1")["waits"], 2)
        self.post("PostToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.assertEqual(self.state(), "working")

    def test_a_subagents_ask_outlives_the_agents_own_tool_calls_and_turn(self):
        self.post("UserPromptSubmit")
        self.post("PermissionRequest", tool_name="Bash", agent_id="sub-1")
        self.post("PostToolUse", tool_name="Bash", tool_use_id="toolu_b")      # the agent's own Bash
        self.assertEqual(self.state(), "waiting")
        self.post("Stop")
        self.post("Notification", notification_type="idle_prompt")
        self.assertEqual(self.state(), "waiting")
        self.post("PostToolUse", tool_name="Bash", tool_use_id="toolu_s", agent_id="sub-1")
        self.assertEqual(self.state(), "idle")           # and its tool calls are not the agent's word

    def test_the_end_of_its_turn_or_a_new_prompt_ends_the_agents_own_ask(self):
        for ender in ("Stop", "UserPromptSubmit", "SessionEnd"):
            agent_hooks.reset()
            self.post("PermissionRequest", tool_name="Write")
            self.post(ender)
            self.assertNotEqual(self.state(), "waiting", ender)

    def test_still_at_the_prompt_does_not_end_a_question(self):
        self.post("PreToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.post("Notification", notification_type="idle_prompt")
        self.assertEqual(self.state(), "waiting")

    def test_a_notification_alone_is_an_ask_any_returning_call_ends(self):
        self.post("UserPromptSubmit")
        self.post("Notification", message="Claude needs your permission to use Bash")
        self.assertEqual(self.state(), "waiting")
        self.post("PostToolUse", tool_name="Bash", tool_use_id="toolu_b")
        self.assertEqual(self.state(), "working")

    def test_another_kind_of_notice_is_an_ask_of_its_own(self):
        self.post("UserPromptSubmit")
        self.post("PermissionRequest", tool_name="Write")
        self.post("Notification", notification_type="permission_prompt")          # the echo
        self.assertEqual(agent_hooks.state_for("pty-1")["waits"], 1)
        self.post("Notification", notification_type="elicitation_dialog", agent_id="sub-1")
        self.post("PostToolUse", tool_name="Write", tool_use_id="toolu_w")        # the first is answered
        self.assertEqual(self.state(), "waiting")
        self.post("PostToolUse", tool_name="mcp__x__y", tool_use_id="toolu_m", agent_id="sub-1")
        self.assertEqual(self.state(), "working")

    def test_a_subagents_end_ends_what_it_asked_and_nothing_else(self):
        self.post("UserPromptSubmit")
        self.post("PreToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.post("PermissionRequest", tool_name="Bash", agent_id="sub-1")
        self.post("PermissionRequest", tool_name="Bash", agent_id="sub-2")
        self.post("SubagentStop", agent_id="sub-1")             # denied: its Bash never came back
        self.post("SubagentStop")                               # which one? says nothing
        self.assertEqual(agent_hooks.state_for("pty-1")["waits"], 2)
        self.post("PostToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q")
        self.post("Stop")
        self.assertEqual(self.state(), "waiting")               # sub-2 still asks
        self.post("SubagentStop", agent_id="sub-2")
        held = agent_hooks.state_for("pty-1")
        self.assertEqual((held["state"], held["event"]), ("idle", "Stop"))     # not the subagent's word

    # --- what attention saw contradicted stays dropped ---

    def test_a_contradicted_state_stays_dropped_until_the_next_hook(self):
        self.post("UserPromptSubmit")
        self.post("PermissionRequest", tool_name="Write")
        at = agent_hooks.state_for("pty-1")["at"]
        agent_hooks.invalidate("pty-1", at)
        self.assertIsNone(agent_hooks.state_for("pty-1"))      # not the older "working" either
        self.assertNotIn("pty-1", agent_hooks.snapshot()["states"])
        self.post("Stop")
        self.assertEqual(self.state(), "idle")
        agent_hooks.invalidate("pty-9", at)                     # unknown: nothing to do

    def test_only_a_terminal_the_hub_owns_as_that_agent(self):
        for over in ({"ptyId": "pty-9"}, {"room": "room-2"}, {"identity": "other"}):
            self.assertEqual(self.post("Stop", **over), {"ok": False, "error": "unknown_agent"})
        self.assertEqual(agent_hooks.snapshot()["states"], {})

    def test_malformed_posts_are_refused_not_raised(self):
        for payload in (None, [], "x", {}, {"event": "Stop"}, {"event": {}},
                        {"room": "room-1", "identity": "eng", "ptyId": "pty-1", "event": {"hook_event_name": 7}},
                        {"room": ["room-1"], "identity": "eng", "ptyId": "pty-1", "event": _event("Stop")},
                        {"room": "room-1", "identity": "eng", "event": _event("Stop")}):
            self.assertEqual(agent_hooks.record(payload, self.owner), {"ok": False, "error": "bad_payload"})

    def test_fields_of_the_wrong_type_are_not_there(self):
        wrong = {"hook_event_name": "Notification", "message": ["wrong type"], "notification_type": 7,
                 "tool_name": {"a": 1}, "agent_id": 3, "session_id": None, 5: "x"}
        res = agent_hooks.record({"room": "room-1", "identity": "eng", "ptyId": "pty-1",
                                  "at": [1], "event": wrong}, self.owner)
        self.assertEqual(res, {"ok": True, "state": "", "changed": False})
        for name in ("StopFailure", "PreToolUse", "PermissionRequest", "SessionStart", "SessionEnd"):
            event = {"hook_event_name": name, "error_type": 5, "error": [], "tool_name": 9,
                     "tool_use_id": {}, "source": 1, "reason": 2}
            self.assertTrue(agent_hooks.record({"room": "room-1", "identity": "eng", "ptyId": "pty-1",
                                                "event": event}, self.owner)["ok"], name)

    def test_a_clock_that_cannot_be_the_hooks_is_replaced(self):
        for at in (5000.0, 10.0, "soon", None, True):
            agent_hooks.reset()
            self.clock = 1000.0
            self.post("Stop", at=at) if at is not None else agent_hooks.record(
                {"room": "room-1", "identity": "eng", "ptyId": "pty-1", "event": _event("Stop")},
                self.owner, now=1001.0)
            self.assertEqual(agent_hooks.state_for("pty-1")["at"], 1001.0, at)


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

class Endpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet = mock.patch.object(dashboard.Handler, "log_message", lambda *a: None)
        cls.quiet.start()
        cls.server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.quiet.stop()

    def setUp(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        owned = types.SimpleNamespace(meta={"room": "room-1", "identity": "eng", "agent": "claude"})
        patch = mock.patch.object(dashboard.ptyrun, "get",
                                  side_effect=lambda pid: owned if pid == "pty-1" else None)
        patch.start()
        self.addCleanup(patch.stop)

    def send(self, body, method="POST", path="/api/agent/hook", **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        conn.request(method, path, body=raw if method == "POST" else None,
                     headers={"Content-Type": "application/json", **headers})
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"{}")

    def payload(self, event="Stop", **over):
        return {"room": "room-1", "identity": "eng", "ptyId": "pty-1", "at": time.time(),
                "event": _event(event) if isinstance(event, str) else event, **over}

    def test_an_agents_hook_is_kept(self):
        gen = dashboard._SESS_GEN
        self.assertEqual(self.send(self.payload("UserPromptSubmit")),
                         (200, {"ok": True, "state": "working", "changed": True}))
        self.assertEqual(agent_hooks.state_for("pty-1")["state"], "working")
        self.assertEqual(dashboard._SESS_GEN, gen)      # no session listing is rebuilt for it

    def test_the_real_command_reaches_it(self):
        env = {**os.environ, **dashboard._agent_hook_env(self.port, "room-1", "eng"),
               "ENSEMBLE_PTY_ID": "pty-1"}
        out = subprocess.run([sys.executable, str(REPO / "agent_hook.py")],
                             input=json.dumps(_event("PermissionRequest", tool_name="Write")),
                             capture_output=True, text=True, encoding="utf-8", env=env,
                             timeout=30, creationflags=NO_WINDOW)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        held = agent_hooks.state_for("pty-1")
        self.assertEqual((held["state"], held["detail"]), ("waiting", "Write"))

    def test_unknown_events_and_agents_are_tolerated(self):
        self.assertEqual(self.send(self.payload("NeverHeardOfIt"))[0], 200)
        self.assertEqual(self.send(self.payload(ptyId="pty-9")), (404, {"ok": False, "error": "unknown_agent"}))
        self.assertEqual(agent_hooks.snapshot()["states"], {})

    def test_malformed_bodies_are_refused(self):
        for body in (b"", b"{not json", b"\xff\xfe", b"[1]", b'"x"', b'{"event": 3}'):
            status, res = self.send(body)
            self.assertEqual(status, 400, body)
        self.assertEqual(self.send(b"x" * 20000)[0], 413)
        # Well-formed JSON whose event fields are not text: answered, not a
        # dropped connection and a traceback.
        for event in ({"hook_event_name": "Notification", "message": ["wrong type"]},
                      {"hook_event_name": "StopFailure", "error_type": {"a": 1}},
                      {"hook_event_name": "PermissionRequest", "tool_name": 7, "agent_id": []}):
            self.assertEqual(self.send(self.payload(event=event))[0], 200, event)
        with mock.patch.object(agent_hooks, "record", side_effect=RuntimeError("boom")):
            self.assertEqual(self.send(self.payload()), (400, {"ok": False, "error": "bad_payload"}))
        self.assertEqual(self.send(self.payload())[0], 200)       # and the hub is still fine

    def test_a_page_on_another_site_is_refused(self):
        self.assertEqual(self.send(self.payload(), Origin="http://evil.example"),
                         (403, {"error": "cross_origin"}))
        self.assertEqual(self.send(self.payload(), Origin=f"http://127.0.0.1:{self.port + 1}")[0], 403)
        self.assertIsNone(agent_hooks.state_for("pty-1"))
        self.assertEqual(self.send(self.payload(), Origin=f"http://127.0.0.1:{self.port}")[0], 200)

    def test_another_machine_is_refused(self):
        with mock.patch.object(dashboard.Handler, "_client_ip", lambda h: "100.64.0.7"):
            self.assertEqual(self.send(self.payload()), (403, {"error": "loopback_only"}))
            self.assertEqual(self.send(None, method="GET", path="/api/agent/hooks")[0], 403)
        self.assertIsNone(agent_hooks.state_for("pty-1"))

    def test_what_is_held_can_be_read_on_this_machine(self):
        self.send(self.payload("Stop"))
        status, snap = self.send(None, method="GET", path="/api/agent/hooks")
        self.assertEqual((status, snap["states"]["pty-1"]["state"]), (200, "idle"))
        self.assertEqual(snap["recent"][-1]["event"], "Stop")


# ---------------------------------------------------------------------------
# attention: the agent's word first, the screen as the fallback
# ---------------------------------------------------------------------------

WORKING = "● Reading the spec\n\n✻ Pondering… (12s · esc to interrupt)\n"
ASKING_TEXT = "● Done. Would you like to merge it now?\n\n❯ \n"
PERMISSION = ("● Write(a.txt)\n\n Do you want to create a.txt?\n ❯ 1. Yes\n   2. Yes, and don't ask again\n"
              "   3. No, and tell Claude what to do differently\n")
WALL = "● Working on it\n  ⎿  API Error: 401 · OAuth token has expired · Please run /login\n\n❯ \n"
HOOK_AT = 1000.0


def _hook(state: str, at: float = HOOK_AT) -> dict:
    return {"state": state, "at": at, "event": "x", "detail": "", "sessionId": "s1", "waits": 0}


def _ev(tail: str, hook: dict | None = None, status: str = "", status_at: float = 0.0,
        idle: float | None = 2, printed: float = HOOK_AT) -> dict:
    return {"ptyId": "p1", "alive": True, "tail": tail, "idleSeconds": idle, "lastSubmit": 0.0,
            "death": None, "scan": attention.analyse(tail), "claudeStatus": status,
            "claudeStatusAt": status_at, "hook": hook, "lastOutput": printed}


def _classify(ev: dict, agent: str = "claude", room: dict | None = None):
    part = {"identity": agent, "agent": agent}
    room = {"status": "active", "mode": "", "participants": [part], **(room or {})}
    return attention._classify_agent(room, part, ev, 900, HOOK_AT + 30)


class AttentionPrefersTheHook(unittest.TestCase):
    def test_waiting_needs_no_match_on_the_screen(self):
        hit = _classify(_ev(WORKING, _hook("waiting")))
        self.assertEqual(hit[:2], ("waiting_for_you", "claude is waiting on your answer to a prompt"))

    def test_working_is_not_waiting_whatever_words_are_on_screen(self):
        self.assertIsNone(_classify(_ev(ASKING_TEXT, _hook("working"))))
        self.assertEqual(_classify(_ev(ASKING_TEXT))[0], "waiting_for_you")      # the screen alone

    def test_finished_with_a_question_in_its_last_words_is_not_a_prompt(self):
        self.assertIsNone(_classify(_ev(ASKING_TEXT, _hook("idle"))))

    def test_a_working_agent_is_not_blocked_by_its_earlier_report(self):
        room = {"openToHuman": {"from": "claude", "kind": "blocked", "text": "need a key", "ts": 5.0}}
        self.assertEqual(_classify(_ev("● ok\n❯ \n"), room=room)[0], "blocked")
        self.assertIsNone(_classify(_ev("● ok\n❯ \n", _hook("working")), room=room))

    def test_walls_stay_with_the_screen(self):
        self.assertEqual(_classify(_ev(WALL, _hook("idle")))[0], "blocked")
        self.assertEqual(_classify(_ev(WALL, _hook("waiting")))[0], "blocked")
        # Working, by its own word, is no wall yet: a real refusal ends the turn.
        self.assertIsNone(_classify(_ev(WALL, _hook("working"))))
        self.assertEqual(_classify(_ev(WALL, _hook("working"), idle=90))[0], "blocked")

    def test_the_same_states_and_words_as_the_status_file(self):
        by_hook = _classify(_ev(PERMISSION, _hook("waiting")))
        by_file = _classify(_ev(PERMISSION, status="waiting"))
        self.assertEqual(by_hook, by_file)


class Staleness(unittest.TestCase):
    def test_a_later_status_file_that_says_otherwise_wins(self):
        # Approved: the file flips to busy at once, the tool's hook comes later.
        ev = _ev(WORKING, _hook("waiting"), status="busy", status_at=HOOK_AT + 0.04)
        self.assertEqual(attention._hook_status(ev), "")
        self.assertIsNone(_classify(ev))
        # A prompt queued during a turn: its turn starts 40 ms after the Stop.
        ev = _ev(ASKING_TEXT, _hook("idle"), status="busy", status_at=HOOK_AT + 0.04)
        self.assertIsNone(_classify(ev))
        # Interrupted with Esc: no Stop fires, the file says idle.
        ev = _ev(WALL, _hook("working"), status="idle", status_at=HOOK_AT + 3)
        self.assertEqual(_classify(ev)[0], "blocked")

    def test_an_earlier_or_agreeing_status_file_changes_nothing(self):
        # AskUserQuestion: the file never says waiting, and says busy since before.
        ev = _ev(WORKING, _hook("waiting"), status="busy", status_at=HOOK_AT - 20)
        self.assertEqual(attention._hook_status(ev), "waiting")
        ev = _ev(ASKING_TEXT, _hook("idle"), status="idle", status_at=HOOK_AT + 0.1)
        self.assertEqual(attention._hook_status(ev), "idle")
        self.assertIsNone(_classify(ev))

    def test_working_on_a_silent_terminal_is_left_behind(self):
        self.assertEqual(attention._hook_status(_ev(WORKING, _hook("working"), idle=59)), "busy")
        ev = _ev(PERMISSION, _hook("working"), idle=60)      # the prompt's hook was lost
        self.assertEqual(attention._hook_status(ev), "")
        self.assertEqual(_classify(ev)[0], "waiting_for_you")

    def test_waiting_or_idle_yields_to_a_screen_that_went_back_to_work(self):
        for state in ("waiting", "idle"):
            moved = _ev(WORKING, _hook(state), printed=HOOK_AT + 6)
            self.assertEqual(attention._hook_status(moved), "", state)
            self.assertIsNone(_classify(moved))
            # Not yet, not while a prompt is up, not once it has gone quiet.
            self.assertEqual(attention._hook_status(_ev(WORKING, _hook(state), printed=HOOK_AT + 4)),
                             attention._HOOK_STATUS[state])
            self.assertTrue(attention._hook_status(_ev(PERMISSION, _hook(state), printed=HOOK_AT + 60)))
            self.assertTrue(attention._hook_status(_ev(WORKING, _hook(state), printed=HOOK_AT + 6, idle=70)))

    def test_what_the_screen_contradicted_does_not_come_back_when_it_goes_quiet(self):
        # A question's hook, then the hooks of its answer and of the turn's end
        # are lost and there is no status file: the screen shows later work,
        # then silence. The old question must not return.
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        agent_hooks.record({"room": "room-1", "identity": "claude", "ptyId": "p1", "at": HOOK_AT,
                            "event": _event("PreToolUse", tool_name="AskUserQuestion")},
                           lambda pid: ("room-1", "claude"), now=HOOK_AT)

        def poll(tail, idle, printed):
            return _classify(_ev(tail, agent_hooks.state_for("p1"), idle=idle, printed=printed))
        self.assertEqual(poll(PERMISSION, 2, HOOK_AT)[0], "waiting_for_you")
        self.assertIsNone(poll(WORKING, 2, HOOK_AT + 10))
        self.assertIsNone(agent_hooks.state_for("p1"))
        self.assertIsNone(poll(WORKING, 70, HOOK_AT + 10))           # later: silence
        # The next hook is believed again.
        agent_hooks.record({"room": "room-1", "identity": "claude", "ptyId": "p1", "at": HOOK_AT + 100,
                            "event": _event("PermissionRequest", tool_name="Write")},
                           lambda pid: ("room-1", "claude"), now=HOOK_AT + 100)
        self.assertEqual(poll(WORKING, 70, HOOK_AT + 10)[0], "waiting_for_you")

    def _hooks(self, *events):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        for at, name, fields in events:
            agent_hooks.record({"room": "room-1", "identity": "claude", "ptyId": "p1", "at": at,
                                "event": _event(name, **fields)},
                               lambda pid: ("room-1", "claude"), now=at)

    def test_the_agents_turn_ending_does_not_answer_its_subagents_question(self):
        self._hooks((HOOK_AT, "UserPromptSubmit", {}),
                    (HOOK_AT + 1, "PermissionRequest", {"tool_name": "Bash", "agent_id": "sub-1"}),
                    (HOOK_AT + 2, "Stop", {}))
        for _ in range(2):          # the file says idle since the Stop; poll after poll
            ev = _ev(PERMISSION, agent_hooks.state_for("p1"), status="idle", status_at=HOOK_AT + 2.1, idle=70)
            self.assertEqual(attention._hook_status(ev), "waiting")
            self.assertEqual(_classify(ev)[0], "waiting_for_you")
        # The agent's own ask does end with its turn, the subagent's beside it stays.
        self._hooks((HOOK_AT, "PermissionRequest", {"tool_name": "Bash", "agent_id": "sub-1"}),
                    (HOOK_AT + 1, "PermissionRequest", {"tool_name": "Write"}))
        ev = _ev(PERMISSION, agent_hooks.state_for("p1"), status="idle", status_at=HOOK_AT + 3)
        self.assertEqual(attention._hook_status(ev), "waiting")
        self.assertEqual(agent_hooks.state_for("p1")["waits"], 1)
        # Approved: the file says busy, and that is about the prompt on screen, whoever asked.
        ev = _ev(WORKING, agent_hooks.state_for("p1"), status="busy", status_at=HOOK_AT + 4)
        self.assertEqual(attention._hook_status(ev), "")
        self.assertIsNone(agent_hooks.state_for("p1"))

    def test_the_screen_still_ends_a_subagents_question_after_the_agents_turn(self):
        # The subagent's answer and its end were lost; the agent's file says
        # idle since its Stop. The screen going back to work ends the ask, for good.
        self._hooks((HOOK_AT, "UserPromptSubmit", {}),
                    (HOOK_AT + 1, "PermissionRequest", {"tool_name": "Bash", "agent_id": "sub-1"}),
                    (HOOK_AT + 2, "Stop", {}))

        def poll(tail, idle, printed):
            return _classify(_ev(tail, agent_hooks.state_for("p1"), status="idle",
                                 status_at=HOOK_AT + 2.1, idle=idle, printed=printed))
        self.assertEqual(poll(PERMISSION, 2, HOOK_AT + 1)[0], "waiting_for_you")
        self.assertEqual(poll(PERMISSION, 2, HOOK_AT + 10)[0], "waiting_for_you")    # prompt still up
        self.assertIsNone(poll(WORKING, 2, HOOK_AT + 10))
        self.assertEqual(agent_hooks.state_for("p1")["state"], "idle")               # the agent's Stop stands
        self.assertIsNone(poll(WORKING, 3600, HOOK_AT + 10))                         # an hour of silence

    def test_a_finished_subagent_leaves_no_question_behind_on_a_quiet_terminal(self):
        # The agent stopped, its subagent asked, was refused and finished: no
        # tool call came back, the file says idle since before, nothing prints.
        self._hooks((HOOK_AT, "Stop", {}),
                    (HOOK_AT + 1, "PermissionRequest", {"tool_name": "Bash", "agent_id": "sub-1"}))
        asked = _ev(PERMISSION, agent_hooks.state_for("p1"), status="idle", status_at=HOOK_AT + 0.1)
        self.assertEqual(_classify(asked)[0], "waiting_for_you")
        self._hooks((HOOK_AT, "Stop", {}),
                    (HOOK_AT + 1, "PermissionRequest", {"tool_name": "Bash", "agent_id": "sub-1"}),
                    (HOOK_AT + 2, "SubagentStop", {"agent_id": "sub-1"}),
                    (HOOK_AT + 60, "Notification", {"notification_type": "idle_prompt"}))
        quiet = _ev(ASKING_TEXT, agent_hooks.state_for("p1"), status="idle", status_at=HOOK_AT + 0.1,
                    idle=3600)
        self.assertEqual(attention._hook_status(quiet), "idle")
        self.assertIsNone(_classify(quiet))

    def test_working_left_behind_stays_dropped_when_the_terminal_prints_again(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        agent_hooks.record({"room": "room-1", "identity": "claude", "ptyId": "p1", "at": HOOK_AT,
                            "event": _event("UserPromptSubmit")},
                           lambda pid: ("room-1", "claude"), now=HOOK_AT)
        quiet = _ev(PERMISSION, agent_hooks.state_for("p1"), idle=61)
        self.assertEqual(_classify(quiet)[0], "waiting_for_you")
        repainted = _ev(PERMISSION, agent_hooks.state_for("p1"), idle=1)     # a resize redraws it
        self.assertIsNone(repainted["hook"])
        self.assertEqual(_classify(repainted)[0], "waiting_for_you")

    def test_an_old_state_is_still_true(self):
        ev = _ev(ASKING_TEXT, _hook("idle", at=HOOK_AT - 3 * 86400), idle=3 * 86400)
        self.assertEqual(attention._hook_status(ev), "idle")

    def test_an_ended_session_says_nothing(self):
        ev = _ev(PERMISSION, _hook("ended"))
        self.assertEqual(attention._hook_status(ev), "")
        self.assertEqual(_classify(ev)[0], "waiting_for_you")


class Fallback(unittest.TestCase):
    """Codex, and a Claude agent that has said nothing, are read as before."""

    def setUp(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        self.sess = types.SimpleNamespace(
            id="pty-1", last_output=HOOK_AT, alive=lambda: True, tail=lambda: PERMISSION,
            info=lambda: {"idleSeconds": 2}, last_submit=lambda: 0.0, death=lambda: None,
            meta={"room": "room-1", "identity": "eng"})
        patch = mock.patch.object(dashboard.ptyrun, "get",
                                  side_effect=lambda pid: self.sess if pid == "pty-1" else None)
        patch.start()
        self.addCleanup(patch.stop)
        with attention._ANALYSIS_LOCK:
            attention._ANALYSIS.clear()
        agent_hooks.record({"room": "room-1", "identity": "eng", "ptyId": "pty-1",
                            "event": _event("UserPromptSubmit")},
                           lambda pid: ("room-1", "eng"))

    def test_codex_never_reads_a_hook(self):
        ev = attention._evidence({"identity": "eng", "agent": "codex", "ptyId": "pty-1"}, {})
        self.assertIsNone(ev["hook"])
        self.assertEqual(_classify(ev, agent="codex")[0], "waiting_for_you")

    def test_claude_reads_its_own_terminals_hook_only(self):
        ev = attention._evidence({"identity": "eng", "agent": "claude", "ptyId": "pty-1"}, {})
        self.assertEqual(ev["hook"]["state"], "working")
        self.sess.id = "pty-2"                       # relaunched: a new terminal
        with mock.patch.object(dashboard.ptyrun, "get", side_effect=lambda pid: self.sess):
            ev = attention._evidence({"identity": "eng", "agent": "claude", "ptyId": "pty-2"}, {})
        self.assertIsNone(ev["hook"])

    def test_a_claude_agent_without_hooks_is_untouched(self):
        agent_hooks.reset()
        part = {"identity": "eng", "agent": "claude", "ptyId": "pty-1", "sessionId": "s1"}
        ev = attention._evidence(part, {"s1": ("waiting", 5.0)})
        self.assertEqual((ev["hook"], ev["claudeStatus"], ev["claudeStatusAt"]), (None, "waiting", 5.0))
        self.assertEqual(_classify(ev)[0], "waiting_for_you")
        ev = attention._evidence(part, {"s1": "busy"})       # the older shape of the map
        self.assertEqual(ev["claudeStatus"], "busy")

    def test_the_status_file_is_found_by_the_hooks_session_too(self):
        part = {"identity": "eng", "agent": "claude", "ptyId": "pty-1", "sessionId": "gone"}
        ev = attention._evidence(part, {"s1": ("idle", HOOK_AT)})
        self.assertEqual(ev["claudeStatus"], "idle")


class DigestReadsTheSameResult(unittest.TestCase):
    """The digest's "attention" fact is attention's item for the room, so a
    working task that once reported itself blocked is not called blocked."""

    def setUp(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)
        room = chatroom.create_room("Motor spec", [{"identity": "claude", "agent": "claude",
                                                    "role": "engineer"}])
        self.rid = room["id"]
        full = chatroom.get_room(self.rid, public=False)
        next(p for p in full["participants"] if p["identity"] == "claude")["ptyId"] = "pty-1"
        chatroom.update_room(full)
        chatroom.record_report(self.rid, "claude", "blocked", "I need the API key")
        sess = types.SimpleNamespace(
            id="pty-1", last_output=time.time(), alive=lambda: True, tail=lambda: "● ok\n❯ \n",
            info=lambda: {"idleSeconds": 2}, last_submit=lambda: 0.0, death=lambda: None,
            meta={"room": self.rid, "identity": "claude"})
        for patch in (
                mock.patch.object(dashboard.ptyrun, "get",
                                  side_effect=lambda pid: sess if pid == "pty-1" else None),
                mock.patch.object(dashboard, "_read_session_files", return_value=[]),
                mock.patch.object(dashboard, "load_session_projects", return_value={}),
                mock.patch.object(dashboard, "load_projects", return_value=[]),
                mock.patch.object(dashboard, "load_labels", return_value={})):
            patch.start()
            self.addCleanup(patch.stop)
        for cache in (attention._SUMMARY_CACHE, attention._ANALYSIS, attention._FIRST_SEEN):
            cache.clear()
            self.addCleanup(cache.clear)

    def attention_fact(self) -> str:
        room = chatroom.get_room(self.rid, public=False)
        with mock.patch.object(digest, "_git_facts", return_value={}):
            return digest._task_facts(room, attention.by_room(max_age=-1), {}, time.time())["attention"]

    def test_blocked_until_it_says_it_is_working_again(self):
        self.assertEqual(self.attention_fact(), "blocked")
        agent_hooks.record({"room": self.rid, "identity": "claude", "ptyId": "pty-1",
                            "event": _event("UserPromptSubmit")}, lambda pid: (self.rid, "claude"))
        self.assertEqual(self.attention_fact(), "")
        agent_hooks.record({"room": self.rid, "identity": "claude", "ptyId": "pty-1",
                            "event": _event("PermissionRequest", tool_name="Bash")},
                           lambda pid: (self.rid, "claude"))
        self.assertEqual(self.attention_fact(), "waiting_for_you")


if __name__ == "__main__":
    unittest.main()
