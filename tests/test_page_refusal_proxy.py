"""A page-only write through the tailnet https address (#432).

tailscale serve proxies https://<machine>.<tailnet>.ts.net to the hub's
loopback port, and on Linux its tailscaled runs as root: lsof, running as the
hub's user, cannot see its sockets. Every page-only write through that
address was refused as "cannot tell which program sent it" (pinning a
project, read marks), and the page reloaded its key every few seconds.

* through the dashboard's whole check (_page_refusal): the page's request
  via the proxy passes; the same headers (X-Forwarded-*, Tailscale-User-Login)
  from an agent, or from a loopback program nobody can name, are refused;
* a refusal is logged, one line per kind a minute, with no cookie in it;
* pagekey.js fetches a new key for every old-key refusal, and for any other
  page_only refusal once per page.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import peer_process  # noqa: E402

NODE = shutil.which("node")
PORT = 8765
PROXY_HOST = "box.example-tailnet.ts.net"


def handler(headers: dict, client=("127.0.0.1", 41000), path="/api/attention/seen"):
    h = object.__new__(dashboard.Handler)
    h.headers = headers
    h.client_address = client
    h.server = mock.Mock(server_address=("127.0.0.1", PORT))
    h.connection = None
    h.command, h.path = "POST", path
    return h


def proxied(**extra) -> dict:
    """The headers of the page's write through tailscale serve."""
    return {"Host": f"127.0.0.1:{PORT}", "Origin": f"https://{PROXY_HOST}", "Sec-Fetch-Site": "same-origin",
            "X-Forwarded-Host": PROXY_HOST, "X-Forwarded-Proto": "https", "X-Forwarded-For": "100.64.1.2",
            "Tailscale-User-Login": "someone@example.com",
            "Cookie": f"ensemble_ui_{PORT}={dashboard._UI_KEY}", **extra}


class ThroughTheProxy(unittest.TestCase):
    def setUp(self):
        dashboard._page_refusal_logged.clear()

    def refusal(self, headers, *, owners=(), uid=0, proxy_uids=frozenset({0}), client=("127.0.0.1", 41000),
                procs=None):
        rows = [(client[0], client[1], "127.0.0.1", PORT, uid)]
        with mock.patch.object(peer_process.sys, "platform", "linux"), \
             mock.patch.object(peer_process.os, "getuid", lambda: 1000, create=True), \
             mock.patch.object(peer_process, "_linux_tcp_rows", lambda *a: rows), \
             mock.patch.object(peer_process, "_linux_proxy_uids", lambda: set(proxy_uids)), \
             mock.patch.object(peer_process, "_posix_owners", lambda c: list(owners)), \
             mock.patch.object(peer_process, "_posix_processes",
                               lambda: procs or {400: (1, "claude"), 410: (400, "curl"), 500: (1, "chrome")}), \
             mock.patch.object(dashboard, "_agent_pids", lambda: set()), \
             contextlib.redirect_stdout(io.StringIO()):
            return handler(headers, client)._page_refusal()

    def test_the_pages_write_through_the_proxy_passes(self):
        self.assertEqual(self.refusal(proxied()), "")

    def test_the_same_headers_from_a_loopback_program_nobody_can_name_are_refused(self):
        self.assertIn("cannot tell which program sent it", self.refusal(proxied(), uid=1000))
        self.assertIn("cannot tell which program sent it", self.refusal(proxied(), proxy_uids=set()))

    def test_the_same_headers_from_an_agent_are_refused(self):
        self.assertIn("agent's process", self.refusal(proxied(), owners=[410], uid=1000))

    def test_the_tailnet_address_is_unchanged(self):
        h = proxied(Host=f"100.101.102.103:{PORT}", Origin=f"http://100.101.102.103:{PORT}")
        for k in ("X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-For", "Tailscale-User-Login"):
            h.pop(k)
        self.assertEqual(self.refusal(h, client=("100.64.1.2", 41000), uid=1000), "")

    def test_a_browser_here_still_passes(self):
        h = {"Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}", "Sec-Fetch-Site": "same-origin",
             "Cookie": f"ensemble_ui_{PORT}={dashboard._UI_KEY}"}
        self.assertEqual(self.refusal(h, owners=[500], uid=1000), "")


class RefusalLog(unittest.TestCase):
    def setUp(self):
        dashboard._page_refusal_logged.clear()

    def test_a_refusal_is_logged_once_a_minute_per_kind(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            why = "Only the dashboard page can do this, and this request did not come from it: x (1)."
            self.assertTrue(dashboard._log_page_refusal("POST", "/api/attention/seen?token=SECRET", "127.0.0.1:1",
                                                        why, now=1000.0))
            self.assertFalse(dashboard._log_page_refusal("POST", "/api/attention/seen", "127.0.0.1:2",
                                                         why.replace("(1)", "(2)"), now=1010.0))
            self.assertTrue(dashboard._log_page_refusal("POST", "/api/projects/pin", "127.0.0.1:3", why, now=1020.0))
            self.assertTrue(dashboard._log_page_refusal("POST", "/api/attention/seen", "127.0.0.1:4", why, now=1061.0))
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 3, lines)
        self.assertTrue(lines[0].startswith("[page-only] refused POST /api/attention/seen from 127.0.0.1:1: Only"))
        self.assertNotIn("SECRET", out.getvalue())
        self.assertIn("(and 1 more like it since the last line)", lines[2])

    def test_a_suffix_on_the_route_is_neither_logged_nor_a_new_kind(self):
        out = io.StringIO()
        why = "Only the dashboard page can do this."
        with contextlib.redirect_stdout(out):
            self.assertTrue(dashboard._log_page_refusal("POST", "/api/attention/seen;token=SECRET1", "c", why, now=1.0))
            self.assertFalse(dashboard._log_page_refusal("POST", "/api/attention/seen;token=SECRET2#x", "c", why,
                                                         now=2.0))
            self.assertFalse(dashboard._log_page_refusal("POST", "/api/attention/seen#SECRET3", "c", why, now=3.0))
        self.assertNotIn("SECRET", out.getvalue())
        self.assertIn("refused POST /api/attention/seen from c:", out.getvalue())

    def test_the_page_refusal_logs_without_the_cookie(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            why = handler({"Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}",
                           "Cookie": f"ensemble_ui_{PORT}=stale-key"})._page_refusal()
        self.assertEqual(why, "Reload the dashboard page and try again.")
        self.assertIn("[page-only] refused POST /api/attention/seen from 127.0.0.1:41000: Reload", out.getvalue())
        self.assertNotIn("stale-key", out.getvalue())


@unittest.skipUnless(NODE, "node is not installed")
class PageKey(unittest.TestCase):
    def run_page(self, refusal: str, writes: int) -> dict:
        js = (ROOT / "static" / "pagekey.js").read_text(encoding="utf-8")
        harness = """
const calls = [];
global.window = global;
global.document = { body: { appendChild(f) { calls.push('frame'); setTimeout(() => f.fire('load'), 0); } },
  createElement() { const l = {}; return { setAttribute() {}, remove() {},
    addEventListener(n, fn) { l[n] = fn; }, fire(n) { l[n] && l[n](); } }; } };
const res = (s, b) => ({ status: s, clone() { return { json: async () => b }; } });
global.fetch = async () => { calls.push('post');
  return res(403, { error: 'page_only', message: %s }); };
""" % json.dumps(refusal) + js + """
(async () => {
  for (let i = 0; i < %d; i++) await pageWrite('/api/attention/seen', { roomId: 'room-a' });
  console.log(JSON.stringify({ calls }));
})();
""" % writes
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "key.cjs"
            script.write_text(harness, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_another_refusal_refreshes_the_key_once_per_page(self):
        got = self.run_page("Only the dashboard page can do this, and this request did not come from it: "
                            "cannot tell which program sent it.", 4)
        self.assertEqual(got["calls"], ["post", "frame", "post", "post", "post", "post"])

    def test_an_old_key_refreshes_every_time(self):
        got = self.run_page("Reload the dashboard page and try again.", 2)
        self.assertEqual(got["calls"], ["post", "frame", "post", "post", "frame", "post"])


if __name__ == "__main__":
    unittest.main()
