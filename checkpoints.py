"""Per-turn checkpoints of a task's git workspace (ED-197).

Every time a task's owner ends a turn the hub saves the whole working tree
(tracked and untracked files, ``.gitignore`` respected) as a commit no branch
points at, under ``refs/ensemble/checkpoints/<room>/<n>``. From one, the
page shows what that turn changed and can put the task's code back exactly
as it was at the end of it; a restore first saves the state it replaces, so
Undo can return to it.

Taking a checkpoint never changes the branch, HEAD, the real index or any
file: it works in a temporary index (``GIT_INDEX_FILE``, a copy of the real
one so unchanged files are not hashed again), ``git add -A``, ``write-tree``
and ``commit-tree``. ``git status`` and ``git log`` stay as they were.

Refs (per room):

* ``.../<room>/<n>``  a turn's checkpoint (0 is the task's base);
* ``.../<room>/r<m>`` the state just before restore or undo number m.

Each commit's message body is the checkpoint's record, as JSON: kind, n,
head, branch, at, msgId, files/add/del (against the state before it), the
index tree (a second parent, so the staged state comes back too), and for a
restore point who restored to what. One ``for-each-ref`` reads them all.

Decoupled from dashboard.py like history.py: it is handed a folder and a
room id, and imports nothing of the hub.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

PREFIX = "refs/ensemble/checkpoints"
MAX_TURNS = 200            # turn checkpoints kept per task; the oldest go
MAX_RESTORES = 50          # restore points kept per task
KEEP_AFTER_DONE_S = 14 * 86400
NAME, EMAIL = "Ensemble checkpoint", "checkpoints@ensemble.local"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ROOM_RE = re.compile(r"^[\w.-]{1,120}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_LOGGED: set[tuple[str, str]] = set()


class CheckpointError(Exception):
    """A restore or undo that cannot be done; its text says why, for a person."""


def _lock(root: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(os.path.normcase(os.path.normpath(root)), threading.RLock())


def _env(index: str = "") -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1", GIT_AUTHOR_NAME=NAME, GIT_AUTHOR_EMAIL=EMAIL,
               GIT_COMMITTER_NAME=NAME, GIT_COMMITTER_EMAIL=EMAIL)
    if index:
        env["GIT_INDEX_FILE"] = index
    else:
        env["GIT_OPTIONAL_LOCKS"] = "0"    # a read never takes the agent's index lock
    return env


def _git(root: str, *args: str, index: str = "", env: dict | None = None,
         input_text: str | None = None, timeout: int = 60, check: bool = True) -> str:
    argv = ["git", "-C", root, "-c", "core.quotepath=false", "-c", "core.longpaths=true",
            "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args]
    # Bytes in and out: a text pipe on Windows would end each line sent with CRLF.
    run = subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW,
                         env=env or _env(index), cwd=root,
                         input=None if input_text is None else input_text.encode("utf-8"))
    stdout = (run.stdout or b"").decode("utf-8", errors="replace").strip()
    if run.returncode != 0:
        if check:
            raise subprocess.CalledProcessError(run.returncode, argv[3:6], stdout,
                                                (run.stderr or b"").decode("utf-8", errors="replace"))
        return ""
    return stdout


def git_root(path: str) -> str:
    """The top of the git checkout ``path`` is in, or ""."""
    if not path or not os.path.isdir(path):
        return ""
    try:
        top = _git(path, "rev-parse", "--show-toplevel", check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return os.path.normpath(top) if top else ""


def own_worktree(path: str) -> bool:
    """``path`` is the top of a linked worktree (``git worktree add``): its
    own HEAD and index, apart from the main checkout and other tasks'."""
    root = git_root(path)
    if not root or os.path.normcase(root) != os.path.normcase(os.path.normpath(path)):
        return False
    try:
        out = _git(root, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir",
                   check=False).splitlines()
    except (OSError, subprocess.SubprocessError):
        return False
    if len(out) != 2:
        return False
    a, b = (os.path.normcase(os.path.normpath(x)) for x in out)
    return a != b


def _valid_room(room: str) -> str:
    if not _ROOM_RE.match(room or "") or ".." in room:
        raise ValueError("bad room id")
    return room


def _log_once(root: str, what: str, err: Exception) -> None:
    key = (root, what)
    if key not in _LOGGED:
        _LOGGED.add(key)
        msg = getattr(err, "stderr", "") or str(err)
        print(f"[checkpoints] {what} skipped in {root}: {str(msg).strip()[:200]}", flush=True)


# ---- reading ----------------------------------------------------------------

def _records(root: str, room: str) -> list[dict]:
    """Every checkpoint and restore point of ``room`` in ``root``, oldest first."""
    out = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(tree)%00%(contents:body)%00%01",
               f"{PREFIX}/{room}/", check=False)
    recs = []
    for chunk in out.split("\x01"):
        parts = chunk.strip("\n").split("\x00")
        if len(parts) < 4 or not parts[0]:
            continue
        ref, sha, tree, body = parts[0].strip(), parts[1], parts[2], parts[3]
        try:
            meta = json.loads(body.strip() or "{}")
        except ValueError:
            continue
        if not isinstance(meta, dict):
            continue
        meta.update(ref=ref, sha=sha, tree=tree)
        recs.append(meta)
    recs.sort(key=lambda r: (int(r.get("seq") or 0), float(r.get("at") or 0)))
    return recs


def list_checkpoints(path: str, room: str) -> dict:
    """``{root, turns: [...], restores: [...]}`` for the page; turns oldest first."""
    room = _valid_room(room)
    root = git_root(path)
    if not root:
        return {"root": "", "turns": [], "restores": []}
    recs = _records(root, room)
    keep = ("n", "m", "kind", "at", "head", "branch", "msgId", "identity", "files", "add", "del",
            "by", "to", "toKind", "undo", "sha")
    turns = [{k: r[k] for k in keep if k in r} for r in recs if r.get("kind") in ("turn", "base")]
    restores = [{k: r[k] for k in keep if k in r} for r in recs if r.get("kind") == "restore"]
    return {"root": root, "turns": turns, "restores": restores}


def _current_state_sha(recs: list[dict]) -> str:
    """The commit the workspace was last known to match: the newest turn's
    checkpoint, or what the newest restore put back."""
    if not recs:
        return ""
    last = recs[-1]
    if last.get("kind") == "restore":
        return last.get("after") or last["sha"]
    return last["sha"]


def _parent_head(root: str, sha: str) -> str:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^1", check=False)


def _counts(root: str, a: str, b: str) -> tuple[int, int, int]:
    """Files changed, lines added and removed between two trees."""
    if not a or a == b:
        return 0, 0, 0
    out = _git(root, "diff-tree", "-r", "--numstat", "--no-renames", a, b, check=False)
    files = add = dele = 0
    for line in out.splitlines():
        p = line.split("\t", 2)
        if len(p) < 3:
            continue
        files += 1
        add += int(p[0]) if p[0].isdigit() else 0
        dele += int(p[1]) if p[1].isdigit() else 0
    return files, add, dele


# ---- taking one -------------------------------------------------------------

def _snapshot(root: str) -> dict:
    """The working tree and the real index as trees, made in a temporary
    index; nothing the agent sees changes. ``{tree, index, head, branch}``."""
    real = _git(root, "rev-parse", "--git-path", "index")
    real = real if os.path.isabs(real) else os.path.join(root, real)
    tmpdir = tempfile.mkdtemp(prefix="ens-cp-")
    try:
        tmp = os.path.join(tmpdir, "index")
        if os.path.isfile(real):
            # copy2 keeps the index's mtime: git trusts a cached stat only
            # for files older than the index, so a fresh mtime would hide a
            # same-size edit made in the second the index was written.
            shutil.copy2(real, tmp)
        index_tree = ""
        if os.path.isfile(tmp):
            # The staged state, as the agent left it (none while a merge has conflicts).
            index_tree = _git(root, "write-tree", index=tmp, check=False)
        # Every file or none: a file git cannot read (locked, unreadable)
        # fails the snapshot rather than leaving it out of it.
        _git(root, "add", "-A", index=tmp, timeout=300)
        tree = _git(root, "write-tree", index=tmp)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return {"tree": tree, "index": index_tree, "head": head, "branch": branch}


def _commit(root: str, snap: dict, meta: dict) -> str:
    """A commit of the snapshot: parents HEAD, then the index tree's own commit."""
    parents = []
    if snap["head"]:
        parents += ["-p", snap["head"]]
    if snap["index"]:
        ic = _git(root, "commit-tree", snap["index"], *parents, "-m", "ensemble checkpoint index")
        parents += ["-p", ic]
    msg = f"ensemble checkpoint\n\n{json.dumps(meta, sort_keys=True)}\n"
    return _git(root, "commit-tree", snap["tree"], *parents, input_text=msg)


def _index_tree(root: str, sha: str) -> str:
    """The staged tree recorded with a checkpoint, else its HEAD's tree."""
    ic = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^2", check=False)
    if ic:
        return _git(root, "rev-parse", f"{ic}^{{tree}}")
    head = _parent_head(root, sha)
    return _git(root, "rev-parse", f"{head}^{{tree}}") if head else ""


def take(path: str, room: str, *, kind: str = "turn", msg_id: str = "", identity: str = "",
         extra: dict | None = None, force: bool = False) -> dict | None:
    """Save the workspace at ``path`` as ``room``'s next checkpoint.

    Returns its record, or None when there is no git checkout, nothing changed
    since the state before (same tree and HEAD; ``force`` saves anyway), or it
    failed (logged once per folder, never raised: an agent's turn never waits
    on it)."""
    room = _valid_room(room)
    root = git_root(path)
    if not root:
        return None
    try:
        with _lock(root):
            return _take(root, room, kind, msg_id, identity, extra or {}, force)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        _log_once(root, "checkpoint", e)
        return None


def _take(root, room, kind, msg_id, identity, extra, force):
    t0 = time.monotonic()
    recs = _records(root, room)
    snap = _snapshot(root)
    prev = _current_state_sha(recs)
    prev_tree = _git(root, "rev-parse", f"{prev}^{{tree}}") if prev else ""
    if prev and not force and prev_tree == snap["tree"] and _parent_head(root, prev) == snap["head"]:
        return None
    base_tree = prev_tree
    if not prev and snap["head"]:
        base_tree = _git(root, "rev-parse", "HEAD^{tree}")
    files, add, dele = _counts(root, base_tree, snap["tree"])
    turns = [r for r in recs if r.get("kind") in ("turn", "base")]
    rests = [r for r in recs if r.get("kind") == "restore"]
    meta = {"kind": kind, "at": round(time.time(), 3), "seq": max([int(r.get("seq") or 0) for r in recs] + [0]) + 1, "head": snap["head"],
            "branch": snap["branch"], "msgId": msg_id, "identity": identity,
            "files": files, "add": add, "del": dele, "prev": prev, **extra}
    if kind == "restore":
        m = max([int(r.get("m") or 0) for r in rests] + [0]) + 1
        meta["m"] = m
        ref = f"{PREFIX}/{room}/r{m}"
    else:
        n = (max(int(r.get("n") or 0) for r in turns) + 1) if turns else 0
        if n == 0:
            meta["kind"] = "base"
        meta["n"] = n
        ref = f"{PREFIX}/{room}/{n}"
    sha = _commit(root, snap, meta)
    _git(root, "update-ref", ref, sha)
    _prune(root, turns, rests, meta["kind"])
    meta.update(ref=ref, sha=sha, tree=snap["tree"], ms=round((time.monotonic() - t0) * 1000))
    return meta


def _prune(root: str, turns: list[dict], rests: list[dict], kind: str) -> None:
    """At most MAX_TURNS turn checkpoints and MAX_RESTORES restore points,
    counting the new one (of ``kind``); the oldest go."""
    new_rest = kind == "restore"
    drop = [r["ref"] for r in turns[:max(0, len(turns) + (not new_rest) - MAX_TURNS)]]
    drop += [r["ref"] for r in rests[:max(0, len(rests) + new_rest - MAX_RESTORES)]]
    if drop:
        _git(root, "update-ref", "--stdin", input_text="".join(f"delete {r}\n" for r in drop), check=False)


# ---- restoring --------------------------------------------------------------

def _find(recs: list[dict], n=None, m=None) -> dict | None:
    for r in recs:
        if n is not None and r.get("kind") in ("turn", "base") and int(r.get("n", -1)) == int(n):
            return r
        if m is not None and r.get("kind") == "restore" and int(r.get("m", -1)) == int(m):
            return r
    return None


def preview(path: str, room: str, n: int | None = None, undo: int | None = None) -> dict:
    """What going back to checkpoint ``n`` (or undoing restore ``undo``) would
    change: ``{files: [{path, status, add, del}], add, del, commits: [{sha,
    subject}], branch}``. Reads only."""
    room = _valid_room(room)
    root = git_root(path)
    if not root:
        raise CheckpointError("This task has no git workspace.")
    recs = _records(root, room)
    target = _target(recs, n, undo)
    with _lock(root):
        snap = _snapshot(root)
    out = _git(root, "diff-tree", "-r", "--numstat", "--no-renames", snap["tree"], target["tree"], check=False)
    st = _git(root, "diff-tree", "-r", "--name-status", "--no-renames", snap["tree"], target["tree"], check=False)
    status = {}
    for line in st.splitlines():
        p = line.split("\t", 1)
        if len(p) == 2:
            status[p[1]] = p[0][:1]
    files, add, dele = [], 0, 0
    for line in out.splitlines():
        p = line.split("\t", 2)
        if len(p) < 3:
            continue
        a = int(p[0]) if p[0].isdigit() else None
        d = int(p[1]) if p[1].isdigit() else None
        add += a or 0
        dele += d or 0
        files.append({"path": p[2], "status": status.get(p[2], "M"), "add": a, "del": d})
    head = _parent_head(root, target["sha"])
    commits = []
    if snap["head"] and head != snap["head"]:
        log = _git(root, "log", "--format=%H%x1f%s", "-n", "50",
                   f"{head}..{snap['head']}" if head else snap["head"], check=False)
        for line in log.splitlines():
            sha, _, subj = line.partition("\x1f")
            commits.append({"sha": sha, "subject": subj})
    return {"root": root, "files": files, "add": add, "del": dele, "commits": commits,
            "branch": target.get("branch") or snap["branch"], "n": target.get("n"),
            "at": target.get("at"), "undo": undo}


def _target(recs: list[dict], n, undo) -> dict:
    if undo is not None:
        r = _find(recs, m=undo)
        if not r:
            raise CheckpointError("That restore can no longer be undone.")
        return r
    r = _find(recs, n=n)
    if not r:
        raise CheckpointError(f"Checkpoint {n} no longer exists.")
    return r


def restore(path: str, room: str, n: int | None = None, undo: int | None = None, by: str = "") -> dict:
    """Put the task's code back as it was at checkpoint ``n`` — or, with
    ``undo``, as it was just before restore number ``undo``. The state it
    replaces is saved first as a restore point (what Undo goes back to).

    The branch moves to the recorded HEAD (only when it is the branch
    recorded, still checked out), the working tree and the index become the
    snapshot's, and files made since are deleted; ignored files are left
    alone. The caller checks the folder is the task's own worktree and the
    agents are idle."""
    room = _valid_room(room)
    root = git_root(path)
    if not root:
        raise CheckpointError("This task has no git workspace.")
    with _lock(root):
        recs = _records(root, room)
        target = _target(recs, n, undo)
        if os.path.exists(_git(root, "rev-parse", "--git-path", "index.lock")):
            raise CheckpointError("Git is busy in this workspace (index.lock). Try again in a moment.")
        _branch_check(root, target)
        extra = {"by": by, "after": target["sha"], "to": target.get("n") if undo is None else None,
                 "toKind": "undo" if undo is not None else "checkpoint", "undo": undo}
        try:
            point = _take(root, room, "restore", "", "", extra, True)
        except (OSError, subprocess.SubprocessError) as e:
            raise CheckpointError("Could not save the current state first, so nothing was changed: "
                                  + _why(e))
        try:
            _apply(root, target)
        except (OSError, subprocess.SubprocessError) as e:
            # Put back what was there (the restore point holds it); once it is
            # back, the restore never happened and its record goes.
            try:
                _apply(root, point)
            except (OSError, subprocess.SubprocessError):
                raise CheckpointError(f"Git could not restore the files ({_why(e)}), nor put the "
                                      f"earlier state back: Undo on this restore brings it back.")
            _git(root, "update-ref", "-d", point["ref"], point["sha"], check=False)
            raise CheckpointError("Git could not restore the files, so nothing was changed: " + _why(e))
        return {"root": root, "restore": point.get("m"), "n": target.get("n"), "undo": undo,
                "at": target.get("at"), "head": _parent_head(root, target["sha"])}


def _branch_check(root: str, target: dict) -> None:
    """Restore moves only the branch the workspace is on, and only when it is
    the one recorded with the checkpoint; it never switches branch or
    deletes one."""
    now = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    was = target.get("branch") or ""
    if now != was:
        raise CheckpointError(
            f"The workspace is now on {('branch ' + now) if now else 'no branch'}, not "
            f"{('branch ' + was) if was else 'no branch'} as at that checkpoint. Nothing was changed.")
    if not _parent_head(root, target["sha"]) and _git(root, "rev-parse", "--verify", "--quiet", "HEAD",
                                                       check=False):
        raise CheckpointError("That checkpoint is from before the branch's first commit; going back "
                              "would delete the branch. Nothing was changed.")


def _why(e: Exception) -> str:
    return (getattr(e, "stderr", "") or str(e)).strip()[:300]


def _apply(root: str, rec: dict) -> None:
    sha = rec["sha"]
    head = _parent_head(root, sha)
    _branch_check(root, rec)
    # Index and files to the snapshot (tracked files it lacks go, untracked
    # ones in the way are overwritten), then the files made since go (not the
    # ignored ones), then the branch to the recorded HEAD and the index to
    # what was staged. The branch never points at the checkpoint commit.
    _git(root, "read-tree", "-u", "--reset", sha, env=_env_write(), timeout=300)
    _git(root, "clean", "-f", "-d", "-q", env=_env_write(), timeout=300)
    if head:
        _git(root, "update-ref", "-m", "ensemble: restore checkpoint", "HEAD", head, env=_env_write())
    itree = _index_tree(root, sha)
    if itree:
        _git(root, "read-tree", itree, env=_env_write())
    else:
        _git(root, "read-tree", "--empty", env=_env_write())
    _git(root, "update-index", "-q", "--refresh", env=_env_write(), check=False)


def _env_write() -> dict:
    env = _env("x")
    env.pop("GIT_INDEX_FILE", None)
    return env


# ---- housekeeping -----------------------------------------------------------

def delete_room(path: str, room: str) -> int:
    """Delete every checkpoint of ``room``; how many refs went."""
    room = _valid_room(room)
    root = git_root(path)
    if not root:
        return 0
    with _lock(root):
        refs = _git(root, "for-each-ref", "--format=%(refname)", f"{PREFIX}/{room}/", check=False).split()
        if refs:
            _git(root, "update-ref", "--stdin", input_text="".join(f"delete {r}\n" for r in refs), check=False)
        return len(refs)


def rooms_in(path: str) -> list[str]:
    """The room ids that have checkpoints in the checkout at ``path``."""
    root = git_root(path)
    if not root:
        return []
    refs = _git(root, "for-each-ref", "--format=%(refname)", f"{PREFIX}/", check=False).split()
    return sorted({r[len(PREFIX) + 1:].split("/", 1)[0] for r in refs if r.startswith(PREFIX + "/")})


def diff(path: str, room: str, n: int, file: str = "") -> dict:
    """What turn ``n`` changed: the diff from the state before it to it, for
    one file or (without ``file``) the list of files."""
    room = _valid_room(room)
    root = git_root(path)
    if not root:
        raise CheckpointError("This task has no git workspace.")
    recs = _records(root, room)
    r = _find(recs, n=n)
    if not r:
        raise CheckpointError(f"Checkpoint {n} no longer exists.")
    prev = r.get("prev") or ""
    if prev and not _git(root, "rev-parse", "--verify", "--quiet", f"{prev}^{{commit}}", check=False):
        prev = ""
    a = _git(root, "rev-parse", f"{prev}^{{tree}}") if prev else (
        _git(root, "rev-parse", f"{r['head']}^{{tree}}") if r.get("head") else
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    b = r["tree"]
    if file:
        out = _git(root, "diff", "--no-color", "--no-renames", a, b, "--", file, check=False)
        return {"root": root, "n": n, "file": file, "diff": out + ("\n" if out else "")}
    num = _git(root, "diff-tree", "-r", "--numstat", "--no-renames", a, b, check=False)
    st = _git(root, "diff-tree", "-r", "--name-status", "--no-renames", a, b, check=False)
    status = {}
    for line in st.splitlines():
        p = line.split("\t", 1)
        if len(p) == 2:
            status[p[1]] = p[0][:1]
    files = []
    for line in num.splitlines():
        p = line.split("\t", 2)
        if len(p) == 3:
            files.append({"path": p[2], "status": status.get(p[2], "M"),
                          "add": int(p[0]) if p[0].isdigit() else None,
                          "del": int(p[1]) if p[1].isdigit() else None})
    return {"root": root, "n": n, "files": files}
