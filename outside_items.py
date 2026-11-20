"""Work items that fill and empty themselves (round 2 step 2).

The routing table's rows list each initiative's work items: tickets and pull
requests (``outside_route``; a message naming one goes straight to that
initiative). Besides the CEO and the PO, the hub keeps them:

- **From hub tasks** (``scan_tasks``): a task of an initiative adds the
  tickets its title, branch or task folder names, and the pull requests its
  reports link to (the pull requests it opened), with ``from`` "task #N".
  Its spec counts too, when it names at most ``SPEC_ITEMS_MAX`` items (a
  spec listing more is a list of other work, not the task's own). Tasks
  are read oldest first, so when tasks of two initiatives name the same new
  item the first one's initiative keeps it; the other is noted. An item a
  row lists already is never moved.
- **From routed messages** and **Send elsewhere**: in ``outside_route``.

**Dropping finished items** (``drop_finished``): an item leaves its row 14
days after its pull request was merged or closed, or its ticket released or
closed. A pull request's state is read with ``gh pr view`` (read only), at
most every 6 hours while it is open, and only for a repo whose organisation
the hub knows (from a link it has seen, or a project's git remote). A
ticket's state comes from the Jira source (``note_ticket_state``, round 2
step 3); until a ticket has one, it is dropped 30 days after it was last
named: in a message, by a task still open, or when it was added.

Runs from the hub's background loop (``maybe_tick``) every 10 minutes, on a
thread of its own, and only when an input source is configured. Reads hub
tasks and never writes to them.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime

import outside
import outside_route as route
import work_items

_d = None


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


EVERY_S = 600                   # how often tasks are read and finished items dropped
SPEC_ITEMS_MAX = 5              # a spec naming more items than this adds none of them
DONE_KEEP_S = 14 * 86400        # a merged or closed pull request, a released or closed ticket
QUIET_S = 30 * 86400            # a ticket with no state from Jira, not named for this long
PR_RECHECK_S = 6 * 3600         # an open pull request's state is read again after this
GH_MAX = 40                     # at most this many ``gh`` reads per run
GH_TIMEOUT_S = 20
DONE_PR = route.DONE_PR_STATES
DONE_TICKET = route.DONE_TICKET_STATES
FORGET_S = 60 * 86400           # what the hub knows of an item in no row, kept this long

_LOCK = threading.Lock()
_LAST = 0.0
_ORGS: dict[str, str] = {}      # repo: organisation, from the projects' git remotes
_ORGS_READ = [0.0]              # when the remotes were last read (an empty result too)
ORGS_EVERY_S = 3600             # the remotes are read again this often (a new project, a move)


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] work items: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# From hub tasks
# ---------------------------------------------------------------------------

def _report_texts(room: dict) -> list[str]:
    out = []
    for k in ("lastReport", "lastRealReport"):
        r = room.get(k)
        if isinstance(r, dict) and isinstance(r.get("text"), str):
            out.append(r["text"])
    return out


def _base(path: str) -> str:
    return str(path or "").rstrip("/").rsplit("/", 1)[-1]


def items_from_tasks(rooms: list[dict], projects: list[dict]) -> dict:
    """The items each initiative's tasks name, read from task records only
    (no hub state): ``{"adds": {project id: [(kind, key, "task #N")]},
    "clashes": [(key, kind, wanted project id, kept project id, "task #N")],
    "open": {keys named by a task not done}, "orgs": {repo: org}}``.
    Each item goes to the first initiative whose task names it: first by
    the strong signs (title, branch, task folder, pull requests in its
    reports) of every task, oldest task first, then by specs."""
    ids = {p.get("id") for p in projects if isinstance(p, dict) and p.get("id")}
    po_rooms = {p.get("poRoomId") for p in projects if isinstance(p, dict) and p.get("poRoomId")}
    # Task references look like ticket keys ("ED-18"): a project key of this hub is not a ticket project.
    task_keys = {str(p.get("key") or "").upper() for p in projects if isinstance(p, dict)}
    tasks = [r for r in rooms if isinstance(r, dict) and r.get("projectId") in ids
             and r.get("id") not in po_rooms and str(r.get("no") or "").strip()]
    tasks.sort(key=lambda r: (route._num(r.get("createdAt")), str(r.get("id"))))
    orgs: dict[str, str] = {}
    for r in tasks:
        for text in _report_texts(r) + [str(r.get("spec") or "")]:
            for org, repo, _n in work_items.pr_links(text):
                orgs[repo] = org
    repos = set(orgs)

    def ok(kind, key):
        return kind != work_items.TICKET or work_items.prefix_of(key) not in task_keys

    strong: list[tuple[dict, list]] = []
    prefixes: set[str] = set()
    for r in tasks:
        got = [x for x in work_items.find(str(r.get("title") or ""), repos) if ok(*x)]
        for text in _report_texts(r):
            got += [(work_items.PR, f"{repo}#{n}") for _o, repo, n in work_items.pr_links(text)]
        strong.append((r, got))
        prefixes |= {work_items.prefix_of(k) for kind, k in got if kind == work_items.TICKET}
    specs: list[tuple[dict, list]] = []
    for r in tasks:
        got = [x for x in work_items.find(str(r.get("spec") or ""), repos) if ok(*x)]
        if len(got) <= SPEC_ITEMS_MAX:
            specs.append((r, got))
            prefixes |= {work_items.prefix_of(k) for kind, k in got if kind == work_items.TICKET}
    prefixes -= task_keys
    for r, got in strong:
        ws = r.get("workspace") if isinstance(r.get("workspace"), dict) else {}
        for name in (str(ws.get("branch") or ""), _base(r.get("taskDir") or ws.get("taskDir") or "")):
            got += work_items.find_in_name(name, prefixes)
    owner: dict[str, str] = {}
    adds: dict[str, list] = {}
    clashes: list[tuple] = []
    open_keys: set[str] = set()
    for r, got in strong + specs:
        pid, src = r["projectId"], f"{route.FROM_TASK}#{r.get('no')}"
        done = r.get("workflow") == "done"
        for kind, key in got:
            if not done:
                open_keys.add(key)
            if key not in owner:
                owner[key] = pid
                adds.setdefault(pid, []).append((kind, key, src))
            elif owner[key] != pid:
                clashes.append((key, kind, pid, owner[key], src))
    return {"adds": adds, "clashes": clashes, "open": open_keys, "orgs": orgs}


def scan_tasks(now: float | None = None) -> dict[str, list[str]]:
    """Read the hub's tasks and add the items they name to their
    initiatives' rows (``items_from_tasks``). Returns the keys added."""
    now = time.time() if now is None else now
    d = _d if _d is not None else getattr(outside, "_d", None)
    try:
        rooms = d.chatroom.list_rooms(copy_rooms=False)
        projects = d.load_projects() or []
    except Exception as e:
        _log(f"tasks could not be read: {str(e)[:200]}")
        return {}
    got = items_from_tasks(rooms, projects)
    names = route.initiatives()
    with route._LOCK:
        st = route.items_state()
        st["orgs"].update({k: v for k, v in got["orgs"].items() if k not in st["orgs"]})
        for key in got["open"]:
            st["seen"][key] = max(route._num(st["seen"].get(key)), now)
        have = {(n.get("key"), n.get("wanted")) for n in st["notes"]}
        for key, kind, wanted, kept, src in got["clashes"]:
            if (key, wanted) in have or wanted not in names:
                continue
            have.add((key, wanted))
            st["notes"].append({"at": now, "key": key, "kind": kind, "wanted": wanted,
                                "wantedName": names[wanted], "kept": kept,
                                "keptName": names.get(kept, kept), "from": src})
        route.save_items_state(st)
    return route.add_items_auto(got["adds"], "named by its tasks", now)


# ---------------------------------------------------------------------------
# Pull request and ticket states
# ---------------------------------------------------------------------------

def _run_gh(args: list[str]) -> str:
    """``gh`` with these arguments (read only: ``pr view``), its output."""
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gh failed").strip()[:200])
    return p.stdout


GH = _run_gh                    # tests put a fake here


def _project_orgs() -> dict[str, str]:
    """{repo: organisation} from the hub's code projects' git remotes."""
    if time.time() - _ORGS_READ[0] < (ORGS_EVERY_S if _ORGS else EVERY_S):
        return _ORGS
    _ORGS_READ[0] = time.time()
    d = _d if _d is not None else getattr(outside, "_d", None)
    try:
        projects = d.load_projects() or []
    except Exception:
        return _ORGS
    fresh: dict[str, str] = {}
    for p in projects:
        path = p.get("path") if isinstance(p, dict) else None
        if not path or not p.get("isGit"):
            continue
        try:
            url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", url)
        if m:
            fresh.setdefault(m.group(2).lower(), m.group(1))
    # The map read now replaces the old one (P439): a removed or moved
    # project's remote is not kept forever.
    _ORGS.clear()
    _ORGS.update(fresh)
    return _ORGS


def _when(iso) -> float:
    if not isinstance(iso, str) or not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def org_of(key: str, orgs: dict[str, str]) -> str:
    """The organisation of a pull request (``repo#12``), or "" when the hub
    does not know it (then ``gh`` is not asked)."""
    repo, _, n = key.partition("#")
    if not n.isdigit():
        return ""
    return orgs.get(repo) or _project_orgs().get(repo) or ""


def pr_state(key: str, orgs: dict[str, str]) -> dict | None:
    """``{state: "open"|"merged"|"closed", at}`` of a pull request (``repo#12``)
    read with ``gh``, or None when its organisation is not known or ``gh``
    fails."""
    org = org_of(key, orgs)
    if not org:
        return None
    repo, _, n = key.partition("#")
    try:
        out = json.loads(GH(["pr", "view", n, "--repo", f"{org}/{repo}", "--json", "state,mergedAt,closedAt"]))
    except Exception as e:
        _log(f"{key}: its state could not be read: {str(e)[:150]}")
        return None
    state = str(out.get("state") or "").lower()
    if state not in ("open", "merged", "closed"):
        return None
    at = _when(out.get("mergedAt")) if state == "merged" else _when(out.get("closedAt"))
    return {"state": state, "at": at}


def note_ticket_state(key: str, state: str, at: float) -> None:
    """For the Jira source (round 2 step 3): a ticket's state ("open",
    "released", "closed" or "done") since ``at``. A released or closed
    ticket leaves its row 14 days after ``at``; a ticket with a state is no
    longer dropped for being quiet."""
    key = str(key or "").upper()
    if not key:
        return
    with route._LOCK:
        st = route.items_state()
        st["tickets"][key] = {"state": str(state or "").lower(), "at": route._num(at)}
        route.save_items_state(st)


# ---------------------------------------------------------------------------
# Dropping finished items
# ---------------------------------------------------------------------------

def _days(s: float) -> int:
    return int(s // 86400)


def _forget(st: dict, listed: set[str], now: float) -> None:
    """What the hub knows of items no row lists (last named, pull request
    and ticket states) is forgotten ``FORGET_S`` after it was last
    touched. ``dropped`` stays (``DROPPED_MAX``), so a finished item does not
    come back."""
    def last(key):
        p, tk = st["prs"].get(key), st["tickets"].get(key)
        return max(route._num(st["seen"].get(key)),
                   route._num(p.get("checked")) if isinstance(p, dict) else 0.0,
                   route._num(tk.get("at")) if isinstance(tk, dict) else 0.0)
    for part in ("seen", "prs", "tickets"):
        st[part] = {k: v for k, v in st[part].items() if k in listed or now - last(k) < FORGET_S}


def drop_finished(now: float | None = None) -> int:
    """Take finished items out of their rows (see the module's words).
    Returns how many were taken out."""
    now = time.time() if now is None else now
    names = route.initiatives()
    t = route._load_one()
    with route._LOCK:
        st = route.items_state()
    budget = GH_MAX
    drops: dict[str, list[tuple[str, str]]] = {}
    for pid in names:
        row = t["rows"].get(pid)
        if not row:
            continue
        for i in row[route.ITEMS]:
            key, kind = i["key"], i["kind"]
            # An item a PO, the CEO or the setup file added after it finished stays 14 days from that add.
            by_hand = route._num(i.get("at")) if i.get("from") in route.PEOPLE_SET else 0.0
            if kind == route.PR:
                info = st["prs"].get(key) if isinstance(st["prs"].get(key), dict) else None
                stale = info is None or (info.get("state") not in DONE_PR
                                         and now - route._num(info.get("checked")) >= PR_RECHECK_S)
                # Only a real ``gh`` read counts against the budget; one that
                # failed waits PR_RECHECK_S like an open pull request.
                if stale and budget > 0 and org_of(key, st["orgs"]):
                    budget -= 1
                    got = pr_state(key, st["orgs"])
                    info = {**got, "checked": now} if got else \
                        {**(info or {"state": "unknown"}), "checked": now}
                    st["prs"][key] = info
                if info and info.get("state") in DONE_PR \
                        and now - max(route._num(info.get("at"), now), by_hand) >= DONE_KEEP_S:
                    drops.setdefault(pid, []).append(
                        (key, f"{work_items.label(kind, key)} {info['state']} "
                              f"{_days(now - route._num(info.get('at')))} days ago"))
                continue
            tk = st["tickets"].get(key) if isinstance(st["tickets"].get(key), dict) else None
            if tk:
                if tk.get("state") in DONE_TICKET and now - max(route._num(tk.get("at"), now), by_hand) >= DONE_KEEP_S:
                    drops.setdefault(pid, []).append(
                        (key, f"{work_items.label(kind, key)} {tk['state']} "
                              f"{_days(now - route._num(tk.get('at')))} days ago"))
                continue
            if kind != work_items.TICKET:
                continue                    # an epic waits for its state from Jira
            last = max(route._num(i.get("at")), route._num(st["seen"].get(key)))
            if now - last >= QUIET_S:
                drops.setdefault(pid, []).append(
                    (key, f"{work_items.label(kind, key)} not named for {_days(now - last)} days"))
    listed = {i["key"] for row in t["rows"].values() if row for i in row[route.ITEMS]}
    with route._LOCK:
        fresh = route.items_state()
        fresh["prs"].update(st["prs"])
        _forget(fresh, listed, now)
        route.save_items_state(fresh)
    n = 0
    for pid, gone in drops.items():
        n += route.drop_items({pid: [k for k, _w in gone]}, "finished: " + "; ".join(w for _k, w in gone), now)
    if n:
        _log(f"{n} finished work item{'s' if n != 1 else ''} taken out of the routing table")
    return n


# ---------------------------------------------------------------------------
# The background run
# ---------------------------------------------------------------------------

def run(now: float | None = None) -> None:
    """One run: tasks, then finished items."""
    scan_tasks(now)
    drop_finished(now)


def _run_then_release() -> None:
    try:
        run()
    except Exception as e:
        _log(f"error: {str(e)[:200]}")
    finally:
        _LOCK.release()


def maybe_tick(now: float | None = None) -> None:
    """From the hub's background loop: a run every ``EVERY_S``, on a thread
    of its own, while an input source is configured."""
    global _LAST
    now = time.time() if now is None else now
    if now - _LAST < EVERY_S or not outside.load_config():
        return
    if not _LOCK.acquire(blocking=False):
        return
    _LAST = now
    threading.Thread(target=_run_then_release, daemon=True, name="outside-items").start()
