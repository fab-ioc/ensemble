"""#190 (GitHub issue 13): the left list's dot tells the truth.

Red ("agent gone") is an agent the hub expected to run that died and that
nobody has dealt with since; a session file whose pid Windows handed to
another process is not a running session.
"""

from __future__ import annotations

import time
import types
import unittest

import attention
import chatroom
import dashboard
from backends import shared
from test_open_ask import _Bell


class AgentGone(_Bell):
    def setUp(self):
        super().setUp()
        self.ended = time.time() - 600
        self.died = {"ptyId": "pty-1", "exitCode": 1, "killed": False,
                     "endedAt": self.ended, "tail": "● ok\n", "lastInput": 0.0}
        self.sess.alive = lambda: False
        self.sess.death = lambda: self.died

    def patch(self, **fields):
        full = chatroom.get_room(self.rid, public=False)
        full.update(fields)
        chatroom.update_room(full)
        attention._SUMMARY_CACHE.clear()

    def patch_part(self, **fields):
        full = chatroom.get_room(self.rid, public=False)
        chatroom.participant(full, "claude").update(fields)
        chatroom.update_room(full)
        attention._SUMMARY_CACHE.clear()

    def state(self):
        it = self.item()
        return it and it["state"]

    def test_a_death_nobody_dealt_with_is_red(self):
        self.assertEqual(self.state(), "agent_gone")

    def test_a_done_task_is_not(self):
        self.patch(workflow="done")
        self.assertIsNone(self.state())

    def test_stopped_after_it_died_is_not(self):
        self.patch(stoppedAt=self.ended + 5)
        self.assertIsNone(self.state())

    def test_stopped_before_it_died_still_is(self):
        self.patch(stoppedAt=self.ended - 3600)
        self.assertEqual(self.state(), "agent_gone")

    def test_resumed_or_rotated_since_is_not(self):
        self.patch_part(resumedAt=self.ended + 5)
        self.assertIsNone(self.state())
        self.patch_part(resumedAt=0, rotatedAt=self.ended + 5)
        self.assertIsNone(self.state())

    def test_a_chat_message_alone_does_not_settle_it(self):
        # Writing to a dead agent brings it back (a resume: new terminal,
        # resumedAt); a message that did not is still waiting on a dead one.
        chatroom.post_message(self.rid, "user", "carry on please")
        attention._SUMMARY_CACHE.clear()
        self.assertEqual(self.state(), "agent_gone")

    def test_using_a_live_teammate_does_not_settle_it(self):
        full = chatroom.get_room(self.rid, public=False)
        full["participants"].append({"identity": "codex", "agent": "codex", "role": "reviewer", "kind": "agent",
                                     "ptyId": "pty-2"})
        chatroom.update_room(full)
        mate = types.SimpleNamespace(
            id="pty-2", alive=lambda: True, tail=lambda: "● ok\n❯ \n", last_output=time.time(),
            info=lambda: {"idleSeconds": 2}, last_submit=lambda: 0.0, death=lambda: None,
            last_input=self.ended + 30, meta={"room": self.rid, "identity": "codex"})
        sessions = {"pty-1": self.sess, "pty-2": mate}
        dashboard.ptyrun.get.side_effect = lambda pid: sessions.get(pid)
        chatroom.post_message(self.rid, "user", "go on", to="codex")
        attention._SUMMARY_CACHE.clear()
        self.assertEqual(self.state(), "agent_gone")
        self.assertIn("claude", self.item()["reason"])

    def test_the_persons_own_keystrokes_ended_it(self):
        self.died.update(exitCode=0, lastInput=self.ended - 5)     # /exit, Ctrl+C
        self.assertIsNone(self.state())
        self.died["lastInput"] = self.ended - 30     # typed a while before: it ended on its own
        self.assertEqual(self.state(), "agent_gone")

    def test_a_crash_right_after_a_prompt_stays_red(self):
        self.died.update(exitCode=1, lastInput=self.ended - 5)
        self.assertEqual(self.state(), "agent_gone")
        self.died.update(exitCode=None)              # status unknown: not shown to be theirs
        self.assertEqual(self.state(), "agent_gone")

    def test_a_kill_is_not(self):
        self.died["killed"] = True
        self.assertIsNone(self.state())


class DealtWith(unittest.TestCase):
    def test_rules(self):
        d = {"endedAt": 1000.0, "exitCode": 0}
        self.assertFalse(attention.dealt_with({}, {}, d))
        self.assertTrue(attention.dealt_with({"workflow": "done"}, {}, d))
        self.assertTrue(attention.dealt_with({"stoppedAt": 1000.0}, {}, d))
        self.assertTrue(attention.dealt_with({}, {"rotatedAt": 1001}, d))
        self.assertFalse(attention.dealt_with({"stoppedAt": 999.0}, {}, d))
        self.assertFalse(attention.dealt_with({}, {"resumedAt": 999.0}, d))
        self.assertTrue(attention.dealt_with({}, {}, {**d, "lastInput": 980.0}))
        self.assertFalse(attention.dealt_with({}, {}, {**d, "lastInput": 979.0}))
        self.assertFalse(attention.dealt_with({}, {}, {**d, "lastInput": 1001.0}))  # after: not its end
        self.assertFalse(attention.dealt_with({}, {}, {**d, "lastInput": 995.0, "exitCode": 1}))  # a crash


class PidReuse(unittest.TestCase):
    def backend(self, created):
        return types.SimpleNamespace(process_started=lambda pid: created)

    def test_a_process_younger_than_the_session_holds_a_reused_pid(self):
        started_ms = 1_000_000 * 1000
        self.assertTrue(shared.pid_reused(self.backend(1_000_000 + 3600), 42, started_ms))
        self.assertFalse(shared.pid_reused(self.backend(1_000_000 - 1), 42, started_ms))
        self.assertTrue(shared.pid_reused(self.backend(1_000_000 + 30), 42, started_ms))   # reused fast
        self.assertFalse(shared.pid_reused(self.backend(1_000_000 + 3), 42, started_ms))   # rounding

    def test_unknown_times_say_not_reused(self):
        self.assertFalse(shared.pid_reused(self.backend(None), 42, 1_000_000_000))
        self.assertFalse(shared.pid_reused(self.backend(5.0), 42, None))
        self.assertFalse(shared.pid_reused(self.backend(5.0), 42, "x"))

        def boom(pid):
            raise OSError("no access")
        self.assertFalse(shared.pid_reused(types.SimpleNamespace(process_started=boom), 42, 1000))

    def test_the_real_backend_dates_this_process(self):
        import os
        from backends import get_backend
        created = get_backend().process_started(os.getpid())
        if created is None:
            self.skipTest("this backend cannot date a process")
        self.assertLess(abs(created - time.time()), 24 * 3600)
        self.assertLessEqual(created, time.time())


if __name__ == "__main__":
    unittest.main()
