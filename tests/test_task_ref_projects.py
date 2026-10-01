"""Which project a bare task number is read in (#156, the CEO's P112).

The opTen PO wrote "Dock released #27 as v0.11.0" and the chip linked opTen's
#27. Now a bare number right after another project's name is that project's
(task_numbers.read_project), the hub's [ref] line under a message follows
the same rule and writes nothing for a number that another project named in
its sentence has too (dashboard.TaskLookup), a PO's message to another PO
goes out with its own numbers written as KEY-N (task_numbers.qualify_text,
po_messages), and the pages get the project list (/api/task/projects). The
page side: tests/test_task_refs.py (the same rule in TaskCard.refsIn) and
tests/test_task_ref_project_page.py (the chips in Chrome)."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import chatroom
import dashboard
import message_refs as mr
import task_numbers as tn
from tests.test_task_numbers import Hub

PROJECTS = tn.project_names(
    [{"id": "dock", "key": "D", "name": "Dock"}, {"id": "ed", "key": "ED", "name": "Ensemble Dashboard"},
     {"id": "op", "key": "OP", "name": "OPtionTradingENgine"}, {"id": "st", "key": "S", "name": "Strats"},
     {"id": "m", "key": "M", "name": "Motors"}, {"id": "m2", "key": "M2", "name": "Music"}],
    {"dock": "Dock PO", "ed": "claude-dashboard windows port iterm2 to windows terminal", "op": "opten",
     "st": "Deeper understanding of my option trading strategies", "m": "Motors"})
OPTEN = {"projects": PROJECTS, "own": "op", "nouns": ["initiative", "initiatives"]}
REAL = "Re P230: no wait was needed. Dock released #27 as v0.11.0 while our task was running, so it's in."


def read(text: str, ctx: dict = OPTEN) -> list[tuple]:
    return [(r["token"], r["project"], r["how"], r["others"]) for r in tn.all_text_refs(text, ctx)]


class Names(unittest.TestCase):
    def test_what_a_chat_calls_a_project(self):
        self.assertEqual(tn.aliases("Dock", "Dock PO"), ["Dock"])
        self.assertEqual(tn.aliases("Ensemble Dashboard", "claude-dashboard windows port iterm2 to windows terminal"),
                         ["Ensemble Dashboard", "Ensemble"], "the first word of a long name; a PO chat titled with a sentence adds nothing")
        self.assertEqual(tn.aliases("OPtionTradingENgine", "opten"), ["OPtionTradingENgine", "opten"], "the PO chat's title")
        self.assertEqual(tn.aliases("Motors", "Motors"), ["Motors"])
        self.assertEqual(tn.aliases("Motors", "Motors product owner"), ["Motors"])
        self.assertEqual(tn.aliases("Strats", "Deeper understanding of my option trading strategies"), ["Strats"])
        self.assertEqual(tn.aliases("Air Co", ""), ["Air Co"], "a first word under four letters is no alias")
        self.assertEqual(tn.aliases("", ""), [])

    def test_a_word_two_projects_answer_to_names_neither(self):
        rows = tn.project_names([{"id": "a", "key": "A", "name": "Air Fleet"}, {"id": "b", "key": "B", "name": "Fleet"}], {"a": "Fleet"})
        self.assertEqual([r["aliases"] for r in rows], [["Air Fleet"], []])
        self.assertEqual([p["aliases"] for p in PROJECTS], [["Dock"], ["Ensemble Dashboard", "Ensemble"], ["OPtionTradingENgine", "opten"],
                                                            ["Strats"], ["Motors"], ["Music"]])


class Rules(unittest.TestCase):
    def test_the_real_sentence_is_docks(self):
        self.assertEqual(read(REAL), [("#27", "dock", "name", [])])
        self.assertEqual(read("Dock's #27 (`setTitle` and `bodyAttrs`) is running in the Dock project."), [("#27", "dock", "name", [])])
        # The CEO's paste: the chip's text broke the line between the name and the number.
        self.assertEqual(read("Opten PO is posting something like this \nDock released \n#27\n27. Amend a tranche by hand\nDone\n as v0.11.0"),
                         [("#27", "dock", "name", [])])

    def test_a_name_right_before_the_number(self):
        for text in ("Dock #27", "Dock's #27", "Dock’s #27", "the Dock project's #27", "the Dock initiative’s #27", "project Dock #27",
                     "in Dock: #27", "Dock, #27", "Dock — #27", "Dock task #27", "Dock released #27", "Dock tagged #27",
                     "Dock v0.11.0 (#27)", "Dock **v0.11.0** (#27)", "Dock v0.11.0 with #27", "Dock 0.11.0 as #27", "DOCK #27"):
            with self.subTest(text=text):
                self.assertEqual(read(text), [("#27", "dock", "name", [])])

    def test_a_name_earlier_in_the_sentence_is_only_a_weak_one(self):
        # Measured on seven days of real PO chats: such a name is the task's
        # title ("Ensemble's 8 needs (#4)") or whom something was told
        # ("Answered Ensemble about #11") far more often than the number's project.
        self.assertEqual(read("Answered Ensemble about #11"), [("#11", "op", "", ["ed"])])
        self.assertEqual(read("Ensemble's 8 needs (#4) has 6 new commits"), [("#4", "op", "", ["ed"])])
        self.assertEqual(read("tell Ensemble that its #148 can switch to Dock's menu"), [("#148", "op", "", ["ed"])])
        self.assertEqual(read("v0.8.0 is the latest tagged Dock, so #149 is on it"), [("#149", "op", "", ["dock"])])
        self.assertEqual(read("told Strats and Dock that #9 is done"), [("#9", "op", "", ["st", "dock"])], "each once, in order")
        self.assertEqual(read("report from task #4 *Ensemble's 8 needs*"), [("#4", "op", "", [])], "a name after the number says nothing")
        self.assertEqual(read("opten's #5 and Ensemble's #6"), [("#5", "op", "name", []), ("#6", "ed", "name", [])], "the own project's name counts as a name too")

    def test_a_sentence_ends_where_a_reader_would_say(self):
        self.assertEqual(read("Dock is next. #27 is ours."), [("#27", "op", "", [])], "a full stop")
        self.assertEqual(read("Dock v0.11.0 is out! #27 next? Strats"), [("#27", "op", "", [])])
        self.assertEqual(read("Dock v0.11.0 is out\n\n#27 is next"), [("#27", "op", "", [])], "a blank line")
        self.assertEqual(read("- Dock v0.11.0 is out\n- #27 is next"), [("#27", "op", "", [])], "a list item")
        self.assertEqual(read("## Dock\n#27 is next"), [("#27", "op", "", [])], "a heading")
        self.assertEqual(read("| Dock | #27 | Strats #4 |"), [("#27", "op", "", []), ("#4", "st", "name", [])], "a table cell")
        self.assertEqual(read("Dock v0.11.0 is out\n#27 is next"), [("#27", "op", "", ["dock"])], "a line break alone is not an end")
        self.assertEqual(read("Dock PO's @codex@27"), [("@codex@27", "op", "", ["dock"])])

    def test_what_is_not_read(self):
        self.assertEqual(read("PR #27 in Dock"), [], "someone else's number")
        self.assertEqual(read("`Dock #27` in code and ```\nDock #28\n```"), [])
        self.assertEqual(read("```\nDock #1\n```\nDock #2 after the fence."), [("#2", "dock", "name", [])], "the code keeps its length: offsets hold")
        self.assertEqual(read("#fff is a colour; ## 27 a heading; page#27 a fragment"), [])
        self.assertEqual(read("D-27 and #D-27 and #ZZ-9"), [("D-27", "dock", "key", []), ("#D-27", "dock", "key", []), ("#ZZ-9", "", "key", [])])
        self.assertEqual(read("UTF-8, ISO-8601 and SHA-256 are not tasks; ZZ-9 is nobody's"), [], "a bare key no project has")
        self.assertEqual([r["token"] for r in tn.all_text_refs("ZZ-9 and #ZZ-9")], ["ZZ-9", "#ZZ-9"], "without a context the hub decides")
        self.assertEqual(read("Dock #27", {"projects": [], "own": "op"}), [("#27", "op", "", [])], "no names: the own project")
        self.assertEqual(tn.all_text_refs("Dock #27")[0]["project"], "", "no context: nothing read")
        self.assertEqual([r["start"] for r in tn.all_text_refs("ab #1 cd #2")], [3, 9])
        self.assertEqual([r["no"] for r in tn.find_text_refs("Dock #27, #27, Dock's #27 and #27", OPTEN)], [27, 27], "each task once: Dock's and ours")


class Rewrite(unittest.TestCase):
    """A PO's message to another PO: its own numbers go out as KEY-N."""

    def setUp(self):
        self.have = {("op", 27), ("op", 154), ("dock", 27), ("ed", 5)}
        self.ctx = {"projects": PROJECTS, "own": "op"}
        self.q = lambda t: tn.qualify_text(t, self.ctx, lambda pid, no: (pid, no) in self.have)

    def test_own_numbers_are_written_in_full(self):
        self.assertEqual(self.q("Needs 15 and 16 are drafted as #27; #154 follows."), "Needs 15 and 16 are drafted as OP-27; OP-154 follows.")
        self.assertEqual(self.q("opten's #27 and opten #154"), "opten's OP-27 and opten OP-154", "named as our own: ours")
        self.assertEqual(self.q("Answered Ensemble about #27"), "Answered Ensemble about OP-27", "a weak name does not stop the rewrite: the sender's bare number is its own")

    def test_what_stays(self):
        self.assertEqual(self.q("Dock released #27 as v0.11.0"), "Dock released #27 as v0.11.0", "another project's task, which it has")
        self.assertEqual(self.q("Dock #99 and #99 are nobody's"), "Dock #99 and #99 are nobody's")
        self.assertEqual(self.q("Ensemble #27 is ours"), "Ensemble #27 is ours", "named another project's: the sender's words stay, even when that project has no such task")
        self.assertEqual(self.q("`#27` and ```\n#27\n``` and @codex@27 and OP-27 and D-27 and #D-27"), "`#27` and ```\n#27\n``` and @codex@27 and OP-27 and D-27 and #D-27")
        self.assertEqual(self.q("PR #27 and issue #154"), "PR #27 and issue #154")
        self.assertEqual(self.q("#fff #112233"), "#fff #112233")
        self.assertEqual(tn.qualify_text("#27", {"projects": [{"id": "x", "key": "", "name": "X", "aliases": []}], "own": "x"}, lambda *a: True), "#27", "no key: nothing")
        self.assertEqual(tn.qualify_text("#27", {"projects": PROJECTS, "own": ""}, lambda *a: True), "#27")


class OnTheHub(Hub):
    """Two projects with tasks numbered alike: Ensemble Dashboard (ED) and
    OPtionTradingENgine (OP, its PO chat titled "opten")."""

    def setUp(self):
        super().setUp()
        self.a = self.room("Alpha", self.ed, numbered=True)          # ED-1
        self.b = self.room("Beta", self.ed, numbered=True)           # ED-2
        self.x = self.room("Trading one", self.ot, numbered=True)    # OP-1
        self.po = self.room("PO", self.ed)
        dashboard.set_project_po(self.ed, self.po)
        self.opo = self.room("opten", self.ot)
        dashboard.set_project_po(self.ot, self.opo)

    def test_the_context_the_hub_reads_in(self):
        ctx = dashboard.ref_context(self.ed)
        self.assertEqual(ctx["own"], self.ed)
        self.assertEqual(ctx["nouns"], ["project", "projects"])
        self.assertEqual([(p["key"], p["aliases"]) for p in ctx["projects"]],
                         [("ED", ["Ensemble Dashboard", "Ensemble"]), ("O", ["OPtionTradingENgine", "opten"])], "the PO chat's title names the project")
        self.assertEqual(dashboard.room_project(self.x), self.ot)
        self.assertEqual(dashboard.room_project(""), "")

    def test_the_line_under_a_message_follows_a_name(self):
        line = lambda text, room=None, project="": dashboard.with_message_refs(text, room or self.a, project).split("\n\n")[1:]   # noqa: E731
        self.assertEqual(line("opten released #1 as v0.11.0.")[0][:38], '[ref #1] task O-1 "Trading one" — not ', "named: that project's, labelled in full")
        self.assertEqual(line("opten's #1 and #2")[0][:30], '[ref #1] task O-1 "Trading one')
        self.assertEqual(line("opten's #1 and #2")[1][:20], '[ref #2] task "Beta"')
        self.assertEqual(line("Plain #1 here")[0][:21], '[ref #1] task "Alpha"', "a plain number: this project's")
        self.assertEqual(line("opten #2 is nobody's there")[0][:20], '[ref #2] task "Beta"', "a named project without the task: this project's")
        self.assertEqual(line("Ensemble #1 in our own words")[0][:21], '[ref #1] task "Alpha"', "our own name")
        self.assertEqual(dashboard.with_message_refs("told opten that #1 is merged", self.a), "told opten that #1 is merged",
                         "a weak name whose project has the number too: no line rather than a guess")
        self.assertEqual(line("told opten that #2 is merged")[0][:20], '[ref #2] task "Beta"', "a weak name whose project lacks it: this project's")
        self.assertEqual(line("Ensemble released #1", self.x)[0][:38], '[ref #1] task ED-1 "Alpha" — not runni', "from the other project's room")
        # A PO's message from another project: the sender's project is the own
        # one, names still count, and the tasks are labelled in full for the
        # room that reads them.
        self.assertEqual([x[:26] for x in line("Ensemble's #2 is done, and so is #1", self.x, project=self.ed)],
                         ['[ref #2] task ED-2 "Beta" ', '[ref #1] task ED-1 "Alpha"'])
        # The hub's own full form, as a PO message goes out: a chip and a line anywhere.
        self.assertEqual(line("ED-2 and O-1 are done; ed-2 is not written so", self.x), ['[ref ED-2] task "Beta" — not running, Done; claude (engineer), codex (reviewer)',
                                                                                        '[ref O-1] task #1 "Trading one" — not running, Done; claude (engineer), codex (reviewer)'])

    def test_ambiguity_is_read_once_per_message(self):
        lookup = dashboard.task_lookup_for(self.a)
        with mock.patch.object(dashboard, "_task_index", wraps=dashboard._task_index) as idx:
            refs = lookup.find("told opten that #1 and #2 and opten's #1 are done")
            self.assertEqual([(r["no"], r["project"], r["how"], r["ambiguous"]) for r in refs],
                             [(1, self.ed, "", True), (2, self.ed, "", False), (1, self.ot, "name", False)])
            self.assertLessEqual(idx.call_count, 3, "the rooms are read for the room's project, the names and the tasks: never per number")
        self.assertEqual(lookup.find("nothing here"), [])
        self.assertEqual(mr.expand_message_refs("told opten that #1 and #2", lambda *a: None, lookup).split("\n\n")[1:], [
            '[ref #2] task "Beta" — not running, Done; claude (engineer), codex (reviewer)'])

    def test_a_plain_lookup_still_works(self):
        # message_refs with a lookup(key, no) that reads no names: every number asked plainly.
        asked = []
        plain = lambda key, no: asked.append((key, no)) or {"label": f"#{no}", "title": "T"}      # noqa: E731
        out = mr.expand_message_refs("opten released #1", lambda *a: None, plain)
        self.assertEqual(asked, [("", 1)])
        self.assertEqual(out.split("\n\n")[1], '[ref #1] task "T"')

    def test_the_pages_project_list(self):
        status, _, body = self.call("GET", f"/api/task/projects?room={self.x}")
        self.assertEqual(status, 200)
        got = json.loads(body)
        self.assertEqual(got["own"], self.ot)
        self.assertEqual(got["nouns"], ["project", "projects"])
        self.assertEqual([(p["id"], p["key"], p["name"], p["aliases"]) for p in got["projects"]],
                         [(self.ed, "ED", "Ensemble Dashboard", ["Ensemble Dashboard", "Ensemble"]),
                          (self.ot, "O", "OPtionTradingENgine", ["OPtionTradingENgine", "opten"])])
        status, _, body = self.call("GET", "/api/task/projects")
        self.assertEqual((status, json.loads(body)["own"]), (200, ""))
        # The board's rows carry the names too (index.html reads a spec's "opten #1" with them).
        rows = {p["id"]: p for p in dashboard.build_projects()["projects"]}
        self.assertEqual(rows[self.ot]["aliases"], ["OPtionTradingENgine", "opten"])

    def test_a_pos_message_goes_out_qualified(self):
        self.assertEqual(dashboard.qualify_task_refs("Drafted as #2; opten's #1 is theirs; #9 is nobody's.", self.ed),
                         "Drafted as ED-2; opten's #1 is theirs; #9 is nobody's.")
        self.assertEqual(dashboard.qualify_task_refs("#1 and #2", self.ot), "O-1 and #2")
        self.assertEqual(dashboard.qualify_task_refs("#1", ""), "#1")
        self.assertEqual(dashboard.qualify_task_refs("no numbers", self.ed), "no numbers")


if __name__ == "__main__":
    unittest.main()
