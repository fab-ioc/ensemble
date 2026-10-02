"""A Claude model at its own limit: how the hub sees it, remembers it and
seats around it (ED-159).

On 2026-10-01 at 17:17 three trading tasks stopped mid-turn on one line:

    You've reached your Fable limit. Run /usage-credits to continue or switch
    models with /model.

Their seats named no model and Settings named none either, so Claude Code's own
default ran (``claude-fable-5-1``), and that model has a limit of its own,
separate from the plan's 5-hour and 7-day windows the allocation reads. The
CLI wrote the line as the turn's last transcript entry and went idle. Nothing
in the hub knew the words, the transcript reader did not count the entry as
the end of a turn, and every resume the next morning ended on the same line
within a second.

What this module holds:

* **The line.** :func:`parse_line` reads the CLI's wording (the model it names
  and a reset time if it gives one). :func:`from_transcript` finds it as the
  *last* entry of a conversation, where only the CLI can put it: an assistant
  entry flagged ``isApiErrorMessage``. A quoted line (a tool result, a chat
  message, an agent discussing this module) is never such an entry, so it
  never counts. The screen path (``attention._BLOCK_RULES``) reads the same
  words off the terminal under the rules for the 5h/7d lines.
* **The memory.** :func:`note` keeps each limited model in
  ``DASHBOARD_DIR/model_limits.json`` with the time it was seen and when it
  clears: the reset time the line names, else ``CLEAR_AFTER_S`` later.
* **The model a seat really runs.** :func:`resolve` follows the launch: the
  seat's model, else Settings › Agent models, else ``model`` in Claude's own
  ``settings.json``, else the CLI's default as last seen in a transcript.
  :func:`choose` then never seats a limited model a seat did not name: it
  falls back to the next model in ``FALLBACK`` and says so. A seat that names
  a limited model is honoured, with a warning.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


# A limit with no reset time in its line is treated as cleared this long after
# it was seen (the plan's own windows are five hours).
CLEAR_AFTER_S = 5 * 3600

# The Claude models, newest family first: a limited model falls back to the
# next one along that is not limited ("fable" → "opus" → "sonnet" → "haiku").
FALLBACK = ("fable", "opus", "sonnet", "haiku")

# The CLI's own wording. "You've reached your Fable limit." with or without the
# apostrophe, any model name of one or two words ("Opus 5.5"), and the advice
# that follows it ("Run /usage-credits to continue").
LINE = re.compile(r"you'?ve\s*reached\s*your\s*((?:fable|opus|sonnet|haiku)(?:\s[\d.]+)?)\s*limit", re.I)
CREDITS = re.compile(r"run\s*/usage-credits", re.I)
_RESET = re.compile(r"resets?\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.I)
# The transcript's mark of a model at its limit (seen 2026-10-01).
API_ERRORS = ("model_requires_usage_credits",)

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------

def family(model: str) -> str:
    """The model family a name, alias or id belongs to ("fable" for "Fable",
    "claude-fable-5-1" or "fable[1m]"), or "" when it names none."""
    low = (model or "").strip().lower()
    for f in FALLBACK:
        if re.search(rf"(?:^|[^a-z]){f}(?:[^a-z]|$)", low):
            return f
    return ""


def title(model: str) -> str:
    """How a model is named to a person: "Fable", "Opus"."""
    f = family(model)
    return f.title() if f else (model or "").strip()


def parse_line(text: str, at: float | None = None) -> dict | None:
    """``{model, family, line, resetAt}`` when ``text`` holds the CLI's
    model-limit line, else None. ``resetAt`` is the next time the line's
    "resets 3pm" names after ``at`` (0 when it names none)."""
    text = text or ""
    m = LINE.search(text)
    if not m and not CREDITS.search(text):
        return None
    model = " ".join(m.group(1).split()) if m else ""
    start = m.start() if m else CREDITS.search(text).start()
    line = " ".join(text[start:start + 300].split())
    return {"model": model, "family": family(model), "line": line,
            "resetAt": reset_at(text, time.time() if at is None else at)}


def reset_at(text: str, at: float) -> float:
    """The reset time the line names, as the first such clock time after
    ``at`` in local time; 0 when it names none."""
    m = _RESET.search(text or "")
    if not m:
        return 0.0
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    minute = int(m.group(2) or 0)
    lt = time.localtime(at)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    return t + 86400 if t <= at else t


def _ts(s) -> float:
    """An ISO transcript timestamp ("2026-10-01T15:17:48.678Z") as epoch."""
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _text_of(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    return " ".join(str(b.get("text") or "") for b in content or []
                    if isinstance(b, dict) and b.get("type") == "text")


def _last_entries(path: Path, want: int = 65536) -> list[dict]:
    """The conversation's newest user/assistant entries, newest first."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - want))
            raw = f.read()
    except OSError:
        return []
    out = []
    for ln in reversed(raw.decode("utf-8", "replace").splitlines()):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") in ("user", "assistant") \
                and not d.get("isSidechain"):
            out.append(d)
    return out


def from_entry(d: dict) -> dict | None:
    """The limit an entry is, or None: an assistant entry the CLI wrote as an
    API error (``isApiErrorMessage``), saying the model-limit line."""
    if d.get("type") != "assistant" or not d.get("isApiErrorMessage"):
        return None
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    at = _ts(d.get("timestamp")) or time.time()
    hit = parse_line(_text_of(msg), at)
    if hit is None and d.get("apiError") in API_ERRORS:
        hit = {"model": "", "family": "", "line": " ".join(_text_of(msg).split())[:300],
               "resetAt": 0.0}
    if hit is None:
        return None
    # A line that names no model is a model's limit only when the CLI says so:
    # its usage-credits error, or the advice to run /usage-credits.
    credits = d.get("apiError") in API_ERRORS or bool(CREDITS.search(hit.get("line", "")))
    return {**hit, "at": at, "credits": credits}


_SEEN: dict[str, tuple[tuple, dict | None]] = {}   # path -> ((mtime, size), hit)


def from_transcript(path) -> dict | None:
    """The limit a Claude conversation ended on: ``{model, family, line,
    resetAt, at}`` when its newest entry is the CLI's limit line, else None.
    Anything after it (a reply, a tool call) means the agent got past it.
    Read again only when the file changed (attention asks on every poll)."""
    if not path:
        return None
    p = Path(path)
    try:
        st = p.stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        return None
    seen = _SEEN.get(str(p))
    if seen and seen[0] == key:
        return dict(seen[1]) if seen[1] else None
    entries = _last_entries(p)
    hit = from_entry(entries[0]) if entries else None
    if len(_SEEN) > 500:
        _SEEN.clear()
    _SEEN[str(p)] = (key, hit)
    return dict(hit) if hit else None


def last_model(path) -> str:
    """The model the conversation's newest real reply came from, or ""."""
    if not path:
        return ""
    for d in _last_entries(Path(path)):
        if d.get("type") != "assistant" or d.get("isApiErrorMessage"):
            continue
        model = ((d.get("message") or {}).get("model") or "") if isinstance(d.get("message"), dict) else ""
        if model and not model.startswith("<"):
            return model
    return ""


# ---------------------------------------------------------------------------
# The memory
# ---------------------------------------------------------------------------

def _file() -> Path:
    return _d.DASHBOARD_DIR / "model_limits.json"


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    for k in ("limits", "reported"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def _save(d: dict) -> None:
    f = _file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError:
        pass


def note(hit: dict, *, room: str = "", identity: str = "", fallback_model: str = "") -> dict | None:
    """Remember that a model hit its limit. ``fallback_model`` names the model
    when the line does not (the model the conversation ran on), and only for a
    hit the CLI marked as a model's limit (``credits``): any other limit line
    is not one. Returns the stored record, or None when no model can be named."""
    fam = hit.get("family") or (family(fallback_model) if hit.get("credits") else "")
    if not fam:
        return None
    at = float(hit.get("at") or time.time())
    until = float(hit.get("resetAt") or 0) or at + CLEAR_AFTER_S
    rec = {"model": hit.get("model") or title(fam), "family": fam, "at": at, "until": until,
           "line": hit.get("line", ""), "room": room, "identity": identity}
    with _LOCK:
        d = _load()
        old = d["limits"].get(fam) or {}
        if float(old.get("at") or 0) >= at:
            return old                      # this sighting, or a newer one, stands
        d["limits"][fam] = rec
        _save(d)
    return rec


def limited(model: str, now: float | None = None) -> dict | None:
    """The standing limit of ``model``'s family, or None (none, or cleared)."""
    fam = family(model)
    if not fam:
        return None
    now = time.time() if now is None else now
    rec = _load()["limits"].get(fam)
    if isinstance(rec, dict) and float(rec.get("until") or 0) > now:
        return rec
    return None


def active(now: float | None = None) -> dict[str, dict]:
    """Every standing limit, by family."""
    now = time.time() if now is None else now
    return {k: v for k, v in _load()["limits"].items()
            if isinstance(v, dict) and float(v.get("until") or 0) > now}


def learn_default(model: str) -> None:
    """Remember the model Claude Code runs when nothing names one (seen in the
    transcript of a session launched with no model)."""
    if not family(model):
        return
    with _LOCK:
        d = _load()
        if d.get("cliDefault") != model:
            d["cliDefault"] = model
            _save(d)


def cli_default() -> str:
    """The CLI's own default model as last seen, or ""."""
    v = _load().get("cliDefault")
    return v if isinstance(v, str) else ""


def reported(key: str) -> dict:
    return dict(_load()["reported"].get(key) or {})


REPORTED_KEEP_S = 86400


def set_reported(key: str, rec: dict | None) -> None:
    """Record (or with None drop) what was told about ``key``; records older
    than a day are pruned on the way."""
    with _LOCK:
        d = _load()
        cut = time.time() - REPORTED_KEEP_S
        d["reported"] = {k: v for k, v in d["reported"].items()
                         if isinstance(v, dict) and max(float(v.get(t) or 0) for t in
                                                        ("at", "toldAt", "wokeAt", "reviewFailed")) > cut}
        if rec is None:
            d["reported"].pop(key, None)
        else:
            d["reported"][key] = rec
        _save(d)


# ---------------------------------------------------------------------------
# The model a seat really runs
# ---------------------------------------------------------------------------

def _settings_model() -> str:
    try:
        s = _d.load_settings()
        return (_d.agent_models.normalise(s.get("agentModels"), s.get("defaultModel"))
                ["claude"]["model"] or "")
    except Exception:
        return ""


def _claude_own() -> str:
    try:
        return _d.agent_models.claude_own()["model"]
    except Exception:
        return ""


def resolve(seat_model: str = "") -> tuple[str, str]:
    """(model, source) a Claude seat runs on when the hub launches it: source
    is ``seat`` | ``settings`` | ``claude settings`` | ``default`` (the CLI's,
    as last seen) | ``""`` (not known yet)."""
    seat_model = (seat_model or "").strip()
    if seat_model:
        return seat_model, "seat"
    m = _settings_model()
    if m:
        return m, "settings"
    m = _claude_own()
    if m:
        return m, "claude settings"
    m = cli_default()
    if m:
        return m, "default"
    return "", ""


def choose(seat_model: str = "", now: float | None = None) -> dict:
    """What a Claude launch passes, judged against the standing limits:
    ``{model, resolved, source, limited, fallback, note}``. ``model`` is what
    to pass ("" for the CLI's own choice). A seat that names a model keeps it
    even when it is limited (``note`` warns); otherwise a limited model is
    replaced by the next one in ``FALLBACK`` that is not limited."""
    resolved, source = resolve(seat_model)
    launch = resolved if source in ("seat", "settings") else ""
    out = {"model": launch, "resolved": resolved, "source": source, "limited": None,
           "fallback": "", "note": ""}
    rec = limited(resolved, now) if resolved else None
    if not rec:
        return out
    out["limited"] = rec
    when = time.strftime("%H:%M", time.localtime(float(rec.get("at") or 0)))
    until = time.strftime("%H:%M", time.localtime(float(rec.get("until") or 0)))
    name = title(resolved)
    if source == "seat":
        out["note"] = (f"Warning: the seat names {name}, which hit its model limit at "
                       f"{when} (until {until}); kept because the seat names it.")
        return out
    fam = family(resolved)
    order = list(FALLBACK[FALLBACK.index(fam) + 1:] if fam in FALLBACK else FALLBACK) + \
        list(FALLBACK[:FALLBACK.index(fam)] if fam in FALLBACK else ())
    nxt = next((m for m in order if not limited(m, now)), "")
    if not nxt:
        out["note"] = (f"Warning: {name} ({_SOURCE_WORDS.get(source, source)}) hit its model "
                       f"limit at {when} (until {until}) and every other model is limited too.")
        return out
    out.update(model=nxt, fallback=nxt,
               note=(f"Runs {title(nxt)}: {name} ({_SOURCE_WORDS.get(source, source)}) hit "
                     f"its model limit at {when} (until {until})."))
    return out


_SOURCE_WORDS = {"settings": "chosen in Settings", "claude settings": "Claude's own setting",
                 "default": "the default", "seat": "the seat's"}


def display(seat_model: str = "") -> str:
    """How a Claude seat's model reads to a person: the seat's own, else the
    model it resolves to with where that comes from (``fable (default)``,
    ``opus (Settings)``), and ``fable at its limit, next start opus`` when a
    limit makes the next launch fall back: the first word is what runs now.
    "" only when nothing is known."""
    c = choose(seat_model)
    if c["source"] == "seat":
        return c["resolved"]
    if c["fallback"]:
        return f"{family(c['resolved']) or c['resolved']} at its limit, next start {c['fallback']}"
    if not c["resolved"]:
        return ""
    word = {"settings": "Settings", "claude settings": "Claude settings",
            "default": "default"}.get(c["source"], c["source"])
    return f"{family(c['resolved']) or c['resolved']} ({word})"
