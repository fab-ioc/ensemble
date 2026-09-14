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
LOCAL_EXCLUDES = ("**/.history/",)


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
    ex = root / ".git" / "info" / "exclude"
    try:
        have = ex.read_text(encoding="utf-8") if ex.is_file() else ""
        missing = [p for p in LOCAL_EXCLUDES if p not in have.splitlines()]
        if missing:
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(have + ("" if not have or have.endswith("\n") else "\n")
                          + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError as e:
        notes.append(f"exclude not written: {e}")
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
    result = {"ok": False, "msg": "", "committed": False, "pushed": False, "exported": 0}
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
            p = _git(root, "push", "-u", "origin", "HEAD", timeout=300)
            if p.returncode != 0:
                result["msg"] += "push failed: " + (p.stderr or "").strip()[:300]
                return result
            result["pushed"] = True
        result["ok"] = True
        result["msg"] += ("committed" if result["committed"] else "nothing new") \
            + ("; pushed" if result["pushed"] else ("; no remote configured" if not remote else "")) \
            + f"; {result['exported']} chat(s) exported"
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
