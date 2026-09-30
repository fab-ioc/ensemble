import io
import json
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch, Mock

import dashboard
import feedback


class Scrubber(unittest.TestCase):
    def test_identities_paths_emails_hosts_tokens(self):
        raw = (r'C:\Users\Alice\work /Users/Alice/work /home/Alice/work '
               'Alice Smith alice@example.com ALICE-LAPTOP @alice-gh '
               'host.tail-name.ts.net http://hub:8765/session?id=secret '
               'ghp_abcdef github_pat_abcdef sk-secret Bearer opaque '
               'token=opaque WindowsUser 100.64.1.2 [image] private.png')
        clean = feedback.scrub(raw, ['Alice Smith', 'Alice', 'ALICE-LAPTOP', 'WindowsUser'])
        for value in ['Alice', 'Smith', 'example.com', 'alice-gh', 'tail-name', 'hub:',
                      'abcdef', 'opaque', 'sk-secret', 'WindowsUser', '100.64', 'private.png']:
            self.assertNotIn(value.lower(), clean.lower())
        self.assertEqual(clean.count('~/work'), 3)

    def test_encoded_and_slash_paths_and_embedded_images(self):
        clean = feedback.scrub('C%3A%5CUsers%5Calice%5Cfoo /Users/bob/a ![alice](alice.png) <img src="foo">')
        self.assertNotIn('alice', clean)
        self.assertNotIn('bob', clean)
        self.assertIn('~/foo', clean)


class FeedbackEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = patch.object(dashboard.Handler, '_gate', return_value=True)
        cls.logs = patch.object(dashboard.Handler, 'log_message')
        cls.gate.start(); cls.logs.start()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        cls.gate.stop(); cls.logs.stop()

    def setUp(self):
        feedback._drafts.clear()
        self.settings = dict(feedbackRepo='fab-ioc/ensemble', feedbackRelayUrl='https://relay.example')
        self.patches = [patch.object(dashboard, 'load_settings', return_value=self.settings),
                        patch.object(feedback, 'output', return_value=''),
                        patch.object(feedback, 'identities', return_value=['Alice', 'alice-gh'])]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.data = dict(title='A bug', description='Please fix this', kind='bug', name='Alice', technical=False)

    def post(self, path, data, headers=None):
        req = urllib.request.Request(self.base + '/api/feedback/' + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e: return e.code, json.load(e)

    def draft(self, **kw):
        code, result = self.post('preview', self.data | kw)
        self.assertEqual(code, 200, result)
        return result

    def test_gh_posts_exact_preview_and_deduplicates(self):
        with patch.object(feedback, 'output', return_value='alice-gh'), patch.object(feedback, 'run') as run:
            run.return_value = Mock(returncode=0, stdout='{"html_url":"https://github.com/fab-ioc/ensemble/issues/42"}')
            draft = self.draft()
            self.assertEqual(draft['route'], 'gh')
            _, result = self.post('send', {'id': draft['id'], 'body': 'injected'})
            self.assertTrue(result['ok'])
            payload = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(payload['body'], draft['body'])
            self.assertEqual(payload['labels'], ['feedback', 'bug'])
            self.post('send', {'id': draft['id']})
            self.assertEqual(run.call_count, 1)

    def test_anonymous_never_posts_with_gh_and_scrubs_preview(self):
        with patch.object(feedback, 'output', return_value='alice-gh'), patch.object(feedback, 'run') as run:
            draft = self.draft(anonymous=True, title='Alice bug', description='alice-gh /Users/Alice/f.txt')
            self.assertEqual(draft['route'], 'relay')
            self.assertNotIn('Alice', draft['title'] + draft['body'])
            opener = Mock()
            opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{"url":"https://github.com/fab-ioc/ensemble/issues/42"}'))
            opener.open.return_value.__exit__ = Mock(return_value=False)
            with patch.object(feedback.urllib.request, 'build_opener', return_value=opener):
                _, result = self.post('send', {'id': draft['id']})
            self.assertTrue(result['ok'])
            payload = json.loads(opener.open.call_args.args[0].data)
            self.assertEqual(set(payload), {'title', 'body', 'kind', 'repo'})
            self.assertEqual(payload['body'], draft['body'])
            run.assert_not_called()

    def test_named_relay_requires_typed_name(self):
        self.assertEqual(self.post('preview', self.data | {'name': ''})[0], 400)
        draft = self.draft()
        self.assertIn('Submitted by: Alice', draft['body'])

    def test_no_relay_fallback_is_explicit_and_keeps_scrubbed_text(self):
        self.settings['feedbackRelayUrl'] = ''
        draft = self.draft(anonymous=True)
        self.assertEqual(draft['route'], 'browser')
        _, result = self.post('send', {'id': draft['id']})
        self.assertTrue(result['fallback'])
        self.assertIn('not anonymous', result['message'])
        self.assertTrue(result['url'].startswith('https://github.com/fab-ioc/ensemble/issues/new?'))
        self.assertNotIn('Alice', result['url'])

    def test_relay_down_offers_fallback_and_preserves_retry(self):
        draft = self.draft(anonymous=True)
        opener = Mock(); opener.open.side_effect = urllib.error.URLError('private host path')
        with patch.object(feedback.urllib.request, 'build_opener', return_value=opener):
            _, result = self.post('send', {'id': draft['id']})
        self.assertTrue(result['fallback'])
        self.assertNotIn('private host', result['message'])
        self.assertEqual(feedback._drafts[draft['id']]['state'], 'ready')

    def test_rate_limit_does_not_bypass_through_fallback(self):
        draft = self.draft(anonymous=True)
        opener = Mock(); opener.open.side_effect = urllib.error.HTTPError('url', 429, 'secret', {}, None)
        with patch.object(feedback.urllib.request, 'build_opener', return_value=opener):
            _, result = self.post('send', {'id': draft['id']})
        self.assertNotIn('fallback', result)
        self.assertIn('limit', result['message'])

    def test_gh_failure_and_changed_identity(self):
        with patch.object(feedback, 'output', return_value='alice-gh'):
            draft = self.draft()
        _, result = self.post('send', {'id': draft['id']})
        self.assertIn('account changed', result['message'])
        with patch.object(feedback, 'output', return_value='alice-gh'), patch.object(feedback, 'run', return_value=Mock(returncode=1)):
            _, result = self.post('send', {'id': draft['id']})
        self.assertIn('refused', result['message'])

    def test_limits_origin_and_diagnostic_allowlist(self):
        self.assertEqual(self.post('preview', self.data | {'description': 'a' * 41000})[0], 413)
        self.assertEqual(self.post('preview', self.data, {'Origin': 'https://evil.example'})[0], 403)
        draft = self.draft(anonymous=True, technical=True, page='/session?secret=1', browser='Alice browser', width='private')
        self.assertIn('Page: Other', draft['body'])
        self.assertNotIn('Alice browser', draft['body'])


class RelayTests(unittest.TestCase):
    def test_node_relay_suite(self):
        r = subprocess.run(['node', '--test', 'relay/feedback/worker.test.mjs'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
