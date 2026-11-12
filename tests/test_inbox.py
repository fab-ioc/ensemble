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
        self.decisions = {}             # rid → (waiting, older)
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
            read_and_old=lambda *a, **k: False, set_aside=lambda *a, **k: False)
        asks = types.SimpleNamespace(
            open_by_room=lambda rooms, now=None: w.open_asks,
            cards_ready=lambda s: True, warm_cards=lambda s: None,
            decisions=lambda s, led=None: w.decisions.get(s["id"], ([], [])),
            _room_asks=lambda s: [], moved_past=lambda s, led: 0.0)
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
        self.w.decisions[PO] = ([card], [])
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
        self.w.decisions[PO] = ([card], [])
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
        self.w.decisions[PO] = ([{"mid": "m-ans", "n": 0, "question": "Which?", "ts": NOW - 10, "who": "po"}], [])
        self.w.ledgers[PO] = {"points": [delivered("P3", "m-q", "m-ans")]}
        out = self.build()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["rows"][0]["points"], ["P3"])
        self.assertNotIn("sources", out["rows"][0])     # only with debug

    def test_answered_leaves(self):
        # Answered: no source lists it any more, so no row.
        self.w.ledgers[PO] = {"points": [delivered("P5", "m-q", "m-a", seen=True)]}
        self.assertEqual(self.build()["count"], 0)

    def test_point_to_try_is_check(self):
        self.w.ledgers[PO] = {"points": [delivered("P5", "m-q", "m-a", seen=True, said="please try it on the phone")]}
        out = self.build()
        self.assertEqual(out["count"], 1)
        r = out["rows"][0]
        self.assertEqual((r["kind"], r["mid"]), ("check", "m-a"))
        self.assertIn("try", r["reason"])

    def test_ready_for_check_appears_and_leaves_when_read(self):
        self.w.snap = {"items": [], "reported": {TASK: {"since": NOW - 60, "line": "Done: the fix", "agent": "claude",
                                                         "mid": "m-rep"}}}
        out = self.build()
        self.assertEqual(out["count"], 1)
        r = out["rows"][0]
        self.assertEqual((r["kind"], r["roomId"], r["mid"], r["ref"], r["project"]),
                         ("check", TASK, "m-rep", "#7", "Alpha"))
        # Read: attention no longer reports it (its seenAt rule), and it leaves.
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
        self.w.decisions[PO2] = ([{"mid": "b1", "n": 0, "question": "Beta?", "ts": NOW, "who": "po"}], [])
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
        self.w.decisions[PO] = ([{"mid": "m1", "n": 0, "question": url, "ts": NOW - 100, "who": "claude"}],
                                [{"mid": "o1", "n": 0, "question": url, "ts": NOW - 9000, "who": "claude", "why": "past"}])
        self.w.open_asks[TASK] = [{"mid": "t1", "n": 0, "question": url, "ts": NOW - 50, "who": "codex"},
                                  {"mid": "t2", "n": 0, "question": "Ship it?", "ts": NOW - 40, "who": "codex"},
                                  {"mid": "t3", "n": 0, "question": "https://example.org/a;b", "ts": NOW - 30,
                                   "who": "codex"}]
        out = self.build()
        lines = sorted(r["line"] for r in out["rows"] + out["older"])
        self.assertEqual(lines, ["Question from claude", "Question from claude", "Question from codex",
                                 "Question from codex", "Ship it?"])

    def test_older_questions_are_dropped_with_reason(self):
        old = {"mid": "o1", "n": 0, "question": "Old?", "ts": NOW - 9000, "who": "po", "why": "past"}
        self.w.decisions[PO] = ([], [old])
        out = self.build(debug=True)
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["dropped"][0]["why"], "older question: the person wrote past it")

    def test_older_questions_come_in_older_newest_first_with_their_ids(self):
        # #310: the Inbox lists them under Older questions so they can be closed.
        a = {"mid": "o1", "n": 0, "question": "Old?", "ts": NOW - 9000, "who": "po", "why": "past", "task": "#7"}
        b = {"mid": "o2", "n": 2, "question": "Newer?", "ts": NOW - 600, "who": "po", "why": "again"}
        self.w.decisions[PO] = ([], [a, b])
        m = self.w.module()
        m._task_index = lambda: [{"id": TASK, "no": 7, "noProjectId": "proj-a", "workflow": "inprogress",
                                  "participants": []}]
        m.task_numbers.find_task = lambda idx, ref, pid, keys, any_project=False: (
            (TASK, "") if ref == "#7" else ("", "no"))
        m._room_is_live = lambda e: False
        m.normalize_workflow = lambda v: v
        inbox.bind(m)
        out = inbox.build(now=NOW)
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["olderCount"], 2)
        self.assertEqual([(r["mid"], r["n"], r["roomId"]) for r in out["older"]], [("o2", 2, PO), ("o1", 0, PO)])
        self.assertEqual([r["age"] for r in out["older"]], [600, 9000])
        self.assertTrue(all(r["older"] and r["kind"] == "answer" for r in out["older"]))
        self.assertEqual((out["older"][1]["taskRef"], out["older"][1]["taskState"]), ("#7", "stopped"))
        self.assertNotIn("taskState", out["older"][0])
        self.assertEqual(inbox.build(project_id="proj-b", now=NOW)["olderCount"], 0)

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

    def test_2_repeated_or_cleared_po_question_is_one_row(self):
        new = {"mid": "m-new", "n": 0, "question": "Ship it?", "ts": NOW - 10, "who": "po"}
        rep = {"mid": "m-old", "n": 0, "question": "Ship it?", "ts": NOW - 900, "who": "po", "why": "repeat"}
        clr = {"mid": "m-clr", "n": 0, "question": "Before?", "ts": NOW - 2000, "who": "po", "why": "cleared"}
        self.w.decisions[PO] = ([new], [rep, clr])
        self.w.open_asks[PO] = [{"mid": m, "n": 0, "question": q, "ts": t, "who": "po"}
                                for m, q, t in (("m-new", "Ship it?", NOW - 10), ("m-old", "Ship it?", NOW - 900),
                                                ("m-clr", "Before?", NOW - 2000))]
        for debug in (False, True):
            out = self.build(debug=debug)
            self.assertEqual([r["mid"] for r in out["rows"]], ["m-new"], debug)
        whys = {(d["mid"], d["source"]): d["why"] for d in out["dropped"]}
        self.assertTrue(whys[("m-old", "ask:open")].startswith("asked again"))
        self.assertTrue(whys[("m-clr", "ask:open")].endswith("before Clear all"))

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
        self.w.decisions[PO2] = ([{"mid": "b1", "n": 0, "question": "Beta?", "ts": NOW, "who": "po"}], [])
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
        self.w.decisions[PO] = ([{"mid": "m-ask", "n": 0, "question": "Ship?", "ts": NOW, "who": "po"}], [])
        out = self.build()
        rows = {(r["roomId"], r["kind"]): r for r in out["rows"]}
        self.assertEqual(out["counts"], {"answer": 1, "check": 2, "waiting": 0})
        g = rows[(PO, "check")]
        self.assertEqual(g["line"], "Alpha PO: 3 answers to check")
        self.assertEqual(g["id"], f"check:{PO}:points")
        self.assertEqual((g["mid"], g["since"]), ("m-a1", NOW - 300), "opens the oldest answer, since the oldest")
        self.assertEqual(g["points"], ["P1", "P2", "P3"])
        self.assertEqual([i["mid"] for i in g["items"]], ["m-a1", "m-a2", "m-a3"])
        self.assertTrue(g["items"][1]["try"])
        self.assertEqual(g["reason"], "1 asks you to try something")
        self.assertEqual(rows[(PO2, "check")]["line"], "Beta PO: 1 answer to check")


if __name__ == "__main__":
    unittest.main()
