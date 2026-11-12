"""#339: updates without a banner. The hub's /api/update-check carries what an
update brings in plain words (merge subjects, or release notes), Skip this
version is stored on the hub, and the page has a chip, a menu item and a
dialog instead of the banner.

* plain_title: the merge prefix, task numbers, point numbers and hashes go;
* a seeded upstream: the first-parent subjects, newest first, capped at 20
  with "more", dates of the running and the newest commit;
* release notes: the newer releases only, newest first, plain;
* the check fetches once per cache window: ?wait=1 (the dialog opening)
  never fetches, and says how many agents Install would wait for;
* skip: stored in settings, honoured until a newer commit arrives, refused
  without an update, validated;
* the page: no #update-banner, the item, the scripts, PAGE_FILES.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_update  # noqa: E402
import dashboard  # noqa: E402
import update_notes  # noqa: E402


def git(cwd, *args, when=""):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    if when:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env).stdout.strip()


class PlainTitle(unittest.TestCase):
    def test_prefixes_refs_and_hashes_go(self):
        cases = {
            "Merge #328 (P294, P296): Projects above the Inbox in one left panel":
                "Projects above the Inbox in one left panel",
            "Merge #327 (P292 follow-up): Ensemble runs Dock v0.24.0; #326's wrapper goes":
                "Ensemble runs Dock v0.24.0; the wrapper goes",
            "Merge #12: x (#330, P306) y": "X y",
            "Merge fix: a thread link lands on the answer": "A thread link lands on the answer",
            "Merge round 2: a files check no longer re-walks": "A files check no longer re-walks",
            "Merge ED-12 (P1): reverted 2d8ec38 for now": "Reverted for now",
            "Fix the 2026 build and the facade": "Fix the 2026 build and the facade",
            "Dock v0.24.0 (P292)": "Dock v0.24.0",
        }
        for subject, want in cases.items():
            self.assertEqual(update_notes.plain_title(subject), want, subject)

    def test_notes_keep_their_markdown(self):
        got = update_notes.plain_notes("## What's new (#339)\n\n- Projects above the Inbox (P294)\n* Fixed in 2d8ec38\n")
        self.assertEqual(got, "## What's new\n\n- Projects above the Inbox\n* Fixed")

    def test_ref_runs_go_with_their_lead_words(self):
        cases = {
            "Fix #12/#13; see P4": "Fix",
            "Fix #14, #15, and #16 in ED-3": "Fix",
            "Fix (P1) and #2 or #3": "Fix",
            "Fix this, and #4.": "Fix this.",
            "#326's wrapper keeps the port after #330": "The wrapper keeps the port",
            "Bump to aceeb60 and fix the cafe": "Bump and fix the cafe",
            "Log in and sign on": "Log in and sign on",
            "Show 12 items and 3.5 MB in 2026 for P2P": "Show 12 items and 3.5 MB in 2026 for P2P",
            "Read https://x.org/a#12 and P4 now": "Read https://x.org/a#12 now",
        }
        for subject, want in cases.items():
            got = update_notes.plain_title(subject)
            self.assertEqual(got, want, subject)
            self.assertNotRegex(got.replace("https://x.org/a#12", ""), r"#\d|\bP\d+\b|ED-\d")

    def test_notes_keep_links_and_clean_their_labels(self):
        got = update_notes.plain_notes("- [Fix ED-12](https://x/y/12) now\n- [#12](https://x/12)\n- Plain #9 ![a](i.png)")
        self.assertEqual(got, "- [Fix](https://x/y/12) now\n- Plain ![a](i.png)")


class Seeded(unittest.TestCase):
    """An upstream with merges and plain commits; the hub's checkout behind it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.up, self.co = base / "up", base / "co"
        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "main")
        (self.up / "f").write_text("0")
        git(self.up, "add", "f")
        git(self.up, "commit", "-q", "-m", "start", when="2026-11-03T10:00:00")
        git(base, "clone", "-q", str(self.up), str(self.co))
        dashboard._UPDATE_CHECK_CACHE = None
        self.settings = base / "settings.json"
        for p in (mock.patch.object(dashboard, "STATIC_DIR", self.co),
                  mock.patch.object(dashboard, "SETTINGS_FILE", self.settings),
                  mock.patch.object(dashboard.app_version, "packaged", return_value=False),
                  mock.patch.object(dashboard, "source_update_keeps_host", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, dashboard, "_UPDATE_CHECK_CACHE", None)

    def merge(self, n, text, when="2026-11-11T10:00:00"):
        git(self.up, "checkout", "-q", "-b", f"t{n}")
        (self.up / f"t{n}").write_text(text)
        git(self.up, "add", ".")
        git(self.up, "commit", "-q", "-m", f"#{n} WIP: inside commit abc1234")
        git(self.up, "checkout", "-q", "main")
        env_date = {"GIT_COMMITTER_DATE": when}
        subprocess.run(["git", "merge", "-q", "--no-ff", f"t{n}", "-m", f"Merge #{n} (P{n}): {text}"],
                       cwd=self.up, check=True, capture_output=True,
                       env={**os.environ, **env_date, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


class GitList(Seeded):
    def test_first_parent_subjects_newest_first_plain(self):
        self.merge(1, "the first feature")
        (self.up / "g").write_text("x")
        git(self.up, "add", "g")
        git(self.up, "commit", "-q", "-m", "A direct fix on main")
        self.merge(2, "the second feature")
        info = dashboard.check_for_update(force=True)
        self.assertTrue(info["available"], info)
        self.assertEqual([i["text"] for i in info["items"]],
                         ["The second feature", "A direct fix on main", "The first feature"])
        self.assertEqual((info["updates"], info["more"]), (3, 0))
        self.assertEqual(info["currentDate"], int(git(self.co, "log", "-1", "--format=%ct", "HEAD")))
        self.assertGreater(info["latestDate"], info["currentDate"])
        self.assertEqual(info["latestId"], git(self.co, "rev-parse", "@{u}"))
        self.assertFalse(info["keepsHost"])
        blob = json.dumps(info["items"])
        self.assertNotIn("#", blob)
        self.assertNotIn("P1", blob)

    def test_capped_at_twenty_with_more(self):
        for n in range(1, 24):
            self.merge(n, f"feature {n}")
        info = dashboard.check_for_update(force=True)
        self.assertEqual(len(info["items"]), 20)
        self.assertEqual(info["items"][0]["text"], "Feature 23")
        self.assertEqual((info["updates"], info["more"]), (23, 3))

    def test_nothing_new_is_not_available(self):
        info = dashboard.check_for_update(force=True)
        self.assertFalse(info["available"])
        self.assertNotIn("items", info)


class ReleaseNotes(unittest.TestCase):
    def rel(self, tag, body, **kw):
        assets = [{"name": f"Ensemble-{tag[1:]}-windows-x64.zip", "browser_download_url": "https://x/z", "size": 1},
                  {"name": app_update.SUMS_NAME, "browser_download_url": "https://x/s"}]
        return {"tag_name": tag, "name": tag, "body": body, "assets": assets,
                "published_at": "2026-11-10T09:00:00Z", **kw}

    def test_newer_releases_newest_first_plain(self):
        rels = [self.rel("v1.0.0", "old"), self.rel("v1.1.0", "- Projects above the Inbox (#328, P294)"),
                self.rel("v1.2.0", "## New\n- Fixed in 2d8ec38"), self.rel("v1.3.0", "draft", draft=True),
                self.rel("v1.4.0-beta.1", "pre", prerelease=True)]
        got = app_update.check("1.0.0", "windows-x64", fetch=lambda: rels)
        self.assertTrue(got["available"])
        self.assertEqual(got["latestId"], "1.2.0")
        self.assertEqual([n["version"] for n in got["notes"]], ["1.2.0", "1.1.0"])
        self.assertEqual(got["notes"][0]["body"], "## New\n- Fixed")
        self.assertEqual(got["notes"][1]["body"], "- Projects above the Inbox")
        self.assertEqual(got["latestDate"], 1794301200)
        self.assertEqual(got["updates"], 2)
        self.assertEqual(got["more"], 0)

    def test_only_up_to_the_release_install_installs(self):
        newest = self.rel("v1.2.0", "- Only on Mac")
        newest["assets"] = [{"name": "Ensemble-1.2.0-macos-arm64.zip"}, {"name": app_update.SUMS_NAME}]
        rels = [newest, self.rel("v1.1.0", "- For everyone"), self.rel("v1.0.5", "- Earlier")]
        got = app_update.check("1.0.0", "windows-x64", fetch=lambda: rels)
        self.assertEqual(got["latestVersion"], "1.1.0")
        self.assertEqual([n["version"] for n in got["notes"]], ["1.1.0", "1.0.5"])

    def test_a_long_release_list_says_how_many_more(self):
        rels = [self.rel(f"v1.{n}.0", f"- Change {n}") for n in range(1, 26)]
        got = app_update.check("1.0.0", "windows-x64", fetch=lambda: rels)
        self.assertEqual(len(got["notes"]), 20)
        self.assertEqual(got["notes"][0]["version"], "1.25.0")
        self.assertEqual((got["updates"], got["more"]), (25, 5))


class Endpoint(Seeded):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(dashboard.Handler, "_gate", return_value=True),
                  mock.patch.object(dashboard.Handler, "log_message"),
                  mock.patch.object(dashboard.Handler, "_restart_refusal", return_value="")):
            p.start()
            self.addCleanup(p.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.merge(1, "the first feature")

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_dialog_open_never_fetches_and_says_who_waits(self):
        real = dashboard._run
        fetches = []
        def run(cmd, *a, **kw):
            if "fetch" in cmd:
                fetches.append(cmd)
            return real(cmd, *a, **kw)
        seen = {"a": {"state": "working"}, "b": {"state": "idle", "background": True}, "c": {"state": "idle"}}
        with mock.patch.object(dashboard, "_run", side_effect=run), \
                mock.patch.object(dashboard.restart_drain, "observe_hub", return_value=seen) as obs, \
                mock.patch.object(dashboard.restart_drain, "holding", return_value=False):
            code, first = self.call("/api/update-check")
            self.assertEqual(code, 200)
            self.assertNotIn("wait", first)
            self.assertEqual(obs.call_count, 0)
            for _ in range(3):
                code, opened = self.call("/api/update-check?wait=1")
            self.assertEqual(len(fetches), 1, fetches)
            self.assertEqual(opened["wait"], {"agents": 2, "minutes": 20})
            self.assertEqual(opened["items"], first["items"])
            # A hub-only update waits for nobody.
            dashboard._UPDATE_CHECK_CACHE[1]["keepsHost"] = True
            self.assertEqual(self.call("/api/update-check?wait=1")[1]["wait"], {"agents": 0})

    def test_skip_holds_until_a_newer_commit(self):
        code, info = self.call("/api/update-check")
        self.assertFalse(info["skipped"])
        code, after = self.call("/api/update-skip", {"skip": True})
        self.assertEqual(code, 200, after)
        self.assertTrue(after["skipped"])
        self.assertEqual(json.loads(self.settings.read_text())["updateSkipped"], info["latestId"])
        # Another page (another device) reads the same.
        self.assertTrue(self.call("/api/update-check")[1]["skipped"])
        # A newer one: the chip comes back.
        self.merge(2, "the second feature")
        dashboard._UPDATE_CHECK_CACHE = None
        code, newer = self.call("/api/update-check")
        self.assertTrue(newer["available"])
        self.assertFalse(newer["skipped"])
        self.assertEqual(newer["items"][0]["text"], "The second feature")
        # {skip: false} clears it.
        self.call("/api/update-skip", {"skip": True})
        self.assertFalse(self.call("/api/update-skip", {"skip": False})[1]["skipped"])

    def test_skip_without_an_update_is_refused(self):
        git(self.co, "pull", "-q")
        dashboard._UPDATE_CHECK_CACHE = None
        self.assertFalse(self.call("/api/update-check")[1]["available"])
        code, body = self.call("/api/update-skip", {"skip": True})
        self.assertEqual(code, 409, body)

    def test_open_with_an_expired_or_cold_cache_never_checks(self):
        real = dashboard._run
        fetches = []
        def run(cmd, *a, **kw):
            if "fetch" in cmd:
                fetches.append(cmd)
            return real(cmd, *a, **kw)
        with mock.patch.object(dashboard, "_run", side_effect=run), \
                mock.patch.object(dashboard.restart_drain, "observe_hub", return_value={}), \
                mock.patch.object(dashboard.restart_drain, "holding", return_value=False):
            code, cold = self.call("/api/update-check?wait=1")
            self.assertEqual((code, cold["available"], fetches), (200, False, []))
            self.assertEqual(self.call("/api/update-skip", {"skip": True})[0], 409)
            first = self.call("/api/update-check")[1]
            self.assertEqual(len(fetches), 1)
            stamp, cached = dashboard._UPDATE_CHECK_CACHE
            dashboard._UPDATE_CHECK_CACHE = (stamp - 2 * dashboard._UPDATE_CHECK_TTL, cached)
            code, opened = self.call("/api/update-check?wait=1")
            self.assertEqual(opened["items"], first["items"])
            self.assertEqual(self.call("/api/update-skip", {"skip": True})[0], 200)
            self.assertEqual(len(fetches), 1, fetches)
            # The page's periodic poll still checks again once it has run out.
            self.call("/api/update-check")
            self.assertEqual(len(fetches), 2, fetches)

    def test_a_built_app_may_wait(self):
        seen = {"a": {"state": "working"}}
        with mock.patch.object(dashboard.app_version, "packaged", return_value=True), \
                mock.patch.object(dashboard.restart_drain, "observe_hub", return_value=seen), \
                mock.patch.object(dashboard.restart_drain, "holding", return_value=False):
            self.assertEqual(dashboard.update_wait(), {"agents": 1, "minutes": 20, "maybe": True})
            with mock.patch.object(dashboard.restart_drain, "observe_hub", return_value={}):
                self.assertEqual(dashboard.update_wait(), {"agents": 0, "minutes": 20, "maybe": True})

    def test_the_fallback_line_is_plain(self):
        view = dashboard.update_check_view({"available": True, "latestId": "x",
                                            "latestMessage": "Merge #12 (P3): the menu after #4"})
        self.assertEqual(view["latestMessage"], "The menu")

    def test_skip_needs_the_page_or_a_po(self):
        with mock.patch.object(dashboard.Handler, "_restart_refusal", return_value="no"):
            self.assertEqual(self.call("/api/update-skip", {"skip": True})[0], 403)

    def test_setting_is_validated(self):
        dashboard.save_settings({"updateSkipped": "bad value; rm"})
        self.assertEqual(dashboard.load_settings()["updateSkipped"], "")
        dashboard.save_settings({"updateSkipped": "1.2.0"})
        self.assertEqual(dashboard.load_settings()["updateSkipped"], "1.2.0")


class Page(unittest.TestCase):
    def test_no_banner_and_the_new_parts(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("update-banner", html)
        self.assertIn('id="me-whatsnew"', html)
        self.assertLess(html.index('id="me-whatsnew"'), html.index('data-me="feedback"'))
        self.assertIn('/static/whatsnew.js', html)
        self.assertIn('/static/whatsnew.css', html)
        for f in ("static/whatsnew.js", "static/whatsnew.css"):
            self.assertIn(f, dashboard.PAGE_FILES)
        js = (ROOT / "static" / "whatsnew.js").read_text(encoding="utf-8")
        # The page never names a hash or a task number: it shows dates.
        self.assertNotIn("latestSha", js)
        self.assertNotIn("currentSha", js)


if __name__ == "__main__":
    unittest.main()
