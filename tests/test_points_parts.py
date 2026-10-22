"""#256: a PO chat's poll works out its points' view once per ledger, not once
per poll; the tasks its points name are looked up once per task index; a
room's card signature does not grow with the square of its rotations.

* the view is the same with the parts kept and worked out afresh, and the
  items it gives are the caller's to change;
* a save of the ledger gives new parts;
* a point with a task in progress shows its task as it is now, on each poll;
* an answer's "try this" test is kept per text;
* the task index is the same list while no room changed, and a task lookup
  is reused for it, giving copies;
* a solo room's card sessions are each listed once, in order.
"""
from __future__ import annotations

import unittest
from unittest import mock

import asks
import chatroom
import dashboard
import points
from test_points import _World


class _Parts(_World):
    def setUp(self):
        super().setUp()
        points._PARTS.clear()
        points._LOOKUPS.clear()
        # the cards as read: no background read between two views
        for name, value in (("cards_ready", True), ("decisions", ([], []))):
            p = mock.patch.object(asks, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def ledger(self, rid):
        """The ledger a poll reads (the cached one)."""
        return points.load(rid, shared=True)

    def some_points(self):
        rid = self.solo_room()
        for k in range(6):
            self.send(rid, f"Point {k}", at=self.t0 + k)
        points.answer_by_tool(rid, "P2", "done, please try it", "claude")
        points.act(rid, "P3", "ack")
        points.answer_by_tool(rid, "P4", "started", "claude", plan=True, task="#12")
        return rid


class KeptParts(_Parts):
    def test_the_view_is_the_same_kept_or_worked_out_afresh(self):
        rid = self.some_points()
        first = points.view(rid)
        led = self.ledger(rid)
        self.assertIs(points._PARTS[rid][0], led, "kept for the cached ledger")
        kept = points.view(rid)
        points._PARTS.clear()
        fresh = points.view(rid)
        self.assertEqual(kept, first)
        self.assertEqual(fresh, first)
        self.assertEqual(points.counts(rid)["delivered"], first["delivered"])

    def test_the_items_given_are_the_callers(self):
        rid = self.some_points()
        v = points.view(rid)
        for item in v["items"]:
            item["text"] = "changed"
        v["needs"]["closed"].clear()
        again = points.view(rid)
        self.assertNotIn("changed", [i["text"] for i in again["items"]])

    def test_a_save_gives_new_parts(self):
        rid = self.some_points()
        before = points.view(rid)
        kept = points._PARTS[rid]
        points.act(rid, "P1", "ack")
        after = points.view(rid)
        self.assertIsNot(points._PARTS[rid], kept)
        self.assertIs(points._PARTS[rid][0], self.ledger(rid))
        state = lambda v: {i["id"]: i["state"] for i in v["items"]}    # noqa: E731
        self.assertEqual((state(before)["P1"], state(after)["P1"]), ("open", "acked"))

    def test_a_copy_of_the_ledger_is_not_kept(self):
        rid = self.some_points()
        points._parts(rid, points.load(rid))         # a copy, changed at will
        self.assertNotIn(rid, points._PARTS)

    def test_a_point_with_a_task_shows_it_as_it_is_now(self):
        rid = self.some_points()
        task = {"ref": "#12", "roomId": "room-x", "title": "Export", "workflow": "doing",
                "workflowName": "In progress", "done": False}
        with mock.patch.object(points, "task_lookup",
                               return_value=lambda ref: dict(task) if ref == "#12" else None):
            p4 = lambda: next(i for i in points.view(rid)["items"] if i["id"] == "P4")    # noqa: E731
            self.assertEqual(p4()["task"]["workflow"], "doing")
            task.update(workflow="review", workflowName="In review")
            self.assertEqual(p4()["task"]["workflow"], "review")

    def test_the_try_test_is_kept_per_text(self):
        points.asks_action.cache_clear()
        self.assertTrue(points.asks_action("Please try it now"))
        self.assertTrue(points.asks_action("Please try it now"))
        self.assertEqual(points.asks_action.cache_info().hits, 1)


class TaskLookups(_Parts):
    def test_the_task_index_is_the_same_list_while_no_room_changed(self):
        self.solo_room()
        first = dashboard._task_index()
        self.assertIs(dashboard._task_index(), first)
        self.team_room()
        second = dashboard._task_index()
        self.assertIsNot(second, first)
        self.assertEqual(len(second), len(first) + 1)
        self.assertIs(dashboard._task_index(), second)

    def test_a_lookup_is_reused_for_the_same_index(self):
        index = [{"id": "room-12", "no": 12, "noProjectId": "p1", "projectId": "p1", "title": "Export",
                  "workflow": "doing"}]
        rid = self.solo_room()
        room = chatroom.get_room(rid, public=False)
        room["projectId"] = "p1"
        found = mock.Mock(side_effect=lambda idx, ref, pid, keys, **kw: ("room-12", "") if ref == "#12" else ("", "no"))
        with mock.patch.object(dashboard, "_task_index", side_effect=lambda: index), \
                mock.patch.object(dashboard, "load_projects", return_value=[{"id": "p1", "key": "ED"}]), \
                mock.patch.object(dashboard.task_numbers, "find_task", found):
            a = points.task_lookup(rid, room)("#12")
            a["title"] = "changed"
            b = points.task_lookup(rid, room)("#12")
            self.assertEqual((b["title"], found.call_count), ("Export", 1))
            index = list(index)         # a room changed: a new list
            points.task_lookup(rid, room)("#12")
            self.assertEqual(found.call_count, 2)
            room["projectId"] = "p2"    # another project reads #12 otherwise
            points.task_lookup(rid, room)("#12")
            self.assertEqual(found.call_count, 3)


class CardSessions(unittest.TestCase):
    def test_each_session_listed_once_in_order(self):
        rots = [{"fromSessionId": f"s{k}", "toSessionId": f"s{k + 1}"} for k in range(300)]
        room = {"mode": "solo", "updatedAt": 1, "messages": [],
                "participants": [{"identity": "claude", "agent": "claude", "sessionId": "s300", "rotations": rots}]}
        with mock.patch.object(dashboard.chatroom, "agent_participants", return_value=room["participants"]), \
                mock.patch.object(points, "_session_stat", return_value=[1, 2]):
            sids, current, sig = asks._cards_sig(room)
        self.assertEqual([s for s, _w in sids], ["s300"] + [f"s{k}" for k in range(300)])
        self.assertEqual(current, ["s300"])
        self.assertEqual(sig[2], tuple(s for s, _w in sids))


if __name__ == "__main__":
    unittest.main()
