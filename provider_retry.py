"""Retry an idle agent whose last turn was refused by the model provider."""
from __future__ import annotations

import json
import hashlib
import re
import threading
import time
from pathlib import Path

_d = None
_LOCK = threading.RLock()
_PATH_LOCK = threading.Lock()
_SEEN = {}
_PATHS = {}
PATH_TTL_S = 30
MISS_TTL_S = 5
DELAYS = (120, 300, 600)
MAX_AGE_S = 3600          # an old refusal is shown but never woken on deploy
_TRANSIENT = re.compile(r"(?:overload|at capacity|temporar(?:ily|y) unavailable|internal server error|bad gateway|service unavailable|gateway timeout|server_error|internal_error|service_unavailable|upstream_timeout|rate limit|too many requests|\b(?:429|500|502|503|504|529)\b)", re.I)
_LIMIT = re.compile(r"(?:usage limit|model_requires_usage_credits|insufficient (?:credit|quota)|quota exceeded)", re.I)


def bind(dashboard_module):
    global _d
    _d = dashboard_module


def _transcript(part):
    """Avoid a rollout glob on every attention poll, including absent rollouts."""
    if (part.get("agent") or "claude") != "codex":
        return _d.rotation._transcript_of(part)
    sid = (part.get("sessionId") or "").strip()
    if not sid:
        return None, _d.rotation.read_rollout
    now = time.monotonic()
    with _PATH_LOCK:
        cached = _PATHS.get(sid)
        if cached and cached[1] > now:
            return cached[0], _d.rotation.read_rollout
        path, reader = _d.rotation._transcript_of(part)
        if len(_PATHS) > 500:
            _PATHS.clear()
        _PATHS[sid] = (path, now + (PATH_TTL_S if path else MISS_TTL_S))
        return path, reader


def _lines(path):
    if not path:
        return []
    try:
        with Path(path).open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read()
        lines = data.splitlines()
        return lines[1:] if size > 256 * 1024 else lines
    except OSError:
        return []


def _error_text(value):
    if isinstance(value, dict):
        return " ".join(str(value.get(k) or "") for k in ("message", "codex_error_info", "type", "code", "status"))
    return str(value or "")


def detect(path, agent):
    """Newest provider refusal only; a newer user turn or completion clears it."""
    if not path:
        return None
    try:
        stat = Path(path).stat()
        cache_key = (str(path), agent)
        signature = (stat.st_mtime_ns, stat.st_size)
        seen = _SEEN.get(cache_key)
        if seen and seen[0] == signature:
            return dict(seen[1]) if seen[1] else None
    except OSError:
        return None
    result = _detect_uncached(path, agent)
    if len(_SEEN) > 500:
        _SEEN.clear()
    _SEEN[cache_key] = (signature, result)
    return dict(result) if result else None


def _detect_uncached(path, agent):
    for raw in reversed(_lines(path)):
        try:
            d = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if agent == "codex":
            p = d.get("payload") or {}
            if d.get("type") != "event_msg":
                continue
            if p.get("type") in ("task_started", "user_message", "turn_aborted"):
                return None
            if p.get("type") != "task_complete":
                continue
            error = p.get("error")
            words = _error_text(error)
            if not error or _LIMIT.search(words) or not _TRANSIENT.search(words):
                return None
        else:
            if d.get("type") not in ("assistant", "user") or d.get("isSidechain"):
                continue
            if d.get("type") == "user":
                return None
            if not d.get("isApiErrorMessage"):
                return None
            msg = d.get("message") or {}
            content = msg.get("content") or []
            detail = content if isinstance(content, str) else " ".join(
                str(c.get("text") or "") for c in content if isinstance(c, dict))
            words = _error_text(d.get("apiError")) + " " + detail
            if _LIMIT.search(words) or not _TRANSIENT.search(words):
                return None
        return {"key": str(d.get("timestamp") or "") + ":" + hashlib.sha256(raw).hexdigest()[:16],
                "at": _d.model_limit._ts(d.get("timestamp")) or time.time(),
                "error": words[:300]}
    return None


def resolved(path, agent):
    """A successful final turn clears an earlier refusal's attempt count."""
    for raw in reversed(_lines(path)):
        try:
            d = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if agent == "codex":
            p = d.get("payload") or {}
            if d.get("type") == "event_msg" and p.get("type") == "task_complete":
                return not bool(p.get("error"))
            if d.get("type") == "event_msg" and p.get("type") in ("task_started", "user_message"):
                return False
        elif d.get("type") == "assistant" and not d.get("isSidechain"):
            return not d.get("isApiErrorMessage")
    return False


def _file():
    return _d.DASHBOARD_DIR / "provider_retries.json"


def _load():
    with _LOCK:
        try:
            data = json.loads(_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def _save(data):
    with _LOCK:
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


def _key(room_id, part):
    return f"{room_id}::{part.get('identity') or ''}" if room_id else ""


def prune_rooms(room_ids):
    """Discard attempts for deleted rooms and old PTY-keyed state."""
    if not room_ids:
        return
    with _LOCK:
        data = _load()
        kept = {k: v for k, v in data.items() if k.split("::", 1)[0] in room_ids and "::" in k}
        if kept != data:
            _save(kept)


def schedule(rec, now):
    """Advance only after a due retry was successfully submitted."""
    count = int(rec.get("attempts") or 0)
    if count >= len(DELAYS):
        return {**rec, "nextAt": 0}
    return {**rec, "attempts": count + 1,
            "nextAt": now + DELAYS[count + 1] if count + 1 < len(DELAYS) else 0,
            **({"lastSentKey": rec["key"], "settleAt": now + DELAYS[0]}
               if count + 1 == len(DELAYS) else {})}


def status(part, room_id="", now=None):
    try:
        now = time.time() if now is None else now
        path, _ = _transcript(part)
        hit = detect(path, part.get("agent") or "claude")
        if not hit:
            return None
        key = _key(room_id, part)
        rec = _load().get(key) or {} if key else {}
        stale = now - hit["at"] > MAX_AGE_S
        if rec.get("key") != hit["key"]:
            # A fresh error after an old incident starts a new retry budget.
            if now - float(rec.get("at") or now) > MAX_AGE_S:
                rec = {}
            attempts = int(rec.get("attempts") or 0)
            rec = {**hit, "attempts": attempts,
                   "nextAt": hit["at"] + DELAYS[attempts] if attempts < len(DELAYS) else 0,
                   "toldAt": rec.get("toldAt", 0),
                   "wokeAt": rec.get("wokeAt", 0),
                   "lastSentKey": rec.get("lastSentKey", ""),
                   "settleAt": rec.get("settleAt", 0)}
        if stale:
            rec = {**rec, "nextAt": 0, "stale": True}
        return rec
    except Exception:
        return None


def reason(rec, *, retry_enabled=True):
    if rec.get("stale"):
        return "the model provider was overloaded; the retry window expired"
    if not retry_enabled:
        return "the model provider was overloaded; automatic retry is paused"
    if rec.get("nextAt"):
        return "the model provider was overloaded, retrying at " + time.strftime("%H:%M", time.localtime(rec["nextAt"]))
    return "the model provider was overloaded; automatic retries were exhausted"


def _input_pending(sess):
    """A terminal editor already holds text that a retry must not overwrite."""
    if getattr(sess, "person_line_text", "") or getattr(sess, "hub_line_typed", False):
        return True
    try:
        tail = sess.tail() if callable(getattr(sess, "tail", None)) else ""
    except Exception:
        return True
    lines = (tail or "").splitlines()
    return bool(lines and re.match(r"^[›❯>]\s*\S", lines[-1].lstrip()))


def _store(key, rec):
    with _LOCK:
        data = _load()
        if data.get(key) != rec:
            data[key] = rec
            _save(data)


def tick(room, part, sess, now):
    """Return true while a provider refusal owns this task's idle state."""
    key = _key(room.get("id") or "", part)
    hit = status(part, room.get("id") or "", now)
    if not hit:
        if key and _load().get(key):
            path, _ = _transcript(part)
            if resolved(path, part.get("agent") or "claude"):
                with _LOCK:
                    data = _load()
                    if data.pop(key, None):
                        _save(data)
        return False
    if not key:
        return True
    if hit.get("stale"):
        return True
    with _LOCK:
        old = _load().get(key) or {}
        rec = hit if old.get("key") != hit["key"] else old
    if now >= rec["nextAt"] > 0 and sess and sess.alive():
        path, reader = _transcript(part)
        rot = _d.rotation
        if (rot._idle(part, reader(path)) and not rot._submitted_lately(sess)
                and not _input_pending(sess)
                and _d._type_input(sess, "[provider retry] Carry on.")):
            rec = schedule(rec, now)
    _store(key, rec)
    exhausted = rec.get("attempts") == len(DELAYS) and not rec.get("nextAt")
    settled = rec.get("key") != rec.get("lastSentKey") or now >= float(rec.get("settleAt") or now)
    if exhausted and settled and not rec.get("toldAt"):
        po = _d.room_po_id(room)
        if po:
            body = (f"**{_d.task_word(room)}: model provider overload**\n\n"
                    f"Three automatic retries did not clear the refusal. Consider switching "
                    f"the agent kind or model. Details with `ensemble_get_task {_d.task_arg(room)}`.")
            po_room = _d.chatroom.get_room(po, public=False)
            if po_room and _d.chatroom.post_report(po, "ensemble", _d.chatroom.po_identity(po_room), body,
                                                  {"reportKind": "digest", "providerRetryTask": room.get("id")}, wake=False):
                rec = {**rec, "toldAt": now}
                _store(key, rec)
    if rec.get("toldAt") and not rec.get("wokeAt"):
        wake = (f"[digest] {_d.task_word(room)}: model provider overload persisted "
                f"after three retries. Consider switching agent kind or model. "
                f"Details with ensemble_get_task {_d.task_arg(room)}.")
        if _d.stall._type_po(room, wake):
            _store(key, {**rec, "wokeAt": now})
    return True
