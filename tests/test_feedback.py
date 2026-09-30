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

USERS = 'Us' + 'ers'  # Synthetic paths must not trip the repository privacy scan.


class Scrubber(unittest.TestCase):
    def test_identities_paths_emails_hosts_tokens(self):
        raw = (rf'C:\{USERS}\Alice\work /{USERS}/Alice/work /home/Alice/work '
               'Alice Smith alice@example.com ALICE-LAPTOP @alice-gh '
               'host.tail-name.ts' + '.net http://hub:8765/session?id=secret '
               'ghp_abcdef github_pat_abcdef sk-secret Bearer opaque '
               'token=opaque WindowsUser 100.64.1.2 [image] private.png')
        clean = feedback.scrub(raw, ['Alice Smith', 'Alice', 'ALICE-LAPTOP', 'WindowsUser'])
        for value in ['Alice', 'Smith', 'example.com', 'alice-gh', 'tail-name', 'hub:',
                      'abcdef', 'opaque', 'sk-secret', 'WindowsUser', '100.64', 'private.png']:
            self.assertNotIn(value.lower(), clean.lower())
        self.assertEqual(clean.count('~/work'), 3)

    def test_encoded_and_slash_paths_and_embedded_images(self):
        clean = feedback.scrub(f'C%3A%5C{USERS}%5Calice%5Cfoo /{USERS}/bob/a\n![alice](alice.png) <img src="foo">')
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
        req = urllib.request.Request(self.base + '/api/feedback/' + path, data=json.dumps(data, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json', **(headers or {})})
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
            self.assertIn('omitted', result['warning'])
            self.assertIn('### Bug report', draft['body'])
            payload = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(payload['body'], draft['body'])
            self.assertEqual(payload['labels'], ['feedback', 'bug'])
            self.post('send', {'id': draft['id']})
            self.assertEqual(run.call_count, 1)

    def test_persisted_hub_token_and_quoted_secrets_are_redacted(self):
        secrets = ['bare-hub-credential', 'opaque-private-value', 'private password with spaces', 'private API key']
        content = 'bare-hub-credential {"access_token":"opaque-private-value", "password":"private password with spaces"} api_key: \'private API key\''
        with patch.object(dashboard, 'ACCESS_TOKEN', secrets[0]):
            draft = self.draft(anonymous=True, title=content, description=content)
        for secret in secrets:
            self.assertNotIn(secret, draft['title'] + draft['body'])
        self.assertNotIn('private_values', draft)

    def test_anonymous_reference_images_and_destinations_are_omitted(self):
        for image in ['![private alt][shot]', '![shot][]', '![shot]', '![nested [alt]][shot]']:
            for destination in ['/user-attachments/assets/private-id', '//private-host/private.png']:
                draft = self.draft(anonymous=True, description=image + '\n\n[shot]: ' + destination)
                self.assertNotIn('![', draft['body'])
                self.assertNotIn(destination, draft['body'])
                self.assertNotIn('private alt', draft['body'])

    def test_percent_decoding_cannot_split_quoted_secret_values(self):
        examples = ['{"password": "prefix%22 secret-tail"}',
                    "password: 'prefix%27 secret-tail'",
                    '{"pass%77ord": "prefix%22 secret-tail"}',
                    '{"api_key": "prefix\\\" escaped-tail"}']
        examples += [urllib.parse.quote(examples[0]), urllib.parse.quote(examples[1])]
        for text in examples:
            with self.subTest(text=text):
                draft = self.draft(anonymous=True, title=text, description=text)
                for field in ['title', 'body']:
                    self.assertNotIn('secret-tail', draft[field])
                    self.assertNotIn('escaped-tail', draft[field])
                    self.assertNotIn('prefix', draft[field])
                    self.assertIn('[secret removed]', draft[field])

    def test_secret_key_variants_encodings_and_formats(self):
        examples = ['aws_secret_access_key = opaque-tail', 'passwd: opaque-tail', 'pwd=opaque-tail',
                    'Cookie: session=opaque-tail', 'CLIENT_SECRET_ID=opaque-tail',
                    'password%253Dopaque-tail', 'AWS_ACCESS_KEY_ID=AKIAOPAQUETAIL1234',
                    'bot xoxb-111-opaque-tail', 'key:\n-----BEGIN PRIVATE KEY-----\nopaque-tail\n-----END PRIVATE KEY-----',
                    'AKIAOPAQUETAIL1234 in prose']
        for text in examples:
            with self.subTest(text=text):
                draft = self.draft(anonymous=True, title=text.replace('\n', ' '), description=text)
                for field in ['title', 'body']:
                    self.assertNotIn('opaque-tail', draft[field].lower())
                    self.assertNotIn('AKIAOPAQUE', draft[field])

    def test_nested_credentials_removed_from_preview_and_relay_payload(self):
        examples = [
            '{"auth":{"access_token":"opaque-private-value"}}',
            '{"auth": {"password": "opaque private value"}}',
            '{"auth":{"password":"opaque private value"}}',
            '{"auth": {"token": "prefix%22 secret-tail"}}',
            '{"auth":{"pass%77ord":"prefix%22 secret-tail"}}',
            '{"config":[{"auth":{"password":"opaque private value"}}]}',
            '{"name":"public","auth":{"access_token":"opaque-private-value","password":"opaque private value"}}',
            'auth:\n  token: \'prefix%27 secret-tail\'',
            'auth:\n  password: opaque private value',
            'auth:\n  password: |\n    opaque private value',
            '{"auth":{"password":{"value": "opaque private value"}}}',
        ]
        examples += [urllib.parse.quote(examples[0]), urllib.parse.quote(examples[3])]
        for text in examples:
            with self.subTest(text=text):
                draft = self.draft(anonymous=True, title=text, description=text)
                opener = Mock()
                opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{"url":"https://github.com/fab-ioc/ensemble/issues/42"}'))
                opener.open.return_value.__exit__ = Mock(return_value=False)
                with patch.object(feedback.urllib.request, 'build_opener', return_value=opener):
                    _, result = self.post('send', {'id': draft['id']})
                self.assertTrue(result['ok'])
                wire = json.loads(opener.open.call_args.args[0].data)
                for field in ('title', 'body'):
                    self.assertEqual(wire[field], draft[field])
                    for secret in ('opaque-private-value', 'opaque private value', 'prefix', 'secret-tail'):
                        self.assertNotIn(secret, draft[field])
                    self.assertIn('[secret removed]', draft[field])

    def test_unicode_relay_envelope_fits_and_posts_exact_preview(self):
        draft = self.draft(description='é' * 9000)
        response = b'{"url":"https://github.com/fab-ioc/ensemble/issues/42"}'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(response))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(feedback.urllib.request, 'build_opener', return_value=opener):
            _, result = self.post('send', {'id': draft['id']})
        self.assertTrue(result['ok'])
        wire = opener.open.call_args.args[0].data
        self.assertLessEqual(len(wire), feedback.MAX_RELAY_REQUEST)
        self.assertEqual(json.loads(wire)['body'], draft['body'])

    def test_encoded_envelope_limit_checked_at_preview(self):
        # Control characters expand sixfold in JSON even without ASCII escaping.
        with self.assertRaises(feedback.FeedbackError):
            feedback.preview(self.data | {'description': '\x01' * 6000}, self.settings)

    def test_mixed_case_repo_and_strict_issue_url(self):
        self.settings['feedbackRepo'] = 'Fab-ioc/Ensemble'
        with patch.object(feedback, 'output', return_value='alice-gh'), patch.object(feedback, 'run') as run:
            run.return_value = Mock(returncode=0, stdout='{"html_url":"https://github.com/fab-ioc/ensemble/issues/42", "labels":[{"name":"feedback"},{"name":"idea"}]}')
            draft = self.draft(kind='idea')
            _, result = self.post('send', {'id': draft['id']})
        self.assertTrue(result['ok'])
        self.assertNotIn('warning', result)
        self.assertIn('### Idea', draft['body'])
        for url in ['https://github.com.evil/fab-ioc/ensemble/issues/42', 'https://github.com/fab-ioc/ensemble/issues/42/evil', 'http://github.com/fab-ioc/ensemble/issues/42']:
            self.assertFalse(feedback.issue_url(url, 'Fab-ioc/Ensemble'))

    def test_anonymous_never_posts_with_gh_and_scrubs_preview(self):
        with patch.object(feedback, 'output', return_value='alice-gh'), patch.object(feedback, 'run') as run:
            draft = self.draft(anonymous=True, title='Alice bug', description=f'alice-gh /{USERS}/Alice/f.txt')
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
