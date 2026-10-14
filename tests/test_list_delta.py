"""#228: the polled lists as deltas, compressed answers, pages sent again only
when they changed.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_list_delta
"""
from __future__ import annotations

import gzip
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import listdelta  # noqa: E402


def merge(rows, d, key, sub="", sub_key=""):
    """What index.html's listFeed().take does, for checking answers end to end."""
    if d["full"]:
        return d["items"]
    old = {str(r[key]): r for r in rows}
    for it in d["items"]:
        k = str(it[key])
        if sub and it.get("subDelta"):
            subs = {str(x[sub_key]): x for x in (old.get(k) or {}).get(sub, [])}
            for x in it[sub]:
                subs[str(x[sub_key])] = x
            m = {f: v for f, v in it.items() if f not in ("subDelta", sub + "Order")}
            m[sub] = [subs[s] for s in it[sub + "Order"] if s in subs]
            old[k] = m
        else:
            old[k] = it
    return [old[k] for k in d["order"] if k in old]


class FeedTest(unittest.TestCase):
    def test_first_answer_is_full(self):
        f = listdelta.Feed("id")
        rows = [{"id": "a", "x": 1}, {"id": "b", "x": 2}]
        d = f.answer(rows, "")
        self.assertTrue(d["full"])
        self.assertEqual(d["items"], rows)
        self.assertTrue(d["rev"])

    def test_delta_carries_only_changed_rows_and_the_order(self):
        f = listdelta.Feed("id")
        r1 = [{"id": "a", "x": 1}, {"id": "b", "x": 2}, {"id": "c", "x": 3}]
        d1 = f.answer(r1, "")
        r2 = [{"id": "d", "x": 4}, {"id": "a", "x": 1}, {"id": "c", "x": 30}]
        d2 = f.answer(r2, d1["rev"])
        self.assertFalse(d2["full"])
        self.assertEqual(d2["order"], ["d", "a", "c"])
        self.assertEqual(sorted(i["id"] for i in d2["items"]), ["c", "d"])
        self.assertEqual(merge(r1, d2, "id"), r2)

    def test_unchanged_list_is_an_empty_delta_with_the_same_rev(self):
        f = listdelta.Feed("id")
        rows = [{"id": "a", "x": 1}]
        d1 = f.answer(rows, "")
        d2 = f.answer([{"id": "a", "x": 1}], d1["rev"])
        self.assertEqual(d2["rev"], d1["rev"])
        self.assertFalse(d2["full"])
        self.assertEqual(d2["items"], [])

    def test_unknown_or_expired_rev_gets_the_full_list(self):
        f = listdelta.Feed("id", keep=2)
        d0 = f.answer([{"id": "a", "x": 0}], "")
        for i in range(1, 4):
            f.answer([{"id": "a", "x": i}], "")
        self.assertTrue(f.answer([{"id": "a", "x": 9}], "nope")["full"])
        self.assertTrue(f.answer([{"id": "a", "x": 9}], d0["rev"])["full"])

    def test_repeated_keys_are_always_sent_whole(self):
        f = listdelta.Feed("id")
        rows = [{"id": "a", "x": 1}, {"id": "a", "x": 2}]
        d1 = f.answer(rows, "")
        self.assertTrue(f.answer(list(rows), d1["rev"])["full"])

    def test_nested_list_goes_as_a_sub_delta(self):
        f = listdelta.Feed("id", "sessions", "sessionId")
        p1 = [{"id": "p", "name": "P", "sessions": [{"sessionId": "s1", "v": 1}, {"sessionId": "s2", "v": 2}]},
              {"id": "q", "name": "Q", "sessions": []}]
        d1 = f.answer(p1, "")
        p2 = [{"id": "p", "name": "P", "sessions": [{"sessionId": "s3", "v": 3}, {"sessionId": "s1", "v": 1}, {"sessionId": "s2", "v": 20}]},
              {"id": "q", "name": "Q", "sessions": []}]
        d2 = f.answer(p2, d1["rev"])
        self.assertEqual(len(d2["items"]), 1)
        it = d2["items"][0]
        self.assertTrue(it["subDelta"])
        self.assertEqual([s["sessionId"] for s in it["sessions"]], ["s3", "s2"])
        self.assertEqual(it["sessionsOrder"], ["s3", "s1", "s2"])
        self.assertEqual(merge(p1, d2, "id", "sessions", "sessionId"), p2)

    def test_same_list_object_is_answered_from_the_memo(self):
        f = listdelta.Feed("id")
        rows = [{"id": "a"}]
        d1 = f.answer(rows, "")
        with mock.patch.object(listdelta, "_h", side_effect=AssertionError("hashed again")):
            self.assertEqual(f.answer(rows, d1["rev"])["items"], [])


class SlimRowTest(unittest.TestCase):
    def test_allocation_keeps_only_what_the_panel_shows(self):
        r = {"sessionId": "s", "allocation": {"changed": True, "reason": "why", "windows": [1] * 100,
                                              "handover": {"n": 2, "reason": "h", "alarm": True, "big": "x" * 999}},
             "reviewAllocations": [{"changed": True, "reason": "r1", "w": 1}, {"changed": False, "reason": "r2"}]}
        s = dashboard.slim_list_row(r)
        self.assertEqual(s["allocation"], {"changed": True, "reason": "why",
                                           "handover": {"n": 2, "reason": "h", "alarm": True}})
        self.assertEqual(s["reviewAllocations"], [{"changed": True, "reason": "r1"}])
        self.assertIn("windows", r["allocation"])     # the source row is untouched

    def test_row_without_allocations_is_itself(self):
        r = {"sessionId": "s"}
        self.assertIs(dashboard.slim_list_row(r), r)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [{"sessionId": "s1", "idleSeconds": 1, "allocation": {"changed": False, "windows": [1, 2]}},
                    {"sessionId": "s2", "idleSeconds": 5}]
        cls.projects = {"projects": [{"id": "p1", "poRoomId": "room-1", "sessions": [{"sessionId": "s1", "v": 1}]}],
                        "summary": {"n": 1}}

        def load(n=200):
            dashboard._SESS_CACHE[n] = (0, 0, cls.rows)
            return cls.rows

        cls.patches = [
            mock.patch.object(dashboard, "load_sessions", side_effect=load),
            mock.patch.object(dashboard, "_SESS_CACHE", {}),
            mock.patch.object(dashboard, "_SLIM_MEMO", {}),
            mock.patch.object(dashboard, "SESSIONS_FEEDS", {}),
            mock.patch.object(dashboard, "PROJECTS_FEED", listdelta.Feed("id", "sessions", "sessionId")),
            mock.patch.object(dashboard, "build_projects", side_effect=lambda: json.loads(json.dumps(cls.projects))),
            mock.patch.object(dashboard, "load_projects", side_effect=lambda: [{"id": "p1", "poRoomId": "room-1", "name": "x"}]),
        ]
        for p in cls.patches:
            p.start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_sessions_without_since_is_the_old_list(self):
        st, _, body = self.get("/api/sessions?n=300")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body), self.rows)

    def test_sessions_since_is_slim_and_a_delta(self):
        _, _, body = self.get("/api/sessions?n=300&since=")
        d = json.loads(body)
        self.assertTrue(d["full"])
        self.assertEqual(d["items"][0]["allocation"], {"changed": False})
        _, _, body = self.get("/api/sessions?n=300&since=" + d["rev"])
        d2 = json.loads(body)
        self.assertFalse(d2["full"])
        self.assertEqual(d2["items"], [])
        self.assertEqual(d2["order"], ["s1", "s2"])

    def test_projects_fields_po(self):
        _, _, body = self.get("/api/projects?fields=po")
        self.assertEqual(json.loads(body), {"projects": [{"id": "p1", "poRoomId": "room-1"}]})

    def test_projects_since_keeps_the_summary(self):
        _, _, body = self.get("/api/projects?since=")
        d = json.loads(body)
        self.assertTrue(d["full"])
        self.assertEqual(d["summary"], {"n": 1})
        _, _, body = self.get("/api/projects?since=" + d["rev"])
        d2 = json.loads(body)
        self.assertEqual((d2["full"], d2["items"], d2["summary"]), (False, [], {"n": 1}))

    def test_large_json_is_gzipped_only_when_asked_from_elsewhere(self):
        big = [{"sessionId": "s%d" % i, "pad": "x" * 100} for i in range(200)]
        with mock.patch.object(dashboard, "load_sessions", side_effect=lambda n=200: big):
            _, h, body = self.get("/api/sessions?n=1", {"Accept-Encoding": "gzip"})
            self.assertNotIn("Content-Encoding", h)       # this machine: not worth it
            self.assertEqual(json.loads(body), big)
        with mock.patch.object(dashboard, "load_sessions", side_effect=lambda n=200: big), \
                mock.patch.object(dashboard.Handler, "_far_reader", return_value=True):
            st, h, body = self.get("/api/sessions?n=1", {"Accept-Encoding": "gzip"})
            self.assertEqual(h.get("Content-Encoding"), "gzip")
            self.assertEqual(json.loads(gzip.decompress(body)), big)
            st, h, body = self.get("/api/sessions?n=1")
            self.assertNotIn("Content-Encoding", h)
            self.assertEqual(json.loads(body), big)

    def test_small_json_is_not_gzipped(self):
        _, h, _ = self.get("/api/projects?fields=po", {"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", h)

    def test_page_has_etag_and_answers_304(self):
        st, h, body = self.get("/", {"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Cache-Control"), "no-cache")
        self.assertEqual(h.get("Content-Encoding"), "gzip")
        self.assertIn(b"<html", gzip.decompress(body)[:2000].lower())
        etag = h["ETag"]
        st, h2, body = self.get("/", {"If-None-Match": etag})
        self.assertEqual(st, 304)
        self.assertEqual(body, b"")
        self.assertEqual(h2.get("ETag"), etag)
        self.assertIn("X-Ensemble-Stamp", h2)


if __name__ == "__main__":
    unittest.main()
