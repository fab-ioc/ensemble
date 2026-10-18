"""Ensemble as an app: the web manifest, its icons, the Install app button in
Settings, and the token gate in front of it (dashboard.py, index.html).

Checked against the hub's handler:

* /manifest.webmanifest is served as application/manifest+json, names
  Ensemble, starts at "/" with scope "/" (the Dock's pop-out pages are inside
  it), standalone, colours from the Light tokens, 192/512 and maskable icons
  that exist at their sizes; neither holds the token;
* the page links it (and the iPhone's touch icon);
* with a token set, the manifest and the icons are served without it (the
  browser fetches a manifest without cookies), and nothing else is;
* a request through a proxy on this machine (tailscale serve: loopback, but a
  forwarding header or a Host that is not loopback) is gated like a remote
  one; a navigation without the token gets the sign-in page; the https origin
  of the proxy passes the same-origin check;
* the token cookie is sent again, a year ahead, when a remote browser opens
  the page with it;
* the iPhone's home screen (#243): every page (the sign-in page, the
  start-up page, the board, a chat, a file) names the apple-touch icons
  (180, 167, 152, 120) and the web-app metas; the two root paths iOS asks for
  answer the 180 icon without the token, also on a hub bound to an address
  that is not loopback, and nothing else is opened.

Checked in headless Chrome over CDP, against a hub in a thread:

* on 127.0.0.1 the page is installable: Page.getAppManifest reads the
  manifest without errors and Page.getInstallabilityErrors is empty;
* in a browser tab the page is not an app, and Settings says why there is no
  button; started with --app=, it is one, and a Dock panel popped out opens in
  a window that is an app window too (display-mode standalone);
* as an iPhone from the tailnet: the sign-in page's head carries the icons,
  which load without cookies; signing in once lands on "/" with no token in
  the address, a reload stays signed in, and Settings says how to add it to
  the home screen.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests.test_documents_project import INDEX, js_function  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
TOKEN = "t0ken-for-the-test"
TOUCH_ICONS = {"apple-touch-icon.png": 180, "apple-touch-icon-167.png": 167,
               "apple-touch-icon-152.png": 152, "apple-touch-icon-120.png": 120}
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1")
HOME_SCREEN_HEAD = (
    '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">',
    '<link rel="apple-touch-icon" sizes="120x120" href="/static/icons/apple-touch-icon-120.png">',
    '<link rel="apple-touch-icon" sizes="152x152" href="/static/icons/apple-touch-icon-152.png">',
    '<link rel="apple-touch-icon" sizes="167x167" href="/static/icons/apple-touch-icon-167.png">',
    '<meta name="apple-mobile-web-app-capable" content="yes">',
    '<meta name="mobile-web-app-capable" content="yes">',
    '<meta name="apple-mobile-web-app-title" content="Ensemble">',
    '<meta name="apple-mobile-web-app-status-bar-style" content="default">',
)


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


class Hub:
    """The hub's handler on a spare port, in a thread."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda *a: None
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def get(self, path, headers=None, method="GET"):
        """(status, headers, body), redirects not followed."""
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {}, method=method)
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TheManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patches = [mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None)]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        cls.hub = Hub()

    @classmethod
    def tearDownClass(cls):
        cls.hub.close()

    def test_it_is_served_as_a_manifest_and_names_the_app(self):
        code, h, body = self.hub.get("/manifest.webmanifest")
        self.assertEqual(code, 200)
        self.assertTrue(h["Content-Type"].startswith("application/manifest+json"), h["Content-Type"])
        m = json.loads(body)
        self.assertEqual((m["name"], m["short_name"]), ("Ensemble", "Ensemble"))
        self.assertEqual((m["start_url"], m["scope"], m["id"], m["display"]), ("/", "/", "/", "standalone"),
                         "scope / holds the page and the Dock's pop-out pages")
        self.assertEqual(m["background_color"], "#F7F8F9", "--bg in Light")
        self.assertEqual(m["theme_color"], "#FFFFFF", "--surface in Light")
        self.assertNotIn("token", body.decode("utf-8").lower())

    def test_the_pop_out_page_is_served_inside_its_scope(self):
        # #101: installed as an app, Chrome opens a window on a page inside the
        # scope as an app window; a blob: page got its address strip.
        scope = json.loads(self.hub.get("/manifest.webmanifest")[2])["scope"]
        self.assertIn("popUrl: '/static/dock/src/popout.html',", INDEX)
        self.assertTrue("/static/dock/src/popout.html".startswith(scope))
        code, h, body = self.hub.get("/static/dock/src/popout.html")
        self.assertEqual((code, h["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(b'id="dk-pop-root"', body)

    def test_its_icons_exist_at_their_sizes(self):
        m = json.loads(self.hub.get("/manifest.webmanifest")[2])
        purposes = {}
        for icon in m["icons"]:
            code, h, _ = self.hub.get(icon["src"])
            self.assertEqual((code, h["Content-Type"]), (200, "image/png"), icon["src"])
            f = ROOT / icon["src"].lstrip("/")
            w, hh = png_size(f)
            self.assertEqual(f"{w}x{hh}", icon["sizes"], icon["src"])
            purposes.setdefault(icon["purpose"], set()).add(icon["sizes"])
        self.assertEqual(purposes, {"any": {"192x192", "512x512"}, "maskable": {"512x512"}})
        self.assertEqual(png_size(ROOT / "static" / "icons" / "apple-touch-icon.png"), (180, 180))

    def test_the_page_links_it(self):
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', INDEX)
        self.assertIn('<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">', INDEX)
        self.assertIn('<meta name="theme-color"', INDEX)
        self.assertIn("import('/static/dock/src/install.js')", INDEX, "the button is the Dock library's")

    def test_every_page_links_the_favicon_and_it_is_served(self):
        for page in ("index.html", "session.html", "fileview.html"):
            text = (ROOT / page).read_text(encoding="utf-8")
            self.assertIn('<link rel="icon" type="image/svg+xml" href="/static/icons/favicon.svg">', text, page)
            self.assertIn('<link rel="icon" href="/static/icons/favicon.ico"', text, page)
        for path in ("/static/icons/favicon.svg", "/static/icons/favicon.ico"):
            code, _, body = self.hub.get(path)
            self.assertEqual(code, 200, path)
        ico = (ROOT / "static" / "icons" / "favicon.ico").read_bytes()
        count = struct.unpack("<HHH", ico[:6])[2]
        sizes = {(ico[6 + 16 * i] or 256, ico[7 + 16 * i] or 256) for i in range(count)}
        self.assertTrue({(16, 16), (32, 32)} <= sizes, sizes)
        self.assertIn('id="install-app-btn"', INDEX)

    def test_every_page_carries_the_home_screen_icon_and_metas(self):
        # The sign-in page is what a new phone, and a new home-screen app (its
        # own cookie jar), sees first: without these iOS shows a screenshot.
        heads = {page: (ROOT / page).read_text(encoding="utf-8").split("</head>")[0]
                 for page in ("index.html", "session.html", "fileview.html", "static/startup.html")}
        heads["sign-in"] = dashboard.SIGN_IN_PAGE.decode("utf-8").split("</head>")[0]
        for page, head in heads.items():
            for line in HOME_SCREEN_HEAD:
                self.assertIn(line, head, page)
            self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', head, page + ": the app starts at /")
            self.assertNotIn("token=", head, page)

    def test_the_touch_icons_exist_at_their_sizes(self):
        for name, px in TOUCH_ICONS.items():
            self.assertEqual(png_size(ROOT / "static" / "icons" / name), (px, px), name)
            code, h, _ = self.hub.get("/static/icons/" + name)
            self.assertEqual((code, h["Content-Type"]), (200, "image/png"), name)
        icon = (ROOT / "static" / "icons" / "apple-touch-icon.png").read_bytes()
        for path in dashboard.APPLE_TOUCH_PATHS:
            code, h, body = self.hub.get(path)
            self.assertEqual((code, h["Content-Type"], body), (200, "image/png", icon), path)


class TheGate(unittest.TestCase):
    """The token gate with a token set, as on the tailnet."""

    @classmethod
    def setUpClass(cls):
        cls.patches = [mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN),
                       mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None)]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        cls.hub = Hub()

    @classmethod
    def tearDownClass(cls):
        cls.hub.close()

    PROXIED = {"Host": "hub.tailnet.example", "X-Forwarded-For": "100.64.0.7",
               "X-Forwarded-Proto": "https", "X-Forwarded-Host": "hub.tailnet.example"}
    NAV = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Accept": "text/html"}

    def test_a_local_request_needs_no_token(self):
        self.assertEqual(self.hub.get("/api/settings")[0], 200)

    def test_a_request_through_a_proxy_here_needs_the_token(self):
        for extra in ({"Host": "hub.tailnet.example"}, {"X-Forwarded-For": "100.64.0.7"},
                      {"Tailscale-User-Login": "someone@example.com"}, {"Forwarded": "for=100.64.0.7"}, self.PROXIED):
            self.assertEqual(self.hub.get("/api/settings", extra)[0], 401, extra)
        self.assertEqual(self.hub.get("/api/settings", {**self.PROXIED, "Cookie": f"ensemble_token={TOKEN}"})[0], 200)
        self.assertEqual(self.hub.get("/api/agent/hooks", self.PROXIED)[0], 401)
        self.assertEqual(self.hub.get("/api/agent/hooks", {**self.PROXIED, "Cookie": f"ensemble_token={TOKEN}"})[0], 403,
                         "loopback-only means this machine, not a proxy on it")

    def test_the_manifest_and_icons_need_no_token_and_nothing_else_is_opened(self):
        for path in ("/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/icon-maskable-512.png"):
            self.assertEqual(self.hub.get(path, self.PROXIED)[0], 200, path)
        for path in ("/", "/static/hl.js", "/static/dock/src/install.js", "/static/icons/../hl.js", "/api/live"):
            self.assertEqual(self.hub.get(path, self.PROXIED)[0], 401, path)

    def test_the_root_touch_icons_need_no_token_and_nothing_else_is_opened(self):
        self.assertEqual(set(dashboard.UNGATED_PATHS),
                         {"/manifest.webmanifest", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"})
        self.assertEqual(dashboard.UNGATED_PREFIXES, ("/static/icons/",))
        for path in dashboard.APPLE_TOUCH_PATHS + ("/static/icons/apple-touch-icon-167.png",):
            code, h, _ = self.hub.get(path, self.PROXIED)
            self.assertEqual((code, h["Content-Type"]), (200, "image/png"), path)
        for path in ("/apple-touch-icon.png/../index.html", "/apple-touch-icon.png.bak", "/apple-touch-icon",
                     "/apple-touch-icon-120x120.png", "/Apple-Touch-Icon.png", "/apple-touch-icon.png%2F..%2Fapi",
                     "/favicon.ico", "/index.html", "/session", "/fileview"):
            self.assertEqual(self.hub.get(path, self.PROXIED)[0], 401, path)
        code, _, _ = self.hub.get("/apple-touch-icon.png?x=1", self.PROXIED)
        self.assertEqual(code, 200, "a query does not change the path")
        # A ";params" alias, which urlparse would cut off: GET and HEAD.
        for path in ("/apple-touch-icon.png;x", "/apple-touch-icon-precomposed.png;", "/manifest.webmanifest;x",
                     "/static/icons/apple-touch-icon.png;x"):
            for method in ("GET", "HEAD"):
                self.assertEqual(self.hub.get(path, self.PROXIED, method=method)[0], 401, (method, path))
        self.assertEqual(self.hub.get("/apple-touch-icon.png", self.PROXIED, method="HEAD")[0], 200)
        # Request targets sent as they are, which urlparse would read as an
        # open path: absolute form, a fragment, an inner double slash, a
        # backslash. (A leading "//" is made "/" by http.server itself, the
        # same path.)
        import http.client
        for target in ("http://elsewhere.example/apple-touch-icon.png", "/apple-touch-icon.png#x",
                       "/static/icons\\apple-touch-icon.png",
                       "http://elsewhere.example/manifest.webmanifest", "/static//icons/icon-192.png"):
            for method in ("GET", "HEAD"):
                c = http.client.HTTPConnection("127.0.0.1", self.hub.port, timeout=10)
                try:
                    c.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
                    for k, v in self.PROXIED.items():
                        c.putheader(k, v)
                    c.endheaders()
                    self.assertEqual(c.getresponse().status, 401, (method, target))
                finally:
                    c.close()

    def test_a_starting_hub_answers_the_root_touch_icons(self):
        for path in dashboard.APPLE_TOUCH_PATHS:
            self.assertIn(path, dashboard.STARTUP_OPEN_PATHS)

    def test_a_page_without_the_token_gets_the_sign_in_page(self):
        code, h, body = self.hub.get("/", {**self.PROXIED, **self.NAV})
        self.assertEqual(code, 401)
        self.assertTrue(h["Content-Type"].startswith("text/html"))
        self.assertIn(b'<form method="get">', body)
        self.assertIn(b'name="token"', body)
        self.assertNotIn(TOKEN.encode(), body)
        code, h, body = self.hub.get("/api/live", self.PROXIED)
        self.assertEqual((code, h["Content-Type"].split(";")[0]), (401, "text/plain"), "a script gets the plain answer")

    def test_the_token_in_the_address_becomes_a_cookie_and_is_renewed(self):
        code, h, _ = self.hub.get(f"/?token={TOKEN}", {**self.PROXIED, **self.NAV})
        self.assertEqual((code, h["Location"]), (303, "/"))
        self.assertIn("Max-Age=31536000", h["Set-Cookie"])
        code, h, _ = self.hub.get("/", {**self.PROXIED, **self.NAV, "Cookie": f"ensemble_token={TOKEN}"})
        self.assertEqual(code, 200)
        cookies = h.get_all("Set-Cookie") or []
        self.assertTrue(any(c.startswith(f"ensemble_token={TOKEN};") and "Max-Age=31536000" in c for c in cookies), cookies)
        code, h, _ = self.hub.get("/", self.NAV)
        self.assertFalse(any(c.startswith("ensemble_token=") for c in (h.get_all("Set-Cookie") or [])),
                         "a local page is not handed the token")

    def test_the_https_origin_of_the_proxy_is_same_origin(self):
        ok = lambda headers: type("H", (), {"headers": headers, "client_address": ("127.0.0.1", 5)})
        same = dashboard.Handler._same_origin_request
        h = ok({"Origin": "https://hub.tailnet.example", "Host": "hub.tailnet.example"})
        h._client_ip = lambda: "127.0.0.1"
        self.assertTrue(same(h), "tailscale serve keeps the Host")
        h = ok({"Origin": "https://hub.tailnet.example", "Host": "127.0.0.1:8765",
                "X-Forwarded-Host": "hub.tailnet.example"})
        h._client_ip = lambda: "127.0.0.1"
        self.assertTrue(same(h), "a proxy that rewrites Host names the page's host")
        h = ok({"Origin": "https://evil.example", "Host": "127.0.0.1:8765", "X-Forwarded-Host": "hub.tailnet.example"})
        h._client_ip = lambda: "127.0.0.1"
        self.assertFalse(same(h))
        h = ok({"Origin": "https://evil.example", "Host": "100.64.0.1:8765", "X-Forwarded-Host": "evil.example"})
        h._client_ip = lambda: "100.64.0.9"
        self.assertFalse(same(h), "only a proxy on this machine names the host")


def _a_lan_address() -> str | None:
    """An address of this machine that is not loopback, or None."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # nothing is sent: picks the outgoing interface
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


class OnANonLoopbackBind(unittest.TestCase):
    """A hub bound to this machine's LAN address, as with --bind tailscale: a
    request to it comes from that address, not loopback, so the gate holds."""

    @classmethod
    def setUpClass(cls):
        cls.ip = _a_lan_address()
        if not cls.ip:
            raise unittest.SkipTest("no address but loopback")
        cls.patches = [mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN),
                       mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None)]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        try:
            cls.server = ThreadingHTTPServer((cls.ip, 0), dashboard.Handler)
        except OSError as e:
            raise unittest.SkipTest(f"cannot bind {cls.ip}: {e}")
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{cls.ip}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.base + path, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_the_icons_and_the_manifest_answer_without_the_cookie(self):
        for path in dashboard.APPLE_TOUCH_PATHS:
            code, h, body = self.get(path)
            self.assertEqual((code, h["Content-Type"]), (200, "image/png"), path)
            self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n", path)
        code, _, body = self.get("/manifest.webmanifest")
        self.assertEqual(code, 200)
        for icon in json.loads(body)["icons"]:
            code, h, _ = self.get(icon["src"])
            self.assertEqual((code, h["Content-Type"]), (200, "image/png"), icon["src"])
        self.assertEqual(self.get("/api/settings")[0], 401, "the gate holds on this bind")
        self.assertEqual(self.get("/")[0], 401)


class TheHint(unittest.TestCase):
    """What Settings says where there is no Install app button."""

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("node is not installed")
        src = js_function("appInstallHint") + """
const cases = {
  app: { app: true }, offer: { offer: true, secure: true, prompts: true },
  http: { secure: false, prompts: true, host: 'hub.tailnet.example:8765' },
  safari: { secure: true, prompts: false }, chrome: { secure: true, prompts: true },
  iphone: { ios: true, secure: false, prompts: false, host: '100.64.0.1:8765' }, iphoneApp: { app: true, ios: true } };
const out = {}; for (const k in cases) out[k] = appInstallHint(cases[k]);
console.log(JSON.stringify(out));"""
        r = subprocess.run([NODE, "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert r.returncode == 0, r.stderr
        cls.o = json.loads(r.stdout)

    def test_each_state_says_what_holds(self):
        self.assertIn("running as an app", self.o["app"])
        self.assertIn("hub.tailnet.example:8765 is plain http", self.o["http"])
        self.assertIn("Create shortcut", self.o["http"])
        self.assertIn("Add to Dock", self.o["safari"])
        self.assertIn("Add to Home Screen", self.o["safari"])
        self.assertIn("Install page as app", self.o["chrome"])
        self.assertIn("Safari › Share › Add to Home Screen, with Open as Web App on", self.o["iphone"])
        self.assertNotIn("Chrome", self.o["iphone"], "plain http is no obstacle on an iPhone")
        self.assertIn("running as an app", self.o["iphoneApp"])


# ---- in headless Chrome -----------------------------------------------------------

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
// A panel's ⋯ in its title bar, then View Mode › <mode> (Dock v0.5.0), with real clicks (a window opens only from one).
const viewMode = async (p, panel, mode) => {
  await p.click(`${panel}.closest(".dk-stack").querySelector(':scope > .dk-head [data-dk-act="menu"]')`);
  await p.click('document.querySelector(".dk-menu.dk-options [data-dk-sub=mode]")');
  await p.click(`document.querySelector('.dk-menu.dk-submenu [data-dk-menu="mode:${mode}"]')`);
};
async function launch(extra) {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--disable-popup-blocking', '--window-size=1440,900', ...extra],
    { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function attach(c, targetId) {
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const click = async (expr) => {
    const [x, y] = await evalIn(`(() => { const r = (${expr}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
  };
  return { sessionId, evalIn, until, click };
}
const APP = '["standalone","window-controls-overlay","minimal-ui","fullscreen","browser"].find(m => matchMedia("(display-mode: " + m + ")").matches)';
const pageReady = 'typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0';
const poReady = 'document.body.classList.contains("po-dock") && !!PD.dock';
const goPo = p => p.evalIn(`(() => { try { localStorage.removeItem('cd-po-dock'); } catch (e) {}
  pdNarrow = () => isPhone();   // the wide dock, kept for step 3's tool strip (a desktop's tabs: tests/test_middle.py)
  SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
async function popBoard(c, p) {
  // The Board beside Points, then real clicks on its ⋯ › View Mode › Window (a window opens only from one).
  await p.evalIn('PD.dock.pin("board"); 0'); await sleep(500);
  const before = new Set((await c.send('Target.getTargets')).targetInfos.map(t => t.targetId));
  // A headless window's first click can go to focusing it: once more if nothing opened.
  let clicks = 0;
  for (; clicks < 3 && !(await p.evalIn('!!PD.dock.popWindow("board")')); clicks++) {
    // A menu left open by a lost click is closed first.
    await p.evalIn('(() => { const m = document.querySelector(".dk-menu.dk-options"); if (m) m.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); return 0; })()');
    try { await viewMode(p, 'PD.els.board', 'window'); } catch (e) { /* again */ }
    try { await p.until('!!PD.dock.popWindow("board")', 3000); } catch (e) { /* again */ }
  }
  if (!(await p.evalIn('!!PD.dock.popWindow("board")'))) return { opened: false, clicks };
  let t = null;
  for (let i = 0; i < 60 && !t; i++) { t = (await c.send('Target.getTargets')).targetInfos.find(x => x.type === 'page' && !before.has(x.targetId)); if (!t) await sleep(150); }
  if (!t) return { opened: false };
  const w = await attach(c, t.targetId);
  await w.until('!!document.querySelector("#dk-pop-root .pd-board")', 15000);
  return { opened: true, url: t.url.slice(0, 5), mode: await w.evalIn(APP), board: await w.evalIn('document.querySelector("#dk-pop-root .pd-board").innerText.length') };
}
async function main() {
  const out = {};
  // ---- In a browser tab: installable, not an app, Settings says why there is no button.
  {
    const { ch, ws } = await launch(['about:blank']);
    const c = new Cdp(ws); await c.open();
    try {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const p = await attach(c, targetId);
      await c.send('Page.navigate', { url: A.base + '/' }, p.sessionId);
      await p.until(pageReady, 30000);
      const man = await c.send('Page.getAppManifest', {}, p.sessionId);
      out.manifest = { url: man.url, errors: man.errors, parsed: man.parsed ? { name: man.parsed.name, scope: man.parsed.scope, display: man.parsed.display } : null };
      let errs = null;
      for (let i = 0; i < 20; i++) { errs = (await c.send('Page.getInstallabilityErrors', {}, p.sessionId)).installabilityErrors; if (!errs.length) break; await sleep(300); }
      out.installErrors = errs;
      // Chrome's offer can come late on a loaded machine (seen in a full run).
      try { await p.until('!document.getElementById("install-app-btn").hidden', 10000); } catch (e) { /* said below */ }
      out.tab = await p.evalIn(`({ mode: ${APP}, btnHidden: document.getElementById('install-app-btn').hidden,
        hint: document.getElementById('install-app-hint').textContent, secure: isSecureContext })`);
      // Settings opened: the button is a Default button, the line under it --fg-muted.
      await p.click('document.getElementById("me-btn")'); await sleep(200);
      await p.click('[...document.querySelectorAll("#me-menu .me-item")].find(b => /Settings/.test(b.textContent))'); await sleep(200);
      out.look = await p.evalIn(`(() => { const b = document.getElementById('install-app-btn'), h = document.getElementById('install-app-hint');
        const cs = getComputedStyle(b), r = b.getBoundingClientRect(), root = getComputedStyle(document.documentElement), tok = n => root.getPropertyValue(n).trim();
        const rgb = c => { const d = document.createElement('i'); d.style.color = c; document.body.appendChild(d); const v = getComputedStyle(d).color; d.remove(); return v; };
        return { h: Math.round(r.height), bg: cs.backgroundColor, fg: cs.color, surface: rgb(tok('--surface')), text: rgb(tok('--fg')),
                 hint: getComputedStyle(h).color, muted: rgb(tok('--fg-muted')), first: document.querySelector('#settings-panel .settings-section').id,
                 theme: document.querySelector('meta[name="theme-color"]').content, surfaceTok: tok('--surface') }; })()`);
      if (A.shots) { const r = await c.send('Page.captureScreenshot', { format: 'png' }, p.sessionId); fs.writeFileSync(path.join(A.shots, 'install-settings.png'), Buffer.from(r.data, 'base64')); }
      await p.evalIn('document.getElementById("settings-panel").hidden = true; 0');
      await goPo(p); await p.until(poReady, 30000); await sleep(500);
      out.tabPop = await popBoard(c, p);
    } finally { ch.kill(); }
  }
  // ---- Started with --app=: an app window, and so is a Dock pop-out.
  {
    const { ch, ws } = await launch(['--app=' + A.base + '/']);
    const c = new Cdp(ws); await c.open();
    try {
      let t = null;
      for (let i = 0; i < 60 && !t; i++) { t = (await c.send('Target.getTargets')).targetInfos.find(x => x.type === 'page' && x.url.startsWith(A.base)); if (!t) await sleep(200); }
      const p = await attach(c, t.targetId);
      await p.until(pageReady, 30000);
      out.app = await p.evalIn(`({ mode: ${APP}, btnHidden: document.getElementById('install-app-btn').hidden,
        hint: document.getElementById('install-app-hint').textContent })`);
      await goPo(p); await p.until(poReady, 30000); await sleep(500);
      out.appPop = await popBoard(c, p);
    } finally { ch.kill(); }
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def state_patches(base: Path, root: Path, state: Path) -> list:
    """A hub with throwaway state under ``base``."""
    return [
        mock.patch.object(dashboard, "PROJECTS_ROOT", root),
        mock.patch.object(dashboard, "DASHBOARD_DIR", state),
        mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
        mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
        mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
        mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
        mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
        mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
        mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
        mock.patch.object(dashboard, "load_live", lambda: []),
        mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
        mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
        mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
    ]


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-app-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = state_patches(base, root, state) + [
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        members = [{"identity": "claude", "agent": "claude", "cwd": str(proj["path"])},
                   {"identity": "codex", "agent": "codex", "cwd": str(proj["path"])}]
        task = chatroom.create_room("Wider board", members)
        dashboard.assign_session_project(task["id"], proj["id"])
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        cls.hub = Hub()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.hub.port}", "proj": proj["id"],
                "shots": os.environ.get("ENSEMBLE_SHOTS", "")}
        script = base / "app_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.hub.close()
        cls.tmp.cleanup()

    def test_the_page_is_installable_on_localhost(self):
        m = self.got["manifest"]
        self.assertTrue(m["url"].endswith("/manifest.webmanifest"), m)
        self.assertEqual(m["errors"], [])
        self.assertEqual(self.got["installErrors"], [], "Chrome would offer to install it")

    def test_in_a_tab_it_is_no_app_and_its_pop_out_has_an_address_bar(self):
        t = self.got["tab"]
        self.assertEqual(t["mode"], "browser")
        self.assertFalse(t["btnHidden"], "Chrome offers to install from 127.0.0.1, so the button shows")
        self.assertIn("A window of its own", t["hint"])
        self.assertEqual(self.got["tabPop"]["mode"], "browser")

    def test_the_settings_section_follows_the_design(self):
        k = self.got["look"]
        self.assertEqual(k["first"], "settings-app")
        self.assertEqual(k["h"], 24, "a compact Default button, as Panels ▾")
        self.assertEqual((k["bg"], k["fg"]), (k["surface"], k["text"]), "a Default button: --surface, --fg")
        self.assertEqual(k["hint"], k["muted"])
        self.assertEqual(k["theme"], k["surfaceTok"], "the app's title bar takes the theme's surface")

    def test_started_with_app_it_and_its_dock_pop_out_are_app_windows(self):
        a = self.got["app"]
        self.assertEqual(a["mode"], "standalone")
        self.assertTrue(a["btnHidden"], "no Install app button in the app")
        self.assertIn("running as an app", a["hint"])
        pop = self.got["appPop"]
        self.assertTrue(pop["opened"], pop)
        self.assertEqual(pop["mode"], "standalone", pop)
        self.assertGreater(pop["board"], 0, "the Board came along")


IPHONE_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); const w = m.id && waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const i = ++id; waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  const out = {};
  try {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await send('Target.attachToTarget', { targetId, flatten: true });
    const ev = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(expr.slice(0, 100) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    await send('Page.enable', {}, s); await send('Network.enable', {}, s);
    // An iPhone 17 Pro Max from the tailnet: its user agent, its screen, touch,
    // and a request the hub sees as not from this machine.
    await send('Network.setUserAgentOverride', { userAgent: A.ua, platform: 'iPhone' }, s);
    await send('Emulation.setDeviceMetricsOverride', { width: 440, height: 956, deviceScaleFactor: 3, mobile: true }, s);
    await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
    await send('Network.setExtraHTTPHeaders', { headers: { 'X-Forwarded-For': '100.64.0.7' } }, s);
    await send('Page.navigate', { url: A.base + '/' }, s);
    await until('!!document.querySelector("input[name=token]")');
    out.signIn = await ev(`(async () => {
      const head = document.head;
      const touch = [...head.querySelectorAll('link[rel="apple-touch-icon"]')].map(l => ({ href: l.getAttribute('href'), sizes: l.getAttribute('sizes') || '' }));
      const meta = n => (head.querySelector('meta[name="' + n + '"]') || {}).content || '';
      const loads = [];
      for (const t of touch) { const r = await fetch(t.href, { credentials: 'omit' }); loads.push([t.href, r.status, r.headers.get('content-type')]); }
      for (const h of ['/apple-touch-icon.png', '/apple-touch-icon-precomposed.png']) { const r = await fetch(h, { credentials: 'omit' }); loads.push([h, r.status, r.headers.get('content-type')]); }
      return { touch, loads, ua: navigator.userAgent, capable: meta('apple-mobile-web-app-capable'), title: meta('apple-mobile-web-app-title'),
               bar: meta('apple-mobile-web-app-status-bar-style'), manifest: !!head.querySelector('link[rel=manifest]') };
    })()`);
    // Signing in once: the token goes, the address is "/" again.
    await ev(`(() => { const i = document.querySelector('input[name=token]'); i.value = ${JSON.stringify(A.token)}; i.form.submit(); return 0; })()`);
    await until('typeof appInstallHint === "function" && document.getElementById("install-app-hint").textContent.length > 0', 30000);
    out.after = await ev('({ href: location.href, hint: document.getElementById("install-app-hint").textContent })');
    await send('Page.reload', {}, s);
    await sleep(500);
    await until('typeof appInstallHint === "function" && document.getElementById("install-app-hint").textContent.length > 0', 30000);
    out.reload = await ev('({ href: location.href, signIn: !!document.querySelector("input[name=token]") })');
    // A file, held sideways: its header, its code and its comment tray clear
    // of the notch and the home indicator (insets as an iPhone Pro Max's).
    await send('Emulation.setDeviceMetricsOverride', { width: 956, height: 440, deviceScaleFactor: 3, mobile: true,
      screenOrientation: { type: 'landscapePrimary', angle: 90 } }, s);
    try { await send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 0, left: 62, right: 62, bottom: 21 } }, s); out.insets = true; }
    catch (e) { out.insets = false; }
    await send('Page.navigate', { url: A.base + '/fileview?path=' + encodeURIComponent(A.file) }, s);
    await until('document.body.classList.contains("code-mode") && !!document.querySelector(".code")', 30000);
    await sleep(300);
    out.file = await ev(`(() => { const r = e => e.getBoundingClientRect(), h = document.querySelector('header'), c = document.querySelector('.code');
      let t = document.getElementById('cmt-tray'); const made = !t; if (made) { t = document.createElement('div'); t.id = 'cmt-tray'; t.textContent = 'x'; document.body.appendChild(t); }
      const out = { w: innerWidth, h: innerHeight, header: [r(h).left, r(h).right], code: [r(c).left, r(c).right, r(c).bottom], tray: [r(t).right, r(t).bottom] };
      if (made) t.remove(); return out; })()`);
  } finally { ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChromeAsAnIphone(unittest.TestCase):
    """The board opened from an iPhone over the tailnet, with the token set."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-iph-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state, root = base / "state", base / "EnsembleProjects"
        for d in (state, root, base / "transcripts", base / "cs"):
            d.mkdir()
        cls.patches = state_patches(base, root, state) + [
            mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        code = Path(proj["path"]) / "motor.py"
        code.write_text("".join(f"def step_{i}():\n    return {i}\n" for i in range(80)), encoding="utf-8")
        cls.hub = Hub()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.hub.port}",
                "ua": IPHONE_UA, "token": TOKEN, "file": str(code)}
        script = base / "iphone_cdp.js"
        script.write_text(IPHONE_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=180)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.hub.close()
        cls.tmp.cleanup()

    def test_the_sign_in_page_names_the_home_screen_icon(self):
        s = self.got["signIn"]
        self.assertIn("iPhone", s["ua"])
        self.assertEqual({t["sizes"] for t in s["touch"]}, {"", "120x120", "152x152", "167x167"})
        self.assertIn({"href": "/static/icons/apple-touch-icon.png", "sizes": ""}, s["touch"])
        self.assertEqual((s["capable"], s["title"], s["bar"], s["manifest"]), ("yes", "Ensemble", "default", True))

    def test_its_icons_load_without_cookies(self):
        for href, code, ctype in self.got["signIn"]["loads"]:
            self.assertEqual((code, ctype), (200, "image/png"), href)

    def test_it_signs_in_once_and_stays_signed_in(self):
        base = f"http://127.0.0.1:{self.hub.port}/"
        self.assertEqual(self.got["after"]["href"], base, "no token left in the address")
        self.assertEqual(self.got["reload"], {"href": base, "signIn": False})

    def test_a_file_held_sideways_keeps_clear_of_the_notch_and_home_indicator(self):
        if not self.got["insets"]:
            self.skipTest("this Chrome cannot emulate safe-area insets")
        f = self.got["file"]
        self.assertEqual((f["w"], f["h"]), (956, 440))
        self.assertGreaterEqual(f["header"][0], 62)
        self.assertLessEqual(f["header"][1], f["w"] - 62)
        self.assertGreaterEqual(f["code"][0], 62)
        self.assertLessEqual(f["code"][1], f["w"] - 62)
        self.assertLessEqual(f["code"][2], f["h"] - 21, "the code scrolls above the home indicator")
        self.assertLessEqual(f["tray"][0], f["w"] - 62 - 12)
        self.assertLessEqual(f["tray"][1], f["h"] - 21 - 12)

    def test_settings_says_how_to_add_it_to_the_home_screen(self):
        self.assertIn("Share › Add to Home Screen", self.got["after"]["hint"])


if __name__ == "__main__":
    unittest.main()
