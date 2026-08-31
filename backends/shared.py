"""Shared, non-OS-specific state used by both dashboard.py and the platform
backends: session-file reading, window-geometry persistence, and the `claude`
command builder. Kept here (not in dashboard.py) so backends can import it
without a circular dependency."""
from __future__ import annotations

import json
import os

from .base import DASHBOARD_DIR, HOME

SESS_DIR = HOME / ".claude" / "sessions"
RENAME_WORKSPACE = DASHBOARD_DIR / "_rename_workspace"
GEOMETRIES_FILE = DASHBOARD_DIR / "geometries.json"
# Registry for sessions the dashboard *launches* for non-Claude agents (codex,
# …). Claude self-registers in SESS_DIR; other CLIs don't, so our launch script
# writes a <pid>.json here (pid = the hosting shell, which shares the terminal
# console — so it doubles as the keystroke-injection target). This is how a
# codex session becomes "live" and reachable by the send/doorbell mechanism.
AGENT_SESS_DIR = DASHBOARD_DIR / "agents"

# Permission mode for sessions the dashboard launches. "bypassPermissions"
# auto-approves everything (no "yes?" prompts). Override with the env var, e.g.
# CLAUDE_DASHBOARD_PERMISSION_MODE=acceptEdits  (or "" to disable the flag).
PERMISSION_MODE = os.environ.get("CLAUDE_DASHBOARD_PERMISSION_MODE", "bypassPermissions")


def claude_cmd_args(*extra: str) -> list[str]:
    """Build the `claude` argv (with the configured permission mode) as a list."""
    parts = ["claude", *extra]
    if PERMISSION_MODE:
        parts += ["--permission-mode", PERMISSION_MODE]
    return parts


def claude_cmd(*extra: str) -> str:
    """Build a `claude` invocation with the configured permission mode."""
    return " ".join(claude_cmd_args(*extra))


# ---------- window geometry (used by the macOS backend) ----------

def load_geometries() -> dict[str, dict]:
    try:
        return json.loads(GEOMETRIES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_geometries(geom: dict) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = GEOMETRIES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(geom, indent=2, sort_keys=True))
    tmp.replace(GEOMETRIES_FILE)


def save_geometry(sid: str, bounds: tuple[int, int, int, int]) -> None:
    g = load_geometries()
    x1, y1, x2, y2 = bounds
    g[sid] = {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}
    save_geometries(g)


# ---------- live session files ----------

_WS = str(RENAME_WORKSPACE)


def is_workspace_cwd(cwd: str) -> bool:
    """True if cwd is our transient `claude -p` rename workspace."""
    return cwd == _WS or cwd.startswith(_WS + os.sep)


def read_session_files() -> list[dict]:
    """Read ~/.claude/sessions/<pid>.json for every live, interactive session."""
    from . import get_backend  # lazy: avoids import cycle at module load
    backend = get_backend()
    out = []
    if not SESS_DIR.exists():
        return out
    for f in SESS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pid = d.get("pid")
        if not pid or not backend.process_alive(pid):
            continue
        if is_workspace_cwd(d.get("cwd", "") or ""):
            continue  # transient `claude -p` invoked by us
        # Skip non-interactive claude processes (e.g. `--bg-spare` daemons that
        # Claude Code spawns as background workers). They register a metadata
        # file but aren't real chat sessions.
        kind = d.get("kind", "")
        if kind and kind != "interactive":
            continue
        out.append(d)
    return out


def read_agent_session_files() -> list[dict]:
    """Read dashboard-launched agent registry files (AGENT_SESS_DIR/<pid>.json).

    Each record describes a non-Claude session we launched — its hosting shell
    pid, the assigned identity, agent key, and cwd. Records whose pid has died
    are pruned on read (the clean-exit path also removes them, but a killed
    session leaves a stale file). Returns only live records."""
    from . import get_backend  # lazy: avoids import cycle at module load
    backend = get_backend()
    out = []
    if not AGENT_SESS_DIR.exists():
        return out
    for f in AGENT_SESS_DIR.glob("*.json"):
        try:
            # utf-8-sig: tolerate a BOM if a PowerShell 5.1 fallback wrote one.
            d = json.loads(f.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        pid = d.get("pid")
        if not pid or not backend.process_alive(int(pid)):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        out.append(d)
    return out
