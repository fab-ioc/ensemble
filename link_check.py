"""Unlinked mentions in what an agent writes (GitHub issue 74).

Every file, folder, picture or page an agent names in a message the person
reads is a clickable link: ``[its name](its absolute path)``. A bare name
(``plan.md``), a plain path (``docs/plan.md``, ``/home/x/plan.md``) or a path
in a code span is wrong, though the page links some of them.

``unlinked(text, resolve)`` finds the mentions of things that exist and are
not written as a link, and says the link to write instead. The hub runs it on
an agent's ``chat_send``, ``ensemble_report``, ``ensemble_message_po`` and
``ensemble_ask`` after the text is posted, and returns ``warning(...)`` with
the tool's answer: the text is never refused, rewritten or changed where it
is stored. ``resolve(raw) -> Path | None`` is the hub's one resolver
(file_refs.resolve over the writer's room folders, folders included), so a
mention warns only when it names something that is really there; everything
else (an identifier, a command, a ratio, a file that is not written yet) is
passed over in silence.

Pure functions; standard library only.
"""
from __future__ import annotations

import bisect
import re
from pathlib import Path
from typing import Callable

# At most this many mentions are looked up for one text (each is a resolve,
# a name search at worst), and at most this many are listed in the warning.
MAX_LOOKUPS = 40
MAX_LISTED = 12
# A bare name of words ("#433 Clickable links.md", "#433 Plans") is a guess:
# each of its readings is a lookup, counted apart so that guesses never use up
# the lookups of the plain paths.
MAX_GUESSES = 24
# At most this many absolute paths are read for their ends (each a scan of up
# to ABS_RUN_MAX characters), found or not.
MAX_RUNS = 200
# Only this much of a text is read: the check runs on the hub's request
# thread, and a mention past it is still posted as written.
MAX_TEXT = 20000

_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.S | re.M)
# [words](target), a target in <...> or with one level of brackets; an <a>
# with its closer. Each part is bounded, so a long line of openers without
# closers is read in linear time.
_MD_LINK = re.compile(r"!?\[(?:[^\[\]\n]|\[[^\[\]\n]{0,300}\]){0,300}\]\((?:<[^<>\n]{0,1000}>|(?:[^()\n]|\([^()\n]{0,300}\)){0,1000})\)")
_HTML_A = re.compile(r"<a\b[^<>]{0,1000}>(?:(?!</?a\b).){0,4000}</a>", re.S | re.I)
_URL = re.compile(r"\b(?:https?|file)://[^\s<>`]+", re.I)
_CODE = re.compile(r"`([^`\n]+)`")
# A line on the end of a mention: x.py:12, x.py:12-30, x.py:12:3, x.py#L12.
_LINE = re.compile(r"(?::(\d+)(?:[-:]\d+)?|#L(\d+)(?:-L?\d+)?)$")
_EXT = re.compile(r"\.[A-Za-z]\w{0,7}$")
_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|~/|/)")
# Running text: an absolute path (it may hold spaces: each end it may have,
# at an extension, a separator or a word's end, is tried from the longest,
# see _abs_runs), a relative path with at least one separator, or a bare
# file name.
# Every run is bounded (a name is at most NAME_MAX characters): a match may
# start at almost any character, and an unbounded run would scan on to the
# end of a long token ("====...", a CSV row) from each of them.
NAME_MAX = 255
_SEG = r"[\w.#&()'+~@=,-]{1,255}"
_ABS_START = re.compile(r"(?<![\w/\\.:])(?:[A-Za-z]:[\\/]|\\\\|~/|/)(?=[\w.#~@-])")
_ABS_STOP = re.compile(r"[`<>|\"*?\n\t]|  |\s[-*>]\s|$")
_ABS_END = re.compile(r"(?:\.[A-Za-z]\w{0,7}(?::\d+(?:[-:]\d+)?)?|[\\/])(?=[\s.,;:!?)'\"]|$)|[\w#)-](?=[\s.,;:!?)'\"]|$)")
ABS_RUN_MAX = 300
_REL = re.compile(r"(?<![\w/\\.:#@-])((?:\.\.?[\\/])?" + _SEG + r"(?:[\\/]" + _SEG + r")+[\\/]?)")
# A relative path whose later folders or file hold spaces, as the pages link
# it (#433): its first folder has none; a name of words starts with a capital,
# a digit or #, has at most four words, and no word but a file's last holds a
# dot ("Documents/#433 Clickable links.md", "Documents/Screen layout/").
# At most 16 folders after the first: a match may start at any word, and an
# unbounded run would scan to the end of a long line from each of them.
_W = r"[\w#+@&-]"
_W0, _W1 = _W + r"{0,255}", _W + r"{1,255}"
_SP = r"[A-Z0-9#]" + _W0 + r"(?: " + _W1 + r"){1,3}"
_SP_LAST = r"[A-Z0-9#]" + _W0 + r"(?: " + _W1 + r"){0,2} " + _W1 + r"\.[A-Za-z]\w{0,7}(?::\d+(?:[-:]\d+)?)?"
_REL_SPACED = re.compile(r"(?<![\w/\\.:#@-])((?:\.\.?[\\/])?" + _SEG + r"[\\/](?>(?:" + _SP + "|" + _SEG
                         + r")[\\/]){0,16}(?:" + _SP_LAST + r")?)(?=[\s.,;:!?)'\"]|$)")
# One whose first folder holds spaces ("My Folder/notes/", "Ensemble
# Dashboard/Documents/"): it may start at any of that folder's words, so each
# is a reading (see _lead_readings), the first word's capital and all.
_REL_SP_FIRST = re.compile(r"(?<![\w/\\.:#@&+-])(" + _SP + r"[\\/](?>(?:" + _SP + "|" + _SEG
                           + r")[\\/]){0,16}(?:" + _SP_LAST + "|" + _SEG + r")?)(?=[\s.,;:!?)'\"]|$)")
_BARE = re.compile(r"(?<![\w/\\.:#@-])([A-Za-z0-9_][\w#&+-]{0,254}(?:\.[\w-]{1,255}){0,16}\.[A-Za-z]\w{0,7}(?::\d+(?:[-:]\d+)?)?)(?![\w/\\])")
_TRAIL = ".,;:!?)'\""
# A bare name of words in running text: a file's ("#433 Clickable links.md",
# "Screen layout.png") or a task folder's ("#433 Plans"); see _bare_spaced.
_BARE_SPACED = re.compile(r"(?<![\w/\\.:#@&+-])([A-Z0-9#]" + _W0 + r"(?: " + _W1 + r"){0,3} " + _W1
                          + r"\.[A-Za-z]\w{0,7}|#\d{1,12} [A-Z0-9]" + _W0 + r"(?: " + _W1 + r"){0,2})(?![\w/\\#@&+-])")


def _strip(text: str) -> str:
    """The text with what is already a link, a URL or a fenced block blanked
    (same length, so nothing joins across what was taken out)."""
    blank = lambda m: " " * len(m.group(0))   # noqa: E731
    s = _FENCE.sub(blank, text)
    s = _MD_LINK.sub(blank, s)
    s = _HTML_A.sub(blank, s)
    return _URL.sub(blank, s)


def _line_of(raw: str) -> tuple[str, int]:
    m = _LINE.search(raw)
    if m and _EXT.search(raw[:m.start()]):
        return raw[:m.start()], int(m.group(1) or m.group(2))
    return raw, 0


def _abs_runs(s: str, at: int) -> list[tuple[int, str]]:
    """The ends an absolute path starting at ``at`` may have, longest first:
    (end, path). It runs to the end of its line at most, stopping at what no
    path holds."""
    stop = _ABS_STOP.search(s, at, at + ABS_RUN_MAX)
    run = s[at:stop.start() if stop else at + ABS_RUN_MAX]
    ends = [m.end() for m in _ABS_END.finditer(run)]
    return [(at + e, run[:e]) for e in sorted(set(ends), reverse=True) if e > 1]


def _candidates(text: str) -> list[tuple[int, list | Callable[[], list]]]:
    """Each mention that may name a file or folder, in the order they come:
    (where it starts, the ways it may be read as (where it ends, the text),
    most likely first, or a function that gives them). There is one way, but for an absolute path in
    running text, whose end is not known before it is looked up."""
    s = _strip(text)
    out: list[tuple[int, list[tuple[int, str]]]] = []

    def code(m):
        c = m.group(1).strip()
        if c and (_EXT.search(_line_of(c)[0]) or re.search(r"[\\/]", c)):
            out.append((m.start(), [(m.end(), c)]))
        return " " * len(m.group(0))
    s = _CODE.sub(code, s)
    # where a path must stop, found once; each path's ways are read only when
    # unlinked reaches it (it looks up a bounded number)
    stops = [m.start() for m in _ABS_STOP.finditer(s)]
    for m in _ABS_START.finditer(s):
        i = bisect.bisect_left(stops, m.start())
        end = min(stops[i] if i < len(stops) else len(s), m.start() + ABS_RUN_MAX)
        if end - m.start() > 1:
            out.append((m.start(), (lambda a=m.start(): _abs_runs(s, a)), end))
    for m in _BARE_SPACED.finditer(s):
        ways = _bare_spaced(m.group(1), m.start(1))
        if ways:
            out.append((m.start(1), [(e, r, True) for e, r in ways]))
    for m in _REL_SP_FIRST.finditer(s):
        ways = _lead_readings(m.group(1), m.start(1))
        if ways:
            out.append((m.start(1), [(e, r, True) for e, r in ways]))
    for rx in (_REL_SPACED, _REL, _BARE):
        for m in rx.finditer(s):
            a, b = m.span(1)
            raw = m.group(1)
            if rx is _REL_SPACED and " " not in raw:
                continue        # _REL reads it the same
            while raw and raw[-1] in _TRAIL and not (raw.endswith(")") and raw.count("(") >= raw.count(")")):
                raw, b = raw[:-1], b - 1
            if not raw:
                continue
            if rx is not _BARE and not (_EXT.search(_line_of(raw)[0]) or "#" in raw or raw.endswith(("/", "\\"))
                                   or re.match(r"^[A-Z.]", raw)):
                # a/b with no extension: a folder only when it reads as one
                # ("Documents/#430", "docs/"), not "and/or" or "1/2".
                continue
            out.append((a, [(b, raw)]))
    # by where they start; at one place the longer reading (an absolute path
    # before the relative one inside it) first
    out = [c if len(c) == 3 else (c[0], c[1], c[1][0][0]) for c in out]
    out.sort(key=lambda c: (c[0], -c[2]))
    return [(a, w) for a, w, _ in out]


def _bare_spaced(raw: str, at: int) -> list[tuple[int, str]]:
    """The readings of a bare name of words, longest first: a file's from its
    first word to its extension, then without its first words ("Read The
    plan.md": "The plan.md"; the last word alone is _BARE's); a task
    folder's ("#433 Plans are ready") from its number, ending at each word
    but the number's own."""
    raw = raw.rstrip(_TRAIL)
    words = raw.split(" ")
    if _EXT.search(raw):
        return [(at + len(raw), " ".join(words[i:])) for i in range(len(words) - 1)]
    return [(at + len(" ".join(words[:k])), " ".join(words[:k])) for k in range(len(words), 1, -1)]


def _lead_readings(raw: str, at: int) -> list[tuple[int, str]]:
    """A relative path whose first folder is words ("Open My Folder/notes/"),
    from each of that folder's words but its last (_REL's), longest first."""
    while raw and raw[-1] in _TRAIL:
        raw = raw[:-1]
    first = re.split(r"[\\/]", raw, maxsplit=1)[0]
    cuts = [0] + [i + 1 for i, ch in enumerate(first) if ch == " "][:-1]
    return [(at + len(raw), raw[c:]) for c in cuts]


def link_for(name: str, path: Path, line: int = 0) -> str:
    """The link to write for a mention: ``[its name](its absolute path)``."""
    target = str(path) + (f":{line}" if line else "")
    if re.search(r"[()]", target) and not re.search(r"[<>]", target):
        target = f"<{target}>"      # a bracket in the path would end the link
    label = name.replace("[", "(").replace("]", ")")
    return f"[{label}]({target})"


def unlinked(text: str, resolve: Callable[[str], Path | None]) -> list[dict]:
    """The mentions in ``text`` of files or folders that exist, not written
    as a link: [{"mention": as written, "path": where it is, "link": the
    link to write}], one per thing, in the order they come."""
    if not isinstance(text, str) or not text.strip():
        return []
    text = text[:MAX_TEXT]
    out: list[dict] = []
    looked = guessed = runs = 0
    covered = -1       # where the last mention found ends: what starts before is part of it
    for start, ways in _candidates(text):
        if start < covered:
            continue
        if callable(ways):
            if runs >= MAX_RUNS:
                continue
            runs += 1
            ways = ways()
        for end, raw, *guess in ways:
            if guess:
                if guessed >= MAX_GUESSES:
                    break
                guessed += 1
            elif looked >= MAX_LOOKUPS:
                return out
            else:
                looked += 1
            body, line = _line_of(raw)
            try:
                fp = resolve(body)
            except Exception:      # noqa: BLE001 - a check never breaks a send
                fp = None
            if fp is None:
                continue
            covered = end
            key = str(fp)
            if not any(o["path"] == key for o in out):
                name = body.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1] or body
                if line:
                    name = f"{name}:{line}"
                out.append({"mention": raw, "path": key, "link": link_for(name, fp, line)})
            break
    return out


def warning(found: list[dict]) -> str:
    """The note returned with the tool's answer; '' when there is none."""
    if not found:
        return ""
    lines = [f"Link check: posted as you wrote it, but {len(found)} "
             f"mention{'s' if len(found) != 1 else ''} of a file or folder "
             f"{'are' if len(found) != 1 else 'is'} not a clickable link. Every file, folder, "
             "picture or page you name is [its name](its absolute path); a bare name, a "
             "plain path or a path in a code span is wrong. Next time write:"]
    for f in found[:MAX_LISTED]:
        lines.append(f"- {f['mention']} → {f['link']}")
    if len(found) > MAX_LISTED:
        lines.append(f"- and {len(found) - MAX_LISTED} more")
    return "\n".join(lines)
