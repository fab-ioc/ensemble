"""A session's files brought into a documents project (Make PO).

A conversation started in a terminal has its documents in the folder it was
started in; made the PO of a documents project, it would otherwise lead a
project whose folder is empty. This module counts what that folder holds
(``scan``, bounded in time and size), says when it must not be offered at all
(``refusal``: a home folder, a drive root, the projects folder), copies it
(``bring``: copy, never move, never overwrite) and takes a copy back
(``undo``: only the files and folders that copy wrote).

Left out, and counted: what the backup and the file history never keep
(``backup.GITIGNORE``, ``backup.LOCAL_EXCLUDES``, ``history.NOT_KEPT_NAMES``),
agent and IDE state, links and junctions, nested git repositories, task
folders, and the hub's own records. A path Windows cannot open (260
characters) is skipped and counted, never an error.

Decoupled from dashboard.py: plain paths in, plain dicts out.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import threading
import time

import backup
import history

# The bound. A documents project is a folder of documents: the hub keeps every
# version of every file in a git database beside them and the backup commits
# them again, so what is brought is paid for three times on disk, and the
# person waits in the dialog while it is copied and first recorded. 2,000 files
# and 300 MB is some fifty times the folder this was built for (56 files,
# 38 MB) and still copies and snapshots in well under a minute on a laptop
# disk; past it the folder is a code tree, a data capture or a home folder, and
# is better looked at by hand than copied whole.
MAX_FILES = 2000
MAX_BYTES = 300 * 1024 * 1024
PREVIEW_S = 2.0          # the dialog's count: answers by then, whatever the folder
SCAN_S = 10.0            # the count before a copy
COPY_S = 120.0           # a copy still running by then is taken back
BACKUP_CAP = backup.MAX_FILE_MB * 1024 * 1024
NAMED_MAX = 20           # files named one by one in a result
_CHUNK = 1024 * 1024
# Windows opens a path of up to 259 characters; elsewhere the limit is far away.
PATH_MAX = 259 if os.name == "nt" else 4095


def _backup_names() -> tuple[set[str], list[str]]:
    """(folder names, file patterns) the backup's exclude rules name."""
    dirs, files = set(), []
    lines = [ln.strip() for ln in backup.GITIGNORE.splitlines()] + list(backup.LOCAL_EXCLUDES)
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        name = ln[3:] if ln.startswith("**/") else ln.lstrip("/")
        if name.endswith("/"):
            dirs.add(name.rstrip("/").lower())
        else:
            files.append(name.lower())
    return dirs, files


_BACKUP_DIRS, _BACKUP_FILES = _backup_names()
LEFT_OUT_DIRS = ({".git", ".idea", ".claude", ".codex", ".ensemble", history.DIR_NAME, "node_modules",
                  "__pycache__", "appdata"} | {d.lower() for d in backup._NO_WALK} | _BACKUP_DIRS)
LEFT_OUT_FILES = tuple(dict.fromkeys([*_BACKUP_FILES, *(p.lower() for p in history.NOT_KEPT_NAMES)]))
# The hub's own records in a project's folder, and a task's in its own. Another
# project's handover or roadmap at the top would pass for this project's own: a
# rotation would start a fresh PO from it.
_ROOT_ONLY = {"project.json", "project.json.tmp", "_linked", "po-handover.md", "roadmap.md"}
_TASK_FILES = tuple(p.lower() for p in history.TASK_EXCLUDES if not p.endswith("/"))


class BringFailed(Exception):
    """The copy could not be finished; what it had written is gone again."""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.realpath(path)))


def _within(path: str, folder: str) -> bool:
    """``path`` is ``folder`` or inside it (both already normalised)."""
    return path == folder or path.startswith(folder.rstrip("\\/") + os.sep)


def user_home() -> str:
    return os.path.expanduser("~")


def refusal(src: str, home: str = "", projects_root: str = "") -> str:
    """Why the files of ``src`` are not brought at all, in one line; "" when
    they may be. ``home`` is the project's folder when it is known."""
    if not src or not os.path.isdir(src):
        return "The session's folder no longer exists, so there are no files to bring."
    real = _norm(src)
    if os.path.dirname(real) == real:
        return (f"The session was started in {src}, a whole drive, so its files are not brought: "
                "copy what the project needs into its folder by hand.")
    if _within(_norm(user_home()), real):
        return (f"The session was started in {src}, which is your home folder or holds it, so its files are "
                "not brought: copy what the project needs into its folder by hand.")
    if projects_root and _within(_norm(projects_root), real):
        return (f"The session was started in {src}, which holds the projects folder itself, so its files "
                "are not brought.")
    if home:
        dest = _norm(home)
        if _within(real, dest):
            return "The session's folder is already in the project's folder, so there is nothing to bring."
        if _within(dest, real):
            return (f"The project's folder is inside the session's folder {src}, so its files are not "
                    "brought: the copy would hold itself.")
    return ""


def _is_link(entry) -> bool:
    """A symlink or a Windows junction: never followed, never copied."""
    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)
        if junction is not None:
            return bool(junction())
        tag = getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0)
        return bool(tag) and tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)
    except OSError:
        return True


def too_long(path: str) -> bool:
    return len(os.path.abspath(path)) > PATH_MAX


def _dir_left_out(name: str, path: str, at_root: bool) -> bool:
    low = name.lower()
    if low in LEFT_OUT_DIRS or history.not_kept(name) or (at_root and low in _ROOT_ONLY):
        return True
    # A repository of its own, or a task's folder: neither is this project's documents.
    return any(os.path.lexists(os.path.join(path, mark)) for mark in (".git", "task.json"))


def _file_left_out(name: str, at_root: bool, task_root: bool) -> bool:
    low = name.lower()
    if low == "task.json" or (at_root and low in _ROOT_ONLY):
        return True
    if any(fnmatch.fnmatchcase(low, p) for p in LEFT_OUT_FILES):
        return True
    return at_root and task_root and any(fnmatch.fnmatchcase(low, p) for p in _TASK_FILES)


def _walk(src: str, out: dict, stop) -> None:
    task_root = os.path.isfile(os.path.join(src, "task.json"))
    stack = [("", src)]
    while stack:
        rel, path = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            out["unreadable"] += 1
            continue
        below = []
        for e in entries:
            if stop():
                out["why"] = out["why"] or "time"
                return
            child = f"{rel}/{e.name}" if rel else e.name
            if too_long(e.path):
                out["tooLong"] += 1
                continue
            if _is_link(e):
                out["leftOut"] += 1
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if _dir_left_out(e.name, e.path, not rel):
                        out["leftOut"] += 1
                    else:
                        below.append((child, e.path))
                    continue
                if not e.is_file(follow_symlinks=False) or _file_left_out(e.name, not rel, task_root):
                    out["leftOut"] += 1
                    continue
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                out["unreadable"] += 1
                continue
            out["files"].append((child, size))
            out["bytes"] += size
            if len(out["files"]) > MAX_FILES:
                out["why"] = "count"
                return
            if out["bytes"] > MAX_BYTES:
                out["why"] = "size"
                return
        stack.extend(reversed(below))


def scan(src: str, seconds: float = SCAN_S) -> dict:
    """What ``src`` holds that would be brought: ``{files: [(rel, size)],
    bytes, leftOut, tooLong, unreadable, why}``. ``why`` is "" when the folder
    was counted whole and is within the bound, else "count", "size" or "time".
    Answers within ``seconds`` whatever the folder: the walk runs on a thread
    of its own, which a single slow directory read cannot hold the caller on."""
    out = {"files": [], "bytes": 0, "leftOut": 0, "tooLong": 0, "unreadable": 0, "why": ""}
    deadline = time.monotonic() + max(0.05, seconds)
    gone = threading.Event()

    def stop() -> bool:
        return gone.is_set() or time.monotonic() > deadline

    walker = threading.Thread(target=_walk, args=(src, out, stop), name="bring-files-scan", daemon=True)
    walker.start()
    walker.join(max(0.05, seconds) + 0.25)
    if walker.is_alive():
        gone.set()
        return {"files": [], "bytes": 0, "leftOut": 0, "tooLong": 0, "unreadable": 0, "why": "time"}
    return out


def _mb(n: int) -> str:
    return f"{n / 1048576:.0f} MB" if n >= 10 * 1048576 else f"{n / 1048576:.1f} MB"


def bound_words(src: str, why: str, seconds: float) -> str:
    """The bound, said to the person."""
    what = {"count": f"it holds more than {MAX_FILES:,} files",
            "size": f"it holds more than {_mb(MAX_BYTES)}",
            "time": f"it could not be counted in {seconds:g} seconds"}.get(why, why)
    return (f"The files in {src} are not brought: {what}, more than a documents project is meant to keep. "
            "Copy what the project needs into its folder by hand.")


def preview(src: str, home: str = "", projects_root: str = "", seconds: float = PREVIEW_S) -> dict:
    """What the dialog shows before the person confirms: ``{offer, reason,
    folder, files, bytes, notInBackup}``. Reads only."""
    res = {"offer": False, "reason": "", "folder": src, "files": 0, "bytes": 0, "notInBackup": 0}
    res["reason"] = refusal(src, home, projects_root)
    if res["reason"]:
        return res
    found = scan(src, seconds)
    if found["why"]:
        res["reason"] = bound_words(src, found["why"], seconds)
        return res
    res.update(files=len(found["files"]), bytes=found["bytes"],
               notInBackup=sum(1 for _, size in found["files"] if size > BACKUP_CAP))
    if not res["files"]:
        res["reason"] = f"{src} holds no files of its own to bring into the project."
        return res
    res["offer"] = True
    return res


def _make_parents(parent: str, res: dict) -> bool:
    """Create ``parent`` and what is missing above it, remembering each folder
    made. False when a file stands where a folder is needed."""
    missing, cur = [], parent
    while cur and not os.path.isdir(cur):
        if os.path.lexists(cur):
            return False
        missing.append(cur)
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    for folder in reversed(missing):
        os.mkdir(folder)
        res["madeDirs"].append(folder)
    return True


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        os.remove(path)


def undo(res: dict) -> None:
    """Take a copy back: the files it wrote, then the folders it made (those
    still empty). Nothing else in the folder is touched."""
    for path in reversed(res.get("written") or []):
        try:
            _remove(path)
        except OSError:
            pass
    for folder in reversed(res.get("madeDirs") or []):
        try:
            os.rmdir(folder)
        except OSError:
            pass
    res["written"], res["madeDirs"] = [], []


def bring(src: str, home: str, files: list, seconds: float = COPY_S) -> dict:
    """Copy ``files`` (``scan``'s ``(rel, size)`` pairs) from ``src`` into
    ``home``. A file already there is never replaced (skipped and counted); so
    is a path too long to open, and a file that cannot be read. A write that
    fails, or a copy not finished in ``seconds``, takes back what it wrote and
    raises BringFailed. Returns the counts, the files over the backup's cap,
    and ``written`` / ``madeDirs`` for ``undo``."""
    res = {"copied": 0, "bytes": 0, "alreadyThere": 0, "tooLong": 0, "unreadable": 0,
           "notInBackup": [], "notInBackupCount": 0, "written": [], "madeDirs": []}
    deadline = time.monotonic() + seconds
    real_home, in_the_way = _norm(home), {}

    def taken(parts: list) -> bool:
        """Something of the project's stands where this file would go: a
        task's folder of the same name, or a link that leads out of the
        project's folder, however far above the folder still to be made
        (realpath follows the part of the path that exists). Asked once per
        folder."""
        parent = os.path.join(home, *parts[:-1])
        if parent not in in_the_way:
            in_the_way[parent] = bool(
                (len(parts) > 1 and os.path.isfile(os.path.join(home, parts[0], "task.json")))
                or not _within(_norm(parent), real_home))
        return in_the_way[parent]

    try:
        for rel, _size in files:
            if time.monotonic() > deadline:
                raise BringFailed(f"Copying the files from {src} took more than {seconds:g} seconds, "
                                  "so the copy was taken back.")
            parts = rel.split("/")
            source, dest = os.path.join(src, *parts), os.path.join(home, *parts)
            if too_long(source) or too_long(dest):
                res["tooLong"] += 1
                continue
            if os.path.lexists(dest) or taken(parts):
                res["alreadyThere"] += 1
                continue
            try:
                fin = open(source, "rb")
            except OSError:
                res["unreadable"] += 1
                continue
            with fin:
                try:
                    if not _make_parents(os.path.dirname(dest), res):
                        res["alreadyThere"] += 1
                        continue
                    fout = open(dest, "xb")
                except FileExistsError:
                    res["alreadyThere"] += 1
                    continue
                except OSError as e:
                    raise BringFailed(f"“{rel}” could not be written in the project's folder "
                                      f"({e.strerror or e}), so the copy was taken back.") from e
                res["written"].append(dest)
                read_failed, size = False, 0
                with fout:
                    while True:
                        try:
                            chunk = fin.read(_CHUNK)
                        except OSError:
                            read_failed = True
                            break
                        if not chunk:
                            break
                        try:
                            fout.write(chunk)
                        except OSError as e:
                            raise BringFailed(f"“{rel}” could not be written in the project's folder "
                                              f"({e.strerror or e}), so the copy was taken back.") from e
                        size += len(chunk)
                        if time.monotonic() > deadline:
                            raise BringFailed(f"Copying the files from {src} took more than {seconds:g} "
                                              "seconds, so the copy was taken back.")
            if read_failed:
                res["written"].remove(dest)
                try:
                    _remove(dest)
                except OSError:
                    pass
                res["unreadable"] += 1
                continue
            try:
                shutil.copystat(source, dest)
            except OSError:
                pass
            res["copied"] += 1
            res["bytes"] += size
            if size > BACKUP_CAP:
                res["notInBackupCount"] += 1
                if len(res["notInBackup"]) < NAMED_MAX:
                    res["notInBackup"].append({"path": rel, "size": size,
                                               "inHistory": size <= history.MAX_FILE_BYTES})
    except BaseException:
        undo(res)
        raise
    return res
