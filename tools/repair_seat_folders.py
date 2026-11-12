"""List the seats an older hub moved to its fallback folder (ED-344). Read only.

    python3 tools/repair_seat_folders.py [--rooms DIR]

A seat of a worktree task recorded outside the task's own folder (an older
hub stored ``~/cs`` there when the task's worktree was missing). A second
checkout inside the task folder (``repo-linux``) is the task's own and is not
listed.

Nothing is written here: a room file edited beside a running hub could lose
what the hub writes meanwhile. The hub repairs them itself, under its room
lock: every launch starts such a seat in its task's folder and records that
(seat_folders.launch_folder), and each hub start writes the task's folder back
on every stopped one (seat_folders.repair_at_start, with a backup in
~/.ensemble/backups/seat-folders/).
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import seat_folders  # noqa: E402  (damaged_seats / scan_rooms need no hub)


def _running_args() -> str:
    try:
        return subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True,
                              timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rooms", default=str(Path.home() / ".ensemble" / "rooms"))
    a = ap.parse_args()
    procs = _running_args()
    rows = seat_folders.scan_rooms(Path(a.rooms))
    for r in rows:
        # A hint only: a fresh Codex session has no id on its command line.
        live = " (its session is running)" if r["sessionId"] and r["sessionId"] in procs else ""
        print(f"{r['room']} #{r.get('no') or '?'} {r['identity']}: {r['was']} "
              f"-> {r['now']} at its next launch{live}")
    print(f"{len(rows)} seat(s) outside their task's folder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
