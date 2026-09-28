"""Moving from claude-dashboard, Ensemble's predecessor.

claude-dashboard was cloned into ``~/.claude/dashboard`` and kept its state
there, next to its own program (dashboard.py, install-launchd.sh, a .git
checkout), with a LaunchAgent (macOS) or a scheduled task (Windows) starting
it on port 8765. Ensemble keeps its state in ``~/.ensemble``.

`migrate()` is the one place the move happens, and the hub calls it at
startup whichever way it was started (``ensemble start``, install-launchd.sh,
the Windows task, ``py dashboard.py``):

* it COPIES the old state files Ensemble can use, each only when Ensemble has
  no file of that name yet, and never touches ``~/.claude/dashboard``;
* a marker file in the state dir, not the state dir's existence, says it ran;
* it returns one line for the log: what was copied, and whether the old
  program's LaunchAgent / task is still loaded, with the command to remove it.
  It never unloads anything itself.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LEGACY_REL = Path(".claude") / "dashboard"
MARKER_NAME = ".migrated-from-claude-dashboard"
LAUNCH_AGENT = "com.claude-code.dashboard"
WINDOWS_TASK = "ClaudeDashboard"

# Every state file claude-dashboard wrote that Ensemble reads the same way,
# copied only when absent. pinned/categories/known_categories are no longer
# read by Ensemble; copying them keeps nothing from being lost and costs
# nothing. Not copied: its rooms/, agents/, _mcp/, logs and pid file, which
# name sessions and ports of the old program.
COPY_FILES = (
    "labels.json", "pinned.json", "categories.json", "archived.json",
    "parents.json", "geometries.json", "favorite_themes.json",
    "jira_links.json", "jira_unlinks.json",
    "known_categories.json", "editors.json",
)
# settings.json: only the keys claude-dashboard had, and each only when
# Ensemble's own settings do not set it — same names, same meanings.
SETTINGS_KEYS = ("openMode", "defaultModel")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _quiet(cmd: list[str]) -> int | None:
    """Exit code of a short probe, None if it could not run."""
    try:
        return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=5, creationflags=_NO_WINDOW).returncode
    except (OSError, subprocess.SubprocessError):
        return None


def old_service(home: Path, platform: str | None = None) -> tuple[str, str] | None:
    """(what, how to remove it) for the old program's autostart when it is
    installed on this machine, else None."""
    platform = platform or sys.platform
    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"
        target = f"gui/{os.getuid()}/{LAUNCH_AGENT}" if hasattr(os, "getuid") else ""
        loaded = bool(target) and _quiet(["launchctl", "print", target]) == 0
        if not (loaded or plist.exists()):
            return None
        state = "loaded" if loaded else "installed but not loaded"
        return (f"its LaunchAgent {LAUNCH_AGENT} is {state}",
                f"launchctl bootout gui/$(id -u)/{LAUNCH_AGENT}; "
                f"rm ~/Library/LaunchAgents/{LAUNCH_AGENT}.plist")
    if platform == "win32":
        if _quiet(["schtasks", "/Query", "/TN", WINDOWS_TASK]) != 0:
            return None
        return (f"its scheduled task {WINDOWS_TASK} is installed",
                f"schtasks /Delete /TN {WINDOWS_TASK} /F")
    return None


def _copy_settings(src: Path, dst: Path) -> bool:
    """Carry over the old settings keys Ensemble does not set yet. True when
    the file changed."""
    try:
        old = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    try:
        cur = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    except ValueError:
        return False  # a broken Ensemble file is Ensemble's to deal with
    if not isinstance(cur, dict):
        return False
    add = {k: old[k] for k in SETTINGS_KEYS if k in old and k not in cur}
    if not add:
        return False
    cur.update(add)
    tmp = dst.with_name(dst.name + ".migrate.tmp")
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    os.replace(tmp, dst)
    return True


def migrate(home: Path, state_dir: Path, platform: str | None = None) -> str | None:
    """Copy claude-dashboard's state into `state_dir` once; return the line to
    log, or None when there is nothing to say."""
    legacy = home / LEGACY_REL
    if not legacy.is_dir():
        return None
    marker = state_dir / MARKER_NAME
    service = old_service(home, platform)
    where = "~/.claude/dashboard"
    if marker.exists():
        # Already copied. Only an old program still set to start is worth a line.
        if service:
            return (f"claude-dashboard: the old program is still at {where} and "
                    f"{service[0]}; remove it with: {service[1]}")
        return None

    copied, kept, failed = [], [], []
    state_dir.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        src, dst = legacy / name, state_dir / name
        if not src.is_file():
            continue
        if dst.exists():
            kept.append(name)
            continue
        try:
            shutil.copy2(src, dst)
            copied.append(name)
        except OSError:
            failed.append(name)
    if (legacy / "settings.json").is_file():
        try:
            if _copy_settings(legacy / "settings.json", state_dir / "settings.json"):
                copied.append("settings.json")
        except OSError:
            failed.append("settings.json")

    if not failed:
        # Written last, so a failed copy is retried on the next start.
        marker.write_text(json.dumps({
            "from": str(legacy), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "copied": copied, "kept": kept,
        }, indent=2), encoding="utf-8")

    parts = [f"claude-dashboard: copied {len(copied)} state file(s) from {where}"
             + (f" ({', '.join(copied)})" if copied else "")
             + (f", kept Ensemble's own {', '.join(kept)}" if kept else "")
             + (f", could not copy {', '.join(failed)} (retried next start)" if failed else "")
             + f"; the old program is still at {where}"]
    if service:
        parts.append(f"{service[0]}; remove it with: {service[1]}")
    else:
        parts.append("no LaunchAgent or scheduled task of it is installed")
    return "; ".join(parts)
