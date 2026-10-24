"""Needs you (#209): an answered point closes by itself once its answer was
seen and the person wrote again (``seen``), when a later point names its task
(``superseded``), or once at the hub's start when it is old (``migrated``);
what a send closed comes back if the send is taken back, and a closed one
reopens. The Decisions are the question cards still waiting on the person:
one asked again keeps its newest card, an old one written past is an older
question, and a chat's cards are read in the background."""
from __future__ import annotations

import json
import shutil
import threading
import time
import unittest
from unittest import mock

import asks
import chatroom
import points
import tests.test_point_cards as point_cards
from tests.test_points import _World, turn


def _join_cards():
    """The background reads of cards (asks.warm_cards) end before the test's
    files go."""
    for t in threading.enumerate():
        if t.name.startswith('cards-'):
            t.join(10)


class Closing(_World):
    def setUp(self):
        super().setUp()
        self.addCleanup(_join_cards)

    def answered(self, rid, text="Is #26 merged?", reply="Yes, merged.", at=None):
        """One point and its answer; returns (pid, answer mid)."""
        at = self.t0 if at is None else at
        out, ids = self.send(rid, text, at=at)
        n = len(self.turns["sid-1"])
        self.add("sid-1", turn("user", out, at + 1), turn("assistant", reply, at + 2))
        points.sync(rid, force=True)
        return ids[0], f"sid-1:{n + 1}"

    def test_seen_then_written_again_closes_and_unseen_stays(self):
        rid = self.solo_room()
        p1, m1 = self.answered(rid, "One?", "one", at=self.t0)
        p2, _m2 = self.answered(rid, "Two?", "two", at=self.t0 + 10)
        self.assertEqual(points.needs_you(self.point(rid, p1)), "new")
        self.assertEqual(points.mark_seen(rid, mids=[m1], now=self.t0 + 15), [p1])
        self.assertEqual(points.mark_seen(rid, mids=[m1], now=self.t0 + 16), [], "seen once")
        self.assertEqual(points.needs_you(self.point(rid, p1)), "")
        self.assertEqual(self.state(rid), {p1: "delivered", p2: "delivered"}, "seen alone closes nothing")
        self.send(rid, "Something else now", at=self.t0 + 20)
        self.assertEqual(self.point(rid, p1)["state"], "acked")
        self.assertEqual(self.point(rid, p1)["ackedBy"], "seen")
        self.assertEqual(self.point(rid, p2)["state"], "delivered", "never seen: never closed")
        v = points.view(rid)
        self.assertEqual(v["needs"]["news"], 1)
        self.assertEqual([c["id"] for c in v["needs"]["closed"]], [p1])

    def test_a_newer_answer_is_unseen_again(self):
        rid = self.solo_room()
        p1, m1 = self.answered(rid, "One?", "one")
        points.mark_seen(rid, mids=[m1])
        self.add("sid-1", turn("assistant", f"Re {p1}: more on it", self.t0 + 5))
        points.sync(rid, force=True)
        self.assertFalse(points.is_seen(self.point(rid, p1)))

    def test_an_answer_by_tool_after_a_seen_balloon_is_new_again(self):
        rid = self.solo_room()
        p1, m1 = self.answered(rid, "One?", "one")
        points.mark_seen(rid, mids=[m1], now=self.t0 + 5)
        points.answer_by_tool(rid, p1, "it is live", "claude")
        p = self.point(rid, p1)
        self.assertFalse(points.is_seen(p), "the answer by doing is the one to see")
        self.assertEqual(points.needs_you(p), "new")
        self.assertEqual(points.answer_balloon(p)["mid"], m1, "links still go to the balloon")
        self.assertEqual(points.mark_seen(rid, mids=[m1], now=self.t0 + 6), [], "the old balloon does not see it")
        self.send(rid, "Something else now", at=time.time() + 1)
        self.assertEqual(self.point(rid, p1)["state"], "delivered", "unseen: not closed")
        self.assertEqual(points.mark_seen(rid, ids=[p1]), [p1])

    def test_the_tool_lists_every_closed_point_with_all(self):
        import dashboard
        import ensemble_tools
        rid = self.solo_room()
        p1, _ = self.answered(rid, "One?", "one")
        p2, _ = self.answered(rid, "Two?", "two", at=self.t0 + 10)
        points.act(rid, p1, "ack")
        points.act(rid, p2, "ack")
        led = points.load(rid)
        next(p for p in led["points"] if p["id"] == p1)["stateAt"] = time.time() - 30 * 86400
        points._save(rid, led)
        ctx = {"room": chatroom.get_room(rid, public=False), "identity": "claude"}
        with mock.patch.object(ensemble_tools, "_d", dashboard):
            week = ensemble_tools._points(ctx, {"action": "list"}, None)
            every = ensemble_tools._points(ctx, {"action": "list", "all": True}, None)
            past = ensemble_tools._points(ctx, {"action": "list", "all": True, "offset": 1}, None)
        self.assertEqual(([c["id"] for c in week["closed"]], week["closedTotal"]), ([p2], 2))
        self.assertIn("all: true", week["closedNote"])
        self.assertEqual([(c["id"], c["reason"]) for c in every["closed"]], [(p2, "click"), (p1, "click")])
        self.assertEqual(every["closed"][1]["text"], "One?")
        self.assertNotIn("closedNext", every)
        self.assertEqual([c["id"] for c in past["closed"]], [p1])

    def test_an_answer_by_tool_is_seen_by_id_and_thumbs_up_still_closes(self):
        rid = self.solo_room()
        p1, _ = self.answered(rid, "One?", "one")
        p2, _ = self.answered(rid, "Two?", "two", at=self.t0 + 10)
        self.assertEqual(points.mark_seen(rid, ids=[p2.lower()]), [p2])
        self.assertEqual(points.act(rid, p1, "ack")["state"], "acked")
        self.assertNotIn(self.point(rid, p1).get("ackedBy"), points.AUTO)

    def test_to_try_answer(self):
        self.assertTrue(points.asks_action("Done. Reload the page and try the new button."))
        self.assertTrue(points.asks_action("It is ready for your check."))
        self.assertFalse(points.asks_action("I tried it and it works."))
        rid = self.solo_room()
        p1, _ = self.answered(rid, "Fix it?", "Fixed: please check the panel now.")
        self.assertEqual(points.needs_you(self.point(rid, p1)), "try")
        self.assertEqual(points.view(rid)["needs"]["totry"], 1)

    def test_a_later_point_on_the_task_supersedes_seen_or_not(self):
        rid = self.solo_room()
        lookup = lambda ref: {"roomId": "room-" + ref.strip("#")} if ref in ("#26", "#27") else None
        with mock.patch.object(points, "task_lookup", return_value=lookup):
            p1, _ = self.answered(rid, "Is #26 merged?", "yes")
            p2, _ = self.answered(rid, "Is #27 merged?", "yes", at=self.t0 + 10)
            answered_at = self.point(rid, p1)["stateAt"]
            _out, ids = self.send(rid, "Now #26 breaks on start", key="k9", at=self.t0 + 20)
            self.assertEqual(self.point(rid, p1)["ackedBy"], "superseded")
            self.assertEqual(self.point(rid, p1)["supersededBy"], ids[0])
            self.assertEqual(self.point(rid, p2)["state"], "delivered")
            # The send taken back: what it closed is answered again, since when it was.
            points.discard(rid, ids, key="k9")
            self.assertEqual(self.point(rid, p1)["state"], "delivered")
            self.assertNotIn("ackedBy", self.point(rid, p1))
            self.assertEqual(self.point(rid, p1)["stateAt"], answered_at)

    def test_an_answer_after_the_later_point_on_its_task_is_not_superseded(self):
        # P1 asks for a fix on #104; P2, later, is planned on #104; then P1 is
        # answered. The sync that records the answer must not close it as
        # superseded: P2 came before the answer, the answer still waits.
        rid = self.solo_room()
        lookup = lambda ref: {"roomId": "room-104"} if ref == "#104" else None
        with mock.patch.object(points, "task_lookup", return_value=lookup):
            out1, ids1 = self.send(rid, "Fix X in #104", at=self.t0)
            self.add("sid-1", turn("user", out1, self.t0 + 1))
            out2, ids2 = self.send(rid, "And Y on #104", key="k2", at=self.t0 + 10)
            self.add("sid-1", turn("user", out2, self.t0 + 11),
                     turn("assistant", f"Re {ids2[0]} (planned #104): starting on it", self.t0 + 12))
            points.sync(rid, force=True)
            self.assertEqual(self.point(rid, ids2[0])["state"], "planned")
            self.add("sid-1", turn("assistant", f"Re {ids1[0]}: done, please try it now in the panel", self.t0 + 20))
            points.sync(rid, force=True)
            p1 = self.point(rid, ids1[0])
            self.assertEqual(p1["state"], "delivered", p1.get("ackedBy"))
            self.assertEqual(points.needs_you(p1), "try")
            # A point on #104 made after the answer still supersedes it.
            self.send(rid, "Now #104 breaks on start", key="k3", at=self.t0 + 30)
            self.assertEqual(self.point(rid, ids1[0])["ackedBy"], "superseded")

    def test_reopen_an_auto_closed_point(self):
        rid = self.solo_room()
        p1, m1 = self.answered(rid, "One?", "one")
        points.mark_seen(rid, mids=[m1], now=self.t0 + 15)
        self.send(rid, "next", at=self.t0 + 20)
        p = points.act(rid, p1, "reopen")
        self.assertEqual(p["state"], "delivered")
        self.assertFalse(points.is_seen(self.point(rid, p1)), "back on the list as new")

    def test_migration_once_old_closes_read_ones_are_seen(self):
        rid = self.solo_room()
        now = time.time()
        old, _ = self.answered(rid, "Old?", "old", at=now - 5 * 86400)
        read, _ = self.answered(rid, "Read?", "read", at=now - 3600)
        new, _ = self.answered(rid, "New?", "new", at=now - 60)
        self.send(rid, "latest", at=now - 30)
        full = chatroom.get_room(rid, public=False)
        full["seenAt"] = now - 3000
        chatroom.update_room(full)
        self.assertEqual(points.migrate(rid, now=now), 0, "a ledger made since needs none")
        led = points.load(rid)
        led["migrated"] = 0     # a ledger from before Needs you
        points._save(rid, led)
        self.assertEqual(points.migrate(rid, now=now), 1)
        self.assertEqual(self.point(rid, old)["ackedBy"], "migrated")
        self.assertEqual(self.point(rid, read)["seenHow"], "read")
        self.assertEqual(self.point(rid, read)["state"], "delivered", "read: closes when they next write")
        self.assertEqual(points.needs_you(self.point(rid, new)), "new")
        self.assertEqual(points.migrate(rid, now=now), 0, "once per ledger")
        self.assertEqual(points.act(rid, old, "reopen")["state"], "delivered")

    def test_seen_endpoint(self):
        rid = self.solo_room()
        p1, m1 = self.answered(rid, "One?", "one")
        got = points.mark_seen(rid, mids=[m1], how="bogus")
        self.assertEqual(got, [p1])
        self.assertEqual(self.point(rid, p1)["seenHow"], "screen")


class Decisions(point_cards.Resolver):
    # The point cards' world (#206); its own tests run there.
    def setUp(self):
        super().setUp()
        self.addCleanup(_join_cards)
        p = mock.patch.object(points, 'exists', return_value=True)
        p.start(); self.addCleanup(p.stop)

    def summary(self):
        return chatroom.get_room(self.rid, public=False)

    def test_open_cards_answered_on_card_or_typed_answer_drop_out(self):
        now = time.time()
        self.say('c1', now - 50, 'Ask: Which one?\n- A\n- B')
        self.say('c2', now - 40, 'Ask: Ship #31 today?\n- Yes\n- No')
        self.say('c3', now - 30, 'Ask: What colour?\n- Red\n- Blue')
        self.assertEqual([c['mid'] for c in asks.open_cards(self.summary())], ['c3', 'c2', 'c1'])
        self.assertEqual(asks.open_cards(self.summary())[1].get('task'), '#31')
        self.answer('c1')
        led = points.load(self.rid)
        led['points'].append({'id': 'P9', 'text': f'Blue. http://h/session?room={self.rid}&msg=c3',
                              'state': 'open', 'createdAt': now - 20, 'answers': []})
        points._save(self.rid, led)
        self.assertEqual([c['mid'] for c in asks.open_cards(self.summary())], ['c2'])

    def test_open_cards_kept_while_room_and_ledger_unchanged(self):
        # The PO's pre-merge fix 2: a poll of an unchanged chat works nothing
        # out again and reads no room beyond the one it was handed.
        now = time.time()
        self.say('c1', now - 50, 'Ask: Which one?\n- A\n- B')
        self.say('c2', now - 40, 'Ask: What colour?\n- Red\n- Blue')
        room = self.summary()
        self.assertEqual([c['mid'] for c in asks.open_cards(room)], ['c2', 'c1'])
        with mock.patch.object(asks, '_open_cards', wraps=asks._open_cards) as worked, \
                mock.patch.object(chatroom, 'get_room', wraps=chatroom.get_room) as read:
            got = asks.open_cards(room)
            got[0]['question'] = 'changed by a caller'
            self.assertEqual([c['mid'] for c in asks.open_cards(room)], ['c2', 'c1'])
            self.assertEqual(asks.open_cards(room)[0]['question'], 'What colour?', "a caller's change stays its own")
            self.assertEqual((worked.call_count, read.call_count), (0, 0))
            self.answer('c1')       # the ledger is saved: worked out again
            self.assertEqual([c['mid'] for c in asks.open_cards(room)], ['c2'])
            self.assertEqual(worked.call_count, 1)
        self.say('c3', now - 30, 'Ask: Ship it?\n- Yes\n- No')     # the room changed
        self.assertEqual([c['mid'] for c in asks.open_cards(self.summary())], ['c3', 'c2'])

    def test_repeat_and_old_go_to_older(self):
        now = time.time()
        self.say('a', now - 9 * 86400, 'Ask: Shall I set it up?\n- Yes\n- No')
        self.say('b', now - 3600, 'Ask: Still waiting on you: P131. Should tasks get your tools?\n- Yes\n- No')
        self.say('c', now - 600, 'Ask: Still waiting on you: P131. Should every task get your tools?\n- Yes\n- No')
        led = points.load(self.rid)
        led['lastPersonAt'] = now - 100
        points._save(self.rid, led)
        waiting, older = asks.decisions(self.summary(), now=now)
        self.assertEqual([c['mid'] for c in waiting], ['c'])
        self.assertEqual([(c['mid'], c['why']) for c in older], [('b', 'repeat'), ('a', 'old')])
        # Not written past: an old question still waits.
        led['lastPersonAt'] = 0
        led['points'] = []
        points._save(self.rid, led)
        self.assertIn('a', [c['mid'] for c in asks.decisions(self.summary(), now=now)[0]])

    def test_repeats_are_worked_out_once_per_list(self):
        # #265: ~100 open asks made ~4.9k comparisons on every poll.
        qs = tuple(f'w{i}a w{i}b w{i}c?' for i in range(120)) + ('w3a w3b w3c?',)
        asks._repeats.cache_clear()
        with mock.patch.object(asks, '_same_ask', wraps=asks._same_ask) as same:
            self.assertEqual(asks._repeats(qs), frozenset({120}))
            first = same.call_count
            self.assertEqual(asks._repeats(qs), frozenset({120}))
            self.assertEqual(same.call_count, first, 'an unchanged list compares nothing again')

    def test_same_ask(self):
        self.assertTrue(asks._same_ask('Item 9: cancel the old comeback call hedge when a new dyna-zero put episode starts?',
                                       'Item 9: make the comeback call hedge give way when a new put episode starts?'))
        self.assertTrue(asks._same_ask('P340: can I run the #177 renewal?', 'P340: do I have your go for the #177 run?'))
        self.assertFalse(asks._same_ask('How many legs?', 'Which GraalVM?'))
        self.assertFalse(asks._same_ask('P340: run #177?', 'P340: run #178?'))

    def test_view_reads_cards_in_the_background(self):
        self.say('c1', time.time(), 'Ask: Which one?\n- A\n- B')
        points._LAST_DECISIONS.pop(self.rid, None)
        self.addCleanup(points._LAST_DECISIONS.pop, self.rid, None)
        started = []
        with mock.patch.object(asks, 'warm_cards', side_effect=started.append):
            needs = points.view(self.rid)['needs']
        self.assertEqual((needs['decisions'], needs.get('pending')), ([], True))
        self.assertEqual(len(started), 1)
        asks.warm_cards(self.summary())
        for _ in range(100):
            if asks.cards_ready(self.summary()):
                break
            time.sleep(0.02)
        needs = points.view(self.rid)['needs']
        self.assertEqual([c['mid'] for c in needs['decisions']], ['c1'])
        self.assertNotIn('pending', needs)
        json.dumps(needs)
        # A new card: the last list shows while the cards are read again, and says so.
        self.say('c2', time.time(), 'Ask: And which colour?\n- Red\n- Blue')
        with mock.patch.object(asks, 'warm_cards'):
            needs = points.view(self.rid)['needs']
        self.assertFalse(asks.cards_ready(self.summary()))
        self.assertEqual(([c['mid'] for c in needs['decisions']], needs.get('pending')), (['c1'], True))

    def test_seen_endpoint(self):
        led = points.load(self.rid)
        status, _, body = self.call('POST', '/api/room/points/seen',
                                    {'roomId': self.rid, 'mids': ['ans'], 'how': 'link'})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['seen'], ['P5'])
        self.assertEqual(next(p for p in points.load(self.rid)['points'] if p['id'] == 'P5')['seenHow'], 'link')


for _name in dir(point_cards.Resolver):
    if _name.startswith('test_') and _name not in vars(Decisions):
        setattr(Decisions, _name, None)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SeenOnlyOnScreen(unittest.TestCase):
    """The chat's seen timing counts only while it is on screen: a Dock panel
    parked or behind another tab keeps its layout (visibility:hidden), so its
    frame says so, not the geometry inside it."""

    def test_parked_or_hidden_frame_is_not_in_front(self):
        from tests.test_session_landing import js_function, run_node
        script = r"""
const frame = { parked: false, rects: 1, vis: 'visible', opacity: '1', hidden: false };
const window = { top: null, frameElement: {
  closest: sel => (sel === '[hidden]' ? frame.hidden : frame.parked) ? {} : null,
  getClientRects: () => ({ length: frame.rects }),
  ownerDocument: { defaultView: { getComputedStyle: () => ({ visibility: frame.vis, opacity: frame.opacity }) } } } };
window.top = { document: { visibilityState: 'visible', hasFocus: () => true } };
const document = { visibilityState: 'visible', hidden: false, hasFocus: () => true };
""" + "\n".join(js_function(n) for n in ("pageInFront", "chatOffScreen", "frameHidden")) + r"""
const out = { shown: pageInFront() };
frame.parked = true; out.parked = pageInFront(); frame.parked = false;
frame.vis = 'hidden'; out.invisible = pageInFront(); frame.vis = 'visible';
frame.rects = 0; out.noBox = pageInFront(); frame.rects = 1;
frame.hidden = true; out.hiddenAttr = pageInFront(); frame.hidden = false;
console.log(JSON.stringify(out));
"""
        self.assertEqual(run_node(script), {"shown": True, "parked": False, "invisible": False,
                                            "noBox": False, "hiddenAttr": False})


if __name__ == '__main__':
    unittest.main()
