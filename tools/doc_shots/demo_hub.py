"""A throwaway Ensemble hub for the documentation's screenshots.

It runs this checkout's dashboard.py with a home folder of its own (HOME,
USERPROFILE), on its own port, with stand-ins for `claude`, `codex` and
`tailscale` first on PATH (fake_agent.py, fake_tailscale.py): no real agent is
ever started, no plan allowance is used, and nothing of the machine's own
Ensemble state (~/.ensemble, ~/EnsembleProjects, ~/.claude) is read or written.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# Variables an agent session (or a hub's own unit) sets that must not leak into
# the demo hub: it would take them for its own.
_DROP = ("ENSEMBLE_ROOM", "ENSEMBLE_TOKEN", "ENSEMBLE_IDENTITY", "ENSEMBLE_HUB", "CHAT_TOKEN",
         "CLAUDECODE", "INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "SYSTEMD_EXEC_PID",
         "MANAGERPID", "ENSEMBLE_PORT", "ENSEMBLE_BIND", "ENSEMBLE_RESTART_ROOMS")


class Hub:
    def __init__(self, base: Path, port: int, user: str = "alex"):
        self.base, self.port = base, port
        self.home = base / user            # shown in paths as /…/<user>/EnsembleProjects/…
        self.bin = base / "bin"
        self.state = self.home / ".ensemble"
        self.agents_log = base / "agents.jsonl"
        self.log = base / "hub.log"
        self.url = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    # --- set-up -----------------------------------------------------------
    def prepare(self, settings: dict) -> None:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", self.port)) == 0:
                raise SystemExit(f"port {self.port} is in use; pick another with --port")
        if self.base.exists():
            shutil.rmtree(self.base)
        for d in (self.home, self.bin, self.state):
            d.mkdir(parents=True)
        py = sys.executable
        for name, script, arg in (("claude", "fake_agent.py", "claude"), ("codex", "fake_agent.py", "codex"),
                                  ("tailscale", "fake_tailscale.py", "")):
            exe = self.bin / name
            exe.write_text(f'#!/bin/sh\nexec "{py}" -u "{HERE / script}" {arg} "$@"\n', encoding="utf-8")
            exe.chmod(0o755)
        (self.state / "settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
        # Claude Code's own settings: a demo statusline is not needed, but a
        # retention period long enough that Settings does not warn.
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 365}),
                                                             encoding="utf-8")
        subprocess.run(["git", "config", "--global", "user.name", "Alex"], env=self.env(), check=True)
        subprocess.run(["git", "config", "--global", "user.email", "alex@example.invalid"],
                       env=self.env(), check=True)
        subprocess.run(["git", "config", "--global", "init.defaultBranch", "main"], env=self.env(), check=True)

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in _DROP}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), ENSEMBLE_BIND="127.0.0.1",
                   ENSEMBLE_NO_DIALOG="1", ENSEMBLE_NO_AUTO_INSTALL="1", ENSEMBLE_RESTART_NO_TASK="1",
                   DOC_AGENTS_LOG=str(self.agents_log), GIT_CONFIG_NOSYSTEM="1",
                   PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin")
        env.pop("XDG_CONFIG_HOME", None)
        return env

    # --- run --------------------------------------------------------------
    def start(self) -> None:
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "dashboard.py"), "--port", str(self.port),
                                      "--log", str(self.log)], cwd=self.base, env=self.env(),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(200):
            try:
                cfg = self.get("/api/config")
                if Path(cfg["stateDir"]).resolve() != self.state.resolve():
                    raise SystemExit(f"the hub on {self.port} is not the demo hub: {cfg['stateDir']}")
                break
            except (OSError, ValueError, KeyError):
                time.sleep(0.2)
        else:
            raise SystemExit(f"the demo hub did not start; see {self.log}")
        self.req("/", nav=True)            # the page's key, as a cookie

    def stop(self) -> None:
        """Stops the hub and every process it started (they all run under the
        demo state folder or the stand-ins), never anything else."""
        pids = set()
        if self.proc:
            pids.add(self.proc.pid)
        mark = str(self.base)
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                env = (d / "environ").read_bytes()
            except OSError:
                continue
            if mark in cmd or f"HOME={self.home}".encode() in env:
                pids.add(int(d.name))
        pids.discard(os.getpid())
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # --- requests ---------------------------------------------------------
    def req(self, path: str, body=None, nav: bool = False, timeout: float = 60):
        headers = {}
        if nav:
            headers = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                       "Sec-Fetch-Site": "none", "Accept": "text/html"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Origin": self.url,
                            "Sec-Fetch-Site": "same-origin"})
        try:
            with self.opener.open(urllib.request.Request(self.url + path, data=data, headers=headers),
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

    def get(self, path: str):
        with urllib.request.urlopen(self.url + path, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path: str, body: dict, timeout: float = 120) -> dict:
        code, res = self.req(path, body, timeout=timeout)
        if code != 200:
            raise RuntimeError(f"{path} -> {code}: {str(res)[:400]}")
        return res

    def mcp(self, token: str, tool: str, args: dict) -> dict:
        """One MCP tool call as the agent whose token it is."""
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        req = urllib.request.Request(self.url + "/mcp", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream",
                                              "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
        if raw.startswith("event:") or raw.startswith("data:"):
            raw = next(line[5:] for line in raw.splitlines() if line.startswith("data:"))
        out = json.loads(raw)
        if "error" in out:
            raise RuntimeError(f"{tool}: {out['error']}")
        res = out.get("result") or {}
        text = "".join(c.get("text", "") for c in res.get("content") or [])
        if res.get("isError"):
            raise RuntimeError(f"{tool}: {text[:400]}")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    def agents(self) -> list[dict]:
        if not self.agents_log.exists():
            return []
        return [json.loads(l) for l in self.agents_log.read_text(encoding="utf-8").splitlines() if l.strip()]
