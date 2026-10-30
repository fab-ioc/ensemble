"""How far task sessions run past their handover limit, and what a lower limit
would save (#290).

    py tools/measure_handover.py --hours 48
    py tools/measure_handover.py --since 2026-10-15 --limit 200000

Reads every transcript in a task worktree (``~/.claude/projects/*-repo/*.jsonl``,
and Codex's rollouts for the peak table). A model call is counted once: Claude
Code logs one line per content block, each with the same usage, so counting
lines (as the first measurement did) nearly doubles the total.

The replay walks each task's owner steps in order, keeping what each step added
to the context, and hands over at a limit: the handover's steps, then a fresh
start and its re-orientation. Two costs: tokens sent, and cache-weighted (a
cached read counts 0.1, a cache write 1.25), which is closer to what an
allowance is likely to charge for a fresh start.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from calendar import timegm
from pathlib import Path

REVIEWER = re.compile(r"you are '[^']+', the (code )?reviewer", re.I)
ASK = "[handover] Your conversation"
START = 50_000              # a fresh session's first model call (measured)
ORIENT = (50_000, 54_000, 57_000, 60_000, 63_000)    # its first steps (measured)
HANDOVER_STEPS = 3          # steps to write the handover (median, measured)
TICK_STEPS = 2              # steps until the next check sees the limit crossed
READ_W, WRITE_W = 0.1, 1.25


def _ts(s) -> float | None:
    try:
        return float(timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def _text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, list):
        return " ".join(str(b.get("text") or "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text")
    return str(c or "")


def _context(u: dict) -> int:
    return sum(int(u.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def read_claude(lines) -> dict:
    """{steps: [(time, context)], lineTokens, asks: [step index], prompt}: each
    model call once, its sub-agents left out; lineTokens is the old count."""
    out = {"steps": [], "lineTokens": 0, "asks": [], "prompt": ""}
    seen, t = set(), None
    for line in lines:
        if not any(k in line for k in ('"usage"', "[handover]", '"user"')):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = _ts(d.get("timestamp")) or t
        typ = d.get("type")
        if typ == "attachment":
            a = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
            if a.get("type") == "queued_command" and ASK in str(a.get("prompt") or ""):
                out["asks"].append(len(out["steps"]))
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        if typ == "user" and not d.get("isMeta"):
            text = _text(msg)
            if not out["prompt"] and text.strip():
                out["prompt"] = text[:300]
            if ASK in text:
                out["asks"].append(len(out["steps"]))
        elif typ == "assistant" and isinstance(msg.get("usage"), dict):
            ctx = _context(msg["usage"])
            if not ctx or d.get("isApiErrorMessage"):
                continue
            out["lineTokens"] += ctx
            mid = msg.get("id") or d.get("uuid")
            if d.get("isSidechain") or mid in seen:
                continue
            seen.add(mid)
            out["steps"].append((t, ctx))
    return out


def read_codex(lines) -> list[int]:
    """The context of each model call of a Codex rollout."""
    steps, prev = [], None
    for line in lines:
        if "token_count" not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        pl = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        info = pl.get("info") or {}
        ctx = int((info.get("last_token_usage") or {}).get("input_tokens") or 0)
        total = (info.get("total_token_usage") or {}).get("total_tokens")
        if pl.get("type") == "token_count" and ctx and total != prev:
            prev = total
            steps.append(ctx)
    return steps


def pct(values, p: float) -> int:
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * p))] if v else 0


def summary(sessions: list[dict], limit: int) -> dict:
    """What the steps of ``sessions`` say about ``limit``."""
    steps = [c for s in sessions for _, c in s["steps"]]
    past = [s for s in sessions if s["steps"] and max(c for _, c in s["steps"]) > limit]
    peaks = [max(c for _, c in s["steps"]) for s in past]
    over = [c for c in steps if c > limit]
    delays, ask_ctx, cool = [], [], 0
    for s in past:
        cross = next(i for i, (_, c) in enumerate(s["steps"]) if c > limit)
        t0 = s["steps"][0][0] or 0
        cool += ((s["steps"][cross][0] or 0) - t0) < 30 * 60
        if s["asks"]:
            i = min(s["asks"][0], len(s["steps"]) - 1)
            ask_ctx.append(s["steps"][i][1])
            delays.append(((s["steps"][i][0] or 0) - (s["steps"][cross][0] or 0)) / 60)
    return {"sessions": len(sessions), "tokens": sum(steps), "lineTokens": sum(s["lineTokens"] for s in sessions),
            "above": sum(over), "excess": sum(c - limit for c in over),
            "past": len(past), "askedPast": len(ask_ctx), "crossedInCooldown": cool,
            "peak": (pct(peaks, .5), pct(peaks, .9), max(peaks, default=0)),
            "askAt": (pct(ask_ctx, .5), pct(ask_ctx, .9), max(ask_ctx, default=0)),
            "askDelayMin": (pct(delays, .5), pct(delays, .9), max(delays, default=0))}


def weighted_cost(ctx: list[int]) -> float:
    """A session's model calls at the cache's prices: each re-reads what the
    last one left and writes what was added."""
    cost, prev = 0.0, 0
    for c in ctx:
        cost += min(prev, c) * READ_W + max(0, c - prev) * WRITE_W
        prev = c
    return cost


def work_of(chain: list[dict]) -> list[int]:
    """What each step of a task's sessions added: the first session whole, then
    without what a replay charges by itself (re-orientation, writing the handover)."""
    out = []
    for n, s in enumerate(chain):
        ctx = [c for _, c in s["steps"]]
        end = min(s["asks"]) if s["asks"] else len(ctx)
        for i in range(1 if n == 0 else len(ORIENT), min(end, len(ctx))):
            out.append(max(0, ctx[i] - ctx[i - 1]))
    return out


def replay(work: list[int], limit: int, over_steps: int = TICK_STEPS + HANDOVER_STEPS) -> dict:
    """The cost of ``work`` handed over ``over_steps`` steps past ``limit``."""
    ctx, tokens, weighted, fresh, above = START, START, START * WRITE_W, 0, 0
    pending = 0
    for add in work:
        weighted += ctx * READ_W + add * WRITE_W
        ctx += add
        tokens += ctx
        if ctx <= limit:
            continue
        above += ctx
        pending += 1
        if pending < over_steps:
            continue
        pending, fresh = 0, fresh + 1
        ctx = ORIENT[-1]
        tokens += sum(ORIENT)
        weighted += START * WRITE_W + sum(o * READ_W for o in ORIENT[:-1]) \
            + (ORIENT[-1] - START) * WRITE_W
    return {"tokens": tokens, "weighted": int(weighted), "fresh": fresh, "above": above}


def load(since: float) -> tuple[list[dict], list[list[int]]]:
    root = Path.home() / ".claude" / "projects"
    sessions = []
    for f in glob.glob(str(root / "*-repo" / "*.jsonl")):
        if os.path.getmtime(f) < since:
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            s = read_claude(fh)
        s.update(sid=Path(f).stem, dir=Path(f).parent.name,
                 reviewer=bool(REVIEWER.search(s["prompt"])))
        if s["steps"]:
            sessions.append(s)
    codex = []
    for f in glob.glob(str(Path.home() / ".codex" / "sessions" / "**" / "*.jsonl"), recursive=True):
        if os.path.getmtime(f) >= since:
            with open(f, encoding="utf-8", errors="replace") as fh:
                steps = read_codex(fh)
            if steps:
                codex.append(steps)
    return sessions, codex


def replay_table(owners: list[dict], limits: list[int]) -> list[dict]:
    chains: dict[str, list[dict]] = {}
    for s in owners:
        chains.setdefault(s["dir"], []).append(s)
    works = [work_of(sorted(c, key=lambda s: s["steps"][0][0] or 0)) for c in chains.values()]
    rows = []
    for limit in limits:
        got = [replay(w, limit) for w in works]
        rows.append({"limit": limit, **{k: sum(g[k] for g in got) for k in got[0]}} if got
                    else {"limit": limit, "tokens": 0, "weighted": 0, "fresh": 0, "above": 0})
    return rows


def report(sessions: list[dict], codex: list[list[int]], limit: int, limits: list[int]) -> str:
    owners = [s for s in sessions if not s["reviewer"]]
    reviewers = [s for s in sessions if s["reviewer"]]
    out = []

    def line(name: str, s: dict) -> None:
        share = 100 * s["above"] / max(1, s["tokens"])
        out.append(f"{name}: {s['sessions']} sessions, {s['tokens'] / 1e9:.2f}B tokens, "
                   f"{s['above'] / 1e9:.2f}B ({share:.0f}%) sent above {limit // 1000}k; "
                   f"{s['past']} past it, peak median/p90/max "
                   + "/".join(f"{p // 1000}k" for p in s["peak"]))

    a, b = summary(owners, limit), summary(reviewers, limit)
    line("owners", a)
    out.append(f"  asked {a['askedPast']} of those; context at the ask median/p90/max "
               + "/".join(f"{p // 1000}k" for p in a["askAt"])
               + f"; minutes from crossing to the ask "
               + "/".join(f"{p:.1f}" for p in a["askDelayMin"])
               + f"; {a['crossedInCooldown']} crossed in their first 30 minutes")
    line("reviewers", b)
    peaks = [max(s) for s in codex]
    over = [p for p in peaks if p > limit]
    out.append(f"codex: {len(codex)} sessions, {sum(map(sum, codex)) / 1e9:.2f}B tokens, "
               f"{len(over)} past {limit // 1000}k, peak median/p90/max "
               f"{pct(over, .5) // 1000}k/{pct(over, .9) // 1000}k/{max(over, default=0) // 1000}k")
    out.append(f"Counting lines instead of model calls gives "
               f"{(a['lineTokens'] + b['lineTokens']) / 1e9:.2f}B.")
    rows = replay_table(owners, limits)
    real_w = sum(weighted_cost([c for _, c in s["steps"]]) for s in owners)
    out.append(f"replay of the owners' work (real: {a['tokens'] / 1e9:.2f}B tokens, "
               f"{real_w / 1e9:.2f}B cache-weighted, {a['sessions']} sessions):")
    out.append("  limit, tokens, cache-weighted, fresh starts, share above the limit")
    for r in rows:
        out.append(f"  {r['limit'] // 1000:>4}k  {r['tokens'] / 1e9:6.2f}B "
                   f"({100 * (r['tokens'] / max(1, a['tokens']) - 1):+.0f}%)  "
                   f"{r['weighted'] / 1e9:6.2f}B "
                   f"({100 * (r['weighted'] / max(1, real_w) - 1):+.0f}%)  "
                   f"{r['fresh']:6d}  {100 * r['above'] / max(1, r['tokens']):.1f}%")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, help="the last so many hours")
    ap.add_argument("--since", help="from this date, YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=200_000)
    a = ap.parse_args(argv)
    since = (time.time() - a.hours * 3600 if a.hours
             else time.mktime(time.strptime(a.since or "2026-10-15", "%Y-%m-%d")))
    sessions, codex = load(since)
    print(report(sessions, codex, a.limit, [200_000, 150_000, 120_000]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
