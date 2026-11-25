"""The README a colleague reads first stays true to the repository, and the
shipped files carry no one's name, machine or home folder.

* every relative link and image in README.md and docs/*.md points at a tracked file;
* every file or folder named in the developer layout table exists;
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
DEVELOPER = (ROOT / "docs" / "developer.md").read_text(encoding="utf-8")
GIT = shutil.which("git")

# Built from pieces so this file does not match itself.
PERSONAL = re.compile("|".join([
    "fa" + "bio",
    r"\bfa" + r"b\b(?!-ioc)",
    "epyc" + "-32",
    "tail" + "4f8740",
    "Idea" + "Projects",
    r"/home/" + "fa" + "b" + r"(?:/|\b)",
    # Any Windows home folder; one without a drive, or a macOS one, unless it is a
    # placeholder (`<name>`, `$USER`).
    r"[A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+(?!<|\$|%|(?:alex|me|u|w|sam|someone)(?=[/\\]|$))[A-Za-z]",
    r"(?<![\w.~])(?:\\\\|\\|/)Users(?:\\\\|\\|/)+(?!<|\$|%|(?:alex|me|u|w|sam|someone)(?=[/\\]|$))[A-Za-z]",
]), re.I)
TAILNET_HOST = re.compile(r"\b(?:[A-Za-z0-9-]+\.)*(?P<tailnet>[A-Za-z0-9-]+)\.ts" + r"\.net\b", re.I)
EXAMPLE_TAILNETS = {"example-tailnet", "other-example-tailnet"}


def personal_hit(line: str):
    """Allow fictional tailnets and a bare suffix, but catch every other name."""
    return PERSONAL.search(line) or next(
        (m for m in TAILNET_HOST.finditer(line) if m.group("tailnet").lower() not in EXAMPLE_TAILNETS), None)


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
        pages = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
        for page in pages:
            body = page.read_text(encoding="utf-8")
            for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", body):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                rel = (page.parent / target.split("#")[0]).resolve().relative_to(ROOT).as_posix()
                self.assertTrue(rel in self.files or rel.rstrip("/") in self.dirs,
                                f"{page.relative_to(ROOT)} links to {target}, not a tracked file")
        self.assertIn("docs/images/overview.png", README)

    def test_the_layout_names_only_what_exists(self):
        section = DEVELOPER.split("## Layout of the repository", 1)[1].split("\n## ", 1)[0]
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
                if personal_hit(line):
                    hits.append(f"{rel}:{no}: {line.strip()[:120]}")
        self.assertEqual(hits, [])

    def test_the_pattern_catches_what_it_is_for(self):
        u = "Us" + "ers"          # pieces, so this test's own source is not a hit
        bs = "\\"
        for bad in ("C:" + bs + u + bs + "rachel" + bs + "x", "C:" + bs + u + bs + "rachel", "C:/" + u + "/rachel",
                    "C:" + bs * 2 + u + bs * 2 + "rachel", "/" + u + "/rachel/x", bs + u + bs + "rachel" + bs + "x",
                    bs * 2 + u + bs * 2 + "rachel", "x = '" + bs * 2 + u + bs * 2 + "rachel'", "host.tailnet" + ".ts" + ".net"):
            self.assertTrue(personal_hit(bad), bad)
        for ok in ("D:\\work\\.ensemble", "<drive>:\\Users\\<u>", "/Users/<name>/x", "~/EnsembleProjects", "a /api/users/1",
                   "devbox.example-tailnet.ts.net", "C:" + bs + u + bs + "alex" + bs + "project"):
            self.assertFalse(personal_hit(ok), ok)
        self.assertTrue(personal_hit("devbox.my-example-tailnet" + ".ts" + ".net"))
        self.assertTrue(personal_hit("devbox.tail" + "4f8740.ts" + ".net"))
        self.assertTrue(personal_hit("C:" + bs + u + bs + "alex-smith" + bs + "project"))
        self.assertTrue(personal_hit("/home/" + "fa" + "b" + "/project"))
        self.assertTrue(personal_hit("Fa" + "b, please check"))
        self.assertFalse(personal_hit("https://github.com/fab-ioc/ensemble"))


if __name__ == "__main__":
    unittest.main()
