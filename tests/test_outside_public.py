"""The repo is public: no real Slack user, channel or workspace id may go into
the outside sources' code, tests or fixtures (design #1, section 7). This
scans them for anything that looks like a real Slack id (a U, C, D, G, S, T
or W, then 0, then 8 or more capitals and digits) other than the made-up
ones (``U000TEST01``, ``C000TEST01``, …), and for a ``slack.com`` address
other than ``example.slack.com``. Jira (#14) too: an Atlassian account id
(``557058:`` then a long id, or 24 hex digits) other than the made-up
``000000:…`` ones, a site id (a UUID) other than the made-up ``00000000-…``
ones, and an ``atlassian.net`` site other than ``example`` or ``made-up``."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Real-looking Slack ids, and the made-up ones that are allowed.
ID_RE = re.compile(r"(?<![A-Za-z0-9])[UCDGSTW]0[A-Z0-9]{8,}(?![A-Za-z0-9])")
MADE_UP_RE = re.compile(r"[UCDGSTW]000TEST\d{2,}")
WORKSPACE_RE = re.compile(r"([A-Za-z0-9-]+)\.slack\.com", re.I)
ALLOWED_WORKSPACES = {"example", "app", "api", "mcp", "files", "hooks"}
ACCOUNT_RE = re.compile(r"(?<![0-9A-Za-z])(\d{6}:[0-9a-f-]{36}|[0-9a-f]{24})(?![0-9A-Za-z-])")
MADE_UP_ACCOUNT_RE = re.compile(r"000000:[0-9a-f-]{36}")
UUID_RE = re.compile(r"(?<![0-9A-Za-z:-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9A-Za-z-])")
SITE_RE = re.compile(r"([A-Za-z0-9-]+)\.atlassian\.net", re.I)
TEXT = {".py", ".json", ".jsonl", ".md", ".txt", ".html", ".js", ".csv", ".yaml", ".yml", ""}


def files() -> list[Path]:
    """The files this rule covers: the frame, every source module, and the
    tests and fixtures of either (the Slack reader's included, when it lands)."""
    found = set(ROOT.glob("outside*.py")) | set(ROOT.glob("sources/**/*")) | {ROOT / "work_items.py"}
    for pattern in ("*outside*", "*slack*", "*jira*"):
        found |= set((ROOT / "tests").glob(pattern))
        found |= set((ROOT / "tests" / "fixtures").glob(f"**/{pattern}"))
    out = []
    for p in found:
        if p.is_dir():
            out += [q for q in p.rglob("*") if q.is_file()]
        elif p.is_file():
            out.append(p)
    return sorted({p for p in out if p.suffix in TEXT and "__pycache__" not in p.parts})


def problems(text: str) -> list[str]:
    out = [m.group(0) for m in ID_RE.finditer(text) if not MADE_UP_RE.fullmatch(m.group(0))]
    out += [m.group(0) for m in WORKSPACE_RE.finditer(text)
            if m.group(1).lower() not in ALLOWED_WORKSPACES]
    out += [m.group(0) for m in ACCOUNT_RE.finditer(text) if not MADE_UP_ACCOUNT_RE.fullmatch(m.group(0))]
    out += [m.group(0) for m in UUID_RE.finditer(text) if not m.group(0).startswith("00000000-")]
    out += [m.group(0) for m in SITE_RE.finditer(text) if m.group(1).lower() not in ("example", "made-up")]
    return out


class PublicRepo(unittest.TestCase):
    def test_no_real_looking_ids_in_the_outside_sources_files(self):
        checked = files()
        self.assertIn(ROOT / "outside.py", checked)
        self.assertIn(ROOT / "sources" / "base.py", checked)
        self.assertIn(ROOT / "tests" / "test_outside.py", checked)
        self.assertIn(ROOT / "outside_route.py", checked)
        self.assertIn(ROOT / "tests" / "test_outside_route.py", checked)
        self.assertIn(ROOT / "work_items.py", checked)
        self.assertIn(ROOT / "outside_items.py", checked)
        self.assertIn(ROOT / "tests" / "test_outside_items.py", checked)
        self.assertIn(ROOT / "outside_model.py", checked)
        self.assertIn(ROOT / "outside_replay.py", checked)
        self.assertIn(ROOT / "tests" / "test_outside_model.py", checked)
        self.assertIn(ROOT / "tests" / "test_outside_replay.py", checked)
        self.assertIn(ROOT / "tests" / "test_outside_conversation.py", checked)
        self.assertIn(ROOT / "sources" / "jira.py", checked)
        self.assertIn(ROOT / "tests" / "test_jira.py", checked)
        self.assertIn(ROOT / "tests" / "jira_fixtures.py", checked)
        found = {}
        for p in checked:
            bad = problems(p.read_text(encoding="utf-8", errors="replace"))
            if bad:
                found[str(p.relative_to(ROOT))] = sorted(set(bad))
        self.assertEqual(found, {}, "use made-up ids (U000TEST01, C000TEST01, example.slack.com)")

    def test_the_scan_finds_what_it_is_for(self):
        # Built at run time, so this file holds no real-looking id itself.
        planted = "by <@" + "U0" + "7QXZ4KJ2M> in " + "C0" + "4ABCDEF99 at " + "acme" + ".slack.com"
        self.assertEqual(len(problems(planted)), 3)
        self.assertEqual(problems("U000TEST01 C000TEST02 https://example.slack.com/archives/C000TEST01"),
                         [])
        self.assertEqual(problems("ABC-123 example-repo 1234 UTC0123"), [])
        planted = ("557058" + ":" + "1f2e3d4c-" + "5b6a-4789-8a9b-0c1d2e3f4a5b, " + "5b10ac8d82e05b22" + "cc7d4ef5, "
                   + "a1b2c3d4" + "-e5f6-4a7b-8c9d-0e1f2a3b4c5d on " + "acme" + ".atlassian.net")
        self.assertEqual(len(problems(planted)), 4)
        self.assertEqual(problems("000000:00000000-0000-4000-8000-000000000001 00000000-0000-4000-8000-0000000000aa "
                                  "https://example.atlassian.net/browse/ABC-12"), [])


if __name__ == "__main__":
    unittest.main()
