"""Port 8798 smoke: a 60-second PTY keeps its PID and entire stream through restart.

Run from the repo root: ``py tests/smoke_hot_restart.py``. Its USERPROFILE and
PTY host are unique scratch state; it never addresses port 8765.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data,
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_hub(old_pid=None, seconds=90, outage=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            cfg = get("/api/config")
            if old_pid is None or cfg["pid"] != old_pid:
                if outage is not None:
                    outage["up"] = time.perf_counter()
                return cfg
        except OSError:
            if outage is not None and "down" not in outage:
                outage["down"] = time.perf_counter()
        time.sleep(0.25)
    raise AssertionError("hub did not come up on 8798")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        if check.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("port 8798 is occupied; leave the other test hub untouched")
    with tempfile.TemporaryDirectory(prefix="ensemble-8798-") as td:
        env = dict(os.environ, USERPROFILE=td, ENSEMBLE_RESTART_NO_TASK="1",
                   ENSEMBLE_RESTART_GRACE="0", ENSEMBLE_BIND="127.0.0.1")
        log_path = Path(td) / "hub.log"
        hub = None
        latest_pid = None
        host = None
        try:
            hub = subprocess.Popen([sys.executable, str(ROOT / "dashboard.py"),
                                    "--port", str(PORT), "--log", str(log_path)],
                                   cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                                   encoding="utf-8",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            cfg = wait_hub(seconds=90)
            assert Path(cfg["stateDir"]).resolve() == (Path(td) / ".ensemble").resolve(), cfg
            old_pid = latest_pid = cfg["pid"]
            command = ("import time; "
                       "[(print('tick %02d' % i, flush=True), time.sleep(1)) for i in range(60)]")
            made = post("/api/pty/create", {"cmd": [sys.executable, "-c", command],
                                             "cwd": td, "label": "smoke agent"})
            pty_id, child_pid = made["id"], made["info"]["pid"]
            received = bytearray()
            stream_id = ""
            with urllib.request.urlopen(BASE + "/api/pty/stream?id=" + pty_id, timeout=10) as stream:
                for line in stream:
                    if line.startswith(b"id: "):
                        stream_id = line[4:].strip().decode("ascii")
                    elif line.startswith(b"data: "):
                        received.extend(base64.b64decode(line[6:].strip()))
                        if b"tick 01" in received:
                            break
            # The test driver asks for an actual restart while the fake agent
            # is mid-command. It constructs the plan for the 8798 process only.
            os.environ.update({k: env[k] for k in ("USERPROFILE", "ENSEMBLE_RESTART_NO_TASK",
                                                   "ENSEMBLE_RESTART_GRACE", "ENSEMBLE_BIND")})
            import dashboard
            dashboard.HUB_PORT = PORT
            old_argv = sys.argv
            sys.argv = ["dashboard.py", "--port", str(PORT)]
            build = dashboard.restart_plan
            dashboard.restart_plan = lambda *a, **kw: {**build(*a, **kw), "hubPid": old_pid}
            try:
                started = dashboard.trigger_restart()
            finally:
                dashboard.restart_plan = build
                sys.argv = old_argv
            assert started["started"] and started["keepAgents"], started
            outage = {}
            new = wait_hub(old_pid=old_pid, seconds=180, outage=outage)
            assert Path(new["stateDir"]).resolve() == (Path(td) / ".ensemble").resolve(), new
            latest_pid = new["pid"]
            listed = {p["id"]: p for p in get("/api/ptys")}
            assert listed[pty_id]["pid"] == child_pid and listed[pty_id]["alive"], listed
            request = urllib.request.Request(BASE + "/api/pty/stream?id=" + pty_id,
                                             headers={"Last-Event-ID": stream_id})
            with urllib.request.urlopen(request, timeout=85) as stream:
                for line in stream:
                    if line.startswith(b"data: "):
                        received.extend(base64.b64decode(line[6:].strip()))
                    if line.startswith(b"event: end"):
                        break
            output = received.decode("utf-8", "replace")
            for i in range(60):
                assert output.count(f"tick {i:02d}") == 1, (i, output[-200:])
            t0 = time.perf_counter()
            get("/api/sessions?n=300", timeout=30)
            first_sessions_ms = round((time.perf_counter() - t0) * 1000)
            print(json.dumps({"oldHubPid": old_pid, "newHubPid": latest_pid,
                              "ptyId": pty_id, "childPid": child_pid,
                              "ticks": 60, "keepAgents": True,
                              "outageSeconds": (round(outage["up"] - outage["down"], 2)
                                                if "down" in outage else None),
                              "firstSessionsMs": first_sessions_ms}))
        finally:
            # Only PIDs from this scratch hub and its descriptor; never by name.
            descriptor = Path(td) / ".ensemble" / "pty-host.json"
            if descriptor.exists():
                try:
                    from backends.ptyhost import connect
                    host = connect(Path(td) / ".ensemble", start=False)
                    if host:
                        host.call("shutdown")
                        for _ in range(50):
                            try:
                                host.call("ping")
                            except (OSError, ConnectionError):
                                break
                            time.sleep(0.1)
                except (OSError, ConnectionError, RuntimeError):
                    pass
            try:
                current = get("/api/config", timeout=2)
                if Path(current["stateDir"]).resolve() == (Path(td) / ".ensemble").resolve():
                    latest_pid = current["pid"]
            except (OSError, KeyError, ValueError):
                pass
            for pid in {latest_pid, hub.pid if hub else None}:
                if pid:
                    try:
                        os.kill(pid, 15)
                    except OSError:
                        pass
            time.sleep(0.5)


if __name__ == "__main__":
    main()
