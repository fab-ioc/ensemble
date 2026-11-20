"""The shape every outside source has (design #1, section 1).

A *source* is one outside message service: Slack and Jira now, Discord later.
Its module only talks to the service: it reads new messages and turns them
into ``Message`` records, reads a whole thread when asked, and says which
settings it needs. Everything that is the same for every service (when to
check, read positions, never twice, catching up, health, decide-only) lives
once in ``outside.py``.

What a source module provides is the ``Source`` class below, with these
methods:

* ``fields()``: the settings its page draws (connection and what is watched).
* ``route_fields()``: the columns of its routing table, one row per
  initiative (for Slack: the channels, checked first, and the topic).
* ``problem(settings)``: "" when it is set up enough to run, else what is
  missing, in plain words.
* ``plan(watch, positions)``: the reads a check will make, by name.
* ``check(watch, positions, now)``: reads only, never sends. Returns a
  ``Check``.
* ``remember(settings, memory)``: the frame hands it its settings and kept
  memory when it makes the object and before each ``thread()``.
* ``found(memory)``: the values of its "found" fields, for the page.
* ``thread(ref)``: a whole thread, for the PO's read tool (step 3).
* ``place_values(m)`` and ``work_items(m)``: what routing matches a
  message by, beyond its channel (Jira: project, epic, components; the
  ticket and epic as work items).
* ``send(reply_to, text)``: later; raises ``NotSupported`` in this round.

**Read positions** are times, in seconds since 1970, one per read (a search,
a channel): where that read got to. The frame hands ``check()`` the time each
read starts from, with the overlap and the 3-day limit already applied. A
read the frame does not know yet is missing from ``positions`` and starts
from ``now``: nothing older than a source's first check is ever delivered. A
service that reads by message id rather than by time (Discord) keeps the id
in its memory (``Watch.memory`` and ``Check.memory``) and still reports the
time for the frame's rules.

Every read a good check makes reports its position; a read that found
nothing new reports ``now``. A read that is still wanted but failed either
fails the whole check or reports its old position again: a read missing
from good checks is taken as no longer made once it has been missing longer
than the source has gone unread since, or longer than the 3-day limit. It is
then forgotten, and starts from ``now`` if it comes back. A position must be
a time no later than ``now``; one after ``now`` is taken as ``now``, one that
is not a number is ignored.

**The repo is public.** No real user, channel or workspace id goes in a
source module, its tests or its fixtures: made-up ones only (``U000TEST01``,
``C000TEST01``, ``example.slack.com``). ``tests/test_outside_public.py``
scans for them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# What a check came to.
OK = "ok"
FAILED = "failed"
NOT_SET_UP = "not set up"
OUTCOMES = (OK, FAILED, NOT_SET_UP)

# Why a message was seen (``Message.why_seen``). A source may only use these,
# so routing and the log can count them the same way for every service.
WHY_DM = "dm"                       # a DM or group DM
WHY_MENTION = "mention"             # mentions the user
WHY_GROUP = "group"                 # mentions a group the user is in
WHY_CHANNEL = "channel"             # every message of a watched channel
WHY_THREAD = "thread"               # a thread the user is in
WHY_INITIATIVE = "initiative"       # a channel or thread an initiative's routing lists
WHY_TICKET = "ticket"               # a ticket the user is on (assigned, reported, watching)
WHY_SEEN = (WHY_DM, WHY_MENTION, WHY_GROUP, WHY_CHANNEL, WHY_THREAD, WHY_INITIATIVE, WHY_TICKET)

# The kinds of place a message is written in (``Place.kind``).
PLACE_KINDS = ("dm", "group dm", "channel", "private channel", "thread", "ticket", "other")


class NotSupported(Exception):
    """The source cannot do this (``send`` in this round)."""


@dataclass
class Place:
    """Where a message was written."""
    id: str                         # the service's id of the channel or DM
    name: str = ""                  # shown on pages and in lines: "#made-up-pricing", "DM with …"
    kind: str = "channel"           # one of PLACE_KINDS


@dataclass
class Author:
    id: str
    name: str = ""
    bot: bool = False
    me: bool = False                # the user themselves


@dataclass
class Thread:
    """The thread a message belongs to."""
    id: str                         # stable for the whole thread: Slack "<channel>:<thread_ts>"
    first: bool = False             # this message started the thread
    # The thread's first message, when the reader has it (Slack fetches it):
    # routing shows it next to a reply, so "looks good" carries its ticket.
    head: str = ""


@dataclass
class Message:
    """One message, the same for every source. The router, delivery, the log
    and the PO's read tool use only this."""
    key: str                        # unique and stable, starts with "<kind>:": "slack:<ws>:<channel>:<ts>"
    source: str                     # the source's kind: "slack"
    place: Place
    author: Author
    at: float                       # when it was written, seconds since 1970
    text: str = ""
    link: str = ""
    thread: Thread | None = None
    why_seen: str = WHY_CHANNEL     # one of WHY_SEEN
    addressed: bool = False         # the user is asked, not only told
    # What a later send needs to reply in this message's thread (Slack:
    # workspace, channel, thread_ts). Only the source reads it; the frame and
    # the queue keep it as it is, so sending later changes nothing here.
    reply_to: dict = field(default_factory=dict)
    # What the service says of the message's place beyond its name, for
    # routing and the PO's read tool. Jira: the event ("comment", "status",
    # ...), the ticket, its project, epic, components and status. Plain
    # strings and lists of them only. Empty for Slack.
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        d = dict(d)
        d["place"] = Place(**_known(Place, d.get("place") or {"id": ""}))
        d["author"] = Author(**_known(Author, d.get("author") or {"id": ""}))
        th = d.get("thread")
        d["thread"] = Thread(**_known(Thread, th)) if isinstance(th, dict) else None
        return cls(**_known(cls, d))


def _known(cls, d: dict) -> dict:
    names = set(cls.__dataclass_fields__)
    return {k: v for k, v in d.items() if k in names}


@dataclass
class Check:
    """What one check found."""
    outcome: str = OK               # one of OUTCOMES
    reason: str = ""                # for "failed" and "not set up": plain words, with the fix
    messages: list[Message] = field(default_factory=list)
    # Where each read got to: {read name: time}, one for every read made. A
    # read that got everything up to now says now; a read whose page was full
    # says the time of the newest message it got, and names itself in
    # ``full``. A read left out is taken as not made (see the module notes).
    positions: dict[str, float] = field(default_factory=dict)
    full: set[str] = field(default_factory=set)
    # What the source wants kept until its next check (its own user id, the
    # threads it follows, group membership): saved by the frame after a good
    # check and handed back in ``Watch.memory``. None keeps what was kept.
    memory: dict | None = None
    # Work item states the source learned, ``[(ticket key, state, since)]``
    # (Jira: "open", "released", "closed" or "done"): the frame hands them
    # to ``outside_items.note_ticket_state`` after a good check.
    item_states: list = field(default_factory=list)

    @property
    def more(self) -> bool:
        """A page was full: more is waiting, read it on the next tick."""
        return bool(self.full)


@dataclass
class Watch:
    """What a check is to read, handed to ``plan`` and ``check``."""
    settings: dict = field(default_factory=dict)    # the values of ``fields()``
    # Channels and threads the routing tables add (step 4): every message
    # there is read even when the user did not list it. Each is a dict with
    # at least "channel", and "thread" for a thread.
    places: list[dict] = field(default_factory=list)
    memory: dict = field(default_factory=dict)      # what the last good check kept
    source_id: str = ""                             # which configured source this is


# Field kinds a page knows how to draw.
FIELD_KINDS = ("switch", "text", "number", "choice", "list", "found")


@dataclass
class Field:
    """One setting a source's page draws, or one column of its routing table.
    The page (step 6) is drawn from these, so a new source needs no page code."""
    key: str
    label: str
    kind: str = "text"              # one of FIELD_KINDS; "found" is shown, not edited
    default: Any = None
    choices: tuple = ()             # for "choice": (value, label) pairs
    help: str = ""                  # one plain sentence under the field

    def to_dict(self) -> dict:
        d = asdict(self)
        d["choices"] = [list(c) for c in self.choices]
        return d


class Source:
    """The base every source module's class extends. A subclass sets the
    class attributes and overrides what it can do."""
    kind = ""                       # stable name used in ids, settings and lines; never changes
    label = ""                      # shown on the page and as the line's label: [slack]
    can_send = False                # False for every source in this round

    def fields(self) -> list[Field]:
        """The settings its page draws: connection and what is watched."""
        return []

    def route_fields(self) -> list[Field]:
        """The columns of its routing table: one row per initiative, these
        columns in it. Empty: the source is not routed by table."""
        return []

    def problem(self, settings: dict) -> str:
        """"" when set up enough to run, else what is missing."""
        return ""

    def plan(self, watch: Watch, positions: dict[str, float]) -> list[str]:
        """The names of the reads a check will make (for the log and tests)."""
        return []

    def check(self, watch: Watch, positions: dict[str, float], now: float) -> Check:
        """Read what is new since ``positions``. Reads only; never sends.
        An exception counts as a failed check, with its words as the reason."""
        raise NotImplementedError

    def remember(self, settings: dict, memory: dict) -> None:
        """The frame hands the source its settings and what its last good
        check kept (``Check.memory``) when it makes the object and before
        each ``thread()``: after a hub restart no check has run yet. A
        source that needs neither outside ``check()`` leaves this alone."""

    def found(self, memory: dict) -> dict:
        """The values of its "found" fields, from the kept memory, for the
        page."""
        return {}

    def place_values(self, m: Message) -> dict:
        """The values of ``m`` for its routing table's place columns other
        than channels, ``{column key: [values]}``: a row that lists one of
        them takes the message (Jira: its project, epic and components).
        Slack's channels are matched by the router itself: empty."""
        return {}

    def work_items(self, m: Message) -> tuple[list, list]:
        """The work items ``m`` is about beyond what its text names, as
        ``work_items.find`` gives them (``[(kind, key)]``): ``(its own,
        around it)``. The router (``outside_route.message_items``) puts its
        own before those its text names, and those around it (Jira: the
        ticket's epic) with its thread's first message: they count only when
        no row lists one of its own. Slack: none (its text says it all)."""
        return [], []

    def thread(self, ref: str) -> list[Message]:
        """The whole thread ``ref`` (a ``Thread.id``)."""
        raise NotSupported(f"{self.label or self.kind} cannot read a whole thread")

    def send(self, reply_to: dict, text: str):
        """Later. No source sends in this round."""
        raise NotSupported(f"{self.label or self.kind} cannot send")

    def describe(self) -> dict:
        """What the Input sources panel needs to know of this type (step 6)."""
        return {"kind": self.kind, "label": self.label, "canSend": bool(self.can_send),
                "ready": True,
                "fields": [f.to_dict() for f in self.fields()],
                "routeFields": [f.to_dict() for f in self.route_fields()]}
