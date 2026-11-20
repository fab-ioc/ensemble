"""The hub chat (#16): the CEO asks in plain words for a change to the hub's
setup; one cheap model call (Haiku) per question answers with a whole new
setup file, or with a reply in words. The hub shows the lines that would
change and what #15's check of the new file says (its problems, what it
would change), and changes nothing until the CEO clicks Apply.

**Setups it can change.** ``KINDS`` holds one entry per setup file the chat
may change; today only the outside sources setup (``outside_setup``). For
anything else the model is told to say it cannot yet and what it can.

**The turns** are kept in ``<data folder>/hub-chat.json`` (``{"turns":
[...]}``, the last ``KEPT``). A user turn: ``{id, at, role: "user", text}``.
A hub turn: ``{id, at, role: "hub", kind, text, ask}`` where ``kind`` is
``reply`` (words only), ``error`` (the call failed or the answer was not in
the form asked for: nothing changed) or ``change``; ``ask`` is the id of the
user turn it answers. A change turn also has ``setup`` (the kind's key),
``proposed`` (the whole new file), ``baseHash`` (``text_hash`` of the file it
was made from), ``diff`` (the changed lines, ``[{before, after}]``),
``check`` (``{problems, changes, failed}``), ``state`` (proposed, applying,
applied, discarded, stale) and, once applied, ``result``.

**One question at a time**: a question waits on a background thread for the
model's answer and the check; the page polls ``view()`` while ``busy``. A
question or an apply left half done by a hub restart becomes an error turn
(or a note on the turn) the next time the turns are read.

**Apply** writes the new file and applies it at once
(``outside_setup.apply_new_text``), as "the hub chat" in the history. When
the file changed since the answer was made from it, nothing is written: the
turn becomes stale and the panel offers to ask again.

**The hub's Settings** (#410): the model may answer with the settings to
change instead (``SETTINGS:``); the turn then has ``setup: "settings"``,
``proposed`` (``{path: value as the save would keep it}``), ``before`` (each
one's value when it was made) and ``diff`` (``[{key, name, where, before,
after}]``, as shown). Checked, applied and kept in a history by
``hub_chat_settings``. ``history()`` is what the panel's History shows: those
changes and the setup file's applies the hub chat made, newest first.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
import uuid
from pathlib import Path

import durable
import hub_chat_settings as hs
import outside
import outside_route as route
import outside_setup

FILE_NAME = "hub-chat.json"
KEPT = 50                     # turns kept
TEXT_MAX = 4000               # a question's length
EARLIER_TURNS = 6             # earlier turns the model sees
EARLIER_CHARS = 600           # of each one's text
NOW_CHARS = 12000             # of routing-now.md the model sees
TIMEOUT_S = 120

PROPOSED, APPLYING, APPLIED, DISCARDED, STALE = "proposed", "applying", "applied", "discarded", "stale"
SETTINGS = "settings"         # a change turn's setup when it changes the hub's Settings

# The model call: (prompt, timeout) -> the answer's text. None: outside_model.RUN. Tests replace it.
RUN = None
BACKGROUND = True             # tests set False: the work runs on the calling thread

_LOCK = threading.RLock()
_BUSY = {"ask": False, "apply": set(), "loaded": False}


class Refused(Exception):
    pass


def _outside_text() -> str:
    """The setup file's text, or the text it would be written with when
    there is none: asking writes nothing (a Settings question on a hub with
    no outside source set up leaves no outside/ folder; #410 review 1)."""
    text = outside_setup._read_text(outside_setup.setup_path())
    if text is not None:
        return text
    with route.reading_only():          # no per-source table carried over by a read
        return outside_setup.first_text()


def _outside_apply(text: str, base_hash: str) -> dict:
    """Apply a change made from the setup file. When it was made from the
    text the file would be written with, the file is written first, and only
    if that text is still the one it was made from (else stale, nothing
    written)."""
    if not outside_setup.setup_path().exists():
        outside_setup.write_first(expect_hash=base_hash)
    return outside_setup.apply_new_text(text, base_hash, outside_setup.HUB_CHAT_BY)


KINDS = {
    "outside": {
        "label": "the outside sources setup",
        "what": "which outside sources the hub reads (Slack, Jira, ...: on, off, pause, or decide only) "
                "and which initiative their messages go to (each initiative's topic, Slack channels, "
                "Jira projects, the work items set for it, and the work items taken out of it with a "
                "Not: line)",
        "path": outside_setup.setup_path,
        "read": _outside_text,
        "hash": outside_setup.text_hash,
        "check": lambda text: outside_setup.apply_text(text, by=outside_setup.HUB_CHAT_BY, check_only=True),
        "apply": _outside_apply,
        "context": lambda: outside_setup.now_text(),
    },
}


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] hub chat: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def reset() -> None:
    """Forget work in progress (tests)."""
    with _LOCK:
        _BUSY.update(ask=False, apply=set(), loaded=False)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def path() -> Path:
    return outside.root().parent / FILE_NAME


def _load() -> dict:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    turns = d.get("turns") if isinstance(d, dict) else None
    st = {"turns": [t for t in turns if isinstance(t, dict)] if isinstance(turns, list) else []}
    if not _BUSY["loaded"]:
        # Work this hub did not finish (it stopped meanwhile) is over.
        _BUSY["loaded"] = True
        changed = False
        for t in st["turns"]:
            if t.get("pending"):
                t.pop("pending")
                changed = True
                st["turns"].append(_hub_turn(t["id"], "error", "The hub restarted before the answer came; "
                                                               "nothing changed. Ask again."))
                break
        for t in st["turns"]:
            if t.get("state") == APPLYING:
                t["state"] = PROPOSED
                t["result"] = {"error": "The hub restarted while this was being applied: look at the setup "
                                        "file to see whether it was."}
                changed = True
        if changed:
            _save(st)
    return st


def _save(st: dict) -> None:
    st["turns"] = st["turns"][-KEPT:]
    durable.write_text(path(), json.dumps(st, ensure_ascii=False, indent=1))


def _new_id() -> str:
    return "t-" + uuid.uuid4().hex[:12]


def _hub_turn(ask_id: str, kind: str, text: str, **more) -> dict:
    return {"id": _new_id(), "at": time.time(), "role": "hub", "kind": kind, "text": text,
            "ask": ask_id, **more}


def _find(st: dict, tid: str) -> dict | None:
    return next((t for t in st["turns"] if t.get("id") == tid), None)


def view() -> dict:
    """What the panel draws: the turns (a proposed change made from a file
    that changed since shows as stale) and whether a question is waiting."""
    with _LOCK:
        st = _load()
        hashes: dict[str, str | None] = {}
        turns = []
        for t in st["turns"]:
            if t.get("kind") == "change" and t.get("state") == PROPOSED and t.get("setup") == SETTINGS:
                try:
                    key = hs.stale_key(t.get("before") or {})
                except Exception:
                    key = None
                if key:
                    t = dict(t, state=STALE, staleWhy=hs.stale_line(key))
            elif t.get("kind") == "change" and t.get("state") == PROPOSED:
                k = t.get("setup") or "outside"
                if k not in hashes:
                    try:
                        hashes[k] = KINDS[k]["hash"](KINDS[k]["read"]())
                    except Exception:
                        hashes[k] = None
                if hashes[k] is not None and hashes[k] != t.get("baseHash"):
                    t = dict(t, state=STALE)
            turns.append({k: v for k, v in t.items() if k not in ("proposed", "before")})
        return {"turns": turns, "busy": _BUSY["ask"], "applying": sorted(_BUSY["apply"]),
                "setups": [{"key": k, "label": v["label"], "path": str(v["path"]())} for k, v in KINDS.items()]}


# ---------------------------------------------------------------------------
# A question
# ---------------------------------------------------------------------------

def ask(text: str) -> dict:
    """The CEO's question: kept as a turn, answered on a thread. Refused
    while another question waits."""
    text = (text or "").replace("\r\n", "\n").strip() if isinstance(text, str) else ""
    if not text:
        raise Refused("write what you want changed")
    if len(text) > TEXT_MAX:
        raise Refused(f"a question can be at most {TEXT_MAX:,} characters")
    with _LOCK:
        if _BUSY["ask"]:
            raise Refused("the hub is still answering the last question")
        st = _load()
        turn = {"id": _new_id(), "at": time.time(), "role": "user", "text": text, "pending": True}
        st["turns"].append(turn)
        _save(st)
        _BUSY["ask"] = True
    _run(_answer, turn["id"])
    return view()


def again(hub_turn_id: str) -> dict:
    """Ask Again on a stale or failed answer: the same question once more."""
    with _LOCK:
        st = _load()
        t = _find(st, hub_turn_id)
        q = _find(st, t.get("ask") or "") if t else None
        if not q:
            raise Refused("that question is no longer kept")
        text = q["text"]
    return ask(text)


def _run(fn, *args) -> None:
    if BACKGROUND:
        threading.Thread(target=fn, args=args, daemon=True, name="hub-chat").start()
    else:
        fn(*args)


def _answer(ask_id: str) -> None:
    try:
        turn = _answer_turn(ask_id)
    except Exception as e:      # never leave the question waiting
        _log(f"answer error: {type(e).__name__}: {str(e)[:200]}")
        turn = _hub_turn(ask_id, "error", f"Something went wrong while answering ({type(e).__name__}); "
                                          f"nothing changed.")
    with _LOCK:
        st = _load()
        q = _find(st, ask_id)
        if q is not None:
            q.pop("pending", None)
            st["turns"].append(turn)
            _save(st)
        _BUSY["ask"] = False


def _answer_turn(ask_id: str) -> dict:
    with _LOCK:
        st = _load()
        q = _find(st, ask_id)
        earlier = [t for t in st["turns"] if t.get("id") != ask_id][-EARLIER_TURNS:]
    if q is None:
        return _hub_turn(ask_id, "error", "The question was cleared before it was answered.")
    key = "outside"
    kind = KINDS[key]
    with route.reading_only():          # asking writes nothing under outside/ (#410)
        current = kind["read"]()
        base = kind["hash"](current)
        try:
            context = kind["context"]()[:NOW_CHARS]
        except Exception:
            context = "(could not be read)"
    import outside_model
    run = RUN or outside_model.RUN
    try:
        answer = run(prompt(q["text"], earlier, key, current, context, *hs.prompt_text()), TIMEOUT_S)
    except Exception as e:
        return _hub_turn(ask_id, "error", f"Haiku did not answer ({type(e).__name__}: {str(e)[:150]}); "
                                          f"nothing changed. Ask again.")
    got = parse(answer or "")
    if got is None:
        return _hub_turn(ask_id, "error", "Haiku's answer was not in the form asked for; nothing changed. "
                                          "Ask again, perhaps in other words.")
    if got["kind"] == "reply":
        return _hub_turn(ask_id, "reply", got["text"])
    if got["kind"] == SETTINGS:
        return _settings_turn(ask_id, got)
    new = got["setup"]
    if kind["hash"](new) == base:
        return _hub_turn(ask_id, "reply", (got["text"] + "\n\n" if got["text"] else "")
                         + "The setup file already says this; nothing to change.")
    try:
        with route.reading_only():
            res = kind["check"](new)
        check = {"problems": [x["text"] for x in res["problems"]], "changes": res["changes"],
                 "failed": res["failed"] or res["lookupFailed"]}
    except Exception as e:
        check = {"problems": [], "changes": [], "failed": f"the check did not run ({type(e).__name__})"}
    return _hub_turn(ask_id, "change", got["text"] or "Here is the change.", setup=key, proposed=new,
                     baseHash=base, diff=diff(current, new), check=check, state=PROPOSED)


def _settings_turn(ask_id: str, got: dict) -> dict:
    """A ``SETTINGS:`` answer: a change of the settings the save takes, or a
    reply when there is none (each refused setting in one line)."""
    p = hs.propose(got["settings"])
    lines = p["refused"] + p["problems"]
    if not p["changes"]:
        if not lines:
            lines = ["Settings already say this; nothing to change."]
        return _hub_turn(ask_id, "reply", "\n".join(lines))
    text = got["text"] or "Here is the change."
    return _hub_turn(ask_id, "change", text, setup=SETTINGS, proposed=p["changes"], before=p["before"],
                     diff=p["diff"], check={"problems": lines, "changes": [], "failed": ""}, state=PROPOSED)


PROMPT = """You help the CEO of a small software team change the setup of their hub (the program
that runs their AI agents and its dashboard). You answer one request at a time.

What you can change, and nothing else:
1. {label}, which sets {what}. It is the file below.
2. The hub's own Settings listed under SETTINGS below, each by its key.
Anything else about the hub (tasks, projects, people, agents' work) you cannot change: then reply
in words that you cannot, and say what you can change. A setting listed as not changeable: reply
with its line, nothing more.

Rules for a change:
- Answer with the WHOLE new file: every line you were not asked to change stays exactly as it is.
- Use only names written in the file or in the routing below. Never make up an initiative, a
  channel, a Jira project or a work item. If the request names one you cannot find, reply in words
  and ask which one is meant.
- An initiative's section is headed `## <its name>` with lines `Topic:`, `Slack channels:`,
  `Jira:`, `Work items:` and `Not:`. To take a work item out of an initiative (and keep the hub
  from adding it back), put it on that section's `Not:` line.
- You may leave out the part between `<!-- problems` and `<!-- end of problems -->`; the hub
  rewrites it.
Rules for a Settings change:
- Give only the settings to change, by their key, each with its new value as JSON.
- Use only the values listed for it. When the request asks for something no setting does (a
  setting by time of day, reviewers alone), reply in words with what is closest, or change the
  closest setting and say in the sentence what it also changes.
- Say what changes in plain words with each setting's name, not its key.
For a question (such as "what goes to Harbour?" or "how often does the PO check?"), answer in words
from the routing or the settings below.

Answer in exactly one of these three forms, nothing before or after it:

REPLY: <your answer in plain words>

or

CHANGE: <one plain sentence saying what you changed>
<<<SETUP {key}
<the whole new file>
SETUP>>>

or

SETTINGS: <one plain sentence saying what you changed, old and new>
<<<SETTINGS
{{"<key>": <new value>, ...}}
SETTINGS>>>

The file now ({path}):
<<<FILE
{current}
FILE>>>

What the hub routes from now (routing-now.md, read only; may be cut short):
<<<ROUTING
{context}
ROUTING>>>

SETTINGS you can change (key: name (where in Settings). Now: value. Values: what it takes):
{settings}

Settings you cannot change (key: the line to reply with):
{refused}

The conversation so far (oldest first; "proposed" changes were not applied):
{earlier}

The CEO's request:
<<<REQUEST
{request}
REQUEST>>>
"""


def _plain(text: str, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(text or ""))
    return text if len(text) <= limit else text[:limit] + "..."


def prompt(request: str, earlier: list[dict], key: str, current: str, context: str,
           settings: str = "(none)", refused: str = "(none)") -> str:
    lines = []
    for t in earlier:
        if t.get("role") == "user":
            lines.append(f"- CEO: {_plain(t.get('text'), EARLIER_CHARS)}")
        else:
            state = f" [{t.get('state')}]" if t.get("kind") == "change" else ""
            lines.append(f"- You ({t.get('kind')}{state}): {_plain(t.get('text'), EARLIER_CHARS)}")
    kind = KINDS[key]
    return PROMPT.format(label=kind["label"], what=kind["what"], key=key, path=kind["path"]().name,
                         current=current, context=context, earlier="\n".join(lines) or "(none)",
                         request=request, settings=settings, refused=refused)


_SETUP = re.compile(r"<<<SETUP[^\n]*\n(.*?)\n?SETUP>>>", re.S)
_SETTINGS = re.compile(r"<<<SETTINGS[^\n]*\n(.*?)\n?SETTINGS>>>", re.S)


def parse(answer: str) -> dict | None:
    """``{kind: reply, text}``, ``{kind: change, text, setup}`` or ``{kind:
    settings, text, settings: {key: value}}``, or None when the answer is in
    none of these forms."""
    a = answer.replace("\r\n", "\n").strip()
    a = re.sub(r"^```\w*\n(.*)\n```$", r"\1", a, flags=re.S).strip()
    m = _SETUP.search(a)
    n = _SETTINGS.search(a)
    if m and n:
        return None
    if n:
        c = re.search(r"^SETTINGS:[ \t]*(.*)$", a[:n.start()], re.M)
        body = re.sub(r"^```\w*\n(.*)\n```$", r"\1", n.group(1).strip(), flags=re.S)
        try:
            changes = json.loads(body)
        except ValueError:
            return None
        if not c or not isinstance(changes, dict) or not changes or not all(isinstance(k, str) for k in changes):
            return None
        return {"kind": SETTINGS, "text": c.group(1).strip()[:600], "settings": changes}
    if m:
        c = re.search(r"^CHANGE:[ \t]*(.*)$", a[:m.start()], re.M)
        if not c or not m.group(1).strip():
            return None
        return {"kind": "change", "text": c.group(1).strip()[:600], "setup": m.group(1).rstrip() + "\n"}
    if a.startswith("REPLY:"):
        text = a[len("REPLY:"):].strip()
        return {"kind": "reply", "text": text[:4000]} if text else None
    return None


def _lines(text: str) -> list[str]:
    """The lines as ``text_hash`` counts them: no problems part, no trailing
    spaces, at most one empty line in a row."""
    lines = [x.rstrip() for x in outside_setup.without_problems(text).strip().split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).split("\n")


def _heading(lines: list[str], upto: int) -> str:
    """The last ``## `` heading above line ``upto``, or ""."""
    for line in reversed(lines[:upto]):
        if line.startswith("## "):
            return line
    return ""


def diff(before: str, after: str) -> list[dict]:
    """The changed lines only, in hunks: ``[{before: [lines], after: [lines],
    section?}]`` (the problems part left out on both sides). ``section`` is
    the heading of the section the hunk sits in, since a ``Not:`` or ``Work
    items:`` line alone does not say which initiative it is in; left out
    when the hunk starts with a heading of its own or sits above them all."""
    a, b = _lines(before), _lines(after)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        hunk = {"before": a[i1:i2], "after": b[j1:j2]}
        first = next((x for x in hunk["after"] or hunk["before"] if x.strip()), "")
        section = "" if first.startswith("## ") else (_heading(b, j1) or _heading(a, i1))
        if section:
            hunk["section"] = section
        out.append(hunk)
    return out


# ---------------------------------------------------------------------------
# Apply, Discard, Clear
# ---------------------------------------------------------------------------

def apply(tid: str) -> dict:
    """The CEO's Apply on a proposed change: written and applied on a thread."""
    with _LOCK:
        st = _load()
        t = _find(st, tid)
        if not t or t.get("kind") != "change":
            raise Refused("there is no such change")
        if t.get("state") != PROPOSED:
            raise Refused(f"this change is {t.get('state')}; only a proposed change can be applied")
        t["state"] = APPLYING
        t.pop("result", None)
        _save(st)
        _BUSY["apply"].add(tid)
    _run(_apply, tid)
    return view()


def _apply(tid: str) -> None:
    with _LOCK:
        t = _find(_load(), tid)
    state, result = PROPOSED, {}
    if t and t.get("setup") == SETTINGS:
        state, result = _apply_settings(st_ask(t), t)
    elif t:
        kind = KINDS.get(t.get("setup") or "outside")
        try:
            res = kind["apply"](t["proposed"], t["baseHash"])
            state = APPLIED
            result = {"changes": res["changes"], "problems": [x["text"] for x in res["problems"]],
                      "failed": res["failed"] or res["lookupFailed"], "at": time.time()}
        except outside_setup.Changed:
            state = STALE
            result = {"error": "The setup file changed since this was made from it, so nothing was "
                               "written. Ask again to get a change made from the file as it is now."}
        except route.Refused as e:
            result = {"error": str(e)}
        except Exception as e:
            _log(f"apply error: {type(e).__name__}: {str(e)[:200]}")
            result = {"error": f"The apply did not go through ({type(e).__name__})."}
    with _LOCK:
        st = _load()
        t = _find(st, tid)
        if t is not None:
            t["state"], t["result"] = state, result
            _save(st)
        _BUSY["apply"].discard(tid)
    if state == APPLIED:
        _log(f"applied a change: {len(result['changes'])} change(s), {len(result['problems'])} problem(s)")


def st_ask(t: dict) -> str:
    """The question a hub turn answers, or ""."""
    with _LOCK:
        q = _find(_load(), t.get("ask") or "")
    return (q or {}).get("text") or ""


def _apply_settings(ask_text: str, t: dict) -> tuple[str, dict]:
    try:
        res = hs.apply(t.get("proposed") or {}, t.get("before") or {}, ask_text, t.get("text") or "")
    except hs.Changed as e:
        return STALE, {"error": hs.stale_line(e.key) + " Nothing was saved: ask again."}
    except ValueError as e:
        return PROPOSED, {"error": str(e)}
    except Exception as e:
        _log(f"settings apply error: {type(e).__name__}: {str(e)[:200]}")
        return PROPOSED, {"error": f"The apply did not go through ({type(e).__name__})."}
    if res.get("historyError"):
        _log(f"settings history not written: {res['historyError']}")
    return APPLIED, {"changes": [f"{r['name']}: {r['before']} → {r['after']}" for r in res["diff"]],
                     "problems": [res["historyError"]] if res.get("historyError") else [],
                     "failed": "", "at": res["at"]}


def discard(tid: str) -> dict:
    with _LOCK:
        st = _load()
        t = _find(st, tid)
        if not t or t.get("kind") != "change":
            raise Refused("there is no such change")
        if t.get("state") not in (PROPOSED, STALE):
            raise Refused(f"this change is {t.get('state')}")
        t["state"] = DISCARDED
        _save(st)
    return view()


def clear() -> dict:
    with _LOCK:
        if _BUSY["ask"] or _BUSY["apply"]:
            raise Refused("wait until the hub has finished answering or applying")
        _load()
        if path().exists():         # nothing to clear writes no file (#409)
            _save({"turns": []})
    return view()


def history() -> dict:
    """The panel's History: every change the hub chat applied, newest first:
    ``{items: [{kind: settings | routing, at, ask, changes: [{name, before,
    after}] | lines: [text]}]}``. The routing ones are the setup file's
    applies made by the hub chat, read only when that file is there."""
    items = [{"kind": SETTINGS, "at": r.get("at"), "ask": r.get("ask") or "",
              "changes": [c for c in r.get("changes") or [] if isinstance(c, dict)]}
             for r in hs.history()]
    try:
        state = outside_setup._state_path()
        applies = outside_setup.load_state()["applies"] if state.exists() else []
    except Exception:
        applies = []
    who = outside_setup._who(outside_setup.HUB_CHAT_BY)
    with _LOCK:
        done = [t for t in _load()["turns"] if t.get("kind") == "change" and t.get("setup") != SETTINGS
                and t.get("state") == APPLIED]
        asks = {t["id"]: t.get("text") or "" for t in _load()["turns"] if t.get("role") == "user"}
    for a in applies:
        if a.get("by") != who:
            continue
        at = a.get("at") if isinstance(a.get("at"), (int, float)) else 0
        # The question: the applied turn whose Apply ended just after it.
        near = [t for t in done if 0 <= ((t.get("result") or {}).get("at") or 0) - at < 120]
        ask_text = asks.get(near[0].get("ask"), "") if near else ""
        items.append({"kind": "routing", "at": at, "ask": ask_text,
                      "lines": [c for c in a.get("changes") or [] if isinstance(c, str)]})
    items.sort(key=lambda x: x.get("at") or 0, reverse=True)
    return {"items": items}


ACTIONS = {"ask": lambda d: ask(d.get("text")), "again": lambda d: again(str(d.get("id") or "")),
           "apply": lambda d: apply(str(d.get("id") or "")), "discard": lambda d: discard(str(d.get("id") or "")),
           "clear": lambda d: clear()}
