"""Who a file belongs to, so a comment made on it in the file view can be
sent there (#297, CEO point P256).

``owner(path, projects, rooms)`` is pure: it reads no disk and no hub state,
so the tests give it plain dicts. The hub (dashboard.file_owner_info) gathers
the projects and rooms, resolves the path the viewer shows and adds the room
the viewer was opened from.

The order, as the spec has it:

1. the task whose own folder holds the path: its task folder, or the folder
   its agents run in (a worktree, a copy). A folder that is a project's own
   (an in-place task runs in the project's code folder) is no task's;
2. else the project whose home, Documents folder or code folder holds it,
   the deepest folder first: its PO. Several projects on one code folder all
   hold it; the oldest is the owner and the others are offered beside it;
3. else nothing owns it.
"""
from __future__ import annotations

import ntpath
import posixpath

__all__ = ["norm", "inside", "owner", "task_name", "po_name"]


def norm(p: str) -> str:
    """A path compared as Windows compares it when it looks like a Windows
    path (a drive, a backslash): case and slashes do not matter there.
    Otherwise as POSIX. Never touches the disk."""
    p = (p or "").strip()
    if not p:
        return ""
    if (len(p) > 1 and p[1] == ":") or "\\" in p:
        return ntpath.normcase(ntpath.normpath(p)).rstrip("\\") or "\\"
    out = posixpath.normpath(p)
    return out.rstrip("/") or "/"


def inside(path: str, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or under it (both from norm)."""
    if not path or not folder:
        return False
    if path == folder:
        return True
    sep = "\\" if ("\\" in folder or (len(folder) > 1 and folder[1] == ":")) else "/"
    return path.startswith(folder if folder.endswith(sep) else folder + sep)


def task_name(room: dict) -> str:
    """"#12 Its title", or the title alone, or "a task"."""
    no = room.get("no")
    title = (room.get("title") or "").strip()
    label = f"#{no}" if isinstance(no, int) and no > 0 else ""
    return (f"{label} {title}".strip() if title else label) or "a task"


def po_name(project: dict) -> str:
    return f"{(project.get('name') or project.get('id') or 'project').strip()} PO"


def _project_folders(p: dict) -> list[str]:
    return [f for f in (norm(p.get("home") or ""), norm(p.get("documentsDir") or ""),
                        norm(p.get("path") or "")) if f]


def owner(path: str, projects: list[dict], rooms: list[dict]) -> dict:
    """Who owns ``path``.

    ``projects``: {id, name, path (code), home, documentsDir (absolute),
    poRoomId, createdAt}. ``rooms``: {id, no, title, taskDir, cwd, projectId,
    updatedAt}. Returns {"owner": entry | None, "others": [entry...]}, an
    entry being {room, name, kind: "task" | "po", projectId}. ``others`` are
    the POs that also hold the path (a shared code folder) and, for a task,
    its project's PO."""
    p = norm(path)
    if not p:
        return {"owner": None, "others": []}
    by_id = {pr.get("id"): pr for pr in projects}
    po_rooms = {pr.get("poRoomId") for pr in projects if pr.get("poRoomId")}
    own = set()
    for pr in projects:
        own.update(_project_folders(pr))

    # 1. A task's own folder; the deepest wins, then the latest touched.
    best, best_rank = None, None
    for r in rooms:
        rid = r.get("id") or ""
        if not rid or rid in po_rooms:
            continue
        for f in {norm(r.get("taskDir") or ""), norm(r.get("cwd") or "")}:
            if not f or any(inside(o, f) for o in own):
                continue   # a project's folder (or one holding it) is no task's
            if inside(p, f):
                rank = (-len(f), -(r.get("updatedAt") or 0))
                if best_rank is None or rank < best_rank:
                    best, best_rank = r, rank
    if best is not None:
        pid = best.get("projectId") or ""
        entry = {"room": best["id"], "name": task_name(best), "kind": "task", "projectId": pid}
        pr = by_id.get(pid)
        others = ([{"room": pr["poRoomId"], "name": po_name(pr), "kind": "po", "projectId": pid}]
                  if pr and pr.get("poRoomId") else [])
        return {"owner": entry, "others": others}

    # 2. A project's folder; the deepest wins, ties (one code folder) oldest first.
    held = []
    for pr in projects:
        depth = max((len(f) for f in _project_folders(pr) if inside(p, f)), default=-1)
        if depth >= 0:
            held.append((-depth, pr.get("createdAt") or 0, pr.get("id") or "", pr))
    held.sort(key=lambda t: t[:3])
    if not held:
        return {"owner": None, "others": []}
    top = held[0][0]
    tied = [t[3] for t in held if t[0] == top and t[3].get("poRoomId")]
    if not tied:
        # The deepest project has no PO: a shallower one's PO still is someone.
        tied = [t[3] for t in held if t[3].get("poRoomId")][:1]
    entries = [{"room": pr["poRoomId"], "name": po_name(pr), "kind": "po", "projectId": pr.get("id") or ""}
               for pr in tied]
    if not entries:
        return {"owner": None, "others": []}
    return {"owner": entries[0], "others": entries[1:]}
