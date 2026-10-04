"""Point matching, conservative project lookup and old closed points."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import points
from tests.test_task_numbers import Hub

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'node required')
class Matching(unittest.TestCase):
    def run_js(self, code):
        script = "globalThis.TaskCard = require('./static/taskcard.js'); const P = require('./static/pointrefs.js');\n" + code
        r = subprocess.run([NODE, '-e', script], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    CTX = {'own': 'ed', 'projects': [
        {'id': 'ed', 'key': 'ED', 'name': 'Ensemble Dashboard', 'aliases': ['Ensemble']},
        {'id': 'dock', 'key': 'DK', 'name': 'Dock', 'aliases': ['Dock']}]}

    def match(self, texts, ctx=None):
        return self.run_js('const ctx = ' + json.dumps(ctx or self.CTX) + '; console.log(JSON.stringify(' +
                           json.dumps(texts) + '.map(t => P.refsIn(t, ctx))));')

    def test_mentions_and_project_context(self):
        texts = ['Re P120:', 'P117', '(CEO point P119)', '- **P23**', 'Dock P23', 'DK P23',
                 'Ensemble Dashboard P23', 'Dock **P23**', 'P23a', 'Answered Dock about P23', 'P23. Done', 'DK-P23']
        rows = self.match(texts)
        self.assertEqual([r[0]['project'] for r in rows], ['ed'] * 4 + ['dock', 'dock', 'ed', 'dock', 'ed', 'ed', 'ed', 'dock'])
        self.assertEqual(rows[9][0]['others'], ['dock'])

    def test_no_code_paths_urls_or_word_fragments(self):
        texts = ['`P23`', '``P23``', '```js\nP23\n```', '~~~\nP23\n~~~', '    P23',
                 'https://example.test/P23', 'https://example.test?q=P23', 'www.example.test/P23',
                 '[P23](https://example.test)', 'C:\\work\\P23.txt', '/tmp/P23', './P23',
                 'P23/file.txt', 'P23.txt', 'file.P23', 'MP3', 'P2P', 'éP23', 'P23é', 'P0', 'P1234567',
                 '`code\nP23\ncode`', 'mailto:P23@example.test', 'P23@example.test', 'ZZ-P23']
        self.assertEqual(self.match(texts), [[] for _ in texts])

    def test_duplicate_project_alias_is_ambiguous(self):
        ctx = json.loads(json.dumps(self.CTX))
        ctx['projects'].append({'id': 'other', 'name': 'Dock', 'key': 'D2'})
        self.assertEqual(self.match(['Dock P23'], ctx), [[]])

    def test_async_resolution_unknown_ambiguous_and_cache(self):
        code = r'''
const ctx = CTX, calls = [];
globalThis.fetch = async url => {
  calls.push(url); const q = new URL(url, 'http://h').searchParams;
  const id = q.get('ref'), pid = q.get('project');
  return {ok: id === 'P23', status: id === 'P23' ? 200 : 404,
    json: async () => ({id, roomId: pid, mid: 'm1', text: '<first>\nsecond', stage: 'planned', createdAt: 10, projectId: pid, project: pid})};
};
const p = P.create({room: 'room', changed() {}}), settle = () => new Promise(r => setImmediate(r));
(async () => {
  const text = 'P23; Dock P23; P99';
  p.replace(text, ctx); await settle(); const html = p.replace(text, ctx);
  p.replace('Answered Dock about P23', ctx); await settle();
  const ambiguous = p.replace('Answered Dock about P23', ctx);
  p.replace(text, ctx); console.log(JSON.stringify({html, ambiguous, calls}));
})();
'''.replace('CTX', json.dumps(self.CTX))
        got = self.run_js(code)
        self.assertEqual(got['html'].count('class="task-chip point-chip"'), 2)
        self.assertIn('in progress', got['html'])
        self.assertNotIn('<first>', got['html'])
        self.assertEqual(got['ambiguous'], 'Answered Dock about P23')
        self.assertEqual(len(got['calls']), 4)
        self.assertEqual(len(set(got['calls'])), 4)


class Lookup(Hub):
    def setUp(self):
        super().setUp()
        self.addCleanup(points._CACHE.clear)
        self.a = self.room('PO', self.ed)
        self.b = self.room('Other PO', self.ot)
        self.seed(self.a, 'P291', 'Our ask', 'acked')
        self.seed(self.b, 'P291', 'Other ask', 'open')
        p = mock.patch.object(points, 'sync', side_effect=lambda rid: points.load(rid))
        p.start(); self.addCleanup(p.stop)

    def seed(self, rid, id, text, state):
        led = points.load(rid)
        led['points'].append({'id': id, 'text': text, 'state': state, 'mid': 'm-old', 'createdAt': 1, 'answers': []})
        points._save(rid, led)

    def test_project_endpoint_and_closed_point(self):
        for pid, text in [(self.ed, 'Our ask'), (self.ot, 'Other ask')]:
            status, _, body = self.call('GET', '/api/point/ref?ref=P291&project=' + pid)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['text'], text)
        status, _, body = self.call('GET', '/api/point/ref?ref=P291&room=' + self.a)
        self.assertEqual((status, json.loads(body)['stage']), (200, 'acked'))

    def test_collision_missing_and_unknown_project_stay_unresolved(self):
        other = self.room('Task', self.ed)
        self.seed(other, 'P291', 'Collision', 'open')
        for ref, pid in [('P291', self.ed), ('P99', self.ot), ('P291', 'missing')]:
            self.assertIsNone(dashboard.point_ref(ref, pid))

    def test_beyond_room_view_cap(self):
        for n in range(1, 210):
            self.seed(self.a, 'P' + str(n), 'closed', 'acked')
        self.assertEqual(dashboard.point_ref('P291', self.ed)['text'], 'Our ask')


if __name__ == '__main__':
    unittest.main()
