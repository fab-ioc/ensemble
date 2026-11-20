#!/usr/bin/env python3
"""Start and stop the test hub of the outside sources work (design #1, section
10.3, as the revised plan's P9 changed it).

    python3 tests/outside_test_hub.py start [--repo <checkout>]
    python3 tests/outside_test_hub.py stop
    python3 tests/outside_test_hub.py status

A second test hub (another task's test while one runs on 8771) takes its own
port and data folder: `--port 8772 --folder ~/ensemble-test-homes/<name>`, the
same options for start, stop and status. 8770 is refused.

The test hub is this checkout's `dashboard.py` on port 8771, with its own data
folder, `~/ensemble-test-homes/outside-sources/`, set by `ENSEMBLE_HOME` (step 1).
It keeps the normal home, so the Claude it starts uses the person's own login;
nothing is copied. Its settings, rooms, projects and log live in the test folder:
`.ensemble/`, `EnsembleProjects/`, `cs/` and `hub.log` there.

What it never touches: port 8770 (the person's own hub), `~/.ensemble`,
`~/EnsembleProjects`, or any process that is not this test hub or its PTY host.

start:
1. Port 8771 must be free.
2. The checkout's hub must know ENSEMBLE_HOME; without it the hub would use the
   person's own data folder, so the script stops.
3. The environment is this one without every ENSEMBLE*, CLAUDECODE, CLAUDE_CODE_*
   and CODEX_* variable except CODEX_HOME (a session started by the person's hub
   carries ENSEMBLE_HOOK_URL and others that point at that hub). HOME, the Claude
   login and CLAUDE_CONFIG_DIR stay. ENSEMBLE_HOME is set to the test folder.
4. The hub starts with `--port 8771`, its output in `<folder>/hub.log`, its pid in
   `<folder>/hub.pid`.
5. `/api/config` must answer with a stateDir inside the test folder; if it does
   not, the hub is stopped and the script fails.

stop:
1. The hub: the pid in hub.pid, else the process listening on 8771, each only if
   its command line is a hub and its environment has ENSEMBLE_HOME=<folder>.
2. Its PTY host: the pid in `<folder>/.ensemble/pty-host.json`, only if its command
   line names its start file in the test folder. The hub keeps that host apart so
   it can outlive a hub restart; the test POs run under it.
3. Any process left whose environment (not its command line) has ENSEMBLE_HOME
   set to the test folder, except this script and the processes that started it.
4. Port 8771 must be free afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PORT = 8771
NEVER_PORT = 8770  # the person's own hub
FOLDER = Path.home() / "ensemble-test-homes" / "outside-sources"
ROOT = Path(__file__).resolve().parent.parent
DATA_HOME_ENV = "ENSEMBLE_HOME"
LAUNCH = ("import sys; sys.path.insert(0, %r); sys.argv = ['dashboard.py', '--port', %r]; "
          "import dashboard; dashboard.main()")


def say(text: str) -> None:
    print(text, flush=True)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def clean_env(folder: Path) -> dict:
    keep = {"CODEX_HOME"}
    env = {k: v for k, v in os.environ.items()
           if k in keep or not (k.startswith("ENSEMBLE") or k.startswith("CLAUDECODE")
                                or k.startswith("CLAUDE_CODE_") or k.startswith("CODEX_"))}
    env[DATA_HOME_ENV] = str(folder)
    env["PYTHONUTF8"] = "1"
    return env


def hub_knows_data_home(repo: Path) -> bool:
    try:
        return DATA_HOME_ENV in (repo / "backends" / "base.py").read_text(encoding="utf-8")
    except OSError:
        return False


def get_config(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def inside(path: str, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


# ---------- processes ----------

def command_of(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def listening_pids(port: int) -> list[int]:
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return [int(x) for x in out.stdout.split() if x.isdigit()]


def _lines(flags: str) -> dict[int, str]:
    out = subprocess.run(["ps", flags, "-o", "pid=,command="], capture_output=True, text=True)
    found = {}
    for line in out.stdout.splitlines():
        pid, _, rest = line.strip().partition(" ")
        if pid.isdigit():
            found[int(pid)] = rest
    return found


def environment_of(pids=None) -> dict[int, str]:
    """{pid: its environment as one line}, for this user's processes. `ps -E`
    shows a process's environment after its command line (macOS; `-e` there
    means every process, not the environment); the command line
    alone (from a second `ps`) is cut off the front, so a command line that
    merely mentions a variable does not count as having it."""
    commands = _lines("-axww")
    with_env = _lines("-axwwE")
    out = {}
    for pid, full in with_env.items():
        if pids is not None and pid not in pids:
            continue
        cmd = commands.get(pid)
        if cmd is None or not full.startswith(cmd):
            continue  # ended or changed between the two reads: left alone
        out[pid] = full[len(cmd):]
    return out


def has_env(env_line: str, name: str, value: str) -> bool:
    return re.search(rf"(?:^|\s){re.escape(name)}={re.escape(value)}(?:\s|$)", env_line) is not None


def ancestors() -> set[int]:
    """This script and the processes that started it (the caller's shell, the
    agent): never ended, whatever their environment says."""
    out, pid = set(), os.getpid()
    while pid > 1 and pid not in out:
        out.add(pid)
        ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        pid = int(ppid) if ppid.isdigit() else 0
    return out


def pids_with_env(name: str, value: str) -> list[int]:
    """Processes of this user whose environment (not command line) holds
    name=value exactly; never this script or its callers."""
    mine = ancestors()
    return [pid for pid, env in environment_of().items() if pid not in mine and has_env(env, name, value)]


def end(pid: int, what: str, must_match: str = "", folder: Path | None = None) -> None:
    """Ask a process to end, then make it. Only a process whose command line has
    must_match, and (with folder) whose environment has ENSEMBLE_HOME=folder, is
    touched; never this script or its callers, and never one listening on 8770."""
    if pid <= 1 or pid in ancestors() or not alive(pid):
        return
    cmd = command_of(pid)
    if must_match and must_match not in cmd:
        say(f"left alone: pid {pid} is not the test {what} ({cmd[:80]!r})")
        return
    if folder is not None and not has_env(environment_of({pid}).get(pid, ""), DATA_HOME_ENV, str(folder)):
        say(f"left alone: pid {pid} does not have {DATA_HOME_ENV}={folder}, so it is not the test {what}")
        return
    if pid in listening_pids(NEVER_PORT):
        say(f"left alone: pid {pid} listens on {NEVER_PORT}, the person's own hub")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        if not alive(pid):
            say(f"stopped the test {what} (pid {pid})")
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    say(f"stopped the test {what} (pid {pid}, after it did not end by itself)")


# ---------- start, stop, status ----------

def start(repo: Path, folder: Path, port: int = PORT) -> int:
    if not port_free(port):
        say(f"port {port} is in use: stop what runs there first (`{sys.argv[0]} stop` if it is the test hub)")
        return 2
    if not hub_knows_data_home(repo):
        say(f"the hub in {repo} has no {DATA_HOME_ENV} setting (step 1), so it would use your own "
            "~/.ensemble and ~/EnsembleProjects: not started")
        return 2
    folder.mkdir(parents=True, exist_ok=True)
    log = open(folder / "hub.log", "a", encoding="utf-8")
    log.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} from {repo}\n")
    log.flush()
    hub = subprocess.Popen([sys.executable, "-c", LAUNCH % (str(repo), str(port))], cwd=str(repo),
                           env=clean_env(folder), stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, start_new_session=True)
    (folder / "hub.pid").write_text(str(hub.pid), encoding="utf-8")
    cfg = None
    for _ in range(120):
        time.sleep(1)
        if hub.poll() is not None:
            say(f"the test hub ended at start: see {folder / 'hub.log'}")
            return 1
        try:
            cfg = get_config(port)
            break
        except Exception:
            continue
    if cfg is None:
        say(f"the test hub did not answer within 2 minutes: stopping it; see {folder / 'hub.log'}")
        stop(folder, port)
        return 1
    state = str(cfg.get("stateDir") or "")
    if not inside(state, folder):
        say(f"the hub on {port} keeps its data in {state!r}, not in the test folder: stopping it")
        stop(folder, port)
        return 1
    say(f"test hub running: http://127.0.0.1:{port} (data in {folder}, log {folder / 'hub.log'})")
    return 0


def stop(folder: Path, port: int = PORT) -> int:
    pid_file = folder / "hub.pid"
    pids = []
    if pid_file.exists():
        try:
            pids.append(int(pid_file.read_text(encoding="utf-8").strip()))
        except ValueError:
            pass
    pids += [p for p in listening_pids(port) if p not in pids]
    for pid in pids:
        end(pid, "hub", must_match="dashboard", folder=folder)
    try:
        host = json.loads((folder / ".ensemble" / "pty-host.json").read_text(encoding="utf-8"))
        if host.get("pid"):
            # The host's command line names its start file in the test folder; a
            # pid left in an old file may belong to another process by now.
            end(int(host["pid"]), "PTY host", must_match=str(folder / ".ensemble" / "pty-host-start.json"))
    except (OSError, ValueError):
        pass
    for pid in pids_with_env(DATA_HOME_ENV, str(folder)):
        end(pid, "hub's process")
    pid_file.unlink(missing_ok=True)
    left = pids_with_env(DATA_HOME_ENV, str(folder))
    if not port_free(port) or left:
        say(f"not everything stopped: port {port} {'free' if port_free(port) else 'in use'}, "
            f"processes still using the test folder: {left or 'none'}")
        return 1
    say(f"test hub stopped; port {port} is free")
    return 0


def status(folder: Path, port: int = PORT) -> int:
    if port_free(port):
        say(f"no test hub: port {port} is free")
        return 1
    try:
        state = str(get_config(port).get("stateDir") or "")
    except Exception as e:
        say(f"something listens on {port} but does not answer as a hub: {e}")
        return 1
    which = "the test hub" if inside(state, folder) else "NOT the test hub"
    say(f"{which} runs on http://127.0.0.1:{port} (data in {state})")
    return 0 if inside(state, folder) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Start or stop an outside sources test hub (port 8771 by default).")
    ap.add_argument("action", choices=("start", "stop", "status"))
    ap.add_argument("--repo", default=str(ROOT), help="the checkout whose dashboard.py runs (default: this one)")
    ap.add_argument("--port", type=int, default=PORT,
                    help=f"the port (default {PORT}); a second test hub needs its own, and never {NEVER_PORT}")
    ap.add_argument("--folder", default=str(FOLDER),
                    help="the test hub's data folder (default ~/ensemble-test-homes/outside-sources); "
                         "a second test hub needs its own")
    a = ap.parse_args(argv)
    folder = Path(a.folder).expanduser().resolve()
    if a.port == NEVER_PORT:
        say(f"port {NEVER_PORT} is the person's own hub: never used by a test hub")
        return 2
    if a.port != PORT and folder == FOLDER.resolve():
        say(f"a test hub on another port than {PORT} needs its own data folder (--folder)")
        return 2
    if a.action == "start":
        return start(Path(a.repo).resolve(), folder, a.port)
    if a.action == "stop":
        return stop(folder, a.port)
    return status(folder, a.port)


if __name__ == "__main__":
    sys.exit(main())
