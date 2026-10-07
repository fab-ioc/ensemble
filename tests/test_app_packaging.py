"""The built app (#183): its version, the update from GitHub Releases (which
release, the checksum, the swap and its rollback), how the hub's scripts run
when built, and the first-run agent check."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app_setup  # noqa: E402
import app_update  # noqa: E402
import app_version  # noqa: E402


def _release(tag, names, draft=False, prerelease=False):
    return {"tag_name": tag, "name": f"Ensemble {tag}", "draft": draft,
            "prerelease": prerelease, "html_url": f"https://x/{tag}",
            "assets": [{"name": n, "browser_download_url": f"https://dl/{tag}/{n}", "size": 10}
                       for n in names]}


def _assets(version, key="windows-x64"):
    return [f"Ensemble-{version}-{key}.zip", "SHA256SUMS.txt"]


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(app_version.parse_version("v1.2.3"), (1, 2, 3, (1,)))
        self.assertEqual(app_version.parse_version("1.2.3"), (1, 2, 3, (1,)))
        self.assertIsNone(app_version.parse_version("1.2"))
        self.assertIsNone(app_version.parse_version("latest"))
        self.assertIsNone(app_version.parse_version(""))
        self.assertIsNotNone(app_version.parse_version(app_version.VERSION))

    def test_compare(self):
        self.assertTrue(app_version.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(app_version.is_newer("v1.10.0", "1.9.9"))
        self.assertFalse(app_version.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(app_version.is_newer("0.9.9", "1.0.0"))
        # A pre-release sorts before its release, after the one before.
        self.assertTrue(app_version.is_newer("1.0.0", "1.0.0-beta.1"))
        self.assertFalse(app_version.is_newer("1.0.0-beta.1", "1.0.0"))
        self.assertTrue(app_version.is_newer("1.0.0-beta.1", "0.9.0"))
        self.assertFalse(app_version.is_newer("junk", "1.0.0"))


class ScriptArgvTest(unittest.TestCase):
    def test_source_runs_the_script(self):
        argv = app_version.script_argv("agent_hook", Path("C:/r/agent_hook.py"), python="C:/py/python.exe",
                                       frozen=False)
        self.assertEqual(argv, ["C:/py/python.exe", str(Path("C:/r/agent_hook.py"))])

    def test_built_app_runs_itself(self):
        argv = app_version.script_argv("agent_hook", Path("C:/r/agent_hook.py"),
                                       python="C:/Program Files/Ensemble/Ensemble.exe", frozen=True)
        self.assertEqual(argv, ["C:/Program Files/Ensemble/Ensemble.exe", "--run", "agent_hook"])
        self.assertEqual(app_version.command_line(argv),
                         '"C:/Program Files/Ensemble/Ensemble.exe" --run "agent_hook"')

    def test_command_line_quotes_and_uses_forward_slashes(self):
        self.assertEqual(app_version.command_line([r"C:\a b\py.exe", r"C:\r\x.py"]),
                         '"C:/a b/py.exe" "C:/r/x.py"')

    def test_hub_script_command_unchanged_from_source(self):
        import dashboard
        path = Path(dashboard.__file__).resolve().parent / "agent_hook.py"
        self.assertFalse(app_version.packaged())
        self.assertEqual(dashboard._script_command("agent_hook", path),
                         f'"{Path(sys.executable).as_posix()}" "{path.as_posix()}"')

    def test_every_script_the_hub_starts_is_runnable_in_the_app(self):
        import ensemble_app
        for name in ("agent_hook", "task_tool_hook", "usage_statusline", "workspace_search",
                     "global_search", "app_update"):
            self.assertIn(name, ensemble_app.SCRIPTS)

    def test_unknown_script_exits_2(self):
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "ensemble_app.py"),
                            "--run", "nope"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown script", r.stderr)


class PickReleaseTest(unittest.TestCase):
    def test_newest_newer_release_with_both_assets(self):
        rels = [_release("v1.0.0", _assets("1.0.0")), _release("v1.2.0", _assets("1.2.0")),
                _release("v1.1.0", _assets("1.1.0"))]
        rel = app_update.pick_release(rels, "1.0.0", "windows-x64")
        self.assertEqual(rel["version"], "1.2.0")
        self.assertEqual(rel["assetName"], "Ensemble-1.2.0-windows-x64.zip")
        self.assertEqual(rel["sumsUrl"], "https://dl/v1.2.0/SHA256SUMS.txt")

    def test_skips_drafts_prereleases_and_incomplete(self):
        rels = [_release("v2.0.0", _assets("2.0.0"), draft=True),
                _release("v1.9.0", _assets("1.9.0"), prerelease=True),
                _release("v1.8.0", ["Ensemble-1.8.0-windows-x64.zip"]),          # no checksums
                _release("v1.7.0", _assets("1.7.0", "macos-universal")),         # other platform
                _release("nightly", _assets("nightly")),
                _release("v1.1.0", _assets("1.1.0"))]
        self.assertEqual(app_update.pick_release(rels, "1.0.0", "windows-x64")["version"], "1.1.0")

    def test_nothing_newer(self):
        rels = [_release("v1.0.0", _assets("1.0.0"))]
        self.assertIsNone(app_update.pick_release(rels, "1.0.0", "windows-x64"))
        self.assertIsNone(app_update.pick_release(rels, "0.1.0", None))
        self.assertIsNone(app_update.pick_release(None, "0.1.0", "windows-x64"))

    def test_check_shapes(self):
        rels = [_release("v9.0.0", _assets("9.0.0"))]
        got = app_update.check("1.0.0", "windows-x64", fetch=lambda: rels)
        self.assertTrue(got["available"])
        self.assertEqual(got["latestVersion"], "9.0.0")
        self.assertEqual(app_update.check("1.0.0", "", fetch=lambda: rels)["reason"],
                         "no_release_for_platform")

        def offline():
            raise OSError("no network")
        got = app_update.check("1.0.0", "windows-x64", fetch=offline)
        self.assertFalse(got["available"])
        self.assertTrue(got["reason"].startswith("error"))

    def test_platform_key(self):
        self.assertEqual(app_update.platform_key("win32", "AMD64"), "windows-x64")
        self.assertEqual(app_update.platform_key("win32", "ARM64"), "windows-x64")
        self.assertEqual(app_update.platform_key("darwin", "arm64"), "macos-universal")
        self.assertIsNone(app_update.platform_key("linux", "x86_64"))


class ChecksumTest(unittest.TestCase):
    def test_parse_sums(self):
        a, b = "a" * 64, "B" * 64
        got = app_update.parse_sums(f"{a}  Ensemble-1.0.0-windows-x64.zip\n{b} *x.dmg\n\nnot a line\n"
                                    f"{'c' * 10}  short\n")
        self.assertEqual(got, {"Ensemble-1.0.0-windows-x64.zip": a, "x.dmg": "b" * 64})

    def test_verify(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.zip"
            p.write_bytes(b"hello")
            digest = hashlib.sha256(b"hello").hexdigest()
            self.assertTrue(app_update.verify_sha256(p, digest))
            self.assertTrue(app_update.verify_sha256(p, digest.upper()))
            self.assertFalse(app_update.verify_sha256(p, "0" * 64))
            self.assertFalse(app_update.verify_sha256(p, None))
            self.assertFalse(app_update.verify_sha256(p, ""))


def _make_app(folder: Path, marker: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Ensemble.exe").write_text(marker)
    (folder / "_internal").mkdir(exist_ok=True)
    (folder / "_internal" / "lib.txt").write_text(marker)
    return folder


class StageTest(unittest.TestCase):
    def _zip(self, path: Path, members: dict) -> bytes:
        with zipfile.ZipFile(path, "w") as z:
            for name, data in members.items():
                z.writestr(name, data)
        return path.read_bytes()

    def test_stage_verifies_and_unpacks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "src.zip"
            data = self._zip(src, {"Ensemble/Ensemble.exe": "new", "Ensemble/_internal/lib.txt": "new"})
            rel = {"version": "2.0.0", "assetName": "Ensemble-2.0.0-windows-x64.zip",
                   "assetUrl": "u", "sumsUrl": "s"}
            sums = f"{hashlib.sha256(data).hexdigest()}  {rel['assetName']}\n".encode()

            def fetch(url, dest):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return dest
            app = app_update.stage(rel, d / "update", "win32", get=lambda u: sums, fetch=fetch)
            self.assertEqual((app / "Ensemble.exe").read_text(), "new")
            self.assertFalse((d / "update" / rel["assetName"]).exists())

            bad = f"{'0' * 64}  {rel['assetName']}\n".encode()
            with self.assertRaises(ValueError):
                app_update.stage(rel, d / "update", "win32", get=lambda u: bad, fetch=fetch)
            self.assertFalse((d / "update" / rel["assetName"]).exists())

    def test_unpack_refuses_paths_outside(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._zip(d / "evil.zip", {"../evil.txt": "x", "Ensemble/Ensemble.exe": "x"})
            with self.assertRaises(ValueError):
                app_update.unpack(d / "evil.zip", d / "out", "win32")
            self.assertFalse((d / "evil.txt").exists())


class SwapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.install = _make_app(self.d / "install", "old")
        (self.install / "unins000.exe").write_text("uninstaller")
        (self.install / "old_only.dll").write_text("old")
        self.new = _make_app(self.d / "new" / "Ensemble", "new")
        self.prev = self.install / app_update.PREVIOUS_DIR
        self.failed = self.install / app_update.FAILED_DIR

    def tearDown(self):
        self._tmp.cleanup()

    def _names(self):
        folder, new_names = app_update.app_entries(self.new, "win32")
        self.assertEqual(new_names, ["Ensemble.exe", "_internal"])
        install_folder, installed = app_update.app_entries(self.install, "win32")
        self.assertNotIn("unins000.exe", installed)
        return new_names, app_update.old_names(self.install, new_names, "win32")

    def test_swap_in_and_back(self):
        new_names, old = self._names()
        self.assertEqual(old, ["Ensemble.exe", "_internal"])
        app_update.swap_in(self.install, old, self.new, new_names, self.prev, tries=1, wait=0)
        self.assertEqual((self.install / "Ensemble.exe").read_text(), "new")
        self.assertEqual((self.prev / "Ensemble.exe").read_text(), "old")
        self.assertEqual((self.install / "unins000.exe").read_text(), "uninstaller")

        app_update.swap_back(self.install, new_names, self.prev, self.failed, tries=1, wait=0)
        self.assertEqual((self.install / "Ensemble.exe").read_text(), "old")
        self.assertEqual((self.install / "_internal" / "lib.txt").read_text(), "old")
        self.assertEqual((self.failed / "Ensemble.exe").read_text(), "new")

    def test_failure_mid_swap_puts_the_old_app_back(self):
        new_names, old = self._names()
        real = app_update._move
        calls = []

        def flaky(src, dst, tries=30, wait=1.0):
            calls.append((src, dst))
            # The old app is out; the new _internal will not move in.
            if Path(src) == self.new / "_internal":
                raise PermissionError("in use")
            real(src, dst, tries, wait)
        with mock.patch.object(app_update, "_move", flaky):
            with self.assertRaises(OSError):
                app_update.swap_in(self.install, old, self.new, new_names, self.prev, tries=1, wait=0)
        self.assertEqual((self.install / "Ensemble.exe").read_text(), "old")
        self.assertEqual((self.install / "_internal" / "lib.txt").read_text(), "old")
        self.assertEqual((self.new / "Ensemble.exe").read_text(), "new")
        self.assertEqual(sorted(p.name for p in self.prev.iterdir()), [])

    @unittest.skipUnless(sys.platform == "win32", "Windows locks open files")
    def test_a_locked_file_leaves_the_old_app_whole(self):
        # Review 1: a copy-then-delete fallback deleted half of _internal.
        new_names, old = self._names()
        held = open(self.install / "_internal" / "lib.txt", "rb")
        try:
            with self.assertRaises(OSError):
                app_update.swap_in(self.install, old, self.new, new_names, self.prev, tries=2, wait=0)
        finally:
            held.close()
        self.assertEqual((self.install / "Ensemble.exe").read_text(), "old")
        self.assertEqual((self.install / "_internal" / "lib.txt").read_text(), "old")
        self.assertEqual(list(self.prev.iterdir()), [])
        self.assertEqual((self.new / "_internal" / "lib.txt").read_text(), "new")

    def test_move_never_merges_into_what_is_there(self):
        (self.prev).mkdir()
        (self.prev / "_internal").mkdir()
        with self.assertRaises(FileExistsError):
            app_update._move(self.install / "_internal", self.prev / "_internal", tries=3, wait=0)
        self.assertTrue((self.install / "_internal" / "lib.txt").is_file())
        self.assertEqual(list((self.prev / "_internal").iterdir()), [])

    def test_new_version_on_another_volume_is_copied_next_to_the_app_first(self):
        new_names, _ = self._names()
        self.assertEqual(app_update.bring_near(self.new, new_names, self.install), self.new)
        with mock.patch.object(app_update, "same_volume", return_value=False):
            near = app_update.bring_near(self.new, new_names, self.install)
        self.assertEqual(near, self.install / app_update.INCOMING_DIR)
        self.assertEqual((near / "_internal" / "lib.txt").read_text(), "new")
        # Never taken for part of the app, nor copied into the helper.
        self.assertNotIn(app_update.INCOMING_DIR, app_update.app_entries(self.install, "win32")[1])

    def test_decide(self):
        self.assertEqual(app_update.decide({"version": "2.0.0"}, "2.0.0"), "keep")
        self.assertEqual(app_update.decide({"version": "1.0.0"}, "2.0.0"), "rollback")
        self.assertEqual(app_update.decide(None, "2.0.0"), "rollback")
        self.assertEqual(app_update.decide({}, "2.0.0"), "rollback")


class HelperFailureTest(unittest.TestCase):
    """Review 1: whatever goes wrong after the preflight, last.json says so and
    something is started on the hub's port."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.install = _make_app(self.d / "install", "old")
        self.new = _make_app(self.d / "new" / "Ensemble", "new")
        self.lease = self.d / "restart.lease"
        self.lease.write_text(json.dumps({"id": "L", "at": 0}))
        self.plan = {"platform": "win32", "fromVersion": "1.0.0", "toVersion": "2.0.0",
                     "app": str(self.install), "newApp": str(self.new),
                     "previousDir": str(self.install / app_update.PREVIOUS_DIR),
                     "failedDir": str(self.install / app_update.FAILED_DIR),
                     "port": 1, "hubPid": 0, "args": [], "launchd": None,
                     "leaseId": "L", "leasePath": str(self.lease),
                     "log": str(self.d / "update.log"), "preflightLog": str(self.d / "p.log"),
                     "resultPath": str(self.d / "last.json")}
        self.h = app_update.Helper(self.plan)
        self.started = []
        self.h.start_hub = lambda exe, via: self.started.append(Path(exe))
        self.h.preflight = lambda exe: True
        self.h.restore_rooms = lambda: None
        self.h.stop_hub_after_failed_start = lambda: None
        for name, value in (("_post", lambda *a, **k: {}), ("_served", lambda *a, **k: None),
                            ("wait_served", lambda *a, **k: None)):
            p = mock.patch.object(app_update, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _last(self):
        return json.loads((self.d / "last.json").read_text())

    def test_a_hub_that_does_not_stop_is_left_alone(self):
        self.h.stop_hub = lambda: (False, False)
        got = self.h.run()
        self.assertEqual(got["stage"], "stop")
        self.assertEqual((self.install / "Ensemble.exe").read_text(), "old")
        self.assertEqual(self._last()["stage"], "stop")
        self.assertEqual(self.started[0], self.install / "Ensemble.exe")

    def test_a_rollback_that_fails_is_recorded_and_the_old_app_started(self):
        self.h.stop_hub = lambda: (False, True)
        with mock.patch.object(app_update, "swap_back", side_effect=PermissionError("in use")):
            got = self.h.run()
        self.assertEqual(got["stage"], "rollback-failed")
        last = self._last()
        self.assertFalse(last["ok"])
        self.assertIn(app_update.PREVIOUS_DIR, last["previous"])
        # The new one (in place, not serving) is tried, then the old one where it was moved.
        self.assertEqual(self.started[-2:], [self.install / "Ensemble.exe",
                                             self.install / app_update.PREVIOUS_DIR / "Ensemble.exe"])

    def test_a_new_program_that_cannot_start_rolls_back_to_the_old_one(self):
        # Review 2: an OSError starting the new exe skipped the old copy and last.json.
        self.h.stop_hub = lambda: (False, True)
        in_place = self.install / "Ensemble.exe"

        def start(exe, via):
            self.started.append(Path(exe))
            if Path(exe) == in_place and (self.install / "Ensemble.exe").read_text() == "new":
                raise OSError(193, "not a valid Win32 application")
        self.h.start_hub = start
        served = iter([{"version": "1.0.0"}])   # only the old one is waited on
        with mock.patch.object(app_update, "wait_served", lambda *a, **k: next(served, None)):
            got = self.h.run()
        self.assertEqual(got["stage"], "rollback")
        self.assertEqual(self._last()["stage"], "rollback")
        self.assertTrue(got["oldServes"])
        self.assertEqual((self.install / "Ensemble.exe").read_text(), "old")
        self.assertEqual(self.started[-1], in_place)

    def test_start_failures_everywhere_still_record(self):
        self.h.stop_hub = lambda: (False, True)
        self.h.start_hub = mock.Mock(side_effect=OSError("bad exe"))
        with mock.patch.object(app_update, "swap_back", side_effect=PermissionError("in use")):
            got = self.h.run()
        self.assertEqual(got["stage"], "rollback-failed")
        self.assertFalse(got["oldServes"])
        tried = [Path(c.args[0]) for c in self.h.start_hub.call_args_list]
        self.assertIn(self.install / app_update.PREVIOUS_DIR / "Ensemble.exe", tried)

    def test_the_lease_is_renewed_through_a_long_copy(self):
        # Review 2: nothing renewed it between the preflight and the swap.
        self.h.stop_hub = lambda: (False, False)
        beats = []
        self.h.renew = lambda: beats.append(1)

        def slow_copy(new_from, names, folder):
            import time as _t
            _t.sleep(0.5)
            return new_from
        with mock.patch.object(app_update, "LEASE_BEAT_S", 0.05), \
                mock.patch.object(app_update, "bring_near", slow_copy):
            self.h.run()
        self.assertGreaterEqual(len(beats), 3)

    def test_anything_unexpected_still_records(self):
        self.h.stop_hub = mock.Mock(side_effect=RuntimeError("boom"))
        got = self.h.run()
        self.assertEqual(got["stage"], "error")
        self.assertEqual(self._last()["reason"], "boom")

    def test_a_started_hub_that_never_serves_is_killed_by_its_pid(self):
        h = app_update.Helper(self.plan)
        h.started = mock.Mock(pid=4242)
        h.started.poll.return_value = 0
        with mock.patch.object(app_update, "_kill") as kill, \
                mock.patch.object(app_update, "_port_free", return_value=True):
            h.stop_hub_after_failed_start()
        kill.assert_called_with(4242)

    def test_the_lease_is_renewed_only_when_it_is_ours(self):
        app_update.renew_lease(str(self.lease), "L")
        self.assertGreater(json.loads(self.lease.read_text())["at"], 0)
        self.lease.write_text(json.dumps({"id": "other", "at": 0}))
        app_update.renew_lease(str(self.lease), "L")
        self.assertEqual(json.loads(self.lease.read_text())["at"], 0)


class MacUpdateReadyTest(unittest.TestCase):
    """#185: what Update now swaps in on a Mac carries no quarantine attribute
    and keeps a signature that verifies, or it is not installed (a quarantined
    copy is blocked by Gatekeeper; a broken signature reads as "damaged")."""

    def _run(self, codesign_rc=0, xattrs=b"", stderr=b"a sealed resource is missing or invalid\n"):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "codesign":
                return subprocess.CompletedProcess(cmd, codesign_rc, b"", stderr if codesign_rc else b"")
            if cmd[:2] == ["xattr", "-r"]:
                return subprocess.CompletedProcess(cmd, 0, xattrs, b"")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return run, calls

    def test_quarantine_removed_then_signature_verified(self):
        run, calls = self._run()
        app = Path("/x/Ensemble.app")
        app_update.ready_mac_app(app, run=run)
        self.assertEqual(calls[0], ["xattr", "-dr", "com.apple.quarantine", str(app)])
        self.assertEqual(calls[-1], ["codesign", "--verify", "--deep", "--strict", str(app)])

    def test_a_signature_that_does_not_verify_is_refused(self):
        run, _ = self._run(codesign_rc=1)
        with self.assertRaisesRegex(ValueError, "does not verify: a sealed resource"):
            app_update.ready_mac_app(Path("/x/Ensemble.app"), run=run)

    def test_quarantine_that_stays_is_refused(self):
        run, _ = self._run(xattrs=b"/x/Ensemble.app: com.apple.quarantine\n")
        with self.assertRaisesRegex(ValueError, "quarantine"):
            app_update.ready_mac_app(Path("/x/Ensemble.app"), run=run)

    def test_stage_readies_the_mac_bundle_and_drops_the_download_when_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            data = b"zip"
            rel = {"version": "2.0.0", "assetName": "Ensemble-2.0.0-macos-universal.zip",
                   "assetUrl": "u", "sumsUrl": "s"}
            sums = f"{hashlib.sha256(data).hexdigest()}  {rel['assetName']}\n".encode()

            def fetch(url, dest):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return dest
            bundle = d / "update" / "2.0.0" / "Ensemble.app"
            readied = []
            with mock.patch.object(app_update, "unpack", return_value=bundle):
                got = app_update.stage(rel, d / "update", "darwin", get=lambda u: sums,
                                       fetch=fetch, ready=readied.append)
                self.assertEqual((got, readied), (bundle, [bundle]))

                def refuse(app):
                    raise ValueError("the signature of Ensemble.app does not verify")
                with self.assertRaises(ValueError):
                    app_update.stage(rel, d / "update", "darwin", get=lambda u: sums,
                                     fetch=fetch, ready=refuse)
            self.assertFalse((d / "update" / rel["assetName"]).exists())
            # Windows has nothing of this.
            with mock.patch.object(app_update, "unpack", return_value=d / "Ensemble"):
                app_update.stage(rel, d / "update", "win32", get=lambda u: sums,
                                 fetch=fetch, ready=refuse)

    def _helper(self, d: Path):
        def bundle(folder: Path, marker: str) -> Path:
            app = folder / "Ensemble.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            (app / "Contents" / "MacOS" / "Ensemble").write_text(marker)
            return app
        cur, new = bundle(d / "Applications", "old"), bundle(d / "new", "new")
        plan = {"platform": "darwin", "fromVersion": "1.0.0", "toVersion": "2.0.0",
                "app": str(cur), "newApp": str(new),
                "previousDir": str(d / "prev"), "failedDir": str(d / "failed"),
                "port": 1, "hubPid": 0, "args": [], "launchd": None,
                "log": str(d / "update.log"), "preflightLog": str(d / "p.log"),
                "resultPath": str(d / "last.json")}
        h = app_update.Helper(plan)
        h.preflight = lambda exe: True
        h.stop_hub = mock.Mock(return_value=(False, False))
        h.start_hub = lambda exe, via: None
        h.restore_rooms = lambda: None
        h.stop_hub_after_failed_start = lambda: None
        for name, value in (("_post", lambda *a, **k: {}), ("_served", lambda *a, **k: None),
                            ("wait_served", lambda *a, **k: None)):
            p = mock.patch.object(app_update, name, value)
            p.start()
            self.addCleanup(p.stop)
        return h, cur, new

    def test_the_helper_readies_what_it_swaps_in_before_stopping_the_hub(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(app_update, "_post", lambda *a, **k: {}):
            h, cur, new = self._helper(Path(d))
            h.ready_mac_app = mock.Mock()
            self.assertEqual(h.run()["stage"], "stop")
            h.ready_mac_app.assert_called_once_with(new)
            h.stop_hub.assert_called_once()

    def test_the_helper_refuses_a_bundle_that_is_not_ready_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            h, cur, new = self._helper(Path(d))
            h.ready_mac_app = mock.Mock(side_effect=ValueError("the signature does not verify"))
            got = h.run()
            self.assertEqual(got["stage"], "copy")
            self.assertIn("does not verify", got["reason"])
            h.stop_hub.assert_not_called()
            self.assertEqual((cur / "Contents" / "MacOS" / "Ensemble").read_text(), "old")


class AgentCheckTest(unittest.TestCase):
    @staticmethod
    def _run(table):
        def run(argv, **kw):
            key = Path(argv[0]).stem
            code, out = table[key]
            if isinstance(code, Exception):
                raise code
            return subprocess.CompletedProcess(argv, code, out, "")
        return run

    def test_both_ready(self):
        run = self._run({"claude": (0, json.dumps({"loggedIn": True, "email": "a@b"})),
                         "codex": (0, "Logged in")})
        got = app_setup.agents_check(refresh=True, which=lambda k: f"/bin/{k}", run=run)
        self.assertTrue(got["usable"])
        self.assertEqual(got["ready"], ["claude", "codex"])
        # Only the yes/no is kept, never the account.
        self.assertNotIn("a@b", json.dumps(got))

    def test_missing_and_signed_out(self):
        run = self._run({"claude": (1, json.dumps({"loggedIn": False})), "codex": (1, "")})
        got = app_setup.agents_check(refresh=True, which=lambda k: f"/bin/{k}", run=run)
        self.assertFalse(got["usable"])
        self.assertEqual(got["missing"], ["claude", "codex"])
        got = app_setup.agents_check(refresh=True, which=lambda k: None, run=run)
        self.assertFalse(got["agents"][0]["installed"])
        self.assertFalse(got["usable"])

    def test_unknown_sign_in_still_counts_as_ready(self):
        run = self._run({"claude": (OSError("boom"), ""), "codex": (2, "")})
        got = app_setup.agents_check(refresh=True, which=lambda k: f"/bin/{k}", run=run)
        self.assertEqual([a["signedIn"] for a in got["agents"]], [None, None])
        self.assertTrue(got["usable"])


if __name__ == "__main__":
    unittest.main()
