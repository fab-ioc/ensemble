"""The inbox projection (#302): one row per thing waiting, folded from the
existing sources. The sources are stubbed: each keeps its own rules for what
is open, tested where it lives (asks, points, attention); here the folding,
the kinds and the project of each row."""
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import inbox
import points as real_points

PO = "room-po"
PO2 = "room-po2"
TASK = "room-task"
OTHER = "room-other"
NOW = 1_800_000_000.0


def summary(rid, **kw):
    return {"id": rid, "title": kw.pop("title", rid), "no": kw.pop("no", None), "noProjectId": "",
            "cwd": "", "projectId": kw.pop("projectId", ""), **kw}


def delivered(pid, mid, answer_mid, seen=False, said="it is live"):
    p = {"id": pid, "mid": mid, "text": f"question of {pid}", "state": "delivered", "createdAt": NOW - 500,
         "stateAt": NOW - 100, "answers": [{"mid": answer_mid, "said": said, "at": NOW - 100}]}
    if seen:
        p["seenKey"] = answer_mid
    return p


class World:
    """The hub as the inbox reads it."""

    def __init__(self):
        self.rooms = [summary(PO), summary(PO2), summary(TASK, no=7, projectId="proj-a", title="Fix it"),
                      summary(OTHER, no=3, projectId="proj-b", title="Elsewhere")]
        self.projects = [{"id": "proj-a", "name": "Alpha", "poRoomId": PO},
                         {"id": "proj-b", "name": "Beta", "poRoomId": PO2}]
        self.open_asks = {}             # rid → open_in rows
        self.decisions = {}             # rid → the open cards (asks.decisions)
        self.ledgers = {}               # rid → {"points": [...]}
        self.snap = {"items": [], "reported": {}}
        self.threads = {}               # rid → index
        self.cold = {}                  # rid → index a build makes (none until then)
        self.sig = {}                   # rid → what its threads are made of (default "s1")

    def module(self):
        w = self
        attention = types.SimpleNamespace(
            _room_summaries=lambda: [dict(r) for r in w.rooms],
            snapshot=lambda *a, **k: w.snap,
            set_aside=lambda *a, **k: False)
        asks = types.SimpleNamespace(
            open_by_room=lambda rooms, now=None: w.open_asks,
            cards_ready=lambda s: True, warm_cards=lambda s: None,
            decisions=lambda s, led=None: w.decisions.get(s["id"], []),
            _room_asks=lambda s: [], answered_by_message=lambda s: 0.0)
        pts = types.SimpleNamespace(
            exists=lambda rid: rid in w.ledgers, load=lambda rid, shared=False: w.ledgers[rid],
            needs_you=real_points.needs_you, last_answer=real_points.last_answer,
            answer_balloon=real_points.answer_balloon, _first_words=lambda t, n=0: t[:n],
            _LAST_DECISIONS={})
        threads = types.SimpleNamespace(_INDEX={rid: ("sig", idx) for rid, idx in w.threads.items()},
                                        _LOCK=threading.Lock(), _BUILDING=set(),
                                        is_threaded=lambda rid: rid in w.threads or rid in w.cold,
                                        _signature=lambda rid, room, lsig: w.sig.get(rid, "s1"),
                                        _led_sig=lambda rid: ())
        w.warmed = []

        def look(rid, show_progress=True, keep=True):   # the thread source's index, a look only
            w.warmed.append((rid, keep))
            return {**w.cold.get(rid, {"threads": {}}), "sig": w.sig.get(rid, "s1")}
        threads.build = look
        return types.SimpleNamespace(
            attention=attention, asks=asks, points=pts, threads=threads,
            chatroom=types.SimpleNamespace(get_room=lambda rid: {"id": rid}),
            load_projects=lambda: w.projects, load_session_projects=lambda: {},
            project_keys=lambda ps: {}, load_labels=lambda: {},
            _project_for_cwd=lambda cwd, ps: "",
            task_numbers=types.SimpleNamespace(label=lambda no, key="": f"#{no}"))


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.w = World()
        inbox._COLD_THREADS.clear()
        self.addCleanup(inbox._COLD_THREADS.clear)

    def build(self, **kw):
        inbox.bind(self.w.module())
        return inbox.build(now=NOW, **kw)

    def test_card_point_and_thread_are_one_row(self):
        # The PO asks in the answer to P12: its card, Needs you's row for the
        # card, the point (answer not seen) and the thread are one question.
        card = {"mid": "m-ans", "n": 0, "question": "Ship it?", "ts": NOW - 100, "who": "claude", "points": ["P12"]}
        self.w.decisions[PO] = [card]
        self.w.open_asks[PO] = [{"mid": "m-ans", "n": 0, "question": "Ship it?", "ts": NOW - 100, "who": "claude"}]
        self.w.ledgers[PO] = {"points": [delivered("P12", "m-q", "m-ans")]}
        self.w.snap = {"items": [{"roomId": PO, "state": "waiting_for_you", "askKind": "asks", "askId": "m-ans",
                                  "askPart": "ask:0", "reason": "claude asked you", "since": NOW - 100}],
                       "reported": {}}
        self.w.threads[PO] = {"threads": {"t1": {"members": ["m-q", "m-ans"], "points": ["P12"]}}}
        out = self.build(debug=True)
        self.assertEqual(out["count"], 1, out["rows"])
        r = out["rows"][0]
        self.assertEqual(r["kind"], "answer")
        self.assertEqual((r["roomId"], r["mid"], r["part"]), (PO, "m-ans", "ask:0"))
        self.assertEqual(set(r["sources"]), {"ask:open", "ask:decision", "point:new",
                                             "attention:waiting_for_you", "thread:t1"})
        self.assertEqual(r["points"], ["P12"])
        self.assertEqual(r["projectId"], "proj-a")
        self.assertTrue(r["isPo"])
        self.assertEqual(out["counts"], {"answer": 1, "check": 0, "waiting": 0})
        whys = [d["why"] for d in out["dropped"]]
        self.assertTrue(any(w.startswith("duplicate of row") for w in whys), whys)

    def test_cold_thread_index_is_built_and_folded(self):
        # Review 1: no view has built the PO chat's threads since the hub
        # started. The inbox starts that build itself, says it is pending,
        # and the next request folds the thread into the one row.
        card = {"mid": "m-ans", "n": 0, "question": "Ship it?", "ts": NOW - 100, "who": "claude", "points": ["P12"]}
        self.w.decisions[PO] = [card]
        self.w.ledgers[PO] = {"points": [delivered("P12", "m-q", "m-ans")]}
        self.w.cold[PO] = {"threads": {"t1": {"members": ["m-q", "m-ans"], "points": ["P12"]}}}
        mod = self.w.module()
        inbox.bind(mod)
        first = inbox.build(now=NOW, debug=True)
        for t in threading.enumerate():
            if t.name.startswith("inbox-threads-"):
                t.join(5)
        self.assertTrue(first.get("pending"), first)
        self.assertEqual(self.w.warmed, [(PO, False)], "a look only, review 2")
        self.assertNotIn(PO, mod.threads._INDEX, "the chat's own view is left its first build")
        out = inbox.build(now=NOW, debug=True)
        self.assertEqual(out["count"], 1, out["rows"])
        self.assertIn("thread:t1", out["rows"][0]["sources"])
        self.assertEqual(out["rows"][0]["threads"], ["t1"])
        self.assertFalse(out.get("pending"), out)

    def test_cold_chat_with_no_threads_stops_being_pending(self):
        self.w.cold[PO] = {"threads": {}}
        inbox.bind(self.w.module())
        self.assertTrue(inbox.build(now=NOW).get("pending"))
        for t in threading.enumerate():
            if t.name.startswith("inbox-threads-"):
                t.join(5)
        self.assertFalse(inbox.build(now=NOW).get("pending"))
        self.assertEqual(len(self.w.warmed), 1, "looked at once, not on every poll")

    def test_point_folds_into_card_on_its_answer_message(self):
        # The card does not name its point, but it sits on the point's answer.
        self.w.decisions[PO] = [{"mid": "m-ans", "n": 0, "question": "Which?", "ts": NOW - 10, "who": "po"}]
        self.w.ledgers[PO] = {"points": [delivered("P3", "m-q", "m-ans")]}
        out = self.build()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["rows"][0]["points"], ["P3"])
        self.assertNotIn("sources", out["rows"][0])     # only with debug

    def test_a_seen_answer_stays_until_done(self):
        # P395: seeing an answer removes nothing; Done (the point acked) does.
        p = delivered("P5", "m-q", "m-a", seen=True)
        self.w.ledgers[PO] = {"points": [p]}
        out = self.build()
        self.assertEqual([(r["kind"], r["mid"], r["dismiss"]) for r in out["rows"]], [("check", "m-a", "done")])
        p["state"] = "acknowledged"
        self.assertEqual(self.build()["count"], 0)

    def test_point_to_try_is_check(self):
        self.w.ledgers[PO] = {"points": [delivered("P5", "m-q", "m-a", seen=True, said="please try it on the phone")]}
        out = self.build()
        self.assertEqual(out["count"], 1)
        r = out["rows"][0]
        self.assertEqual((r["kind"], r["mid"]), ("check", "m-a"))
        self.assertIn("try", r["reason"])

    def test_ready_for_check_appears_and_leaves_when_attention_drops_it(self):
        self.w.snap = {"items": [], "reported": {TASK: {"since": NOW - 60, "line": "Done: the fix", "agent": "claude",
                                                         "mid": "m-rep"}}}
        out = self.build()
        self.assertEqual(out["count"], 1)
        r = out["rows"][0]
        self.assertEqual((r["kind"], r["roomId"], r["mid"], r["ref"], r["project"]),
                         ("check", TASK, "m-rep", "#7", "Alpha"))
        # Done (or a later report, or the task done/stopped): attention no longer
        # reports it, and it leaves. Reading or writing in it does not (#394,
        # P395: test_needs_you_real.AFinishedReport).
        self.w.snap = {"items": [], "reported": {}}
        self.assertEqual(self.build()["count"], 0)

    def test_waiting_item_and_question_report(self):
        self.w.snap = {"items": [
            {"roomId": TASK, "state": "stalled", "reason": "no output for 40 min", "since": NOW - 2400},
            {"roomId": OTHER, "state": "waiting_for_you", "askKind": "question", "askId": "m-q2",
             "quote": "Which port?", "reason": "asks", "since": NOW - 30}], "reported": {}}
        out = self.build()
        kinds = {r["roomId"]: r["kind"] for r in out["rows"]}
        self.assertEqual(kinds, {TASK: "waiting", OTHER: "answer"})
        self.assertEqual(out["rows"][0]["roomId"], OTHER)        # answers first

    def test_cross_project_rows_carry_their_project(self):
        self.w.decisions[PO2] = [{"mid": "b1", "n": 0, "question": "Beta?", "ts": NOW, "who": "po"}]
        self.w.snap = {"items": [{"roomId": OTHER, "state": "blocked", "reason": "stuck", "since": NOW}],
                       "reported": {TASK: {"since": NOW, "line": "done", "agent": "claude"}}}
        out = self.build()
        got = {(r["roomId"], r["projectId"], r["project"]) for r in out["rows"]}
        self.assertEqual(got, {(PO2, "proj-b", "Beta"), (OTHER, "proj-b", "Beta"), (TASK, "proj-a", "Alpha")})

    def test_other_projects_task_never_shows_as_this_projects(self):
        self.w.snap = {"items": [{"roomId": OTHER, "state": "blocked", "reason": "stuck", "since": NOW}],
                       "reported": {TASK: {"since": NOW, "line": "done", "agent": "claude"}}}
        out = self.build(project_id="proj-a", debug=True)
        self.assertEqual([r["roomId"] for r in out["rows"]], [TASK])
        self.assertTrue(any(d["why"].startswith("another project") for d in out["dropped"]))

    def test_no_row_shows_a_bare_link_as_its_question(self):
        # P321: an ask whose words are only a link reads as who asks it.
        url = "http://127.0.0.1:8765/session?room=room-8d56cd21&msg=d6801a1e8ab0&part=ask:0"
        self.w.decisions[PO] = [{"mid": "m1", "n": 0, "question": url, "ts": NOW - 100, "who": "claude"},
                                {"mid": "o1", "n": 0, "question": url, "ts": NOW - 9000, "who": "claude"}]
        self.w.open_asks[TASK] = [{"mid": "t1", "n": 0, "question": url, "ts": NOW - 50, "who": "codex"},
                                  {"mid": "t2", "n": 0, "question": "Ship it?", "ts": NOW - 40, "who": "codex"},
                                  {"mid": "t3", "n": 0, "question": "https://example.org/a;b", "ts": NOW - 30,
                                   "who": "codex"}]
        out = self.build()
        lines = sorted(r["line"] for r in out["rows"])
        # #361: a PO chat's card is the PO's, whichever agent sits in its seat.
        self.assertEqual(lines, ["Question from codex", "Question from codex", "Question from the PO",
                                 "Question from the PO", "Ship it?"])

    def test_a_tasks_ask_names_the_seat_by_its_role(self):
        # #361: "The engineer asks", not the agent's kind; the PO's chat "The PO asks".
        s = {"participants": [{"identity": "claude", "kind": "agent", "role": "engineer: owns it"},
                              {"identity": "codex", "kind": "agent", "role": "reviewer"}]}
        self.assertEqual(inbox._asker(s, "claude", False), "the engineer")
        self.assertEqual(inbox._asker(s, "codex", False), "the reviewer")
        self.assertEqual(inbox._asker(s, "codex", True), "the PO")
        self.assertEqual(inbox._asker(s, "gemini", False), "gemini")
        self.assertEqual(inbox._asker({}, "", False), "an agent")

    def test_every_open_po_card_is_a_row_with_its_ids(self):
        # P396: no Older questions group: a card written past or asked again
        # later is a row in Needs your answer, newest first, with its ids.
        a = {"mid": "o1", "n": 0, "question": "Old?", "ts": NOW - 9000, "who": "Claude", "task": "#7"}
        b = {"mid": "o2", "n": 2, "question": "Newer?", "ts": NOW - 600, "who": "po"}
        self.w.decisions[PO] = [a, b]
        m = self.w.module()
        m._task_index = lambda: [{"id": TASK, "no": 7, "noProjectId": "proj-a", "workflow": "inprogress",
                                  "participants": []}]
        m.task_numbers.find_task = lambda idx, ref, pid, keys, any_project=False: (
            (TASK, "") if ref == "#7" else ("", "no"))
        m._room_is_live = lambda e: False
        m.normalize_workflow = lambda v: v
        inbox.bind(m)
        out = inbox.build(now=NOW)
        self.assertNotIn("older", out)
        self.assertNotIn("olderCount", out)
        self.assertEqual(out["counts"]["answer"], 2)
        rows = out["rows"]
        self.assertEqual([(r["mid"], r["n"], r["roomId"], r["kind"]) for r in rows],
                         [("o2", 2, PO, "answer"), ("o1", 0, PO, "answer")])
        self.assertEqual([r["age"] for r in rows], [600, 9000])
        self.assertEqual([r["dismiss"] for r in rows], ["close", "close"])
        self.assertEqual((rows[1]["taskRef"], rows[1]["taskState"]), ("#7", "stopped"))
        self.assertNotIn("taskState", rows[0])
        # #361: the seat by its role, never an identity.
        self.assertEqual([r["reason"] for r in rows], ["The PO asks"] * 2)
        self.assertEqual(inbox.build(project_id="proj-b", now=NOW)["count"], 0)

    def test_a_needs_you_item_for_a_closed_po_card_is_no_row(self):
        # #394: a PO chat's cards are its Decisions. A Needs you item for a
        # card not among them (just closed; the snapshot not read again yet)
        # makes no row of its own. A task room's ask item still does.
        self.w.snap = {"items": [
            {"roomId": PO, "state": "waiting_for_you", "askKind": "asks", "askId": "m-gone",
             "askPart": "ask:0", "reason": "claude asked you", "since": NOW - 50},
            {"roomId": TASK, "state": "waiting_for_you", "askKind": "asks", "askId": "m-t",
             "askPart": "ask:0", "reason": "claude asked you", "since": NOW - 40}],
            "reported": {}}
        out = self.build(debug=True)
        self.assertEqual([(r["roomId"], r["mid"]) for r in out["rows"]], [(TASK, "m-t")], out["rows"])
        whys = [(d.get("mid"), d["why"]) for d in out["dropped"]]
        self.assertIn(("m-gone", "not among the PO chat's Decisions (answered or closed)"), whys)

    def test_one_answer_to_two_points_is_one_row(self):
        self.w.ledgers[PO] = {"points": [delivered("P1", "q1", "m-a"), delivered("P2", "q2", "m-a")]}
        out = self.build()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["rows"][0]["points"], ["P1", "P2"])
        self.assertEqual(len(out["rows"][0]["items"]), 1, "one answer to check")


def join_looks():
    for t in threading.enumerate():
        if t.name.startswith("inbox-threads-"):
            t.join(5)


class PoFixes(unittest.TestCase):
    """The PO's fixes before merge (PO-FIXES.md, 11-04)."""
    setUp = InboxTest.setUp
    build = InboxTest.build

    def test_1_cold_chat_is_not_built_again_while_its_signature_holds(self):
        self.w.cold[PO] = {"threads": {}}
        inbox.bind(self.w.module())
        old = inbox.COLD_THREADS_S
        inbox.COLD_THREADS_S = 0.0
        self.addCleanup(setattr, inbox, "COLD_THREADS_S", old)
        for _ in range(4):
            inbox.build(now=NOW)
            join_looks()
        self.assertEqual(len(self.w.warmed), 1, "same signature: the look stands")
        self.w.sig[PO] = "s2"           # a new turn in the chat
        inbox.build(now=NOW)
        join_looks()
        self.assertEqual(len(self.w.warmed), 2)

    def test_1_a_look_leaves_the_chats_progress_line(self):
        import threads as real_threads
        src = Path(real_threads.__file__).read_text(encoding="utf-8")
        self.assertIn("    if keep:\n        with _LOCK:\n            _INDEX[room_id] = (sig, idx)\n"
                      "        progress.clear(key)", src.replace("\r\n", "\n"))

    def test_2_asked_again_is_two_rows_and_cleared_is_none(self):
        # P396: the old and the newer card are rows until each is closed; a
        # card from before Clear all is not in the Decisions and not a row.
        new = {"mid": "m-new", "n": 0, "question": "Ship it?", "ts": NOW - 10, "who": "po"}
        rep = {"mid": "m-old", "n": 0, "question": "Ship it?", "ts": NOW - 900, "who": "po"}
        self.w.decisions[PO] = [new, rep]
        self.w.open_asks[PO] = [{"mid": m, "n": 0, "question": q, "ts": t, "who": "po"}
                                for m, q, t in (("m-new", "Ship it?", NOW - 10), ("m-old", "Ship it?", NOW - 900),
                                                ("m-clr", "Before?", NOW - 2000))]
        for debug in (False, True):
            out = self.build(debug=debug)
            self.assertEqual([r["mid"] for r in out["rows"]], ["m-new", "m-old"], debug)
        whys = {(d["mid"], d["source"]): d["why"] for d in out["dropped"]}
        self.assertIn(("m-clr", "ask:open"), whys)
        # Closed (its card no longer open): the row goes, the other stays.
        self.w.decisions[PO] = [new]
        self.w.open_asks[PO] = self.w.open_asks[PO][:1]
        self.assertEqual([r["mid"] for r in self.build()["rows"]], ["m-new"])

    def test_3_one_cached_build_one_flight(self):
        inbox.bind(self.w.module())
        inbox._CACHE.clear()
        self.addCleanup(inbox._CACHE.clear)
        calls = []
        real = inbox._build_all

        def slow(debug=False, now=None):
            calls.append(debug)
            time.sleep(0.2)
            return real(debug, now)
        self.w.snap = {"items": [], "reported": {TASK: {"since": NOW, "line": "done", "agent": "claude"},
                                                 OTHER: {"since": NOW, "line": "done", "agent": "codex"}}}
        with mock.patch.object(inbox, "_build_all", slow):
            got = {}
            ts = [threading.Thread(target=lambda p=p: got.__setitem__(p, inbox.snapshot(p)))
                  for p in ("", "proj-a", "proj-b", "junk-1", "junk-2")]
            for t in ts:
                t.start()
            for t in ts:
                t.join(5)
        self.assertEqual(calls, [False], "concurrent misses waited for one build")
        self.assertEqual(list(inbox._CACHE), [False], "no entry per projectId")
        self.assertEqual((got[""]["count"], got["proj-a"]["count"], got["proj-b"]["count"], got["junk-1"]["count"]),
                         (2, 1, 1, 0))

    def test_4_row_ids_are_unique(self):
        # Split points answered by a tool call: no answer message, so both
        # fall back to the point's own message; and a report on that message.
        a = {"id": "P12a", "mid": "m-q", "text": "a", "state": "delivered", "createdAt": NOW - 50,
             "stateAt": NOW - 40, "answers": [{"said": "done", "at": NOW - 40}]}
        b = dict(a, id="P12b", text="b")
        self.w.ledgers[PO] = {"points": [a, b]}
        self.w.snap = {"items": [{"roomId": TASK, "state": "stalled", "reason": "x", "since": NOW}],
                       "reported": {TASK: {"since": NOW, "line": "done", "agent": "claude"}}}
        ids = [r["id"] for r in self.build()["rows"]]
        self.assertEqual(len(ids), len(set(ids)), ids)
        R = inbox._Rows()
        r1 = R.add({"id": "x", "roomId": "r", "mid": ""})
        r2 = R.add({"id": "x", "roomId": "r", "mid": ""})
        self.assertNotEqual(r1["id"], r2["id"])

    def test_5_a_failing_source_is_skipped_and_named(self):
        self.w.decisions[PO2] = [{"mid": "b1", "n": 0, "question": "Beta?", "ts": NOW, "who": "po"}]
        mod = self.w.module()

        def boom(*a, **k):
            raise RuntimeError("attention down")
        mod.attention.snapshot = boom
        mod.points.load = boom
        self.w.ledgers[PO] = {"points": []}
        inbox.bind(mod)
        out = inbox.build(now=NOW, debug=True)
        self.assertEqual([r["mid"] for r in out["rows"]], ["b1"])
        self.assertEqual({e["source"] for e in out["errors"]}, {"attention", "points", "decisions"})
        # Rooms not read at all: an empty inbox with an error, never a raise.
        mod.attention._room_summaries = boom
        inbox._CACHE.clear()
        self.addCleanup(inbox._CACHE.clear)
        out = inbox.snapshot("", True)
        self.assertEqual((out["count"], out["error"]), (0, "rooms not read"))
        with mock.patch.object(inbox, "_build_all", boom):
            inbox._CACHE.clear()
            self.assertEqual(inbox.snapshot()["error"], "not built")

    def test_6_answers_to_check_are_one_row_per_po_chat(self):
        self.w.ledgers[PO] = {"points": [
            dict(delivered("P1", "q1", "m-a1"), stateAt=NOW - 300, answers=[{"mid": "m-a1", "said": "x", "at": NOW - 300}]),
            dict(delivered("P2", "q2", "m-a2"), answers=[{"mid": "m-a2", "said": "please try it", "at": NOW - 200}]),
            delivered("P3", "q3", "m-a3")]}
        self.w.ledgers[PO2] = {"points": [delivered("P9", "q9", "m-b9")]}
        # A question stays a row of its own.
        self.w.decisions[PO] = [{"mid": "m-ask", "n": 0, "question": "Ship?", "ts": NOW, "who": "po"}]
        out = self.build()
        rows = {(r["roomId"], r["kind"]): r for r in out["rows"]}
        self.assertEqual(out["counts"], {"answer": 1, "check": 2, "waiting": 0})
        g = rows[(PO, "check")]
        self.assertEqual(g["line"], "PO: 3 answers to check")
        self.assertEqual(g["id"], f"check:{PO}:points")
        self.assertEqual((g["mid"], g["since"]), ("m-a1", NOW - 300), "opens the oldest answer, since the oldest")
        self.assertEqual(g["points"], ["P1", "P2", "P3"])
        self.assertEqual([i["mid"] for i in g["items"]], ["m-a1", "m-a2", "m-a3"])
        self.assertTrue(g["items"][1]["try"])
        self.assertEqual(g["reason"], "1 asks you to try something")
        self.assertEqual(rows[(PO2, "check")]["line"], "PO: 1 answer to check")


    # #352: every row has somewhere to go and a way to close it.
    def folder_item(self, since=NOW - 600, **kw):
        return {"roomId": TASK, "state": "blocked", "cause": "folder_missing", "ref": "AL-7",
                "reason": "could not start: claude's folder /x/y/z is missing (gone)",
                "since": since, "askedAt": since, **kw}

    def test_352_every_row_has_open_and_dismiss(self):
        self.w.decisions[PO] = [{"mid": "m-ask", "n": 0, "question": "Ship?", "ts": NOW, "who": "po"}]
        self.w.ledgers[PO] = {"points": [delivered("P3", "q3", "m-a3")]}
        self.w.snap = {"items": [
            self.folder_item(),
            {"roomId": OTHER, "state": "waiting_for_you", "askKind": "question", "askId": "m-q2",
             "quote": "Which port?", "reason": "asks", "since": NOW - 30, "canDismiss": True}],
            "reported": {}}
        rows = {r["id"]: r for r in self.build()["rows"]}
        acts = {(r["kind"], r["roomId"]): (r["open"], r["dismiss"]) for r in rows.values()}
        self.assertEqual(acts, {("answer", PO): ("message", "close"), ("check", PO): ("message", "done"),
                                ("waiting", TASK): ("chat", "dealt"), ("answer", OTHER): ("message", "dealt")})

    def test_352_folder_missing_says_what_to_do_without_the_path(self):
        self.w.snap = {"items": [self.folder_item()], "reported": {}}
        r = self.build()["rows"][0]
        self.assertEqual(r["line"], "AL-7 could not start: its work folder is missing")
        self.assertEqual(r["reason"], "Its agent cannot start because its folder was removed. Press Done, or open the task and Stop it.")
        self.assertNotIn("/x/y/z", r["line"] + r["reason"])
        self.w.snap = {"items": [self.folder_item(folderKind="elsewhere", ref="")], "reported": {}}
        r = self.build()["rows"][0]
        self.assertEqual(r["line"], "This task could not start: its conversation was recorded in another folder")

    def test_352_done_sets_it_aside_until_its_state_is_new(self):
        self.w.snap = {"items": [self.folder_item(since=NOW - 600)], "reported": {}}
        self.assertEqual(self.build()["count"], 1)
        self.w.rooms[2]["dealtAt"] = NOW - 300          # Done in the Inbox, after the refusal
        out = self.build(debug=True)
        self.assertEqual(out["count"], 0, out["rows"])
        self.assertIn("set aside: Done in the Inbox", [d["why"] for d in out["dropped"]])
        self.w.snap = {"items": [self.folder_item(since=NOW - 100)], "reported": {}}    # refused again
        self.assertEqual(self.build()["count"], 1)

    def test_352_a_dismissible_item_keeps_its_own_rule(self):
        # canDismiss items are cleared by attention itself (set_aside); the
        # inbox does not drop them again on its own.
        self.w.rooms[3]["dealtAt"] = NOW
        self.w.snap = {"items": [{"roomId": OTHER, "state": "waiting_for_you", "askKind": "question",
                                  "askId": "m-q2", "reason": "asks", "since": NOW - 30, "canDismiss": True}],
                       "reported": {}}
        self.assertEqual(self.build()["count"], 1)

if __name__ == "__main__":
    unittest.main()


class InputSourcesRows(unittest.TestCase):
    """Outside sources step 6: a source not working, and read again, are hub
    rows: no room, no project, in every project's Inbox, opening the Input
    sources panel on that source."""

    def setUp(self):
        self.w = World()
        inbox._COLD_THREADS.clear()
        self.addCleanup(inbox._COLD_THREADS.clear)
        self.down, self.up = [], []

    def build(self, **kw):
        m = self.w.module()
        m.outside = types.SimpleNamespace(down_rows=lambda now=None: list(self.down),
                                          recovered_rows=lambda now=None: list(self.up))
        inbox.bind(m)
        return inbox.build(now=NOW, **kw)

    def test_down_and_works_again_rows(self):
        self.down = [{"source": "slack", "label": "Slack", "since": NOW - 900, "reason": "no connection",
                      "text": "Slack is not being read since 10:42: no connection"}]
        self.up = [{"source": "slack-2", "label": "Slack", "from": NOW - 3000, "at": NOW - 60,
                    "text": "Slack is read again (not read from 09:50 to 10:40)"}]
        rows = {r["source"]: r for r in self.build()["rows"]}
        down, up = rows["slack"], rows["slack-2"]
        self.assertEqual((down["kind"], down["open"], down["dismiss"]), ("waiting", "source", ""))
        self.assertEqual(down["id"], f"outside:slack:down:{int(NOW - 900)}")
        self.assertEqual(down["line"], "Slack is not being read since 10:42: no connection")
        self.assertEqual((down["roomId"], down["projectId"], down["task"], down["hub"]),
                         ("", "", "Input sources", True))
        self.assertEqual((up["kind"], up["open"], up["dismiss"]), ("check", "source", "seen"))
        self.assertEqual(up["since"], NOW - 60)

    def test_hub_rows_show_in_every_project(self):
        self.down = [{"source": "slack", "label": "Slack", "since": NOW - 900, "reason": "x",
                      "text": "Slack is not being read since 10:42: x"}]
        self.assertEqual([r["source"] for r in self.build(project_id="proj-a")["rows"]], ["slack"])
        self.assertEqual(self.build(project_id="proj-b")["count"], 1)

    def test_a_failing_frame_is_left_out(self):
        def boom(now=None):
            raise RuntimeError("no")
        m = self.w.module()
        m.outside = types.SimpleNamespace(down_rows=boom, recovered_rows=boom)
        inbox.bind(m)
        out = inbox.build(now=NOW, debug=True)
        self.assertEqual(out["count"], 0)
        self.assertIn("input sources", [e["source"] for e in out["errors"]])
