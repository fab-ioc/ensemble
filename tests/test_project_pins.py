"""Pinned projects (#362): POST /api/projects/pins and summary.pinned.

* {projectId, pinned} pins at the end of the pinned group, unpins; an unknown
  project is 404; a bad body is 400; only the page may write;
* {order: [...]} reorders the pinned only: it never pins or unpins, and a
  pinned one it leaves out keeps its place after the named ones;
* a settings PUT never writes the list; junk saved by hand reads as none;
* build_projects()'s summary carries the list (only registered ones), so a
  second browser reads the same order;
* many threads at once leave settings.json valid with every pin landed.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
from unittest import mock  # noqa: E402
from test_project_settings import Hub  # noqa: E402


class Pins(Hub):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(dashboard, "SETTINGS_FILE", Path(self.tmp.name) / "state" / "settings.json")
        p.start()
        self.addCleanup(p.stop)
        self.c = self.project("Cobalt")
        self.d = self.project("Delta")

    def post(self, body):
        st, _, raw = self.call("POST", "/api/projects/pins", body)
        return st, json.loads(raw)

    def saved(self):
        return json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))["pinnedProjects"]

    def test_pin_appends_and_unpin_removes(self):
        self.assertEqual(self.post({"projectId": self.c, "pinned": True}), (200, {"ok": True, "pinned": [self.c]}))
        self.assertEqual(self.post({"projectId": self.ed, "pinned": True})[1]["pinned"], [self.c, self.ed])
        self.assertEqual(self.post({"projectId": self.c, "pinned": True})[1]["pinned"], [self.c, self.ed],
                         "pinning a pinned one keeps its place")
        self.assertEqual(self.post({"projectId": self.c, "pinned": False})[1]["pinned"], [self.ed])
        self.assertEqual(self.saved(), [self.ed])
        self.assertEqual(self.post({"projectId": self.d, "pinned": False})[1]["pinned"], [self.ed],
                         "unpinning one not pinned changes nothing")

    def test_refusals(self):
        self.assertEqual(self.post({"projectId": "proj-nope", "pinned": True})[0], 404)
        self.assertEqual(self.post({"projectId": "__unassigned__", "pinned": True})[0], 404)
        self.assertEqual(self.post({"projectId": self.c, "pinned": "yes"})[0], 400)
        self.assertEqual(self.post({"pinned": True})[0], 400)
        self.assertEqual(self.post({"order": "x"})[0], 400)
        self.page = False
        st, body = self.post({"projectId": self.c, "pinned": True})
        self.assertEqual((st, body["error"]), (403, "page_only"))
        self.assertFalse(dashboard.SETTINGS_FILE.exists() and self.saved())

    def test_order_reorders_only_the_pinned(self):
        for pid in (self.ed, self.ot, self.c):
            self.post({"projectId": pid, "pinned": True})
        st, body = self.post({"order": [self.c, self.d, "proj-nope", self.ed, self.c, 7]})
        self.assertEqual(st, 200)
        self.assertEqual(body["pinned"], [self.c, self.ed, self.ot],
                         "the named pinned in order, then the one left out; Delta is not pinned by it")
        self.assertEqual(self.saved(), [self.c, self.ed, self.ot])
        self.assertEqual(self.post({"order": []})[1]["pinned"], [self.c, self.ed, self.ot])

    def test_settings_put_never_writes_the_list_and_junk_reads_as_none(self):
        self.post({"projectId": self.c, "pinned": True})
        dashboard.save_settings({"pinnedProjects": [self.d], "openMode": "tab"})
        self.assertEqual(dashboard.load_settings()["pinnedProjects"], [self.c])
        self.assertEqual(dashboard.load_settings()["openMode"], "tab")
        cur = json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))
        for junk in ("x", {"a": 1}, [1, None, "", "y" * 81, self.d, self.d]):
            cur["pinnedProjects"] = junk
            dashboard.SETTINGS_FILE.write_text(json.dumps(cur), encoding="utf-8")
            self.assertEqual(dashboard.load_settings()["pinnedProjects"],
                             [self.d] if isinstance(junk, list) else [])

    def test_summary_carries_the_registered_pins(self):
        self.post({"projectId": self.d, "pinned": True})
        self.post({"projectId": self.ed, "pinned": True})
        cur = json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))
        cur["pinnedProjects"] = [self.d, "proj-gone", self.ed]
        dashboard.SETTINGS_FILE.write_text(json.dumps(cur), encoding="utf-8")
        self.assertEqual(dashboard.build_projects()["summary"]["pinned"], [self.d, self.ed])
        st, _, raw = self.call("GET", "/api/projects")
        self.assertEqual(json.loads(raw)["summary"]["pinned"], [self.d, self.ed], "what a second browser reads")
        self.assertEqual(self.post({"projectId": self.c, "pinned": True})[1]["pinned"], [self.d, self.ed, self.c],
                         "a write drops the gone one")

    def test_concurrent_writes_keep_the_file_valid(self):
        ids = [self.project(f"P{i:02d}") for i in range(16)]
        errs = []

        def worker(pid):
            try:
                for _ in range(3):
                    dashboard.set_project_pins({"projectId": pid, "pinned": True})
                    dashboard.set_project_pins({"order": list(reversed(ids))})
                    dashboard.save_settings({"openMode": "window"})
            except Exception as e:      # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=worker, args=(pid,)) for pid in ids]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        got = self.saved()
        self.assertEqual(sorted(got), sorted(ids), "every pin landed, each once")


if __name__ == "__main__":
    unittest.main()
