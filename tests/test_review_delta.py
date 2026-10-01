"""A later review reads the change since the last one, not the whole branch
(#147): dashboard.review_git_context / review_brief / previous_review."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}


def git(root: str, *args: str) -> str:
    out = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                         encoding="utf-8", env=GIT_ENV, check=True)
    return out.stdout.strip()


class _Repo(unittest.TestCase):
    """main with one commit; a branch with two commits (a.txt, then b.txt)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = str(Path(self.temp.name) / "repo")
        os.makedirs(self.root)
        git(self.root, "init", "-q", "-b", "main")
        self.write("base.txt", "base\n")
        git(self.root, "add", "."); git(self.root, "commit", "-qm", "base")
        git(self.root, "checkout", "-qb", "sess/x")
        self.write("a.txt", "first\n")
        git(self.root, "add", "."); git(self.root, "commit", "-qm", "first change")
        self.first = git(self.root, "rev-parse", "--short", "HEAD")
        self.write("b.txt", "second\n")
        git(self.root, "add", "."); git(self.root, "commit", "-qm", "second change")
        self.room = {"id": "room-1", "title": "Delta", "spec": "Do it", "cwd": self.root,
                     "participants": [{"identity": "claude", "kind": "agent", "agent": "claude",
                                       "role": "engineer"},
                                      {"identity": "codex", "kind": "agent", "agent": "codex",
                                       "role": "reviewer"}],
                     "messages": []}
        self.part = self.room["participants"][1]
        self.msg = {"id": "m1", "from": "claude", "text": "please review"}

    def write(self, name: str, text: str) -> None:
        Path(self.root, name).write_text(text, encoding="utf-8")

    def brief(self, n: int, git_ctx: dict, log: str = "") -> str:
        with mock.patch.object(dashboard, "review_log_path", lambda r: Path(self.root) / "REVIEW-LOG.md"):
            return dashboard.review_brief(self.room, self.part, self.msg, n, git_ctx, log)


class FirstReview(_Repo):
    def test_round_one_inlines_the_whole_diff(self):
        ctx = dashboard.review_git_context(self.root)
        self.assertNotIn("since", ctx)
        brief = self.brief(1, ctx)
        self.assertIn("Diff command:", brief)
        self.assertIn("+first", brief)
        self.assertIn("+second", brief)
        self.assertNotIn("Since review", brief)
        self.assertNotIn("Last:", brief)

    def test_no_previous_review_means_no_since(self):
        ctx = dashboard.review_git_context(self.root, since={})
        self.assertNotIn("since", ctx)
        self.assertEqual(dashboard.previous_review({}), {})
        self.assertEqual(dashboard.previous_review({"review": {"n": 1, "head": "x"}}), {},
                         "a review still running is not a previous one")
        self.assertEqual(dashboard.previous_review({"review": {"n": 1, "head": "x", "endedAt": 1.0}})["head"], "x")
        self.assertEqual(dashboard.previous_review(
            {"reviews": [{"n": 1, "head": "x"}, {"n": 2, "head": "y"}], "review": {"n": 3}})["head"], "y")


class LaterReview(_Repo):
    def prev(self, head=None, branch="sess/x", n=1) -> dict:
        return {"n": n, "branch": branch, "head": head or self.first}

    def test_round_two_reads_only_the_change_since(self):
        self.write("c.txt", "uncommitted\n")
        ctx = dashboard.review_git_context(self.root, since=self.prev())
        self.assertEqual(ctx["since"], self.first)
        self.assertEqual(ctx["sinceReview"], 1)
        self.assertIn("second change", ctx["sinceCommits"])
        self.assertNotIn("first change", ctx["sinceCommits"])
        self.assertIn("+second", ctx["sinceDiff"])
        self.assertNotIn("+first", ctx["sinceDiff"])
        self.assertIn("+first", ctx["diff"], "the whole diff is still known")
        brief = self.brief(2, ctx)
        self.assertIn(f"**Since review 1**, which read this branch at `{self.first}`", brief)
        self.assertIn(f'git -C "{self.root}" diff {self.first}', brief)
        self.assertIn("+second", brief)
        self.assertNotIn("+first", brief, "the whole diff is not inlined")
        self.assertIn(f'read the whole diff (`git -C "{self.root}" diff {ctx["against"]}`)', brief)
        self.assertIn("Whole change, stat only:", brief)
        self.assertIn("a.txt", brief.split("Whole change, stat only:")[1])
        self.assertIn("c.txt", brief.split("Uncommitted:")[1].split("```")[1])

    def test_the_whole_diff_is_still_a_command_when_the_delta_is_big(self):
        self.write("big.txt", "x" * (dashboard._BRIEF_DIFF_MAX + 10) + "\n")
        git(self.root, "add", "."); git(self.root, "commit", "-qm", "big")
        ctx = dashboard.review_git_context(self.root, since=self.prev())
        brief = self.brief(2, ctx)
        self.assertIn("The diff since is", brief)
        self.assertNotIn("````diff", brief)

    def test_a_rebased_branch_falls_back_to_the_whole_diff(self):
        ctx = dashboard.review_git_context(self.root, since=self.prev(head="0123abc"))
        self.assertNotIn("since", ctx)
        self.assertIn("Diff command:", self.brief(2, ctx))

    def test_another_branch_is_ignored(self):
        ctx = dashboard.review_git_context(self.root, since=self.prev(branch="sess/other"))
        self.assertNotIn("since", ctx)

    def test_main_merged_in_since_falls_back_to_the_whole_diff(self):
        # The branch took main in after review 1: the change since would carry
        # main's own commits, so the reviewer gets the whole diff instead.
        git(self.root, "checkout", "-q", "main")
        self.write("m.txt", "on main\n")
        git(self.root, "add", "."); git(self.root, "commit", "-qm", "main moves")
        git(self.root, "checkout", "-q", "sess/x")
        git(self.root, "merge", "-q", "--no-edit", "main")
        ctx = dashboard.review_git_context(self.root, since=self.prev())
        self.assertNotIn("since", ctx)
        self.assertNotIn("+on main", ctx["diff"])

    def test_the_same_head_again_is_only_what_is_uncommitted(self):
        head = git(self.root, "rev-parse", "--short", "HEAD")
        self.write("b.txt", "second, edited\n")
        ctx = dashboard.review_git_context(self.root, since=self.prev(head=head, n=2))
        self.assertEqual(ctx["since"], head)
        self.assertEqual(ctx["sinceCommits"], "")
        self.assertIn("+second, edited", ctx["sinceDiff"])
        self.assertIn("Commits since:\n```\n(none)", self.brief(3, ctx))


class LastReviewLine(unittest.TestCase):
    LOG = ("# Review log — Delta\n\n## Review 1 — changes requested — 2026-10-01 10:00\n\n"
           "- Reviewer: codex (codex)\n- Summary: two bugs\n\nfindings\n\n"
           "## Review 2 — approved — 2026-10-01 11:00\n\n- Reviewer: codex (codex)\n"
           "- Summary: both fixed\n\nok\n")

    def test_the_last_entry_is_one_line(self):
        self.assertEqual(dashboard.last_review_line(self.LOG), "Review 2: approved — both fixed")
        self.assertEqual(dashboard.last_review_line(""), "")
        self.assertEqual(dashboard.last_review_line("## Review 3 — comments — now\n\nno summary\n"),
                         "Review 3: comments")

    def test_the_brief_leads_the_log_with_it(self):
        room = {"id": "room-1", "title": "Delta", "spec": "", "cwd": "", "messages": [],
                "participants": [{"identity": "codex", "kind": "agent", "agent": "codex",
                                  "role": "reviewer"}]}
        with mock.patch.object(dashboard, "review_log_path", lambda r: "REVIEW-LOG.md"):
            brief = dashboard.review_brief(room, room["participants"][0],
                                           {"id": "m", "from": "user", "text": "look"}, 3, {}, self.LOG)
        self.assertIn("Last: Review 2: approved — both fixed\n\n# Review log", brief)


if __name__ == "__main__":
    unittest.main()
