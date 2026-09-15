"""The PO's progress digest: what changed across a project's tasks, on a timer.

A project's PO keeps an eye on progress without watching every task. Every few
minutes (``digestIntervalMin``: a global default in settings, overridable per
project in its ``project.json``; 0 turns it off) the hub gathers the facts
about the project's tasks and compares them with what the PO was last told:

* **Nothing changed** → nothing is sent and nobody is woken. The PO has the
  longest history of any session, so each wake is the most expensive of all —
  the check itself costs no model call and is only logged.
* **Something changed** → a short digest lands in the PO's room and rings the
  PO once. A cheap model (``claude -p --model haiku`` on the plan) writes the
  facts up; if that call fails, the plain facts go instead.

The facts come from the hub alone: each task's status, board column, attention
state and reason, how long since it last did something, the commits on its
branch, and what finished since the last digest. "Changed" is judged on the
stable ones only — status, column, attention state, reports, the branch head —
never on idle time, which moves every second.

The baseline (what the PO was last told) is kept in ``DASHBOARD_DIR/digests.json``
so a hub restart neither re-sends the same news nor forgets news it has not
delivered. It advances only when a digest is delivered (or there was nothing to
say): while the PO is not running, changes accumulate into the next digest.

The first check of a project ever records the baseline without sending — the
PO already knows the state it started from.

Bound to the dashboard module like ``attention``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

# Windows: the model call must not open a console window (a console-less host,
# pythonw.exe, would otherwise give it one and lose the keyboard focus to it).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


DEFAULT_INTERVAL_MIN = 5
MAX_INTERVAL_MIN = 1440
DEFAULT_MODEL = "haiku"
MODEL_TIMEOUT_S = 90
TICK_S = 15                 # how often the scheduler looks at the clock
SENDER = "ensemble"         # who a digest is from in the PO's room
_WAKE_MAX = 900             # the doorbell carries the digest on one line

# The fields whose change is news. Idle time and attention *reasons* (which
# carry durations) are facts, not triggers.
_WATCHED = ("status", "column", "attention", "report", "head", "title", "merged")

_LOCK = threading.Lock()    # one check at a time: scheduler vs "check now"
_STATE: dict[str, dict] = {}   # projectId -> {lastCheck, nextCheck, lastResult, ...}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def clamp_interval(v) -> int | None:
    """An interval in whole minutes, 0 (off) … MAX; None when not a number."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return max(0, min(MAX_INTERVAL_MIN, v))


def interval_min(project: dict) -> int:
    """The project's own interval if it set one, else the hub default."""
    own = clamp_interval(project.get("digestIntervalMin"))
    if own is not None:
        return own
    dflt = clamp_interval(_d.load_settings().get("digestIntervalMin", DEFAULT_INTERVAL_MIN))
    return DEFAULT_INTERVAL_MIN if dflt is None else dflt


def _model() -> str:
    m = _d.load_settings().get("digestModel", DEFAULT_MODEL)
    return m.strip() if isinstance(m, str) and m.strip() else DEFAULT_MODEL


def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "digests.json"


def _load_baselines() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_baseline(pid: str, entry: dict) -> None:
    all_ = _load_baselines()
    all_[pid] = entry
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(all_, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save the digest baseline: {e}")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] digest: {msg}", flush=True)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _idle_seconds(room: dict, now: float) -> float:
    """Seconds since the task last did something: the quietest-most-recent of
    its live terminals, or — stopped — since its record last changed."""
    best = None
    for p in room.get("participants", []):
        if p.get("kind") != "agent" or not p.get("ptyId"):
            continue
        sess = _d.ptyrun.get(p["ptyId"])
        if sess and sess.alive():
            try:
                idle = float(sess.info().get("idleSeconds") or 0)
            except Exception:
                continue
            best = idle if best is None else min(best, idle)
    if best is not None:
        return best
    return max(0.0, now - float(room.get("updatedAt") or now))


# Branch reflog entries that record a commit made on the branch itself, as
# opposed to it being created, fast-forwarded, reset or rebased onto main.
_OWN_COMMIT = ("commit", "cherry-pick", "revert", "am:")


def _has_own_commits(cwd: str, branch: str) -> bool:
    """Whether the branch has ever had commits of its own.

    Nothing ahead of main reads the same for a task that has not committed yet
    and for one whose work was merged, so ahead-counting cannot tell them apart.
    The branch's reflog can: it records every commit made on the branch. A
    branch with no reflog (or one git has expired) counts as having none —
    better a merge not noticed than a new task read as finished."""
    log = _d._git_out(cwd, "reflog", "show", "--format=%gs", "refs/heads/" + branch)
    return any(s.startswith(_OWN_COMMIT) for s in log.splitlines())


def _git_facts(room: dict) -> dict:
    """The task's branch and what is on it, when it works in a repo of its own
    branch. A task on the main line has no commits of its own to count.

    ``merged`` is true when the branch has commits of its own and all of them
    are on the base (nothing ahead of it); ``commits`` then reads 0."""
    cwd = (room.get("cwd") or "").strip()
    if not cwd or not (Path(cwd) / ".git").exists():
        return {}
    b = _d.git_branch_base(cwd)
    if not b.get("base"):
        return {}
    out = {"branch": b.get("branch", ""), "base": b["base"], "commits": b.get("ahead", 0),
           "merged": False, "sha": _d._git_out(cwd, "rev-parse", "HEAD")}
    out["head"] = out["sha"][:7]
    if not out["commits"] and out["branch"] not in ("", "HEAD"):
        out["merged"] = _has_own_commits(cwd, out["branch"])
    if out["commits"] or out["merged"]:
        out["lastCommit"] = _d._git_out(cwd, "log", "-1", "--format=%s (%cr)")
    return out


def _merged_at(cwd: str, base: str, sha: str) -> float:
    """When ``base`` first held ``sha``, from the base's reflog; 0 when it
    cannot tell (no reflog, or the merge is older than what it keeps)."""
    later = set(_d._git_out(cwd, "rev-list", "--ancestry-path", f"{sha}..{base}").split())
    later.add(sha)
    at = 0.0
    # Newest first: walk back while the base still held the work.
    for line in _d._git_out(cwd, "reflog", "show", "--date=unix", "--format=%H %gd",
                            "refs/heads/" + base).splitlines():
        h, _, sel = line.partition(" ")
        if h not in later:
            break
        try:
            at = float(sel.rsplit("@{", 1)[1].rstrip("}"))
        except (IndexError, ValueError):
            break
    return at


def _settle_merges(tasks: list[dict]) -> list[str]:
    """Move each task whose work was merged into its base to Done — once per
    merge, and only when nobody moved the card after the merge: the owner's
    drag always wins, so a card dragged back out of Done stays out. This acts
    on the PO's merge, a fact in git, not on a task going quiet. Updates the
    facts in place and returns the ids it moved."""
    moved = []
    for t in tasks:
        if not t.get("merged") or not t.get("sha"):
            continue
        room = _d.chatroom.get_room(t["id"], public=False)
        if room is None or room.get("mergedHead") == t["sha"]:
            continue                        # this merge was already dealt with
        now = time.time()
        at = _merged_at(room.get("cwd", ""), t["base"], t["sha"]) or now
        fields = {"mergedHead": t["sha"], "mergedAt": at}
        move = t["column"] != "done" and float(room.get("workflowAt") or 0) <= at
        if move:
            fields.update(workflow="done", workflowAt=now)
        if _d.chatroom.patch_room(t["id"], **fields) is None:
            continue
        if move:
            _d._patch_task_json(room.get("taskDir", ""), workflow="done")
            _log(f"{t['title']} ({t['id']}): merged into {t['base']} — moved "
                 f"{t['column']} → done")
            t["column"] = "done"
            moved.append(t["id"])
    return moved


def _task_facts(room: dict, attn: dict, labels: dict, now: float) -> dict:
    et = _d.ensemble_tools
    item = attn.get(room["id"]) or {}
    rep = room.get("lastReport") if isinstance(room.get("lastReport"), dict) else {}
    g = _git_facts(room)
    return {
        "id": room["id"],
        # How the digest names it: its number, else its id.
        "label": _d.task_label(room) or room["id"],
        "title": et._title(room, labels),
        "status": et._status(room),
        "column": _d.workflow_of(room),
        "attention": item.get("state", ""),
        "attentionReason": item.get("reason", ""),
        "idleSeconds": round(_idle_seconds(room, now)),
        "branch": g.get("branch", ""),
        "base": g.get("base", ""),
        "commits": g.get("commits", 0),
        "merged": g.get("merged", False),
        "sha": g.get("sha", ""),
        "lastCommit": g.get("lastCommit", ""),
        "head": g.get("head", ""),
        # A report is identified by when it was made; its kind and gist are facts.
        "report": float(rep.get("ts") or 0),
        "reportKind": rep.get("kind", ""),
        "reportText": " ".join((rep.get("text") or "").split())[:300],
    }


def gather(project: dict) -> list[dict]:
    """Facts for every launched task of the project, except the PO's own."""
    now = time.time()
    links = _d.load_session_projects()
    labels = _d.load_labels()
    try:
        attn = _d.attention.by_room()
    except Exception:
        attn = {}
    po_room = (project.get("poRoomId") or "").strip()
    out = []
    for room in _d.chatroom.list_rooms():
        if room["id"] == po_room or not room.get("launched", True):
            continue
        if _d.ensemble_tools._project_of_room(room, links) != project["id"]:
            continue
        out.append(_task_facts(room, attn, labels, now))
    out.sort(key=lambda t: t["id"])
    return out


def _stable(t: dict) -> dict:
    return {k: t.get(k) for k in _WATCHED}


def diff(before: dict, tasks: list[dict]) -> list[dict]:
    """What changed since ``before`` ({taskId: stable facts}), one entry per
    task: ``{id, title, what: [..], finished: bool}``."""
    changes = []
    now_ids = set()
    for t in tasks:
        now_ids.add(t["id"])
        old = before.get(t["id"])
        if old is None:
            changes.append({"id": t["id"], "label": t.get("label") or t["id"], "title": t["title"],
                            "what": ["new task"], "finished": False})
            continue
        what, finished = [], False
        if old.get("title") != t["title"]:
            what.append(f"renamed from '{old.get('title')}'")
        if old.get("status") != t["status"]:
            what.append(f"status {old.get('status')} → {t['status']}")
            finished |= t["status"] == "stopped"
        if old.get("column") != t["column"]:
            what.append(f"moved {old.get('column')} → {t['column']}")
            finished |= t["column"] in ("inreview", "done")
        if old.get("attention") != t["attention"]:
            what.append(f"attention {old.get('attention') or 'none'} → {t['attention'] or 'none'}")
        if (old.get("report") or 0) != t["report"] and t["report"]:
            what.append(f"reported {t['reportKind']}")
            finished |= t["reportKind"] == "completed"
        if t["merged"] and not old.get("merged"):
            what.append(f"its work merged into {t['base']}")
            finished = True
        elif old.get("head") != t["head"] and t["head"] and not t["merged"]:
            what.append("new commits" if old.get("head") else "first commits on its branch")
        if what:
            changes.append({"id": t["id"], "label": t.get("label") or t["id"], "title": t["title"],
                            "what": what, "finished": finished})
    for tid, old in before.items():
        if tid not in now_ids:
            changes.append({"id": tid, "label": old.get("label") or tid, "title": old.get("title", tid),
                            "what": ["no longer in this project"], "finished": False})
    return changes


def _ago(s: float) -> str:
    s = int(s or 0)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 2 * 86400:
        return f"{s / 3600:.1f} h"
    return f"{s // 86400} d"


def plain_facts(project: dict, tasks: list[dict], changes: list[dict], since: float) -> str:
    """The facts as text: what the model is given, and what is sent when it fails."""
    when = time.strftime("%H:%M", time.localtime(since)) if since else "the start"
    lines = [f"Project '{project.get('name', project['id'])}' — changes since {when}:"]
    fin = [c for c in changes if c["finished"]]
    name = lambda x: x.get("label") or x["id"]
    if fin:
        lines.append("Finished: " + "; ".join(f"{c['title']} ({name(c)})" for c in fin))
    for c in changes:
        lines.append(f"- {c['title']} ({name(c)}): " + ", ".join(c["what"]))
    changed_ids = {c["id"] for c in changes}
    open_ = [t for t in tasks if t["column"] != "done" or t["id"] in changed_ids]
    if open_:
        lines.append("")
        lines.append("Tasks now:")
    for t in open_:
        bits = [t["status"], t["column"], f"last active {_ago(t['idleSeconds'])} ago"]
        if t["attention"]:
            bits.append(f"needs attention — {t['attention']}: {t['attentionReason'][:200]}")
        if t["branch"]:
            # Three different facts, never one number: zero commits ahead is
            # both "merged" and "not started", and must not be left to guess.
            if t["merged"]:
                c = f"work merged into {t['base']} (branch {t['branch']}, nothing left to merge)"
            elif t["commits"]:
                c = f"{t['commits']} commit(s) on {t['branch']} not yet in {t['base']}"
            else:
                c = f"no commits yet on {t['branch']}"
            if t["lastCommit"]:
                c += f", last: {t['lastCommit']}"
            bits.append(c)
        if t["reportKind"] and t["id"] in changed_ids:
            bits.append(f"report ({t['reportKind']}): {t['reportText']}")
        lines.append(f"- {t['title']} ({name(t)}): " + "; ".join(bits))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writing it up
# ---------------------------------------------------------------------------

_PROMPT = (
    "You write a progress digest for the product owner (PO) of a software "
    "project, who runs the tasks listed below and will read this between other "
    "work. Use ONLY the facts given; never guess or add anything. State only "
    "what the facts say about branches and merges: a task is merged only when "
    "the facts say its work is merged, and never write that work is awaiting a "
    "merge, a hub restart or a deploy unless the facts say so. Lead with what "
    "finished or changed since the last digest, then call out any task that "
    "needs attention and why. Name tasks by title with their number (#18) "
    "or id in parentheses, as the facts give it. Plain text, at most 8 short lines, no headings, no greeting, "
    "no closing remarks.\n\nFacts:\n"
)


def write_up(facts: str) -> tuple[str, str]:
    """(text, how): the model's digest, or the plain facts and why the model
    wasn't used. Never raises."""
    bin_path = _d._claude_bin()
    if not bin_path:
        return facts, "plain facts — the claude command line was not found"
    workspace = _d.DASHBOARD_DIR / "_rename_workspace"
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # No session file, no tools, no MCP servers: the facts are the only input.
        r = subprocess.run(
            [bin_path, "-p", "--model", _model(), "--no-session-persistence",
             "--tools", "", "--strict-mcp-config"],
            input=_PROMPT + facts, cwd=str(workspace), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=MODEL_TIMEOUT_S,
            creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return facts, f"plain facts — the model call failed: {str(e)[:160]}"
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        why = " ".join((r.stderr or r.stdout or "").split())[:160] or f"exit {r.returncode}"
        return facts, f"plain facts — the model call failed: {why}"
    return out, f"written by {_model()}"


# ---------------------------------------------------------------------------
# Delivering it
# ---------------------------------------------------------------------------

def _po_target(project: dict) -> tuple[dict | None, str, str]:
    """(room, PO identity, why-not). The room is None when there is no one to
    wake right now."""
    rid = (project.get("poRoomId") or "").strip()
    if not rid:
        return None, "", "the project has no PO"
    room = _d.chatroom.get_room(rid)
    if room is None:
        return None, "", f"the PO task {rid} no longer exists"
    ident = _d.chatroom.po_identity(room)
    if not ident:
        return None, "", "the PO task has no agent"
    if not _d._room_is_live(room):
        return None, ident, "the PO is not running"
    return room, ident, ""


def _deliver(project: dict, room: dict, ident: str, text: str, how: str,
             changes: list[dict]) -> bool:
    body = f"**Progress digest** — {how}\n\n{text}"
    res = _d.chatroom.post_report(room["id"], SENDER, ident, body,
                                  {"reportKind": "digest", "changedTasks": [c["id"] for c in changes]})
    if not res:
        return False
    # The doorbell carries the digest itself, on one line: a multi-line paste
    # lands unsubmitted in a TUI, and a one-agent PO has no chat_read.
    flat = " ".join(text.split())
    if len(flat) > _WAKE_MAX:
        flat = flat[:_WAKE_MAX] + "…"
    wake = (f"[digest] {project.get('name', project['id'])}: {flat} — details with "
            f"ensemble_list_tasks / ensemble_get_task.")
    part = _d.chatroom.participant(room, ident) or {}
    sess = _d.ptyrun.get(part.get("ptyId") or "")
    if sess and sess.alive():
        sess.send_line(wake)
        return True
    return False


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------

def check(project: dict, force: bool = False) -> dict:
    """Check one project now. ``force`` sends even when nothing changed (the
    baseline still advances). Returns {result, ...} and logs it."""
    with _LOCK:
        return _check(project, force)


def _check(project: dict, force: bool) -> dict:
    pid, name = project["id"], project.get("name", project["id"])
    now = time.time()
    st = _STATE.setdefault(pid, {})
    st["lastCheck"] = now
    base = _load_baselines().get(pid)
    tasks = gather(project)
    # Before anything else, and whether or not a digest goes out: the board
    # follows a merge even while the PO is not running.
    _settle_merges(tasks)
    stable = {t["id"]: _stable(t) for t in tasks}

    def done(result: str, **extra) -> dict:
        st["lastResult"] = result
        st.update(extra)
        _log(f"{name}: {result}")
        return {"projectId": pid, "result": result, **extra}

    if base is None:
        _save_baseline(pid, {"tasks": stable, "lastCheck": now, "lastSent": 0})
        return done(f"first check — baseline of {len(tasks)} task(s) recorded, nothing sent")
    changes = diff(base.get("tasks") or {}, tasks)
    if not changes and not force:
        base["lastCheck"] = now
        _save_baseline(pid, base)
        return done("nothing changed — skipped, PO not woken")
    room, ident, why = _po_target(project)
    if room is None:
        # Keep the old baseline: these changes go into the next digest.
        return done(f"{len(changes)} change(s), not sent — {why}", pending=len(changes))
    facts = plain_facts(project, tasks, changes, float(base.get("lastSent") or 0))
    text, how = write_up(facts)
    woke = _deliver(project, room, ident, text, how, changes)
    _save_baseline(pid, {"tasks": stable, "lastCheck": now, "lastSent": now})
    st["lastSent"] = now
    return done(f"{len(changes)} change(s) — digest sent ({how})"
                + ("" if woke else ", but the PO could not be rung"),
                pending=0, woke=woke, text=text, how=how)


# ---------------------------------------------------------------------------
# Scheduler and status
# ---------------------------------------------------------------------------

def status() -> dict:
    """Per project: its interval, when it was last checked / sent to, what the
    last check concluded and when the next is due."""
    base = _load_baselines()
    out = []
    for p in _d.load_projects():
        st = _STATE.get(p["id"], {})
        b = base.get(p["id"]) or {}
        out.append({"projectId": p["id"], "name": p.get("name", ""),
                    "poRoomId": p.get("poRoomId", ""),
                    "intervalMin": interval_min(p),
                    "ownInterval": clamp_interval(p.get("digestIntervalMin")),
                    "lastCheck": st.get("lastCheck") or b.get("lastCheck", 0),
                    "lastSent": b.get("lastSent", 0),
                    "nextCheck": st.get("nextCheck", 0),
                    "lastResult": st.get("lastResult", "")})
    return {"model": _model(), "projects": out}


def _tick() -> None:
    now = time.time()
    for p in _d.load_projects():
        if not (p.get("poRoomId") or "").strip():
            continue
        mins = interval_min(p)
        st = _STATE.setdefault(p["id"], {})
        if mins <= 0:
            st["nextCheck"] = 0
            continue
        # Re-derived every tick from the last check, so a changed interval
        # takes effect at once — no restart, no waiting out the old one. The
        # first check comes a minute after start, once the hub has settled.
        last = st.get("lastCheck") or 0
        st["nextCheck"] = last + mins * 60 if last else _STARTED + 60
        if now >= st["nextCheck"]:
            check(p)


_STARTED = 0.0


def start_scheduler() -> None:
    """Background loop; settings and project.json are re-read every tick."""
    global _STARTED
    _STARTED = time.time()

    def loop():
        while True:
            try:
                _tick()
            except Exception as e:          # keep the loop alive
                _log(f"scheduler error: {str(e)[:200]}")
            time.sleep(TICK_S)

    threading.Thread(target=loop, daemon=True, name="po-digest").start()
