"""Measure scratch hub read latency with 30 live PTYs; never use the live hub."""
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request


PORT = 8799
ROOT = Path(__file__).resolve().parent.parent
BASE = f"http://127.0.0.1:{PORT}"


def request(path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("benchmark port is occupied")
    with tempfile.TemporaryDirectory(prefix="ensemble-pty-30-") as td:
        env = dict(os.environ, USERPROFILE=td, ENSEMBLE_BIND="127.0.0.1")
        hub = subprocess.Popen([sys.executable, str(ROOT / "dashboard.py"),
                                "--port", str(PORT), "--log", str(Path(td) / "hub.log")],
                               cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ids = []
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    cfg = request("/api/config")
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.25)
            assert Path(cfg["stateDir"]).resolve() == (Path(td) / ".ensemble").resolve()
            for i in range(30):
                made = request("/api/pty/create", {"cmd": [sys.executable, "-c", "import time; time.sleep(120)"],
                                                     "cwd": td, "label": f"benchmark {i}"})
                ids.append(made["id"])
            times = {}
            for path in ("/api/sessions?n=300", "/api/ptys"):
                samples = []
                for _ in range(5):
                    at = time.perf_counter()
                    result = request(path)
                    samples.append(round((time.perf_counter() - at) * 1000, 1))
                    if path == "/api/ptys":
                        assert len(result) >= 30, len(result)
                times[path] = {"medianMs": statistics.median(samples),
                               "maxMs": max(samples), "samplesMs": samples}
            print(json.dumps({"agents": len(ids), "requests": times}))
        finally:
            for ident in ids:
                try:
                    request("/api/pty/kill", {"id": ident})
                except OSError:
                    pass
            try:
                sys.path.insert(0, str(ROOT))
                from backends import ptyhost
                pin = ptyhost.descriptor(Path(td) / ".ensemble")
                if pin:
                    ptyhost.stop(Path(td) / ".ensemble", expected=pin)
            except (OSError, ValueError):
                pass
            hub.terminate()
            try:
                hub.wait(timeout=10)
            except subprocess.TimeoutExpired:
                hub.kill()


if __name__ == "__main__":
    main()
