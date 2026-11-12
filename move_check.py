"""The move check (#331): what a copy of the state and the projects root to
another machine (or another system: Windows to Linux, a Mac to Windows) left
pointing at the old one, repaired where that is safe and listed where not.

Run at hub start (``dashboard.main``, before the rooms that were running come
back), from Settings (``POST /api/move-check``) and by a project's PO
(``ensemble_move_check``). Four kinds:

``code``
    A project's code folder (``code`` in its project.json, ``path`` in
    projects.json) that is not there. Repaired when its old form maps to a
    folder that is there and is a git repository (``map_old_path``); listed
    otherwise.
``taskFolder``
    A task's folder, working folder or a seat's folder that is not there and
    maps the same way to one that is: the room record is rewritten.
``worktree``
    A task's worktree whose ``.git`` file names a git folder that is not there
    (git fails in it). Its files are hashed into a tree in the project's
    repository. When that tree is a commit's of the repository (nothing the
    repository does not have), the folder is put aside as
    ``repo.moved-<date>`` and the worktree is made again on the task's branch
    at that commit. Otherwise the files may be the only copy of the task's
    work: they stay where they are, their difference from the main branch is
    saved as ``moved-<date>.patch`` in the task folder, committed on a new
    branch when the task's own branch is still there, and listed.
``transcript``
    A Claude seat whose conversation is not in the folder Claude reads for the
    seat's working folder (``~/.claude/projects/<slug>``) but is in another
    one (the old machine's slug): copied, never moved. A Codex seat resumes by
    its id wherever it runs; one whose rollout names a folder that is gone is
    only counted (``codexOldCwd``).

Done and archived tasks are not looked at. A running task's worktree is not
touched (it is listed until it stops). Every repair is logged to
``logs/move-check.log``; the last result is kept in ``move-check.json``.
"""
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()
_LOCK = threading.Lock()
KINDS = ("code", "taskFolder", "worktree", "transcript")
NOTICE_KIND = "move-check"
# Commits looked at for a tree equal to a worktree's files.
TREE_SCAN_LIMIT = 50000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


# ---------- paths from another machine (pure) ----------

_DRIVE = re.compile(r"^[A-Za-z]:$")


def path_parts(old: str) -> list[str] | None:
    """The folders of an absolute path written on any system ("C:\\a\\b",
    "C:/a/b", "\\\\?\\C:\\a", "/a/b"), without the drive or root; None for a
    relative path."""
    s = (old or "").strip()
    if s.startswith("\\\\?\\") or s.startswith("//?/"):
        s = s[4:]
    segs = [x for x in re.split(r"[\\/]+", s)]
    if not segs:
        return None
    if _DRIVE.match(segs[0]):
        rest = segs[1:]
    elif s.startswith(("/", "\\")):
        rest = segs[1:]
    else:
        return None
    return [x for x in rest if x not in ("", ".")]


def map_old_path(old: str, home: Path, root: Path) -> list[Path]:
    """Where a folder of the old machine is on this one, by the rules the
    projects root already follows (it is self-describing: a project's home is
    the folder its project.json is in, wherever the root was copied to):

      1. anything under a folder named like the projects root
         (``C:\\Users\\u\\EnsembleProjects\\X\\y``) → ``<root>/X/y``;
      2. anything under a home folder (``C:\\Users\\u\\rest``,
         ``/Users/u/rest``, ``/home/u/rest``) → ``<home>/rest``.

    The candidates in that order, each different from ``old``; a path on
    another drive outside a home (``D:\\Code\\x``) has none."""
    parts = path_parts(old)
    if not parts:
        return []
    out: list[Path] = []
    low = [p.casefold() for p in parts]
    rname = root.name.casefold()
    if rname in low:
        i = len(low) - 1 - low[::-1].index(rname)
        out.append(root.joinpath(*parts[i + 1:]))
    if len(parts) >= 2 and low[0] in ("users", "home"):
        out.append(home.joinpath(*parts[2:]))
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.normpath(str(p)))
        if k in seen or k == os.path.normcase(os.path.normpath(old)):
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def mapped_dir(old: str, home: Path, root: Path, git: bool = False) -> Path | None:
    """The first candidate of ``map_old_path`` that is a folder (and a git
    repository, with ``git``)."""
    for p in map_old_path(old, home, root):
        if p.is_dir() and (not git or is_repo(p)):
            return p
    return None


def is_repo(p: Path) -> bool:
    """Git can run in ``p`` and ``p`` is its repository's top (a ``.git`` that
    is empty or names a folder that is gone is not one)."""
    if not (p / ".git").exists():
        return False
    try:
        top = _ok(_git(str(p), "rev-parse", "--show-toplevel", timeout=30))
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(top) and os.path.normcase(os.path.realpath(top)) == \
        os.path.normcase(os.path.realpath(str(p)))


def worktree_gitdir(wt: Path) -> tuple[bool, str]:
    """(is a worktree, the git folder its .git file names). A folder whose
    .git is a folder is a repository of its own: (False, "")."""
    g = wt / ".git"
    if not g.is_file():
        return False, ""
    try:
        text = g.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True, ""
    m = re.search(r"^gitdir:\s*(.+?)\s*$", text, re.M)
    if not m:
        return True, ""
    gd = m.group(1)
    if path_parts(gd) is None:          # relative to the worktree
        gd = str((wt / gd).resolve())
    return True, gd


def gitdir_broken(wt: Path) -> str:
    """The git folder a worktree names when it is not there, else ""."""
    is_wt, gd = worktree_gitdir(wt)
    if not is_wt:
        return ""
    if not gd:
        return "(unreadable .git file)"
    # A Windows path on another system never "is" a folder, whatever its shape.
    if _DRIVE.match((re.split(r"[\\/]", gd) or [""])[0]) and os.name != "nt":
        return gd
    return "" if os.path.isdir(gd) else gd


def claude_slug(path: str) -> str:
    """Claude Code's folder for a working folder (dashboard.claude_project_slug)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def claude_dir_for(proj_dir: Path, cwd: str) -> Path | None:
    """The folder Claude reads a seat's conversations from. Claude cuts a
    slug longer than 200 characters and adds a hash of its own: such a folder
    is used only when exactly one with that start holds a conversation that
    says it ran in ``cwd``; else None (the folder cannot be named safely)."""
    slug = claude_slug(cwd)
    if len(slug) <= 200:
        return proj_dir / slug
    hits = [p for p in proj_dir.glob(glob_escape(slug[:200]) + "-*")
            if p.is_dir() and _ran_in(p, cwd)]
    return hits[0] if len(hits) == 1 else None


def _ran_in(folder: Path, cwd: str, files: int = 5, lines: int = 50) -> bool:
    """A conversation in ``folder`` records ``cwd`` as its working folder."""
    want = os.path.normcase(os.path.normpath(cwd))
    for f in sorted(folder.glob("*.jsonl"))[:files]:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for _, line in zip(range(lines), fh):
                    try:
                        got = json.loads(line).get("cwd")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(got, str) and got:
                        return os.path.normcase(os.path.normpath(got)) == want
        except OSError:
            continue
    return False


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)


# ---------- git ----------

def _git(repo: str, *args, env=None, timeout=120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env,
                          creationflags=_NO_WINDOW)


def _ok(r: subprocess.CompletedProcess) -> str:
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def main_ref(repo: str) -> str:
    """The repository's main branch: origin's HEAD, else main/master, else HEAD."""
    sym = _ok(_git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"))
    if sym:
        return sym
    for name in ("main", "master"):
        if _ok(_git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + name)):
            return "refs/heads/" + name
    return "HEAD"


def branch_tip(repo: str, branch: str) -> str:
    """The commit of ``branch`` (local, else origin's), or ""."""
    if not branch:
        return ""
    for ref in ("refs/heads/" + branch, "refs/remotes/origin/" + branch):
        sha = _ok(_git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}"))
        if sha:
            return sha
    return ""


def files_tree(repo: str, wt: Path, seed: str = "", ignore_mode: bool = False
               ) -> tuple[str, list[str]]:
    """A worktree's files (what git would commit: its .gitignore applies) as
    a tree in ``repo``'s object store, and the ignored files and folders it
    has besides (no commit holds those). Line ends are compared as committed
    (a checkout made with autocrlf on Windows has CRLF on disk). With
    ``ignore_mode`` (a folder copied from Windows, whose files all carry the
    exec bit) file modes are taken from ``seed`` (a commit) where it has the
    file, so the copy's modes are no change."""
    fd, index = tempfile.mkstemp(prefix="move-check-index-")
    os.close(fd)
    os.unlink(index)
    env = {**os.environ, "GIT_INDEX_FILE": index, "GIT_WORK_TREE": str(wt)}
    env.pop("GIT_DIR", None)
    try:
        base = ["-c", "core.autocrlf=input", "-c", "core.safecrlf=false",
                "--work-tree", str(wt)]
        if seed:
            r = _git(repo, "read-tree", seed, env=env, timeout=300)
            if r.returncode != 0:
                raise RuntimeError("git read-tree: " + (r.stderr or "").strip()[:200])
        mode = ["-c", f"core.fileMode={'false' if ignore_mode else 'true'}"]
        r = _git(repo, *base, *mode, "add", "-A", "--", ".", env=env, timeout=600)
        if r.returncode != 0:
            raise RuntimeError("git add: " + (r.stderr or "").strip()[:200])
        r = _git(repo, *base, "write-tree", env=env)
        if r.returncode != 0:
            raise RuntimeError("git write-tree: " + (r.stderr or "").strip()[:200])
        tree = r.stdout.strip()
        r = _git(repo, *base, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
                 "--directory", env=env, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("git ls-files: " + (r.stderr or "").strip()[:200])
        ignored = [x.rstrip("/") for x in (r.stdout or "").split("\0") if x.strip("/")]
        return tree, ignored
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass


_TREES: dict = {}


def commit_with_tree(repo: str, tree: str, prefer: str = "", main: str = "") -> str:
    """A commit whose tree is ``tree`` ("" when none), only in the task's own
    history: ``prefer`` (the task branch's tip) first, then a commit the task
    branch or the main branch ``main`` reaches. Another branch's commit is no
    proof the files are the task's."""
    if prefer and _ok(_git(repo, "rev-parse", prefer + "^{tree}")) == tree:
        return prefer
    refs = [x for x in (prefer, main) if x]
    if not refs:
        return ""
    heads = " ".join(_ok(_git(repo, "rev-parse", r)) for r in refs)
    key = (os.path.normcase(repo), heads)
    idx = _TREES.get(key)
    if idx is None:
        r = _git(repo, "log", *refs, f"-n{TREE_SCAN_LIMIT}", "--format=%H %T", "--",
                 timeout=300)
        idx = {}
        for line in (r.stdout or "").splitlines():
            c, _, t = line.partition(" ")
            idx.setdefault(t, c)
        if len(_TREES) > 64:
            _TREES.clear()
        _TREES[key] = idx
    return idx.get(tree, "")


def base_at(repo: str, ref: str, when: float) -> str:
    """The main branch's commit when the task was made (its first-parent
    history at that time), else its tip."""
    if when:
        sha = _ok(_git(repo, "rev-list", "-1", "--first-parent",
                       f"--before={int(when)}", ref))
        if sha:
            return sha
    return _ok(_git(repo, "rev-parse", ref + "^{commit}"))


# ---------- the state ----------

def _state_path() -> Path:
    return _d.DASHBOARD_DIR / "move-check.json"


def read_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [move] {msg}"
    print(f"[move] {msg}", flush=True)
    try:
        p = _d.DASHBOARD_DIR / "logs" / "move-check.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _aside(p: Path, tag: str) -> Path:
    """``<p>.<tag>``, or ``-2``, ``-3``… when that is taken."""
    cand, n = p.with_name(f"{p.name}.{tag}"), 2
    while cand.exists():
        cand = p.with_name(f"{p.name}.{tag}-{n}")
        n += 1
    return cand


# ---------- the check ----------

class _Run:
    def __init__(self, repair: bool):
        self.repair = repair
        self.found = {k: 0 for k in KINDS}
        self.found["codexOldCwd"] = 0
        self.repaired: list[dict] = []
        self.open: list[dict] = []
        self.home = Path(_d.HOME)
        self.root = Path(_d.PROJECTS_ROOT)
        self.date = time.strftime("%Y%m%d")
        self.cache = (read_state().get("worktrees") or {})
        self.worktrees: dict = {}

    def fixed(self, kind: str, what: str, detail: str, **extra) -> None:
        self.repaired.append({"kind": kind, "what": what, "detail": detail, **extra})
        log(f"repaired {kind}: {what}: {detail}")

    def left(self, kind: str, what: str, detail: str, **extra) -> None:
        self.open.append({"kind": kind, "what": what, "detail": detail, **extra})
        log(f"not repaired {kind}: {what}: {detail}")


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _check_code(run: _Run) -> None:
    root_ids = set()
    if run.root.is_dir():
        for d in sorted(run.root.iterdir()):
            pj = d / "project.json"
            if not pj.is_file():
                continue
            try:
                meta = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or not meta.get("id"):
                continue
            root_ids.add(meta["id"])
            code = (meta.get("code") or "").strip()
            if not code or os.path.isdir(code):
                continue
            name = meta.get("name") or d.name
            run.found["code"] += 1
            new = mapped_dir(code, run.home, run.root, git=True)
            if new is None:
                run.left("code", name, f"code folder {code} is not on this machine",
                         projectId=meta["id"])
                continue
            if run.repair:
                meta["code"] = str(new)
                try:
                    _write_json(pj, meta)
                except OSError as e:
                    run.left("code", name, f"{code} → {new}: could not write project.json: {e}",
                             projectId=meta["id"])
                    continue
                run.fixed("code", name, f"{code} → {new}", projectId=meta["id"])
            else:
                run.left("code", name, f"{code} → {new} (repairable)", projectId=meta["id"])
    try:
        projects = json.loads(Path(_d.PROJECTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        projects = None
    if isinstance(projects, list):
        changed = False
        for p in projects:
            if not isinstance(p, dict) or p.get("id") in root_ids:
                continue
            path = (p.get("path") or "").strip()
            if not path or os.path.isdir(path):
                continue
            run.found["code"] += 1
            name = p.get("name") or path
            new = mapped_dir(path, run.home, run.root, git=bool(p.get("isGit")))
            if new is None:
                run.left("code", name, f"code folder {path} is not on this machine",
                         projectId=p.get("id", ""))
                continue
            if run.repair:
                p["path"] = str(new)
                home = (p.get("home") or "").strip()
                if home and not os.path.isdir(home):
                    nh = mapped_dir(home, run.home, run.root)
                    if nh is not None:
                        p["home"] = str(nh)
                changed = True
                run.fixed("code", name, f"{path} → {new}", projectId=p.get("id", ""))
            else:
                run.left("code", name, f"{path} → {new} (repairable)", projectId=p.get("id", ""))
        if changed:
            _d.save_projects(projects)
    with _d._PROJECTS_CACHE_LOCK:
        _d._PROJECTS_CACHE.clear()


def _rooms_to_check() -> list[dict]:
    archived = _d.load_archived()
    out = []
    for r in _d.chatroom.list_rooms():
        if r.get("id") in archived:
            continue
        if _d.normalize_workflow(r.get("workflow")) == "done":
            continue
        full = _d.chatroom.get_room(r["id"], public=False)
        if full:
            out.append(full)
    return out


def _label(room: dict) -> str:
    try:
        return _d.task_label(room) or room.get("title") or room.get("id", "")
    except Exception:       # noqa: BLE001
        return room.get("title") or room.get("id", "")


def _check_task_folders(run: _Run, room: dict) -> dict:
    """Rewrite a room's folders that are gone and map to ones that are."""
    fields, label = {}, _label(room)
    for key in ("cwd", "taskDir"):
        v = (room.get(key) or "").strip()
        if v and not os.path.isdir(v):
            new = mapped_dir(v, run.home, run.root)
            if new is not None:
                fields[key] = str(new)
    ws = dict(room.get("workspace") or {})
    v = (ws.get("taskDir") or "").strip()
    if v and not os.path.isdir(v):
        new = mapped_dir(v, run.home, run.root)
        if new is not None:
            ws["taskDir"] = str(new)
            fields["workspace"] = ws
    parts, pchanged = [dict(p) for p in room.get("participants") or []], False
    for p in parts:
        v = (p.get("cwd") or "").strip()
        if v and not os.path.isdir(v):
            new = mapped_dir(v, run.home, run.root)
            if new is not None:
                p["cwd"] = str(new)
                pchanged = True
    if pchanged:
        fields["participants"] = parts
    if not fields:
        return room
    run.found["taskFolder"] += 1
    detail = ", ".join(sorted(fields))
    if not run.repair:
        run.left("taskFolder", label, f"{detail} (repairable)", roomId=room["id"])
        return room
    _d.chatroom.patch_room(room["id"], **fields)
    run.fixed("taskFolder", label, f"{detail} moved to this machine's folders", roomId=room["id"])
    return {**room, **fields}


def _project_of(room: dict, projects: dict) -> dict | None:
    try:
        pid = _d.ensemble_tools._project_of_room(room)
    except Exception:       # noqa: BLE001
        pid = room.get("projectId") or ""
    return projects.get(pid)


def _check_worktree(run: _Run, room: dict, projects: dict) -> None:
    ws = room.get("workspace") or {}
    if ws.get("mode") != "worktree":
        return
    cwd = (room.get("cwd") or "").strip()
    if not cwd or not os.path.isdir(cwd):
        return
    wt = Path(cwd)
    gd = gitdir_broken(wt)
    if not gd:
        return
    run.found["worktree"] += 1
    label, rid = _label(room), room["id"]
    project = _project_of(room, projects) or {}
    repo = project.get("path") or ""
    if not repo or not is_repo(Path(repo)):
        run.left("worktree", label, f"git cannot run in {wt} (its git folder {gd} is gone), "
                 f"and the project's code folder is not a repository on this machine",
                 roomId=rid)
        return
    branch = (ws.get("branch") or "").strip()
    key = os.path.normcase(str(wt))
    if run.repair and _room_working(room):
        run.left("worktree", label, f"git cannot run in {wt} (its git folder {gd} is gone); "
                 f"an agent of the task is running, so it is looked at again when it stops",
                 roomId=rid)
        return
    tip = branch_tip(repo, branch)
    ref = main_ref(repo)
    # Files copied from Windows all carry the exec bit: their modes are no change.
    from_windows = bool(_DRIVE.match((re.split(r"[\\/]", gd) or [""])[0]))
    try:
        tree, ignored = files_tree(repo, wt, seed=tip or ref, ignore_mode=from_windows)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        run.left("worktree", label, f"git cannot run in {wt}; its files could not be read: {e}",
                 roomId=rid)
        return
    same = commit_with_tree(repo, tree, prefer=tip, main=ref)
    if same:
        if not run.repair:
            run.left("worktree", label, f"{wt}: files equal commit {same[:10]} (repairable)",
                     roomId=rid)
            return
        _recreate(run, room, wt, repo, branch, tip, same, gd, ignored)
        return
    # Its files are no commit's: possibly the only copy of the task's work.
    prev = run.cache.get(key) or {}
    if prev.get("tree") == tree and prev.get("state") == "kept" and \
            Path(prev.get("patch") or "").is_file():
        # Looked at before with these very files: their patch is already saved.
        run.worktrees[key] = prev
        run.left("worktree", label, prev.get("detail", ""), roomId=rid,
                 patch=prev.get("patch", ""), branch=prev.get("newBranch", ""))
        return
    task_dir = Path(room.get("taskDir") or ws.get("taskDir") or wt.parent)
    base = base_at(repo, ref, float(room.get("createdAt") or 0))
    patch, n = task_dir / f"moved-{run.date}.patch", 2
    while patch.exists():
        patch, n = task_dir / f"moved-{run.date}-{n}.patch", n + 1
    detail = (f"git cannot run in {wt} (its git folder {gd} is gone) and its files are in no "
              f"commit of the task's branch or the main branch: kept as they are")
    new_branch = ""
    if run.repair:
        try:
            diff = _git(repo, "diff", "--binary", base, tree, timeout=300)
            if diff.returncode != 0:
                raise RuntimeError((diff.stderr or "").strip()[:200])
            head = (f"# Move check: the files of {wt}\n# against {ref} at {base}"
                    f" (its commit when the task was made); the task branch {branch or '-'}"
                    f" {'is at ' + tip if tip else 'is not in the repository'}.\n")
            patch.write_text(head + diff.stdout, encoding="utf-8")
            detail += f"; their difference from {ref.rsplit('/', 1)[-1]} is in {patch}"
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            detail += f"; the patch could not be written: {e}"
            patch = Path("")
        if tip:
            # The base is clear: the task's branch is there. The files go on a
            # new branch from it, the task's branch is left as it is.
            name, n = f"{branch}-moved-{run.date}", 2
            while branch_tip(repo, name):
                name, n = f"{branch}-moved-{run.date}-{n}", n + 1
            c = _git(repo, "commit-tree", tree, "-p", tip,
                     "-m", f"Files of {wt.name} as found after a move to another machine")
            sha = _ok(c)
            if sha and _git(repo, "branch", name, sha).returncode == 0:
                new_branch = name
                detail += f"; committed on branch {name}"
        entry = {"tree": tree, "state": "kept", "patch": str(patch), "detail": detail,
                 "newBranch": new_branch}
        run.worktrees[key] = entry
    else:
        detail += " (a patch is saved on repair)"
    run.left("worktree", label, detail, roomId=rid, patch=str(patch) if run.repair else "",
             branch=new_branch)


def _room_working(room: dict) -> bool:
    """An agent of the room is at work: a terminal of the hub's that the room
    names or that names the room, or an agent in a terminal of its own whose
    process is alive (one recorded on the old machine is not)."""
    if _d._room_is_live(room) or _d._room_has_live_pty(room["id"]):
        return True
    for pp in room.get("participants") or []:
        pid = pp.get("pid")
        if pp.get("kind") == "agent" and pid and not pp.get("ptyId"):
            try:
                if _d.pid_alive(int(pid)):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _copy_ignored(src: Path, dst: Path, names: list[str]) -> tuple[int, list[str]]:
    """The ignored files of the old folder into the new worktree (no commit
    holds them), never over a file that is there: (files copied, the paths
    that are not in the new worktree as they were in the old folder: a copy
    that failed, or a file there already with other contents)."""
    copied, missed = 0, []

    def one(s: Path, d: Path, rel: str) -> None:
        nonlocal copied
        try:
            if s.is_symlink() or not s.is_dir():
                if d.exists() or d.is_symlink():
                    # Never through a link: a link is the same only as the same link.
                    if s.is_symlink() or d.is_symlink():
                        same = s.is_symlink() and d.is_symlink() and os.readlink(s) == os.readlink(d)
                    else:
                        same = d.is_file() and filecmp.cmp(s, d, shallow=False)
                    if not same:
                        missed.append(rel)
                    return
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d, follow_symlinks=False)
                copied += 1
                return
            if d.is_symlink() or (d.exists() and not d.is_dir()):
                missed.append(rel)          # never written through a link
                return
            d.mkdir(parents=True, exist_ok=True)
            for c in sorted(s.iterdir()):
                one(c, d / c.name, f"{rel}/{c.name}")
        except OSError:
            missed.append(rel)

    for name in names:
        one(src / name, dst / name, name)
    return copied, missed


def _recreate(run: _Run, room: dict, wt: Path, repo: str, branch: str, tip: str,
              commit: str, gd: str, ignored: list[str]) -> None:
    """Put the old folder aside and make the worktree again. Any failure
    after the folder was put aside brings it back; a failed bring-back is
    listed with where the folder is."""
    label, rid = _label(room), room["id"]
    aside = _aside(wt, f"moved-{run.date}")
    try:
        os.replace(wt, aside)
    except OSError as e:
        run.left("worktree", label, f"{wt} could not be put aside: {e}", roomId=rid)
        return
    made, name = False, branch
    try:
        _git(repo, "worktree", "prune")
        if tip and tip == commit and \
                _ok(_git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch)):
            r = _git(repo, "worktree", "add", str(wt), branch, timeout=600)
        else:
            name = branch or f"sess/moved-{wt.parent.name}"
            base, n = name, 2
            while _ok(_git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + name)):
                name, n = (f"{base}-moved-{run.date}" if n == 2
                           else f"{base}-moved-{run.date}-{n}"), n + 1
            r = _git(repo, "worktree", "add", "-b", name, str(wt), commit, timeout=600)
        if r.returncode != 0:
            raise RuntimeError("git worktree add failed: " + (r.stderr or "").strip()[:200])
        made = True
        if name != branch:
            ws = dict(room.get("workspace") or {})
            ws["branch"] = name
            _d.chatroom.patch_room(rid, workspace=ws)
    except Exception as e:      # noqa: BLE001 — whatever failed, the folder comes back
        why = str(e) or repr(e)
        try:
            if made or wt.exists():
                try:
                    _git(repo, "worktree", "remove", "--force", str(wt))
                except (OSError, subprocess.SubprocessError):
                    pass
                if wt.exists():
                    shutil.rmtree(wt)
            os.replace(aside, wt)
        except OSError as e2:
            run.left("worktree", label, f"{why}; and the old folder could not be put back "
                     f"({e2}): it is {aside}", roomId=rid, aside=str(aside))
            return
        try:
            _git(repo, "worktree", "prune")
        except (OSError, subprocess.SubprocessError):
            pass
        run.left("worktree", label, f"{why}; the folder is back as it was", roomId=rid)
        return
    detail = (f"made again at {commit[:10]} on {name or 'a new branch'}; "
              f"the old folder (git folder {gd}) is kept as {aside}")
    if ignored:
        copied, missed = _copy_ignored(aside, wt, ignored)
        detail += f"; {copied} ignored file(s) copied in from it"
        if missed:
            shown = ", ".join(missed[:5]) + (" …" if len(missed) > 5 else "")
            run.left("worktree", label, f"{detail}; but {len(missed)} ignored file(s) are only "
                     f"in {aside} (not copied, or the new worktree has another file there): "
                     f"{shown}", roomId=rid, aside=str(aside))
            return
    run.fixed("worktree", label, detail, roomId=rid, aside=str(aside))


def _check_transcripts(run: _Run, room: dict) -> None:
    proj_dir = Path(_d.PROJ_DIR)
    base = room.get("cwd") or ""
    for p in room.get("participants") or []:
        if p.get("kind") not in (None, "agent"):
            continue
        sid = (p.get("sessionId") or "").strip()
        if not sid or any(c in sid for c in "*?[]/\\"):
            continue
        agent = p.get("agent") or ""
        cwd = (p.get("cwd") or "").strip() or (
            base if room.get("sharedCwd") else os.path.join(base, p.get("identity") or ""))
        if not cwd:
            continue
        if agent == "codex":
            _codex_seat(run, sid, cwd)
            continue
        if agent != "claude":
            continue
        target_dir = claude_dir_for(proj_dir, cwd)
        if target_dir is not None and (target_dir / f"{sid}.jsonl").is_file():
            continue
        hits = [h for h in proj_dir.glob(f"*/{sid}.jsonl")
                if target_dir is None or h.parent != target_dir]
        if not hits:
            continue        # not on this machine at all: not a move's (lost_sessions)
        run.found["transcript"] += 1
        what = f"{_label(room)} · {p.get('identity') or agent}"
        src = max(hits, key=lambda h: h.stat().st_mtime)
        if target_dir is None:
            run.left("transcript", what, f"conversation {sid} is in {src.parent.name}; the folder "
                     f"Claude reads for {cwd} cannot be named (its name is cut)", roomId=room["id"])
            continue
        if not run.repair:
            run.left("transcript", what, f"{src.parent.name} → {target_dir.name} (repairable)",
                     roomId=room["id"])
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target_dir / src.name)
            side = src.parent / sid           # its subagents' conversations
            if side.is_dir() and not (target_dir / sid).exists():
                shutil.copytree(side, target_dir / sid)
        except OSError as e:
            run.left("transcript", what, f"could not copy {src}: {e}", roomId=room["id"])
            continue
        run.fixed("transcript", what, f"copied from {src.parent.name} to {target_dir.name}",
                  roomId=room["id"])


def _codex_seat(run: _Run, sid: str, cwd: str) -> None:
    try:
        cx = _d.agents.get_agent("codex")
        was = cx.cwd_for_session(sid) if cx is not None and hasattr(cx, "cwd_for_session") else ""
    except Exception:       # noqa: BLE001
        return
    if was and not os.path.isdir(was) and os.path.normcase(was) != os.path.normcase(cwd):
        run.found["codexOldCwd"] += 1


def run_check(repair: bool = True, notify: bool = True) -> dict:
    """Look, repair what is safe (``repair``), keep and return the result:
    {at, found: {kind: n}, repaired: [...], open: [...]}."""
    with _LOCK:
        run = _Run(repair)
        try:
            _check_code(run)
        except Exception as e:      # noqa: BLE001
            log(f"code folders not checked: {e!r}")
        projects = {p["id"]: p for p in _d.load_projects()}
        for room in _rooms_to_check():
            try:
                room = _check_task_folders(run, room)
                _check_worktree(run, room, projects)
                _check_transcripts(run, room)
            except Exception as e:  # noqa: BLE001 — one room never stops the check
                log(f"{room.get('id')}: not checked: {e!r}")
        prev = read_state()
        result = {"at": time.time(), "repair": repair, "found": run.found,
                  "repaired": run.repaired, "open": run.open}
        if repair:
            state = {**result, "worktrees": run.worktrees,
                     "notified": prev.get("notified") or {}}
            if notify:
                state["notified"] = _tell_pos(run.open, state["notified"])
            _write_state(state)
        counts = ", ".join(f"{k} {v}" for k, v in run.found.items() if v) or "nothing"
        log(f"check done: found {counts}; repaired {len(run.repaired)}, not repaired {len(run.open)}")
        return result


def last() -> dict:
    s = read_state()
    s.pop("worktrees", None)
    s.pop("notified", None)
    return s


def _tell_pos(items: list[dict], notified: dict) -> dict:
    """One line in each PO's chat whose project has something the check could
    not repair, when that list changed since the last line."""
    try:
        projects = {p["id"]: p for p in _d.load_projects()}
    except Exception:       # noqa: BLE001
        return notified
    by_project: dict[str, list[dict]] = {}
    for it in items:
        pid = it.get("projectId") or ""
        if not pid and it.get("roomId"):
            room = _d.chatroom.get_room(it["roomId"], public=False) or {}
            p = _project_of(room, projects)
            pid = (p or {}).get("id", "")
        if pid:
            by_project.setdefault(pid, []).append(it)
    out = {}
    for pid, its in by_project.items():
        po = (projects.get(pid) or {}).get("poRoomId") or ""
        sig = "|".join(sorted(f"{i['kind']}:{i['what']}" for i in its))
        out[pid] = sig
        if not po or notified.get(pid) == sig or _d.chatroom.get_room(po, public=False) is None:
            continue
        kinds = {}
        for i in its:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
        what = ", ".join(f"{n} {_KIND_WORDS[k][n != 1]}" for k, n in kinds.items())
        try:
            _d.chatroom.post_notice(
                po, _d.digest.SENDER,
                f"Move check: {what} of this project could not be repaired by the hub after "
                f"the move to this machine; Settings › This instance lists them.",
                {"noticeKind": NOTICE_KIND})
        except Exception as e:      # noqa: BLE001
            log(f"the PO of {pid} could not be told: {e!r}")
            out.pop(pid, None)
    return out


_KIND_WORDS = {
    "code": ("code folder", "code folders"),
    "taskFolder": ("task folder", "task folders"),
    "worktree": ("task worktree", "task worktrees"),
    "transcript": ("conversation", "conversations"),
}
