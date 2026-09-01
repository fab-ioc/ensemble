"""Platform backend interface for claude-dashboard.

`Backend` defines every OS-specific operation the dashboard needs: process
introspection, terminal control (open/focus/close/themes), and desktop
integration (open folder, open in editor). The base class ships POSIX-friendly
defaults so a platform only has to override what differs.

Shared path constants live here too so the platform backends can import them
without depending on `dashboard.py` (which would be a circular import —
`dashboard.py` imports *this* package, not the other way around).
"""
from __future__ import annotations

import json
import os
import re
import signal
import time
from pathlib import Path

HOME = Path.home()
DASHBOARD_DIR = HOME / ".claude" / "dashboard"
PRESETS_DIR = HOME / ".claude" / "iterm-presets"   # macOS / iTerm only
CS_ROOT = HOME / "cs"
EDITOR_MAP_FILE = DASHBOARD_DIR / "editors.json"
ICON_CACHE_DIR = DASHBOARD_DIR / "static" / "editors"
NUMBERED_RE = re.compile(r"^(\d+)_")


# Marker-file → language detection. Pure filesystem logic, shared by every
# platform; the editor *map* (language → app) is platform-specific.
_LANG_CHECKS = [
    ("pom.xml",          "java"),
    ("build.gradle",     "java"),
    ("build.gradle.kts", "kotlin"),
    ("Cargo.toml",       "rust"),
    ("go.mod",           "go"),
    ("pyproject.toml",   "python"),
    ("setup.py",         "python"),
    ("requirements.txt", "python"),
    ("Gemfile",          "ruby"),
    ("composer.json",    "php"),
    ("Package.swift",    "swift"),
    ("build.sbt",        "scala"),
]


def detect_repo_language(path: Path) -> str:
    """Best-effort language detection from a repo's top-level project markers."""
    if not path.is_dir():
        return "default"
    for fname, lang in _LANG_CHECKS:
        if (path / fname).exists():
            return lang
    if (path / "package.json").exists():
        return "typescript" if (path / "tsconfig.json").exists() else "javascript"
    return "default"


def app_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


class Backend:
    """Cross-platform base. Defaults are safe POSIX/no-op behaviors; platform
    subclasses override the parts that differ. Methods that touch a terminal we
    can't drive return the string ``"unsupported"`` so the UI can surface it."""

    # --- platform metadata (overridden per platform) ---
    os_name = "posix"
    terminal_name = "terminal"
    file_manager_name = "file manager"
    default_editor_map: dict[str, str] = {"default": "Visual Studio Code"}
    features = {
        "focus": False,
        "themes": False,       # False | True | "launch-only"
        "geometry": False,
        "liveTitle": False,
        "consolidate": False,  # merge all terminal windows into one (iTerm only)
        "split": False,        # explode live sessions into separate windows
        "send": False,         # inject text into a live session's terminal (chat doorbell)
    }

    # ---------- process introspection ----------

    def process_alive(self, pid: int) -> bool:
        """POSIX default: signal 0 probes the process without touching it.
        (Windows MUST override — os.kill there calls TerminateProcess.)"""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def live_cwd_of_pid(self, pid: int) -> str | None:
        """Resolve a live process's cwd. Default: unknown (recorded cwd is used)."""
        return None

    def terminate(self, pid: int) -> None:
        """POSIX default: SIGTERM, wait briefly, then SIGKILL."""
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return
        deadline = time.time() + 1.5
        while time.time() < deadline and self.process_alive(pid):
            time.sleep(0.05)
        if self.process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    # ---------- terminal control ----------

    def focus(self, session: dict) -> str:
        return "unsupported"

    def open_resume(self, cwd: str, session_id: str, fork: bool = False,
                    new_session_id: str | None = None, initial_prompt: str = "",
                    label: str = "", command: list[str] | None = None,
                    agent: str = "", identity: str = "") -> str:
        # `command`, when given, is a fully-built argv (e.g. from a non-Claude
        # agent like `codex resume <id>`); the backend launches it verbatim
        # instead of composing a `claude` invocation. `agent`/`identity`, when
        # set, ask the backend to register the launched session for liveness +
        # message injection (platform support varies).
        return "unsupported"

    def open_new(self, cwd: str, initial_prompt: str = "", label: str = "",
                 session_id: str = "", model: str | None = None,
                 open_mode: str = "window", command: list[str] | None = None,
                 agent: str = "", identity: str = "",
                 env: dict | None = None, extra_args: list[str] | None = None) -> str:
        return "unsupported"

    def headless_launch(self, cwd: str, argv: list[str], prompt: str = ""):
        """Build a command to run `argv` in a headless PTY (backends/ptyrun).
        Default (POSIX): return the argv list with the prompt appended as a
        positional; ptyrun spawns it directly under a Unix pty. Windows overrides
        to wrap it in a one-shot .ps1 (robust multiline-prompt quoting)."""
        cmd = list(argv)
        if prompt:
            cmd.append(prompt)
        return cmd

    def close(self, session: dict) -> str:
        """Default: just terminate the process (no window geometry)."""
        pid = session.get("pid")
        if pid:
            self.terminate(pid)
        return "ok"

    def send_text(self, pid: int, text: str, submit: bool = True) -> str:
        """Inject text into a live session's terminal (the chat "doorbell" that
        wakes an agent to read a message). Default: unsupported."""
        return "unsupported"

    def maybe_resync_label(self, pid: int, label: str) -> None:
        return None

    def push_label(self, session_id: str, label: str) -> None:
        return None

    # ---------- window management (iTerm-only niceties) ----------

    def consolidate_windows(self) -> tuple[bool, str]:
        """Merge all terminal windows into one (tabs). Default: unsupported."""
        return False, "unsupported"

    def split_live(self, sessions: list[dict]) -> tuple[bool, str]:
        """Explode the given live sessions into separate windows. Default: unsupported."""
        return False, "unsupported"

    # ---------- self-update ----------

    def self_update(self, install_dir: Path) -> dict:
        """Pull the latest code and restart the running server. Platform-specific
        (launchd on macOS, Task Scheduler on Windows). Default: not wired up."""
        return {"started": False, "error": "self-update not supported on this platform"}

    # ---------- themes ----------

    def list_themes(self) -> list[dict]:
        return []

    def apply_theme(self, session: dict, theme: str, cwd: str = "") -> str:
        return "unsupported"

    def set_theme_for_cwd(self, cwd: str, theme: str) -> str:
        """Set a session's theme without a running terminal (e.g. a historical
        session) — writes the theme marker so the next Open uses it."""
        return "unsupported"

    def current_theme_for_cwd(self, cwd: str) -> str:
        return ""

    def theme_colors(self, name: str) -> dict:
        """Resolve a theme/scheme name to normalized colors for the headless
        chat window. Empty when the backend can't map colors."""
        return {}

    def prepare_session_theme(self, target_dir: Path) -> None:
        """Hook for `+ New`: seed a theme marker in a freshly created session dir."""
        return None

    # ---------- desktop integration ----------

    def open_path(self, path: str, app: str = "default") -> str:
        return "unsupported"

    def load_editor_map(self) -> dict[str, str]:
        base = dict(self.default_editor_map)
        try:
            user = json.loads(EDITOR_MAP_FILE.read_text())
            if isinstance(user, dict):
                for k, v in user.items():
                    if isinstance(k, str) and isinstance(v, str):
                        base[k] = v
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return base

    def editor_icon_path(self, app_name: str) -> Path | None:
        return None

    def resolve_editor_for_repo(self, repo_path: Path) -> tuple[str, str, str | None]:
        """Return (language, editor_app_name, icon_url_or_None) for a repo."""
        lang = detect_repo_language(repo_path)
        editors = self.load_editor_map()
        editor = editors.get(lang, editors.get("default", ""))
        icon_url = None
        if editor and self.editor_icon_path(editor):
            icon_url = f"/static/editors/{app_slug(editor)}.png"
        return lang, editor, icon_url

    # ---------- metadata ----------

    def info(self) -> dict:
        return {
            "os": self.os_name,
            "terminalName": self.terminal_name,
            "fileManagerName": self.file_manager_name,
            "editorDefault": self.default_editor_map.get("default", ""),
            "features": dict(self.features),
        }
