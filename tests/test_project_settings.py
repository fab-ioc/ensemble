"""A project's settings (#329): rename it and give it an icon.

* /api/projects/rename: ok (the name changes everywhere the record is read;
  its folder, id and key stay), empty, a name another project has (in any
  case), an unknown project, only from the page; the PO chat's title
  follows and its PO is told once, without a wake;
* /api/projects/icon: PNG, JPEG, WebP and a clean SVG are kept beside
  project.json; too big, not an image, a renamed text file, an SVG with a
  script, an event attribute, an outside link or a DTD are refused; null
  removes it; GET serves it only with the page key, with its type, nosniff
  and a CSP that allows nothing.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import project_icon  # noqa: E402
import quiet_reports  # noqa: E402

PORT = 8798
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP = b"RIFF\x20\x00\x00\x00WEBPVP8 " + b"\x00" * 30
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" fill="#f80"/></svg>'


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class Hub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        for p in (mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                  mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
                  mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
                  mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
                  mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.object(quiet_reports, "_STATE", None),
                  mock.patch.object(dashboard, "load_sessions", lambda *a, **k: [])):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.ed = self.project("Ensemble Dashboard")
        self.ot = self.project("Option Trading")
        self.page = True

    def project(self, name: str) -> str:
        ok, proj, _ = dashboard.register_project(name)
        self.assertTrue(ok)
        return proj["id"]

    def home(self, pid: str) -> Path:
        return Path(dashboard.project_home(dashboard.find_project(pid), create=False))

    def meta(self, pid: str) -> dict:
        return json.loads((self.home(pid) / "project.json").read_text(encoding="utf-8"))

    def call(self, method, path, body=None, cookie=True, site="same-origin"):
        raw = json.dumps(body).encode() if body is not None else b""
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                     "Host": f"127.0.0.1:{PORT}", "Sec-Fetch-Site": site}
        if cookie:
            h.headers["Cookie"] = f"ensemble_ui_{PORT}={dashboard._UI_KEY}"
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        h._page_refusal = lambda: "" if self.page else "Only the dashboard page can do this."
        (h.do_POST if method == "POST" else h.do_GET)()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        hdrs = dict(ln.split(": ", 1) for ln in head.decode("latin-1").split("\r\n")[1:] if ": " in ln)
        return int(head.split(b" ", 2)[1]), hdrs, payload


class Rename(Hub):
    def rename(self, pid, name):
        st, _, body = self.call("POST", "/api/projects/rename", {"projectId": pid, "name": name})
        return st, json.loads(body)

    def test_a_rename_changes_the_name_and_nothing_else(self):
        before = dashboard.find_project(self.ed)
        key = dashboard.project_keys()[self.ed]
        st, body = self.rename(self.ed, "  Harbour   Lights ")
        self.assertEqual((st, body["name"]), (200, "Harbour Lights"))
        after = dashboard.find_project(self.ed)
        self.assertEqual(after["name"], "Harbour Lights")
        self.assertEqual((after["id"], after["path"], after["home"]), (before["id"], before["path"], before["home"]))
        self.assertEqual(dashboard.project_keys()[self.ed], key, "the key stays")
        self.assertTrue(self.home(self.ed).is_dir(), "the folder is not moved")
        names = [g["name"] for g in dashboard.build_projects()["projects"]]
        self.assertIn("Harbour Lights", names)
        self.assertNotIn("Ensemble Dashboard", names)

    def test_empty_duplicate_and_unknown_are_refused(self):
        st, body = self.rename(self.ed, "   ")
        self.assertEqual((st, body["error"]), (400, "empty_name"))
        st, body = self.rename(self.ed, "option TRADING")
        self.assertEqual((st, body["error"]), (400, "name_taken"))
        self.assertIn("already called", body["message"])
        st, body = self.rename("proj-nope", "Fine")
        self.assertEqual((st, body["error"]), (404, "no_such_project"))
        self.assertEqual(dashboard.find_project(self.ed)["name"], "Ensemble Dashboard")
        self.assertEqual(self.rename(self.ed, "ensemble dashboard")[0], 200, "its own name in another case is fine")

    def test_the_inbox_has_the_new_name_at_once(self):
        import inbox
        inbox.snapshot()                     # a fresh build is kept a while
        self.rename(self.ed, "Harbour Lights")
        with mock.patch.object(inbox, "_build_all", wraps=inbox._build_all) as b:
            inbox.snapshot()
        self.assertEqual(b.call_count, 1, "a rename forgets the kept build")

    def test_a_build_begun_before_the_rename_is_not_kept(self):
        import threading
        import inbox
        inbox.forget()
        real, started, release = inbox._build_all, threading.Event(), threading.Event()

        def slow(*a, **k):
            out = real(*a, **k)              # read before the rename: the old name
            started.set()
            release.wait(5)
            return out
        with mock.patch.object(inbox, "_build_all", side_effect=slow):
            t = threading.Thread(target=inbox.snapshot)
            t.start()
            self.assertTrue(started.wait(5))
            self.rename(self.ed, "Harbour Lights")   # forget() while that build runs
            release.set()
            t.join(5)
        self.assertNotIn(False, inbox._CACHE, "the pre-rename build is not kept")
        with mock.patch.object(inbox, "_build_all", wraps=inbox._build_all) as b:
            inbox.snapshot()
        self.assertEqual(b.call_count, 1, "the next read builds with the new name")

    def test_only_the_page_renames(self):
        self.page = False
        st, body = self.rename(self.ed, "Other")
        self.assertEqual((st, body["error"]), (403, "page_only"))
        self.assertEqual(dashboard.find_project(self.ed)["name"], "Ensemble Dashboard")

    def test_the_po_is_told_once_without_a_wake(self):
        rid = chatroom.create_room("Ensemble Dashboard PO",
                                   [{"identity": "claude", "agent": "claude", "model": "", "role": "po"}])["id"]
        dashboard._set_project_meta(self.ed, "poRoomId", rid)
        self.assertEqual(self.rename(self.ed, "Harbour")[0], 200)
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["title"], "Harbour PO")
        told = [m for m in room["messages"] if m.get("reportKind") == "renamed"]
        self.assertEqual(len(told), 1)
        self.assertIn("now called \u201cHarbour\u201d (was \u201cEnsemble Dashboard\u201d)", told[0]["text"])
        self.assertEqual(told[0]["rang"], [], "it rides the PO's next wake")
        self.assertEqual(len(quiet_reports._load().get(rid, [])), 1)
        self.assertEqual(self.rename(self.ed, "Harbour")[0], 200)
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(len([m for m in room["messages"] if m.get("reportKind") == "renamed"]), 1,
                         "the same name again tells nobody")


class Icon(Hub):
    def upload(self, raw, mime="image/png", name="logo.png", pid=None):
        st, _, body = self.call("POST", "/api/projects/icon",
                                {"projectId": pid or self.ed, "dataUrl": data_url(raw, mime), "name": name})
        return st, json.loads(body)

    def get(self, **kw):
        return self.call("GET", f"/api/projects/icon?id={self.ed}&v=1", **kw)

    def test_each_type_is_kept_beside_project_json(self):
        for raw, mime, ext in ((PNG, "image/png", "png"), (JPG, "image/jpeg", "jpg"),
                               (WEBP, "image/webp", "webp"), (SVG, "image/svg+xml", "svg")):
            st, body = self.upload(raw, mime, name=f"C:\\pics\\logo.{ext}")
            self.assertEqual(st, 200, (ext, body))
            self.assertEqual(body["icon"]["file"], f"project-icon.{ext}")
            self.assertEqual(body["icon"]["name"], f"logo.{ext}")
            self.assertEqual(body["icon"]["size"], len(raw))
            self.assertEqual(self.meta(self.ed)["icon"]["file"], f"project-icon.{ext}")
            files = sorted(f.name for f in self.home(self.ed).glob("project-icon.*"))
            self.assertEqual(files, [f"project-icon.{ext}"], "a replaced icon leaves no other file")
            self.assertEqual(dashboard.find_project(self.ed)["icon"]["file"], f"project-icon.{ext}")
            group = next(g for g in dashboard.build_projects()["projects"] if g["id"] == self.ed)
            self.assertEqual(group["icon"]["file"], f"project-icon.{ext}")
            st, hdrs, payload = self.get()
            self.assertEqual((st, payload), (200, raw))
            self.assertEqual(hdrs["Content-Type"], project_icon.TYPES[ext])
            self.assertEqual(hdrs["X-Content-Type-Options"], "nosniff")
            self.assertIn("default-src 'none'", hdrs["Content-Security-Policy"])

    def test_the_type_is_what_the_bytes_are(self):
        st, body = self.upload(PNG, "image/svg+xml", name="lie.svg")
        self.assertEqual((st, body["icon"]["file"]), (200, "project-icon.png"))
        st, body = self.upload(b"hello, not an image", "image/png")
        self.assertEqual((st, body["error"]), (400, "bad_type"))
        st, body = self.upload(b"GIF89a" + b"\x00" * 20, "image/gif")
        self.assertEqual((st, body["error"]), (400, "bad_type"))

    def test_too_big_is_refused(self):
        st, body = self.upload(PNG + b"\x00" * project_icon.MAX_BYTES)
        self.assertEqual((st, body["error"]), (413, "too_big"))
        self.assertIn("1 MB", body["message"])
        self.assertNotIn("icon", self.meta(self.ed))
        st, _, body = self.call("POST", "/api/projects/icon",
                                {"projectId": self.ed, "dataUrl": "x" * (project_icon.MAX_BODY + 10)})
        self.assertEqual((st, json.loads(body)["error"]), (413, "too_big"), "a body past the cap is not read")

    def test_an_unsafe_svg_is_refused(self):
        ns = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        for bad in (f'<svg {ns}><script>alert(1)</script></svg>',
                    f'<svg {ns} onload="alert(1)"/>',
                    f'<svg {ns}><a href="javascript:alert(1)"><rect/></a></svg>',
                    f'<svg {ns}><image xlink:href="https://example.com/x.png"/></svg>',
                    f'<svg {ns}><foreignObject><div/></foreignObject></svg>',
                    f'<svg {ns}><style>@import url(https://example.com/a.css);</style></svg>',
                    f'<svg {ns}><rect style="fill:url(https://example.com/#a)"/></svg>',
                    f'<svg {ns}><use href="https://example.com/s.svg#a"/></svg>',
                    f'<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg {ns}><text>&a;</text></svg>',
                    # review 1: forms the url() check alone did not see
                    f'<svg {ns} xml:base="https://example.com/"><image href="#x"/></svg>',
                    f'<svg {ns}><rect style="fill:u\\72l(https://example.com/a.svg#b)"/></svg>',
                    f'<svg {ns}><rect fill="u\\72l(https://example.com/a.svg#b)"/></svg>',
                    f'<svg {ns}><style>@\\69mport "https://example.com/a.css";</style></svg>',
                    f'<svg {ns}><style>@font-face {{ font-family: x; src: local(x) }}</style></svg>',
                    f'<svg {ns}><rect style="fill: image-set(\'https://example.com/a.png\' 1x)"/></svg>'):
            st, body = self.upload(bad.encode(), "image/svg+xml", name="x.svg")
            self.assertEqual((st, body["error"]), (400, "unsafe_svg"), bad)
        st, body = self.upload(f'<svg {ns}><defs><linearGradient id="g"/></defs><style>.a {{ fill: url(#g); font-family: "Inter" }}</style>'
                               f'<rect class="a" fill="url(#g)" width="4" height="4"/><use href="#g"/></svg>'.encode(),
                               "image/svg+xml", name="ok.svg")
        self.assertEqual(st, 200, "local references are fine")
        st, body = self.upload(b"<svg><rect></svg>", "image/svg+xml", name="broken.svg")
        self.assertEqual((st, body["error"]), (400, "bad_svg"))

    def test_remove_brings_the_square_back(self):
        self.assertEqual(self.upload(PNG)[0], 200)
        st, _, body = self.call("POST", "/api/projects/icon", {"projectId": self.ed, "dataUrl": None})
        self.assertEqual(st, 200)
        self.assertNotIn("icon", self.meta(self.ed))
        self.assertNotIn("icon", dashboard.find_project(self.ed))
        self.assertEqual(list(self.home(self.ed).glob("project-icon.*")), [])
        self.assertEqual(self.get()[0], 404)

    def test_served_only_with_the_page_key(self):
        self.assertEqual(self.upload(PNG)[0], 200)
        self.assertEqual(self.get()[0], 200)
        self.assertEqual(self.get(cookie=False)[0], 403)
        self.assertEqual(self.get(site="cross-site")[0], 403)
        self.assertEqual(self.get(site="same-site")[0], 403)
        self.page = False
        st, body = self.upload(JPG, "image/jpeg")
        self.assertEqual((st, body["error"]), (403, "page_only"))
        self.assertEqual(self.meta(self.ed)["icon"]["file"], "project-icon.png")

    def test_unknown_project(self):
        st, body = self.upload(PNG, pid="proj-nope")
        self.assertEqual((st, body["error"]), (404, "no_such_project"))


if __name__ == "__main__":
    unittest.main()
