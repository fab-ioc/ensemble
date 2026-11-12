"""Linux backend.

Process introspection works (POSIX signals from the base class, plus /proc for
cwd) and the desktop opens via xdg-open. Terminal control (open/focus/close/
themes) is not wired up yet — those return "unsupported" so the UI degrades
gracefully. Flesh this out per terminal emulator (e.g. gnome-terminal,
konsole, wezterm) later.

The plain restart and Update now work as on macOS: a detached helper
(restart_helper.py) preflights the code on disk and restarts the hub. A hub
that a systemd user unit runs (install-systemd.sh) is stopped and started
through that unit; anything the hub starts to outlive it (the helper, the PTY
host) goes into a scope of its own, since stopping the unit ends every
process in the unit's cgroup. A hub started by hand is stopped by signal and
started again by the helper."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .base import Backend, DASHBOARD_DIR


def _cgroup_unit(text: str) -> str:
    """The .service unit a /proc/<pid>/cgroup names as the process's own
    cgroup (its last path part), or "". ``user@1000.service`` further up the
    path is the user manager, not the hub's unit."""
    for line in text.splitlines():
        # cgroup v2: "0::/user.slice/.../app.slice/ensemble.service"
        _, _, path = line.partition("::")
        if not path:
            continue
        leaf = path.rstrip("/").rsplit("/", 1)[-1]
        return leaf if leaf.endswith(".service") else ""
    return ""


class LinuxBackend(Backend):
    os_name = "linux"
    terminal_name = "terminal"
    file_manager_name = "file manager"
    # Language → editor id (backends.base.EDITORS) for Automatic.
    default_editor_map = {
        "java":       "idea",
        "kotlin":     "idea",
        "python":     "pycharm",
        "javascript": "code",
        "typescript": "code",
        "rust":       "code",
        "go":         "code",
        "default":    "code",
    }
    # Terminal control not implemented yet; process + desktop do work.
    features = {"focus": False, "themes": False, "geometry": False, "liveTitle": False,
                "consolidate": False, "split": False}

    _unit: dict | None = None
    _unit_known = False

    # ---------- systemd ----------

    def systemd_service(self) -> dict | None:
        """The systemd user unit running this hub as its main process, as
        {"unit", "transient"}; None for a hub started by hand (or by an agent
        inside the unit, whose main process is another). Read once, at the
        hub's start."""
        if self._unit_known:
            return self._unit
        self._unit_known = True
        try:
            unit = _cgroup_unit(Path("/proc/self/cgroup").read_text(encoding="utf-8"))
        except OSError:
            unit = ""
        if not unit or not shutil.which("systemctl"):
            return None
        try:
            r = subprocess.run(["systemctl", "--user", "show", unit,
                                "--property=MainPID", "--property=Transient"],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        props = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
        if r.returncode != 0 or props.get("MainPID") != str(os.getpid()):
            return None
        self._unit = {"unit": unit, "transient": props.get("Transient") == "yes"}
        return self._unit

    def _outside_unit(self, argv: list[str]) -> list[str] | None:
        """``argv`` in a scope of its own when this hub runs under a unit, so
        stopping the unit does not end it. ``systemd-run --scope`` execs the
        command itself: the pid Popen returns is the command's. None under a
        unit without ``systemd-run``: the command would stay in the unit's
        cgroup and die with the stop it is meant to outlive."""
        if not self.systemd_service():
            return list(argv)
        run = shutil.which("systemd-run")
        if not run:
            return None
        return [run, "--user", "--scope", "--quiet", "--collect",
                f"--unit=ensemble-detached-{uuid.uuid4().hex[:12]}", "--", *argv]

    NO_SCOPE = ("this hub runs under the systemd unit {unit}, and systemd-run is not on "
                "PATH: a process started now would stop with the unit")

    def start_detached(self, argv: list[str], log_file: Path) -> dict:
        wrapped = self._outside_unit(argv)
        if wrapped is None:
            return {"started": False,
                    "error": self.NO_SCOPE.format(unit=self.systemd_service()["unit"])}
        return super().start_detached(wrapped, log_file)

    # ---------- self-update ----------

    def self_restart(self, plan: dict) -> dict:
        """A detached helper preflights, then restarts only this hub: through
        its systemd unit when ``plan["systemd"]`` names one."""
        state = Path(plan["log"]).parent.parent / "_launch"
        state.mkdir(parents=True, exist_ok=True)
        path = state / f"restart-{time.time_ns()}.json"
        # The plan holds the hub's environment: readable by this user only.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        argv = [sys.executable, str(Path(plan["repo"]) / "restart_helper.py"), str(path)]
        result = self.start_detached(argv, Path(plan["log"]))
        if not result.get("started"):
            path.unlink(missing_ok=True)
        return result

    def self_update(self, install_dir, port=None, log_file=None) -> dict:
        """`ensemble update` detached (git fetch + reset to the upstream, then
        a restart): through the hub's systemd unit when it runs under one,
        else the script's stop and start on the hub's port. Its output is
        appended to the hub's log."""
        cli = Path(install_dir) / "ensemble"
        if not cli.exists():
            return {"started": False, "error": f"{cli} not found"}
        log = Path(log_file) if log_file else DASHBOARD_DIR / "logs" / "ensemble.log"
        argv = [shutil.which("bash") or "/bin/bash", str(cli), "update"]
        if port:
            argv += ["--port", str(port)]
        env = dict(os.environ, ENSEMBLE_STDIO_IS_LOG="1")
        unit = self.systemd_service()
        env.pop("ENSEMBLE_SYSTEMD_UNIT", None)
        if unit:
            env["ENSEMBLE_SYSTEMD_UNIT"] = unit["unit"]
        wrapped = self._outside_unit(argv)
        if wrapped is None:
            return {"started": False, "error": self.NO_SCOPE.format(unit=unit["unit"])}
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "ab") as out:
                subprocess.Popen(wrapped, cwd=str(install_dir),
                                 stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 close_fds=True, env=env)
        except (subprocess.SubprocessError, OSError) as e:
            return {"started": False, "error": f"{e.__class__.__name__}: {e}"}
        return {"started": True, "pid": os.getpid(), "log": str(log)}

    # ---------- process ----------

    def live_cwd_of_pid(self, pid: int) -> str | None:
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None

    def _locate_editor(self, eid: str) -> str | None:
        """On PATH, else JetBrains Toolbox's launch script."""
        hit = shutil.which(eid)
        if hit:
            return hit
        script = Path.home() / ".local" / "share" / "JetBrains" / "Toolbox" / "scripts" / eid
        return str(script) if script.is_file() else None

    def open_path(self, path: str, app: str = "default", command: bool = False) -> str:
        p = Path(path)
        if not p.exists():
            return f"path does not exist: {path}"
        if app and app not in ("default", "finder"):
            label, exe, extra = self.launch_target(app, command)
            if not exe:
                try:
                    subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError) as e:
                    return f"error: {e}"
                return (f"{label} was not found on this computer, so the folder opened "
                        f"instead. Choose the editor in Settings › Editor.")
            args = [exe, *extra, str(p)]
        else:
            args = ["xdg-open", str(p)]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "ok"
        except (OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"
