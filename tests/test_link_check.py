"""The hub's link check (#433, GitHub issue 74): what an agent posts for the
person to read is checked, once posted, for files and folders it names that
exist and are not written as a link; the tool's answer carries a note that
says the link to write. Nothing is ever refused or rewritten.

link_check.unlinked over a temporary tree with the hub's own resolver, and
dashboard.link_check_content as _mcp_tool_call appends it.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import file_refs  # noqa: E402
import link_check  # noqa: E402


class Unlinked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        docs = self.root / "Ensemble Dashboard" / "Documents"
        (docs / "#433 Plans").mkdir(parents=True)
        (docs / "#433 Clickable links.md").write_text("x")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x\n" * 50)
        (self.root / "notes (old).md").write_text("x")
        (self.root / "My Folder" / "notes").mkdir(parents=True)
        self.docs = docs

    def tearDown(self):
        self.tmp.cleanup()

    def found(self, text):
        # As the hub's: the room's folders and its project's Documents folder.
        bases = [str(self.root), str(self.root / "Ensemble Dashboard"), str(self.docs)]
        res = lambda raw: file_refs.resolve(raw, bases, dirs=True, search=bases)[0]  # noqa: E731
        return link_check.unlinked(text, res)

    def test_each_unlinked_form_is_found(self):
        abs_md = str(self.docs / "#433 Clickable links.md")
        cases = {
            "see src/app.py now": "src/app.py",
            "see `src/app.py:12` now": "src/app.py:12",
            "the file app.py is done": "app.py",
            f"wrote {abs_md} today": abs_md,
            "the folder Documents/#433 Plans/ holds them": "Documents/#433 Plans/",
            "see Documents/#433 Clickable links.md, then": "Documents/#433 Clickable links.md",
            f"`{self.docs}`": str(self.docs),
            # Bare spaced names in running text: a file name ending in an extension, a task folder.
            "see #433 Clickable links.md now": "#433 Clickable links.md",
            "the folder #433 Plans holds them": "#433 Plans",
            "#433 Plans are ready": "#433 Plans",
            # A relative path whose first folder has a space.
            "My Folder/notes/": "My Folder/notes/",
            "see My Folder/notes/ now": "My Folder/notes/",
            "Open My Folder/notes.": "My Folder/notes",
        }
        for text, mention in cases.items():
            with self.subTest(text=text):
                got = self.found(text)
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0]["mention"].endswith(mention) or mention.endswith(got[0]["mention"]), got)
                self.assertTrue(got[0]["link"].startswith("["), got)
                self.assertIn(str(self.root), got[0]["link"])

    def test_the_link_to_write(self):
        got = self.found("see `src/app.py:12`")
        self.assertEqual(got[0]["link"], f"[app.py:12]({self.root / 'src' / 'app.py'}:12)")
        # A bracket in the path would end the link: the target goes in <...>.
        got = self.found("see `notes (old).md`")
        self.assertEqual([g["link"] for g in got], [f"[notes (old).md](<{self.root / 'notes (old).md'}>)"])

    def test_a_link_already_written_is_quiet(self):
        abs_py = self.root / "src" / "app.py"
        for text in (f"[app.py]({abs_py})", f"[app.py](<{abs_py}>)", f"see [the plan]({self.docs}/#433 Clickable links.md)",
                     "```\nsrc/app.py\n```", f"file://{abs_py}"):
            with self.subTest(text=text):
                self.assertEqual(self.found(text), [])

    def test_what_names_nothing_is_quiet(self):
        for text in ("and/or 1/2 now", "`git status`", "missing.md and docs/nothing.py", "e.g. `x = a/b`",
                     "#433 is done", "#12 Fix the thing", "see #433 Missing plan.md now",
                     "see Your Folder/notes/ now", "Open the My Folder/nothing/ now"):
            with self.subTest(text=text):
                self.assertEqual(self.found(text), [])

    def test_one_entry_per_thing(self):
        got = self.found("src/app.py, then `src/app.py` and app.py again")
        self.assertEqual(len(got), 1, got)

    def test_warning(self):
        self.assertEqual(link_check.warning([]), "")
        w = link_check.warning(self.found("see src/app.py"))
        self.assertIn("Link check", w)
        self.assertIn("→ [app.py](", w)
        many = [{"mention": f"m{i}", "path": f"/p{i}", "link": f"[m{i}](/p{i})"} for i in range(15)]
        self.assertIn("and 3 more", link_check.warning(many))

    def test_a_resolver_that_fails_is_quiet(self):
        def boom(raw):
            raise OSError("nope")
        self.assertEqual(link_check.unlinked("see src/app.py", boom), [])


class Linear(unittest.TestCase):
    """A long line of text that almost names a path is read in linear time:
    the spaced form may start at any word, so it scans a bounded run."""

    SHAPES = ["/A b", "Documents/#1 A b ", "a/A b/", "A b ", "x/A b c.md ", "b/", "A/ ",
              "/a ", "(/a", "/a(", "<a x>", "[a](", "[", "`a ", "#1 A b ", "A b c.md ", "C:\\a ", "~/a ",
              "A b/", "A b/c ", "A B C d/e "]

    def test_long_lines_are_fast(self):
        # The whole check, readings and all, with a resolver that finds nothing (the slowest case).
        import time
        for u in self.SHAPES:
            with self.subTest(shape=u):
                line = u * (50000 // len(u))
                t = time.perf_counter()
                link_check.unlinked(line, lambda raw: None)
                self.assertLess(time.perf_counter() - t, 0.5)

    def test_long_tokens_without_a_separator_are_fast(self):
        # A run of name characters with no slash is tried from almost every
        # character: unbounded, each try scanned to the token's end (6 to 27 s
        # on the hub's request thread before the runs were bounded).
        import time
        csv = ",".join(f"a{i}=b+c&d" for i in range(5000))[:40000]
        for line in ("+a" * 20000, "=" * 20000, csv, "(=" * 20000, "a" * 50000 + ".md", "x-" * 20000):
            with self.subTest(line=line[:12]):
                t = time.perf_counter()
                link_check.unlinked(line, lambda raw: None)
                self.assertLess(time.perf_counter() - t, 0.5)

    def test_only_the_start_of_a_long_text_is_read(self):
        seen = []
        link_check.unlinked("see src/app.py " + "x " * 20000 + "then docs/late.md", lambda raw: seen.append(raw))
        self.assertIn("src/app.py", seen)
        self.assertNotIn("docs/late.md", seen)


class ToolAnswer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "plan.md").write_text("x")
        p = mock.patch.object(dashboard, "_file_ref_places", return_value=([str(self.root)], [str(self.root)]))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_each_posting_tool_is_checked(self):
        for name, args in (("chat_send", {"message": "see plan.md"}),
                           ("ensemble_report", {"kind": "update", "text": "see plan.md"}),
                           ("ensemble_message_po", {"text": "see plan.md"}),
                           ("ensemble_ask", {"context": "see plan.md", "questions": [{"question": "ok?"}]}),
                           ("ensemble_ask", {"context": "x", "questions": [{"question": "ok?", "options": [{"label": "plan.md"}]}]}),
                           ("ensemble_ask", {"context": "x", "questions": [{"question": "ok?", "options": [
                               {"label": "A", "detail": "writes plan.md"}, "bad"]}]})):
            with self.subTest(name=name, args=args):
                c = dashboard.link_check_content(name, args, "room-00000000")
                self.assertEqual(len(c), 1, c)
                self.assertEqual(c[0]["type"], "text")
                self.assertIn(f"[plan.md]({self.root / 'plan.md'})", c[0]["text"])

    def test_nothing_to_say(self):
        self.assertEqual(dashboard.link_check_content("chat_send", {"message": f"[plan.md]({self.root / 'plan.md'})"}, ""), [])
        self.assertEqual(dashboard.link_check_content("chat_read", {}, ""), [])
        self.assertEqual(dashboard.link_check_content("chat_send", {"message": None}, ""), [])
        self.assertEqual(dashboard.link_check_content("ensemble_ask", {"questions": "bad"}, ""), [])

    def test_an_ask_that_posted_nothing_gets_no_note(self):
        # A retry answers with the first call's result, and a question an open
        # card asks is not posted again: no "posted as you wrote it" note.
        import json
        args = {"context": "see plan.md", "questions": [{"question": "ok?"}]}
        for answer in ({"ok": True, "posted": True, "repeated": True}, {"ok": True, "posted": False}):
            with self.subTest(answer=answer):
                self.assertEqual(dashboard.link_check_content("ensemble_ask", args, "", json.dumps(answer)), [])
        posted = dashboard.link_check_content("ensemble_ask", args, "", json.dumps({"ok": True, "posted": True}))
        self.assertEqual(len(posted), 1, posted)
        self.assertIn("posted as you wrote it", posted[0]["text"])
        # another tool's answer is not read for it
        self.assertEqual(len(dashboard.link_check_content("chat_send", {"message": "see plan.md"}, "", '{"posted": false}')), 1)

    def test_a_failing_check_never_fails_the_send(self):
        with mock.patch.object(dashboard, "unlinked_mentions", side_effect=RuntimeError("x")):
            self.assertEqual(dashboard.link_check_content("chat_send", {"message": "see plan.md"}, ""), [])


class DirOfRef(unittest.TestCase):
    def test_a_relative_folder_is_found_in_the_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Documents" / "#9 Plans").mkdir(parents=True)
            (Path(tmp) / "a.md").write_text("x")
            with mock.patch.object(dashboard, "_file_ref_places", return_value=([tmp], [tmp])):
                self.assertEqual(dashboard.dir_of_ref("Documents/#9 Plans/", "room-00000000"),
                                 str(Path(tmp) / "Documents" / "#9 Plans"))
                self.assertEqual(dashboard.dir_of_ref("a.md", "room-00000000"), "a.md")       # a file: as written
                self.assertEqual(dashboard.dir_of_ref("nope/", "room-00000000"), "nope/")
                self.assertEqual(dashboard.dir_of_ref("/abs/x", "room-00000000"), "/abs/x")


if __name__ == "__main__":
    unittest.main()
