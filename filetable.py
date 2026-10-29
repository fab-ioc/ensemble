"""The files under a folder and their size and time, kept up to date by work
that follows what changed, not how much history there is (#228).

The hub's lists walked every transcript (~2,000), Codex rollout (~1,200) and
room (~580) on each 2-second refresh: ~8,000 stats, most of a second, more
every day. A folder's time moves when a file in it is added, removed or
replaced (the hub writes rooms by replacing them), not when one is appended
to. So a refresh lists again only the folders whose time moved, and stats
again only the files changed lately (HOT_S) or named by the caller (a live
session's transcript). A full walk, at most every FULL_S, catches the rest:
an old file appended to after a quiet day shows within a minute.

A folder none of whose files changed for QUIET_S (a finished task's
transcripts) is not looked at between full walks either (#256: 600 project
folders cost 46 ms a refresh, most of them for tasks done long ago): a
session started in it again shows within a minute, and so does a lookup by
session id that does not glob on a miss (dashboard.find_transcript with
immediate=False: a points sync between sends); a send's lookup finds it at
once. A folder made or changed lately is not quiet, files or not.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

HOT_S = 6 * 3600.0
QUIET_S = 24 * 3600.0
FULL_S = 60.0
# File times tick coarsely (~16 ms on Windows): a file added in the tick a
# folder was listed in leaves the folder's time as it was. A listing is
# trusted only once taken this long after the folder's last change.
SETTLE_NS = 2_000_000_000


class FileTable:
    """``root`` (a callable, so a test may point the module's folder elsewhere),
    ``depth`` folders below it before the files (0: files in root itself),
    ``match`` a file name test. ``files()``: {path: (mtime, size, mtime_ns)}."""

    def __init__(self, root, depth: int, match):
        self._root_fn = root
        self._depth = depth
        self._match = match
        self._lock = threading.Lock()
        self._reset("")

    def _reset(self, root: str) -> None:
        self._root = root
        # folder -> (mtime_ns, sub folders, names, listing settled, newest file's time)
        self._dirs: dict[str, tuple[int, list[str], list[str], bool, float]] = {}
        self._files: dict[str, tuple[float, int, int]] = {}
        self._full_at = 0.0
        self._at = 0.0

    def forget(self) -> None:
        """The next look walks everything (a test, or files changed behind it)."""
        with self._lock:
            self._full_at = self._at = 0.0

    def files(self, hot_names=(), fresh_s: float = 0.0) -> dict[str, tuple[float, int, int]]:
        """``fresh_s``: the last look, if younger than that (a lookup per row
        of one refresh)."""
        root = str(self._root_fn())
        now = time.time()
        with self._lock:
            if root != self._root:
                self._reset(root)
            elif fresh_s and not hot_names and now - self._at < fresh_s:
                return dict(self._files)
            self._at = now
            full = now - self._full_at >= FULL_S
            if full:
                self._full_at = now
            files: dict[str, tuple[float, int, int]] = {}
            dirs: dict[str, tuple[int, list[str], list[str], bool, float]] = {}
            self._walk(root, 0, full, now, set(hot_names), files, dirs)
            self._dirs, self._files = dirs, files
            return dict(files)

    def _walk(self, d: str, level: int, full: bool, now: float, hot: set,
              files: dict, dirs: dict) -> None:
        seen = self._dirs.get(d)
        if not full and seen and seen[3] and 0 < level == self._depth and seen[4] < now - QUIET_S \
                and not any(n.rsplit(".", 1)[0] in hot for n in seen[2]):
            # quiet for a day: as the last walk had it, until the next full one
            # (never the root: the rooms are its own files, written by this hub)
            dirs[d] = seen
            for name in seen[2]:
                p = os.path.join(d, name)
                if p in self._files:
                    files[p] = self._files[p]
            return
        try:
            dm = os.stat(d).st_mtime_ns
        except OSError:
            return
        listed: dict[str, tuple[float, int, int]] = {}
        if not full and seen and seen[3] and seen[0] == dm:
            subs, names, settled = seen[1], seen[2], True
        else:
            subs, names = [], []
            settled = time.time_ns() - dm > SETTLE_NS
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if level < self._depth:
                                # A linked folder is followed (#285): a
                                # transcript folder moved to another disk
                                # still counts. The walk stops at ``depth``,
                                # so a link back up cannot loop.
                                if e.is_dir():
                                    subs.append(e.name)
                            elif self._match(e.name) and e.is_file():
                                names.append(e.name)
                                # Windows: from the listing, no extra call. It
                                # can lag an append, never an add or a replace.
                                est = e.stat()
                                listed[e.name] = (est.st_mtime, est.st_size, est.st_mtime_ns)
                        except OSError:
                            continue
            except OSError:
                return
        for s in subs:
            self._walk(os.path.join(d, s), level + 1, full, now, hot, files, dirs)
        hot_after = now - HOT_S
        for name in names:
            p = os.path.join(d, name)
            had = self._files.get(p)
            if had is not None and not full and had[0] < hot_after \
                    and name.rsplit(".", 1)[0] not in hot:
                # quiet for hours: as it was, or as a new listing of its
                # folder has it (a replaced file), until the next full walk
                files[p] = listed.get(name, had)
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            files[p] = (st.st_mtime, st.st_size, st.st_mtime_ns)
        newest = max((files[p][0] for p in (os.path.join(d, n) for n in names) if p in files),
                     default=0.0)
        newest = max(newest, dm / 1e9)     # an empty folder just made is not quiet
        dirs[d] = (dm, subs, names, settled, newest)


_PATHS: dict[str, Path] = {}


def as_path(p: str) -> Path:
    """The Path of a listed file, made once."""
    hit = _PATHS.get(p)
    if hit is None:
        if len(_PATHS) > 100_000:
            _PATHS.clear()
        hit = _PATHS[p] = Path(p)
    return hit


def newest_first(table: dict[str, tuple[float, int, int]]) -> list[tuple[float, Path]]:
    """(mtime, Path) of each file, newest first: what the old glob-and-stat built."""
    out = [(v[0], as_path(p)) for p, v in table.items()]
    out.sort(key=lambda x: x[0], reverse=True)
    return out
