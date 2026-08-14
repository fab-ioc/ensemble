"""Linux backend (early stub).

Process introspection works (POSIX signals from the base class, plus /proc for
cwd) and the desktop opens via xdg-open. Terminal control (open/focus/close/
themes) is not wired up yet — those return "unsupported" so the UI degrades
gracefully. Flesh this out per terminal emulator (e.g. gnome-terminal,
konsole, wezterm) later."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .base import Backend


class LinuxBackend(Backend):
    os_name = "linux"
    terminal_name = "terminal"
    file_manager_name = "file manager"
    default_editor_map = {
        "java":       "idea",
        "kotlin":     "idea",
        "python":     "pycharm",
        "javascript": "code",
        "typescript": "code",
        "rust":       "code",
        "go":         "code",
        "default":    "code",
    }
    # Terminal control not implemented yet; process + desktop do work.
    features = {"focus": False, "themes": False, "geometry": False, "liveTitle": False,
                "consolidate": False, "split": False}

    def live_cwd_of_pid(self, pid: int) -> str | None:
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None

    def open_path(self, path: str, app: str = "default") -> str:
        p = Path(path)
        if not p.exists():
            return f"path does not exist: {path}"
        if app and app not in ("default", "finder"):
            import shutil
            exe = shutil.which(app)
            args = [exe or app, str(p)]
        else:
            args = ["xdg-open", str(p)]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "ok"
        except (OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"
