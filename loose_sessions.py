"""Which conversations on disk the session list leaves out (#350, P318).

The list of conversations no task holds (``dashboard._load_sessions_uncached``:
the left panel's loose rows, the Projects view, search, the make-PO picker)
shows only those a person started by hand. Never listed, though nothing is
deleted from disk:

* a conversation the hub records having had: any room's current or past
  session (``dashboard._room_holds``), and those of a room deleted since
  (``chatroom.gone_sessions``, kept when the room is deleted);
* a one-shot, non-interactive run: ``claude -p`` and the Agent SDK
  (``entrypoint`` ``sdk-…`` in the transcript), ``codex exec`` (``source``
  ``exec`` in the rollout's ``session_meta``). Tests and the hub's own checks
  run those;
* one whose first input is hub text (``dashboard._hub_words_kind``: a
  ``HUB_INPUT_KINDS`` line, a PO brief, a task agent's standing brief): the
  backstop for one no record names;
* one run in a temporary folder, outside every project (an agent's scratchpad,
  a test's throwaway HOME, a probe's folder);
* one run in a task's folder or below it (a probe an agent of the task ran
  there), also when the folder was written on another machine and the state
  was moved here (``move_check.map_old_path``).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import filefacts
import move_check

# A Claude transcript's ``entrypoint`` for ``claude -p`` (``sdk-cli``) and the
# Agent SDK (``sdk-ts``, ``sdk-py``).
CLAUDE_ONE_SHOT = ("sdk-",)
# Whether a temporary folder hides a conversation. The hub's own tests build
# their conversations in this machine's temporary folder and turn it off.
TEMP_RULE = True
# How much of a file is read, at most, for its launch facts.
SCAN_BYTES = 8 * 1024 * 1024

_LOCK = threading.Lock()
_ONE_SHOT: dict[str, bool] = {}
_UNTOLD: dict[str, tuple] = {}     # path -> (size, mtime) it told nothing at


def _key(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def _decide(path: Path, judge):
    """``judge`` over the file's lines from its start, until one tells (True /
    False) or SCAN_BYTES are read: None when none told yet."""
    try:
        with open(path, "rb") as f:
            seen = 0
            while seen < SCAN_BYTES:
                raw = f.readline(SCAN_BYTES - seen)
                if not raw:
                    return None
                seen += len(raw)
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue        # cut short, or still being written
                if isinstance(d, dict):
                    said = judge(d)
                    if said is not None:
                        return said
    except OSError:
        return None
    return None


def _one_shot(path, kind: str, judge) -> bool:
    """``judge`` over the file's first lines: True / False once it can tell,
    None to read on. Remembered once told (in memory and in filefacts): how a
    conversation was started never changes. A file that tells nothing yet is
    read again once it has changed (it may be growing)."""
    k = str(path)
    with _LOCK:
        hit = _ONE_SHOT.get(k)
    if hit is not None:
        return hit
    kept = filefacts.get(kind, k, None)
    if isinstance(kept, bool):
        with _LOCK:
            _ONE_SHOT[k] = kept
        return kept
    try:
        st = os.stat(k)
        sig = (st.st_size, st.st_mtime_ns)
    except OSError:
        return False
    with _LOCK:
        if _UNTOLD.get(k) == sig:
            return False
    said = _decide(Path(k), judge)
    if said is None:
        with _LOCK:
            _UNTOLD[k] = sig
        return False
    out = bool(said)
    with _LOCK:
        _ONE_SHOT[k] = out
        _UNTOLD.pop(k, None)
    filefacts.put(kind, k, None, out)
    return out


def _claude_judge(d: dict):
    ep = d.get("entrypoint")
    if isinstance(ep, str) and ep:
        return ep.startswith(CLAUDE_ONE_SHOT)
    return None


def _codex_judge(d: dict):
    if d.get("type") != "session_meta":
        return None
    p = d.get("payload") or {}
    return p.get("source") == "exec" or p.get("originator") == "codex_exec"


def claude_one_shot(path) -> bool:
    """A Claude transcript of ``claude -p`` or the Agent SDK."""
    return _one_shot(path, "oneshot", _claude_judge)


def codex_one_shot(path) -> bool:
    """A Codex rollout of ``codex exec``."""
    return _one_shot(path, "codexexec", _codex_judge) if path else False


# ---------- folders (pure) ----------

def _parts_key(parts: list[str]) -> list[str]:
    return [x.casefold() for x in parts]


def in_temp_folder(cwd: str, tmp: str | None = None) -> bool:
    """A folder under this machine's temporary folder, or one that reads as a
    temporary folder on any system (``…\\AppData\\Local\\Temp\\…``, ``/tmp``,
    ``/var/tmp``, macOS's ``/var/folders``)."""
    if not cwd:
        return False
    t = _key(tmp if tmp is not None else tempfile.gettempdir())
    c = _key(cwd)
    if t and t != _key(os.sep) and (c == t or c.startswith(t.rstrip(os.sep) + os.sep)):
        return True
    parts = move_check.path_parts(cwd)
    if not parts:
        return False
    low = _parts_key(parts)
    for i in range(len(low) - 2):
        if low[i:i + 3] == ["appdata", "local", "temp"]:
            return True
    if cwd.strip().startswith("/"):
        return (low[:1] == ["tmp"] or low[:2] in (["var", "tmp"], ["private", "tmp"], ["var", "folders"])
                or low[:3] == ["private", "var", "folders"])
    return False


def _within(c: str, folders) -> bool:
    """``c`` (a _key) is one of ``folders`` or below one."""
    if not c or not folders:
        return False
    cur = c
    while True:
        if cur in folders:
            return True
        up = os.path.dirname(cur)
        if not up or up == cur:
            return False
        cur = up


def task_folders(rooms: list[dict], project_dirs, home=None, root=None) -> frozenset:
    """Each task's folder (``taskDir``), as a _key, and where it is on this
    machine when it was recorded on another (``map_old_path``, as a
    conversation's folder is), but never one that is, or holds, a project's
    own folder: a person works there."""
    pj = {_key(p) for p in project_dirs if p}
    out = set()
    for rm in rooms:
        td = rm.get("taskDir") if isinstance(rm, dict) else ""
        if not isinstance(td, str) or not td.strip():
            continue
        names = _candidates(td, home, root)
        # One alias a project's folder, or holding one: none of them is kept
        # (the old copy of a moved project is still a person's folder).
        if any(k in pj or any(_within(p, {k}) for p in pj) for k in names):
            continue
        out.update(names)
    return frozenset(out)


def _candidates(cwd: str, home, root) -> list[str]:
    out = [_key(cwd)]
    if home and root:
        out += [_key(str(p)) for p in move_check.map_old_path(cwd, Path(home), Path(root))]
    return [c for c in out if c]


def in_task_folder(cwd: str, folders, home=None, root=None) -> bool:
    """A folder at or below a task's folder, here or as moved from another
    machine (``map_old_path``)."""
    if not cwd or not folders:
        return False
    return any(_within(c, folders) for c in _candidates(cwd, home, root))


def in_project(cwd: str, project_dirs) -> bool:
    if not cwd:
        return False
    return _within(_key(cwd), {_key(p) for p in project_dirs if p})


def reason(first: str, cwd: str, *, hub_words, one_shot=None, task_dirs=frozenset(),
           project_dirs=(), home=None, root=None, tmp=None) -> str:
    """Why a conversation no record holds is left out of the list, or "" for a
    person's own. ``hub_words(text)`` names the hub kind a first message
    begins (None for a person's words); ``one_shot()`` is asked last (it may
    read the file)."""
    s = (first or "").strip()
    if s and hub_words(s) is not None:
        return "hub words"
    if task_dirs and in_task_folder(cwd, task_dirs, home, root):
        return "task folder"
    if TEMP_RULE and in_temp_folder(cwd, tmp) and not in_project(cwd, project_dirs):
        return "temporary folder"
    if one_shot is not None and one_shot():
        return "one-shot run"
    return ""
