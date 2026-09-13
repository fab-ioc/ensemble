"""Go to file and search in files, for a Workspace.

The Workspace's find box lists the files of one folder (a task's, or a
project's) and searches their text. Both stay inside that folder:

* the walk never descends into ``.git`` or ``node_modules``, nor into what a
  repo's own ``.gitignore`` excludes (git decides, per repo met on the way);
* a folder reached through a symlink or a junction is never entered, and a
  linked file counts only when its target is inside the folder too;
* a folder is listed, and a file read, only when what was actually opened is
  inside the folder: a name can be swapped for a link, or a folder above it
  can, between being listed and being read.

The hub lists files in its own process (one git call per repo, fast), but runs
a search as a child process (``python workspace_search.py`` reading a JSON
request on stdin): a regular expression can take unbounded time, Python's
``re`` holds the interpreter while it runs, and a hub stuck in one would stop
serving every page and agent. A child can be killed, by a timeout or by the
next search from the same box.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time

SKIP_DIRS = {".git", "node_modules"}
FILES_MAX = 50_000            # files listed; past this the list says it is cut
MATCH_MAX = 1000              # matches returned; past this "more results not shown"
FILE_BYTES_MAX = 2 * 1024 * 1024   # larger files are counted as skipped, not read
LINE_SHOWN = 240              # characters of a matching line sent back
BEFORE_MATCH = 24             # of which, at most this many before the first match
QUERY_MAX = 500
DEADLINE_S = 8.0              # a search stops here and returns what it found

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# A FIFO opened for reading would wait for a writer; this way it opens at once
# and is then passed over as not a regular file.
_READ_FLAGS = getattr(os, "O_NONBLOCK", 0)


def _inside(real: str, real_root: str) -> bool:
    return real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep)


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


# ---- Where an opened file or folder really is -------------------------------

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _INVALID_HANDLE = wintypes.HANDLE(-1).value

    def _handle_final(h) -> str:
        """Where what ``h`` opened really is, every link resolved, as Windows
        writes it: ``\\\\?\\C:\\...`` or ``\\\\?\\UNC\\server\\...``."""
        buf = ctypes.create_unicode_buffer(32768)
        n = _k32.GetFinalPathNameByHandleW(h, buf, len(buf), 0)
        if not n or n >= len(buf):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.value

    def _plain(final: str) -> str:
        if final.startswith("\\\\?\\UNC\\"):
            final = "\\\\" + final[8:]
        elif final.startswith("\\\\?\\"):
            final = final[4:]
        return os.path.normcase(final)

    def _fd_real(fd: int) -> str:
        return _plain(_handle_final(msvcrt.get_osfhandle(fd)))

    def _long(p: str) -> str:
        """``p`` spelt the way Windows opens it past 260 characters."""
        if p.startswith("\\\\?\\"):
            return p
        p = os.path.abspath(p)
        return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p

    def _scan(d: str, real_root: str, ignores_rel: str | None) -> tuple[str, list[os.DirEntry], set[str]] | None:
        # Held open without FILE_SHARE_DELETE while it is listed and git is
        # asked about it: Windows then refuses to rename or remove it, or any
        # folder above it. And reached by where it really is, not by its name:
        # a name that is a junction can be pointed elsewhere once its target
        # has been checked, but the target, held, stays where it was checked.
        # That spelling (\\?\...) also reaches past 260 characters.
        h = _k32.CreateFileW(d, 0x80000000, 0x1 | 0x2, None, 3, 0x02000000, None)  # GENERIC_READ, share read+write, OPEN_EXISTING, BACKUP_SEMANTICS
        if h == _INVALID_HANDLE:
            return None
        try:
            final = _handle_final(h)
            if not _inside(_plain(final), real_root):
                return None
            return _listed(final, final, ignores_rel)
        except OSError:
            return None
        finally:
            _k32.CloseHandle(h)

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _JOB = None

    def _children_die_with_me() -> None:
        """Every process this one starts ends when it ends, however it ends:
        a search killed during its ``git ls-files`` leaves no git running. It
        joins a job that kills what is in it once the job's last handle, held
        only here, is closed by this process ending. ``OSError`` when it cannot."""
        global _JOB
        _k32.CreateJobObjectW.restype = wintypes.HANDLE
        _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        _k32.GetCurrentProcess.restype = wintypes.HANDLE
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not (_k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # ExtendedLimitInformation
                and _k32.AssignProcessToJobObject(job, _k32.GetCurrentProcess())):
            err = ctypes.WinError(ctypes.get_last_error())
            _k32.CloseHandle(job)
            raise err
        _JOB = job
else:
    def _fd_real(fd: int) -> str:
        if sys.platform == "darwin":
            import fcntl
            buf = fcntl.fcntl(fd, 50, b"\0" * 1024)                  # F_GETPATH
            return os.path.normcase(buf.split(b"\0", 1)[0].decode("utf-8", "surrogateescape"))
        return os.path.normcase(os.readlink(f"/proc/self/fd/{fd}"))

    def _long(p: str) -> str:
        return p

    def _scan(d: str, real_root: str, ignores_rel: str | None) -> tuple[str, list[os.DirEntry], set[str]] | None:
        # Listed through the very folder that was opened and checked; git is
        # asked about it by the path that folder reported.
        try:
            fd = os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return None
        try:
            real = _fd_real(fd)
            if not _inside(real, real_root):
                return None
            return _listed(fd, real, ignores_rel)
        except OSError:
            return None
        finally:
            os.close(fd)

    def _children_die_with_me() -> None:
        """The hub starts a search in a process group of its own and ends the
        whole group, so nothing is needed here."""


def _git_ignored(d: str, rel: str) -> set[str]:
    """What git ignores under ``d``, as paths relative to the walk's root:
    files as ``a/b.log``, wholly ignored folders as ``a/build``."""
    try:
        out = subprocess.run(
            ["git", "-C", d, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {rel + p.rstrip("/") for p in (out.stdout or "").split("\x00") if p}


def _listed(source, base: str, ignores_rel: str | None) -> tuple[str, list[os.DirEntry], set[str]]:
    """A checked folder, while it is still held: ``base``, the path its entries
    are reached by; its entries, by name; and, when ``ignores_rel`` is given and
    it is a repo, what git ignores in it."""
    with os.scandir(source) as it:
        entries = sorted(it, key=lambda e: e.name)
    repo = ignores_rel is not None and any(e.name == ".git" for e in entries)
    return base, entries, _git_ignored(base, ignores_rel) if repo else set()


def _is_link(e: os.DirEntry) -> bool:
    try:
        return e.is_symlink() or bool(getattr(e, "is_junction", lambda: False)())
    except OSError:
        return True


def list_files(root: str, enclosing_ignores: bool = False, limit: int = FILES_MAX) -> tuple[list[str], bool]:
    """The files under ``root``, as ``/``-separated paths relative to it, and
    whether the list was cut at ``limit``. ``enclosing_ignores`` applies the
    ignore rules of a repo that ``root`` sits inside (not only those of repos
    found under it)."""
    real_root = _real(root)
    ignored: set[str] = _git_ignored(root, "") if enclosing_ignores else set()
    files: list[str] = []
    stack = [(root, "")]
    while stack:
        d, rel = stack.pop()
        scanned = _scan(d, real_root, None if rel == "" and enclosing_ignores else rel)
        if scanned is None:
            continue
        base, entries, found = scanned
        ignored |= found
        dirs = []
        for e in entries:
            r = rel + e.name
            if e.name in SKIP_DIRS or r in ignored:
                continue
            path = os.path.join(base, e.name)
            link = _is_link(e)
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if link:
                # Never walk a linked folder: it can lead out of the root, or
                # round in a loop. A linked file counts when it lands inside.
                try:
                    if not os.path.isfile(path) or not _inside(_real(path), real_root):
                        continue
                except OSError:
                    continue
            elif is_dir:
                dirs.append((path, r + "/"))
                continue
            if len(files) >= limit:
                return files, True
            files.append(r)
        stack.extend(reversed(dirs))
    return files, False


def compile_query(q: str, case: bool = False, regex: bool = False) -> re.Pattern:
    """The pattern a query searches for; ``re.error`` for a regex that is not one."""
    flags = re.MULTILINE | (0 if case else re.IGNORECASE)
    return re.compile(q if regex else re.escape(q), flags)


def _u16(s: str) -> int:
    """Length in UTF-16 code units: how a browser counts a string."""
    return len(s) if s.isascii() else len(s.encode("utf-16-le")) // 2


def _line_hit(line: str, spans: list[tuple[int, int]]) -> dict:
    """A matching line as sent to the page: the part of it worth showing, and
    where the matches fall in that part. ``at`` is where the part starts in the
    line, so ``at + range start`` is the match's column in the file. All
    offsets are UTF-16 units, the way the page will slice the text."""
    first = spans[0][0]
    indent = len(line) - len(line.lstrip())
    start = indent if first >= indent else 0
    start = max(start, first - BEFORE_MATCH)
    end = min(len(line), start + LINE_SHOWN)
    part = line[start:end]
    at = _u16(line[:start])
    ranges = []
    for s, e in spans:
        if s >= end:
            break
        e = min(e, end)
        ranges.append([_u16(line[start:s]), _u16(line[s:e])])
    return {"at": at, "text": part, "ranges": ranges, "n": len(spans),
            "cutStart": bool(line[:start].strip()), "cutEnd": end < len(line)}


def _open_read(path: str, flags: int) -> int:
    return os.open(path, flags | _READ_FLAGS)


def search(root: str, q: str, case: bool = False, regex: bool = False, enclosing_ignores: bool = False,
           limit: int = MATCH_MAX, deadline_s: float = DEADLINE_S) -> dict:
    t0 = time.monotonic()
    pat = compile_query(q, case, regex)
    files, list_cut = list_files(root, enclosing_ignores)
    real_root = _real(root)
    out: list[dict] = []
    n = searched = binary = large = unreadable = 0
    cut = timed_out = False
    for rel in files:
        if time.monotonic() - t0 > deadline_s:
            timed_out = True
            break
        try:
            with open(_long(os.path.join(root, rel)), "rb", opener=_open_read) as f:
                # Judged by the file that opened, not by its name: a link put
                # at that name, or at a folder above it, since it was listed
                # leads here to a file outside, which is passed over.
                if not _inside(_fd_real(f.fileno()), real_root):
                    continue
                st = os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode):
                    continue
                if st.st_size > FILE_BYTES_MAX:
                    large += 1
                    continue
                raw = f.read(FILE_BYTES_MAX + 1)
        except OSError:
            unreadable += 1
            continue
        if len(raw) > FILE_BYTES_MAX:             # grew since it was measured
            large += 1
            continue
        searched += 1
        if b"\x00" in raw:
            binary += 1
            continue
        # Windows line ends read as plain ones, so a regex's $ ends a line there too.
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if not pat.search(text):
            continue
        hits = []
        for i, line in enumerate(text.split("\n")):
            spans = []
            for m in pat.finditer(line):
                if m.end() == m.start():
                    continue          # an empty match marks nothing
                if n >= limit:
                    cut = True
                    break
                spans.append((m.start(), m.end()))
                n += 1
            if spans:
                hits.append({"line": i + 1, **_line_hit(line, spans)})
            if cut:
                break
        if hits:
            out.append({"path": rel, "matches": hits})
        if cut:
            break
    return {"files": out, "matches": n, "truncated": cut, "limit": limit,
            "filesListed": len(files), "listTruncated": list_cut, "filesSearched": searched,
            "skipped": {"binary": binary, "large": large, "unreadable": unreadable},
            "largeBytes": FILE_BYTES_MAX, "timedOut": timed_out, "deadline": deadline_s,
            "ms": round((time.monotonic() - t0) * 1000)}


def _answer(res: dict) -> None:
    sys.stdout.buffer.write(json.dumps(res).encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        _children_die_with_me()
    except OSError as e:
        # Searching anyway would leave git running whenever a search is
        # stopped during its git call: say so instead.
        _answer({"error": "search_failed", "detail": f"could not make the search's git calls end with it: {e}"})
        return 1
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    try:
        res = search(req["root"], req["q"], bool(req.get("case")), bool(req.get("regex")),
                     bool(req.get("enclosingIgnores")), int(req.get("limit", MATCH_MAX)),
                     float(req.get("deadline", DEADLINE_S)))
    except re.error as e:
        res = {"error": "bad_regex", "detail": str(e)}
    _answer(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
