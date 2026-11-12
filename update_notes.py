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
# A run of references: "#14, #15, and #16", "#12/#13", "P294 & P296".
_REF_RUN = _REF + r"(?:\s*(?:,\s*(?:and|or|&)\s+|,|/|&|\band\b|\bor\b)\s*" + _REF + r")*"
_REF_PARENS = re.compile(r"\s*\(\s*" + _REF_RUN + r"(?:\s+[a-z][\w-]*)?\s*\)")
# "#326's wrapper" reads "the wrapper".
_REF_POSSESSIVE = re.compile(r"(?:[A-Z][A-Z0-9]{1,9}-\d+|#\d+)['’]s\b")
_SHA_HEX = r"(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}"
# A word that only leads into references goes with them: "after #330",
# "see P4", "in ED-3", "for #12 and #13", "as in #9", "to aceeb60".
_REF_LEAD = re.compile(r"(?i:(?:,\s*)?\b(?:see\s+also|as\s+in|see|in|for|after|before|from|per|via|"
                       r"since|until|cf\.?|and|or)\s+)(?<![\w/])" + _REF_RUN + r"(?![\w-])"
                       r"|(?i:\b(?:to|from|at|since|in|by|after|before)\s+)" + _SHA_HEX + r"(?![\w/])")
_REF_LONE = re.compile(r"(?<!\w)" + _REF_RUN + r"(?![\w-])")
# A commit hash: 7 to 40 hex characters with a digit and a letter in it.
_SHA = re.compile(r"(?<![\w/])" + _SHA_HEX + r"(?![\w/])")
# What the cleanup never touches: links and bare URLs ("\x00N\x00" stands in).
_URL = re.compile(r"https?://[^\s)\]>]+")
_KEEP = re.compile(r"\x00(\d+)\x00")

def _tidy(s: str) -> str:
    s = re.sub(r"\(\s*[,;:/&]?\s*\)", "", s)               # "()" left behind
    s = re.sub(r"\s+([,;:.)])", r"\1", s)                    # "word ," -> "word,"
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)            # ", ;" -> ","
    s = re.sub(r"(^|[,;:]\s*)[,;:/&]\s*", r"\1", s)          # a separator left alone
    s = re.sub(r"\b(the|a|an)\s+the\b", r"\1", s, flags=re.I)
    # A conjunction its reference left alone: "Fix and." -> "Fix."
    s = re.sub(r"(?i)[\s,]+(?:and|or)(?=\s*(?:[,;:.)]|$))", "", s)
    return re.sub(r"\s{2,}", " ", s).strip(" ,;:/&-–—")


def _plain(s: str) -> str:
    """References out; the punctuation tidied only where one was taken out,
    so a line without any (a wrapped "drag and") is left as it is."""
    was = s
    s = _REF_PARENS.sub("", s)
    s = _REF_POSSESSIVE.sub("the", s)
    s = _REF_LEAD.sub("", s)
    s = _REF_LONE.sub("", s)
    s = _SHA.sub("", s)
    return s if s == was else _tidy(s)


def plain_title(subject: str, links: bool = False) -> str:
    """A commit subject as a feature line: the merge prefix, task and point
    numbers and hashes taken out, the leftover punctuation tidied, the first
    letter a capital. URLs are kept as they are. With ``links`` (a line of
    release notes): Markdown links keep their targets (their labels are
    cleaned, and a link whose label was only references goes), and the line
    keeps its first letter and a "Merge …:" without a number."""
    # NUL marks what is kept (below); one in the text itself is dropped first.
    s = (subject or "").replace("\x00", "")
    s = _MERGE_PREFIX.sub("", s, count=1)
    if not links:
        s = _MERGE_WORD.sub("", s, count=1)
    kept: list[str] = []

    def keep(text: str) -> str:
        kept.append(text)
        return f"\x00{len(kept) - 1}\x00"

    if links:
        def link(m: re.Match) -> str:
            label = _plain(m.group(2))
            return keep(f"{m.group(1)}[{label}]({m.group(3)})") if label else ""
        s = re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]*(?:\s+\"[^\"]*\")?)\)", link, s)
    s = _URL.sub(lambda m: keep(m.group(0)), s)
    s = _plain(s)
    s = _KEEP.sub(lambda m: kept[int(m.group(1))], s)
    return s[:1].upper() + s[1:] if s and not links else s


def plain_notes(body: str) -> str:
    """Release notes with task and point numbers and hashes taken out, line by
    line, so the Markdown keeps its shape (links keep their targets)."""
    out = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        lead = re.match(r"^(\s*(?:[-*+]|\d+\.|#{1,6})?\s*)", line).group(1)
        rest = line[len(lead):]
        if not rest.strip():
            out.append(line)
            continue
        text = plain_title(rest, links=True)
        if text:
            out.append(lead + text)
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


def release_notes(releases: list, current: str, upto: str | None = None) -> list[dict]:
    """The published, non-pre-release releases newer than ``current`` and, with
    ``upto`` (the release Install installs), not newer than it; newest first:
    [{version, at, body}], the body as plain Markdown. All of them: the caller
    caps the list."""
    out = []
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "")
        parsed = app_version.parse_version(tag)
        if parsed is None or not app_version.is_newer(tag, current):
            continue
        if upto and app_version.is_newer(tag, upto):
            continue      # newer than what Install gets (no build for this platform)
        out.append((parsed, {"version": tag[1:] if tag.startswith("v") else tag,
                             "at": _published(rel),
                             "body": plain_notes(str(rel.get("body") or ""))[:8000]}))
    out.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in out]
