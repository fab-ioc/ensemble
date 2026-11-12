"""What an update brings, in plain words, for the What's new dialog (#339).

A checkout: the merge subjects on the upstream branch since the running
commit (``git log --first-parent``), newest first, with the bookkeeping taken
out: no "Merge #N (Pn):" prefix, no task numbers, no point numbers, no commit
hashes. A built app: the notes of every release newer than the running one.
"""
from __future__ import annotations

import calendar
import re
import time
from typing import Callable

import app_version

MAX_ITEMS = 20

# "Merge #328 (P294, P296): …", "Merge #327 (P292 follow-up): …", "Merge #12: …"
_MERGE_PREFIX = re.compile(r"^\s*Merge\s+(?:[A-Z][A-Z0-9]{1,9}-)?#?\d+\b\s*(?:\([^)]*\))?\s*[:\-–—]\s*")
# "Merge fix: …", "Merge round 2: …", "Merge follow-up: …"
_MERGE_WORD = re.compile(r"^\s*Merge\b[^:#\n]{0,24}:\s*")
# A whole parenthesis of references: "(P294, P296)", "(#330, P306)", "(ED-12)".
_REF = r"(?:[A-Z][A-Z0-9]{1,9}-\d+|#\d+|P\d+[a-z]?)"
_REF_PARENS = re.compile(r"\s*\(\s*" + _REF + r"(?:\s*(?:,|and|&|/)\s*" + _REF + r")*"
                         r"(?:\s+[a-z][\w-]*)?\s*\)")
# "#326's wrapper" reads "the wrapper"; "after #330" reads "after".
_REF_POSSESSIVE = re.compile(r"(?:[A-Z][A-Z0-9]{1,9}-\d+|#\d+)['’]s\b")
_REF_LONE = re.compile(r"(?<![\w/])" + _REF + r"(?![\w-])")
# A commit hash: 7 to 40 hex characters with a digit and a letter in it.
_SHA = re.compile(r"(?<![\w/])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w/])")


def plain_title(subject: str) -> str:
    """A commit subject as a feature line: the merge prefix, task and point
    numbers and hashes taken out, the leftover punctuation tidied."""
    s = _MERGE_PREFIX.sub("", subject or "", count=1)
    s = _MERGE_WORD.sub("", s, count=1)
    s = _REF_PARENS.sub("", s)
    s = _REF_POSSESSIVE.sub("the", s)
    s = _REF_LONE.sub("", s)
    s = _SHA.sub("", s)
    s = re.sub(r"\(\s*[,;:]?\s*\)", "", s)                 # "()" left behind
    s = re.sub(r"\s+([,;:.)])", r"\1", s)                  # "word ," -> "word,"
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)          # ", ;" -> ","
    s = re.sub(r"\b(the|a|an)\s+the\b", r"\1", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;:-–—")
    return s[:1].upper() + s[1:] if s else ""


def plain_notes(body: str) -> str:
    """Release notes with task and point numbers and hashes taken out, line by
    line, so the Markdown keeps its shape."""
    out = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        lead = re.match(r"^(\s*(?:[-*+]|\d+\.|#{1,6})?\s*)", line).group(1)
        rest = line[len(lead):]
        out.append(lead + plain_title(rest) if rest.strip() else line)
    return "\n".join(out).strip()


def git_items(git: Callable[..., str], current: str, upstream: str) -> dict:
    """The feature list between ``current`` and ``upstream``: {items, more,
    updates, currentDate, latestDate}. ``git(*args)`` returns stdout."""
    log = git("log", "--first-parent", "--format=%ct%x09%s", f"{current}..{upstream}")
    items = []
    for line in log.splitlines():
        stamp, _, subject = line.partition("\t")
        title = plain_title(subject)
        if title:
            items.append({"text": title[:400], "at": int(stamp) if stamp.isdigit() else 0})
    dates = {}
    for name, ref in (("currentDate", current), ("latestDate", upstream)):
        out = git("log", "-1", "--format=%ct", ref)
        dates[name] = int(out) if out.isdigit() else 0
    return {"items": items[:MAX_ITEMS], "more": max(0, len(items) - MAX_ITEMS),
            "updates": len(items), **dates}


def _published(rel: dict) -> int:
    stamp = str(rel.get("published_at") or rel.get("created_at") or "")
    try:
        return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return 0


def release_notes(releases: list, current: str) -> list[dict]:
    """The published, non-pre-release releases newer than ``current``, newest
    first: [{version, at, body}], the body as plain Markdown."""
    out = []
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "")
        parsed = app_version.parse_version(tag)
        if parsed is None or not app_version.is_newer(tag, current):
            continue
        out.append((parsed, {"version": tag[1:] if tag.startswith("v") else tag,
                             "at": _published(rel),
                             "body": plain_notes(str(rel.get("body") or ""))[:8000]}))
    out.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in out[:MAX_ITEMS]]
