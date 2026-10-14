"""#231: the hub speaks HTTP/1.1. Several requests share one connection
(every answer says where it ends, a body is always read before the answer),
an idle connection is closed after KEEPALIVE_IDLE_S, a stopping server closes
the ones it holds, and a browser revalidates a GET answer by itself."""
import gzip
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import dashboard

ROOT = Path(__file__).resolve().parent.parent


class Counting(dashboard.HubServer):
    """The hub's server, counting the connections it accepted."""
    accepted = 0

    def process_request(self, request, client_address):
        self.accepted += 1
        super().process_request(request, client_address)


class Probe(dashboard.Handler):
    """Routes for the framing safety nets: an answer that says no length,
    one that never answers, and what each request asked with."""
    seen: list = []

    def do_GET(self):
        Probe.seen.append((self.path, self.headers.get("If-None-Match") or "", self.client_address[1]))
        if self.path == "/x/nolength":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"until the connection closes")
            return
        if self.path == "/x/silent":
            return
        super().do_GET()


def serve(test, handler=dashboard.Handler):
    server = Counting(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


def until(cond, s=5.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Base(unittest.TestCase):
    def setUp(self):
        self.roadmap = {"items": ["a"]}
        for p in (mock.patch.object(dashboard, "load_projects", return_value=[{"id": "proj-1"}]),
                  mock.patch.object(dashboard, "read_roadmap", side_effect=lambda proj: dict(self.roadmap)),
                  mock.patch.object(dashboard, "load_settings", return_value={"x": 1})):
            p.start()
            self.addCleanup(p.stop)
        Probe.seen = []
        self.server = serve(self, Probe)
        self.port = self.server.server_port

    def conn(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(c.close)
        return c

    def ask(self, c, method, path, body=None, headers=None):
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r, r.read()


class KeepAlive(Base):
    def test_several_requests_on_one_connection(self):
        c = self.conn()
        r, body = self.ask(c, "GET", "/api/roadmap?project=proj-1")
        self.assertEqual((r.status, r.version), (200, 11))
        self.assertEqual(json.loads(body), {"items": ["a"]})
        etag = r.headers["ETag"]
        self.assertTrue(etag.startswith('W/"'), etag)
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        sock = c.sock
        # A POST with a body the handler reads.
        r, body = self.ask(c, "POST", "/api/nowhere", b'{"a": 1}', {"Content-Type": "application/json"})
        self.assertEqual(r.status, 404)
        # A refusal that never reads its body (a hook from another site): the
        # body is read before the answer, so the next request is not taken from it.
        r, body = self.ask(c, "POST", "/api/agent/hook", b'{"room": "' + b"x" * 5000 + b'"}',
                           {"Content-Type": "application/json", "Origin": "http://elsewhere.example"})
        self.assertEqual(r.status, 403)
        self.assertIsNone(r.headers.get("Connection"))
        # Revalidated: a bodiless 304 that says no length.
        r, body = self.ask(c, "GET", "/api/roadmap?project=proj-1", headers={"If-None-Match": etag})
        self.assertEqual((r.status, body), (304, b""))
        self.assertIsNone(r.headers.get("Content-Length"))
        # Compressed for a reader elsewhere: the same weak ETag as the plain one.
        self.roadmap = {"items": ["a" * 20000]}
        with mock.patch.object(dashboard.Handler, "_far_reader", return_value=True):
            r, body = self.ask(c, "GET", "/api/roadmap?project=proj-1", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.headers["Content-Encoding"], "gzip")
        self.assertEqual(int(r.headers["Content-Length"]), len(body))
        gz_etag = r.headers["ETag"]
        r, plain = self.ask(c, "GET", "/api/roadmap?project=proj-1")
        self.assertEqual(gzip.decompress(body), plain)
        self.assertEqual(r.headers["ETag"], gz_etag)
        # HEAD: the GET's headers, no body.
        r, body = self.ask(c, "HEAD", "/api/roadmap?project=proj-1")
        self.assertEqual((r.status, body), (200, b""))
        self.assertEqual(int(r.headers["Content-Length"]), len(plain))
        r, body = self.ask(c, "GET", "/api/projects?fields=po")
        self.assertEqual(json.loads(body), {"projects": [{"id": "proj-1", "poRoomId": ""}]})
        self.assertIs(c.sock, sock, "the connection was closed on the way")
        self.assertEqual(self.server.accepted, 1)

    def test_a_page_and_a_script_revalidate_on_the_same_connection(self):
        c = self.conn()
        r, body = self.ask(c, "GET", "/static/hl.js")
        self.assertEqual(r.status, 200)
        etag = r.headers["ETag"]
        r, body = self.ask(c, "GET", "/static/hl.js", headers={"If-None-Match": etag})
        self.assertEqual((r.status, body), (304, b""))
        r, body = self.ask(c, "GET", "/static/hl.js", headers={"If-None-Match": '"other", ' + etag})
        self.assertEqual(r.status, 304)
        r, body = self.ask(c, "GET", "/static/nothing-here.js")
        self.assertEqual(r.status, 404)
        self.assertEqual(self.server.accepted, 1)

    def test_an_answer_without_a_length_closes_its_connection(self):
        c = self.conn()
        r, body = self.ask(c, "GET", "/x/nolength")
        self.assertEqual(body, b"until the connection closes")
        self.assertEqual(r.headers["Connection"], "close")
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_a_handler_that_never_answers_closes_rather_than_hangs(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(s.close)
        s.sendall(b"GET /x/silent HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        self.assertEqual(s.recv(100), b"")

    def test_a_body_too_big_to_read_closes_after_the_answer(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(s.close)
        n = dashboard.BODY_DRAIN_MAX + 1
        s.sendall(b"POST /api/agent/hook HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: http://elsewhere.example\r\n"
                  b"Content-Length: %d\r\n\r\n" % n)
        got = b""
        while b"\r\n\r\n" not in got:
            got += s.recv(4096)
        self.assertIn(b"403", got.split(b"\r\n")[0])
        self.assertIn(b"Connection: close", got)

    def test_a_chunked_body_is_not_reused(self):
        c = self.conn()
        c.putrequest("POST", "/api/nowhere")
        c.putheader("Transfer-Encoding", "chunked")
        c.endheaders()
        c.send(b"2\r\n{}\r\n0\r\n\r\n")
        r = c.getresponse()
        r.read()
        self.assertEqual(r.headers["Connection"], "close")

    def test_what_stays_out_of_the_browser_cache(self):
        c = self.conn()
        built = {"projects": [{"id": "proj-1", "sessions": []}], "summary": {}}
        p = mock.patch.object(dashboard, "build_projects", return_value=built)
        p.start()
        self.addCleanup(p.stop)
        r, _ = self.ask(c, "GET", "/api/settings")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIsNone(r.headers.get("ETag"))
        # A poll that names the copy it holds: an URL of its own every time.
        r, _ = self.ask(c, "GET", "/api/projects?since=abc")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIsNone(r.headers.get("ETag"))
        r, _ = self.ask(c, "GET", "/api/projects?fields=po")
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        self.assertTrue(r.headers.get("ETag"))


class IdleAndShutdown(Base):
    def idle_conns(self, n):
        socks = []
        for _ in range(n):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            self.addCleanup(s.close)
            s.sendall(b"GET /api/projects?fields=po HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            got = b""
            while not got.endswith(b"]}"):
                got += s.recv(4096)
            socks.append(s)
        return socks

    def closed(self, s):
        try:
            s.settimeout(0.05)
            return s.recv(1) == b""
        except socket.timeout:
            return False
        except OSError:
            return True

    def test_an_idle_connection_is_closed_and_its_thread_ends(self):
        before = threading.active_count()
        with mock.patch.object(dashboard, "KEEPALIVE_IDLE_S", 0.5):
            socks = self.idle_conns(40)
            self.assertGreaterEqual(threading.active_count(), before + 40)
            self.assertTrue(until(lambda: all(self.closed(s) for s in socks), 10))
            self.assertTrue(until(lambda: threading.active_count() <= before + 1, 10),
                            threading.active_count() - before)
            self.assertFalse(self.server.open_handlers)

    def test_stopping_closes_the_connections_it_holds_at_once(self):
        socks = self.idle_conns(5)          # KEEPALIVE_IDLE_S: 15 s
        t = time.monotonic()
        self.server.shutdown()
        self.assertTrue(until(lambda: all(self.closed(s) for s in socks), 2))
        self.assertLess(time.monotonic() - t, 2)
        self.assertTrue(until(lambda: not self.server.open_handlers, 2))


CHILD = r"""
import sys, threading
sys.path.insert(0, sys.argv[1])
import dashboard
try:
    s = dashboard._listen_or_exit("127.0.0.1", int(sys.argv[2]))
except dashboard.PortTaken:
    print("taken", flush=True); sys.exit(3)
print("listening", flush=True)
s.serve_forever()
"""


class RestartWithOpenConnections(unittest.TestCase):
    def test_the_next_hub_binds_the_port_and_a_second_one_is_refused(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="h11-") as home:
            env = dict(os.environ, USERPROFILE=home, HOME=home, PYTHONIOENCODING="utf-8")

            def start():
                p = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(port)], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8")
                self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
                return p, p.stdout.readline().strip()

            first, said = start()
            self.assertEqual(said, "listening")
            held = []
            for _ in range(4):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                self.addCleanup(c.close)
                c.request("GET", "/api/health")
                c.getresponse().read()
                held.append(c)
            first.kill()
            first.wait()
            second, said = start()
            self.assertEqual(said, "listening")
            third, said = start()
            if sys.platform == "win32":
                self.assertIn("is taken", said)
            # A kept connection to the hub that is gone is replaced by a new one.
            c = held[0]
            try:
                c.request("GET", "/api/health")
                status = c.getresponse().status
            except (OSError, http.client.HTTPException):
                c.close()
                c.request("GET", "/api/health")
                status = c.getresponse().status
            self.assertIn(status, (200, 503))


NODE = shutil.which("node")
try:
    from tests.test_top_bar import CDP_JS as BASE_JS, CHROME
    from tests import chrome_profile
except Exception:                              # noqa: BLE001
    BASE_JS, CHROME = "", ""

BROWSER = r"""
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  try {
    const {targetId} = await c.send('Target.createTarget', {url: A.base + '/ui-key'});
    const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten: true});
    const evaluate = async expression => {
      const r = await c.send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, sessionId);
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
    };
    await sleep(500);
    const out = await evaluate(`(async () => {
      const got = [];
      for (let i = 0; i < 3; i++) {
        const r = await fetch('/api/roadmap?project=proj-1');
        got.push({status: r.status, body: await r.json()});
      }
      return got;
    })()`);
    console.log(JSON.stringify(out));
  } finally { c.ws.close(); ch.kill(); }
}
main().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME and BASE_JS, "needs Node and Chrome")
class ChromeRevalidates(Base):
    def test_chrome_asks_with_the_etag_by_itself_on_kept_connections(self):
        js = BASE_JS[:BASE_JS.index("// What the bar shows")] + BROWSER
        with tempfile.TemporaryDirectory(prefix="h11c-", ignore_cleanup_errors=True) as tmp:
            script = Path(tmp) / "run.js"
            script.write_text(js, encoding="utf-8")
            args = {"base": f"http://127.0.0.1:{self.port}", "tmp": tmp, **chrome_profile.node_args()}
            r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True,
                               encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual([g["body"] for g in got], [{"items": ["a"]}] * 3)
        asked = [inm for path, inm, _ in Probe.seen if path.startswith("/api/roadmap")]
        self.assertEqual(len(asked), 3)
        self.assertEqual(asked[0], "")
        self.assertTrue(asked[1] and asked[1] == asked[2], asked)     # Chrome's own If-None-Match
        # Chrome may open a spare connection ahead (counted as accepted, never
        # used): count the connections that carried a request.
        used = {port for _, _, port in Probe.seen}
        self.assertLess(len(used), len(Probe.seen), "every request on a new connection")


if __name__ == "__main__":
    unittest.main()
