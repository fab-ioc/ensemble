"""Automatic file history of a documents project.

A documents project is a folder of files (ads, contracts, letters), not code.
The hub keeps every version of every file in it, so a person can see what a
task changed and put an older version back.

The history is a private git repository: a bare git dir at
``<home>/.history`` used with ``--git-dir``/``--work-tree``. There is never a
``.git`` in the project folder, so the folder is not mistaken for a code repo
(``isGit`` stays false) and the projects backup, one git repo over the whole
projects root, keeps committing its files as plain files instead of a gitlink.

What is not kept (``info/exclude``, rewritten when the task folders change):
``.history`` itself, ``project.json``, ``_linked/`` chat exports, and in every
task folder (a folder holding ``task.json``) its ``task.json``, ``chat.json``,
``*.jsonl`` transcripts, ``.claude/`` and a ``repo/`` checkout. A task's own
Markdown notes and any document it writes are kept. Files over
``MAX_FILE_BYTES`` are skipped and named in the snapshot's result.

Every snapshot is a commit that records who: the task(s) that were running,
or "you" when none was. Snapshots come from a background thread (a task's turn
ending or its report, a scan every few minutes that commits only when
``git status`` finds something, and Snapshot now), never from a request
handler.

Decoupled from dashboard.py like backup.py: the scheduler is handed the
projects and the running tasks, so this module imports nothing of the hub.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
import threading
import time
from pathlib import Path

DIR_NAME = ".history"
MAX_FILE_BYTES = 50 * 1024 * 1024      # larger files are not kept (recorded as skipped)
DIFF_BYTES_MAX = 2 * 1024 * 1024       # larger versions are not compared line by line
SCAN_INTERVAL_S = 180                  # the scan that catches changes nobody announced
TICK_S = 10
LOG_MAX = 200
EMAIL = "history@ensemble.local"

# Windows: never a console window for the git the hub runs.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Names never kept wherever they are (a file, or a folder and all it holds).
NOT_KEPT_NAMES = (".git", ".DS_Store", "Thumbs.db", "desktop.ini", "~$*", ".~lock.*#", "*.tmp")
BASE_EXCLUDES = [
    "# Managed by Ensemble: what the file history does not keep.",
    "/.history/",
    "/project.json",
    "/project.json.tmp",
    "/_linked/",
    *NOT_KEPT_NAMES,
]


def not_kept(name: str) -> bool:
    """Whether a file or folder named ``name`` is one the history never
    keeps (whatever the case), so a change to it could not be put back."""
    low = (name or "").lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in NOT_KEPT_NAMES)
TASK_EXCLUDES = ("task.json", "chat.json", "*.jsonl", ".claude/", "repo/", ".wt-scheme")

_REV = re.compile(r"^[0-9a-f]{7,40}$")
_STATE = {"lastError": "", "lastTick": 0.0}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_REQS: dict[str, dict] = {}
_REQS_LOCK = threading.Lock()
_WAKE = threading.Event()


def _key(home: str) -> str:
    return os.path.normcase(os.path.normpath(home))


def _lock(home: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(_key(home), threading.RLock())


def lock(home: str) -> threading.RLock:
    """The lock every snapshot of ``home`` takes: a change to the folder made
    under it lands in one snapshot, never split by the scan."""
    return _lock(home)


def git_dir(home: str) -> str:
    return os.path.join(home, DIR_NAME)


def exists(home: str) -> bool:
    return os.path.isfile(os.path.join(git_dir(home), "HEAD"))


def _env(read: bool) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LITERAL_PATHSPECS"] = "1"     # a file named "a*b" is that file, not a glob
    if read:
        env["GIT_OPTIONAL_LOCKS"] = "0"    # a read never takes the index lock from a snapshot
    return env


def _git(home: str, *args: str, input_bytes: bytes | None = None, text: bool = True,
         read: bool = False, timeout: int = 120) -> subprocess.CompletedProcess:
    argv = ["git", f"--git-dir={git_dir(home)}", f"--work-tree={home}",
            "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotepath=false",
            "-c", "core.longpaths=true", "-c", f"user.email={EMAIL}", *args]
    kw: dict = {"capture_output": True, "timeout": timeout, "creationflags": _NO_WINDOW,
                "env": _env(read), "cwd": home}
    if text:
        # utf-8 + replace: the Windows default decode fails silently in the reader thread.
        kw.update(text=True, encoding="utf-8", errors="replace")
    if input_bytes is not None:
        kw["input"] = input_bytes.decode("utf-8", errors="surrogateescape") if text else input_bytes
    return subprocess.run(argv, **kw)


def _escape_pattern(name: str) -> str:
    out = "".join("\\" + c if c in "[]*?!#\\" else c for c in name)
    return out[:-1] + "\\ " if out.endswith(" ") else out


def task_dirs(home: str) -> list[str]:
    """The folder names directly under ``home`` that are task folders."""
    out = []
    try:
        for c in Path(home).iterdir():
            if c.name != DIR_NAME and c.is_dir() and (c / "task.json").is_file():
                out.append(c.name)
    except OSError:
        pass
    return sorted(out)


def excludes_text(home: str) -> str:
    lines = list(BASE_EXCLUDES)
    lines.append(f"# Task folders: their notes are kept, their records and chats are not.")
    for name in task_dirs(home):
        for pat in TASK_EXCLUDES:
            lines.append(f"/{_escape_pattern(name)}/{pat}")
    return "\n".join(lines) + "\n"


def ensure(home: str) -> tuple[bool, str]:
    """Create the history for ``home`` if it has none, and bring its exclude
    rules up to date. Idempotent and cheap when nothing changed."""
    if not os.path.isdir(home):
        return False, "project folder missing"
    gd = git_dir(home)
    if not exists(home):
        try:
            r = subprocess.run(["git", "init", "--bare", "-q", "-b", "main", gd], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60,
                               creationflags=_NO_WINDOW, env=_env(False))
            if r.returncode != 0:
                r = subprocess.run(["git", "init", "--bare", "-q", gd], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60,
                                   creationflags=_NO_WINDOW, env=_env(False))
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"git init failed: {e}"
        if r.returncode != 0:
            return False, "git init failed: " + (r.stderr or "").strip()[:200]
    ex = Path(gd) / "info" / "exclude"
    want = excludes_text(home)
    try:
        if not ex.is_file() or ex.read_text(encoding="utf-8") != want:
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(want, encoding="utf-8")
    except OSError as e:
        return False, f"cannot write the exclude rules: {e}"
    return True, "ok"


def rel_path(home: str, path: str) -> str:
    """``path`` (relative to ``home`` or absolute inside it) as a clean posix
    path relative to ``home``; "" when it is outside, empty or in ``.history``."""
    p = path or ""
    if not p.strip():                 # " notes.txt" is a real name: never stripped
        return ""
    if os.path.isabs(p) or re.match(r"^[A-Za-z]:", p):
        try:
            rel = os.path.relpath(os.path.normpath(p), os.path.normpath(home))
        except ValueError:            # another drive
            return ""
    else:
        rel = os.path.normpath(p)
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if rel in ("", ".") or ".." in parts or parts[0] == DIR_NAME or any(not x for x in parts):
        return ""
    return rel


# ---- who -------------------------------------------------------------------

def _clean(s: str, n: int = 120) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", s or "").split())[:n]


def _who_lines(who) -> tuple[str, list[str]]:
    """(author name, trailer lines) for ``who``: None is you, {kind: "user",
    name} a person by name (a file uploaded, moved or deleted from the page),
    else a list of {id, title} tasks."""
    if isinstance(who, dict):
        name = _clean(str(who.get("name") or ""), 60) if who.get("kind") == "user" else ""
        return (name, ["Ensemble-Who: user", f"Ensemble-User: {name}"]) if name else ("you", ["Ensemble-Who: you"])
    tasks = [t for t in (who or []) if isinstance(t, dict) and t.get("id")]
    if not tasks:
        return "you", ["Ensemble-Who: you"]
    lines = ["Ensemble-Who: task"]
    for t in tasks:
        lines.append(f"Ensemble-Task: {_clean(t['id'], 80).replace(' ', '')} {_clean(t.get('title', ''))}".rstrip())
    name = ", ".join(_clean(t.get("title") or t["id"], 60) for t in tasks)
    return name[:200], lines


def _parse_who(body: str, author: str) -> dict:
    tasks, kind, reason, user = [], "", "", ""
    for ln in (body or "").splitlines():
        if ln.startswith("Ensemble-Who: "):
            kind = ln[14:].strip()
        elif ln.startswith("Ensemble-User: "):
            user = ln[15:].strip()
        elif ln.startswith("Ensemble-Task: "):
            rid, _, title = ln[15:].strip().partition(" ")
            tasks.append({"id": rid, "title": title.strip()})
        elif ln.startswith("Ensemble-Reason: "):
            reason = ln[17:].strip()
    if kind == "task" and tasks:
        return {"kind": "task", "tasks": tasks, "label": ", ".join(t["title"] or t["id"] for t in tasks),
                "reason": reason}
    if kind == "user" and user:
        return {"kind": "user", "tasks": [], "name": user, "label": user, "reason": reason}
    return {"kind": "you", "tasks": [], "label": "you" if kind == "you" else (author or "you"), "reason": reason}


# ---- status and snapshot -----------------------------------------------------

def _status(home: str) -> list[tuple[str, str]] | None:
    """[(code, path)] of what differs from the last snapshot, untracked files
    one by one; None when git failed."""
    r = _git(home, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames",
             "--ignore-submodules=all", read=True, timeout=60)
    if r.returncode != 0:
        return None
    out = []
    for rec in (r.stdout or "").split("\x00"):
        if len(rec) > 3:
            out.append((rec[:2], rec[3:]))
    return out


def status(home: str) -> dict:
    """What changed since the last snapshot, shaped like the hub's git status:
    {root, isGit, history, files: [{path, status, staged, tooLarge?}]}."""
    ok, msg = ensure(home)
    if not ok:
        return {"root": home, "isGit": False, "history": True, "files": [], "error": msg}
    st = _status(home)
    if st is None:
        return {"root": home, "isGit": False, "history": True, "files": [], "error": "git status failed"}
    files = []
    for code, path in st:
        c = "?" if code == "??" else (code.strip()[:1] or "M")
        f = {"path": path, "status": c, "staged": False}
        try:
            if os.path.getsize(os.path.join(home, path)) > MAX_FILE_BYTES:
                f["tooLarge"] = True
        except OSError:
            pass
        files.append(f)
    files.sort(key=lambda f: f["path"].lower())
    return {"root": home, "isGit": True, "history": True, "files": files,
            "maxFileBytes": MAX_FILE_BYTES}


def snapshot(home: str, who=None, reason: str = "scan", message: str = "", allow_empty: bool = False) -> dict:
    """Commit whatever changed since the last snapshot. Cheap when nothing did
    (one ``git status``). ``allow_empty`` commits even then, for an event that
    must be a snapshot of its own (a restore). Returns {ok, committed, rev,
    files, skipped, msg}."""
    res = {"ok": False, "committed": False, "rev": "", "files": 0, "skipped": [], "msg": ""}
    with _lock(home):
        try:
            ok, msg = ensure(home)
            if not ok:
                res["msg"] = msg
                return res
            st = _status(home)
            if st is None:
                res["msg"] = "git status failed"
                return res
            if not st and not allow_empty:
                res["ok"] = True
                return res
            add, drop = [], []
            for code, path in st:
                try:
                    size = os.path.getsize(os.path.join(home, path))
                except OSError:
                    size = -1                  # gone: its deletion is recorded
                if size > MAX_FILE_BYTES:
                    res["skipped"].append({"path": path, "size": size})
                    if code != "??":
                        drop.append(path)      # grew past the limit: no longer kept
                    continue
                add.append(path)
            if drop:
                _git(home, "rm", "--cached", "-q", "--ignore-unmatch", "--pathspec-from-file=-",
                     "--pathspec-file-nul", input_bytes="\x00".join(drop).encode("utf-8", "surrogateescape"))
            if add:
                r = _git(home, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul",
                         input_bytes="\x00".join(add).encode("utf-8", "surrogateescape"))
                if r.returncode != 0:
                    res["msg"] = "git add failed: " + (r.stderr or "").strip()[:200]
                    return res
            if not allow_empty and _git(home, "diff", "--cached", "--quiet").returncode == 0:
                res["ok"] = True               # only skipped files changed
                return res
            n = len(add) + len(drop)
            author, lines = _who_lines(who)
            subject = _clean(message, 200) or f"{n} file{'s' if n != 1 else ''} changed"
            body = "\n".join(lines + [f"Ensemble-Reason: {_clean(reason, 40) or 'scan'}"])
            c = _git(home, "-c", f"user.name={author}", "commit", "-q", "--no-verify",
                     *(["--allow-empty"] if allow_empty else []), "-m", subject, "-m", body)
            if c.returncode != 0:
                res["msg"] = "commit failed: " + (c.stderr or c.stdout or "").strip()[:200]
                return res
            res.update(ok=True, committed=True, files=n,
                       rev=(_git(home, "rev-parse", "HEAD", read=True).stdout or "").strip())
            return res
        except (OSError, subprocess.SubprocessError) as e:
            res["msg"] = f"error: {str(e)[:200]}"
            return res


# ---- reading the history -------------------------------------------------------

_FMT = "%x1e%H%x1f%at%x1f%an%x1f%s%x1f%b%x1f"


def _records(out: str):
    for chunk in (out or "").split("\x1e"):
        parts = chunk.split("\x1f")
        if len(parts) < 6 or not _REV.match(parts[0].strip()):
            continue
        yield parts[0].strip(), parts[1], parts[2], parts[3], parts[4], parts[5]


def _tokens(rest: str) -> list[str]:
    toks = rest.split("\x00")
    return [t.lstrip("\n") for t in toks if t.lstrip("\n")]


def _name_status(rest: str) -> list[dict]:
    toks, out, i = _tokens(rest), [], 0
    while i < len(toks):
        code = toks[i]
        if not code or code[0] not in "AMDRCTU" or not code[:1].isalpha():
            i += 1
            continue
        if code[0] in "RC" and i + 2 < len(toks):
            out.append({"path": toks[i + 2], "status": "R", "from": toks[i + 1]})
            i += 3
        elif i + 1 < len(toks):
            out.append({"path": toks[i + 1], "status": code[0]})
            i += 2
        else:
            break
    return out


def _numstat(rest: str) -> dict:
    toks, out, i = _tokens(rest), {}, 0
    while i < len(toks):
        m = re.match(r"^(-|\d+)\t(-|\d+)\t(.*)$", toks[i], re.S)
        if not m:
            i += 1
            continue
        a, d, path = m.group(1), m.group(2), m.group(3)
        if path == "" and i + 2 < len(toks):     # a rename: old, then new
            path = toks[i + 2]
            i += 3
        else:
            i += 1
        out[path] = (None if a == "-" else int(a), None if d == "-" else int(d))
    return out


def _head(home: str) -> str:
    out = (_git(home, "rev-parse", "-q", "--verify", "HEAD^{commit}", read=True).stdout or "").strip()
    return out if _REV.match(out) else ""


def _entries(home: str, head: str, extra: list[str], path: str = "") -> list[dict]:
    # Both reads start at the same commit: a snapshot landing between them
    # would otherwise shift the second read's window by one.
    spec = ["--", path] if path else []
    names = _git(home, "log", f"--format={_FMT}", "-M", "--name-status", "-z", *extra, head, *spec, read=True)
    if names.returncode != 0:
        return []
    nums = _git(home, "log", f"--format={_FMT}", "-M", "--numstat", "-z", *extra, head, *spec, read=True)
    counts = {h: _numstat(rest) for h, *_x, rest in _records(nums.stdout or "")}
    out = []
    for h, at, an, subject, body, rest in _records(names.stdout or ""):
        files = _name_status(rest)
        c = counts.get(h, {})
        for f in files:
            a, d = c.get(f["path"], (None, None))
            f["added"], f["removed"] = a, d
            f["binary"] = a is None and d is None and f["path"] in c
        out.append({"rev": h, "time": int(at) if at.strip().isdigit() else 0, "subject": subject.strip(),
                    "who": _parse_who(body, an), "files": files})
    return out


def log(home: str, path: str = "", limit: int = 50, skip: int = 0) -> dict:
    """The snapshots, newest first: the project's, or one file's (following
    renames). {entries: [{rev, time, subject, who, files: [{path, status,
    from?, added, removed, binary}]}], more}."""
    head = _head(home) if exists(home) else ""
    if not head:
        return {"entries": [], "more": False}
    limit = max(1, min(LOG_MAX, int(limit or 50)))
    extra = [f"-n{limit + 1}", f"--skip={max(0, int(skip or 0))}"]
    if path:
        extra.append("--follow")
    ents = _entries(home, head, extra, path)
    return {"entries": ents[:limit], "more": len(ents) > limit}


THROUGH_MAX = 10000


def _deletions(home: str, head: str):
    """Every deletion the history records, newest first, as (rev, path, row):
    ``row`` is the listing entry when this is the file's latest deletion and
    the file is still gone, else None. (rev, path) is a place in this order
    that does not move when files come back or new ones are deleted."""
    r = _git(home, "log", f"--format={_FMT}", "-M", "--diff-filter=D", "--name-only", "-z", head, read=True)
    seen = set()
    for h, at, an, _s, body, rest in _records(r.stdout or ""):
        for p in _tokens(rest):
            first = p not in seen
            seen.add(p)
            row = None
            if first and not os.path.lexists(os.path.join(home, p)):
                row = {"path": p, "rev": h, "from": h + "^", "time": int(at) if at.strip().isdigit() else 0,
                       "who": _parse_who(body, an)}
            yield h, p, row


def deleted(home: str, limit: int = LOG_MAX, after: tuple | None = None, through: tuple | None = None) -> dict:
    """Files the history holds that are gone from the folder now, newest
    deletion first: {files: [{path, rev, time, who, from}], more}; ``from``
    is the snapshot holding its last version. Pages go by place, not by count,
    so a file restored between two pages shifts nothing: ``after`` (rev, path)
    of the last row shown gives the next ``limit``; ``through`` gives every
    row down to that place (a refresh of the pages already shown)."""
    head = _head(home) if exists(home) else ""
    if not head:
        return {"files": [], "more": False}
    cap = THROUGH_MAX if through else max(1, min(LOG_MAX, int(limit or LOG_MAX)))
    after, through = (tuple(after) if after else None), (tuple(through) if through else None)
    out, started, done = [], after is None, False
    for h, p, row in _deletions(home, head):
        if not started:
            started = (h, p) == after
            continue
        if done or len(out) >= cap:
            if row:
                return {"files": out, "more": True}
            continue
        if row:
            out.append(row)
        if through and (h, p) == through:
            done = True
    return {"files": out, "more": False}


def _valid_rev(home: str, rev: str) -> str:
    """A full commit id for ``rev`` ("abc123" or "abc123^"), or ""."""
    base, caret = (rev[:-1], "^") if rev.endswith("^") else (rev, "")
    if not _REV.match(base or ""):
        return ""
    r = _git(home, "rev-parse", "--verify", "-q", f"{base}{caret}^{{commit}}", read=True)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def file_at(home: str, rev: str, path: str) -> tuple[int, bytes | str]:
    """(200, bytes) of ``path`` as it was at ``rev``, else (code, error)."""
    if not exists(home):
        return 404, "no_history"
    full = _valid_rev(home, rev)
    rel = rel_path(home, path)
    if not full or not rel:
        return 400, "bad_rev_or_path"
    size = _git(home, "cat-file", "-s", f"{full}:{rel}", read=True)
    if size.returncode != 0:
        return 404, "not_in_that_version"
    try:
        if int((size.stdout or "0").strip()) > MAX_FILE_BYTES:
            return 413, "too_large"
    except ValueError:
        pass
    r = _git(home, "cat-file", "blob", f"{full}:{rel}", text=False, read=True)
    if r.returncode != 0:
        return 404, "not_in_that_version"
    return 200, r.stdout


def _current(home: str, rel: str) -> bytes | None:
    try:
        with open(os.path.join(home, rel), "rb") as fh:
            return fh.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None


def _unified(old: bytes | None, new: bytes | None, a: str, b: str) -> dict:
    if (old and len(old) > DIFF_BYTES_MAX) or (new and len(new) > DIFF_BYTES_MAX):
        return {"diff": "", "tooLarge": True}
    if (old and b"\x00" in old) or (new and b"\x00" in new):
        return {"diff": "", "binary": True, "same": old == new}
    ot = (old or b"").decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    nt = (new or b"").decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    if ot and ot[-1] == "":
        ot.pop()
    if nt and nt[-1] == "":
        nt.pop()
    lines = list(difflib.unified_diff(ot, nt, "a/" + a if old is not None else "/dev/null",
                                      "b/" + b if new is not None else "/dev/null", n=3, lineterm=""))
    return {"diff": "\n".join(lines) + ("\n" if lines else ""), "same": old == new}


def diff(home: str, rev: str, path: str, against: str = "current", now: str = "") -> tuple[int, dict]:
    """A unified diff for one file. ``rev`` "" compares the last snapshot
    with the file now (what the Changes tab shows); otherwise ``against``
    "current" compares that version with the file now (at ``now`` when it has
    been renamed since), "parent" shows what that version changed."""
    rel = rel_path(home, path)
    if not rel:
        return 400, {"error": "bad_path"}
    cur = rel_path(home, now) or rel
    if not exists(home):
        return 404, {"error": "no_history"}
    if not rev:
        head = (_git(home, "rev-parse", "-q", "--verify", "HEAD", read=True).stdout or "").strip()
        old = None
        if head:
            code, data = file_at(home, head, rel)
            old = data if code == 200 else None
        return 200, {"file": rel, **_unified(old, _current(home, rel), rel, rel)}
    if not _valid_rev(home, rev):
        return 400, {"error": "bad_rev"}
    code, data = file_at(home, rev, rel)
    if code != 200:
        return code, {"error": data}
    if against == "parent":
        pc, pdata = file_at(home, rev + "^", rel) if _valid_rev(home, rev + "^") else (404, b"")
        return 200, {"file": rel, **_unified(pdata if pc == 200 else None, data, rel, rel)}
    return 200, {"file": rel, **_unified(data, _current(home, cur), rel, cur)}


def restore(home: str, rev: str, path: str, who_now=None) -> dict:
    """Write ``path`` as it was at ``rev`` back as the current file. What the
    folder held before is snapshotted first (credited to ``who_now``), and the
    restore is a snapshot of its own, by you."""
    with _lock(home):
        rel = rel_path(home, path)
        full = _valid_rev(home, rev) if exists(home) else ""
        if not rel or not full:
            return {"ok": False, "error": "bad_rev_or_path"}
        code, data = file_at(home, full, rel)
        if code != 200:
            return {"ok": False, "error": data}
        before = snapshot(home, who_now, reason="before restore")
        if not before["ok"]:
            return {"ok": False, "error": before["msg"] or "snapshot failed"}
        dest = Path(home) / rel
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.restore.tmp")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            return {"ok": False, "error": f"could not write {rel}: {e}"}
        at = _git(home, "log", "-1", "--format=%at", full, read=True).stdout.strip()
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(at))) if at.isdigit() else full[:8]
        # Its own snapshot even when the file already held that version, so
        # every restore shows in the history.
        after = snapshot(home, None, reason="restore", message=f"Restored {rel} from {when}", allow_empty=True)
        if not (after.get("ok") and after.get("committed")):
            return {"ok": False, "written": True, "path": rel, "from": full, "restoredFrom": when,
                    "error": f"{rel} was put back as it was on {when}, but the history could not record "
                             f"the restore: {after.get('msg') or 'snapshot failed'}"}
        return {"ok": True, "path": rel, "from": full, "restoredFrom": when,
                "rev": after.get("rev", ""), "committed": True}


# ---- the scheduler ---------------------------------------------------------------

def last_time(home: str) -> int:
    """When the last snapshot was taken (epoch seconds), 0 before the first."""
    if not exists(home):
        return 0
    out = (_git(home, "log", "-1", "--format=%at", read=True).stdout or "").strip()
    return int(out) if out.isdigit() else 0


def credit(who_now, pid: str, home: str, now: float | None = None):
    """Who a snapshot of changes nobody announced is credited to: the tasks
    ``who_now(pid, active_within)`` names as having worked within the time
    since the last snapshot, so an agent sitting idle is not credited with a
    change someone else made meanwhile."""
    last = last_time(home)
    now = time.time() if now is None else now
    return who_now(pid, max(0.0, now - last) if last else None)

def request(home: str, who=None, reason: str = "turn") -> None:
    """Ask for a snapshot of ``home`` soon, credited to ``who``. Never blocks:
    the scheduler's thread takes it within a tick."""
    if not home:
        return
    k = _key(home)
    with _REQS_LOCK:
        cur = _REQS.setdefault(k, {"home": home, "who": [], "reason": reason})
        for t in (who or []):
            if isinstance(t, dict) and t.get("id") and not any(x["id"] == t["id"] for x in cur["who"]):
                cur["who"].append({"id": t["id"], "title": t.get("title", "")})
        cur["reason"] = reason
    _WAKE.set()


def _take_requests() -> list[dict]:
    with _REQS_LOCK:
        out = list(_REQS.values())
        _REQS.clear()
    return out


def state() -> dict:
    return dict(_STATE)


def tick(homes: dict, who_now, last_scan: dict, turn_ends=None, now: float | None = None) -> list[dict]:
    """One pass of the scheduler, separated out so it can be tested:
    ``homes`` {project id: home} of the documents projects, ``who_now(pid,
    active_within)`` the tasks working there (see ``credit``), ``turn_ends()`` [(home, who)] of tasks whose
    turn just ended. Returns the snapshot results."""
    now = time.time() if now is None else now
    done = []
    if turn_ends is not None:
        for home, who in turn_ends() or []:
            request(home, who, "turn")
    known = {_key(h): (pid, h) for pid, h in homes.items()}
    for r in _take_requests():
        hit = known.get(_key(r["home"]))
        if hit:
            done.append(snapshot(hit[1], r["who"] or credit(who_now, hit[0], hit[1]), reason=r["reason"]))
            last_scan[_key(hit[1])] = now
    for pid, home in homes.items():
        if now - last_scan.get(_key(home), 0) >= SCAN_INTERVAL_S:
            last_scan[_key(home)] = now
            done.append(snapshot(home, credit(who_now, pid, home), reason="scan"))
    return done


def start_scheduler(get_homes, who_now, turn_ends=None, first_delay_s: int = 20) -> None:
    """Background loop: ``get_homes()`` -> {project id: home} of the
    documents projects, re-read every tick so a project switched to documents
    starts its history without a restart."""
    def loop():
        time.sleep(first_delay_s)
        last_scan: dict = {}
        while True:
            try:
                _STATE["lastTick"] = time.time()
                tick(get_homes() or {}, who_now, last_scan, turn_ends)
            except Exception as e:                      # keep the loop alive
                _STATE["lastError"] = str(e)[:200]
            _WAKE.wait(TICK_S)
            _WAKE.clear()

    threading.Thread(target=loop, daemon=True, name="file-history").start()
