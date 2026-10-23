"""A frozen copy of the live hub's state for tools/perf_po_chat.py (#265).

    py tools/perf_snapshot.py [DEST]        (default ~/e265)

DEST becomes a home folder: DEST/.ensemble (the state, without logs, the
PTY host's files, the running-rooms record or the access token, so a hub
started on it never reaches the live terminals nor brings an agent back),
DEST/.claude/projects and DEST/.codex/sessions (the transcripts). A copy,
not links: the agents keep writing the live ones, and a replay must read
the same bytes each time. Kept short (~/e265): a transcript's path from a
task's scratch folder is near Windows' 260 characters already.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

LIVE = Path.home()
# Never copied: what would tie a copy to the live hub or its agents.
SKIP_TOP = {"logs", "pty-spool", "pty-preflight", "_launch", "_mcp", "bin", "rtk",
            "pty-host.json", "pty-host-start.json", "running-rooms.json", "restart.lease",
            "restart-drain.json", "restart-snapshot.last.json", "access-token.txt"}


def default_dest() -> Path:
    return Path.home() / "e265"


LONG = "\\\\?\\"


def _long(p: Path) -> str:
    """A path past 260 characters still copies on Windows."""
    return LONG + str(p.resolve()) if os.name == "nt" else str(p)


def _copytree(src: Path, dst: Path, skip=frozenset()) -> int:
    n = 0
    for root, dirs, files in os.walk(_long(src)):
        root = root[len(LONG):] if root.startswith(LONG) else root
        rel = Path(root).relative_to(src)
        if rel == Path("."):
            dirs[:] = [d for d in dirs if d not in skip]
            files = [f for f in files if f not in skip]
        dirs[:] = [d for d in dirs if not d.startswith("pty-")]
        out = dst / rel
        os.makedirs(_long(out), exist_ok=True)
        for f in files:
            if f.endswith(".tmp"):
                continue
            try:
                shutil.copy2(_long(Path(root) / f), _long(out / f))
                n += 1
            except OSError as e:          # a file an agent holds open just now
                print(f"skipped {Path(root) / f}: {e}", file=sys.stderr)
    return n


def snapshot(dest: Path | None = None) -> Path:
    dest = Path(dest or default_dest())
    if dest.exists():
        shutil.rmtree(_long(dest))
    t0 = time.time()
    n = _copytree(LIVE / ".ensemble", dest / ".ensemble", SKIP_TOP)
    n += _copytree(LIVE / ".claude" / "projects", dest / ".claude" / "projects")
    codex = Path(os.environ.get("CODEX_HOME") or (LIVE / ".codex")) / "sessions"
    if codex.is_dir():
        n += _copytree(codex, dest / ".codex" / "sessions")
    (dest / ".snapshot").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    print(f"snapshot: {n} files in {time.time() - t0:.0f} s at {dest}")
    return dest


if __name__ == "__main__":
    snapshot(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
