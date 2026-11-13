"""A seat whose conversation file is gone (GitHub issue 33).

Claude Code deletes conversation files older than ``cleanupPeriodDays`` (30
days when unset) each time it starts, and a Codex rollout can be deleted or
archived by hand. A PO or task left idle for longer keeps its ``sessionId``
on the room, and ``claude --resume <id>`` then prints "No conversation found
with session ID: …" and exits: every message and every Resume ended the same
way (measured 2026-11-09: the line, exit code 0; Codex prints "no rollout
found for thread id …").

This module:

* tells whether a seat's conversation is gone before it is resumed
  (:func:`conversation_gone`), and reads the CLI's own line off a dying
  screen as the fallback (:func:`screen_says_gone`);
* says why in the one chat line (:func:`notice`). The fresh session itself
  is the token-limit rotation's (rotation.rotate_gone): the PO from
  ``PO-HANDOVER.md`` (or, with none, the board, the roadmap and
  :func:`chat_lines`), a task owner from ``TASK-HANDOVER.md``. Only a seat
  the rotation does not take (no task behind it) gets :func:`session_brief`;
* reads and, on the person's click only, raises Claude Code's
  ``cleanupPeriodDays`` (:func:`retention`, :func:`keep_for_a_year`). That
  one key is the only write Ensemble makes to Claude's settings.

Standard library only; the dashboard module is bound in by ``bind``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


DEFAULT_CLEANUP_DAYS = 30           # Claude Code's own default when the key is unset
KEEP_DAYS = 365                     # what "Keep conversations for a year" sets
# Where the page shows it (index.html's Preferences tab, "Conversations"),
# as the hub log and `ensemble.ps1 doctor` name it.
WHERE = "Settings > Preferences > Conversations"
CHAT_LINES = 12                     # how many of the hub's chat lines a brief shows
_LINE_MAX = 600                     # one line's text in a brief, at most

# The CLIs' own words when the conversation to resume is not on disk.
GONE_RE = re.compile(
    r"No conversation found with session ID"     # Claude Code 2.x
    r"|no rollout found for thread id"           # Codex 0.16x (exec and TUI)
    r"|No saved session found", re.I)            # older Codex

# A resume that fails for a missing conversation dies at once; a terminal that
# ran longer than this was working, whatever its last screen shows.
SHORT_LIFE_S = 30.0


def screen_says_gone(tail: str) -> bool:
    """Whether a terminal's last screen is the CLI saying the conversation it
    was asked to resume does not exist."""
    return bool(tail) and bool(GONE_RE.search(_strip_ansi(tail)[-4000:]))


_RETRIED: set[tuple[str, str, str]] = set()     # (room, identity, sid) retried once
_RETRY_WAIT = 90.0                              # seconds a resume may take to settle


def on_death(rec: dict, wait: float = _RETRY_WAIT, background: bool = True) -> bool:
    """The fallback when the check before a resume missed it: a resumed
    terminal that died saying its conversation does not exist. The seat is
    marked (``conversationGone``), so the next start rotates it, and the room
    is started again once per conversation id, once the failed resume has
    settled (what it held comes along). Returns whether a retry was set off.

    Only a terminal that died within ``SHORT_LIFE_S`` of starting, whose
    conversation is really not on disk: a session that worked for hours and
    printed the words (a test of this very feature, a grep) is left alone."""
    meta = rec.get("meta") or {}
    rid, ident, sid = meta.get("room", ""), meta.get("identity", ""), meta.get("resumeOf", "")
    if not (rid and ident and sid) or rec.get("killed"):
        return False
    try:
        lived = float(rec.get("endedAt") or time.time()) - float(rec.get("startedAt"))
    except (TypeError, ValueError):
        return False            # how long it ran is not known: not marked
    if lived > SHORT_LIFE_S:
        return False
    if not screen_says_gone(rec.get("tail") or ""):
        return False
    room = _d.chatroom.get_room(rid, public=False) or {}
    part = _d.chatroom.participant(room, ident) or {}
    if not _missing_on_disk(part.get("agent") or meta.get("agent") or "claude", sid):
        return False
    if not _d.chatroom.mark_conversation_gone(rid, ident, sid):
        return False
    key = (rid, ident, sid)
    if key in _RETRIED:
        return False
    _RETRIED.add(key)
    print(f"[lost] {rid}/{ident}: the CLI found no conversation {sid} — rotating and "
          f"starting it again", flush=True)
    if background:
        import threading
        threading.Thread(target=_retry, args=(rid, wait), daemon=True,
                         name=f"lost-retry-{rid}").start()
    else:
        _retry(rid, wait)
    return True


def _retry(rid: str, wait: float) -> None:
    """Start the room again once its resume is no longer in flight: a
    resume of a stopped room (carrying what the failed one held), or, when
    other agents of the room still run, a start of the dead seats."""
    deadline = time.time() + wait
    while True:
        with _d._RESUMES_LOCK:
            res = _d._RESUMES.get(rid)
            busy = res is not None and res.state == "resuming"
        if not busy or time.time() >= deadline:
            break
        time.sleep(1.0)
    room = _d.chatroom.get_room(rid, public=False)
    if room is None or room.get("pruned") or room.get("deleted"):
        return
    try:
        h = _d.hub_launcher()
        if _d._room_is_live(room):
            h._start_or_resume_room(room, restart={})
        else:
            h._resume_room(room, quiet=True)
    except Exception as e:      # noqa: BLE001 — said in the log; the next send retries
        print(f"[lost] {rid}: start after a gone conversation failed: {str(e)[:200]}",
              flush=True)


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def conversation_gone(part: dict, cwd: str = "") -> bool:
    """Whether the conversation the seat would resume is not on disk. A seat
    the hub already found gone on screen
    (``conversationGone`` is its current id) is gone whatever the disk says:
    the CLI could not open it."""
    sid = (part.get("sessionId") or "").strip()
    if not sid:
        # Nothing named to resume: the resume path already starts it fresh
        # (a Codex seat from its folder's latest rollout, or its first prompt).
        return False
    if part.get("conversationGone") == sid:
        return True
    if not _SID_RE.match(sid):
        # Both CLIs name a conversation by a UUID; any other id was not
        # minted by them and is resumed as before.
        return False
    return _missing_on_disk(part.get("agent") or "", sid)


def _missing_on_disk(agent: str, sid: str) -> bool:
    """Whether no file of this conversation is on disk now."""
    if agent == "codex":
        return not _codex_rollouts(sid)
    return _claude_transcript(sid) is None


def claude_config_dir() -> Path:
    """Claude Code's own folder: ``CLAUDE_CONFIG_DIR`` when set (as the
    launch's login check reads it, agent_launch._login_file), else ~/.claude."""
    env = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(os.path.expanduser(env)) if env else Path(os.path.expanduser("~")) / ".claude"


def _claude_transcript(sid: str, immediate: bool = True):
    """The hub's lookup, then ``<CLAUDE_CONFIG_DIR>/projects`` when Claude
    keeps its conversations somewhere other than the folder the hub reads."""
    hit = _d.find_transcript(sid, immediate=immediate)
    if hit is not None:
        return hit
    projects = claude_config_dir() / "projects"
    try:
        if projects.resolve() == Path(_d.PROJ_DIR).resolve() or not projects.is_dir():
            return None
        hits = [p for p in projects.glob(f"*/{sid}.jsonl")] if re.fullmatch(r"[\w-]+", sid) else []
    except OSError:
        return None
    # The most recently written copy of an id held in several folders (ED-355).
    return _d._newest(hits)


_SID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _existing(paths) -> list:
    out = []
    for p in paths or []:
        try:
            if Path(p).exists():
                out.append(p)
        except OSError:
            pass
    return out


def _codex_rollouts(sid: str) -> list:
    """The rollouts of a Codex session that are on disk NOW. The hub's lookup
    remembers its answer for a while (a board poll asks for every Codex
    conversation), so a rollout deleted since is filtered out here and the
    lookup is made afresh before the session counts as gone."""
    hits: list = []
    for attempt in range(2):
        try:
            hits = _existing(_d._codex_rollouts(sid))
        except Exception:       # noqa: BLE001
            hits = []
        if hits:
            return hits
        if attempt == 0:
            try:
                _d._CODEX_ROLLOUT_PATHS.pop(sid, None)
            except Exception:   # noqa: BLE001
                break
    cx = _d.agents.get_agent("codex")
    if cx is None or not hasattr(cx, "rollouts_for_session"):
        return []
    try:
        return _existing(cx.rollouts_for_session(sid))
    except Exception:       # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# What the fresh session and the chat are told
# ---------------------------------------------------------------------------

def why_gone(agent: str) -> str:
    """Why a conversation is not on disk, as the chat line and the brief say it."""
    if agent == "codex":
        return "its Codex rollout is no longer in the Codex sessions folder"
    return (f"Claude Code deletes conversations after {cleanup_days()} days by default "
            f"(its cleanupPeriodDays setting)")


def chat_lines(room: dict, part: dict, old_sid: str, n: int = CHAT_LINES) -> str:
    """The last ``n`` lines of the seat's chat as the hub keeps it: a team's
    room messages, else a solo seat's text turns the hub stored before the
    conversation file went (turnstore). "" when the hub has none."""
    rows: list[tuple[str, str]] = []
    msgs = [m for m in room.get("messages") or []
            if isinstance(m, dict) and (m.get("text") or "").strip()
            and m.get("kind") != "notice"]
    if msgs:
        for m in msgs[-n:]:
            rows.append((str(m.get("from") or "?"), str(m.get("text") or "")))
    elif old_sid:
        try:
            turns = _d.read_session_turns(old_sid)
        except Exception:       # noqa: BLE001 — the brief goes without them
            turns = None
        human = _d.operator_name()
        for t in [t for t in turns or [] if not t.get("hidden") and not t.get("interim")][-n:]:
            rows.append((human if t.get("role") == "user" else part.get("identity", "agent"),
                         str(t.get("text") or "")))
    if not rows:
        return ""
    out = []
    for who, text in rows:
        text = " ".join(text.split())
        if len(text) > _LINE_MAX:
            text = text[:_LINE_MAX] + " …"
        out.append(f"- {who}: {text}")
    return "\n".join(out)


def session_brief(room: dict, part: dict, old_sid: str) -> str:
    """A seat the rotation does not take (a one-agent session with no task
    behind it, an adopted one): the same folder, told its earlier
    conversation is gone and shown the hub's record of its last lines."""
    name = room.get("title") or room.get("id", "")
    parts = [f"[recovery] You are a fresh session for '{name}'. The previous session's "
             f"conversation (session {old_sid or 'unknown'}) is no longer on disk: "
             f"{why_gone(part.get('agent', ''))}. It cannot be resumed or loaded; the hub "
             f"started you in its place, in the same folder, under the same name and with "
             f"the same tools."]
    chat = chat_lines(room, part, old_sid)
    parts.append("Its last lines, as the hub keeps them:\n\n" + chat if chat
                 else "The hub has no record of its last lines.")
    parts.append(f"Do not redo anything these lines show as done. Reply with one line saying "
                 f"you are ready, then wait for {_d.operator_name()}'s next message.")
    return "\n\n".join(parts)


_SPEC_MAX = 8000                    # the spec's characters a no-handover brief carries


def task_context(room: dict, part: dict, old_sid: str, cwd: str = "") -> str:
    """What a task owner whose conversation is gone and who never wrote a
    handover is given instead: the spec as context, the branch as it is, and
    the chat's last lines (the spec of GitHub issue 33, step 2)."""
    spec = (room.get("spec") or "").strip()
    if len(spec) > _SPEC_MAX:
        spec = spec[:_SPEC_MAX] + "\n…(cut; the whole spec is on the task)"
    out = ["**The task's spec (context only):**\n\n" + (spec or "(the task has no spec)")]
    if cwd and os.path.isdir(cwd):
        base = ((room.get("workspace") or {}).get("base") or "main").strip() or "main"
        log = (_d._git_out(cwd, "log", "--oneline", "-20", f"{base}..HEAD")
               or _d._git_out(cwd, "log", "--oneline", "-5"))
        status = _d._git_out(cwd, "status", "--short")
        branch = _d._git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        out.append(f"**The branch** (`{branch or '?'}` in `{cwd}`):\n\n"
                   f"Commits:\n```\n{log or '(none beyond ' + base + ')'}\n```\n"
                   f"Uncommitted:\n```\n{status or '(nothing)'}\n```")
    chat = chat_lines(room, part, old_sid)
    out.append("**The chat's last lines:**\n\n" + (chat or "(the hub has none)"))
    return "\n\n".join(out)


def notice(kind: str, part: dict, ident: str, handover: bool = True) -> str:
    """The one chat line said when a fresh session takes over. ``kind`` is
    po | owner | session; ``handover`` False when there was none to start from."""
    whose = "this PO" if kind == "po" else ident if kind == "owner" else "this session"
    if kind == "session":
        src = "the hub's record of its last messages"
    elif not handover:
        src = ("the board, the roadmap and its last chat lines" if kind == "po"
               else "the branch and the task's chat")
    else:
        src = "`PO-HANDOVER.md`" if kind == "po" else "`TASK-HANDOVER.md`"
    return (f"**The earlier conversation of {whose} was removed from disk** "
            f"({why_gone(part.get('agent', ''))}); a fresh session took over from {src}.")


# ---------------------------------------------------------------------------
# Claude Code's retention (cleanupPeriodDays)
# ---------------------------------------------------------------------------

def claude_settings_path() -> Path:
    return claude_config_dir() / "settings.json"


def _read_settings(path: Path) -> tuple[dict | None, str]:
    """(settings, "") or (None, why it cannot be read as a JSON object). A
    missing file is an empty one."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as e:
        return None, f"could not read {path}: {e}"
    if not raw.strip():
        return {}, ""
    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, f"{path} is not valid JSON ({e}); it was left as it is"
    if not isinstance(data, dict):
        return None, f"{path} is not a JSON object; it was left as it is"
    return data, ""


def cleanup_days(path: Path | None = None) -> int:
    """Claude Code's ``cleanupPeriodDays`` in the user's settings, 30 when
    unset or unreadable."""
    data, _ = _read_settings(path or claude_settings_path())
    v = (data or {}).get("cleanupPeriodDays")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return DEFAULT_CLEANUP_DAYS
    return int(v)


def _born(p: Path) -> float:
    """When a transcript was started: its creation time where the system keeps
    one (Windows, macOS), else its first change."""
    try:
        st = p.stat()
    except OSError:
        return 0.0
    return float(getattr(st, "st_birthtime", 0) or (st.st_ctime if os.name == "nt" else st.st_mtime))


def retention(rooms: list[dict] | None = None, now: float | None = None,
              path: Path | None = None) -> dict:
    """Whether Claude Code's retention is shorter than the conversations the
    hub keeps resuming: ``{days, set, oldestDays, atRisk, gone, warn}``.
    ``atRisk`` counts the live seats whose conversation is older than
    ``days``; ``gone``, the live Claude seats whose conversation is already
    missing. A seat counts when its room is not deleted, archived or pruned."""
    now = now or time.time()
    path = path or claude_settings_path()
    data, err = _read_settings(path)
    raw = (data or {}).get("cleanupPeriodDays")
    days = cleanup_days(path)
    if rooms is None:
        try:
            rooms = _d.chatroom.list_rooms()
        except Exception:       # noqa: BLE001
            rooms = []
    oldest, at_risk, gone = 0.0, 0, 0
    for room in rooms:
        if (room.get("pruned") or room.get("archived") or room.get("deleted")
                or room.get("workflow") == "done" or not room.get("launched", True)):
            continue
        for p in room.get("participants") or []:
            if p.get("kind") != "agent" or p.get("agent") == "codex":
                continue
            sid = (p.get("sessionId") or "").strip()
            if not sid:
                continue
            t = _claude_transcript(sid, immediate=False)
            if t is None:
                gone += 1
                continue
            age = (now - _born(t)) / 86400.0 if _born(t) else 0.0
            oldest = max(oldest, age)
            if age > days:
                at_risk += 1
    return {"days": days, "set": raw is not None and days == raw, "default": DEFAULT_CLEANUP_DAYS,
            "keepDays": KEEP_DAYS, "oldestDays": round(oldest, 1), "atRisk": at_risk,
            "gone": gone, "warn": bool(at_risk or gone),
            "settingsPath": str(path), "readError": err}


def keep_for_a_year(path: Path | None = None) -> dict:
    """Set ``cleanupPeriodDays`` to a year in Claude Code's user settings,
    every other key as it was. Only ever on the person's click (Settings, or
    the start-up banner). Refuses a file it cannot read as a JSON object
    rather than overwrite it. Returns {ok, days, path} or {ok: False, error}."""
    path = path or claude_settings_path()
    data, err = _read_settings(path)
    if data is None:
        return {"ok": False, "error": err}
    if cleanup_days(path) >= KEEP_DAYS and "cleanupPeriodDays" in data:
        return {"ok": True, "days": cleanup_days(path), "path": str(path), "changed": False}
    data["cleanupPeriodDays"] = KEEP_DAYS
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"ok": False, "error": f"could not write {path}: {e}"}
    return {"ok": True, "days": KEEP_DAYS, "path": str(path), "changed": True}
