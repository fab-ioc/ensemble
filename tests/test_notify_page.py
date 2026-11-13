"""Settings › Notifications (#359) in a real page: headless Chrome over CDP,
against a hub in a thread. The browser's push service is faked in the page
(a subscription with real keys, no network) and the hub's sender is faked, so
the test covers the page, the routes and notify.py together:

* off: the switch unchecked, Send a test disabled, other devices listed with
  Remove;
* turning it on asks for permission, subscribes with the hub's key, lists this
  device first-hand with its THIS DEVICE lozenge and no Remove, and starts
  the page's "in front" heartbeat;
* Send a test reaches the hub and says so; Remove drops another device;
  turning it off removes this one;
* blocked by the browser, an iPhone's Safari tab, plain http: no way to turn
  it on, a line that says why and what to do;
* /?inbox=<project> opens the Inbox and clears the address.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import notify  # noqa: E402
import webpush  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

EP_PAGE = "https://fcm.googleapis.com/fcm/send/this-browser"
EP_OTHER = "https://web.push.apple.com/the-iphone"


def keys():
    k = webpush.new_private_key()
    return {"p256dh": webpush.b64u(webpush.public_bytes(k)), "auth": webpush.b64u(os.urandom(16))}


# The browser's side, faked before the page's own scripts run: permission,
# a service worker registration and its push manager.
FAKE = r"""
(() => {
  const A = %s;
  window.__nt = { perm: 'default', sub: null, subscribed: [], seen: [] };
  const mkSub = (key) => ({ endpoint: A.endpoint, options: { applicationServerKey: key },
    toJSON() { return { endpoint: A.endpoint, keys: A.keys }; },
    async unsubscribe() { window.__nt.sub = null; return true; } });
  const pm = { async getSubscription() { return window.__nt.sub; },
    async subscribe(o) { window.__nt.subscribed.push(o.applicationServerKey.length); window.__nt.sub = mkSub(o.applicationServerKey.buffer); return window.__nt.sub; } };
  const reg = { pushManager: pm, scope: '/' };
  const sw = { register: async () => reg, getRegistration: async () => reg, ready: Promise.resolve(reg),
    addEventListener() {}, removeEventListener() {}, controller: null };
  Object.defineProperty(navigator, 'serviceWorker', { value: sw, configurable: true });
  if (!window.PushManager) window.PushManager = function () {};
  const N = window.Notification || function () {};
  Object.defineProperty(N, 'permission', { get: () => window.__nt.perm, configurable: true });
  N.requestPermission = async () => { if (window.__nt.perm === 'default') window.__nt.perm = 'granted'; return window.__nt.perm; };
  window.Notification = N;
  const f = window.fetch;
  window.fetch = function (u, o) {
    if (String(u).includes('/api/notify/seen')) { try { window.__nt.seen.push(JSON.parse(o.body)); } catch (e) {} }
    return f.apply(this, arguments);
  };
})();
"""

CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const errors = [];
  const onmsg = c.ws.onmessage;
  c.ws.onmessage = ev => {
    try { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; errors.push(((d.exception && d.exception.description) || d.text || '').slice(0, 300)); } } catch (e) {}
    onmsg(ev);
  };
  const page = async (w, h, url) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false }, sessionId);
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: A.fake }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + (url || '/') }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!PREFS', 30000);
    return { evalIn, until, shot };
  };
  const TAB = `(() => { const s = document.getElementById('settings-notify'), on = document.getElementById('nt-on'), t = document.getElementById('nt-test');
    const st = s.querySelector('.nt-st'), why = s.querySelector('.nt-why');
    return { shown: s.getClientRects().length > 0, on: on ? { checked: on.checked, disabled: on.disabled } : null,
      test: t ? { disabled: t.disabled } : null, status: st ? st.textContent : '', why: why ? why.textContent : '',
      what: !!s.querySelector('.nt-what'),
      devs: [...s.querySelectorAll('.nt-devs li')].map(li => ({ name: li.querySelector('b').firstChild.textContent,
        me: !!li.querySelector('.nt-me'), remove: !!li.querySelector('[data-nt-remove]'), line: li.querySelector('small').textContent })),
      overflow: document.documentElement.scrollWidth > innerWidth }; })()`;
  try {
    const p = await page(1440, 900);
    await p.evalIn("openSettings('notify'), 0");
    await p.until("!!document.getElementById('nt-on') && !document.getElementById('nt-on').disabled");
    out.off = await p.evalIn(TAB);
    await p.shot('notify-off');
    await p.evalIn("document.getElementById('nt-on').click(), 0");
    await p.until("document.getElementById('nt-on').checked && !NT.busy && /On/.test(document.querySelector('#settings-notify .nt-st').textContent)");
    out.on = await p.evalIn(TAB);
    out.subscribedKeyLen = await p.evalIn('window.__nt.subscribed');
    await p.until("window.__nt.seen.some(s => s.front && s.endpoint)", 5000).catch(() => null);
    out.seen = await p.evalIn('window.__nt.seen');
    await p.shot('notify-on');
    await p.evalIn("document.getElementById('nt-test').click(), 0");
    await p.until("!NT.busy && /Sent/.test(document.querySelector('#settings-notify .nt-st').textContent)");
    out.tested = await p.evalIn(TAB);
    await p.evalIn("document.querySelector('#settings-notify [data-nt-remove]').click(), 0");
    await p.until("!NT.busy && document.querySelectorAll('#settings-notify .nt-devs li').length === 1");
    out.removed = await p.evalIn(TAB);
    await p.evalIn("document.getElementById('nt-on').click(), 0");
    await p.until("!NT.busy && !document.getElementById('nt-on').checked && /Off/.test(document.querySelector('#settings-notify .nt-st').textContent)");
    out.offAgain = await p.evalIn(TAB);
    out.seenOff = await p.evalIn('window.__nt.seen');
    // Blocked by the browser.
    await p.evalIn("window.__nt.perm = 'denied', ntRefresh()");
    out.denied = await p.evalIn(TAB);
    // An iPhone's Safari tab.
    await p.evalIn("window.__nt.perm = 'default', window.isIosDevice = () => true, ntRefresh()");
    out.tab = await p.evalIn(TAB);
    // Plain http.
    await p.evalIn("window.isIosDevice = () => false, Object.defineProperty(window, 'isSecureContext', { get: () => false, configurable: true }), ntRefresh()");
    out.http = await p.evalIn(TAB);
    // A tap on "3 new in Motors": the Inbox, the address cleared.
    const q = await page(1440, 900, '/?inbox=proj-none');
    await sleep(800);
    out.deep = await q.evalIn(`({ search: location.search, scope: IBX.scope })`);
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class NotificationsTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-nt-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("root", "transcripts", "cs"):
            (base / d).mkdir()
        cls.sent = []

        def fake_send(vapid, dev, payload, topic="", timeout=0, **_kw):
            cls.sent.append((dev["endpoint"], json.loads(payload)))
            return 201

        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "root"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(notify, "_sender", fake_send),
            mock.patch.object(notify, "_VAPID", None),
            mock.patch.object(notify, "_log"),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        notify._FRONT.clear()
        cls.other, _ = notify.subscribe({"endpoint": EP_OTHER, "keys": keys()}, "iPhone · Home Screen app")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "shots": shots,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "fake": FAKE % json.dumps({"endpoint": EP_PAGE, "keys": keys()})}
        script = base / "notify_page_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_off(self):
        g = self.got["off"]
        self.assertTrue(g["shown"])
        self.assertEqual(g["on"], {"checked": False, "disabled": False})
        self.assertTrue(g["test"]["disabled"])
        self.assertTrue(g["what"])
        self.assertEqual(g["devs"], [{"name": "iPhone · Home Screen app", "me": False, "remove": True,
                                      "line": g["devs"][0]["line"]}])
        self.assertTrue(g["devs"][0]["line"].startswith("Added "), g["devs"][0]["line"])
        self.assertFalse(g["overflow"])

    def test_on_lists_this_device_and_starts_the_heartbeat(self):
        g = self.got["on"]
        self.assertEqual(g["on"], {"checked": True, "disabled": False})
        self.assertFalse(g["test"]["disabled"])
        self.assertEqual(self.got["subscribedKeyLen"], [65])        # the hub's P-256 key
        me = [d for d in g["devs"] if d["me"]]
        self.assertEqual(len(me), 1)
        self.assertFalse(me[0]["remove"])
        self.assertIn("Chrome", me[0]["name"])
        self.assertIn("(the hub machine)", me[0]["name"])
        self.assertIn({"endpoint": EP_PAGE, "front": True}, self.got["seen"])

    def test_test_remove_and_off(self):
        self.assertEqual(self.got["tested"]["status"], "Sent. It shows here within a few seconds.")
        self.assertIn(EP_PAGE, [s[0] for s in self.sent])
        self.assertEqual(self.sent[-1][0], EP_PAGE)
        self.assertEqual([d["me"] for d in self.got["removed"]["devs"]], [True])
        g = self.got["offAgain"]
        self.assertEqual(g["devs"], [])
        self.assertTrue(g["test"]["disabled"])
        self.assertIn({"endpoint": EP_PAGE, "front": False}, self.got["seenOff"])

    def test_states_that_cannot_turn_on(self):
        d = self.got["denied"]
        self.assertTrue(d["on"]["disabled"])
        self.assertIn("blocks notifications for this site", d["why"])
        t = self.got["tab"]
        self.assertIsNone(t["on"])
        self.assertIn("Add to Home Screen", t["why"])
        h = self.got["http"]
        self.assertTrue(h["on"]["disabled"])
        self.assertIn("https", h["why"])

    def test_a_burst_link_opens_the_inbox(self):
        self.assertEqual(self.got["deep"], {"search": "", "scope": "project"})

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
