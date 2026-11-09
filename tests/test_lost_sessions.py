"""A seat whose conversation file is gone (GitHub issue 33, #321): it is
found before a resume (or read off the CLI's dying screen), rotated as at
the token limit from its handover, said once in the chat; Settings reads
and raises Claude Code's cleanupPeriodDays."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import lost_sessions
import rotation
from test_rotation_kind import _Base, _FakeLauncher, _FakePty, _snap

UUID = "7d49c2f0-b609-4c3b-b066-d2ee572504fe"


class Detection(unittest.TestCase):
    def test_no_session_id_is_not_gone(self):
        self.assertFalse(lost_sessions.conversation_gone({"agent": "claude"}))

    def test_a_non_uuid_id_is_resumed_as_before(self):
        with mock.patch.object(dashboard, "find_transcript", return_value=None):
            self.assertFalse(lost_sessions.conversation_gone(
                {"agent": "claude", "sessionId": "sess-claude"}))

    def test_claude_with_no_transcript_is_gone(self):
        with mock.patch.object(dashboard, "find_transcript", return_value=None):
            self.assertTrue(lost_sessions.conversation_gone(
                {"agent": "claude", "sessionId": UUID}))
        with mock.patch.object(dashboard, "find_transcript", return_value=Path("x.jsonl")):
            self.assertFalse(lost_sessions.conversation_gone(
                {"agent": "claude", "sessionId": UUID}))

    def test_codex_with_no_rollout_is_gone(self):
        with mock.patch.object(lost_sessions, "_codex_rollouts", return_value=[]):
            self.assertTrue(lost_sessions.conversation_gone(
                {"agent": "codex", "sessionId": UUID}))
        with mock.patch.object(lost_sessions, "_codex_rollouts", return_value=["r.jsonl"]):
            self.assertFalse(lost_sessions.conversation_gone(
                {"agent": "codex", "sessionId": UUID}))

    def test_marked_gone_on_screen_wins_over_the_disk(self):
        with mock.patch.object(dashboard, "find_transcript", return_value=Path("x.jsonl")):
            self.assertTrue(lost_sessions.conversation_gone(
                {"agent": "claude", "sessionId": "sess-x", "conversationGone": "sess-x"}))
            # A mark for an earlier id does not count.
            self.assertFalse(lost_sessions.conversation_gone(
                {"agent": "claude", "sessionId": UUID, "conversationGone": "older"}))

    def test_the_screen_line_of_each_cli(self):
        self.assertTrue(lost_sessions.screen_says_gone(
            f"\x1b[31mNo conversation found with session ID: {UUID}\x1b[0m\r\n"))
        self.assertTrue(lost_sessions.screen_says_gone(
            f"Error: no rollout found for thread id {UUID}"))
        self.assertFalse(lost_sessions.screen_says_gone("Welcome back! > "))
        self.assertFalse(lost_sessions.screen_says_gone(""))


class Notice(unittest.TestCase):
    def test_says_why_and_from_what(self):
        with mock.patch.object(lost_sessions, "cleanup_days", return_value=30):
            po = lost_sessions.notice("po", {"agent": "claude"}, "claude")
            owner = lost_sessions.notice("owner", {"agent": "codex"}, "codex", handover=False)
        self.assertIn("this PO", po)
        self.assertIn("after 30 days", po)
        self.assertIn("`PO-HANDOVER.md`", po)
        self.assertIn("codex", owner)
        self.assertIn("Codex rollout", owner)
        self.assertIn("the branch and the task's chat", owner)


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".claude" / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_keep_for_a_year_merges_and_keeps_other_keys(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"model": "opus", "cleanupPeriodDays": 30}),
                             encoding="utf-8")
        res = lost_sessions.keep_for_a_year(self.path)
        self.assertEqual((res["ok"], res["days"], res["changed"]), (True, 365, True))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"model": "opus", "cleanupPeriodDays": 365})
        again = lost_sessions.keep_for_a_year(self.path)
        self.assertEqual((again["ok"], again["changed"]), (True, False))

    def test_keep_for_a_year_writes_a_missing_file(self):
        res = lost_sessions.keep_for_a_year(self.path)
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"cleanupPeriodDays": 365})

    def test_keep_for_a_year_refuses_a_file_it_cannot_read(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        res = lost_sessions.keep_for_a_year(self.path)
        self.assertFalse(res["ok"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")

    def test_retention_counts_old_and_gone_seats(self):
        old = Path(self.tmp.name) / "old.jsonl"
        old.write_text("{}", encoding="utf-8")
        now = time.time()
        rooms = [
            {"participants": [{"kind": "agent", "agent": "claude", "sessionId": "a"},
                              {"kind": "agent", "agent": "claude", "sessionId": "b"},
                              {"kind": "agent", "agent": "codex", "sessionId": "c"}]},
            {"archived": True, "participants": [
                {"kind": "agent", "agent": "claude", "sessionId": "d"}]},
        ]
        found = {"a": old, "b": None, "d": None}
        with mock.patch.object(dashboard, "find_transcript",
                               side_effect=lambda sid, immediate=True: found[sid]), \
                mock.patch.object(lost_sessions, "_born", return_value=now - 40 * 86400):
            r = lost_sessions.retention(rooms, now=now, path=self.path)
        self.assertEqual((r["days"], r["set"], r["atRisk"], r["gone"], r["warn"]),
                         (30, False, 1, 1, True))
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"cleanupPeriodDays": 365}), encoding="utf-8")
        with mock.patch.object(dashboard, "find_transcript",
                               side_effect=lambda sid, immediate=True: found[sid]):
            r = lost_sessions.retention(rooms, now=now, path=self.path)
        # A year kept, but one seat's conversation is already gone: still said.
        self.assertEqual((r["days"], r["set"], r["atRisk"], r["gone"], r["warn"]),
                         (365, True, 0, 1, True))
        found["b"] = old
        with mock.patch.object(dashboard, "find_transcript",
                               side_effect=lambda sid, immediate=True: found[sid]):
            r = lost_sessions.retention(rooms, now=now, path=self.path)
        self.assertFalse(r["warn"])

    def test_a_long_period_still_warns_for_an_older_transcript(self):
        """Review 1: the warning is about the period being shorter than a live
        transcript's age, whatever the period."""
        old = Path(self.tmp.name) / "old.jsonl"
        old.write_text("{}", encoding="utf-8")
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"cleanupPeriodDays": 400}), encoding="utf-8")
        rooms = [{"participants": [{"kind": "agent", "agent": "claude", "sessionId": "a"}]}]
        now = time.time()
        for age, warn in ((500, True), (300, False)):
            with mock.patch.object(dashboard, "find_transcript", return_value=old), \
                    mock.patch.object(lost_sessions, "_born", return_value=now - age * 86400):
                r = lost_sessions.retention(rooms, now=now, path=self.path)
            self.assertEqual((r["days"], r["atRisk"], r["warn"]), (400, int(warn), warn), age)


class ClaudeConfigDir(unittest.TestCase):
    """PO finding 2: CLAUDE_CONFIG_DIR moves Claude's conversations and its
    settings; both the gone check and Keep follow it."""

    def test_follows_the_variable(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conf = Path(tmp.name) / "claude-conf"
        (conf / "projects" / "C--work").mkdir(parents=True)
        (conf / "projects" / "C--work" / f"{UUID}.jsonl").write_text("{}", encoding="utf-8")
        with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(conf)}), \
                mock.patch.object(dashboard, "find_transcript", return_value=None):
            self.assertEqual(lost_sessions.claude_settings_path(), conf / "settings.json")
            self.assertFalse(lost_sessions.conversation_gone({"agent": "claude", "sessionId": UUID}))
            other = "11111111-2222-3333-4444-555555555555"
            self.assertTrue(lost_sessions.conversation_gone({"agent": "claude", "sessionId": other}))
            res = lost_sessions.keep_for_a_year()
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads((conf / "settings.json").read_text(encoding="utf-8")),
                         {"cleanupPeriodDays": 365})
        with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": ""}):
            self.assertEqual(lost_sessions.claude_settings_path(),
                             Path.home() / ".claude" / "settings.json")


class WhereItIs(unittest.TestCase):
    """PO finding 5: the hub log and the doctor name the page's real place."""

    def test_one_name_for_the_place(self):
        root = Path(dashboard.__file__).resolve().parent
        self.assertIn("Settings > Preferences > Conversations",
                      (root / "ensemble.ps1").read_text(encoding="utf-8"))
        self.assertEqual(lost_sessions.WHERE, "Settings > Preferences > Conversations")
        src = (root / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn("{lost_sessions.WHERE} offers", src)
        self.assertNotIn("Settings › Agents offers the fix", src)
        page = (root / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="pref-retention-days"', page)
        self.assertIn(">Conversations<", page)


class KeepEndpoint(unittest.TestCase):
    """POST /api/claude-retention/keep: page-only; 200 only when it wrote."""

    def setUp(self):
        from test_points import http
        self.http = http
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".claude" / "settings.json"
        for p in (mock.patch.object(lost_sessions, "claude_settings_path", return_value=self.path),
                  mock.patch.object(lost_sessions, "retention", return_value={"days": 365})):
            p.start()
            self.addCleanup(p.stop)

    def test_refused_off_the_page(self):
        with mock.patch.object(dashboard.Handler, "_page_refusal", return_value="page only"):
            status, r = self.http("/api/claude-retention/keep", {})
        self.assertEqual((status, r["error"]), (403, "page_only"))
        self.assertFalse(self.path.exists())

    def test_writes_once_then_409(self):
        with mock.patch.object(dashboard.Handler, "_page_refusal", return_value=""):
            status, r = self.http("/api/claude-retention/keep", {})
            self.assertEqual((status, r["changed"]), (200, True))
            status, r = self.http("/api/claude-retention/keep", {})
            self.assertEqual((status, r["error"]), (409, "not_written"))
            self.assertIn("retention", r)
            self.path.write_text(json.dumps({"cleanupPeriodDays": 500}), encoding="utf-8")
            status, _ = self.http("/api/claude-retention/keep", {})
            self.assertEqual(status, 409)
            self.path.write_text("{bad", encoding="utf-8")
            status, r = self.http("/api/claude-retention/keep", {})
            self.assertEqual((status, r["ok"]), (409, False))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{bad")


class CodexCache(unittest.TestCase):
    """Review 1: a rollout deleted after the hub's 30 s lookup cache took it
    still counts as gone."""

    def test_a_cached_but_deleted_rollout_is_gone(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "sessions"
        day = root / "2026" / "11" / "09"
        day.mkdir(parents=True)
        f = day / f"rollout-2026-11-09T00-00-00-{UUID}.jsonl"
        f.write_text("{}", encoding="utf-8")
        cx = mock.Mock()
        cx.sessions_dir.return_value = root
        cx.rollouts_for_session.return_value = [f]
        self.addCleanup(dashboard._CODEX_ROLLOUT_PATHS.pop, UUID, None)
        dashboard._CODEX_ROLLOUT_PATHS[UUID] = (time.time(), [f])
        part = {"agent": "codex", "sessionId": UUID}
        with mock.patch.object(dashboard.agents, "get_agent", return_value=cx):
            self.assertFalse(lost_sessions.conversation_gone(part))
            f.unlink()
            self.assertTrue(lost_sessions.conversation_gone(part))


class OnDeath(_Base):
    """The fallback: a resumed terminal dies saying the conversation does not
    exist; the seat is marked and the room started again, once."""

    def setUp(self):
        super().setUp()
        lost_sessions._RETRIED.clear()
        self.room_ = self.room()
        self.rid = self.room_["id"]

    def rec(self, tail, sid="old-sid", killed=False, lived=3.0):
        now = time.time()
        return {"meta": {"room": self.rid, "identity": "claude", "resumeOf": sid},
                "tail": tail, "killed": killed, "startedAt": now - lived, "endedAt": now}

    def setUp_disk(self, on_disk):
        p = mock.patch.object(lost_sessions, "_claude_transcript",
                              return_value=Path("x.jsonl") if on_disk else None)
        p.start()
        self.patches.append(p)

    def test_a_long_run_or_a_conversation_on_disk_is_not_marked(self):
        """PO finding 1: a resumed session that worked for a while and printed
        the words, or whose transcript is on disk, is never marked gone."""
        line = "No conversation found with session ID: old-sid"
        with mock.patch.object(lost_sessions, "_retry") as retry:
            with mock.patch.object(lost_sessions, "_claude_transcript", return_value=None):
                self.assertFalse(lost_sessions.on_death(self.rec(line, lived=3 * 3600),
                                                        background=False))
                bad = self.rec(line)
                bad.pop("startedAt")
                self.assertFalse(lost_sessions.on_death(bad, background=False))
            with mock.patch.object(lost_sessions, "_claude_transcript",
                                   return_value=Path("x.jsonl")):
                self.assertFalse(lost_sessions.on_death(self.rec(line), background=False))
        retry.assert_not_called()
        part = chatroom.participant(chatroom.get_room(self.rid, public=False), "claude")
        self.assertNotIn("conversationGone", part)
        # The broad "Session … not found" is no longer read as the CLI's line.
        self.assertFalse(lost_sessions.screen_says_gone("Session abc not found in cache"))

    def test_marks_the_seat_and_retries_once(self):
        self.setUp_disk(False)
        with mock.patch.object(lost_sessions, "_retry") as retry:
            self.assertTrue(lost_sessions.on_death(
                self.rec("No conversation found with session ID: old-sid"), background=False))
            self.assertFalse(lost_sessions.on_death(
                self.rec("No conversation found with session ID: old-sid"), background=False))
        retry.assert_called_once()
        part = chatroom.participant(chatroom.get_room(self.rid, public=False), "claude")
        self.assertEqual(part["conversationGone"], "old-sid")
        self.assertTrue(lost_sessions.conversation_gone(part))

    def test_leaves_other_deaths_alone(self):
        self.setUp_disk(False)
        with mock.patch.object(lost_sessions, "_retry") as retry:
            self.assertFalse(lost_sessions.on_death(self.rec("Usage limit reached"),
                                                    background=False))
            self.assertFalse(lost_sessions.on_death(
                self.rec("No conversation found with session ID", killed=True),
                background=False))
            # A seat that has moved on to another session is not marked.
            self.assertFalse(lost_sessions.on_death(
                self.rec("No conversation found with session ID", sid="other"),
                background=False))
        retry.assert_not_called()
        part = chatroom.participant(chatroom.get_room(self.rid, public=False), "claude")
        self.assertNotIn("conversationGone", part)

    def test_the_retry_resumes_a_stopped_room(self):
        launcher = mock.Mock()
        with mock.patch.object(dashboard, "hub_launcher", return_value=launcher), \
                mock.patch.object(dashboard, "_room_is_live", return_value=False):
            lost_sessions._retry(self.rid, wait=0)
        launcher._resume_room.assert_called_once()
        self.assertTrue(launcher._resume_room.call_args.kwargs.get("quiet"))

    def test_the_retry_starts_dead_seats_of_a_live_team(self):
        launcher = mock.Mock()
        with mock.patch.object(dashboard, "hub_launcher", return_value=launcher), \
                mock.patch.object(dashboard, "_room_is_live", return_value=True):
            lost_sessions._retry(self.rid, wait=0)
        launcher._start_or_resume_room.assert_called_once()
        launcher._resume_room.assert_not_called()


class BeforeAResume(_Base):
    """Every start or resume of a room checks its seats first
    (Handler._rotate_lost_seats, called by _start_or_resume_room)."""

    def setUp(self):
        super().setUp()
        self.h = object.__new__(dashboard.Handler)
        self.room_ = self.room()
        p = mock.patch.object(dashboard, "load_projects", return_value=[])
        p.start()
        self.patches.append(p)

    def test_a_gone_seat_is_rotated_and_a_kept_one_left(self):
        gone = lambda part, cwd="": part["identity"] == "claude"
        with mock.patch.object(lost_sessions, "conversation_gone", side_effect=gone), \
                mock.patch.object(dashboard, "_pty_alive", return_value=False), \
                mock.patch.object(rotation, "rotate_gone",
                                  return_value={"result": "ok",
                                                "rotation": {"toSessionId": "n"}}) as rg:
            briefs = self.h._rotate_lost_seats(self.room_, None)
        self.assertEqual(briefs, {})
        rg.assert_called_once_with(self.room_["id"], "claude", project=None)

    def test_a_running_or_stopped_seat_is_not_checked(self):
        with mock.patch.object(lost_sessions, "conversation_gone", return_value=True) as cg, \
                mock.patch.object(dashboard, "_pty_alive", return_value=True), \
                mock.patch.object(rotation, "rotate_gone") as rg:
            self.h._rotate_lost_seats(self.room_, None)
        cg.assert_not_called()
        rg.assert_not_called()
        with mock.patch.object(lost_sessions, "conversation_gone", return_value=True), \
                mock.patch.object(dashboard, "_pty_alive", return_value=False), \
                mock.patch.object(rotation, "rotate_gone") as rg:
            self.h._rotate_lost_seats(self.room_, {"claude": "stopped", "codex": "stopped"})
        rg.assert_not_called()

    def test_a_rotation_that_raises_leaves_the_other_seats(self):
        """PO finding 3: one seat's failed rotation never stops the room."""
        calls = []

        def rg(rid, ident, project=None):
            calls.append(ident)
            if ident == "claude":
                raise RuntimeError("boom")
            return {"result": "ok", "rotation": {"toSessionId": "n"}}
        with mock.patch.object(lost_sessions, "conversation_gone", return_value=True), \
                mock.patch.object(dashboard, "_pty_alive", return_value=False), \
                mock.patch.object(chatroom, "is_on_mention", return_value=False), \
                mock.patch.object(rotation, "rotate_gone", side_effect=rg):
            briefs = self.h._rotate_lost_seats(self.room_, None)
        self.assertEqual(briefs, {})
        self.assertEqual(sorted(calls), ["claude", "codex"])

    def test_a_seat_no_rotation_takes_gets_a_brief(self):
        gone = lambda part, cwd="": part["identity"] == "claude"
        with mock.patch.object(lost_sessions, "conversation_gone", side_effect=gone), \
                mock.patch.object(dashboard, "_pty_alive", return_value=False), \
                mock.patch.object(rotation, "rotate_gone",
                                  return_value={"result": "no owner"}), \
                mock.patch.object(lost_sessions, "cleanup_days", return_value=30):
            briefs = self.h._rotate_lost_seats(self.room_, None)
        self.assertEqual(list(briefs), ["claude"])
        self.assertTrue(briefs["claude"].startswith("[recovery] "))
        self.assertIn("old-sid", briefs["claude"])


class RotateGone(_Base):
    """The seat is rotated on the token-limit path, from its handover."""

    def setUp(self):
        super().setUp()
        self.launcher = _FakeLauncher()
        for p in [
            mock.patch.object(dashboard, "hub_launcher", side_effect=lambda: self.launcher),
            mock.patch.object(dashboard.ptyrun, "kill"),
            mock.patch.object(dashboard.ptyrun, "get", return_value=None),
            mock.patch.object(rotation, "_await_death"),
            mock.patch.object(rotation, "_spawn", side_effect=lambda fn, *a: fn(*a)),
            mock.patch.object(dashboard.usage, "snapshot", return_value=_snap(40, 10)),
            mock.patch.object(lost_sessions, "cleanup_days", return_value=30),
        ]:
            p.start()
            self.patches.append(p)

    def owner_room(self, handover: bool):
        room = self.room()
        if handover:
            (Path(self.temp.name) / rotation.TASK_HANDOVER_NAME).write_text(
                "# handover\nNext: finish it.\n", encoding="utf-8")
        return room

    def test_an_owner_is_rotated_with_the_one_chat_line(self):
        room = self.owner_room(handover=True)
        res = rotation.rotate_gone(room["id"], "claude")
        self.assertIn("rotation", res, res)
        self.assertEqual(len(self.launcher.launched), 1)
        launched = self.launcher.launched[0]
        self.assertEqual(launched["identity"], "claude")
        after = chatroom.get_room(room["id"], public=False)
        part = chatroom.participant(after, "claude")
        self.assertNotEqual(part.get("sessionId"), "old-sid")
        notes = [m for m in after["messages"]
                 if "was removed from disk" in (m.get("text") or "")]
        self.assertEqual(len(notes), 1, [m.get("text") for m in after["messages"]])
        self.assertIn("`TASK-HANDOVER.md`", notes[0]["text"])
        self.assertIn("TASK-HANDOVER.md", launched["text"])

    def test_an_owner_with_no_handover_starts_from_the_branch(self):
        room = self.owner_room(handover=False)
        res = rotation.rotate_gone(room["id"], "claude")
        self.assertIn("rotation", res, res)
        after = chatroom.get_room(room["id"], public=False)
        notes = [m for m in after["messages"]
                 if "was removed from disk" in (m.get("text") or "")]
        self.assertEqual(len(notes), 1)
        self.assertIn("the branch and the task's chat", notes[0]["text"])
        # PO finding 4: with no handover it is given the spec as context and
        # the chat, and asked to report where things stand.
        text = self.launcher.launched[0]["text"]
        self.assertIn("There is no handover", text)
        self.assertIn("The task's spec (context only)", text)
        self.assertIn("The chat's last lines", text)
        self.assertIn("ensemble_report", text)
        self.assertNotIn("carry on with the next action", text)

    def test_task_context_carries_spec_branch_and_chat(self):
        room = {"spec": "Build the widget.", "messages": [
            {"from": "claude", "text": "half done"}]}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(dashboard, "_git_out",
                               side_effect=lambda cwd, *a, **k: "abc123 step one" if a[0] == "log"
                               else ""):
            ctx = lost_sessions.task_context(room, {"identity": "claude"}, "old", tmp.name)
        self.assertIn("Build the widget.", ctx)
        self.assertIn("abc123 step one", ctx)
        self.assertIn("claude: half done", ctx)

    def test_a_running_seat_is_not_rotated(self):
        room = self.owner_room(handover=True)
        with mock.patch.object(dashboard.ptyrun, "get", return_value=_FakePty(True)):
            res = rotation.rotate_gone(room["id"], "claude")
        self.assertNotIn("rotation", res)
        self.assertIn("running", res["result"])
        self.assertEqual(self.launcher.launched, [])


if __name__ == "__main__":
    unittest.main()
