"""A rendered HTML file's own pictures and styles (CEO point P67): opening a
page in the file view used to show every relative <img>/<link>/url() broken,
because fileview.html's renderPage() sets ifr.srcdoc = text, whose base URL is
about:srcdoc — nothing relative resolves anywhere.

* dashboard.py's resolve_file_at: serves a file inside a Windows-drive or a
  POSIX-absolute folder, refuses a literal "." or ".." segment — including
  one hidden behind a percent-encoded "/" or "\" inside a single URL segment
  (a browser resolving a <base href>'s "../x" already collapses it before the
  request is sent — only a crafted request would carry the dots themselves) —
  refuses a folder outside workspace_access_ok's allowed roots, and 404s a
  missing file;
* file_at_mime: a stylesheet gets text/css with a charset, a picture its own
  image type;
* the route through the hub's handler: the same token gate as /api/file (a
  local request needs none; a proxied/tailnet one does), a real 200 with the
  file's bytes, and the CSP sandbox + nosniff headers that keep an .html file
  under this route from running as a same-origin document if opened directly;
* fileview.html's fileAtBase + withFileAtBase + frameAnchorTarget (run in
  Node, skipped without it): the base href built for a Windows and a POSIX
  folder, where the <base> tag lands — right after <head> (not a <header>
  element), or after a doctype when there is none, so it wins over everything
  the srcdoc fetches — and the element an in-page "#id"/"#name"/"#" link
  targets now that the <base> makes the browser treat it as cross-document
  instead of just scrolling.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402

PAGE = (ROOT / "fileview.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
PORT = 8798
TOKEN = "t0ken-for-the-test"
PROXIED = {"X-Forwarded-For": "100.64.0.7"}   # a tailnet request through tailscale serve


def js_function(name: str) -> str:
    m = re.search(rf"^[ \t]*function {re.escape(name)}\(", PAGE, re.M)
    i = m.start()
    j = PAGE.index("{", i)
    depth = 0
    k = j
    while True:
        c = PAGE[k]
        depth += (c == "{") - (c == "}")
        k += 1
        if depth == 0:
            break
    return PAGE[i:k]


def at_tail(style: str, segments: list[str]) -> str:
    return "/".join([style] + [quote(s, safe="") for s in segments])


class ResolveFileAt(unittest.TestCase):
    """Unit-level: resolve_file_at against a real temp folder standing in for
    a registered project's home (workspace_access_ok always allows anything
    under PROJECTS_ROOT, project or not)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        self.addCleanup(self.tmp.cleanup)
        for patch in (
            mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = self.root / "Motors" / "Screen layout"
        self.folder.mkdir(parents=True)
        (self.folder / "style.css").write_text("body { color: red; }", encoding="utf-8")
        (self.folder / "today").mkdir()
        (self.folder / "today" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        # Windows drive segments, mirroring what fileview.html's fileAtBase builds.
        self.segs = [self.folder.drive] + list(self.folder.parts[1:])

    def test_serves_a_file_in_the_folder(self):
        fp = dashboard.resolve_file_at(at_tail("w", self.segs + ["style.css"]))
        self.assertEqual(fp, self.folder / "style.css")
        fp = dashboard.resolve_file_at(at_tail("w", self.segs + ["today", "pic.png"]))
        self.assertEqual(fp, self.folder / "today" / "pic.png")

    def test_a_missing_file_is_none(self):
        self.assertIsNone(dashboard.resolve_file_at(at_tail("w", self.segs + ["nope.css"])))

    def test_refuses_a_dotdot_or_dot_segment(self):
        for bad in (
            self.segs + ["..", "style.css"],
            self.segs + ["today", "..", "style.css"],
            self.segs + [".", "style.css"],
            [".."] + self.segs + ["style.css"],
        ):
            self.assertIsNone(dashboard.resolve_file_at(at_tail("w", bad)), bad)

    def test_refuses_a_dot_segment_hidden_behind_an_encoded_separator(self):
        # "today%2F.." decodes to one segment "today/.." — the plain dot
        # check never sees a bare ".." unless it's split on the separator
        # first, the way an unencoded URL segment already would be.
        for bad in (
            self.segs + ["today/..", "style.css"],
            self.segs + ["today\\..\\..", "style.css"],
        ):
            self.assertIsNone(dashboard.resolve_file_at(at_tail("w", bad)), bad)

    def test_refuses_a_folder_outside_allowed_roots(self):
        with tempfile.TemporaryDirectory() as outside:
            op = Path(outside)
            (op / "x.css").write_text("a", encoding="utf-8")
            segs = [op.drive] + list(op.parts[1:])
            self.assertIsNone(dashboard.resolve_file_at(at_tail("w", segs + ["x.css"])))

    def test_refuses_a_bad_or_missing_style_or_drive(self):
        self.assertIsNone(dashboard.resolve_file_at(at_tail("x", self.segs + ["style.css"])))
        self.assertIsNone(dashboard.resolve_file_at("w/style.css"))
        self.assertIsNone(dashboard.resolve_file_at(at_tail("w", ["NotADrive"] + self.segs[1:] + ["style.css"])))
        self.assertIsNone(dashboard.resolve_file_at(at_tail("w", self.segs)))   # no relative part at all
        self.assertIsNone(dashboard.resolve_file_at("w"))

    def test_a_posix_style_folder_is_reachable_through_an_allowed_root(self):
        # Not a real POSIX machine here, but the parsing + workspace_access_ok
        # gate is exercised the same way; the final existence check is real.
        with mock.patch.object(dashboard, "workspace_access_ok", return_value=True), \
             mock.patch.object(Path, "is_file", return_value=True):
            fp = dashboard.resolve_file_at(at_tail("p", ["home", "alex", "proj", "style.css"]))
            self.assertIsNotNone(fp)
            self.assertTrue(str(fp).replace("\\", "/").endswith("home/alex/proj/style.css"), fp)


class FileAtMime(unittest.TestCase):
    def test_a_stylesheet_gets_a_charset(self):
        self.assertEqual(dashboard.file_at_mime(Path("x/style.css")), "text/css; charset=utf-8")

    def test_a_picture_keeps_its_own_type_with_no_charset(self):
        self.assertEqual(dashboard.file_at_mime(Path("x/pic.png")), "image/png")

    def test_an_unknown_extension_is_a_generic_stream(self):
        self.assertEqual(dashboard.file_at_mime(Path("x/thing.zzzz")), "application/octet-stream")


class FileAtRoute(unittest.TestCase):
    """Through the hub's handler: the route, and the same token gate as
    /api/file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        self.addCleanup(self.tmp.cleanup)
        for patch in (
            mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = self.root / "Motors" / "Screen layout"
        self.folder.mkdir(parents=True)
        (self.folder / "style.css").write_text("body { color: red; }", encoding="utf-8")
        self.segs = [self.folder.drive] + list(self.folder.parts[1:])

    def get(self, path, headers=None):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
        h.requestline = f"GET {path} HTTP/1.1"
        h.headers = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        h.do_GET()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        hdrs = dict(ln.split(": ", 1) for ln in head.decode("latin-1").split("\r\n")[1:] if ": " in ln)
        return status, hdrs, payload

    def path(self, *rel):
        return "/api/file-at/" + at_tail("w", self.segs + list(rel))

    def test_a_local_request_serves_the_file_with_its_content_type(self):
        status, hdrs, body = self.get(self.path("style.css"))
        self.assertEqual(status, 200)
        self.assertEqual(hdrs["Content-Type"], "text/css; charset=utf-8")
        self.assertEqual(body, b"body { color: red; }")

    def test_response_carries_a_sandbox_csp_and_nosniff(self):
        # Unlike /api/file, this route can be opened directly (a pasted link,
        # a bookmark); an .html file under it must not run as a same-origin
        # document with the hub's own APIs and token cookie. CSP sandbox is
        # ignored for non-document responses (images, CSS), so it's cheap to
        # send on every response rather than branch on the file's type.
        _, hdrs, _ = self.get(self.path("style.css"))
        self.assertEqual(hdrs["Content-Security-Policy"], "sandbox")
        self.assertEqual(hdrs["X-Content-Type-Options"], "nosniff")

    def test_a_missing_file_is_404(self):
        status, _, _ = self.get(self.path("nope.css"))
        self.assertEqual(status, 404)

    def test_a_dotdot_segment_is_404_not_a_leak(self):
        status, _, _ = self.get("/api/file-at/" + at_tail("w", self.segs + ["..", "..", "elsewhere"]))
        self.assertEqual(status, 404)

    def test_same_token_gate_as_api_file(self):
        # Same gate dashboard.py._gate() applies to every route: no token, a
        # proxied (tailnet) request is refused; a header token passes; a
        # query token is swapped for a cookie by redirect, same as /api/file.
        with mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN):
            api_file = "/api/file?path=" + quote(str(self.folder / "style.css"))
            at = self.path("style.css")
            for target, sep in ((api_file, "&"), (at, "?")):
                self.assertEqual(self.get(target, PROXIED)[0], 401, target)
                self.assertEqual(self.get(target, {**PROXIED, "X-Ensemble-Token": TOKEN})[0], 200, target)
                status, hdrs, _ = self.get(f"{target}{sep}token={TOKEN}", PROXIED)
                self.assertEqual(status, 303, target)
                self.assertIn(f"ensemble_token={TOKEN}", hdrs.get("Set-Cookie", ""), target)


@unittest.skipUnless(NODE, "node is not installed")
class FileAtBaseHref(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = (js_function("fileAtBase") + "\n" + js_function("withFileAtBase") + "\n"
                + js_function("frameAnchorTarget"))
        script = f"""
{code}
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {{}};
for (const [k, c] of Object.entries(cases)) {{
  if (c.kind === 'base') out[k] = fileAtBase(c.folder);
  else if (c.kind === 'inject') out[k] = withFileAtBase(c.html, c.base);
  else {{
    const d = {{
      body: 'BODY', documentElement: 'DOCELEM',
      getElementById: id => c.ids[id] || null,
      getElementsByName: name => c.names[name] ? [c.names[name]] : [],
    }};
    out[k] = frameAnchorTarget(d, c.href);
  }}
}}
console.log(JSON.stringify(out));
"""
        cases = {
            "win": {"kind": "base", "folder": "C:\\Work\\alex\\EnsembleProjects\\Ensemble Dashboard\\Documents\\Screen layout"},
            "posix": {"kind": "base", "folder": "/home/alex/project/docs"},
            "unc": {"kind": "base", "folder": "\\\\server\\share\\folder"},
            "empty": {"kind": "base", "folder": ""},
            "withHead": {"kind": "inject", "html": "<!doctype html>\n<html><head><title>t</title></head><body>hi</body></html>",
                        "base": "/api/file-at/w/C%3A/x/"},
            "noHead": {"kind": "inject", "html": "<!doctype html>\n<p>hi</p>", "base": "/api/file-at/w/C%3A/x/"},
            "noDoctypeNoHead": {"kind": "inject", "html": "<p>hi</p>", "base": "/api/file-at/w/C%3A/x/"},
            "noBase": {"kind": "inject", "html": "<!doctype html>\n<p>hi</p>", "base": ""},
            "headerNotHead": {"kind": "inject", "html": "<!doctype html>\n<body><header>hi</header></body>",
                              "base": "/api/file-at/w/C%3A/x/"},
            "anchorById": {"kind": "anchor", "href": "#t", "ids": {"t": "ELEM_T"}, "names": {}},
            "anchorByName": {"kind": "anchor", "href": "#a%20b", "ids": {}, "names": {"a b": "ELEM_NAME"}},
            "anchorEmpty": {"kind": "anchor", "href": "#", "ids": {}, "names": {}},
            "anchorMissing": {"kind": "anchor", "href": "#nope", "ids": {}, "names": {}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.cjs"
            f.write_text(script, encoding="utf-8")
            proc = subprocess.run([NODE, str(f)], input=json.dumps(cases), capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.out = json.loads(proc.stdout)

    def test_a_windows_folder_becomes_a_w_style_base(self):
        self.assertEqual(self.out["win"],
                         "/api/file-at/w/C%3A/Work/alex/EnsembleProjects/Ensemble%20Dashboard/Documents/Screen%20layout/")

    def test_a_posix_folder_becomes_a_p_style_base(self):
        self.assertEqual(self.out["posix"], "/api/file-at/p/home/alex/project/docs/")

    def test_a_unc_or_empty_folder_gets_no_base(self):
        self.assertEqual(self.out["unc"], "")
        self.assertEqual(self.out["empty"], "")

    def test_the_base_tag_lands_right_after_head(self):
        self.assertIn('<head><base href="/api/file-at/w/C%3A/x/"><title>t</title></head>', self.out["withHead"])

    def test_with_no_head_it_lands_after_the_doctype(self):
        self.assertTrue(self.out["noHead"].startswith('<!doctype html><base href="/api/file-at/w/C%3A/x/">'), self.out["noHead"])

    def test_a_header_element_is_not_mistaken_for_head(self):
        # <header> starts with "head" too; the regex needs a real boundary
        # (a space or ">") right after it, or the <base> would land inside a
        # visible <header>, before any <link> that precedes it.
        self.assertTrue(self.out["headerNotHead"].startswith('<!doctype html><base href="/api/file-at/w/C%3A/x/">'),
                        self.out["headerNotHead"])

    def test_anchor_target_by_id(self):
        self.assertEqual(self.out["anchorById"], "ELEM_T")

    def test_anchor_target_by_name_with_decoded_href(self):
        self.assertEqual(self.out["anchorByName"], "ELEM_NAME")

    def test_bare_hash_targets_the_body(self):
        self.assertEqual(self.out["anchorEmpty"], "BODY")

    def test_an_unmatched_anchor_target_is_null(self):
        self.assertIsNone(self.out["anchorMissing"])

    def test_with_no_doctype_and_no_head_it_lands_at_the_very_start(self):
        self.assertTrue(self.out["noDoctypeNoHead"].startswith('<base href="/api/file-at/w/C%3A/x/">'), self.out["noDoctypeNoHead"])

    def test_no_base_leaves_the_html_untouched(self):
        self.assertEqual(self.out["noBase"], "<!doctype html>\n<p>hi</p>")


if __name__ == "__main__":
    unittest.main()
