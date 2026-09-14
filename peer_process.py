"""Is a request to the hub coming from an agent on this machine?

Headers and cookies prove nothing about who sent a request: an agent's curl can
send any Origin, any Sec-Fetch-* and, after fetching the page the same way a
browser does, the page's key cookie. What an agent cannot forge is its place in
the process tree. The operating system knows which process owns the client end
of a TCP connection; when that process descends from an agent (a PTY the hub
runs, or a `claude`/`codex` CLI started anywhere), the request is an agent's.

Used for writes only the person at the dashboard may make, such as restoring an
old version of a file. It is a guard against an agent calling such a route by
habit or by mistake (a curl, a script, a shell it runs), not a security
boundary against an agent set on it. The walk follows live parents only, so an
agent can cut it: a wrapper that exits leaves an orphan whose parent is gone.
It can also drive the person's own browser, or start a process outside its
tree. A lock would buy nothing there anyway: a task in a documents project
writes those files itself and can read their history's database directly.

Windows reads the TCP table (GetExtendedTcpTable) and Toolhelp32; macOS and
Linux ask `lsof` and `ps`. Standalone: imports nothing of the hub.
"""
from __future__ import annotations

import ipaddress
import os
import subprocess
import sys

AGENT_EXES = {"claude", "codex"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Unknown(Exception):
    """The owner of a local connection could not be found out."""


def _plain_ip(ip: str) -> str:
    ip = (ip or "").split("%")[0]
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 6 and a.ipv4_mapped:
        return str(a.ipv4_mapped)
    return str(a)


def _exe_stem(name: str) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


# ---- Windows -----------------------------------------------------------------

def _win_tables():
    import ctypes
    import socket
    from ctypes import wintypes

    iphlp = ctypes.WinDLL("iphlpapi")
    fn = iphlp.GetExtendedTcpTable
    fn.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG,
                   ctypes.c_int, wintypes.ULONG)
    fn.restype = wintypes.DWORD

    class Row4(ctypes.Structure):
        _fields_ = [("state", wintypes.DWORD), ("laddr", wintypes.DWORD), ("lport", wintypes.DWORD),
                    ("raddr", wintypes.DWORD), ("rport", wintypes.DWORD), ("pid", wintypes.DWORD)]

    class Row6(ctypes.Structure):
        _fields_ = [("laddr", ctypes.c_ubyte * 16), ("lscope", wintypes.DWORD), ("lport", wintypes.DWORD),
                    ("raddr", ctypes.c_ubyte * 16), ("rscope", wintypes.DWORD), ("rport", wintypes.DWORD),
                    ("state", wintypes.DWORD), ("pid", wintypes.DWORD)]

    def port(v: int) -> int:
        return ((v & 0xFF) << 8) | ((v >> 8) & 0xFF)

    rows = []
    for af, row, fam in ((2, Row4, socket.AF_INET), (23, Row6, socket.AF_INET6)):
        size = wintypes.DWORD(0)
        fn(None, ctypes.byref(size), False, af, 4, 0)       # 4: TCP_TABLE_OWNER_PID_CONNECTIONS
        for _ in range(4):
            buf = ctypes.create_string_buffer(max(size.value, 4))
            rc = fn(buf, ctypes.byref(size), False, af, 4, 0)
            if rc == 0:
                break
            if rc != 122:                                    # ERROR_INSUFFICIENT_BUFFER
                raise Unknown(f"GetExtendedTcpTable failed ({rc})")
        else:
            raise Unknown("the TCP table kept growing")
        n = wintypes.DWORD.from_buffer_copy(buf.raw[:4]).value
        off = ctypes.sizeof(wintypes.DWORD)
        if off + n * ctypes.sizeof(row) > len(buf.raw):
            raise Unknown("short TCP table")
        for r in (row * n).from_buffer_copy(buf.raw, off):
            if fam == socket.AF_INET:
                la = socket.inet_ntop(fam, r.laddr.to_bytes(4, "little"))
                ra = socket.inet_ntop(fam, r.raddr.to_bytes(4, "little"))
            else:
                la, ra = socket.inet_ntop(fam, bytes(r.laddr)), socket.inet_ntop(fam, bytes(r.raddr))
            rows.append((_plain_ip(la), port(r.lport), _plain_ip(ra), port(r.rport), int(r.pid)))
    return rows


def _win_processes() -> dict[int, tuple[int, str]]:
    from backends import windows
    procs = windows._process_map()
    if not procs:
        raise Unknown("no process list")
    return {pid: (ppid, _exe_stem(exe)) for pid, (ppid, exe) in procs.items()}


# ---- macOS / Linux -------------------------------------------------------------

def _run(argv: list[str]) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=10, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise Unknown(f"{argv[0]}: {e}")
    return r.stdout or ""


def _posix_owners(client: tuple[str, int]) -> list[int]:
    host = client[0] if ":" not in client[0] else f"[{client[0]}]"
    out = _run(["lsof", "-nP", f"-iTCP@{host}:{client[1]}", "-Fp"])
    return [int(ln[1:]) for ln in out.splitlines() if ln.startswith("p") and ln[1:].isdigit()]


def _posix_processes() -> dict[int, tuple[int, str]]:
    procs = {}
    for ln in _run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "comm="]).splitlines():
        parts = ln.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), _exe_stem(parts[2] if len(parts) > 2 else ""))
    if not procs:
        raise Unknown("no process list")
    return procs


# ---- the question ----------------------------------------------------------------

def owner(client: tuple[str, int], server: tuple[str, int]) -> int | None:
    """The pid of the process on this machine that holds the client end of the
    connection ``client`` -> ``server``; None when no process here does (the
    request came from another machine). Raises Unknown when it cannot tell."""
    cip, cport = _plain_ip(client[0]), int(client[1])
    sip, sport = _plain_ip(server[0]), int(server[1])
    me = os.getpid()
    if sys.platform == "win32":
        for la, lp, ra, rp, pid in _win_tables():
            if lp == cport and rp == sport and la == cip and ra == sip and pid != me:
                return pid
        return None
    pids = [p for p in _posix_owners((cip, cport)) if p != me]
    return pids[0] if pids else None


def agent_ancestor(pid: int, procs: dict[int, tuple[int, str]], agent_pids: set[int],
                   hub_pid: int = 0) -> int | None:
    """The first process from ``pid`` upwards that is an agent: one of
    ``agent_pids`` or a claude/codex executable. None when there is none, or
    when the walk reaches the hub first (a browser the hub opened is not an
    agent's, even if an agent once started the hub)."""
    seen, cur = set(), pid
    for _ in range(64):
        if cur in seen or cur <= 0:
            return None
        seen.add(cur)
        if cur in agent_pids:
            return cur
        if hub_pid and cur == hub_pid:
            return None
        ent = procs.get(cur)
        if ent is None:
            return None
        ppid, exe = ent
        if exe in AGENT_EXES:
            return cur
        cur = ppid
    return None


def from_agent(client: tuple[str, int], server: tuple[str, int], agent_pids: set[int]) -> str:
    """Why this request must be treated as an agent's, or "" when it is not.
    A connection from this machine whose owner cannot be found is refused."""
    loopback = False
    try:
        loopback = ipaddress.ip_address(_plain_ip(client[0])).is_loopback
    except ValueError:
        pass
    try:
        pid = owner(client, server)
        if pid is None:
            return "cannot tell which program sent it" if loopback else ""
        procs = _win_processes() if sys.platform == "win32" else _posix_processes()
    except Unknown as e:
        return f"cannot tell which program sent it ({e})"
    except Exception as e:                       # noqa: BLE001 - a refusal, never a crash
        return f"cannot tell which program sent it ({type(e).__name__})"
    hit = agent_ancestor(pid, procs, set(agent_pids), os.getpid())
    return f"it came from an agent's process ({hit})" if hit else ""
