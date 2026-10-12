"""Needs you (#209): an answered point closes by itself once its answer was
seen and the person wrote again (``seen``), when a later point names its task
(``superseded``), or once at the hub's start when it is old (``migrated``);
what a send closed comes back if the send is taken back, and a closed one
reopens. The Decisions are the question cards still waiting on the person:
one asked again keeps its newest card, an old one written past is an older
question, and a chat's cards are read in the background."""
from __future__ import annotations

import json
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
            _out, ids = self.send(rid, "Now #26 breaks on start", key="k9", at=self.t0 + 20)
            self.assertEqual(self.point(rid, p1)["ackedBy"], "superseded")
            self.assertEqual(self.point(rid, p1)["supersededBy"], ids[0])
            self.assertEqual(self.point(rid, p2)["state"], "delivered")
            # The send taken back: what it closed is answered again.
            points.discard(rid, ids, key="k9")
            self.assertEqual(self.point(rid, p1)["state"], "delivered")
            self.assertNotIn("ackedBy", self.point(rid, p1))

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


if __name__ == '__main__':
    unittest.main()
