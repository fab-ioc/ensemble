"""#228: the hub's hot endpoints measured against a history of a given size.

Run as ``py tests/budget_hub.py <scale>`` with USERPROFILE and HOME pointing at
a throwaway folder (test_budgets does that): it writes a fixture there — a PO
chat of 4000 x scale messages (10 MB at scale 1) with 600 x scale points, a
project, and 2000 x scale Claude transcripts — serves the hub on a spare port
in this process and prints, per endpoint, the bytes sent and the server time
of a warm call as one JSON object."""
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MESSAGES = 4000          # x scale; ~2.5 KB each: a 10 MB chat at scale 1
TRANSCRIPTS = 2000       # x scale
POINTS = 600             # x scale; the live 10 MB PO chat holds ~600
ROOM = "room-b0d9e7a1"
STATES = ["acked"] * 17 + ["followed"] * 2 + ["delivered"]


def fixture(home: Path, scale: int) -> None:
    ens = home / ".ensemble"
    (ens / "rooms").mkdir(parents=True, exist_ok=True)
    proj = home / "proj"
    (proj / "Documents").mkdir(parents=True, exist_ok=True)
    words = ("history " * 320)[:2500]
    t0 = 1_700_000_000
    msgs = [{"id": f"m{i:06d}", "from": "claude" if i % 2 else "user", "to": "all",
             "text": f"message {i} {words}", "ts": t0 + i} for i in range(MESSAGES * scale)]
    # The PO's points, as the live ledger keeps them: a question, its answer.
    points = []
    for i in range(1, POINTS * scale + 1):
        mid = f"m{(i * 6) % len(msgs):06d}"
        points.append({"id": f"P{i}", "n": i, "text": f"point {i} " + "w" * 220, "owner": "claude",
                       "state": STATES[i % len(STATES)], "createdAt": t0 + i, "stateAt": t0 + i + 1,
                       "openedAt": t0 + i, "key": f"send:{i}", "mid": mid,
                       "answers": [{"key": f"a{i}", "mid": f"a{i}", "at": t0 + i + 1, "how": "re",
                                    "said": f"Re P{i}: " + "s" * 600}],
                       "followUps": [], "reminded": 0.0})
    (ens / "points").mkdir(parents=True, exist_ok=True)
    (ens / "points" / f"{ROOM}.json").write_text(json.dumps({
        "version": 1, "roomId": ROOM, "next": len(points) + 1, "points": points, "approvals": {},
        "asks": {}, "asksSettledAt": 0.0, "lastPersonAt": 0.0, "derived": 1, "asksKeys": 2,
        "migrated": 1}), encoding="utf-8")
    room = {"id": ROOM, "title": "Budget PO", "messages": msgs, "updatedAt": t0 + len(msgs),
            "participants": [{"identity": "claude", "kind": "agent", "agent": "claude"}]}
    (ens / "rooms" / f"{ROOM}.json").write_text(json.dumps(room), encoding="utf-8")
    (ens / "projects.json").write_text(json.dumps([{
        "id": "proj-budget", "name": "Budget", "path": str(proj), "isGit": False,
        "createdAt": t0, "poRoomId": ROOM}]), encoding="utf-8")
    tdir = home / ".claude" / "projects" / "C--proj"
    tdir.mkdir(parents=True, exist_ok=True)
    for i in range(TRANSCRIPTS * scale):
        sid = f"00000000-0000-4000-8000-{i:012d}"
        line = {"type": "user", "sessionId": sid, "cwd": str(proj), "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": f"task {i}"}}
        p = tdir / f"{sid}.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        os.utime(p, (t0 + i, t0 + i))


def main() -> None:
    scale = int(sys.argv[1])
    home = Path.home()
    fixture(home, scale)
    import dashboard
    server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(path: str) -> tuple[bytes, float]:
        t = time.perf_counter()
        with urlopen(Request(base + path), timeout=120) as r:
            body = r.read()
        return body, (time.perf_counter() - t) * 1000

    def warm(path: str) -> dict:
        get(path)                                   # the first call fills the caches
        runs = [get(path) for _ in range(3)]
        return {"bytes": len(runs[-1][0]), "ms": round(min(ms for _, ms in runs), 1)}

    def again(path: str) -> dict:
        """#231: the same first read once more, asked with the ETag of the
        copy the browser keeps: a bodiless 304 while nothing changed."""
        with urlopen(Request(base + path), timeout=120) as r:
            etag = r.headers["ETag"]
            r.read()
        try:
            with urlopen(Request(base + path, headers={"If-None-Match": etag}), timeout=120) as r:
                return {"status": r.status, "bytes": len(r.read())}
        except HTTPError as e:
            return {"status": e.code, "bytes": len(e.read())}

    out: dict = {"scale": scale}
    # A list's first read (opening the app), then what a switch asks for: what
    # changed since the copy the page holds.
    for name in ("sessions", "projects"):
        out[name] = warm(f"/api/{name}?since=")
        out[name + "Again"] = again(f"/api/{name}?since=")
        rev = json.loads(get(f"/api/{name}?since=")[0])["rev"]
        out[name + "Delta"] = warm(f"/api/{name}?since={quote(str(rev))}")
    first, _ = get(f"/api/room?id={ROOM}&n=50")
    out["room"] = warm(f"/api/room?id={ROOM}&n=50")
    out["roomAgain"] = again(f"/api/room?id={ROOM}&n=50")
    room = json.loads(first)
    out["roomPointsBytes"] = len(json.dumps(room.get("points"), separators=(",", ":")))
    out["roomDelta"] = warm(f"/api/room?id={ROOM}&n=50&cursor={quote(room['revision'])}"
                            f"&statusSig={quote(room['statusSig'])}")
    oldest = room["messages"][0]["id"]
    out["roomOlder"] = warm(f"/api/room?id={ROOM}&n=100&before={quote(oldest)}")
    # /api/room reads the indexed message dicts without copying them: none
    # of the requests above may have changed one.
    import chatroom
    held = chatroom._INDEX[ROOM]["room"]["messages"]
    on_disk = json.loads((home / ".ensemble" / "rooms" / f"{ROOM}.json").read_text(encoding="utf-8"))["messages"]
    out["messagesUntouched"] = held == on_disk
    print(json.dumps(out), flush=True)
    server.shutdown()
    os._exit(0)                                     # the hub's daemon threads


if __name__ == "__main__":
    main()
