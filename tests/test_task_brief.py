"""#316: what an open board card says about a task — task_brief.py, the goal
on ensemble_create_task / ensemble_update_task, the state on ensemble_report,
the goal backfill, and the branch's commit times the stale mark is made from."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools
import quiet_reports
import task_brief


class _Handler:
    def __init__(self):
        self.reports: list = []

    def _ring(self, room_id, idents, wake, ways=None):
        return list(idents)

    def _ring_recipients(self, room_id, res):
        return res["recipients"]

    def _ring_report(self, po_room_id, result, task_id, title, reporter, kind, text):
        self.reports.append((po_room_id, kind))
        return (result or {}).get("recipients") or []


class Clean(unittest.TestCase):
    def test_short_text_is_kept(self):
        self.assertEqual(task_brief.clean("  Ship the  card. "), ("Ship the card.", False))

    def test_two_lines_at_most(self):
        self.assertEqual(task_brief.clean("one\n\ntwo\nthree"), ("one\ntwo", True))

    def test_long_text_is_cut_at_a_word(self):
        out, cut = task_brief.clean("word " * 100)
        self.assertTrue(cut)
        self.assertLessEqual(len(out), task_brief.TEXT_MAX)
        self.assertTrue(out.endswith("word…"), out)

    def test_not_text_is_refused(self):
        with self.assertRaises(ValueError):
            task_brief.clean(5)


class SpecSentence(unittest.TestCase):
    def test_first_plain_sentence(self):
        spec = "## Ask\n\n- **Make** the [board](http://x) readable. Then more.\n"
        self.assertEqual(task_brief.spec_sentence(spec), "Make the board readable.")

    def test_nothing_without_prose(self):
        self.assertEqual(task_brief.spec_sentence("# Head\n---\n| a | b |\n"), "")


class _Rooms(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        self.po_rid = None
        for p in (mock.patch.object(chatroom, "ROOMS_DIR", tmp / "rooms"),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", tmp / "hub"),
                  mock.patch.object(dashboard, "_room_is_live", return_value=True),
                  mock.patch.object(dashboard, "room_po_id", side_effect=lambda r, *a, **k: self.po_rid),
                  mock.patch.object(ensemble_tools, "_projects", side_effect=lambda: self.projects)):
            p.start()
            self.addCleanup(p.stop)
        (tmp / "hub").mkdir()
        quiet_reports.reset()
        self.addCleanup(quiet_reports.reset)
        self.po_rid = chatroom.create_room("Motors PO", [{"identity": "po", "agent": "claude",
                                                          "role": "ProductOwner"}])["id"]
        self.task_dir = tmp / "task"
        self.task_dir.mkdir()
        (self.task_dir / "task.json").write_text("{}", encoding="utf-8")
        self.rid = chatroom.create_room(
            "Motor spec", [{"identity": "claude", "agent": "claude", "role": "engineer"}])["id"]
        chatroom.patch_room(self.rid, no=7, spec="## Ask\nBuild the motor page. It lists motors.",
                            taskDir=str(self.task_dir), cwd=str(self.task_dir), mode="collab")
        self.projects = {"p1": {"id": "p1", "name": "Motors", "poRoomId": self.po_rid}}
        self.handler = _Handler()

    def room(self, rid=None) -> dict:
        return chatroom.get_room(rid or self.rid, public=False)

    def ctx(self, ident="claude") -> dict:
        full = self.room()
        return {"room": full, "identity": ident, "part": chatroom.participant(full, ident),
                "projectId": "p1"}

    def po_ctx(self) -> dict:
        po = self.room(self.po_rid)
        return {"room": po, "identity": "po", "part": chatroom.participant(po, "po"), "projectId": ""}

    def task_json(self) -> dict:
        return json.loads((self.task_dir / "task.json").read_text(encoding="utf-8"))


class Goal(_Rooms):
    def test_the_po_sets_and_clears_a_goal(self):
        res = ensemble_tools._update_task(self.po_ctx(), {"taskId": self.rid, "goal": "Motors on one page."},
                                          self.handler)
        self.assertTrue(res["ok"], res)
        r = self.room()
        self.assertEqual((r["goal"], r["goalSource"]), ("Motors on one page.", "po"))
        self.assertEqual(self.task_json()["goal"], "Motors on one page.")
        ensemble_tools._update_task(self.po_ctx(), {"taskId": self.rid, "goal": ""}, self.handler)
        self.assertEqual(self.room().get("goal"), "")
        self.assertEqual(task_brief.row_brief(self.room(), cold=True)["goalSource"], "spec")

    def test_a_long_goal_is_cut_and_said_so(self):
        res = ensemble_tools._update_task(self.po_ctx(), {"taskId": self.rid, "goal": "x " * 300},
                                          self.handler)
        self.assertLessEqual(len(self.room()["goal"]), task_brief.TEXT_MAX)
        self.assertIn(ensemble_tools._GOAL_CUT_NOTE, json.dumps(res))

    def test_an_owner_cannot_set_the_goal(self):
        with self.assertRaises(ensemble_tools.ToolError):
            ensemble_tools._update_task(self.ctx(), {"taskId": self.rid, "goal": "Mine."}, self.handler)
        self.assertFalse(self.room().get("goal"))

    def test_an_owner_cannot_clear_the_goal_with_null(self):
        task_brief.set_goal(self.room(), "The PO's goal.")
        with self.assertRaises(ensemble_tools.ToolError):
            ensemble_tools._update_task(self.ctx(), {"taskId": self.rid, "goal": None,
                                                     "workflow": "inreview"}, self.handler)
        self.assertEqual(self.room()["goal"], "The PO's goal.")

    def test_task_json_gets_goal_at(self):
        task_brief.set_goal(self.room(), "The PO's goal.")
        self.assertGreater(self.task_json()["goalAt"], 0)
        task_brief.set_goal(self.room(), "")
        self.assertTrue(task_brief.set_auto_goal(self.room(), "A model's goal."))
        self.assertEqual(self.task_json()["goalAt"], self.room()["goalAt"])

    def test_the_auto_goal_never_replaces_one(self):
        task_brief.set_goal(self.room(), "The PO's goal.")
        self.assertFalse(task_brief.set_auto_goal(self.room(), "A model's goal."))
        self.assertEqual((self.room()["goal"], self.room()["goalSource"]), ("The PO's goal.", "po"))

    def test_the_auto_goal_fills_an_empty_one(self):
        self.assertTrue(task_brief.set_auto_goal(self.room(), "A model's goal."))
        self.assertEqual((self.room()["goal"], self.room()["goalSource"]), ("A model's goal.", "auto"))
        self.assertEqual(self.task_json()["goalSource"], "auto")

    def test_without_a_goal_the_card_shows_the_spec(self):
        b = task_brief.row_brief(self.room(), cold=True)
        self.assertEqual((b["goal"], b["goalSource"]), ("Build the motor page.", "spec"))
        self.assertIsNone(b["state"])


class State(_Rooms):
    def report(self, **extra) -> dict:
        return ensemble_tools._report(self.ctx(), {"kind": "update", "text": "half done", **extra},
                                      self.handler)

    def test_a_report_stores_the_state_by_whom_and_when(self):
        t0 = time.time()
        self.report(state="The page lists motors; filters next.")
        st = self.room()["stateNote"]
        self.assertEqual((st["text"], st["by"]), ("The page lists motors; filters next.", "claude"))
        self.assertGreaterEqual(st["at"], t0)
        b = task_brief.row_brief(self.room(), cold=True)
        self.assertEqual(b["state"]["text"], st["text"])
        self.assertTrue(b["report"]["msg"], b)

    def test_a_report_without_state_keeps_the_last(self):
        self.report(state="First.")
        self.report()
        self.assertEqual(self.room()["stateNote"]["text"], "First.")

    def test_a_long_state_is_cut_and_said_so(self):
        res = self.report(state="y " * 300)
        self.assertLessEqual(len(self.room()["stateNote"]["text"]), task_brief.TEXT_MAX)
        self.assertEqual(res.get("stateNote"), ensemble_tools._GOAL_CUT_NOTE)

    def test_the_slim_room_keeps_goal_and_state(self):
        for k in ("goal", "goalSource", "goalAt", "stateNote"):
            self.assertIn(k, chatroom.SLIM_KEEP)


class Backfill(_Rooms):
    def setUp(self):
        super().setUp()
        self.other = chatroom.create_room("Gears", [{"identity": "claude", "agent": "claude"}])["id"]
        chatroom.patch_room(self.other, no=8, spec="Sort the gears.")
        p = mock.patch.object(dashboard, "load_projects",
                              return_value=[{"id": "p1", "poRoomId": self.po_rid}])
        p.start()
        self.addCleanup(p.stop)

    def test_candidates_skip_the_po_and_written_goals(self):
        task_brief.set_goal(self.room(self.other), "Already there.")
        ids = [r["id"] for r in task_brief.backfill_candidates()]
        self.assertEqual(ids, [self.rid])

    def test_old_done_tasks_are_left(self):
        po = {self.po_rid}
        now = time.time()
        r = {"id": "x", "no": 3, "spec": "Do.", "workflow": "done", "updatedAt": now - 30 * 86400}
        self.assertFalse(task_brief.needs_goal(r, po, now))
        r["updatedAt"] = now - 86400
        self.assertTrue(task_brief.needs_goal(r, po, now))

    def test_one_pass_writes_auto_goals_but_never_over_a_po_goal(self):
        def ask(batch):
            # The PO writes a goal while the model is thinking.
            task_brief.set_goal(self.room(self.other), "PO wrote this.")
            return {r["id"]: f"Goal of {r['title']}." for r in batch}
        res = task_brief.backfill(ask=ask, sleep=lambda s: None)
        self.assertEqual(res, {"written": 1, "failed": 0, "stopped": False})
        self.assertEqual(self.room()["goalSource"], "auto")
        self.assertEqual((self.room(self.other)["goal"], self.room(self.other)["goalSource"]),
                         ("PO wrote this.", "po"))

    def test_three_failed_calls_stop_the_pass(self):
        calls = []
        for i in range(20):
            rid = chatroom.create_room(f"T{i}", [{"identity": "claude", "agent": "claude"}])["id"]
            chatroom.patch_room(rid, no=100 + i, spec="Do it.")
        with mock.patch.object(task_brief, "BACKFILL_BATCH", 2):
            res = task_brief.backfill(ask=lambda b: calls.append(b) or None, sleep=lambda s: None)
        self.assertTrue(res["stopped"], res)
        self.assertEqual(len(calls), task_brief.BACKFILL_MAX_FAILS)
        self.assertFalse(self.room().get("goal"))

    def test_a_task_the_model_passed_over_is_not_asked_again_until_its_spec_changes(self):
        asked = []

        def ask(batch):
            asked.append([r["id"] for r in batch])
            return {self.rid: "Goal of the motors."}      # nothing for self.other
        task_brief.backfill(ask=ask, sleep=lambda s: None)
        self.assertEqual(self.room(self.other)["goalTried"]["spec"], task_brief.spec_key("Sort the gears."))
        self.assertIn("goalTried", chatroom.SLIM_KEEP)
        self.assertEqual([r["id"] for r in task_brief.backfill_candidates()], [])
        chatroom.patch_room(self.other, spec="Sort the gears by size.")
        self.assertEqual([r["id"] for r in task_brief.backfill_candidates()], [self.other])

    def test_an_empty_answer_counts_as_passed_over(self):
        task_brief.backfill(ask=lambda b: {r["id"]: "  " for r in b} | {"x": "y"}, sleep=lambda s: None)
        self.assertEqual(task_brief.backfill_candidates(), [])

    def test_a_failed_call_does_not_mark_anything(self):
        task_brief.backfill(ask=lambda b: None, sleep=lambda s: None)
        self.assertEqual(len(task_brief.backfill_candidates()), 2)

    def test_one_start_asks_about_at_most_the_cap(self):
        for i in range(task_brief.BACKFILL_MAX_TASKS + 5):
            rid = chatroom.create_room(f"T{i}", [{"identity": "claude", "agent": "claude"}])["id"]
            chatroom.patch_room(rid, no=100 + i, spec="Do it.")
        self.assertEqual(len(task_brief.backfill_candidates()), task_brief.BACKFILL_MAX_TASKS)

    def test_the_answer_must_be_the_json_asked_for(self):
        batch = {"t1": {"id": "room-a"}, "t2": {"id": "room-b"}}
        self.assertEqual(task_brief.parse_answer('x {"t1": "Goal A.", "t3": "?"} y', batch),
                         {"room-a": "Goal A."})
        self.assertIsNone(task_brief.parse_answer("no json here", batch))


class Commits(unittest.TestCase):
    """The branch's commits ahead of main, with their times (newest first)."""

    def git(self, *a, **env):
        subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True, encoding="utf-8",
                       env={**__import__("os").environ, **env})

    def test_counts_commits_ahead_with_their_times(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("checkout", "-q", "-b", "feat")
        # A rebase gives every commit the same new committer time; the
        # author times are the ones a card compares with.
        for i, when in enumerate(("1700000000", "1700000100", "1700000200")):
            self.git("commit", "-q", "--allow-empty", "-m", f"c{i}",
                     GIT_COMMITTER_DATE="@1800000000 +0000", GIT_AUTHOR_DATE=f"@{when} +0000")
        got = dashboard.branch_commits(str(self.repo), background=False)
        self.assertEqual(got["ahead"], 3, got)
        self.assertEqual(got["times"], [1700000200, 1700000100, 1700000000])


NODE = __import__("shutil").which("node")
BRIEF_JS = r"""
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
const agoSpan = () => '1m', WORKFLOW_COLS = [], workflowOf = () => '', REVIEW_WORDS = {};
const changesChipHtml = () => '', msgLinkHtml = () => '';
%s
const st = { text: 'Half', by: 'claude', at: 100 };
const card = (ahead, times) => cardBriefHtml({ brief: { goal: 'G', state: st }, commits: { ahead, times } });
const t20 = Array.from({ length: 20 }, (_, i) => 200 + i);
console.log(JSON.stringify({ exact: card(20, t20), more: card(25, t20), some: card(25, [300, 50]) }));
"""


@unittest.skipUnless(NODE, "node is not installed")
class StaleMark(unittest.TestCase):
    """'before the last 20+ commits' only when there are more than 20."""

    def test_plus_only_past_the_times_sent(self):
        index = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        i = index.index("function cardBriefHtml(")
        src = index[i:index.index("\n}\n", i) + 3]
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "brief.cjs"
            script.write_text(BRIEF_JS % src, encoding="utf-8")
            out = subprocess.run([NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertIn("before the last 20 commits", got["exact"])
        self.assertIn("before the last 20+ commits", got["more"])
        self.assertIn("before the last 1 commit<", got["some"])


if __name__ == "__main__":
    unittest.main()
