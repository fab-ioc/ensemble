"""The seeded Chrome profiles (tests/chrome_profile.py): what the seed holds,
that a launch without it is refused, and that every launcher in the repo gets
its --user-data-dir from the helper. None of these start Chrome."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")


def chrome_time(unix: float) -> int:
    return int((unix + 11644473600) * 1_000_000)


class Seed(unittest.TestCase):
    def test_seed_says_not_blank_and_changed_now(self):
        before = chrome_time(time.time())
        pm = json.loads(chrome_profile.local_state())["password_manager"]
        after = chrome_time(time.time())
        self.assertIs(pm["os_password_blank"], False)
        self.assertIsInstance(pm["os_password_last_changed"], str, "an int64 pref is a string in Local State")
        # Chrome probes again only if the password changed after this value: now is after any change so far.
        self.assertTrue(before <= int(pm["os_password_last_changed"]) <= after)

    def test_new_profile_is_new_seeded_and_checked(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = chrome_profile.new_profile(t), chrome_profile.new_profile(t)
            self.assertNotEqual(a, b, "one profile per launch")
            self.assertEqual(sorted(p.name for p in Path(a).iterdir()), ["Local State"])
            chrome_profile.check(a)

    def test_check_refuses_a_profile_without_the_value(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            with self.assertRaisesRegex(RuntimeError, "refusing to launch"):
                chrome_profile.check(d)
            for text in ("", "{}", '{"password_manager": {}}',
                         '{"password_manager": {"os_password_blank": false, "os_password_last_changed": "0"}}',
                         '{"password_manager": {"os_password_blank": true, "os_password_last_changed": "5"}}'):
                (d / "Local State").write_text(text, encoding="utf-8")
                with self.assertRaises(RuntimeError, msg=text):
                    chrome_profile.check(d)

    def test_no_chrome_env_empties_chrome(self):
        env = {**os.environ, "ENSEMBLE_NO_CHROME": "1"}
        out = subprocess.run([sys.executable, "-c", "from tests import chrome_profile, test_page_update as t; "
                              "print(repr(chrome_profile.CHROME), repr(t.CHROME))"],
                             cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.stdout.strip(), "'' ''", out.stderr)


@unittest.skipUnless(NODE, "needs Node")
class NodeSide(unittest.TestCase):
    def run_js(self, args: dict) -> subprocess.CompletedProcess:
        js = chrome_profile.JS + "const A = JSON.parse(process.argv[1]); console.log(chromeProfile(A));"
        return subprocess.run([NODE, "-e", js, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=60)

    def test_chrome_profile_writes_the_seed(self):
        with tempfile.TemporaryDirectory() as t:
            args = {**chrome_profile.node_args(), "tmp": t}
            out = self.run_js(args)
            self.assertEqual(out.returncode, 0, out.stderr)
            d = out.stdout.strip()
            self.assertEqual(Path(d).parent, Path(t))
            chrome_profile.check(d)
            self.assertEqual((Path(d) / "Local State").read_text(encoding="utf-8"), args["chromeLocalState"])

    def test_chrome_profile_throws_without_the_seed(self):
        with tempfile.TemporaryDirectory() as t:
            for bad in ({}, {"chromeLocalState": "{}"}, {"chromeLocalState": "not json"}):
                out = self.run_js({"chrome": "x", "tmp": t, **bad})
                self.assertNotEqual(out.returncode, 0, bad)
                self.assertIn("refusing to launch Chrome", out.stderr)


class EveryLauncher(unittest.TestCase):
    """Every --user-data-dir in the repo's tests and tools comes from the helper."""

    def test_no_launcher_makes_its_own_profile(self):
        found = 0
        for p in sorted([*(ROOT / "tests").glob("*.py"), *(ROOT / "tools").glob("*.py")]):
            if p.name in ("chrome_profile.py", "test_chrome_profile.py"):
                continue
            src = p.read_text(encoding="utf-8")
            for line in src.splitlines():
                if "user-data-dir" not in line:
                    continue
                found += 1
                ok = ("'--user-data-dir=' + udd" in line and "const udd = chromeProfile(A);" in src
                      and "chrome_profile.JS" in src and "chrome_profile.node_args()" in src) \
                    or re.search(r"--user-data-dir=\{chrome_profile\.new_profile\(", line)
                self.assertTrue(ok, f"{p.name}: {line.strip()}")
        self.assertGreaterEqual(found, 10)


if __name__ == "__main__":
    unittest.main()
