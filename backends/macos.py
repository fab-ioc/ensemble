"""macOS backend: drives iTerm2 via AppleScript (osascript), resolves processes
via lsof/ps, and opens the desktop via `open`. This is the original
ensemble behavior, relocated behind the Backend interface unchanged."""
from __future__ import annotations

import plistlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .base import (
    Backend, CS_ROOT, HOME, ICON_CACHE_DIR, NUMBERED_RE, PRESETS_DIR, app_slug,
    editor_id, jetbrains_homes,
)
from .shared import SESS_DIR, claude_cmd, load_geometries, save_geometry
from . import themes

# shlex only needed to quote the initial prompt for the shell `write text` call.
import os
import shlex

IJ_APP = os.environ.get("ENSEMBLE_IJ_APP", "IntelliJ IDEA")  # legacy fallback

# The editors' app bundle names (a prefix: "IntelliJ IDEA CE.app" is IntelliJ
# IDEA's); the first is also the name Launch Services is asked for.
_APP_NAMES = {
    "idea": ("IntelliJ IDEA",), "pycharm": ("PyCharm",), "webstorm": ("WebStorm",),
    "goland": ("GoLand",), "rustrover": ("RustRover",), "rubymine": ("RubyMine",),
    "phpstorm": ("PhpStorm",), "clion": ("CLion",), "rider": ("Rider",),
    "code": ("Visual Studio Code",), "cursor": ("Cursor",), "xcode": ("Xcode",),
}


_FOCUS_SCRIPT = """
on run argv
    set targetTTY to item 1 of argv
    tell application "iTerm"
        repeat with W in windows
            repeat with T in tabs of W
                repeat with S in sessions of T
                    if (tty of S) is targetTTY then
                        tell W to select
                        tell T to select
                        tell S to select
                        activate
                        return "ok"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return "not_found"
end run
"""

_OPEN_SCRIPT = """
on run argv
    set wd to item 1 of argv
    set cmdStr to item 2 of argv
    set openMode to item 3 of argv
    -- Bounds are 4 numbers appended to argv when the caller wants to
    -- restore window geometry. Only meaningful in "window" mode.
    set hasGeom to (count of argv) is 7
    tell application "iTerm"
        if openMode is "tab" and (count of windows) > 0 then
            tell current window
                set newTab to (create tab with default profile)
            end tell
            set newSess to current session of newTab
        else
            -- Fallback to a new window: explicit "window" mode, or "tab"
            -- was asked but no window is open yet.
            set newWin to (create window with default profile)
            if hasGeom then
                set bounds of newWin to {item 4 of argv as integer, item 5 of argv as integer, item 6 of argv as integer, item 7 of argv as integer}
            end if
            set newSess to current session of newWin
        end if
        tell newSess
            write text "cd " & quoted form of wd & " && " & cmdStr
        end tell
        activate
        return tty of newSess
    end tell
end run
"""

# iTerm's "Merge All Windows" menu action, invoked via System Events. Requires
# macOS Accessibility permission for /usr/bin/osascript.
_MERGE_WINDOWS_SCRIPT = """
tell application "iTerm" to activate
delay 0.15
tell application "System Events"
    tell process "iTerm2"
        click menu item "Merge All Windows" of menu 1 of menu bar item "Window" of menu bar 1
    end tell
end tell
"""

# Move each of the given TTYs to its own window. Uses iTerm's "Move Session
# to New Window" menu action. Requires macOS Accessibility permission.
_SPLIT_SESSIONS_SCRIPT = """
on run argv
    tell application "iTerm" to activate
    delay 0.15
    repeat with i from 1 to (count of argv)
        set targetTTY to item i of argv
        set foundIt to false
        tell application "iTerm"
            set winList to windows
            repeat with W in winList
                if (count of tabs of W) > 1 and not foundIt then
                    repeat with T in tabs of W
                        if not foundIt then
                            repeat with S in sessions of T
                                if (tty of S) is targetTTY then
                                    tell W to select T
                                    tell T to select S
                                    set foundIt to true
                                    exit repeat
                                end if
                            end repeat
                        end if
                    end repeat
                end if
            end repeat
        end tell
        if foundIt then
            tell application "System Events"
                tell process "iTerm2"
                    try
                        click menu item "Move Session to New Window" of menu 1 of menu bar item "Shell" of menu bar 1
                    on error
                        try
                            click menu item "Move Tab to New Window" of menu 1 of menu bar item "Window" of menu bar 1
                        end try
                    end try
                end tell
            end tell
            delay 0.2
        end if
    end repeat
end run
"""

# Reserved claude subcommands — if the initial prompt starts with one of these
# tokens, commander.js parses it as a subcommand instead of a user prompt.
# Prepend a space to disambiguate.
_CLAUDE_RESERVED_SUBCOMMANDS = frozenset({
    "agents", "auth", "auto-mode", "doctor", "gateway", "install",
    "mcp", "plugin", "plugins", "project", "setup-token",
    "ultrareview", "update", "upgrade",
})

_ACCESS_HINT = ("A macOS Accessibility permission is required. Look for a "
                "system dialog asking to allow osascript, OR open "
                "System Settings → Privacy & Security → Accessibility, "
                "click + and add /usr/bin/osascript. Then click again.")


def _safe_claude_prompt(text: str) -> str:
    """Return a prompt safe to pass as claude's positional arg."""
    if not text:
        return text
    first = text.lstrip().split(None, 1)
    if first and first[0].lower() in _CLAUDE_RESERVED_SUBCOMMANDS:
        return " " + text
    return text


def _osascript_error_summary(err: str) -> str:
    """Normalize the common macOS Accessibility gate into a UI-actionable
    message. Everything else falls through as-is."""
    low = (err or "").lower()
    if "assistive access" in low or "-1719" in low or "not authorized" in low:
        return ("accessibility_required: macOS won't let osascript click iTerm menus. "
                "Open System Settings → Privacy & Security → Accessibility, click +, "
                "and add /usr/bin/osascript. Then try again.")
    return err.strip() or "unknown"

_CLOSE_SCRIPT = """
on run argv
    set targetTTY to item 1 of argv
    tell application "iTerm"
        repeat with W in windows
            repeat with T in tabs of W
                repeat with S in sessions of T
                    if (tty of S) is targetTTY then
                        set b to bounds of W
                        set boundsStr to (item 1 of b as text) & " " & (item 2 of b as text) & " " & (item 3 of b as text) & " " & (item 4 of b as text)
                        tell S to close
                        return boundsStr
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return ""
end run
"""

_SET_NAME_SCRIPT = """
on run argv
    set targetTTY to item 1 of argv
    set newName to item 2 of argv
    tell application "iTerm"
        repeat with W in windows
            repeat with T in tabs of W
                repeat with S in sessions of T
                    if (tty of S) is targetTTY then
                        tell S to set name to newName
                        return "ok"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return "not_found"
end run
"""

_SEND_SCRIPT = """
on run argv
    set targetTTY to item 1 of argv
    set theText to item 2 of argv
    set doSubmit to (item 3 of argv is "1")
    tell application "iTerm"
        repeat with W in windows
            repeat with T in tabs of W
                repeat with S in sessions of T
                    if (tty of S) is targetTTY then
                        if doSubmit then
                            tell S to write text theText
                        else
                            tell S to write text theText newline NO
                        end if
                        return "ok"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return "not_found"
end run
"""

_APPLESCRIPT_COLOR_KEYS = {
    "Foreground Color": "foreground color",
    "Background Color": "background color",
    "Bold Color": "bold color",
    # "Link Color" omitted — `link color` clashes with an AppleScript identifier
    # and triggers a parser error in iTerm's dictionary when set via `tell S`.
    "Selection Color": "selection color",
    "Selected Text Color": "selected text color",
    "Cursor Color": "cursor color",
    "Cursor Text Color": "cursor text color",
    "Ansi 0 Color": "ANSI black color",
    "Ansi 1 Color": "ANSI red color",
    "Ansi 2 Color": "ANSI green color",
    "Ansi 3 Color": "ANSI yellow color",
    "Ansi 4 Color": "ANSI blue color",
    "Ansi 5 Color": "ANSI magenta color",
    "Ansi 6 Color": "ANSI cyan color",
    "Ansi 7 Color": "ANSI white color",
    "Ansi 8 Color": "ANSI bright black color",
    "Ansi 9 Color": "ANSI bright red color",
    "Ansi 10 Color": "ANSI bright green color",
    "Ansi 11 Color": "ANSI bright yellow color",
    "Ansi 12 Color": "ANSI bright blue color",
    "Ansi 13 Color": "ANSI bright magenta color",
    "Ansi 14 Color": "ANSI bright cyan color",
    "Ansi 15 Color": "ANSI bright white color",
}


def _color_to_16bit(c: dict) -> tuple[int, int, int] | None:
    try:
        return (
            int(round(c["Red Component"] * 65535)),
            int(round(c["Green Component"] * 65535)),
            int(round(c["Blue Component"] * 65535)),
        )
    except (KeyError, TypeError):
        return None


def _strip_preset_suffix(raw: str) -> str:
    raw = raw.strip()
    return raw[:-len(".itermcolors")] if raw.endswith(".itermcolors") else raw


class MacBackend(Backend):
    os_name = "darwin"
    terminal_name = "iTerm"
    file_manager_name = "Finder"
    # Language → editor id (backends.base.EDITORS) for Automatic.
    default_editor_map = {
        "java":       "idea",
        "kotlin":     "idea",
        "scala":      "idea",
        "python":     "pycharm",
        "javascript": "webstorm",
        "typescript": "webstorm",
        "rust":       "rustrover",
        "go":         "goland",
        "ruby":       "rubymine",
        "php":        "phpstorm",
        "swift":      "xcode",
        "default":    "code",
    }
    features = {"focus": True, "themes": True, "geometry": True, "liveTitle": True,
                "consolidate": True, "split": True, "send": True}

    def __init__(self):
        # Throttled "iTerm tab name follows label" re-pusher. Some shells
        # overwrite the session name on every prompt; we re-push periodically so
        # the dashboard's label sticks.
        self._label_push_at: dict[int, float] = {}
        self._label_push_interval = 15.0  # seconds

    # ---------- process introspection ----------

    def live_cwd_of_pid(self, pid: int) -> str | None:
        """Resolve a process's cwd via lsof. Works even after the dir was
        renamed (the inode tracking gives us the new path)."""
        try:
            r = subprocess.run(
                ["lsof", "-p", str(pid), "-a", "-d", "cwd", "-F", "n"],
                capture_output=True, text=True, timeout=2,
            )
        except subprocess.SubprocessError:
            return None
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            if line.startswith("n") and len(line) > 1:
                return line[1:]
        return None

    def _tty_of_pid(self, pid: int) -> str | None:
        try:
            out = subprocess.check_output(
                ["ps", "-o", "tty=", "-p", str(pid)], text=True, timeout=2
            ).strip()
        except subprocess.SubprocessError:
            return None
        if not out or out == "?":
            return None
        return out if out.startswith("/dev/") else "/dev/" + out

    # ---------- terminal control ----------

    def focus(self, session: dict) -> str:
        pid = session.get("pid")
        tty = self._tty_of_pid(pid) if pid else None
        if not tty:
            return "no_tty"
        try:
            r = subprocess.run(
                ["osascript", "-e", _FOCUS_SCRIPT, tty],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() or r.stderr.strip() or "unknown"
        except subprocess.SubprocessError as e:
            return f"error: {e}"

    def send_text(self, pid: int, text: str, submit: bool = True) -> str:
        """Inject text into a live iTerm session (the chat doorbell). iTerm's
        `write text` delivers to the session by tty; with a newline it submits."""
        if not pid:
            return "no_pid"
        tty = self._tty_of_pid(int(pid))
        if not tty:
            return "no_tty"
        try:
            r = subprocess.run(
                ["osascript", "-e", _SEND_SCRIPT, tty, text, "1" if submit else "0"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() or r.stderr.strip() or "unknown"
        except subprocess.SubprocessError as e:
            return f"error: {e}"

    def open_new(self, cwd: str, initial_prompt: str = "", label: str = "",
                 session_id: str = "", model: str | None = None,
                 open_mode: str = "window", command: list[str] | None = None,
                 agent: str = "", identity: str = "",
                 env: dict | None = None, extra_args: list[str] | None = None) -> str:
        if command:
            # Non-Claude agent (e.g. codex): run its argv verbatim.
            cmd_str = shlex.join(command)
        else:
            extra = []
            if session_id:
                extra += ["--session-id", session_id]
            if model:
                extra += ["--model", model]
            # The launch's own flags (its MCP config, its settings), as on
            # Windows (ED-216).
            extra += [str(a) for a in extra_args or []]
            cmd_str = claude_cmd(*(shlex.quote(a) for a in extra))
            if initial_prompt:
                cmd_str = f"{cmd_str} {shlex.quote(_safe_claude_prompt(initial_prompt))}"
        args = ["osascript", "-e", _OPEN_SCRIPT, cwd, cmd_str, open_mode or "window"]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except subprocess.SubprocessError as e:
            return f"error: {e}"
        if r.returncode != 0:
            return r.stderr.strip() or "error"
        new_tty = r.stdout.strip()
        theme = self.current_theme_for_cwd(cwd)
        if theme and new_tty:
            self._apply_theme_tty(new_tty, theme, cwd)
        if label and new_tty:
            self._set_iterm_name(new_tty, label)
        return "ok"

    def open_resume(self, cwd: str, session_id: str, initial_prompt: str = "",
                    label: str = "", command: list[str] | None = None,
                    agent: str = "", identity: str = "") -> str:
        if command:
            # Non-Claude agent (e.g. `codex resume <id>`): run verbatim.
            cmd = shlex.join(command)
        else:
            cmd = claude_cmd("--resume", session_id)
            if initial_prompt:
                cmd = f"{cmd} {shlex.quote(initial_prompt)}"
        geom = load_geometries().get(session_id)
        # Resumed sessions always reopen as a window (never a tab) so saved
        # geometry can be restored.
        args = ["osascript", "-e", _OPEN_SCRIPT, cwd, cmd, "window"]
        if geom:
            args += [
                str(geom["x"]), str(geom["y"]),
                str(geom["x"] + geom["w"]), str(geom["y"] + geom["h"]),
            ]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except subprocess.SubprocessError as e:
            return f"error: {e}"
        if r.returncode != 0:
            return r.stderr.strip() or "error"
        new_tty = r.stdout.strip()
        theme = self.current_theme_for_cwd(cwd)
        if theme and new_tty:
            self._apply_theme_tty(new_tty, theme, cwd)
        if label and new_tty:
            self._set_iterm_name(new_tty, label)
        return "ok"

    def close(self, session: dict) -> str:
        pid = session.get("pid")
        session_id = session.get("sessionId", "")
        tty = self._tty_of_pid(pid) if pid else None
        if not tty:
            return "no_tty"
        try:
            r = subprocess.run(
                ["osascript", "-e", _CLOSE_SCRIPT, tty],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.SubprocessError as e:
            return f"error: {e}"
        if r.returncode != 0:
            return r.stderr.strip() or "error"
        out = r.stdout.strip()
        if out and session_id:
            try:
                parts = [int(x) for x in out.split()]
                if len(parts) == 4:
                    save_geometry(session_id, (parts[0], parts[1], parts[2], parts[3]))
            except ValueError:
                pass
        # iTerm sent SIGHUP via session close, but the claude process can linger
        # for a beat (writing its final state). Make sure it's actually dead
        # before we return — otherwise the next /api/sessions poll would still
        # see the session as live and the dashboard would flicker.
        if pid:
            self.terminate(pid)
            try:
                (SESS_DIR / f"{pid}.json").unlink()
            except (FileNotFoundError, OSError):
                pass
        return "ok"

    def _set_iterm_name(self, tty: str, name: str) -> None:
        """Fire-and-forget AppleScript: set the iTerm session's name."""
        if not (tty and name):
            return
        try:
            subprocess.run(
                ["osascript", "-e", _SET_NAME_SCRIPT, tty, name],
                capture_output=True, timeout=3,
            )
        except subprocess.SubprocessError:
            pass

    def maybe_resync_label(self, pid: int, label: str) -> None:
        if not label or not pid:
            return
        now = time.time()
        last = self._label_push_at.get(pid, 0)
        if now - last < self._label_push_interval:
            return
        self._label_push_at[pid] = now
        tty = self._tty_of_pid(pid)
        if not tty:
            return
        try:
            # Popen, not run — fire-and-forget so /api/sessions stays snappy.
            subprocess.Popen(
                ["osascript", "-e", _SET_NAME_SCRIPT, tty, label],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except subprocess.SubprocessError:
            pass

    def push_label(self, session_id: str, label: str) -> None:
        """Best-effort: update the iTerm tab name for a live session."""
        if not label:
            return
        from .shared import read_session_files
        for s in read_session_files():
            if s.get("sessionId") != session_id:
                continue
            pid = s.get("pid")
            if not pid:
                return
            tty = self._tty_of_pid(pid)
            if not tty:
                return
            try:
                subprocess.run(
                    ["osascript", "-e", _SET_NAME_SCRIPT, tty, label],
                    capture_output=True, timeout=3,
                )
            except subprocess.SubprocessError:
                pass
            return

    # ---------- window management ----------

    def consolidate_windows(self) -> tuple[bool, str]:
        try:
            r = subprocess.run(
                ["osascript", "-e", _MERGE_WINDOWS_SCRIPT],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            return False, _ACCESS_HINT
        except subprocess.SubprocessError as e:
            return False, f"subprocess error: {e}"
        if r.returncode == 0:
            return True, "ok"
        return False, _osascript_error_summary(r.stderr)

    def split_live(self, sessions: list[dict]) -> tuple[bool, str]:
        # Resolve TTYs from the live sessions' pids; skip anything unattached.
        ttys = []
        for s in sessions:
            pid = s.get("pid")
            if not isinstance(pid, int):
                continue
            tty = self._tty_of_pid(pid)
            if tty:
                ttys.append(tty)
        if not ttys:
            return True, "ok"
        try:
            r = subprocess.run(
                ["osascript", "-e", _SPLIT_SESSIONS_SCRIPT, *ttys],
                capture_output=True, text=True, timeout=5 + 2 * len(ttys),
            )
        except subprocess.TimeoutExpired:
            return False, _ACCESS_HINT
        except subprocess.SubprocessError as e:
            return False, f"subprocess error: {e}"
        if r.returncode == 0:
            return True, "ok"
        return False, _osascript_error_summary(r.stderr)

    # ---------- self-update ----------

    def self_restart(self, plan: dict) -> dict:
        """A detached helper preflights, then restarts only this hub."""
        import app_version
        state = Path(plan["log"]).parent.parent / "_launch"
        state.mkdir(parents=True, exist_ok=True)
        path = state / f"restart-{time.time_ns()}.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        if app_version.packaged():
            argv = [sys.executable, "--run", "restart_helper", str(path)]
        else:
            argv = [sys.executable, str(Path(plan["repo"]) / "restart_helper.py"), str(path)]
        result = self.start_detached(argv, Path(plan["log"]))
        if not result.get("started"):
            path.unlink(missing_ok=True)
        return result

    def self_update(self, install_dir, port=None, log_file=None) -> dict:
        """Spawn `ensemble update` detached so it survives our restart
        (launchctl kickstart -k SIGKILLs us as part of the refresh). Through
        bash, so a launcher that lost its executable bit still runs; with the
        hub's port, so a restart without the LaunchAgent comes back on it; its
        output appended to the hub's log, so a failed restart says why."""
        cli = Path(install_dir) / "ensemble"
        if not cli.exists():
            return {"started": False, "error": f"{cli} not found"}
        log = Path(log_file) if log_file else HOME / "Library" / "Logs" / "ensemble.log"
        argv = [shutil.which("bash") or "/bin/bash", str(cli), "update"]
        if port:
            argv += ["--port", str(port)]
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "ab") as out:
                subprocess.Popen(
                    argv,
                    cwd=str(install_dir),
                    stdout=out, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=dict(os.environ, ENSEMBLE_STDIO_IS_LOG="1"),
                )
        except (subprocess.SubprocessError, OSError) as e:
            return {"started": False, "error": f"{e.__class__.__name__}: {e}"}
        return {"started": True, "pid": os.getpid(), "log": str(log)}

    # ---------- themes ----------

    def list_themes(self) -> list[dict]:
        # iTerm `.itermcolors` presets (for theming a real iTerm tab) PLUS the
        # cross-platform built-in palette (used to recolor the headless chat
        # window, so the picker works even with no presets installed).
        out: list[dict] = []
        seen: set[str] = set()
        if PRESETS_DIR.is_dir():
            for p in sorted(PRESETS_DIR.glob("*.itermcolors")):
                out.append({"name": p.stem, "file": p.name})
                seen.add(p.stem.lower())
        for n in themes.names():
            if n.lower() not in seen:
                out.append({"name": n, "file": n})
        return out

    def current_theme_for_cwd(self, cwd: str) -> str:
        """Read the active preset from <cwd>/.iterm-preset (cs convention).
        If missing and cwd is ~/cs/NN_<slug>, fall back to a sibling
        ~/cs/NN_<other>/ that has one."""
        if not cwd:
            return ""
        p = Path(cwd)
        try:
            raw = (p / ".iterm-preset").read_text().strip()
            if raw:
                return _strip_preset_suffix(raw)
        except (FileNotFoundError, OSError):
            pass
        # Sibling lookup
        try:
            if p.parent.resolve() == CS_ROOT.resolve():
                m = NUMBERED_RE.match(p.name)
                if m:
                    prefix = m.group(1) + "_"
                    candidates = []
                    for sib in CS_ROOT.iterdir():
                        if sib == p or not sib.is_dir() or not sib.name.startswith(prefix):
                            continue
                        pf = sib / ".iterm-preset"
                        if pf.exists():
                            try:
                                candidates.append((pf.stat().st_mtime, pf))
                            except OSError:
                                pass
                    if candidates:
                        candidates.sort(reverse=True)
                        try:
                            raw = candidates[0][1].read_text().strip()
                            if raw:
                                return _strip_preset_suffix(raw)
                        except OSError:
                            pass
        except OSError:
            pass
        return ""

    def apply_theme(self, session: dict, theme: str, cwd: str = "") -> str:
        pid = session.get("pid")
        tty = self._tty_of_pid(pid) if pid else None
        if not tty:
            # No live tty (e.g. historical session) — persist for next Open.
            return self.set_theme_for_cwd(cwd, theme)
        return self._apply_theme_tty(tty, theme, cwd)

    def set_theme_for_cwd(self, cwd: str, theme: str) -> str:
        """Persist the preset to <cwd>/.iterm-preset for the next Open, without
        touching a running session."""
        if not cwd:
            return "no_cwd"
        try:
            (Path(cwd) / ".iterm-preset").write_text(
                f"{theme}.itermcolors\n", encoding="utf-8")
        except OSError as e:
            return f"error: {e}"
        return "queued"

    def _apply_theme_tty(self, tty: str, preset_name: str, cwd: str = "") -> str:
        preset_path = PRESETS_DIR / f"{preset_name}.itermcolors"
        if not preset_path.exists():
            return f"preset not found: {preset_name}"
        try:
            with preset_path.open("rb") as f:
                preset = plistlib.load(f)
        except Exception as e:
            return f"error parsing preset: {e}"

        lines = []
        for plist_key, as_prop in _APPLESCRIPT_COLOR_KEYS.items():
            c = preset.get(plist_key)
            if not isinstance(c, dict):
                continue
            rgb = _color_to_16bit(c)
            if rgb is None:
                continue
            # Inside the `tell S` block — no `of S` suffix (matches cs script).
            lines.append(f"                            set {as_prop} to {{{rgb[0]}, {rgb[1]}, {rgb[2]}}}")
        if not lines:
            return "preset has no colors"

        script = (
            'on run argv\n'
            '    set targetTTY to item 1 of argv\n'
            '    tell application "iTerm"\n'
            '        repeat with W in windows\n'
            '            repeat with T in tabs of W\n'
            '                repeat with S in sessions of T\n'
            '                    if (tty of S) is targetTTY then\n'
            '                        tell S\n'
            + "\n".join(lines) + "\n"
            '                        end tell\n'
            '                        return "ok"\n'
            '                    end if\n'
            '                end repeat\n'
            '            end repeat\n'
            '        end repeat\n'
            '    end tell\n'
            '    return "not_found"\n'
            'end run\n'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script, tty],
                capture_output=True, text=True, timeout=5,
            )
            result = r.stdout.strip() or r.stderr.strip() or "unknown"
        except subprocess.SubprocessError as e:
            return f"error: {e}"

        # Persist the choice in <cwd>/.iterm-preset so `cs change theme` and the
        # dashboard share the same source of truth.
        if result == "ok" and cwd:
            try:
                (Path(cwd) / ".iterm-preset").write_text(f"{preset_name}.itermcolors\n")
            except OSError:
                pass
        return result

    def prepare_session_theme(self, target_dir: Path) -> None:
        """Seed a random iTerm preset into a freshly created `+ New` session."""
        if PRESETS_DIR.is_dir():
            presets = list(PRESETS_DIR.glob("*.itermcolors"))
            if presets:
                chosen = random.choice(presets)
                try:
                    (target_dir / ".iterm-preset").write_text(chosen.name + "\n")
                except OSError:
                    pass

    # ---------- desktop integration ----------

    def open_path(self, path: str, app: str = "default", command: bool = False) -> str:
        p = Path(path)
        if not p.exists():
            return f"path does not exist: {path}"
        if app in ("", "default", "finder"):
            return self._run_open(["open", str(p)])
        if app == "ij":
            # Back-compat: legacy clients sending app="ij" — use IJ_APP default.
            app = IJ_APP
        label, target, extra = self.launch_target(app, command)
        if target and not target.endswith(".app"):
            # A command (a CLI launcher on PATH): it takes the folder itself.
            try:
                subprocess.Popen([target, *extra, str(p)], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return "ok"
            except (OSError, subprocess.SubprocessError) as e:
                return f"error: {e}"
        # An app: its bundle when found, else by name, which Launch Services
        # may still know (an app outside every folder looked in).
        if command:
            name = target or (self._command_argv(app) or [app])[0]
        else:
            name = target or _APP_NAMES.get(editor_id(app), (app,))[0]
        res = self._run_open(["open", "-na", name, "--args", *extra, str(p)])
        if res == "ok" or target:
            return res
        self._run_open(["open", str(p)])
        return (f"{label} was not found on this Mac, so the folder opened in Finder "
                f"instead. Choose the editor in Settings › Editor.")

    def _run_open(self, args: list[str]) -> str:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return "ok"
            return r.stderr.strip() or f"exit {r.returncode}"
        except (OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"

    # ---------- finding an editor ----------

    def _app_dirs(self) -> list[Path]:
        """Where apps are installed: the system's and the person's
        Applications folders, and JetBrains Toolbox's folder in each."""
        home = Path.home()
        return [Path("/Applications"), home / "Applications",
                home / "Applications" / "JetBrains Toolbox",
                Path("/Applications") / "JetBrains Toolbox"]

    def _caches_dir(self) -> Path:
        return Path.home() / "Library" / "Caches" / "JetBrains"

    def _locate_editor(self, eid: str) -> str | None:
        """The editor's .app: where a JetBrains version's .home file says it
        is, else the newest bundle by name in an Applications folder
        (Toolbox installs included)."""
        for home in jetbrains_homes(eid, self._caches_dir()):
            app = next((q for q in (home, *home.parents) if q.suffix == ".app"), None)
            if app and app.is_dir():
                return str(app)
        found = []
        for d in self._app_dirs():
            for name in _APP_NAMES.get(eid, ()):
                try:
                    found += [a for a in d.glob(name + "*.app") if a.is_dir()]
                except OSError:
                    pass
        if found:
            return str(max(found, key=lambda a: a.stat().st_mtime))
        return None

    def _locate_program(self, cmd: str) -> str | None:
        """A program the person named: a .app path, an app's name in an
        Applications folder, or a command on PATH."""
        cmd = (cmd or "").strip().strip('"\'')
        if not cmd:
            return None
        if cmd.endswith(".app") and Path(cmd).is_dir():
            return cmd
        name = cmd[:-4] if cmd.endswith(".app") else cmd
        if "/" not in name:
            for d in self._app_dirs():
                if (d / f"{name}.app").is_dir():
                    return str(d / f"{name}.app")
        if Path(cmd).is_file():
            return cmd
        return shutil.which(cmd)

    def editor_icon_path(self, app_name: str) -> Path | None:
        """Cached PNG of the app's icon, extracted from its .app (a path, or a
        name in /Applications) via `sips` on first use. None if not installed or extraction fails."""
        if not app_name:
            return None
        app_path = Path(app_name) if app_name.endswith(".app") else Path(f"/Applications/{app_name}.app")
        cache = ICON_CACHE_DIR / f"{app_slug(app_path.stem)}.png"
        if cache.exists():
            return cache
        if not app_path.exists():
            return None
        info = app_path / "Contents" / "Info.plist"
        try:
            with info.open("rb") as f:
                plist = plistlib.load(f)
        except (OSError, Exception):
            return None
        icon_name = plist.get("CFBundleIconFile") or plist.get("CFBundleIconName")
        if not icon_name:
            return None
        if not icon_name.endswith(".icns"):
            icon_name += ".icns"
        icns = app_path / "Contents" / "Resources" / icon_name
        if not icns.exists():
            return None
        try:
            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["sips", "-s", "format", "png", str(icns),
                 "--out", str(cache), "-z", "64", "64"],
                capture_output=True, check=True, timeout=10,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return None
        return cache if cache.exists() else None
