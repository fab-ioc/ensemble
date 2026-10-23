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
                 'Ensemble Dashboard P23', 'Dock **P23**', 'P23a', 'Answered Dock about P23', 'P23. Done', 'DK-P23',
                 '- outer\n    - P23', '- outer\n\t- P23', '__P23__', '_P23_', '__Dock__ __P23__']
        rows = self.match(texts)
        self.assertEqual([r[0]['project'] for r in rows], ['ed'] * 4 + ['dock', 'dock', 'ed', 'dock', 'ed', 'ed', 'ed', 'dock'] + ['ed'] * 4 + ['dock'])
        self.assertEqual(rows[9][0]['others'], ['dock'])

    def test_no_code_paths_urls_or_word_fragments(self):
        texts = ['`P23`', '``P23``', '```js\nP23\n```', '~~~\nP23\n~~~', '    P23',
                 'https://example.test/P23', 'https://example.test?q=P23', 'www.example.test/P23',
                 '[P23](https://example.test)', 'C:\\work\\P23.txt', '/tmp/P23', './P23',
                 'P23/file.txt', 'P23.txt', 'file.P23', 'MP3', 'P2P', 'éP23', 'P23é', 'P0', 'P1234567',
                 '`code\nP23\ncode`', 'mailto:P23@example.test', 'P23@example.test', 'ZZ-P23',
                 'word__P23__', '__P23__word', '    code P23', '\tP23', '    - P23',
                 '- prose\n\n        - P23']
        self.assertEqual(self.match(texts), [[] for _ in texts])

    def test_duplicate_project_alias_is_ambiguous(self):
        ctx = json.loads(json.dumps(self.CTX))
        ctx['projects'].append({'id': 'other', 'name': 'Dock', 'key': 'D2'})
        self.assertEqual(self.match(['Dock P23', 'Answered Dock about P23'], ctx), [[], []])

    def test_long_unbroken_balloon_is_scanned_without_backtracking(self):
        got = self.run_js("const t = performance.now(); const s = 'x'.repeat(300000) + ' P23'; "
                          "const rows = P.refsIn(s, " + json.dumps(self.CTX) + "); "
                          "console.log(JSON.stringify({ms:performance.now()-t, ids:rows.map(r=>r.id)}));")
        self.assertEqual(got['ids'], ['P23'])
        self.assertLess(got['ms'], 1000, 'a long prose token must not cause quadratic URL/path scanning')

    def test_async_resolution_unknown_ambiguous_and_cache(self):
        code = r'''
const ctx = CTX, calls = [];
globalThis.fetch = async url => {
  const q = new URL(url, 'http://h').searchParams, pid = q.get('project'), ids = q.get('ids').split(',');
  calls.push(...ids.map(id => pid + ':' + id));
  return {ok: true, status: 200, json: async () => ({points: Object.fromEntries(ids.map(id => [id, id === 'P23'
    ? {id, roomId: pid, mid: 'm1', text: '<first>\nsecond', stage: 'planned', createdAt: 10, projectId: pid, project: pid} : null]))})};
};
const p = P.create({room: 'room', changed() {}}), settle = () => new Promise(r => setImmediate(r));
(async () => {
  const text = 'P23; Dock P23; P99';
  p.replace(text, ctx); await settle(); const html = p.replace(text, ctx);
  p.replace('Answered Dock about P23', ctx); await settle();
  const ambiguous = p.replace('Answered Dock about P23', ctx);
  const duplicateCtx = {...ctx, projects: [...ctx.projects, {id:'other',name:'Dock',key:'D2'}]};
  const duplicateAlias = p.replace('Answered Dock about P23', duplicateCtx);
  p.replace(text, ctx); console.log(JSON.stringify({html, ambiguous, duplicateAlias, calls}));
})();
'''.replace('CTX', json.dumps(self.CTX))
        got = self.run_js(code)
        self.assertEqual(got['html'].count('class="task-chip point-chip"'), 2)
        self.assertIn('in progress', got['html'])
        self.assertNotIn('<first>', got['html'])
        self.assertEqual(got['ambiguous'], 'Answered Dock about P23')
        self.assertEqual(got['duplicateAlias'], 'Answered Dock about P23')
        # Each point of each project asked once, for the page's life.
        self.assertEqual(len(got['calls']), len(set(got['calls'])), got['calls'])
        self.assertEqual(len(got['calls']), 4, got['calls'])


class Lookup(Hub):
    def setUp(self):
        super().setUp()
        self.addCleanup(points._CACHE.clear)
        dashboard._POINT_INDEX.clear()
        self.addCleanup(dashboard._POINT_INDEX.clear)
        self.a = self.room('PO', self.ed)
        self.b = self.room('Other PO', self.ot)
        self.seed(self.a, 'P291', 'Our ask', 'acked')
        self.seed(self.b, 'P291', 'Other ask', 'open')
        p = mock.patch.object(points, 'sync', side_effect=lambda rid, **k: points.load(rid, **k))
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

    def test_the_points_of_a_page_in_one_request(self):
        """#265: every chip of a draw asked at once; an unknown one is null."""
        status, _, body = self.call('GET', '/api/point/refs?ids=P291,P99&project=' + self.ed)
        got = json.loads(body)['points']
        self.assertEqual((status, got['P291']['text'], got['P99']), (200, 'Our ask', None))
        status, _, body = self.call('GET', '/api/point/refs?ids=P291&room=' + self.b)
        self.assertEqual(json.loads(body)['points']['P291']['text'], 'Other ask')

    def test_a_lookup_reads_the_index_not_the_transcripts(self):
        """#265: a lookup is a dict read kept in step with the ledgers: a
        point saved since is found at once, and no transcript is read."""
        self.assertIsNone(dashboard.point_ref('P300', self.ed))
        self.seed(self.a, 'P300', 'New ask', 'open')
        self.assertEqual(dashboard.point_ref('P300', self.ed)['text'], 'New ask')
        points.sync.reset_mock()
        with mock.patch.object(dashboard, '_task_index', wraps=dashboard._task_index) as walk:
            for _ in range(5):
                dashboard.point_ref('P291', self.ed)
        self.assertEqual(walk.call_count, 0, 'the rooms are not walked per lookup')
        points.sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
