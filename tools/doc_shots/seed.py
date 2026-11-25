"""Populate a disposable hub with a small, entirely fictional working board."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from demo_hub import Hub


def transcript(home: Path, cwd: str, sid: str, words: str) -> None:
    folder = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"type": "assistant", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
             "sessionId": sid, "cwd": cwd, "uuid": f"demo-{time.time_ns()}",
             "message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": words}]}}
    with (folder / f"{sid}.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry) + "\n")


def agent(hub: Hub, sid: str, kind: str = "claude") -> dict:
    for _ in range(100):
        found = [a for a in hub.agents() if a["sid"] == sid and a["kind"] == kind]
        if found:
            return found[-1]
        time.sleep(.15)
    raise RuntimeError(f"demo {kind} did not launch for {sid}")


def task(hub: Hub, pid: str, title: str, spec: str, workflow: str,
         priority: str = "medium", start: bool = False, collab: bool = False) -> dict:
    lineup = ([{"agent": "claude", "role": "engineer"}, {"agent": "codex", "role": "reviewer"}]
              if collab else [{"agent": "claude", "role": "engineer"}])
    room = hub.post("/api/room/new", {"title": title, "task": spec, "projectId": pid,
                                     "agents": lineup, "workspace": "worktree", "priority": priority,
                                     "start": start})["room"]
    if workflow != room.get("workflow"):
        hub.post("/api/room/workflow", {"roomId": room["id"], "workflow": workflow})
    return room


def seed_usage(hub: Hub) -> None:
    now = int(time.time())
    usage = {"asOf": now, "rateLimits": {
        "five_hour": {"used_percentage": 42, "resets_at": now + 7200},
        "seven_day": {"used_percentage": 61, "resets_at": now + 360000}}}
    folder = hub.state / "usage"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "claude-rate-limits.json").write_text(json.dumps(usage), encoding="utf-8")


def seed(hub: Hub) -> dict:

    p = hub.post("/api/projects/new", {"name": "Recipe Box", "kind": "code"})["project"]
    pid = p["id"]
    code = Path(p["path"])
    (code / "src").mkdir(exist_ok=True)
    (code / "src" / "search.py").write_text(
        '"""Find recipes by ingredient."""\n\n'
        'RECIPES = {"Lemon pasta": ["lemon", "pasta"], "Tomato soup": ["tomato", "basil"]}\n\n'
        'def find(ingredient):\n    """Return recipe names with this ingredient."""\n'
        '    return [name for name, items in RECIPES.items() if ingredient.lower() in items]\n',
        encoding="utf-8")
    (code / "README.md").write_text("# Recipe Box\n\nA small sample app for planning dinner.\n", encoding="utf-8")
    po = hub.post("/api/room/new", {"title": "Recipe Box", "task": False, "projectId": pid,
                                    "agents": [{"agent": "claude", "role": "po"}],
                                    "workspace": "inplace"})["room"]
    hub.post("/api/projects/po", {"projectId": pid, "roomId": po["id"]})
    po_id = po["id"]
    po_seat = po["participants"][0]
    po_agent = agent(hub, po_seat["sessionId"])
    # This send is a real user point in the PO chat, visible on the Points tab.
    hub.post("/api/room/say", {"roomId": po_id,
                               "text": "Please make it easy to find recipes by ingredient."})
    transcript(hub.home, po_seat["cwd"], po_seat["sessionId"],
               "I have broken the ingredient search into a small implementation task and a review. "
               "The board shows the work as it moves through each stage.")
    hub.post("/api/room/resume", {"roomId": po_id, "text": "Also include a vegetarian filter.",
                                  "newThread": True, "threadName": "Vegetarian recipes"})
    transcript(hub.home, po_seat["cwd"], po_seat["sessionId"],
               "I will track the vegetarian filter in its own thread so the decisions stay together.")
    ask = hub.mcp(po_agent["token"], "ensemble_ask", {"questions": [{
        "question": "Which ingredient list should the first release use?",
        "options": [{"label": "Local sample", "detail": "A small list included with the app", "recommended": True},
                    {"label": "Imported file", "detail": "Let people choose a file"}]}]})
    hub.mcp(po_agent["token"], "ensemble_ask", {"questions": [{
        "question": "Should search ignore letter case?", "yesno": True}]})

    active = task(hub, pid, "Add ingredient search",
                  "# Goal\nFind recipes by ingredient.\n\n# Acceptance\n- Search ignores case.\n- Tests cover a match and no match.\n",
                  "inprogress", "highest", True, True)
    prompt = hub.post("/api/room/say", {"roomId": active["id"],
                                             "text": "Please check ingredient search against the acceptance criteria."})
    owner = agent(hub, active["participants"][0]["sessionId"])
    hub.mcp(owner["token"], "chat_send", {"to": "all", "message":
        "## Progress\n\nThe search function is in place. I am checking the tests and preparing a commit for review."})
    hub.mcp(owner["token"], "ensemble_report", {"kind": "update", "state": "Search built; review next.",
        "text": "## Search progress\n\nThe ingredient search is working in the demo. Next I will request review."})
    work = Path(active["cwd"])
    (work / "src").mkdir(exist_ok=True)
    (work / "src" / "search.py").write_text(
        '"""Find a sample recipe by ingredient."""\n\n'
        'def find(ingredient):\n    return ["Lemon pasta"] if ingredient.lower() == "lemon" else []\n',
        encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "src/search.py"], check=True, env=hub.env())
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "Add ingredient search"],
                   check=True, env=hub.env())
    head = subprocess.check_output(["git", "-C", str(work), "rev-parse", "--short", "HEAD"],
                                   text=True, env=hub.env()).strip()
    hub.mcp(owner["token"], "chat_send", {"to": "codex", "message":
        f"## Review request\n\nPlease review commit `{head}` for ingredient search. Check matching and case handling."})
    review_room = next(r for r in hub.get("/api/rooms") if r["id"] == active["id"])
    reviewer = next((p for p in review_room["participants"] if p.get("identity") == "codex"), None)
    if reviewer and reviewer.get("sessionId"):
        reviewer_agent = agent(hub, reviewer["sessionId"], "codex")
        hub.mcp(reviewer_agent["token"], "review_done", {"verdict": "approve",
            "summary": "The sample search matches the brief.",
            "findings": "## Findings\n\nThe case-insensitive match works for the sample recipe. No blocking findings."})
    task(hub, pid, "Write search tests", "Cover matches, case and missing ingredients.", "todo", "high")
    task(hub, pid, "Add saved favourites", "Save favourite recipes locally.", "backlog")
    task(hub, pid, "Review recipe labels", "Check labels and empty states.", "inreview")
    task(hub, pid, "Set up starter app", "Create the starter application and README.", "done")
    gated = task(hub, pid, "Add shopping list", "Start after ingredient search; group ingredients by recipe.",
                 "backlog", "low")
    hub.mcp(po_agent["token"], "ensemble_update_task", {"taskId": f"#{gated['no']}",
        "after": [{"task": f"#{active['no']}", "when": "merged"}], "onReady": "tell"})
    p2 = hub.post("/api/projects/new", {"name": "Garden Notes", "kind": "code"})["project"]
    task(hub, p2["id"], "Sketch planting calendar", "Make a month-by-month view.", "backlog")
    # A documents project exercises the alternate project type without personal files.
    try:
        hub.post("/api/projects/new", {"name": "Field Journal", "kind": "documents"})
    except RuntimeError:
        pass
    return {"project": pid, "po": po_id, "active": active["id"], "file": str(code / "src" / "search.py"),
            "ask": ask["messageId"], "progress": prompt["result"]["message"]["id"]}
