"""The PO chat replay's budgets (#265 review 2): every endpoint's tail, a
page request left without an answer, and a step that ends away from the chat
are failures."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import perf_po_chat as P  # noqa: E402


class Analyse(unittest.TestCase):
    def run_on(self, requests=(), page=(), steps=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        for name, rows in (("requests", requests), ("page", page), ("steps", steps)):
            (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return P.analyse(out, 30)["fails"]

    def test_a_rare_endpoints_tail_counts(self):
        rows = [{"at": 1.0, "m": "GET", "p": "/api/git/log", "q": "", "ms": 900.0}] * 2
        self.assertTrue(any("/api/git/log: p95" in f for f in self.run_on(requests=rows)))

    def test_another_servers_wait_is_held_to_the_max_only(self):
        rows = [{"at": 1.0, "m": "GET", "p": "/api/update-check", "q": "", "ms": 900.0}] * 2
        self.assertFalse([f for f in self.run_on(requests=rows) if "update-check" in f])
        rows = [{"at": 1.0, "m": "GET", "p": "/api/update-check", "q": "", "ms": 2500.0}]
        self.assertTrue([f for f in self.run_on(requests=rows) if "update-check: max" in f])

    def test_a_page_request_without_an_answer_counts(self):
        page = [{"at": 10.0, "m": "GET", "p": "/api/room", "q": "", "ms": None, "open": True, "endAt": 15.0},
                {"at": 14.5, "m": "GET", "p": "/api/live", "q": "", "ms": None, "open": True, "endAt": 15.0},
                {"at": 1.0, "m": "POST", "p": "/api/attention/seen", "q": "", "status": 200, "ms": None, "open": True, "endAt": 15.0}]
        fails = [f for f in self.run_on(page=page) if "no answer" in f]
        self.assertEqual(len(fails), 1)
        self.assertIn("/api/room", fails[0])

    def test_a_step_that_ends_away_from_the_chat_counts(self):
        steps = [{"at": 5.0, "do": "active", "ok": False, "why": "another chat is on screen"},
                 {"at": 6.0, "do": "active", "ok": True}]
        self.assertEqual(len([f for f in self.run_on(steps=steps) if "not the one on screen" in f]), 1)


if __name__ == "__main__":
    unittest.main()
