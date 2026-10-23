"""The PO chat replay and its budgets (#265): fails when the page gets slow.

    py tools/perf_po_chat.py                 (6 minutes, the Ensemble PO chat)
    py tools/perf_po_chat.py --minutes 30 --room room-3f303104 --project proj-2773f8c8

What it does, in one command:

1. a copy of the live state (tools/perf_snapshot.py; made once at ~/e265,
   ``--fresh`` makes it again), and for this run a working copy of its
   ``.ensemble`` (a send writes to it; the transcripts are linked, read only);
2. a hub on that copy on a spare port (tools/perf_hub.py: no agents, no PTY
   host, every request timed; the PO chat gets a stand-in agent);
3. headless Chrome on the board with the project's PO chat docked
   (tools/perf_po_chat.js): its polls run, and every 20 s one step of a fixed
   script: open a point chip and follow each link of its card, scroll up and
   back, send a message, open a thread; the page is reloaded once halfway;
4. the numbers against the budgets below. Exit 1 when one is over, with the
   table of what was measured (and DIR/summary.json, DIR/summary.md).

The budgets (BUDGETS): a page's poll endpoint (asked at least once a
minute) p95 under 50 ms; any request under 1 s; a send shown at once and
held by the hub under 300 ms; no endpoint asked for one key more than once
per 2 s poll on average over a minute (a storm), and no endpoint over 120
requests a minute in all; every point link lands on its message or card,
never on an empty thread or nowhere. Port 8799 is never used (the PO's
merge proof); 8798 is the default.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tests import chrome_profile  # noqa: E402
import perf_snapshot  # noqa: E402

BUDGETS = {
    "poll_p95_ms": 50,          # an endpoint the page asks at least once a minute
    "max_ms": 1000,             # any request the page makes
    "send_shown_ms": 100,       # the balloon on screen after Send
    "send_sent_ms": 300,        # … and held by the hub
    "per_key_per_min": 36,      # one endpoint, one query: once per 2 s poll (and its jitter)
    "per_endpoint_per_min": 120,
    "lag_ms": 500,              # the interpreter held this long by one thing
}
# Long by nature: a terminal's stream, an agent's tool call, a long poll.
NOT_TIMED = {"/api/pty/stream", "/mcp", "/api/events"}


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _run_home(snap: Path, out: Path) -> Path:
    """This run's home: the state copied (a run writes it), the transcripts
    linked (read only, 4 GB)."""
    home = out / "home"
    shutil.copytree(snap / ".ensemble", home / ".ensemble")
    for sub in (Path(".claude") / "projects", Path(".codex") / "sessions"):
        src = snap / sub
        if not src.is_dir():
            continue
        (home / sub).parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(src), str(home / sub))
        else:
            os.symlink(src, home / sub)
    (home / ".snapshot").write_text((snap / ".snapshot").read_text(encoding="utf-8"), encoding="utf-8")
    return home


def _unlink_home(home: Path) -> None:
    for sub in (Path(".claude") / "projects", Path(".codex") / "sessions"):
        p = home / sub
        if p.is_symlink() or (os.name == "nt" and p.exists() and os.path.isjunction(p)):
            os.unlink(p) if os.name != "nt" else os.rmdir(p)
    shutil.rmtree(home, ignore_errors=True)


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def analyse(out: Path, minutes: float, budgets=BUDGETS) -> dict:
    hub = [r for r in _jsonl(out / "requests.jsonl") if r["p"] not in NOT_TIMED]
    page = [r for r in _jsonl(out / "page.jsonl") if r["p"] not in NOT_TIMED]
    steps = _jsonl(out / "steps.jsonl")
    lag = _jsonl(out / "lag.jsonl")
    span = max(1 / 60, minutes)
    by = defaultdict(list)
    for r in hub:
        by[(r["m"], r["p"])].append(r)
    rows = []
    for (m, p), rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ms = [r["ms"] for r in rs]
        keys = Counter(r["q"] for r in rs)
        # The busiest minute, in all and for one key.
        per_min = Counter(int(r["at"] // 60) for r in rs)
        key_min = Counter((int(r["at"] // 60), r["q"]) for r in rs)
        rows.append({"endpoint": f"{m} {p}", "n": len(rs), "perMin": round(len(rs) / span, 1),
                     "keys": len(keys), "p50": round(statistics.median(ms), 1), "p95": round(pct(ms, .95), 1),
                     "max": round(max(ms), 1), "peakMin": max(per_min.values()), "peakKeyMin": max(key_min.values()),
                     "slow": sum(1 for x in ms if x >= 1000)})
    page_ms = defaultdict(list)
    for r in page:
        if r.get("ms") is not None:
            page_ms[(r["m"], r["p"])].append(r["ms"])
    for row in rows:
        m, p = row["endpoint"].split(" ", 1)
        xs = page_ms.get((m, p), [])
        row["pageP95"] = round(pct(xs, .95), 1) if xs else None
        row["pageMax"] = round(max(xs), 1) if xs else None
    sends = [s for s in steps if s.get("do") == "send"]
    links = [s for s in steps if s.get("do") == "point-link"]
    threads = [s for s in steps if s.get("do") == "thread"]
    fails = []
    for row in rows:
        if row["perMin"] >= 1 and row["p95"] > budgets["poll_p95_ms"]:
            fails.append(f"{row['endpoint']}: p95 {row['p95']} ms > {budgets['poll_p95_ms']} ms")
        if row["max"] > budgets["max_ms"]:
            fails.append(f"{row['endpoint']}: max {row['max']} ms > {budgets['max_ms']} ms")
        if row["peakKeyMin"] > budgets["per_key_per_min"]:
            fails.append(f"{row['endpoint']}: one key asked {row['peakKeyMin']} times in a minute > {budgets['per_key_per_min']}")
        if row["peakMin"] > budgets["per_endpoint_per_min"]:
            fails.append(f"{row['endpoint']}: {row['peakMin']} requests in a minute > {budgets['per_endpoint_per_min']}")
    for s in sends:
        if s.get("shownMs") is None or s["shownMs"] > budgets["send_shown_ms"]:
            fails.append(f"send at {s['at']} s: shown after {s.get('shownMs')} ms > {budgets['send_shown_ms']} ms")
        if s.get("sentMs") is None or s["sentMs"] > budgets["send_sent_ms"]:
            fails.append(f"send at {s['at']} s: held by the hub after {s.get('sentMs')} ms > {budgets['send_sent_ms']} ms ({s.get('state')})")
    for s in links:
        if s.get("empty") or not s.get("landed"):
            fails.append(f"{s.get('ref')} '{s.get('link')}' at {s['at']} s: "
                         + ("an empty thread" if s.get("empty") else f"did not land ({s.get('note') or 'nothing'})"))
    for s in threads:
        if s.get("empty"):
            fails.append(f"thread at {s['at']} s opened empty")
    for s in steps:
        if s.get("error"):
            fails.append(f"step {s.get('do')} at {s['at']} s failed: {s['error'][:160]}")
        if s.get("do") == "open" and not s.get("ok"):
            fails.append(f"the page did not open its chat at {s['at']} s")
    worst_lag = max((x["ms"] for x in lag), default=0)
    if worst_lag > budgets["lag_ms"]:
        fails.append(f"the interpreter was held {worst_lag} ms at once > {budgets['lag_ms']} ms")
    summary = {"minutes": minutes, "requests": len(hub), "pageRequests": len(page), "endpoints": rows,
               "sends": sends, "links": links, "threads": threads,
               "opens": [s for s in steps if s.get("do") == "open"],
               "lag": {"max": worst_lag, "p99": pct([x["ms"] for x in lag], .99), "over100": sum(1 for x in lag if x["ms"] > 100)},
               "budgets": budgets, "fails": fails}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "summary.md").write_text(markdown(summary), encoding="utf-8")
    return summary


def markdown(s: dict) -> str:
    L = [f"Replay of {s['minutes']} min: {s['requests']} requests answered, {s['pageRequests']} made by the page.", "",
         "| Endpoint | n | /min | keys | p50 ms | p95 ms | max ms | page p95 | page max | peak/min | one key peak/min |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in s["endpoints"]:
        L.append(f"| `{r['endpoint']}` | {r['n']} | {r['perMin']} | {r['keys']} | {r['p50']} | {r['p95']} | {r['max']} | "
                 f"{r['pageP95'] if r['pageP95'] is not None else ''} | {r['pageMax'] if r['pageMax'] is not None else ''} | {r['peakMin']} | {r['peakKeyMin']} |")
    L += ["", "Sends (ms from Send to the balloon shown / held by the hub): "
          + ", ".join(f"{x.get('shownMs')}/{x.get('sentMs')}" for x in s["sends"]),
          "", f"Point links: {len(s['links'])}, landed {sum(1 for x in s['links'] if x.get('landed'))}, "
          f"empty threads {sum(1 for x in s['links'] if x.get('empty'))}",
          f"Opens (s to the chat drawn): " + ", ".join(str(x.get('s')) for x in s["opens"]),
          f"Interpreter held at once: max {s['lag']['max']} ms, p99 {s['lag']['p99']} ms, {s['lag']['over100']} s over 100 ms",
          "", "Over budget:" if s["fails"] else "Within every budget."]
    L += [f"- {f}" for f in s["fails"][:80]]
    if len(s["fails"]) > 80:
        L.append(f"- … and {len(s['fails']) - 80} more")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default=str(perf_snapshot.default_dest()))
    ap.add_argument("--fresh", action="store_true", help="copy the live state again first")
    ap.add_argument("--minutes", type=float, default=6)
    ap.add_argument("--project", default="proj-0db97f64")
    ap.add_argument("--room", default="room-8d56cd21")
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    ap.add_argument("--keep", action="store_true", help="keep the run's state copy")
    a = ap.parse_args()
    if a.port == 8799:
        sys.exit("8799 is the PO's merge-proof port")
    snap = Path(a.snapshot)
    if a.fresh or not (snap / ".snapshot").exists():
        perf_snapshot.snapshot(snap)
    out = Path(a.out or (Path.home() / "e265run" / time.strftime("%Y%m%d-%H%M%S")))
    out.mkdir(parents=True, exist_ok=True)
    if not _free(a.port):
        sys.exit(f"port {a.port} is taken")
    home = _run_home(snap, out)
    log = open(out / "hub.log", "w", encoding="utf-8")
    hub = subprocess.Popen([sys.executable, str(ROOT / "tools" / "perf_hub.py"), "--home", str(home),
                            "--port", str(a.port), "--out", str(out), "--live", a.room],
                           stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    try:
        t0, base = time.time(), f"http://127.0.0.1:{a.port}"
        while time.time() - t0 < 180:
            if hub.poll() is not None:
                sys.exit(f"the hub stopped: see {out / 'hub.log'}")
            if "perf_hub: ready" in (out / "hub.log").read_text(encoding="utf-8", errors="replace"):
                cfg = json.loads(urllib.request.urlopen(base + "/api/config", timeout=30).read())
                if Path(cfg.get("stateDir", "")).resolve() != (home / ".ensemble").resolve():
                    sys.exit(f"the hub on {a.port} is not this run's: {cfg.get('stateDir')}")
                break
            time.sleep(0.5)
        else:
            sys.exit("the hub did not come up in 3 minutes")
        print(f"hub up in {time.time() - t0:.0f} s; replaying {a.minutes} min of {a.room}", flush=True)
        args = {**chrome_profile.node_args(), "tmp": str(out), "base": base, "out": str(out),
                "project": a.project, "room": a.room, "minutes": a.minutes, "seed": a.seed}
        js = chrome_profile.JS + (ROOT / "tools" / "perf_po_chat.js").read_text(encoding="utf-8")
        (out / "driver.js").write_text(js, encoding="utf-8")
        node = subprocess.run(["node", str(out / "driver.js"), json.dumps(args)], cwd=str(out),
                              timeout=a.minutes * 60 + 600, stdout=open(out / "driver.log", "w", encoding="utf-8"),
                              stderr=subprocess.STDOUT)
        if node.returncode:
            print(f"the browser script failed: see {out / 'driver.log'}", flush=True)
    finally:
        # The hub and the stand-in agents' terminals under it.
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(hub.pid), "/T", "/F"], capture_output=True)
        else:
            hub.terminate()
        try:
            hub.wait(20)
        except subprocess.TimeoutExpired:
            hub.kill()
        log.close()
        if not a.keep:
            _unlink_home(home)
    s = analyse(out, a.minutes)
    print(markdown(s))
    print(f"(details in {out})")
    return 1 if s["fails"] or node.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
