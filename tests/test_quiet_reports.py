"""Reports that do not wake their PO (#288): quiet_reports.py, the rider in
dashboard._type_input_now, and ensemble_tools._report's quiet update."""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools
import po_messages
import quiet_reports


class _Handler:
    def __init__(self):
        self.reports: list = []

    def _ring_report(self, po_room_id, result, task_id, title, reporter, kind, text):
        self.reports.append((po_room_id, kind))
        return (result or {}).get("recipients") or []


class _Rooms(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        for p in (mock.patch.object(chatroom, "ROOMS_DIR", tmp / "rooms"),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", tmp / "hub")):
            p.start()
            self.addCleanup(p.stop)
        (tmp / "hub").mkdir()
        quiet_reports.reset()
        self.addCleanup(quiet_reports.reset)
        self.po_rid = chatroom.create_room("Motors PO", [{"identity": "po", "agent": "claude",
                                                          "role": "ProductOwner"}])["id"]
        self.sess = types.SimpleNamespace(meta={"room": self.po_rid, "identity": "po",
                                                "agent": "claude"})


class Queue(_Rooms):
    def test_hold_rider_told(self):
        self.assertEqual(quiet_reports.rider(self.po_rid, "po"), ("", []))
        quiet_reports.hold(self.po_rid, "m1", quiet_reports.gist("update from #12 (claude)", "half done"))
        quiet_reports.hold(self.po_rid, "m2", "review 2 approved of #13 (codex): fine")
        extra, ids = quiet_reports.rider(self.po_rid, "po")
        self.assertEqual(ids, ["m1", "m2"])
        self.assertIn("update from #12 (claude): half done; review 2 approved", extra)
        self.assertEqual(quiet_reports.rider(self.po_rid, "claude"), ("", []), "only the room's PO")
        quiet_reports.told(self.po_rid, ["m1"])
        self.assertEqual([i["id"] for i in quiet_reports.pending(self.po_rid)], ["m2"])
        # It is kept on disk across a hub restart.
        quiet_reports.reset()
        self.assertEqual([i["id"] for i in quiet_reports.pending(self.po_rid)], ["m2"])

    def test_a_long_gist_and_a_long_queue_are_cut(self):
        self.assertTrue(quiet_reports.gist("u", "x" * 500).endswith("…"))
        for i in range(30):
            quiet_reports.hold(self.po_rid, f"m{i}", "y" * 150)
        extra, ids = quiet_reports.rider(self.po_rid, "po")
        self.assertLess(len(extra), quiet_reports._LINE_MAX + 120)
        self.assertIn("more", extra)
        self.assertEqual(len(ids), 30, "the ones only counted are told too")


class Rider(_Rooms):
    def type_now(self, text: str, ok: bool = True, **kw) -> list:
        typed = []

        def reserved(sess, t, provenance=None, **k):
            typed.append(t)
            return ok
        with mock.patch.object(dashboard.rotation, "reserve_hub_line", return_value=object()), \
                mock.patch.object(dashboard.rotation, "release_hub_line"), \
                mock.patch.object(dashboard, "checkpoint_wait_restore"), \
                mock.patch.object(dashboard, "_type_input_reserved", side_effect=reserved):
            dashboard._type_input_now(self.sess, text, **kw)
        return typed

    def test_a_hub_line_carries_them_and_clears_them(self):
        quiet_reports.hold(self.po_rid, "m1", "update from #12 (claude): half done")
        typed = self.type_now("[report] #14 completed")
        self.assertTrue(typed[0].startswith("[report] #14 completed | Quiet reports since"))
        self.assertIn("half done", typed[0])
        self.assertEqual(quiet_reports.pending(self.po_rid), [])

    def test_a_line_not_taken_keeps_them(self):
        quiet_reports.hold(self.po_rid, "m1", "update from #12 (claude): half done")
        self.type_now("[report] #14 completed", ok=False)
        self.assertEqual(len(quiet_reports.pending(self.po_rid)), 1)

    def test_the_person_words_slash_commands_and_probes_carry_nothing(self):
        quiet_reports.hold(self.po_rid, "m1", "update from #12 (claude): half done")
        for text, kw in (("hello", {"provenance": {"from": "user"}}), ("/compact", {}),
                         ("[probe]", {"record": False})):
            with self.subTest(text=text):
                self.assertEqual(self.type_now(text, **kw), [text])
        self.assertEqual(len(quiet_reports.pending(self.po_rid)), 1)


class Tick(_Rooms):
    def test_after_thirty_idle_minutes_one_batched_line(self):
        quiet_reports.hold(self.po_rid, "m1", "update from #12 (claude): half done", now=1000.0)
        quiet_reports.hold(self.po_rid, "m2", "review 2 approved of #13", now=1500.0)
        typed = []
        with mock.patch.object(po_messages, "_target", return_value=self.sess), \
                mock.patch.object(dashboard, "_type_input",
                                  side_effect=lambda s, t, *a, **k: typed.append(t) or True):
            self.assertEqual(quiet_reports.tick(1000.0 + quiet_reports.IDLE_S - 1), [])
            self.assertEqual(quiet_reports.tick(1000.0 + quiet_reports.IDLE_S), [self.po_rid])
        self.assertEqual(len(typed), 1)
        self.assertTrue(typed[0].startswith("[quiet reports] 2 reports that did not wake you"))
        self.assertEqual(quiet_reports.pending(self.po_rid), [])

    def test_a_busy_or_stopped_po_is_not_typed_to(self):
        quiet_reports.hold(self.po_rid, "m1", "u", now=1.0)
        with mock.patch.object(po_messages, "_target", return_value=None), \
                mock.patch.object(dashboard, "_type_input") as typed:
            self.assertEqual(quiet_reports.tick(1.0 + quiet_reports.IDLE_S), [])
        typed.assert_not_called()
        self.assertEqual(len(quiet_reports.pending(self.po_rid)), 1)

    def test_a_line_not_taken_puts_them_back(self):
        quiet_reports.hold(self.po_rid, "m1", "u", now=1.0)
        with mock.patch.object(po_messages, "_target", return_value=self.sess), \
                mock.patch.object(dashboard, "_type_input", return_value=False):
            quiet_reports.tick(1.0 + quiet_reports.IDLE_S)
        self.assertEqual([i["id"] for i in quiet_reports.pending(self.po_rid)], ["m1"])


class UpdateReport(_Rooms):
    def setUp(self):
        super().setUp()
        self.rid = chatroom.create_room("Motor spec", [{"identity": "claude", "agent": "claude",
                                                        "role": "engineer"}])["id"]
        projects = {"p1": {"id": "p1", "name": "Motors", "poRoomId": self.po_rid}}
        p = mock.patch.object(ensemble_tools, "_projects", return_value=projects)
        p.start()
        self.addCleanup(p.stop)
        self.handler = _Handler()

    def report(self, kind: str) -> dict:
        full = chatroom.get_room(self.rid, public=False)
        ctx = {"room": full, "identity": "claude", "part": chatroom.participant(full, "claude"),
               "projectId": "p1"}
        return ensemble_tools._report(ctx, {"kind": kind, "text": f"{kind} text"}, self.handler)

    def test_an_update_is_posted_and_held_a_completed_rings(self):
        res = self.report("update")
        self.assertFalse(res["poWoken"])
        self.assertIn("without waking it", res["note"])
        self.assertEqual(self.handler.reports, [])
        last = chatroom.get_room(self.po_rid, public=False)["messages"][-1]
        self.assertEqual((last["reportKind"], last["rang"]), ("update", []))
        self.assertIn("update text", quiet_reports.pending(self.po_rid)[0]["line"])
        res = self.report("completed")
        self.assertTrue(res["poWoken"])
        self.assertEqual(self.handler.reports, [(self.po_rid, "completed")])


class HubInputKinds(unittest.TestCase):
    def test_the_new_lines_have_kinds(self):
        kinds = dict(dashboard.HUB_INPUT_KINDS)
        self.assertEqual(kinds[dashboard.REVIEW_LIMIT_PREFIX], "reviewlimit")
        self.assertEqual(kinds[quiet_reports.PREFIX], "quiet")


if __name__ == "__main__":
    unittest.main()
