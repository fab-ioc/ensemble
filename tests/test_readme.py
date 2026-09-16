"""The README a colleague reads first stays true to the repository, and the
shipped files carry no one's name, machine or home folder.

* every relative link and image in README.md points at a tracked file;
* every file or folder named in its layout table exists;
* no tracked file except LICENSE names the person, their machines, their
  tailnet or a home folder of theirs.

Skipped without git (the file list is git's).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
GIT = shutil.which("git")

# Built from pieces so this file does not match itself.
PERSONAL = re.compile("|".join([
    "fab" + "io",
    "epyc" + "-32",
    "tail" + "4f8740",
    r"\.ts" + r"\.net",
    "Idea" + "Projects",
    # Any Windows home folder; one without a drive, or a macOS one, unless it is a
    # placeholder (`<name>`, `$USER`).
    r"[A-Za-z]:(?:\\\\|\\|/)+Users\b",
    r"(?<![\w.~])(?:\\\\|\\|/)Users(?:\\\\|\\|/)+(?!<|\$|%)[A-Za-z]",
]), re.I)


def tracked() -> list[str]:
    out = subprocess.run([GIT, "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                         encoding="utf-8", check=True)
    return [p for p in out.stdout.split("\0") if p]


@unittest.skipUnless(GIT, "git not on PATH")
class Readme(unittest.TestCase):
    def setUp(self):
        self.files = set(tracked())
        self.dirs = {q.as_posix() for f in self.files for q in Path(f).parents if q.as_posix() != "."}

    def test_relative_links_and_images_resolve_to_tracked_files(self):
        targets = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", README)
        local = [t.split("#")[0] for t in targets if not re.match(r"[a-z]+:", t) and not t.startswith("#")]
        self.assertIn("docs/board.png", local)
        self.assertIn("docs/po-chat.png", local)
        for t in local:
            self.assertTrue(t in self.files or t.rstrip("/") in self.dirs, f"README links to {t}, not a tracked file")

    def test_the_layout_names_only_what_exists(self):
        section = README.split("## Layout of the repository", 1)[1].split("\n## ", 1)[0]
        names = [n for cell in re.findall(r"^\| ([^|]+) \|", section, re.M)
                 for n in re.findall(r"`([^`]+)`", cell)]
        self.assertGreater(len(names), 20)
        for n in names:
            self.assertTrue((ROOT / n.rstrip("/")).exists(), f"the layout names {n}, which does not exist")
            top = n.rstrip("/")
            self.assertTrue(top in self.files or top in self.dirs, f"{n} is not tracked")

    def test_no_personal_names_machines_or_home_folders_in_tracked_files(self):
        hits = []
        for rel in sorted(self.files):
            if rel == "LICENSE":
                continue
            path = ROOT / rel
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue          # images and other binaries
            for no, line in enumerate(text.splitlines(), 1):
                if PERSONAL.search(line):
                    hits.append(f"{rel}:{no}: {line.strip()[:120]}")
        self.assertEqual(hits, [])

    def test_the_pattern_catches_what_it_is_for(self):
        u = "Us" + "ers"          # pieces, so this test's own source is not a hit
        bs = "\\"
        for bad in ("C:" + bs + u + bs + "alex" + bs + "x", "C:" + bs + u + bs + "me", "C:/" + u + "/me",
                    "C:" + bs * 2 + u + bs * 2 + "alex", "/" + u + "/alex/x", bs + u + bs + "alex" + bs + "x",
                    bs * 2 + u + bs * 2 + "alex", "x = '" + bs * 2 + u + bs * 2 + "alex'", "host.tailnet" + ".ts" + ".net"):
            self.assertTrue(PERSONAL.search(bad), bad)
        for ok in ("D:\\work\\.ensemble", "<drive>:\\Users\\<u>", "/Users/<name>/x", "~/EnsembleProjects", "a /api/users/1"):
            self.assertFalse(PERSONAL.search(ok), ok)


if __name__ == "__main__":
    unittest.main()
