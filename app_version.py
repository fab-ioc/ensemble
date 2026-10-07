"""Which Ensemble this is: its version, and whether it runs as the built app
(Ensemble.exe / Ensemble.app) or from a source checkout.

Standard library only: the hub, its hooks and the release build all import it.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# The release workflow refuses a tag that is not "v" + this.
VERSION = "0.9.0"

# Where releases are published: the app's updates come from here.
REPO = "fab-ioc/ensemble"


def packaged() -> bool:
    """True in the built app: PyInstaller sets ``sys.frozen``; Nuitka leaves a
    ``__compiled__`` in every module it compiled."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def app_executable() -> Path:
    """The built app's own program (``Ensemble.exe``; on a Mac the one in
    ``Ensemble.app/Contents/MacOS``)."""
    return Path(sys.executable).resolve()


def app_root(exe: Path | None = None, platform: str = sys.platform) -> Path:
    """What an update replaces: the folder holding Ensemble.exe on Windows, the
    ``Ensemble.app`` bundle on a Mac."""
    exe = exe or app_executable()
    if platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
    return exe.parent


def script_argv(name: str, path: Path, python: str | None = None,
                frozen: bool | None = None) -> list[str]:
    """How to run one of the hub's scripts (``agent_hook``, ``task_tool_hook``,
    ``usage_statusline``, ``workspace_search``, ``global_search``) as a process
    of its own: through the app itself when built (``ensemble_app.py``), else
    with this Python. ``path`` is the script's file in a checkout."""
    python = python or sys.executable
    if packaged() if frozen is None else frozen:
        return [python, "--run", name]
    return [python, str(path)]


def command_line(argv: list[str]) -> str:
    """``argv`` as the command string an agent's settings file holds: every
    part quoted, forward slashes (Claude Code runs it through a shell)."""
    out = []
    for part in argv:
        if part.startswith("--"):
            out.append(part)
        else:
            out.append(f'"{Path(part).as_posix()}"')
    return " ".join(out)


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?([0-9A-Za-z.]+))?$")


def parse_version(text: str) -> tuple | None:
    """``1.2.3`` or ``v1.2.3`` (a pre-release like ``1.2.3-beta.1`` sorts
    before ``1.2.3``); None for anything else."""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    # A release (no suffix) sorts after any pre-release of the same number.
    return (int(major), int(minor), int(patch), (1,) if not pre else (0, pre))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


def describe() -> dict:
    """What /api/version and Settings show."""
    return {"version": VERSION, "packaged": packaged(), "repo": REPO,
            "executable": str(app_executable()) if packaged() else sys.executable,
            "pid": os.getpid()}
