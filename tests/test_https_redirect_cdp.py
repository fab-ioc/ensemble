"""In a real Chrome: a page opened on the tailnet's plain http goes to https (P386).

The hub listens on this machine's LAN address, which a browser treats like the
tailnet address: not loopback, so plain http there is not a secure context and
the browser sends no Sec-Fetch-* headers. For the test that address counts as
the tailnet (dashboard._TAILNET_NETS), and tailscale serve's https address is
a name that does not resolve (so nothing is fetched there). Checked, from the
network log and the pages:

* the dashboard (/) with ?token= and a chat page (/session, no embed) are a
  302 to the same page and query on https, the token taken along, and the
  browser goes there;
* without a token the 302 goes without one;
* a chat's frame address (embed=1) opened there is not redirected: it gets the
  sign-in page, which is not a secure context (the reason the mic hid);
* a fetch from that page (an API call) is not redirected.

Skipped without Node and Chrome, or without a non-loopback IPv4 address.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import hub_links  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402

NODE = shutil.which('node')
NAME = 'ensemble-hub.invalid'
TOKEN = 'tok-cdp-0123456789abcdef'
ROOM = 'room-1edda3f5'


def _lan_ip() -> str:
    """This machine's address on its default route (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 9))
            ip = s.getsockname()[0]
    except OSError:
        return ''
    return '' if ipaddress.ip_address(ip).is_loopback else ip


LAN = _lan_ip()

CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A),
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
  const ws = await new Promise((res, rej) => { let buf = ''; ch.stderr.on('data', d => {
    buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no CDP ' + buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq = 0;
  const redirects = [], docs = [];
  await new Promise((res, rej) => { c.onopen = res; c.onerror = rej; });
  c.onmessage = ev => { const m = JSON.parse(ev.data);
    if (m.method === 'Network.requestWillBeSent') {
      const p = m.params;
      if (p.redirectResponse) redirects.push({from: p.redirectResponse.url, status: p.redirectResponse.status,
        location: p.redirectResponse.headers.Location || p.redirectResponse.headers.location || '', to: p.request.url});
      if (p.type === 'Document') docs.push(p.request.url);
    }
    const w = waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w[1](new Error(m.error.message)) : w[0](m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const id = ++seq; waits.set(id, [res, rej]); c.send(JSON.stringify({id, method, params, sessionId})); });
  const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
  const {sessionId: s} = await send('Target.attachToTarget', {targetId, flatten: true});
  await send('Page.enable', {}, s);
  await send('Network.enable', {}, s);
  const ev = async expr => (await send('Runtime.evaluate', {expression: expr, awaitPromise: true, returnByValue: true}, s)).result.value;
  const visit = async (url, settle = 1500) => {
    const r0 = redirects.length, d0 = docs.length;
    await send('Page.navigate', {url}, s).catch(() => {});
    await sleep(settle);
    return {redirects: redirects.slice(r0), docs: docs.slice(d0)};
  };
  const out = {};
  out.dashboard = await visit(A.base + '/?token=' + A.token);
  out.chat = await visit(A.base + '/session?id=' + A.room + '&msg=m-1&token=' + A.token);
  out.anon = await visit(A.base + '/');
  out.frame = await visit(A.base + '/session?id=' + A.room + '&embed=1');
  out.framePage = await ev(`({secure: window.isSecureContext, href: location.href, title: document.title})`);
  out.fetch = await ev(`fetch('/api/ptys', {redirect: 'manual'}).then(r => ({status: r.status, type: r.type}))`);
  console.log(JSON.stringify(out));
  await send('Browser.close').catch(() => {});
  ch.kill();
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME and LAN, 'needs Node, Chrome and a non-loopback IPv4 address')
class HttpsRedirectInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-https-redirect-', ignore_cleanup_errors=True)
        cls.patches = [
            mock.patch.object(dashboard, 'ACCESS_TOKEN', TOKEN),
            mock.patch.object(dashboard, '_TAILNET_NETS', (ipaddress.ip_network(LAN + '/32'),)),
            mock.patch.object(hub_links, '_HTTPS', [(NAME, 443)]),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        cls.server = ThreadingHTTPServer((LAN, 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name, 'token': TOKEN, 'room': ROOM,
                'base': f'http://{LAN}:{cls.server.server_address[1]}'}
        script = base / 'https_redirect_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=120)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def _one_redirect(self, visit):
        hops = [r for r in visit['redirects'] if r['from'].startswith('http://')]
        self.assertEqual(len(hops), 1, visit)
        hop = hops[0]
        self.assertEqual(hop['status'], 302, hop)
        u = urlsplit(hop['location'])
        self.assertEqual((u.scheme, u.netloc), ('https', NAME), 'the https address')
        self.assertTrue(any(d.startswith(f'https://{NAME}/') for d in visit['docs']), 'the browser went there')
        return u.path, parse_qs(u.query)

    def test_the_dashboard_goes_to_https_with_its_token(self):
        path, q = self._one_redirect(self.got['dashboard'])
        self.assertEqual(path, '/')
        self.assertTrue(q.get('token') == [TOKEN], 'the token is taken along')

    def test_a_chat_page_goes_to_the_same_page(self):
        path, q = self._one_redirect(self.got['chat'])
        self.assertEqual(path, '/session')
        self.assertEqual((q.get('id'), q.get('msg')), ([ROOM], ['m-1']))

    def test_without_a_token_it_goes_without(self):
        path, q = self._one_redirect(self.got['anon'])
        self.assertEqual(path, '/')
        self.assertNotIn('token', q)

    def test_a_frame_address_and_an_api_call_stay(self):
        self.assertEqual(self.got['frame']['redirects'], [], 'embed=1 is not redirected')
        page = self.got['framePage']
        self.assertTrue(page['href'].startswith(f'http://{LAN}:'), page)
        self.assertFalse(page['secure'], 'plain http to another address is not a secure context')
        self.assertEqual(self.got['fetch']['status'], 401, 'an API call gets its answer, not a redirect')


if __name__ == '__main__':
    unittest.main()
