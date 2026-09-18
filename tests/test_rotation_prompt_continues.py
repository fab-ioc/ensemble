"""A fresh session carries on with what it inherited: its first prompt does not
end in "wait", and both it and the handover ask name the ``## Due`` section
(seen 2026-09-18: a fresh PO summarised, waited four hours, and a promised test
was never run)."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import dashboard
import rotation
from test_rotation_ask import _Base


class FirstPromptTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(rotation, "handover_path", return_value=Path("/home/p/PO-HANDOVER.md")),
            mock.patch.object(dashboard, "roadmap_path", return_value=Path("/home/p/ROADMAP.md")),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def po_prompt(self, spec="Set the project up. Create task A."):
        return rotation.first_prompt({"id": "p1", "name": "Trading"}, {"id": "room-po", "spec": spec},
                                     "sid-old", 152_000)

    def test_the_fresh_po_is_not_told_to_wait(self):
        text = self.po_prompt()
        self.assertTrue(text.startswith("[rotation] "))
        self.assertNotIn("and wait", text)
        self.assertNotRegex(text.rstrip(), r"\bwait\.?$")
        last = text.split("\n\n")[-1]
        self.assertIn("short summary of where things stand", last)
        self.assertIn("Carrying on with:", last)
        self.assertIn("in flight, due or promised", last)
        self.assertIn("promises to sam first", last)
        # The one thing it still waits for.
        self.assertIn("Wait only where the handover says the decision is sam's", last)

    def test_the_fresh_po_is_told_of_the_due_section_and_its_wake(self):
        text = self.po_prompt()
        self.assertIn("'## Due'", text)
        self.assertIn("[due] line", text)

    def test_the_spec_is_still_background_only(self):
        text = self.po_prompt()
        self.assertIn("do not repeat any step it asks for", text)
        self.assertLess(text.index("<original-brief>"), text.index("Carrying on with:"))
        self.assertNotIn("<original-brief>", self.po_prompt(spec=""))

    def test_a_rotated_owner_carries_on(self):
        for solo in (True, False):
            text = rotation.task_first_prompt({"id": "room-1", "title": "T"}, "sid-old", 210_000,
                                              Path("/t/TASK-HANDOVER.md"), solo)
            last = text.split("\n\n")[-1]
            self.assertIn("carry on with the next action", last)
            self.assertIn("in flight, due or promised", last)
            self.assertIn("do not stop after a summary", last)
            self.assertIn("'## Due'", last)
            self.assertNotRegex(text, r"(?i)\band wait\b")
            # It still never carries the spec as an instruction.
            self.assertIn("for reference only", text)


class AskNamesDueTests(_Base):
    def test_the_po_is_asked_for_a_due_section(self):
        self.check(self.subject("po"))
        ask = self.sess.typed[0]
        self.assertIn("'## Due' section", ask)
        self.assertIn("'- HH:MM — what'", ask)
        self.assertIn("'- MM-DD HH:MM — what'", ask)
        self.assertIn("promises to sam first", ask)
        self.assertIn("local time", ask)
        self.assertNotIn("\n", ask)             # one typed line

    def test_an_owner_is_asked_for_a_due_section(self):
        self.check(self.subject())
        ask = self.sess.typed[0]
        self.assertIn("'## Due' section", ask)
        self.assertIn("'- HH:MM — what'", ask)
        self.assertNotIn("promises to", ask)
        self.assertNotIn("\n", ask)

    def test_the_skill_says_the_same(self):
        skill = (Path(rotation.__file__).resolve().parent / "skills" / "ensemble" / "SKILL.md"
                 ).read_text(encoding="utf-8")
        po = skill[skill.index("## Running a project as its PO"):]
        self.assertIn("**Write what is due at a time under `## Due`.**", po)
        self.assertIn("- 15:40 — the paper-1 hand test", po)
        self.assertIn("Carrying on with:", po)
        self.assertIn("It\ndoes not summarise and wait.", po)


if __name__ == "__main__":
    unittest.main()
