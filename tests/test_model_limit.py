"""A task stopped by a model limit, and a silent owner (ED-159).

Measured on 2026-10-01 at 17:17: three trading owners (OP-159..161) and a
reviewer, seated with no model, ran Claude Code's default ``claude-fable-5-1``
and stopped on the CLI's line "You've reached your Fable limit. Run
/usage-credits to continue or switch models with /model." The transcript's
last entry was a synthetic assistant entry (``isApiErrorMessage``, stop_reason
"stop_sequence"), the stall check read it as mid-turn, attention matched no
rule, and every resume the next morning ended on the same line.
"""
from __future__ import annotations

import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agent_hooks
import attention
import chatroom
import dashboard
import ensemble_tools
import model_limit
import rotation
import stall
from test_ask_to_po import _Stall

FIXTURE = Path(__file__).parent / "fixtures" / "fable_limit_screen.txt"
LINE = "You've reached your Fable limit. Run /usage-credits to continue or switch models with /model."


def _limit_entry(ts="2026-10-01T15:17:48.678Z") -> dict:
    return {"type": "assistant", "isApiErrorMessage": True, "apiError": "model_requires_usage_credits",
            "error": "rate_limit", "timestamp": ts, "isSidechain": False,
            "message": {"role": "assistant", "model": "<synthetic>", "stop_reason": "stop_sequence",
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                        "content": [{"type": "text", "text": LINE}]}}


def _reply(text="Done.", model="claude-fable-5-1", ts="2026-10-01T15:10:00.000Z") -> dict:
    return {"type": "assistant", "timestamp": ts, "isSidechain": False,
            "message": {"role": "assistant", "model": model, "stop_reason": "end_turn",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                        "content": [{"type": "text", "text": text}]}}


def _user(text="carry on", ts="2026-10-01T15:17:47.000Z") -> dict:
    return {"type": "user", "timestamp": ts, "isSidechain": False,
            "message": {"role": "user", "content": text}}


class _State(unittest.TestCase):
    """A throwaway hub state folder, no Settings model, no Claude settings."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        model_limit._SEEN.clear()
        dashboard._SHOWN.clear()
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir),
                  mock.patch.object(dashboard, "load_settings", return_value={}),
                  mock.patch.object(dashboard.agent_models, "claude_own", return_value={"model": ""})):
            p.start()
            self.addCleanup(p.stop)

    def transcript(self, *entries, name="t.jsonl") -> Path:
        p = self.dir / name
        p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
        model_limit._SEEN.clear()
        return p


class TheLine(_State):
    def test_the_screen_line_marks_blocked_naming_the_model(self):
        hit = attention.find_block(FIXTURE.read_text(encoding="utf-8"))
        self.assertIsNotNone(hit)
        why, cause, quote = hit
        self.assertEqual(cause, "model_limit")
        self.assertTrue(why.startswith("is blocked: model limit (Fable)"), why)
        self.assertIn("reached your Fable limit", quote)

    def test_a_quoted_line_does_not(self):
        screen = ("● The trading tasks stopped on: \"You've reached your Fable limit. Run "
                  "/usage-credits to continue\" and I am looking into it.\n\n❯ \n")
        self.assertIsNone(attention.find_block(screen))

    def test_the_transcript_entry_is_the_limit_and_only_while_it_is_last(self):
        hit = model_limit.from_transcript(self.transcript(_reply(), _user(), _limit_entry()))
        self.assertEqual((hit["model"], hit["family"]), ("Fable", "fable"))
        self.assertEqual(hit["at"], model_limit._ts("2026-10-01T15:17:48.678Z"))
        # Got past it: a reply after the line.
        self.assertIsNone(model_limit.from_transcript(self.transcript(
            _limit_entry(), _user(), _reply(ts="2026-10-02T08:00:00Z"))))
        # An agent saying the words is no limit: only the CLI's own error entry.
        self.assertIsNone(model_limit.from_transcript(self.transcript(_reply(text=LINE))))

    def test_the_entry_ends_the_turn_and_counts_no_tokens(self):
        tr = rotation.read_transcript(self.transcript(_reply(), _user(), _limit_entry()))
        self.assertTrue(tr["turnOver"])
        self.assertNotEqual(tr["tokens"], 0)

    def test_the_hook_stop_learns_the_limit_from_the_transcript(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        owner = lambda pid: ("room-x", "claude")
        agent_hooks.record({"room": "room-x", "identity": "claude", "ptyId": "p1",
                            "event": {"hook_event_name": "Stop", "session_id": "s1"}}, owner)
        tpath = self.transcript(_reply(), _user(), _limit_entry())
        with mock.patch.object(dashboard, "find_transcript", return_value=tpath), \
                mock.patch.object(dashboard.chatroom, "get_room", return_value=None):
            dashboard._hook_turn_ended("p1", "room-x", "claude", "s1")
        st = agent_hooks.state_for("p1")
        self.assertEqual((st["state"], st["limit"]["model"]), ("idle", "Fable"))
        self.assertTrue(st["lastEventAt"])
        self.assertTrue(model_limit.limited("claude-fable-5-1", now=st["limit"]["at"] + 60))
        self.assertEqual(model_limit.cli_default(), "claude-fable-5-1")     # learnt from its reply
        # The next prompt drops it.
        agent_hooks.record({"room": "room-x", "identity": "claude", "ptyId": "p1",
                            "event": {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}}, owner)
        self.assertNotIn("limit", agent_hooks.state_for("p1"))


class Allocation(_State):
    def setUp(self):
        super().setUp()
        model_limit.learn_default("claude-fable-5-1")

    def limit(self, model="Fable"):
        model_limit.note({"model": model, "family": model_limit.family(model), "line": LINE,
                          "resetAt": 0, "at": time.time() - 60})

    def test_a_seat_with_no_model_shows_its_resolved_model(self):
        self.assertEqual(dashboard.seat_model_display("claude", ""), "fable (default)")
        self.assertEqual(dashboard.seat_model_display("claude", "opus"), "opus")
        room = {"participants": [{"kind": "agent", "identity": "claude", "agent": "claude", "model": "",
                                  "role": "engineer"}]}
        with mock.patch.object(dashboard.chatroom, "is_on_mention", return_value=False):
            self.assertEqual(ensemble_tools._agents_view(room)[0]["model"], "fable (default)")
        view = ensemble_tools._allocation_view({"chosen": [{"agent": "claude", "model": ""}], "usage": {}})
        self.assertEqual(view["chosen"][0]["model"], "fable (default)")

    def test_allocation_skips_a_limited_model_and_names_it(self):
        self.limit()
        self.assertEqual(dashboard.hub_launch_model("claude", "")[0], "opus")
        reason = dashboard._with_model_note("Preferred line-up kept.", [{"agent": "claude", "model": ""}])
        self.assertIn("Runs Opus: Fable (the default) hit its model limit", reason)
        self.assertEqual(dashboard.seat_model_display("claude", ""), "opus (Fable at its limit)")
        dashboard._SHOWN.clear()
        # Opus at its limit too: the next one along.
        self.limit("Opus")
        self.assertEqual(dashboard.hub_launch_model("claude", "")[0], "sonnet")

    def test_a_seat_naming_the_model_keeps_it_with_a_warning(self):
        self.limit()
        self.assertEqual(dashboard.hub_launch_model("claude", "fable")[0], "fable")
        self.assertIn("Warning: the seat names Fable", dashboard.model_note("claude", "fable"))

    def test_the_limit_clears_after_five_hours_or_at_its_reset(self):
        at = time.time()
        model_limit.note({"model": "Fable", "family": "fable", "line": LINE, "resetAt": 0, "at": at})
        self.assertTrue(model_limit.limited("fable", at + model_limit.CLEAR_AFTER_S - 1))
        self.assertIsNone(model_limit.limited("fable", at + model_limit.CLEAR_AFTER_S + 1))
        hit = model_limit.parse_line("You've reached your Opus limit · resets 3pm", at)
        self.assertEqual(time.localtime(hit["resetAt"])[3:5], (15, 0))


class ResumeEndingInTheLine(_Stall):
    """A resume (or a typed line) answered by the limit is reported to the PO
    once as blocked, never nudged as idle."""

    def setUp(self):
        super().setUp()
        model_limit._SEEN.clear()
        dashboard._SHOWN.clear()
        self.tpath = Path(dashboard.DASHBOARD_DIR) / "owner.jsonl"
        self.at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 30))
        self.tpath.write_text("".join(json.dumps(e) + "\n" for e in
                                      (_reply(), _user(), _limit_entry(self.at))), encoding="utf-8")
        dashboard.rotation._transcript_of.return_value = (self.tpath, rotation.read_transcript)
        dashboard.rotation._transcript_of.side_effect = \
            lambda part: (self.tpath, rotation.read_transcript) if part.get("identity") == "claude" \
            else (None, lambda p: {"turnOver": True})

    def told(self):
        return [m for m in chatroom.get_room(self.po_rid, public=False)["messages"]
                if (m.get("meta") or m).get("limitTask") == self.rid
                or "model limit" in (m.get("text") or "")]

    def test_reported_once_as_blocked_and_not_nudged(self):
        t = time.time()
        did = stall.tick(t)[self.rid]
        self.assertIn("blocked: model limit (Fable)", did)
        self.assertEqual(len(self.told()), 1)
        self.assertIn(LINE, self.told()[0]["text"])
        self.assertNotIn(stall.NUDGE, self.typed)
        self.assertEqual(stall.tick(t + 60).get(self.rid, ""), "")
        self.assertEqual(len(self.told()), 1)
        self.assertTrue(model_limit.limited("fable"))
        it = self.item()
        self.assertEqual((it["state"], it["cause"], it["model"]), ("blocked", "model_limit", "Fable"))
        self.assertIn("blocked: model limit (Fable)", it["reason"])

    def test_an_idle_po_is_woken_once(self):
        po_room = chatroom.get_room(self.po_rid, public=False)
        chatroom.participant(po_room, "po")["ptyId"] = "pty-po"
        chatroom.update_room(po_room)
        po_sess = types.SimpleNamespace(alive=lambda: True, last_submit=lambda: 0.0,
                                        info=lambda: {"idleSeconds": 30.0})
        owner_pty = dashboard.rotation._pty.side_effect
        dashboard.rotation._pty.side_effect = \
            lambda part: po_sess if part.get("ptyId") == "pty-po" else owner_pty(part)
        typed_into = []
        dashboard._type_input.side_effect = lambda s, t: typed_into.append((s, t)) or True
        t = time.time()
        self.assertIn("PO woken", stall.tick(t)[self.rid])
        self.assertEqual(stall.tick(t + 60).get(self.rid, ""), "")
        to_po = [txt for s, txt in typed_into if s is po_sess]
        self.assertEqual(len(to_po), 1)
        self.assertTrue(to_po[0].startswith("[digest] ") and "model limit (Fable)" in to_po[0])

    def test_a_reviewer_mid_review_ends_its_review_as_failed(self):
        full = self.room()
        chatroom.participant(full, "claude-2")["review"] = {"n": 2, "startedAt": time.time() - 300}
        chatroom.update_room(full)
        dashboard.rotation._transcript_of.side_effect = \
            lambda part: (self.tpath, rotation.read_transcript) if part.get("identity") == "claude-2" \
            else (None, lambda p: {"turnOver": False})
        with mock.patch.object(dashboard, "finish_review") as fin:
            did = stall.tick()[self.rid]
        fin.assert_called_once_with(self.rid, "claude-2", "failed")
        self.assertIn("ended as failed", did)
        self.assertIn("ended as failed", self.told()[0]["text"])


class SilentOwner(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        for p in (mock.patch.object(dashboard.rotation, "_transcript_of",
                                    return_value=(None, lambda p: {"turnOver": True})),):
            p.start()
            self.addCleanup(p.stop)

    def room(self, **kw):
        return {"id": "room-s", "status": "active", "mode": "collab", "launched": True,
                "owners": ["claude"], "workflow": "inprogress", "po": "room-po", "cwd": "",
                "openToHuman": None, "lastMessage": {"from": "user", "ts": 0, "rang": []},
                "participants": [{"identity": "claude", "kind": "agent", "agent": "claude",
                                  "resumedAt": self.now - 7200},
                                 {"identity": "codex", "kind": "agent", "agent": "codex"}], **kw}

    def ev(self, quiet, **kw):
        return {"ptyId": "p", "alive": True, "tail": "", "idleSeconds": quiet, "lastSubmit": 0.0,
                "death": None, "scan": {"block": None, "busy": False, "prompt": False},
                "claudeStatus": "idle", "claudeStatusAt": 0.0, "hook": None,
                "lastOutput": self.now - quiet, "limit": None, **kw}

    def classify(self, room, ev):
        part = {"identity": "claude", "agent": "claude", "resumedAt": self.now - 7200}
        return attention._classify_agent(room, part, ev, 900, self.now)

    def test_the_threshold_is_thirty_minutes(self):
        self.assertEqual(stall.SILENT_S, 1800)

    def test_a_silent_owner_is_flagged(self):
        hit = self.classify(self.room(), self.ev(stall.SILENT_S + 60))
        self.assertEqual((hit[0], hit[2]["cause"]), ("stalled", "silent"))
        self.assertIn("produced nothing for 31 min", hit[1])
        self.assertIsNone(self.classify(self.room(), self.ev(stall.SILENT_S - 60)))

    def test_a_hook_event_or_a_commit_is_a_sign_of_life(self):
        q = stall.SILENT_S + 60
        self.assertIsNone(self.classify(self.room(), self.ev(q, hook={"lastEventAt": self.now - 5})))
        with mock.patch.object(stall, "commit_at", return_value=self.now - 5):
            self.assertIsNone(self.classify(self.room(), self.ev(q)))

    def test_a_suite_run_is_not(self):
        self.assertIsNone(self.classify(self.room(), self.ev(stall.SILENT_S + 60, claudeStatus="shell")))

    def test_waiting_on_a_reviewer_is_not(self):
        room = self.room()
        room["participants"][1]["review"] = {"startedAt": self.now - 3000}
        self.assertIsNone(self.classify(room, self.ev(stall.SILENT_S + 60)))
        handed = self.room(lastMessage={"from": "claude", "ts": self.now - 3000, "rang": ["codex"]})
        self.assertIsNone(self.classify(handed, self.ev(stall.SILENT_S + 60)))

    def test_waiting_on_the_po_done_or_paused_is_not(self):
        q = stall.SILENT_S + 60
        for room in (self.room(openToHuman={"kind": "question", "to": "po", "from": "claude"}),
                     self.room(workflow="done"), self.room(status="paused")):
            self.assertIsNone(self.classify(room, self.ev(q)))


class SilentOwnerTellsThePo(_Stall):
    def test_the_po_is_told_once_per_silent_episode(self):
        self.sess.last_output = time.time() - stall.SILENT_S - 120
        self.idle = 5.0                     # not idle by the nudge's clock: only the backstop
        self.sess.info = lambda: {"idleSeconds": self.idle}
        full = self.room()
        chatroom.participant(full, "claude")["resumedAt"] = time.time() - stall.SILENT_S - 120
        full["messages"] = []
        chatroom.update_room(full)
        with mock.patch.object(dashboard.rotation, "_idle", return_value=False):
            t = time.time()
            self.assertIn("PO told (silent)", stall.tick(t)[self.rid])
            self.assertEqual(stall.tick(t + 60).get(self.rid, ""), "")
        silent = [m for m in chatroom.get_room(self.po_rid, public=False)["messages"]
                  if "silent since" in (m.get("text") or "")]
        self.assertEqual(len(silent), 1)


if __name__ == "__main__":
    unittest.main()
