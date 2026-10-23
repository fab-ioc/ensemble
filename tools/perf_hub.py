"""A hub on a copy of the live state, for the PO chat replay (#265).

    py tools/perf_hub.py --home ~/e265 --port 8798 --out DIR [--live room-…,room-…]

Run by tools/perf_po_chat.py; never on the live home. USERPROFILE/HOME point
at the copy before dashboard is imported, so every path the hub has is the
copy's. What a hub start does beyond serving is left out: no agent is ever
started (launches stay refused), no PTY host (terminals are this process's),
no skills installed, no backup, digest, rotation, history, usage or restart
monitor, no boot restore. The PO chats named by --live get a stand-in agent
(a terminal that takes what is typed and says nothing), so a send to them
is typed in as it is to a running PO.

Every request is timed (from its request line to its answer) and written to
DIR/requests.jsonl; DIR/lag.jsonl has, per second, how late a 2 ms sleep
woke (the interpreter held by something else: what a page waits on too).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--home", required=True)
ap.add_argument("--port", type=int, default=8798)
ap.add_argument("--out", required=True)
ap.add_argument("--live", default="")
args = ap.parse_args()

home = Path(args.home).resolve()
if home == Path.home().resolve() or not (home / ".snapshot").exists():
    sys.exit(f"{home} is not a snapshot (tools/perf_snapshot.py makes one)")
os.environ.update(USERPROFILE=str(home), HOME=str(home), CODEX_HOME=str(home / ".codex"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard as D  # noqa: E402
from backends import ptyrun  # noqa: E402

assert Path(D.DASHBOARD_DIR).resolve() == (home / ".ensemble"), D.DASHBOARD_DIR
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
_wlock = threading.Lock()
_req = open(OUT / "requests.jsonl", "a", encoding="utf-8")
_tl = threading.local()
T0 = time.time()


def _note(rec: dict) -> None:
    with _wlock:
        _req.write(json.dumps(rec) + "\n")
        _req.flush()


_parse = D.Handler.parse_request


def parse_request(self):
    _tl.t = time.perf_counter()
    return _parse(self)


_one = D.Handler.handle_one_request


def handle_one_request(self):
    _tl.t = None
    try:
        _one(self)
    finally:
        t = getattr(_tl, "t", None)
        if t is not None and getattr(self, "path", ""):
            path, _, query = str(self.path).partition("?")
            _note({"at": round(time.time() - T0, 3), "m": self.command, "p": path, "q": query[:300],
                   "ms": round((time.perf_counter() - t) * 1000, 1)})


D.Handler.parse_request = parse_request
D.Handler.handle_one_request = handle_one_request
# Chrome here is a child of this session's agent: not an agent's request.
D.Handler._agent_peer = lambda self, *a, **k: ""
try:
    D.peer_process.from_agent = lambda *a, **k: ""
except AttributeError:
    pass


def _lag() -> None:
    f = open(OUT / "lag.jsonl", "a", encoding="utf-8")
    sec, worst = int(time.time()), 0.0
    while True:
        t = time.perf_counter()
        time.sleep(0.002)
        late = time.perf_counter() - t - 0.002
        now = int(time.time())
        if now != sec:
            f.write(json.dumps({"at": sec - int(T0), "ms": round(worst * 1000, 1)}) + "\n")
            f.flush()
            sec, worst = now, 0.0
        worst = max(worst, late)


def _stand_in(rid: str) -> None:
    """A running PO for the chat: a terminal that reads and discards."""
    room = D.chatroom.get_room(rid, public=False)
    if room is None:
        print(f"perf_hub: no room {rid}", flush=True)
        return
    for part in room.get("participants", []):
        if part.get("kind") != "agent":
            continue
        sess = ptyrun.create([sys.executable, "-c", "import sys\nfor _ in sys.stdin: pass"],
                             cwd=str(home), label="stand-in", meta={"room": rid, "identity": part.get("identity", "")})
        part["ptyId"] = sess.id
    D.chatroom.update_room(room)
    print(f"perf_hub: {rid} has a stand-in agent", flush=True)


# What main() starts that a replay must not.
D.attach_pty_host = lambda: ptyrun.use_local()
D.agent_launch.allow = lambda: None
D.install_agent_skills = lambda: []
D.start_boot_restore = lambda: None
D.checkpoint_scheduler = lambda: None
D._launch_file_pruner = lambda: None
D._prune_auto_loop = lambda: None
for mod, fn in ((D.backup, "start_scheduler"), (D.backup, "ensure_repo"), (D.file_history, "start_scheduler"),
                (D.usage, "start_scheduler"), (D.digest, "start_scheduler"), (D.rotation, "start_scheduler"),
                (D.restart_drain, "start_monitor")):
    setattr(mod, fn, lambda *a, **k: {})

threading.Thread(target=_lag, daemon=True).start()

_WAITS = ("wait", "sleep", "select", "accept", "recv", "readinto", "read", "_wait_for_tstate_lock", "get", "poll")


def _sample() -> None:
    """PERF_SAMPLE=1: every 10 ms, where each thread not waiting is (its
    innermost repo frames, 8 in a request), counted; DIR/samples.txt, rewritten each 30 s."""
    import collections
    import traceback
    me, counts, n, last = threading.get_ident(), collections.Counter(), 0, time.time()
    names = {}
    while True:
        time.sleep(0.01)
        n += 1
        for tid, fr in sys._current_frames().items():
            if tid == me or fr.f_code.co_name in _WAITS:
                continue
            stack = traceback.extract_stack(fr)
            mine = [f for f in stack if str(ROOT) in f.filename and "tools" not in f.filename]
            if not mine:
                continue
            if tid not in names:
                names[tid] = next((t.name for t in threading.enumerate() if t.ident == tid), str(tid))
            depth = 8 if any(f.name in ("do_GET", "do_POST") for f in mine) else 4
            top = " < ".join(f"{Path(f.filename).stem}:{f.name}" for f in reversed(mine[-depth:]))
            counts[(names[tid].split("-")[0], top)] += 1
        if time.time() - last > 30:
            last = time.time()
            with open(OUT / "samples.txt", "w", encoding="utf-8") as f:
                f.write(f"{n} samples of 10 ms\n")
                for (who, top), c in counts.most_common():
                    f.write(f"{c:6d}  {who:12s} {top}\n")


if os.environ.get("PERF_SAMPLE") == "1":
    threading.Thread(target=_sample, daemon=True).start()


def _after_boot() -> None:
    # Started (begin() ran), then ready: the first task list is read, as on
    # a hub the CEO has had open a while.
    while D.startup.is_ready():
        time.sleep(0.05)
    while not D.startup.is_ready():
        time.sleep(0.2)
    for rid in filter(None, args.live.split(",")):
        _stand_in(rid.strip())
    print("perf_hub: ready", flush=True)


threading.Thread(target=_after_boot, daemon=True).start()
sys.argv = [sys.argv[0], "--port", str(args.port)]
D.main()
