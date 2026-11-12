"""Put back the seats an older hub moved to its fallback folder (ED-344).

    py tools/repair_seat_folders.py [--dry-run] [--rooms DIR] [--backups DIR]

A seat of a worktree task whose folder is outside the task's own folder (an
older hub recorded ``~/cs`` there when the task's worktree was missing) gets
the task's folder again. A seat in another folder of the task (a second
checkout such as ``repo-linux``) is left, and so is a seat whose session is
running now (its process runs where it runs; the hub repairs it at its next
start once it has stopped). Each room changed is backed up first.

Writes the room files only; never calls the hub. The hub does the same at its
start (seat_folders.repair_at_start), so this is for a hub that is running.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import seat_folders  # noqa: E402  (damaged_seats / repair_rooms need no hub)

HOME = Path.home()


def _running_args() -> str:
    try:
        return subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True,
                              timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    ap.add_argument("--rooms", default=str(HOME / ".ensemble" / "rooms"))
    ap.add_argument("--backups", default=str(HOME / ".ensemble" / "backups" / "seat-folders"))
    a = ap.parse_args()

    procs = _running_args()

    def is_live(room, part):
        sid = (part.get("sessionId") or "").strip()
        return bool(sid) and sid in procs

    rooms, backups = Path(a.rooms), Path(a.backups)
    if a.dry_run:
        rows = []
        import json
        for f in sorted(rooms.glob("room-*.json")):
            try:
                room = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for p in seat_folders.damaged_seats(room):
                row = {"room": room.get("id", f.stem), "no": room.get("no"),
                       "identity": p.get("identity", ""), "was": p.get("cwd", ""),
                       "now": room["cwd"]}
                if is_live(room, p):
                    row["skipped"] = "running now"
                rows.append(row)
    else:
        rows = seat_folders.repair_rooms(rooms, backups, is_live)
    for r in rows:
        what = f"left ({r['skipped']})" if r.get("skipped") else f"-> {r['now']}"
        print(f"{r['room']} #{r.get('no') or '?'} {r['identity']}: {r['was']} {what}")
    fixed = sum(1 for r in rows if not r.get("skipped"))
    verb = "would be repaired" if a.dry_run else "repaired"
    print(f"{fixed} seat(s) {verb}, {len(rows) - fixed} left"
          + ("" if a.dry_run or not fixed else f"; backups in {backups}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
