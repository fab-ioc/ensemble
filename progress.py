"""Small, thread-safe snapshot of hub work for the existing page polls."""

import threading
import time
import json
from pathlib import Path

_lock = threading.Lock()
_work: dict[str, dict] = {}


def set_progress(key: str, phase: str, done: int = 0, total: int = 0,
                 progress_file: str = "") -> None:
    """Publish completed units. Zero total means the phase is indeterminate."""
    total = max(0, int(total))
    done = max(0, min(int(done), total)) if total else max(0, int(done))
    with _lock:
        _work[key] = {"phase": phase, "done": done, "total": total,
                      "at": time.time(), "progress_file": progress_file}


def clear(key: str) -> None:
    with _lock:
        _work.pop(key, None)


def snapshot() -> dict[str, dict]:
    with _lock:
        work = {key: value.copy() for key, value in _work.items()}
    for value in work.values():
        path = value.pop("progress_file", "")
        if path:
            try:
                value["done"] = min(value["total"], max(0, int(json.loads(Path(path).read_text())["done"])))
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return work
