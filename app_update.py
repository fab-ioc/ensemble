"""Update now in the built app: from GitHub Releases, not git.

The hub (``dashboard.trigger_update`` when ``app_version.packaged()``):
  1. reads the releases of ``app_version.REPO`` and picks the newest one with an
     update asset for this platform (``pick_release``),
  2. downloads it and ``SHA256SUMS.txt`` and refuses it unless the checksum
     matches (``verify_sha256``),
  3. unpacks it into ``~/.ensemble/update/<version>/``,
  4. copies the running app to ``~/.ensemble/update/helper/`` and starts that
     copy as the helper (``Ensemble --run app_update apply <plan>``), outside
     the hub's process tree. A copy, because Windows will not let a running
     program be moved or overwritten; and the old version's code, which is
     known to start.

The helper (``apply``):
  1. preflight: the new version must start and serve its version on a spare
     port, or the hub is not touched;
  2. asks the hub to note who is running (``/api/restart/snapshot``) and stops it;
  3. swaps the app's entries (``Ensemble.exe`` and ``_internal``, or the
     ``Ensemble.app`` bundle) for the new ones, keeping the old ones in
     ``<install>/.previous`` (``swap_in``);
  4. starts the hub the way it ran, on the same port, and waits for it to serve
     the new version;
  5. rolls back (``swap_back``, start the old one) when it does not
     (``decide``); either way it brings the rooms back (``/api/restart/restore``)
     and writes ``~/.ensemble/update/last.json``.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import app_version

SUMS_NAME = "SHA256SUMS.txt"
RELEASES_URL = "https://api.github.com/repos/{repo}/releases?per_page=20"
USER_AGENT = "Ensemble-updater"
PREVIOUS_DIR = ".previous"
FAILED_DIR = ".failed"
INCOMING_DIR = ".incoming"
# The update's own folders that can sit in the install folder.
UPDATE_DIRS = (PREVIOUS_DIR, FAILED_DIR, INCOMING_DIR, ".ensemble-previous", ".ensemble-failed")
# How long a new version has to answer, on the preflight port and then on the
# hub's own, before it counts as not starting.
SERVE_TIMEOUT_S = 120
# How often the helper renews the restart lease while it runs.
LEASE_BEAT_S = 20
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------- which asset ----------

def platform_key(platform: str = sys.platform, machine: str | None = None) -> str | None:
    """The release asset family this machine runs: ``windows-x64`` (Windows on
    Arm runs it too, emulated), ``macos-universal``; None elsewhere (Linux runs
    from source)."""
    machine = (machine if machine is not None else _platform.machine()).lower()
    if platform == "win32":
        return "windows-x64" if machine in ("amd64", "x86_64", "x64", "arm64", "aarch64") else None
    if platform == "darwin":
        return "macos-universal"
    return None


def update_asset_name(version: str, key: str) -> str:
    """The zip an update downloads (the installer and the .dmg are for people)."""
    return f"Ensemble-{version}-{key}.zip"


def pick_release(releases: list, current: str, key: str | None) -> dict | None:
    """The newest published, non-pre-release release newer than ``current``
    that carries this platform's update zip and the checksum file; None when
    there is none (or this platform has no asset family)."""
    if not key:
        return None
    best = None
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "")
        parsed = app_version.parse_version(tag)
        if parsed is None or not app_version.is_newer(tag, current):
            continue
        version = tag[1:] if tag.startswith("v") else tag
        assets = {a.get("name"): a for a in rel.get("assets") or [] if isinstance(a, dict)}
        zip_asset = assets.get(update_asset_name(version, key))
        sums = assets.get(SUMS_NAME)
        if not zip_asset or not sums:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, {
                "version": version, "tag": tag,
                "assetName": zip_asset["name"],
                "assetUrl": zip_asset.get("browser_download_url", ""),
                "size": int(zip_asset.get("size") or 0),
                "sumsUrl": sums.get("browser_download_url", ""),
                "name": str(rel.get("name") or tag)[:120],
                "url": rel.get("html_url", ""),
            })
    return best[1] if best else None


# ---------- checksums ----------

def parse_sums(text: str) -> dict[str, str]:
    """``sha256sum`` output (``<hex>  <name>``, ``*name`` for binary mode) as
    {name: lowercase hex}. Lines that are not that are skipped."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].strip().lstrip("*")
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) and name:
            out[name] = digest
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str | None) -> bool:
    """Whether ``path`` has the checksum the release lists. No listed checksum
    is a refusal, never a pass."""
    return bool(expected) and sha256_file(path) == expected.lower()


# ---------- the hub side ----------

def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_releases(repo: str = app_version.REPO, timeout: float = 15) -> list:
    # ENSEMBLE_RELEASES_URL: another list in the same shape (a rehearsal, a fork).
    url = os.environ.get("ENSEMBLE_RELEASES_URL") or RELEASES_URL.format(repo=repo)
    return json.loads(_get(url, timeout).decode("utf-8"))


def check(current: str = app_version.VERSION, key: str | None = None, fetch=fetch_releases) -> dict:
    """What the update banner shows, in the shape of the git check: available,
    and which version."""
    key = key if key is not None else platform_key()
    if not key:
        return {"available": False, "reason": "no_release_for_platform", "kind": "release",
                "currentVersion": current}
    try:
        rel = pick_release(fetch(), current, key)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return {"available": False, "reason": f"error: {e.__class__.__name__}", "kind": "release",
                "currentVersion": current}
    if not rel:
        return {"available": False, "kind": "release", "currentVersion": current}
    return {"available": True, "kind": "release", "currentVersion": current,
            "latestVersion": rel["version"], "latestMessage": rel["name"], "release": rel}


def download(url: str, dest: Path, timeout: float = 60) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(part, dest)
    return dest


def unpack(zip_path: Path, dest: Path, platform: str = sys.platform) -> Path:
    """Unpack an update zip into ``dest`` (emptied first); returns the app
    inside it (the ``Ensemble`` folder, or ``Ensemble.app``). A Mac bundle
    holds symlinks and executable bits zipfile drops, so ``ditto`` unpacks it."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", str(zip_path), str(dest)], check=True,
                       capture_output=True, timeout=300)
    else:
        with zipfile.ZipFile(zip_path) as z:
            root = dest.resolve()
            for name in z.namelist():
                target = (dest / name).resolve()
                if target != root and root not in target.parents:
                    raise ValueError(f"unsafe path in the update: {name}")
            z.extractall(dest)
    return find_app(dest, platform)


def ready_mac_app(app: Path, run=subprocess.run) -> None:
    """Make a Mac update's bundle one Gatekeeper never stops: no quarantine
    attribute on any of it (a download through this updater gets none, but a
    zip that came another way may), and its ad-hoc signature intact. Raises
    ValueError when the signature does not verify: a broken one gives
    "Ensemble is damaged", with no way to open it."""
    run(["xattr", "-dr", "com.apple.quarantine", str(app)], capture_output=True, timeout=120)
    left = run(["xattr", "-r", str(app)], capture_output=True, timeout=120)
    # An xattr that cannot list the attributes proves nothing: refused too.
    if left.returncode != 0 or b"com.apple.quarantine" in (left.stdout or b""):
        raise ValueError(f"could not make sure {app.name} carries no quarantine attribute; not installed")
    sig = run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, timeout=300)
    if sig.returncode != 0:
        why = (sig.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"the signature of {app.name} does not verify"
                         + (f": {why[-1]}" if why else "") + "; not installed")


def find_app(folder: Path, platform: str = sys.platform) -> Path:
    if platform == "darwin":
        for p in [folder / "Ensemble.app", *folder.glob("*.app")]:
            if (p / "Contents" / "MacOS").is_dir():
                return p
    else:
        for p in [folder / "Ensemble", folder]:
            if (p / "Ensemble.exe").is_file():
                return p
    raise FileNotFoundError(f"no Ensemble app in {folder}")


# What a Windows build puts next to nothing else: the program and its folder.
WINDOWS_APP_NAMES = ("Ensemble.exe", "_internal")


def app_entries(app: Path, platform: str = sys.platform) -> tuple[Path, list[str]]:
    """(the folder the swap happens in, the names in it that are the app).
    Windows: the install folder; its app is Ensemble.exe, _internal and
    whatever else a build puts there, but not the installer's uninstaller or
    the update's own folders. Mac: the folder holding the bundle, and the
    bundle."""
    if platform == "darwin":
        return app.parent, [app.name]
    skip = UPDATE_DIRS
    return app, sorted(p.name for p in app.iterdir()
                       if p.name not in skip and not p.name.lower().startswith("unins"))


def old_names(folder: Path, new_names: list[str], platform: str = sys.platform) -> list[str]:
    """What the swap moves out of ``folder``: what the new app replaces, and
    the app's own entries even when a new build has dropped one."""
    keep = set(new_names) | (set() if platform == "darwin" else set(WINDOWS_APP_NAMES))
    return sorted(n for n in keep if (folder / n).exists())


def exe_in(app: Path, platform: str = sys.platform) -> Path:
    if platform == "darwin":
        return app / "Contents" / "MacOS" / "Ensemble"
    return app / "Ensemble.exe"


def stage(rel: dict, update_dir: Path, platform: str = sys.platform, get=_get,
          fetch=download, ready=ready_mac_app) -> Path:
    """Download, check and unpack ``rel`` (from ``pick_release``) under
    ``update_dir``; returns the new app. Raises ValueError on a checksum that
    does not match, or a Mac bundle whose signature does not verify, and the
    download is deleted."""
    work = update_dir / rel["version"]
    zip_path = update_dir / rel["assetName"]
    sums = parse_sums(get(rel["sumsUrl"]).decode("utf-8", "replace"))
    fetch(rel["assetUrl"], zip_path)
    try:
        if not verify_sha256(zip_path, sums.get(rel["assetName"])):
            raise ValueError(f"checksum of {rel['assetName']} does not match {SUMS_NAME}; not installed")
        app = unpack(zip_path, work, platform)
        if platform == "darwin":
            ready(app)
        return app
    finally:
        zip_path.unlink(missing_ok=True)


def copy_helper(app: Path, update_dir: Path, platform: str = sys.platform) -> Path:
    """A copy of the running app to run the helper from; returns its program."""
    dest = update_dir / "helper"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    if platform == "darwin":
        target = dest / app.name
        subprocess.run(["ditto", str(app), str(target)], check=True, capture_output=True, timeout=300)
    else:
        target = dest / app.name
        shutil.copytree(app, target, symlinks=True,
                        ignore=shutil.ignore_patterns(*UPDATE_DIRS, "unins*"))
    return exe_in(target, platform)


# ---------- the swap ----------

def same_volume(a: Path, b: Path) -> bool:
    """Whether a rename from ``a`` into ``b`` (or the nearest existing parent of
    each) stays on one volume."""
    def dev(p: Path) -> int | None:
        for q in (p, *p.parents):
            try:
                return os.stat(q).st_dev
            except OSError:
                continue
        return None
    da, db = dev(a), dev(b)
    return da is not None and da == db


def _move(src: Path, dst: Path, tries: int = 30, wait: float = 1.0) -> None:
    """Rename, retrying: on Windows a hook an agent started a moment ago can hold
    the program open for a little while. Only ever a rename: all or nothing,
    never a copy then a delete (which, on a locked file, deletes half the app),
    and never into something already there."""
    if os.path.lexists(dst):
        raise FileExistsError(f"{dst} is already there; not moving {src} into it")
    for i in range(tries):
        try:
            os.rename(src, dst)
            return
        except FileExistsError:
            raise
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def bring_near(new_from: Path, new_names: list[str], folder: Path) -> Path:
    """The new entries on the same volume as ``folder``, so the swap can rename
    them in: ``new_from`` itself when it already is, else a copy next to the
    app (made while the hub still runs, so a slow copy stops nothing)."""
    if same_volume(new_from, folder):
        return new_from
    near = folder / INCOMING_DIR
    if near.exists():
        shutil.rmtree(near)
    near.mkdir(parents=True)
    for n in new_names:
        src = new_from / n
        if src.is_dir():
            shutil.copytree(src, near / n, symlinks=True)
        else:
            shutil.copy2(src, near / n)
    return near


def swap_in(folder: Path, names: list[str], new_from: Path, new_names: list[str],
            prev: Path, tries: int = 30, wait: float = 1.0) -> None:
    """Move the old app's ``names`` from ``folder`` into ``prev`` (emptied
    first), then the new app's ``new_names`` from ``new_from`` into ``folder``.
    On a failure part-way, what moved is moved back and the error raised: the
    old app stays where it was."""
    if prev.exists():
        shutil.rmtree(prev)
    prev.mkdir(parents=True)
    moved_old: list[str] = []
    moved_new: list[str] = []
    try:
        for n in names:
            _move(folder / n, prev / n, tries, wait)
            moved_old.append(n)
        for n in new_names:
            _move(new_from / n, folder / n, tries, wait)
            moved_new.append(n)
    except OSError:
        for n in reversed(moved_new):
            _move(folder / n, new_from / n, tries, wait)
        for n in reversed(moved_old):
            _move(prev / n, folder / n, tries, wait)
        raise


def swap_back(folder: Path, new_names: list[str], prev: Path, failed: Path,
              tries: int = 30, wait: float = 1.0) -> None:
    """Undo ``swap_in``: the new entries out of ``folder`` into ``failed``,
    the old ones back from ``prev``."""
    if failed.exists():
        shutil.rmtree(failed, ignore_errors=True)
    failed.mkdir(parents=True, exist_ok=True)
    for n in new_names:
        if os.path.lexists(folder / n):
            _move(folder / n, failed / n, tries, wait)
    for p in sorted(prev.iterdir()):
        _move(p, folder / p.name, tries, wait)


def decide(served: dict | None, expected_version: str) -> str:
    """After the swap: ``keep`` when the hub on the port serves the new
    version, else ``rollback``."""
    if served and str(served.get("version") or "") == expected_version:
        return "keep"
    return "rollback"


# ---------- the helper ----------

def _served(port: int, timeout: float = 3) -> dict | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/version")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def wait_served(port: int, version: str | None, timeout: float, renew=lambda: None,
                served=_served) -> dict | None:
    """The first /api/version answer on ``port`` (of ``version``, when given)
    within ``timeout`` seconds; None if none."""
    end = time.time() + timeout
    while time.time() < end:
        got = served(port)
        if got and (version is None or got.get("version") == version):
            return got
        renew()
        time.sleep(1)
    return None


def _post(port: int, path: str, body: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _spare_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(pid: int) -> None:
    if not pid:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=_NO_WINDOW, timeout=30)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _wait_exit(proc: subprocess.Popen, timeout: float = 30) -> None:
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        pass


def _start(exe: Path, args: list[str], log: Path | None = None,
           env: dict | None = None) -> subprocess.Popen:
    """The app, detached, with no window."""
    kw: dict = {"stdin": subprocess.DEVNULL, "close_fds": True, "cwd": str(exe.parent), "env": env}
    if sys.platform == "win32":
        kw["creationflags"] = _NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    out = open(log, "ab") if log else subprocess.DEVNULL
    try:
        return subprocess.Popen([str(exe), *args], stdout=out, stderr=subprocess.STDOUT, **kw)
    finally:
        if log:
            out.close()


def renew_lease(path: str | None, lease_id: str | None) -> None:
    """Keep the hub's restart lease alive while an update runs (see
    dashboard._take_restart_lease): the hub while it downloads, the helper
    after."""
    if not path:
        return
    try:
        lease = json.loads(Path(path).read_text(encoding="utf-8"))
        if lease.get("id") == lease_id:
            lease["at"] = time.time()
            Path(path).write_text(json.dumps(lease), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class Helper:
    """One update, from a plan the hub wrote (``dashboard.update_plan``)."""

    def __init__(self, plan: dict):
        self.plan = plan
        self.port = int(plan["port"])
        self.log_path = Path(plan["log"])
        self.platform = plan.get("platform", sys.platform)
        self.started: subprocess.Popen | None = None    # the hub this helper last started
        self.ready_mac_app = ready_mac_app

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [update] {msg}\n"
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def renew(self) -> None:
        renew_lease(self.plan.get("leasePath"), self.plan.get("leaseId"))

    def drop_lease(self) -> None:
        path = self.plan.get("leasePath")
        try:
            if path and json.loads(Path(path).read_text(encoding="utf-8")).get("id") == self.plan.get("leaseId"):
                Path(path).unlink()
        except (OSError, ValueError):
            pass

    def record(self, **result) -> dict:
        result = {"at": time.time(), "from": self.plan.get("fromVersion"),
                  "to": self.plan.get("toVersion"), "log": str(self.log_path), **result}
        try:
            out = Path(self.plan["resultPath"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=1), encoding="utf-8")
        except (OSError, KeyError):
            pass
        self.log(f"result: {json.dumps(result)}")
        return result

    # The hub's service, if one runs it: launchd on a Mac (the LaunchAgent
    # would start the old app again the moment it stops). On Windows the app
    # starts at sign-in from the Run key, which supervises nothing.
    def stop_service(self) -> bool:
        svc = self.plan.get("launchd")
        if not svc:
            return False
        r = subprocess.run(["launchctl", "bootout", svc["target"]], capture_output=True, timeout=60)
        self.log(f"launchctl bootout {svc['target']}: {r.returncode}")
        return True

    def start_hub(self, exe: Path, via_service: bool) -> None:
        svc = self.plan.get("launchd")
        if via_service and svc:
            r = subprocess.run(["launchctl", "bootstrap", svc["domain"], svc["plist"]],
                               capture_output=True, timeout=60)
            self.log(f"launchctl bootstrap {svc['plist']}: {r.returncode}")
            if r.returncode == 0:
                return
        # Not started by launchd: its job name must not reach the hub, or the
        # next update would think launchd runs it.
        env = {k: v for k, v in os.environ.items() if k != "XPC_SERVICE_NAME"}
        self.started = _start(exe, list(self.plan.get("args") or []), env=env)
        self.log(f"started {exe} {' '.join(self.plan.get('args') or [])} (pid {self.started.pid})")

    def _gone(self, pid: int) -> bool:
        return not _pid_alive(pid) and _port_free(self.port)

    def stop_hub(self) -> tuple[bool, bool]:
        """(via launchd, stopped): stopped only when the old hub's process is
        gone and its port free, so nothing of the old app is still open."""
        via_service = self.stop_service()
        pid = int(self.plan.get("hubPid") or 0)
        for attempt in range(2):
            _kill(pid)
            for _ in range(30):
                if self._gone(pid):
                    return via_service, True
                self.renew()
                time.sleep(1)
            self.log(f"the hub (pid {pid}) has not stopped yet" + ("; again" if not attempt else ""))
        return via_service, False

    def preflight(self, new_exe: Path) -> bool:
        port = _spare_port()
        # --background: the try opens no browser tab.
        proc = _start(new_exe, ["--port", str(port), "--background", "--log", str(self.plan["preflightLog"])])
        try:
            got = wait_served(port, self.plan["toVersion"], SERVE_TIMEOUT_S, self.renew)
        finally:
            _kill(proc.pid)
            _wait_exit(proc)
        self.log(f"preflight of {self.plan['toVersion']} on port {port}: {'passed' if got else 'FAILED'}")
        return got is not None

    def restore_rooms(self) -> None:
        try:
            res = _post(self.port, "/api/restart/restore", {"lease": self.plan.get("leaseId", "")})
            self.log(f"rooms: {res.get('note', '')} {len(res.get('rooms') or [])} in the snapshot")
        except (OSError, ValueError, urllib.error.URLError) as e:
            self.log(f"rooms not brought back: {e}")

    def run(self) -> dict:
        """The update, with the lease renewed every 20 s throughout (a long
        copy or swap holds it as the hub's download did)."""
        done = threading.Event()

        def beat() -> None:
            while not done.wait(LEASE_BEAT_S):
                self.renew()
        threading.Thread(target=beat, daemon=True).start()
        try:
            return self._run()
        finally:
            done.set()

    def _run(self) -> dict:
        new_app = Path(self.plan["newApp"])
        cur_app = Path(self.plan["app"])
        new_from, new_names = app_entries(new_app, self.platform)
        if self.platform != "darwin":
            new_from = new_app
        folder = cur_app.parent if self.platform == "darwin" else cur_app
        names = old_names(folder, new_names, self.platform)
        prev, failed = Path(self.plan["previousDir"]), Path(self.plan["failedDir"])
        if not same_volume(prev.parent, folder):
            # The old app only ever moves by a rename: keep it beside itself.
            prev, failed = folder / ".ensemble-previous", folder / ".ensemble-failed"
        self.log(f"=== update {self.plan.get('fromVersion')} -> {self.plan.get('toVersion')} "
                 f"of {cur_app} on port {self.port} ===")
        if not self.preflight(exe_in(new_app, self.platform)):
            self.drop_lease()
            return self.record(ok=False, stage="preflight",
                               reason="the new version did not start; nothing was changed")
        try:
            new_from = bring_near(new_from, new_names, folder)
            if self.platform == "darwin":
                # What is swapped in, the staged bundle or its copy next to
                # the app: unquarantined, its signature intact.
                for n in new_names:
                    self.ready_mac_app(new_from / n)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            self.drop_lease()
            return self.record(ok=False, stage="copy", reason="could not copy the new version "
                               f"next to the app; nothing was changed: {e}")
        try:
            return self._swap_and_start(cur_app, folder, names, new_from, new_names, prev, failed)
        except Exception as e:      # noqa: BLE001 — whatever happens, leave an app serving
            self.log(f"unexpected: {e!r}")
            try:
                self.ensure_serving(cur_app, prev, False)
            except Exception as e2:     # noqa: BLE001 — the result is still recorded
                self.log(f"could not start any app: {e2!r}")
            return self.record(ok=False, stage="error", reason=str(e), previous=str(prev))

    def _swap_and_start(self, cur_app: Path, folder: Path, names: list[str], new_from: Path,
                        new_names: list[str], prev: Path, failed: Path) -> dict:
        try:
            snap = _post(self.port, "/api/restart/snapshot", {"lease": self.plan.get("leaseId", "")})
            self.log(f"snapshot: {len(snap.get('rooms') or [])} room(s)")
        except (OSError, ValueError, urllib.error.URLError) as e:
            self.log(f"no snapshot before the stop: {e}")
        via_service, stopped = self.stop_hub()
        if not stopped:
            # Its files are still open: a swap now would fail half-way. Leave it.
            self.ensure_serving(cur_app, prev, via_service)
            self.restore_rooms()
            return self.record(ok=False, stage="stop",
                               reason="the running hub did not stop; nothing was changed")
        try:
            swap_in(folder, names, new_from, new_names, prev)
        except OSError as e:
            self.log(f"swap failed, the old version stays: {e}")
            self.ensure_serving(cur_app, prev, via_service)
            self.restore_rooms()
            return self.record(ok=False, stage="swap", reason=str(e))
        self.log(f"swapped in the new version; the old one is in {prev}")
        if new_from.name == INCOMING_DIR:
            shutil.rmtree(new_from, ignore_errors=True)
        served = None
        if self._try_start(exe_in(cur_app, self.platform), via_service):
            served = wait_served(self.port, self.plan["toVersion"], SERVE_TIMEOUT_S, self.renew)
        if decide(served, self.plan["toVersion"]) == "keep":
            self.restore_rooms()
            return self.record(ok=True, stage="done")
        self.log("the new version did not serve: rolling back")
        self.stop_hub_after_failed_start()
        try:
            swap_back(folder, new_names, prev, failed)
        except OSError as e:
            self.log(f"rollback failed: {e}")
            back = self.ensure_serving(cur_app, prev, via_service)
            self.restore_rooms()
            return self.record(ok=False, stage="rollback-failed", rolledBack=False,
                               oldServes=bool(back), previous=str(prev),
                               reason="the new version did not serve and putting the old one "
                                      f"back failed ({e}); the old version is in {prev}")
        back = self.ensure_serving(cur_app, prev, via_service)
        self.restore_rooms()
        return self.record(ok=False, stage="rollback", rolledBack=True, oldServes=bool(back),
                           reason="the new version did not serve on the hub's port")

    def ensure_serving(self, cur_app: Path, prev: Path, via_service: bool) -> dict | None:
        """Something on the hub's port, whatever state the swap was left in:
        the app in place if it is whole, else the old one where it was moved."""
        got = _served(self.port)
        if got:
            return got
        old_in_prev = prev / cur_app.name if self.platform == "darwin" else prev
        in_place = exe_in(cur_app, self.platform)
        for exe in (in_place, exe_in(old_in_prev, self.platform)):
            if not exe.is_file() or not self._try_start(exe, via_service and exe == in_place):
                continue
            got = wait_served(self.port, None, SERVE_TIMEOUT_S, self.renew)
            if got:
                return got
            self.stop_hub_after_failed_start()
        return None

    def _try_start(self, exe: Path, via_service: bool) -> bool:
        """start_hub, False when the program cannot even be started (a broken
        or half-written executable raises)."""
        try:
            self.start_hub(exe, via_service)
            return True
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            self.log(f"could not start {exe}: {e!r}")
            return False

    def stop_hub_after_failed_start(self) -> None:
        """Stop a hub this helper started that did not serve: by its own pid
        (one that hangs never answers /api/version), and whatever answers."""
        self.stop_service()
        if self.started is not None:
            _kill(self.started.pid)
            _wait_exit(self.started)
        pid = (_served(self.port) or {}).get("pid")
        if pid:
            _kill(int(pid))
        for _ in range(30):
            if _port_free(self.port) and (self.started is None or self.started.poll() is not None):
                return
            self.renew()
            time.sleep(1)


def apply(plan_path: str) -> int:
    """The helper's entry: ``Ensemble --run app_update apply <plan.json>``."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    Path(plan_path).unlink(missing_ok=True)
    # Started outside the hub (WMI on Windows) with the user's default
    # environment: take the hub's, which the hub that comes back inherits.
    os.environ.update({k: str(v) for k, v in (plan.get("env") or {}).items()})
    result = Helper(plan).run()
    return 0 if result.get("ok") else 1


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "apply":
        return apply(argv[2])
    print("usage: app_update apply <plan.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
