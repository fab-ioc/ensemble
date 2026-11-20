"""Jira as an outside source (#14, outside sources round 2 step 3).

This module decides what to ask Jira for and which of the answers are new
events; `jira_connector.py` makes the reads. Nothing here writes to Jira.

**What is read.** One search finds the tickets the user is on that changed
since the last check: assigned to them, reported by them, watched by them, or
mentioning them in a comment or the description (each a switch). For each
ticket found, one run reads its history and its last comments. Each of these
becomes one message:

* a **new ticket** (created since the last check),
* a **comment**,
* a **status change** ("Status: To Do → In Progress"),
* an **assignment** to or away from the user,
* a **linked pull request** (the ticket's count of pull requests went up).

Other changes (sprint, description, labels ...) are not handed on.

**A ticket too large to read whole** (a long history: Claude passes on about
50 KB of one answer) is read again without its history. Its status and
assignee changes are then found by comparing the ticket with what was kept
of it at the last read, so who made them is not known; a note on the health
line names the ticket. A first sighting of such a ticket only sets what is
kept (as for pull requests).

**Left out by rule, before any model:** a move to one of the statuses listed
under `dropStatuses` ("Ready for Release", "Released" by default) by someone
other than the user. They are counted, and the count is on the health line.
The user's own comments and moves are handed on, marked as theirs, unless
`mine` is off.

**Who the user is** is found, not typed: the account id and the Jira site
from the connection on the first check, the name from the first ticket that
shows it. Kept in the source's memory under "identity", shown on the page.

**Read position:** one read, "tickets". A search that came back full (or more
tickets changed than one check reads in detail) is named in `Check.full` and
keeps reporting the catch-up's start as its position; where the next search
starts (the last ticket it took) is kept in the source's memory as "page".
The next tick searches from there and still counts events from the
catch-up's start, so a ticket left for a later page keeps its older events.

**Places only route.** A routing table row may list Jira projects, epics and
components; a message whose ticket is in one of them goes to that row. They
do not widen what is read: a ticket the user is not on is never read.

The repo is public: no real account id, site or ticket key goes in this file
or its tests (made-up ones: `example.atlassian.net`, `ABC-12`).
"""
from __future__ import annotations

import html
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime

import work_items

from .base import (FAILED, NOT_SET_UP, OK, WHY_MENTION, WHY_TICKET, Author, Check, Field, Message,
                   NotSupported, Place, Source, Thread, Watch)
from .jira_connector import (PAGE_SIZE, SMALL_PAGE, TICKETS_PER_RUN, Query, ReadError, add_usage,
                             find_claude, make_reader)

TICKETS = "tickets"             # the one read with a position
NAME_READ = "name"              # finds the user's name, once
MAX_DETAILED = 3 * TICKETS_PER_RUN   # tickets read in detail in one check; the rest on the next tick
COMMENTS_READ = 20              # newest comments read per ticket
FEW_COMMENTS = 5                # read instead when 20 were too large to pass on
TICKETS_KEEP_S = 30 * 86400     # a ticket is remembered this long after it last changed
TICKETS_MAX = 2000
DROPPED_KEEP = 500              # moves left out by rule, remembered for today's count
TEXT_MAX = 4000                 # characters of a comment or description handed on

SEARCH_FIELDS = ["summary", "status", "assignee", "reporter", "updated", "created", "project", "parent",
                 "components", "issuetype"]

CREATED, COMMENT, STATUS, ASSIGNED, PULL_REQUEST = "created", "comment", "status", "assigned", "pr"
EVENTS = (CREATED, COMMENT, STATUS, ASSIGNED, PULL_REQUEST)

_KEY = re.compile(r"[A-Z][A-Z0-9_]*-\d+")


@dataclass
class JiraCheck(Check):
    """A Check with what the frame may want for health and the log (as Slack's)."""
    code: str = ""
    usage: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    runs: int = 0


# ---------- settings ----------

DEFAULTS = {
    "reader": "connector",
    "assigned": True,
    "reported": True,
    "watched": True,
    "mentioned": True,
    "mine": True,
    "dropStatuses": ["Ready for Release", "Released"],
}


def settings_of(settings: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (settings or {}).items() if v is not None})
    v = out.get("dropStatuses") or []
    out["dropStatuses"] = [v] if isinstance(v, str) else [str(x).strip() for x in v if str(x).strip()]
    return out


# ---------- small helpers ----------

def ts_of(value) -> float | None:
    """Seconds since 1970 of a Jira time ("2026-11-16T11:50:48.744+0100")."""
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    return None


_MENTION = re.compile(r'<[^>]*data-type="mention"[^>]*>(.*?)</[a-z]+>', re.I | re.S)
_BREAK = re.compile(r"<\s*(br|/p|/li|/h\d|/tr|/div)\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")


def plain(text, limit: int = TEXT_MAX) -> str:
    """A comment's or description's words, without markup: a mention becomes
    "@Name", paragraphs become lines."""
    s = str(text or "")
    s = _MENTION.sub(lambda m: _TAG.sub("", m.group(1)), s)
    s = _BREAK.sub("\n", s)
    s = html.unescape(_TAG.sub("", s))
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s if len(s) <= limit else s[:limit].rstrip() + " …"


def mentions(text, account_id: str) -> bool:
    return bool(account_id) and f'data-user-id="{account_id}"' in str(text or "")


def _person(d) -> tuple[str, str]:
    """(account id, name) of a Jira user; never their e-mail address."""
    if not isinstance(d, dict):
        return "", ""
    return str(d.get("accountId") or ""), str(d.get("displayName") or "")


_PR_JSON = re.compile(r'"pullrequest"\s*:\s*\{\s*"overall"\s*:\s*\{([^{}]*)\}')
_PR_PLAIN = re.compile(r"pullrequest=\{[^{}]*?state=(\w+)[^{}]*?stateCount=(\d+)")


def pull_requests(fields: dict) -> dict:
    """{count, state, at} of the pull requests Jira's Development field shows."""
    dev = ((fields or {}).get("customFields") or {}).get("Development")
    value = dev.get("value") if isinstance(dev, dict) else dev
    s = str(value or "")
    m = _PR_JSON.search(s)
    if m:
        body = m.group(1)
        count = re.search(r'"count"\s*:\s*(\d+)', body)
        state = re.search(r'"state"\s*:\s*"(\w+)"', body)
        at = re.search(r'"lastUpdated"\s*:\s*"([^"]+)"', body)
        if count:
            return {"count": int(count.group(1)), "state": state.group(1) if state else "",
                    "at": ts_of(at.group(1)) if at else None}
    m = _PR_PLAIN.search(s)
    if m:
        return {"count": int(m.group(2)), "state": m.group(1), "at": None}
    return {"count": 0, "state": "", "at": None}


# ---------- one ticket ----------

@dataclass
class Ticket:
    key: str
    summary: str = ""
    updated: str = ""
    created: float | None = None
    status: str = ""
    category: str = ""              # Jira's status category: "new", "indeterminate" or "done"
    kind: str = ""                  # Bug, Task, Epic ...
    project: str = ""
    project_name: str = ""
    epic: str = ""
    parent: str = ""
    components: list = field(default_factory=list)
    assignee: tuple = ("", "")
    reporter: tuple = ("", "")

    @property
    def updated_at(self) -> float | None:
        return ts_of(self.updated)


def ticket_of(issue: dict) -> Ticket | None:
    key = str((issue or {}).get("key") or "")
    if not _KEY.fullmatch(key):
        return None
    f = issue.get("fields") or {}
    itype = f.get("issuetype") or {}
    parent = f.get("parent") or {}
    pfields = parent.get("fields") or {}
    ptype = pfields.get("issuetype") or {}
    pkey = str(parent.get("key") or "")
    is_epic = str(itype.get("name") or "").lower() == "epic" or itype.get("hierarchyLevel") == 1
    parent_epic = str(ptype.get("name") or "").lower() == "epic" or ptype.get("hierarchyLevel") == 1
    project = f.get("project") or {}
    return Ticket(
        key=key,
        summary=str(f.get("summary") or ""),
        updated=str(f.get("updated") or ""),
        created=ts_of(f.get("created")),
        status=str((f.get("status") or {}).get("name") or ""),
        category=str(((f.get("status") or {}).get("statusCategory") or {}).get("key") or ""),
        kind=str(itype.get("name") or ""),
        project=str(project.get("key") or key.split("-")[0]),
        project_name=str(project.get("name") or ""),
        epic=key if is_epic else (pkey if parent_epic else ""),
        parent=pkey,
        components=[str(c.get("name")) for c in f.get("components") or [] if isinstance(c, dict) and c.get("name")],
        assignee=_person(f.get("assignee")),
        reporter=_person(f.get("reporter")),
    )


# A ticket's state for the routing table's work items (#13,
# ``outside_items.note_ticket_state``): a released, closed or done ticket
# leaves its row 14 days later.
RELEASED = {"released", "deployed", "live"}
CLOSED = {"closed", "won't do", "wont do", "won't fix", "cancelled", "canceled", "rejected", "obsolete",
          "duplicate", "declined", "abandoned"}
DONE = {"done", "resolved", "complete", "completed", "finished"}
FINISHED_STATES = ("released", "closed", "done")
PAGE_OVERLAP_S = 5 * 60         # the frame's overlap (outside.OVERLAP_S)


def ticket_state(t: Ticket) -> str:
    """"released", "closed", "done" or "open", from the status's name, else
    its category. "Ready for Release" and the like are still open."""
    name = t.status.casefold().strip()
    if name in RELEASED:
        return "released"
    if name in CLOSED:
        return "closed"
    if name in DONE:
        return "done"
    if name.startswith("ready for") or name.startswith("waiting"):
        return "open"
    return "done" if t.category == "done" else "open"


def comments_may_be_missing(answer: dict, since: float) -> bool:
    """True when a ticket has more comments than were read (only the newest
    20, or 5, are) and even the oldest one read is new: comments between it
    and ``since`` were not read."""
    cs = [c for c in (answer or {}).get("comments") or [] if isinstance(c, dict)]
    if not cs:
        return False
    total = answer.get("total")
    more = answer.get("isLast") is False or (isinstance(total, int) and total > len(cs))
    times = [ts_of(c.get("created")) for c in cs]
    oldest = min((x for x in times if x is not None), default=None)
    return more and oldest is not None and oldest >= since


def status_since(t: Ticket, issue: dict) -> float | None:
    """When the ticket moved to its present status, from its history (None
    when the history does not show it)."""
    best = None
    for h in ((issue or {}).get("changelog") or {}).get("histories") or []:
        at = ts_of((h or {}).get("created"))
        for item in (h or {}).get("items") or []:
            if (str((item or {}).get("field") or "").lower() == "status" and at is not None
                    and str(item.get("toString") or "").casefold() == t.status.casefold()):
                best = at if best is None else max(best, at)
    return best


def details_of(t: Ticket, kind: str) -> dict:
    out = {"event": kind, "ticket": t.key, "summary": t.summary, "type": t.kind, "status": t.status,
           "project": t.project, "projectName": t.project_name, "epic": t.epic, "parent": t.parent,
           "components": list(t.components)}
    return {k: v for k, v in out.items() if v not in ("", [], None) or k == "event"}


# ---------- planning ----------

def jql_for(s: dict, account_id: str, since: float, now: float) -> str:
    who = []
    if s["assigned"]:
        who.append("assignee = currentUser()")
    if s["reported"]:
        who.append("reporter = currentUser()")
    if s["watched"]:
        who.append("watcher = currentUser()")
    if s["mentioned"] and account_id:
        # A mention is stored with the account id in the text.
        who += [f'comment ~ "{account_id}"', f'description ~ "{account_id}"']
    # Minutes back from now (Jira's own clock and time zone do the rest):
    # a time written out would be read in the Jira user's time zone.
    minutes = max(1, math.ceil((now - since) / 60) + 1)
    return f'({" OR ".join(who)}) AND updated >= "-{minutes}m" ORDER BY updated ASC'


def identity_queries(memory: dict) -> list:
    ident = (memory or {}).get("identity") or {}
    out = []
    if not ident.get("accountId"):
        out.append(Query("me", "me", {}))
    if not ident.get("cloudId"):
        out.append(Query("sites", "sites", {}))
    return out


def learn_identity(memory: dict, answers: dict) -> dict:
    ident = dict((memory or {}).get("identity") or {})
    me = answers.get("me") or {}
    if me.get("accountId"):
        ident["accountId"] = str(me["accountId"])
    for r in (answers.get("sites") or {}).get("resources") or []:
        products = {str((p or {}).get("id")) for p in r.get("products") or [] if isinstance(p, dict)}
        if "jira" in products and r.get("cloudId") and r.get("url") and not ident.get("cloudId"):
            ident["cloudId"] = str(r["cloudId"])
            ident["site"] = re.sub(r"^https?://", "", str(r["url"])).strip("/")
    return ident


def plan_queries(s: dict, ident: dict, positions: dict, now: float) -> list:
    since = positions.get(TICKETS, now)
    out = [Query(TICKETS, "search", {"cloudId": ident.get("cloudId", ""),
                                     "jql": jql_for(s, ident.get("accountId", ""), since, now),
                                     "fields": SEARCH_FIELDS, "maxResults": PAGE_SIZE, "view": "full"})]
    if not ident.get("name"):
        out.append(Query(NAME_READ, "search", {
            "cloudId": ident.get("cloudId", ""),
            "jql": "assignee = currentUser() OR reporter = currentUser() ORDER BY updated DESC",
            "fields": ["assignee", "reporter"], "maxResults": 1,
            "view": "full"}))   # the compact view's people carry no account id
    return out


def issue_query(key: str, cloud: str, history: bool = True) -> Query:
    args = {"cloudId": cloud, "issueIdOrKey": key, "view": "evidence"}
    if history:
        args["expand"] = "changelog"
    return Query(f"issue:{key}", "issue", args)


def comments_query(key: str, cloud: str, n: int = COMMENTS_READ) -> Query:
    return Query(f"comments:{key}", "comments", {"cloudId": cloud, "issueIdOrKey": key, "orderBy": "-created",
                                                 "maxResults": n})


def detail_queries(tickets: list, cloud: str) -> list:
    out = []
    for t in tickets:
        out += [issue_query(t.key, cloud), comments_query(t.key, cloud)]
    return out


def smaller(q: Query, cloud: str) -> Query | None:
    """The same read asking for less, after its answer was too large; None
    when it cannot ask for less."""
    if q.kind == "issue" and "expand" in q.args:
        return issue_query(q.args["issueIdOrKey"], cloud, history=False)
    if q.kind == "comments" and q.args.get("maxResults", 0) > FEW_COMMENTS:
        return comments_query(q.args["issueIdOrKey"], cloud, FEW_COMMENTS)
    if q.kind == "search" and q.args.get("maxResults", 0) > SMALL_PAGE:
        return Query(q.label, q.kind, dict(q.args, maxResults=SMALL_PAGE))
    return None


def read_less_if_large(reader, queries: list, cloud: str, notes: list) -> tuple[dict, set]:
    """Make the reads; ask again for less where an answer was too large.
    Returns (answers, labels read with less); a read still too large is left
    out with a note."""
    got = reader.read(queries)
    answers = dict(got.answers)
    by_label = {q.label: q for q in queries}
    again = [x for x in (smaller(by_label[label], cloud) for label in sorted(got.too_large)) if x]
    lessened = {q.label for q in again}
    for label in sorted(got.too_large - lessened):
        notes.append(f"the Jira read {label} was too large to read even when asking for less")
    for i in range(0, len(again), 2 * TICKETS_PER_RUN):
        more = reader.read(again[i:i + 2 * TICKETS_PER_RUN])
        answers.update(more.answers)
        for label in sorted(more.too_large):
            notes.append(f"the Jira read {label} was too large to read even when asking for less")
    return answers, lessened


def learn_name(ident: dict, answers: dict) -> dict:
    """The user's name, from a ticket where they are the assignee or reporter."""
    if ident.get("name"):
        return ident
    me = ident.get("accountId")
    for label in (NAME_READ, TICKETS):
        for issue in (answers.get(label) or {}).get("issues") or []:
            f = (issue or {}).get("fields") or {}
            for who in (f.get("assignee"), f.get("reporter")):
                aid, name = _person(who)
                if aid and aid == me and name:
                    return dict(ident, name=name)
    return ident


# ---------- events of one ticket ----------

class Events:
    """Turns one ticket's history and comments into messages."""

    def __init__(self, s: dict, ident: dict, since: float, now: float):
        self.s = s
        self.me = ident.get("accountId", "")
        self.my_name = ident.get("name", "")
        self.site = ident.get("site", "")
        self.since = since
        self.now = now
        self.drop = {x.casefold() for x in s["dropStatuses"]}
        self.dropped: set = set()      # "<ticket>:<history id>" of the moves left out by rule

    def _is_me(self, aid: str, name: str) -> bool:
        if aid:
            return aid == self.me
        return bool(self.my_name) and name == self.my_name

    def link(self, key: str, comment: str = "") -> str:
        base = f"https://{self.site}/browse/{key}" if self.site else ""
        return f"{base}?focusedCommentId={comment}" if base and comment else base

    def message(self, t: Ticket, kind: str, ident: str, at: float, who: tuple, text: str,
                why: str = WHY_TICKET, addressed: bool = False, comment: str = "") -> Message:
        aid, name = who
        return Message(
            key=f"jira:{self.site}:{t.key}:{kind}:{ident}",
            source="jira",
            place=Place(id=t.key, name=f"{t.key} {t.summary}".strip(), kind="ticket"),
            author=Author(id=aid, name=name, me=self._is_me(aid, name)),
            at=float(at),
            text=text,
            link=self.link(t.key, comment),
            thread=Thread(id=t.key, first=kind == CREATED, head=t.summary),
            why_seen=why,
            addressed=addressed,
            reply_to={"site": self.site, "ticket": t.key},
            details=details_of(t, kind),
        )

    def of(self, t: Ticket, issue: dict, comments: dict, known: dict | None, history: bool = True) -> list:
        """history False: the ticket's history could not be read (too large):
        status and assignee changes come from what was kept of it."""
        out = []
        f = (issue or {}).get("fields") or {}
        if t.created is not None and t.created >= self.since:
            desc = plain(f.get("description"))
            to_me = bool(t.assignee[0]) and t.assignee[0] == self.me
            out.append(self.message(t, CREATED, "0", t.created, t.reporter,
                                    f"New ticket: {t.summary}" + (f"\n{desc}" if desc else ""),
                                    WHY_MENTION if to_me or mentions(f.get("description"), self.me) else WHY_TICKET,
                                    to_me and not self._is_me(*t.reporter)))
        if history:
            for h in ((issue or {}).get("changelog") or {}).get("histories") or []:
                out += self._history(t, h)
        else:
            out += self._kept_changes(t, known)
        for c in (comments or {}).get("comments") or []:
            out += self._comment(t, c)
        out += self._pull_requests(t, f, known)
        if not self.s["mine"]:
            out = [m for m in out if not m.author.me]
        return out

    def _history(self, t: Ticket, h: dict) -> list:
        at = ts_of((h or {}).get("created"))
        hid = str((h or {}).get("id") or "")
        if at is None or at < self.since or not hid:
            return []
        who = _person(h.get("author"))
        mine = self._is_me(*who)
        out = []
        for item in h.get("items") or []:
            fname = str((item or {}).get("field") or "").lower()
            was, now = str(item.get("fromString") or ""), str(item.get("toString") or "")
            if fname == "status":
                if now.casefold() in self.drop and not mine:
                    self.dropped.add(f"{t.key}:{hid}")
                    continue
                out.append(self.message(t, STATUS, hid, at, who, f"Status: {was or '(none)'} → {now or '(none)'}"))
            elif fname == "assignee":
                to_me = item.get("to") == self.me if item.get("to") else bool(self.my_name) and now == self.my_name
                from_me = (item.get("from") == self.me if item.get("from")
                           else bool(self.my_name) and was == self.my_name)
                if to_me and not from_me:
                    text = f"Assigned to you (was {was})" if was else "Assigned to you"
                    out.append(self.message(t, ASSIGNED, hid, at, who, text, WHY_MENTION, not mine))
                elif from_me and not to_me:
                    text = f"Assigned to {now} (was you)" if now else "No longer assigned to you"
                    out.append(self.message(t, ASSIGNED, hid, at, who, text))
        return out

    def _kept_changes(self, t: Ticket, known: dict | None) -> list:
        """Status and assignee changes since the last read, from what was kept
        of the ticket then; who made them is not known."""
        if not known:
            return []
        at = min(max(t.updated_at or self.now, self.since), self.now)
        ident = f"k{int(at)}"
        nobody = ("", "")
        out = []
        was = str(known.get("status") or "")
        if was and t.status and was != t.status:
            if t.status.casefold() in self.drop:
                self.dropped.add(f"{t.key}:{ident}")
            else:
                out.append(self.message(t, STATUS, ident, at, nobody, f"Status: {was} → {t.status}"))
        if "assignee" in known and str(known.get("assignee") or "") != t.assignee[0]:
            before, now = str(known.get("assignee") or ""), t.assignee[0]
            if now and now == self.me:
                out.append(self.message(t, ASSIGNED, ident, at, nobody, "Assigned to you", WHY_MENTION, True))
            elif before and before == self.me:
                text = f"Assigned to {t.assignee[1]} (was you)" if t.assignee[1] else "No longer assigned to you"
                out.append(self.message(t, ASSIGNED, ident, at, nobody, text))
        return out

    def _comment(self, t: Ticket, c: dict) -> list:
        at = ts_of((c or {}).get("created"))
        cid = str((c or {}).get("id") or "")
        if at is None or at < self.since or not cid:
            return []
        who = _person(c.get("author"))
        body = c.get("body") or ""
        named = mentions(body, self.me)
        return [self.message(t, COMMENT, cid, at, who, plain(body) or "(an empty comment)",
                             WHY_MENTION if named else WHY_TICKET, named and not self._is_me(*who), comment=cid)]

    def _pull_requests(self, t: Ticket, f: dict, known: dict | None) -> list:
        pr = pull_requests(f)
        if known is not None:
            before = int(known.get("prCount") or 0)
        else:
            new = t.created is not None and t.created >= self.since
            before = 0 if new else pr["count"]
        if pr["count"] <= before:
            return []
        at = max(pr["at"] or self.now, self.since)
        state = pr["state"].lower().replace("_", " ")
        n = pr["count"]
        text = f"Pull request linked ({n} in all" + (f", {state}" if state else "") + ")"
        return [self.message(t, PULL_REQUEST, str(n), min(at, self.now), ("", "Jira"), text)]


# ---------- the source ----------

class JiraSource(Source):
    kind = "jira"
    label = "Jira"
    can_send = False

    def __init__(self, claude_path: str = "", cwd: str = "", reader_factory=None, memory: dict | None = None):
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
                  choices=(("connector", "Claude's Atlassian connection"),),
                  help="Jira is read through the Atlassian connection of your Claude login."),
            Field("assigned", "Tickets assigned to me", "switch", True),
            Field("reported", "Tickets I reported", "switch", True),
            Field("watched", "Tickets I watch", "switch", True),
            Field("mentioned", "Tickets that mention me", "switch", True,
                  help="In a comment or the description."),
            Field("mine", "My own changes too", "switch", True,
                  help="Your own comments and status moves are handed on too, marked as yours."),
            Field("dropStatuses", "Statuses left out", "list", list(DEFAULTS["dropStatuses"]),
                  help="A move to one of these statuses by someone else is not handed on and costs no "
                       "model call. The health line counts them."),
            Field("accountId", "Your Atlassian account", "found", "", help="Found by the first check."),
            Field("name", "Your Jira name", "found", ""),
            Field("site", "Jira site", "found", "", help="Found by the first check, for links."),
        ]

    def route_fields(self) -> list[Field]:
        return [
            Field("epics", "Jira epics", "list", [],
                  help="Epic keys (ABC-10). A ticket in one of these epics goes to the initiative."),
            Field("components", "Jira components", "list", [],
                  help="Component names. Checked after epics."),
            Field("projects", "Jira projects", "list", [],
                  help="Project keys (ABC). Checked last. They only route: tickets you are not on are not read."),
        ]

    def place_values(self, m: Message) -> dict:
        d = m.details or {}
        return {"epics": [d["epic"]] if d.get("epic") else [],
                "components": list(d.get("components") or []),
                "projects": [x for x in (d.get("project"), d.get("projectName")) if x]}

    def work_items(self, m: Message) -> tuple[list, list]:
        """(the ticket itself, its epic): a row listing the ticket takes the
        message; its epic counts only when no row lists the ticket. Its pull
        requests count when the text links them (the router reads the text);
        Jira gives only their number, not which they are."""
        d = m.details or {}
        ticket, epic = str(d.get("ticket") or ""), str(d.get("epic") or "")
        own = work_items.find(ticket, (), [work_items.prefix_of(ticket)]) if ticket else []
        around = (work_items.find(epic, (), [work_items.prefix_of(epic)])
                  if epic and epic != ticket else [])
        return own, around

    def problem(self, settings: dict) -> str:
        s = settings_of(settings)
        if s["reader"] != "connector":
            return "Only Claude's Atlassian connection can read Jira for now: choose it."
        if not (s["assigned"] or s["reported"] or s["watched"] or s["mentioned"]):
            return "Nothing is chosen to watch: turn on at least one kind of ticket."
        if self._factory is None and not (os.path.exists(find_claude(self.claude_path))
                                          or shutil.which("claude")):
            return "Claude Code is not installed, so Claude's Atlassian connection cannot be used."
        return ""

    def plan(self, watch: Watch, positions: dict[str, float]) -> list[str]:
        memory = watch.memory or {}
        ident = memory.get("identity") or {}
        names = [q.label for q in identity_queries(memory)]
        return names + [q.label for q in plan_queries(settings_of(watch.settings), ident, positions or {}, 0.0)]

    # -- reading ---------------------------------------------------------------
    def check(self, watch: Watch, positions: dict[str, float], now: float) -> Check:
        settings = settings_of(watch.settings)
        self.settings = settings
        why_not = self.problem(settings)
        if why_not:
            return JiraCheck(outcome=NOT_SET_UP, reason=why_not, code="not_set_up")
        memory = dict(watch.memory or {})
        out = JiraCheck()
        try:
            reader = self._reader(settings, memory)
        except ReadError as e:
            return self._failed(e, out)
        try:
            return self._check(settings, memory, positions or {}, now, reader, out)
        except ReadError as e:
            return self._failed(e, out)
        finally:
            out.usage = add_usage(out.usage, getattr(reader, "usage", {}))
            out.runs = getattr(reader, "runs", 0)

    def _failed(self, e: ReadError, out: JiraCheck) -> JiraCheck:
        out.outcome = NOT_SET_UP if e.code == "not_set_up" else FAILED
        out.reason = f"Jira could not be read: {e}"
        out.code = e.code
        out.messages, out.positions, out.full, out.memory = [], {}, set(), None
        return out

    def _check(self, settings, memory, positions, now, reader, out) -> JiraCheck:
        # 1. Who the user is and which site, on the first check.
        wanted = identity_queries(memory)
        if wanted:
            memory["identity"] = learn_identity(memory, reader.read(wanted).answers)
        ident = memory.get("identity") or {}
        if not ident.get("accountId"):
            raise ReadError("your Atlassian account gave no account id", "unreadable")
        if not ident.get("cloudId"):
            raise ReadError("your Atlassian connection has no Jira site", "not_set_up")

        # 2. The tickets that changed.
        # Events count from the frame's position. During a catch-up (the last
        # check was full) the position stays at the catch-up's start, and the
        # search goes on from where the last page ended (kept in memory): a
        # ticket left for a later page may have events older than that page's
        # last ticket, and they must still be handed on.
        # (The frame's positions only move forward, so the position it hands
        # back may be a little after the catch-up's start: the overlap.)
        since = float(positions.get(TICKETS, now))
        # The page counts while the frame's start is between the window and
        # the page's end, or past that end by no more than the frame's own
        # overlap (the page lay within the overlap and the frame hands back
        # its older position). A start past both (the frame cut a gap to its
        # limit, e.g. the machine slept during the catch-up) ends the page:
        # events from before that limit are not handed on.
        page = memory.get("page") if isinstance(memory.get("page"), dict) else {}
        search_from = since
        window, page_from = float(page.get("window") or 0), float(page.get("from") or 0)
        if window and page_from and window - 1 <= since and (since < page_from or since <= window + PAGE_OVERLAP_S):
            since, search_from = window, min(page_from, now)
        queries = plan_queries(settings, ident, {TICKETS: search_from}, now)
        got, lessened = read_less_if_large(reader, queries, ident["cloudId"], out.notes)
        if TICKETS not in got:
            raise ReadError("the search for changed tickets was too large to read even 8 at a time", "too_large")
        ident = learn_name(ident, got)
        memory["identity"] = ident
        issues = (got.get(TICKETS) or {}).get("issues") or []
        found = [t for t in (ticket_of(i) for i in issues) if t]
        found.sort(key=lambda t: t.updated_at or 0.0)
        # Counted before any issue is left out as unreadable.
        page_full = len(issues) >= (SMALL_PAGE if TICKETS in lessened else PAGE_SIZE)

        # 3. Their history and comments, for those not read at this same change.
        tickets = dict(memory.get("tickets") or {})
        todo = [t for t in found if (tickets.get(t.key) or {}).get("updated") != t.updated]
        cut = len(todo) > MAX_DETAILED
        todo = todo[:MAX_DETAILED]
        detail, no_history = {}, set()
        for i in range(0, len(todo), TICKETS_PER_RUN):
            batch = todo[i:i + TICKETS_PER_RUN]
            got, lessened = read_less_if_large(reader, detail_queries(batch, ident["cloudId"]), ident["cloudId"],
                                               out.notes)
            detail.update(got)
            no_history |= {label.split(":", 1)[1] for label in lessened
                           if label.startswith("issue:") and label in got}
        if no_history:
            out.notes.append(f"history too large to read for {', '.join(sorted(no_history))}: status and "
                             "assignee changes there were taken from the ticket, without who made them")

        # 4. The events, and what to keep.
        ev = Events(settings, ident, since, now)
        messages, more_comments = [], []
        for t in todo:
            known = tickets.get(t.key)
            issue = detail.get(f"issue:{t.key}") or {}
            if f"issue:{t.key}" not in detail:
                # Not read at all (too large even without its history): kept as
                # it was, so it is read again at its next change.
                continue
            comments = detail.get(f"comments:{t.key}") or {}
            messages += ev.of(t, issue, comments, known, t.key not in no_history)
            if comments_may_be_missing(comments, since):
                more_comments.append(t.key)
            ident = learn_name_from_history(ident, issue)
            state = ticket_state(t)
            kept_state = (known or {}).get("state")
            # A finished state is always reported; "open" only when the ticket
            # was finished before (reopened). A ticket that merely moves along
            # stays quiet, so the work items' 30-day quiet rule still works.
            if kept_state != state and (state != "open" or kept_state in FINISHED_STATES):
                out.item_states.append((t.key, state, status_since(t, issue) or t.updated_at or now))
            tickets[t.key] = {"updated": t.updated, "prCount": pull_requests(issue.get("fields") or {})["count"],
                              "status": t.status, "state": state, "assignee": t.assignee[0],
                              "at": t.updated_at or now}
        out.messages = sorted(messages, key=lambda m: m.at)
        if more_comments:
            out.notes.append(f"only the newest comments are read, and all of them were new on "
                             f"{', '.join(sorted(more_comments))}: older new comments there may be missing")

        next_page = None
        if cut or page_full:
            rest = todo if cut else found
            at = (rest[-1].updated_at if rest else None) or now
            if at < search_from + 60:
                # More than a page of tickets changed within one minute (a bulk
                # change): searches go by the minute, so the rest of that minute
                # is skipped rather than read again for ever.
                at = min(search_from + 60, now)
                out.notes.append("more tickets changed within one minute than one search returns; "
                                 "some of them were not read")
            next_page = {"window": since, "from": float(int(at))}
            out.positions = {TICKETS: since}
            out.full = {TICKETS}
        else:
            out.positions = {TICKETS: float(now)}

        # The moves left out by rule today, each once (a ticket read again
        # within the overlap shows the same move again).
        dropped = dict(memory.get("dropped") or {})
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        keys = [str(k) for k in dropped.get("keys") or []] if dropped.get("day") == today else []
        keys += sorted(ev.dropped - set(keys))
        dropped = {"day": today, "keys": keys[-DROPPED_KEEP:]}
        if keys:
            out.notes.append(f"{len(keys)} status moves to {', '.join(settings['dropStatuses'])} "
                             "left out by rule today")

        kept = dict(memory)
        kept["identity"] = ident
        kept["tickets"] = _trim(tickets, now)
        kept["dropped"] = dropped
        kept.pop("page", None)
        if next_page:
            kept["page"] = next_page
        prefix = getattr(reader, "prefix", "")
        if prefix:
            kept["toolPrefix"] = prefix
        out.memory = kept
        out.outcome = OK
        self.memory = kept
        return out

    def remember(self, settings: dict, memory: dict) -> None:
        self.settings = settings_of(settings)
        self.memory = dict(memory or {})

    def found(self, memory: dict | None = None) -> dict:
        ident = ((memory if memory is not None else self.memory) or {}).get("identity") or {}
        return {k: ident.get(k, "") for k in ("accountId", "name", "site")}

    def thread(self, ref: str) -> list[Message]:
        """The ticket ``ref`` ("ABC-12"): its start and its last comments, oldest first."""
        key = (ref or "").strip()
        if not _KEY.fullmatch(key):
            raise ValueError(f"not a Jira ticket: {ref!r}")
        ident = (self.memory.get("identity") or {})
        if not ident.get("cloudId"):
            raise ValueError("Jira has not been read yet: its site is not known")
        reader = self._reader(self.settings, self.memory)
        cq = comments_query(key, ident["cloudId"])
        got, _ = read_less_if_large(reader, [
            Query("ticket", "search", {"cloudId": ident["cloudId"], "jql": f"key = {key}",
                                       "fields": SEARCH_FIELDS + ["description"], "maxResults": 1,
                                       "view": "full"}),
            cq], ident["cloudId"], [])
        got["comments"] = got.get(cq.label) or {}
        issues = (got.get("ticket") or {}).get("issues") or []
        t = ticket_of(issues[0]) if issues else None
        if t is None:
            raise ValueError(f"Jira has no ticket {key} that you can see")
        ev = Events(settings_of(self.settings), ident, 0.0, time.time())
        ev.s = dict(ev.s, mine=True)
        out = [m for m in ev.of(t, {"fields": issues[0].get("fields") or {}}, got.get("comments") or {}, {})
               if m.details.get("event") in (CREATED, COMMENT)]
        return sorted(out, key=lambda m: m.at)

    def send(self, reply_to: dict, text: str):
        raise NotSupported("Jira comments are not written in this round")


def learn_name_from_history(ident: dict, issue: dict) -> dict:
    if ident.get("name"):
        return ident
    for h in ((issue or {}).get("changelog") or {}).get("histories") or []:
        aid, name = _person((h or {}).get("author"))
        if aid and aid == ident.get("accountId") and name:
            return dict(ident, name=name)
    return ident


def _trim(tickets: dict, now: float) -> dict:
    keep = {k: v for k, v in tickets.items()
            if isinstance(v, dict) and now - float(v.get("at") or now) <= TICKETS_KEEP_S}
    if len(keep) > TICKETS_MAX:
        newest = sorted(keep, key=lambda k: float(keep[k].get("at") or 0), reverse=True)[:TICKETS_MAX]
        keep = {k: keep[k] for k in newest}
    return keep
