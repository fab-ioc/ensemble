"""Starting the built app: ``Ensemble`` with ``dashboard.py``'s arguments, plus

  --background      do not open the browser (the start at sign-in)
  --autostart on|off   start at sign-in, or not, and exit

Opened a second time (the Start menu entry, the Dock icon) while a hub
already serves the port, it opens the dashboard and exits.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _port(args: list[str]) -> int:
    if "--port" in args:
        return int(args[args.index("--port") + 1])
    return int(os.environ.get("ENSEMBLE_PORT", "8765"))


def _served(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _open_when_up(port: int) -> None:
    def wait() -> None:
        for _ in range(120):
            if _served(port):
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
            time.sleep(0.5)
    threading.Thread(target=wait, daemon=True).start()


def main(args: list[str]) -> int:
    import app_version
    import app_setup
    home = Path.home()
    # Where a checkout's hub logs too (dashboard.DEFAULT_LOG_FILE).
    log = (home / "Library" / "Logs" / "ensemble.log" if sys.platform == "darwin"
           else home / ".ensemble" / "logs" / "ensemble.log")
    port = _port(args)
    if "--autostart" in args:
        want = args[args.index("--autostart") + 1].lower() in ("on", "1", "yes", "true")
        res = app_setup.set_autostart(want, app_version.app_executable(), port, log)
        print(json.dumps(res))
        return 0
    background = "--background" in args
    args = [a for a in args if a != "--background"]
    if sys.platform == "darwin" and app_version.packaged():
        # Started from Finder or launchd: only the system folders are on PATH.
        os.environ["PATH"] = app_setup.mac_path(home, app_setup.login_shell_path()
                                                or os.environ.get("PATH", ""))
    if _served(port):
        # Already running: show it.
        if not background:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0
    if "--log" not in args:
        # A built app has no console to write to.
        args += ["--log", str(log)]
    if not background:
        _open_when_up(port)
    sys.argv = ["dashboard.py", *args]
    import dashboard
    dashboard.main()
    return 0
