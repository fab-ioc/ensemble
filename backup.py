"""Periodic git backup of the user's projects root (PROJECTS_ROOT).

The app owns the repo: it initialises it on first run, exports each task's chat
history into the task folder, then commits and pushes on a schedule (default
hourly, configurable) or on demand.

Security: only the remote URL is stored (in settings). Credentials are NEVER
stored or handled here — authentication comes from the machine's git credential
helper or SSH key. Room bearer tokens live in ~/.ensemble and are never exported.

Decoupled from dashboard.py: callers pass a config getter and an export hook, so
this module has no import-time dependency on the server.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

# Windows: never a console window for the git the hub runs (a console-less
# host, pythonw.exe, would otherwise open one per call and steal the focus).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "lastRun": 0.0,        # epoch of the last attempt
    "lastOk": None,        # True/False/None (never ran)
    "lastMsg": "",
    "nextRun": 0.0,        # epoch of the next scheduled attempt (0 = disabled)
}

GITIGNORE = """# Projects backup — managed by the app.
# Code the agents cloned lives under <task>/repo/ and is recoverable from its
# own remote, so it is not duplicated here.
**/repo/
# Machine junk
__pycache__/
*.pyc
.DS_Store
Thumbs.db
node_modules/
.venv/
"""

MIN_INTERVAL_MIN = 5

# Kept out of the backup whatever the .gitignore says (the user may have edited
# theirs): a documents project's own file history (history.py) is a git
# database of the same files the backup already carries.
# Agent and hub state that a throwaway test hub leaves inside a task folder
# (a scratch home with .claude/, AppData/) is not project content either, and
# its paths run past Windows' 260 characters.
LOCAL_EXCLUDES = ("**/.history/", "**/.claude/", "**/.codex/", "**/.ensemble/", "**/AppData/",
                  "**/.local/")
# Folders never walked when looking for nested repositories (excluded, ignored
# or huge by design).
_NO_WALK = {".git", ".history", ".claude", ".codex", ".ensemble", "AppData", ".local", "repo",
            "node_modules", "__pycache__", ".venv"}
# A file above this is not a document: a build, a binary, a market-data
# capture. GitHub refuses files over 100 MB and warns from 50.
MAX_FILE_MB = 20


def _exclude_pattern(rel: str) -> str:
    """One anchored .git/info/exclude line for a path relative to the root."""
    out = "".join("\\" + ch if ch in "\\[]*?#!" else ch for ch in rel)
    return "/" + out


def scan(root: Path, max_depth: int = 8, max_file_mb: int | None = None) -> tuple[list[str], list[str]]:
    """Walk ``root`` and return ``(nested, oversize)``: git repositories below
    it (a folder with its own .git) and files above ``max_file_mb``, both as
    slash-separated paths relative to root. ``git add -A`` fails on a repo that
    has no commit yet and records a useless pointer for one that has, so the
    backup keeps them out; a task's code lives in its own remote anyway."""
    nested: list[str] = []
    oversize: list[str] = []
    root_s = str(root)
    cap = (MAX_FILE_MB if max_file_mb is None else max_file_mb) * 1024 * 1024
    for cur, dirs, files in os.walk(root_s):
        rel = os.path.relpath(cur, root_s)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth and (".git" in dirs or ".git" in files):
            nested.append(rel.replace(os.sep, "/"))
            dirs[:] = []
            continue
        if depth >= max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _NO_WALK]
        for f in files:
            try:
                if os.path.getsize(os.path.join(cur, f)) > cap:
                    oversize.append((f if rel == "." else rel.replace(os.sep, "/") + "/" + f))
            except OSError:
                pass
    return nested, oversize


def nested_repos(root: Path, max_depth: int = 8) -> list[str]:
    return scan(root, max_depth)[0]


def _add_excludes(root: Path, lines: list[str]) -> str | None:
    """Append the lines missing from .git/info/exclude; the error text if any."""
    ex = root / ".git" / "info" / "exclude"
    try:
        have = ex.read_text(encoding="utf-8") if ex.is_file() else ""
        missing = [p for p in lines if p not in have.splitlines()]
        if missing:
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(have + ("" if not have or have.endswith("\n") else "\n")
                          + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError as e:
        return f"exclude not written: {e}"
    return None


def _git(root: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    # utf-8 + errors=replace: on Windows the default cp1252 decode fails silently
    # inside subprocess's reader thread and yields stdout=None.
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)


def ensure_repo(root: Path, remote: str = "") -> dict:
    """Make ``root`` a git repo with our .gitignore and (optionally) ``origin``.
    Idempotent. Returns {ok, notes[]}."""
    notes: list[str] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "notes": [f"cannot create {root}: {e}"]}
    if not (root / ".git").exists():
        r = _git(root, "init", "-b", "main")
        if r.returncode != 0:                      # older git without -b
            r = _git(root, "init")
        if r.returncode != 0:
            return {"ok": False, "notes": ["git init failed: " + (r.stderr or "").strip()[:200]]}
        notes.append("initialised repo")
    err = _add_excludes(root, list(LOCAL_EXCLUDES))
    if err:
        notes.append(err)
    if os.name == "nt":
        # A task folder's name plus a file deep inside it passes 260 characters;
        # without this git add stops at "Filename too long".
        _git(root, "config", "core.longpaths", "true")
    gi = root / ".gitignore"
    if not gi.exists():
        try:
            gi.write_text(GITIGNORE, encoding="utf-8")
            notes.append("wrote .gitignore")
        except OSError as e:
            notes.append(f".gitignore not written: {e}")
    remote = (remote or "").strip()
    if remote:
        cur = _git(root, "remote", "get-url", "origin")
        if cur.returncode != 0:
            _git(root, "remote", "add", "origin", remote)
            notes.append("origin added")
        elif (cur.stdout or "").strip() != remote:
            _git(root, "remote", "set-url", "origin", remote)
            notes.append("origin updated")
    return {"ok": True, "notes": notes}


def run_backup(root: Path, remote: str = "", export_hook=None) -> dict:
    """Export chats (via hook), stage everything, commit if changed, push if a
    remote is configured. Never raises; returns {ok, msg, committed, pushed,
    exported}. Serialised — a second caller while one runs is told so."""
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "a backup is already running", "committed": False,
                "pushed": False, "exported": 0}
    _STATE["running"] = True
    _STATE["lastRun"] = time.time()
    result = {"ok": False, "msg": "", "committed": False, "pushed": False, "exported": 0,
              "skipped": 0}
    try:
        er = ensure_repo(root, remote)
        if not er["ok"]:
            result["msg"] = "; ".join(er["notes"])
            return result
        if export_hook is not None:
            try:
                result["exported"] = int(export_hook() or 0)
            except Exception as e:                    # the hook must never sink the backup
                result["msg"] = f"chat export error: {str(e)[:120]}; "
        nested, oversize = scan(root)
        if nested or oversize:
            err = _add_excludes(root, [_exclude_pattern(n) + "/" for n in nested]
                                + [_exclude_pattern(f) for f in oversize])
            if err:
                result["msg"] += err + "; "
        result["skipped"] = len(nested) + len(oversize)
        add = _git(root, "add", "-A")
        if add.returncode != 0:
            result["msg"] += "git add failed: " + (add.stderr or "").strip()[:200]
            return result
        st = _git(root, "status", "--porcelain")
        if (st.stdout or "").strip():
            ts = time.strftime("%Y-%m-%d %H:%M")
            c = _git(root, "-c", "user.name=Ensemble Backup", "-c", "user.email=backup@ensemble.local",
                     "commit", "-q", "-m", f"Backup {ts}")
            if c.returncode != 0:
                result["msg"] += "commit failed: " + (c.stderr or "").strip()[:200]
                return result
            result["committed"] = True
        remote = (remote or "").strip()
        if remote:
            p = _git(root, "push", "-u", "origin", "HEAD", timeout=1800)
            if p.returncode != 0:
                result["msg"] += "push failed: " + (p.stderr or "").strip()[:300]
                return result
            result["pushed"] = True
        result["ok"] = True
        result["msg"] += ("committed" if result["committed"] else "nothing new") \
            + ("; pushed" if result["pushed"] else ("; no remote configured" if not remote else "")) \
            + f"; {result['exported']} chat(s) exported" \
            + (f"; {result['skipped']} nested repo(s) or file(s) over {MAX_FILE_MB} MB left out"
               if result.get("skipped") else "")
        return result
    except (OSError, subprocess.SubprocessError) as e:
        result["msg"] += f"error: {str(e)[:200]}"
        return result
    finally:
        _STATE["running"] = False
        _STATE["lastOk"] = result["ok"]
        _STATE["lastMsg"] = result["msg"]
        _LOCK.release()


def status() -> dict:
    return dict(_STATE)


def start_scheduler(get_config, export_hook=None, first_delay_s: int = 120) -> None:
    """Background loop. ``get_config()`` -> (root: Path, remote: str,
    interval_min: int, enabled: bool), re-read every tick so settings changes
    apply without a restart. First run waits ``first_delay_s`` after start."""
    _STATE["nextRun"] = time.time() + first_delay_s

    def loop():
        while True:
            try:
                root, remote, interval_min, enabled = get_config()
                interval_s = max(MIN_INTERVAL_MIN, int(interval_min or 60)) * 60
                if not enabled:
                    _STATE["nextRun"] = 0.0
                else:
                    if _STATE["nextRun"] <= 0:               # (re)enabled → schedule soon
                        _STATE["nextRun"] = time.time() + 30
                    if time.time() >= _STATE["nextRun"] and not _STATE["running"]:
                        run_backup(Path(root), remote, export_hook)
                        _STATE["nextRun"] = time.time() + interval_s
            except Exception as e:                          # keep the loop alive
                _STATE["lastMsg"] = "scheduler error: " + str(e)[:200]
            time.sleep(30)

    threading.Thread(target=loop, daemon=True, name="projects-backup").start()
