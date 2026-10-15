"""Port 8798 smoke (#241): the PTY host dies under a running hub.

A throwaway hub runs this checkout with two rooms of fake Claude agents (A:
one agent, B: two). The PTY host is killed with every agent under it while
the hub runs on. The hub's watch starts a new host and brings every agent
back (a new process each), types nothing into the idle ones, and says so in
restart.log.

    py tests/smoke_pty_host_dies.py

Windows only for now (taskkill). Run from the repo root. It never addresses
port 8765, refuses an occupied 8798, and stops only the processes it started
(by pid). Not in discover.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_update_brings_back as s   # noqa: E402  (its req/wait helpers, port 8798)


def kill(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)] if os.name == "nt"
                   else ["kill", "-9", str(pid)], capture_output=True)


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        if check.connect_ex(("127.0.0.1", s.PORT)) == 0:
            raise SystemExit("port 8798 is occupied; leave the other test hub untouched")
    td = Path(tempfile.mkdtemp(prefix="p241-", dir=os.environ.get("TEMP") or None))
    home, state = td / "home", td / "home" / ".ensemble"
    bin_dir, proj = td / "bin", td / "proj"
    for d in (home, bin_dir, proj):
        d.mkdir(parents=True)
    (bin_dir / "fake_claude.py").write_text(s.FAKE_CLAUDE, encoding="utf-8")
    (bin_dir / "claude.cmd").write_text('@py -u "%~dp0fake_claude.py" %*\r\n', encoding="utf-8")
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home), ENSEMBLE_RESTART_NO_TASK="1",
               ENSEMBLE_BIND="127.0.0.1", ENSEMBLE_NO_DIALOG="1",
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    for k in ("ENSEMBLE_ROOM", "ENSEMBLE_TOKEN", "ENSEMBLE_IDENTITY", "ENSEMBLE_HUB", "CLAUDECODE"):
        env.pop(k, None)
    hub_pids: set[int] = set()
    ok = False
    try:
        hub = subprocess.Popen([sys.executable, str(s.ROOT / "dashboard.py"), "--port", str(s.PORT),
                                "--log", str(td / "hub.log")],
                               cwd=s.ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        hub_pids.add(hub.pid)
        cfg = s.wait("the hub", s.config, 120)
        assert Path(cfg["stateDir"]).resolve() == state.resolve(), cfg
        hub_pids.add(cfg["pid"])
        s.req("/", nav=True)
        code, body = s.req("/api/projects/new", {"path": str(proj), "name": "P241"})
        assert code == 200, (code, body)
        pid = (body.get("project") or {}).get("id") or body.get("id")
        rooms = {}
        for name, agents in (("A", [{"agent": "claude", "role": "engineer"}]),
                             ("B", [{"agent": "claude", "role": "engineer"},
                                    {"agent": "claude", "role": "designer"}])):
            code, body = s.req("/api/room/new", {"title": f"Room {name}", "task": "Say hello.",
                                                  "projectId": pid, "agents": agents})
            assert code == 200, (code, body)
            rooms[name] = (body.get("room") or body)["id"]
        before = s.wait("3 agents running",
                        lambda: (lambda m: m if sum(len(m.get(r, {})) for r in rooms.values()) == 3
                                 else None)(s.agents_by_room()), 90)
        print("before:", json.dumps({n: before[r] for n, r in rooms.items()}))
        # The heartbeat writes down who runs (every 30 s) and the watch sees
        # the host answer once (every 10 s).
        time.sleep(40)
        rlog = state / "logs" / "restart.log"
        r0 = rlog.stat().st_size if rlog.exists() else 0
        host = int(json.loads((state / "pty-host.json").read_text(encoding="utf-8"))["pid"])
        kill(host)
        print("killed the PTY host", host, "and every agent; the hub runs on")
        after = s.wait("every agent running again on a new host",
                       lambda: (lambda m: m if all(set(m.get(rooms[n], {})) == set(before[rooms[n]])
                                                   for n in rooms) else None)(s.agents_by_room()), 120)
        print("after: ", json.dumps({n: after[r] for n, r in rooms.items()}))
        new_host = int(json.loads((state / "pty-host.json").read_text(encoding="utf-8"))["pid"])
        assert new_host != host, "no new PTY host"
        for n in rooms:
            for i, p in before[rooms[n]].items():
                assert after[rooms[n]][i] != p, f"{n}/{i} kept a dead process id"
        assert s.config()["pid"] == cfg["pid"], "the hub restarted"
        line = s.wait("the summary", lambda: next(
            (ln for ln in s.tail(rlog, r0).splitlines() if "PTY host: " in ln and "running again" in ln),
            None), 60)
        print("restart.log:\n" + "\n".join("  " + ln.split("[restart] ", 1)[-1]
                                           for ln in s.tail(rlog, r0).splitlines()))
        assert "2 of 2 room(s) running again" in line, line
        typed = [json.loads(ln) for ln in (bin_dir / "calls.log").read_text(encoding="utf-8").splitlines()
                 if '"typed"' in ln]
        print("typed into the agents:", typed)
        ok = True
        print("OK")
    finally:
        try:
            kill(int(json.loads(state.joinpath("pty-host.json").read_text(encoding="utf-8"))["pid"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        for p in hub_pids:
            kill(p)
        time.sleep(1)
        if ok:
            shutil.rmtree(td, ignore_errors=True)
        else:
            print("scratch kept:", td)


if __name__ == "__main__":
    main()
