"""Detached macOS helper for a plain hub restart on code already on disk."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from backends import ptyhost


def hub_args(plan: dict, port: int) -> list[str]:
    """Keep the original bind and flags while replacing this instance's port."""
    args = [plan["python"]]
    if plan.get("script"):
        args.append(plan["script"])
    original = iter(plan.get("args") or [])
    for arg in original:
        if arg in ("--port", "--log"):
            next(original, None)
        elif not (arg.startswith("--port=") or arg.startswith("--log=") or arg == "--background"):
            args.append(arg)
    args.extend(["--port", str(port), "--log", plan["preflightLog"] if port != plan["port"]
                 else plan["log"], "--background"])
    return args


def run(plan_path: str) -> int:
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    path.unlink(missing_ok=True)
    try:
        return _run(plan)
    finally:
        lease_path = Path(plan.get("leasePath") or "")
        try:
            if lease_path.is_file() and json.loads(lease_path.read_text(encoding="utf-8")).get("id") == plan.get("leaseId"):
                lease_path.unlink()
        except (OSError, ValueError):
            pass


def started(get) -> bool:
    """Up and past its start-up steps (ED-227): a hub listens before them,
    so /api/platform alone no longer says the code on disk starts. A hub
    from before /api/health is up once /api/platform answers."""
    try:
        return json.loads(get("/api/health")).get("phase") != "starting"
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return False
        try:
            get("/api/platform")
            return True
        except OSError:
            return False
    except (OSError, ValueError, AttributeError):
        return False


def _run(plan: dict) -> int:
    os.environ.update(plan["env"])
    log_path = Path(plan["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(message):
        with log_path.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [restart] {message}\n")

    def get(port, endpoint, timeout=10):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{endpoint}", timeout=timeout) as resp:
            return resp.read()

    def post(endpoint, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{plan['port']}{endpoint}",
                                     data=data, headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def spawn(port):
        args = hub_args(plan, port)
        env = dict(plan["env"])
        if port != plan["port"]:
            env["ENSEMBLE_PTY_PREFLIGHT"] = "1"
        with log_path.open("a", encoding="utf-8") as out:
            return subprocess.Popen(args, cwd=plan["repo"], env=env,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True, text=True, encoding="utf-8")

    def wait(port, seconds, proc=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if proc is not None and proc.poll() is not None:
                return False        # a start-up step failed and the hub exited
            if started(lambda endpoint: get(port, endpoint, timeout=3)):
                return True
            time.sleep(1)
        return False

    log("mode: " + ("hub only; agents keep running" if plan["keepAgents"]
                     else "classic drain and resume"))
    pf = spawn(plan["preflightPort"])
    try:
        if not wait(plan["preflightPort"], 120, pf):
            log("preflight failed; hub left running")
            return 1
        # "/" is the start-up page until the first list is read: skip it
        # to load the board itself.
        for endpoint in ("/api/sessions?n=5", "/?startup=skip", "/api/projects"):
            get(plan["preflightPort"], endpoint, timeout=170)
        # A hub line must reach a throwaway agent on the preflight's PTY host.
        req = urllib.request.Request(
            f"http://127.0.0.1:{plan['preflightPort']}/api/restart/typing-check",
            data=json.dumps({"lease": plan["leaseId"]}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            check = json.loads(resp.read().decode("utf-8"))
        log(f"preflight typing check: {check.get('detail')}")
        if not check.get("ok"):
            log("preflight failed: a hub line did not reach the throwaway agent; hub left running")
            return 1
    except OSError as exc:
        log(f"preflight failed: {exc}; hub left running")
        return 1
    finally:
        try:
            data = json.dumps({"lease": plan["leaseId"]}).encode("utf-8")
            req = urllib.request.Request(f"http://127.0.0.1:{plan['preflightPort']}/api/restart/host-stop",
                                         data=data, headers={"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(req, timeout=10).close()
        except OSError:
            pass
        pf.terminate()
        try:
            pf.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pf.kill()

    time.sleep(max(0, plan.get("graceSeconds", 0)))
    lease = {"lease": plan["leaseId"]}
    pinned_host = None
    try:
        if plan["keepAgents"]:
            post("/api/restart/prepare", lease)
        else:
            for attempt in range(3):
                try:
                    snap = post("/api/restart/snapshot", lease)
                    if not snap.get("ok"):
                        raise ValueError("snapshot was not confirmed")
                    break
                except (OSError, ValueError):
                    if attempt == 2:
                        raise
                    time.sleep(1)
            pinned_host = ptyhost.descriptor(Path(plan["leasePath"]).parent)
            if pinned_host is None:
                if plan.get("ptyHostVersion"):
                    raise ValueError("old PTY host identity is missing")
                pinned_host = {"absent": True}
    except (OSError, ValueError) as exc:
        log(f"restart preparation failed: {exc}")
        if not plan["keepAgents"]:
            return 1

    svc = plan.get("launchd")
    if svc:
        res = subprocess.run(["launchctl", "bootout", svc["target"]],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        log(f"launchctl bootout: {res.returncode}")
    else:
        try:
            os.kill(int(plan["hubPid"]), signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.monotonic() + 30
    while time.monotonic() < end:
        try:
            os.kill(int(plan["hubPid"]), 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        log("old hub did not stop; leaving it running")
        return 1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        if check.connect_ex(("127.0.0.1", plan["port"])) == 0:
            log("old hub port is still occupied; leaving PTY host intact")
            return 1
    if not plan["keepAgents"]:
        state = Path(plan["leasePath"]).parent
        try:
            host_stopped = ptyhost.stop(state, expected=pinned_host)
        except Exception as exc:
            log(f"old PTY host stop failed: {exc}")
            host_stopped = False
        if not host_stopped:
            log("old PTY host did not stop cleanly; force-killing pinned process tree")
            try:
                forced = ptyhost.force_stop(pinned_host)
            except Exception as exc:
                log(f"pinned PTY host force-stop failed: {exc}")
                forced = False
            log(f"pinned PTY host force-stop confirmed: {forced}; starting hub anyway")
    if svc:
        res = subprocess.run(["launchctl", "bootstrap", svc["domain"], svc["plist"]],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        log(f"launchctl bootstrap: {res.returncode}")
        if res.returncode != 0:
            log("launchd bootstrap failed; starting hub directly")
            spawn(plan["port"])
    else:
        spawn(plan["port"])
    if not wait(plan["port"], 90):
        log("hub did not come back")
        return 1
    if not plan["keepAgents"]:
        try:
            post("/api/restart/restore", lease)
        except OSError as exc:
            log(f"rooms could not be restored: {exc}")
    log("hub up; restart complete")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
