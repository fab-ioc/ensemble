"""The hub's slow answers, counted for /api/health (#265)."""
import json
import time
import unittest
from unittest import mock

import dashboard
from tests.test_task_numbers import Hub


class SlowRequests(Hub):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(dashboard, "_SLOW", dashboard.collections.deque(maxlen=50))
        p.start()
        self.addCleanup(p.stop)

    def test_the_last_hour_by_endpoint_most_first(self):
        now = time.monotonic()
        dashboard._SLOW.extend([(now - 4000, "GET /api/room", 9.0),         # over an hour ago
                                (now - 60, "GET /api/point/ref", 5.2), (now - 30, "GET /api/point/ref", 7.84),
                                (now - 10, "POST /api/room/resume", 6.0)])
        got = dashboard.slow_requests()
        self.assertEqual(got["n"], 3)
        self.assertEqual(list(got["by"]), ["GET /api/point/ref", "POST /api/room/resume"])
        self.assertEqual(got["by"]["GET /api/point/ref"], {"n": 2, "maxS": 7.8})
        self.assertEqual(got["overS"], dashboard.SLOW_REQUEST_S)

    def test_health_says_it(self):
        dashboard._SLOW.append((time.monotonic(), "GET /api/room", 5.5))
        status, _, body = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["slow"]["by"]["GET /api/room"], {"n": 1, "maxS": 5.5})


class GitChangedCount(unittest.TestCase):
    """A project list asks each project's changed files: an old count is
    answered at once and read again in the background (#265)."""

    def setUp(self):
        for name, val in (("_GIT_STAT_CACHE", {}), ("_GIT_STAT_BUSY", set())):
            p = mock.patch.object(dashboard, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_first_read_waits_then_old_one_does_not(self):
        import threading
        gate, calls = threading.Event(), []

        def run(cmd, **kw):
            calls.append(cmd)
            if len(calls) > 1:
                gate.wait(5)
            return mock.Mock(stdout=" M a\n M b\n" if len(calls) == 1 else " M a\n")
        with mock.patch.object(dashboard, "_run", run):
            self.assertEqual(dashboard.git_changed_count("R"), 2)
            dashboard._GIT_STAT_CACHE["R"] = (time.time() - 60, 2)
            t = time.perf_counter()
            self.assertEqual(dashboard.git_changed_count("R"), 2)        # the old count, git still running
            self.assertEqual(dashboard.git_changed_count("R"), 2)        # one read again at a time
            self.assertLess(time.perf_counter() - t, 1)
            gate.set()
            for _ in range(100):
                if not dashboard._GIT_STAT_BUSY:
                    break
                time.sleep(0.02)
            self.assertEqual(len(calls), 2)
            self.assertEqual(dashboard.git_changed_count("R"), 1)


if __name__ == "__main__":
    unittest.main()
