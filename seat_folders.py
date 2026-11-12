"""A task's agents run in the task's own folder, and nowhere else (ED-344).

A seat's folder (``part["cwd"]``, else the room's ``cwd``) can be missing: a
task moved from another machine, a worktree removed by hand. The hub used to
start the agent in its own home folder instead (``~/cs``) and record that as
the seat's folder, so every later start went there too, and ``claude
--resume`` looked for the conversation in the wrong place.

Now, before any launch or resume (``launch_folder``):

* the folder is there: the agent starts in it;
* it is the worktree of a ``worktree`` task whose branch is still in the
  project's repository: the worktree is made again there (``git worktree
  prune`` + ``git worktree add <path> <branch>``), the task's chat says so,
  and the agent starts in it;
* it is the hub's own per-agent scratch folder of a task (a fresh seat of an
  ``empty`` task): made, as at a first launch;
* anything else: the start is refused (``MissingFolderError``) in words that
  name the folder and what to do, and the task is marked for the person
  (``folderMissing``, read by attention). Nothing starts in another folder.

A Claude seat resumes only in the folder its conversation was recorded in
(Claude keeps a conversation under its folder's name): ``resume_folder_check``
refuses when the conversation is on disk for another folder only.

``repair_rooms`` puts back the seats an older hub moved to its fallback
folder: a seat of a worktree task whose folder is outside the task's own
folder gets the task's folder again (at hub start, and ``tools/
repair_seat_folders.py`` once).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

_d = None  # the dashboard module, set by bind()

SENDER = "ensemble"


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def _same(a: str, b: str) -> bool:
    return bool(a and b) and _norm(a) == _norm(b)


def _inside(child: str, parent: str) -> bool:
    """``child`` is ``parent`` or a folder under it."""
    if not child or not parent:
        return False
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _is_worktree_room(room: dict) -> bool:
    return (room.get("workspace") or {}).get("mode") == "worktree"


def _empty_dir(path: str) -> bool:
    try:
        return os.path.isdir(path) and not os.listdir(path)
    except OSError:
        return False


def _project_path(room: dict) -> str:
    pid = _d._room_project_id(room.get("id", ""), room)
    proj = _d.find_project(pid) if pid else None
    return (proj or {}).get("path") or ""


def _git(root: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return _d._run(["git", "-C", root, *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=timeout)


# ---------------------------------------------------------------------------
# Recreating a worktree
# ---------------------------------------------------------------------------

def recreate_worktree(room: dict) -> tuple[bool, str]:
    """Make the worktree of a ``worktree`` task again at its recorded folder,
    on its recorded branch. (ok, why not)."""
    path = (room.get("cwd") or "").strip()
    ws = room.get("workspace") or {}
    branch = (ws.get("branch") or "").strip()
    if not _is_worktree_room(room):
        return False, "the task is not a worktree task"
    if not path:
        return False, "the task records no folder"
    if not branch:
        return False, "the task records no branch"
    repo = _project_path(room)
    if not repo or not os.path.isdir(repo):
        return False, f"its project's repository {repo or '(none)'} is not there"
    if not _d._branch_exists(repo, branch):
        return False, f"its branch {branch} is not in the project's repository {repo}"
    try:
        _git(repo, "worktree", "prune")
        out = _git(repo, "worktree", "add", path, branch)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"git worktree add failed: {e}"
    if out.returncode != 0:
        return False, f"git worktree add failed: {(out.stderr or out.stdout or '').strip()[:300]}"
    if not os.path.isdir(path):
        return False, "git worktree add made no folder"
    return True, ""


# ---------------------------------------------------------------------------
# The mark the person sees, and the hub's note
# ---------------------------------------------------------------------------

def _mark(room: dict, ident: str, path: str, reason: str, kind: str = "missing") -> None:
    mark = {"path": path, "identity": ident, "reason": reason, "kind": kind,
            "since": time.time()}
    room["folderMissing"] = mark
    rid = room.get("id", "")
    if not rid:
        return
    try:
        # updatedAt: a stopped task marked now is not one finished long ago.
        _d.chatroom.patch_room(rid, folderMissing=mark, updatedAt=time.time())
    except Exception as e:      # noqa: BLE001 — the refusal itself stands
        print(f"[folder] {rid}: mark not written: {str(e)[:200]}", flush=True)


def clear_mark(room: dict) -> None:
    if not room.get("folderMissing"):
        return
    room.pop("folderMissing", None)
    rid = room.get("id", "")
    if rid:
        try:
            _d.chatroom.patch_room(rid, folderMissing=None)
        except Exception:       # noqa: BLE001
            pass


def _note(room: dict, text: str) -> None:
    """A hub line in the task's chat, also on the caller's copy of the room so
    a later write of that copy keeps it."""
    rid = room.get("id", "")
    if not rid:
        return
    try:
        msg = _d.chatroom.post_notice(rid, SENDER, text, {"noticeKind": "folder"})
    except Exception as e:      # noqa: BLE001
        print(f"[folder] {rid}: note not posted: {str(e)[:200]}", flush=True)
        return
    if msg is not None:
        msgs = room.setdefault("messages", [])
        if not any(m.get("id") == msg.get("id") for m in msgs):
            msgs.append(msg)


def refusal_text(room: dict, ident: str, path: str, why: str = "") -> str:
    who = f"{ident}'s folder" if ident else "The task's folder"
    because = f" ({why})" if why else ""
    return (f"{who} {path} is missing{because}: recreate it, or move the task to a folder "
            f"that exists, then start it again. The hub does not start an agent in "
            f"another folder.")


def refuse(room: dict, ident: str, path: str, text: str, kind: str = "missing"):
    _mark(room, ident, path, text, kind)
    print(f"[folder] {room.get('id', '')}/{ident}: start refused: {text}", flush=True)
    raise _d.MissingFolderError(text)


# ---------------------------------------------------------------------------
# The check before every launch
# ---------------------------------------------------------------------------

def launch_folder(room: dict, ident: str, cwd: str, scratch: str = "") -> str:
    """The folder to start seat ``ident`` in: ``cwd`` itself, made again when
    it is the task's missing worktree, or made when it is ``scratch`` (the
    hub's own per-agent folder of a task whose folder is there). Raises
    MissingFolderError otherwise; the task is then marked."""
    room_cwd = (room.get("cwd") or "").strip()
    worktree_path = _is_worktree_room(room) and _inside(cwd, room_cwd)
    # An empty folder at a worktree's place: an older hub made it for an
    # agent that found the worktree gone. git adds a worktree into it.
    if os.path.isdir(cwd) and not (worktree_path and _empty_dir(room_cwd)):
        clear_mark(room)
        return cwd
    if worktree_path:
        ok, why = recreate_worktree(room)
        if ok and os.path.isdir(cwd):
            branch = (room.get("workspace") or {}).get("branch", "")
            print(f"[folder] {room.get('id', '')}: worktree {room_cwd} made again on {branch}",
                  flush=True)
            _note(room, f"The task's folder `{room_cwd}` was missing: the hub made its "
                        f"worktree again there, on its branch `{branch}`.")
            clear_mark(room)
            return cwd
        refuse(room, ident, cwd, refusal_text(room, ident, cwd, why or "not made again"))
    if scratch and _same(cwd, scratch) and os.path.isdir(os.path.dirname(os.path.normpath(cwd))):
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            pass
        if os.path.isdir(cwd):
            clear_mark(room)
            return cwd
    why = ("the task is not a worktree task, so the hub cannot make it again"
           if room_cwd and _inside(cwd, room_cwd) and not _is_worktree_room(room) else "")
    refuse(room, ident, cwd, refusal_text(room, ident, cwd, why))
    return cwd  # not reached


# ---------------------------------------------------------------------------
# A Claude conversation resumes in its own folder
# ---------------------------------------------------------------------------

_SLUG_MAX = 200     # Claude shortens a longer folder name (and adds a hash)


def _slug_matches(name: str, folder: str) -> bool:
    for p in {folder, os.path.realpath(folder)}:
        slug = _d.claude_project_slug(p)
        if name == slug or (len(slug) > _SLUG_MAX and name.startswith(slug[:_SLUG_MAX])):
            return True
    return False


def _transcripts(sid: str) -> list[Path]:
    roots = [Path(_d.PROJ_DIR)]
    try:
        other = _d.lost_sessions.claude_config_dir() / "projects"
        if other.resolve() != roots[0].resolve():
            roots.append(other)
    except Exception:           # noqa: BLE001
        pass
    out: list[Path] = []
    for root in roots:
        try:
            out += sorted(root.glob(f"*/{sid}.jsonl"))
        except OSError:
            pass
    return out


def _recorded_cwd(path: Path) -> str:
    """The folder a transcript says its conversation ran in ('' unknown)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 200:
                    break
                if '"cwd"' not in line:
                    continue
                try:
                    cwd = (json.loads(line) or {}).get("cwd")
                except ValueError:
                    continue
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        pass
    return ""


def resume_folder_check(room: dict, ident: str, sid: str, cwd: str) -> None:
    """Refuse a Claude resume in ``cwd`` when conversation ``sid`` is on disk
    only under another folder: ``claude --resume`` would not find it there.
    Nothing is said when it is nowhere on disk (the lost-session path)."""
    if not sid or any(c in sid for c in "*?[]/\\"):
        return
    hits = _transcripts(sid)
    if not hits or any(_slug_matches(h.parent.name, cwd) for h in hits):
        return
    where = _recorded_cwd(hits[0]) or f"the folder Claude names {hits[0].parent.name}"
    text = (f"{ident}'s conversation {sid} was recorded in {where}, not in {cwd} where the "
            f"hub would resume it: Claude finds a conversation only in the folder it was "
            f"recorded in. Move the seat back to that folder, or give it a fresh session, "
            f"then start the task again.")
    refuse(room, ident, cwd, text, kind="elsewhere")


# ---------------------------------------------------------------------------
# Repair: seats an older hub moved to its fallback folder
# ---------------------------------------------------------------------------

def damaged_seats(room: dict) -> list[dict]:
    """The agent seats of a worktree task whose folder is outside the task's
    own folder (its task folder, else its worktree): an older hub's
    fallback (``~/cs``). A seat in another folder of the task (a second
    checkout someone made, ``repo-linux``) was moved there on purpose."""
    if not _is_worktree_room(room):
        return []
    room_cwd = (room.get("cwd") or "").strip()
    if not room_cwd:
        return []
    own = (room.get("taskDir") or (room.get("workspace") or {}).get("taskDir")
           or "").strip() or room_cwd
    out = []
    for p in room.get("participants") or []:
        if p.get("kind") != "agent":
            continue
        pc = (p.get("cwd") or "").strip()
        if pc and not _same(pc, room_cwd) and not _inside(pc, own) and not _inside(pc, room_cwd):
            out.append(p)
    return out


def repair_room_file(path: Path, backup_dir: Path, is_live=None) -> list[dict]:
    """Repair one room file in place (backup first). Returns what changed:
    [{room, no, identity, was, now}], and [{..., skipped}] for a seat running
    now (its record stays as its process is; it is put right once stopped)."""
    try:
        room = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    seats = damaged_seats(room)
    if not seats:
        return []
    out, changed = [], False
    for p in seats:
        row = {"room": room.get("id", path.stem), "no": room.get("no"),
               "identity": p.get("identity", ""), "was": p.get("cwd", ""), "now": room["cwd"]}
        if is_live is not None and is_live(room, p):
            out.append({**row, "skipped": "running now"})
            continue
        p["cwd"] = room["cwd"]
        changed = True
        out.append(row)
    if changed:
        room["_revision"] = uuid.uuid4().hex     # chatroom's index reads it again
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, backup_dir / f"{path.stem}.{stamp}.json")
        tmp = path.with_name(path.name + ".seatfix.tmp")
        tmp.write_text(json.dumps(room, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    return out


def repair_rooms(rooms_dir: Path, backup_dir: Path, is_live=None) -> list[dict]:
    """``repair_room_file`` for every room. Idempotent: a repaired seat is
    the room's folder, which is not damaged."""
    out: list[dict] = []
    for f in sorted(Path(rooms_dir).glob("room-*.json")):
        out += repair_room_file(f, backup_dir, is_live)
    return out


def repair_at_start() -> list[dict]:
    """The hub's start: under the room lock, through chatroom (its cache sees
    the change), seats running now left as they are."""
    cr = _d.chatroom
    out: list[dict] = []
    for entry in cr.room_index():
        room0 = entry.get("room") or {}
        if not damaged_seats(room0):
            continue
        rid = room0.get("id", "")
        with cr._LOCK:
            room = cr._load(rid)
            if room is None:
                continue
            changed = False
            for p in damaged_seats(room):
                row = {"room": rid, "no": room.get("no"), "identity": p.get("identity", ""),
                       "was": p.get("cwd", ""), "now": room["cwd"]}
                if _d._pty_alive(p.get("ptyId")):
                    out.append({**row, "skipped": "running now"})
                    continue
                if not changed and not _backup(rid):
                    out.append({**row, "skipped": "no backup could be made"})
                    break
                p["cwd"] = room["cwd"]
                changed = True
                out.append(row)
            if changed:
                cr._write(room)
    return out


def _backup(rid: str) -> bool:
    try:
        src = Path(_d.chatroom.ROOMS_DIR) / f"{rid}.json"
        dst = Path(_d.DASHBOARD_DIR) / "backups" / "seat-folders"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst / f"{rid}.{time.strftime('%Y%m%d-%H%M%S')}.json")
        return True
    except OSError as e:
        print(f"[folder] {rid}: no backup made, not repaired: {e}", flush=True)
        return False
