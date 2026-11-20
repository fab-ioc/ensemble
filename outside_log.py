"""The log view of outside messages (design #1, section 9, step 7; as changed
by the revised plan: no Inbox row for a message no initiative fits, P10, and
small talk never logged, P11).

**What it lists.** One row per logged message, newest first, made from the
routing log (``outside_route.log_lines``): its first line says where routing
sent it ("routed", or "no match") and why (channel, thread or model); later
lines for the same message are the CEO's actions on it, shown as events
under the row ("Moved from Motors to Boats by you"). Filters: the source,
the initiative (a message it has now, or had before it was moved), and
"no match" only.

**The CEO's three actions**, each at most once:

* **Send elsewhere**: a routed message goes to another initiative. Its
  thread's memory changes (``outside_route.correct``), so later replies go
  there too, and the model sees the new initiative as where it went when it
  decides the next messages of its DM, channel or thread; it is queued for
  the new initiative (``outside.queue_put``), which step 3's delivery
  brings to that PO. The first initiative: still waiting in the queue, it is taken out (its PO never saw it, and now never
  will); already typed to its PO, the PO is told quietly that it was moved
  (``outside_deliver.tell_moved``: a line in its chat and a quiet report
  that rides its next wake, so it is not woken for it).
  The work items the message names (tickets, pull requests) move with it
  to the new initiative's row (round 2 step 2).
* **Send anyway**: a "no match" message goes to an initiative the CEO picks;
  the work items it names that no row lists join that row.
* **Make a rule**: the message's channel is added to an initiative's row of
  the routing table (``outside_route.set_row``, kept in the change history
  as the CEO's). Only for a message in a channel; a channel another row
  lists is refused with the table's own words.

**Once and only once.** Each action runs under this module's lock and, for
the two that deliver, under delivery's lock too, so a delivery look cannot
type the message to the first PO meanwhile. The message's current place is
read from the log under the lock: a second press finds it already there and
is refused. ``outside.queue_put`` never queues a (message, initiative) twice
in 4 days, and delivery never types a message twice into one PO's chat.

A message read in "Decide only" mode is never delivered: the actions still
change where it belongs (and the rules), and its queue item stays decide
only.

Bound to the dashboard module like ``outside_route``: nothing here reads
``_d`` at import time.
"""
from __future__ import annotations

import threading
import time

import outside
import outside_deliver
import outside_route
import outside_setup

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


SCAN_MAX = 20000            # log lines read to build the rows (90 days of them)
LIMIT_MAX = 500             # rows in one answer
RECENT = 5                  # an initiative's settings show its latest this many
CHANNEL_KINDS = ("channel", "private channel")
MOVED = "moved"
SENT_ANYWAY = "sent anyway"
RULE = "rule"

_LOCK = threading.RLock()


class Refused(Exception):
    """An action that cannot be done: the words say why."""


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------

def _source_label(source_id: str, labels: dict) -> str:
    if source_id not in labels:
        cfg = outside.source_config(source_id)
        labels[source_id] = outside.label_of(cfg) if cfg else source_id
    return labels[source_id]


def _where(place: dict) -> str:
    place = place if isinstance(place, dict) else {}
    kind = place.get("kind") or ""
    name = str(place.get("name") or place.get("id") or "")
    if kind == "dm":
        return "DM"
    if kind == "group dm":
        return f"group DM ({name})" if name else "group DM"
    if kind in CHANNEL_KINDS and name and not name.startswith("#"):
        return "#" + name
    return name


def _channel(place: dict) -> str:
    """The channel a rule would list, or "" for a place that is not one."""
    place = place if isinstance(place, dict) else {}
    if place.get("kind") not in CHANNEL_KINDS:
        return ""
    return outside_route.norm(place.get("name") or place.get("id") or "")


def _by(line: dict) -> str:
    by = line.get("by") if isinstance(line.get("by"), dict) else {}
    return "you" if by.get("kind", "ceo") == "ceo" else str(by.get("identity") or "an agent")


def _event(ln: dict, names: dict) -> dict:
    """One later line about a message, as words for the page."""
    to = (ln.get("to") or [""])[0]
    to_name = names.get(to) or (ln.get("toNames") or [to])[0] or "?"
    if ln.get("action") == RULE:
        text = f"Rule made: #{ln.get('channel') or '?'} goes to {to_name}"
    else:
        before = ln.get("before") or ""
        before_name = names.get(before) or ln.get("beforeName") or before
        if ln.get("action") == SENT_ANYWAY:
            text = f"Sent anyway to {to_name} by {_by(ln)}"
        elif ln.get("action") == MOVED:
            text = f"Moved from {before_name} to {to_name} by {_by(ln)}"
        else:
            text = (f"Corrected from {before_name} to {to_name} by {_by(ln)}" if before_name
                    else f"Corrected to {to_name} by {_by(ln)}")
        if ln.get("note"):
            text += f" ({ln['note']})"
    return {"at": ln.get("at") or 0, "kind": ln.get("action") or "correction", "text": text}


def _merge(lines: list[dict], names: dict, labels: dict) -> list[dict]:
    """The log lines (newest first) as one row per message, newest first.
    A message whose first line is no longer in the log is left out."""
    by_key: dict[str, dict] = {}
    later: dict[str, list[dict]] = {}
    for ln in reversed(lines):      # oldest first
        key = ln.get("key")
        sid = ln.get("source")
        if not isinstance(key, str) or not isinstance(sid, str):
            continue
        k = f"{sid}\n{key}"
        if ln.get("outcome") in ("routed", outside_route.NO_MATCH):
            if k not in by_key:
                by_key[k] = ln
            continue
        if k in by_key:
            later.setdefault(k, []).append(ln)
    rows = []
    for k, base in by_key.items():
        to = (base.get("to") or [""])[0]
        to_name = (base.get("toNames") or [to])[0] if to else ""
        been = [to] if to else []
        how, why = base.get("how") or "", str(base.get("why") or "")
        events = []
        for ln in later.get(k, []):
            if ln.get("action") != RULE and ln.get("how") == outside_route.BY_CORRECTION \
                    and (ln.get("to") or [""])[0]:
                to = ln["to"][0]
                to_name = (ln.get("toNames") or [to])[0]
                if to not in been:
                    been.append(to)
                # Why it is where it is now: the correction, not the first
                # decision (the events below say where it was before).
                how, why = outside_route.BY_CORRECTION, f"sent here by {_by(ln)}"
            events.append(_event(ln, names))
        sid = base["source"]
        place = base.get("place") if isinstance(base.get("place"), dict) else {}
        rows.append({
            "key": base["key"], "source": sid, "sourceLabel": _source_label(sid, labels),
            "at": base.get("at") or 0, "writtenAt": base.get("writtenAt") or 0,
            "who": str(base.get("author") or "") or "someone", "where": _where(place),
            "text": str(base.get("text") or ""), "more": int(base.get("more") or 0),
            "link": str(base.get("link") or ""), "threadId": str(base.get("threadId") or ""),
            "channel": _channel(place),
            "to": to, "toName": (names.get(to) or to_name or to) if to else "",
            "noMatch": not to, "been": been,
            "how": how, "why": why,
            "decideOnly": bool(base.get("decideOnly")), "events": events})
    # Newest first; of two logged at the same time, the later line first.
    return [r for _i, r in sorted(enumerate(rows), key=lambda x: (x[1]["at"], x[0]),
                                  reverse=True)]


def _all_rows(source_id: str | None = None) -> list[dict]:
    names = outside_route.initiatives()
    return _merge(outside_route.log_lines(source_id, SCAN_MAX), names, {})


def rows(source_id: str = "", project_id: str = "", no_match: bool = False,
         limit: int = 200) -> dict:
    """What the log view draws: ``{rows, sources, initiatives}``. A row is
    one message: when, source, who, where, text, where it is now (``to``,
    empty for "no match"), why, the events since, and which actions it
    offers. ``project_id`` keeps the messages an initiative has, or had
    before they were moved."""
    names = outside_route.initiatives()
    labels: dict = {}
    out = _merge(outside_route.log_lines(source_id or None, SCAN_MAX), names, labels)
    if project_id:
        out = [r for r in out if project_id in r["been"]]
    if no_match:
        out = [r for r in out if r["noMatch"]]
    limit = max(1, min(LIMIT_MAX, int(limit)))
    total = len(out)
    out = _offers(out[:limit], names)
    sources = []
    for sid in outside_route.routed_sources():
        sources.append({"id": sid, "label": _source_label(sid, labels)})
    return {"rows": out, "total": total, "sources": sources,
            "initiatives": [{"id": pid, "name": n} for pid, n in names.items()]}


def _offers(out: list[dict], names: dict) -> list[dict]:
    """Which actions each row offers: ``canSend`` (Send elsewhere or Send
    anyway: the message is still kept whole), ``rule`` ("offer", or why
    not: the channel another row lists; "" when not in a channel)."""
    owners: dict[tuple, list[str]] = {}
    for r in out:
        kept = outside_route.kept_message(r["source"], r["key"]) is not None
        # Another initiative than the one that has it must exist.
        r["canSend"] = kept and len(set(names) - {r["to"]}) > 0
        rule = ""
        if r["channel"]:
            ck = (r["source"], r["channel"])
            if ck not in owners:
                try:
                    owners[ck] = outside_route.channel_rows(*ck)
                except outside_route.Refused:
                    owners[ck] = []
            have = [names.get(p, p) for p in owners[ck]]
            rule = f"#{r['channel']} is in {', '.join(have)}'s row" if have else "offer"
        r["rule"] = rule
    return out


def recent_for(project_id: str, n: int = RECENT) -> list[dict]:
    """An initiative's latest routed messages (it has them now), for its
    settings dialog."""
    return [r for r in _all_rows() if r["to"] == project_id][:n]


# ---------------------------------------------------------------------------
# The actions
# ---------------------------------------------------------------------------

def _row(source_id: str, key: str) -> dict:
    r = next((r for r in _all_rows(source_id) if r["key"] == key), None)
    if r is None:
        raise Refused("that message is not in the log (it keeps 90 days)")
    return _offers([r], outside_route.initiatives())[0]


def _whole(source_id: str, key: str) -> dict:
    m = outside_route.kept_message(source_id, key)
    if m is None:
        raise Refused("that message is no longer kept whole (30 days), so it cannot be sent on")
    return m


def _target(to: str) -> str:
    names = outside_route.initiatives()
    if to not in names:
        raise Refused(f"there is no initiative {to!r} on this hub")
    return names[to]


def _given(key: str, to: str, to_name: str) -> None:
    """Refused when ``to`` has this message already (queued, or given to
    its PO in the last 4 days): it is never given twice."""
    if outside_deliver.was_given(key, to) or any(i["key"] == key for i in outside.queue_items(to)):
        raise Refused(f"{to_name} has this message already: it is not given twice")


def _queue(source_id: str, key: str, to: str, message: dict, why: str,
           decide_only: bool, now: float) -> bool:
    return bool(outside.queue_put([{"key": key, "to": to, "source": source_id, "message": message,
                                    "how": outside_route.BY_CORRECTION, "why": why,
                                    "decideOnly": decide_only}], now, also=True))


def _answer(r: dict, to_name: str, note: str) -> str:
    if r["decideOnly"]:
        return (f"Now in {to_name}. It was read in Decide only mode, so it is not given "
                f"to the PO.")
    return f"Sent to {to_name}." + (f" {note[0].upper()}{note[1:]}." if note else "")


def send_elsewhere(source_id: str, key: str, to: str, now: float | None = None) -> dict:
    """Send a routed message to another initiative (see the module's words).
    Returns ``{"message", "row"}``: the words for the page and the row now."""
    now = time.time() if now is None else now
    with _LOCK, outside_deliver._LOCK:
        to_name = _target(to)
        r = _row(source_id, key)
        if r["noMatch"]:
            raise Refused("no initiative has this message yet: use Send anyway")
        if r["to"] == to:
            raise Refused(f"it is in {to_name} already")
        message = _whole(source_id, key)
        _given(key, to, to_name)
        before = r["to"]
        before_name = r["toName"] or before
        got = outside_deliver.take_back(key, before)
        note = {"taken back": f"{before_name}'s PO had not been given it",
                "told": f"{before_name}'s PO is told quietly",
                "never": ""}[got]
        outside_route.correct(source_id, key, to, {"kind": "ceo"}, now=now,
                              thread=r["threadId"], action=MOVED, note=note,
                              before=before, forgotten_ok=True, message=message)
        _queue(source_id, key, to, message, f"sent here by the CEO (it was in {before_name})",
               r["decideOnly"], now)
        if got == "told" and not outside_deliver.tell_moved(before, key, message, to_name, now):
            note = f"{before_name} has no PO to tell"
    return {"message": _answer(r, to_name, note), "row": _row(source_id, key)}


def send_anyway(source_id: str, key: str, to: str, now: float | None = None) -> dict:
    """Send a "no match" message to the initiative the CEO picks."""
    now = time.time() if now is None else now
    with _LOCK, outside_deliver._LOCK:
        to_name = _target(to)
        r = _row(source_id, key)
        if not r["noMatch"]:
            raise Refused(f"it is in {r['toName'] or r['to']} already: use Send elsewhere")
        message = _whole(source_id, key)
        _given(key, to, to_name)
        outside_route.correct(source_id, key, to, {"kind": "ceo"}, now=now,
                              thread=r["threadId"], action=SENT_ANYWAY, forgotten_ok=True,
                              message=message)
        _queue(source_id, key, to, message, "sent here by the CEO (no initiative fitted it)",
               r["decideOnly"], now)
    return {"message": _answer(r, to_name, ""), "row": _row(source_id, key)}


def make_rule(source_id: str, key: str, to: str, now: float | None = None) -> dict:
    """Add the message's channel to ``to``'s row of the routing table, as
    the CEO, and rewrite the Slack channels line of ``to``'s section of the
    setup file from the row. Refused for a message not in a channel, for a
    channel another row lists (the table's words), and while ``to``'s
    section is written loosely with words not applied yet."""
    now = time.time() if now is None else now
    try:
        with outside_setup.section_change(to, {"slack"}):
            return _make_rule(source_id, key, to, now)
    except outside_route.Refused as e:
        raise Refused(str(e)) from None


def _make_rule(source_id: str, key: str, to: str, now: float) -> dict:
    with _LOCK:
        to_name = _target(to)
        r = _row(source_id, key)
        ch = r["channel"]
        if not ch:
            raise Refused("this message is not in a channel, so there is no channel to add")
        try:
            if to in outside_route.channel_rows(source_id, ch):
                raise Refused(f"#{ch} is in {to_name}'s row already")
            rows_now = outside_route.table(source_id)["rows"] or []
            mine = next((x for x in rows_now if x.get("projectId") == to), None)
            old = list(((mine or {}).get("values") or {}).get(outside_route.CHANNELS) or [])
            res = outside_route.set_row(source_id, to, {outside_route.CHANNELS: old + [ch]},
                                        {"kind": "ceo"}, now=now)
        except outside_route.Refused as e:
            raise Refused(str(e)) from None
        if not res["changed"]:
            raise Refused(f"#{ch} is in {to_name}'s row already")
        outside_route.log_event({"source": source_id, "key": key, "action": RULE, "channel": ch,
                                 "to": [to], "toNames": [to_name], "by": {"kind": "ceo"}}, now)
    # The setup file is what people set (#15): section_change rewrites the
    # Slack channels line of ``to``'s section, or the next apply of the file
    # would take the channel out again.
    return {"message": f"#{ch} is in {to_name}'s row now: its next messages go there. "
                       f"This one stays where it is.",
            "row": _row(source_id, key)}


def _refusing(fn):
    """The routing module's refusals come out as this module's, which the
    dashboard turns into words for the page."""
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except outside_route.Refused as e:
            raise Refused(str(e)) from None
    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


ACTIONS = {"move": _refusing(send_elsewhere), "send": _refusing(send_anyway),
           "rule": _refusing(make_rule)}
