"""Platform backend interface for ensemble.

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
import shlex
import shutil
import signal
import threading
import time
from pathlib import Path

from . import themes

HOME = Path.home()
# Ensemble keeps its own state in a neutral, agent-agnostic home — NOT nested
# inside any single agent's config dir. (Agent adapters still read each agent's
# own data, e.g. ~/.claude/projects or ~/.codex/sessions.)
DASHBOARD_DIR = HOME / ".ensemble"
# The predecessor's state (~/.claude/dashboard) is copied in by
# legacy_install.migrate() when the hub starts, never moved.
PRESETS_DIR = DASHBOARD_DIR / "iterm-presets"   # macOS / iTerm only
CS_ROOT = HOME / "cs"
# The product name lives in ONE place; user-facing folder names derive from it,
# so a future rename is a single edit here (plus a migration of existing
# folders). ~/.ensemble (state) and the OS task/scripts are swept later.
APP_NAME = "Ensemble"
# Where the user's projects live — and, under each project, its tasks/sessions.
# Successor of CS_ROOT (~/cs). Meant to be a git repo the user backs up, so a
# rebuilt machine recovers every project and the knowledge built inside it.
PROJECTS_ROOT = HOME / f"{APP_NAME}Projects"
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


# The editors Open in editor knows by name (Settings › Editor offers those
# found installed), as (id, label). JetBrains IDEs come first: when the
# language's editor is missing, Automatic takes the first one installed.
EDITORS = [
    ("idea",      "IntelliJ IDEA"),
    ("pycharm",   "PyCharm"),
    ("webstorm",  "WebStorm"),
    ("goland",    "GoLand"),
    ("rustrover", "RustRover"),
    ("rubymine",  "RubyMine"),
    ("phpstorm",  "PhpStorm"),
    ("clion",     "CLion"),
    ("rider",     "Rider"),
    ("code",      "VS Code"),
    ("cursor",    "Cursor"),
    ("xcode",     "Xcode"),
]
EDITOR_LABELS = dict(EDITORS)
# JetBrains IDEs: the folder names each version keeps its caches under. Each
# holds a ``.home`` file naming where that version is installed, wherever the
# person put it (an unpacked zip outside every standard folder included).
_JETBRAINS_DIRS = {
    "idea":      ("IntelliJIdea", "IdeaIC"),
    "pycharm":   ("PyCharm", "PyCharmCE"),
    "webstorm":  ("WebStorm",),
    "goland":    ("GoLand",),
    "rustrover": ("RustRover",),
    "rubymine":  ("RubyMine",),
    "phpstorm":  ("PhpStorm",),
    "clion":     ("CLion",),
    "rider":     ("Rider",),
}


def jetbrains_homes(eid: str, caches: Path) -> list[Path]:
    """Where the ``.home`` files under ``caches`` say ``eid`` is installed,
    newest version first; only folders that still exist."""
    found = []
    for prefix in _JETBRAINS_DIRS.get(eid, ()):
        try:
            dirs = [d for d in caches.glob(prefix + "*")
                    if re.fullmatch(re.escape(prefix) + r"\d[\d.]*", d.name)]
        except OSError:
            continue
        for d in dirs:
            try:
                text = (d / ".home").read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                continue
            home = Path(text) if text else None
            if home and home.is_absolute() and home.is_dir():
                ver = tuple(int(x) for x in re.findall(r"\d+", d.name[len(prefix):]))
                found.append((ver, home))
    return [h for _v, h in sorted(found, key=lambda x: x[0], reverse=True)]


# Older names a hand-written editors.json may use for an editor.
_EDITOR_ALIASES = {"ij": "idea", "intellij": "idea", "visual studio code": "code",
                   "vscode": "code", "idea64": "idea", "pycharm64": "pycharm"}
# Settings › Editor's "Other command…".
EDITOR_CUSTOM = "custom"
# How long a look for the installed editors holds: each Open in editor and
# each page's platform read would look again otherwise.
_EDITOR_FIND_TTL = 30.0


def editor_id(value: str) -> str:
    """The catalogue id ``value`` names (an id, a label or an older name), or ""."""
    v = (value or "").strip().lower()
    if v.endswith(".app"):
        v = v[:-4].rsplit("/", 1)[-1]
    if v in EDITOR_LABELS:
        return v
    if v in _EDITOR_ALIASES:
        return _EDITOR_ALIASES[v]
    for eid, label in EDITORS:
        if v == label.lower() or (eid == "idea" and v.startswith("intellij idea")):
            return eid
    return ""


def command_label(cmd: str) -> str:
    """How a command is named to the person: its program's name."""
    name = re.split(r"[\\/]", (cmd or "").strip().strip('"'))[-1]
    return re.sub(r"\.(exe|cmd|bat|app)$", "", name, flags=re.I) or cmd


def split_command(cmd: str, posix: bool = True) -> list[str]:
    """A command line as its program and arguments: shell words on macOS and
    Linux, Windows' own rules (CommandLineToArgvW's) on Windows, where a
    backslash is a path's, not an escape."""
    cmd = (cmd or "").strip()
    if not cmd:
        return []
    if posix:
        try:
            return shlex.split(cmd)
        except ValueError:            # an unclosed quote: the line as typed
            return [cmd.strip('"\'')]
    return _split_windows(cmd)


def _split_windows(s: str) -> list[str]:
    # The program: up to the closing quote, else the first blank (no escapes).
    if s.startswith('"'):
        end = s.find('"', 1)
        if end < 0:
            return [s[1:]]
        args, rest = [s[1:end]], s[end + 1:]
    else:
        m = re.match(r"[^ \t]+", s)
        args, rest = [m.group()], s[m.end():]
    cur: list[str] = []
    quoted = started = False
    i = 0
    while i < len(rest):
        c = rest[i]
        if c == "\\":
            j = i
            while j < len(rest) and rest[j] == "\\":
                j += 1
            n = j - i
            if j < len(rest) and rest[j] == '"':
                # 2n backslashes and a quote: n backslashes, then the quote
                # toggles; 2n+1: n backslashes and a literal quote.
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    j += 1
            else:
                cur.append("\\" * n)
            i, started = j, True
            continue
        if c == '"':
            if quoted and rest[i + 1:i + 2] == '"':
                cur.append('"')
                i += 2
                continue
            quoted, started = not quoted, True
        elif c in " \t" and not quoted:
            if started:
                args.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(c)
            started = True
        i += 1
    if started:
        args.append("".join(cur))
    return args


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

    def process_started(self, pid: int) -> float | None:
        """When the process holding ``pid`` was created (epoch seconds), or
        None when unknown. Default: unknown."""
        return None

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

    def open_resume(self, cwd: str, session_id: str, initial_prompt: str = "",
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

    def self_update(self, install_dir: Path, port: int | None = None,
                    log_file: Path | None = None) -> dict:
        """Pull the latest code and restart the running server. Platform-specific
        (launchd on macOS, Task Scheduler on Windows). `port` is the hub's own,
        for a restart without the service; the update's output is appended to
        `log_file` (the hub's log). Default: not wired up."""
        return {"started": False, "error": "self-update not supported on this platform"}

    def self_restart(self, plan: dict) -> dict:
        """Stop and start the running server on the code on disk, no git, from
        a helper that outlives it (see dashboard.restart_plan). Default: not
        wired up."""
        return {"started": False, "error": "a plain restart is not supported on this platform"}

    def start_detached(self, argv: list[str], log_file: Path) -> dict:
        """Start ``argv`` so that it outlives the hub (the built app's update
        helper): its own session, so stopping the hub's service does not end
        it; its output appended to ``log_file``."""
        import subprocess
        log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_file, "ab") as out:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out,
                                        stderr=subprocess.STDOUT, close_fds=True,
                                        start_new_session=True)
        except OSError as e:
            return {"started": False, "error": f"{e.__class__.__name__}: {e}"}
        return {"started": True, "helperPid": proc.pid}

    # ---------- themes ----------

    def list_themes(self) -> list[dict]:
        # Cross-platform built-in palette — every platform can offer these; a
        # platform may override to merge its own scheme source (WT / iTerm).
        return [{"name": n, "file": n} for n in themes.names()]

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
        chat window, from the cross-platform built-in palette."""
        return themes.colors_for(name)

    def prepare_session_theme(self, target_dir: Path) -> None:
        """Hook for `+ New`: seed a theme marker in a freshly created session dir."""
        return None

    # ---------- desktop integration ----------

    def open_path(self, path: str, app: str = "default", command: bool = False) -> str:
        """Open ``path`` in ``app``: an editor id or name, or (``command``)
        Settings › Editor's Other command…, run as typed."""
        return "unsupported"

    def load_editor_map(self) -> dict[str, str]:
        """Language → editor for Automatic: the platform's map, under what a
        hand-written editors.json says (read only, never written)."""
        base = dict(self.default_editor_map)
        try:
            user = json.loads(EDITOR_MAP_FILE.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                for k, v in user.items():
                    if isinstance(k, str) and isinstance(v, str):
                        base[k] = v
        except (OSError, ValueError):
            pass
        return base

    # --- finding an editor (platforms override _locate_editor / _locate_command) ---

    def _locate_editor(self, eid: str) -> str | None:
        """Where the catalogue editor ``eid`` is installed (what launches it),
        or None. Default: its command on PATH."""
        return shutil.which(eid)

    # How a command line splits: shell words, or Windows' rules (windows.py).
    posix_command_line = True

    def _locate_program(self, prog: str) -> str | None:
        """What launches a program the person named (a path or a name on
        PATH), or None when not found."""
        prog = (prog or "").strip().strip('"')
        if not prog:
            return None
        if os.path.isfile(prog):
            return prog
        return shutil.which(prog)

    def _command_argv(self, cmd: str) -> list[str]:
        """Other command…'s program and its arguments ("code -n", a quoted
        path and flags). A whole line that names a program is one, unsplit: an
        unquoted path with blanks, or an app's name ("Sublime Text")."""
        bare = (cmd or "").strip().strip('"\'')
        if not bare:
            return []
        if self._locate_program(bare):
            return [bare]
        return split_command(cmd, posix=self.posix_command_line) or [bare]

    def _locate_command(self, cmd: str) -> str | None:
        """What launches a command the person typed, or None when not found."""
        argv = self._command_argv(cmd)
        return self._locate_program(argv[0]) if argv else None

    def _command_label(self, cmd: str) -> str:
        argv = self._command_argv(cmd)
        return command_label(argv[0] if argv else cmd)

    # --- the installed editors: one look for them all, held _EDITOR_FIND_TTL
    # and refreshed in the background, so no page read waits on the folders ---

    def _scan_editors(self) -> dict[str, str | None]:
        """One look for every catalogue editor (platforms may share work
        between them)."""
        found: dict[str, str | None] = {}
        for eid, _label in EDITORS:
            try:
                found[eid] = self._locate_editor(eid)
            except OSError:
                found[eid] = None
        return found

    def _editor_state(self) -> dict:
        st = self.__dict__.get("_editor_found")
        if st is None:
            st = self.__dict__.setdefault("_editor_found", {
                "at": 0.0, "found": None, "first": threading.Lock(),
                "guard": threading.Lock(), "busy": False})
        return st

    def editors_found(self) -> dict[str, str | None]:
        """Where each catalogue editor is (None: not found). The last look is
        served; one older than _EDITOR_FIND_TTL is looked at again in the
        background. Only the very first look is waited for."""
        st = self._editor_state()
        found = st["found"]
        if found is None:
            with st["first"]:
                if st["found"] is None:
                    st["found"], st["at"] = self._scan_editors(), time.monotonic()
            return st["found"]
        if time.monotonic() - st["at"] >= _EDITOR_FIND_TTL:
            self._rescan_editors(st)
        return found

    def _rescan_editors(self, st: dict) -> None:
        with st["guard"]:
            if st["busy"]:
                return
            st["busy"] = True

        def run():
            try:
                st["found"], st["at"] = self._scan_editors(), time.monotonic()
            except Exception:
                st["at"] = time.monotonic()      # try again a TTL later
            finally:
                st["busy"] = False
        threading.Thread(target=run, name="editor-scan", daemon=True).start()

    def warm_editors(self) -> None:
        """Look for the editors now, in the background (at the hub's start)."""
        threading.Thread(target=self.editors_found, name="editor-scan", daemon=True).start()

    def find_editor(self, eid: str) -> str | None:
        """Where the catalogue editor ``eid`` is, from the held look."""
        return self.editors_found().get(eid)

    def installed_editors(self) -> list[dict]:
        """The catalogue's editors found on this computer, in its order."""
        out = []
        found = self.editors_found()
        for eid, label in EDITORS:
            where = found.get(eid)
            if where:
                out.append({"id": eid, "label": label, "path": where})
        return out

    def choose_editor(self, lang: str, setting: dict | None = None) -> dict:
        """What Open in editor opens for a repo of language ``lang``.

        ``setting`` is Settings › Editor: ``editor`` ("" Automatic, an id of
        EDITORS, or "custom") and ``editorCommand``. Returns ``{id, label,
        path, how}``: ``id`` is what /api/open-path is sent (an id or the
        command), ``path`` what launches it ("" when not found), ``how``
        "chosen" | "language" | "fallback" | "none". Automatic never names an
        editor that is not installed; with none found, ``id`` is "" and the
        folder opens instead."""
        setting = setting or {}
        want = setting.get("editor") or ""
        if want == EDITOR_CUSTOM:
            cmd = str(setting.get("editorCommand") or "").strip()
            if cmd:
                return {"id": cmd, "label": self._command_label(cmd),
                        "path": self._locate_command(cmd) or "", "how": "chosen"}
            want = ""
        if want in EDITOR_LABELS:
            return {"id": want, "label": EDITOR_LABELS[want],
                    "path": self.find_editor(want) or "", "how": "chosen"}
        mapped = self.load_editor_map()
        pref = mapped.get(lang, mapped.get("default", ""))
        eid = editor_id(pref)
        if eid and self.find_editor(eid):
            return {"id": eid, "label": EDITOR_LABELS[eid], "path": self.find_editor(eid),
                    "how": "language"}
        if pref and not eid:
            where = self._locate_command(pref)
            if where:
                return {"id": pref, "label": self._command_label(pref), "path": where, "how": "language"}
        found = self.installed_editors()
        if found:
            return {"id": found[0]["id"], "label": found[0]["label"], "path": found[0]["path"],
                    "how": "fallback"}
        return {"id": "", "label": "", "path": "", "how": "none"}

    def editor_button_label(self, setting: dict | None = None) -> str:
        """The editor every repo opens in, named on the button; "" when it
        depends on the repo's language, or when it is not found (the folder
        opens then, and the button must not say the editor does)."""
        langs = set(self.load_editor_map()) | {"default"}
        labels = set()
        for lang in langs:
            c = self.choose_editor(lang, setting)
            labels.add(c["label"] if c["path"] else "")
        return labels.pop() if len(labels) == 1 else ""

    def launch_target(self, app: str, command: bool = False) -> tuple[str, str | None, list[str]]:
        """(label, what launches it or None, the arguments before the folder)
        for what /api/open-path was sent: a catalogue id, an older editor name,
        or a command. Other command… (``command``) runs as typed, never as the
        catalogue editor its name looks like ("IntelliJ IDEA 2023.app" is that
        app, not the newest IntelliJ)."""
        eid = "" if command else editor_id(app)
        if eid:
            return EDITOR_LABELS[eid], self.find_editor(eid), []
        argv = self._command_argv(app) or [app]
        return command_label(argv[0]), self._locate_program(argv[0]), argv[1:]

    def editor_icon_path(self, app_name: str) -> Path | None:
        return None

    def resolve_editor_for_repo(self, repo_path: Path,
                                setting: dict | None = None) -> tuple[str, dict, str | None]:
        """Return (language, choose_editor's choice, icon_url_or_None) for a repo."""
        lang = detect_repo_language(repo_path)
        choice = self.choose_editor(lang, setting)
        icon = self.editor_icon_path(choice["path"] or choice["label"]) if choice["id"] else None
        icon_url = f"/static/editors/{icon.name}" if icon else None
        return lang, choice, icon_url

    # ---------- metadata ----------

    def info(self) -> dict:
        return {
            "os": self.os_name,
            "terminalName": self.terminal_name,
            "fileManagerName": self.file_manager_name,
            "editorDefault": self.default_editor_map.get("default", ""),
            "features": dict(self.features),
        }
