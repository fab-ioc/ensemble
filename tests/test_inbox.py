"""The inbox projection (#302): one row per thing waiting, folded from the
existing sources. The sources are stubbed: each keeps its own rules for what
is open, tested where it lives (asks, points, attention); here the folding,
the kinds and the project of each row."""
import threading
import types
import unittest

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
                                        is_threaded=lambda rid: rid in w.threads or rid in w.cold)
        w.warmed = []

        def look(rid, show_progress=True, keep=True):   # the thread source's index, a look only
            w.warmed.append((rid, keep))
            return w.cold.get(rid, {"threads": {}})
        threads.build = look
        return types.SimpleNamespace(
            attention=attention, asks=asks, points=pts, threads=threads,
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

    def test_older_questions_are_dropped_with_reason(self):
        old = {"mid": "o1", "n": 0, "question": "Old?", "ts": NOW - 9000, "who": "po", "why": "past"}
        self.w.decisions[PO] = ([], [old])
        out = self.build(debug=True)
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["dropped"][0]["why"], "older question: the person wrote past it")

    def test_one_answer_to_two_points_is_one_row(self):
        self.w.ledgers[PO] = {"points": [delivered("P1", "q1", "m-a"), delivered("P2", "q2", "m-a")]}
        out = self.build()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["rows"][0]["points"], ["P1", "P2"])


if __name__ == "__main__":
    unittest.main()
