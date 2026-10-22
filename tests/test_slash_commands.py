"""Slash commands from the chat (#253).

The hub side: ``/api/room/command`` types the person's command raw into the
agent's terminal (no point, no hub wrapper, no message refs, no queue behind
hub text) and keeps a room record of it; ``/api/room/command-output`` adds
what it printed, once; neither is ever news for an agent (chat_read). The
catalogue lists built-ins, skills and custom commands. ``/clear`` and
``/new`` move the seat to the new conversation and keep the old one.

The page side: the screen reading (session.html cmdScreen / cmdOutput) on
the screens measured on Claude Code 2.1.308 and Codex 0.163.0
(tests/fixtures/slash_screens_*.txt), run in Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import slash

ROOT = Path(__file__).resolve().parent.parent
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
FIX = ROOT / "tests" / "fixtures"
NODE = shutil.which("node")


class FakePty:
    def __init__(self, pty_id, room, identity, agent="claude"):
        self.id = pty_id
        self.meta = {"room": room, "identity": identity, "agent": agent}
        self._alive = True
        self.writes: list[str] = []
        self.last_input = 0.0

    def alive(self):
        return self._alive

    def write(self, data):
        self.writes.append(data)
        return 0                # pywinpty's count: always 0 on Windows

    def tail(self, *a, **k):
        return ""


class Hub(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.ptys: dict[str, FakePty] = {}
        self.patches = [
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: self.ptys.get(pid)),
            mock.patch.object(dashboard.time, "sleep", lambda s: None),
            mock.patch.object(dashboard, "checkpoint_wait_restore", lambda rid: None, create=True),
        ]
        for p in self.patches:
            p.start()
        dashboard._COMMANDS.clear()
        dashboard._MENUS.clear()
        dashboard._MENU_HELD.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        dashboard._COMMANDS.clear()
        dashboard._MENUS.clear()
        dashboard._MENU_HELD.clear()
        chatroom.ROOMS_DIR = self.old_dir
        self.temp.cleanup()

    def room(self, agents=(("claude", "claude"),), mode="solo"):
        parts = [{"identity": ident, "agent": kind, "model": "", "role": "engineer" if i == 0 else "reviewer"}
                 for i, (ident, kind) in enumerate(agents)]
        r = chatroom.create_room("t", parts)
        full = chatroom.get_room(r["id"], public=False)
        full["mode"] = mode
        for p in full["participants"]:
            if p.get("kind") == "agent":
                pid = "pty-" + p["identity"]
                p["ptyId"], p["sessionId"] = pid, "sid-" + p["identity"]
                self.ptys[pid] = FakePty(pid, full["id"], p["identity"], p["agent"])
        chatroom.update_room(full)
        return full["id"]

    # ---- the raw send path ----
    def test_a_command_is_typed_raw_with_no_point_no_wrapper_and_no_queue(self):
        rid = self.room()
        with mock.patch.object(dashboard.points, "take", side_effect=AssertionError("no point")), \
             mock.patch.object(dashboard, "with_message_refs", side_effect=AssertionError("no refs")):
            code, reply = dashboard.send_command(rid, "  /mcp  ", key="k1")
        self.assertEqual(code, 200, reply)
        self.assertEqual(self.ptys["pty-claude"].writes, ["/mcp", "\r"])
        self.assertEqual(reply["identity"], "claude")
        room = chatroom.get_room(rid, public=False)
        rec = room["messages"][-1]
        self.assertEqual((rec["kind"], rec["noticeKind"], rec["from"], rec["to"], rec["text"]),
                         ("notice", "command", "user", "claude", "/mcp"))
        self.assertIsNone(dashboard.attention.open_ask(room), "a command asks nothing")
        self.assertEqual(rec["rang"], [], "a command wakes nobody")

    def test_a_busy_agent_gets_the_command_at_once(self):
        rid = self.room()
        self.ptys["pty-claude"].last_output = time.time()      # mid-turn
        code, _ = dashboard.send_command(rid, "/cost", key="k1")
        self.assertEqual(code, 200)
        self.assertEqual(self.ptys["pty-claude"].writes, ["/cost", "\r"])

    def test_the_same_key_twice_types_once(self):
        rid = self.room()
        dashboard.send_command(rid, "/mcp", key="k1")
        code, reply = dashboard.send_command(rid, "/mcp", key="k1")
        self.assertEqual(code, 200)
        self.assertTrue(reply.get("duplicate"))
        self.assertEqual(self.ptys["pty-claude"].writes, ["/mcp", "\r"])

    def test_not_a_command_and_a_stopped_agent_are_refused(self):
        rid = self.room()
        self.assertEqual(dashboard.send_command(rid, "hello", key="k")[0], 400)
        self.assertEqual(dashboard.send_command(rid, "/a\n/b", key="k")[0], 400)
        self.assertEqual(dashboard.send_command(rid, "/usr/bin", key="k")[0], 400)
        self.ptys["pty-claude"]._alive = False
        code, reply = dashboard.send_command(rid, "/mcp", key="k2")
        self.assertEqual((code, reply["error"]), (409, "not_running"))
        self.assertIn("not running", reply["message"])
        self.assertEqual(dashboard.send_command("room-nope", "/mcp")[0], 404)

    def test_a_team_command_goes_to_the_owner_or_the_named_agent(self):
        rid = self.room((("claude", "claude"), ("codex", "codex")), mode="collab")
        room = chatroom.get_room(rid, public=False)
        room["participants"][-1]["onMention"] = True
        chatroom.update_room(room)
        self.assertEqual(dashboard.send_command(rid, "/status", key="a")[1]["identity"], "claude")
        self.assertEqual(dashboard.send_command(rid, "/status", identity="codex", key="b")[1]["identity"], "codex")
        self.assertEqual(self.ptys["pty-codex"].writes, ["/status", "\r"], "no second Enter on Codex")
        self.assertEqual(dashboard.send_command(rid, "/status", identity="nobody", key="c")[0], 404)

    def test_output_is_posted_once_as_the_agent_and_agents_never_read_either(self):
        rid = self.room((("claude", "claude"), ("claude-2", "claude")), mode="collab")
        dashboard.send_command(rid, "/cost", key="k1")
        code, r = dashboard.command_output(rid, "k1", "Total cost: $0.01")
        self.assertEqual(code, 200)
        self.assertEqual((r["message"]["noticeKind"], r["message"]["from"]), ("commandOutput", "claude"))
        self.assertIsNone(dashboard.attention.open_ask(chatroom.get_room(rid, public=False)), "its output asks nothing")
        self.assertTrue(dashboard.command_output(rid, "k1", "again")[1]["duplicate"])
        self.assertEqual(dashboard.command_output(rid, "nope", "x")[0], 404)
        kinds = [m.get("noticeKind") for m in chatroom.get_room(rid)["messages"]]
        self.assertEqual(kinds.count("commandOutput"), 1)
        for ident in ("claude", "claude-2"):
            self.assertEqual([m for m in chatroom.read_new_for(rid, ident) if chatroom.is_command_record(m)], [])
        self.assertTrue(dashboard._hub_row({"kind": "notice", "noticeKind": "command"}))
        self.assertTrue(dashboard._hub_row({"kind": "notice", "noticeKind": "commandOutput"}))

    # ---- the hub follows a new conversation ----
    def _codex_rollouts(self, latest):
        """Codex's rollout lookup: ``latest[0]`` is what it finds."""
        cx = mock.Mock()
        cx.latest_session_id_for_cwd.side_effect = lambda cwd, since=0.0: latest[0]
        dashboard._CODEX_SID_TRIED.clear()
        return mock.patch.object(dashboard.agents, "get_agent", return_value=cx)

    def test_codex_new_keeps_the_seat_until_a_new_rollout_appears(self):
        rid = self.room((("codex", "codex"),))
        dashboard.send_command(rid, "/new", key="k1")
        part = chatroom.participant(chatroom.get_room(rid, public=False), "codex")
        self.assertEqual(part["sessionId"], "sid-codex", "nothing has started yet")
        self.assertEqual(part["pendingNew"]["fromSessionId"], "sid-codex")
        latest = ["sid-codex"]          # refused, or no prompt yet: the old one is still the newest
        with self._codex_rollouts(latest):
            dashboard._backfill_codex_session_ids(chatroom.get_room(rid, public=False))
            part = chatroom.participant(chatroom.get_room(rid, public=False), "codex")
            self.assertEqual((part["sessionId"], part.get("cleared")), ("sid-codex", None))
            latest[0] = "sid-codex-new"
            dashboard._CODEX_SID_TRIED.clear()
            dashboard._backfill_codex_session_ids(chatroom.get_room(rid, public=False))
        part = chatroom.participant(chatroom.get_room(rid, public=False), "codex")
        self.assertEqual(part["sessionId"], "sid-codex-new")
        self.assertNotIn("pendingNew", part)
        self.assertEqual(part["cleared"][0]["fromSessionId"], "sid-codex")
        self.assertIn("sid-codex", chatroom.agent_conversations(part))
        past = dashboard.room_past_sessions(chatroom.get_room(rid, public=False))
        self.assertEqual([(p["sessionId"], p["via"]) for p in past], [("sid-codex", "clear")])

    def test_codex_resume_can_go_back_to_an_earlier_conversation(self):
        rid = self.room((("codex", "codex"),))
        room = chatroom.get_room(rid, public=False)
        room["participants"][0]["cleared"] = [{"fromSessionId": "sid-old", "toSessionId": "sid-codex", "at": 1.0}]
        chatroom.update_room(room)
        dashboard.send_command(rid, "/resume", key="k1")
        with self._codex_rollouts(["sid-old"]):
            dashboard._backfill_codex_session_ids(chatroom.get_room(rid, public=False))
        self.assertEqual(chatroom.participant(chatroom.get_room(rid, public=False), "codex")["sessionId"], "sid-old")

    def test_an_unknown_codex_command_is_refused(self):
        rid = self.room((("codex", "codex"),))
        code, reply = dashboard.send_command(rid, "/nosuchcmd", key="k1")
        self.assertEqual((code, reply["error"]), (400, "unknown_command"))
        self.assertEqual(self.ptys["pty-codex"].writes, [])
        self.assertEqual(dashboard.send_command(rid, "/clear", key="k2")[0], 400, "Codex 0.163 has no /clear")
        self.assertEqual(dashboard.send_command(rid, "/permissions", key="k3")[0], 200)
        rid2 = self.room()
        self.assertEqual(dashboard.send_command(rid2, "/nosuchcmd", key="k4")[0], 200, "Claude says it itself")

    def test_the_same_key_sent_during_the_enter_pause_types_once(self):
        rid = self.room()
        pty = self.ptys["pty-claude"]
        second = {}

        def write(data):
            pty.writes.append(data)
            if data == "/mcp" and not second:
                second["r"] = dashboard.send_command(rid, "/mcp", key="k1")
            return 0
        pty.write = write
        with mock.patch.object(dashboard, "_COMMAND_WAIT_S", 0.2, create=True):
            code, _ = dashboard.send_command(rid, "/mcp", key="k1")
        self.assertEqual(code, 200)
        self.assertEqual(pty.writes, ["/mcp", "\r"])
        self.assertEqual(second["r"][0], 409, "still typing: not typed again")
        self.assertTrue(dashboard.send_command(rid, "/mcp", key="k1")[1].get("duplicate"))

    # ---- the hub's lines wait while a menu is open ----
    def test_hub_lines_wait_while_a_menu_is_open_then_go_in_order(self):
        rid = self.room()
        pty = self.ptys["pty-claude"]
        started = []
        typed = []
        with mock.patch.object(dashboard.threading, "Thread",
                               lambda target, args=(), daemon=None: mock.Mock(start=lambda: started.append(args))), \
             mock.patch.object(dashboard, "_type_input_now",
                               lambda sess, text, prov=None, record=True, parts=None, now="": typed.append(text) or True):
            dashboard.send_command(rid, "/model", key="k1")
            dashboard.menu_report("pty-claude", True)
            self.assertTrue(dashboard._type_input(pty, "[digest] one"))
            self.assertTrue(dashboard._type_input(pty, "two"))
            self.assertEqual(typed, [], "nothing typed into the menu")
            self.assertEqual(len(started), 1, "one drain")
            dashboard.menu_report("pty-claude", False)
            dashboard._menu_drain(pty)
            self.assertEqual(typed, ["[digest] one", "two"])
            self.assertNotIn("pty-claude", dashboard._MENU_HELD)
            self.assertTrue(dashboard._type_input(pty, "three"))
            self.assertEqual(typed[-1], "three", "no menu: typed at once")

    def test_a_menu_left_open_with_no_page_is_read_off_the_screen(self):
        self.room()
        pty = self.ptys["pty-claude"]
        dashboard.menu_report("pty-claude", True)
        dashboard._MENUS["pty-claude"]["seen"] = time.time() - 60          # the page went away
        pty.tail = lambda *a, **k: "Select model\n> 1. Default\nEnter to confirm · Esc to exit"
        self.assertTrue(dashboard.menu_open(pty))
        pty.tail = lambda *a, **k: "> \n? for shortcuts"
        self.assertFalse(dashboard.menu_open(pty))
        self.assertNotIn("pty-claude", dashboard._MENUS)
        dashboard.menu_typed("pty-claude")
        self.assertTrue(dashboard.menu_open(pty), "a command typed, before the page looked")
        dashboard.menu_report("pty-claude", False)
        self.assertFalse(dashboard.menu_open(pty))

    def test_the_menu_check_on_the_measured_screens(self):
        screens = {}
        for agent in ("claude", "codex"):
            for block in (FIX / f"slash_screens_{agent}.txt").read_text(encoding="utf-8").split("===== ")[1:]:
                name, _, body = block.partition(" =====\n")
                screens[(agent, name)] = body.splitlines()
        self.assertTrue(dashboard.screen_menu_open(screens[("codex", "model-open")]))
        self.assertFalse(dashboard.screen_menu_open(screens[("codex", "model-esc")]))
        self.assertFalse(dashboard.screen_menu_open(screens[("codex", "status")]))

    def test_claude_clear_hook_moves_the_seat(self):
        rid = self.room()
        self.assertFalse(dashboard._follow_cleared(rid, "claude", "pty-other", "sid-new"), "another terminal")
        self.assertTrue(dashboard._follow_cleared(rid, "claude", "pty-claude", "sid-new"))
        self.assertFalse(dashboard._follow_cleared(rid, "claude", "pty-claude", "sid-new"), "already there")
        part = chatroom.participant(chatroom.get_room(rid, public=False), "claude")
        self.assertEqual(part["sessionId"], "sid-new")
        self.assertEqual(part["cleared"][0]["fromSessionId"], "sid-claude")
        self.assertEqual(self.ptys["pty-claude"].meta["sessionId"], "sid-new")
        self.assertEqual(chatroom.agent_conversations(part), ["sid-new", "sid-claude"])
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('event.get("source") in ("clear", "resume")', src, "/resume is followed too")


class Transcript(unittest.TestCase):
    def test_command_turns_and_the_compact_summary_are_counted_but_hidden(self):
        rows = [
            {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": "2026-10-22T08:00:00Z"},
            {"type": "user", "message": {"role": "user", "content": "/compact"}, "timestamp": "2026-10-22T08:01:00Z"},
            {"type": "user", "isCompactSummary": True, "timestamp": "2026-10-22T08:01:05Z",
             "message": {"role": "user", "content": "This session is being continued from a previous conversation."}},
            {"type": "user", "message": {"role": "user", "content": "/usr/bin is broken"}, "timestamp": "2026-10-22T08:02:00Z"},
            {"type": "user", "message": {"role": "user", "content": "/tmp"}, "timestamp": "2026-10-22T08:03:00Z"},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            turns = dashboard._claude_text_turns(p)
        self.assertEqual([t.get("hidden") for t in turns], [None, "command", "summary", None, None])


class Catalogue(unittest.TestCase):
    def test_builtins_skills_and_commands(self):
        with tempfile.TemporaryDirectory() as d:
            home, cwd = Path(d) / "home", Path(d) / "proj"
            (home / ".claude/skills/tidy").mkdir(parents=True)
            (home / ".claude/skills/tidy/SKILL.md").write_text("---\nname: tidy\ndescription: Tidy up\n---\n", encoding="utf-8")
            (home / ".claude/commands/git").mkdir(parents=True)
            (home / ".claude/commands/git/sync.md").write_text("Pull and push\n", encoding="utf-8")
            (cwd / ".claude/commands").mkdir(parents=True)
            (cwd / ".claude/commands/hello.md").write_text("---\ndescription: Say hello\n---\nSay hello", encoding="utf-8")
            (home / ".codex/prompts").mkdir(parents=True)
            (home / ".codex/prompts/fix.md").write_text("---\ndescription: Fix it\n---\n", encoding="utf-8")
            cl = {c["name"]: c for c in slash.catalogue("claude", str(cwd), home)}
            with mock.patch.dict("os.environ", {"CODEX_HOME": ""}):
                cx = {c["name"]: c for c in slash.catalogue("codex", str(cwd), home)}
        self.assertEqual(cl["mcp"]["source"], "builtin")
        self.assertEqual((cl["tidy"]["source"], cl["tidy"]["description"]), ("skill", "Tidy up"))
        self.assertEqual((cl["git:sync"]["source"], cl["git:sync"]["description"]), ("command", "Pull and push"))
        self.assertEqual(cl["hello"]["description"], "Say hello")
        self.assertNotIn("exit", cl)
        self.assertIn("model", cx)
        self.assertEqual(cx["prompts:fix"]["source"], "prompt")
        self.assertNotIn("tidy", cx)

    def test_what_is_a_command(self):
        self.assertTrue(slash.is_command("/model opus"))
        self.assertEqual(slash.command_name("/Model opus"), "model")
        for t in ("model", "/usr/bin", "/a\n/b", "", "/ x"):
            self.assertFalse(slash.is_command(t), t)
        self.assertTrue(slash.starts_new_conversation("codex", "/new"))
        self.assertFalse(slash.starts_new_conversation("claude", "/compact"))


def screens(kind: str) -> dict[str, list[str]]:
    text = (FIX / f"slash_screens_{kind}.txt").read_text(encoding="utf-8")
    out, name = {}, None
    for line in text.split("\n"):
        m = re.match(r"^===== (.+) =====$", line)
        if m:
            name = m.group(1)
            out[name] = []
        elif name:
            out[name].append(line)
    return out


@unittest.skipUnless(NODE, "node is not installed")
class ScreenReading(unittest.TestCase):
    """session.html's screen reading, on the measured screens."""

    def run_page(self, cases: list[dict]) -> list:
        start = SESSION.index("// ---- Reading the agent's screen ----")
        end = SESSION.index("function cmdWatch(")
        js = SESSION[start:end] + "\nconst cases = " + json.dumps(cases) + ";\n" + """
console.log(JSON.stringify(cases.map(k => {
  if (k.op === 'screen') return cmdScreen(k.agent, k.lines, k.s0).menu;
  const c = { agent: k.agent, text: k.text, s0: k.s0 };
  c.echo0 = k.s0.filter(l => cmdEcho(c, l)).length;
  return cmdOutput(c, k.lines);
})));
"""
        r = subprocess.run([NODE, "-"], input=js, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_claude(self):
        s = screens("claude")
        menu_mcp, menu_cost, closed, out_model, out_compact, out_skill = self.run_page([
            {"op": "screen", "agent": "claude", "lines": s["mcp-open"], "s0": s["prompt1"]},
            {"op": "screen", "agent": "claude", "lines": s["cost"], "s0": s["model-esc"]},
            {"op": "screen", "agent": "claude", "lines": s["model-esc"], "s0": s["prompt1"]},
            {"op": "out", "agent": "claude", "text": "/model", "lines": s["model-esc"], "s0": s["mcp-esc2"]},
            {"op": "out", "agent": "claude", "text": "/compact", "lines": s["compact"], "s0": s["skill"]},
            {"op": "out", "agent": "claude", "text": "/ensemble", "lines": s["skill"], "s0": s["hello"]},
        ])
        self.assertTrue(menu_mcp.startswith("Manage MCP servers"), menu_mcp)
        self.assertIn("claude-in-chrome", menu_mcp)
        self.assertNotIn("Baked", menu_mcp)
        self.assertIn("Total cost:", menu_cost)
        self.assertIsNone(closed)
        self.assertEqual(out_model["text"], "Kept model as Haiku 5.5")
        self.assertTrue(out_compact["text"].startswith("Compacted"), out_compact)
        self.assertTrue(out_skill["prompt"], "a skill's reply is the agent's turn")

    def test_codex(self):
        s = screens("codex")
        menu, closed, status, mcp = self.run_page([
            {"op": "screen", "agent": "codex", "lines": s["model-open"], "s0": s["prompt1"]},
            {"op": "screen", "agent": "codex", "lines": s["model-esc"], "s0": s["prompt1"]},
            {"op": "out", "agent": "codex", "text": "/status", "lines": s["status"], "s0": s["model-esc2"]},
            {"op": "out", "agent": "codex", "text": "/mcp", "lines": s["mcp"], "s0": s["status"]},
        ])
        self.assertTrue(menu.strip().startswith("Select Model and Effort"), menu)
        self.assertIn("› 2. GPT-6-Astra (current)", menu)
        self.assertIn("enter select", menu)
        self.assertIsNone(closed)
        self.assertIn("Model:", status["text"])
        self.assertNotIn("for shortcuts", status["text"])
        self.assertTrue(mcp["text"].startswith("🔌"), mcp)
        self.assertNotIn("Model:", mcp["text"])


class Page(unittest.TestCase):
    def test_the_page_has_the_parts(self):
        for needle in ('id="slashpop"', 'id="cmdpanel"', "'/api/room/command'", "'/api/room/command-output'",
                       "const cmd = !atts.length && await slCommandOf(t);", "isCmdRow(m)"):
            self.assertIn(needle, SESSION)


if __name__ == "__main__":
    unittest.main()
