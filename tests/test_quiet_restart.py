"""A planned hub restart brings every room that was running back by itself, and
quietly: an agent that was idle when the hub stopped is typed nothing, one that
was in the middle of a turn gets one short line (dashboard.RESTART_NOTE).

* ``attention.turn_state``: who is mid-turn, from the hooks, Claude's status
  file, then the screen.
* ``dashboard.take_restart_snapshot``: what the stopping hub writes down.
* ``Handler._start_or_resume_room(restart=…)`` and ``_restore_after_restart``:
  what the hub that comes back does with it, and what it logs.
* ``RESUME_NOTE`` is still what a person or a PO starting a stopped task types,
  without "read your spec again" when this session has read the spec as it is.
* ``restart-hub.ps1`` asks for the snapshot before the stop and the restore
  after it, and still resumes the plan's rooms when the hub had no snapshot.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agent_hooks
import attention
import chatroom
import dashboard
from test_send_resumes import FakePty

ROOT = Path(__file__).resolve().parent.parent
HELPER = (ROOT / "restart-hub.ps1").read_text(encoding="ascii").replace("\r\n", "\n")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8", errors="replace")

IDLE_SCREEN = "● Done.\n\n❯ \n"
BUSY_SCREEN = "● Reading files\n\n✻ Working… (12s · esc to interrupt)\n"


class TurnState(unittest.TestCase):
    def setUp(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        self.sessions = {}
        p = mock.patch.object(dashboard.ptyrun, "get", lambda pid: self.sessions.get(pid))
        p.start()
        self.addCleanup(p.stop)
        attention._ANALYSIS.clear()
        self.addCleanup(attention._ANALYSIS.clear)

    def live(self, pty_id, tail, idle=2):
        self.sessions[pty_id] = types.SimpleNamespace(
            id=pty_id, last_output=time.time() - idle, alive=lambda: True, tail=lambda: tail,
            info=lambda: {"idleSeconds": idle}, last_submit=lambda: 0.0, death=lambda: None)

    def hook(self, pty_id, name, **fields):
        event = {"hook_event_name": name, "session_id": "s1", **fields}
        agent_hooks.record({"room": "room-1", "identity": "claude", "ptyId": pty_id, "event": event},
                           lambda pid: ("room-1", "claude"))

    def test_the_hooks_say_it_first(self):
        part = {"identity": "claude", "agent": "claude", "ptyId": "p1", "sessionId": "s1"}
        self.live("p1", IDLE_SCREEN)
        self.hook("p1", "UserPromptSubmit")
        self.assertEqual(attention.turn_state(part, {}), ("working", "hook"))
        self.hook("p1", "PermissionRequest", tool_name="Bash")
        self.assertEqual(attention.turn_state(part, {}), ("waiting", "hook"))
        self.hook("p1", "PostToolUse", tool_name="Bash")
        self.hook("p1", "Stop")
        self.assertEqual(attention.turn_state(part, {}), ("idle", "hook"))

    def test_the_status_file_when_no_hook_has_spoken(self):
        part = {"identity": "claude", "agent": "claude", "ptyId": "p1", "sessionId": "s1"}
        self.live("p1", IDLE_SCREEN)
        self.assertEqual(attention.turn_state(part, {"s1": ("busy", 0.0)}), ("working", "status"))
        self.assertEqual(attention.turn_state(part, {"s1": ("idle", 0.0)}), ("idle", "status"))

    def test_codex_is_read_off_its_screen(self):
        part = {"identity": "codex", "agent": "codex", "ptyId": "p2"}
        self.live("p2", BUSY_SCREEN)
        self.assertEqual(attention.turn_state(part, {}), ("working", "screen"))
        attention._ANALYSIS.clear()
        self.live("p2", IDLE_SCREEN, idle=400)
        self.assertEqual(attention.turn_state(part, {}), ("idle", "screen"))
        # A working indicator left on a screen that has been still is a leftover.
        attention._ANALYSIS.clear()
        self.live("p2", BUSY_SCREEN, idle=400)
        self.assertEqual(attention.turn_state(part, {}), ("idle", "screen"))

    def test_no_live_terminal_is_stopped(self):
        self.assertEqual(attention.turn_state({"identity": "claude", "ptyId": "gone"}, {}), ("stopped", ""))


class _Hub(unittest.TestCase):
    """Rooms on disk in a temp state dir, fake terminals, and a handler whose
    resume spawns a FakePty and records what it was asked for."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = self.dir / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old_rooms)
        dashboard._RESUMES.clear()
        self.addCleanup(dashboard._RESUMES.clear)
        self.ptys: dict[str, FakePty] = {}
        self.listed: list[dict] = []
        self.projects: list[dict] = []
        self.states: dict[str, tuple] = {}
        self.starts = 0
        self.reviews: list[tuple] = []
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir),
                  mock.patch.object(dashboard.ptyrun, "get", lambda pid: self.ptys.get(pid)),
                  mock.patch.object(dashboard.ptyrun, "list_sessions", lambda: list(self.listed)),
                  mock.patch.object(dashboard.rotation, "IDLE_S", 0),
                  mock.patch.object(dashboard, "load_projects", lambda: self.projects),
                  mock.patch.object(dashboard, "RESUME_NOTE_WAIT_S", 2),
                  mock.patch.object(dashboard.attention, "_claude_status_by_session", lambda: {}),
                  mock.patch.object(dashboard.attention, "turn_state",
                                    lambda part, statuses=None: self.states.get(
                                        part.get("ptyId"), ("stopped", "")))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.join)

    def join(self, timeout=8):
        for t in threading.enumerate():
            if t.name.startswith("resume-deliver-"):
                t.join(timeout)

    def room(self, agents=("claude",), title="t", roles=None, spec="Do it."):
        roles = roles or ["engineer", "designer"]
        parts = [{"identity": a, "agent": a.split("-")[0], "model": "", "role": roles[i]}
                 for i, a in enumerate(agents)]
        rid = chatroom.create_room(title, parts)["id"]
        full = chatroom.get_room(rid, public=False)
        full.update(mode="solo" if len(agents) == 1 else "collab", launched=True, spec=spec)
        full["messages"] = [{"id": "m0", "from": "user", "text": "spec", "to": "", "ts": time.time()}]
        for p in full["participants"]:
            if p.get("kind") == "agent":
                p["sessionId"] = "sid-" + p["identity"]
        chatroom.update_room(full)
        return rid

    def running(self, rid, identity, state, source="hook"):
        """The agent has a live terminal in this state (before the restart)."""
        pid = f"old-{rid}-{identity}"
        chatroom.patch_participant(rid, identity, {"ptyId": pid})
        self.ptys[pid] = FakePty(pid)
        self.listed.append({"id": pid, "alive": True, "meta": {"room": rid, "identity": identity}})
        self.states[pid] = (state, source)
        return pid

    def hub_stops(self):
        """Every terminal dies with the hub; the new process knows none."""
        self.ptys.clear()
        self.listed.clear()
        self.states.clear()

    def handler(self):
        test = self

        class H(dashboard.Handler):
            def __init__(self):
                pass

            def _resume_room_agent_pty(self, room_full, part, collab=True, seed="", human=False):
                test.starts += 1
                pid = f"pty-{part['identity']}-{test.starts}"
                test.ptys[pid] = FakePty(pid)
                return {"ptyId": pid, "cwd": "", "sessionId": part.get("sessionId", ""),
                        "prompted": False}

            def _launch_room_agent_pty(self, *a, **k):
                raise AssertionError("a room coming back is not launched fresh")

            def _start_review(self, room_id, ident, msg):
                test.reviews.append((room_id, ident, msg.get("id")))
                return {"n": 2}

        return H()

    def typed(self, rid):
        """{identity: [what was typed into its new terminal]} for a room."""
        room = chatroom.get_room(rid, public=False)
        return {p["identity"]: list(self.ptys[p["ptyId"]].typed)
                for p in room["participants"]
                if p.get("kind") == "agent" and p.get("ptyId") in self.ptys}

    def lease(self, ident="lease-1"):
        (self.dir / "restart.lease").write_text(json.dumps({"id": ident, "at": time.time()}),
                                                encoding="utf-8")
        return ident

    def log(self) -> str:
        path = self.dir / "logs" / "restart.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""


class Snapshot(_Hub):
    def test_it_lists_every_live_room_and_what_each_agent_is_doing(self):
        idle, busy, stopped = self.room(title="idle"), self.room(title="busy"), self.room(title="off")
        self.running(idle, "claude", "idle")
        self.running(busy, "claude", "working", "screen")
        snap = dashboard.take_restart_snapshot(self.lease(), wake_room="")
        on_disk = json.loads((self.dir / "restart-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, snap)
        self.assertEqual((snap["leaseId"], snap["hubPid"]), ("lease-1", os.getpid()))
        self.assertAlmostEqual(snap["at"], time.time(), delta=5)
        by = {r["roomId"]: r for r in snap["rooms"]}
        self.assertEqual(sorted(by), sorted([idle, busy]))          # never the stopped one
        self.assertNotIn(stopped, by)
        self.assertEqual([(a["identity"], a["state"], a["source"]) for a in by[busy]["agents"]],
                         [("claude", "working", "screen")])
        self.assertEqual(by[idle]["agents"][0]["state"], "idle")

    def test_the_helpers_snapshot_keeps_the_po_that_asked(self):
        po = self.room(title="PO")
        self.running(po, "claude", "working")
        dashboard.take_restart_snapshot(self.lease(), wake_room=po)
        self.assertEqual(dashboard.take_restart_snapshot("lease-1")["wakeRoom"], po)
        self.assertEqual(dashboard.take_restart_snapshot("another")["wakeRoom"], "")

    def test_a_reviewer_at_work_is_recorded_with_its_request(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        self.running(rid, "claude", "idle")
        self.running(rid, "codex", "working", "screen")
        chatroom.patch_participant(rid, "codex", {"review": {"n": 2, "messageId": "m7"}})
        snap = dashboard.take_restart_snapshot(self.lease(), wake_room="")
        codex = next(a for a in snap["rooms"][0]["agents"] if a["identity"] == "codex")
        self.assertEqual((codex.get("onMention"), codex.get("reviewMessageId")), (True, "m7"))


class Restore(_Hub):
    def restart(self, wake_room="", age=0.0, lease="lease-1"):
        """Snapshot, stop, and the new hub's restore (another process: another pid)."""
        dashboard.take_restart_snapshot(self.lease(lease), wake_room=wake_room)
        path = self.dir / "restart-snapshot.json"
        snap = json.loads(path.read_text(encoding="utf-8"))
        snap.update(hubPid=os.getpid() + 1, at=snap["at"] - age)
        path.write_text(json.dumps(snap), encoding="utf-8")
        self.hub_stops()
        out = self.handler()._restore_after_restart(lease)
        self.join()
        return out

    def test_idle_gets_nothing_and_mid_turn_gets_exactly_one_line(self):
        idle, busy = self.room(title="Selling X5"), self.room(title="Busy one")
        self.running(idle, "claude", "idle")
        self.running(busy, "claude", "working")
        out = self.restart()
        self.assertEqual(sorted(r["roomId"] for r in out["rooms"]), sorted([idle, busy]))
        self.assertEqual(self.typed(idle), {"claude": []})
        self.assertEqual(self.typed(busy), {"claude": [dashboard.RESTART_NOTE]})
        # Both are live again, and nobody called resume.
        for rid in (idle, busy):
            self.assertTrue(dashboard._room_is_live(chatroom.get_room(rid, public=False)))
        # The idle one looks as it did: no "asked to carry on" for attention to time.
        self.assertNotIn("resumedAt", chatroom.participant(chatroom.get_room(idle, public=False), "claude"))
        self.assertIn("resumedAt", chatroom.participant(chatroom.get_room(busy, public=False), "claude"))
        log = self.log()
        self.assertIn(f"restore {idle} 'Selling X5' (was: claude idle/hook): brought back; nothing typed", log)
        self.assertIn(f"restore {busy} 'Busy one' (was: claude working/hook): brought back; one line to claude", log)
        self.assertIn(f"restore {busy}/claude: typed the one restart line", log)

    def test_the_line_is_short_and_says_what_not_to_do(self):
        note = dashboard.RESTART_NOTE
        self.assertLess(len(note), 260)
        for words in ("do not start over", "do not read your spec again", "do not report the restart"):
            self.assertIn(words, note)
        self.assertNotIn("ensemble_get_task", note)
        self.assertEqual(dashboard.hub_input_kind(note), {"kind": "restart"})
        self.assertFalse(dashboard.typed_by_person(note))
        self.assertIn("restart: 'Hub restarted'", SESSION)

    def test_a_prompt_that_was_up_is_a_turn_not_over(self):
        rid = self.room()
        self.running(rid, "claude", "waiting")
        self.restart()
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESTART_NOTE]})

    def test_in_a_team_each_agent_by_what_it_was_doing(self):
        rid = self.room(agents=("claude", "codex"))
        self.running(rid, "claude", "idle")
        self.running(rid, "codex", "working", "screen")
        self.restart()
        self.assertEqual(self.typed(rid), {"claude": [], "codex": [dashboard.RESTART_NOTE]})

    def test_a_stale_snapshot_brings_rooms_back_but_trusts_no_turn(self):
        rid = self.room()
        self.running(rid, "claude", "working")
        self.restart(age=dashboard.RESTART_SNAPSHOT_MAX_AGE_S + 60)
        self.assertEqual(self.typed(rid), {"claude": []})
        self.assertIn("who was mid-turn is unknown", self.log())

    def test_no_snapshot_or_another_restarts_does_nothing(self):
        rid = self.room()
        self.lease("lease-1")
        self.assertEqual(self.handler()._restore_after_restart("lease-1")["rooms"], [])
        self.running(rid, "claude", "working")
        dashboard.take_restart_snapshot("older-restart", wake_room="")
        self.hub_stops()
        out = self.handler()._restore_after_restart("lease-1")
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertIn("another restart", out["note"])

    def test_the_hub_that_took_the_snapshot_never_restores_it(self):
        # The stop failed and the same process is still up: nothing is typed
        # into agents that never went away.
        rid = self.room()
        self.running(rid, "claude", "working")
        dashboard.take_restart_snapshot(self.lease(), wake_room="")
        out = self.handler()._restore_after_restart("lease-1")
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertIn("did not restart", out["note"])

    def test_it_happens_once(self):
        rid = self.room()
        self.running(rid, "claude", "working")
        self.restart()
        self.assertEqual(self.handler()._restore_after_restart("lease-1")["rooms"], [])
        self.assertEqual(self.starts, 1)
        self.assertTrue((self.dir / "restart-snapshot.last.json").exists())

    def test_the_restarting_po_is_left_to_the_helper_and_another_po_gets_a_row(self):
        mine, other, busy_po = self.room(title="ED PO"), self.room(title="Motors PO"), self.room(title="Docs PO")
        self.projects = [{"id": "ed", "poRoomId": mine}, {"id": "mo", "poRoomId": other},
                         {"id": "do", "poRoomId": busy_po}]
        self.running(mine, "claude", "working")         # it called ensemble_restart_hub
        self.running(other, "claude", "idle")
        self.running(busy_po, "claude", "working")
        self.restart(wake_room=mine)
        self.assertEqual(self.typed(mine), {"claude": []})           # the helper types its note
        self.assertEqual(self.typed(other), {"claude": []})
        self.assertEqual(self.typed(busy_po), {"claude": [dashboard.RESTART_NOTE]})
        rows = {rid: [m for m in chatroom.get_room(rid)["messages"] if m.get("noticeKind") == "restart"]
                for rid in (mine, other, busy_po)}
        self.assertEqual([len(rows[r]) for r in (mine, other, busy_po)], [0, 1, 0])
        row = rows[other][0]
        self.assertRegex(row["text"], r"^The hub was restarted at \d\d:\d\d\.$")
        self.assertEqual((row["kind"], row["rang"]), ("notice", []))
        self.assertIn("the helper tells it", self.log())

    def test_a_room_already_running_is_left_alone(self):
        rid = self.room()
        self.running(rid, "claude", "working")
        dashboard.take_restart_snapshot(self.lease(), wake_room="")
        path = self.dir / "restart-snapshot.json"
        snap = json.loads(path.read_text(encoding="utf-8"))
        snap["hubPid"] = os.getpid() + 1
        path.write_text(json.dumps(snap), encoding="utf-8")
        out = self.handler()._restore_after_restart("lease-1")      # its terminal is still there
        self.assertEqual((out["rooms"][0]["outcome"], self.starts), ("already running: left alone", 0))

    def test_a_review_that_was_running_is_started_again_with_its_request(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        full = chatroom.get_room(rid, public=False)
        full["messages"].append({"id": "m7", "from": "claude", "to": "codex", "text": "@codex review abc",
                                 "ts": time.time()})
        chatroom.update_room(full)
        self.running(rid, "claude", "idle")
        self.running(rid, "codex", "working", "screen")
        chatroom.patch_participant(rid, "codex", {"review": {"n": 2, "messageId": "m7"}})
        self.restart()
        self.assertEqual(self.reviews, [(rid, "codex", "m7")])
        self.assertEqual(self.typed(rid).get("claude"), [])          # its owner waits on, untouched
        self.assertIn("the review by codex that was running was started again", self.log())

    def test_one_room_failing_does_not_stop_the_rest(self):
        bad, good = self.room(title="a"), self.room(title="b")
        self.running(bad, "claude", "idle")
        self.running(good, "claude", "working")
        h = self.handler()
        real = h._resume_room_agent_pty

        def flaky(room_full, part, **kw):
            if room_full["id"] == bad:
                raise RuntimeError("no such session")
            return real(room_full, part, **kw)
        h._resume_room_agent_pty = flaky
        dashboard.take_restart_snapshot(self.lease(), wake_room="")
        path = self.dir / "restart-snapshot.json"
        snap = json.loads(path.read_text(encoding="utf-8"))
        snap["hubPid"] = os.getpid() + 1
        path.write_text(json.dumps(snap), encoding="utf-8")
        self.hub_stops()
        out = {r["roomId"]: r["outcome"] for r in h._restore_after_restart("lease-1")["rooms"]}
        self.join()
        self.assertIn("NOT brought back", out[bad])
        self.assertEqual(self.typed(good), {"claude": [dashboard.RESTART_NOTE]})
        self.assertNotIn(bad, dashboard._RESUMES)


class StartingAStoppedTaskIsUnchanged(_Hub):
    def test_a_person_or_a_po_starting_it_still_types_the_resume_note(self):
        rid = self.room()
        self.handler()._resume_room(chatroom.get_room(rid, public=False))
        self.join()
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE]})
        self.assertIn("read it again with ensemble_get_task", dashboard.RESUME_NOTE)

    def test_quiet_brings_it_back_without_the_note(self):
        rid = self.room()
        self.handler()._resume_room(chatroom.get_room(rid, public=False), quiet=True)
        self.join()
        self.assertEqual(self.typed(rid), {"claude": []})
        self.assertTrue(dashboard._room_is_live(chatroom.get_room(rid, public=False)))

    def test_the_note_drops_read_your_spec_again_when_this_session_has_read_it(self):
        rid = self.room(spec="Sell the X5.")
        part = chatroom.participant(chatroom.get_room(rid, public=False), "claude")
        chatroom.patch_participant(rid, "claude", dashboard.spec_seen(part, "Sell the X5."))
        room = chatroom.get_room(rid, public=False)
        note = dashboard.resume_note_for(room, chatroom.participant(room, "claude"))
        self.assertEqual(note, dashboard.RESUME_NOTE_SAME_SPEC)
        self.assertNotIn("ensemble_get_task", note)
        self.assertIn("do not start over", note)
        self.assertEqual(dashboard.hub_input_kind(note), {"kind": "resumed"})
        self.handler()._resume_room(room)
        self.join()
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE_SAME_SPEC]})

    def test_a_changed_spec_or_another_session_gets_the_full_note(self):
        rid = self.room(spec="Sell the X5.")
        part = chatroom.participant(chatroom.get_room(rid, public=False), "claude")
        chatroom.patch_participant(rid, "claude", dashboard.spec_seen(part, "Sell the X5."))
        room = chatroom.get_room(rid, public=False)
        room["spec"] = "Sell the X5, and the winter tyres."
        self.assertEqual(dashboard.resume_note_for(room, chatroom.participant(room, "claude")),
                         dashboard.RESUME_NOTE)
        room["spec"] = "Sell the X5."
        rotated = {**chatroom.participant(room, "claude"), "sessionId": "a-fresh-session"}
        self.assertEqual(dashboard.resume_note_for(room, rotated), dashboard.RESUME_NOTE)
        # Codex has no session id at its launch: nothing is assumed.
        self.assertEqual(dashboard.resume_note_for(room, {**dashboard.spec_seen({}, "Sell the X5.")}),
                         dashboard.RESUME_NOTE)

    def test_a_note_typed_with_a_message_is_still_two_turns(self):
        for note in (dashboard.RESUME_NOTE_SAME_SPEC, dashboard.RESTART_NOTE):
            turns = dashboard.classify_turns([{"role": "user", "text": note + "\n\nand step 2 please"}])
            self.assertEqual([t["text"] for t in turns], [note, "and step 2 please"])


class TheHelper(unittest.TestCase):
    def test_it_asks_for_the_snapshot_before_the_stop_and_the_restore_after(self):
        snap = HELPER.index("Post '/api/restart/snapshot'")
        stop = HELPER.index("Stop-Process -Id $v.ProcessId")
        up = HELPER.index('L "hub up: $up')
        restore = HELPER.index("Post '/api/restart/restore'")
        wake = HELPER.index("if ($cfg.wakeRoom) {")
        self.assertLess(snap, stop)
        self.assertLess(stop, up)
        self.assertLess(up, restore)
        self.assertLess(restore, wake)
        self.assertEqual(HELPER.count("lease = [string]$cfg.leaseId"), 2)

    def test_a_hub_without_a_snapshot_still_gets_the_plans_rooms_resumed(self):
        # The first restart onto this code stops a hub that has no snapshot
        # endpoint: the call fails, the restart goes on, and the plan's rooms
        # (the PO) are resumed the old way.
        i = HELPER.index("Post '/api/restart/snapshot'")
        self.assertIn("} catch {", HELPER[i:i + 400])
        self.assertIn("if (-not $room -or ($back -contains $room)) { continue }", HELPER)
        self.assertIn("Post '/api/room/resume' @{ roomId = $room }", HELPER)


class TheEndpoints(unittest.TestCase):
    def test_both_ask_for_the_lease_of_the_restart_under_way(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        i = src.index('if p in ("/api/restart/snapshot", "/api/restart/restore"):')
        block = src[i:src.index('if p == "/api/iterm/consolidate":', i)]
        self.assertIn("hmac.compare_digest(lease, held)", block)
        self.assertLess(block.index("compare_digest"), block.index("take_restart_snapshot"))
        self.assertLess(block.index("compare_digest"), block.index("_restore_after_restart"))


if __name__ == "__main__":
    unittest.main()
