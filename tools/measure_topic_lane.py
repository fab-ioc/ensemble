"""Measure thread reply counts on isolated copies of three PO rooms.

Reads transcript files, but writes thread records only under a temporary copy.
Run with ``py tools/measure_topic_lane.py``.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chatroom
import dashboard
import points
import threads

ROOMS = {"room-3f303104": ("opTen", "P633"),
         "room-8d56cd21": ("Ensemble", "P193")}


def measure(idx, led, pid):
    by_mid = {r["mid"]: r for r in idx["recs"]}
    point = next(p for p in led["points"] if p["id"] == pid)
    pos = next(i for i, r in enumerate(idx["recs"]) if r["mid"] == point["mid"])
    at = idx["recs"][pos]["ts"]
    prior = [r["ts"] for r in idx["recs"][:pos]
             if r["role"] == "user" and r.get("kind") == "human" and r["ts"] < at - 7200]
    mark = max(prior)
    before, after = set(), set()
    detail = {}
    for r in idx["recs"][:pos]:
        if r["ts"] <= mark or r["role"] == "user" and r.get("kind") == "human":
            continue
        for tid in idx["of"].get(r["mid"], []):
            if tid == "team:activity" or idx["threads"][tid]["root"] == r["mid"]:
                continue
            before.add(tid)
            detail.setdefault(tid, []).append({"mid": r["mid"], "kind": r.get("kind"),
                                                "work": r["mid"] in idx["work"],
                                                "ask": r.get("ask"), "live": r.get("live"),
                                                "re": r.get("re"),
                                                "text": (r["turn"].get("text") or "")[:90]})
            if r["mid"] not in idx["work"]:
                after.add(tid)
    return {"point": pid, "awayHours": round((at - mark) / 3600, 2),
            "before": len(before), "after": len(after), "detail": detail}


def main():
    source = Path.home() / ".ensemble"
    with tempfile.TemporaryDirectory(prefix="ensemble-topics-lane-") as tmp:
        dest = Path(tmp)
        for sub in ("rooms", "points", "threads", "sends", "input-provenance"):
            (dest / sub).mkdir()
            for rid in ROOMS:
                for ext in (".json", ".jsonl"):
                    src = source / sub / (rid + ext)
                    if src.exists():
                        shutil.copy2(src, dest / sub / src.name)
        for filename in ("projects.json", "settings.json", "session_projects.json"):
            shutil.copy2(source / filename, dest / filename)
        with mock.patch.object(dashboard, "DASHBOARD_DIR", dest), \
             mock.patch.object(dashboard, "PROJECTS_FILE", dest / "projects.json"), \
             mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", dest / "session_projects.json"), \
             mock.patch.object(dashboard, "SETTINGS_FILE", dest / "settings.json"), \
             mock.patch.object(chatroom, "ROOMS_DIR", dest / "rooms"):
            for rid, (name, pid) in ROOMS.items():
                threads.forget()
                idx = threads.build(rid, show_progress=False)
                print(json.dumps({"room": name, **measure(idx, points.load(rid), pid)}), flush=True)


if __name__ == "__main__":
    main()
