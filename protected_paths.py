"""The files a project's PO works from, which no cleanup ever deletes (#317).

A project's home holds ``PO-HANDOVER.md``, ``PO-HANDOVER-ARCHIVE.md``,
``ROADMAP.md``, ``project.json`` and its Documents folder. Every deletion path
(the Files panel's delete and move, the cleanup's prune, a task's delete) asks
:func:`refusal` before it removes a path: a path that is one of those, or a
folder that holds one, is refused with a sentence the person can read.

Names are compared without regard to case: Windows and macOS find
``roadmap.md`` when asked for ``ROADMAP.md``. Paths are compared as written and
as they resolve on disk (a short 8.3 name, a link).

No imports from the hub: dashboard.py and prune.py both hand it the
(home, Documents folder) pairs of the registered projects.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Iterable

NAMES = ("PO-HANDOVER.md", "PO-HANDOVER-ARCHIVE.md", "ROADMAP.md", "project.json")
LOWER_NAMES = frozenset(n.lower() for n in NAMES)


def _forms(p: str) -> set[str]:
    """``p`` as written and as it resolves, normalised and lower-cased."""
    out = set()
    try:
        out.add(os.path.normcase(os.path.normpath(os.path.abspath(p))).lower())
    except (OSError, ValueError):
        pass
    try:
        out.add(os.path.normcase(os.path.realpath(p)).lower())
    except (OSError, ValueError):
        pass
    return out


def _holds(folder: str, item: str) -> bool:
    """``item`` is ``folder`` or under it (both already normalised)."""
    return item == folder or item.startswith(folder.rstrip("\\/") + os.sep)


def items(home: str, documents_dir: str = "") -> list[tuple[str, str]]:
    """(name shown, path) of each protected item of one project home."""
    if not home:
        return []
    out = [(n, os.path.join(home, n)) for n in NAMES]
    if documents_dir:
        out.append((os.path.basename(os.path.normpath(documents_dir)) or documents_dir, documents_dir))
    return out


_CACHE_S = 30.0
_cache: dict = {"key": None, "at": 0.0, "forms": []}
_cache_lock = threading.Lock()


def _item_forms(places: Iterable[tuple[str, str]]) -> list[tuple[str, set[str]]]:
    """(name shown, forms) of every protected item. A cleanup asks for each
    of its parts with the same places: resolved once per run, not per part."""
    key = tuple((h or "", d or "") for h, d in places)
    now = time.monotonic()
    with _cache_lock:
        if _cache["key"] == key and now - _cache["at"] < _CACHE_S:
            return _cache["forms"]
    forms = [(name, _forms(item)) for home, docs in key for name, item in items(home, docs)]
    with _cache_lock:
        _cache.update(key=key, at=now, forms=forms)
    return forms


def refusal(path: str, places: Iterable[tuple[str, str]]) -> str:
    """Why ``path`` may not be deleted, moved away or replaced, or "" when it
    may. ``places`` are the (home, Documents folder) pairs of the registered
    projects."""
    if not path:
        return ""
    mine = _forms(path)
    for name, its in _item_forms(places):
        for it in its:
            for m in mine:
                if m == it:
                    return f"“{name}” is protected: the project's PO works from it, so it is never deleted."
                if _holds(m, it):
                    return (f"“{os.path.basename(os.path.normpath(path)) or path}” holds the project's "
                            f"“{name}”, which its PO works from, so it is never deleted.")
    return ""
