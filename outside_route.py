"""Routing outside messages to initiatives: the routing table and the first
two routing steps (design #1, section 4, as changed by the revised plan:
P7, P12, P13, P15; round 2 step 1: one table for every source).

**The routing table.** The hub has one table, with one row per initiative
(a project of this hub), whatever the source. A row has a ``topic`` (a few
words step 5's model matches the text against), its ``items`` (work items:
tickets ``ABC-123``, epics ``epic ABC-100`` and pull requests ``repo#123``,
each with where it came from, the CEO or the PO, and when; round 2 step 2
matches messages against them) and its ``places`` per source type: the
type's ``route_fields()`` without the shared columns (for Slack:
``channels``, a list, checked first). Two sources of one type share its
places. A new initiative shows as an empty row by itself; the row of a
deleted one drops out of the table and out of routing. The table is kept as
JSON in the hub's data folder, ``outside/routing.json``, never in code.

**The per-source tables before it.** Round 1 kept one table per source,
``outside/routing-<source id>.json``. Each is carried over into the one
table the first time the table is read (``_carry_over``): its channels to
the row's places for the source's type, its topic to the row's topic when
the row has none (the other one is noted), its history with each change
marked with the source. The old file is then renamed
``routing-<source id>.json.carried`` and kept. Carrying over again (the hub
stopped before the rename) changes nothing. A table whose source is not
configured is left until it is. One source's view of the table, its places
and the topic as round 1 had them, is still served (``table``,
``set_row``) for older callers.

**Who may change a row.** The CEO, from the Routing page of the Input
sources panel, through the hub's API; and any agent the hub runs (Claude or
Codex, PO or task agent) through ``ensemble_routing``, but only the row of
its own initiative. A channel or a work item may be listed in one row only:
a save that would list it in a second row is refused, naming the row that
has it. Every change is kept in the table's history: who (the CEO, or the
agent's identity and its initiative), when, and the row before and after.

**Routing, steps 1 and 2.** The frame (``outside.py``) hands each source's
unrouted messages to ``route``:

0. A message already routed goes where it went the first time. If the hub
   stops after a message was queued but before the frame saved its
   unrouted list, the frame hands it on again; it must not reach a second PO.
0b. A work item (a ticket or pull request, #13) listed in exactly one
   row: that initiative (``item_decision``). The items are those the
   source says the message is about (Jira: its ticket, ``Source.work_items``)
   and those its text names; a Jira ticket's epic counts only when no row
   lists one of those.
1. The message is in a channel listed in exactly one row: that initiative.
   No model call.
1b. Its other places (``Source.place_values``; Jira: the ticket's epic,
   then its components, then its project) name exactly one row, in the
   order the source's place columns come: that initiative. A column whose
   value two rows list decides nothing; the next one is tried. No model
   call. Places only route: they never widen what a source reads.
2. A reply in a thread already routed: the thread's initiative (thread
   memory, kept 30 days after the thread's last message). A correction
   changes the thread's memory, so one correction fixes every later reply;
   a thread corrected by the CEO follows the correction even in a channel a
   row lists (it is checked before step 1).
3. Anything else "needs a model": routing step 3 (``outside_model``, one
   Haiku call per batch against each row's topic) decides it, and becomes
   the frame's router in place of ``route``. Its answer is an initiative,
   "small talk" or "no match". Small talk is dropped without a log line; a
   message no initiative fits is dropped too, with a "no match" line in the
   log for step 7's view. Both are still remembered as decided, so a message
   handed on twice is never asked about or logged twice. The model also sees
   the last few messages of the same conversation (``recent``): what the
   router has already handed on from that DM, group DM, channel or thread,
   and where each went. Nothing is read from the service for it. So
   "moved" or a bare link after a routed message can go where the
   conversation went, while chat in between stays small talk. A DM has no
   memory of its own that decides without the model: the replay of Slack
   Man's messages (#11) showed most DM messages after a work message are
   chat that no PO needs. A correction by the CEO changes where the
   corrected message "went" in what the model sees next.

Each routed message goes into the frame's queue (``outside.queue_put``) and
is one line in the log, ``outside/log/<year>-<month>.jsonl``: where it went
and why (channel, thread, model, correction). Links in the logged text are
turned into short forms ("example-repo 1234", "ABC-123"). Lines are kept
90 days. Step 7's view reads them with ``log_lines``.

Bound to the dashboard module like ``outside``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import durable
import outside
import sources
import work_items
from sources.base import Field, Message

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    """Bind to the hub and become the frame's router and places."""
    global _d
    _d = dashboard_module
    outside.ROUTER = route
    outside.PLACES = places


# The column the channel rule reads, and the one step 5's model reads.
CHANNELS = "channels"
TOPIC = "topic"
# The one table's other parts: a row's work items and its places per source type.
ITEMS = "items"
PLACES = "places"
SHARED = (TOPIC, ITEMS)         # columns every source shares, not a source's own
# What ``set_initiative`` takes besides the columns: items to add or remove.
ADD_ITEMS = "addItems"
REMOVE_ITEMS = "removeItems"
NOT = "not"                     # items a person took out of a row: the hub does not add them back to it (#16)
# The kinds of work item a row lists, and who listed one.
TICKET = "ticket"
EPIC = "epic"
PR = "pr"
ITEM_KINDS = (TICKET, EPIC, PR)
ITEMS_MAX = 200                 # at most this many work items in one row
FROM_PO = "po"                  # listed by an agent (``ensemble_routing``)
FROM_CEO = "ceo"                # listed by the CEO on the Routing page
FROM_SETUP = "setup file"       # listed in the setup file (outside_setup, #15)
PEOPLE_SET = (FROM_PO, FROM_CEO, FROM_SETUP)    # items people listed, not the hub
# Listed by the hub itself (round 2 step 2): "task #12" (a hub task of the
# initiative names it), a message routed to the initiative named it, or the
# CEO's Send elsewhere moved it here with its message.
FROM_TASK = "task "             # followed by the task's number: "task #12"
FROM_ROUTED = "routed"
FROM_MOVED = "send elsewhere"
HUB = {"kind": "hub"}           # who made a change the hub made by itself

# How a message was routed (``how`` in decisions and log lines).
BY_ITEM = "item"                # a work item listed in one row (round 2 step 2)
BY_CHANNEL = "channel"
BY_PLACE = "place"              # a place other than a channel (a Jira epic, component, project)
BY_THREAD = "thread"
BY_MODEL = "model"              # step 5
BY_CORRECTION = "correction"
NEEDS_MODEL = "needs a model"   # neither step 1 nor step 2 decided
# What the model may answer besides an initiative (``dropped`` in decisions):
# neither is delivered; only "no match" is written to the log (CEO, P10, P11).
SMALL_TALK = "small talk"
NO_MATCH = "no match"
DROPPED = (SMALL_TALK, NO_MATCH)

THREAD_KEEP_S = 30 * 86400      # thread memory: kept this long after the thread's last message
DECIDED_KEEP_S = 30 * 86400     # a message's decision: kept as long as thread memory, so it can be corrected
DECIDED_MAX = 2000              # at most this many decisions are kept (the oldest go first)
THREADS_MAX = 2000              # at most this many threads are remembered (the oldest go first)
CHANNEL_NAMES_MAX = 5000        # channel id and name pairs remembered
# The earlier messages the model sees with a message (step 3).
RECENT_N = 5                    # of one conversation (a DM, group DM, channel or thread)
RECENT_CHARS = 200              # the start of each one's text
RECENT_KEEP_S = 2 * 86400       # older ones are no help to the model
RECENT_MAX = 1000               # at most this many conversations are kept
LOG_KEEP_S = 90 * 86400         # log lines are kept this long
LOG_TEXT_CHARS = 300            # the start of the text a log line keeps
HISTORY_MAX = 2000              # the change history keeps the newest this many
KEPT_S = DECIDED_KEEP_S         # a logged message is kept whole this long, for the log view's actions
KEPT_MAX = DECIDED_MAX          # at most this many whole messages per source
LIST_MAX = 100                  # at most this many values in one list cell
ITEM_CHARS = 120                # one value of a list cell
TEXT_CHARS = 1000               # a text cell (a topic may list tickets and pull requests)

# The columns every row has, whatever the sources.
TOPIC_FIELD = Field(TOPIC, "Topic", "text", "",
                    help="A few words Haiku matches the message against.")
ITEMS_FIELD = Field(ITEMS, "Work items", "list", [],
                    help="Tickets (ABC-123), epics (epic ABC-100) and pull requests (repo#123) of the initiative. "
                         "A message naming one goes to this initiative first. The hub adds most by itself.")

_LOCK = threading.RLock()


class Refused(Exception):
    """A change that is not allowed; its words say why, for the CEO or agent."""


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] outside routing: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _one_file() -> Path:
    return outside.root() / "routing.json"


def _memory_file(source_id: str) -> Path:
    return outside.root() / f"routes-{source_id}.json"


def _log_dir() -> Path:
    return outside.root() / "log"


def _kept_file(source_id: str) -> Path:
    return outside.root() / f"logged-{source_id}.json"


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(path: Path, data: dict) -> None:
    durable.write_text(path, json.dumps(data, indent=1, ensure_ascii=False))


def _num(v, default: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


# ---------------------------------------------------------------------------
# Initiatives and sources
# ---------------------------------------------------------------------------

def initiatives() -> dict[str, str]:
    """{project id: name} of the hub's current initiatives, in board order."""
    d = _d if _d is not None else outside._d
    load = getattr(d, "load_projects", None)
    if load is None:
        return {}
    out = {}
    for p in load() or []:
        if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]:
            out[p["id"]] = str(p.get("name") or p["id"])
    return out


def place_fields(kind: str) -> list[Field]:
    """The place columns a source type has in the one table (Slack:
    ``channels``): its ``route_fields()`` without the shared columns. Empty
    for a type this hub has no reader for."""
    make = sources.TYPES.get(kind or "")
    if make is None:
        return []
    try:
        fields = list(make().route_fields())
    except Exception:
        return []
    return [f for f in fields if f.key not in SHARED]


def _kind_of(source_id: str) -> str:
    return (outside.source_config(source_id) or {}).get("type") or ""


def place_types() -> list[dict]:
    """The source types the one table has place columns for: the types of
    the configured sources that have place fields, once each, in the order
    the sources were added. ``[{kind, label, fields}]``."""
    out, have = [], set()
    for cfg in outside.load_config().values():
        kind = cfg.get("type") or ""
        if kind in have:
            continue
        fields = place_fields(kind)
        if not fields:
            continue
        have.add(kind)
        out.append({"kind": kind, "label": sources.TYPES[kind].label or kind, "fields": fields})
    return out


def columns(source_id: str) -> list[Field]:
    """One source's view of the table, as before round 2: its type's place
    columns, then the shared topic. Refused for a source that is not
    configured or whose type this hub has no reader for; empty for a type
    that has no places (it is not routed by table)."""
    cfg = outside.source_config(source_id)
    if cfg is None:
        raise Refused(f"there is no input source called {source_id!r}")
    make = sources.TYPES.get(cfg.get("type") or "")
    if make is None:
        raise Refused(f"this hub has no reader for the type {cfg.get('type') or '(none)'}")
    src = outside.instance(source_id, cfg) or make()
    fields = [f for f in src.route_fields() if f.key not in SHARED]
    return fields + [TOPIC_FIELD] if fields else []


def routed_sources() -> list[str]:
    """The configured sources that are routed by the table (their type has
    place columns)."""
    out = []
    for sid in outside.load_config():
        try:
            if columns(sid):
                out.append(sid)
        except Refused:
            continue
    return out


# ---------------------------------------------------------------------------
# Cell values
# ---------------------------------------------------------------------------

def norm(value: str) -> str:
    """A channel as compared: no spaces round it, no leading '#', lower case.
    So "#Made-Up-Pricing", "made-up-pricing" and a channel's id typed in any case
    are each one channel."""
    return str(value or "").strip().lstrip("#").strip().lower()


def _empty(f: Field):
    if f.kind == "list":
        return []
    if f.kind == "text":
        return ""
    return f.default


def _clean(f: Field, value):
    """A cell's value as saved, or Refused with what is wrong."""
    if f.kind == "list":
        if value is None:
            return []
        if isinstance(value, str):
            value = [v for v in re.split(r"[,\s]+", value) if v]
        if not isinstance(value, (list, tuple)):
            raise Refused(f"{f.label} must be a list")
        out, have = [], set()
        for v in value:
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                raise Refused(f"{f.label}: {v!r} is not a name")
            s = str(v).strip()
            if f.key == CHANNELS:
                s = s.lstrip("#").strip()
            if not s:
                continue
            if len(s) > ITEM_CHARS:
                raise Refused(f"{f.label}: {s[:40]}… is longer than {ITEM_CHARS} characters")
            k = norm(s) if f.key == CHANNELS else s
            if k in have:
                continue
            have.add(k)
            out.append(s)
        if len(out) > LIST_MAX:
            raise Refused(f"{f.label}: at most {LIST_MAX} values")
        return out
    if f.kind == "text":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise Refused(f"{f.label} must be text")
        s = " ".join(value.split())
        if len(s) > TEXT_CHARS:
            raise Refused(f"{f.label} is longer than {TEXT_CHARS} characters")
        return s
    if f.kind == "switch":
        return bool(value)
    if f.kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise Refused(f"{f.label} must be a number")
        return value
    if f.kind == "choice":
        allowed = [c[0] for c in f.choices]
        if value not in allowed:
            raise Refused(f"{f.label} must be one of {allowed}")
        return value
    raise Refused(f"{f.label} cannot be changed here")


def _row_values(cols: list[Field], stored) -> dict:
    """A stored row read against the columns: every column there, anything
    unreadable taken as empty."""
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for f in cols:
        try:
            out[f.key] = _clean(f, stored.get(f.key)) if f.key in stored else _empty(f)
        except Refused:
            out[f.key] = _empty(f)
    return out


def _is_empty(cols: list[Field], values: dict) -> bool:
    return all(values.get(f.key) == _empty(f) for f in cols)


# ---------------------------------------------------------------------------
# Work items (stored and shown here; matched in messages from R2-2)
# ---------------------------------------------------------------------------

_ITEM_PR_URL = re.compile(r"^https?://(?:www\.)?github\.com/[^/\s]+/([^/\s]+)/pulls?/(\d+)", re.I)
_ITEM_PR = re.compile(r"^(?:[A-Za-z0-9_.-]+/)?([A-Za-z0-9_.-]+)\s*#\s*(\d+)$")
_ITEM_PR_SPACE = re.compile(r"^(?:[A-Za-z0-9_.-]+/)?([A-Za-z0-9_.-]+)\s+(\d+)$")   # "example-repo 1234"
_ITEM_TICKET = re.compile(r"^([A-Za-z][A-Za-z0-9]*-\d+)$")
_ITEM_BROWSE = re.compile(r"^https?://\S+/browse/([A-Za-z][A-Za-z0-9]*-\d+)", re.I)
_ITEM_WORD = re.compile(r"^(ticket|epic|pr|pull request)\s*[:\s]\s*(\S.*)$", re.I)


def parse_item(value, kind: str = "") -> tuple[str, str]:
    """``(kind, key)`` of a work item as a person or agent writes it: a
    ticket key (``ABC-123``, or its ``…/browse/ABC-123`` link), an epic
    (``epic ABC-100``, or the kind given), a pull request (``repo#123``,
    ``org/repo#123``, ``repo 123`` when the name has a lower-case letter,
    or its GitHub link). A ticket key is kept
    in upper case and a pull request as ``repo#123`` in lower case, the
    forms R2-2 matches messages against. Refused with what is wrong."""
    if isinstance(value, dict):
        kind = str(value.get("kind") or kind or "")
        value = value.get("key")
    if not isinstance(value, str) or not value.strip():
        raise Refused("a work item needs its key: a ticket (ABC-123), an epic "
                      "(epic ABC-100) or a pull request (repo#123)")
    s = " ".join(value.split())
    kind = str(kind or "").strip().lower()
    m = _ITEM_WORD.match(s)
    if m:
        kind = kind or m.group(1).lower()
        s = m.group(2).strip()
    kind = PR if kind == "pull request" else kind
    if kind and kind not in ITEM_KINDS:
        raise Refused(f"a work item is a ticket, an epic or a pull request, not {kind!r}")
    if len(s) > ITEM_CHARS:
        raise Refused(f"work item {s[:40]}… is longer than {ITEM_CHARS} characters")
    pr = _ITEM_PR_URL.match(s) or _ITEM_PR.match(s)
    if not pr:
        # "repo 123" is a pull request, as the log writes one; "ABC 12" is a
        # ticket key typed with a space, not a pull request of a repo "abc".
        sp = _ITEM_PR_SPACE.match(s)
        if sp and (kind == PR or (not kind and re.search(r"[a-z]", sp.group(1)))):
            pr = sp
        elif sp:
            raise Refused(f"{s!r} is not a ticket key: write it with a dash ({sp.group(1).upper()}-{sp.group(2)}), "
                          f"or a pull request as {sp.group(1).lower()}#{sp.group(2)}")
    if pr:
        if kind not in ("", PR):
            raise Refused(f"{s} is a pull request, not {'an' if kind == EPIC else 'a'} {kind}")
        return PR, f"{pr.group(1).lower()}#{int(pr.group(2))}"
    t = _ITEM_BROWSE.match(s) or _ITEM_TICKET.match(s)
    if t:
        if kind == PR:
            raise Refused(f"{s} is a ticket key, not a pull request (repo#123)")
        return kind or TICKET, t.group(1).upper()
    raise Refused(f"{s!r} is not a ticket key (ABC-123) or a pull request (repo#123 or its link)")


def item_text(item: dict) -> str:
    """A work item as the page shows it and takes it back: ``ABC-12``,
    ``epic ABC-10``, ``repo#123``."""
    return f"epic {item['key']}" if item.get("kind") == EPIC else str(item.get("key") or "")


def _item_list(value) -> list:
    """Items as given: a list, or text with one item per comma or line."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,\n]+", value) if v.strip()]
    if not isinstance(value, (list, tuple)):
        raise Refused("work items must be a list")
    return list(value)


def _stored_items(value) -> list[dict]:
    """A row's items as read from the file: well-formed ones, once each."""
    out, have = [], set()
    for x in value if isinstance(value, list) else []:
        if not (isinstance(x, dict) and x.get("kind") in ITEM_KINDS
                and isinstance(x.get("key"), str) and x["key"] and x["key"] not in have):
            continue
        have.add(x["key"])
        out.append({"kind": x["kind"], "key": x["key"], "from": str(x.get("from") or ""),
                    "at": _num(x.get("at"))})
    return out


# ---------------------------------------------------------------------------
# The one table: storage and the carry-over of the per-source tables
# ---------------------------------------------------------------------------

def _empty_row() -> dict:
    return {TOPIC: "", ITEMS: [], PLACES: {}}


def _clean_row(stored) -> dict:
    """A stored row as read: its topic, items and the places of every source
    type it has. The places of a type no source has any more are kept as
    stored, so a source removed and added again finds its rows."""
    stored = stored if isinstance(stored, dict) else {}
    try:
        topic = _clean(TOPIC_FIELD, stored.get(TOPIC))
    except Refused:
        topic = ""
    places = {}
    raw = stored.get(PLACES) if isinstance(stored.get(PLACES), dict) else {}
    for kind, v in raw.items():
        if not (isinstance(kind, str) and kind and isinstance(v, dict)):
            continue
        fields = place_fields(kind)
        if not fields:
            if v:
                places[kind] = dict(v)
            continue
        vals = _row_values(fields, v)
        if not _is_empty(fields, vals):
            places[kind] = vals
    out = {TOPIC: topic, ITEMS: _stored_items(stored.get(ITEMS)), PLACES: places}
    # The Not list is kept only when it has something, so rows without one
    # stay as they were before it existed.
    nots = [{"kind": i["kind"], "key": i["key"]} for i in _stored_items(stored.get(NOT))]
    if nots:
        out[NOT] = nots
    return out


def _row_empty(row: dict) -> bool:
    return not row[TOPIC] and not row[ITEMS] and not row[PLACES] and not row.get(NOT)


def not_keys(row: dict | None) -> set[str]:
    """The keys of the items on a row's Not list: the hub does not add them
    to that row by itself."""
    return {i["key"] for i in (row or {}).get(NOT) or []}


# A preview of the setup file (outside_setup.apply_text with check_only)
# changes its own copy of the table, on its own thread only: nothing is
# written, nothing logged, and no other thread sees it.
_PREVIEW = threading.local()


def preview_table(on: bool) -> None:
    """Start (a copy of the table as it is) or end this thread's preview."""
    _PREVIEW.table = None
    if on:
        _PREVIEW.table = json.loads(json.dumps(_load_one()))


def _previewing() -> bool:
    return getattr(_PREVIEW, "table", None) is not None


def _load_one() -> dict:
    """``{rows, history, carried}`` of the one table, after carrying over any
    per-source table still there. During this thread's preview, its copy."""
    if _previewing():
        return json.loads(json.dumps(_PREVIEW.table))
    with _LOCK:
        _carry_over()
        d = _read(_one_file())
    rows = d.get("rows") if isinstance(d.get("rows"), dict) else {}
    return {"rows": {k: _clean_row(v) for k, v in rows.items() if isinstance(k, str) and k
                     and isinstance(v, dict)},
            "history": [h for h in d.get("history") or [] if isinstance(h, dict)],
            "carried": d.get("carried") if isinstance(d.get("carried"), dict) else {}}


def _save_one(t: dict) -> None:
    if _previewing():
        _PREVIEW.table = json.loads(json.dumps({**t, "history": t["history"][-HISTORY_MAX:]}))
        return
    _write(_one_file(), {"version": 1, "rows": t["rows"], "history": t["history"][-HISTORY_MAX:],
                         "carried": t["carried"]})


_NOT_CARRIED: set[str] = set()


def _carried_name(p: Path) -> Path:
    """Where an old table goes once carried over: ``routing-<id>.json.carried``
    (no longer read as a table), with a number when that name is taken."""
    target = p.with_name(p.name + ".carried")
    n = 2
    while target.exists():
        target = p.with_name(f"{p.name}.carried-{n}")
        n += 1
    return target


def _carry_over(now: float | None = None) -> None:
    """Carry each per-source table (``routing-<source id>.json``, round 1 and
    #9's import) into the one table, once, then rename it
    ``routing-<source id>.json.carried`` (kept, never deleted). A row's
    channels go to its places for the source's type, its topic to the shared
    topic, and its history comes along, each change marked with the source.
    It only fills: a list (channels) gains what the old table lists; a topic
    the row has already stays, and the one not carried is noted in
    ``carried``. So it is the same if it is done again (the hub stopped
    between writing the one table and renaming the old file) or if a second
    source of the same type had a table. A table whose source is not
    configured, or whose type this hub has no reader for, is left where it
    is until it is."""
    try:
        olds = sorted(p for p in outside.root().glob("routing-*.json") if p.is_file())
    except OSError:
        return
    if not olds:
        return
    now = time.time() if now is None else now
    d = _read(_one_file())
    rows = {k: v for k, v in (d.get("rows") or {}).items() if isinstance(k, str) and isinstance(v, dict)} \
        if isinstance(d.get("rows"), dict) else {}
    hist = [h for h in d.get("history") or [] if isinstance(h, dict)]
    carried = d.get("carried") if isinstance(d.get("carried"), dict) else {}
    seen = {(h.get("source"), h.get("at"), h.get("projectId")) for h in hist if h.get("source")}
    names = initiatives()
    done = []
    for p in olds:
        sid = p.name[len("routing-"):-len(".json")]
        kind = _kind_of(sid)
        fields = place_fields(kind)
        if not fields:
            if p.name not in _NOT_CARRIED:
                _NOT_CARRIED.add(p.name)
                _log(f"{p.name} is not carried over into the one table yet: no configured "
                     f"source {sid!r} of a type this hub reads")
            continue
        old = _read(p)
        notes, n_rows = [], 0
        old_rows = old.get("rows") if isinstance(old.get("rows"), dict) else {}
        for pid, v in old_rows.items():
            if not (isinstance(pid, str) and pid and isinstance(v, dict)):
                continue
            n_rows += 1
            nm = names.get(pid, pid)
            row = _clean_row(rows.get(pid))
            if TOPIC in v:
                try:
                    topic = _clean(TOPIC_FIELD, v.get(TOPIC))
                except Refused as e:
                    topic = ""
                    notes.append(f"{nm}: the topic was not carried over ({e})")
                if topic and not row[TOPIC]:
                    row[TOPIC] = topic
                elif topic and topic != row[TOPIC]:
                    notes.append(f"{nm}: the row has a topic already, so this one was not "
                                 f"carried over: {topic}")
            vals = _row_values(fields, row[PLACES].get(kind))
            for f in fields:
                if f.key not in v:
                    continue
                if f.kind == "list":
                    got = []
                    # One value at a time: a bad one (a file edited by hand) is noted, the rest kept.
                    raw = v[f.key]
                    raw = ([x for x in re.split(r"[,\s]+", raw) if x] if isinstance(raw, str) else
                           [] if raw is None else raw if isinstance(raw, list) else [raw])
                    for x in raw:
                        try:
                            got += [y for y in _clean(f, [x])
                                    if norm(y) not in {norm(z) for z in got}]
                        except Refused as e:
                            notes.append(f"{nm}: a value of {f.label} was not carried over ({e})")
                    have = {norm(x) for x in vals[f.key]}
                    new = [x for x in got if norm(x) not in have]
                    room = max(0, LIST_MAX - len(vals[f.key]))
                    if len(new) > room:
                        notes.append(f"{nm}: {len(new) - room} of {f.label} were not carried over, "
                                     f"a row has at most {LIST_MAX}: {', '.join(new[room:])}")
                        new = new[:room]
                    vals[f.key] = vals[f.key] + new
                    if f.key == CHANNELS:
                        ids = _ids_of_kind(kind)
                        for x in new:
                            other = next((o for o, r in rows.items() if o != pid and canon(x, ids) in
                                          {canon(y, ids) for y in ((_clean_row(r)[PLACES].get(kind) or {})
                                                                    .get(f.key) or [])}), None)
                            if other:
                                notes.append(f"{nm}: #{x} is also in {names.get(other, other)}'s row, so its messages "
                                             f"go to neither until it is taken out of one")
                    continue
                try:
                    got = _clean(f, v[f.key])
                except Refused as e:
                    notes.append(f"{nm}: {f.label} was not carried over ({e})")
                    continue
                if vals[f.key] == _empty(f):
                    vals[f.key] = got
                elif got != vals[f.key]:
                    notes.append(f"{nm}: {f.label} was not carried over, the row has one already")
            if not _is_empty(fields, vals):
                row[PLACES][kind] = vals
            rows[pid] = row
        n_hist = 0
        for h in old.get("history") or []:
            if not isinstance(h, dict):
                continue
            key = (sid, h.get("at"), h.get("projectId"))
            if key in seen:
                continue
            seen.add(key)
            n_hist += 1
            hist.append({**{k: h[k] for k in ("at", "projectId", "projectName", "by") if k in h},
                         "source": sid, "before": _old_change(h.get("before"), kind, fields),
                         "after": _old_change(h.get("after"), kind, fields)})
        target = _carried_name(p)
        prev = carried.get(sid) if isinstance(carried.get(sid), dict) else None
        if prev:
            # Carried again (the rename failed, or a table was put back): the
            # first record stays, with this one's file and counts added.
            also = prev.get("also") if isinstance(prev.get("also"), list) else []
            if target.name != prev.get("file") and target.name not in also:
                # A rename that keeps failing gives the same name on each read.
                also = [*also, target.name]
            rec = {**prev, "rows": max(int(_num(prev.get("rows"))), n_rows),
                   "changes": int(_num(prev.get("changes"))) + n_hist}
            if also:
                rec["also"] = also
            old_notes = prev.get("notes") if isinstance(prev.get("notes"), list) else []
            more = old_notes + [n for n in notes if n not in old_notes]
            if more:
                rec["notes"] = more
            carried[sid] = rec
        else:
            carried[sid] = {"at": now, "type": kind, "file": target.name, "rows": n_rows,
                            "changes": n_hist, **({"notes": notes} if notes else {})}
        done.append((p, target))
    if not done:
        return
    hist.sort(key=lambda h: _num(h.get("at")))
    try:
        _write(_one_file(), {"version": 1, "rows": rows, "history": hist[-HISTORY_MAX:],
                             "carried": carried})
    except OSError as e:
        _log(f"the one routing table could not be written, nothing carried over: {e}")
        return
    for p, target in done:
        try:
            p.rename(target)
        except OSError as e:
            _log(f"{p.name} was carried over but could not be renamed: {e}")
            continue
        _log(f"{p.name} carried over into the one routing table, kept as {target.name}")


def _old_change(v, kind: str, fields: list[Field]) -> dict:
    """A per-source history entry's row (``{channels, topic}``) in the one
    table's shape: only the columns it had."""
    v = v if isinstance(v, dict) else {}
    out = {}
    if TOPIC in v:
        out[TOPIC] = v[TOPIC]
    pl = {f.key: v[f.key] for f in fields if f.key in v}
    if pl:
        out[PLACES] = {kind: pl}
    return out


# ---------------------------------------------------------------------------
# One source's view of the table (round 1's shape: its places and the topic)
# ---------------------------------------------------------------------------

def _source_values(row: dict, kind: str, cols: list[Field]) -> dict:
    places = [f for f in cols if f.key != TOPIC]
    vals = _row_values(places, row[PLACES].get(kind))
    return {f.key: row[TOPIC] if f.key == TOPIC else vals[f.key] for f in cols}


def _live_rows(source_id: str, cols: list[Field] | None = None) -> dict[str, dict]:
    """{project id: values} for the current initiatives only, as one
    source sees them: its places and the topic."""
    cols = columns(source_id) if cols is None else cols
    kind = _kind_of(source_id)
    stored = _load_one()["rows"]
    return {pid: _source_values(stored.get(pid) or _empty_row(), kind, cols) for pid in initiatives()}


def topics() -> dict[str, str]:
    """{project id: topic} of the current initiatives: one topic per
    initiative, whatever the source (the model's input)."""
    stored = _load_one()["rows"]
    return {pid: (stored.get(pid) or _empty_row())[TOPIC] for pid in initiatives()}


def _ids_by_name(source_id: str) -> dict[str, str]:
    """{channel name: channel id}, both as compared, learned from the
    messages routed: a row may list a channel by its name or by its id, and
    both must count as the same channel."""
    with _LOCK:
        pairs = _load_memory(source_id)["channelNames"]
    return {name: cid for cid, name in pairs.items()}


def _ids_of_kind(kind: str) -> dict[str, str]:
    """{channel name: id} learned by every configured source of a type."""
    out: dict[str, str] = {}
    for sid, cfg in outside.load_config().items():
        if (cfg.get("type") or "") == kind:
            out.update(_ids_by_name(sid))
    return out


def canon(channel: str, ids: dict[str, str]) -> str:
    """A channel as compared, its id when its name's id is known."""
    n = norm(channel)
    return ids.get(n, n)


def _channel_owners(rows: dict[str, dict], ids: dict[str, str]) -> dict[str, list[str]]:
    """{channel as compared (its id when known): [project ids listing it]}."""
    out: dict[str, list[str]] = {}
    for pid, values in rows.items():
        for c in values.get(CHANNELS) or []:
            got = out.setdefault(canon(c, ids), [])
            if pid not in got:
                got.append(pid)
    return out


def table(source_id: str) -> dict:
    """One source's view of the table (round 1's shape, kept for the import
    of #9 and older callers): its place columns and the topic, one row per
    current initiative (empty rows included), the last change of each row,
    and any channel listed in two rows. A save refuses that, but one row can
    list a channel by its name and another by its id before the hub has
    seen a message there that pairs the two; or a file was edited by hand.
    Such a channel routes nobody until it is fixed."""
    cols = columns(source_id)
    kind = _kind_of(source_id)
    names = initiatives()
    t = _load_one()
    last = _last_changes(t["history"], names)
    rows, live = [], {}
    for pid, name in names.items():
        values = _source_values(t["rows"].get(pid) or _empty_row(), kind, cols)
        live[pid] = values
        h = last.get(pid)
        rows.append({"projectId": pid, "projectName": name, "values": values,
                     "empty": _is_empty(cols, values),
                     "changedAt": h.get("at") if h else None,
                     "changedBy": h.get("by") if h else None})
    clashes = [{"channel": c, "projectIds": pids}
               for c, pids in sorted(_channel_owners(live, _ids_of_kind(kind)).items())
               if len(pids) > 1]
    return {"source": source_id, "columns": [f.to_dict() for f in cols], "rows": rows,
            "clashes": clashes}


def _last_changes(hist: list[dict], names: dict) -> dict[str, dict]:
    last: dict[str, dict] = {}
    for h in hist:
        if h.get("projectId") in names:
            last[h["projectId"]] = h
    return last


def history(source_id: str | None = None, project_id: str | None = None,
            limit: int = 200) -> list[dict]:
    """The one table's changes, newest first (one row's when
    ``project_id``), each with the row before and after. ``source_id`` is
    no longer needed: every source shares the table."""
    hist = _load_one()["history"]
    out = [h for h in hist if project_id is None or h.get("projectId") == project_id]
    return out[::-1][:max(0, int(limit))]


def set_row(source_id: str, project_id: str, values: dict, by: dict,
            now: float | None = None) -> dict:
    """Change one initiative's row as one source sees it (round 1's call,
    kept for the log view's Make a rule, #9's import and older callers):
    ``values`` holds only the columns to change, the source's places and
    ``topic``. The change goes into the one table (``set_initiative``), with
    its rules. Returns ``{"row", "before", "changed"}`` in this source's
    view."""
    cols = columns(source_id)
    kind = _kind_of(source_id)
    if not isinstance(values, dict):
        raise Refused("the values must be an object of column: value")
    by_key = {f.key: f for f in cols}
    unknown = [k for k in values if k not in by_key]
    if unknown:
        raise Refused(f"this table has no column {unknown[0]!r}; its columns are "
                      f"{', '.join(by_key) or 'none'}")
    if not values:
        raise Refused("nothing to change: give at least one column")
    change: dict = {}
    if TOPIC in values:
        change[TOPIC] = values[TOPIC]
    pl = {k: v for k, v in values.items() if k != TOPIC}
    if pl:
        change[PLACES] = {kind: pl}
    res = set_initiative(project_id, change, by, now=now)
    return {"row": _source_values(res["row"], kind, cols),
            "before": _source_values(res["before"], kind, cols), "changed": res["changed"]}


# ---------------------------------------------------------------------------
# The one table: what the panel and the agents' tool see, and changes
# ---------------------------------------------------------------------------

def _flat_key(kind: str, key: str) -> str:
    return f"{kind}.{key}"


def _flat_columns(types: list[dict]) -> list[dict]:
    cols = [{**TOPIC_FIELD.to_dict()}, {**ITEMS_FIELD.to_dict(), "kind": "items"}]
    for t in types:
        for f in t["fields"]:
            cols.append({**f.to_dict(), "key": _flat_key(t["kind"], f.key),
                         "label": f"{t['label']} {f.label.lower()}", "source": t["kind"],
                         "field": f.key})
    return cols


def _flat(row: dict, types: list[dict] | None = None) -> dict:
    """A row as the page's cells: ``topic``, ``items`` (as text) and
    ``<type>.<column>`` for each place column. With ``types`` every place
    column of those types is there; without, only those the row has (a
    history entry)."""
    out = {}
    if TOPIC in row:
        out[TOPIC] = row[TOPIC]
    if ITEMS in row:
        out[ITEMS] = [item_text(i) for i in _stored_items(row[ITEMS])]
    places = row.get(PLACES) if isinstance(row.get(PLACES), dict) else {}
    if types is None:
        for kind, vals in places.items():
            for k, v in (vals if isinstance(vals, dict) else {}).items():
                out[_flat_key(kind, k)] = v
        return out
    for t in types:
        vals = _row_values(t["fields"], places.get(t["kind"]))
        for f in t["fields"]:
            out[_flat_key(t["kind"], f.key)] = vals[f.key]
    return out


def _clashes(rows: dict[str, dict], types: list[dict]) -> list[dict]:
    """What two rows list in a column where a value belongs to one row only:
    a place (a Slack channel) or a work item. ``[{column, what, value,
    shown, projectIds}]``. Such a value routes nobody until it is fixed."""
    out = []
    for t in types:
        ids = _ids_of_kind(t["kind"])
        for f in t["fields"]:
            if f.kind != "list":
                continue
            owners: dict[str, list[str]] = {}
            shown: dict[str, str] = {}
            for pid, row in rows.items():
                for v in (row[PLACES].get(t["kind"]) or {}).get(f.key) or []:
                    k = canon(v, ids) if f.key == CHANNELS else norm(v)
                    shown.setdefault(k, v)
                    if pid not in owners.setdefault(k, []):
                        owners[k].append(pid)
            for k, pids in sorted(owners.items()):
                if len(pids) > 1:
                    out.append({"column": _flat_key(t["kind"], f.key),
                                "what": f"{t['label']} {f.label.lower().rstrip('s')}",
                                "value": shown[k],
                                "shown": ("#" + shown[k]) if f.key == CHANNELS else shown[k],
                                "projectIds": pids})
    owners = {}
    for pid, row in rows.items():
        for i in row[ITEMS]:
            if pid not in owners.setdefault(i["key"], []):
                owners[i["key"]].append(pid)
    for k, pids in sorted(owners.items()):
        if len(pids) > 1:
            out.append({"column": ITEMS, "what": "work item", "value": k, "shown": k,
                        "projectIds": pids})
    return out


def one_table() -> dict:
    """The one routing table as the Input sources panel draws it: the
    columns (topic, work items, then each source type's places), one row per
    current initiative (empty rows included) with its last change, what two
    rows list (``clashes``) and which per-source tables were carried over."""
    names = initiatives()
    types = place_types()
    t = _load_one()
    last = _last_changes(t["history"], names)
    rows, live = [], {}
    for pid, name in names.items():
        r = t["rows"].get(pid) or _empty_row()
        live[pid] = r
        h = last.get(pid)
        rows.append({"projectId": pid, "projectName": name, TOPIC: r[TOPIC], ITEMS: r[ITEMS],
                     PLACES: {x["kind"]: _row_values(x["fields"], r[PLACES].get(x["kind"]))
                              for x in types},
                     "values": _flat(r, types), "empty": _row_empty(r),
                     "changedAt": h.get("at") if h else None,
                     "changedBy": h.get("by") if h else None})
    return {"columns": _flat_columns(types),
            "types": [{"kind": x["kind"], "label": x["label"],
                       "fields": [f.to_dict() for f in x["fields"]]} for x in types],
            "rows": rows, "clashes": _clashes(live, types), "carried": t["carried"],
            "itemNotes": _item_notes(names)}


def _item_notes(names: dict) -> list[dict]:
    """The items the hub left out of a row because another row has them,
    newest first, while both initiatives still exist and the other row
    still lists the item (``ensemble_routing`` shows a PO its own)."""
    live = _live(_load_one(), names)
    owners, _k = item_owners(live)
    out = []
    for n in reversed(items_state()["notes"]):
        key, wanted, kept = n.get("key"), n.get("wanted"), n.get("kept")
        if wanted in names and kept in names and kept in (owners.get(key) or []) \
                and wanted not in (owners.get(key) or []):
            out.append({**n, "wantedName": names[wanted], "keptName": names[kept]})
    return out


def flat_history(project_id: str | None = None, limit: int = 200) -> list[dict]:
    """The changes newest first, each row before and after as the page's
    cells (only the columns the entry has)."""
    return [{**h, "before": _flat(h.get("before") if isinstance(h.get("before"), dict) else {}),
             "after": _flat(h.get("after") if isinstance(h.get("after"), dict) else {})}
            for h in history(None, project_id, limit)]


def _who(by) -> dict:
    """Who is making a change, checked: the CEO, the setup file
    (``outside_setup``), the hub chat applying a new setup file (#16), or an
    agent with its identity and its initiative."""
    if not isinstance(by, dict) or by.get("kind") not in ("ceo", "setup", "hubchat", "agent"):
        raise Refused("a change needs to say who makes it")
    if by["kind"] in ("ceo", "setup", "hubchat"):
        return {"kind": by["kind"]}
    who = {"kind": "agent", "identity": str(by.get("identity") or ""),
           "projectId": str(by.get("projectId") or "")}
    for k in ("agent", "role", "roomId", "task"):
        if by.get(k):
            who[k] = str(by[k])
    if not who["identity"]:
        raise Refused("a change by an agent needs the agent's identity")
    return who


CHANGE_KEYS = (TOPIC, ITEMS, ADD_ITEMS, REMOVE_ITEMS, PLACES, NOT)


def set_initiative(project_id: str, change: dict, by: dict, now: float | None = None) -> dict:
    """Change one initiative's row of the one table. ``change`` holds only
    what changes: ``topic``; ``places`` ``{source type: {column: value}}``
    (only the columns given change; a list replaces the whole list);
    ``items`` (the whole list), or ``addItems`` and ``removeItems``;
    ``not`` (the whole Not list: items taken out of the row that the hub
    does not add back to it by itself; an item a person adds to the row
    later comes off it). An item
    is ``ABC-12``, ``epic ABC-10``, ``repo#123`` or ``{kind, key}``
    (``parse_item``); an item added gets ``from`` "ceo" from the page and
    "po" from an initiative's agent, "setup file" from the setup file, and
    the time. ``by`` is ``{"kind": "ceo"}``, ``{"kind": "setup"}`` or ``{"kind": "agent", "identity", "projectId",
    ...}``: an agent may change only its own initiative's row. Refused, with
    why: another initiative's row, an initiative that does not exist, a
    column the table does not have, a bad value, a place or work item
    another row lists, an item to remove that the row does not have.
    Returns ``{"row", "before", "changed"}``; a change that changes nothing
    is not kept in the history."""
    now = time.time() if now is None else now
    who = _who(by)
    names = initiatives()
    if project_id not in names:
        raise Refused(f"there is no initiative {project_id!r} on this hub")
    if who["kind"] == "agent":
        own = who["projectId"]
        if not own:
            raise Refused("your task has no initiative, so it has no row to change")
        if own != project_id:
            raise Refused(f"you may change only your own initiative's row "
                          f"({names.get(own, own)}), not {names[project_id]}'s")
        who["projectName"] = names[project_id]
    if not isinstance(change, dict):
        raise Refused("the change must be an object of column: value")
    unknown = [k for k in change if k not in CHANGE_KEYS]
    if unknown:
        raise Refused(f"a row has no column {unknown[0]!r}; it has topic, items (or addItems "
                      f"and removeItems), places and not")
    if not change:
        raise Refused("nothing to change: give at least one column")
    types = {t["kind"]: t for t in place_types()}
    new_places: dict[str, dict] = {}
    if PLACES in change:
        if not isinstance(change[PLACES], dict):
            raise Refused("places must be an object of source type: {column: value}")
        for kind, vals in change[PLACES].items():
            t = types.get(kind)
            if t is None:
                raise Refused(f"this hub has no input source of the type {kind!r} with places; "
                              f"it has {', '.join(types) or 'none'}")
            if not isinstance(vals, dict):
                raise Refused(f"the {t['label']} places must be an object of column: value")
            by_key = {f.key: f for f in t["fields"]}
            bad = [k for k in vals if k not in by_key]
            if bad:
                raise Refused(f"the {t['label']} places have no column {bad[0]!r}; they have "
                              f"{', '.join(by_key)}")
            new_places[kind] = {k: _clean(by_key[k], v) for k, v in vals.items()}
    topic = _clean(TOPIC_FIELD, change[TOPIC]) if TOPIC in change else None
    whole = [parse_item(x) for x in _item_list(change[ITEMS])] if ITEMS in change else None
    add = [parse_item(x) for x in _item_list(change.get(ADD_ITEMS))]
    remove = [parse_item(x)[1] for x in _item_list(change.get(REMOVE_ITEMS))]
    nots = None
    if NOT in change:
        nots = list({key: (kind, key) for kind, key in (parse_item(x) for x in _item_list(change[NOT]))}.values())
        listed = {key for _k, key in (whole or []) + add}
        both = [key for _k, key in nots if key in listed]
        if both:
            raise Refused(f"{both[0]} is both a work item to add and on the Not list")
    # The hub chat writes the setup file: what it lists is the file's.
    src = {"ceo": FROM_CEO, "setup": FROM_SETUP, "hubchat": FROM_SETUP}.get(who["kind"], FROM_PO)
    with _LOCK:
        t = _load_one()
        live = {pid: t["rows"].get(pid) or _empty_row() for pid in names}
        before = live[project_id]
        after = json.loads(json.dumps(before))
        if topic is not None:
            after[TOPIC] = topic
        for kind, vals in new_places.items():
            fields = types[kind]["fields"]
            cur = {**_row_values(fields, after[PLACES].get(kind)), **vals}
            if _is_empty(fields, cur):
                after[PLACES].pop(kind, None)
            else:
                after[PLACES][kind] = cur
        after[ITEMS] = _new_items(after[ITEMS], whole, add, remove, src, now, names[project_id])
        if nots is not None:
            after[NOT] = [{"kind": kind, "key": key} for kind, key in nots]
        else:
            # An item a person adds to the row comes off its Not list.
            listed = {key for _k, key in (whole or []) + add}
            after[NOT] = [i for i in after.get(NOT) or [] if i["key"] not in listed]
        gone = not_keys(after)
        after[ITEMS] = [i for i in after[ITEMS] if i["key"] not in gone]
        if not after[NOT]:
            after.pop(NOT)
        if len(after[ITEMS]) > ITEMS_MAX:
            raise Refused(f"a row has at most {ITEMS_MAX} work items")
        _one_row_only(project_id, before, after, new_places, live, types, names)
        if after == before:
            return {"row": after, "before": before, "changed": False}
        # Rows of deleted initiatives are dropped from the file at each save.
        t["rows"] = {pid: v for pid, v in t["rows"].items() if pid in names}
        t["rows"][project_id] = after
        t["history"].append({"at": now, "projectId": project_id,
                             "projectName": names[project_id], "by": who,
                             "before": before, "after": after})
        _save_one(t)
    if not _previewing():
        _log(f"{names[project_id]}'s row changed by "
             f"{ {'ceo': 'the CEO', 'setup': 'the setup file', 'hubchat': 'the hub chat'}.get(who['kind']) or who['identity']}")
    return {"row": after, "before": before, "changed": True}


def _new_items(items: list[dict], whole, add, remove, src: str, now: float,
               name: str) -> list[dict]:
    """The row's items after a change. An item kept keeps where it came
    from and when; one given again with another kind (ticket or epic) takes
    that kind."""
    by_key = {i["key"]: i for i in items}
    if whole is not None:
        out, have = [], set()
        for kind, key in whole:
            if key in have:
                continue
            have.add(key)
            old = by_key.get(key)
            out.append({**old, "kind": kind} if old else
                       {"kind": kind, "key": key, "from": src, "at": now})
    else:
        out = [dict(i) for i in items]
    for kind, key in add:
        old = next((i for i in out if i["key"] == key), None)
        if old:
            old["kind"] = kind
        else:
            out.append({"kind": kind, "key": key, "from": src, "at": now})
    for key in remove:
        if not any(i["key"] == key for i in out):
            raise Refused(f"{key} is not in {name}'s row")
        out = [i for i in out if i["key"] != key]
    return out


def _one_row_only(project_id: str, before: dict, after: dict, new_places: dict,
                  live: dict, types: dict, names: dict) -> None:
    """Refuses a change that would list a place (a channel) or a work item
    another row lists. Every value of a place column the change touches is
    checked, so a row that shares a channel already can still change its
    topic, but not its channels, until the clash is fixed."""
    others = {pid: r for pid, r in live.items() if pid != project_id}
    for kind, vals in new_places.items():
        ids = _ids_of_kind(kind)
        t = types[kind]
        for f in t["fields"]:
            if f.key not in vals or f.kind != "list":
                continue
            same = (lambda x: canon(x, ids)) if f.key == CHANNELS else norm
            for v in (after[PLACES].get(kind) or {}).get(f.key) or []:
                other = next((pid for pid, r in others.items()
                              if same(v) in {same(x) for x in (r[PLACES].get(kind) or {}).get(f.key) or []}),
                             None)
                if other is None:
                    continue
                if f.key == CHANNELS:
                    raise Refused(f"#{v} is already in {names[other]}'s row: a channel can be "
                                  f"in one row only. Take it out of that row first.")
                raise Refused(f"{v} is already in {names[other]}'s row: "
                              f"{'an' if f.label[:1].lower() in 'aeiou' else 'a'} "
                              f"{f.label.lower().rstrip('s')} can be in one row only. "
                              f"Take it out of that row first.")
    had = {i["key"] for i in before[ITEMS]}
    for i in after[ITEMS]:
        if i["key"] in had:
            continue
        other = next((pid for pid, r in others.items() if any(x["key"] == i["key"] for x in r[ITEMS])),
                     None)
        if other:
            raise Refused(f"{i['key']} is already in {names[other]}'s row: a work item can be "
                          f"in one row only. Take it out of that row first.")


def places(source_id: str) -> list[dict]:
    """The channels the routing table lists, for the frame to read even when
    the user's own settings do not watch them (``outside.PLACES``)."""
    try:
        rows = _live_rows(source_id)
    except Refused:
        return []
    out, have = [], set()
    for values in rows.values():
        for c in values.get(CHANNELS) or []:
            if norm(c) not in have:
                have.add(norm(c))
                out.append({"channel": c})
    return out



# ---------------------------------------------------------------------------
# Work items: routing step 1, and rows that fill themselves (round 2 step 2)
# ---------------------------------------------------------------------------
#
# A message that names a ticket or a pull request listed in exactly one row
# goes to that initiative, before any other rule and with no model call
# (``item_decision``). The message's own text is read first; only when it
# names nothing any row lists is its thread's first message read. Items of
# two different rows decide nothing: the next rule decides, and the log line
# says which items were in which rows.
#
# Rows fill their items by themselves: a routed message adds the items it
# names that no row has to the row it went to (``_fill_routed``); a hub task
# adds its own (``outside_items``); Send elsewhere moves a message's items
# with it (``_move_with``). Filling never moves an item another row has.
# What the hub learns about items as it goes (when each was last named in a
# message, the organisation of each repo, items it could not add) is kept in
# ``outside/work-items.json``, apart from the table and its history.

ITEM_NOTES_MAX = 50             # items the hub could not add, newest kept
DROPPED_MAX = 5000              # items the hub took out as finished, newest kept
DONE_PR_STATES = ("merged", "closed")
DONE_TICKET_STATES = ("released", "closed", "done")


def _items_file() -> Path:
    return outside.root() / "work-items.json"


def items_state() -> dict:
    """``{seen: {key: when last named in a message}, orgs: {repo: org},
    prs: {key: {state, at, checked}}, tickets: {key: {state, at}},
    dropped: {key: when the hub took it out as finished}, notes: [...]}``
    (``outside_items`` keeps ``prs`` and ``tickets``)."""
    d = _read(_items_file())
    st = {}
    for k in ("seen", "orgs", "prs", "tickets", "dropped"):
        st[k] = {a: b for a, b in (d.get(k) or {}).items() if isinstance(a, str)} \
            if isinstance(d.get(k), dict) else {}
    st["notes"] = [n for n in d.get("notes") or [] if isinstance(n, dict)][-ITEM_NOTES_MAX:]
    return st


def save_items_state(st: dict) -> None:
    st["notes"] = st.get("notes", [])[-ITEM_NOTES_MAX:]
    dropped = st.get("dropped") or {}
    if len(dropped) > DROPPED_MAX:
        st["dropped"] = dict(sorted(dropped.items(), key=lambda kv: _num(kv[1]))[-DROPPED_MAX:])
    _write(_items_file(), st)


def _live(t: dict, names: dict) -> dict[str, dict]:
    return {pid: t["rows"].get(pid) or _empty_row() for pid in names}


def item_owners(rows: dict[str, dict]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """``({key: [project ids listing it]}, {key: kind})`` of the rows'
    work items."""
    owners: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for pid, row in rows.items():
        for i in row[ITEMS]:
            got = owners.setdefault(i["key"], [])
            if pid not in got:
                got.append(pid)
            kinds.setdefault(i["key"], i["kind"])
    return owners, kinds


def known_repos(rows: dict[str, dict]) -> set[str]:
    """The repos the rows list pull requests of: their ``repo 123`` counts
    as a pull request in a message."""
    return {work_items.repo_of(i["key"]) for r in rows.values() for i in r[ITEMS] if i["kind"] == PR}


def known_prefixes(rows: dict[str, dict]) -> set[str]:
    """The ticket projects the hub knows: those of the rows' tickets and
    epics, and the hub's own Jira setting (``JIRA_PREFIXES``). A routed
    message adds only tickets of these, so "GPT-4" in a message is not
    taken for a ticket."""
    out = {work_items.prefix_of(i["key"]) for r in rows.values() for i in r[ITEMS] if i["kind"] != PR}
    d = _d if _d is not None else getattr(outside, "_d", None)
    out |= {str(p).upper() for p in getattr(d, "JIRA_PREFIXES", None) or ()}
    return {p for p in out if p}


def source_items(m: Message) -> tuple[list, list]:
    """What ``m``'s source says it is about beyond its text
    (``Source.work_items``; Jira: the ticket, and its epic around it)."""
    cls = sources.TYPES.get(m.source)
    if cls is None:
        return [], []
    try:
        own, around = cls().work_items(m)
        return [tuple(x) for x in own or []], [tuple(x) for x in around or []]
    except Exception:
        return [], []


def _joined(*lists) -> list:
    out, seen = [], set()
    for found in lists:
        for kind, key in found:
            if key not in seen:
                seen.add(key)
                out.append((kind, key))
    return out


def message_items(m: Message, repos) -> tuple[list, list]:
    """``(own, head)``: the items ``m`` is about (its source's first, then
    those its text names), and those its thread's first message names (for
    a reply) with what its source says is around it (a Jira ticket's epic)."""
    src_own, around = source_items(m)
    own = _joined(src_own, work_items.find(m.text or "", repos))
    head = []
    if m.thread and not m.thread.first and m.thread.head:
        head = work_items.find(m.thread.head, repos)
    return own, _joined(head, around)


def _labels(found: list[tuple[str, str]], kinds: dict[str, str]) -> str:
    words = [work_items.label(kinds.get(key, kind), key) for kind, key in found]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def item_decision(m: Message, owners: dict, kinds: dict, repos, names: dict) -> tuple[dict | None, str]:
    """Routing step 1 for one message: ``(decision, note)``. The decision
    is ``{to, how: "item", why}`` when the items it names (else its thread's
    first message's) are listed in exactly one row; else None, and ``note``
    names the items of two rows, for the log ("" when there are none)."""
    own, head = message_items(m, repos)
    for where, found in (("", own), (" (in the thread's first message)", head)):
        owned = [(kind, key) for kind, key in found
                 if len(owners.get(key) or []) == 1 and owners[key][0] in names]
        if not owned:
            continue
        rows = {}
        for kind, key in owned:
            rows.setdefault(owners[key][0], []).append((kind, key))
        if len(rows) == 1:
            pid = next(iter(rows))
            return {"to": pid, "how": BY_ITEM, "why": _labels(owned, kinds) + where}, ""
        return None, "work items in two rows: " + "; ".join(
            f"{_labels(v, kinds)} in {names.get(pid, pid)}'s" for pid, v in rows.items()) + where
    return None, ""


def _history_entry(project_id: str, name: str, by: dict, before: dict, after: dict, now: float,
                   note: str) -> dict:
    return {"at": now, "projectId": project_id, "projectName": name, "by": by,
            "note": note, "before": before, "after": after}


def finished_out(st: dict, kind: str, key: str) -> bool:
    """Whether the hub took ``key`` out as finished and must not add it back
    by itself: a pull request never (its links stay in a done task's
    reports), a ticket while Jira says it is finished or until a message or
    an open task names it again. The PO or the CEO can still add it."""
    if key not in st["dropped"]:
        return False
    if kind == work_items.PR:
        return True
    tk = st["tickets"].get(key)
    if isinstance(tk, dict) and tk.get("state") in DONE_TICKET_STATES:
        return True
    return _num(st["seen"].get(key)) <= _num(st["dropped"][key])


def add_items_auto(adds: dict[str, list[tuple[str, str, str]]], note: str,
                   now: float | None = None, by: dict | None = None) -> dict[str, list[str]]:
    """Add items to rows, by the hub itself: ``{project id: [(kind, key,
    from)]}``. An item another row lists is never taken from it: it is left
    out and noted (``items_state()['notes']``) with the row that keeps it.
    So is one past a row's ``ITEMS_MAX``, and one the hub took out as
    finished (``finished_out``). Each row changed gets one history
    entry, by the hub, with ``note``. Returns ``{project id: keys added}``."""
    now = time.time() if now is None else now
    by = by or HUB
    added: dict[str, list[str]] = {}
    with _LOCK:
        names = initiatives()
        t = _load_one()
        live = _live(t, names)
        owners, _k = item_owners(live)
        st = items_state()
        have_notes = {(n.get("key"), n.get("wanted")) for n in st["notes"]}
        changed = False
        for pid, items in adds.items():
            if pid not in names:
                continue
            before = live[pid]
            after = json.loads(json.dumps(before))
            for kind, key, src in items:
                kept = [o for o in owners.get(key) or [] if o != pid]
                if pid in (owners.get(key) or []) or finished_out(st, kind, key) \
                        or key in not_keys(before):
                    continue
                if kept:
                    if (key, pid) not in have_notes:
                        have_notes.add((key, pid))
                        st["notes"].append({"at": now, "key": key, "kind": kind, "wanted": pid,
                                            "wantedName": names[pid], "kept": kept[0],
                                            "keptName": names.get(kept[0], kept[0]), "from": src})
                    continue
                if len(after[ITEMS]) >= ITEMS_MAX:
                    continue
                after[ITEMS].append({"kind": kind, "key": key, "from": src, "at": now})
                owners[key] = [pid]
                added.setdefault(pid, []).append(key)
            if after != before:
                t["rows"][pid] = after
                live[pid] = after
                t["history"].append(_history_entry(pid, names[pid], by, before, after, now, note))
                changed = True
        if changed:
            t["rows"] = {pid: v for pid, v in t["rows"].items() if pid in names}
            _save_one(t)
        save_items_state(st)
    for pid, keys in added.items():
        _log(f"{len(keys)} work item{'s' if len(keys) != 1 else ''} added to {pid}'s row ({note})")
    return added


def drop_items(drops: dict[str, list[str]], note: str, now: float | None = None) -> int:
    """Take finished items out of rows, by the hub: ``{project id: [keys]}``.
    One history entry per row, with ``note``. Returns how many left."""
    now = time.time() if now is None else now
    n = 0
    with _LOCK:
        names = initiatives()
        t = _load_one()
        st = items_state()
        changed = False
        for pid, keys in drops.items():
            if pid not in names or pid not in t["rows"]:
                continue
            before = t["rows"][pid]
            gone = set(keys)
            after = {**json.loads(json.dumps(before)),
                     ITEMS: [dict(i) for i in before[ITEMS] if i["key"] not in gone]}
            if after == before:
                continue
            n += len(before[ITEMS]) - len(after[ITEMS])
            for i in before[ITEMS]:
                if i["key"] in gone:
                    st["dropped"][i["key"]] = now
            t["rows"][pid] = after
            t["history"].append(_history_entry(pid, names[pid], HUB, before, after, now, note))
            changed = True
        if changed:
            t["rows"] = {pid: v for pid, v in t["rows"].items() if pid in names}
            _save_one(t)
            save_items_state(st)
    return n


def _note_seen(st: dict, found, m: Message) -> None:
    for _kind, key in found:
        st["seen"][key] = max(_num(st["seen"].get(key)), m.at)
    for org, repo, _n in work_items.pr_links(m.text or ""):
        st["orgs"][repo] = org


def _fill_routed(fresh: list[tuple[dict, dict, Message]], now: float) -> None:
    """After routing: the items each routed message names that no row has
    join the row it went to (``from`` "routed"); a ticket only of a project
    the hub knows. Every item named is marked as seen now (for dropping
    tickets nobody talks about any more)."""
    if not fresh:
        return
    with _LOCK:
        names = initiatives()
        live = _live(_load_one(), names)
        owners, _k = item_owners(live)
        repos, prefixes = known_repos(live), known_prefixes(live)
        st = items_state()
        adds: dict[str, list[tuple[str, str, str]]] = {}
        taken: set[str] = set()
        for _e, dec, m in fresh:
            sure = {key for _k, key in source_items(m)[0]}    # its source says it is a ticket
            own = _joined(source_items(m)[0], work_items.find(m.text or "", repos))
            _note_seen(st, own, m)
            if dec.get("to") not in names:
                continue
            for kind, key in own:
                if key in owners or key in taken or key in not_keys(live[dec["to"]]):
                    continue
                if (kind == work_items.TICKET and key not in sure
                        and work_items.prefix_of(key) not in prefixes):
                    continue
                taken.add(key)
                adds.setdefault(dec["to"], []).append((kind, key, FROM_ROUTED))
        save_items_state(st)
    if adds:
        add_items_auto(adds, "named in a message routed here", now)


def _move_with(message: dict, before: str, to: str, action: str, now: float) -> list[str]:
    """Send elsewhere (or Send anyway) of ``message``: the items it names
    that ``before``'s row lists move to ``to``'s row, and those no row lists
    join it. An item a third row lists stays there. As the CEO's change,
    in the history of both rows. Returns the keys moved or added."""
    try:
        m = Message.from_dict(message)
    except (TypeError, ValueError, KeyError):
        return []
    with _LOCK:
        names = initiatives()
        if to not in names:
            return []
        t = _load_one()
        live = _live(t, names)
        owners, _k = item_owners(live)
        sure = {key for _k, key in source_items(m)[0]}
        found = _joined(source_items(m)[0], work_items.find(m.text or "", known_repos(live)))
        prefixes = known_prefixes(live)
        moved, added = [], []
        for kind, key in found:
            have = owners.get(key) or []
            if to in have or key in not_keys(live[to]):
                continue
            if have == [before] and before in names:
                moved.append((kind, key))
            elif not have and (kind != work_items.TICKET or key in sure
                               or work_items.prefix_of(key) in prefixes):
                added.append((kind, key))
        # Only what fits in ``to``'s row moves: an item past ITEMS_MAX stays where it is.
        room = ITEMS_MAX - len(live[to][ITEMS])
        fits = {k for _x, k in (moved + added)[:max(room, 0)]}
        moved = [x for x in moved if x[1] in fits]
        added = [x for x in added if x[1] in fits]
        if not moved and not added:
            return []
        word = "Send elsewhere" if action != "sent anyway" else "Send anyway"
        if moved:
            b = live[before]
            keys = {k for _x, k in moved}
            after_b = {**json.loads(json.dumps(b)), ITEMS: [dict(i) for i in b[ITEMS] if i["key"] not in keys]}
            t["rows"][before] = after_b
            t["history"].append(_history_entry(before, names[before], {"kind": "ceo"}, b, after_b, now,
                                               f"moved to {names[to]}'s row with a message ({word})"))
        a = live[to]
        after_a = json.loads(json.dumps(a))
        src = FROM_MOVED if action != "sent anyway" else FROM_ROUTED
        for kind, key in moved + added:
            old = next((i for i in live[before][ITEMS] if i["key"] == key), None) if before in live else None
            after_a[ITEMS].append({"kind": old["kind"] if old else kind, "key": key, "from": src, "at": now})
        t["rows"][to] = after_a
        t["history"].append(_history_entry(to, names[to], {"kind": "ceo"}, a, after_a, now,
                                           f"came with a message ({word})"
                                           + (f" from {names[before]}'s row" if moved and before in names else "")))
        t["rows"] = {pid: v for pid, v in t["rows"].items() if pid in names}
        _save_one(t)
    return [k for _x, k in moved + added]


# ---------------------------------------------------------------------------
# Thread memory and decisions
# ---------------------------------------------------------------------------

def _load_memory(source_id: str) -> dict:
    d = _read(_memory_file(source_id))
    threads = {k: v for k, v in (d.get("threads") or {}).items()
               if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("to"), str)}
    decided = {k: v for k, v in (d.get("messages") or {}).items()
               if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("to"), str)}
    names = {k: v for k, v in (d.get("channelNames") or {}).items()
             if isinstance(k, str) and isinstance(v, str)}
    recent = {k: [x for x in v if _recent_ok(x)]
              for k, v in (d.get("recent") or {}).items()
              if isinstance(k, str) and isinstance(v, list)}
    return {"threads": threads, "messages": decided, "channelNames": names,
            "recent": recent}


def _recent_ok(x) -> bool:
    # A damaged entry would stop routing by model for the whole source, so
    # only well-formed ones are kept.
    return (isinstance(x, dict) and all(isinstance(x.get(f), str) for f in ("key", "to", "text", "from"))
            and _num(x.get("at"), -1.0) >= 0)


def _newest(items: dict, when: str, keep: int) -> dict:
    """The ``keep`` entries with the latest ``when``, in their order."""
    if len(items) <= keep:
        return items
    cut = sorted(items, key=lambda k: _num(items[k].get(when)), reverse=True)[:keep]
    chosen = set(cut)
    return {k: v for k, v in items.items() if k in chosen}


def _save_memory(source_id: str, mem: dict, now: float) -> None:
    """Write the memory file, kept small: threads and decisions older than
    30 days go, and so do the oldest past a count (the file is rewritten
    on every batch, and model routing decides every busy channel's
    messages)."""
    mem["threads"] = _newest({k: v for k, v in mem["threads"].items()
                              if now - _num(v.get("last"), now) <= THREAD_KEEP_S},
                             "last", THREADS_MAX)
    mem["messages"] = _newest({k: v for k, v in mem["messages"].items()
                               if now - _num(v.get("at"), now) <= DECIDED_KEEP_S},
                              "at", DECIDED_MAX)
    names = mem.get("channelNames") or {}
    mem["channelNames"] = dict(list(names.items())[-CHANNEL_NAMES_MAX:])
    recent = {k: v for k, v in (mem.get("recent") or {}).items()
              if v and now - max(_num(x.get("at")) for x in v) <= RECENT_KEEP_S}
    newest = {k: {"at": max(_num(x.get("at")) for x in v)} for k, v in recent.items()}
    mem["recent"] = {k: recent[k] for k in _newest(newest, "at", RECENT_MAX)}
    _write(_memory_file(source_id), mem)


def learn_channels(source_id: str, entries: list[dict], now: float | None = None) -> None:
    """Remember each channel's id and name from the messages handed on, so
    a row that lists a channel by name and another by id clash (``canon``)."""
    now = time.time() if now is None else now
    seen = {}
    for e in entries:
        place = (e.get("message") or {}).get("place") or {}
        cid, name = norm(place.get("id") or ""), norm(place.get("name") or "")
        if cid and name and cid != name and place.get("kind") in ("channel", "private channel"):
            seen[cid] = name
    if not seen:
        return
    with _LOCK:
        mem = _load_memory(source_id)
        pairs = mem["channelNames"]
        if all(pairs.get(cid) == name for cid, name in seen.items()):
            return
        for cid, name in seen.items():
            pairs.pop(cid, None)
            pairs[cid] = name               # newest last, so the oldest are dropped first
        _save_memory(source_id, mem, now)


def thread_memory(source_id: str) -> dict:
    """{thread id: {to, how, at, last}}: where each routed thread goes."""
    with _LOCK:
        return _load_memory(source_id)["threads"]


def context_of(m: Message) -> str:
    """The conversation whose earlier messages the model sees with ``m``:
    its thread for a reply in one, else its DM, group DM or channel."""
    if m.thread and m.thread.id and not m.thread.first:
        return "thread " + m.thread.id
    return "place " + (m.place.id or m.place.name)


def recent(source_id: str, messages: list[Message]) -> dict[str, list[dict]]:
    """{conversation (``context_of``): its earlier messages, oldest first}
    for the conversations of ``messages``: at most RECENT_N, each written
    before the first of ``messages`` in that conversation. Each is
    ``{at, from, text, to}``, ``to`` the initiative's id it went to or ""."""
    with _LOCK:
        kept = _load_memory(source_id)["recent"]
    first: dict[str, float] = {}
    keys = {m.key for m in messages}
    for m in messages:
        c = context_of(m)
        first[c] = min(first.get(c, m.at), m.at)
    out = {}
    for c, at in first.items():
        got = [x for x in kept.get(c) or [] if _num(x.get("at")) < at and x.get("key") not in keys]
        if got:
            out[c] = got[-RECENT_N:]
    return out


def _place_columns(source_id: str, cols: list[Field]) -> list[Field]:
    """The list place columns other than channels, in the source's order."""
    return [f for f in cols if f.kind == "list" and f.key not in (CHANNELS, TOPIC, ITEMS)]


def _place_owners(rows: dict[str, dict], cols: list[Field]) -> dict[str, dict[str, list[str]]]:
    """{column: {value as compared: [project ids listing it]}}."""
    out: dict[str, dict[str, list[str]]] = {}
    for f in cols:
        got = out.setdefault(f.key, {})
        for pid, values in rows.items():
            for v in values.get(f.key) or []:
                pids = got.setdefault(norm(v), [])
                if pid not in pids:
                    pids.append(pid)
    return out


def _by_place(src, m: Message, cols: list[Field], owners: dict) -> dict | None:
    """Step 1b: the first place column whose values name exactly one row."""
    if src is None or not cols:
        return None
    try:
        values = src.place_values(m) or {}
    except Exception:
        return None
    for f in cols:
        pids, hit = set(), ""
        for v in values.get(f.key) or []:
            got = owners.get(f.key, {}).get(norm(v), [])
            if got and not hit:
                hit = str(v)
            pids.update(got)
        if len(pids) == 1:
            label = f.label[:-1] if f.label.endswith("s") else f.label
            return {"to": pids.pop(), "how": BY_PLACE, "why": f"{label} {hit}".strip()}
    return None


def _decide(m: Message, owners: dict[str, list[str]], ids: dict[str, str],
            mem: dict, names: dict[str, str], items: tuple | None = None,
            by_place: dict | None = None) -> dict:
    """The rules that need no model, for one message: ``{to, how, why}``,
    ``to`` empty and ``how`` NEEDS_MODEL when none decides. First a work
    item listed in one row (``items``: ``(owners, kinds, repos)`` of the
    rows' items), then a corrected thread, the channel, the message's other
    places (``by_place``: what ``_by_place`` found), thread memory."""
    if items is not None:
        dec, _note = item_decision(m, *items, names)
        if dec:
            return dec
    th = mem["threads"].get(m.thread.id) if m.thread and m.thread.id else None
    if th and th.get("to") not in names:
        th = None                       # its initiative was deleted
    if th and th.get("how") == BY_CORRECTION:
        return {"to": th["to"], "how": BY_THREAD, "why": "thread memory (corrected by the CEO)"}
    # The channel's id and its name may each be listed: by the same row, or
    # (a clash) by two, and then the channel decides nothing.
    pids = set()
    for c in (m.place.id, m.place.name):
        if norm(c):
            pids.update(owners.get(norm(c), []))
            pids.update(owners.get(canon(c, ids), []))
    if len(pids) == 1:
        label = m.place.name or m.place.id
        if not label.startswith("#") and m.place.kind in ("channel", "private channel"):
            label = "#" + label
        return {"to": pids.pop(), "how": BY_CHANNEL, "why": f"channel {label}"}
    if by_place and by_place.get("to") in names:
        return by_place
    if th:
        return {"to": th["to"], "how": BY_THREAD, "why": "thread memory"}
    return {"to": "", "how": NEEDS_MODEL, "why": "no channel or thread decides it"}


def decide(source_id: str, entries: list[dict]) -> list[tuple[dict, dict]]:
    """The rules that need no model (already decided, work item, corrected
    thread, channel, thread memory) for the frame's unrouted entries,
    without changing anything: ``[(entry, decision)]``. A decision is ``{to, how, why}``,
    with ``again: True`` for a message decided before; one the model
    dropped before has ``dropped`` (small talk or no match) and no ``to``.
    Step 5 calls this and asks the model about those that need one."""
    cols = columns(source_id)
    names = initiatives()
    rows = _live_rows(source_id, cols)
    live = _live(_load_one(), names)
    items = (*item_owners(live), known_repos(live))
    with _LOCK:
        mem = _load_memory(source_id)
    ids = {name: cid for cid, name in mem["channelNames"].items()}
    owners = _channel_owners(rows, ids)
    pcols = _place_columns(source_id, cols)
    powners = _place_owners(rows, pcols)
    cfg = outside.source_config(source_id) or {}
    src = outside.instance(source_id, cfg) if cfg else None
    out = []
    for e in entries:
        try:
            m = Message.from_dict(e["message"])
        except (TypeError, ValueError, KeyError):
            continue
        before = mem["messages"].get(e["key"])
        if before and before.get("to") in names:
            out.append((e, {"to": before["to"], "how": before.get("how", ""),
                            "why": before.get("why", ""), "again": True}))
            continue
        if before and before.get("dropped") in DROPPED:
            out.append((e, {"to": "", "dropped": before["dropped"], "how": before.get("how", ""),
                            "why": before.get("why", ""), "again": True}))
            continue
        out.append((e, _decide(m, owners, ids, mem, names, items, _by_place(src, m, pcols, powners))))
    return out


def record(source_id: str, decided: list[tuple[dict, dict]], now: float | None = None) -> list[str]:
    """Keep routed messages: remember each decision and its thread, put each
    in the frame's queue and write its log line. Step 5 calls this with the
    model's decisions too (``how`` BY_MODEL), including the ones it drops
    (``dropped``: small talk or no match): those are remembered, never
    queued and never change a thread's memory; only "no match" is logged.
    The first decision about a message stands: one decided before keeps it.
    Entries with neither an initiative nor ``dropped`` are left alone.
    Returns the keys routed or dropped (for the frame to take out of its
    unrouted list)."""
    now = time.time() if now is None else now
    names = initiatives()
    todo = [(e, dec) for e, dec in decided
            if dec.get("to") in names or (not dec.get("to") and dec.get("dropped") in DROPPED)]
    if not todo:
        return []
    live = _live(_load_one(), names)
    items = (*item_owners(live), known_repos(live))
    with _LOCK:
        mem = _load_memory(source_id)
        fresh, dropped = [], []
        for e, dec in todo:
            m = Message.from_dict(e["message"])
            prev = mem["messages"].get(e["key"])
            if prev and prev.get("dropped") in DROPPED and prev.get("to") not in names:
                dropped.append((e, {**dec, "to": "", "dropped": prev["dropped"]}, m, prev))
                continue                            # dropped the first time: stays dropped
            if prev and prev.get("to") in names:
                dec = {**dec, "to": prev["to"]}     # never a second initiative
                dec.pop("dropped", None)
            else:
                prev = None
            if dec.get("dropped"):
                mem["messages"][e["key"]] = {"to": "", "dropped": dec["dropped"], "how": dec["how"],
                                             "why": dec.get("why", ""), "at": now,
                                             "thread": m.thread.id if m.thread else "",
                                             "logged": False}
                _remember_recent(mem, e["key"], m, "")
                dropped.append((e, dec, m, None))
                continue
            if prev is None and dec.get("how") != BY_ITEM:
                _x, note = item_decision(m, *items, names)
                if note and note not in dec.get("why", ""):
                    dec = {**dec, "why": f"{dec.get('why', '')} ({note})".strip()}
            mem["messages"][e["key"]] = {"to": dec["to"], "how": dec["how"],
                                         "why": dec.get("why", ""), "at": now,
                                         "thread": m.thread.id if m.thread else "",
                                         "logged": bool(prev and prev.get("logged"))}
            _remember_recent(mem, e["key"], m, dec["to"])
            if m.thread and m.thread.id:
                th = mem["threads"].get(m.thread.id) or {}
                if th.get("how") == BY_CORRECTION:
                    # Only a new correction changes a corrected thread.
                    th = {**th, "last": max(_num(th.get("last")), m.at)}
                else:
                    th = {"to": dec["to"], "how": dec["how"], "at": now,
                          "last": max(_num(th.get("last")), m.at)}
                mem["threads"][m.thread.id] = th
            fresh.append((e, dec, m))
        _save_memory(source_id, mem, now)
    _fill_routed([(e, dec, m) for e, dec, m in fresh], now)
    if fresh:
        outside.queue_put([{"key": e["key"], "to": dec["to"], "source": source_id,
                            "message": e["message"], "how": dec["how"], "why": dec.get("why", ""),
                            "decideOnly": bool(e.get("decideOnly"))} for e, dec, _m in fresh], now)
    # A decision may already be gone from memory (the count limit) when its
    # log line is written: then it is logged and not marked.
    lines = [_line(source_id, e, dec, m, names, now) for e, dec, m in fresh
             if not (mem["messages"].get(e["key"]) or {}).get("logged")]
    lines += [_line(source_id, e, dec, m, names, now) for e, dec, m, prev in dropped
              if dec["dropped"] == NO_MATCH and not (prev or {}).get("logged")]
    if lines:
        whole = {e["key"]: e["message"] for e, _dec, _m in fresh}
        whole.update({e["key"]: e["message"] for e, _dec, _m, _p in dropped})
        _keep_whole(source_id, {ln["key"]: whole[ln["key"]] for ln in lines if ln["key"] in whole}, now)
        _append_log(lines, now)
        with _LOCK:
            mem = _load_memory(source_id)
            for ln in lines:
                if ln["key"] in mem["messages"]:
                    mem["messages"][ln["key"]]["logged"] = True
            _save_memory(source_id, mem, now)
    return [e["key"] for e, _dec, _m in fresh] + [e["key"] for e, _dec, _m, _p in dropped]


def _remember_recent(mem: dict, key: str, m: Message, to: str) -> None:
    """Keep ``m`` among its conversation's last messages, for the model. A
    message that starts a thread is kept for its DM or channel and for its
    thread, so a reply sees where the thread went."""
    text = " ".join(short_links(m.text or "").split())
    one = {"key": key, "at": m.at, "from": m.author.name or "", "to": to,
           "text": text if len(text) <= RECENT_CHARS else text[:RECENT_CHARS] + " …"}
    convs = [context_of(m)]
    if m.thread and m.thread.id and m.thread.first:
        convs.append("thread " + m.thread.id)
    for c in convs:
        got = [x for x in mem["recent"].get(c) or [] if x.get("key") != key] + [one]
        got.sort(key=lambda x: _num(x.get("at")))
        mem["recent"].pop(c, None)
        mem["recent"][c] = got[-RECENT_N:]


def route(source_id: str, entries: list[dict]) -> list[str]:
    """The frame's router (``outside.ROUTER``): routes what steps 1 and 2
    decide and returns those keys. The rest need a model and stay in the
    unrouted list for step 5."""
    try:
        columns(source_id)
    except Refused:
        return []                   # no routing table for this source
    learn_channels(source_id, entries)
    decided = decide(source_id, entries)
    return record(source_id, decided)


def correct(source_id: str, key: str, to: str, by: dict, now: float | None = None,
            thread: str = "", action: str = "", note: str = "",
            before: str = "", forgotten_ok: bool = False, message: dict | None = None) -> dict:
    """The CEO says a routed message belongs to ``to``: its decision and its
    thread's memory change, so later replies in the thread go there too,
    and where it "went" changes in the earlier messages the model sees with
    the next messages of its DM, channel or thread; a log line says so. A
    message's decision is kept 30 days; for an older one the log line's
    ``threadId`` given as ``thread`` still changes the thread's memory.
    Delivering it to the new initiative is step 7's Send elsewhere
    (``outside_log``), which names itself in ``action`` ("moved", or "sent
    anyway" for a message no initiative fitted) and what became of the
    first initiative's copy in ``note``: the log line keeps both. With
    ``forgotten_ok`` a message whose decision is no longer kept and has no
    thread is not refused: only the log line is written, with ``before``
    (what the log says it was in) as where it was. With ``message`` (the
    message whole), the work items it names move with it: from the row it
    was in to ``to``'s, and those no row lists join ``to``'s
    (``_move_with``). Returns ``{"before", "to", "thread"}``, with
    ``items`` (the keys moved or added) when ``message`` is given."""
    now = time.time() if now is None else now
    who = _who(by)
    if who["kind"] != "ceo":
        raise Refused("only the CEO corrects where a message went")
    names = initiatives()
    if to not in names:
        raise Refused(f"there is no initiative {to!r} on this hub")
    with _LOCK:
        mem = _load_memory(source_id)
        dec = mem["messages"].get(key)
        if dec is None and not thread and not forgotten_ok:
            raise Refused("that message was not routed, or was routed too long ago to correct")
        if dec is None:
            before = (mem["threads"].get(thread) or {}).get("to", "") or before
            tid = thread
        else:
            before = dec.get("to", "")
            dec.update({"to": to, "how": BY_CORRECTION, "why": "corrected by the CEO", "at": now})
            dec.pop("dropped", None)
            tid = dec.get("thread") or ""
        if tid:
            th = mem["threads"].get(tid) or {}
            mem["threads"][tid] = {"to": to, "how": BY_CORRECTION, "at": now,
                                   "last": max(_num(th.get("last")), now)}
        for kept in mem["recent"].values():
            for x in kept:
                if x.get("key") == key:
                    x["to"] = to            # the model sees where it went now
        _save_memory(source_id, mem, now)
    _append_log([{"at": now, "source": source_id, "key": key, "threadId": tid,
                  "how": BY_CORRECTION, "why": "corrected by the CEO", "to": [to],
                  "toNames": [names[to]], "before": before,
                  "beforeName": names.get(before, before), "by": who,
                  **({"action": action} if action else {}),
                  **({"note": note} if note else {})}], now)
    out = {"before": before, "to": to, "thread": tid}
    if message:
        out["items"] = _move_with(message, before, to, action, now)
    return out


def channel_rows(source_id: str, channel: str) -> list[str]:
    """The initiatives whose row lists ``channel`` (by its name or its id)."""
    rows = _live_rows(source_id)
    ids = _ids_by_name(source_id)
    return _channel_owners(rows, ids).get(canon(channel, ids), [])


def log_event(line: dict, now: float | None = None) -> None:
    """Add one line to the log that is not a routing decision (the log
    view's Make a rule): it names the message's ``key`` and its ``action``."""
    now = time.time() if now is None else now
    _append_log([{"at": now, **line}], now)


# ---------------------------------------------------------------------------
# Whole messages, for the log view's actions
# ---------------------------------------------------------------------------

def _keep_whole(source_id: str, messages: dict[str, dict], now: float) -> None:
    """Keep the logged messages whole (the log keeps only their start), so
    the log view can send one on: the newest :data:`KEPT_MAX`, for
    :data:`KEPT_S`."""
    if not messages:
        return
    with _LOCK:
        d = _read(_kept_file(source_id))
        kept = {k: v for k, v in (d.get("messages") or {}).items()
                if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("message"), dict)
                and now - _num(v.get("at"), now) <= KEPT_S}
        for k, m in messages.items():
            kept.pop(k, None)
            kept[k] = {"at": now, "message": m}
        _write(_kept_file(source_id), {"messages": _newest(kept, "at", KEPT_MAX)})


def kept_message(source_id: str, key: str, now: float | None = None) -> dict | None:
    """A logged message whole, or None once it is no longer kept."""
    now = time.time() if now is None else now
    with _LOCK:
        v = (_read(_kept_file(source_id)).get("messages") or {}).get(key)
    if not isinstance(v, dict) or not isinstance(v.get("message"), dict):
        return None
    return v["message"] if now - _num(v.get("at"), now) <= KEPT_S else None


# ---------------------------------------------------------------------------
# Short forms of links
# ---------------------------------------------------------------------------

_SLACK_LINK = re.compile(r"<(https?://[^>|\s]+)(?:\|([^>]*))?>")
_URL = re.compile(r"https?://[^\s<>()\"']+")
_GITHUB = re.compile(r"^https?://(?:www\.)?github\.com/[^/\s]+/([^/\s]+)/(?:pull|pulls|issues)/(\d+)",
                     re.I)
_TICKET = re.compile(r"/browse/([A-Z][A-Z0-9]+-\d+)")
_TICKET_ANY = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])")
_SLACK_PERMALINK = re.compile(r"^https?://[^/\s]+\.slack\.com/archives/", re.I)


def short_link(url: str, label: str = "") -> str:
    """One link's short form: a pull request or issue as "<repo> <number>",
    a ticket as its key, a Slack message as "Slack message", anything else
    as its label or its site's name."""
    url = url.rstrip(".,;:!?")
    m = _GITHUB.match(url)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = _TICKET.search(url)
    if m:
        return m.group(1)
    if _SLACK_PERMALINK.match(url):
        return label.strip() or "Slack message"
    if label.strip():
        return label.strip()
    m = _TICKET_ANY.search(url)
    if m and "atlassian" in url.lower():
        return m.group(1)
    host = re.sub(r"^https?://(?:www\.)?", "", url).split("/", 1)[0]
    return f"{host} link"


def short_links(text: str) -> str:
    """Every link in ``text`` in its short form (Slack's ``<url|label>``
    marks included), so the log stays readable and "…/pull/1234" in the
    example-repo repository reads "example-repo 1234"."""
    def bare(m):
        url = m.group(0)
        tail = url[len(url.rstrip(".,;:!?")):]     # a full stop after a link is the sentence's
        return short_link(url) + tail
    text = _SLACK_LINK.sub(lambda m: short_link(m.group(1), m.group(2) or ""), text or "")
    return _URL.sub(bare, text)


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------

def _line(source_id: str, e: dict, dec: dict, m: Message, names: dict, now: float) -> dict:
    """One log line. ``outcome`` is "routed" or "no match"; a "no match"
    line has no initiative (``to`` is empty)."""
    text = short_links(m.text)
    nomatch = dec.get("dropped") == NO_MATCH
    return {"at": now, "source": source_id, "key": e["key"], "writtenAt": m.at,
            "place": {"id": m.place.id, "name": m.place.name, "kind": m.place.kind},
            "author": m.author.name or "", "text": text[:LOG_TEXT_CHARS],
            "more": max(0, len(text) - LOG_TEXT_CHARS),
            "threadId": m.thread.id if m.thread else "", "link": m.link,
            "whySeen": m.why_seen, "outcome": NO_MATCH if nomatch else "routed",
            "to": [] if nomatch else [dec["to"]],
            "toNames": [] if nomatch else [names.get(dec["to"], dec["to"])],
            "how": dec["how"], "why": dec.get("why", ""),
            "decideOnly": bool(e.get("decideOnly"))}


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.localtime(ts))


def _append_log(lines: list[dict], now: float) -> None:
    """Add lines to this month's file, and drop month files whose every line
    is older than the keeping time."""
    d = _log_dir()
    with _LOCK:
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{_month(now)}.jsonl", "a", encoding="utf-8") as f:
                for ln in lines:
                    f.write(json.dumps(ln, ensure_ascii=False) + "\n")
        except OSError as e:
            _log(f"cannot write the log: {e}")
            return
        keep_from = _month(now - LOG_KEEP_S)
        for p in d.glob("*.jsonl"):
            if p.stem < keep_from:
                try:
                    p.unlink()
                except OSError:
                    pass


def log_lines(source_id: str | None = None, limit: int = 200,
              now: float | None = None) -> list[dict]:
    """Routing decisions, newest first (step 7's view), from the last 90
    days, of one source when ``source_id``."""
    now = time.time() if now is None else now
    out: list[dict] = []
    files = sorted(_log_dir().glob("*.jsonl"), reverse=True)
    for p in files:
        try:
            raw = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for s in reversed(raw):
            try:
                ln = json.loads(s)
            except ValueError:
                continue
            if not isinstance(ln, dict) or now - _num(ln.get("at")) > LOG_KEEP_S:
                continue
            if source_id is not None and ln.get("source") != source_id:
                continue
            out.append(ln)
            if len(out) >= limit:
                return out
    return out
