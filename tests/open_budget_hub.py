"""#247: the first answers of each open, measured on a hub just started.

Run as ``py tests/open_budget_hub.py fill|restart|cold`` with USERPROFILE and
HOME pointing at a throwaway folder (test_open_budgets does that):

- ``fill`` writes the fixture — a project whose PO chat has a 10 MB
  transcript (3000 balloons, 300 points answered in threads) and 10 MB of
  room messages, and a task chat with 10 MB of messages and a 10 MB
  transcript — opens each once, as the hub that ran before did, and saves the
  restart caches as a restart does;
- ``restart`` is the hub started again: its caches loaded, the chats read
  ahead (as ``main`` does), then each open asked the way the page asks;
- ``cold`` the same with no restart cache (a crash, a new version);
- ``restart-now`` / ``cold-now``: the same, but each open is asked the
  moment the hub listens, its read ahead still running behind it.

Prints, per open, the bytes and server time of the first answer and the best
of three warm ones, as one JSON object."""
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

PO_ROOM = "room-247b0d9e"
TASK_ROOM = "room-247c7a5c"
PO_SID = "24700000-0000-4000-8000-000000000001"
TASK_SID = "24700000-0000-4000-8000-000000000002"
REVIEW_SID = "24700000-0000-4000-8000-000000000003"
PO_POINTS = 300
PO_BALLOONS = 3000          # ~3.3 KB each: a 10 MB transcript
ROOM_MESSAGES = 4000        # ~2.5 KB each: a 10 MB chat
WORDS = ("history " * 420)
SUBJECTS = ["speed chart", "brake light", "login page", "export button", "night theme",
            "search box", "phone layout", "backup job", "report email", "price table"]


def _turn(sid: str, role: str, text: str, at: float) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    if role == "user":
        return {"type": "user", "timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


def _write(path: Path, lines: list[dict]) -> None:
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def fixture(home: Path) -> None:
    import chatroom
    import dashboard
    import points
    import threads
    proj = home / "proj"
    (proj / "Documents").mkdir(parents=True, exist_ok=True)
    tdir = home / ".claude" / "projects" / "C--proj"
    tdir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    t0 = now - 20 * 86400                   # within the topics' 30 days
    msgs = lambda rid, who: [{"id": f"m{i:06d}", "from": who[i % len(who)], "to": "all",   # noqa: E731
                              "text": f"[report] update {i} " + WORDS[:2500], "ts": t0 + i * 60}
                             for i in range(ROOM_MESSAGES)]
    po = {"id": PO_ROOM, "title": "Budget PO", "mode": "solo", "updatedAt": now,
          "participants": [{"identity": "claude", "kind": "agent", "agent": "claude",
                            "role": "Product owner", "sessionId": PO_SID}]}
    task = {"id": TASK_ROOM, "title": "Budget task", "updatedAt": now - 60,
            "participants": [{"identity": "claude", "kind": "agent", "agent": "claude", "sessionId": TASK_SID},
                             {"identity": "codex", "kind": "agent", "agent": "claude", "sessionId": REVIEW_SID}]}
    chatroom.ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    for room in (po, task):
        room["messages"] = msgs(room["id"], ["claude", "user"])
        (chatroom.ROOMS_DIR / f"{room['id']}.json").write_text(json.dumps(room), encoding="utf-8")
    dashboard.PROJECTS_FILE.write_text(json.dumps([{
        "id": "proj-247b", "name": "Budget", "path": str(proj), "isGit": False,
        "createdAt": t0, "poRoomId": PO_ROOM}]), encoding="utf-8")
    threads._ensure_since(PO_ROOM, t0 - 60)
    # The PO's transcript: the person's points, each answered (a thread), and
    # the PO's own turns between them.
    lines = [_turn(PO_SID, "assistant", "I'm the PO for Budget.", t0)]
    step = (now - 3600 - t0) / PO_BALLOONS
    every = PO_BALLOONS // PO_POINTS
    for i in range(1, PO_BALLOONS):
        at = t0 + i * step
        if i % every == 0:
            n = i // every
            subject = SUBJECTS[n % len(SUBJECTS)]
            q, ids = points.take(chatroom.get_room(PO_ROOM, public=False),
                                 f"How is the {subject} going, part {n}?", key=f"P{n}", now=at, sid=PO_SID)
            lines.append(_turn(PO_SID, "user", q, at))
        elif i % every == 1 and i > every:
            n = i // every
            lines.append(_turn(PO_SID, "assistant", f"Re P{n}: the {SUBJECTS[n % len(SUBJECTS)]} is done. "
                               + WORDS[:3000], at))
        else:
            lines.append(_turn(PO_SID, "assistant", f"working {i} " + WORDS[:3300], at))
    _write(tdir / f"{PO_SID}.jsonl", lines)
    points.sync(PO_ROOM, force=True)
    for sid in (TASK_SID, REVIEW_SID):
        _write(tdir / f"{sid}.jsonl", [_turn(sid, "user" if i % 2 else "assistant", f"turn {i} " + WORDS[:3300],
                                             t0 + i * 60) for i in range(PO_BALLOONS)])


def main() -> None:
    mode = sys.argv[1]
    now = mode.endswith("-now")
    mode = mode.removesuffix("-now")
    home = Path.home()
    if mode == "fill":
        fixture(home)
    import dashboard
    import warm_cache
    if mode == "restart":
        dashboard.load_restart_caches()
    elif mode == "cold":
        warm_cache.path(dashboard.DASHBOARD_DIR).unlink(missing_ok=True)
    server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(path: str) -> tuple[bytes, float]:
        t = time.perf_counter()
        with urlopen(Request(base + path), timeout=300) as r:
            body = r.read()
        return body, (time.perf_counter() - t) * 1000

    out: dict = {"mode": mode + ("-now" if now else "")}
    if now:
        # As main() starts it: behind the opens, not before them. The page
        # asks for a PO chat's room and its channel together.
        warm = threading.Thread(target=dashboard.warm_rooms, daemon=True)
        warm.start()
        got: dict = {}

        def ask(name: str, path: str) -> None:
            body, ms = get(path)
            d = json.loads(body)
            got[name] = {"bytes": len(body), "firstMs": round(ms, 1), "pending": bool(d.get("pending")),
                         "quick": bool(d.get("quick")),
                         "items": len(d.get("items") or d.get("messages") or [])}
        asks = [threading.Thread(target=ask, args=a) for a in (
            ("poRoom", f"/api/room?id={PO_ROOM}&n=50&lite=1"),
            ("poChannel", f"/api/room/channel?id={PO_ROOM}&n=40&lite=1"))]
        for a in asks:
            a.start()
        for a in asks:
            a.join()
        ask("taskRoom", f"/api/room?id={TASK_ROOM}&n=50&lite=1")
        ask("soloTail", f"/api/session/{TASK_SID}?full=1&tail=120&bytes=96000")
        out["now"] = got
        t = time.perf_counter()
        warm.join()
        for _ in range(600):                # a build the open itself started may still run
            d = json.loads(get(f"/api/room/channel?id={PO_ROOM}&n=40&lite=1")[0])
            if not d.get("pending") and not d.get("quick"):
                break
            time.sleep(0.1)
        out["readAheadLeftMs"] = round((time.perf_counter() - t) * 1000)
    else:
        t = time.perf_counter()
        dashboard.warm_rooms()              # as main() does once the hub is up
        out["readAheadMs"] = round((time.perf_counter() - t) * 1000)
        get("/api/themes")                  # the page's own first requests come before any open
    # Each open as the page asks for it first: a PO chat (the room, light, and
    # its channel's newest entries), a thread of it, a topic, a task chat, and
    # a one-agent chat's last balloons.
    opens = {"poRoom": f"/api/room?id={PO_ROOM}&n=50&lite=1",
             "poChannel": f"/api/room/channel?id={PO_ROOM}&n=40&lite=1",
             "taskRoom": f"/api/room?id={TASK_ROOM}&n=50&lite=1",
             "soloTail": f"/api/session/{TASK_SID}?full=1&tail=120&bytes=96000"}
    first = {}
    for name, path in list(opens.items()):
        body, ms = get(path)
        out[name] = {"bytes": len(body), "firstMs": round(ms, 1)}
        first[name] = json.loads(body)
        if name == "poChannel":
            d = json.loads(body)
            out["channelPending"] = bool(d.get("pending"))
            tid = next((t for t in sorted(d.get("threads") or {}) if t.startswith("P")), "")
            opens["poThread"] = f"/api/room/thread?id={PO_ROOM}&t={quote(tid)}&n=40"
            body, ms = get(opens["poThread"])
            out["poThread"] = {"bytes": len(body), "firstMs": round(ms, 1), "thread": tid}
            for _ in range(600):            # the topics, grouped in the background
                grouping = json.loads(get(f"/api/room/topics?id={PO_ROOM}")[0])
                if not grouping.get("pending"):
                    break
                time.sleep(0.1)
            topic = next(iter(grouping.get("topics") or []), {}).get("id", "")
            opens["poTopic"] = f"/api/room/channel?id={PO_ROOM}&n=40&lite=1&topic={quote(topic)}"
            body, ms = get(opens["poTopic"])
            out["poTopic"] = {"bytes": len(body), "firstMs": round(ms, 1), "topic": topic,
                              "items": len(json.loads(body).get("items") or [])}
    for name, path in opens.items():
        out[name]["warmMs"] = round(min(get(path)[1] for _ in range(3)), 1)
    # The polls of an open chat with nothing new: the channel's (its sig) and
    # the transcript's (the turns after those held, the last 4 again).
    polls = {"poChannelPoll": opens["poChannel"] + "&sig=" + quote(first["poChannel"].get("sig", "")),
             "soloPoll": f"/api/session/{TASK_SID}?full=1&tail=120&bytes=96000"
                         f"&after={first['soloTail'].get('total', 0) - 4}"}
    for name, path in polls.items():
        runs = [get(path) for _ in range(3)]
        out[name] = {"bytes": len(runs[-1][0]), "warmMs": round(min(ms for _, ms in runs), 1)}
    if mode == "fill":
        dashboard.save_restart_caches(why="restart")
    print(json.dumps(out), flush=True)
    server.shutdown()
    os._exit(0)                                     # the hub's daemon threads


if __name__ == "__main__":
    main()
