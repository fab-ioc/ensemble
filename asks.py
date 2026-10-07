"""Asks: the questions an agent marks for the person in a message, each
answered on its own with one click (GitHub issue 7, #163).

**The marker.** A line of its own that starts ``Ask:`` is one ask; the list
right under it, if any, is its options::

    Ask: Which colour should the badge be?
    - Amber (recommended): it is the colour that means "needs you"
    - Grey: quieter

    Ask (yes/no): Restart the hub tonight?

    Ask: What should the empty list say?

It reads as plain text wherever it is copied, folded or quoted, and it is the
shape both Claude and Codex already write: the line may carry list, quote or
heading marks and bold (``1. **Ask:** …``, ``- Ask: …``, ``**Ask (yes/no):**
…``), and the options may be any list (``-``, ``*``, ``1.``, ``a)``),
indented under a numbered ask or not. Nothing in a fenced code block is an
ask, so the rule can be quoted.

**Kinds.** An ask with options is a ``decision``; one whose options are just
Yes and No, or that says ``(yes/no)``, is ``yesno``; one with neither is
``open`` (a comment only). At most one option is recommended: the first one
marked ``(recommended)`` (or ``[recommended]``, ``— recommended``, a leading
``Recommended:``).

**An option's label** is what its button says and what the answer sends: the
bold words it starts with, else its words up to the first `` — ``, `` - `` or
``: ``. The rest is its detail.

The page parses the same way (``parseAsks`` in session.html); the tests hold
the two to the same answers. The hub parses only to check an answer against
the message it answers, to word the answer, and to find the asks still open
(the Needs you list).

**Answers** are kept in the room's points ledger (``asks``: message id → ask
number → the answer), once per ask from any device, and sent as the person's
message, ``Re “<ask>”: <option>`` with their comment under it — a point like
any other.
"""
from __future__ import annotations

import re
import threading
import time

_d = None   # the dashboard module (bind)


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


QUESTION_MAX = 200      # an ask's words kept and quoted
LABEL_MAX = 80          # an option's label
OPEN_DAYS = 7           # an unanswered ask counts as waiting this long
CACHE_S = 5.0           # the open asks of every room are worked out at most this often

_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_QUOTE = re.compile(r"^[ \t]*>[ \t]?")
_ASK = re.compile(
    r"^(?P<ind>[ \t]*)(?P<lead>(?:(?:#{1,6}|[-*+]|[0-9]{1,3}[.)])[ \t]+)*)"
    r"(?:\*\*|__)?[ \t]*ask(?:[ \t]*\((?P<kind>[^)\n]{1,24})\))?[ \t]*(?:\*\*|__)?[ \t]*:"
    r"[ \t]*(?:\*\*|__)?[ \t]*(?P<q>.*)$", re.I)
_ITEM = re.compile(r"^(?P<ind>[ \t]*)(?:[-*+]|[0-9]{1,3}[.)]|[A-Za-z][.)])[ \t]+(?P<t>\S.*)$")
_LIST_LEAD = re.compile(r"(?:[-*+]|[0-9]{1,3}[.)])[ \t]+")
# The page reads the same lines and spaces: every line break its "." stops at
# is a new line, and every other space either language trims is a plain one.
_LINE_BREAKS = re.compile(r"\r\n?|[\u2028\u2029]")
_OTHER_SPACE = re.compile(r"[\x0b\x0c\x1c-\x1f\x85\xa0\u1680\u2000-\u200a\u202f\u205f\u3000\ufeff]")
_CHECKBOX = re.compile(r"^\[[ xX]\][ \t]+")
_REC = re.compile(
    r"[ \t]*(?:\*\*|__|\*|_)?[(\[][ \t]*recommended[ \t]*[)\]](?:\*\*|__|\*|_)?"
    r"|[ \t]+[—–-][ \t]*(?:\*\*|__|\*|_)?recommended(?:\*\*|__|\*|_)?\.?[ \t]*$", re.I)
_REC_LEAD = re.compile(r"^(?:\*\*|__)?recommended(?:\*\*|__)?[ \t]*[:—–-][ \t]*(?:\*\*|__)?[ \t]*", re.I)
_BOLD_HEAD = re.compile(r"^(?:\*\*|__)(?P<l>[^*_\n]+?)(?:\*\*|__)[ \t]*(?P<rest>.*)$")
_SPLIT = re.compile(r"[ \t]+[—–][ \t]+|[ \t]+-{1,2}[ \t]+|:[ \t]+")
_MARKS = re.compile(r"\*\*|__|`")
_YESNO_TAG = re.compile(r"^(?:yes[ \t]*(?:/|-|or)[ \t]*no|y/n|yesno)$", re.I)
_OPEN_TAG = re.compile(r"^(?:open|free[ \t]*text|text)$", re.I)


def _indent(s: str) -> int:
    return len(s.replace("\t", "    "))


def _plain(s: str) -> str:
    return " ".join(_MARKS.sub("", s or "").split())


def _option(text: str) -> dict | None:
    t = _CHECKBOX.sub("", text.strip())
    rec = False
    if _REC_LEAD.match(t):
        t, rec = _REC_LEAD.sub("", t, count=1), True
    if _REC.search(t):
        t, rec = _REC.sub("", t, count=1).strip(), True
    m = _BOLD_HEAD.match(t)
    if m:
        label, detail = m.group("l"), re.sub(r"^[ \t]*[:—–-]+[ \t]*", "", m.group("rest"))
    else:
        s = _SPLIT.search(t)
        label, detail = (t[:s.start()], t[s.end():]) if s else (t, "")
    label = _plain(label).rstrip(".:;,").strip()
    if len(label) > LABEL_MAX:
        label = label[:LABEL_MAX - 1].rstrip() + "…"
    if not label:
        return None
    return {"label": label, "detail": _plain(detail), "recommended": rec}


def parse(text: str) -> list[dict]:
    """The asks a message marks, in order: ``{n, question, kind, options:
    [{label, detail, recommended}], recommended (index or -1), line, end}``
    (``line``..``end``: its lines, end exclusive). [] for an unmarked one."""
    lines = _OTHER_SPACE.sub(" ", _LINE_BREAKS.sub("\n", text or "")).split("\n")
    bare = [_QUOTE.sub("", ln, count=1) for ln in lines]
    fenced, out, i = False, [], 0
    while i < len(lines):
        ln = bare[i]
        if _FENCE.match(ln):
            fenced = not fenced
            i += 1
            continue
        m = None if fenced else _ASK.match(ln)
        if not m:
            i += 1
            continue
        start, nested = i, bool(_LIST_LEAD.search(m.group("lead") or ""))
        ind = _indent(m.group("ind"))
        q = re.sub(r"(?:\*\*|__)[ \t]*$", "", m.group("q")).strip()
        i += 1
        if not q:
            # "**Ask:**" alone: the question is the next line of words.
            j = i
            while j < len(lines) and not bare[j].strip():
                j += 1
            if j < len(lines) and not _ITEM.match(bare[j]) and not _ASK.match(bare[j]) and not _FENCE.match(bare[j]):
                q, i = bare[j].strip(), j + 1
        opts: list[dict] = []
        end = i
        j, gap = i, 0
        while j < len(lines):
            b = bare[j]
            if not b.strip():
                gap += 1
                if gap > 1:
                    break
                j += 1
                continue
            it = _ITEM.match(b)
            if not it or _ASK.match(b) or _FENCE.match(b):
                break
            if nested and _indent(it.group("ind")) <= ind:
                break       # a sibling of a listed ask, not an option of it
            o = _option(it.group("t"))
            if o:
                opts.append(o)
            gap = 0
            j += 1
            end = j
        i = end
        question = _plain(q)[:QUESTION_MAX]
        if not question:
            continue
        tag = " ".join((m.group("kind") or "").split())
        labels = sorted(o["label"].lower() for o in opts)
        if opts and labels == ["no", "yes"]:
            kind = "yesno"
        elif opts:
            kind = "decision"
        elif _YESNO_TAG.match(tag):
            kind = "yesno"
            opts = [{"label": "Yes", "detail": "", "recommended": False},
                    {"label": "No", "detail": "", "recommended": False}]
        else:
            kind = "open"
        rec = next((k for k, o in enumerate(opts) if o["recommended"]), -1)
        for k, o in enumerate(opts):
            o["recommended"] = k == rec
        out.append({"n": len(out), "question": question, "kind": kind, "options": opts,
                    "recommended": rec, "line": start, "end": max(end, start + 1)})
    return out


def answer_text(ask: dict, option: str, comment: str) -> str:
    """The person's message for an answer: ``Re “<ask>”: <option>`` and their
    comment under it (or after the colon, with no option)."""
    head = f"Re “{ask['question']}”: "
    option, comment = (option or "").strip(), (comment or "").strip()
    if option and comment:
        return f"{head}{option}\n\n{comment}"
    return head + (option or comment)


def check(ask: dict, option: str) -> str | None:
    """The option as the ask names it (case as written), or None when the ask
    has no such option. "" stays "" (a comment alone)."""
    option = (option or "").strip()
    if not option:
        return ""
    return next((o["label"] for o in ask["options"] if o["label"].lower() == option.lower()), None)


def validated(questions) -> list[dict]:
    """Normalize tool input, refusing any question that cannot make a card."""
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a non-empty list")
    if len(questions) > 10:
        raise ValueError("questions may contain at most 10 questions")
    out = []
    for n, item in enumerate(questions):
        where = f"questions[{n}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        q = item.get("question")
        if not isinstance(q, str) or not q.strip() or len(q.strip()) > QUESTION_MAX:
            raise ValueError(f"{where}.question must be 1-{QUESTION_MAX} characters")
        yesno = item.get("yesno", False)
        if not isinstance(yesno, bool):
            raise ValueError(f"{where}.yesno must be true or false")
        raw = item.get("options", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise ValueError(f"{where}.options must be a list")
        if yesno and raw:
            raise ValueError(f"{where}: choose yesno or options, not both")
        if raw and not 1 <= len(raw) <= 6:
            raise ValueError(f"{where}.options must have 1-6 options")
        opts = []
        if yesno:
            opts = [{"label": label, "detail": "", "recommended": False} for label in ("Yes", "No")]
        else:
            for k, opt in enumerate(raw):
                loc = f"{where}.options[{k}]"
                if not isinstance(opt, dict):
                    raise ValueError(f"{loc} must be an object")
                label = opt.get("label")
                if not isinstance(label, str) or not label.strip() or len(label.strip()) > LABEL_MAX:
                    raise ValueError(f"{loc}.label must be 1-{LABEL_MAX} characters")
                detail = opt.get("detail", "")
                if not isinstance(detail, str):
                    raise ValueError(f"{loc}.detail must be text")
                rec = opt.get("recommended", False)
                if not isinstance(rec, bool):
                    raise ValueError(f"{loc}.recommended must be true or false")
                opts.append({"label": label.strip(), "detail": detail.strip(), "recommended": rec})
        if len({o["label"].casefold() for o in opts}) != len(opts):
            raise ValueError(f"{where}.options have duplicate labels")
        recommended = [k for k, o in enumerate(opts) if o["recommended"]]
        if len(recommended) > 1:
            raise ValueError(f"{where}.options may have at most one recommended option")
        labels = sorted(o["label"].lower() for o in opts)
        kind = "yesno" if yesno or labels == ["no", "yes"] else "decision" if opts else "open"
        out.append({"n": n, "question": " ".join(q.split()), "kind": kind,
                    "options": opts, "recommended": recommended[0] if recommended else -1})
    return out


_DECISION = re.compile(r"^[ \t]*(?:\*\*)?Decision needed:(?:\*\*)?[ \t]*(.+)$", re.I | re.M)
_TRAIL_OPTION = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?(?:[A-Ca-c]|[1-6])"
    r"(?:[.)]|[ \t]*\(recommended\):|:)(?:\*\*)?[ \t]+(.+)$", re.I)


def safety(text: str) -> list[dict]:
    """Conservative fallback for a final, direct question in plain agent prose."""
    if parse(text):
        return []
    clean = []
    fenced = False
    for line in _LINE_BREAKS.sub("\n", text or "").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and not line.lstrip().startswith(">"):
            clean.append(line)
    body = "\n".join(clean).strip()
    if not body:
        return []
    parts = re.split(r"\n[ \t]*\n", body)
    tail = parts[-1].strip()
    decision = list(_DECISION.finditer(body))
    if decision and len(body) - decision[-1].start() <= 1000:
        q = decision[-1].group(1).strip()
        following = body[decision[-1].end():].strip().splitlines()
    elif tail.endswith("?"):
        tail_lines = tail.splitlines()
        q = tail_lines[-1].strip()
        preceding = tail_lines[:-1]
        following = [line for line in preceding if _TRAIL_OPTION.match(line)]
        if len(following) < 2 or len(preceding) - len(following) > 1:
            following = []
        if not following and len(parts) > 1:
            preceding = parts[-2].strip().splitlines()
            following = [line for line in preceding if _TRAIL_OPTION.match(line)]
            if len(following) < 2 or len(preceding) - len(following) > 1:
                following = []
    else:
        return []
    q = re.sub(r"^(?:[-*]|[1-6][.)])[ \t]+", "", q).strip("* ")
    if not q or len(q) > QUESTION_MAX:
        return []
    options = []
    for line in following:
        match = _TRAIL_OPTION.match(line)
        if match:
            opt = _option(match.group(1))
            if opt:
                options.append(opt)
        elif line.strip() and options:
            break
    if not 1 <= len(options) <= 6:
        options = []
    if options:
        for opt in options:
            opt["recommended"] = False
    else:
        options = [{"label": "Yes", "detail": "", "recommended": False},
                   {"label": "No", "detail": "", "recommended": False}]
    return [{"n": 0, "question": _plain(q), "kind": "decision" if len(options) > 2 else "yesno",
             "options": options, "recommended": -1}]


def of_message(message: dict, eligible: bool = True) -> list[dict]:
    if not eligible:
        return []
    stored = message.get("asks")
    if isinstance(stored, list):
        return stored
    parsed = parse(message.get("text") or "")
    return parsed if parsed else safety(message.get("text") or "")


# ---------------------------------------------------------------------------
# Which asks are still open (the Needs you list)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_SID_CACHE: dict[str, tuple] = {}       # sid → (stat, [(mid, ts, [questions])])
_ROOM_CACHE: dict[str, tuple] = {}      # room id → (updatedAt, [(mid, ts, [questions])])
_RESULT: tuple[float, dict] = (0.0, {})


def _marked(mid: str, ts: float, text: str, who: str, message: dict | None = None) -> tuple | None:
    found = of_message(message or {"text": text})
    return (mid, ts, [a["question"] for a in found], who) if found else None


def _session_asks(room: dict, sid: str, who: str) -> list[tuple]:
    if _d.room_po_id(room):
        return []
    st = _d.points._session_stat(sid)
    hit = _SID_CACHE.get(sid)
    if hit is not None and st is not None and hit[0] == st:
        return hit[1]
    turns = _d.solo_turns(room, sid) or {}
    found = []
    for mid, t in turns.items():
        if t.get("role") == "user":
            continue
        x = _marked(mid, _d._turn_epoch(t.get("timestamp")), t.get("text") or "", who)
        if x:
            found.append(x)
    if st is not None:
        _SID_CACHE[sid] = (st, found)
    return found


def message_asks(room: dict) -> list[tuple]:
    """A room's agents' messages that mark asks: ``[(mid, ts, [questions],
    who)]``, from what the room holds (a team's messages)."""
    has_po = bool(_d.room_po_id(room))
    agents = {p.get("identity") for p in room.get("participants", [])
              if p.get("kind") == "agent" and not str(p.get("role") or "").lower().startswith("reviewer")}
    out = []
    for m in room.get("messages") or []:
        if m.get("from") in agents and (not has_po or m.get("askAudience") == "user") \
                and m.get("kind") not in ("report", "notice") \
                and m.get("askAudience") != "po" and not m.get("rang") \
                and (m.get("to") or "").lower() in ("", "all", "user"):
            x = _marked(m.get("id", ""), float(m.get("ts") or 0), m.get("text") or "",
                        m.get("from") or "", m)
            if x and x[0]:
                out.append(x)
    return out


def _room_asks(summary: dict) -> list[tuple]:
    rid = summary.get("id", "")
    agents = [p for p in summary.get("participants", []) if p.get("kind") == "agent"]
    if (summary.get("mode") or "") == "solo":
        room = _d.chatroom.get_room(rid)
        if room is None:
            return []
        out = message_asks(room)
        for p in agents:
            sid = (p.get("sessionId") or "").strip()
            if not sid:
                continue
            out += _session_asks(room, sid, p.get("identity") or "")
        return out
    hit = _ROOM_CACHE.get(rid)
    if hit is not None and hit[0] == summary.get("updatedAt"):
        return hit[1]
    room = _d.chatroom.get_room(rid)
    found = message_asks(room) if room else []
    _ROOM_CACHE[rid] = (summary.get("updatedAt"), found)
    return found


def answered(room_id: str) -> dict:
    """The asks answered in a room: ``{mid: {"<n>": answer}}``."""
    if not _d.points.exists(room_id):
        return {}
    return _d.points.load(room_id).get("asks") or {}


def open_in(summary: dict, now: float | None = None) -> list[dict]:
    """The asks of a room still waiting for the person, oldest first:
    ``[{mid, n, question, ts, who}]``. An ask counts for :data:`OPEN_DAYS`
    (one whose time is unknown, until answered), and until the person
    writes in the chat after it (``asksSettledAt``: they answered in words,
    or set it aside); one whose message was approved with a thumbs up ("go
    with your recommendation") is answered if it has a recommendation. The
    page's ``openAsks`` keeps the same rules."""
    now = time.time() if now is None else now
    rid = summary.get("id", "")
    marked = [x for x in _room_asks(summary) if not x[1] or now - x[1] < OPEN_DAYS * 86400]
    if not marked:
        return []
    led = _d.points.load(rid) if _d.points.exists(rid) else {}
    done, approved = led.get("asks") or {}, led.get("approvals") or {}
    settled = float(led.get("asksSettledAt") or 0)
    settled = max(settled, *(float(p.get("answeredAt") or 0)
                              for p in summary.get("participants", []) if p.get("kind") == "agent"))
    out = []
    for mid, ts, qs, who in marked:
        if ts and ts < settled:
            continue
        got = done.get(mid) or {}
        for n, q in enumerate(qs):
            if str(n) in got:
                continue
            if mid in approved and _approved_settles(rid, mid, n):
                continue
            out.append({"mid": mid, "n": n, "question": q, "ts": ts, "who": who})
    out.sort(key=lambda a: (a["ts"], a["n"]))
    return out


def _approved_settles(room_id: str, mid: str, n: int) -> bool:
    found = balloon_asks(room_id, mid)
    return n < len(found) and found[n]["recommended"] >= 0


def balloon_asks(room_id: str, mid: str) -> list[dict]:
    room = _d.chatroom.get_room(room_id)
    if room is None:
        return []
    has_po = bool(_d.room_po_id(room))
    for message in room.get("messages") or []:
        if message.get("id") == mid:
            agents = {p.get("identity") for p in room.get("participants", []) if p.get("kind") == "agent"}
            if message.get("from") not in agents or message.get("kind") in ("notice",) \
                    or message.get("rang") or (message.get("to") or "").lower() not in ("", "all", "user") \
                    or message.get("askAudience") == "po" or (has_po and message.get("askAudience") != "user"):
                return []
            return of_message(message)
    if has_po:
        return []
    try:
        text = _d.points._balloon_text(room_id, mid, {})
    except Exception:       # noqa: BLE001
        return []
    return of_message({"text": text or ""})


def open_by_room(summaries: list[dict], now: float | None = None) -> dict[str, list[dict]]:
    """:func:`open_in` for every room, worked out at most every
    :data:`CACHE_S` (the attention poll asks every second and a half)."""
    global _RESULT
    now = time.time() if now is None else now
    with _LOCK:
        if now - _RESULT[0] < CACHE_S:
            return _RESULT[1]
        out = {}
        for s in summaries:
            if not s.get("launched", True) or _d.normalize_workflow(s.get("workflow")) == "done":
                continue        # a draft asks nothing yet; a Done task's asks are over
            try:
                got = open_in(s, now)
            except Exception as e:      # noqa: BLE001 — one room's trouble hides no other's
                print(f"[asks] {s.get('id')}: open asks not read: {e!r}", flush=True)
                continue
            if got:
                out[s["id"]] = got
        _RESULT = (now, out)
        return out


def forget() -> None:
    """Drop the worked-out open asks (an answer was just recorded)."""
    global _RESULT
    with _LOCK:
        _RESULT = (0.0, {})
