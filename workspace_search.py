"""Go to file and search in files, for a Workspace.

The Workspace's find box lists the files of one folder (a task's, or a
project's) and searches their text. Both stay inside that folder:

* the walk never descends into ``.git`` or ``node_modules``, nor into what a
  repo's own ``.gitignore`` excludes (git decides, per repo met on the way);
* a folder reached through a symlink or a junction is never entered, and a
  linked file counts only when its target is inside the folder too.

The hub lists files in its own process (one git call per repo, fast), but runs
a search as a child process (``python workspace_search.py`` reading a JSON
request on stdin): a regular expression can take unbounded time, Python's
``re`` holds the interpreter while it runs, and a hub stuck in one would stop
serving every page and agent. A child can be killed, by a timeout or by the
next search from the same box.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time

SKIP_DIRS = {".git", "node_modules"}
FILES_MAX = 50_000            # files listed; past this the list says it is cut
MATCH_MAX = 1000              # matches returned; past this "more results not shown"
FILE_BYTES_MAX = 2 * 1024 * 1024   # larger files are counted as skipped, not read
LINE_SHOWN = 240              # characters of a matching line sent back
BEFORE_MATCH = 24             # of which, at most this many before the first match
QUERY_MAX = 500
DEADLINE_S = 8.0              # a search stops here and returns what it found

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _inside(real: str, real_root: str) -> bool:
    return real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep)


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _git_ignored(d: str, rel: str) -> set[str]:
    """What git ignores under ``d``, as paths relative to the walk's root:
    files as ``a/b.log``, wholly ignored folders as ``a/build``."""
    try:
        out = subprocess.run(
            ["git", "-C", d, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {rel + p.rstrip("/") for p in (out.stdout or "").split("\x00") if p}


def _is_link(e: os.DirEntry) -> bool:
    try:
        return e.is_symlink() or bool(getattr(e, "is_junction", lambda: False)())
    except OSError:
        return True


def list_files(root: str, enclosing_ignores: bool = False, limit: int = FILES_MAX) -> tuple[list[str], bool]:
    """The files under ``root``, as ``/``-separated paths relative to it, and
    whether the list was cut at ``limit``. ``enclosing_ignores`` applies the
    ignore rules of a repo that ``root`` sits inside (not only those of repos
    found under it)."""
    real_root = _real(root)
    ignored: set[str] = _git_ignored(root, "") if enclosing_ignores else set()
    files: list[str] = []
    stack = [(root, "")]
    while stack:
        d, rel = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        if any(e.name == ".git" for e in entries) and not (rel == "" and enclosing_ignores):
            ignored |= _git_ignored(d, rel)
        dirs = []
        for e in entries:
            r = rel + e.name
            if e.name in SKIP_DIRS or r in ignored:
                continue
            link = _is_link(e)
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if link:
                # Never walk a linked folder: it can lead out of the root, or
                # round in a loop. A linked file counts when it lands inside.
                try:
                    if not os.path.isfile(e.path) or not _inside(_real(e.path), real_root):
                        continue
                except OSError:
                    continue
            elif is_dir:
                dirs.append((e.path, r + "/"))
                continue
            if len(files) >= limit:
                return files, True
            files.append(r)
        stack.extend(reversed(dirs))
    return files, False


def compile_query(q: str, case: bool = False, regex: bool = False) -> re.Pattern:
    """The pattern a query searches for; ``re.error`` for a regex that is not one."""
    flags = re.MULTILINE | (0 if case else re.IGNORECASE)
    return re.compile(q if regex else re.escape(q), flags)


def _u16(s: str) -> int:
    """Length in UTF-16 code units: how a browser counts a string."""
    return len(s) if s.isascii() else len(s.encode("utf-16-le")) // 2


def _line_hit(line: str, spans: list[tuple[int, int]]) -> dict:
    """A matching line as sent to the page: the part of it worth showing, and
    where the matches fall in that part. ``at`` is where the part starts in the
    line, so ``at + range start`` is the match's column in the file. All
    offsets are UTF-16 units, the way the page will slice the text."""
    first = spans[0][0]
    indent = len(line) - len(line.lstrip())
    start = indent if first >= indent else 0
    start = max(start, first - BEFORE_MATCH)
    end = min(len(line), start + LINE_SHOWN)
    part = line[start:end]
    at = _u16(line[:start])
    ranges = []
    for s, e in spans:
        if s >= end:
            break
        e = min(e, end)
        ranges.append([_u16(line[start:s]), _u16(line[s:e])])
    return {"at": at, "text": part, "ranges": ranges, "n": len(spans),
            "cutStart": bool(line[:start].strip()), "cutEnd": end < len(line)}


def search(root: str, q: str, case: bool = False, regex: bool = False, enclosing_ignores: bool = False,
           limit: int = MATCH_MAX, deadline_s: float = DEADLINE_S) -> dict:
    t0 = time.monotonic()
    pat = compile_query(q, case, regex)
    files, list_cut = list_files(root, enclosing_ignores)
    real_root = _real(root)
    out: list[dict] = []
    n = searched = binary = large = unreadable = 0
    cut = timed_out = False
    for rel in files:
        if time.monotonic() - t0 > deadline_s:
            timed_out = True
            break
        p = os.path.join(root, rel)
        try:
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                # Listed as inside; checked again, since it can change between.
                if not _inside(_real(p), real_root):
                    continue
                st = os.stat(p)
            if not stat.S_ISREG(st.st_mode):
                continue
            if st.st_size > FILE_BYTES_MAX:
                large += 1
                continue
            with open(p, "rb") as f:
                raw = f.read(FILE_BYTES_MAX + 1)
        except OSError:
            unreadable += 1
            continue
        searched += 1
        if b"\x00" in raw[:8192]:
            binary += 1
            continue
        # Windows line ends read as plain ones, so a regex's $ ends a line there too.
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if not pat.search(text):
            continue
        hits = []
        for i, line in enumerate(text.split("\n")):
            spans = []
            for m in pat.finditer(line):
                if m.end() == m.start():
                    continue          # an empty match marks nothing
                if n >= limit:
                    cut = True
                    break
                spans.append((m.start(), m.end()))
                n += 1
            if spans:
                hits.append({"line": i + 1, **_line_hit(line, spans)})
            if cut:
                break
        if hits:
            out.append({"path": rel, "matches": hits})
        if cut:
            break
    return {"files": out, "matches": n, "truncated": cut, "limit": limit,
            "filesListed": len(files), "listTruncated": list_cut, "filesSearched": searched,
            "skipped": {"binary": binary, "large": large, "unreadable": unreadable},
            "largeBytes": FILE_BYTES_MAX, "timedOut": timed_out, "deadline": deadline_s,
            "ms": round((time.monotonic() - t0) * 1000)}


def main() -> int:
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    try:
        res = search(req["root"], req["q"], bool(req.get("case")), bool(req.get("regex")),
                     bool(req.get("enclosingIgnores")), int(req.get("limit", MATCH_MAX)),
                     float(req.get("deadline", DEADLINE_S)))
    except re.error as e:
        res = {"error": "bad_regex", "detail": str(e)}
    sys.stdout.buffer.write(json.dumps(res).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
