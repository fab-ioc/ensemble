"""The access token exists at every start, and a request that is not local
never passes the gate without it (ED-341, P315).

`tailscale serve --https=443 http://127.0.0.1:8765` exposes a hub that listens
on loopback only. Such a hub used to start with no token, and every gate let a
request through on an empty token, so the tailnet read /api/ptys freely.

Checked:

* _load_access_token: ENSEMBLE_TOKEN wins and writes nothing; otherwise the
  token in DASHBOARD_DIR/access-token.txt is used; otherwise a new one is
  minted and kept there (owner-only), and the next start loads the same one;
  a state dir that cannot be written still gives a token;
* main() loads it unconditionally, not only for a remote listener;
* with the token as a hub without --bind and without ENSEMBLE_TOKEN gets it,
  a request shaped like tailscale serve's (loopback peer, X-Forwarded-For /
  Tailscale-User-Login, Host epyc-32.tail4f8740.ts.net): /api/ptys is 401, a
  page navigation gets the sign-in page; the same request with ?token= is
  swapped for the cookie, and with the cookie passes; loopback without proxy
  headers passes with no token;
* a non-ASCII credential (?token=%C3%A9, a cookie, a bearer, a drag key)
  gets the normal 401 or sign-in page, and the handler logs no traceback:
  compare_digest on str raised TypeError for it;
* should the token still be empty, such a request is refused all the same
  (an empty cookie never matches an empty token), and local ones pass.

The token is never printed: assertions compare it, messages never hold it.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
from tests.test_app_install import Hub  # noqa: E402

HOST = "epyc-32.tail4f8740.ts.net"
SERVE = {"Host": HOST, "X-Forwarded-For": "100.64.0.7", "X-Forwarded-Proto": "https",
         "X-Forwarded-Host": HOST, "Tailscale-User-Login": "someone@example.com"}
NAV = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Accept": "text/html"}


class LoadingTheToken(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name) / ".ensemble"
        self.file = self.dir / "access-token.txt"
        p = mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ENSEMBLE_TOKEN", None)
        os.environ.pop("ENSEMBLE_PTY_PREFLIGHT", None)

    def test_a_new_token_is_minted_kept_and_loaded_again(self):
        tok = dashboard._load_access_token()
        self.assertGreaterEqual(len(tok), 24, "a token of some strength")
        self.assertEqual(self.file.read_text(encoding="utf-8"), tok, "kept for the next start")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.file.stat().st_mode) & 0o077, 0, "readable by the owner only")
        self.assertTrue(dashboard._load_access_token() == tok, "the next start loads the same token")

    def test_the_kept_token_is_used(self):
        self.dir.mkdir(parents=True)
        self.file.write_text("kept-one\n", encoding="utf-8")
        self.assertTrue(dashboard._load_access_token() == "kept-one")

    def test_an_empty_file_gets_a_new_token(self):
        self.dir.mkdir(parents=True)
        self.file.write_text("  \n", encoding="utf-8")
        tok = dashboard._load_access_token()
        self.assertTrue(tok and self.file.read_text(encoding="utf-8") == tok)

    def test_a_preflight_start_writes_nothing(self):
        os.environ["ENSEMBLE_PTY_PREFLIGHT"] = "1"
        self.assertTrue(dashboard._load_access_token())
        self.assertFalse(self.file.exists(), "the preflight leaves the state as it was")
        if os.name == "posix":
            self.dir.mkdir(parents=True)
            self.file.write_text("kept-one", encoding="utf-8")
            os.chmod(self.file, 0o644)
            self.assertTrue(dashboard._load_access_token() == "kept-one")
            self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o644)

    def test_ensemble_token_wins_and_writes_nothing(self):
        self.dir.mkdir(parents=True)
        self.file.write_text("kept-one", encoding="utf-8")
        os.environ["ENSEMBLE_TOKEN"] = " from-the-env "
        self.assertTrue(dashboard._load_access_token() == "from-the-env")
        self.assertTrue(self.file.read_text(encoding="utf-8") == "kept-one")

    def test_a_state_dir_that_cannot_be_written_still_gives_a_token(self):
        with mock.patch.object(dashboard.tempfile, "mkstemp", side_effect=PermissionError):
            self.assertTrue(dashboard._load_access_token())
        self.assertFalse(self.file.exists())

    @unittest.skipUnless(os.name == "posix", "file modes and links as on Linux and macOS")
    def test_an_empty_file_others_could_read_is_replaced_by_an_owner_only_one(self):
        self.dir.mkdir(parents=True)
        self.file.write_text("", encoding="utf-8")
        os.chmod(self.file, 0o644)
        tok = dashboard._load_access_token()
        self.assertTrue(self.file.read_text(encoding="utf-8") == tok)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["access-token.txt"], "no temporary file left")

    @unittest.skipUnless(os.name == "posix", "file modes and links as on Linux and macOS")
    def test_a_kept_token_others_could_read_is_made_owner_only(self):
        self.dir.mkdir(parents=True)
        self.file.write_text("kept-one", encoding="utf-8")
        os.chmod(self.file, 0o644)
        self.assertTrue(dashboard._load_access_token() == "kept-one")
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "file modes and links as on Linux and macOS")
    def test_a_link_is_replaced_and_its_target_left_alone(self):
        self.dir.mkdir(parents=True)
        target = self.dir.parent / "elsewhere.txt"
        target.write_text("", encoding="utf-8")
        os.chmod(target, 0o644)
        self.file.symlink_to(target)
        tok = dashboard._load_access_token()
        self.assertFalse(self.file.is_symlink(), "the link itself is replaced")
        self.assertTrue(self.file.read_text(encoding="utf-8") == tok)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(target.read_text(encoding="utf-8"), "", "the target is not written")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644, "nor its mode changed")

    def test_main_loads_it_at_every_start(self):
        src = inspect.getsource(dashboard.main)
        line = next(l for l in src.splitlines() if "ACCESS_TOKEN = " in l)
        self.assertEqual(line, "    ACCESS_TOKEN = _load_access_token()",
                         "at the top level of main, under no condition")
        self.assertNotIn("want_remote", src)


class TheGateBehindTailscaleServe(unittest.TestCase):
    """The hub as systemd starts it: no --bind, no ENSEMBLE_TOKEN."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.td.cleanup)
        env = mock.patch.dict(os.environ)
        env.start()
        cls.addClassCleanup(env.stop)
        os.environ.pop("ENSEMBLE_TOKEN", None)
        os.environ.pop("ENSEMBLE_BIND", None)
        with mock.patch.object(dashboard, "DASHBOARD_DIR", Path(cls.td.name)):
            cls.token = dashboard._load_access_token()
        for p in (mock.patch.object(dashboard, "ACCESS_TOKEN", cls.token),
                  mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None)):
            p.start()
            cls.addClassCleanup(p.stop)
        cls.hub = Hub()
        cls.addClassCleanup(cls.hub.close)

    def test_the_tailnet_needs_the_token(self):
        for extra in (SERVE, {"Host": HOST}, {"X-Forwarded-For": "100.64.0.7"},
                      {"Tailscale-User-Login": "someone@example.com"}):
            code, _, body = self.hub.get("/api/ptys", extra)
            self.assertIn(code, (401, 403), sorted(extra))
            self.assertNotIn(self.token.encode(), body)

    def test_a_page_gets_the_sign_in_page(self):
        code, h, body = self.hub.get("/", {**SERVE, **NAV})
        self.assertEqual(code, 401)
        self.assertEqual(body, dashboard.SIGN_IN_PAGE)
        self.assertNotIn(self.token.encode(), body)
        self.assertIsNone(h.get("Set-Cookie"))

    def test_the_token_passes(self):
        code, h, _ = self.hub.get(f"/api/ptys?token={self.token}", SERVE)
        self.assertEqual(code, 303, "swapped for the cookie, the address loses it")
        self.assertTrue(h["Set-Cookie"].startswith(f"ensemble_token={self.token};"))
        self.assertEqual(h["Location"], "/api/ptys")
        self.assertEqual(self.hub.get("/api/ptys", {**SERVE, "Cookie": f"ensemble_token={self.token}"})[0], 200)
        self.assertEqual(self.hub.get("/api/ptys", {**SERVE, "X-Ensemble-Token": self.token})[0], 200)
        code, _, body = self.hub.get("/", {**SERVE, **NAV, "Cookie": f"ensemble_token={self.token}"})
        self.assertEqual(code, 200)
        self.assertNotEqual(body, dashboard.SIGN_IN_PAGE)

    def test_a_write_needs_the_token_too(self):
        """PUT went to its routes before any gate: /api/settings was saved
        through the proxy without the token."""
        put = lambda path, h: self.hub.get(path, {**SERVE, "Content-Type": "application/json", **h}, method="PUT")
        for path in ("/api/settings", "/api/backup/now"):
            self.assertEqual(put(path, {})[0], 401, path)
        with mock.patch.object(dashboard, "save_settings") as save:
            self.assertEqual(put("/api/settings", {})[0], 401)
            save.assert_not_called()
        for method in ("POST", "DELETE"):
            self.assertEqual(self.hub.get("/api/settings", SERVE, method=method)[0], 401, method)

    def test_a_wrong_token_does_not(self):
        self.assertEqual(self.hub.get("/api/ptys", {**SERVE, "Cookie": "ensemble_token=nope"})[0], 401)

    def test_this_machine_needs_none(self):
        self.assertEqual(self.hub.get("/api/ptys")[0], 200)
        self.assertEqual(self.hub.get("/api/ptys", {"Host": f"localhost:{self.hub.port}"})[0], 200)


class ANonAsciiTokenIsRefusedNotThrown(unittest.TestCase):
    """compare_digest on two str raises TypeError when either holds a non-ASCII
    character, so ?token=%C3%A9 threw in the handler and logged a traceback."""

    @classmethod
    def setUpClass(cls):
        for p in (mock.patch.object(dashboard, "ACCESS_TOKEN", "a-real-token-for-the-test"),
                  mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None)):
            p.start()
            cls.addClassCleanup(p.stop)
        cls.hub = Hub()
        cls.addClassCleanup(cls.hub.close)

    def _get(self, path, headers):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.hub.get(path, headers)
        self.assertNotIn("Traceback", err.getvalue(), path)
        self.assertNotIn("TypeError", err.getvalue(), path)
        return out

    def test_the_compare_takes_any_text(self):
        self.assertFalse(dashboard._same_secret("é", "a-real-token-for-the-test"))
        self.assertFalse(dashboard._same_secret("\udce9", "x"))
        self.assertFalse(dashboard._same_secret("", "x"))
        self.assertTrue(dashboard._same_secret("é", "é"))

    def test_each_way_of_presenting_it_gets_401(self):
        # http.client sends header values as latin-1; the server reads them back as "é".
        for path, extra in (("/api/ptys?token=%C3%A9", {}),
                            ("/api/ptys?token=%E9", {}),
                            ("/api/ptys", {"Cookie": "ensemble_token=\u00e9"}),
                            ("/api/ptys", {"X-Ensemble-Token": "\u00e9"}),
                            ("/api/ptys", {"Authorization": "Bearer \u00e9"}),
                            ("/api/file/download?drag=%C3%A9&path=x", {})):
            code, _, _ = self._get(path, {**SERVE, **extra})
            self.assertEqual(code, 401, (path, sorted(extra)))

    def test_a_page_gets_the_sign_in_page(self):
        code, _, body = self._get("/?token=%C3%A9", {**SERVE, **NAV})
        self.assertEqual(code, 401)
        self.assertEqual(body, dashboard.SIGN_IN_PAGE)
        code, _, body = self._get("/", {**SERVE, **NAV, "Cookie": "ensemble_token=\u00e9"})
        self.assertEqual(code, 401)
        self.assertEqual(body, dashboard.SIGN_IN_PAGE)

    def test_a_non_ascii_page_key_is_refused_too(self):
        """The page's key cookie is compared the same way (here on this machine)."""
        cookie = f"ensemble_ui_{self.hub.port}=\u00e9"
        code, _, _ = self._get("/api/projects/icon?id=x", {"Cookie": cookie})
        self.assertEqual(code, 403)

class AnEmptyTokenLetsNoOneIn(unittest.TestCase):
    """Belt and braces: should no token exist, the gate fails closed."""

    @classmethod
    def setUpClass(cls):
        for p in (mock.patch.object(dashboard, "ACCESS_TOKEN", ""),
                  mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None)):
            p.start()
            cls.addClassCleanup(p.stop)
        cls.hub = Hub()
        cls.addClassCleanup(cls.hub.close)

    def test_the_tailnet_is_refused(self):
        for extra in (SERVE, {**SERVE, "Cookie": "ensemble_token="}, {**SERVE, "X-Ensemble-Token": ""}):
            self.assertIn(self.hub.get("/api/ptys", extra)[0], (401, 403), sorted(extra))
        self.assertIn(self.hub.get("/api/ptys?token=", SERVE)[0], (401, 403))
        code, _, body = self.hub.get("/", {**SERVE, **NAV})
        self.assertEqual((code, body), (401, dashboard.SIGN_IN_PAGE))

    def test_a_write_is_refused(self):
        self.assertEqual(self.hub.get("/api/settings", SERVE, method="PUT")[0], 401)

    def test_the_drag_bearer_opens_nothing_either(self):
        drag = dashboard._FILE_DRAG_TOKEN
        self.assertEqual(self.hub.get(f"/api/file/download?path=x&drag={drag}", SERVE)[0], 401)

    def test_the_manifest_still_opens_and_this_machine_passes(self):
        self.assertEqual(self.hub.get("/manifest.webmanifest", SERVE)[0], 200)
        self.assertEqual(self.hub.get("/api/ptys")[0], 200)


if __name__ == "__main__":
    unittest.main()
