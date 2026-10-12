"""A point's question card (#206): the agent's card that follows a point,
and a point chip that lands on it while it waits ("the P515 card" always)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import asks
import chatroom
import dashboard
import points
from tests.test_task_numbers import Hub

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')

ASK_A = [{'n': 0, 'question': 'Which one?', 'kind': 'decision', 'recommended': 0,
          'options': [{'label': 'A', 'detail': '', 'recommended': True}, {'label': 'B', 'detail': '', 'recommended': False}]}]


class Resolver(Hub):
    def setUp(self):
        super().setUp()
        self.addCleanup(points._CACHE.clear)
        asks._ROOM_CACHE.clear()
        self.addCleanup(asks._ROOM_CACHE.clear)
        for kept in (asks._CARDS, asks._SID_CARDS):
            kept.clear(); self.addCleanup(kept.clear)
        self.rid = self.room('PO', self.ed)
        p = mock.patch.object(points, 'sync', side_effect=lambda rid, **k: points.load(rid))
        p.start(); self.addCleanup(p.stop)
        self.point('P5', 100, [{'key': 'ans', 'mid': 'ans', 'at': 110, 'how': 're'}])
        self.point('P6', 100, [])

    def point(self, pid, at, answers):
        led = points.load(self.rid)
        led['points'].append({'id': pid, 'text': 'his words', 'state': 'delivered', 'mid': 'his-' + pid,
                              'createdAt': at, 'answers': answers})
        points._save(self.rid, led)

    def say(self, mid, ts, text, asks_=None, **kw):
        full = chatroom.get_room(self.rid, public=False)
        m = {'id': mid, 'from': 'claude', 'to': 'user', 'text': text, 'ts': ts, **kw}
        if asks_ is not None:
            m.update(asks=asks_, askAudience='user')
        full['messages'].append(m)
        full['updatedAt'] = ts
        chatroom.update_room(full)
        asks._ROOM_CACHE.clear()

    def card(self, pid='P5'):
        ref = dashboard.point_ref(pid, self.ed)
        self.assertIsNotNone(ref)
        return ref.get('card')

    def answer(self, mid, n=0):
        led = points.load(self.rid)
        led['asks'].setdefault(mid, {})[str(n)] = {'option': 'A', 'comment': '', 'question': 'Which one?', 'at': 1}
        points._save(self.rid, led)

    def test_no_card_keeps_his_message(self):
        self.say('ans', 110, 'Re P5: done, nothing to ask.')
        self.assertIsNone(self.card())
        self.assertEqual(dashboard.point_ref('P5', self.ed)['mid'], 'his-P5')

    def test_answer_holding_an_ask_is_the_card(self):
        self.say('ans', 110, 'Re P5: here is why.\n\nAsk: Which one?\n- A\n- B')
        self.assertEqual(self.card(), {'mid': 'ans', 'n': 0, 'question': 'Which one?', 'open': True})

    def test_later_re_card_without_colon_and_newest_open_wins(self):
        self.say('ans', 110, 'Re P5: here is why.\n\nAsk: Which one?\n- A\n- B')
        self.say('later', 120, 'Re P5 (the leg limit in #240).', ASK_A)
        self.say('p6', 125, 'Re P6 (other).', ASK_A)
        self.say('early', 90, 'Re P5 (before it was raised).', ASK_A)
        self.say('quoted', 126, '> Re P5 (only quoted)', ASK_A)
        self.assertEqual(self.card()['mid'], 'later')
        self.assertEqual(self.card('P6')['mid'], 'p6')
        self.answer('later')
        self.assertEqual(self.card(), {'mid': 'ans', 'n': 0, 'question': 'Which one?', 'open': True})
        self.answer('ans')
        self.assertEqual(self.card(), {'mid': 'later', 'n': 0, 'question': 'Which one?', 'open': False},
                         'all answered: the newest card, for "the P5 card"')

    def test_approval_of_a_recommendation_answers_it(self):
        self.say('later', 120, 'Re P5 (legs).', ASK_A)
        led = points.load(self.rid)
        led['approvals']['later'] = 1
        points._save(self.rid, led)
        self.assertFalse(self.card()['open'])

    def test_second_ask_of_a_card_still_open(self):
        two = ASK_A + [{**ASK_A[0], 'n': 1, 'question': 'And then?'}]
        self.say('later', 120, 'Re P5 (two asks).', two)
        self.answer('later', 0)
        self.assertEqual(self.card(), {'mid': 'later', 'n': 1, 'question': 'And then?', 'open': True})

    def test_done_task_waits_for_nobody(self):
        self.say('later', 120, 'Re P5 (legs).', ASK_A)
        full = chatroom.get_room(self.rid, public=False)
        full['workflow'] = 'done'
        chatroom.update_room(full)
        self.assertFalse(self.card()['open'])

    def test_each_point_gets_its_own_question_of_one_card(self):
        self.say('both', 120, 'Re P5: the legs.\n\nAsk: First?\n- A\n- B\n\nRe P6: the stops.\n\nAsk: Second?\n- C\n- D')
        with mock.patch.object(asks, 'message_asks', wraps=asks.message_asks) as read:
            self.assertEqual(self.card('P5'), {'mid': 'both', 'n': 0, 'question': 'First?', 'open': True})
            self.assertEqual(self.card('P6'), {'mid': 'both', 'n': 1, 'question': 'Second?', 'open': True})
        self.assertEqual(read.call_count, 1, 'the room is read once for both chips')
        self.answer('both', 1)
        self.assertTrue(self.card('P5')['open'])
        self.assertEqual(self.card('P6'), {'mid': 'both', 'n': 1, 'question': 'Second?', 'open': False})

    def test_a_point_whose_part_asks_nothing_has_no_card_there(self):
        self.say('split', 120, 'Re P5: answered in prose.\n\nRe P6: decisions.\n\nAsk: First?\n- A\n- B\n\nAsk: Second?\n- C\n- D')
        self.assertIsNone(self.card('P5'))
        self.assertEqual(self.card('P6'), {'mid': 'split', 'n': 0, 'question': 'First?', 'open': True})
        self.answer('split', 0)
        self.answer('split', 1)
        self.assertIsNone(self.card('P5'))
        self.assertEqual(self.card('P6'), {'mid': 'split', 'n': 0, 'question': 'First?', 'open': False})

    def test_stored_card_with_several_heads_is_no_points_card(self):
        # Whose stored ask is whose is not written down: the chips keep his message.
        two = ASK_A + [{**ASK_A[0], 'n': 1, 'question': 'And then?'}]
        self.say('pair', 120, 'Re P5: answered in prose.\n\nRe P6: decisions.', two)
        self.assertIsNone(self.card('P5'))
        self.assertIsNone(self.card('P6'))
        self.say('three', 130, 'Re P5 (legs).\n\nRe P6 (stops).', ASK_A)
        self.assertIsNone(self.card('P6'))
        self.assertEqual(dashboard.point_ref('P6', self.ed)['mid'], 'his-P6')

    def test_answer_that_says_re_another_point_lends_it_nothing(self):
        # The reply after P5 is its answer in the ledger, but it asks Re P6.
        self.say('ans', 110, 'We finished P5 in prose.\n\nRe P6: decisions.\n\nAsk: Which one?\n- A\n- B')
        self.assertIsNone(self.card('P5'))
        self.assertEqual(dashboard.point_ref('P5', self.ed)['mid'], 'his-P5')
        self.assertEqual(self.card('P6'), {'mid': 'ans', 'n': 0, 'question': 'Which one?', 'open': True})

    def test_answer_without_heads_holds_its_asks(self):
        self.say('ans', 110, 'Here is why.\n\nAsk: Which one?\n- A\n- B')
        self.assertEqual(self.card(), {'mid': 'ans', 'n': 0, 'question': 'Which one?', 'open': True})

    def test_one_head_for_several_points_and_code_is_no_head(self):
        self.say('list', 120, 'Re P5, P6 (both legs).', ASK_A)
        self.assertEqual(self.card('P6')['mid'], 'list')
        self.say('code', 130, 'An example:\n\n    Re P5 (in code)\n', ASK_A)
        self.say('fence', 131, '```\nRe P5 (in code)\n```', ASK_A)
        self.assertEqual(self.card()['mid'], 'list')

    def test_card_in_a_session_the_chat_rotated_out_of(self):
        full = chatroom.get_room(self.rid, public=False)
        full['mode'] = 'solo'
        for p in full['participants']:
            if p.get('identity') == 'claude':
                p['rotations'] = [{'fromSessionId': 'old', 'toSessionId': ''}]
        chatroom.update_room(full)
        text = 'Re P5 (the legs).\n\nAsk: Which one?\n- A\n- B'
        turns = {'old:3': {'role': 'assistant', 'text': text, 'timestamp': '1970-01-01T00:02:10Z'}}
        for target, fake in ((dashboard, {'solo_turns': lambda room, sid: turns if sid == 'old' else {}}),
                             (points, {'_session_stat': lambda sid: [1, 1]})):
            for name, f in fake.items():
                p = mock.patch.object(target, name, side_effect=f)
                p.start(); self.addCleanup(p.stop)
        self.assertEqual(self.card(), {'mid': 'old:3', 'n': 0, 'question': 'Which one?', 'open': True})

    def test_endpoint_carries_the_card(self):
        self.say('later', 120, 'Re P5 (legs).', ASK_A)
        status, _, body = self.call('GET', '/api/point/ref?ref=P5&project=' + self.ed)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['card'], {'mid': 'later', 'n': 0, 'question': 'Which one?', 'open': True})


@unittest.skipUnless(NODE, 'node required')
class Chip(unittest.TestCase):
    """The chip's link and its card's links, from what /api/point/ref says."""

    def chips(self, texts, card):
        code = r'''
globalThis.TaskCard = require('./static/taskcard.js'); const P = require('./static/pointrefs.js');
const CARD = %s;
globalThis.fetch = async url => ({ok: true, status: 200, json: async () => ({id: 'P5', roomId: 'room-po', mid: 'his-P5',
  text: 'his words', stage: 'delivered', createdAt: 10, projectId: 'ed', project: 'E', ...(CARD ? {card: CARD} : {})})});
const ctx = {own: 'ed', projects: [{id: 'ed', key: 'ED', name: 'E'}]};
const p = P.create({room: 'room-po', changed() {}});
(async () => {
  const texts = %s; texts.forEach(t => p.replace(t, ctx)); await new Promise(r => setImmediate(r));
  console.log(JSON.stringify(texts.map(t => p.replace(t, ctx))));
})();
''' % (json.dumps(card), json.dumps(texts))
        r = subprocess.run([NODE, '-e', code], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def attr(html, name):
        import re, html as h
        m = re.search(name + r'="([^"]*)"', html)
        return h.unescape(m.group(1)) if m else None

    def test_open_card_takes_the_chip_and_card_offers_both(self):
        [html] = self.chips(['Waiting on P5.'], {'mid': 'q1', 'n': 0, 'question': 'Which one?', 'open': True})
        self.assertEqual(self.attr(html, 'data-ref-msg'), 'q1')
        self.assertEqual(self.attr(html, 'data-ref-part'), 'ask:0')
        self.assertIn('msg=q1&part=ask:0', self.attr(html, 'href'))
        card = json.loads(self.attr(html, 'data-card'))
        self.assertEqual([l['text'] for l in card['links']], ['Answer the question ↓', 'His message'])
        self.assertEqual([l['point']['msg'] for l in card['links']], ['q1', 'his-P5'])
        self.assertEqual(card['links'][1]['point']['part'], 'pt:P5')

    def test_answered_card_only_for_the_card_phrase(self):
        answered = {'mid': 'q1', 'n': 0, 'question': 'Which one?', 'open': False}
        plain, phrase, before = self.chips(['Waiting on P5.', 'The P5 card: legs.', 'See card P5.'], answered)
        self.assertEqual(self.attr(plain, 'data-ref-msg'), 'his-P5')
        self.assertNotIn('links', json.loads(self.attr(plain, 'data-card')))
        for html in (phrase, before):
            self.assertEqual(self.attr(html, 'data-ref-msg'), 'q1')
            card = json.loads(self.attr(html, 'data-card'))
            self.assertEqual([l['text'] for l in card['links']], ['The question ↓', 'His message'])

    def test_no_card_is_todays_chip(self):
        [html] = self.chips(['The P5 card.'], None)
        self.assertEqual(self.attr(html, 'data-ref-msg'), 'his-P5')
        self.assertEqual(self.attr(html, 'data-ref-part'), 'pt:P5')


if __name__ == '__main__':
    unittest.main()
