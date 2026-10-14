"""#228: lists a page reads again on every return (documents, git log, roadmap)
come back as a bodiless 304 while they have not changed: the page asks with
the ETag of the copy it holds (apiHeld)."""
import json
import shutil
import subprocess
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


@unittest.skipUnless(shutil.which("node"), "needs Node")
class PageHoldsTheCopy(unittest.TestCase):
    def test_the_page_asks_with_its_etag_and_answers_a_304_from_its_copy(self):
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        i = page.index("const API_HELD = new Map();")
        source = page[i:page.index("\n}\n", page.index("async function apiHeld(", i)) + 3]
        js = source + r"""
const sent = [];
let answer = () => ({ status: 200, ok: true, etag: '"e1"', body: { items: [1] } });
globalThis.fetch = async (path, init) => {
  sent.push((init && init.headers && init.headers['If-None-Match']) || '');
  const a = answer();
  return { status: a.status, ok: a.ok, headers: { get: k => k === 'ETag' ? a.etag : null },
           json: async () => a.body, statusText: '' };
};
(async () => {
  const first = await apiHeld('/api/roadmap?project=p');
  first.items.push('edited by the caller');
  answer = () => ({ status: 304, ok: false, etag: '"e1"', body: null });
  const second = await apiHeld('/api/roadmap?project=p');
  answer = () => ({ status: 200, ok: true, etag: '"e2"', body: { items: [2] } });
  const third = await apiHeld('/api/roadmap?project=p');
  console.log(JSON.stringify({ sent, second, third }));
})();
"""
        r = subprocess.run([shutil.which("node"), "-e", js], text=True, encoding="utf-8",
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["sent"], ["", '"e1"', '"e1"'])
        self.assertEqual(out["second"], {"items": [1]}, "the held copy, not the caller's edit")
        self.assertEqual(out["third"], {"items": [2]})


if __name__ == "__main__":
    unittest.main()
