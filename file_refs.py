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
5. the project's code folder;
6. the task folders of the project;
7. nowhere at that relative path: the file whose path ends with what was
   written (the agent dropped some folders), under the same folders, the
   shallowest and then the newest.

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
MAX_DEPTH = 6
MAX_ENTRIES = 40000
INDEX_TTL = 60.0          # seconds a folder's name index is reused (a file at the
                          # written path is found without it, the moment it is there)

_PCT = re.compile(r"%[0-9A-Fa-f]{2}")


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
_INDEX: dict[str, tuple[float, dict[str, list[tuple[int, str]]]]] = {}
_INDEX_LOCK = threading.Lock()


def name_index(root: str) -> dict[str, list[tuple[int, str]]]:
    """{lowercased file name: [(depth, full path), ...]} for the files under
    ``root``: hidden and dependency folders skipped, depth and entries capped
    (the same bounds the tail search always had). Reused for INDEX_TTL
    seconds, so a page's batch of checks walks a folder once."""
    try:
        key = os.path.normcase(os.path.normpath(os.path.expanduser(root)))
    except (ValueError, OSError):
        return {}
    now = time.monotonic()
    with _INDEX_LOCK:
        hit = _INDEX.get(key)
        if hit and now - hit[0] < INDEX_TTL:
            return hit[1]
    idx: dict[str, list[tuple[int, str]]] = {}
    if os.path.isdir(key):
        base_depth = key.rstrip("\\/").count(os.sep)
        budget = MAX_ENTRIES
        for dirpath, dirnames, filenames in os.walk(key, topdown=True):
            depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SEARCH_SKIP] \
                if depth < MAX_DEPTH else []
            budget -= len(filenames) + len(dirnames)
            for fn in filenames:
                idx.setdefault(fn.lower(), []).append((depth, os.path.join(dirpath, fn)))
            if budget <= 0:
                break
    with _INDEX_LOCK:
        if len(_INDEX) > 64:
            _INDEX.clear()
        _INDEX[key] = (now, idx)
    return idx


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


def find_by_tail(rel: str, bases: list[str]) -> Path | None:
    """The file under one of ``bases`` whose path ends with ``rel`` (a bare
    name or a partial path): the first base that has one, its shallowest and
    then newest."""
    parts = _parts(rel)
    if not parts or ".." in parts:
        return None
    name, tail = parts[-1].lower(), os.sep.join(parts).lower()
    best: tuple[int, float, str] | None = None
    for b in _roots(bases):
        for depth, full in name_index(b).get(name, []):
            if not full.lower().endswith(tail):
                continue
            # the tail must start at a folder boundary: "a.md" is not "data.md"
            pre = full[:len(full) - len(tail)]
            if pre and pre[-1] not in "\\/":
                continue
            try:
                mtime = os.stat(full).st_mtime
            except OSError:
                continue
            cand = (depth, -mtime, full)
            if best is None or cand[:2] < best[:2]:
                best = cand
        if best is not None and best[0] == 0:
            break
    return Path(best[2]) if best else None


def resolve(raw: str, bases: list[str], dirs: bool = False,
            search: list[str] | None = None) -> tuple[Path | None, str]:
    """(the file ``raw`` names, how it was found: "absolute", "relative" or
    "tail"), or (None, "") when nothing is there. ``bases`` are the folders a
    relative path is tried under, in order (the module docstring). ``dirs``:
    a folder counts too (a link to a folder opens its listing). ``search``:
    the folders walked for step 7, when not all of ``bases``."""
    forms = spellings(raw)
    for s in forms:
        fp = _expand(s)
        if fp is not None and fp.is_absolute():
            if _is(fp, dirs):
                return fp, "absolute"
            continue
        rel = os.sep.join(_parts(s)) if s.strip() not in (".", "./") else ""
        if not rel:
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
        hit = find_by_tail(s, bases if search is None else search)
        if hit is not None:
            return hit, "tail"
    return None, ""


_NO_RE = re.compile(r"^#?(\d+)\b")


def _stem(name: str) -> str:
    return os.path.splitext(name)[0].lower()


def suggest(raw: str, roots: list[str], limit: int = 8) -> dict:
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
    for r in _roots(roots):
        idx = name_index(r)
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
