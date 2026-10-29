"""Freeing disk space (#260): what Ensemble keeps that can go, and taking it away.

Ensemble's state grows and nothing took it away: a Done task keeps its
worktree, its branch, its turn checkpoints, its task folder, every agent
conversation it had and its whole chat. This module finds what can go
(:func:`scan`, a preview with sizes, grouped) and deletes what the person
ticked in that preview (:func:`run`), each part checked again just before it
goes. Every deletion is a line in ``~/.ensemble/logs/prune.log``.

The groups of a preview:

* ``done``: Done tasks older than N days. A task goes as one item: its git
  worktree (``git worktree remove``), its branch when ``main`` holds it (an
  unmerged branch is never deleted, only named), its checkpoint refs, its task
  folder, its agents' conversations, its chat log and what only it uploaded.
  Its room stays as a slim record (number, title, spec, dates, final report and
  one line saying it was pruned), so its number and links keep working.
* ``po``: a PO's past conversations (rotated out, or from before the room
  recorded them), older than N days. Its current one never. Its text is kept
  by the hub first (``Hub.keep_text``, #265): the PO's chat shows it, and its
  points, links and threads lead into it; one whose text cannot be kept stays.
* ``hub``: conversations the hub started that no task holds (progress checks,
  tasks deleted before #260), older than N days.
* ``own``: the person's own conversations, older than N days: listed, never
  ticked by itself.
* ``orphans``: task folders under a project's home that no task names, and
  worktrees git still lists whose folder is gone.
* ``housekeeping``: rotated logs, uploads, sends and terminal spools of tasks
  that are gone, launch files older than a day.

Never touched, whatever is ticked: a task that is not Done, a task with a live
terminal, a PO's room and its current conversation, a project's Documents
folder, its roadmap and PO-HANDOVER files, ``main``, and anything outside
Ensemble's state, the project homes and the agents' conversation folders.

Decoupled from dashboard.py like checkpoints.py: the hub hands it a
:class:`Hub` of callables, so a test hands it a fake one.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fslinks

DAY = 86400
DEFAULT_DAYS = 30
RECENT_S = 3600                 # a conversation written this recently is never listed
LOG_NAME = "prune.log"
# A task's copy of a project with no git: what it changed may be in no other place.
COPY_NOTE = "its repo/ is a copy with no git: its work may exist nowhere else"
GROUPS = ("done", "po", "hub", "own", "orphans", "housekeeping")
# Ticked by itself in a preview: everything but the person's own conversations.
TICKED = {"done": True, "po": True, "hub": True, "own": False, "orphans": True, "housekeeping": True}
# Names a task folder holds that say it is one (and never a project's own).
_KEEP_NAMES = {"documents", "roadmap.md", "po-handover.md", "po-handover-archive.md", "project.json",
               ".history", ".git"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ROTATED_LOG = re.compile(r"\.log\.\d+$")
_SID_TAIL = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
# How long a preview measures one item (#282): one slower than this is kept,
# named "too slow to measure", and the scan goes on with the rest.
ITEM_BUDGET_S = 30.0
TOO_SLOW = f"too slow to measure (over {int(ITEM_BUDGET_S)} s)"


@dataclass
class Hub:
    """What the scan and the deletions need of the hub."""
    state_dir: Path                         # ~/.ensemble
    claude_dir: Path                        # ~/.claude/projects
    codex_dir: Path                         # ~/.codex/sessions
    projects: Callable[[], list]
    rooms: Callable[[], list]               # every room, full (participants), messages not needed
    live_rooms: Callable[[], set]           # room ids with a live terminal
    live_sessions: Callable[[], set]        # conversation ids a live terminal runs
    room_sessions: Callable[[dict], list]   # [(agent, sid)] every conversation of a room
    current_sessions: Callable[[dict], set]  # its seats' current conversation ids
    classify: Callable[[str, Path, str], tuple]  # (agent, file, sid) -> ("po", rid) | ("hub", why) | ("own", first words)
    slim_room: Callable[[str, dict], bool]
    forget_checkpoints: Callable[[dict], int]
    checkpoint_counts: Callable[[str], dict]  # repo -> {room: refs}
    project_home: Callable[[dict], str]
    documents_dir: Callable[[dict], str]
    scratch_root: Callable[[str], str] = lambda cwd: ""
    pty_ids: Callable[[], set] = lambda: set()  # every terminal the hub lists, live or kept
    progress: Callable[..., None] = lambda *a, **k: None
    now: Callable[[], float] = time.time
    # (agent, sid, file) -> whether the conversation's text is kept by the hub
    # (its turn store, #265): a PO's chat shows it, links and threads lead into it.
    keep_text: Callable[[str, str, str], bool] = lambda agent, sid, path: True


# ---------------------------------------------------------------------------
# Paths: long ones on Windows, sizes, removal
# ---------------------------------------------------------------------------

def _long(p) -> str:
    """The path as Windows takes it past 260 characters (``\\\\?\\``); itself
    elsewhere. A relative path is made absolute first: the prefix turns off
    every other rewriting."""
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    s = os.path.abspath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _short(p: str) -> str:
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    if p.startswith("\\\\?\\"):
        return p[4:]
    return p


class TooSlow(Exception):
    """A measure ran past its item's time budget."""


def size_of(path, *, stop: Callable[[], bool] | None = None, until: float = 0.0) -> int:
    """Bytes under ``path`` (a file or a folder), paths past 260 characters
    too. A link (symlink, junction, mount point) is never followed and counts
    as 0 (:mod:`fslinks`). 0 when it is not there. ``stop``: cut short (the
    bytes so far). ``until``: a ``time.monotonic()`` past which it raises
    :class:`TooSlow`."""
    top = _long(path)
    try:
        st = os.lstat(top)
    except OSError:
        return 0
    if fslinks.is_link(top):
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    stack = [top]
    n = 0
    while stack:
        if stop and stop():
            break
        if until and time.monotonic() > until:
            raise TooSlow(path)
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    n += 1
                    if n % 2000 == 0:
                        if until and time.monotonic() > until:
                            raise TooSlow(path)      # never a part reported as the whole
                        if stop and stop():
                            break
                    try:
                        if fslinks.entry_is_link(e):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def remove(path) -> None:
    """Delete a file or a folder: read-only files (git's objects) and paths
    past Windows' 260 characters too. A link in it goes as a link: what it
    points to stays (:func:`fslinks.remove_tree`). Raises OSError when
    something stays."""
    p = _long(path)
    try:
        fslinks.remove_tree(p)
    except FileNotFoundError:
        pass
    if os.path.lexists(p):
        raise OSError(f"still there: {_short(p)}")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p))) if p else ""


def within(child: str, parent: str) -> bool:
    """``child`` is ``parent`` or under it."""
    c, p = _norm(child), _norm(parent)
    return bool(c and p) and (c == p or c.startswith(p.rstrip(os.sep) + os.sep))


def _git(root: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)


def _git_out(root: str, *args: str) -> str:
    try:
        r = _git(root, *args)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def main_branch(root: str) -> str:
    for cand in ("main", "master"):
        if _git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{cand}"):
            return cand
    return ""


def branch_state(root: str, branch: str, cache: dict | None = None) -> str:
    """``merged`` (main holds every commit of it), ``unmerged``, or ``gone``.
    ``cache``: repo -> every branch's state, read once per scan (two git calls
    for all its branches instead of three for each)."""
    if cache is not None and branch:
        states = cache.get(_norm(root))
        if states is None:
            states = cache[_norm(root)] = _branch_states(root)
        if states is not None:
            return states.get(branch, "gone")
    if not branch or not _git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        return "gone"
    main = main_branch(root)
    if not main or main == branch:
        return "unmerged"
    try:
        r = _git(root, "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"refs/heads/{main}")
    except (OSError, subprocess.SubprocessError):
        return "unmerged"
    return "merged" if r.returncode == 0 else "unmerged"


def _branch_states(root: str) -> dict | None:
    """branch -> ``merged`` | ``unmerged`` for every local branch, or None when
    git cannot say (each branch is then asked by itself)."""
    fmt = "--format=%(refname)"
    try:
        every = _git(root, "for-each-ref", fmt, "refs/heads")
        if every.returncode:
            return None
        main = main_branch(root)
        merged = _git(root, "for-each-ref", fmt, f"--merged=refs/heads/{main}", "refs/heads") if main else None
    except (OSError, subprocess.SubprocessError):
        return None
    if merged is not None and merged.returncode:
        return None
    def names(out: str) -> list[str]:
        return [x[len("refs/heads/"):] for x in out.split() if x.startswith("refs/heads/")]
    held = set(names(merged.stdout)) if merged is not None else set()
    return {b: "merged" if b in held and b != main else "unmerged" for b in names(every.stdout)}


def worktrees(root: str) -> list[dict]:
    """``git worktree list`` of a repository: {path, branch, prunable}."""
    out, cur = [], {}
    for line in _git_out(root, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur = {"path": line[9:], "branch": "", "prunable": False}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
        elif line.startswith("prunable"):
            cur["prunable"] = True
    return out


def is_worktree_of(repo: str, path: str, cache: dict | None = None) -> bool:
    """``git worktree list`` of ``repo`` names ``path`` (and it is not the
    repository's own checkout). ``cache``: repo -> its list, for one scan."""
    if not repo or not path or _norm(path) == _norm(repo):
        return False
    if cache is None:
        listed = worktrees(repo)
    else:
        listed = cache.get(_norm(repo))
        if listed is None:
            listed = cache[_norm(repo)] = worktrees(repo)
    return any(_norm(w["path"]) == _norm(path) for w in listed[1:])


def dirty(path: str) -> bool:
    """The checkout at ``path`` has uncommitted changes. A ``git status``
    that fails says so too: nothing is taken on a guess."""
    try:
        r = _git(path, "status", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or bool(r.stdout.strip())


def checkout_risk(folder: str, repo: str) -> tuple[str, str]:
    """``(keep, note)`` for a folder no task names that may hold a git
    checkout (itself, or its ``repo/``): ``keep`` when a commit in it is not in
    the project's repository (a clone's own branch, or one git cannot read),
    ``note`` when it has uncommitted changes (a ``git status`` that fails says
    so too)."""
    keep, notes = "", []
    for c in (folder, os.path.join(folder, "repo")):
        g = os.path.join(c, ".git")
        if not os.path.lexists(_long(g)):
            continue
        if dirty(c):
            notes.append("it has uncommitted changes")
        heads = [_git_out(c, "rev-parse", "HEAD")]
        if os.path.isdir(_long(g)):                 # a clone: every branch of its own
            heads += _git_out(c, "for-each-ref", "--format=%(objectname)", "refs/heads").split()
        heads = [h for h in heads if h]
        if not heads:
            keep = keep or "its git checkout cannot be read"
        for h in heads:
            try:
                ok = bool(repo) and _git(repo, "cat-file", "-e", f"{h}^{{commit}}").returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
            if not ok:
                keep = keep or "it holds commits the project's repository does not have"
                break
    return keep, "; ".join(dict.fromkeys(notes))


def _unread_rooms(hub: Hub, room_ids: set) -> tuple[set, set, set]:
    """Rooms on disk the hub could not read (their file or log does not
    parse): never "gone". ``(ids, folders, session ids)`` found in their raw
    text, so nothing of theirs is listed."""
    ids, folders, sids = set(), set(), set()
    try:
        files = [e for e in os.scandir(hub.state_dir / "rooms") if e.is_file() and e.name.startswith("room-")
                 and e.name.endswith(".json")]
    except OSError:
        files = []
    for e in files:
        rid = e.name[:-5]
        if rid in room_ids:
            continue
        ids.add(rid)
        try:
            raw = Path(e.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"(?:taskDir|cwd)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw):
            try:
                folders.add(_norm(json.loads(f'"{m.group(1)}"')))
            except ValueError:
                pass
        sids |= set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", raw))
    return ids, folders, sids


def room_on_disk(hub: Hub, rid: str) -> bool:
    return os.path.exists(hub.state_dir / "rooms" / f"{rid}.json")


def unlisted_checkout(r: dict, folder: str) -> str:
    """A git checkout inside a task folder that no worktree part covers (git
    does not list it, or listing failed): its path, else "". The folder then
    stays, since its work was never checked."""
    for c in {r.get("cwd") or "", os.path.join(folder, "repo") if folder else ""}:
        if c and folder and within(c, folder) and os.path.lexists(_long(os.path.join(c, ".git"))):
            return c
    return ""


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()


def log(hub: Hub, what: str, path: str, size: int, note: str = "", how: str = "manual") -> None:
    line = json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "how": how, "what": what,
                       "path": path, "bytes": int(size), **({"note": note} if note else {})},
                      ensure_ascii=False)
    d = hub.state_dir / "logs"
    with _LOG_LOCK:
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / LOG_NAME, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"[prune] not logged: {line} ({e!r})", flush=True)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def _transcript_index(hub: Hub) -> dict[str, list[tuple[str, str]]]:
    """sid -> [(agent, path)]: a Claude conversation's ``<sid>.jsonl`` and its
    ``<sid>/`` folder (subagents, tool results), a Codex conversation's
    rollouts."""
    idx: dict[str, list[tuple[str, str]]] = {}
    try:
        dirs = [e.path for e in os.scandir(hub.claude_dir) if e.is_dir()]
    except OSError:
        dirs = []
    for d in dirs:
        try:
            with os.scandir(d) as it:
                for e in it:
                    name = e.name
                    sid = name[:-6] if name.endswith(".jsonl") else name
                    if _SID_TAIL.fullmatch(sid):
                        idx.setdefault(sid, []).append(("claude", e.path))
        except OSError:
            continue
    for root, _dirs, files in fslinks.walk(hub.codex_dir):
        for f in files:
            if f.startswith("rollout-") and f.endswith(".jsonl"):
                m = _SID_TAIL.search(f[:-6])
                if m:
                    idx.setdefault(m.group(1), []).append(("codex", os.path.join(root, f)))
    return idx


def _mtime(path: str) -> float:
    try:
        return os.lstat(_long(path)).st_mtime
    except OSError:
        return 0.0


def _done_at(room: dict) -> float:
    for k in ("workflowAt", "mergedAt", "updatedAt"):
        try:
            v = float(room.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v:
            return v
    return 0.0


def _project_of(room: dict, projects: dict) -> dict | None:
    return projects.get(room.get("projectId") or room.get("noProjectId") or "")


def _protected(hub: Hub, projects: list) -> list[str]:
    """Folders nothing in a task folder may contain or be."""
    out = []
    for p in projects:
        for f in (p.get("path") or "", hub.project_home(p) or "", hub.documents_dir(p) or ""):
            if f:
                out.append(f)
    return out


def task_folder_ok(hub: Hub, folder: str, project: dict | None, projects: list) -> str:
    """Why a task folder may not be deleted, or "" when it may: it must be a
    folder of its own under its project's home (or a scratch folder of the
    hub's), and neither be nor hold a project's folder, home or Documents."""
    if not folder:
        return "no folder"
    if not os.path.isdir(_long(folder)):
        return "not there"
    home = hub.project_home(project) if project else ""
    scratch = hub.scratch_root(folder)
    if not ((home and within(folder, home) and _norm(folder) != _norm(home)) or scratch):
        return "outside the project's home"
    for f in _protected(hub, projects):
        if within(f, folder):
            return "holds a project's own folder"
    try:
        names = {n.lower() for n in os.listdir(_long(folder))}
    except OSError as e:
        return f"unreadable: {e}"
    if names & _KEEP_NAMES:
        return "holds a project's own files"
    return ""


def _item(group: str, iid: str, label: str, parts: list[dict], **extra) -> dict:
    return {"id": iid, "group": group, "label": label, "parts": parts,
            "bytes": sum(int(p.get("bytes") or 0) for p in parts), "ticked": TICKED[group], **extra}


def _part(kind: str, path: str = "", size: int = 0, **extra) -> dict:
    return {"kind": kind, "path": path, "bytes": int(size), **extra}


def scan(hub: Hub, days: int = DEFAULT_DAYS, groups=GROUPS, stop: Callable[[], bool] | None = None) -> dict:
    """What can go, grouped, with sizes. Reads only."""
    hub = _memo_homes(hub)
    t0 = hub.now()
    days = max(0, int(days))
    cutoff = t0 - days * DAY
    projects = [p for p in hub.projects() if isinstance(p, dict)]
    by_pid = {p.get("id"): p for p in projects}
    rooms = [r for r in hub.rooms() if isinstance(r, dict) and r.get("id")]
    room_ids = {r["id"] for r in rooms}
    unread_ids, unread_folders, unread_sids = _unread_rooms(hub, room_ids)
    live = set(hub.live_rooms())
    live_sids = set(hub.live_sessions())
    po_rooms = {(p.get("poRoomId") or "").strip() for p in projects} - {""}
    hub.progress("Listing conversations")
    tidx = _transcript_index(hub)
    held: dict[str, str] = {}               # sid -> room
    current: set[str] = set(live_sids)
    for r in rooms:
        for _agent, sid in hub.room_sessions(r):
            if sid:
                held.setdefault(sid, r["id"])
        current |= set(hub.current_sessions(r))
    current |= unread_sids                  # a room not read is still a room
    items: list[dict] = []
    kept: list[dict] = []                   # what stays and why (unmerged branches…)
    for rid in sorted(unread_ids):
        kept.append({"room": rid, "what": "room", "path": str(hub.state_dir / "rooms" / f"{rid}.json"),
                     "why": "its record could not be read: nothing of it is listed"})

    # Uploads: a room's own folder, unless another room's chat names it.
    att_dir = hub.state_dir / "attachments"
    att_rooms = set()
    try:
        att_rooms = {e.name for e in os.scandir(att_dir) if e.is_dir()}
    except OSError:
        pass
    named_elsewhere: dict[str, set] = {}
    if att_rooms:
        pat = re.compile(rb"attachments[\\/]+(?:\\\\)?(room-[0-9a-f]+)")
        for r in rooms:
            try:
                raw = (hub.state_dir / "rooms" / "messages" / f"{r['id']}.jsonl").read_bytes()
            except OSError:
                continue
            for m in pat.finditer(raw):
                named_elsewhere.setdefault(m.group(1).decode(), set()).add(r["id"])

    def uploads_only_of(rid: str) -> bool:
        return rid in att_rooms and not (named_elsewhere.get(rid, set()) - {rid})

    cp_counts: dict[str, dict] = {}
    wt_cache: dict[str, list] = {}          # repo -> git worktree list, once per scan
    br_cache: dict[str, dict] = {}          # repo -> its branches' states, once per scan

    def cp_count(project: dict | None, rid: str) -> int:
        path = (project or {}).get("path") or ""
        if not path or not (project or {}).get("isGit"):
            return 0
        if path not in cp_counts:
            try:
                cp_counts[path] = hub.checkpoint_counts(path) or {}
            except Exception:                       # noqa: BLE001
                cp_counts[path] = {}
        return int(cp_counts[path].get(rid, 0))

    # Done tasks
    if "done" in groups:
        done = [r for r in rooms if (r.get("workflow") or "").lower() == "done" and not r.get("pruned")]
        for i, r in enumerate(done):
            if stop and stop():
                break
            hub.progress("Measuring Done tasks", i, len(done))
            rid = r["id"]
            at = _done_at(r)
            why_not = ("a PO's room" if rid in po_rooms else
                       "its terminal is running" if rid in live else
                       f"Done {int((t0 - at) // DAY)} days ago" if at > cutoff else "")
            if why_not:
                continue
            try:
                items.append(_done_item(hub, r, _project_of(r, by_pid), projects, tidx, live_sids,
                                        uploads_only_of(rid), cp_count(_project_of(r, by_pid), rid), kept,
                                        stop, wt_cache, time.monotonic() + ITEM_BUDGET_S, br_cache))
            except TooSlow:
                kept.append({"room": rid, "no": r.get("no"), "what": "folder",
                             "path": r.get("taskDir") or (r.get("workspace") or {}).get("taskDir") or "",
                             "why": TOO_SLOW})

    # Conversations no current seat holds
    want_conv = {"po", "hub", "own"} & set(groups)
    if want_conv:
        po_parts: dict[str, list] = {}
        n = 0
        for sid, files in tidx.items():
            n += 1
            if n % 200 == 0:
                hub.progress("Reading conversations", n, len(tidx))
            if stop and stop():
                break
            if sid in current:
                continue
            rid = held.get(sid)
            newest = max(_mtime(p) for _a, p in files)
            # Written in the last hour: perhaps a live one no record names (a
            # live Codex is known by its folder only), whatever the days say.
            if newest > min(cutoff, t0 - RECENT_S):
                continue
            if rid is not None:
                # A past conversation of a PO's room; a task's go with the task.
                if rid in po_rooms and "po" in groups:
                    po_parts.setdefault(rid, []).extend(
                        _part("conversation", p, size_of(p), sid=sid, agent=a) for a, p in files)
                continue
            agent, main = files[0][0], next((p for a, p in files if p.endswith(".jsonl")), files[0][1])
            try:
                kind, who = hub.classify(agent, Path(main), sid)
            except Exception:                       # noqa: BLE001
                kind, who = "own", ""
            if kind == "po" and who in po_rooms and who not in live:
                if "po" in groups:
                    po_parts.setdefault(who, []).extend(
                        _part("conversation", p, size_of(p), sid=sid, agent=a) for a, p in files)
                continue
            group = "hub" if kind == "hub" else "own"
            if group not in groups:
                continue
            parts = [_part("conversation", p, size_of(p), sid=sid, agent=a) for a, p in files]
            items.append(_item(group, f"{group}:{sid}", _conv_label(agent, main, who, newest), parts,
                               sid=sid, agent=agent, at=newest))
        rooms_by_id = {r["id"]: r for r in rooms}
        for rid, parts in po_parts.items():
            pj = next((p for p in projects if (p.get("poRoomId") or "") == rid), None)
            name = (pj or {}).get("name") or (rooms_by_id.get(rid) or {}).get("title") or rid
            items.append(_item("po", f"po:{rid}", f"{name} · PO: {len(parts)} past conversation"
                               f"{'' if len(parts) == 1 else 's'}", parts, room=rid))

    if "orphans" in groups:
        items += _orphans(hub, projects, rooms, cutoff, kept, unread_folders, stop)
    if "housekeeping" in groups and not (stop and stop()):
        items += _housekeeping(hub, room_ids | unread_ids, live, cutoff)

    out_groups = []
    for g in GROUPS:
        its = sorted((x for x in items if x["group"] == g), key=lambda x: -x["bytes"])
        if g in groups:
            out_groups.append({"id": g, "items": its, "bytes": sum(x["bytes"] for x in its),
                               "ticked": TICKED[g]})
    return {"at": t0, "days": days, "took": round(hub.now() - t0, 1), "groups": out_groups,
            "bytes": sum(g["bytes"] for g in out_groups), "kept": kept}


def _memo_homes(hub: Hub) -> Hub:
    """The hub with each project's home and Documents read once per scan:
    every item asks them of every project."""
    def memo(fn):
        seen: dict = {}

        def get(p):
            key = (p or {}).get("id") if isinstance(p, dict) else None
            if key is None:
                return fn(p)
            if key not in seen:
                seen[key] = fn(p)
            return seen[key]
        return get
    return dataclasses.replace(hub, project_home=memo(hub.project_home), documents_dir=memo(hub.documents_dir))


def _conv_label(agent: str, path: str, who: str, at: float) -> str:
    """Its first words (or the hub's kind of it), and when it last changed."""
    when = time.strftime("%Y-%m-%d", time.localtime(at)) if at else ""
    if not who:
        who = ("Codex conversation" if agent == "codex" else
               f"Claude conversation in {os.path.basename(os.path.dirname(path))}")
    return f"{who} · {when}" if when else who


def _done_item(hub: Hub, r: dict, project: dict | None, projects: list, tidx: dict,
               live_sids: set, uploads: bool, checkpoints: int, kept: list, stop=None,
               wt_cache: dict | None = None, until: float = 0.0, br_cache: dict | None = None) -> dict:
    """One Done task as an item. Raises :class:`TooSlow` past ``until``."""
    rid = r["id"]
    parts: list[dict] = []
    ws = r.get("workspace") or {}
    folder = r.get("taskDir") or ws.get("taskDir") or ""
    repo = (project or {}).get("path") or ""
    cwd = r.get("cwd") or ""
    if ws.get("mode") == "worktree" and repo and cwd and folder and within(cwd, folder) \
            and _norm(cwd) != _norm(repo) and os.path.isdir(_long(cwd)) \
            and _allowed_path(hub, cwd, projects) and is_worktree_of(repo, cwd, wt_cache):
        parts.append(_part("worktree", cwd, size_of(cwd, stop=stop, until=until), repo=repo))
    branch = ws.get("branch") or ""
    if branch and repo:
        state = branch_state(repo, branch, br_cache)
        if state == "merged":
            parts.append(_part("branch", branch, 0, repo=repo))
        elif state == "unmerged":
            kept.append({"room": rid, "no": r.get("no"), "what": "branch", "branch": branch, "repo": repo,
                         "why": "not merged into main"})
    if checkpoints:
        parts.append(_part("checkpoints", f"refs/ensemble/checkpoints/{rid}", 0, count=checkpoints,
                           repo=repo))
    why = task_folder_ok(hub, folder, project, projects) if folder else "no folder"
    wt = next((p for p in parts if p["kind"] == "worktree"), None)
    if not why and not wt and unlisted_checkout(r, folder):
        why = "it holds a git checkout that git does not list: its work was never checked"
    if not why:
        size = size_of(folder, stop=stop, until=until) - (wt["bytes"] if wt else 0)
        parts.append(_part("folder", folder, max(0, size)))
    elif why not in ("no folder", "not there"):
        kept.append({"room": rid, "no": r.get("no"), "what": "folder", "path": folder, "why": why})
    for agent, sid in hub.room_sessions(r):
        if sid in live_sids:
            continue
        for a, p in tidx.get(sid, []):
            parts.append(_part("conversation", p, size_of(p, stop=stop, until=until), sid=sid, agent=a))
    st = hub.state_dir
    for kind, p in (("chat", st / "rooms" / "messages" / f"{rid}.jsonl"),
                    ("chat", st / "rooms" / "before-message-log" / f"{rid}.json"),
                    ("chat", st / "input-provenance" / f"{rid}.jsonl"),
                    ("sends", st / "sends" / f"{rid}.json")):
        s = size_of(p)
        if s:
            parts.append(_part(kind, str(p), s))
    room_file = st / "rooms" / f"{rid}.json"
    parts.append(_part("room", str(room_file), max(0, size_of(room_file) - 4096)))
    if uploads:
        p = st / "attachments" / rid
        parts.append(_part("uploads", str(p), size_of(p)))
    no = r.get("no")
    title = r.get("title") or ""
    if no:
        title = re.sub(rf"^\s*#?{no}[.:)]?\s+", "", title)       # "18. Fix it" is #18 already
    label = f"#{no} {title}".strip() if no else (title or rid)
    extra = {}
    if wt and dirty(wt["path"]):
        # Work never committed would go with the worktree: never by itself.
        extra = {"note": "its worktree has uncommitted changes", "ticked": False}
    elif ws.get("mode") == "copy" and any(p["kind"] == "folder" for p in parts) \
            and os.path.isdir(_long(os.path.join(folder, "repo"))) \
            and not os.path.lexists(_long(os.path.join(folder, "repo", ".git"))):
        extra = {"note": COPY_NOTE, "ticked": False}
    item = _item("done", f"done:{rid}", label, parts, room=rid, no=no,
                 project=(project or {}).get("name") or "", at=_done_at(r), **extra)
    # A no-project task's folder is the user's document store. Keep it visible
    # in the Done preview, but require an explicit selection before deletion.
    if not project:
        item["ticked"] = False
    return item


def _orphans(hub: Hub, projects: list, rooms: list, cutoff: float, kept: list,
             unread_folders: set = frozenset(), stop: Callable[[], bool] | None = None) -> list[dict]:
    items = []
    named = set(unread_folders)
    for r in rooms:
        for k in ("taskDir", "cwd"):
            if r.get(k):
                named.add(_norm(r[k]))
        for f in ((r.get("workspace") or {}).get("taskDir"),):
            if f:
                named.add(_norm(f))
        for p in r.get("participants") or []:
            if isinstance(p, dict) and p.get("cwd"):
                named.add(_norm(p["cwd"]))
    seen_repos = set()
    for pj in projects:
        home = hub.project_home(pj) or ""
        if home and os.path.isdir(home):
            try:
                children = [e for e in os.scandir(home) if fslinks.is_dir(e)]
            except OSError:
                children = []
            for e in children:
                if stop and stop():
                    return items
                if not os.path.isfile(os.path.join(e.path, "task.json")):
                    continue
                if any(n == _norm(e.path) or within(n, e.path) for n in named):
                    continue
                if _mtime(e.path) > cutoff and _mtime(os.path.join(e.path, "task.json")) > cutoff:
                    continue
                why = task_folder_ok(hub, e.path, pj, projects)
                if why:
                    kept.append({"what": "folder", "path": e.path, "why": why})
                    continue
                repo = (pj.get("path") or "") if pj.get("isGit") else ""
                # A checkout in it: commits only it holds keep it; work not
                # committed lists it unticked (failing to read it counts as that).
                keep, note = checkout_risk(e.path, repo)
                if keep:
                    kept.append({"what": "folder", "path": e.path, "why": keep})
                    continue
                try:
                    parts = [_part("folder", e.path, size_of(e.path, stop=stop,
                                                             until=time.monotonic() + ITEM_BUDGET_S), repo=repo)]
                except TooSlow:
                    kept.append({"what": "folder", "path": e.path, "why": TOO_SLOW})
                    continue
                repo_dir = os.path.join(e.path, "repo")
                has_git = os.path.lexists(_long(os.path.join(repo_dir, ".git")))
                if os.path.isdir(repo_dir) and not has_git and not pj.get("isGit"):
                    note = (note + "; " if note else "") + COPY_NOTE
                if repo and has_git and is_worktree_of(repo, repo_dir):
                    br = _git_out(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
                    if br and br != "HEAD" and branch_state(repo, br) == "unmerged":
                        note = (note + "; " if note else "") + f"its branch {br} is not merged into main: the branch is kept"
                    parts.insert(0, _part("worktree", repo_dir, 0, repo=repo))
                items.append(_item("orphans", f"orphan:{_norm(e.path)}", os.path.basename(e.path), parts,
                                   project=pj.get("name") or "", note=note,
                                   **({"ticked": False} if note else {})))
        repo = pj.get("path") or ""
        if pj.get("isGit") and repo and _norm(repo) not in seen_repos:
            seen_repos.add(_norm(repo))
            gone = [w for w in worktrees(repo) if w.get("prunable")]
            if gone:
                items.append(_item("orphans", f"wtprune:{_norm(repo)}",
                                   f"{pj.get('name') or repo}: {len(gone)} worktree record"
                                   f"{'' if len(gone) == 1 else 's'} whose folder is gone",
                                   [_part("worktree-records", repo, 0, count=len(gone))],
                                   project=pj.get("name") or ""))
    return items


def _housekeeping(hub: Hub, room_ids: set, live: set, cutoff: float) -> list[dict]:
    st = hub.state_dir
    items = []

    def files_in(d: Path, keep=lambda e: True):
        try:
            return [e for e in os.scandir(d) if keep(e)]
        except OSError:
            return []

    day_ago = hub.now() - DAY                 # nothing written today, whatever the days say
    parts = [_part("log", e.path, e.stat().st_size) for e in files_in(
        st / "logs", lambda e: e.is_file() and e.name != LOG_NAME and e.stat().st_mtime < day_ago
        and (_ROTATED_LOG.search(e.name) or e.stat().st_mtime < cutoff))]
    if parts:
        items.append(_item("housekeeping", "hk:logs", "Old and rotated logs", parts))

    def gone_room(e) -> bool:
        # Gone only when its record is not on disk at all (one that does not
        # parse is still a task), and only once older than the days asked.
        rid = re.sub(r"\.(json|jsonl)$", "", e.name)
        return (rid.startswith("room-") and rid not in room_ids and not room_on_disk(hub, rid)
                and _mtime(e.path) < cutoff)
    for key, label, d in (("uploads", "Uploads of tasks that are gone", st / "attachments"),
                          ("sends", "Sends of tasks that are gone", st / "sends"),
                          ("chat", "Message logs of tasks that are gone", st / "rooms" / "messages"),
                          ("chat", "Input records of tasks that are gone", st / "input-provenance")):
        parts = [_part(key, e.path, size_of(e.path), room=re.sub(r"\.(json|jsonl)$", "", e.name))
                 for e in files_in(d, gone_room)]
        if parts:
            items.append(_item("housekeeping", f"hk:{key}:{d.name}", label, parts))
    ptys = {f"{i}.bin" for i in hub.pty_ids()}
    parts = [_part("spool", e.path, e.stat().st_size) for e in files_in(
        st / "pty-spool", lambda e: e.is_file() and e.name not in ptys and e.stat().st_mtime < min(cutoff, day_ago))]
    if parts:
        items.append(_item("housekeeping", "hk:spool", "Old terminal spools", parts))
    parts = [_part("launch", e.path, size_of(e.path)) for e in files_in(
        st / "_launch", lambda e: e.stat().st_mtime < hub.now() - DAY)]
    if parts:
        items.append(_item("housekeeping", "hk:launch", "Launch files older than a day", parts))
    return items


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------

def run(hub: Hub, preview: dict, ids, *, how: str = "manual", stop: Callable[[], bool] | None = None) -> dict:
    """Delete the items of ``preview`` whose id is in ``ids``. Each is checked
    again first against the state now (a task still Done and not running, a
    PO's conversation still past, a folder still safe); what fails a check, or
    cannot be deleted, is reported and left. Returns what went and what not."""
    want = set(ids or [])
    projects = [p for p in hub.projects() if isinstance(p, dict)]
    by_pid = {p.get("id"): p for p in projects}
    rooms = {r["id"]: r for r in hub.rooms() if isinstance(r, dict) and r.get("id")}
    live = set(hub.live_rooms())
    live_sids = set(hub.live_sessions())
    po_rooms = {(p.get("poRoomId") or "").strip() for p in projects} - {""}
    seats_of = {k: set(hub.current_sessions(r)) for k, r in rooms.items()}
    current: set[str] = set(live_sids).union(*seats_of.values()) | _unread_rooms(hub, set(rooms))[2]
    cutoff = hub.now() - int(preview.get("days", DEFAULT_DAYS)) * DAY
    done, skipped, freed = [], [], 0
    todo = [it for g in preview.get("groups", []) for it in g.get("items", []) if it.get("id") in want]
    for i, it in enumerate(todo):
        if stop and stop():
            skipped.append({"id": it["id"], "why": "stopped"})
            continue
        hub.progress("Freeing space", i, len(todo))
        try:
            if it["group"] == "done":
                rid = it.get("room", "")
                r = rooms.get(rid)
                why = ("the task is gone" if not r else
                       "the task is no longer Done" if (r.get("workflow") or "").lower() != "done" else
                       "already pruned" if r.get("pruned") else
                       "a PO's room" if rid in po_rooms else
                       "its terminal is running" if rid in live else
                       "it was moved out of Done since" if _done_at(r) > cutoff else "")
                wt = next((p for p in it.get("parts", []) if p["kind"] == "worktree"), None)
                if not why and wt and dirty(wt["path"]) \
                        and (how != "manual" or it.get("ticked") is not False):
                    # Work never committed goes only when the person ticked
                    # it by hand, seeing the note; never by itself.
                    why = "its worktree has uncommitted changes"
                if why:
                    skipped.append({"id": it["id"], "label": it.get("label"), "why": why})
                    continue
                others = set().union(*(seats for k, seats in seats_of.items() if k != rid))
                n, errs = _prune_task(hub, r, by_pid, projects, it, live_sids | others, how)
            else:
                n, errs = _prune_parts(hub, it, current, projects, how, set(rooms))
        except Exception as e:                      # noqa: BLE001
            n, errs = 0, [f"{type(e).__name__}: {e}"]
        freed += n
        done.append({"id": it["id"], "label": it.get("label"), "bytes": n, "errors": errs})
    hub.progress("", 0, 0)
    return {"freed": freed, "done": done, "skipped": skipped}


def _prune_task(hub: Hub, r: dict, by_pid: dict, projects: list, it: dict, live_sids: set,
                how: str) -> tuple[int, list[str]]:
    rid = r["id"]
    label = it.get("label") or rid
    project = _project_of(r, by_pid)
    freed, errs = 0, []
    parts = {p["kind"]: p for p in it.get("parts", []) if p["kind"] not in ("conversation", "chat")}
    # Checkpoints first: they are read in the task's own folder while it is there.
    if "checkpoints" in parts:
        try:
            n = hub.forget_checkpoints(r)
            log(hub, f"{label}: checkpoint refs", parts["checkpoints"]["path"], 0, f"{n} refs", how)
        except Exception as e:                      # noqa: BLE001
            errs.append(f"checkpoints: {e}")
    wt = parts.get("worktree")
    if wt:
        repo, cwd = wt.get("repo") or "", wt["path"]
        size = size_of(cwd)
        # Only what git names as this repository's worktree, in Ensemble's folders.
        listed = is_worktree_of(repo, cwd) and _allowed_path(hub, cwd, projects)
        ok = False
        if listed:
            try:
                # git for Windows walks into a junction and empties its
                # target: the links go first, as links. One that stays
                # leaves the folder to remove() below, which never follows it.
                fslinks.unlink_links(_long(cwd))
                ok = _git(repo, "worktree", "remove", "--force", cwd, timeout=300).returncode == 0
            except (OSError, subprocess.SubprocessError) as e:
                print(f"[prune] worktree left to remove(): {cwd} ({e!r})", flush=True)
                ok = False
        # Not listed: no worktree any more, only files in the task folder,
        # which go with the folder below (under its own checks).
        if listed and (not ok or os.path.lexists(_long(cwd))):
            # git cannot remove a path past 260 characters on Windows: the
            # folder goes by hand, then git forgets the record.
            try:
                remove(cwd)
                ok = True
            except OSError as e:
                errs.append(f"worktree: {e}")
            _git_out(repo, "worktree", "prune")
        if ok:
            freed += size
            log(hub, f"{label}: worktree", cwd, size, how=how)
    br = parts.get("branch")
    if br and not errs:
        repo = br.get("repo") or ""
        if branch_state(repo, br["path"]) == "merged":
            r2 = _git(repo, "branch", "-D", br["path"])
            if r2.returncode == 0:
                log(hub, f"{label}: branch (merged into main)", br["path"], 0, how=how)
            else:
                errs.append(f"branch: {r2.stderr.strip()[:200]}")
    fo = parts.get("folder")
    if fo:
        why = task_folder_ok(hub, fo["path"], project, projects)
        if not why and unlisted_checkout(r, fo["path"]):
            why = "it holds a git checkout that git does not list"
        if why and why != "not there":
            errs.append(f"folder kept: {why}")
        elif not why:
            size = size_of(fo["path"])
            try:
                remove(fo["path"])
                freed += size
                log(hub, f"{label}: task folder", fo["path"], size, how=how)
            except OSError as e:
                errs.append(f"folder: {e}")
            if (project or {}).get("isGit") and project.get("path"):
                _git_out(project["path"], "worktree", "prune")   # a worktree the folder held
    sids_now = {sid for _a, sid in hub.room_sessions(r)}
    recent = hub.now() - RECENT_S
    for p in it.get("parts", []):
        if p["kind"] == "conversation":
            # live_sids: live terminals and every other room's current seats.
            if p.get("sid") in live_sids or p.get("sid") not in sids_now or _mtime(p["path"]) > recent:
                continue
            freed += _remove_logged(hub, f"{label}: conversation", p["path"], how, errs)
    up = parts.get("uploads")
    if up:
        freed += _remove_logged(hub, f"{label}: uploads", up["path"], how, errs)
    if "sends" in parts:
        freed += _remove_logged(hub, f"{label}: sends", parts["sends"]["path"], how, errs)
    # The chat last: the room becomes its slim record (its messages' log goes).
    before = sum(size_of(p["path"]) for p in it.get("parts", []) if p["kind"] in ("chat", "room"))
    meta = {"at": hub.now(), "freed": freed + before, "how": how,
            "kept": [k for k in ("branch",) if k not in parts and (r.get("workspace") or {}).get("branch")]}
    if hub.slim_room(rid, meta):
        for p in it.get("parts", []):
            if p["kind"] == "chat" and not p["path"].endswith(f"messages{os.sep}{rid}.jsonl") \
                    and not p["path"].endswith(f"messages/{rid}.jsonl"):
                _remove_logged(hub, f"{label}: chat record", p["path"], how, errs)
        after = sum(size_of(p["path"]) for p in it.get("parts", []) if p["kind"] in ("chat", "room"))
        freed += max(0, before - after)
        log(hub, f"{label}: chat (the task keeps a slim record)", str(hub.state_dir / "rooms" / f"{rid}.json"),
            max(0, before - after), how=how)
    else:
        errs.append("the chat could not be made a slim record")
    return freed, errs


def _remove_logged(hub: Hub, what: str, path: str, how: str, errs: list) -> int:
    size = size_of(path)
    try:
        remove(path)
    except OSError as e:
        errs.append(f"{what}: {e}")
        return 0
    log(hub, what, path, size, how=how)
    return size


def _allowed_path(hub: Hub, path: str, projects: list) -> bool:
    """Only Ensemble's own state, the agents' conversation folders and the
    project homes; never a project's folder, Documents or home itself."""
    roots = [str(hub.state_dir), str(hub.claude_dir), str(hub.codex_dir)]
    homes = [hub.project_home(p) for p in projects if hub.project_home(p)]
    if not any(within(path, x) and _norm(path) != _norm(x) for x in roots + homes):
        return False
    for p in projects:
        for f in (p.get("path") or "", hub.project_home(p) or "", hub.documents_dir(p) or ""):
            if f and within(f, path):
                return False
    return True


def _prune_parts(hub: Hub, it: dict, current: set, projects: list, how: str,
                 room_ids: set = frozenset()) -> tuple[int, list[str]]:
    freed, errs = 0, []
    label = it.get("label") or it["id"]
    recent = hub.now() - RECENT_S
    fo = next((p for p in it.get("parts", []) if p["kind"] == "folder"), None)
    if it.get("group") == "orphans" and fo:
        # Checked again, before its worktree goes: a commit only it holds
        # keeps it; uncommitted work goes only when the person ticked it by
        # hand, seeing the note, never by itself, never if new since.
        keep, note = checkout_risk(fo["path"], fo.get("repo") or "")
        if keep or (note and (how != "manual" or it.get("ticked") is not False)):
            return 0, [f"{fo['path']}: {keep or note}, kept"]
    for p in it.get("parts", []):
        kind, path = p["kind"], p.get("path") or ""
        if kind == "conversation" and (p.get("sid") in current or _mtime(path) > recent):
            errs.append(f"{p.get('sid')}: in use since the preview, kept")
            continue
        if p.get("room") and (p["room"] in room_ids or room_on_disk(hub, p["room"])):
            errs.append(f"{p['room']}: its task is back, kept")
            continue
        if kind == "conversation" and it.get("group") == "po" and path.endswith(".jsonl")                 and not hub.keep_text(p.get("agent") or "", p.get("sid") or "", path):
            errs.append(f"{p.get('sid')}: its text could not be kept, kept")
            continue
        if kind == "worktree-records":
            n = len([w for w in worktrees(path) if w.get("prunable")])
            if _git(path, "worktree", "prune").returncode == 0:
                log(hub, f"{label}", path, 0, f"{n} records", how)
            continue
        if kind == "worktree":
            repo = p.get("repo") or ""
            if repo and os.path.isdir(_long(path)):
                try:
                    fslinks.unlink_links(_long(path))     # git would empty a link's target
                    _git(repo, "worktree", "remove", "--force", path, timeout=300)
                except (OSError, subprocess.SubprocessError) as e:
                    # the folder part after it goes by remove()
                    print(f"[prune] worktree left to remove(): {path} ({e!r})", flush=True)
            continue
        if not _allowed_path(hub, path, projects):
            errs.append(f"{path}: not Ensemble's to delete")
            continue
        freed += _remove_logged(hub, f"{label}: {kind}", path, how, errs)
        if kind == "folder":
            for pj in projects:
                if pj.get("isGit") and within(path, hub.project_home(pj) or "\0"):
                    _git_out(pj.get("path") or "", "worktree", "prune")
    return freed, errs


# ---------------------------------------------------------------------------
# A preview job and the retention rule, for the hub
# ---------------------------------------------------------------------------

class Job:
    """One scan or deletion at a time, in the background; the page polls.

    Each job has its own stop flag, handed to its ``fn(stop)``: a new job never
    clears an old one's. A scan asked while a scan runs replaces it: the old
    one is stopped and waited for (``REPLACE_WAIT_S``) before the new one
    starts, so two never run side by side (#282)."""

    REPLACE_WAIT_S = 3.0

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": "", "preview": None, "result": None, "error": "", "phase": "",
                      "done": 0, "total": 0, "stopped": False}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state, default=str))

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def progress(self, phase: str, done: int = 0, total: int = 0):
        self._set(phase=phase, done=done, total=total)

    def start(self, kind: str, fn: Callable[[Callable[[], bool]], None], *, replace: bool = False) -> bool:
        """Run ``fn(stop)`` in the background. False when another job runs
        (or, with ``replace``, a job of the same kind did not stop in time)."""
        with self.lock:
            old = self._thread if self.state["running"] else None
            if old and not (replace and self.state["running"] == kind):
                return False
            old_stop = self._stop
        if old:
            old_stop.set()
            old.join(self.REPLACE_WAIT_S)
            if old.is_alive():
                return False
        with self.lock:
            if self.state["running"]:
                return False                        # another request got in first
            stop = self._stop = threading.Event()
            self.state.update(running=kind, error="", phase="", done=0, total=0, stopped=False)

            def go():
                try:
                    fn(stop.is_set)
                except Exception as e:              # noqa: BLE001
                    self._set(error=f"{type(e).__name__}: {e}")
                    print(f"[prune] {kind} failed: {e!r}", flush=True)
                finally:
                    self._set(running="", phase="", done=0, total=0, stopped=stop.is_set())
            t = self._thread = threading.Thread(target=go, name=f"prune-{kind}", daemon=True)
            t.start()
        return True

    def cancel(self):
        self._stop.set()

    def cancelled(self) -> bool:
        return self._stop.is_set()


def auto_prune(hub: Hub, days: int) -> dict:
    """The retention rule: every Done task older than ``days`` goes, by the
    same checks as a ticked preview. Returns run()'s result."""
    if days <= 0:
        return {"freed": 0, "done": [], "skipped": []}
    preview = scan(hub, days, groups=("done",))
    # Only what a preview ticks by itself: never a task with uncommitted work.
    ids = [it["id"] for g in preview["groups"] for it in g["items"] if it.get("ticked")]
    res = run(hub, preview, ids, how="auto")
    log(hub, f"auto-prune of Done tasks older than {days} days: {len(res['done'])} tasks",
        "", res["freed"], how="auto")
    return res
