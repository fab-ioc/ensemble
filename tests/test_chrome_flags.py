"""Every launch of Chrome in the repo passes --use-mock-keychain.

Why: on a Mac, Chrome keeps its storage key in the login keychain. A launch
without these flags makes macOS show "Keychain Not Found - A keychain cannot
be found to store "Chrome"" while the tests run. A file that starts Chrome
(it has a quoted --headless flag) must also have --use-mock-keychain and
--password-store=basic. Launch lines are checked one by one, so a file with
two launches cannot hide one that lacks the flags.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCH = re.compile(r"""["']--""" + "headless")


def launching_files():
    for folder in (ROOT / "tests", ROOT / "tools", ROOT):
        pattern = "*" if folder != ROOT else "*.py"
        for p in sorted(folder.glob(pattern)):
            if p.is_file() and p.suffix in (".py", ".js") and p != Path(__file__).resolve():
                yield p


class ChromeFlagsTest(unittest.TestCase):
    def test_every_chrome_launch_has_the_keychain_flags(self):
        launches = 0
        missing = []
        for p in launching_files():
            text = p.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                if not LAUNCH.search(line):
                    continue
                launches += 1
                # a launch list may continue on the next line
                near = "\n".join(text.splitlines()[n - 1:n + 3])
                for flag in ("--use-mock-keychain", "--password-store=basic"):
                    if flag not in near:
                        missing.append(f"{p.relative_to(ROOT)}:{n} lacks {flag}")
        self.assertGreater(launches, 50, "found too few Chrome launches; the search is broken")
        self.assertEqual(missing, [], "Chrome launches without the keychain flags:\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
