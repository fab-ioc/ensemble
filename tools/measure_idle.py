"""How long running tasks sat idle, and why (#292).

    py tools/measure_idle.py --days 7
    py tools/measure_idle.py --days 7 --json out.json

Walks the transcripts of every owner of a task in a project with a PO. An idle
stretch is a gap of at least ``--min`` minutes (10) between two lines of the
owner's transcript while its turn had ended, or the end of its last line up to
now (still running) or to its stop. A gap with a tool call open is a command
running in the foreground: the agent is working, and it is not a stretch.

Each stretch gets one cause, the first that holds:

    stopped       the next input is a resume: the agent was not running
    rotation      the next input is a fresh session's or a handover's
    review        a reviewer ran over at least half of it
    background    its turn ended on a background command or subagent of its
                  own, and a task notification ended the gap
    merged        its branch was merged before the gap: nothing left to do
    po_merge      its last report was completed: waiting for the PO to accept
    question      its last report was a question or blocked
    update        its last report was an update, and nothing came after it
    nothing       none of these: it stopped without saying why

A stretch that runs up to a Stop has no input to prove the agent was still
there, so it carries ``ended`` "stop" (else "input" or "now"): the report gives
the wakes with and without those.

The simulation replays the rule over the stretches: a wake at the threshold,
then on the back-off, or the first alert only when the PO acts on it; a project
gets at most one line per ``floor`` minutes, and what falls due meanwhile joins
that line. Its token cost is the PO's mean context per model call, times its
model calls per turn.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import time
from calendar import timegm
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
ENS = HOME / ".ensemble"
# What the rule never alerts on: each is covered by something else.
QUIET = {"stopped", "rotation", "review", "background", "po_merge", "question"}
BACKOFF_MIN = (10, 30, 120, 360)
FLOORS_MIN = (5, 30, 60, 120)
CHECK_MIN = 5
RESUME = ("[resumed]", "[hub restarted]")
ROTATE = ("[rotation]", "[handover]")
NOTIFY = "<task-notification"


def _ts(s) -> float | None:
    try:
        return float(timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def _text(c) -> str:
    if isinstance(c, list):
        return " ".join(str(b.get("text") or "") for b in c if isinstance(b, dict))
    return str(c or "")


def claude_events(path):
    """(time, kind, detail): kind is input | say | call | result."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("isSidechain"):
                continue
            t, typ = _ts(d.get("timestamp")), d.get("type")
            if t is None:
                continue
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            if typ == "attachment":
                a = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
                if a.get("type") == "queued_command":
                    yield t, "input", str(a.get("prompt") or "")
            elif typ == "user" and not d.get("isMeta"):
                c = msg.get("content")
                if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                               for b in c):
                    yield t, "result", ""
                else:
                    yield t, "input", _text(c)
            elif typ == "assistant":
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        yield t, "call", (b.get("name") or "", b.get("input") or {})
                    elif isinstance(b, dict) and b.get("type") == "text":
                        yield t, "say", ""


def codex_events(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = _ts(d.get("timestamp")) if isinstance(d, dict) else None
            p = d.get("payload") if isinstance(d, dict) else None
            if t is None or not isinstance(p, dict) or d.get("type") != "response_item":
                continue
            typ = p.get("type")
            if typ == "message":
                if p.get("role") == "user":
                    yield t, "input", _text(p.get("content"))
                elif p.get("role") == "assistant":
                    yield t, "say", ""
            elif typ == "function_call":
                try:
                    args = json.loads(p.get("arguments") or "{}")
                except ValueError:
                    args = {}
                yield t, "call", (p.get("name") or "", args)
            elif typ == "function_call_output":
                yield t, "result", ""


def index_transcripts() -> dict:
    out = {}
    for f in glob.glob(str(HOME / ".claude/projects/*/*.jsonl")):
        out[Path(f).stem] = ("claude", f)
    for f in glob.glob(str(HOME / ".codex/sessions/**/rollout-*.jsonl"), recursive=True):
        m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl$", f)
        if m:
            out[m.group(1)] = ("codex", f)
    return out


def owner_sessions(room: dict) -> list[str]:
    seen = []
    for p in room.get("participants") or []:
        if p.get("kind") != "agent" or "review" in str(p.get("role") or ""):
            continue
        for r in p.get("rotations") or []:
            for k in ("fromSessionId", "toSessionId"):
                if r.get(k) and r[k] not in seen:
                    seen.append(r[k])
        if p.get("sessionId") and p["sessionId"] not in seen:
            seen.append(p["sessionId"])
    return seen


def reviews_of(room: dict) -> list[tuple[float, float]]:
    out = []
    for p in room.get("participants") or []:
        for r in [*(p.get("reviews") or []), p.get("review") or {}]:
            if r.get("startedAt"):
                out.append((float(r["startedAt"]), float(r.get("endedAt") or r["startedAt"] + 600)))
    return out


def report_kind(call) -> str:
    name, args = call
    if "ensemble_report" in name and isinstance(args, dict):
        return str(args.get("kind") or "")
    return ""


def stretches(events, start: float, room: dict, end: float | None, cap_min: float) -> list[dict]:
    """The idle stretches of one session's events."""
    out, last_t, last_report, open_calls = [], None, "", 0
    revs = reviews_of(room)
    merged = float(room.get("mergedAt") or 0)

    def stretch(a, b, nxt, ended="input"):
        if b - a < cap_min * 60 or b < start:
            return
        over = sum(max(0, min(b, y) - max(a, x)) for x, y in revs)
        if nxt.startswith(RESUME):
            cause = "stopped"
        elif nxt.startswith(ROTATE):
            cause = "rotation"
        elif over >= (b - a) / 2:
            cause = "review"
        elif NOTIFY in nxt[:200]:
            cause = "background"
        elif merged and merged <= a:
            cause = "merged"
        elif last_report == "completed":
            cause = "po_merge"
        elif last_report in ("question", "blocked"):
            cause = "question"
        elif last_report == "update":
            cause = "update"
        else:
            cause = "nothing"
        out.append({"from": a, "to": b, "cause": cause, "next": nxt[:60], "ended": ended})

    for t, kind, detail in events:
        if last_t is not None and not open_calls and kind == "input":
            stretch(last_t, t, str(detail))
        if kind == "input":
            if NOTIFY not in str(detail)[:200] and not str(detail).startswith(RESUME + ROTATE):
                last_report = ""
            open_calls = 0
        elif kind == "call":
            open_calls += 1
            last_report = report_kind(detail) or last_report
        elif kind == "result":
            open_calls = max(0, open_calls - 1)
        last_t = t
    if end and last_t and not open_calls:
        stretch(last_t, end[0], "", ended=end[1])
    return out


def simulate(items: list[dict], threshold_min: float, quiet: set, days: float, start: float,
             floor_min: float = CHECK_MIN, acts: bool = False, confirmed: bool = False,
             merge_after_h: float = 0) -> dict:
    """Wakes a day per project. ``quiet`` leaves causes out; ``confirmed`` leaves
    out what ran up to a Stop; ``merge_after_h`` alerts on a completed report the
    PO left alone for that long."""
    backoff = [threshold_min, *[m for m in BACKOFF_MIN[1:] if m > threshold_min]]
    alerts = collections.defaultdict(list)
    for s in items:
        if confirmed and s["ended"] == "stop":
            continue
        first = threshold_min * 60
        if s["cause"] == "po_merge" and merge_after_h:
            first = merge_after_h * 3600
        elif s["cause"] in quiet:
            continue
        at, i = s["from"] + first, 0
        while at < s["to"]:
            if at >= start:
                alerts[s["project"]].append(at)
                if acts:
                    break
            i += 1
            at += (backoff[i] if i < len(backoff) else backoff[-1]) * 60
    out = {}
    for p, due in alerts.items():
        due.sort()
        wakes, i, free = 0, 0, 0.0
        while i < len(due):
            wake = max(due[i], free)
            wakes += 1
            free = wake + max(floor_min, CHECK_MIN) * 60
            while i < len(due) and due[i] <= wake:
                i += 1
        out[p] = wakes / days
    return out


def po_context(rooms: list[tuple], index: dict, start: float) -> dict:
    """Project id -> {"context": mean context per model call, "callsPerTurn": ...}."""
    out = {}
    for pid, po in rooms:
        total, n, turns = 0, 0, 0
        for sid in owner_sessions(po):
            hit = index.get(sid)
            if not hit or hit[0] != "claude":
                continue
            seen = set()
            with open(hit[1], encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    t = _ts(d.get("timestamp")) if isinstance(d, dict) else None
                    if not t or t < start:
                        continue
                    if d.get("type") == "attachment" and "queued_command" in line:
                        turns += 1
                    m = d.get("message")
                    if d.get("type") == "user" and not d.get("isMeta") and isinstance(m, dict) \
                            and isinstance(m.get("content"), str):
                        turns += 1
                    if not isinstance(m, dict) or not m.get("usage") or m.get("id") in seen:
                        continue
                    seen.add(m.get("id"))
                    u = m.get("usage") or {}
                    c = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                         "cache_creation_input_tokens"))
                    if c:
                        total += c
                        n += 1
        if n:
            out[pid] = {"context": total / n, "callsPerTurn": n / max(turns, 1)}
    return out


def board_now(rooms: list[dict], live: dict, names: dict) -> dict:
    """Per project, where the task cards sit now: the column against whether an
    agent runs and is working."""
    out = collections.defaultdict(collections.Counter)
    for r in rooms:
        if r.get("archived"):
            continue
        col = str(r.get("workflow") or "none")
        rec = live.get(r.get("id"))
        if not rec:
            state = "stopped"
        else:
            busy = any(a.get("state") not in ("idle", None) for a in rec.get("agents") or [])
            state = "working" if busy else "idle"
        out[names[r["projectId"]]][f"{col}/{state}"] += 1
    return {k: dict(sorted(v.items())) for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--min", type=float, default=10)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    now = time.time()
    start = now - a.days * 86400
    projects = json.loads((ENS / "projects.json").read_text(encoding="utf-8"))
    if isinstance(projects, dict):
        projects = projects.get("projects") or list(projects.values())
    names = {p["id"]: p.get("name", "") for p in projects if p.get("poRoomId")}
    pos = {p["poRoomId"]: p["id"] for p in projects if p.get("poRoomId")}
    live = {}
    try:
        running = json.loads((ENS / "running-rooms.json").read_text(encoding="utf-8"))
        live = dict(running.get("rooms") or {}) if isinstance(running, dict) else {}
    except (OSError, ValueError):
        pass
    index = index_transcripts()
    items, po_rooms, task_count, tasks = [], [], 0, []
    for f in glob.glob(str(ENS / "rooms/room-*.json")):
        try:
            room = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = room.get("projectId") or ""
        if room.get("id") in pos:
            po_rooms.append((pos[room["id"]], room))
            continue
        if pid not in names or not room.get("launched", True):
            continue
        tasks.append(room)
        if float(room.get("updatedAt") or 0) < start and not room.get("id") in live:
            continue
        task_count += 1
        sids = owner_sessions(room)
        for i, sid in enumerate(sids):
            hit = index.get(sid)
            if not hit:
                continue
            ev = list((claude_events if hit[0] == "claude" else codex_events)(hit[1]))
            ended = None
            if i == len(sids) - 1:
                stops = [float(room.get("stoppedAt") or 0)]
                stops += [float((p.get("lastExit") or {}).get("endedAt") or 0)
                          for p in room.get("participants") or []]
                ended = (now, "now") if room.get("id") in live else (max(stops), "stop")
            for s in stretches(ev, start, room, ended, a.min):
                s.update(project=pid, no=room.get("no"), live=room.get("id") in live, agent=hit[0])
                s["column"] = room.get("workflow") or ""
                s["seconds"] = s["to"] - max(s["from"], start)
                items.append(s)
    causes = sorted({s["cause"] for s in items})

    def tally(rows):
        return {c: {"n": sum(s["cause"] == c for s in rows),
                    "hours": round(sum(s["seconds"] for s in rows if s["cause"] == c) / 3600, 1),
                    "uptoStop": sum(s["cause"] == c and s["ended"] == "stop" for s in rows)}
                for c in causes if any(s["cause"] == c for s in rows)}

    def minutes(rows):
        d = sorted(s["seconds"] / 60 for s in rows)
        return {q: round(d[int(len(d) * f)]) if d else 0
                for q, f in (("p25", .25), ("p50", .5), ("p75", .75), ("p90", .9))}

    ctx = po_context(po_rooms, index, start)
    sim = {}
    for th in (10, 15, 20, 30, 60):
        for floor in FLOORS_MIN:
            for acts in (False, True):
                for confirmed in (False, True):
                    got = simulate(items, th, QUIET, a.days, start, floor, acts, confirmed, 4)
                    key = f"{th}min floor{floor} {'acts' if acts else 'ignores'} " \
                          f"{'confirmed' if confirmed else 'all'}"
                    sim[key] = {names.get(p, p): round(v, 1) for p, v in sorted(got.items())}
    ends = collections.Counter()
    for s in items:
        if s["cause"] == "nothing":
            ends[s["ended"] if s["ended"] != "input" else (s["next"][:8] or "text")] += 1
    res = {"days": a.days, "tasks": task_count, "stretches": len(items), "total": tally(items),
           "byProject": {names.get(p, p): tally([s for s in items if s["project"] == p])
                         for p in sorted({s["project"] for s in items})},
           "minutes": {c: minutes([s for s in items if s["cause"] == c]) for c in causes},
           "nothingEndedBy": dict(ends.most_common(12)),
           "byColumn": {c: tally([s for s in items if s["column"] == c])
                        for c in sorted({s["column"] for s in items})},
           "poContext": {names.get(p, p): {"context": round(v["context"]),
                                           "callsPerTurn": round(v["callsPerTurn"], 1)}
                         for p, v in ctx.items()},
           "boardNow": board_now(tasks, live, names),
           "simulated": sim}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps({**res, "items": items}, indent=1, ensure_ascii=False),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
