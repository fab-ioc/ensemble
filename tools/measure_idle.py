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

The simulation replays the rule over the stretches: a wake at the threshold,
then on the back-off, one line per project per check, leaving out what the
rule leaves out. Its token cost is the PO's mean context per model call.
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


def background(call) -> bool:
    name, args = call
    return bool(isinstance(args, dict) and args.get("run_in_background")) or name in ("Agent", "Task")


def stretches(events, start: float, room: dict, end: float | None, cap_min: float) -> list[dict]:
    """The idle stretches of one session's events."""
    out, last_t, last_report, bg, open_calls = [], None, "", False, 0
    revs = reviews_of(room)
    merged = float(room.get("mergedAt") or 0)

    def stretch(a, b, nxt, stopped=False):
        if b - a < cap_min * 60 or b < start:
            return
        over = sum(max(0, min(b, y) - max(a, x)) for x, y in revs)
        if stopped or nxt.startswith(RESUME):
            cause = "stopped"
        elif nxt.startswith(ROTATE):
            cause = "rotation"
        elif over >= (b - a) / 2:
            cause = "review"
        elif NOTIFY in nxt[:200] and bg:
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
        out.append({"from": a, "to": b, "cause": cause, "next": nxt[:60]})

    for t, kind, detail in events:
        if last_t is not None and not open_calls and kind == "input":
            stretch(last_t, t, str(detail))
        if kind == "input":
            if not str(detail).startswith(("[digest", "[quiet")):
                pass
            if NOTIFY not in str(detail)[:200]:
                bg = False
                if not str(detail).startswith(RESUME + ROTATE):
                    last_report = ""
            open_calls = 0
        elif kind == "call":
            open_calls += 1
            last_report = report_kind(detail) or last_report
            bg = bg or background(detail)
        elif kind == "result":
            open_calls = max(0, open_calls - 1)
        last_t = t
    if end and last_t and not open_calls:
        stretch(last_t, end, "", stopped=False)
    return out


def simulate(items: list[dict], threshold_min: float, quiet: set, day_s: float) -> dict:
    """Replay the rule: per project, wakes coalesced per 5-minute check."""
    backoff = [threshold_min, *[m for m in BACKOFF_MIN[1:] if m > threshold_min]]
    alerts = collections.defaultdict(set)
    for s in items:
        if s["cause"] in quiet:
            continue
        at, i = s["from"] + threshold_min * 60, 0
        while at < s["to"]:
            alerts[s["project"]].add(int(at // 300))
            i += 1
            step = backoff[i] if i < len(backoff) else backoff[-1]
            at += step * 60
    return {p: len(v) / day_s for p, v in alerts.items()}


def po_context(rooms: list[dict], index: dict, start: float) -> dict:
    """Project id -> (mean context per PO model call, calls a day)."""
    out = {}
    for pid, po in rooms:
        total, n = 0, 0
        for sid in owner_sessions(po):
            hit = index.get(sid)
            if not hit or hit[0] != "claude":
                continue
            seen = set()
            with open(hit[1], encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    m = d.get("message") if isinstance(d, dict) else None
                    t = _ts(d.get("timestamp")) if isinstance(d, dict) else None
                    if not isinstance(m, dict) or not t or t < start or m.get("id") in seen:
                        continue
                    seen.add(m.get("id"))
                    u = m.get("usage") or {}
                    c = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                         "cache_creation_input_tokens"))
                    if c:
                        total += c
                        n += 1
        if n:
            out[pid] = total / n
    return out


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
    pos = {p["poRoomId"] for p in projects if p.get("poRoomId")}
    live = set()
    try:
        running = json.loads((ENS / "running-rooms.json").read_text(encoding="utf-8"))
        live = set(running.get("rooms") or {}) if isinstance(running, dict) else set()
    except (OSError, ValueError):
        pass
    index = index_transcripts()
    items, po_rooms, task_count = [], [], 0
    for f in glob.glob(str(ENS / "rooms/room-*.json")):
        try:
            room = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = room.get("projectId") or ""
        if room.get("id") in pos:
            po_rooms.append((pid, room))
            continue
        if pid not in names or not room.get("launched", True):
            continue
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
                ended = now if room.get("id") in live else max(stops) or None
            for s in stretches(ev, start, room, ended, a.min):
                s.update(project=pid, no=room.get("no"), live=room.get("id") in live, agent=hit[0])
                s["from"] = max(s["from"], start)
                items.append(s)
    causes = sorted({s["cause"] for s in items})
    table = {}
    for p in sorted({s["project"] for s in items}):
        rows = [s for s in items if s["project"] == p]
        table[names.get(p, p)] = {
            c: {"n": sum(s["cause"] == c for s in rows),
                "hours": round(sum(s["to"] - s["from"] for s in rows if s["cause"] == c) / 3600, 1)}
            for c in causes}
    day_s = a.days
    ctx = po_context(po_rooms, index, start)
    sim = {}
    for th in (10, 15, 20, 30, 60):
        got = simulate(items, th, QUIET, day_s)
        sim[th] = {names.get(p, p): {"wakesPerDay": round(v, 1),
                                     "tokensPerDay": round(v * ctx.get(p, 0))}
                   for p, v in got.items()}
    dur = sorted((s["to"] - s["from"]) / 60 for s in items if s["cause"] in ("nothing", "update", "merged"))
    res = {"days": a.days, "tasks": task_count, "stretches": len(items), "causes": table,
           "totals": {c: {"n": sum(s["cause"] == c for s in items),
                          "hours": round(sum(s["to"] - s["from"] for s in items if s["cause"] == c) / 3600, 1)}
                      for c in causes},
           "minutesOfUnexplained": {"p25": dur[len(dur) // 4] if dur else 0,
                                    "p50": dur[len(dur) // 2] if dur else 0,
                                    "p75": dur[len(dur) * 3 // 4] if dur else 0},
           "poContext": {names.get(p, p): round(v) for p, v in ctx.items()},
           "simulated": sim}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps({**res, "items": items}, indent=1, ensure_ascii=False),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
