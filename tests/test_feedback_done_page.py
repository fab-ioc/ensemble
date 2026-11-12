"""Real Chrome, #332: Send feedback ends in a done state with one primary Close
(GitHub login, typed name, anonymous relay, prefilled-page fallback), a failed
send keeps the text, the next open is blank, and an unsent draft is labelled
and can be cleared. Computer (1440) and phone (390) widths.

GitHub and the relay are stubbed: nothing here can file a real issue."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

import feedback
from tests import test_settings_themes_page as browser
from tests.test_top_bar import CDP_JS as BASE_JS, NODE, CHROME

ISSUE = "https://github.com/fab-ioc/ensemble/issues/"
MODE = {"login": "", "relay": "ok"}   # set by the page script through the control server
POSTS = []                            # what reached the stubs


class Response:
    def __init__(self, n): self.body = json.dumps({"url": ISSUE + str(n)}).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.body


class Opener:
    def open(self, request, timeout=None):
        if not request.full_url.startswith("https://relay.test.invalid"):
            raise AssertionError("a test reached a real relay: " + request.full_url)
        POSTS.append(("relay", json.loads(request.data)))
        if MODE["relay"] != "ok":
            raise feedback.urllib.error.URLError("test: relay down")
        return Response(40 + len(POSTS))


def fake_output(*args, timeout=20):
    return MODE["login"] if args[:2] == ("gh", "api") else ""


def fake_run(*args, input=None, timeout=20):
    assert args[:2] == ("gh", "api") and "POST" in args, args
    POSTS.append(("gh", json.loads(input)))
    stdout = json.dumps({"html_url": ISSUE + "77", "labels": [{"name": "feedback"}, {"name": "bug"}]})
    return feedback.subprocess.CompletedProcess(args, 0, stdout, "")


class Control(BaseHTTPRequestHandler):
    def do_GET(self):
        MODE.update(dict(parse_qsl(urlsplit(self.path).query, keep_blank_values=True)))
        self.send_response(200); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a): pass


SCRIPT = BASE_JS[:BASE_JS.index('// What the bar shows')] + r"""
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  try {
    const {targetId} = await c.send('Target.createTarget', {url: 'about:blank'});
    const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten: true});
    const ev = async expression => {
      const r = await c.send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, sessionId);
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
    };
    const until = async e => { for (let i = 0; i < 200; i++) { try { if (await ev(e)) return; } catch {} await sleep(100); } throw Error('timeout ' + e); };
    const ctl = q => fetch(A.ctl + '/?' + q);
    const key = async k => { for (const type of ['keyDown', 'keyUp'])
      await c.send('Input.dispatchKeyEvent', {type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0}, sessionId); };
    // Fill and preview; send; wait for the done state or a status.
    const fill = (title, opts = {}) => ev(`(() => { const $ = id => document.getElementById(id);
      $('feedback-kind').value = ${JSON.stringify(opts.kind || 'bug')}; $('feedback-title').value = ${JSON.stringify(title)};
      $('feedback-description').value = 'What happened at ' + innerWidth; $('feedback-anonymous').checked = ${!!opts.anonymous};
      $('feedback-name').value = ${JSON.stringify(opts.name || '')};
      $('feedback-title').dispatchEvent(new Event('input', {bubbles: true}));
      ${opts.image ? `const dt = new DataTransfer(); dt.items.add(new File(['image'], 'shot.png', {type: 'image/png'}));
        $('feedback-description').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true}));` : ''}
      $('feedback-form').requestSubmit(); })()`);
    const previewed = () => until(`!document.getElementById('feedback-preview').hidden && !document.getElementById('feedback-send').disabled`);
    const send = async () => { try { await previewed(); } catch (e) { throw Error(e.message + ' ' + await ev(FORM).then(JSON.stringify)); } await ev(`document.getElementById('feedback-send').click()`);
      await until(`!document.getElementById('feedback-done').hidden || document.getElementById('feedback-status').className === 'feedback-error'`); };
    const DONE = `(() => { const $ = id => document.getElementById(id), d = $('feedback-dialog'), b = getComputedStyle($('feedback-done-close')), r = $('feedback-done-close').getBoundingClientRect();
      return {open: d.open, formHidden: $('feedback-form').hidden, heading: $('feedback-done-heading').textContent, labelledby: d.getAttribute('aria-labelledby'),
        what: $('feedback-done-what').textContent, link: ($('feedback-done-what').querySelector('a') || {}).href || '', hint: $('feedback-done-hint').hidden ? '' : $('feedback-done-hint').textContent,
        downloads: [...$('feedback-done-hint').querySelectorAll('a[download]')].map(a => a.download),
        kind: $('feedback-sent-kind').textContent, title: $('feedback-sent-title').textContent, body: $('feedback-sent-body').textContent,
        label: $('feedback-sent-label').textContent, primaries: [...d.querySelectorAll('.po-btn.primary')].filter(x => x.getClientRects().length).map(x => x.textContent),
        focused: document.activeElement === $('feedback-done-close'), closeBg: b.backgroundColor, closeFg: b.color,
        accent: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(), closeH: r.height, closeW: r.width,
        inside: r.right <= innerWidth && d.getBoundingClientRect().right <= innerWidth, scroll: d.scrollWidth <= d.clientWidth + 1}; })()`;
    const FORM = `(() => { const $ = id => document.getElementById(id);
      return {open: $('feedback-dialog').open, title: $('feedback-title').value, description: $('feedback-description').value, kind: $('feedback-kind').value,
        images: $('feedback-images').children.length, draft: !$('feedback-draft').hidden, done: !$('feedback-done').hidden, form: !$('feedback-form').hidden,
        preview: !$('feedback-preview').hidden, status: $('feedback-status').textContent, error: $('feedback-status').className === 'feedback-error',
        name: $('feedback-name').value, anonymous: $('feedback-anonymous').checked, send: !$('feedback-send').hidden && !$('feedback-send').disabled,
        focusTitle: document.activeElement === $('feedback-title')}; })()`;
    const reopen = () => ev(`document.getElementById('me-btn').click(); document.querySelector('[data-me="feedback"]').click();`);
    const relay = url => ev(`(async () => { document.getElementById('feedback-relay').value = ${JSON.stringify(url)}; await feedbackSaveSettings();
      return document.getElementById('feedback-settings-status').textContent; })()`);
    const out = {};
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const o = out[w] = {};
      await c.send('Emulation.setDeviceMetricsOverride', {width: w, height: h, deviceScaleFactor: 1, mobile: w < 500}, sessionId);
      await c.send('Emulation.setTouchEmulationEnabled', {enabled: w < 500}, sessionId);
      await c.send('Page.navigate', {url: A.base + '/'}, sessionId);
      await sleep(300); await until('typeof feedbackOpen === "function" && document.readyState === "complete"');
      // A click on a GitHub link never leaves the test machine.
      await ev(`document.addEventListener('click', e => { if (e.target.closest('a[href^="https://github.com"]')) e.preventDefault(); }, true)`);
      o.relaySaved = await relay('https://relay.test.invalid/hook');
      await ctl('login=&relay=ok');

      // Typed name through the relay: done, Escape closes, the next open is blank.
      await reopen(); await fill('Named relay ' + w, {name: 'Tester', kind: 'idea'}); await send();
      o.named = await ev(DONE);
      await key('Escape'); await sleep(200);
      o.namedClosed = await ev(FORM);
      await reopen(); o.namedReopen = await ev(FORM);

      // Anonymous relay: done, Close closes, blank again.
      await fill('Anonymous relay ' + w, {anonymous: true}); await send();
      o.anonymous = await ev(DONE);
      await ev(`document.getElementById('feedback-done-close').click()`);
      o.anonymousClosed = await ev(FORM);
      await reopen(); o.anonymousReopen = await ev(FORM);

      // GitHub login with a screenshot: the download links move into the done state.
      await ctl('login=octocat');
      await fill('GitHub login ' + w, {image: true}); await send();
      o.gh = await ev(DONE);
      await ev(`document.getElementById('feedback-done-close').click()`); await reopen();
      o.ghReopen = await ev(FORM);
      await ctl('login=');

      // Relay down: stays open, error, text kept, Send retries; the retry works.
      await ctl('relay=down');
      await fill('Relay down ' + w, {anonymous: true}); await send();
      o.failed = await ev(FORM);
      o.failedLink = await ev(`(document.querySelector('#feedback-status a') || {}).textContent`);
      await ctl('relay=ok');
      await ev(`document.getElementById('feedback-send').click()`);
      await until(`!document.getElementById('feedback-done').hidden`);
      o.retried = await ev(DONE);
      await ev(`document.getElementById('feedback-done-close').click()`);

      // Relay down, then the prefilled GitHub page: "Opened on GitHub".
      await ctl('relay=down'); await reopen();
      await fill('Opened fallback ' + w, {name: 'Tester', image: true}); await send();
      await ev(`document.querySelector('#feedback-status a').click()`);
      await until(`!document.getElementById('feedback-done').hidden`);
      o.opened = await ev(DONE);
      o.openedAgain = await ev(`document.querySelector('#feedback-done-hint a:not([download])').href`);
      await key('Escape'); await sleep(200); await reopen();
      o.openedReopen = await ev(FORM);
      await ctl('relay=ok');

      // No relay at all (prefilled-page route).
      o.noRelaySaved = await relay('');
      await fill('No relay ' + w, {anonymous: true}); await send();
      o.browser = await ev(FORM);
      await ev(`document.querySelector('#feedback-status a').click()`);
      await until(`!document.getElementById('feedback-done').hidden`);
      o.browserDone = await ev(DONE);
      await ev(`document.getElementById('feedback-done-close').click()`);
      await relay('https://relay.test.invalid/hook');

      // Closed without sending: kept as a labelled draft; Clear blanks it.
      await reopen(); o.fresh = await ev(FORM);
      await ev(`(() => { const $ = id => document.getElementById(id); $('feedback-kind').value = 'idea'; $('feedback-title').value = 'Half written';
        $('feedback-description').value = 'Not done yet'; $('feedback-title').dispatchEvent(new Event('input', {bubbles: true}));
        $('feedback-close').click(); })()`);
      await reopen(); o.draft = await ev(FORM);
      await ev(`document.getElementById('feedback-clear').click()`);
      o.cleared = await ev(FORM);
      await ev(`document.getElementById('feedback-close').click()`); await reopen();
      o.clearedReopen = await ev(FORM);
      await ev(`document.getElementById('feedback-close').click()`);
    }
    console.log(JSON.stringify(out));
  } finally { c.ws.close(); ch.kill(); }
}
main().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class FeedbackDonePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctl = ThreadingHTTPServer(("127.0.0.1", 0), Control)
        threading.Thread(target=cls.ctl.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.ctl.server_close)
        cls.addClassCleanup(cls.ctl.shutdown)
        script = SCRIPT.replace("A.ctl", json.dumps(f"http://127.0.0.1:{cls.ctl.server_address[1]}"))
        stubs = [patch.object(browser, 'CDP_JS', script), patch.object(feedback, 'output', fake_output),
                 patch.object(feedback, 'run', fake_run), patch.object(feedback, 'gh_login', lambda: MODE["login"]),
                 patch.object(feedback, 'DEFAULT_RELAY', ''), patch.object(feedback.urllib.request, 'build_opener', lambda *a: Opener())]
        with stubs[0], stubs[1], stubs[2], stubs[3], stubs[4], stubs[5]:
            browser.SettingsScrollsAndThemeSubmenu.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.SettingsScrollsAndThemeSubmenu.tearDownClass.__func__(cls)

    def each(self):
        return [(w, self.got[w]) for w in ('1440', '390')]

    def assertDone(self, d, w, heading='Feedback sent'):
        self.assertTrue(d['open']); self.assertTrue(d['formHidden'])
        self.assertEqual(d['heading'], heading); self.assertEqual(d['labelledby'], 'feedback-done-heading')
        self.assertEqual(d['primaries'], ['Close'])
        self.assertTrue(d['focused'])
        self.assertTrue(d['inside']); self.assertTrue(d['scroll'])
        if w == '390':
            self.assertGreaterEqual(d['closeH'], 44); self.assertGreaterEqual(d['closeW'], 44)

    def assertBlank(self, f):
        self.assertTrue(f['open']); self.assertTrue(f['form']); self.assertFalse(f['done'])
        self.assertEqual((f['title'], f['description'], f['kind'], f['images']), ('', '', 'bug', 0))
        self.assertFalse(f['draft']); self.assertFalse(f['preview']); self.assertEqual(f['status'], '')
        self.assertTrue(f['focusTitle'])

    def test_named_relay_success_escape_and_blank_reopen(self):
        for w, o in self.each():
            self.assertEqual(o['relaySaved'], 'Saved.')
            d = o['named']; self.assertDone(d, w)
            self.assertRegex(d['link'], r'/issues/\d+$')
            self.assertIn('#' + d['link'].rsplit('/', 1)[1], d['what'])
            self.assertIn('through the relay with your typed name', d['what'])
            self.assertEqual(d['kind'], 'Idea'); self.assertEqual(d['title'], 'Named relay ' + w)
            self.assertIn('Submitted by: Tester', d['body']); self.assertEqual(d['label'], 'What was sent')
            self.assertFalse(o['namedClosed']['open'])
            self.assertBlank(o['namedReopen'])
            self.assertEqual(o['namedReopen']['name'], 'Tester')   # a preference, kept

    def test_close_button_is_the_accent_primary(self):
        for w, o in self.each():
            self.assertTrue(o['named']['accent'])
            self.assertNotEqual(o['named']['closeBg'], 'rgba(0, 0, 0, 0)')
            self.assertEqual(o['named']['closeBg'], o['anonymous']['closeBg'])

    def test_anonymous_relay_and_close(self):
        for w, o in self.each():
            self.assertDone(o['anonymous'], w)
            self.assertIn('posted anonymously through the relay', o['anonymous']['what'])
            self.assertFalse(o['anonymousClosed']['open'])
            self.assertBlank(o['anonymousReopen'])
            self.assertTrue(o['anonymousReopen']['anonymous'])

    def test_github_login_keeps_screenshot_downloads(self):
        for w, o in self.each():
            self.assertDone(o['gh'], w)
            self.assertIn('posted as GitHub user octocat', o['gh']['what'])
            self.assertTrue(o['gh']['link'].endswith('/issues/77'))
            self.assertEqual(o['gh']['downloads'], ['screenshot.png'])
            self.assertIn('drag it onto the GitHub issue', o['gh']['hint'])
            self.assertBlank(o['ghReopen'])

    def test_failure_keeps_text_and_retry_succeeds(self):
        for w, o in self.each():
            f = o['failed']
            self.assertTrue(f['open']); self.assertFalse(f['done']); self.assertTrue(f['error'])
            self.assertTrue(f['status'].startswith('Could not confirm delivery. The relay '))
            self.assertIn('Check the repository before retrying', f['status'])
            self.assertIn('press Send to retry', f['status'])
            self.assertEqual(f['title'], 'Relay down ' + w); self.assertEqual(f['description'], 'What happened at ' + w)
            self.assertTrue(f['send'])
            self.assertEqual(o['failedLink'], 'open GitHub (not anonymous)')
            self.assertDone(o['retried'], w)

    def test_prefilled_page_fallback_is_opened_on_github(self):
        for w, o in self.each():
            d = o['opened']; self.assertDone(d, w, 'Opened on GitHub')
            self.assertIn('not anonymous', d['what']); self.assertEqual(d['label'], 'What was filled in')
            self.assertEqual(d['downloads'], ['screenshot.png'])   # still downloadable until Close
            self.assertIn('drag it onto the GitHub page', d['hint'])
            self.assertTrue(o['openedAgain'].startswith('https://github.com/fab-ioc/ensemble/issues/new?'))
            self.assertBlank(o['openedReopen'])
            self.assertEqual(o['noRelaySaved'], 'Saved.')
            b = o['browser']
            self.assertTrue(b['error']); self.assertTrue(b['status'].startswith('Not sent. No feedback relay is configured.'))
            self.assertNotIn('press Send to retry', b['status']); self.assertIn('to finish there', b['status'])
            self.assertEqual(b['title'], 'No relay ' + w)
            self.assertDone(o['browserDone'], w, 'Opened on GitHub')

    def test_unsent_draft_is_labelled_and_clear_blanks_it(self):
        for w, o in self.each():
            self.assertBlank(o['fresh'])
            d = o['draft']
            self.assertTrue(d['draft']); self.assertEqual((d['title'], d['description'], d['kind']), ('Half written', 'Not done yet', 'idea'))
            self.assertBlank(o['cleared']); self.assertBlank(o['clearedReopen'])

    def test_nothing_left_the_machine(self):
        self.assertTrue(POSTS)
        self.assertTrue(all(kind in ('relay', 'gh') for kind, _ in POSTS))


if __name__ == '__main__':
    unittest.main()
