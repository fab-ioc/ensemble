"""The log view of outside messages (outside_log.py, step 7): one row per
logged message with where it is now and why, the filters, and the CEO's
three actions, each done once and only once: Send elsewhere (the first PO
never sees a message taken back before it was typed, and is told quietly,
without a wake, when it was typed), Send anyway (a "no match" message), and
Make a rule (kept in the routing table's history as the CEO's; a channel in
another row is refused). Made-up ids only (the repo is public)."""
from __future__ import annotations

import json
import threading
import time
import types
from unittest import mock

import chatroom
import dashboard
import outside
import outside_deliver as od
import outside_log as olog
import outside_route as route
import quiet_reports
import sources
from sources.base import Author, Message, Place, Thread
from sources.fake import FakeSource

from tests.test_outside_deliver import _World

SID = "made-up"
OPTEN, DOCK, ALT = "proj-opten", "proj-dock", "proj-alt"


def msg(n: int, at: float, channel: str = "C000TEST01", name: str = "made-up-one",
        kind: str = "channel", thread: str = "") -> Message:
    return Message(key=f"fake:example:{channel}:{n}", source="slack",
                   place=Place(id=channel, name=name, kind=kind),
                   author=Author(id="U000TEST02", name="Made-up Person"), at=float(at),
                   text=f"message {n} about made-up things",
                   link=f"https://example.slack.com/archives/{channel}/p{n}",
                   thread=Thread(id=f"{channel}:{thread}", first=thread == str(n)) if thread else None)


class _Log(_World):
    def setUp(self):
        super().setUp()
        self.now = time.time()          # the whole messages are kept 30 days
        for p in (mock.patch.dict(sources.TYPES, {"fake": FakeSource}),
                  mock.patch.object(route, "_log"),
                  mock.patch.object(quiet_reports, "_STATE", None)):
            p.start()
            self.addCleanup(p.stop)
        outside.set_source(SID, type="fake", mode="on", settings={"account": "made-up"})
        outside.remove_source("slack")     # the world's own, for the tools only

    def routed(self, n: int, to: str = OPTEN, decide: bool = False, **kw) -> str:
        m = msg(n, self.now - 60, **kw)
        e = {"key": m.key, "message": m.to_dict(), "readAt": self.now, "decideOnly": decide}
        route.record(SID, [(e, {"to": to, "how": route.BY_CHANNEL, "why": "channel #made-up-one"})],
                     now=self.now)
        return m.key

    def nomatch(self, n: int, **kw) -> str:
        m = msg(n, self.now - 60, **kw)
        e = {"key": m.key, "message": m.to_dict(), "readAt": self.now}
        route.record(SID, [(e, {"to": "", "dropped": route.NO_MATCH, "how": route.BY_MODEL,
                                "why": "about nothing an initiative does"})], now=self.now)
        return m.key

    def row(self, key: str) -> dict:
        return next(r for r in olog.rows()["rows"] if r["key"] == key)

    def queued(self) -> list[tuple[str, str]]:
        return sorted((i["key"], i["to"]) for i in outside.queue_items())

    def typed_keys(self, pty: str) -> int:
        return len(self.typed(pty))


class Rows(_Log):
    def test_each_message_is_one_row_with_where_it_went_and_why(self):
        a = self.routed(1)
        b = self.nomatch(2, kind="dm", name="")
        c = self.routed(3, to=DOCK, channel="C000TEST02", name="made-up-two")
        got = olog.rows()
        self.assertEqual([r["key"] for r in got["rows"]], [c, b, a], "newest first")
        ra = self.row(a)
        self.assertEqual((ra["who"], ra["where"], ra["toName"], ra["how"], ra["why"], ra["noMatch"]),
                         ("Made-up Person", "#made-up-one", "opten", "channel",
                          "channel #made-up-one", False))
        self.assertEqual(ra["text"], "message 1 about made-up things")
        self.assertEqual((ra["sourceLabel"], ra["canSend"], ra["rule"]), ("Made-up source", True, "offer"))
        rb = self.row(b)
        self.assertEqual((rb["noMatch"], rb["to"], rb["where"], rb["rule"]), (True, "", "DM", ""))
        self.assertEqual(got["sources"], [{"id": SID, "label": "Made-up source"}])
        self.assertIn({"id": DOCK, "name": "Dock"}, got["initiatives"])
        self.assertEqual(self.row(c)["toName"], "Dock")

    def test_the_filters(self):
        a = self.routed(1)
        b = self.nomatch(2)
        c = self.routed(3, to=DOCK)
        keys = lambda **kw: {r["key"] for r in olog.rows(**kw)["rows"]}   # noqa: E731
        self.assertEqual(keys(project_id=OPTEN), {a})
        self.assertEqual(keys(project_id=DOCK), {c})
        self.assertEqual(keys(no_match=True), {b})
        self.assertEqual(keys(source_id=SID), {a, b, c})
        self.assertEqual(keys(source_id="another"), set())
        self.assertEqual(len(olog.rows(limit=1)["rows"]), 1)
        self.assertEqual(olog.rows(limit=1)["total"], 3)
        # A moved message shows for the initiative it left too, with the move.
        olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(keys(project_id=OPTEN), {a})
        self.assertEqual(keys(project_id=DOCK), {a, c})
        self.assertEqual([r["key"] for r in olog.recent_for(DOCK)], [c, a])
        self.assertEqual(olog.recent_for(OPTEN), [], "an initiative's page lists what it has now")

    def test_small_talk_is_never_listed(self):
        m = msg(5, self.now)
        e = {"key": m.key, "message": m.to_dict(), "readAt": self.now}
        route.record(SID, [(e, {"to": "", "dropped": route.SMALL_TALK, "how": route.BY_MODEL})],
                     now=self.now)
        self.assertEqual(olog.rows()["rows"], [])

    def test_a_channel_another_row_lists_says_so_instead_of_offering_a_rule(self):
        route.set_row(SID, DOCK, {"channels": ["made-up-one"]}, {"kind": "ceo"})
        a = self.routed(1)
        self.assertEqual(self.row(a)["rule"], "#made-up-one is in Dock's row")


class SendElsewhere(_Log):
    def test_before_it_was_typed_the_first_po_never_sees_it(self):
        a = self.routed(1, thread="1")
        self.assertEqual(self.queued(), [(a, OPTEN)])
        res = olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(res["message"], "Sent to Dock. Opten's PO had not been given it.")
        self.assertEqual(self.queued(), [(a, DOCK)])
        self.tick()
        self.assertEqual(self.typed_keys("pty-opten"), 0)
        self.assertEqual(self.typed_keys("pty-dock"), 1)
        self.assertIn(od.msg_id(a), self.typed("pty-dock")[0])
        self.assertEqual(self.notices(self.opten), [])
        self.assertEqual(quiet_reports.pending(self.opten), [])
        # Its thread's memory follows the move.
        self.assertEqual(route.thread_memory(SID)["C000TEST01:1"]["to"], DOCK)
        r = res["row"]
        self.assertEqual((r["to"], r["toName"]), (DOCK, "Dock"))
        [ev] = r["events"]
        self.assertEqual(ev["text"], "Moved from opten to Dock by you (opten's PO had not been "
                                     "given it)")

    def test_a_held_message_taken_back_cannot_be_read_by_the_first_po(self):
        a = self.routed(1)
        st = od._load()
        st["wakes"][self.opten] = [self.now - 60] * od.WAKES_PER_HOUR
        od._save(st)
        self.tick()                                 # held: kept for the read tool
        self.assertIn(od.msg_id(a), {r["id"] for r in od.records(OPTEN)})
        olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(od.records(OPTEN), [])

    def test_after_it_was_typed_the_first_po_is_told_quietly(self):
        a = self.routed(1)
        self.tick()
        self.assertEqual(self.typed_keys("pty-opten"), 1)
        res = olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(res["message"], "Sent to Dock. Opten's PO is told quietly.")
        self.now += 15
        self.tick()
        self.assertEqual(self.typed_keys("pty-opten"), 1, "the first PO is not woken for it")
        self.assertEqual(self.typed_keys("pty-dock"), 1)
        moved = [n for n in self.notices(self.opten) if n.get("movedTo")]
        self.assertEqual(len(moved), 1)
        self.assertIn("Moved by the CEO to Dock", moved[0]["text"])
        [q] = quiet_reports.pending(self.opten)
        self.assertIn("moved by the CEO to Dock", q["line"])
        # Its read tool says so.
        got = self.ok(self.opten, "ensemble_read_outside")
        [m] = [x for x in got["messages"] if x["id"] == od.msg_id(a)]
        self.assertEqual(m["movedAway"], "the CEO sent it to Dock: it is not yours")

    def test_twice_is_refused_and_it_is_delivered_once(self):
        a = self.routed(1)
        olog.send_elsewhere(SID, a, DOCK)
        with self.assertRaisesRegex(olog.Refused, "in Dock already"):
            olog.send_elsewhere(SID, a, DOCK)
        for _ in range(3):
            self.tick()
            self.now += 15
        self.assertEqual(self.typed_keys("pty-dock"), 1)
        self.assertEqual(len(self.row(a)["events"]), 1)

    def test_two_presses_at_once_deliver_once(self):
        a = self.routed(1)
        got, errs = [], []
        go = threading.Barrier(2)

        def press():
            go.wait()
            try:
                got.append(olog.send_elsewhere(SID, a, DOCK))
            except olog.Refused as e:
                errs.append(str(e))
        ts = [threading.Thread(target=press) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual((len(got), len(errs)), (1, 1))
        self.assertEqual(self.queued(), [(a, DOCK)])

    def test_back_to_an_initiative_that_was_given_it_is_refused(self):
        a = self.routed(1)
        self.tick()
        olog.send_elsewhere(SID, a, DOCK)
        with self.assertRaisesRegex(olog.Refused, "opten has this message already"):
            olog.send_elsewhere(SID, a, OPTEN)

    def test_refused_for_a_no_match_message_and_an_unknown_initiative(self):
        b = self.nomatch(2)
        with self.assertRaisesRegex(olog.Refused, "use Send anyway"):
            olog.send_elsewhere(SID, b, DOCK)
        a = self.routed(1)
        with self.assertRaisesRegex(olog.Refused, "no initiative"):
            olog.send_elsewhere(SID, a, "proj-none")
        with self.assertRaisesRegex(olog.Refused, "not in the log"):
            olog.send_elsewhere(SID, "fake:nothing", DOCK)

    def test_a_message_no_longer_kept_whole_cannot_be_sent(self):
        a = self.routed(1)
        route._kept_file(SID).unlink()
        with self.assertRaisesRegex(olog.Refused, "no longer kept whole"):
            olog.send_elsewhere(SID, a, DOCK)
        self.assertFalse(self.row(a)["canSend"])
        self.assertEqual(self.queued(), [(a, OPTEN)], "nothing changed")

    def test_decide_only_changes_where_it_belongs_but_delivers_nothing(self):
        a = self.routed(1, decide=True)
        res = olog.send_elsewhere(SID, a, DOCK)
        self.assertIn("Decide only", res["message"])
        self.tick()
        self.assertEqual((self.typed_keys("pty-opten"), self.typed_keys("pty-dock")), (0, 0))
        self.assertEqual(self.row(a)["to"], DOCK)

    def test_a_message_whose_decision_was_forgotten_still_moves(self):
        # A DM has no thread; once its decision has left memory (it keeps
        # the newest 2000) the move must still happen, not stop half-way
        # after the first copy was taken back.
        a = self.routed(1, kind="dm", name="")
        mem = route._load_memory(SID)
        mem["messages"].pop(a)
        route._save_memory(SID, mem, self.now)
        res = olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(res["message"], "Sent to Dock. Opten's PO had not been given it.")
        self.assertEqual(self.queued(), [(a, DOCK)])
        r = res["row"]
        self.assertEqual(r["to"], DOCK)
        self.assertEqual(r["events"][0]["text"], "Moved from opten to Dock by you (opten's PO had "
                                                 "not been given it)")

    def test_the_work_items_a_message_names_move_with_it(self):
        route.set_initiative(OPTEN, {"addItems": ["ABC-1", "ABC-2"]}, {"kind": "ceo"}, now=self.now)
        m = Message(key="fake:example:C000TEST01:77", source="slack",
                    place=Place(id="C000TEST01", name="made-up-one", kind="channel"),
                    author=Author(id="U000TEST02", name="Made-up Person"), at=self.now - 60,
                    text="ABC-1 is ready, ABC-3 comes next")
        e = {"key": m.key, "message": m.to_dict(), "readAt": self.now, "decideOnly": False}
        route.record(SID, [(e, {"to": OPTEN, "how": route.BY_CHANNEL, "why": "channel #made-up-one"})],
                     now=self.now)
        items = lambda pid: [(i["key"], i["from"]) for r in route.one_table()["rows"]   # noqa: E731
                             if r["projectId"] == pid for i in r["items"]]
        self.assertEqual(items(OPTEN), [("ABC-1", "ceo"), ("ABC-2", "ceo"), ("ABC-3", "routed")])
        olog.send_elsewhere(SID, m.key, DOCK, now=self.now + 60)
        self.assertEqual(items(OPTEN), [("ABC-2", "ceo")])
        self.assertEqual(items(DOCK), [("ABC-1", "send elsewhere"), ("ABC-3", "send elsewhere")])
        # The thread's next message naming ABC-1 now goes to Dock by its item.
        n = Message(key="fake:example:C000TEST01:78", source="slack", place=m.place, author=m.author,
                    at=self.now, text="ABC-1 merged")
        [(_, dec)] = route.decide(SID, [{"key": n.key, "message": n.to_dict(), "readAt": self.now}])
        self.assertEqual((dec["to"], dec["how"]), (DOCK, route.BY_ITEM))

    def went(self, conv: str) -> list[str]:
        """Where the earlier messages the model sees for ``conv`` went."""
        return [x["to"] for x in route._load_memory(SID)["recent"].get(conv) or []]

    def test_a_dm_message_moved_shows_its_new_initiative_to_the_model(self):
        # The model deciding the DM's next message sees where the CEO sent
        # this one (#11).
        a = self.routed(1, kind="dm", name="", channel="D000TEST01")
        self.assertEqual(self.went("place D000TEST01"), [OPTEN])
        olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(self.went("place D000TEST01"), [DOCK])
        nxt = msg(2, self.now + 60, channel="D000TEST01", name="", kind="dm")
        [(_e, d)] = route.decide(SID, [{"key": nxt.key, "message": nxt.to_dict()}])
        self.assertEqual(d["how"], route.NEEDS_MODEL)
        [got] = route.recent(SID, [nxt])["place D000TEST01"]
        self.assertEqual(got["to"], DOCK)

    def test_a_dm_thread_reply_moved_moves_its_thread_and_what_the_model_sees_of_it(self):
        self.routed(1, kind="dm", name="", channel="D000TEST01")
        b = self.routed(3, kind="dm", name="", channel="D000TEST01", thread="2")
        olog.send_elsewhere(SID, b, DOCK)
        self.assertEqual(route.thread_memory(SID)["D000TEST01:2"]["to"], DOCK)
        self.assertEqual(self.went("thread D000TEST01:2"), [DOCK])
        self.assertEqual(self.went("place D000TEST01"), [OPTEN])

    def test_a_routing_refusal_comes_back_as_words_not_an_error(self):
        a = self.routed(1)
        with mock.patch.object(route, "correct", side_effect=route.Refused("made-up refusal")):
            with self.assertRaisesRegex(olog.Refused, "made-up refusal"):
                olog.ACTIONS["move"](SID, a, DOCK)

    def test_a_team_po_being_started_does_not_get_a_message_moved_meanwhile(self):
        a = self.routed(1)
        st = od._load()
        st["resuming"][self.opten] = {"at": self.now, "key": "", "items": [[a, OPTEN]]}
        od._save(st)
        res = olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(res["message"], "Sent to Dock. Opten's PO had not been given it.")
        self.assertEqual(self.queued(), [(a, DOCK)])
        # Nothing is left for its line: the start record goes, as when its
        # messages leave the queue some other way.
        self.assertNotIn(self.opten, od._load()["resuming"])
        self.assertEqual(self.notices(self.opten), [])

    def _stopped_opten_started_for(self, n: int) -> str:
        """opten's PO is stopped; a look starts it with message n's line."""
        self.ptys.pop("pty-opten")
        chatroom.patch_participant(self.opten, "claude", {"sessionId": "sess-opten"})
        a = self.routed(n)
        self.tick()
        [(rid, _, key)] = self.launcher.calls
        self.assertEqual((rid, key), (self.opten, f"outside:{od.msg_id(a)}"))
        return a

    def test_a_po_whose_start_fails_after_the_move_is_never_typed_it_later(self):
        a = self._stopped_opten_started_for(1)
        res = olog.send_elsewhere(SID, a, DOCK)
        self.assertEqual(res["message"], "Sent to Dock. Opten's PO is told quietly.")
        self.assertEqual(self.queued(), [(a, DOCK)])
        # The start fails after the move: the held line is dropped, so no
        # later start of opten's PO carries it in.
        self.launcher.held[self.opten]["state"] = "failed"
        self.now += 15
        self.tick()
        self.assertNotIn(self.opten, self.launcher.held)
        self.assertNotIn(self.opten, od._load()["resuming"])
        self.assertEqual(self.queued(), [], "the Dock copy was typed to Dock")
        self.assertEqual(self.typed_keys("pty-dock"), 1)

    def test_a_start_still_under_way_keeps_its_line_and_counts_as_given(self):
        a = self._stopped_opten_started_for(1)
        olog.send_elsewhere(SID, a, DOCK)
        self.now += 15
        self.tick()
        # Nothing is queued for opten any more, but its start is under way:
        # its record stays until the start ends.
        self.assertIn(self.opten, od._load()["resuming"])
        self.assertIn(self.opten, self.launcher.held)
        self.launcher.typed_in(self.opten)
        self.now += 15
        self.tick()
        self.assertNotIn(self.opten, od._load()["resuming"])
        # It was typed to opten's PO with its start: moving it back is refused.
        self.assertTrue(od.was_given(a, OPTEN))
        with self.assertRaisesRegex(olog.Refused, "opten has this message already"):
            olog.send_elsewhere(SID, a, OPTEN)

    def test_an_initiative_whose_po_room_was_typed_it_is_not_given_it_again(self):
        # Alt is led from opten's PO room: opten's PO was typed the message,
        # so delivery would skip it for Alt without a word.
        a = self.routed(1)
        self.tick()
        self.assertEqual(self.typed_keys("pty-opten"), 1)
        self.assertTrue(od.was_given(a, ALT))
        olog.send_elsewhere(SID, a, DOCK)
        with self.assertRaisesRegex(olog.Refused, "Alt has this message already"):
            olog.send_elsewhere(SID, a, ALT)
        self.assertFalse(od.was_given(a, DOCK), "queued, not typed yet")

    def test_with_one_initiative_a_routed_message_offers_no_send(self):
        a = self.routed(1)
        b = self.nomatch(2)
        with mock.patch.object(route, "initiatives", return_value={OPTEN: "opten"}):
            got = {r["key"]: r["canSend"] for r in olog.rows()["rows"]}
        self.assertEqual(got, {a: False, b: True})


class SendAnyway(_Log):
    def test_a_no_match_message_goes_to_the_picked_initiative_once(self):
        b = self.nomatch(2, thread="2")
        res = olog.send_anyway(SID, b, DOCK)
        self.assertEqual(res["message"], "Sent to Dock.")
        self.assertEqual(self.queued(), [(b, DOCK)])
        r = res["row"]
        self.assertEqual((r["noMatch"], r["to"], r["events"][0]["text"]),
                         (False, DOCK, "Sent anyway to Dock by you"))
        with self.assertRaisesRegex(olog.Refused, "use Send elsewhere"):
            olog.send_anyway(SID, b, DOCK)
        for _ in range(2):
            self.tick()
            self.now += 15
        self.assertEqual(self.typed_keys("pty-dock"), 1)
        self.assertEqual(route.thread_memory(SID)["C000TEST01:2"]["to"], DOCK)
        self.assertEqual(olog.rows(no_match=True)["rows"], [])


    def test_a_dm_message_sent_anyway_shows_its_initiative_to_the_model(self):
        b = self.nomatch(2, kind="dm", name="", channel="D000TEST01")
        kept = lambda: [x["to"] for x in route._load_memory(SID)["recent"]["place D000TEST01"]]
        self.assertEqual(kept(), [""])
        olog.send_anyway(SID, b, DOCK)
        self.assertEqual(kept(), [DOCK])


class MakeRule(_Log):
    def test_the_channel_goes_into_the_row_as_the_ceos_change(self):
        a = self.routed(1)
        route.set_row(SID, DOCK, {"channels": ["made-up-other"]}, {"kind": "ceo"})
        res = olog.make_rule(SID, a, DOCK)
        self.assertIn("#made-up-one is in Dock's row now", res["message"])
        dock = next(r for r in route.table(SID)["rows"] if r["projectId"] == DOCK)
        self.assertEqual(dock["values"]["channels"], ["made-up-other", "made-up-one"])
        h = route.history(SID, DOCK)[0]
        self.assertEqual(h["by"], {"kind": "ceo"})
        self.assertEqual(res["row"]["events"][-1]["text"], "Rule made: #made-up-one goes to Dock")
        self.assertEqual(res["row"]["to"], OPTEN, "the message itself stays where it is")
        self.assertEqual(res["row"]["rule"], "#made-up-one is in Dock's row")
        with self.assertRaisesRegex(olog.Refused, "in Dock's row already"):
            olog.make_rule(SID, a, DOCK)
        self.assertEqual(len(route.history(SID, DOCK)), 2)

    def test_a_channel_in_another_row_is_refused_with_the_tables_words(self):
        route.set_row(SID, OPTEN, {"channels": ["made-up-one"]}, {"kind": "ceo"})
        a = self.routed(1)
        with self.assertRaisesRegex(olog.Refused, "already in opten's row"):
            olog.make_rule(SID, a, DOCK)

    def test_a_loose_section_not_applied_yet_is_left_alone(self):
        import outside_setup
        a = self.routed(1)
        path = outside_setup.setup_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Outside sources setup\n\n## Dock\nplease read what people say about docks\n")
        with self.assertRaisesRegex(olog.Refused, "written loosely"):
            olog.make_rule(SID, a, DOCK)
        self.assertEqual(route.history(SID, DOCK), [])
        self.assertIn("please read what people say about docks", path.read_text())

    def test_a_message_not_in_a_channel_has_no_rule(self):
        b = self.nomatch(2, kind="dm", name="")
        with self.assertRaisesRegex(olog.Refused, "not in a channel"):
            olog.make_rule(SID, b, DOCK)


class PageApi(_Log):
    def setUp(self):
        super().setUp()
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.sent = []
        self.handler._send_json = lambda code, payload: self.sent.append((code, payload))
        self.handler._page_refusal = lambda: ""
        self.handler._page_read_ok = lambda: True
        self.handler._agent_peer = lambda: ""

    def test_the_view_and_an_action_through_the_api(self):
        a = self.routed(1)
        self.handler._outside_get("/api/outside/messages",
                                  types.SimpleNamespace(query=f"source={SID}&project={OPTEN}"))
        code, body = self.sent[-1]
        self.assertEqual((code, [r["key"] for r in body["rows"]]), (200, [a]))
        self.handler._outside_post("/api/outside/messages",
                                   {"action": "move", "source": SID, "key": a, "to": DOCK})
        self.assertEqual(self.sent[-1][0], 200)
        self.handler._outside_post("/api/outside/messages",
                                   {"action": "move", "source": SID, "key": a, "to": DOCK})
        self.assertEqual(self.sent[-1], (400, {"error": "refused", "message": "it is in Dock already"}))
        self.handler._outside_get("/api/outside/messages",
                                  types.SimpleNamespace(query=f"recent={DOCK}"))
        self.assertEqual([r["key"] for r in self.sent[-1][1]["rows"]], [a])
        self.assertIs(self.sent[-1][1]["routed"], True)

    def test_an_agent_cannot_read_or_act(self):
        a = self.routed(1)
        self.handler._agent_peer = lambda: "it was started by an agent"
        self.handler._outside_get("/api/outside/messages", types.SimpleNamespace(query=""))
        self.assertEqual(self.sent[-1][0], 403)
        self.handler._page_refusal = lambda: "Only the dashboard page can do this."
        self.handler._outside_post("/api/outside/messages",
                                   {"action": "move", "source": SID, "key": a, "to": DOCK})
        self.assertEqual(self.sent[-1][0], 403)
        self.assertEqual(self.queued(), [(a, OPTEN)])

    def test_correcting_without_delivering_is_gone(self):
        a = self.routed(1)
        self.handler._outside_post("/api/outside/routing/correct",
                                   {"source": SID, "key": a, "to": DOCK})
        self.assertEqual(self.sent[-1][0], 404)
        self.assertEqual(self.row(a)["to"], OPTEN)

    def test_an_unknown_action_is_refused(self):
        self.handler._outside_post("/api/outside/messages", {"action": "drop", "source": SID})
        self.assertEqual(self.sent[-1][0], 400)


if __name__ == "__main__":
    import unittest
    unittest.main()
