"""A page opened from another device on the tailnet's plain http is sent to
tailscale serve's https address (P386).

A browser lets only a secure page use the microphone, and http to another
machine is not one: the mic was hidden on a Mac and an iPhone that opened the
hub at http://<tailnet address>:8765. Checked, on Handler._to_https with a
request shaped like each case (no socket, no listener):

* hub_links.https_address: the address tailscale serve proxies to the hub's
  port, the default port first and without it; another port as host:port; a
  serve address proxying another program is not one; none gives "";
* a page navigation (/ and /session without embed=1) from a tailnet address
  on the tailnet listener is a 302 to the same page and query on https; the
  reader's valid token (cookie or ?token=) goes along as ?token=, a wrong one
  is dropped, a reader with none goes without;
* left alone: this machine (loopback), a proxy here (tailscale serve itself),
  a LAN address, a chat's frame (embed=1), an API call, a fetch that is not a
  navigation, a POST, and a hub that tailscale serve does not serve.
"""
from __future__ import annotations

import email.message
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import hub_links  # noqa: E402

NAME = "epyc-32.tail4f8740.ts.net"
TS_IP = "100.102.197.23"
MAC = "100.87.122.45"
TOKEN = "tok-for-tests-0123456789"
# A navigation on plain http: no Sec-Fetch-* (a browser sends those to secure
# origins only), Upgrade-Insecure-Requests and an HTML Accept.
HTTP_NAV = {"Upgrade-Insecure-Requests": "1",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


class _Sock:
    def __init__(self, addr):
        self.addr = addr

    def getsockname(self):
        return (self.addr, 8765)


def _handler(path, *, client=MAC, local=TS_IP, headers=None, command="GET"):
    h = dashboard.Handler.__new__(dashboard.Handler)
    h.command, h.path = command, path
    h.client_address = (client, 50000)
    h.connection = _Sock(local)
    msg = email.message.Message()
    for k, v in (HTTP_NAV if headers is None else headers).items():
        msg[k] = v
    msg["Host"] = f"{local}:8765"
    h.headers = msg
    h.sent = {"code": None, "headers": {}}
    h.send_response = lambda code, *a: h.sent.__setitem__("code", code)
    h.send_header = lambda k, v: h.sent["headers"].__setitem__(k, v)
    h.end_headers = lambda: None
    return h


class HttpsAddress(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(hub_links, "_HTTPS", [])
        p.start()
        self.addCleanup(p.stop)

    def _learn(self, web):
        hub_links.learn_tailscale({"Self": {"DNSName": NAME + "."}}, {"Web": web})

    def test_the_default_port_first_and_bare(self):
        self._learn({f"{NAME}:9443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}},
                     f"{NAME}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}})
        self.assertEqual(hub_links.https_address(), NAME)

    def test_another_port_keeps_it(self):
        self._learn({f"{NAME}:9443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}})
        self.assertEqual(hub_links.https_address(), f"{NAME}:9443")

    def test_a_serve_address_for_another_program_is_not_the_hubs(self):
        # Today's machine: 443 serves the hub, 9443 another program.
        self._learn({f"{NAME}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8789"}}}})
        self.assertEqual(hub_links.https_address(), "")
        self._learn({f"{NAME}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}},
                     f"{NAME}:9443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8789"}}}})
        self.assertEqual(hub_links.https_address(), NAME)

    def test_no_serve_no_address(self):
        hub_links.learn_tailscale(None, None)
        self.assertEqual(hub_links.https_address(), "")


class ToHttps(unittest.TestCase):
    def setUp(self):
        for target, attr, value in ((hub_links, "_HTTPS", [(NAME, 443)]),
                                    (dashboard, "ACCESS_TOKEN", TOKEN)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def _go(self, path, **kw):
        h = _handler(path, **kw)
        done = h._to_https()
        return done, h.sent["code"], h.sent["headers"].get("Location")

    def _sent_to(self, path, **kw):
        done, code, loc = self._go(path, **kw)
        self.assertTrue(done, f"{path} is sent to https")
        self.assertEqual(code, 302)
        u = urlsplit(loc)
        self.assertEqual((u.scheme, u.netloc), ("https", NAME))
        return u.path, parse_qs(u.query)

    def _stays(self, path, why, **kw):
        done, code, _ = self._go(path, **kw)
        self.assertFalse(done, why)
        self.assertIsNone(code, why + ": nothing was sent")

    def test_the_dashboard_with_the_cookie_takes_the_token_along(self):
        hdr = dict(HTTP_NAV, Cookie=f"{dashboard.TOKEN_COOKIE}={TOKEN}")
        path, q = self._sent_to("/", headers=hdr)
        self.assertEqual(path, "/")
        self.assertTrue(q.get("token") == [TOKEN], "the token goes along")

    def test_a_chat_page_keeps_its_query(self):
        path, q = self._sent_to(f"/session?id=room-1edda3f5&msg=m-1&token={TOKEN}")
        self.assertEqual(path, "/session")
        self.assertEqual(q.get("id"), ["room-1edda3f5"])
        self.assertEqual(q.get("msg"), ["m-1"])
        self.assertEqual(len(q.get("token") or []), 1, "the token once")

    def test_no_or_a_wrong_token_goes_without(self):
        self.assertNotIn("token", self._sent_to("/")[1])
        self.assertNotIn("token", self._sent_to("/?token=wrong")[1])

    def test_ipv6_tailnet_reader(self):
        self._sent_to("/", client="fd7a:115c:a1e0::1234", local="fd7a:115c:a1e0::642b:c518")

    def test_left_alone(self):
        self._stays("/", "this machine", client="127.0.0.1", local="127.0.0.1")
        self._stays("/", "tailscale serve itself (a proxy here)", client="127.0.0.1",
                    local="127.0.0.1",
                    headers=dict(HTTP_NAV, **{"X-Forwarded-For": MAC, "X-Forwarded-Host": NAME}))
        self._stays("/", "a LAN reader cannot reach the tailnet name", client="192.168.1.178",
                    local="0.0.0.0")
        self._stays("/session?id=room-1edda3f5&embed=1", "a chat's frame stays on its page's origin")
        self._stays("/api/ptys", "an API call")
        self._stays("/fileview?path=/x", "only the dashboard and a chat page")
        self._stays("/", "a fetch, not a navigation", headers={"Accept": "*/*"})
        self._stays("/", "a POST", command="POST")

    def test_not_served_on_https_nothing_changes(self):
        with mock.patch.object(hub_links, "_HTTPS", []):
            self._stays("/", "no https address to send it to")


if __name__ == "__main__":
    unittest.main()
