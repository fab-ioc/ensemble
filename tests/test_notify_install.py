"""Notifications ship with every install (P359, GitHub issue 50).

* requirements.txt, the launch and install scripts, the PyInstaller spec and
  the release workflow name cryptography (and its cffi part), the Mac build
  as universal2;
* webpush imports in a clean venv made from requirements.txt;
* a checkout's hub that misses the package installs it once, in the
  background (PEP 668 fallback), then loads webpush again without a restart;
  a built app never runs pip;
* Settings says what is missing in one plain sentence with the one step to
  take, never the Python error; the technical reason stays in ``reason``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import notify  # noqa: E402


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class Packaging(unittest.TestCase):
    def test_requirements_name_cryptography_for_every_platform(self):
        lines = [x.split("#")[0].strip() for x in text("requirements.txt").splitlines()]
        crypto = [x for x in lines if x.startswith("cryptography")]
        self.assertEqual(len(crypto), 1, crypto)
        self.assertNotIn(";", crypto[0], "cryptography is needed on every platform")

    def test_spec_bundles_cryptography_and_stops_without_it(self):
        spec = text("packaging/ensemble.spec")
        self.assertRegex(spec, r'hiddenimports = \[[^\]]*"webpush"')
        self.assertRegex(spec, r'hiddenimports = \[[^\]]*"_cffi_backend"')
        self.assertRegex(spec, r'hiddenimports = \[[^\]]*"cryptography\.hazmat\.bindings\._rust"')
        self.assertIn("hiddenimports=hiddenimports", spec)
        # The build stops when the package is missing, not ships a hub that cannot notify.
        self.assertRegex(spec, r"import cryptography\.hazmat\.bindings\._rust[\s\S]*?raise SystemExit")

    def test_release_workflow_mac_universal2_and_smoke_checks(self):
        wf = text(".github/workflows/release.yml")
        mac = wf[wf.index("  macos:"):wf.index("  windows:") if wf.index("  windows:") > wf.index("  macos:") else len(wf)]
        self.assertIn("sh packaging/mac-universal2-wheels.sh venv/bin/python", mac)
        self.assertLess(mac.index("-r requirements.txt"), mac.index("mac-universal2-wheels.sh"))
        self.assertLess(mac.index("mac-universal2-wheels.sh"), mac.index("pyinstaller --noconfirm"))
        self.assertEqual(wf.count("/api/notify"), 3, "both smoke tests ask the built hub whether it can notify")
        script = text("packaging/mac-universal2-wheels.sh")
        for need in ("macosx_10_9_universal2", "macosx_11_0_arm64", "macosx_10_13_x86_64",
                     "delocate-merge", 'CFFI="', 'CRYPTOGRAPHY="', "lipo -archs"):
            self.assertIn(need, script)

    def test_launch_and_install_scripts_install_it(self):
        self.assertEqual(text("ensemble").count('python3 -c "import ptyprocess, cryptography"'), 2)
        self.assertIn('"$PYTHON" -c "import ptyprocess, cryptography"', text("install-systemd.sh"))
        self.assertIn("pip install --user -r \"$REQ\"", text("install-launchd.sh"))
        self.assertIn("import winpty, cryptography", text("ensemble.ps1"))


class CleanVenv(unittest.TestCase):
    def test_webpush_imports_in_a_venv_made_from_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "v"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=120)
            py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            r = subprocess.run([str(py), "-c", "import cryptography"], capture_output=True, timeout=60)
            self.assertNotEqual(r.returncode, 0, "the venv must start without cryptography")
            r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
                                "-r", str(ROOT / "requirements.txt")],
                               capture_output=True, text=True, timeout=600)
            if r.returncode != 0 and re.search(r"(?i)connection|network|resolve|temporary failure|No matching distribution",
                                               r.stdout + r.stderr):
                self.skipTest("no package index here: " + (r.stderr.strip().splitlines() or [""])[-1][:200])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([str(py), "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                                "import webpush; k = webpush.new_private_key(); print(len(webpush.public_bytes(k)))",
                                str(ROOT)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "65")


class _State(unittest.TestCase):
    def setUp(self):
        saved = (notify.webpush, notify._IMPORT_ERROR, notify._MISSING, dict(notify._INSTALL),
                 notify._RELOAD_AT, notify._VAPID, notify._UNAVAILABLE)

        def restore():
            (notify.webpush, notify._IMPORT_ERROR, notify._MISSING, inst,
             notify._RELOAD_AT, notify._VAPID, notify._UNAVAILABLE) = saved
            notify._INSTALL.clear()
            notify._INSTALL.update(inst)
        self.addCleanup(restore)
        notify._INSTALL.update(state="", detail="")
        p = mock.patch.object(notify, "_log")
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("ENSEMBLE_NO_AUTO_INSTALL", None)

    def missing(self):
        notify.webpush = None
        notify._MISSING = True
        notify._IMPORT_ERROR = ("the Web Push module did not load (ModuleNotFoundError(\"No module named "
                                "'cryptography'\")); is the cryptography package installed?")
        notify._VAPID = None


class Classify(_State):
    def test_a_missing_package_is_told_apart_from_another_failure(self):
        with mock.patch.dict(sys.modules):
            for name in [m for m in sys.modules if m == "webpush" or m.split(".")[0] == "cryptography"]:
                del sys.modules[name]
            sys.modules["cryptography"] = None
            m, err, missing = notify._load_webpush()
        self.assertIsNone(m)
        self.assertTrue(missing)
        self.assertIn("is the cryptography package installed?", err)
        with mock.patch.dict(sys.modules):
            sys.modules.pop("webpush", None)
            with mock.patch("builtins.__import__", side_effect=OSError("broken")):
                m, err, missing = notify._load_webpush()
        self.assertIsNone(m)
        self.assertFalse(missing)

    def test_loaded_here(self):
        m, err, missing = notify._load_webpush()
        self.assertIsNotNone(m)
        self.assertEqual((err, missing), ("", False))


class AutoInstall(_State):
    def run_install(self, results, packaged=False):
        """auto_install() with pip answering `results` in turn; the argv lists it got."""
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            rc, out = results[len(calls) - 1]
            return subprocess.CompletedProcess(argv, rc, out, "")

        threads = []

        class Now:
            def __init__(self, target, **_kw):
                threads.append(target)

            def start(self):
                threads[-1]()

        with mock.patch.object(notify.app_version, "packaged", return_value=packaged), \
             mock.patch.object(notify.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(notify.threading, "Thread", Now), \
             mock.patch.object(notify, "_reload", side_effect=lambda: setattr(notify, "webpush", object()) or True):
            started = notify.auto_install()
        return started, calls

    def test_a_checkout_installs_requirements_once_then_loads(self):
        self.missing()
        started, calls = self.run_install([(0, "Successfully installed cryptography")])
        self.assertTrue(started)
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[1:4], ["-m", "pip", "install"])
        self.assertEqual(argv[-2:], ["-r", str(ROOT / "requirements.txt")])
        self.assertEqual("--user" in argv, not notify._in_venv())
        self.assertEqual(notify._INSTALL["state"], "done")
        self.assertIsNotNone(notify.webpush)
        # Once per hub: not again.
        notify.webpush = None
        self.assertEqual(self.run_install([(0, "")]), (False, []))

    def test_pep668_falls_back_to_break_system_packages(self):
        self.missing()
        started, calls = self.run_install([(1, "error: externally-managed-environment\n..."), (0, "ok")])
        self.assertTrue(started)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--break-system-packages", calls[0])
        self.assertIn("--break-system-packages", calls[1])
        self.assertEqual(notify._INSTALL["state"], "done")

    def test_a_failed_install_says_the_one_step(self):
        self.missing()
        self.run_install([(1, "Collecting cryptography\nERROR: Could not find a version that satisfies the requirement")])
        self.assertEqual(notify._INSTALL["state"], "failed")
        self.assertIn("Could not find a version", notify._INSTALL["detail"])
        help_ = notify._help()
        self.assertIn("-m pip install", help_)
        self.assertIn("cryptography", help_)
        self.assertIn("then come back here", help_)
        self.assertNotIn("ModuleNotFoundError", help_)
        self.assertNotIn("Could not find", help_)

    def test_a_built_app_never_runs_pip(self):
        self.missing()
        self.assertEqual(self.run_install([(0, "")], packaged=True), (False, []))
        self.assertEqual(notify._INSTALL["state"], "")

    def test_off_when_asked_or_when_another_failure(self):
        self.missing()
        with mock.patch.dict(os.environ, {"ENSEMBLE_NO_AUTO_INSTALL": "1"}):
            self.assertEqual(self.run_install([(0, "")]), (False, []))
        notify._MISSING = False
        self.assertEqual(self.run_install([(0, "")]), (False, []))

    def test_reload_after_an_install_by_hand(self):
        """webpush loads again in place: a hub that started without it can notify."""
        self.missing()
        self.assertTrue(notify._reload())
        self.assertIsNotNone(notify.webpush)
        self.assertEqual((notify._IMPORT_ERROR, notify._MISSING), ("", False))


class Help(_State):
    def status(self):
        with mock.patch.object(notify, "_reload", return_value=False), \
             mock.patch.object(notify, "_devices", return_value=[]):
            return notify.status()

    def test_status_keeps_the_reason_and_adds_plain_help(self):
        self.missing()
        with mock.patch.object(notify.app_version, "packaged", return_value=False):
            notify._INSTALL["state"] = "running"
            st = self.status()
            self.assertFalse(st["available"])
            self.assertTrue(st["installing"])
            self.assertIn("ModuleNotFoundError", st["reason"])     # the technical reason, as before
            self.assertIn("installing", st["help"])
            notify._INSTALL["state"] = "failed"
            st = self.status()
            self.assertFalse(st["installing"])
            self.assertIn("pip install", st["help"])

    def test_one_plain_sentence_each(self):
        self.missing()
        cases = []
        with mock.patch.object(notify.app_version, "packaged", return_value=True):
            cases.append(notify._help())
            self.assertIn("Install now", cases[-1])
            self.assertNotIn("pip", cases[-1])
        with mock.patch.object(notify.app_version, "packaged", return_value=False):
            for state in ("running", "failed"):
                notify._INSTALL["state"] = state
                cases.append(notify._help())
            notify._MISSING = False
            cases.append(notify._help())
            self.assertIn("Send feedback", cases[-1])
        for c in cases:
            self.assertTrue(c.endswith("."), c)
            self.assertEqual(c.count(". "), 0, f"one sentence: {c}")
            for word in ("Error", "module", "ModuleNotFound", "Traceback", "\n"):
                self.assertNotIn(word, c)

    def test_available_has_no_help(self):
        with mock.patch.object(notify, "_devices", return_value=[]):
            st = notify.status()
        if st["available"]:
            self.assertEqual((st["help"], st["installing"]), ("", False))

    def test_the_page_shows_help_before_the_reason_and_looks_again(self):
        page = text("index.html")
        i = page.index("function ntBlocker()")
        block = page[i:page.index("\n}\n", i)]
        self.assertIn("NT.hub.help ||", block)
        j = page.index("async function ntRefresh()")
        refresh = page[j:page.index("\n}\n", j)]
        self.assertIn("NT.hub.installing", refresh)
        self.assertIn("setTimeout", refresh)


if __name__ == "__main__":
    unittest.main()
