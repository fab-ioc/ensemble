"""Starting the built app: ``Ensemble`` with ``dashboard.py``'s arguments, plus

  --background      do not open the browser (the start at sign-in)
  --autostart on|off   start at sign-in, or not, and exit
  --port-status     say what holds the port (``key=value`` lines), and exit
  --stop-old-hub    stop an older Ensemble hub holding the port (and the
                    LaunchAgent that runs it), and exit; never anything else

Opened a second time (the Start menu entry, the Dock icon) while a hub
already serves the port, it opens the dashboard and exits. When the port is
held by something else (an older Ensemble, such as a hub run from a source
checkout, or another program) it says so in a dialog and the log, before it
changes anything; a hub that stops while starting says so too.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import app_version

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Files next to a checkout's dashboard.py (Ensemble's, or claude-dashboard's
# before it) and the LaunchAgent labels they name.
CHECKOUT_MARKERS = ("install-launchd.sh", "com.ensemble.dashboard.plist.template",
                    "com.claude-code.dashboard.plist.template")
CHECKOUT_LABELS = ("com.ensemble.dashboard", "com.claude-code.dashboard")
_PYTHON_RE = re.compile(r"^(python[\d.]*(\.exe)?|Python)$", re.I)
_TITLE_RE = re.compile(r"<title>\s*(Ensemble|Claude[ -]?(Code )?Dashboard)\s*</title>", re.I)


def _port(args: list[str]) -> int:
    if "--port" in args:
        return int(args[args.index("--port") + 1])
    return int(os.environ.get("ENSEMBLE_PORT", "8765"))


def log_path(home: Path | None = None, platform: str = sys.platform) -> Path:
    """Where the app logs (where a checkout's hub logs too:
    dashboard.DEFAULT_LOG_FILE)."""
    home = home or Path.home()
    return (home / "Library" / "Logs" / "ensemble.log" if platform == "darwin"
            else home / ".ensemble" / "logs" / "ensemble.log")


_LOG: Path | None = None


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} ensemble app: {msg}"
    try:
        if _LOG:
            _LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
    except OSError:
        pass
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError, AttributeError):
        pass


# ---------- what holds the port ----------

def _served(port: int) -> dict | None:
    """/api/version of the Ensemble hub on the port, None if there is none."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) and data.get("version") else None
    except (OSError, ValueError):
        return None


def _http(port: int, path: str) -> tuple[int, str] | None:
    """(status, start of the body) of a GET, None when nothing answers HTTP."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(65536).decode("utf-8", "replace")
        except OSError:
            body = ""
        return e.code, body
    except (OSError, ValueError):
        return None


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _tool(name: str, *fallbacks: str) -> str:
    for p in fallbacks:
        if os.path.exists(p):
            return p
    return shutil.which(name) or name


def _out(cmd: list[str], timeout: float = 5) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _holder(port: int) -> dict | None:
    """The process listening on the port, from lsof (macOS, Linux):
    {"pid", "command", "cwd"}; None when it cannot be told (Windows, a
    process of another user)."""
    if sys.platform == "win32":
        return None
    lsof = _tool("lsof", "/usr/sbin/lsof", "/usr/bin/lsof")
    pids = [int(l[1:]) for l in _out([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"]).splitlines()
            if l.startswith("p") and l[1:].isdigit()]
    if not pids:
        return None
    pid = pids[0]
    command = _out([_tool("ps", "/bin/ps"), "-o", "args=", "-p", str(pid)]).strip()
    cwd = next((l[1:] for l in _out([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines()
                if l.startswith("n")), "")
    return {"pid": pid, "command": command, "cwd": cwd}


def _is_checkout_script(script: Path) -> bool:
    """A checkout's dashboard.py: next to it, the install-launchd.sh (or its
    plist template) that names Ensemble's LaunchAgent, or claude-dashboard's
    before it. No other program's dashboard.py has that."""
    try:
        if script.name != "dashboard.py" or not script.is_file():
            return False
    except OSError:
        return False
    for name in CHECKOUT_MARKERS:
        try:
            text = (script.parent / name).read_text(encoding="utf-8", errors="replace")[:200000]
        except OSError:
            continue
        if any(label in text for label in CHECKOUT_LABELS):
            return True
    return False


def _is_python(prefix: str) -> bool:
    """Whether ``prefix`` (the start of a command line) is a Python
    interpreter, with only its single-dash flags after it (-u, -B ...)."""
    words = prefix.split()
    while words and re.fullmatch(r"-[A-Za-z]+", words[-1]):
        words.pop()
    if not words:
        return False
    exe = " ".join(words)
    if not _PYTHON_RE.match(Path(exe).name):
        return False
    bare = "/" not in exe and "\\" not in exe
    try:
        return bare or Path(exe).is_file()
    except OSError:
        return False


def is_ensemble_command(command: str, cwd: str = "") -> bool:
    """Whether a command line runs Ensemble: the built app's own program, or
    Python running a checkout's dashboard.py (see _is_checkout_script). Only
    as the program itself: a path merely mentioned in another program's
    arguments does not count."""
    # ps shows the arguments unquoted: a path with spaces in it is tried from
    # the start (the program) or from each space (an argument).
    for m in re.finditer(r"Ensemble\.app/Contents/MacOS/Ensemble(?=\s|$)|Ensemble\.exe(?=[\s\"]|$)", command):
        try:
            if Path(command[:m.end()].strip('"')).is_file():
                return True
        except OSError:
            pass
    for m in re.finditer(r"dashboard\.py(?=[\s\"']|$)", command):
        for start in [i + 1 for i, c in enumerate(command[:m.start()]) if c.isspace()]:
            if not _is_python(command[:start]):
                continue
            script = Path(command[start:m.end()].strip("\"' "))
            if not script.is_absolute() and cwd:
                script = Path(cwd) / script
            if _is_checkout_script(script):
                return True
    return False


def port_status(port: int) -> dict:
    """What holds the port:

    * ``free``: nothing listens;
    * ``ensemble``: a hub that answers /api/version (its ``version`` and
      ``executable``, as it says), and whose command line is Ensemble's
      when the process can be named;
    * ``old``: an older Ensemble hub, without /api/version (a checkout's
      dashboard.py; when the process cannot be named, a page titled Ensemble);
    * ``other``: anything else.

    ``pid`` and ``command`` are those of the process listening, from the
    system (lsof, ps), never from what it answers; ``verified`` is true only
    when that command line is Ensemble's (is_ensemble_command). Only a
    verified hub is ever stopped.
    """
    v = _served(port)
    if not v and not _listening(port):
        return {"state": "free", "port": port}
    h = _holder(port) or {}
    out = {"state": "other", "port": port, "pid": h.get("pid"), "command": h.get("command", ""),
           "verified": bool(h.get("command")) and is_ensemble_command(h["command"], h.get("cwd", ""))}
    if v and (out["verified"] or not h.get("command")):
        # A known process counts only by its command line: an answer on
        # /api/version alone proves nothing. Unknown (Windows, another
        # user's process): taken at its word, never stopped.
        out.update(state="ensemble", version=str(v.get("version")), executable=str(v.get("executable") or ""))
    elif out["verified"]:
        out["state"] = "old"
    elif not h.get("command"):
        # Unknown (Windows, another user's process): told by its page, never
        # stopped. A known process counts only by its command line (any web
        # server started in a checkout serves a page titled Ensemble).
        page = _http(port, "/")
        if page and _TITLE_RE.search(page[1]):
            out["state"] = "old"
    return out


def _who(st: dict) -> str:
    if st.get("pid") and st.get("command"):
        return f"process {st['pid']}: {st['command']}"
    if st.get("pid"):
        return f"process {st['pid']}"
    return "a process this app cannot name"


def describe_status(st: dict, version: str) -> str:
    """One paragraph for the person: what holds the port and what to do."""
    port = st["port"]
    if st["state"] == "old":
        return (f"Port {port} is in use by an older Ensemble hub ({_who(st)}), so Ensemble "
                f"{version} cannot start. Stop that hub (and the sign-in service that starts "
                f"it, if any), then open Ensemble again.")
    if st["state"] == "ensemble":
        return (f"Ensemble {st.get('version')} is already running on port {port} "
                f"({st.get('executable') or _who(st)}). Quit it to use Ensemble {version}.")
    if st["state"] == "other":
        return (f"Port {port} is in use by another program ({_who(st)}), so Ensemble cannot "
                f"start. Quit that program, then open Ensemble again.")
    return f"Port {port} is free."


# ---------- stopping an older hub ----------

def _launch_agents(home: Path) -> list[tuple[str, Path]]:
    import plistlib
    out = []
    for plist in sorted((home / "Library" / "LaunchAgents").glob("*.plist")):
        try:
            data = plistlib.loads(plist.read_bytes())
        except Exception:
            continue
        if isinstance(data, dict) and data.get("Label"):
            out.append((str(data["Label"]), plist))
    return out


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


def _agent_pid(label: str) -> int | None:
    text = _out(["launchctl", "print", f"gui/{_uid()}/{label}"])
    m = re.search(r"^\s*pid = (\d+)", text, re.M)
    return int(m.group(1)) if m else None


def _alive(pid: int) -> bool:
    # Not os.kill(pid, 0): on Windows that ends the process.
    from app_update import _pid_alive
    return _pid_alive(pid)


def stop_launch_agents(pid: int, home: Path) -> list[str]:
    """Boots out the LaunchAgent whose job is process ``pid`` and moves its
    file to ``~/.ensemble/old-launch-agents``, so it neither starts the hub
    again now (KeepAlive) nor at the next sign-in. What it did, as lines."""
    did = []
    for label, plist in _launch_agents(home):
        if _agent_pid(label) != pid:
            continue
        _out(["launchctl", "bootout", f"gui/{_uid()}/{label}"], timeout=30)
        did.append(f"stopped its sign-in service {label}")
        try:
            import app_setup
            aside = app_setup.move_launch_agent_aside(plist, home)
            did.append(f"moved {plist} to {aside} (move it back to start that hub at sign-in again)")
        except OSError as e:
            did.append(f"could not move {plist} aside ({e}): that hub starts again at the next sign-in")
    return did


def stop_old_hub(port: int, version: str, home: Path | None = None) -> dict:
    """Stops the Ensemble hub holding the port when it is not this version (an
    older Ensemble): first the LaunchAgent that runs it (booted out, its file
    moved to ``~/.ensemble/old-launch-agents`` so it does not start it again at
    the next sign-in), then the process. Anything that is not Ensemble is never
    stopped. {"ok", "did": [lines], "error"}."""
    home = home or Path.home()
    st = port_status(port)
    did: list[str] = []
    if st["state"] == "free":
        return {"ok": True, "did": [f"port {port} is free"]}
    if st["state"] == "ensemble" and not app_version.is_newer(version, st.get("version", "")):
        return {"ok": False, "did": did,
                "error": f"Ensemble {st.get('version')} serves port {port}: not older than {version}, not stopped"}
    if st["state"] not in ("old", "ensemble") or not st.get("verified"):
        return {"ok": False, "did": did, "error": describe_status(st, version)
                + " Its process is not one this app can tell is Ensemble: not stopped."}
    pid = st.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return {"ok": False, "did": did, "error": describe_status(st, version) + " Its process is not known: not stopped."}

    def same_hub() -> dict | None:
        # Only while that same verified process still holds the port, checked
        # again just before each step: a process id is soon given to another
        # program. None when it does; else what is there now.
        now = port_status(port)
        return None if now.get("verified") and now.get("pid") == pid else now

    if sys.platform == "darwin":
        now = same_hub()
        if now is not None:
            return {"ok": False, "did": did, "error": f"port {port} changed hands: {describe_status(now, version)}"}
        did += stop_launch_agents(pid, home)
    for sig, wait in ((signal.SIGTERM, 15), (getattr(signal, "SIGKILL", signal.SIGTERM), 5)):
        if not _alive(pid) and not _listening(port):
            break
        if _alive(pid):
            now = same_hub()
            if now is not None and now["state"] == "free":
                break
            if now is not None:
                return {"ok": False, "did": did, "error": f"port {port} changed hands: {describe_status(now, version)}"}
            try:
                os.kill(pid, sig)
                did.append(f"sent {getattr(sig, 'name', sig)} to process {pid}")
            except ProcessLookupError:
                pass
            except OSError as e:
                return {"ok": False, "did": did, "error": f"could not stop process {pid}: {e}"}
        end = time.time() + wait
        while time.time() < end and (_alive(pid) or _listening(port)):
            time.sleep(0.25)
    if _listening(port):
        return {"ok": False, "did": did, "error": describe_status(port_status(port), version)}
    did.append(f"port {port} is free")
    did += take_over_sign_in(did, port, home)
    return {"ok": True, "did": did}


def take_over_sign_in(did: list[str], port: int, home: Path) -> list[str]:
    """After the older hub's sign-in service was moved aside, the built app
    writes its own in its place (the same label), so Ensemble still starts at
    sign-in: the person is moved over, not left without it. Not loaded now:
    the app is started next, by the installer or the person."""
    if sys.platform != "darwin" or not app_version.packaged() \
            or not any(line.startswith("moved ") for line in did):
        return []
    import app_setup
    try:
        res = app_setup.set_autostart(True, app_version.app_executable(), port, log_path(home))
    except OSError as e:
        return [f"could not set Ensemble to start at sign-in ({e}): turn it on in Settings"]
    return [f"Ensemble {app_version.VERSION} now starts at sign-in in its place ({app_setup.launch_agent_plist()})"] \
        if res.get("thisApp") else []


# ---------- telling the person ----------

def _as_text(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dialog(message: str, buttons: tuple[str, ...] = ("OK",), default: str | None = None) -> str | None:
    """Shows a message the person sees without a terminal; the button
    chosen, None when none could be shown. ENSEMBLE_NO_DIALOG=1 (tests)
    only logs it."""
    _log(f"tells the person: {message}")
    if os.environ.get("ENSEMBLE_NO_DIALOG") == "1":
        return None
    default = default or buttons[-1]
    if sys.platform == "darwin":
        script = (f"activate\ndisplay dialog {_as_text(message)} with title \"Ensemble\" "
                  f"buttons {{{', '.join(map(_as_text, buttons))}}} default button {_as_text(default)} "
                  f"with icon caution giving up after 600")
        try:
            r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=660)
        except (OSError, subprocess.SubprocessError) as e:
            _log(f"could not show the dialog: {e}")
            return None
        m = re.search(r"button returned:(.*?)(, gave up:|$)", r.stdout.strip())
        return m.group(1) if m and m.group(1) else None
    if sys.platform == "win32":
        try:
            import ctypes
            if len(buttons) > 1:
                # Yes / No: Yes is the last button given.
                r = ctypes.windll.user32.MessageBoxW(None, message, "Ensemble", 0x4 | 0x30 | 0x100)
                return buttons[-1] if r == 6 else buttons[0]
            ctypes.windll.user32.MessageBoxW(None, message, "Ensemble", 0x30)
            return buttons[0]
        except Exception as e:
            _log(f"could not show the dialog: {e}")
    return None


def notify(message: str) -> None:
    """A notification that does not wait for the person (macOS)."""
    _log(f"tells the person: {message}")
    if os.environ.get("ENSEMBLE_NO_DIALOG") == "1" or sys.platform != "darwin":
        return
    try:
        subprocess.Popen(["/usr/bin/osascript", "-e",
                          f"display notification {_as_text(message)} with title \"Ensemble\""],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        _log(f"could not show the notification: {e}")


def open_browser(url: str) -> bool:
    """Opens the dashboard in the default browser and logs it. On a Mac
    through /usr/bin/open: an app started from Finder has only the system
    folders on PATH."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/open"):
        try:
            r = subprocess.run(["/usr/bin/open", url], capture_output=True, encoding="utf-8",
                               errors="replace", timeout=20)
            if r.returncode == 0:
                _log(f"opened the dashboard in the browser: {url}")
                return True
            _log(f"open {url} failed ({r.returncode}): {(r.stderr or '').strip()}")
        except (OSError, subprocess.SubprocessError) as e:
            _log(f"open {url} failed: {e}")
    try:
        if webbrowser.open(url):
            _log(f"opened the dashboard in the browser: {url}")
            return True
    except Exception as e:
        _log(f"the browser did not open: {e}")
    _log(f"could not open the browser at {url}")
    return False


def _open_when_up(port: int, seconds: float = 60) -> threading.Thread:
    def wait() -> None:
        end = time.time() + seconds
        while time.time() < end:
            if _served(port):
                if not open_browser(f"http://127.0.0.1:{port}/"):
                    notify(f"Ensemble is running, but the browser did not open: "
                           f"open http://127.0.0.1:{port}/ in your browser.")
                return
            time.sleep(0.5)
        notify(f"Ensemble started but does not answer on port {port} after {int(seconds)} s. "
               f"See the log: {_LOG}")
    t = threading.Thread(target=wait, daemon=True)
    t.start()
    return t


def _port_held(st: dict, version: str, background: bool, home: Path) -> bool:
    """Tells the person what holds the port. True when they chose to stop an
    older Ensemble hub and it stopped (the app then starts)."""
    msg = describe_status(st, version)
    if background:
        # Started at sign-in: say it once; exiting 0 keeps launchd from
        # starting the app again every few seconds.
        notify(msg)
        return False
    if st["state"] in ("old", "ensemble") and st.get("verified") and st.get("pid"):
        stop = "Stop it and start Ensemble"
        if dialog(msg, ("Quit", stop), default="Quit") == stop:
            res = stop_old_hub(st["port"], version, home)
            for line in res.get("did", []):
                _log(line)
            if res["ok"]:
                return True
            dialog(f"The older hub was not stopped: {res.get('error')}")
        return False
    dialog(msg)
    return False


def main(args: list[str]) -> int:
    global _LOG
    home = Path.home()
    log = _LOG = log_path(home)
    port = _port(args)
    if "--port-status" in args:
        st = port_status(port)
        for k, v in st.items():
            print(f"{k}={'' if v is None else str(v).replace(chr(10), ' ')}")
        print(f"message={describe_status(st, app_version.VERSION)}")
        return 0
    if "--stop-old-hub" in args:
        res = stop_old_hub(port, app_version.VERSION, home)
        for line in res.get("did", []):
            print(line)
        if not res["ok"]:
            print(res.get("error", ""), file=sys.stderr)
        return 0 if res["ok"] else 1
    import app_setup
    if "--autostart" in args:
        want = args[args.index("--autostart") + 1].lower() in ("on", "1", "yes", "true")
        res = app_setup.set_autostart(want, app_version.app_executable(), port, log)
        print(json.dumps(res))
        return 0
    background = "--background" in args
    args = [a for a in args if a != "--background"]
    # Before anything changes (skills, state): whether this app can have the port.
    st = port_status(port)
    if st["state"] == "ensemble" and not (app_version.packaged() and
                                          app_version.is_newer(app_version.VERSION, st["version"])):
        # Already running (this version, or a newer one): show it.
        url = f"http://127.0.0.1:{port}/"
        if not background and not open_browser(url):
            dialog(f"Ensemble is running, but the browser did not open: open {url} in your browser.")
        return 0
    if st["state"] != "free":
        _log(f"port {port}: {st}")
        if not _port_held(st, app_version.VERSION, background, home):
            return 0 if background else 1
    if sys.platform == "darwin" and app_version.packaged():
        # Started from Finder or launchd: only the system folders are on PATH.
        os.environ["PATH"] = app_setup.mac_path(home, app_setup.login_shell_path()
                                                or os.environ.get("PATH", ""))
    if "--log" not in args:
        # A built app has no console to write to.
        args += ["--log", str(log)]
    else:
        _LOG = Path(args[args.index("--log") + 1])
    if not background:
        _open_when_up(port)
    sys.argv = ["dashboard.py", *args]
    try:
        import dashboard
        dashboard.main()
    except SystemExit:
        raise
    except OSError as e:
        traceback.print_exc()
        if e.errno != errno.EADDRINUSE:
            return _stopped(e, background)
        st = port_status(port)
        _log(f"port {port} was taken while starting: {st}")
        msg = describe_status(st, app_version.VERSION) if st["state"] != "free" else \
            f"Port {port} was in use while Ensemble started. Open Ensemble again."
        (notify if background else dialog)(msg)
        return 0 if background else 1
    except Exception as e:
        traceback.print_exc()
        return _stopped(e, background)
    return 0


def _stopped(e: BaseException, background: bool) -> int:
    msg = f"Ensemble stopped while starting: {type(e).__name__}: {e}. The log has the details: {_LOG}"
    if background:
        _log(msg)
    else:
        dialog(msg)
    return 1
