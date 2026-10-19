"""Exercise a published app updating to an unpublished release zip.

Locally on Windows:
    python tests/release_update_e2e.py --old-dir C:\\old-release --new-dir C:\\new-release

The folders contain the platform's Ensemble-<version>-<platform>.zip. CI can
download the newest published predecessor with --download-previous. No app in
this test ever sees the real user's home, port, or release feed.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.cookiejar
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app_version  # noqa: E402


def step(label: str, fn):
    start = time.monotonic()
    print(f"STEP {label}: start", flush=True)
    try:
        result = fn()
    except BaseException:
        print(f"STEP {label}: FAILED after {time.monotonic() - start:.1f}s", flush=True)
        raise
    print(f"STEP {label}: passed in {time.monotonic() - start:.1f}s", flush=True)
    return result


def spare_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port != 8765
    return port


def asset(folder: Path, platform: str) -> tuple[str, Path]:
    pattern = re.compile(rf"Ensemble-(.+)-{re.escape(platform)}\.zip")
    matches = [(m.group(1), p) for p in folder.iterdir()
               if p.is_file() and (m := pattern.fullmatch(p.name))]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {platform} update zip in {folder}; found {len(matches)}")
    return matches[0]


def published_previous(dest: Path, platform: str, target: str) -> tuple[str, Path]:
    api = f"https://api.github.com/repos/{app_version.REPO}/releases?per_page=100"
    request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "Ensemble-release-test"})
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    candidates = []
    for rel in releases:
        tag = str(rel.get("tag_name") or "")
        parsed = app_version.parse_version(tag)
        if rel.get("draft") or rel.get("prerelease") or not parsed:
            continue
        if not app_version.is_newer(target, tag):
            continue
        version = tag.removeprefix("v")
        name = f"Ensemble-{version}-{platform}.zip"
        match = next((a for a in rel.get("assets", []) if a.get("name") == name), None)
        if match:
            candidates.append((parsed, version, match["browser_download_url"]))
    if not candidates:
        raise RuntimeError(f"no published {platform} release older than {target}")
    _, version, url = max(candidates)
    dest.mkdir(parents=True, exist_ok=True)
    output = dest / f"Ensemble-{version}-{platform}.zip"
    print(f"previous published release: {version}", flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Ensemble-release-test"}),
                                timeout=120) as response, output.open("wb") as file:
        shutil.copyfileobj(response, file)
    return version, output


class Feed:
    def __init__(self, zip_path: Path, version: str):
        self.zip_path = zip_path
        self.version = version
        self.port = spare_port()
        self.server = None

    def __enter__(self):
        zip_path = self.zip_path
        version = self.version
        with zip_path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        sums = f"{digest}  {zip_path.name}\n".encode()
        base = f"http://127.0.0.1:{self.port}"
        release = [{"tag_name": f"v{version}", "name": f"Release test {version}",
                    "draft": False, "prerelease": False,
                    "assets": [{"name": zip_path.name, "browser_download_url": f"{base}/{zip_path.name}",
                                "size": zip_path.stat().st_size},
                               {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"}]}]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/releases.json":
                    data = json.dumps(release).encode()
                elif self.path == "/SHA256SUMS.txt":
                    data = sums
                elif self.path == f"/{zip_path.name}":
                    self.send_response(200)
                    self.send_header("Content-Length", str(zip_path.stat().st_size))
                    self.end_headers()
                    with zip_path.open("rb") as file:
                        shutil.copyfileobj(file, self.wfile)
                    return
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"{base}/releases.json"

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


def install_old(old_zip: Path, install: Path, platform: str) -> Path:
    install.mkdir(parents=True)
    if platform == "macos-universal":
        subprocess.run(["ditto", "-x", "-k", str(old_zip), str(install)], check=True, timeout=180)
        exe = install / "Ensemble.app" / "Contents" / "MacOS" / "Ensemble"
    else:
        with zipfile.ZipFile(old_zip) as archive:
            archive.extractall(install)
        exe = install / "Ensemble" / "Ensemble.exe"
    if not exe.is_file():
        raise RuntimeError(f"installed executable missing: {exe}")
    return exe


class Hub:
    def __init__(self, port: int):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path: str, *, navigate: bool = False):
        headers = {"Accept": "text/html", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"} if navigate else {}
        with self.opener.open(urllib.request.Request(self.base + path, headers=headers), timeout=5) as response:
            raw = response.read()
            if response.status != 200:
                raise RuntimeError(f"GET {path}: HTTP {response.status}")
            return json.loads(raw) if path.startswith("/api/") else raw

    def post(self, path: str, body: dict, method: str = "POST"):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Origin": self.base,
                                              "Sec-Fetch-Site": "same-origin"})
        with self.opener.open(req, timeout=30) as response:
            return json.load(response)


def wait(label: str, check, seconds: int):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError, ValueError, urllib.error.URLError):
            result = check()
            if result:
                return result
        time.sleep(1)
    raise TimeoutError(f"{label} did not finish within {seconds}s")


def stop_own_hub(hub: Hub, home: Path, initial_proc: subprocess.Popen, exe: Path, env: dict):
    pids = {initial_proc.pid} if initial_proc.poll() is None else set()
    with contextlib.suppress(Exception):
        config = hub.get("/api/config")
        if Path(config["stateDir"]).resolve() == (home / ".ensemble").resolve():
            pids.add(int(hub.get("/api/version")["pid"]))
    for pid in pids:
        if not pid:
            continue
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 15)
    # The PTY host is a detached child with its own log file. Ask the app to
    # stop the host authenticated by this isolated state directory's token.
    # Killing by a descriptor PID alone could target an unrelated process.
    state = home / ".ensemble"
    if (state / "pty-host.json").exists():
        result = subprocess.run([str(exe), "--run", "pty_host", "--stop", str(state)],
                                env=env, cwd=exe.parent, capture_output=True, timeout=30)
        if result.returncode:
            print("cleanup: isolated PTY host did not stop cleanly", flush=True)


def scenario(root: Path, old_zip: Path, new_zip: Path, old: str, new: str,
             platform: str, rollback: bool):
    name = "rollback" if rollback else "update"
    home, install = root / name / "home", root / name / "install"
    home.mkdir(parents=True)
    exe = step(f"{name}: install {old}", lambda: install_old(old_zip, install, platform))
    port = spare_port()
    hub = Hub(port)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), ENSEMBLE_NO_DIALOG="1")
    if rollback:
        env["ENSEMBLE_UPDATE_TEST_FAIL_PORT"] = str(port)
        env["ENSEMBLE_UPDATE_TEST_FAIL_VERSION"] = new
    with Feed(new_zip, new) as feed:
        env["ENSEMBLE_RELEASES_URL"] = feed
        log = root / name / "hub-output.log"
        with log.open("wb") as output:
            proc = subprocess.Popen([str(exe), "--background", "--port", str(port)], env=env,
                                    cwd=exe.parent, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT)
        # On macOS the updater tests os.kill(pid, 0) while stopping the old
        # hub. A child left as a zombie still answers that probe, so reap it
        # as soon as it exits instead of waiting until this scenario ends.
        threading.Thread(target=proc.wait, daemon=True).start()
        try:
            def started():
                version = hub.get("/api/version")
                return version if version.get("version") == old and version.get("packaged") else None
            step(f"{name}: old app serves /api/platform and {old}",
                 lambda: (wait("old hub", started, 90), hub.get("/api/platform")))
            config = step(f"{name}: state opens in isolated HOME",
                          lambda: wait("hub ready", lambda: hub.get("/api/config"), 90))
            if Path(config["stateDir"]).resolve() != (home / ".ensemble").resolve():
                raise RuntimeError(f"hub state escaped throwaway HOME: {config['stateDir']}")
            project_id = step(f"{name}: create project and setting", lambda: create_state(hub, name))
            hub.get("/", navigate=True)
            available = step(f"{name}: find unpublished {new}", lambda: hub.get("/api/update-check"))
            if not available.get("available") or available.get("latestVersion") != new:
                raise RuntimeError(f"update source was not selected: {available}")
            result = step(f"{name}: Update now request", lambda: hub.post("/api/update", {"when": "safe", "reason": "release test"}))
            if not result.get("started"):
                raise RuntimeError(f"Update now did not start: {result}")

            def finished():
                path = home / ".ensemble" / "update" / "last.json"
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            final = step(f"{name}: updater result", lambda: wait("update result", finished, 420))
            if rollback:
                if final.get("stage") != "rollback" or not final.get("rolledBack") or not final.get("oldServes"):
                    raise RuntimeError(f"a failed post-swap launch did not roll back: {final}")
                expected = old
            else:
                if final.get("stage") != "done" or not final.get("ok"):
                    raise RuntimeError(f"update failed: {final}")
                expected = new

            def served():
                got = hub.get("/api/version")
                return got if got.get("version") == expected else None
            step(f"{name}: {expected} serves pages and keeps state",
                 lambda: (wait("restarted hub", served, 60), verify_state(hub, project_id)))
            print(f"RESULT {name}: {json.dumps(final, sort_keys=True)}", flush=True)
        except BaseException:
            for path in (home / ".ensemble" / "logs" / "update.log", log):
                if path.exists():
                    print(f"DIAGNOSTIC {path.name}:\n" + "\n".join(
                        path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]), flush=True)
            raise
        finally:
            stop_own_hub(hub, home, proc, exe, env)


def create_state(hub: Hub, name: str) -> str:
    project = hub.post("/api/projects/new", {"name": f"Release test {name}"})["project"]
    changed = hub.post("/api/settings", {"openMode": "tab"}, method="PUT")
    if changed.get("openMode") != "tab":
        raise RuntimeError(f"setting was not saved: {changed}")
    return project["id"]


def verify_state(hub: Hub, project_id: str):
    for page in ("/", "/session", "/fileview"):
        hub.get(page)
    projects = hub.get("/api/projects")
    if project_id not in {p["id"] for p in projects.get("projects", [])}:
        raise RuntimeError(f"project {project_id} was lost")
    if hub.get("/api/settings").get("openMode") != "tab":
        raise RuntimeError("setting openMode was lost")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-dir", type=Path)
    parser.add_argument("--new-dir", type=Path, required=True)
    parser.add_argument("--download-previous", action="store_true")
    parser.add_argument("--platform", choices=["windows-x64", "macos-universal"],
                        default="windows-x64" if os.name == "nt" else "macos-universal")
    parser.add_argument("--keep", action="store_true", help="keep scratch files after success")
    args = parser.parse_args()
    if args.platform != ("windows-x64" if os.name == "nt" else "macos-universal"):
        parser.error("platform does not match this runner")
    if args.download_previous == bool(args.old_dir):
        parser.error("choose exactly one of --old-dir or --download-previous")
    new, new_zip = asset(args.new_dir, args.platform)
    scratch = Path(tempfile.mkdtemp(prefix="eu-"))
    success = False
    print(f"release test scratch: {scratch}", flush=True)
    try:
        if args.download_previous:
            old, old_zip = step("download previous published release",
                                lambda: published_previous(scratch / "old", args.platform, new))
        else:
            old, old_zip = asset(args.old_dir, args.platform)
        if not app_version.is_newer(new, old):
            raise RuntimeError(f"new release {new} is not newer than previous {old}")
        scenario(scratch, old_zip, new_zip, old, new, args.platform, rollback=False)
        scenario(scratch, old_zip, new_zip, old, new, args.platform, rollback=True)
        success = True
        print(f"PASS {args.platform}: {old} -> {new}; post-swap failure -> {old}", flush=True)
    finally:
        if success and not args.keep:
            for attempt in range(10):
                try:
                    shutil.rmtree(scratch)
                    break
                except PermissionError:
                    if attempt == 9:
                        print(f"cleanup: scratch kept: {scratch}", flush=True)
                    else:
                        time.sleep(1)
        else:
            print(f"scratch kept: {scratch}", flush=True)


if __name__ == "__main__":
    main()
