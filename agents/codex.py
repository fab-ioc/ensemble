"""Codex CLI agent adapter.

Codex stores each session as a JSONL "rollout" file:

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl

Line 1 is a `session_meta` record carrying the session id, cwd, and start
timestamp. Subsequent lines are events. Unlike Claude, Codex writes **no**
live/pid registry, so discovery here yields history only (`is_live=False`);
liveness for dashboard-launched Codex sessions is tracked separately by the
dashboard's own launch registry.

CODEX_HOME overrides the default `~/.codex` location, matching the CLI.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import filefacts
import filetable

try:
    import tomllib
except ImportError:             # Python 3.10: trust is checked by its header
    tomllib = None

from .base import AgentSession, AgentType

_FALLBACK_LOCK = threading.Lock()
_FALLBACK_INDEX: dict[Path, tuple[float, set[Path], dict[str, list[Path]]]] = {}
_FALLBACK_RESCAN_SECONDS = 60


def _legacy_rollouts(root: Path, session_id: str) -> list[Path]:
    """Index unusual rollout names once, then inspect only new files."""
    with _FALLBACK_LOCK:
        now = time.monotonic()
        hit = _FALLBACK_INDEX.get(root)
        if hit is None:
            at, seen, ids = 0.0, set(), {}
        else:
            at, seen, ids = hit
        if now - at >= _FALLBACK_RESCAN_SECONDS:
            current = set(root.glob("*/*/*/rollout-*.jsonl"))
            if seen - current:
                for key, files in list(ids.items()):
                    ids[key] = [f for f in files if f in current]
                    if not ids[key]:
                        del ids[key]
            parsed = set(current & seen)
            for f in current - seen:
                meta = _read_meta(f)
                key = meta.get("session_id") or meta.get("id")
                if key:
                    ids.setdefault(key, []).append(f)
                    parsed.add(f)
            # An empty rollout can be observed before its session_meta line
            # is written. Recheck such files on the next inexpensive rescan.
            _FALLBACK_INDEX[root] = (now, parsed, ids)
            if len(_FALLBACK_INDEX) > 8:
                for old_root in list(_FALLBACK_INDEX)[:-8]:
                    _FALLBACK_INDEX.pop(old_root, None)
        return list(ids.get(session_id, []))


def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else (Path.home() / ".codex")


_TRUST_LOCK = threading.Lock()


def _trust_keys(cwd: str) -> list[str]:
    """The keys Codex may look ``cwd`` up by: its real path (Codex resolves
    links in its working folder) and the path as given, when that differs.
    Windows compares paths without case, and an older hub wrote them
    lowercased there, so its lowercased form is added on Windows only."""
    keys = []
    for k in (os.path.realpath(cwd), os.path.normpath(os.path.abspath(cwd))):
        if k not in keys:
            keys.append(k)
    if os.name == "nt":
        keys += [k.lower() for k in keys if k.lower() not in keys]
    return keys


def _toml_key(s: str) -> str:
    """``s`` as a TOML basic-string key: quotes, backslashes (Windows paths)
    and control characters escaped."""
    out = []
    for ch in s:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _trust_states(text: str, keys: list[str]) -> dict[str, str] | None:
    """Each key's entry in the config text: "trusted", "untrusted" (or any
    other trust level: not trusted), "notrust" (its table has no trust
    level), or "missing". None when the text does not parse, or there is
    no TOML parser to read it with (Python 3.10)."""
    if tomllib is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    projects = data.get("projects")
    out = {}
    for k in keys:
        entry = projects.get(k) if isinstance(projects, dict) else None
        if entry is None:
            out[k] = "missing"
        elif not isinstance(entry, dict) or "trust_level" not in entry:
            out[k] = "notrust"
        else:
            out[k] = "trusted" if entry.get("trust_level") == "trusted" else "untrusted"
    return out


def trust_blocker(cwd: str) -> str:
    """Why the hub must leave ``cwd``'s Codex trust screen to a person, or
    "": "untrusted" when any key Codex may look it up by is marked with a
    trust level other than "trusted" (the person decided), "noparser" when
    there is no TOML parser to read the config with (Python 3.10: the hub
    never guesses at it), "unreadable" when the config cannot be read or
    parsed."""
    if tomllib is None:
        return "noparser"
    cfg = _codex_home() / "config.toml"
    try:
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    except OSError:
        return "unreadable"
    states = _trust_states(text, _trust_keys(cwd or "."))
    if states is None:
        return "unreadable"
    return "untrusted" if any(st == "untrusted" for st in states.values()) else ""


def _iso_to_epoch(ts: str) -> float:
    """Parse Codex's ISO-8601 timestamps (e.g. '2026-08-06T09:09:06.301Z')."""
    if not ts:
        return 0.0
    try:
        # Python 3.11+ fromisoformat accepts the trailing 'Z'.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return 0.0


# Prefixes that mark a Codex-injected pseudo-'user' message rather than a real
# human prompt: XML-ish context blocks, and the project AGENTS.md preamble
# (Codex's equivalent of CLAUDE.md, fed in as a leading user turn).
_SYNTHETIC_PREFIXES = (
    "<",                       # <environment_context>, <permissions ...>, <user_instructions ...>
    "# AGENTS.md",
    "# instructions",
)


def _is_synthetic(text: str) -> bool:
    """True if this 'user' message is an injected context/instructions block,
    not something the human actually typed."""
    t = text.lstrip()
    low = t.lower()
    return t.startswith(_SYNTHETIC_PREFIXES) or low.startswith("# agents.md")


# The parts around an image a person's turn carries (seen in codex 0.8x):
# "<image name=[Image #1]>", the image, "</image>"; then the words with the
# placeholder "[Image #1]" in them, which the chat page does not show.
_IMAGE_WRAP = re.compile(r"^\s*(?:<image name=\[Image #\d+\]>|</image>)\s*$")


def _text_of(content) -> str:
    """Flatten a Codex message `content` (str or list of typed parts)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text"):
                if _IMAGE_WRAP.match(c.get("text") or ""):
                    continue
                parts.append(c.get("text", ""))
        return " ".join(parts).strip()
    return ""


def _read_meta(path: Path) -> dict:
    """The `session_meta` payload (line 1) only — id, cwd, start timestamp.

    Reading one line is orders of magnitude cheaper than `_parse_rollout`, which
    walks the whole file; use this whenever only the session's identity or cwd
    is needed (id lookup, cwd lookup, liveness backfill).
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            d = json.loads(fh.readline() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    if d.get("type") != "session_meta":
        return {}
    return d.get("payload") or {}


def _started(meta: dict, path: Path) -> float:
    """When a rollout's conversation started: its `session_meta` timestamp,
    else the file's creation time (Windows; a change time elsewhere)."""
    t = _iso_to_epoch(str(meta.get("timestamp") or ""))
    if t:
        return t
    try:
        return path.stat().st_ctime
    except OSError:
        return 0.0


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_ROLLOUT_CACHE: dict[str, tuple] = {}

_TABLES: dict[str, filetable.FileTable] = {}


def _rollout_table(root: Path, fresh_s: float = 0.0) -> dict[str, tuple[float, int, int]]:
    """Every rollout under ``root`` with its time and size, from a file table
    (#228): a refresh re-lists only day folders that moved and re-stats only
    recent rollouts, not all of them."""
    key = str(root)
    t = _TABLES.get(key)
    if t is None:
        t = _TABLES[key] = filetable.FileTable(
            lambda: key, 3, lambda n: n.startswith("rollout-") and n.endswith(".jsonl"))
    return t.files(fresh_s=fresh_s)


def _parse_rollout(path: Path, sig: tuple | None = None) -> AgentSession | None:
    """One rollout file as an AgentSession, remembered per file on its size and
    modification time: the task list asks for every rollout on every refresh,
    and an unchanged file always parses the same. A copy is returned, so a
    caller marking a session live can never change the remembered one.
    ``sig`` (mtime_ns, size), when the caller has it, saves the stat."""
    if sig is None:
        try:
            stt = path.stat()
            sig = (stt.st_mtime_ns, stt.st_size)
        except OSError:
            sig = None
    if sig is not None:
        hit = _ROLLOUT_CACHE.get(str(path))
        if hit and hit[0] == sig:
            return copy.copy(hit[1]) if hit[1] is not None else None
        # What an earlier hub read of this file, while it is unchanged (ED-200).
        kept = filefacts.get("rollout", path, (sig[1], sig[0]))
        if isinstance(kept, dict):
            try:
                res = AgentSession(**kept)
            except TypeError:
                res = None
            if res is not None:
                _ROLLOUT_CACHE[str(path)] = (sig, res)
                return copy.copy(res)
    res = _parse_rollout_uncached(path)
    if sig is not None:
        _ROLLOUT_CACHE[str(path)] = (sig, res)
        if res is not None:
            filefacts.put("rollout", path, (sig[1], sig[0]), dataclasses.asdict(res))
    return copy.copy(res) if res is not None else None


def _parse_rollout_uncached(path: Path) -> AgentSession | None:
    """Read one rollout file into a normalized AgentSession."""
    session_id = ""
    cwd = ""
    started = 0.0
    first = last = ""
    turns = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                typ = d.get("type")
                payload = d.get("payload") or {}
                if typ == "session_meta":
                    session_id = payload.get("session_id") or payload.get("id") or ""
                    cwd = payload.get("cwd", "") or ""
                    started = _iso_to_epoch(payload.get("timestamp", ""))
                    continue
                # Real user input shows up either as an event_msg/user_message
                # or a response_item message with role == "user".
                is_user_msg = (
                    (typ == "event_msg" and payload.get("type") == "user_message")
                    or (isinstance(payload, dict)
                        and payload.get("type") == "message"
                        and payload.get("role") == "user")
                )
                if not is_user_msg:
                    continue
                text = _text_of(payload.get("content") or payload.get("message") or "")
                if not text or _is_synthetic(text):
                    continue
                turns += 1
                if not first:
                    first = text[:200]
                last = text[:200]
    except OSError:
        return None

    if not session_id:
        # Fall back to the uuid embedded in the filename.
        stem = path.stem  # rollout-<ts>-<uuid>
        session_id = stem.split("-", 2)[-1] if "-" in stem else stem

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = started

    return AgentSession(
        agent="codex",
        session_id=session_id,
        cwd=cwd,
        started_at=started,
        updated_at=mtime,
        first=first,
        last=last,
        turns=turns,
        transcript_path=str(path),
        is_live=False,
    )


class CodexAgent(AgentType):
    key = "codex"
    display_name = "Codex"

    def installed(self) -> bool:
        return shutil.which("codex") is not None

    def sessions_dir(self) -> Path:
        return _codex_home() / "sessions"

    def list_sessions(self, limit: int = 200, dropped=None,
                      max_age: float = 0.0) -> list[AgentSession]:
        """The newest `limit` sessions. With `dropped` (session -> bool: the
        caller will not show this one), `limit` counts the sessions it keeps,
        not the files looked at: a session outside every task stays listed
        however many rollouts the hub's own tasks wrote after it. The dropped
        ones are still returned, for the caller to drop as it always has. Past
        the newest `limit` files, a rollout older than `max_age` seconds ends
        the walk."""
        root = self.sessions_dir()
        if not root.exists():
            return []
        table = _rollout_table(root)
        # Newest first by mtime. A parsed rollout is remembered per file, so
        # walking past `limit` files costs nothing more than the table.
        files = [p for _, p in filetable.newest_first(table)]
        if dropped is None:
            files = files[:limit]
        oldest = (time.time() - max_age) if max_age else 0.0
        out: list[AgentSession] = []
        seen: set[str] = set()
        kept = 0
        for i, f in enumerate(files):
            if kept >= limit:
                break
            st = table.get(str(f))
            s = _parse_rollout(f, (st[2], st[1]) if st else None)
            if s is None:
                continue
            if i >= limit and s.updated_at < oldest:
                break
            # A resumed session writes a fresh rollout file under the same id.
            # Files are mtime-desc, so the first one we see is the most recent.
            if s.session_id in seen:
                continue
            seen.add(s.session_id)
            out.append(s)
            if dropped is None or not dropped(s):
                kept += 1
        return out

    def launch_argv(self, cwd: str, prompt: str = "",
                    extra: list[str] | None = None) -> list[str]:
        argv = ["codex"]
        if extra:
            argv += list(extra)
        if prompt:
            argv.append(prompt)
        return argv

    def rollout_files(self) -> list[Path]:
        """Every rollout file, newest first (by mtime)."""
        root = self.sessions_dir()
        if not root.exists():
            return []
        return [p for _, p in filetable.newest_first(_rollout_table(root))]

    def rollouts_for_session(self, session_id: str, new: bool = True) -> list[Path]:
        """Every rollout belonging to ``session_id``, oldest first.

        The id is the uuid in the filename, so the rollout table (a second
        old at most: one refresh asks for many sessions), else a targeted glob
        (a rollout written since), finds it without touching the rest of the
        history — this runs on the dashboard's poll loop, so it must not cost
        a full scan. Only when both miss (older
        layouts, or a resume that forked a file under a different name) do we
        fall back to reading each rollout's `session_meta` line.
        ``new`` False: a session that ended long ago (a chat's rotated-out
        one), whose rollout the table already lists if it has one — no glob
        (#305: a room's cards asked for its 200 rotated sessions, the glob
        walking every day folder for each that has no rollout)."""
        root = self.sessions_dir()
        if not session_id or not root.exists():
            return []
        tail = f"-{session_id}.jsonl"
        hits = [filetable.as_path(p) for p in _rollout_table(root, 1.0)
                if p.endswith(tail) and os.path.basename(p).startswith("rollout-")]
        if not hits and new:
            hits = list(root.glob(f"*/*/*/rollout-*-{session_id}.jsonl"))
        if not hits:
            # A missing old solo-session ID is common. Do not scan every
            # rollout's metadata per ID or every minute; the fallback index
            # scans filenames and reads metadata only for new files.
            hits = _legacy_rollouts(root, session_id)
        hits.sort(key=_mtime)
        return hits

    def session_stat(self, session_id: str, new: bool = True) -> dict | None:
        """Aggregate size + newest mtime across the session's rollouts, or None
        when it has none yet. Backs the dashboard's cheap "did the transcript
        grow?" poll, the same signal a Claude session's .jsonl provides."""
        files = self.rollouts_for_session(session_id, new)
        if not files:
            return None
        size, mtime = 0, 0.0
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            size += st.st_size
            mtime = max(mtime, st.st_mtime)
        return {"size": size, "mtime": mtime}

    def read_turns(self, session_id: str) -> list[dict]:
        """User + assistant text turns, chronological — the shape the chat view
        renders as bubbles: ``{timestamp, role, text}``.

        Only `response_item` messages are read: codex records each one a second
        time as an `event_msg`/`item_completed`, so taking both would double
        every bubble. `developer` messages (skills, harness preamble) and
        injected `<environment_context>`-style user blocks are dropped — they're
        plumbing, not conversation."""
        turns: list[dict] = []
        seen: set[str] = set()
        for path in self.rollouts_for_session(session_id):
            try:
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for ln in fh:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            d = json.loads(ln)
                        except json.JSONDecodeError:
                            continue
                        if d.get("type") != "response_item":
                            continue
                        payload = d.get("payload") or {}
                        if payload.get("type") != "message":
                            continue
                        role = payload.get("role")
                        if role not in ("user", "assistant"):
                            continue
                        text = _text_of(payload.get("content"))
                        if not text or (role == "user" and _is_synthetic(text)):
                            continue
                        key = payload.get("id") or f"{role}:{text}"
                        if key in seen:
                            continue
                        seen.add(key)
                        turns.append({"timestamp": d.get("timestamp", ""),
                                      "role": role, "text": text})
            except OSError:
                continue
        return turns

    def latest_session_id_for_cwd(self, cwd: str, since: float = 0.0, *,
                                  skip: set | frozenset = frozenset(),
                                  started_since: float = 0.0) -> str:
        """The most recent codex session id whose rollout ran in `cwd` — used to
        `codex resume <id>` a headless session after a dashboard restart, and to
        learn the id of a session the dashboard just launched (codex, unlike
        claude, won't take a caller-supplied one). `since` skips rollouts older
        than that epoch, which keeps a "which session did I just start?" lookup
        to the handful of files written after the launch. Ids in `skip` (another
        agent's) are passed over for the next one; `started_since` takes only a
        conversation that started at or after that epoch (a /new, #253)."""
        if not cwd:
            return ""
        target = os.path.normcase(os.path.normpath(cwd))
        try:
            for f in self.rollout_files():        # newest first
                if since and _mtime(f) < since:
                    break
                meta = _read_meta(f)
                if not meta:
                    continue
                if os.path.normcase(os.path.normpath(meta.get("cwd") or "")) != target:
                    continue
                sid = meta.get("session_id") or meta.get("id") or ""
                if not sid or sid in skip:
                    continue
                if started_since and _started(meta, f) < started_since:
                    continue
                return sid
        except OSError:
            pass
        return ""

    def delete_session(self, session_id: str) -> list[str]:
        """Remove every rollout file belonging to ``session_id`` (a resumed
        session writes several under the same id). Returns the deleted paths;
        empty when nothing matched. Used by the dashboard's unified Delete so a
        removed collaboration doesn't resurface as a Codex history row."""
        removed: list[str] = []
        for f in self.rollouts_for_session(session_id):
            try:
                f.unlink()
                removed.append(str(f))
            except OSError:
                pass
        return removed

    def cwd_for_session(self, session_id: str) -> str:
        """The cwd a rollout ran in, for the given session id (best-effort)."""
        for f in self.rollouts_for_session(session_id):
            cwd = (_read_meta(f).get("cwd") or "").strip()
            if cwd:
                return cwd
        return ""

    def resume_argv(self, session_id: str,
                    extra: list[str] | None = None) -> list[str]:
        argv = ["codex", "resume", session_id]
        if extra:
            argv += list(extra)
        return argv

    def ensure_trusted(self, cwd: str) -> bool:
        """Pre-authorize ``cwd`` in ``$CODEX_HOME/config.toml`` so an unattended
        launch skips Codex's interactive "Trust this folder?" screen, which
        otherwise holds the session before it reads its first message (ED-340:
        ten reviews waited eleven hours on it). Codex records trust as
        ``[projects."<path>"] trust_level = "trusted"`` and looks the folder up
        by its exact path: Codex 0.167 on Linux asks again for a lowercased
        key, and is satisfied by the folder's own entry in a worktree or in a
        subfolder of a repository (where its screen offers to trust the
        repository root instead). So the folder itself is trusted, by its
        real path and the path as given, never the repository it is in.

        All or nothing: every key's state is worked out first, and if any of
        them is marked otherwise (``trust_level = "untrusted"``, or a table
        for it without a trust level) nothing is written. Missing entries go
        in with one append, checked to parse first; a config that does not
        parse is left as it is. Idempotent; True when every key is trusted
        afterwards.

        Known limit: without a TOML parser (Python 3.10, no ``tomllib``) the
        config is never written, since a header appended to a config the hub
        cannot read could duplicate a table. Codex then shows its trust screen
        and the seat is flagged for a person (``first_run`` / ``attention``)."""
        if not cwd or tomllib is None:
            return False
        keys = _trust_keys(cwd)
        with _TRUST_LOCK:
            cfg = _codex_home() / "config.toml"
            try:
                text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
            except OSError:
                return False
            states = _trust_states(text, keys)
            if states is None:
                return False
            if any(st not in ("trusted", "missing") for st in states.values()):
                return False
            missing = [k for k in keys if states[k] == "missing"]
            if not missing:
                return True
            blocks = "\n".join(f"[projects.{_toml_key(k)}]\ntrust_level = \"trusted\"\n"
                               for k in missing)
            sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
            try:
                tomllib.loads(text + sep + blocks)
            except tomllib.TOMLDecodeError:
                # `projects` is written another way (dotted keys, an inline
                # table): a header of ours would break the file.
                return False
            try:
                cfg.parent.mkdir(parents=True, exist_ok=True)
                # Appended, not rewritten: Codex itself rewrites this file, and
                # a config kept as a link (dotfiles) stays a link.
                with cfg.open("a", encoding="utf-8") as fh:
                    fh.write(sep + blocks)
                return True
            except OSError:
                return False
