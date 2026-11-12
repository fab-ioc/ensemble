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
* should the token still be empty, such a request is refused all the same
  (an empty cookie never matches an empty token), and local ones pass.

The token is never printed: assertions compare it, messages never hold it.
"""
from __future__ import annotations

import inspect
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

    def test_ensemble_token_wins_and_writes_nothing(self):
        self.dir.mkdir(parents=True)
        self.file.write_text("kept-one", encoding="utf-8")
        os.environ["ENSEMBLE_TOKEN"] = " from-the-env "
        self.assertTrue(dashboard._load_access_token() == "from-the-env")
        self.assertTrue(self.file.read_text(encoding="utf-8") == "kept-one")

    def test_a_state_dir_that_cannot_be_written_still_gives_a_token(self):
        with mock.patch.object(dashboard.os, "open", side_effect=PermissionError):
            self.assertTrue(dashboard._load_access_token())

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

    def test_a_wrong_token_does_not(self):
        self.assertEqual(self.hub.get("/api/ptys", {**SERVE, "Cookie": "ensemble_token=nope"})[0], 401)

    def test_this_machine_needs_none(self):
        self.assertEqual(self.hub.get("/api/ptys")[0], 200)
        self.assertEqual(self.hub.get("/api/ptys", {"Host": f"localhost:{self.hub.port}"})[0], 200)


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

    def test_the_manifest_still_opens_and_this_machine_passes(self):
        self.assertEqual(self.hub.get("/manifest.webmanifest", SERVE)[0], 200)
        self.assertEqual(self.hub.get("/api/ptys")[0], 200)


if __name__ == "__main__":
    unittest.main()
