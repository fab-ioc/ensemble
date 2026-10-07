"""First run and sign-in start for the app.

* Which coding agents this machine has: ``claude`` and ``codex`` installed, and
  signed in as their own CLIs report it (``claude auth status``, ``codex login
  status``). Ensemble never reads their sign-in files or tokens.
* Start Ensemble when I sign in: the Run key on Windows (no admin needed), a
  LaunchAgent on a Mac (what ``install-launchd.sh`` writes for a checkout).

Standard library only.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

AGENTS = (
    {"key": "claude", "name": "Claude Code",
     "installUrl": "https://docs.anthropic.com/en/docs/claude-code/setup",
     "signIn": "Run claude in a terminal and sign in."},
    {"key": "codex", "name": "Codex",
     "installUrl": "https://developers.openai.com/codex/cli",
     "signIn": "Run codex login in a terminal."},
)


def _signed_in(key: str, exe: str, run=subprocess.run) -> bool | None:
    """What the agent's own CLI says; None when it would not say (too old,
    timed out). Only the yes/no is kept: never the account it names."""
    argv = [exe, "auth", "status"] if key == "claude" else [exe, "login", "status"]
    try:
        r = run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if key == "claude":
        try:
            return bool(json.loads(r.stdout or "").get("loggedIn"))
        except (ValueError, AttributeError):
            return None if r.returncode not in (0, 1) else r.returncode == 0
    # codex login status: 0 signed in, 1 not.
    return r.returncode == 0 if r.returncode in (0, 1) else None


def agent_status(agent: dict, which=shutil.which, run=subprocess.run) -> dict:
    exe = which(agent["key"])
    out = {**agent, "installed": bool(exe), "signedIn": False}
    if exe:
        out["signedIn"] = _signed_in(agent["key"], exe, run)
    out["ready"] = out["installed"] and out["signedIn"] is not False
    return out


def summary(agents: list[dict]) -> dict:
    """Whether the agents let Ensemble work: one ready agent is enough."""
    ready = [a["key"] for a in agents if a["ready"]]
    return {"agents": agents, "ready": ready, "usable": bool(ready),
            "missing": [a["key"] for a in agents if not a["ready"]]}


_CACHE: tuple[float, dict] | None = None
_LOCK = threading.Lock()
CACHE_S = 60


def agents_check(refresh: bool = False, which=shutil.which, run=subprocess.run) -> dict:
    """Both agents, checked side by side; kept a minute."""
    global _CACHE
    with _LOCK:
        if not refresh and _CACHE and time.time() - _CACHE[0] < CACHE_S:
            return _CACHE[1]
    results: list[dict] = [{}] * len(AGENTS)

    def one(i: int, a: dict) -> None:
        results[i] = agent_status(a, which, run)
    threads = [threading.Thread(target=one, args=(i, a), daemon=True) for i, a in enumerate(AGENTS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    out = summary([r or {**a, "installed": False, "signedIn": None, "ready": False}
                   for r, a in zip(results, AGENTS)])
    with _LOCK:
        _CACHE = (time.time(), out)
    return out


# ---------- start at sign-in ----------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Ensemble"
LAUNCHD_LABEL = "com.ensemble.dashboard"


def launch_agent_plist(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def autostart_command(exe: Path, port: int) -> list[str]:
    return [str(exe), "--port", str(port), "--background"]


def mac_path(home: Path | None = None, login_path: str = "") -> str:
    """The PATH the app runs agents with on a Mac: an app started from Finder
    or launchd gets only the system folders, and ``claude``/``codex`` live in
    the user's own (the login shell's, plus the usual install places)."""
    home = home or Path.home()
    parts = [p for p in (login_path or "").split(":") if p]
    for extra in (f"{home}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
                  f"{home}/.npm-global/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if extra not in parts:
            parts.append(extra)
    return ":".join(parts)


def login_shell_path(timeout: float = 10) -> str:
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        r = subprocess.run([shell, "-ilc", 'printf "%s" "$PATH"'], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def mac_launch_agent(exe: Path, port: int, log: Path, path: str) -> dict:
    """The LaunchAgent for the app, as ``install-launchd.sh`` writes it for a
    checkout (same label, so there is only ever one)."""
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [*autostart_command(exe, port), "--log", str(log)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False, "Crashed": True},
        "ThrottleInterval": 10,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {"PATH": path},
    }


def autostart_status(exe: Path, platform: str = sys.platform) -> dict:
    """Whether the app starts at sign-in, and whether that is this app."""
    if platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                value, _ = winreg.QueryValueEx(k, RUN_VALUE)
        except OSError:
            return {"enabled": False, "supported": True}
        return {"enabled": True, "supported": True, "command": value,
                "thisApp": str(exe).lower() in str(value).lower()}
    if platform == "darwin":
        plist = launch_agent_plist()
        try:
            data = plistlib.loads(plist.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            return {"enabled": False, "supported": True}
        args = data.get("ProgramArguments") or []
        return {"enabled": True, "supported": True, "command": " ".join(map(str, args)),
                "thisApp": bool(args) and str(args[0]) == str(exe)}
    return {"enabled": False, "supported": False}


def set_autostart(enabled: bool, exe: Path, port: int, log: Path,
                  platform: str = sys.platform) -> dict:
    if platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if enabled:
                cmd = subprocess.list2cmdline(autostart_command(exe, port))
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return autostart_status(exe, platform)
    if platform == "darwin":
        plist = launch_agent_plist()
        if enabled:
            plist.parent.mkdir(parents=True, exist_ok=True)
            log.parent.mkdir(parents=True, exist_ok=True)
            data = mac_launch_agent(exe, port, log, os.environ.get("PATH", ""))
            plist.write_bytes(plistlib.dumps(data))
            # Loaded for the next sign-in; not started now (this app is the
            # hub already running).
        else:
            # Not booted out: when launchd runs this hub, that would stop it.
            # Without the file it is not started at the next sign-in.
            plist.unlink(missing_ok=True)
        return autostart_status(exe, platform)
    return {"enabled": False, "supported": False}
