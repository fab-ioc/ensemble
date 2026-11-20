"""Slack as an outside source (design #1, sections 2 and 3).

Ported from Slack Man's watcher (`slackwatch/rules.py` and the reading half of
`slackwatch/check.py`). This module decides what to ask Slack for and which of
the answers are new messages; `slack_connector.py` makes the reads, and
`slack_parse.py` and `slack_threads.py` read the answers. Nothing here sends.

What is watched, all of it reads:

* **DMs and group DMs** ("dms"), leaving out bots.
* **Mentions of the user** ("mention:<id>").
* **User groups** ("group:<id>"): a group listed under `groups` counts only for
  messages written while the user was in it, judged from Slackbot's "added to" and
  "removed from" DMs ("slackbot"); one listed under `groupsAlways` always counts.
* **Threads the user is in** (see `slack_threads.py`).
* **Channels**, every message: the user's own (`channels`) and the ones the
  routing tables add (`Watch.places`), plus the threads the tables list.

**Who the user is** is found, not typed. The first check reads the user's own
profile (their id), Slackbot's DM (its id, for group membership) and one search
result (the workspace's address, for message keys and links). They are kept in
the source's memory under "identity" and shown on the page read-only.

**Read positions** (see `sources/base.py`): one per search, named by its label.
The frame hands in where each one starts, with its overlap applied; a read it
does not know yet starts at `now`. A full page reports the time of the newest
message it got and is named in `Check.full`.

The repo is public: no real Slack id goes in this file or its tests.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field

from . import slack_threads as threadlist
from .base import (OK, FAILED, NOT_SET_UP, WHY_CHANNEL, WHY_DM, WHY_GROUP, WHY_INITIATIVE,
                   WHY_MENTION, WHY_THREAD, Author, Check, Field, Message, NotSupported, Place,
                   Source, Thread, Watch)
from .slack_connector import PAGE_SIZE, Query, ReadError, ReadResult, add_usage, find_claude, make_reader
from .slack_parse import SLACKBOT, link_for, membership_events, workspace_from_link

# How far back to read Slackbot's DM when the user's group membership is not known yet.
MEMBERSHIP_LOOKBACK_SECONDS = 14 * 24 * 3600
# Added/removed messages kept per group, so a late-found mention is judged by the
# membership at its own time, not by the latest one.
HISTORY_KEEP = 40
THREAD_DAYS = 14
MAX_THREAD_PAGES = 5  # pages of replies read for a whole thread (thread())

INITIATIVE_THREADS = "initiative-threads"
SLACKBOT_READ = "slackbot"
# Reads that find things for the source itself, never messages to hand on.
_NOT_MESSAGES = (SLACKBOT_READ, threadlist.SEED)
SEED_FROM = "seedFrom"  # memory: where the one-time thread look back goes on from

_USER_ID = re.compile(r"[UW][A-Z0-9]{2,}")
_GROUP_ID = re.compile(r"S[A-Z0-9]{2,}")
_CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]{2,}")
_TS = re.compile(r"\d+\.\d+")


@dataclass
class SlackCheck(Check):
    """A Check with what the frame may want for health and the log.

    code: a fixed word for a failure (from slack_connector.ReadError): not_connected,
    not_signed_in, timeout, unreadable, unplanned_call, write_tool, slack_error,
    not_set_up, error. The frame's grace after a wake applies to not_connected.
    usage: the plan use of the Claude runs this check made (tokens, cost).
    notes: plain sentences for the health line ("3 channels left out ...").
    """
    code: str = ""
    usage: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    runs: int = 0


# ---------- settings ----------

DEFAULTS = {
    "reader": "connector",
    "dms": True,
    "mentions": True,
    "groups": [],
    "groupsAlways": [],
    "threads": True,
    "threadDays": THREAD_DAYS,
    "maxThreadChannels": threadlist.MAX_CHANNELS,
    "channels": [],
}


def settings_of(settings: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (settings or {}).items() if v is not None})
    for k in ("groups", "groupsAlways", "channels"):
        v = out.get(k) or []
        out[k] = [v] if isinstance(v, str) else [str(x).strip() for x in v if str(x).strip()]
    return out


def _ids(values, pattern) -> list:
    """The values that are well-formed ids, once each, in order. Anything else is left
    out here (and named by problem()): an id goes into a search filter as it is."""
    return list(dict.fromkeys(v for v in values if pattern.fullmatch(v)))


# ---------- planning ----------

def _search(label, after, **extra):
    args = dict(extra)
    args.update({
        "after": str(int(after)),
        "sort": "timestamp",
        "sort_dir": "asc",
        "limit": PAGE_SIZE,
        "include_context": False,
        "response_format": "detailed",
    })
    return Query(label, "search", args)


def place_channels(places) -> list:
    return _ids([str((p or {}).get("channel") or "") for p in places or [] if not (p or {}).get("thread")],
                _CHANNEL_ID)


def place_threads(places) -> set:
    """{"channel:ts"} of the threads the routing tables list."""
    out = set()
    for p in places or []:
        ch, ts = str((p or {}).get("channel") or ""), str((p or {}).get("thread") or "")
        if _CHANNEL_ID.fullmatch(ch) and _TS.fullmatch(ts):
            out.add(f"{ch}:{ts}")
    return out


def plan_queries(settings: dict, memory: dict, positions: dict, now: float, places=()) -> list:
    """The Slack reads for this check (the user's id must be known).

    Each search starts where the frame says (positions[label]); one it does not know
    starts now, so nothing older than the first check is handed on. The one-time
    look back over threads starts thread_days ago.
    """
    s = settings_of(settings)
    ident = (memory or {}).get("identity") or {}
    me = ident["userId"]
    positions = positions or {}

    def after_for(label):
        return positions.get(label, now)

    not_me = f"-from:<@{me}>"
    queries = []
    if s["dms"]:
        queries.append(_search("dms", after_for("dms"), filters=not_me, channel_types="im,mpim",
                               include_bots=False))
    if s["mentions"]:
        label = f"mention:{me}"
        queries.append(_search(label, after_for(label), keywords=[f"<@{me}>"], filters=not_me,
                               include_bots=True))
    member_only = _ids(s["groups"], _GROUP_ID)
    for gid in list(dict.fromkeys(member_only + _ids(s["groupsAlways"], _GROUP_ID))):
        # The bare group id finds both ways a group mention is stored: `<@S...>` when a
        # person types it, `<!subteam^S...>` when a bot posts it (seen on a real
        # workspace, 2026-11-11: `<!subteam^S...>` alone misses every mention typed
        # by a person). Slackbot's added/removed DMs carry the id too; they are read
        # from the Slackbot DM, so the search leaves them out.
        label = f"group:{gid}"
        queries.append(_search(label, after_for(label), keywords=[gid],
                               filters=f"{not_me} -from:<@{SLACKBOT}>", include_bots=True))
    for cid in list(dict.fromkeys(_ids(s["channels"], _CHANNEL_ID) + place_channels(places))):
        label = f"channel:{cid}"
        queries.append(_search(label, after_for(label), filters=f"in:<#{cid}> {not_me}", include_bots=True))
    if s["threads"]:
        queries += _thread_queries(s, memory, me, now, after_for, positions)
    wanted = sorted({k.split(":", 1)[0] for k in place_threads(places)} - {""})
    wanted = [c for c in wanted if not c.startswith("D")]
    if wanted:
        within = " ".join(f"in:<#{c}>" for c in wanted)
        queries.append(_search(INITIATIVE_THREADS, after_for(INITIATIVE_THREADS),
                               filters=f"is:thread {within} {not_me}", include_bots=True))
    if member_only and ident.get("slackbotDm"):
        known = (memory or {}).get("membership", {})
        oldest = after_for(SLACKBOT_READ)
        if any(gid not in known for gid in member_only):
            oldest = min(oldest, now - MEMBERSHIP_LOOKBACK_SECONDS)
        queries.append(Query(SLACKBOT_READ, "channel", {
            "channel_id": ident["slackbotDm"],
            "oldest": str(int(oldest)),
            "limit": PAGE_SIZE,
            "response_format": "detailed",
        }))
    return queries


def _thread_queries(s, memory, me, now, after_for, positions):
    """The searches for new replies in threads the user is in (see slack_threads.py)."""
    not_me = f"-from:<@{me}>"
    out = [_search(threadlist.MY_THREADS, after_for(threadlist.MY_THREADS),
                   filters=f"with:<@{me}> is:thread {not_me}", include_bots=True)]
    channels = threadlist.follow_channels((memory or {}).get("threads", {}), s["maxThreadChannels"])
    if channels:
        within = " ".join(f"in:<#{c}>" for c in channels)
        out.append(_search(threadlist.MENTIONED_THREADS, after_for(threadlist.MENTIONED_THREADS),
                           filters=f"is:thread {within} {not_me}", include_bots=True))
    if not (memory or {}).get("threads_seeded"):
        # Once: the threads where the user was mentioned in the last thread_days days.
        # Where a full page left it is kept in the source's memory ("seedFrom"), not
        # in the frame's positions: the frame never starts a read more than
        # maxGapDays back, and the look back goes further.
        start = (memory or {}).get(SEED_FROM) or now - int(s["threadDays"]) * 86400
        out.append(_search(threadlist.SEED, start, keywords=[f"<@{me}>"], filters=not_me, include_bots=True))
    return out


def identity_queries(memory: dict, need_slackbot: bool) -> list:
    """The reads that find who the user is, for what is not known yet."""
    ident = (memory or {}).get("identity") or {}
    out = []
    if not ident.get("userId"):
        out.append(Query("profile", "profile", {"response_format": "detailed"}))
    if need_slackbot and not ident.get("slackbotDm"):
        # Reading the DM by Slackbot's user id answers with the DM's own id.
        out.append(Query("slackbot-dm", "channel", {"channel_id": SLACKBOT, "limit": 1,
                                                    "response_format": "detailed"}))
    if not ident.get("workspace"):
        # Any message's link carries the workspace's address. A search needs a
        # filter or words (one with neither is refused), so: the newest thread reply.
        out.append(Query("workspace", "search", {"filters": "is:thread", "sort": "timestamp",
                                                 "sort_dir": "desc", "limit": 1, "include_context": False,
                                                 "include_bots": True, "response_format": "detailed"}))
    return out


def learn_identity(memory: dict, got: ReadResult) -> dict:
    ident = dict((memory or {}).get("identity") or {})
    prof = got.facts.get("profile")
    if prof:
        ident["userId"], ident["name"] = prof["id"], prof.get("name", "")
    dm = got.facts.get("slackbot-dm") or {}
    if dm.get("id", "").startswith("D"):
        ident["slackbotDm"] = dm["id"]
    for msgs in got.messages.values():
        for m in msgs:
            ws = workspace_from_link(m.permalink)
            if ws and not ident.get("workspace"):
                ident["workspace"] = ws
    return ident


# ---------- group membership ----------

def update_membership(prior: dict, events):
    """Apply Slackbot's added/removed messages to the known membership.

    prior: {group id: {"member": bool, "ts": float, "history": [[ts, member], ...]}}.
    events: [(ts, group id, member)]. Returns the new membership: the latest state
    ("member", "ts") and the last HISTORY_KEEP changes.
    """
    out = {}
    for gid, known in (prior or {}).items():
        history = [list(h) for h in known.get("history") or [[known["ts"], known["member"]]]]
        out[gid] = {"member": known["member"], "ts": known["ts"], "history": history}
    for ts, gid, member in sorted(events):
        known = out.setdefault(gid, {"member": member, "ts": ts, "history": []})
        if [ts, member] not in known["history"]:
            known["history"].append([ts, member])
        known["history"] = sorted(known["history"])[-HISTORY_KEEP:]
        latest_ts, latest_member = known["history"][-1]
        known["member"], known["ts"] = latest_member, latest_ts
    return out


def member_at(gid: str, ts: float, prior: dict, events) -> bool:
    """Was the user in the group at time ts? The latest added/removed message before ts
    decides. With no message at all before ts, the user counts as not in the group."""
    known = (prior or {}).get(gid) or {}
    changes = [tuple(h) for h in known.get("history") or []]
    if not changes and "ts" in known:
        changes = [(known["ts"], known["member"])]
    changes += [(ev_ts, member) for ev_ts, ev_gid, member in events if ev_gid == gid]
    before = [c for c in changes if c[0] <= ts]
    return bool(before and max(before)[1])


def group_mentions(fetched, settings: dict, memory: dict, events) -> dict:
    """{message key: group id} for the messages a group search found that count: all
    of them for a group in groupsAlways, else those written while the user was in it."""
    s = settings_of(settings)
    always = set(_ids(s["groupsAlways"], _GROUP_ID))
    prior = (memory or {}).get("membership", {})
    out = {}
    for label, messages in fetched.messages.items():
        if not label.startswith("group:"):
            continue
        gid = label.split(":", 1)[1]
        for msg in messages:
            if msg.sender_id == SLACKBOT:
                continue
            if gid in always or member_at(gid, float(msg.ts), prior, events):
                out.setdefault(msg.key, gid)
    return out


# ---------- choosing the new messages ----------

def select_new(fetched, settings: dict, memory: dict, me: str, group_hits: dict, threads: dict,
               places=()) -> list:
    """The messages to hand on: watched and not the user's own. A message found by
    several searches is handed on once. Sorted by time. (Never twice across checks
    is the frame's job: it keeps the keys it has seen.)"""
    wanted_threads = place_threads(places)
    chosen = {}
    for label, messages in fetched.messages.items():
        if label in _NOT_MESSAGES or label.startswith(threadlist.FIRST_PREFIX):
            continue
        for msg in messages:
            if msg.key in chosen:
                continue
            if msg.sender_id == me or msg.sender_id == SLACKBOT:
                continue
            if label == "dms" and msg.is_bot:
                continue
            if label == threadlist.MY_THREADS and msg.is_bot and msg.channel_id.startswith("D"):
                continue  # bot DMs are left out, as in the DM search
            if label == threadlist.MENTIONED_THREADS and not threadlist.followed(msg, threads):
                continue  # a thread in the same channel that the user is not in
            if label == INITIATIVE_THREADS and threadlist.key(msg) not in wanted_threads:
                continue  # another thread in a channel where a routing table lists one
            if label.startswith("group:") and msg.key not in group_hits:
                continue  # written while the user was not in the group
            chosen[msg.key] = msg
    return sorted(chosen.values(), key=lambda m: float(m.ts))


def first_new_mention(memory: dict, fetched, me: str, group_hits: dict, max_channels: int,
                      now: float | None = None):
    """Where the "mentioned-threads" search must start next time to see every reply
    in a thread whose channel it did not search this time.

    A mention found now puts its thread on the list, but its channel is searched only
    from the next check on. A reply written between the mention and this check
    would be missed. Starting at the mention itself covers it; older replies in the
    thread are not wanted, so not earlier. A group mention that counts is the same.
    A thread found by the one-time look back is older than the look back's own reach
    for replies, so its channel is searched from this check on (`now`), not from the
    mention: replies written before it are not handed on.
    """
    searched = set(threadlist.follow_channels((memory or {}).get("threads", {}), max_channels))
    found = list(fetched.messages.get(f"mention:{me}", []))
    for label, messages in fetched.messages.items():
        if label.startswith("group:"):
            found += [m for m in messages if m.key in (group_hits or {})]
    times = [float(m.ts) for m in found if not m.channel_id.startswith("D") and m.channel_id not in searched]
    start = int(min(times)) - 1 if times else None
    if now is not None and any(not m.channel_id.startswith("D") and m.channel_id not in searched
                               for m in fetched.messages.get(threadlist.SEED, [])):
        start = float(now) if start is None else min(start, float(now))
    return start


def new_positions(queries, fetched, now: float):
    """(positions, full, seeded): where each read got to. A full page says the time of
    its newest message and is named in full; any other read got everything up to now."""
    positions, full, seeded = {}, set(), False
    for q in queries:
        msgs = fetched.messages.get(q.label) or []
        if q.label in fetched.full_pages and msgs:
            positions[q.label] = float(int(max(float(m.ts) for m in msgs)))
            full.add(q.label)
        else:
            positions[q.label] = float(now)
            if q.label == threadlist.SEED:
                seeded = True  # the look back is done
    return positions, full, seeded


# ---------- message records ----------

def _found_by(fetched) -> dict:
    out = {}
    for label, messages in fetched.messages.items():
        for msg in messages:
            out.setdefault(msg.key, set()).add(label)
    return out


def why_seen(msg, found_by: set, me: str, group: str | None, threads: dict, own_channels: set,
             wanted_threads: set) -> str:
    entry = (threads or {}).get(threadlist.key(msg), {})
    if msg.channel_id.startswith("D") or "dms" in found_by:
        return WHY_DM
    if threadlist.mentions(msg.text, me):
        return WHY_MENTION
    if group:
        return WHY_GROUP
    if (entry.get("mentioned") or entry.get("written") or threadlist.MY_THREADS in found_by
            or threadlist.MENTIONED_THREADS in found_by):
        return WHY_THREAD
    if msg.channel_id in own_channels:
        return WHY_CHANNEL
    if INITIATIVE_THREADS in found_by or threadlist.key(msg) in wanted_threads or any(
            label.startswith("channel:") for label in found_by):
        return WHY_INITIATIVE
    return WHY_CHANNEL


ADDRESSED = (WHY_DM, WHY_MENTION, WHY_GROUP, WHY_THREAD)


def _place_kind(msg, found_by: set) -> str:
    if msg.channel_id.startswith("D"):
        return "dm"
    if "dms" in found_by or (msg.channel_name or "").lstrip("#").startswith("mpdm-"):
        return "group dm"
    return "channel"


def to_record(msg, workspace: str, found_by: set, me: str, group, threads: dict, fetched,
              own_channels: set, wanted_threads: set) -> Message:
    why = why_seen(msg, found_by, me, group, threads, own_channels, wanted_threads)
    reply = threadlist.is_reply(msg)
    thread = None
    if reply or msg.reply_count or threadlist.key(msg) in (threads or {}):
        head = threadlist.first_of(msg, threads, fetched)[0] if reply else ""
        thread = Thread(id=threadlist.key(msg), first=not reply, head=head)
    return Message(
        key=f"slack:{workspace}:{msg.channel_id}:{msg.ts}",
        source="slack",
        place=Place(id=msg.channel_id, name=msg.channel_name or "", kind=_place_kind(msg, found_by)),
        author=Author(id=msg.sender_id, name=re.sub(r"\s*<[^>]*>", "", msg.sender_name or "").strip(),
                      bot=bool(msg.is_bot), me=msg.sender_id == me),
        at=float(msg.ts),
        text=msg.text or "",
        link=msg.permalink or link_for(workspace, msg.channel_id, msg.ts, msg.thread_ts),
        thread=thread,
        why_seen=why,
        addressed=why in ADDRESSED,
        reply_to={"workspace": workspace, "channel": msg.channel_id, "thread_ts": msg.thread_ts or msg.ts},
    )


# ---------- the source ----------

class SlackSource(Source):
    kind = "slack"
    label = "Slack"
    can_send = False

    def __init__(self, claude_path: str = "", cwd: str = "", reader_factory=None, memory: dict | None = None):
        """claude_path: the Claude Code to run (found on PATH when empty). cwd: the
        folder the Claude runs start in. reader_factory(settings, memory): for tests
        and a later token reader; make_reader by default. memory: what the last good
        check kept, for thread() before any check."""
        self.claude_path = claude_path
        self.cwd = cwd
        self._factory = reader_factory
        self.memory = dict(memory or {})
        self.settings: dict = {}

    def _reader(self, settings, memory):
        if self._factory is not None:
            return self._factory(settings, memory)
        return make_reader(settings, memory, claude_path=self.claude_path, cwd=self.cwd)

    # -- what the page draws -------------------------------------------------
    def fields(self) -> list[Field]:
        return [
            Field("reader", "Reader", "choice", "connector",
                  choices=(("connector", "Claude's Slack connection"),),
                  help="Slack is read through the Slack connection of your Claude login. A Slack token comes later."),
            Field("dms", "DMs and group DMs", "switch", True, help="Every DM and group DM, except from bots."),
            Field("mentions", "Mentions of me", "switch", True),
            Field("groups", "User groups, while I am in them", "list", [],
                  help="Group ids (S…). A mention counts only if you were in the group when it was written."),
            Field("groupsAlways", "User groups, always", "list", [],
                  help="Group ids (S…). Every mention counts."),
            Field("threads", "Threads I am in", "switch", True,
                  help="Threads where you are mentioned or have written."),
            Field("threadDays", "Follow a thread for (days)", "number", THREAD_DAYS,
                  help="A thread with no new message for this long is no longer followed."),
            Field("maxThreadChannels", "Most channels searched for threads", "number", threadlist.MAX_CHANNELS,
                  help="Threads where you are only mentioned are searched in at most this many channels, "
                       "the most recently active first. The health line says how many are left out."),
            Field("channels", "Channels with every message", "list", [], help="Channel ids (C…)."),
            Field("userId", "Your Slack id", "found", "", help="Found by the first check."),
            Field("name", "Your Slack name", "found", ""),
            Field("workspace", "Workspace", "found", "", help="Found by the first check, for links."),
            Field("slackbotDm", "Slackbot DM", "found", "",
                  help="Found by the first check: Slackbot says there when you are added to or removed from a group."),
        ]

    def route_fields(self) -> list[Field]:
        return [Field("channels", "Channels", "list", [],
                      help="Every message in these channels goes to the initiative. Checked first.")]

    def problem(self, settings: dict) -> str:
        s = settings_of(settings)
        if s["reader"] != "connector":
            return "The Slack token reader is not built yet: choose Claude's Slack connection."
        bad = [g for g in s["groups"] + s["groupsAlways"] if not _GROUP_ID.fullmatch(g)]
        if bad:
            return f"These are not Slack group ids (they start with S): {', '.join(bad)}."
        bad = [c for c in s["channels"] if not _CHANNEL_ID.fullmatch(c)]
        if bad:
            return f"These are not Slack channel ids (they start with C): {', '.join(bad)}."
        try:
            days, most = int(s["threadDays"]), int(s["maxThreadChannels"])
        except (TypeError, ValueError):
            return "The thread days and the most channels must be whole numbers."
        if days < 1 or most < 0:
            return "Threads must be followed for at least 1 day, and the most channels cannot be below 0."
        if not (s["dms"] or s["mentions"] or s["threads"] or s["groups"] or s["groupsAlways"] or s["channels"]):
            return "Nothing is chosen to watch: turn on DMs, mentions or threads, or list a group or a channel."
        if self._factory is None and not (os.path.exists(find_claude(self.claude_path))
                                          or shutil.which("claude")):
            return "Claude Code is not installed, so Claude's Slack connection cannot be used."
        return ""

    def plan(self, watch: Watch, positions: dict[str, float]) -> list[str]:
        s = settings_of(watch.settings)
        memory = watch.memory or {}
        ident = memory.get("identity") or {}
        names = [q.label for q in identity_queries(memory, bool(_ids(s["groups"], _GROUP_ID)))]
        if not ident.get("userId"):
            # Not known before the first check: the read names then say "me".
            memory = dict(memory, identity=dict(ident, userId="me"))
        names += [q.label for q in plan_queries(s, memory, positions, 0.0, watch.places)]
        return names

    # -- reading ---------------------------------------------------------------
    def check(self, watch: Watch, positions: dict[str, float], now: float) -> Check:
        settings = settings_of(watch.settings)
        self.settings = settings
        why_not = self.problem(settings)
        if why_not:
            return SlackCheck(outcome=NOT_SET_UP, reason=why_not, code="not_set_up")
        memory = dict(watch.memory or {})
        out = SlackCheck()
        try:
            reader = self._reader(settings, memory)
        except ReadError as e:
            return self._failed(e, out)
        try:
            return self._check(settings, memory, positions or {}, now, watch.places, reader, out)
        except ReadError as e:
            return self._failed(e, out, reader)
        finally:
            out.usage = add_usage(out.usage, getattr(reader, "usage", {}))
            out.runs = getattr(reader, "runs", 0)

    def _failed(self, e: ReadError, out: SlackCheck, reader=None) -> SlackCheck:
        out.outcome = NOT_SET_UP if e.code == "not_set_up" else FAILED
        out.reason = f"Slack could not be read: {e}"
        out.code = e.code
        out.messages, out.positions, out.full, out.memory = [], {}, set(), None
        return out

    def _check(self, settings, memory, positions, now, places, reader, out) -> SlackCheck:
        member_only = _ids(settings["groups"], _GROUP_ID)
        # 1. Who the user is, on the first check (and after "Find again").
        wanted = identity_queries(memory, bool(member_only))
        if wanted:
            got = reader.read(wanted)
            memory["identity"] = learn_identity(memory, got)
        ident = memory.get("identity") or {}
        if not ident.get("userId"):
            raise ReadError("your own Slack profile gave no user id", "unreadable")
        if not ident.get("workspace"):
            raise ReadError("no message was found to learn the workspace's address from; "
                            "the next check tries again", "error")

        # 2. The reads that find new messages.
        me = ident["userId"]
        queries = plan_queries(settings, memory, positions, now, places)
        fetched = reader.read(queries)
        events = membership_events(fetched.messages.get(SLACKBOT_READ, []))
        group_hits = group_mentions(fetched, settings, memory, events)
        days = int(settings["threadDays"])
        most = int(settings["maxThreadChannels"])
        threads = memory.get("threads", {})
        if settings["threads"]:
            threads = threadlist.learn(threads, fetched, me, now, days, group_hits)
        new = select_new(fetched, settings, memory, me, group_hits, threads, places)

        # 3. The first message of threads whose replies came in, read once and kept.
        # If this read fails, the messages still go, without it.
        firsts = threadlist.first_queries(new, threads, fetched)
        if firsts:
            try:
                got = reader.read(firsts)
                threads = threadlist.add_firsts(threads, got, me, now)
                fetched.messages.update(got.messages)
            except ReadError as e:
                out.notes.append(f"the first message of {len(firsts)} threads could not be read: {str(e)[:200]}")

        # 4. Records, positions and what to keep.
        found_by = _found_by(fetched)
        own = set(_ids(settings["channels"], _CHANNEL_ID))
        wanted_threads = place_threads(places)
        out.messages = [to_record(m, ident["workspace"], found_by.get(m.key, set()), me, group_hits.get(m.key),
                                  threads, fetched, own, wanted_threads) for m in new]
        out.positions, out.full, seeded = new_positions(queries, fetched, now)
        # The look back's position stays with the source (see _thread_queries); its
        # name stays in `full` so the frame reads again soon while it is not done.
        seed_at = out.positions.pop(threadlist.SEED, None)
        if settings["threads"]:
            # The frame never moves a position back (it keeps the later of old and
            # new). So a mention older than the "mentioned-threads" position, found
            # while the mention search catches up page by page, cannot pull it back:
            # replies in that thread between the mention and that position are
            # missed. Slack Man could move it back; this is rare and left as it is.
            start = first_new_mention(memory, fetched, me, group_hits, most, now)
            if start is not None and threadlist.MENTIONED_THREADS in out.positions:
                out.positions[threadlist.MENTIONED_THREADS] = min(out.positions[threadlist.MENTIONED_THREADS],
                                                                  float(start))
            elif start is not None:
                out.positions[threadlist.MENTIONED_THREADS] = float(start)
        left_out = threadlist.channels_left_out(threads, most) if settings["threads"] else 0
        if left_out:
            out.notes.append(f"{left_out} channels with threads where you are mentioned are not searched "
                             f"(more than {most}); replies there are missed until you are mentioned again")
        kept = dict(memory)
        kept["membership"] = update_membership(memory.get("membership", {}), events)
        kept["threads"] = threads
        kept["channelsLeftOut"] = left_out
        if seeded:
            kept["threads_seeded"] = True
            kept.pop(SEED_FROM, None)
        elif seed_at is not None and threadlist.SEED in out.full:
            kept[SEED_FROM] = seed_at
        prefix = getattr(reader, "prefix", "")
        if prefix:
            kept["toolPrefix"] = prefix
        out.memory = kept
        out.outcome = OK
        self.memory = kept
        return out

    def remember(self, settings: dict, memory: dict) -> None:
        """What thread() reads with until the next check: after a hub
        restart, the identity (workspace, user id) and tool prefix the last
        good check kept."""
        self.settings = settings_of(settings)
        self.memory = dict(memory or {})

    def found(self, memory: dict | None = None) -> dict:
        """The values of the "found" fields, for the page."""
        ident = ((memory if memory is not None else self.memory) or {}).get("identity") or {}
        return {k: ident.get(k, "") for k in ("userId", "name", "workspace", "slackbotDm")}

    def thread(self, ref: str) -> list[Message]:
        """The whole thread ref ("<channel>:<ts of its first message>"), oldest first."""
        channel, _, ts = (ref or "").partition(":")
        if not _CHANNEL_ID.fullmatch(channel) or not _TS.fullmatch(ts):
            raise ValueError(f"not a Slack thread: {ref!r}")
        memory = self.memory
        ident = memory.get("identity") or {}
        reader = self._reader(self.settings, memory)
        got_all, cursor = {}, ""
        for page in range(MAX_THREAD_PAGES):
            args = {"channel_id": channel, "message_ts": ts, "limit": 100, "response_format": "detailed"}
            if cursor:
                args["cursor"] = cursor
            got = reader.read([Query(f"thread:{page}", "thread", args)])
            for m in got.messages.get(f"thread:{page}", []):
                got_all.setdefault(m.key, m)
            cursor = (got.facts.get(f"thread:{page}") or {}).get("cursor", "")
            if not cursor:
                break
        me = ident.get("userId", "")
        workspace = ident.get("workspace", "")
        msgs = sorted(got_all.values(), key=lambda m: float(m.ts))
        whole = ReadResult(messages={"thread": msgs})
        known = {ref: {}}  # every message here belongs to this thread, the first one too
        return [to_record(m, workspace, {"thread"}, me, None, known, whole, set(), set()) for m in msgs]

    def send(self, reply_to: dict, text: str):
        raise NotSupported("Slack messages are not sent in this round")
