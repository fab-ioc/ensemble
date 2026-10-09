"""How a file an agent names in a chat is found: the one resolver behind every
file link the pages make (a balloon, the PO chat, the file view, the Changes
view, a quick-answer card, a comment). The pages build every link with
static/filelinks.js and the hub answers it here; nothing else decides where a
written path points.

The order, for a path as an agent wrote it (`Documents\\#3 Plan.md`,
`docs/a.md`, `C:\\x\\y.md`, `plan.md`):

1. an absolute path (``C:\\...``, ``\\\\server\\...``, ``~/...``, ``/...``): that file;
2. a relative one, under the room's own folders: the cwd the link carries,
   then the room's cwd, task folder, shared folder and each agent's cwd;
3. the project home;
4. the project's Documents folder;
5. the project's code folder (not the other tasks' folders: their files
   are reached through the project home);
6. nowhere at that relative path: the file whose path ends with what was
   written (the agent dropped some folders), under the same folders, the
   shallowest and then the newest. One request walks at most MAX_ENTRIES
   entries over all its folders (``Budget``), and a page's periodic recheck
   does not search by name at all.

Each way the text may be spelled is tried in that order: as written, then
with ``%20``/``%23``-style escapes decoded. A path that resolves to nothing is
"not written yet": the pages show it as a quiet chip, and the file view lists
the files of the same or a close name (``suggest``), so a moved or renamed
file opens in one click.

Pure functions of a list of folders: dashboard.py supplies the folders of a
room (``_file_ref_bases``) and serves /api/file, /api/files/check and
/api/file/suggest from them.
"""
from __future__ import annotations

import difflib
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import unquote

SEARCH_SKIP = {"node_modules", ".venv", "venv", "__pycache__", "target", "dist",
               "build", ".idea", ".tox", "site-packages", ".git"}
MAX_DEPTH = 12          # Java sources sit 9 folders down (src/main/java/com/...)
MAX_ENTRIES = 40000     # entries walked for one request, over all its folders (Budget)
INDEX_TTL = 60.0          # seconds a folder's name index is reused (a file at the
                          # written path is found without it, the moment it is there)
INDEX_KEEP = 64           # folders whose index is kept; past it the oldest goes

_PCT = re.compile(r"%[0-9A-Fa-f]{2}")
# a line on the end of a file name: "x.py:12", "x.py:12:3", "x.java#L887", "#L10-L20"
_LINE = re.compile(r"(?:#L\d+(?:C\d+)?(?:-L?\d+(?:C\d+)?)?|:\d+(?::\d+)?)$")
_ELLIPSIS = {"...", "…"}


def spellings(raw: str) -> list[str]:
    """The ways ``raw`` may be meant, most literal first: as written (quotes
    and angle brackets around it dropped), then with its percent escapes
    decoded when it has any (a markdown target or a pasted URL path)."""
    s = (raw or "").strip()
    if len(s) > 1 and s[0] == s[-1] and s[0] in "\"'`":
        s = s[1:-1].strip()
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()
    out = [s] if s else []
    if _PCT.search(s):
        try:
            d = unquote(s)
        except (ValueError, UnicodeDecodeError):
            d = s
        if d and d not in out:
            out.append(d)
    # then each without a line on its end, when what is left is a file name
    for f in list(out):
        m = _LINE.search(f)
        if m and m.start() and re.search(r"\.\w{1,8}$", f[:m.start()]) and f[:m.start()] not in out:
            out.append(f[:m.start()])
    return out


def _expand(p: str) -> Path | None:
    try:
        return Path(os.path.expanduser(p))
    except (ValueError, OSError):
        return None


def _is(p: Path | None, dirs: bool) -> bool:
    try:
        return bool(p) and (p.is_file() or (dirs and p.is_dir()))
    except (ValueError, OSError):
        return False


def _parts(rel: str) -> list[str]:
    return [x for x in re.split(r"[\\/]", rel) if x and x != "."]


# ---- the name index of a folder: one bounded walk, reused a while ---------
# key -> (made at, index, entries walked, complete, cap it was walked with)
_INDEX: dict[str, tuple[float, dict[str, list[tuple[int, str]]], int, bool, int]] = {}
_INDEX_LOCK = threading.Lock()
_BUILDING: dict[str, threading.Event] = {}   # one walk of a folder at a time
WALKS = 0                                    # walks made (the tests count them)


class Budget:
    """The entries one request may walk, over all its folders: a page's batch
    of checks, one file view, one list of suggestions. A folder costs the
    entries its index holds, walked now or cached, so the cap limits how
    much one request walks: once spent, the folders after go unsearched."""

    def __init__(self, left: int | None = None):
        self.left = MAX_ENTRIES if left is None else left


def _usable(hit, cap: int, now: float) -> bool:
    return bool(hit) and now - hit[0] < INDEX_TTL and (hit[3] or hit[4] >= cap)


def name_index(root: str, cap: int | None = None) -> tuple[dict[str, list[tuple[int, str]]], int]:
    """({lowercased file name: [(depth, full path), ...]}, entries walked) for
    the files under ``root``: hidden and dependency folders skipped, at most
    MAX_DEPTH down and ``cap`` entries (default MAX_ENTRIES). Reused for
    INDEX_TTL seconds; a folder is walked by one request at a time, and the
    others asking for it meanwhile wait for that walk and share it."""
    cap = MAX_ENTRIES if cap is None else cap
    try:
        top = os.path.normpath(os.path.expanduser(root))
        key = os.path.normcase(top)   # the cache key; the walk keeps the folder's own case
    except (ValueError, OSError):
        return {}, 0
    while True:
        with _INDEX_LOCK:
            hit = _INDEX.get(key)
            if _usable(hit, cap, time.monotonic()):
                return hit[1], hit[2]
            ev = _BUILDING.get(key)
            if ev is None:
                ev = _BUILDING[key] = threading.Event()
                break
        ev.wait(30)       # another request is walking it: take its index
    try:
        idx, n, complete = _walk(top, cap)
        with _INDEX_LOCK:
            _INDEX[key] = (time.monotonic(), idx, n, complete, cap)
            while len(_INDEX) > INDEX_KEEP:
                del _INDEX[min(_INDEX, key=lambda k: _INDEX[k][0])]
        return idx, n
    finally:
        with _INDEX_LOCK:
            _BUILDING.pop(key, None)
        ev.set()


def _walk(top: str, cap: int) -> tuple[dict[str, list[tuple[int, str]]], int, bool]:
    global WALKS
    WALKS += 1
    idx: dict[str, list[tuple[int, str]]] = {}
    n = 0
    if not os.path.isdir(top):
        return idx, 0, True
    base_depth = top.rstrip("\\/").count(os.sep)
    for dirpath, dirnames, filenames in os.walk(top, topdown=True):
        depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SEARCH_SKIP] \
            if depth < MAX_DEPTH else []
        n += len(filenames) + len(dirnames)
        for fn in filenames:
            idx.setdefault(fn.lower(), []).append((depth, os.path.join(dirpath, fn)))
        if n >= cap:
            return idx, n, False
    return idx, n, True


def _indexes(bases: list[str], budget: Budget | None):
    """(root, index) for each folder in turn, while the request's budget lasts."""
    budget = budget or Budget()
    for r in _roots(bases):
        if budget.left <= 0:
            return
        idx, n = name_index(r, budget.left)
        budget.left -= n
        yield r, idx


def forget_indexes() -> None:
    with _INDEX_LOCK:
        _INDEX.clear()


def _roots(bases: list[str]) -> list[str]:
    out, seen = [], set()
    for b in bases:
        try:
            k = os.path.normcase(os.path.normpath(os.path.expanduser(b)))
        except (ValueError, OSError):
            continue
        if k not in seen and os.path.isdir(k):
            seen.add(k)
            out.append(b)
    return out


def find_by_tail(rel: str, bases: list[str], budget: Budget | None = None) -> Path | None:
    """The file under one of ``bases`` whose path ends with ``rel`` (a bare
    name or a partial path): the first base that has one, its shallowest and
    then newest. A ``...`` folder in ``rel`` ("common/.../tws/X.java", the
    middle left out) matches any folders: the part after it is the tail, and
    the folders before it must appear in the path, in order. ``budget``:
    the entries the request may still walk (a fresh Budget by default)."""
    parts = _parts(rel)
    lead: list[str] = []
    if any(x in _ELLIPSIS for x in parts):
        i = max(i for i, x in enumerate(parts) if x in _ELLIPSIS)
        lead = [x.lower() for x in parts[:i] if x not in _ELLIPSIS]
        parts = parts[i + 1:]
    if not parts or ".." in parts:
        return None
    name, tail = parts[-1].lower(), os.sep.join(parts).lower()
    best: tuple[int, float, str] | None = None
    for _b, idx in _indexes(bases, budget):
        for depth, full in idx.get(name, []):
            if not full.lower().endswith(tail):
                continue
            # the tail must start at a folder boundary: "a.md" is not "data.md"
            pre = full[:len(full) - len(tail)]
            if pre and pre[-1] not in "\\/":
                continue
            if lead and not _in_order(lead, _parts(pre)):
                continue
            try:
                mtime = os.stat(full).st_mtime
            except OSError:
                continue
            cand = (depth, -mtime, full)
            if best is None or cand[:2] < best[:2]:
                best = cand
        if best is not None:      # the first folder in the order that has one: the task's own
            break                 # file before another task's under the project home
    return Path(best[2]) if best else None


def _in_order(want: list[str], have: list[str]) -> bool:
    it = iter(x.lower() for x in have)
    return all(w in it for w in want)


def resolve(raw: str, bases: list[str], dirs: bool = False,
            search: list[str] | None = None,
            budget: Budget | None = None) -> tuple[Path | None, str]:
    """(the file ``raw`` names, how it was found: "absolute", "relative" or
    "tail"), or (None, "") when nothing is there. ``bases`` are the folders a
    relative path is tried under, in order (the module docstring). ``dirs``:
    a folder counts too (a link to a folder opens its listing). ``search``:
    the folders walked for step 6, when not all of ``bases`` (``[]``: no
    search by name). ``budget``: shared by the resolves of one request."""
    forms = spellings(raw)
    for s in forms:
        fp = _expand(s)
        if fp is not None and fp.is_absolute():
            if _is(fp, dirs):
                return fp, "absolute"
            continue
        rel = os.sep.join(_parts(s)) if s.strip() not in (".", "./") else ""
        if not rel or any(x in _ELLIPSIS for x in _parts(s)):
            continue
        for b in bases:
            bp = _expand(b)
            if bp is None:
                continue
            cand = bp / rel
            if _is(cand, dirs):
                return cand, "relative"
    for s in forms:
        fp = _expand(s)
        if fp is None or fp.is_absolute():
            continue
        hit = find_by_tail(s, bases if search is None else search, budget)
        if hit is not None:
            return hit, "tail"
    return None, ""


_NO_RE = re.compile(r"^#?(\d+)\b")


def _stem(name: str) -> str:
    return os.path.splitext(name)[0].lower()


def suggest(raw: str, roots: list[str], limit: int = 8, budget: Budget | None = None) -> dict:
    """What a missing path may have become: ``same`` — files with its name
    anywhere under ``roots`` (moved); ``close`` — files of the same kind whose
    name is close to it (renamed: most of the words, or the same task number
    in front, "#3 ..."), best first."""
    forms = spellings(raw)
    name = _parts(forms[-1])[-1] if forms and _parts(forms[-1]) else ""
    out = {"name": name, "same": [], "close": []}
    if not name:
        return out
    low, ext, stem = name.lower(), os.path.splitext(name)[1].lower(), _stem(name)
    no = _NO_RE.match(stem)
    seen: set[str] = set()
    close: list[tuple[float, int, str]] = []
    for _r, idx in _indexes(roots, budget):
        for depth, full in sorted(idx.get(low, [])):
            k = os.path.normcase(full)
            if k not in seen:
                seen.add(k)
                out["same"].append(full)
        for n, entries in idx.items():
            if n == low or os.path.splitext(n)[1] != ext:
                continue
            st = _stem(n)
            ratio = difflib.SequenceMatcher(None, stem, st).ratio()
            m = _NO_RE.match(st)
            if no and m and m.group(1) == no.group(1):
                ratio = max(ratio, 0.8)
            if ratio < 0.72:
                continue
            for depth, full in entries:
                k = os.path.normcase(full)
                if k not in seen:
                    seen.add(k)
                    close.append((-ratio, depth, full))
    close.sort()
    out["same"] = out["same"][:limit]
    out["close"] = [c[2] for c in close[:max(0, limit - len(out["same"]))]]
    return out
