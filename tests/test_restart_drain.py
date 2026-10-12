"""A hub restart at a safe point (#211, restart_drain.py).

* The state machine on its own: a request, a second request merged into it,
  what each agent's state becomes from what the hub sees, when the restart
  goes ahead (every agent ready, the deadline, forced), the lines.
* The hub driving it: a working agent is told once and an idle one nothing;
  a ready call lets the restart go; held sends, starts, reviews and wakes;
  a cancel and a failed preflight let everything go and say so; the hub that
  comes back types each agent its own line, then what was held.
"""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from unittest import mock

import chatroom
import dashboard
import restart_drain as rd
import sends
from test_quiet_restart import _Hub
from test_send_resumes import FakePty

PO = {"roomId": "room-po", "identity": "claude", "label": "the Ensemble PO"}


class StateMachine(unittest.TestCase):
    def test_a_request_drains_and_a_second_one_joins_it(self):
        st, merged = rd.request(None, "merged #206", PO, "restart", 100.0, 1200)
        self.assertFalse(merged)
        self.assertEqual((st["state"], st["action"], st["deadline"]), ("draining", "restart", 1300.0))
        st2, merged = rd.request(st, "merged #208", {"label": "the dashboard"}, "update", 200.0, 60)
        self.assertTrue(merged)
        self.assertIs(st2, st)
        self.assertEqual(st["reasons"], ["merged #206", "merged #208"])
        self.assertEqual(st["action"], "update")            # an update wins
        self.assertEqual(st["deadline"], 1300.0)            # the first deadline holds
        self.assertEqual(len(st["requestedBy"]), 2)
        rd.request(st, "merged #206", PO, "restart", 300.0, 60)
        self.assertEqual(len(st["reasons"]), 2)              # same reason, same PO: once
        self.assertEqual(len(st["requestedBy"]), 2)
        self.assertEqual(rd.reason_text(st), "merged #206; merged #208")

    def seen(self, k, state, background=False):
        rid, ident = k.split("/")
        return {"roomId": rid, "identity": ident, "label": k, "kind": "task",
                "state": state, "background": background}

    def obs(self, st, now, **agents):
        """observe() with agents given as a=state (room "r")."""
        return rd.observe(st, {f"r/{i}": self.seen(f"r/{i}", *(v if isinstance(v, tuple) else (v,)))
                               for i, v in agents.items()}, now)

    def test_working_is_told_once_idle_is_ready_at_once(self):
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        tell = self.obs(st, 1.0, a="working", b="idle")
        self.assertEqual(tell, ["r/a"])
        self.assertEqual(st["agents"]["r/b"]["state"], "idle")
        rd.mark_told(st, "r/a", 1.0)
        self.assertEqual(self.obs(st, 2.0, a="working", b="idle"), [])
        self.assertEqual([a["roomId"] + "/" + a["identity"] for a in rd.waiting_for(st)], ["r/a"])
        self.assertEqual(rd.outcome(st, 3.0), "")

    def test_a_command_in_the_background_counts_as_working_until_told(self):
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        self.assertEqual(self.obs(st, 1.0, a=("idle", True)), ["r/a"])
        rd.mark_told(st, "r/a", 1.0)
        self.obs(st, 2.0, a=("idle", True))
        self.assertEqual(st["agents"]["r/a"]["state"], "working")     # told: settles in time
        self.obs(st, 2.0 + rd.SETTLED_S, a=("idle", True))
        self.assertEqual(st["agents"]["r/a"]["state"], "settled")
        self.assertEqual(rd.outcome(st, 40.0), "ready")

    def test_told_then_idle_without_the_tool_settles_and_working_again_resets(self):
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        self.obs(st, 1.0, a="working")
        rd.mark_told(st, "r/a", 1.0)
        self.obs(st, 5.0, a="idle")
        self.obs(st, 10.0, a="working")
        self.obs(st, 11.0, a="idle")
        self.obs(st, 11.0 + rd.SETTLED_S - 1, a="idle")
        self.assertEqual(st["agents"]["r/a"]["state"], "working")
        self.obs(st, 11.0 + rd.SETTLED_S, a="idle")
        self.assertEqual(st["agents"]["r/a"]["state"], "settled")

    def test_ready_holds_and_lets_the_restart_go(self):
        st = rd.new_state("merged #1", PO, "restart", 0.0, 600)
        self.obs(st, 1.0, a="working")
        self.assertEqual(rd.outcome(st, 2.0), "")
        a = rd.ready(st, "r", "a", "  rerun the suite,\n then ask for review 1. ", 3.0)
        self.assertEqual(a["next"], "rerun the suite, then ask for review 1.")
        self.obs(st, 4.0, a="working")       # its turn is still ending
        self.assertEqual(st["agents"]["r/a"]["state"], "ready")
        self.assertEqual(rd.outcome(st, 5.0), "ready")
        self.assertEqual(rd.resume_line(st, a),
                         "[hub restarted] The hub restarted for merged #1. You said you would next: "
                         "rerun the suite, then ask for review 1. Carry on from there.")
        self.assertIsNone(rd.ready(None, "r", "a", "x", 1.0))

    def test_a_terminal_gone_is_not_waited_for(self):
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        self.obs(st, 1.0, a="working")
        rd.observe(st, {}, 2.0)
        self.assertEqual(st["agents"]["r/a"]["state"], "stopped")
        self.assertEqual(rd.outcome(st, 3.0), "ready")

    def test_deadline_and_force(self):
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        self.obs(st, 1.0, a="working")
        self.assertEqual(rd.outcome(st, 599.0), "")
        self.assertEqual(rd.outcome(st, 600.0), "deadline")
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        self.obs(st, 1.0, a="working")
        self.assertTrue(rd.force(st, "the dashboard"))
        self.assertEqual(rd.outcome(st, 2.0), "forced")
        st["state"] = "restarting"
        self.assertFalse(rd.force(st, "x"))
        self.assertEqual(rd.outcome(st, 2.0), "")

    def test_end_gives_back_what_it_held(self):
        st = rd.new_state("x", PO, "restart", 0.0, 600)
        rd.hold(st, {"kind": "send", "roomId": "r", "key": "k1", "text": "hi"}, 1.0)
        self.assertTrue(rd.holds_send(st, "r", "k1"))
        self.assertFalse(rd.holds_send(st, "r", "k2"))
        held = rd.end(st, "cancelled", "cancelled by the dashboard", 2.0)
        self.assertEqual([h["key"] for h in held], ["k1"])
        self.assertEqual((st["state"], st["held"]), ("cancelled", []))
        self.assertEqual(rd.outcome(st, 3.0), "")

    def test_the_lines(self):
        self.assertEqual(dashboard.hub_input_kind(rd.DRAIN_LINE), {"kind": "drain"})
        self.assertEqual(dashboard.hub_input_kind(rd.RESUME_LINE), {"kind": "restart"})
        self.assertIn("ensemble_ready_for_restart", rd.DRAIN_LINE)
        self.assertIn("Do not wait for background commands", rd.DRAIN_LINE)
        self.assertEqual(rd.screen_line("● Reading foo.py\n⏺ Running the full suite\n\n> "),
                         "Running the full suite")
        self.assertEqual(rd.deadline_s({}), 1200.0)
        self.assertEqual(rd.deadline_s({"restartDrainMinutes": 0}), 60.0)
        self.assertEqual(rd.deadline_s({"restartDrainMinutes": "x"}), 1200.0)

    def test_a_background_shell_under_the_agent(self):
        procs = {10: (1, "claude"), 11: (10, "bash"), 12: (11, "pytest"),
                 20: (1, "codex"), 21: (20, "cmd"), 22: (21, "node"),
                 30: (1, "claude"), 31: (30, "node")}
        self.assertTrue(rd.background(10, procs))
        self.assertFalse(rd.background(20, procs))      # an MCP server's launcher
        self.assertFalse(rd.background(30, procs))
        self.assertFalse(rd.background(None, procs))


class Drain(_Hub):
    """The hub driving a drain, with fake terminals and no real restart."""

    def setUp(self):
        super().setUp()
        rd.reset()
        self.addCleanup(rd.reset)
        self.restarts = []
        self.lease_id = "lease-d1"

        def fake_restart(room_id="", drain=False):
            self.restarts.append((room_id, drain))
            (self.dir / "restart.lease").write_text(
                json.dumps({"id": self.lease_id, "at": time.time()}), encoding="utf-8")
            return {"started": True, "status": 202, "leaseId": self.lease_id}
        self.handler_obj = self.handler()
        for p in (mock.patch.object(dashboard, "trigger_restart", fake_restart),
                  mock.patch.object(dashboard, "hub_launcher", lambda: self.handler_obj),
                  mock.patch.object(dashboard, "load_settings", lambda: {}),
                  mock.patch.object(dashboard.peer_process, "processes", lambda: {}),
                  mock.patch.object(dashboard.attention, "looks_like_prompt", lambda t: t == "PROMPT"),
                  mock.patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("ENSEMBLE_RESTART_DRY_RUN", None)
        self.po = self.room(title="PO room")
        self.projects.append({"id": "proj-1", "name": "Ensemble", "poRoomId": self.po})
        self.addCleanup(self.join_release)

    def join_release(self, timeout=8):
        for t in threading.enumerate():
            if t.name == "restart-drain-release":
                t.join(timeout)

    def po_by(self):
        return {"roomId": self.po, "identity": "claude", "label": "the Ensemble PO"}

    def test_working_told_idle_nothing_ready_restarts(self):
        busy, idle = self.room(title="busy"), self.room(title="idle")
        busy_pty = self.running(busy, "claude", "working")
        idle_pty = self.running(idle, "claude", "idle")
        po_pty = self.running(self.po, "claude", "working")
        res = rd.request_restart("merged #206", self.po_by())
        self.assertEqual(res["status"], 202)
        self.assertTrue(rd.holding())
        rd.drain_tick()
        self.assertEqual(self.ptys[busy_pty].typed, [rd.DRAIN_LINE])
        self.assertEqual(self.ptys[idle_pty].typed, [])
        self.assertEqual(self.ptys[po_pty].typed, [])         # the PO that asked is never told
        rd.drain_tick()
        self.assertEqual(self.ptys[busy_pty].typed, [rd.DRAIN_LINE])      # once
        self.assertEqual(self.restarts, [])
        self.assertEqual([w["label"] for w in rd.view(rd.current(), time.time())["waitingFor"]], ["busy"])
        rd.agent_ready(busy, "claude", "rerun the suite")
        rd.drain_tick()
        self.assertEqual(self.restarts, [(self.po, True)])
        st = rd.current()
        self.assertEqual((st["state"], st["lease"]), ("restarting", self.lease_id))
        self.assertTrue(rd.holding())
        self.assertIn("every agent reached a safe point", self.log())

    def test_holds_sends_starts_reviews_and_wakes_to_a_ready_agent(self):
        busy = self.room(title="busy")
        stopped = self.room(title="stopped")
        team = self.room(agents=("claude", "codex"), title="team")
        self.running(busy, "claude", "idle")
        rd.request_restart("x", self.po_by())
        rd.drain_tick()
        h = self.handler_obj
        sends.accept(busy, "send:1", "hello", "")
        out = h._resume_room(chatroom.get_room(busy, public=False), text="hello", key="send:1")
        self.assertTrue(out["afterRestart"])
        self.assertTrue(sends.get(busy, "send:1")["afterRestart"])
        self.assertEqual(dashboard._send_left_behind(busy, "send:1"), "")
        self.assertEqual(sends.reconcile(busy)[0]["state"], "queued")
        self.assertEqual(self.ptys[f"old-{busy}-claude"].typed, [])
        out = h._resume_room(chatroom.get_room(stopped, public=False))
        self.assertTrue(out["afterRestart"])
        self.assertFalse(dashboard._room_is_live(chatroom.get_room(stopped, public=False)))
        self.assertEqual(dashboard.Handler._start_room(h, chatroom.get_room(stopped, public=False)), [])
        review = dashboard.Handler._start_review(h, team, "codex", {"id": "m9"})
        self.assertEqual(review, {"held": True, "n": 0})
        self.assertEqual(h._ring(busy, ["claude"], "wake up"), ["claude"])     # idle: ready
        self.assertEqual(self.ptys[f"old-{busy}-claude"].typed, [])
        kinds = [it["kind"] for it in rd.current()["held"]]
        self.assertEqual(kinds, ["send", "start", "start", "review", "wake"])

    def test_cancel_lets_everything_go_and_says_so(self):
        busy, other = self.room(title="busy"), self.room(title="other")
        busy_pty = self.running(busy, "claude", "working")
        other_pty = self.running(other, "claude", "working")
        po_pty = self.running(self.po, "claude", "idle")
        rd.request_restart("x", self.po_by())
        rd.drain_tick()
        rd.agent_ready(busy, "claude", "push the branch")
        sends.accept(busy, "send:1", "after", "")
        self.handler_obj._resume_room(chatroom.get_room(busy, public=False), text="after", key="send:1")
        res = rd.cancel_drain("the dashboard")
        self.assertTrue(res["ok"])
        self.assertFalse(rd.holding())
        self.join_release()
        self.assertEqual(self.ptys[busy_pty].typed[1:2], [rd.CANCEL_READY_LINE.format(
            why="cancelled by the dashboard", next="push the branch")])
        self.assertEqual(self.ptys[other_pty].typed[1:],
                         [rd.CANCEL_TOLD_LINE.format(why="cancelled by the dashboard")])
        self.assertIn("after", self.ptys[busy_pty].typed[-1])        # the held send, now typed
        self.assertNotIn("afterRestart", sends.get(busy, "send:1"))
        self.assertEqual(len(self.ptys[po_pty].typed), 1)
        self.assertIn("did not happen: cancelled by the dashboard", self.ptys[po_pty].typed[0])
        self.assertEqual(self.restarts, [])

    def test_a_failed_preflight_cancels_the_drain(self):
        busy = self.room(title="busy")
        self.running(busy, "claude", "idle")
        rd.request_restart("x", self.po_by())
        rd.drain_tick()
        self.assertEqual(rd.current()["state"], "restarting")
        st = rd.current()
        st["startedAt"] = time.time() - rd.LEASE_GONE_S - 1
        rd.save(st)
        (self.dir / "logs").mkdir(exist_ok=True)
        with open(self.dir / "logs" / "restart.log", "a", encoding="utf-8") as f:
            f.write("2026-10-12T15:00:00 [restart] PREFLIGHT FAILED: port 5555 did not serve\n")
        (self.dir / "restart.lease").unlink()
        rd.drain_tick()
        st = rd.current()
        self.assertEqual(st["state"], "failed")
        self.assertIn("PREFLIGHT FAILED: port 5555 did not serve", st["outcome"])
        self.assertFalse(rd.holding())

    def test_the_deadline_cuts_off_and_notes_the_screen(self):
        busy = self.room(title="busy")
        busy_pty = self.running(busy, "claude", "working")
        self.ptys[busy_pty]._tail = "● Editing restart_drain.py\n✻ Working… (3s)"
        rd.request_restart("x", self.po_by())
        rd.drain_tick()
        st = rd.current()
        st["deadline"] = time.time() - 1
        rd.save(st)
        rd.drain_tick()
        a = rd.current()["agents"][rd.key(busy, "claude")]
        self.assertTrue(a["interrupted"])
        self.assertEqual(a["screen"], "Editing restart_drain.py")
        self.assertEqual(rd.current()["outcome"], "the deadline came")

    def test_force(self):
        busy = self.room(title="busy")
        self.running(busy, "claude", "working")
        rd.request_restart("x", self.po_by())
        rd.drain_tick()
        self.assertEqual(self.restarts, [])
        self.assertTrue(rd.force_drain("the dashboard")["ok"])
        rd.drain_tick()
        self.assertEqual(len(self.restarts), 1)
        self.assertEqual(rd.current()["outcome"], "forced by the dashboard")

    def test_a_restart_already_started_takes_no_more_requests(self):
        rd.request_restart("x", self.po_by())
        rd.drain_tick()
        res = rd.request_restart("y", {"label": "the dashboard"})
        self.assertEqual(res["status"], 409)

    def test_dry_run_ends_the_drain(self):
        with mock.patch.dict(os.environ, {"ENSEMBLE_RESTART_DRY_RUN": "1"}):
            rd.request_restart("x", {"label": "the dashboard"})
            rd.drain_tick()
        self.assertEqual(rd.current()["state"], "done")
        self.assertEqual(self.restarts, [])

    def test_the_hub_that_comes_back_types_each_its_own_line_then_what_was_held(self):
        ready, cut, idle = self.room(title="ready"), self.room(title="cut"), self.room(title="idle")
        self.running(ready, "claude", "working")
        cut_pty = self.running(cut, "claude", "working")
        self.running(idle, "claude", "idle")
        self.ptys[cut_pty]._tail = "● Running the full suite\n✻ Working…"
        rd.request_restart("merged #206", self.po_by())
        rd.drain_tick()
        rd.agent_ready(ready, "claude", "rerun the suite, then ask for review 1")
        sends.accept(ready, "send:9", "a message typed during the drain", "")
        self.handler_obj._resume_room(chatroom.get_room(ready, public=False),
                                      text="a message typed during the drain", key="send:9")
        rd.force_drain("the dashboard")
        rd.drain_tick()
        self.assertEqual(rd.current()["state"], "restarting")
        # The snapshot the restart takes, then the new hub process.
        dashboard.take_restart_snapshot(self.lease_id, wake_room=self.po)
        path = self.dir / "restart-snapshot.json"
        snap = json.loads(path.read_text(encoding="utf-8"))
        snap["hubPid"] = os.getpid() + 1
        path.write_text(json.dumps(snap), encoding="utf-8")
        self.hub_stops()
        self.handler_obj._restore_after_restart(self.lease_id)
        self.join()
        self.join_release()
        self.join()
        typed = self.typed(ready)["claude"]
        self.assertEqual(typed[0].replace("\x1b[200~", "").split("\n\n")[0],
                         "[hub restarted] The hub restarted for merged #206. You said you would "
                         "next: rerun the suite, then ask for review 1. Carry on from there.")
        self.assertIn("a message typed during the drain", "\n".join(typed))
        self.assertLess("\n".join(typed).index("You said"),
                        "\n".join(typed).index("a message typed during the drain"))
        cut_typed = self.typed(cut)["claude"]
        self.assertEqual(cut_typed, [dashboard.RESTART_NOTE + " The last thing your screen showed "
                                     "you doing: Running the full suite"])
        self.assertEqual(self.typed(idle)["claude"], [])
        self.assertEqual(rd.current()["state"], "done")
        self.assertFalse(rd.holding())
        self.assertIn("paused at a safe point and resumed: ready", rd.current()["outcome"])
        self.assertIn("cut off and resumed: cut", rd.current()["outcome"])

    def test_a_hub_that_died_mid_drain_calls_it_off(self):
        rd.request_restart("x", {"label": "the dashboard"})
        with mock.patch.object(rd, "CANCEL_RELEASE_S", 0), mock.patch.object(dashboard, "HUB_PORT", 8765):
            rd.on_startup()
            for _ in range(50):
                if not rd.holding():
                    break
                time.sleep(0.05)
        self.assertEqual(rd.current()["state"], "cancelled")
        self.assertEqual(rd.current()["outcome"], "the hub stopped during the drain")


class Tool(unittest.TestCase):
    def test_every_agent_has_the_ready_tool(self):
        import ensemble_tools
        self.assertIn("ensemble_ready_for_restart", ensemble_tools.COMMON_TOOL_NAMES)
        schema = next(t for t in ensemble_tools.RESTART_TOOLS if t["name"] == "ensemble_restart_hub")
        self.assertEqual(set(schema["inputSchema"]["properties"]), {"when", "reason", "cancel"})


if __name__ == "__main__":
    unittest.main()
