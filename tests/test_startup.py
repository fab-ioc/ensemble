"""ED-227: a starting hub answers at once and says what it is doing."""
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import dashboard
import progress
import startup

NAV = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"}


class StartupHubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def tearDown(self):
        startup.ready()
        progress.clear("sessions:300")

    def req(self, method, path, headers=None, body=None, timeout=10):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        conn.request(method, path, body=body, headers=headers or {})
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def test_starting_hub_answers_health_and_the_startup_page(self):
        startup.begin(steps=7)
        startup.step("Connecting to the agents' terminals")
        res, data = self.req("GET", "/api/health")
        self.assertEqual(res.status, 200)
        h = json.loads(data)
        self.assertEqual((h["phase"], h["step"], h["done"], h["total"], h["unit"]),
                         ("starting", "Connecting to the agents' terminals", 0, 7, "steps"))
        self.assertIn("version", h)
        for path in ("/", "/index.html", "/session?room=room-x", "/fileview?path=x"):
            res, data = self.req("GET", path, NAV)
            self.assertEqual(res.status, 200, path)
            self.assertEqual(res.getheader("X-Ensemble-Startup"), "1", path)
            self.assertIn(b'name="sw-state" content="starting"', data)
        # What the restart helper and the app launcher probe still answers.
        self.assertEqual(self.req("GET", "/api/platform")[0].status, 200)
        self.assertEqual(self.req("GET", "/api/version")[0].status, 200)
        # The rest of the API says it is starting, at once.
        t0 = time.time()
        res, data = self.req("GET", "/api/sessions?n=300")
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(res.status, 503)
        self.assertEqual(res.getheader("Retry-After"), "2")
        self.assertEqual(json.loads(data)["error"], "starting")

    def test_a_write_waits_for_the_boot(self):
        startup.begin(steps=1)
        out = {}

        def post():
            out["res"], _ = self.req("POST", "/api/no-such-thing", {"Content-Type": "application/json"}, b"{}")
            out["at"] = time.time()

        t = threading.Thread(target=post)
        t.start()
        time.sleep(0.5)
        self.assertNotIn("res", out)
        booted_at = time.time()
        startup.booted()
        t.join(10)
        self.assertGreaterEqual(out["at"], booted_at)
        self.assertNotEqual(out["res"].status, 503)

    def test_a_write_held_too_long_is_refused(self):
        startup.begin(steps=1)
        with mock.patch.object(dashboard, "STARTUP_WRITE_WAIT_S", 0.3):
            res, data = self.req("POST", "/api/no-such-thing", {"Content-Type": "application/json"}, b"{}")
        self.assertEqual(res.status, 503)

    def test_agent_hook_is_never_held(self):
        startup.begin(steps=1)
        t0 = time.time()
        res, _ = self.req("POST", "/api/agent/hook", {"Content-Type": "application/json"}, b"{}")
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(res.status, 503)

    def test_warming_shows_the_count_and_only_the_board_waits(self):
        startup.begin(steps=1)
        startup.booted()
        startup.warming("Reading the task list", "sessions:300")
        progress.set_progress("sessions:300", "Reading session files", 812, 1987)
        h = json.loads(self.req("GET", "/api/health")[1])
        self.assertEqual((h["phase"], h["done"], h["total"], h["unit"]), ("warming", 812, 1987, "files"))
        self.assertEqual(h["step"], "Reading session files")
        self.assertEqual(self.req("GET", "/", NAV)[0].getheader("X-Ensemble-Startup"), "1")
        res, data = self.req("GET", "/?startup=skip", NAV)
        self.assertIsNone(res.getheader("X-Ensemble-Startup"))
        self.assertNotIn(b'name="sw-state"', data)
        res, _ = self.req("GET", "/session?room=room-x", NAV)
        self.assertIsNone(res.getheader("X-Ensemble-Startup"))
        # The page's own reads go through once booted.
        self.assertEqual(self.req("GET", "/api/platform")[0].status, 200)
        self.assertNotEqual(self.req("GET", "/api/live")[0].status, 503)

    def test_ready_serves_the_board(self):
        startup.ready()
        res, data = self.req("GET", "/", NAV)
        self.assertIsNone(res.getheader("X-Ensemble-Startup"))
        self.assertEqual(json.loads(self.req("GET", "/api/health")[1])["phase"], "ready")
        # The start-up page is always there for the service worker to keep.
        res, data = self.req("GET", "/startup")
        self.assertEqual(res.status, 200)
        self.assertIn(b'name="sw-state"', data)

    def test_service_worker_is_served_for_the_whole_site(self):
        res, data = self.req("GET", "/sw.js")
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Service-Worker-Allowed"), "/")
        self.assertIn("javascript", res.getheader("Content-Type"))
        self.assertNotIn(b"__STARTUP_STAMP__", data)
        self.assertRegex(data, rb"const STAMP = '[0-9a-f]{12}'")


class StartupHealthTest(unittest.TestCase):
    def tearDown(self):
        startup.ready()

    def test_eta_from_the_rate_so_far(self):
        startup.begin(steps=1)
        startup.booted()
        startup.warming("Reading the task list", "sessions:500")
        with startup._LOCK:
            startup._state["phaseAt"] = time.time() - 10
        progress.set_progress("sessions:500", "Reading session files", 500, 2000)
        try:
            h = startup.health()
        finally:
            progress.clear("sessions:500")
        self.assertAlmostEqual(h["eta"], 30, delta=2)

    def test_not_started_process_is_ready(self):
        self.assertTrue(startup.is_ready())
        self.assertTrue(startup.wait_booted(0))


class SessionsAnswerWhileReadTest(unittest.TestCase):
    def test_busy_list_answers_with_the_rows_it_has(self):
        n = 9301
        lock = dashboard._SESS_LOCKS.setdefault(n, threading.Lock())
        dashboard._SESS_CACHE[n] = (0.0, dashboard._SESS_GEN, [{"sessionId": "held"}])
        lock.acquire()
        try:
            with mock.patch.object(dashboard, "_load_sessions_uncached") as read:
                t0 = time.time()
                rows = dashboard.load_sessions(n)
            self.assertLess(time.time() - t0, 1)
            read.assert_not_called()
            self.assertEqual(rows, [{"sessionId": "held"}])
        finally:
            lock.release()
            dashboard._SESS_CACHE.pop(n, None)
            dashboard._SESS_LOCKS.pop(n, None)

    def test_boot_rows_are_read_again_on_first_use(self):
        n = 9302
        dashboard._SESS_CACHE[n] = (time.time(), dashboard._SESS_GEN, [{"sessionId": "saved"}])
        dashboard._SESS_BOOT.add(n)
        try:
            with mock.patch.object(dashboard, "_load_sessions_uncached",
                                   return_value=[{"sessionId": "fresh"}]):
                self.assertEqual(dashboard.load_sessions(n), [{"sessionId": "fresh"}])
            self.assertNotIn(n, dashboard._SESS_BOOT)
        finally:
            dashboard._SESS_BOOT.discard(n)
            dashboard._SESS_CACHE.pop(n, None)
            dashboard._SESS_LOCKS.pop(n, None)

    def test_a_changed_list_waits_for_the_fresh_one(self):
        n = 9303
        lock = dashboard._SESS_LOCKS.setdefault(n, threading.Lock())
        dashboard._SESS_CACHE[n] = (0.0, dashboard._SESS_GEN - 1, [{"sessionId": "before archive"}])
        lock.acquire()
        got = {}
        t = threading.Thread(target=lambda: got.setdefault("rows", dashboard.load_sessions(n)))
        try:
            with mock.patch.object(dashboard, "_load_sessions_uncached",
                                   return_value=[{"sessionId": "after"}]):
                t.start()
                time.sleep(0.3)
                self.assertNotIn("rows", got)
                lock.release()
                t.join(5)
            self.assertEqual(got["rows"], [{"sessionId": "after"}])
        finally:
            if lock.locked():
                lock.release()
            dashboard._SESS_CACHE.pop(n, None)
            dashboard._SESS_LOCKS.pop(n, None)


if __name__ == "__main__":
    unittest.main()
