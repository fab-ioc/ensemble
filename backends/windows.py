"""Windows backend: drives Windows Terminal (wt.exe), introspects processes via
the Win32 API (ctypes), and opens the desktop via Explorer.

Notable platform limitations vs. macOS (surfaced through `features` so the UI
adapts):
  * focus    — no tty/handle maps a claude pane to a WT tab; not supported.
  * geometry — WT exposes no per-tab window bounds; not saved/restored.
  * themes   — WT can't re-theme a running tab, so a chosen color scheme is
               persisted to <cwd>/.wt-scheme and applied at the next Open via
               `--colorScheme` ("launch-only").
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path

from .base import Backend, DASHBOARD_DIR
from .shared import claude_cmd_args

LAUNCH_DIR = DASHBOARD_DIR / "_launch"
SCHEME_MARKER = ".wt-scheme"

# ---------- Win32 process helpers ----------

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5

# ---------- Win32 window helpers (for focus) ----------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = (_WNDENUMPROC, wintypes.LPARAM)
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetClassNameW.restype = ctypes.c_int
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.ShowWindow.restype = wintypes.BOOL
_user32.IsIconic.argtypes = (wintypes.HWND,)
_user32.IsIconic.restype = wintypes.BOOL

# Windows Terminal's top-level window class.
_WT_WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
_SW_RESTORE = 9

# Windows Terminal's built-in color schemes (shipped in the app's defaults.json,
# not the user's settings.json, but usable as --colorScheme values everywhere).
_WT_BUILTIN_SCHEMES = [
    "Campbell", "Campbell Powershell", "Vintage",
    "One Half Dark", "One Half Light",
    "Solarized Dark", "Solarized Light",
    "Tango Dark", "Tango Light",
]

# ---------- process-tree helpers (Toolhelp32, for Close) ----------

_TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
_kernel32.Process32NextW.restype = wintypes.BOOL

# Shells we launch sessions under; killing this ancestor closes the WT tab.
_SHELL_EXES = {"pwsh.exe", "powershell.exe"}
# Walking up must stop here — never kill the terminal host or the desktop.
_STOP_EXES = {"windowsterminal.exe", "explorer.exe", "openconsole.exe", "conhost.exe"}


def _process_map() -> dict[int, tuple[int, str]]:
    """Snapshot every process → {pid: (parent_pid, exe_name_lower)}."""
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out: dict[int, tuple[int, str]] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID), entry.szExeFile.lower())
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return out


def _shell_ancestor(pid: int) -> int | None:
    """Walk up from `pid` to the nearest pwsh/powershell ancestor that launched
    this session (stopping before the terminal host). Returns its pid, or None."""
    procs = _process_map()
    seen = set()
    cur = pid
    for _ in range(8):  # bounded walk — guards against cycles
        if cur in seen or cur not in procs:
            return None
        seen.add(cur)
        ppid, _exe = procs[cur]
        pexe = procs.get(ppid, (0, ""))[1]
        if pexe in _STOP_EXES:
            # Parent is the terminal host: `cur` is the shell we want (if it is one).
            cur_exe = procs[cur][1]
            return cur if cur_exe in _SHELL_EXES else None
        if pexe in _SHELL_EXES:
            return ppid
        cur = ppid
    return None


def _enum_wt_windows() -> list[tuple[int, str]]:
    """Return [(hwnd, title), …] for every visible Windows Terminal window."""
    found: list[tuple[int, str]] = []

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 256)
        if buf.value != _WT_WINDOW_CLASS:
            return True
        tbuf = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, tbuf, 512)
        found.append((int(hwnd), tbuf.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return found


def _bring_to_front(hwnd: int) -> None:
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)


class WindowsBackend(Backend):
    os_name = "win32"
    terminal_name = "Windows Terminal"
    file_manager_name = "Explorer"
    # Values double as the launch command (resolved via PATH) and button label.
    default_editor_map = {
        "java":       "idea",
        "kotlin":     "idea",
        "scala":      "idea",
        "python":     "pycharm",
        "javascript": "code",
        "typescript": "code",
        "rust":       "code",
        "go":         "code",
        "ruby":       "rubymine",
        "php":        "phpstorm",
        "default":    "code",
    }
    features = {"focus": True, "themes": "launch-only", "geometry": False,
                "liveTitle": False, "consolidate": False, "split": False}

    # ---------- process introspection ----------

    def process_alive(self, pid: int) -> bool:
        if not pid:
            return False
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            # No handle: either the pid is gone, or it exists but we lack rights.
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if _kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == _STILL_ACTIVE
            return True
        finally:
            _kernel32.CloseHandle(h)

    def terminate(self, pid: int) -> None:
        if not pid:
            return
        h = _kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if h:
            try:
                _kernel32.TerminateProcess(h, 1)
                return
            finally:
                _kernel32.CloseHandle(h)
        # Fallback: taskkill (also kills the child tree if claude spawned any).
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    # ---------- terminal control ----------

    def focus(self, session: dict) -> str:
        """Best-effort focus: bring the Windows Terminal window whose title
        matches this session's label to the front. Works when the session runs
        in its own titled window (the common case); it can't pinpoint a single
        tab inside a merged window. Falls back to the sole WT window if there's
        exactly one and no title match."""
        label = (session.get("label") or "").strip()
        windows = _enum_wt_windows()
        if not windows:
            return "no Windows Terminal window found"
        target = None
        if label:
            low = label.lower()
            for hwnd, title in windows:
                if title and low in title.lower():
                    target = hwnd
                    break
        if target is None:
            if len(windows) == 1:
                target = windows[0][0]  # only one WT window — must be it
            else:
                return "no_match"  # can't tell which window without a matching title
        try:
            _bring_to_front(target)
            return "ok"
        except OSError as e:
            return f"error: {e}"

    def open_new(self, cwd: str, initial_prompt: str = "", label: str = "",
                 session_id: str = "", model: str | None = None,
                 open_mode: str = "window") -> str:
        extra = ["--session-id", session_id] if session_id else []
        if model:
            extra += ["--model", model]
        return self._launch_wt(cwd, extra, initial_prompt, label, open_mode=open_mode)

    def open_resume(self, cwd: str, session_id: str, fork: bool = False,
                    new_session_id: str | None = None, initial_prompt: str = "",
                    label: str = "") -> str:
        extra = ["--resume", session_id]
        if fork:
            extra.append("--fork-session")
        if new_session_id:
            extra += ["--session-id", new_session_id]
        # Resumed/forked sessions always open in a new window.
        return self._launch_wt(cwd, extra, initial_prompt, label, open_mode="window")

    def close(self, session: dict) -> str:
        from .shared import SESS_DIR
        pid = session.get("pid")
        if not pid:
            return "no_pid"
        # Kill ONLY claude (and its worker children), NOT the parent pwsh. The
        # pwsh script is blocked at `& claude`; once claude dies it resumes,
        # resets the terminal, and exits 0 — so Windows Terminal graceful-closes
        # the tab on its own. (Killing pwsh instead would leave a "[process
        # exited]" tab, because WT keeps tabs whose process died with non-zero.)
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            self.terminate(pid)
        try:
            (SESS_DIR / f"{pid}.json").unlink()
        except (FileNotFoundError, OSError):
            pass
        return "ok"

    def _launch_wt(self, cwd: str, claude_extra: list[str], initial_prompt: str,
                   label: str, open_mode: str = "window") -> str:
        wt = shutil.which("wt")
        if not wt:
            return "Windows Terminal (wt.exe) not found"
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return "PowerShell not found"

        # Compose the `claude` argv (permission mode + caller extras). The
        # initial prompt is appended inside the script as a literal here-string
        # so any content (spaces, quotes, newlines) survives intact.
        args = claude_cmd_args(*claude_extra)
        script_path = self._write_launch_script(cwd, args, initial_prompt)

        # tab → open a tab in the most-recently-used window; window → new window.
        # `-w last` targets the last active WT window (creating one if none).
        if open_mode == "tab":
            cmd = [wt, "-w", "last", "new-tab", "-d", cwd]
        else:
            cmd = [wt, "-w", "new", "-d", cwd]
        if label:
            cmd += ["--title", label]
        scheme = self.current_theme_for_cwd(cwd)
        if scheme:
            cmd += ["--colorScheme", scheme]
        # No -NoExit: when claude ends (normal exit OR killed on Close), the
        # script finishes and pwsh exits 0, so WT graceful-closes the tab. The
        # script resets terminal modes before exiting, so no leftover garbage.
        cmd += [shell, "-ExecutionPolicy", "Bypass", "-File", script_path]
        try:
            subprocess.Popen(cmd, close_fds=True)
        except (OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"
        return "ok"

    def _write_launch_script(self, cwd: str, claude_argv: list[str],
                             initial_prompt: str) -> str:
        """Write a one-shot .ps1 that cd's into cwd, runs claude, then deletes
        itself. Using a script file (rather than a wt-parsed command line) keeps
        arbitrary cwd paths and prompts robust against wt's quoting."""
        LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
        script_path = LAUNCH_DIR / f"{uuid.uuid4().hex}.ps1"
        # Build the claude call. claude_argv tokens are simple (no spaces); the
        # prompt is the only free-form part and goes in a literal here-string.
        claude_line = " ".join(_ps_quote(a) for a in claude_argv)
        if initial_prompt:
            here = "@'\n" + initial_prompt.replace("\r\n", "\n") + "\n'@"
            claude_line = f"{claude_line} {here}"
        # After claude exits, reset any terminal modes it left enabled (xterm
        # mouse tracking 1000/1002/1003/1006, bracketed paste 2004, focus
        # reporting 1004, alt-screen 1049) so the surviving pwsh prompt doesn't
        # spew escape codes on mouse movement.
        reset_line = (
            "$__e = [char]27\n"
            "[Console]::Write("
            "\"$__e[?1000l$__e[?1002l$__e[?1003l$__e[?1006l"
            "$__e[?1004l$__e[?2004l$__e[?1049l\")\n"
        )
        body = (
            f"Set-Location -LiteralPath {_ps_quote(cwd)}\n"
            f"& {claude_line}\n"
            f"{reset_line}"
            f"Remove-Item -LiteralPath {_ps_quote(str(script_path))} "
            f"-ErrorAction SilentlyContinue\n"
        )
        script_path.write_text(body, encoding="utf-8")
        return str(script_path)

    # ---------- self-update ----------

    def self_update(self, install_dir) -> dict:
        """git fetch+reset the install dir, then restart the server. If we're
        running under the ClaudeDashboard scheduled task, bouncing the task
        kills this process and relaunches it windowless; otherwise fall back to
        `claude-dashboard.ps1 restart`. Runs detached so it survives our exit."""
        install_dir = Path(install_dir)
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return {"started": False, "error": "PowerShell not found"}
        LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
        script_path = LAUNCH_DIR / f"update-{uuid.uuid4().hex}.ps1"
        ps1 = install_dir / "claude-dashboard.ps1"
        body = (
            f"Set-Location -LiteralPath {_ps_quote(str(install_dir))}\n"
            "git fetch --quiet 2>$null\n"
            "$up = (git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null)\n"
            "if ($up) { git reset --hard $up 2>$null }\n"
            "$t = Get-ScheduledTask -TaskName ClaudeDashboard -ErrorAction SilentlyContinue\n"
            "if ($t) {\n"
            "  try { Stop-ScheduledTask -TaskName ClaudeDashboard -ErrorAction SilentlyContinue } catch {}\n"
            "  Start-Sleep -Seconds 1\n"
            "  Start-ScheduledTask -TaskName ClaudeDashboard\n"
            f"}} elseif (Test-Path {_ps_quote(str(ps1))}) {{\n"
            f"  & {_ps_quote(str(ps1))} restart\n"
            "}\n"
            f"Remove-Item -LiteralPath {_ps_quote(str(script_path))} -ErrorAction SilentlyContinue\n"
        )
        try:
            script_path.write_text(body, encoding="utf-8")
            subprocess.Popen(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", str(script_path)],
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return {"started": False, "error": f"{e.__class__.__name__}: {e}"}
        return {"started": True, "pid": os.getpid()}

    # ---------- themes ----------

    def list_themes(self) -> list[dict]:
        # Merge the user's custom schemes (settings.json) with WT's built-in
        # schemes — the built-ins live in the app's defaults.json, not the
        # user's settings, but are all valid `--colorScheme` values.
        names = set(_read_wt_scheme_names()) | set(_WT_BUILTIN_SCHEMES)
        return [{"name": n, "file": n} for n in sorted(names, key=str.lower)]

    def current_theme_for_cwd(self, cwd: str) -> str:
        if not cwd:
            return ""
        try:
            return (Path(cwd) / SCHEME_MARKER).read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return ""

    def apply_theme(self, session: dict, theme: str, cwd: str = "") -> str:
        return self.set_theme_for_cwd(cwd, theme)

    def set_theme_for_cwd(self, cwd: str, theme: str) -> str:
        if not cwd:
            return "no_cwd"
        try:
            (Path(cwd) / SCHEME_MARKER).write_text(theme + "\n", encoding="utf-8")
        except OSError as e:
            return f"error: {e}"
        # WT can't re-theme a live tab; the scheme is applied at next Open.
        return "queued"

    def prepare_session_theme(self, target_dir: Path) -> None:
        names = _read_wt_scheme_names()
        if not names:
            return
        # Deterministic-ish pick avoids importing random here; any scheme is fine.
        import random
        try:
            (target_dir / SCHEME_MARKER).write_text(
                random.choice(sorted(names)) + "\n", encoding="utf-8")
        except OSError:
            pass

    # ---------- desktop integration ----------

    def open_path(self, path: str, app: str = "default") -> str:
        p = Path(path)
        if not p.exists():
            return f"path does not exist: {path}"
        if app in ("", "default", "finder", "explorer"):
            # Launch explorer.exe directly rather than os.startfile: ShellExecute
            # can silently no-op when called from a windowless/background process
            # (the dashboard runs under pythonw.exe via a scheduled task).
            # explorer.exe returns exit code 1 even on success, so fire-and-forget.
            try:
                explorer = os.path.join(
                    os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                exe = explorer if os.path.exists(explorer) else "explorer"
                subprocess.Popen([exe, str(p)], close_fds=True)
                return "ok"
            except (OSError, subprocess.SubprocessError) as e:
                return f"error: {e}"
        if app == "ij":
            app = "idea"
        exe = shutil.which(app)
        if not exe:
            # Unknown editor command — fall back to opening the folder in Explorer.
            try:
                explorer = os.path.join(
                    os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                subprocess.Popen([explorer if os.path.exists(explorer) else "explorer",
                                  str(p)], close_fds=True)
                return f"editor not found: {app} (opened folder instead)"
            except (OSError, subprocess.SubprocessError) as e:
                return f"error: {e}"
        try:
            subprocess.Popen([exe, str(p)], close_fds=True)
            return "ok"
        except (OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"


# ---------- module helpers ----------

def _ps_quote(s: str) -> str:
    """Single-quote a string for PowerShell (doubling embedded single quotes)."""
    return "'" + s.replace("'", "''") + "'"


_WT_SETTINGS_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows Terminal" / "settings.json",
]


def _wt_settings_path() -> Path | None:
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        # Store-installed Windows Terminal lives under a Packages\...\LocalState dir.
        pkgs = Path(local) / "Packages"
        if pkgs.is_dir():
            for pkg in pkgs.glob("Microsoft.WindowsTerminal*"):
                cand = pkg / "LocalState" / "settings.json"
                if cand.exists():
                    return cand
    for cand in _WT_SETTINGS_CANDIDATES:
        if cand.exists():
            return cand
    return None


def _read_wt_scheme_names() -> list[str]:
    path = _wt_settings_path()
    if not path:
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    data = _loads_jsonc(raw)
    if not isinstance(data, dict):
        return []
    schemes = data.get("schemes")
    names: list[str] = []
    if isinstance(schemes, list):
        for s in schemes:
            if isinstance(s, dict) and isinstance(s.get("name"), str):
                names.append(s["name"])
    return names


def _loads_jsonc(text: str):
    """Parse JSON that may contain // and /* */ comments and trailing commas
    (Windows Terminal's settings.json is JSONC)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip block comments, then line comments, then trailing commas.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(^|[^:])//[^\n]*", lambda m: m.group(1), text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
