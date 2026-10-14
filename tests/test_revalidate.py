"""#228: lists a page reads again on every return (documents, git log, roadmap)
come back as a bodiless 304 while they have not changed: the page asks with
the ETag of the copy it holds. #231: the browser does that by itself on
HTTP/1.1 (tests/test_http11.py), so the page keeps no copy of its own."""
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import dashboard

ROOT = Path(__file__).resolve().parent.parent


class RevalidatedLists(unittest.TestCase):
    def setUp(self):
        self.roadmap = {"items": ["a"]}
        for p in (patch.object(dashboard, "load_projects", return_value=[{"id": "proj-1"}]),
                  patch.object(dashboard, "read_roadmap", side_effect=lambda proj: dict(self.roadmap))):
            p.start()
            self.addCleanup(p.stop)
        server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}/api/roadmap?project=proj-1"

    def get(self, etag=""):
        req = Request(self.url, headers={"If-None-Match": etag} if etag else {})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except HTTPError as e:
            return e.code, e.headers, e.read()

    def test_an_unchanged_list_is_a_304_and_a_changed_one_comes_whole(self):
        code, h, body = self.get()
        self.assertEqual(code, 200)
        etag = h["ETag"]
        self.assertTrue(etag)
        code, h, body = self.get(etag)
        self.assertEqual((code, body), (304, b""))
        self.roadmap = {"items": ["a", "b"]}
        code, h, body = self.get(etag)
        self.assertEqual(code, 200)
        self.assertIn(b'"b"', body)
        self.assertNotEqual(h["ETag"], etag)


class PageKeepsNoCopy(unittest.TestCase):
    def test_the_page_reads_through_the_browser_cache(self):
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("apiHeld", page)
        self.assertNotIn("API_HELD", page)


if __name__ == "__main__":
    unittest.main()
