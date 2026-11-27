"""The plan picture (#426, P469, P470): the hub draws a PO's plan as
``PO Plan.html`` in the project's Documents folder, from the board alone.

* the trigger: a PO's second task within 10 minutes, any gate set or
  cleared, and once the page exists, any change of state of a task on it;
  one task alone, or a task created by someone who is not the PO, draws nothing;
* the page from a fixture board (Recipe Box: five tasks, two gates, one
  waiting on the CEO, one done): boxes, gates, states, points, days;
* static: no script, no external load; a finished plan goes to "Earlier";
* no model is called (no subprocess, no digest model, no network);
* Settings › Plan picture off: nothing drawn, the tool results say nothing.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard as d
import digest
import ensemble_tools as et
import plan_picture as pp
import points


_REAL_DOCS_DIR = d.project_documents_dir


class PlanPicture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.docs = self.tmp / "home" / "Documents"
        self.project = {"id": "p", "name": "Recipe Box", "poRoomId": "room-po"}
        self.settings = {**d._SETTINGS_DEFAULTS}
        self.attn = {}
        self.admin = True
        for patch in (
            mock.patch.object(chatroom, "ROOMS_DIR", self.tmp / "rooms"),
            mock.patch.object(d, "DASHBOARD_DIR", self.tmp),
            mock.patch.object(d, "load_projects", return_value=[self.project]),
            mock.patch.object(d, "load_session_projects", return_value={}),
            mock.patch.object(d, "load_labels", return_value={}),
            mock.patch.object(d, "project_documents_dir", side_effect=lambda p, persist=True: str(self.docs)),
            mock.patch.object(d, "_room_is_live", side_effect=lambda r: r.get("live", False)),
            mock.patch.object(d.attention, "by_room", side_effect=lambda max_age=0: self.attn),
            mock.patch.object(et, "is_admin_caller", side_effect=lambda room, ident: self.admin),
            mock.patch.object(digest, "_git_facts", side_effect=AssertionError("no git")),
            mock.patch.object(pp, "_LAST_TICK", 0.0),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.load = mock.patch.object(d, "load_settings", side_effect=lambda: self.settings)
        self.load.start()
        self.addCleanup(self._stop_load)
        self.ctx = {"projectId": "p", "part": {"agent": "claude"}, "room": {"id": "room-po"},
                    "identity": "claude"}

    def _stop_load(self):
        self.load.stop()

    # -- helpers ---------------------------------------------------------------

    def room(self, no, title, **fields):
        r = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "role": "engineer"},
                                         {"identity": "codex", "agent": "codex", "role": "reviewer"}])
        return chatroom.patch_room(r["id"], **{"no": no, "projectId": "p", "launched": False,
                                               "createdAt": 1000, "updatedAt": 1000, **fields})

    def create(self, no, title, spec="Spec", after=None):
        """ensemble_create_task as the PO calls it, with the hub's create stubbed."""
        task = self.room(no, title, spec=spec)
        args = {"title": title, "spec": spec, **({"after": after} if after else {})}
        with mock.patch.object(d, "create_task", return_value=(True, task, "")):
            return et._create_task(self.ctx, args, mock.Mock()), task

    def page(self) -> str:
        return (self.docs / pp.FILE_NAME).read_text(encoding="utf-8")

    def recipe_box(self):
        """Five tasks, two gates (#2 after #1 merged, met; #4 after #3
        approved, not yet), #3 waiting on the CEO, #1 done."""
        t1 = self.room(1, "Recipe store: save, list and delete recipes (P10)", priority=2,
                       launched=True, workflow="done", mergedAt=1795500000,
                       spec="Estimate: 1 day")
        t2 = self.room(2, "Add and edit a recipe: the form, photos and ingredients (P11)", priority=2,
                       launched=True, live=True, workflow="inprogress",
                       after=[{"task": t1["id"], "when": "merged"}], spec="Estimate: 3 days")
        t3 = self.room(3, "Tags and search: find a recipe by name or by tag (P12, P12b)",
                       launched=True, live=True, workflow="inprogress", spec="~2 days of work")
        chatroom.patch_room(t3["id"], participants=[{"identity": "codex", "agent": "codex",
                                                     "role": "engineer", "kind": "agent"}])
        t4 = self.room(4, "Shopping list from the recipes you pick (P13)",
                       after=[{"task": t3["id"], "when": "approved"}], spec="Estimate: 1 day")
        t5 = self.room(5, "Print view: one recipe on one page (P14)", priority=4,
                       launched=True, workflow="inreview", spec="No estimate here; within 10 days.")
        self.attn = {t3["id"]: {"state": "waiting_for_you",
                                "reason": "Tags from a fixed list, or anything typed?"}}
        return t1, t2, t3, t4, t5

    # -- the trigger ---------------------------------------------------------------

    def test_one_task_alone_draws_nothing_the_second_within_ten_minutes_does(self):
        with mock.patch("plan_picture.time.time", return_value=5000):
            first, _ = self.create(1, "One")
        self.assertNotIn("planPicture", first)
        self.assertFalse((self.docs / pp.FILE_NAME).exists())
        with mock.patch("plan_picture.time.time", return_value=5000 + 599):
            second, _ = self.create(2, "Two")
        self.assertIn(pp.FILE_NAME, second["planPicture"])
        self.assertIn(f"[{pp.FILE_NAME}](", second["planPicture"])
        self.assertEqual(second["planPicture"].count("\n"), 0)
        page = self.page()
        self.assertIn("One", page)
        self.assertIn("Two", page)

    def test_a_second_task_after_ten_minutes_is_not_a_plan(self):
        with mock.patch("plan_picture.time.time", return_value=5000):
            self.create(1, "One")
        with mock.patch("plan_picture.time.time", return_value=5000 + 600):
            second, _ = self.create(2, "Two")
        self.assertNotIn("planPicture", second)
        self.assertFalse((self.docs / pp.FILE_NAME).exists())

    def test_tasks_not_created_by_the_po_draw_nothing(self):
        self.admin = False
        self.create(1, "One")
        second, _ = self.create(2, "Two")
        self.assertNotIn("planPicture", second)
        self.assertFalse((self.docs / pp.FILE_NAME).exists())

    def test_a_gate_at_creation_or_set_or_cleared_later_redraws(self):
        dep = self.room(7, "Dependency")
        res, task = self.create(8, "Gated", after=[{"task": dep["id"]}])
        self.assertIn("planPicture", res)
        self.assertIn("Dependency", self.page())
        other = self.room(9, "Another")
        res = et._update_task(self.ctx, {"taskId": other["id"], "after": [{"task": task["id"],
                                                                         "when": "approved"}]}, mock.Mock())
        self.assertIn("planPicture", res)
        self.assertIn("Another", self.page())
        res = et._update_task(self.ctx, {"taskId": other["id"], "after": []}, mock.Mock())
        self.assertIn("planPicture", res)          # cleared
        res = et._update_task(self.ctx, {"taskId": other["id"], "after": []}, mock.Mock())
        self.assertNotIn("planPicture", res)       # nothing changed
        res = et._update_task(self.ctx, {"taskId": other["id"], "title": "Renamed"}, mock.Mock())
        self.assertNotIn("planPicture", res)

    def test_a_state_change_of_a_task_on_the_page_redraws_it_on_the_tick(self):
        dep = self.room(1, "First")
        _, task = self.create(2, "Second", after=[{"task": dep["id"]}])
        self.assertIn("NOT STARTED", self.page())
        before = self.page()
        pp.maybe_tick(now=10 ** 10)
        self.assertEqual(self.page(), before)            # nothing moved: not rewritten
        chatroom.patch_room(dep["id"], launched=True, live=True, workflow="inprogress")
        pp.maybe_tick(now=10 ** 10 + pp.TICK_S)
        self.assertIn("RUNNING", self.page())
        # A task that is not on the page changes nothing.
        stray = self.room(9, "Stray")
        sig = pp._load()["p"]["sig"]
        chatroom.patch_room(stray["id"], launched=True, live=True)
        pp.maybe_tick(now=10 ** 10 + 2 * pp.TICK_S)
        self.assertEqual(pp._load()["p"]["sig"], sig)

    def test_a_finished_plan_goes_to_earlier_and_only_the_last_stays(self):
        a = self.room(1, "Alpha")
        self.create(2, "Beta", after=[{"task": a["id"]}])
        for r in chatroom.list_rooms():
            chatroom.patch_room(r["id"], launched=True, workflow="done")
        pp.maybe_tick(now=10 ** 10)
        page = self.page()
        self.assertIn("No plan under way", page)
        self.assertIn("Earlier", page)
        self.assertIn("Alpha", page)
        c = self.room(3, "Gamma")
        self.create(4, "Delta", after=[{"task": c["id"]}])
        for r in (c,):
            chatroom.patch_room(r["id"], launched=True, workflow="done")
        rid = next(r["id"] for r in chatroom.list_rooms() if r.get("no") == 4)
        chatroom.patch_room(rid, launched=True, workflow="done")
        pp.maybe_tick(now=10 ** 10 + pp.TICK_S)
        page = self.page()
        self.assertIn("Gamma", page)
        self.assertNotIn("Alpha", page)
        self.assertEqual(sorted(p.name for p in self.docs.iterdir()), [pp.FILE_NAME])

    # -- the page from a fixture board ------------------------------------------

    def test_recipe_box_page_shows_boxes_gates_states_points_and_days(self):
        t1, t2, t3, t4, t5 = self.recipe_box()
        state = {"p": {"groups": [[t1["id"], t2["id"], t3["id"], t4["id"], t5["id"]]]}}
        path = pp._redraw("p", state, 1795700000)
        self.assertEqual(path, str(self.docs / pp.FILE_NAME))
        page = self.page()
        self.assertIn("<title>Recipe Box: the plan</title>", page)
        for chip in ("5 tasks", "1 done", "1 running", "1 needs you", "1 in review",
                     "1 waits for a gate", "about 6 days left (4 of 5 tasks give days)"):
            self.assertIn(chip, page)
        for word in ("DONE", "RUNNING", "NEEDS YOU", "IN REVIEW", "WAITS FOR #3"):
            self.assertIn(f">{word}</text>", page)
        # Gates as arrows: one met (solid, green), one not yet (dashed).
        self.assertEqual(page.count("marker-end=\"url(#ah"), 2)
        self.assertEqual(page.count("stroke-dasharray=\"5 4\" marker-end"), 1)
        self.assertIn(">merged ✓</text>", page)
        self.assertIn(">approved</text>", page)
        self.assertIn("after #1 merged ✓", page)
        self.assertIn("after #3 approved", page)
        # Points, owner kind, priority, days, the question.
        for p in ("P10", "P11", "P12", "P12b", "P13", "P14"):
            self.assertIn(f">{p}</", page)
        self.assertIn(">codex</text>", page)
        self.assertIn("▲ High", page)
        self.assertIn("▼ Low", page)
        self.assertIn("3 days", page)
        self.assertIn("no estimate", page)
        self.assertIn("Asks you: “Tags from a fixed list, or anything typed?”", page)
        # Layout as drawn: #1 #3 #5 first, #2 and #4 then, beside their gates.
        rows = pp._collect(self.project, state["p"], 0)[0]["rows"]
        pos = {r["no"]: xy for r, xy in zip(rows, [pp.layout(rows)[r["id"]] for r in rows])}
        self.assertEqual(pos, {"#1": (0, 0), "#3": (0, 1), "#5": (0, 2), "#2": (1, 0), "#4": (1, 1)})

    def test_the_list_runs_in_task_order_and_titles_drop_their_points(self):
        t1, t2, t3, t4, t5 = self.recipe_box()
        state = {"p": {"groups": [[t1["id"], t2["id"], t3["id"], t4["id"], t5["id"]]]}}
        pp._redraw("p", state, 1795700000)
        page = self.page()
        lis = re.findall(r'<li><span class="no">(#\d+)</span><span class="t">([^<]*)</span>', page)
        self.assertEqual([n for n, _ in lis], ["#1", "#2", "#3", "#4", "#5"])
        self.assertIn(("#3", "Tags and search: find a recipe by name or by tag"), lis)
        # A gate comes before the task it holds, whatever the numbers.
        rows = [{"id": "a", "no": "#1", "gates": [{"id": "b"}]}, {"id": "b", "no": "#2", "gates": []}]
        self.assertEqual([r["no"] for r in pp.in_order(rows)], ["#2", "#1"])

    def test_the_page_is_static_and_self_contained(self):
        t1, t2, t3, t4, t5 = self.recipe_box()
        pp._redraw("p", {"p": {"groups": [[t1["id"], t2["id"], t3["id"], t4["id"], t5["id"]]]}}, 0)
        page = self.page().lower()
        self.assertNotIn("<script", page)
        self.assertNotIn(" on", re.sub(r"<[^>]*>", lambda m: "", page)[:0])   # sanity
        self.assertIsNone(re.search(r"<[^>]+\son[a-z]+=", page))
        self.assertIsNone(re.search(r"(src|href)=\"(https?:)?//", page))
        self.assertNotIn("@import", page)
        self.assertNotIn("url(http", page)
        self.assertIn('name="viewport"', page)
        self.assertIn("@media (max-width:1100px)", page)

    def test_the_graph_is_fluid_within_the_visual_bounds(self):
        """#427 (GitHub issue 70): the page and its graph fit the width they
        are shown at, between the file view's bounds; no fixed width."""
        t1, t2, t3, t4, t5 = self.recipe_box()
        pp._redraw("p", {"p": {"groups": [[t1["id"], t2["id"], t3["id"], t4["id"], t5["id"]]]}}, 0)
        page = self.page()
        svg = re.search(r"<svg [^>]*>", page).group(0)
        vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
        self.assertTrue(vb, svg)
        drawn = int(vb.group(1))
        self.assertIn('width="100%"', svg)
        self.assertNotIn("height=", svg.replace("viewBox", ""), "the height follows the width")
        lo = int(re.search(r"min-width:(\d+)px", svg).group(1))
        hi = int(re.search(r"max-width:(\d+)px", svg).group(1))
        self.assertEqual(lo, min(drawn, max(pp.VISUAL_MIN, round(drawn * pp.GRAPH_SHRINK))))
        self.assertEqual(hi, min(pp.VISUAL_MAX, round(drawn * pp.GRAPH_GROW)))
        self.assertLess(lo, drawn, "it shrinks in a narrow panel")
        self.assertGreater(hi, drawn, "and grows in a wide one")
        self.assertIn(".graph svg{display:block;width:100%;height:auto}", page)
        self.assertIn(f"min-width:{pp.VISUAL_MIN}px;max-width:{pp.VISUAL_MAX}px", page)
        self.assertNotIn("max-width:none", page)
        # A wide graph never passes the most; a one-box graph never under its size.
        many = [{"id": str(i), "no": f"#{i}", "title": "t", "gates": [{"id": str(i - 1)}] if i else []}
                for i in range(8)]
        self.assertEqual(pp.layout(many)["7"], (7, 0))
        w = pp.PAD * 2 + 8 * pp.BOX_W + 7 * pp.GAP
        self.assertEqual(min(pp.VISUAL_MAX, round(w * pp.GRAPH_GROW)), pp.VISUAL_MAX)

    def test_the_bounds_are_the_file_views(self):
        fv = (Path(pp.__file__).parent / "fileview.html").read_text(encoding="utf-8")
        m = re.search(r"--visual-min:\s*(\d+)px;\s*--visual-max:\s*(\d+)px;", fv)
        self.assertTrue(m)
        self.assertEqual((int(m.group(1)), int(m.group(2))), (pp.VISUAL_MIN, pp.VISUAL_MAX))

    def test_titles_and_questions_are_escaped(self):
        a = self.room(1, "<b>bold</b> & \"quoted\"")
        self.create(2, "Two", after=[{"task": a["id"]}])
        page = self.page()
        self.assertNotIn("<b>bold</b>", page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp;", page)

    def test_spec_days(self):
        self.assertEqual(pp.spec_days("Estimate: 2 days"), 2)
        self.assertEqual(pp.spec_days("Estimated 1-3 days"), 3)
        self.assertEqual(pp.spec_days("about ~½ day"), 0.5)
        self.assertEqual(pp.spec_days("Estimate: 1.5 d"), 1.5)
        self.assertIsNone(pp.spec_days("within 10 days of the release"))
        self.assertIsNone(pp.spec_days("No estimate here; within 10 days."))
        self.assertEqual(pp.spec_days("Estimated effort: 2-4 days"), 4)
        self.assertIsNone(pp.spec_days(""))

    def test_points_planned_on_the_task_in_the_pos_ledger(self):
        a = self.room(1, "Alpha")
        led = {"points": [{"id": "P77", "task": "#1"}, {"id": "P78", "task": "#2"}]}
        with mock.patch.object(points, "exists", return_value=True), \
                mock.patch.object(points, "load", return_value=led):
            self.create(2, "Beta", after=[{"task": a["id"]}])
        page = self.page()
        self.assertIn(">P77</", page)
        self.assertIn(">P78</", page)

    def test_ledger_points_named_with_the_project_key_count_too(self):
        a = self.room(1, "Alpha")
        led = {"points": [{"id": "P79", "task": "RB-1"}, {"id": "P80", "task": "rb-2"},
                          {"id": "P81", "task": "XX-1"}]}
        with mock.patch.object(points, "exists", return_value=True), \
                mock.patch.object(points, "load", return_value=led), \
                mock.patch.object(d, "project_keys", return_value={"p": "RB"}):
            self.create(2, "Beta", after=[{"task": a["id"]}])
        page = self.page()
        self.assertIn(">P79</", page)
        self.assertIn(">P80</", page)
        self.assertNotIn("P81", page, "another project's #1")

    def test_a_task_moved_to_another_project_leaves_the_page(self):
        a = self.room(1, "Alpha stays")
        self.create(2, "Beta moves", after=[{"task": a["id"]}])
        self.assertIn("Beta moves", self.page())
        beta = next(r for r in chatroom.list_rooms() if r.get("title") == "Beta moves")
        chatroom.patch_room(beta["id"], projectId="q", no=7)
        pp.maybe_tick(now=10 ** 10)
        self.assertNotIn("Beta moves", self.page())
        self.assertIn("Alpha stays", self.page())
        self.assertEqual(pp._load()["p"]["groups"], [[a["id"]]])

    def test_a_room_that_cannot_be_read_now_stays_in_the_plan(self):
        a = self.room(1, "Alpha")
        self.create(2, "Beta", after=[{"task": a["id"]}])
        beta = next(r for r in chatroom.list_rooms() if r.get("title") == "Beta")
        real = chatroom.get_room
        with mock.patch.object(chatroom, "get_room",
                               side_effect=lambda rid, **kw: None if rid == beta["id"] else real(rid, **kw)):
            pp.maybe_tick(now=10 ** 10)
        self.assertEqual([sorted(g) for g in pp._load()["p"]["groups"]], [sorted([a["id"], beta["id"]])])
        self.assertNotIn("Beta", self.page(), "left out of this drawing only")
        # Readable again: back on the page. Deleted: gone for good.
        chatroom.patch_room(beta["id"], live=True)
        pp.maybe_tick(now=2 * 10 ** 10)
        self.assertIn("Beta", self.page())
        os.remove(chatroom._room_path(beta["id"]))
        pp.maybe_tick(now=3 * 10 ** 10)
        self.assertEqual(pp._load()["p"]["groups"], [[a["id"]]])

    def test_a_task_deleted_while_off_the_page_leaves_the_saved_plan(self):
        a = self.room(1, "Alpha")
        self.create(2, "Beta", after=[{"task": a["id"]}])
        real = chatroom.get_room
        with mock.patch.object(chatroom, "get_room",
                               side_effect=lambda rid, **kw: None if rid == a["id"] else real(rid, **kw)):
            pp.maybe_tick(now=10 ** 10)
        # Deleted now: the page looks the same, the saved plan still drops it.
        os.remove(chatroom._room_path(a["id"]))
        pp.maybe_tick(now=2 * 10 ** 10)
        self.assertNotIn(a["id"], [i for g in pp._load()["p"]["groups"] for i in g])

    def test_an_unreadable_gate_keeps_its_task_waiting(self):
        a = self.room(1, "Alpha")
        self.create(2, "Beta", after=[{"task": a["id"]}])
        real = chatroom.get_room
        with mock.patch.object(chatroom, "get_room",
                               side_effect=lambda rid, **kw: None if rid == a["id"] else real(rid, **kw)):
            pp.maybe_tick(now=10 ** 10)
        page = self.page()
        self.assertIn(">WAITS FOR A TASK</text>", page)
        self.assertIn("after a task merged (cannot be read just now)", page)
        self.assertNotIn("Not started", page)

    def test_a_group_with_an_unreadable_task_does_not_retire(self):
        a = self.room(1, "Alpha", workflow="done", launched=True)
        self.create(2, "Beta", after=[{"task": a["id"]}])
        beta = next(r for r in chatroom.list_rooms() if r.get("title") == "Beta")
        chatroom.patch_room(beta["id"], workflow="done", launched=True)
        real = chatroom.get_room
        with mock.patch.object(chatroom, "get_room",
                               side_effect=lambda rid, **kw: None if rid == beta["id"] else real(rid, **kw)):
            pp.maybe_tick(now=10 ** 10)
        self.assertEqual([sorted(g) for g in pp._load()["p"]["groups"]], [sorted([a["id"], beta["id"]])])

    def test_the_page_stays_in_the_folder_chosen_first(self):
        # A documents project whose home already has a "Documents" folder of
        # the CEO's: the hub picks another name once and keeps it.
        home = self.tmp / "ceo-home"
        (home / "Documents").mkdir(parents=True)
        self.project.update(kind="documents")
        saved = {}

        def set_meta(pid, field, value):
            saved[field] = value
            return True, {}

        with mock.patch.object(d, "project_documents_dir", _REAL_DOCS_DIR), \
                mock.patch.object(d, "project_home", lambda p, create=True: str(home)), \
                mock.patch.object(d, "_set_project_meta", side_effect=set_meta):
            first = pp.page_path(self.project)
            os.makedirs(os.path.dirname(first))
            Path(first).write_text("x", encoding="utf-8")
            second = pp.page_path(self.project)
        self.assertEqual(first, second)
        self.assertNotEqual(os.path.dirname(first), str(home / "Documents"))
        self.assertEqual(saved, {"documentsDir": Path(first).parent.name})

    # -- no model ---------------------------------------------------------------------

    def test_no_model_is_called(self):
        boom = AssertionError("a model or a process was called")
        with mock.patch.object(subprocess, "run", side_effect=boom), \
                mock.patch.object(subprocess, "Popen", side_effect=boom), \
                mock.patch.object(subprocess, "check_output", side_effect=boom), \
                mock.patch("urllib.request.urlopen", side_effect=boom), \
                mock.patch.object(digest, "_run_model", side_effect=boom, create=True), \
                mock.patch.object(digest, "_haiku", side_effect=boom, create=True):
            t1, t2, t3, t4, t5 = self.recipe_box()
            res = et._update_task(self.ctx, {"taskId": t5["id"], "after": [{"task": t1["id"]}]},
                                  mock.Mock())
            self.assertIn("planPicture", res)
            self.assertIn("IN REVIEW", self.page())
            chatroom.patch_room(t5["id"], live=True, workflow="inprogress")
            pp.maybe_tick(now=10 ** 10)
        self.assertIn("RUNNING", self.page())

    # -- the setting ---------------------------------------------------------------------

    def test_on_by_default_and_saved_as_a_bool(self):
        self.assertIs(d._SETTINGS_DEFAULTS["planPicture"], True)
        self.load.stop()   # the real load/save, in this test's state dir
        with mock.patch.object(d, "SETTINGS_FILE", self.tmp / "settings.json"), \
                mock.patch.object(d, "DASHBOARD_DIR", self.tmp):
            self.assertIs(d.save_settings({"planPicture": 0})["planPicture"], False)
            self.assertIs(d.save_settings({"planPicture": "yes"})["planPicture"], True)
        self.load.start()

    def test_off_draws_nothing_and_the_results_say_nothing(self):
        self.settings = {**self.settings, "planPicture": False}
        dep = self.room(1, "Dependency")
        first, task = self.create(2, "Gated", after=[{"task": dep["id"]}])
        second, _ = self.create(3, "Third")
        res = et._update_task(self.ctx, {"taskId": task["id"], "after": []}, mock.Mock())
        for r in (first, second, res):
            self.assertNotIn("planPicture", r)
            self.assertNotIn(pp.FILE_NAME, str(r))
        pp.maybe_tick(now=10 ** 10)
        self.assertFalse(self.docs.exists() and any(self.docs.iterdir()))
        self.assertFalse((self.tmp / "plan_pictures.json").exists())

    def test_settings_page_has_the_toggle_beside_the_cheap_reader(self):
        page = (Path(d.__file__).parent / "index.html").read_text(encoding="utf-8")
        reader = page.index('id="pref-reader-tasks"')
        plan = page.index('id="pref-plan-picture"')
        self.assertLess(reader, plan)
        self.assertLess(plan - reader, 1500)
        self.assertIn("setPref('planPicture'", page)
        self.assertIn("PREFS.planPicture === false ? 'off' : 'on'", page)


if __name__ == "__main__":
    unittest.main()
