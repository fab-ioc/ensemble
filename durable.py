"""Writes that a power cut cannot tear (ED-227).

A temp file renamed over the target is atomic for a crash of the process, not
for a power cut: the rename is journaled at once, the data only when the disk
gets to it, so the target can come back empty or full of zeros. Each write
here reaches the disk (fsync) before the rename, and the files that hold
expensive caches keep the copy they replace as ``<name>.prev``, which a read
falls back to when the newest one does not parse.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


def prev_of(path: Path) -> Path:
    return path.with_name(path.name + ".prev")


def write_tmp(tmp: Path, text: str) -> None:
    """``tmp.write_text(text)``, on the disk before it returns: for callers
    that rename the temp file themselves."""
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def write_text(path: Path, text: str, *, keep_prev: bool = False, tries: int = 8) -> None:
    """Replace ``path`` with ``text``: on the disk, then renamed into place.
    ``keep_prev`` keeps the file it replaces as ``<name>.prev``. A rename
    Windows refuses for a moment (a scanner or the indexer holding the file)
    is tried again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Per thread too: two saves of one file at once must not share a temp file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    write_tmp(tmp, text)
    for attempt in range(tries):
        try:
            if keep_prev and path.exists():
                os.replace(path, prev_of(path))
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05 * (attempt + 1))


def read_json(path: Path):
    """What ``path`` holds, or what its ``.prev`` holds when the newest copy
    is missing or does not parse (cut off mid-write by a power cut). None
    when neither reads."""
    for p in (path, prev_of(path)):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None
