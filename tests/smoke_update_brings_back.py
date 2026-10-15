"""Port 8798 smoke (#240, GitHub issue 21): Update now in a source checkout
brings back every session that was running before it.

A throwaway hub runs from a clone of this repo whose upstream is a local bare
repository. Two rooms run fake Claude agents: A (one agent) and B (two). A
commit is pushed to the upstream, the banner's check sees it, and the page's
Update now (POST /api/update, when=safe) fetches, resets and restarts the hub.

    py tests/smoke_update_brings_back.py              # the PTY host is unchanged: hub-only
    py tests/smoke_update_brings_back.py --kill-one   # ... and one of B's agents dies with the hub
    py tests/smoke_update_brings_back.py --kill-host  # ... and the PTY host dies with the hub
    py tests/smoke_update_brings_back.py --host       # the commit changes backends/ptyrun.py
    py tests/smoke_update_brings_back.py --branch main --kill-one   # the same against main
    py tests/smoke_update_brings_back.py --mac-helper ...  # through the Mac restart helper

Windows only for now (taskkill).

Run from the repo root. It never addresses port 8765, refuses an occupied
8798, and stops only the processes it started (by pid). Not in discover.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE_CLAUDE = r'''import json, os, sys, time
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.log")
def note(**kw):
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%H:%M:%S"), "pid": os.getpid(), **kw}) + "\n")
note(argv=sys.argv[1:])
if "--version" in sys.argv:
    print("2.1.0 (Claude Code)"); sys.exit(0)
print("fake claude ready", flush=True)
while True:
    try:
        line = input()
    except EOFError:
        time.sleep(1); continue
    note(typed=line)
'''

# The hub with the macOS backend's restart (backends/macos.py self_restart):
# the update's restart goes through restart_helper.py, the Mac helper. The
# hub it starts again is the plain one.
MAC_HUB = r'''import json, sys, time, types
from pathlib import Path
co = sys.argv.pop(1)
sys.path.insert(0, co)
sys.argv[0] = co + "/dashboard.py"
import dashboard
from backends.base import Backend


def self_restart(self, plan):
    state = Path(plan["log"]).parent.parent / "_launch"
    state.mkdir(parents=True, exist_ok=True)
    path = state / f"restart-{time.time_ns()}.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    argv = [sys.executable, str(Path(plan["repo"]) / "restart_helper.py"), str(path)]
    result = Backend.start_detached(self, argv, Path(plan["log"]))
    if not result.get("started"):
        path.unlink(missing_ok=True)
    return result


dashboard.BACKEND.self_restart = types.MethodType(self_restart, dashboard.BACKEND)
dashboard.main()
'''

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def req(path, body=None, nav=False, timeout=30):
    headers = {}
    if nav:
        headers = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                   "Sec-Fetch-Site": "none", "Accept": "text/html"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.update({"Content-Type": "application/json", "Origin": BASE,
                        "Sec-Fetch-Site": "same-origin"})
    try:
        with opener.open(urllib.request.Request(BASE + path, data=data, headers=headers),
                         timeout=timeout) as resp:
            raw, code = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    if nav:
        return code, None
    try:
        return code, json.loads(raw.decode("utf-8"))
    except ValueError:
        return code, raw[:300]


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def wait(what, check, seconds):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = check()
            if last:
                return last
        except OSError as e:
            last = e
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}: {last!r}")


def config() -> dict | None:
    """The hub's /api/config once it serves (503 while it starts)."""
    code, body = req("/api/config", timeout=3)
    return body if code == 200 and isinstance(body, dict) and body.get("stateDir") else None


def agents_by_room() -> dict:
    """{roomId: {identity: pid}} of the live agent terminals."""
    code, ptys = req("/api/ptys")
    if code != 200:
        return {}
    out: dict = {}
    for p in ptys:
        meta = p.get("meta") or {}
        if p.get("alive") and meta.get("room") and meta.get("identity"):
            out.setdefault(meta["room"], {})[meta["identity"]] = (p.get("info") or p).get("pid")
    return out


def tail(path: Path, start: int = 0) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            f.seek(start)
            return f.read()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", action="store_true", help="the update changes the PTY host")
    ap.add_argument("--kill-one", action="store_true",
                    help="one of room B's agents dies with the old hub")
    ap.add_argument("--kill-host", action="store_true",
                    help="the PTY host and every agent die with the old hub (launchd on a Mac)")
    ap.add_argument("--mac-helper", action="store_true",
                    help="restart through the Mac helper (restart_helper.py), as a source "
                         "install on a Mac does")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder")
    ap.add_argument("--branch", help="the branch the hub runs (default: this checkout's)")
    args = ap.parse_args()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        if check.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit("port 8798 is occupied; leave the other test hub untouched")
    # Short: Windows paths under the scratch home get long.
    td = Path(tempfile.mkdtemp(prefix="u240-", dir=os.environ.get("TEMP") or None))
    home, state = td / "home", td / "home" / ".ensemble"
    up, co, work, bin_dir, proj = td / "up.git", td / "co", td / "w", td / "bin", td / "proj"
    for d in (home, bin_dir, proj):
        d.mkdir(parents=True)
    branch = args.branch or git("rev-parse", "--abbrev-ref", "HEAD", cwd=ROOT)
    git("clone", "--quiet", "--bare", "--branch", branch, str(ROOT), str(up))
    git("clone", "--quiet", "--branch", branch, str(up), str(co))
    (bin_dir / "fake_claude.py").write_text(FAKE_CLAUDE, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "claude.cmd").write_text('@py -u "%~dp0fake_claude.py" %*\r\n', encoding="utf-8")
    else:
        (bin_dir / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" -u "{bin_dir}/fake_claude.py" "$@"\n',
                                        encoding="utf-8")
        (bin_dir / "claude").chmod(0o755)
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home), ENSEMBLE_RESTART_NO_TASK="1",
               ENSEMBLE_RESTART_GRACE="0", ENSEMBLE_BIND="127.0.0.1", ENSEMBLE_NO_DIALOG="1",
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    for k in ("ENSEMBLE_ROOM", "ENSEMBLE_TOKEN", "ENSEMBLE_IDENTITY", "ENSEMBLE_HUB", "CLAUDECODE"):
        env.pop(k, None)
    hub_pids: set[int] = set()
    ok = False
    try:
        start = [str(co / "dashboard.py")]
        if args.mac_helper:
            (td / "machub.py").write_text(MAC_HUB, encoding="utf-8")
            start = [str(td / "machub.py"), str(co)]
        hub = subprocess.Popen([sys.executable, *start, "--port", str(PORT),
                                "--log", str(td / "hub.log")],
                               cwd=co, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        hub_pids.add(hub.pid)
        cfg = wait("the hub", config, 120)
        assert Path(cfg["stateDir"]).resolve() == state.resolve(), cfg
        old_pid = cfg["pid"]
        hub_pids.add(old_pid)
        req("/", nav=True)                                  # the page's key, as a cookie
        code, body = req("/api/projects/new", {"path": str(proj), "name": "U240"})
        assert code == 200, (code, body)
        pid = (body.get("project") or {}).get("id") or body.get("id")
        rooms = {}
        for name, agents in (("A", [{"agent": "claude", "role": "engineer"}]),
                             ("B", [{"agent": "claude", "role": "engineer"},
                                    {"agent": "claude", "role": "designer"}])):
            code, body = req("/api/room/new", {"title": f"Room {name}", "task": "Say hello.",
                                                "projectId": pid, "agents": agents})
            assert code == 200, (code, body)
            rooms[name] = (body.get("room") or body)["id"]
        before = wait("3 agents running",
                      lambda: (lambda m: m if sum(len(m.get(r, {})) for r in rooms.values()) == 3
                               else None)(agents_by_room()), 90)
        print("before:", json.dumps({n: before[r] for n, r in rooms.items()}))

        # The commit the banner offers.
        git("clone", "--quiet", "--branch", branch, str(up), str(work))
        git("config", "user.email", "smoke@example.invalid", cwd=work)
        git("config", "user.name", "smoke", cwd=work)
        if args.host:
            f = work / "backends" / "ptyrun.py"
            f.write_text(f.read_text(encoding="utf-8") + "\n# smoke: a PTY host change\n",
                         encoding="utf-8", newline="\n")
            git("commit", "--quiet", "-am", "smoke: change the PTY host", cwd=work)
        else:
            git("commit", "--quiet", "--allow-empty", "-m", "smoke: an update", cwd=work)
        git("push", "--quiet", "origin", branch, cwd=work)
        target = git("rev-parse", "HEAD", cwd=work)
        code, info = req("/api/update-check?force=1")
        assert info.get("available"), info                 # the banner shows
        rlog, ulog = state / "logs" / "restart.log", state / "logs" / "update.log"
        r0 = rlog.stat().st_size if rlog.exists() else 0

        code, res = req("/api/update", {"when": "safe", "reason": "an update"}, timeout=180)
        print("update:", code, json.dumps({k: res.get(k) for k in ("started", "keepAgents", "draining")
                                            if isinstance(res, dict)}))
        assert code in (200, 202), res
        if args.host:
            assert res.get("draining"), res
            time.sleep(3)
            code, forced = req("/api/restart/drain", {"action": "force"})   # Restart now
            print("restart now:", code)
        else:
            assert res.get("keepAgents"), res
        killed = None
        if args.kill_one or args.kill_host:
            # Once the helper is about to stop the old hub (launchd's bootout on
            # a Mac takes agents along), before the new hub attaches.
            wait("the helper to stop the old hub",
                 lambda: "warm caches prepared before the stop" in tail(rlog, r0), 120)
            if args.kill_host:
                victim = int(json.loads((state / "pty-host.json").read_text(encoding="utf-8"))["pid"])
            else:
                ident = sorted(before[rooms["B"]])[-1]
                killed = (ident, before[rooms["B"]][ident])
                victim = killed[1]
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(victim)] if os.name == "nt"
                           else ["kill", "-9", str(victim)], capture_output=True)
            print("killed:", killed or f"the PTY host {victim} and every agent")

        new = wait("the new hub",
                   lambda: (lambda c: c if c and c.get("pid") != old_pid else None)(config()),
                   240)
        hub_pids.add(new["pid"])
        assert git("rev-parse", "HEAD", cwd=co) == target, "the checkout was not updated"
        after = wait("every agent running again",
                     lambda: (lambda m: m if all(set(m.get(rooms[n], {})) == set(before[rooms[n]])
                                                 for n in rooms) else None)(agents_by_room()), 240)
        print("after: ", json.dumps({n: after[r] for n, r in rooms.items()}))
        line = wait("the update's summary",
                    lambda: next((ln for ln in tail(ulog).splitlines()
                                  if "after the update:" in ln), None), 240)
        print("update.log:", line.split("[update] ", 1)[-1])
        assert "2 of 2 room(s) running again" in line, line
        same = {n: {i: after[rooms[n]][i] == p for i, p in before[rooms[n]].items()} for n in rooms}
        print("kept their process:", json.dumps(same))
        if not (args.host or args.kill_host):
            for n in rooms:
                for i, kept in same[n].items():
                    if killed and n == "B" and i == killed[0]:
                        assert not kept, "the dead agent was not started again"
                    else:
                        assert kept, f"{n}/{i} was started a second time"
        ok = True
        print("OK")
    finally:
        # The scratch PTY host, by the pid its descriptor names (a host the
        # update changed does not answer this checkout's client), with every
        # agent under it.
        try:
            host_pid = int(json.loads(state.joinpath("pty-host.json").read_text(encoding="utf-8"))["pid"])
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(host_pid)] if os.name == "nt"
                           else ["kill", "-9", str(host_pid)], capture_output=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            code, c = req("/api/config", timeout=2)
            if code == 200 and Path(c["stateDir"]).resolve() == state.resolve():
                hub_pids.add(c["pid"])
        except (OSError, KeyError, ValueError, TypeError):
            pass
        for p in hub_pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p)] if os.name == "nt"
                           else ["kill", "-9", str(p)], capture_output=True)
        time.sleep(1)
        if ok and not args.keep:
            shutil.rmtree(td, ignore_errors=True)
        else:
            print("scratch kept:", td)


if __name__ == "__main__":
    main()
