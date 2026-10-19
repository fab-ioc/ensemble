"""Measure thread reply counts on isolated copies of three PO rooms.

Reads transcript files, but writes thread records only under a temporary copy.
Run with ``py tools/measure_topic_lane.py``.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chatroom
import dashboard
import points
import threads
import topics

ROOMS = {"room-3f303104": ("opTen", ("P633", "P664", "P665")),
         "room-8d56cd21": ("Ensemble", ("P193",)),
         "room-e325b967": ("Motors", ())}


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
                                                "text": (r["turn"].get("text") or "")[:120]})
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
            for rid, (name, pids) in ROOMS.items():
                threads.forget()
                idx = threads.build(rid, show_progress=False)
                led = points.load(rid)
                for pid in pids:
                    print(json.dumps({"room": name, **measure(idx, led, pid)}), flush=True)
                if name == "opTen":
                    found = threads.search_balloons(rid, chatroom.get_room(rid), [["backtest"]])
                    print(json.dumps({"room": name, "query": "backtest", "latest": found["latest"],
                                      "poHits": [h for h in found["messages"] if h["fromLabel"] == "PO"][:5]}), flush=True)
                project = next((p for p in dashboard.load_projects() if p.get("poRoomId") == rid), None)
                if project:
                    t0 = time.perf_counter()
                    got = topics.view(project, chatroom.get_room(rid), chatroom.ROOMS_DIR)
                    cold_ms = (time.perf_counter() - t0) * 1000
                    t0 = time.perf_counter()
                    for _ in range(10):
                        topics.view(project, chatroom.get_room(rid), chatroom.ROOMS_DIR)
                    cached_ms = (time.perf_counter() - t0) * 100
                    if name == "opTen":
                        a = topics._features(idx["threads"]["P708"], idx)
                        b = topics._features(idx["threads"]["P725"], idx)
                        print(json.dumps({"pair": "P708-P725", "commonRoot": sorted(a["root"] & b["root"]),
                                          "cross": sorted((a["root"] & b["all"]) | (b["root"] & a["all"])),
                                          "both": sorted(a["all"] & b["all"])}), flush=True)
                    wanted = ("backtest", "skew", "single", "massive", "x5", "autoscout", "ad")
                    print(json.dumps({"room": name, "topicCount": len(got["topics"]),
                                      "coldMs": round(cold_ms, 2), "cachedMs": round(cached_ms, 2),
                                      "singleThreadCount": sum(t["threadCount"] == 1 for t in got["topics"]),
                                      "largest": [{"name": t["name"], "id": t["id"], "task": t["task"], "count": t["threadCount"],
                                                   "sample": t["threadOptions"][:3]}
                                                  for t in sorted(got["topics"], key=lambda t: t["threadCount"], reverse=True)[:8]],
                                      "cases": [{"thread": tid, "name": t["name"], "count": t["threadCount"],
                                                 "latest": t["latest"],
                                                 "root": idx["threads"][tid]["title"],
                                                 "terms": sorted(topics._features(idx["threads"][tid], idx)["root"]),
                                                 "all": sorted(topics._features(idx["threads"][tid], idx)["all"])}
                                                for tid in ("P708", "P725", "P683", "P145")
                                                for t in got["topics"] if tid in t["threads"]],
                                      "suggestions": list(got["suggestions"].items())[:10],
                                      "suggestionCount": len(got["suggestions"]),
                                      "topics": [{"name": t["name"],
                        "threadCount": t["threadCount"], "latest": t["latest"]}
                        for t in got["topics"] if any(w in t["name"].lower() for w in wanted)]}), flush=True)


if __name__ == "__main__":
    main()
