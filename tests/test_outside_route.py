"""Routing tables and routing steps 1 and 2 (outside_route.py): one row per
initiative, a channel listed in one row routes there, a channel in two rows
is refused, thread memory and a correction, an empty table routes nothing,
the same message routed twice goes to the same initiative, only the CEO or
the initiative's own agents change a row and every change is kept with who
made it, links in the log are short, and the log and thread memory are
kept as long as they should be. Ids are made up (the repo is public)."""
from __future__ import annotations

import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import ensemble_tools
import outside
import outside_route as route
import sources
from sources.base import Author, Message, Place, Thread
from sources.fake import FakeSource

WED = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
SID = "made-up"
ALPHA, BETA, GAMMA = "p-alpha", "p-beta", "p-gamma"
CEO = {"kind": "ceo"}


def agent(project: str, identity: str = "claude") -> dict:
    return {"kind": "agent", "identity": identity, "projectId": project, "agent": "claude",
            "role": "engineer", "roomId": "room-made-up"}


def msg(n: int, at: float = WED, channel: str = "C000TEST01", name: str = "made-up-one",
        thread: str = "", text: str = "", kind: str = "channel") -> Message:
    return Message(key=f"fake:example:{channel}:{n}", source="fake",
                   place=Place(id=channel, name=name, kind=kind),
                   author=Author(id="U000TEST02", name="Made-up Person"), at=float(at),
                   text=text or f"message {n}",
                   link=f"https://example.slack.com/archives/{channel}/p{n}",
                   thread=Thread(id=f"{channel}:{thread}", first=thread == str(n)) if thread else None)


def entry(m: Message, decide: bool = False) -> dict:
    return {"key": m.key, "message": m.to_dict(), "readAt": WED, "decideOnly": decide}


class RoutingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-route-")
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.projects = [{"id": ALPHA, "name": "Alpha"}, {"id": BETA, "name": "Beta"}]
        hub = types.SimpleNamespace(DASHBOARD_DIR=self.state,
                                    load_projects=lambda: list(self.projects))
        for p in (mock.patch.object(outside, "_d", None),
                  mock.patch.object(route, "_d", None),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None),
                  mock.patch.object(outside, "_log"),
                  mock.patch.object(route, "_log"),
                  mock.patch.dict(sources.TYPES, {"fake": FakeSource})):
            p.start()
            self.addCleanup(p.stop)
        outside.bind(hub)
        route.bind(hub)
        outside.reset()
        self.addCleanup(outside.reset)
        outside.set_source(SID, type="fake", mode="on", settings={"account": "made-up"})

    # -- helpers -----------------------------------------------------------
    def row(self, pid: str) -> dict:
        return next(r for r in route.table(SID)["rows"] if r["projectId"] == pid)

    def queued(self) -> list[tuple[str, str]]:
        return [(i["key"], i["to"]) for i in outside.queue_items()]

    # -- the table ---------------------------------------------------------
    def test_bind_makes_it_the_frames_router(self):
        self.assertIs(outside.ROUTER, route.route)
        self.assertIs(outside.PLACES, route.places)

    def test_an_empty_table_has_an_empty_row_per_initiative_and_routes_nothing(self):
        t = route.table(SID)
        self.assertEqual([c["key"] for c in t["columns"]], ["channels", "topic"])
        self.assertEqual([(r["projectId"], r["values"], r["empty"]) for r in t["rows"]],
                         [(ALPHA, {"channels": [], "topic": ""}, True),
                          (BETA, {"channels": [], "topic": ""}, True)])
        self.assertEqual(t["clashes"], [])
        e = entry(msg(1))
        [(_, dec)] = route.decide(SID, [e])
        self.assertEqual((dec["to"], dec["how"]), ("", route.NEEDS_MODEL))
        self.assertEqual(route.route(SID, [e]), [])
        self.assertEqual(self.queued(), [])
        self.assertEqual(route.log_lines(), [])
        self.assertEqual(route.places(SID), [])
        self.assertFalse((self.state / "outside" / f"routing-{SID}.json").exists(),
                         "reading the table writes nothing")

    def test_a_new_initiative_shows_by_itself_and_a_deleted_one_drops_out(self):
        route.set_row(SID, BETA, {"channels": ["C000TEST01"]}, CEO)
        self.projects.append({"id": GAMMA, "name": "Gamma"})
        self.assertEqual(self.row(GAMMA)["values"], {"channels": [], "topic": ""})
        self.projects = [p for p in self.projects if p["id"] != BETA]
        self.assertEqual([r["projectId"] for r in route.table(SID)["rows"]], [ALPHA, GAMMA])
        self.assertEqual(route.route(SID, [entry(msg(1))]), [], "a deleted row routes nothing")
        self.assertEqual(route.places(SID), [])
        # Its channel is free for another row, and the next save drops its row.
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        saved = json.loads((self.state / "outside" / "routing.json").read_text())
        self.assertEqual(sorted(saved["rows"]), [ALPHA])
        self.assertEqual(saved["rows"][ALPHA], {"topic": "", "items": [],
                                                "places": {"fake": {"channels": ["C000TEST01"]}}})

    def test_a_table_needs_a_configured_source_with_a_known_type(self):
        with self.assertRaises(route.Refused):
            route.table("not-there")
        outside.set_source("other", type="discord", mode="off")
        with self.assertRaises(route.Refused):
            route.table("other")
        self.assertEqual(route.routed_sources(), [SID])

    # -- step 1: channels --------------------------------------------------
    def test_a_channel_in_one_row_goes_to_that_initiative(self):
        route.set_row(SID, ALPHA, {"channels": ["#made-up-one"], "topic": "pricing"}, CEO)
        route.set_row(SID, BETA, {"channels": ["c000test09"]}, CEO)
        by_name = msg(1, channel="C000TEST01", name="made-up-one")
        by_id = msg(2, channel="C000TEST09", name="made-up-nine")
        other = msg(3, channel="C000TEST05", name="made-up-five")
        taken = route.route(SID, [entry(by_name), entry(by_id), entry(other)])
        self.assertEqual(taken, [by_name.key, by_id.key])
        self.assertEqual(self.queued(), [(by_name.key, ALPHA), (by_id.key, BETA)])
        q = outside.queue_items()[0]
        self.assertEqual((q["how"], q["why"], q["source"]), ("channel", "channel #made-up-one", SID))
        lines = route.log_lines()
        self.assertEqual([(ln["key"], ln["to"], ln["how"]) for ln in lines],
                         [(by_id.key, [BETA], "channel"), (by_name.key, [ALPHA], "channel")])
        self.assertEqual(lines[1]["toNames"], ["Alpha"])
        self.assertEqual(lines[1]["author"], "Made-up Person")
        self.assertEqual(route.places(SID), [{"channel": "made-up-one"}, {"channel": "c000test09"}])

    def test_a_channel_in_two_rows_is_refused_when_saved(self):
        route.set_row(SID, ALPHA, {"channels": ["made-up-one"]}, CEO)
        for value in (["made-up-one"], ["#Made-Up-One"], " #made-up-one ", ["made-up-two", "MADE-UP-ONE"]):
            with self.subTest(value=value):
                with self.assertRaises(route.Refused) as cm:
                    route.set_row(SID, BETA, {"channels": value}, CEO)
                self.assertIn("Alpha's row", str(cm.exception))
                with self.assertRaises(route.Refused):
                    route.set_row(SID, BETA, {"channels": value}, agent(BETA))
        self.assertTrue(self.row(BETA)["empty"])
        self.assertEqual(len(route.history(SID)), 1, "a refused change is not kept")
        # Moving it is two saves: out of one row, then into the other.
        route.set_row(SID, ALPHA, {"channels": []}, CEO)
        route.set_row(SID, BETA, {"channels": ["made-up-one"]}, CEO)
        self.assertEqual(self.row(BETA)["values"]["channels"], ["made-up-one"])

    def test_a_channel_by_name_in_one_row_and_by_id_in_another_is_refused(self):
        route.set_row(SID, ALPHA, {"channels": ["made-up-one"]}, CEO)
        # A message there pairs the channel's id and its name.
        route.route(SID, [entry(msg(1, channel="C000TEST01", name="made-up-one"))])
        with self.assertRaises(route.Refused) as cm:
            route.set_row(SID, BETA, {"channels": ["c000test01"]}, agent(BETA))
        self.assertIn("Alpha's row", str(cm.exception))
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        with self.assertRaises(route.Refused):
            route.set_row(SID, BETA, {"channels": ["#Made-Up-One"]}, CEO)
        # One row may list both forms.
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01", "made-up-one"]}, CEO)
        route.route(SID, [entry(msg(2, channel="C000TEST01", name="made-up-one"))])
        self.assertEqual(self.queued()[-1], (msg(2).key, ALPHA))

    def test_a_name_and_an_id_saved_before_they_were_paired_route_nobody(self):
        route.set_row(SID, ALPHA, {"channels": ["made-up-one"]}, CEO)
        route.set_row(SID, BETA, {"channels": ["C000TEST01"]}, CEO)   # not known to be one yet
        self.assertEqual(route.table(SID)["clashes"], [])
        m = msg(1, channel="C000TEST01", name="made-up-one")
        self.assertEqual(route.route(SID, [entry(m)]), [], "neither row gets it: needs a model")
        self.assertEqual(self.queued(), [])
        self.assertEqual(route.table(SID)["clashes"],
                         [{"channel": "c000test01", "projectIds": [ALPHA, BETA]}])
        route.set_row(SID, BETA, {"channels": []}, CEO)
        self.assertEqual(route.route(SID, [entry(m)]), [m.key])
        self.assertEqual(self.queued(), [(m.key, ALPHA)])

    def test_a_channel_two_rows_list_in_a_file_edited_by_hand_routes_nobody(self):
        path = self.state / "outside" / f"routing-{SID}.json"
        path.write_text(json.dumps({"rows": {ALPHA: {"channels": ["C000TEST01"]},
                                             BETA: {"channels": ["C000TEST01"]}}}))
        self.assertEqual(route.table(SID)["clashes"],
                         [{"channel": "c000test01", "projectIds": [ALPHA, BETA]}])
        [(_, dec)] = route.decide(SID, [entry(msg(1))])
        self.assertEqual(dec["how"], route.NEEDS_MODEL)

    # -- step 2: thread memory and a correction -----------------------------
    def test_a_reply_follows_its_thread(self):
        head = msg(1, channel="C000TEST05", name="made-up-five", thread="1")
        # The thread's first message was routed by the model (step 5).
        self.assertEqual(route.record(SID, [(entry(head), {"to": BETA, "how": route.BY_MODEL,
                                                          "why": "made-up reason"})]), [head.key])
        reply = msg(2, at=WED + 60, channel="C000TEST05", name="made-up-five", thread="1")
        stranger = msg(3, at=WED + 60, channel="C000TEST05", name="made-up-five", thread="3")
        self.assertEqual(route.route(SID, [entry(reply), entry(stranger)]), [reply.key])
        self.assertEqual(self.queued(), [(head.key, BETA), (reply.key, BETA)])
        self.assertEqual(route.log_lines()[0]["how"], "thread")
        self.assertEqual(route.thread_memory(SID)["C000TEST05:1"]["last"], WED + 60)

    def test_a_thread_routed_by_its_channel_keeps_going_there_after_the_channel_is_removed(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        head = msg(1, thread="1")
        route.route(SID, [entry(head)])
        route.set_row(SID, ALPHA, {"channels": []}, CEO)
        reply = msg(2, at=WED + 60, thread="1")
        route.route(SID, [entry(reply)])
        self.assertEqual(self.queued(), [(head.key, ALPHA), (reply.key, ALPHA)])

    def test_a_correction_changes_the_thread_for_every_later_reply(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        head = msg(1, thread="1")
        route.route(SID, [entry(head)])
        res = route.correct(SID, head.key, BETA, CEO, now=WED + 30)
        self.assertEqual(res, {"before": ALPHA, "to": BETA, "thread": "C000TEST01:1"})
        # The thread now goes to Beta, even though its channel is Alpha's.
        reply = msg(2, at=WED + 60, thread="1")
        route.route(SID, [entry(reply)])
        self.assertIn((reply.key, BETA), self.queued())
        new_thread = msg(3, at=WED + 60, thread="3")
        route.route(SID, [entry(new_thread)])
        self.assertIn((new_thread.key, ALPHA), self.queued(), "other threads follow the channel")
        lines = route.log_lines()
        corr = next(ln for ln in lines if ln["how"] == "correction")
        self.assertEqual((corr["before"], corr["to"], corr["by"]), (ALPHA, [BETA], CEO))
        self.assertEqual(next(ln for ln in lines if ln["key"] == reply.key)["why"],
                         "thread memory (corrected by the CEO)")
        # A second correction changes it again.
        route.correct(SID, reply.key, ALPHA, CEO, now=WED + 90)
        last = msg(4, at=WED + 120, thread="1")
        route.route(SID, [entry(last)])
        self.assertIn((last.key, ALPHA), self.queued())

    def test_only_the_ceo_corrects_and_only_a_routed_message(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        m = msg(1, thread="1")
        route.route(SID, [entry(m)])
        with self.assertRaises(route.Refused):
            route.correct(SID, m.key, BETA, agent(BETA))
        with self.assertRaises(route.Refused):
            route.correct(SID, "fake:example:C000TEST01:99", BETA, CEO)
        with self.assertRaises(route.Refused):
            route.correct(SID, m.key, "p-nobody", CEO)

    def test_a_message_older_than_its_kept_decision_is_corrected_by_its_thread(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        m = msg(1, thread="1")
        route.record(SID, [(entry(m), {"to": ALPHA, "how": route.BY_CHANNEL})], now=WED)
        later = WED + route.DECIDED_KEEP_S + 3600
        # Something else saved later drops the old decision; the thread stays
        # because a reply came in.
        route.record(SID, [(entry(msg(2, at=later - 60, thread="1")),
                            {"to": ALPHA, "how": route.BY_THREAD})], now=later)
        with self.assertRaises(route.Refused):
            route.correct(SID, m.key, BETA, CEO, now=later)
        res = route.correct(SID, m.key, BETA, CEO, now=later, thread="C000TEST01:1")
        self.assertEqual(res, {"before": ALPHA, "to": BETA, "thread": "C000TEST01:1"})
        self.assertEqual(route.thread_memory(SID)["C000TEST01:1"]["to"], BETA)

    def test_thread_memory_is_kept_30_days_after_the_last_message(self):
        route.record(SID, [(entry(msg(1, thread="1")), {"to": ALPHA, "how": route.BY_MODEL})],
                     now=WED)
        late = WED + route.THREAD_KEEP_S + 3600
        route.record(SID, [(entry(msg(9, at=late, channel="C000TEST05")),
                            {"to": ALPHA, "how": route.BY_MODEL})], now=late)
        self.assertNotIn("C000TEST01:1", route.thread_memory(SID))

    # -- the same message twice ---------------------------------------------
    def test_the_same_message_routed_twice_goes_to_the_same_initiative(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        m = msg(1)
        self.assertEqual(route.route(SID, [entry(m)]), [m.key])
        # The table changes, then the frame hands the message on again (the
        # hub stopped before it saved its unrouted list).
        route.set_row(SID, ALPHA, {"channels": []}, CEO)
        route.set_row(SID, BETA, {"channels": ["C000TEST01"]}, CEO)
        [(_, dec)] = route.decide(SID, [entry(m)])
        self.assertEqual((dec["to"], dec.get("again")), (ALPHA, True))
        self.assertEqual(route.route(SID, [entry(m)]), [m.key])
        self.assertEqual(self.queued(), [(m.key, ALPHA)], "never a second PO")
        self.assertEqual(len(route.log_lines()), 1, "logged once")
        # A model decision for it (step 5) cannot move it either.
        route.record(SID, [(entry(m), {"to": BETA, "how": route.BY_MODEL})])
        self.assertEqual(self.queued(), [(m.key, ALPHA)])

    def test_a_crash_before_the_unrouted_list_is_saved_reaches_one_po(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        fake = FakeSource()
        fake.messages.append(msg(1, at=WED + 10))

        class Shared(FakeSource):
            def __new__(cls):
                return fake

        with mock.patch.dict(sources.TYPES, {"fake": Shared}):
            outside.run_check(SID, WED)
            outside.run_check(SID, WED + 60)
            self.assertEqual(len(outside.pending(SID)), 1)
            def stop_on_save(sid, st):
                raise OSError("the hub stopped")

            with mock.patch.object(outside, "_save_state", stop_on_save):
                with self.assertRaises(OSError):
                    outside.hand_on(outside.ROUTER, [SID])
            self.assertEqual(len(outside.pending(SID)), 1, "still in the unrouted list")
            route.set_row(SID, ALPHA, {"channels": []}, CEO)
            route.set_row(SID, BETA, {"channels": ["C000TEST01"]}, CEO)
            self.assertEqual(outside.hand_on(outside.ROUTER, [SID]), 1)
        self.assertEqual(outside.pending(SID), [])
        self.assertEqual([to for _k, to in self.queued()], [ALPHA])

    def test_the_frame_routes_through_it_and_decide_only_stays_decide_only(self):
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        fake = FakeSource()

        class Shared(FakeSource):
            def __new__(cls):
                return fake

        outside.set_source(SID, type="fake", mode="decide", settings={"account": "made-up"})
        with mock.patch.dict(sources.TYPES, {"fake": Shared}):
            outside.maybe_tick(background=False, now=WED)
            fake.messages += [msg(1, at=WED + 10), msg(2, at=WED + 20, channel="C000TEST05")]
            outside.maybe_tick(background=False, now=WED + 60)
        self.assertEqual(fake.calls[-1]["places"], [{"channel": "C000TEST01"}])
        self.assertEqual(self.queued(), [(msg(1).key, ALPHA)])
        self.assertEqual(outside.queue_due(), [], "read in decide-only: never due")
        self.assertTrue(route.log_lines()[0]["decideOnly"])
        self.assertEqual([e["key"] for e in outside.pending(SID)], [msg(2, channel="C000TEST05").key],
                         "what needs a model waits in the unrouted list")

    # -- who may change a row ----------------------------------------------
    def test_an_agent_changes_only_its_own_initiatives_row(self):
        res = route.set_row(SID, ALPHA, {"channels": ["made-up-one"], "topic": "made-up pricing"},
                            agent(ALPHA), now=WED)
        self.assertTrue(res["changed"])
        for by in (agent(BETA), agent(""), {"kind": "agent", "projectId": BETA}):
            with self.subTest(by=by):
                with self.assertRaises(route.Refused):
                    route.set_row(SID, ALPHA, {"topic": "taken over"}, by)
        self.assertEqual(self.row(ALPHA)["values"]["topic"], "made-up pricing")
        with self.assertRaises(route.Refused):
            route.set_row(SID, ALPHA, {"topic": "x"}, {"kind": "someone"})
        with self.assertRaises(route.Refused):
            route.set_row(SID, "p-nobody", {"topic": "x"}, CEO)

    def test_every_change_is_kept_with_who_when_and_the_row_before_and_after(self):
        route.set_row(SID, ALPHA, {"channels": ["made-up-one"]}, agent(ALPHA, "codex"), now=WED)
        route.set_row(SID, ALPHA, {"topic": "made-up pricing"}, CEO, now=WED + 60)
        route.set_row(SID, ALPHA, {"topic": "made-up pricing"}, CEO, now=WED + 120)
        hist = route.history(SID)
        self.assertEqual(len(hist), 2, "a change that changes nothing is not kept")
        self.assertEqual(hist[0]["by"], CEO)
        self.assertEqual(hist[0]["at"], WED + 60)
        fake = {"fake": {"channels": ["made-up-one"]}}
        self.assertEqual(hist[0]["before"], {"topic": "", "items": [], "places": fake})
        self.assertEqual(hist[0]["after"], {"topic": "made-up pricing", "items": [], "places": fake})
        self.assertEqual(hist[1]["by"], {"kind": "agent", "identity": "codex", "projectId": ALPHA,
                                         "agent": "claude", "role": "engineer",
                                         "roomId": "room-made-up", "projectName": "Alpha"})
        self.assertEqual(hist[1]["before"], {"topic": "", "items": [], "places": {}})
        self.assertEqual(self.row(ALPHA)["changedBy"], CEO)
        self.assertEqual(route.history(SID, BETA), [])

    def test_bad_values_are_refused(self):
        for values in ({"words": ["x"]}, {}, {"topic": 5}, {"channels": {"a": 1}},
                       {"topic": "x" * (route.TEXT_CHARS + 1)},
                       {"channels": [f"c{i}" for i in range(route.LIST_MAX + 1)]}):
            with self.subTest(values=values):
                with self.assertRaises(route.Refused):
                    route.set_row(SID, ALPHA, values, CEO)
        res = route.set_row(SID, ALPHA, {"channels": "#a, b  #a", "topic": "  two   words "}, CEO)
        self.assertEqual(res["row"], {"channels": ["a", "b"], "topic": "two words"})

    # -- round 2 step 1: one table for every source ---------------------------
    def one_file(self) -> dict:
        return json.loads((self.state / "outside" / "routing.json").read_text())

    def old_table(self, sid: str = SID, topic: str = "made-up pricing") -> Path:
        path = self.state / "outside" / f"routing-{sid}.json"
        path.write_text(json.dumps({"rows": {ALPHA: {"channels": ["made-up-one"], "topic": topic}},
                                    "history": [
            {"at": WED, "projectId": ALPHA, "projectName": "Alpha", "by": CEO,
             "before": {"channels": [], "topic": ""},
             "after": {"channels": ["made-up-one"], "topic": ""}},
            {"at": WED + 60, "projectId": ALPHA, "projectName": "Alpha", "by": CEO,
             "before": {"channels": ["made-up-one"], "topic": ""},
             "after": {"channels": ["made-up-one"], "topic": topic}}]}))
        return path

    def test_a_per_source_table_is_carried_over_once_with_its_history(self):
        topic = ("made-up pricing, ABC-1, repo#2 " * 40)[:route.TEXT_CHARS - 1] + "x"   # #9's longest
        path = self.old_table(topic=topic)
        self.assertEqual(self.row(ALPHA)["values"], {"channels": ["made-up-one"], "topic": topic})
        self.assertFalse(path.exists())
        self.assertTrue(path.with_name(path.name + ".carried").exists(), "kept, never deleted")
        d = self.one_file()
        self.assertEqual(d["rows"][ALPHA], {"topic": topic, "items": [],
                                            "places": {"fake": {"channels": ["made-up-one"]}}})
        self.assertEqual([(h["source"], h["at"]) for h in d["history"]], [(SID, WED), (SID, WED + 60)])
        self.assertEqual(d["history"][0]["after"], {"topic": "", "places": {"fake": {"channels": ["made-up-one"]}}})
        self.assertEqual({k: d["carried"][SID][k] for k in ("type", "file", "rows", "changes")},
                         {"type": "fake", "file": f"routing-{SID}.json.carried", "rows": 1, "changes": 2})
        self.assertNotIn("notes", d["carried"][SID])
        [(_, dec)] = route.decide(SID, [entry(msg(1))])
        self.assertEqual((dec["to"], dec["how"]), (ALPHA, route.BY_CHANNEL), "it routes as before")
        # The hub stopped before the rename: carried again, nothing doubled.
        self.old_table(topic=topic)
        route.table(SID)
        again = self.one_file()
        self.assertEqual(again["rows"], d["rows"])
        self.assertEqual(len(again["history"]), 2)
        self.assertTrue(path.with_name(path.name + ".carried-2").exists())
        rec = again["carried"][SID]
        self.assertEqual((rec["file"], rec["rows"], rec["changes"], rec["also"]),
                         (f"routing-{SID}.json.carried", 1, 2, [f"routing-{SID}.json.carried-2"]),
                         "the first record stays, the second file is added")

    def test_a_rename_that_keeps_failing_does_not_grow_the_record(self):
        path = self.old_table()
        with mock.patch.object(Path, "rename", side_effect=OSError("made-up")):
            for _ in range(4):
                route.table(SID)
        self.assertTrue(path.exists(), "the old file stays where it was")
        rec = self.one_file()["carried"][SID]
        self.assertEqual((rec["file"], rec.get("also")), (f"routing-{SID}.json.carried", None))
        self.assertEqual(len(self.one_file()["history"]), 2, "nothing doubled")

    def test_two_old_tables_listing_one_channel_in_two_rows_say_so(self):
        outside.set_source("second", type="fake", mode="on", settings={"account": "made-up-2"})
        (self.state / "outside" / f"routing-{SID}.json").write_text(json.dumps(
            {"rows": {ALPHA: {"channels": ["general"]}}}))
        (self.state / "outside" / "routing-second.json").write_text(json.dumps(
            {"rows": {BETA: {"channels": ["#general", "boats"]}}}))
        t = route.one_table()
        self.assertEqual([(c["value"], c["projectIds"]) for c in t["clashes"]], [("general", [ALPHA, BETA])])
        self.assertEqual(t["carried"]["second"]["notes"],
                         ["Beta: #general is also in Alpha's row, so its messages go to neither "
                          "until it is taken out of one"])

    def test_a_bad_value_in_an_old_list_is_left_out_and_the_rest_kept(self):
        (self.state / "outside" / f"routing-{SID}.json").write_text(json.dumps(
            {"rows": {ALPHA: {"channels": ["ok", "x" * (route.ITEM_CHARS + 1), "next"]},
                      BETA: {"channels": "#one, two"}}}))
        self.assertEqual(self.row(ALPHA)["values"]["channels"], ["ok", "next"])
        self.assertEqual(self.row(BETA)["values"]["channels"], ["one", "two"], "a list written as text")
        [note] = route.one_table()["carried"][SID]["notes"]
        self.assertTrue(note.startswith("Alpha: a value of Channels was not carried over"), note)

    def test_a_ticket_key_typed_with_a_space_is_not_a_pull_request(self):
        with self.assertRaises(route.Refused) as cm:
            route.parse_item("ABC 12")
        self.assertIn("ABC-12", str(cm.exception))
        self.assertEqual(route.parse_item("example-repo 1234"), ("pr", "example-repo#1234"))
        self.assertEqual(route.parse_item("pr ABC 12"), ("pr", "abc#12"))
        self.assertEqual(route.parse_item("ABC#12"), ("pr", "abc#12"))
        with self.assertRaises(route.Refused) as cm:
            route.parse_item("ticket abc 12")
        self.assertIn("ABC-12", str(cm.exception), "said to be a ticket: the dash is suggested")

    def test_a_carried_topic_never_replaces_the_one_the_row_has(self):
        route.set_initiative(ALPHA, {"topic": "made-up risk"}, CEO, now=WED - 60)
        self.old_table()
        self.assertEqual(self.row(ALPHA)["values"], {"channels": ["made-up-one"], "topic": "made-up risk"})
        [note] = self.one_file()["carried"][SID]["notes"]
        self.assertIn("made-up pricing", note)
        self.assertEqual([h["at"] for h in route.history()], [WED + 60, WED, WED - 60],
                         "the carried changes are in time order with the row's own")

    def test_a_table_of_a_source_not_configured_is_left_until_it_is(self):
        path = self.old_table(sid="later")
        route.table(SID)
        self.assertTrue(path.exists())
        self.assertFalse((self.state / "outside" / "routing.json").exists())
        outside.set_source("later", type="fake", mode="on", settings={"account": "made-up-2"})
        self.assertEqual(self.row(ALPHA)["values"]["channels"], ["made-up-one"])
        self.assertFalse(path.exists())

    def test_two_sources_of_one_type_share_its_places(self):
        outside.set_source("second", type="fake", mode="on", settings={"account": "made-up-2"})
        route.set_row(SID, ALPHA, {"channels": ["made-up-one"]}, CEO)
        self.assertEqual(route.routed_sources(), [SID, "second"])
        second = next(r for r in route.table("second")["rows"] if r["projectId"] == ALPHA)
        self.assertEqual(second["values"]["channels"], ["made-up-one"])
        t = route.one_table()
        self.assertEqual([c["key"] for c in t["columns"]], ["topic", "items", "fake.channels"])
        self.assertEqual([x["kind"] for x in t["types"]], ["fake"])

    def test_work_items_are_kept_with_their_kind_and_who_listed_them(self):
        res = route.set_initiative(ALPHA, {"addItems": ["abc-1", "epic ABC-10", "Repo#12",
                                                        "https://github.com/made-up/Other/pull/7",
                                                        "https://made-up.example/browse/abc-2"]},
                                   agent(ALPHA), now=WED)
        self.assertEqual([(i["kind"], i["key"], i["from"], i["at"]) for i in res["row"]["items"]],
                         [("ticket", "ABC-1", "po", WED), ("epic", "ABC-10", "po", WED),
                          ("pr", "repo#12", "po", WED), ("pr", "other#7", "po", WED),
                          ("ticket", "ABC-2", "po", WED)])
        # The whole list: kept items keep who listed them.
        res = route.set_initiative(ALPHA, {"items": "ABC-1, epic ABC-10, ABC-3"}, CEO, now=WED + 60)
        self.assertEqual([(i["key"], i["from"]) for i in res["row"]["items"]],
                         [("ABC-1", "po"), ("ABC-10", "po"), ("ABC-3", "ceo")])
        res = route.set_initiative(ALPHA, {"removeItems": ["abc-3"]}, agent(ALPHA), now=WED + 120)
        self.assertEqual([i["key"] for i in res["row"]["items"]], ["ABC-1", "ABC-10"])
        for change in ({"removeItems": ["ABC-99"]}, {"addItems": ["not an item"]},
                       {"addItems": [{"kind": "pr", "key": "ABC-1"}]},
                       {"items": [f"ABC-{i}" for i in range(route.ITEMS_MAX + 1)]}):
            with self.subTest(change=change):
                with self.assertRaises(route.Refused):
                    route.set_initiative(ALPHA, change, CEO)
        with self.assertRaises(route.Refused) as cm:
            route.set_initiative(BETA, {"addItems": ["ABC-1"]}, CEO)
        self.assertIn("Alpha's row", str(cm.exception))
        self.assertEqual(route.one_table()["rows"][0]["values"]["items"], ["ABC-1", "epic ABC-10"])

    def test_a_place_or_item_two_rows_list_in_a_file_edited_by_hand_shows_as_a_clash(self):
        (self.state / "outside" / "routing.json").write_text(json.dumps({"version": 1, "rows": {
            ALPHA: {"items": [{"kind": "ticket", "key": "ABC-1"}],
                    "places": {"fake": {"channels": ["C000TEST01"]}}},
            BETA: {"items": [{"kind": "ticket", "key": "ABC-1"}],
                   "places": {"fake": {"channels": ["c000test01"]}}}}}))
        self.assertEqual([(c["column"], c["value"], c["shown"], c["projectIds"])
                          for c in route.one_table()["clashes"]],
                         [("fake.channels", "C000TEST01", "#C000TEST01", [ALPHA, BETA]),
                          ("items", "ABC-1", "ABC-1", [ALPHA, BETA])])
        [(_, dec)] = route.decide(SID, [entry(msg(1))])
        self.assertEqual(dec["how"], route.NEEDS_MODEL)

    def tool(self, project: str, **args):
        hub = types.SimpleNamespace(load_projects=lambda: list(self.projects), outside=outside)
        ctx = {"room": {"id": "room-made-up"}, "identity": "claude", "projectId": project,
               "part": {"agent": "codex", "role": "po"}}
        with mock.patch.object(ensemble_tools, "_d", hub):
            return ensemble_tools._routing(ctx, args, None)

    def test_the_tool_changes_the_callers_own_row_only(self):
        out = self.tool(ALPHA, action="set", channels=["made-up-one"], topic="made-up pricing")
        self.assertEqual((out["initiative"], out["changed"]), ("Alpha", True))
        self.assertEqual(out["row"]["places"], {"fake": {"channels": ["made-up-one"]}})
        self.assertEqual(route.history(SID)[0]["by"]["identity"], "claude")
        self.assertEqual(route.history(SID)[0]["by"]["agent"], "codex")
        # No argument names another initiative: Beta's agent changes Beta's row.
        out = self.tool(BETA, action="set", topic="made-up risk", projectId=ALPHA)
        self.assertEqual(out["initiative"], "Beta")
        self.assertEqual(self.row(ALPHA)["values"]["topic"], "made-up pricing")
        with self.assertRaises(ensemble_tools.ToolError) as cm:
            self.tool(BETA, action="set", channels=["#made-up-one"])
        self.assertIn("Alpha's row", str(cm.exception))
        with self.assertRaises(ensemble_tools.ToolError):
            self.tool("", action="set", topic="no initiative")
        read = self.tool(BETA, history=True)
        self.assertEqual([(r["projectId"], r["yours"]) for r in read["rows"]],
                         [(ALPHA, False), (BETA, True)])
        self.assertEqual(len(read["yourRowHistory"]), 1)

    def test_the_tool_adds_items_as_the_pos_and_takes_places(self):
        out = self.tool(ALPHA, action="set", addItems=["ABC-1"],
                        places={"fake": {"channels": ["made-up-one"]}})
        self.assertEqual([(i["key"], i["from"]) for i in out["row"]["items"]], [("ABC-1", "po")])
        self.assertEqual(out["row"]["places"], {"fake": {"channels": ["made-up-one"]}})
        out = self.tool(ALPHA, action="set", removeItems=["ABC-1"], channels=[], source=SID)
        self.assertEqual((out["row"]["items"], out["row"]["places"]), ([], {}))
        with self.assertRaises(ensemble_tools.ToolError):
            self.tool(ALPHA, action="set", channels=["x"], source="not-there")
        read = self.tool(ALPHA)
        self.assertEqual([c["key"] for c in read["columns"]], ["topic", "items", "fake.channels"])
        self.assertNotIn("itemsNotAdded", read)
        # An item the hub found for Beta but Alpha's row has: Beta's PO is told, Alpha's is not.
        self.tool(ALPHA, action="set", addItems=["ABC-2"])
        route.add_items_auto({BETA: [("ticket", "ABC-2", "task #3")]}, "named by its tasks", WED)
        [n] = self.tool(BETA)["itemsNotAdded"]["items"]
        self.assertEqual((n["key"], n["from"], n["keptName"]), ("ABC-2", "task #3", "Alpha"))
        self.assertNotIn("itemsNotAdded", self.tool(ALPHA))

    def test_the_agents_of_a_routed_project_are_offered_the_tool(self):
        # #409: ensemble_routing is offered only once a source is set up, and
        # only to the agents (PO and tasks) of a project with a routing row:
        # the CEO gives a project its first row in the setup file.
        hub = types.SimpleNamespace(
            chatroom=types.SimpleNamespace(participant=lambda room, ident: {"role": "reviewer"},
                                           is_on_mention=lambda room, part: False),
            may_restart_hub=lambda rid, ident: False, outside=outside,
            load_session_projects=lambda: {}, load_projects=lambda: list(self.projects),
            _project_for_cwd=lambda cwd, projects: "")

        def names(pid):
            with mock.patch.object(ensemble_tools, "_d", hub), \
                    mock.patch.object(ensemble_tools, "is_admin_caller", return_value=False), \
                    mock.patch.object(ensemble_tools, "_is_project_po", return_value=False):
                return [t["name"] for t in ensemble_tools.tool_schemas(
                    {"id": "room-x", "projectId": pid}, "claude-2")]
        self.assertNotIn("ensemble_routing", names(ALPHA))
        self.tool(ALPHA, action="set", channels=["made-up-one"])
        self.assertIn("ensemble_routing", names(ALPHA))
        self.assertNotIn("ensemble_routing", names(BETA))
        self.assertNotIn("ensemble_routing", names(""))
        # The last source removed: no agent is offered it, the row kept or not.
        outside.remove_source(SID)
        self.assertEqual(outside.load_config(), {})
        self.assertNotIn("ensemble_routing", names(ALPHA))
        self.assertIn("ensemble_routing", ensemble_tools.NAMES)

    # -- the log -------------------------------------------------------------
    def test_links_in_the_log_are_short(self):
        cases = {
            "see https://github.com/example-org/example-repo/pull/1234/files": "see example-repo 1234",
            "<https://example.atlassian.net/browse/ABC-123|the ticket> done":
                "ABC-123 done",
            "<https://example.slack.com/archives/C000TEST01/p1700000000000100>": "Slack message",
            "issue https://github.com/example-org/ensemble/issues/12.": "issue ensemble 12.",
            "doc <https://docs.example.com/a/b|Plan> and https://www.example.com/x?y=1":
                "doc Plan and example.com link",
            "no link here": "no link here",
        }
        for text, short in cases.items():
            with self.subTest(text=text):
                self.assertEqual(route.short_links(text), short)
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        m = msg(1, text="please look https://github.com/example-org/example-repo/pull/1234 " + "x" * 400)
        route.route(SID, [entry(m)])
        ln = route.log_lines()[0]
        self.assertTrue(ln["text"].startswith("please look example-repo 1234 x"))
        self.assertEqual(len(ln["text"]), route.LOG_TEXT_CHARS)
        self.assertGreater(ln["more"], 0)

    def test_log_lines_are_kept_90_days(self):
        logdir = self.state / "outside" / "log"
        logdir.mkdir(parents=True)
        old = WED - route.LOG_KEEP_S - 40 * 86400
        (logdir / f"{route._month(old)}.jsonl").write_text(
            json.dumps({"at": old, "source": SID, "key": "fake:example:C000TEST01:0"}) + "\n")
        route.set_row(SID, ALPHA, {"channels": ["C000TEST01"]}, CEO)
        with mock.patch.object(route.time, "time", return_value=WED):
            route.route(SID, [entry(msg(1))])
        self.assertEqual([p.stem for p in logdir.glob("*.jsonl")], [route._month(WED)])
        self.assertEqual(len(route.log_lines(SID, now=WED)), 1)
        self.assertEqual(route.log_lines("another-source", now=WED), [])


class PageApi(unittest.TestCase):
    """The page's API: reading the log is refused to an agent; the routing
    table's page endpoints are gone (the setup file, #15)."""

    def setUp(self):
        import dashboard
        self.dashboard = dashboard
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.sent = []
        self.handler._send_json = lambda code, payload: self.sent.append((code, payload))

    def test_reading_the_log_is_refused_to_an_agent(self):
        u = types.SimpleNamespace(query="source=made-up")
        self.handler._page_read_ok = lambda: True
        self.handler._agent_peer = lambda: "it was started by an agent"
        self.handler._outside_get("/api/outside/log", u)
        self.assertEqual(self.sent[-1][0], 403)
        self.handler._page_read_ok = lambda: False
        self.handler._agent_peer = lambda: ""
        self.handler._outside_get("/api/outside/log", u)
        self.assertEqual(self.sent[-1][0], 403)
        self.handler._page_read_ok = lambda: True
        with mock.patch.object(route, "log_lines", return_value=[]) as lines:
            self.handler._outside_get("/api/outside/log", u)
        self.assertEqual(self.sent[-1], (200, {"lines": []}))
        lines.assert_called_once_with("made-up", 200)

    def test_the_routing_page_endpoints_are_gone(self):
        self.handler._page_refusal = lambda: ""
        self.handler._page_read_ok = lambda: True
        self.handler._agent_peer = lambda: ""
        with mock.patch.object(route, "set_row") as set_row:
            self.handler._outside_post("/api/outside/routing",
                                       {"projectId": ALPHA, "values": {"topic": "made-up"}})
            self.assertEqual(self.sent[-1][0], 404)
            self.handler._outside_get("/api/outside/routing", types.SimpleNamespace(query=""))
            self.assertEqual(self.sent[-1][0], 404)
        set_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()
