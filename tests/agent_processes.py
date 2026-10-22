"""The agent processes (Claude Code, Codex, a shell running an agent launch
script) among this process's descendants: what a test run must never leave
behind (#257). Not a test module; used by test_no_agent_launch.py and
test_zz_no_agent_started.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_launch  # noqa: E402


def _processes() -> list[dict]:
    """[{pid, ppid, cmd}] for every process on the machine."""
    if sys.platform == "win32":
        ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-CimInstance Win32_Process"
              " | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = json.loads(out or "[]", strict=False)     # prompts hold line breaks
        rows = rows if isinstance(rows, list) else [rows]
        return [{"pid": r.get("ProcessId"), "ppid": r.get("ParentProcessId"),
                 "cmd": r.get("CommandLine") or ""} for r in rows]
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=60).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]),
                         "cmd": parts[2] if len(parts) > 2 else ""})
    return rows


def _looks_like_agent(cmd: str) -> bool:
    words = cmd.replace('"', " ").split()
    if not words:
        return False
    if agent_launch._name(words[0]) in agent_launch.AGENTS:
        return True
    # A shell running a launch script (the script deletes itself once read
    # to the end, so its name is the evidence, not its content).
    return (agent_launch._name(words[0]) in agent_launch.SHELLS
            and any("_launch" in w and w.lower().endswith(".ps1") for w in words)) \
        or bool(agent_launch.agent_in(words))


def agent_descendants(root: int | None = None) -> list[dict]:
    """The agent processes started, directly or not, by ``root`` (this one)."""
    root = os.getpid() if root is None else root
    rows = _processes()
    kids: dict[int, list[dict]] = {}
    for r in rows:
        kids.setdefault(r["ppid"], []).append(r)
    found, todo, seen = [], [root], set()
    while todo:
        pid = todo.pop()
        for r in kids.get(pid, []):
            if r["pid"] in seen or r["pid"] == root:
                continue
            seen.add(r["pid"])
            todo.append(r["pid"])
            if _looks_like_agent(r["cmd"]):
                found.append(r)
    return found
