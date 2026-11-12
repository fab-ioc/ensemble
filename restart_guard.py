"""The restart guard (ED-342): a restart or a stop never takes a project's
long-running app down with it.

Stopping a systemd unit ends every process in its cgroup, not just its main
one. An app an agent starts from its terminal (``setsid nohup java … &``)
stays in the cgroup of that terminal's PTY host, an ``ensemble-detached-*``
scope, or of ``ensemble.service`` itself: ``setsid`` and ``nohup`` change the
session and the signals, never the cgroup. So before anything stops the
hub's unit or the PTY host, the guard lists the processes in those cgroups
and sorts them:

* **expected**: the hub (the unit's main process), the PTY host, whatever
  runs the guard (the restart helper, ``install-systemd.sh``), every
  terminal of the PTY host (the agent CLIs: claude, codex) and every
  descendant of one of those (shells, tool runs, MCP servers);
* **foreign**: any other process tree whose top process is detached (its own
  session: ``setsid``; SIGHUP ignored: ``nohup``; a child of the PTY host
  without a terminal) or has run for ``THRESHOLD_S`` or longer;
* **recent**: the rest: a young process neither detached nor started by an
  expected one (``cmd &`` left by a tool run, about to end; a child of a
  process that moved itself elsewhere, as Chrome does): left where it is.

Each foreign tree is moved, before the stop, into a transient scope of its
own (``kept-<name>-<pid>.scope``), through the user manager's
``StartTransientUnit`` with the tree's ``PIDs``: the same call
``systemd-run --scope`` makes for its own pid, which the user manager allows
for any process of the same user. The processes run on untouched; stopping
the hub's unit or the PTY host's scope no longer reaches them. A tree that
cannot be moved (no ``busctl`` or ``gdbus``, the call refused) makes the
guard refuse the stop, naming each process, unless it is forced.

Everything it finds and does is logged to ``~/.ensemble/logs/restart.log``.

macOS and Windows: a no-op (``protect`` returns ok with nothing found).
launchd and the Windows task stop the hub's own process, not a cgroup, and
the PTY host is stopped by its own ``shutdown``, which ends its terminals
only: a detached process survives both there already.

Run as a script (``install-systemd.sh``, ``ensemble update``)::

    python3 restart_guard.py [--unit U]... [--state-dir D] [--force] [--no-detached]

exits 0 when the stop may go ahead (nothing foreign, all of it moved, or
forced) and 3 when it is refused.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# A process tree that has run this long is long-lived, detached or not.
THRESHOLD_S = 120
CGROUP_ROOT = Path("/sys/fs/cgroup")
DETACHED_GLOB = "ensemble-detached-*"
DEFAULT_UNITS = ("ensemble.service",)
REFUSED_EXIT = 3
# Rescans after a move: a child forked while its tree was being moved stays
# behind and is moved on the next pass.
PASSES = 3
MOVE_WAIT_S = 3.0
# The agents' terminals carry these (dashboard._agent_hook_env, ptyrun): a
# process an agent started inherits them.
ROOM_ENV, IDENTITY_ENV, PTY_ENV = "ENSEMBLE_HOOK_ROOM", "ENSEMBLE_HOOK_IDENTITY", "ENSEMBLE_PTY_ID"


def supported() -> bool:
    return sys.platform.startswith("linux") and (CGROUP_ROOT / "cgroup.controllers").exists() \
        and shutil.which("systemctl") is not None


def default_log() -> Path:
    return Path.home() / ".ensemble" / "logs" / "restart.log"


# ---------------------------------------------------------------------------
# Reading the machine (each function is one seam the tests replace)
# ---------------------------------------------------------------------------

def _systemctl(*args: str, timeout: float = 20) -> str:
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def unit_cgroups(units, detached: bool = True) -> dict[str, dict]:
    """{unit: {"cgroup": Path, "mainPid": int}} for the given units that are
    running, and (``detached``) for every running ``ensemble-detached-*``
    scope (the PTY host's, the restart helper's)."""
    names = [u for u in units if u]
    listed = _systemctl("list-units", "--type=scope", "--state=active", "--plain",
                        "--no-legend", DETACHED_GLOB) if detached else ""
    for line in listed.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.endswith(".scope") and name not in names:
            names.append(name)
    if not names:
        return {}
    out = _systemctl("show", "--property=Id", "--property=ControlGroup",
                     "--property=MainPID", *names)
    found = {}
    for block in out.split("\n\n"):
        props = dict(ln.split("=", 1) for ln in block.splitlines() if "=" in ln)
        unit, cg = props.get("Id", ""), props.get("ControlGroup", "")
        if unit and cg:
            try:
                main = int(props.get("MainPID") or 0)
            except ValueError:
                main = 0
            found[unit] = {"cgroup": CGROUP_ROOT / cg.lstrip("/"), "mainPid": main}
    return found


def cgroup_pids(cgroup: Path) -> list[int]:
    """Every process in ``cgroup`` and the cgroups below it."""
    pids = []
    try:
        files = [cgroup / "cgroup.procs", *sorted(cgroup.rglob("*/cgroup.procs"))]
    except OSError:
        return pids
    for f in files:
        try:
            pids += [int(x) for x in f.read_text().split()]
        except (OSError, ValueError):
            pass
    return pids


def _clock_ticks() -> int:
    try:
        return os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        return 100


def _uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def read_proc(pid: int) -> dict | None:
    """What the guard knows of one process, or None once it is gone."""
    base = Path(f"/proc/{pid}")
    try:
        stat = (base / "stat").read_text()
    except OSError:
        return None
    try:
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        fields = stat[stat.rindex(")") + 2:].split()
        ppid, pgid, sid, tty = int(fields[1]), int(fields[2]), int(fields[3]), int(fields[4])
        start = int(fields[19]) / _clock_ticks()
    except (ValueError, IndexError):
        return None
    info = {"pid": pid, "ppid": ppid, "pgid": pgid, "sid": sid, "tty": tty != 0,
            "comm": comm, "age": max(0.0, _uptime() - start), "argv": [], "nohup": False,
            "cwd": "", "room": "", "identity": "", "pty": "", "cgroup": ""}
    try:
        info["argv"] = [a for a in (base / "cmdline").read_bytes().decode(
            "utf-8", "replace").split("\0") if a]
    except OSError:
        pass
    try:
        for line in (base / "status").read_text().splitlines():
            if line.startswith("SigIgn:"):
                info["nohup"] = bool(int(line.split()[1], 16) & 1)    # SIGHUP is signal 1
    except (OSError, ValueError, IndexError):
        pass
    try:
        info["cwd"] = os.readlink(base / "cwd")
    except OSError:
        pass
    try:
        env = dict(kv.split("=", 1) for kv in (base / "environ").read_bytes().decode(
            "utf-8", "replace").split("\0") if "=" in kv)
        info.update(room=env.get(ROOM_ENV, ""), identity=env.get(IDENTITY_ENV, ""),
                    pty=env.get(PTY_ENV, ""))
    except OSError:
        pass
    try:
        info["cgroup"] = (base / "cgroup").read_text().split("::", 1)[1].strip()
    except (OSError, IndexError):
        pass
    return info


def own_ancestry() -> set[int]:
    """This process and every process above it: whatever runs the guard is
    not stopped by it."""
    seen, pid = set(), os.getpid()
    while pid > 1 and pid not in seen:
        info = read_proc(pid)
        if not info or info["comm"] == "systemd":
            break       # the user manager takes in every orphan: not ours
        seen.add(pid)
        pid = info["ppid"]
    return seen


def pty_host_pids(state_dirs) -> set[int]:
    """The PTY hosts the hub's state dirs name (pty-host.json)."""
    pids = set()
    for d in state_dirs or ():
        try:
            pid = int(json.loads((Path(d) / "pty-host.json").read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid > 0:
            pids.add(pid)
    return pids


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def classify(procs: dict[int, dict], hubs: set[int], hosts: set[int], mine: set[int],
             threshold: float = THRESHOLD_S, moved=frozenset()) -> dict[int, dict]:
    """{pid: {"kind": expected|foreign|recent, "why": str, "root": pid}} for
    every process in ``procs`` (the processes of the cgroups a stop ends).
    ``hubs``, ``hosts``, ``mine``: the hub's pids, its PTY hosts', the guard's
    own and its ancestors'. ``moved``: the pids this run moved out already
    (a child forked while its tree was being moved follows it)."""
    out: dict[int, dict] = {}

    def anchor(pid: int, p: dict | None) -> str:
        if pid in hubs:
            return "the hub"
        if pid in hosts:
            return "the PTY host"
        if pid in mine:
            return "runs this restart"
        if p and p["ppid"] in hosts and p["tty"]:
            return f"a terminal of the PTY host ({_name(p)})"
        return ""

    def sort(pid: int, path: tuple = ()) -> dict:
        if pid in out:
            return out[pid]
        p = procs[pid]
        why = anchor(pid, p)
        if why:
            res = {"kind": "expected", "why": why, "root": pid}
        elif p["ppid"] in hosts:
            # Not a terminal (anchor): the PTY host starts nothing else.
            res = {"root": pid, **_sort_top(p, hosts, threshold, moved)}
        elif p["ppid"] in procs and p["ppid"] not in path:
            up = sort(p["ppid"], path + (pid,))
            if up["kind"] == "expected":
                top = procs.get(up["root"])
                res = {"kind": "expected", "root": up["root"],
                       "why": f"runs under {_name(top) if top else 'an expected process'} "
                              f"(pid {up['root']})"}
            else:
                res = {**up}
        elif anchor(p["ppid"], procs.get(p["ppid"])):
            res = {"kind": "expected", "root": p["ppid"],
                   "why": f"runs under {anchor(p['ppid'], None) or 'an expected process'}"}
        else:
            res = {"root": pid, **_sort_top(p, hosts, threshold, moved)}
        out[pid] = res
        return res

    for pid in procs:
        sort(pid)
    return out


def _sort_top(p: dict, hosts: set[int], threshold: float, moved) -> dict:
    """The top process of a tree no expected process runs."""
    signs = []
    if p["sid"] == p["pid"]:
        signs.append("in a session of its own (setsid)")
    if p["nohup"]:
        signs.append("ignores hang-ups (nohup)")
    if p["ppid"] in hosts:
        signs.append("a child of the PTY host without a terminal")
    if p["ppid"] in moved:
        signs.append(f"its parent (pid {p['ppid']}) was moved out")
    if p["age"] >= threshold:
        signs.append(f"running for {_duration(p['age'])}")
    if signs:
        return {"kind": "foreign", "why": "; ".join(signs)}
    return {"kind": "recent", "why": f"started {_duration(p['age'])} ago, not detached"}


def _name(p: dict) -> str:
    argv0 = (p.get("argv") or [p.get("comm") or "?"])[0]
    return os.path.basename(argv0) or p.get("comm") or "?"


def _duration(seconds: float) -> str:
    s = int(seconds)
    if s < 120:
        return f"{s}s"
    m = s // 60
    if m < 120:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h {m:02d}m"
    return f"{h // 24}d {h % 24}h"


def describe(p: dict, unit: str = "", room_name=None) -> str:
    """One process for a person: pid, command, who started it, how long it
    has run, where it is."""
    cmd = " ".join(p.get("argv") or [p.get("comm") or "?"])
    if len(cmd) > 160:
        cmd = cmd[:157] + "…"
    who = []
    if p.get("room"):
        named = ""
        if room_name:
            try:
                named = room_name(p["room"]) or ""
            except Exception:       # noqa: BLE001 — a name is a nicety
                named = ""
        who.append(named or f"room {p['room']}")
    if p.get("identity"):
        who.append(p["identity"])
    if p.get("pty"):
        who.append(f"terminal {p['pty']}")
    started = ("started by " + ", ".join(who)) if who else "who started it is not known"
    where = f" in {unit}" if unit else ""
    cwd = f", in {p['cwd']}" if p.get("cwd") else ""
    return f"pid {p['pid']} `{cmd}` ({started}{cwd}; running {_duration(p.get('age', 0))}){where}"


# ---------------------------------------------------------------------------
# Moving out
# ---------------------------------------------------------------------------

def _scope_name(p: dict) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]", "-", _name(p))[:40].strip("-.") or "process"
    return f"kept-{stem}-{p['pid']}.scope"


def start_scope(name: str, pids: list[int], description: str) -> str:
    """A transient scope named ``name`` that takes in ``pids`` (the user
    manager's StartTransientUnit). "" on success, else why not."""
    props_busctl = ["PIDs", "au", str(len(pids)), *map(str, pids),
                    "Description", "s", description,
                    "CollectMode", "s", "inactive-or-failed"]
    tries = []
    if shutil.which("busctl"):
        tries.append(["busctl", "--user", "call", "org.freedesktop.systemd1",
                      "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                      "StartTransientUnit", "ssa(sv)a(sa(sv))", name, "fail", "3",
                      *props_busctl, "0"])
    if shutil.which("gdbus"):
        listed = ", ".join(f"uint32 {p}" for p in pids)
        tries.append(["gdbus", "call", "--session", "--dest", "org.freedesktop.systemd1",
                      "--object-path", "/org/freedesktop/systemd1", "--method",
                      "org.freedesktop.systemd1.Manager.StartTransientUnit", name, "fail",
                      f"[('PIDs', <[{listed}]>), ('Description', <{json.dumps(description)}>), "
                      f"('CollectMode', <'inactive-or-failed'>)]", "[]"])
    if not tries:
        return "neither busctl nor gdbus is installed"
    why = ""
    for argv in tries:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            why = f"{argv[0]}: {e}"
            continue
        if r.returncode == 0:
            return ""
        why = f"{argv[0]}: {(r.stderr or r.stdout).strip()[:200] or f'exit {r.returncode}'}"
    return why


def _cgroup_of(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cgroup").read_text().split("::", 1)[1].strip()
    except (OSError, IndexError):
        return None


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

class Guard:
    """One run: list, sort, move out, say. The seams (``units``, ``pids``,
    ``proc``, ``ancestry``, ``hosts``, ``start``, ``where``, ``now``) default
    to the machine; the tests give their own."""

    def __init__(self, units=DEFAULT_UNITS, hub_pids=(), state_dirs=(), threshold=THRESHOLD_S,
                 log_path: Path | None = None, room_name=None, what: str = "the restart",
                 detached: bool = True):
        self.units = list(units)
        self.detached = detached
        self.hub_pids = {int(p) for p in hub_pids if p}
        self.state_dirs = list(state_dirs)
        self.threshold = threshold
        self.log_path = log_path
        self.room_name = room_name
        self.what = what
        self.unit_cgroups = unit_cgroups
        self.cgroup_pids = cgroup_pids
        self.read_proc = read_proc
        self.ancestry = own_ancestry
        self.hosts = pty_host_pids
        self.start = start_scope
        self.where = _cgroup_of
        self.sleep = time.sleep

    def log(self, msg: str) -> None:
        if self.log_path is None:
            return
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [restart] guard: {msg}\n"
        for _ in range(5):
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                return
            except OSError:
                time.sleep(0.05)

    def scan(self, moved=frozenset()):
        """(units, procs {pid: info with "unit"}, sorted {pid: verdict})."""
        units = self.unit_cgroups(self.units, self.detached)
        procs = {}
        for unit, u in units.items():
            for pid in self.cgroup_pids(u["cgroup"]):
                if pid in procs:
                    continue
                info = self.read_proc(pid)
                if info:
                    procs[pid] = {**info, "unit": unit}
        hubs = self.hub_pids | {u["mainPid"] for name, u in units.items()
                                if u["mainPid"] and name.endswith(".service")}
        hosts = self.hosts(self.state_dirs)
        verdicts = classify(procs, hubs, hosts, self.ancestry(), self.threshold, moved)
        return units, procs, verdicts

    def protect(self, force: bool = False) -> dict:
        """Move every foreign tree out of the cgroups a stop would end.
        ``ok``: the stop may go ahead. ``message``: what was found and done,
        for the PO (empty when nothing was found)."""
        result = {"ok": True, "supported": True, "units": [], "foreign": [], "moved": [],
                  "failed": [], "recent": [], "forced": False, "message": ""}
        failed: dict[int, dict] = {}
        moved: set[int] = set()
        for attempt in range(PASSES):
            units, procs, verdicts = self.scan(moved)
            result["units"] = sorted(units)
            if attempt == 0:
                result["recent"] = [describe(procs[p], procs[p]["unit"], self.room_name)
                                    for p, v in verdicts.items()
                                    if v["kind"] == "recent" and v["root"] == p]
                if result["recent"]:
                    self.log(f"left in place, started under {_duration(self.threshold)} ago and "
                             f"not detached: " + "; ".join(result["recent"]))
            trees: dict[int, list[int]] = {}
            for pid, v in verdicts.items():
                if v["kind"] == "foreign":
                    trees.setdefault(v["root"], []).append(pid)
            if not trees:
                break
            for root, pids in sorted(trees.items()):
                top = procs[root]
                text = describe(top, top["unit"], self.room_name)
                if attempt == 0:
                    result["foreign"].append({"pid": root, "pids": sorted(pids), "unit": top["unit"],
                                              "why": verdicts[root]["why"], "text": text})
                    self.log(f"found {text}: {verdicts[root]['why']}"
                             + (f"; with {len(pids) - 1} child process(es)" if len(pids) > 1 else ""))
                name = _scope_name(top)
                if attempt:
                    name = name[:-len(".scope")] + f"-{attempt}.scope"
                why = self.start(name, sorted(pids),
                                 f"Kept out of {top['unit']} by the Ensemble restart guard: "
                                 f"{' '.join(top.get('argv') or [top['comm']])[:200]}")
                if not why:
                    why = self._wait_moved(pids, units)
                if why:
                    failed[root] = {"pid": root, "text": text, "error": why}
                    self.log(f"could not move pid {root} out of {top['unit']}: {why}")
                    continue
                failed.pop(root, None)
                moved.update(pids)
                result["moved"].append({"pid": root, "pids": sorted(pids), "scope": name,
                                        "from": top["unit"], "text": text})
                self.log(f"moved pid {root}" + (f" and {len(pids) - 1} child(ren)" if len(pids) > 1 else "")
                         + f" from {top['unit']} into {name}: {self.what} no longer stops it")
            if failed and all(r in failed for r in trees):
                break       # nothing new moved on this pass: another pass changes nothing
        result["failed"] = list(failed.values())
        if failed:
            if force:
                result["forced"] = True
                self.log(f"forced: {self.what} goes ahead and stops "
                         + "; ".join(f["text"] for f in result["failed"]))
            else:
                result["ok"] = False
                self.log(f"refused: {self.what} would stop "
                         + "; ".join(f["text"] for f in result["failed"]))
        result["message"] = message(result, self.what)
        return result

    def _wait_moved(self, pids: list[int], units: dict) -> str:
        """"" once every pid still alive is out of the cgroups a stop ends."""
        inside = [str(u["cgroup"].relative_to(CGROUP_ROOT)) for u in units.values()
                  if str(u["cgroup"]).startswith(str(CGROUP_ROOT))]
        end = time.monotonic() + MOVE_WAIT_S
        while True:
            left = []
            for pid in pids:
                cg = self.where(pid)
                if cg is None:
                    continue        # it ended meanwhile
                cg = cg.lstrip("/")
                if any(cg == i or cg.startswith(i + "/") for i in inside):
                    left.append(pid)
            if not left:
                return ""
            if time.monotonic() >= end:
                return f"still in place after the move: pid {', '.join(map(str, left))}"
            self.sleep(0.1)


def message(result: dict, what: str = "the restart") -> str:
    """What the PO is told: nothing when nothing was found."""
    if not result.get("supported", True):
        return ""
    parts = []
    if result["moved"]:
        parts.append(f"Moved out before {what}, so it does not stop them (each now runs in a "
                     "systemd scope of its own): "
                     + "; ".join(f"{m['text']} → {m['scope']}" for m in result["moved"]) + ".")
    if result["failed"]:
        names = "; ".join(f"{f['text']} ({f['error']})" for f in result["failed"])
        if result.get("forced"):
            parts.append(f"Forced: {what} goes ahead and stops these, which could not be moved "
                         f"out: {names}.")
        else:
            parts.append(f"Refused: {what} would stop these processes, which could not be moved "
                         f"out of the hub's cgroups: {names}. Stop them or move each into a unit "
                         "of its own (systemd-run --user --unit=<project>-<name> …).")
    return " ".join(parts)


def protect(units=DEFAULT_UNITS, hub_pids=(), state_dirs=(), force: bool = False,
            log_path: Path | None = None, room_name=None, what: str = "the restart",
            threshold: float = THRESHOLD_S, detached: bool = True) -> dict:
    """The one guard every restart and stop runs first. A no-op off Linux
    (see the module's docstring) and on a machine without systemd."""
    if not supported():
        return {"ok": True, "supported": False, "units": [], "foreign": [], "moved": [],
                "failed": [], "recent": [], "forced": False, "message": ""}
    g = Guard(units, hub_pids, state_dirs, threshold,
              default_log() if log_path is None else log_path, room_name, what, detached)
    try:
        return g.protect(force=force)
    except Exception as e:      # noqa: BLE001 — a guard that breaks must say so, not pass silently
        g.log(f"the guard failed: {e.__class__.__name__}: {e}")
        res = {"ok": bool(force), "supported": True, "units": [], "foreign": [], "moved": [],
               "failed": [{"pid": 0, "text": "the guard itself", "error": f"{e.__class__.__name__}: {e}"}],
               "recent": [], "forced": bool(force), "message": ""}
        res["message"] = (f"The restart guard failed ({e.__class__.__name__}: {e}), so it could "
                          f"not tell what {what} would stop" + ("; forced, so it goes ahead."
                                                                if force else "; refused."))
        return res


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Move a project's long-running processes out of "
                                             "the hub's cgroups before a restart or stop.")
    ap.add_argument("--unit", action="append", default=[],
                    help="a unit the stop ends (ensemble.service by default); every "
                         "ensemble-detached-* scope is checked too")
    ap.add_argument("--state-dir", action="append", default=[],
                    help="a hub state dir whose PTY host is expected (~/.ensemble by default)")
    ap.add_argument("--hub-pid", action="append", type=int, default=[])
    ap.add_argument("--no-detached", action="store_true",
                    help="check only the --unit units, not the ensemble-detached-* scopes")
    ap.add_argument("--force", action="store_true", help="go ahead even if some could not be moved")
    ap.add_argument("--what", default="the restart")
    ap.add_argument("--log", default="", help="log file (~/.ensemble/logs/restart.log)")
    a = ap.parse_args(argv)
    res = protect(units=a.unit or DEFAULT_UNITS, hub_pids=a.hub_pid,
                  state_dirs=a.state_dir or [Path.home() / ".ensemble"], force=a.force,
                  log_path=Path(a.log) if a.log else None, what=a.what,
                  detached=not a.no_detached)
    if res["message"]:
        print(res["message"], file=sys.stdout if res["ok"] else sys.stderr)
    return 0 if res["ok"] else REFUSED_EXIT


if __name__ == "__main__":
    sys.exit(main())
