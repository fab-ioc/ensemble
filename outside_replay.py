"""Replay saved Slack messages through the full routing and compare each
decision with a dispatch log (design #1, section 10.2).

A command-line tool, run locally and never in the tests' real form: it reads
a Slack watcher's notices file (``inbox.md``: the messages as found, one
notice per check) and a PO's dispatch log (one line per message sent to an
initiative, raised as a card, or not sent), and writes a report.

1. Each message in the notices file becomes a ``Message`` again.
2. The notices are routed in order, one notice as one batch, through the
   hub's own routing: the channel table and thread memory
   (``outside_route``), then one Haiku call per batch for the rest
   (``outside_model``), with thread memory and each conversation's earlier
   messages (for the model) built up as it goes. The routing
   table is a JSON file the caller writes (it holds real channels and
   topics, so it is kept outside the repository). Everything is written to a
   scratch folder, never to a hub's own data. Haiku's answers are kept in
   that folder too, so a second run with the same table asks nothing again.
3. Each message is matched with the dispatch log's line for it, by day,
   minute (a line may cover a range of minutes), author's first name and
   place.
4. The report (Markdown) gives how many messages were routed the same and
   lists every difference with both decisions and the reason for each.

**What counts as the same.** The log's first initiative and the hub's
initiative are the same initiative (the log's names go through the table's
``aliases`` first, for initiatives renamed since). A message the log did not
send to any initiative (not sent, a card to the CEO, or no line at all) and
that the hub drops (small talk or no match) is the same too: neither
delivers it. A hub initiative that is the log's second one counts as partly
the same and is listed.

The table file::

    {"initiatives": [{"name": "Alpha", "topic": "a few words",
                      "channels": ["#a-channel"]}],
     "aliases": {"Old name": "Alpha"}}

Run::

    python3 outside_replay.py --inbox inbox.md --log "dispatch log.md" \
        --table table.json --work scratch/ --out "report.md" [--from "2026-11-10 17:20"]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path

import outside
import outside_model as model
import outside_route as route
from sources.base import (WHY_CHANNEL, WHY_DM, WHY_GROUP, WHY_MENTION, WHY_THREAD, Author,
                          Message, Place, Thread)

SOURCE_ID = "replay"
CEO = {"kind": "ceo"}
TRIES = 3               # a batch whose answer failed twice is asked again, this many rounds in all

# What the log decided, besides an initiative.
NOT_SENT = "not sent"
CARD = "card to the CEO"
NOT_LOGGED = "no line in the log"
UNCLEAR = "unclear"
# What the hub decided, besides an initiative and the model's two drops.
FAILED = "no answer"


# ---------------------------------------------------------------------------
# The notices file
# ---------------------------------------------------------------------------

_NOTICE = re.compile(r"^## (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
_HEAD = re.compile(r"^(\d+)\. \*\*(.+?)\*\* \(`([A-Z0-9]+)`\) · (.+?) \(`([A-Z0-9]+)`\) · "
                   r"(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d \w+\s*$")
_IN_THREAD = re.compile(r"in a thread \(thread `([0-9.]+)`\), message `([0-9.]+)`")
_STARTS = re.compile(r"starts a thread with \d+ repl\w*, message `([0-9.]+)`")
_ALONE = re.compile(r"^message `([0-9.]+)`")
_FIRST = re.compile(r"^first message of the thread \(([^)]*)\): (.*)$")
_LINK = re.compile(r"^https?://([A-Za-z0-9-]+)\.slack\.com/\S+$")


@dataclass
class Seen:
    """One message of the notices file, with what matching needs."""
    message: Message
    day: str                    # "2026-11-12"
    minute: int                 # minutes since midnight, as the notice shows the time
    hhmm: str
    place: str                  # as the notice names it: "DM", "Group DM", "#name"


def parse_inbox(text: str) -> list[list[Seen]]:
    """The notices file as batches, one per notice, each its messages in
    order. A notice with no message in the expected form is left out."""
    batches: list[list[Seen]] = []
    cur: list[Seen] | None = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _NOTICE.match(line):
            cur = []
            batches.append(cur)
            i += 1
            continue
        h = _HEAD.match(line)
        if not h or cur is None:
            i += 1
            continue
        place, cid, author, uid, day, hhmm = (h.group(2), h.group(3), h.group(4), h.group(5),
                                              h.group(6), h.group(7))
        body, quote = [], []
        i += 1
        while i < len(lines) and lines[i].startswith("   "):
            s = lines[i][3:]
            if s.startswith(">"):
                quote.append(s[1:].lstrip(" ") if s != ">" else "")
            else:
                body.append(s.strip())
            i += 1
        ts, thread, head, ws, why = "", None, "", "example", ""
        for b in body:
            if m := _IN_THREAD.search(b):
                ts = m.group(2)
                thread = Thread(id=f"{cid}:{m.group(1)}", first=False)
            elif m := _STARTS.search(b):
                ts = m.group(1)
                thread = Thread(id=f"{cid}:{ts}", first=True)
            elif m := _ALONE.match(b):
                ts = m.group(1)
            elif m := _FIRST.match(b):
                head = m.group(2)
            elif m := _LINK.match(b):
                ws = m.group(1)
            elif b.startswith("first message of the thread:"):
                head = ""
            elif re.match(r"^(?:\S+ )?in thread:", b):
                why = b
        if not ts:
            continue
        if thread is not None and head:
            thread.head = head
        kind = ("dm" if place == "DM" else "group dm" if place.lower() == "group dm"
                else "channel")
        why_seen = (WHY_DM if kind in ("dm", "group dm") else
                    WHY_MENTION if "mentioned in this message" in why else
                    WHY_GROUP if "group" in why else
                    WHY_THREAD if "**yes**" in why else WHY_CHANNEL)
        msg = Message(key=f"slack:{ws}:{cid}:{ts}", source="slack",
                      place=Place(id=cid, name=place.lstrip("#") if kind == "channel" else place,
                                  kind=kind),
                      author=Author(id=uid, name=author), at=float(ts), text="\n".join(quote),
                      link="", thread=thread, why_seen=why_seen)
        hh, mm = hhmm.split(":")
        cur.append(Seen(message=msg, day=day, minute=int(hh) * 60 + int(mm), hhmm=hhmm,
                        place=place))
    return [b for b in batches if b]


# ---------------------------------------------------------------------------
# The dispatch log
# ---------------------------------------------------------------------------

_DAY = re.compile(r"^## (\d{4}-\d\d-\d\d)\s*$")
_LINE = re.compile(r"^- (\d\d):(\d\d)(?:-(\d\d):(\d\d))? · (?:\(re (\d\d):(\d\d)\) )?(.*)$")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_SKIP_AUTHORS = ("correction", "re-routed", "(card closed)")


@dataclass
class Logged:
    """One line of the dispatch log."""
    day: str
    start: int                  # minutes since midnight
    end: int
    authors: list[str]          # first names, lower case
    kind: str                   # "dm", "group dm", "channel" or "" (unknown)
    channels: list[str]         # for "channel": the names it gives, lower case, no "#"
    outcome: str                # "routed", NOT_SENT, CARD or UNCLEAR
    to: list[str] = field(default_factory=list)
    text: str = ""              # the line, for the report
    later: bool = False         # "(re 12:33)": a later decision about an earlier message; it wins


def _first_name(name: str) -> str:
    words = re.sub(r"^\(re [^)]*\)\s*", "", name.strip()).split()
    return words[0].casefold() if words else ""


def parse_log(text: str, aliases: dict[str, str] | None = None) -> list[Logged]:
    """The dispatch log's message lines. Corrections and notes that are not
    about one message are left out."""
    aliases = {k.casefold(): v for k, v in (aliases or {}).items()}
    out: list[Logged] = []
    day = ""
    for line in text.splitlines():
        if m := _DAY.match(line):
            day = m.group(1)
            continue
        m = _LINE.match(line)
        if not m or not day:
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = int(m.group(3)) * 60 + int(m.group(4)) if m.group(3) else start
        later = bool(m.group(5))
        if later:
            start = end = int(m.group(5)) * 60 + int(m.group(6))
        rest = m.group(7)
        head, arrow, tail = rest.partition("→")
        if not arrow:
            continue
        who, _, where = head.partition(",")
        if who.strip().casefold().startswith(_SKIP_AUTHORS):
            continue
        authors = [_first_name(a) for a in who.split("/") if a.strip()]
        where = where.strip()
        low = re.split(r"[:,]", where, 1)[0].casefold()
        if "group dm" in low:
            kind, chans = "group dm", []
        elif low.startswith("dm"):
            kind, chans = "dm", []
        elif low.startswith("#"):
            kind = "channel"
            first = re.split(r"[:,(]", where, 1)[0]
            chans = [c.casefold().rstrip("…") for c in re.findall(r"#([\w…-]+)", first)]
        else:
            kind, chans = "", []
        t = tail.strip()
        low_t = t.casefold()
        names = [n.strip() for n in _BOLD.findall(t)
                 if "card" not in n.casefold() and "missed" not in n.casefold()]
        if ("card" in low_t[:80] or low_t.startswith("no initiative")
                or (not names and "ask card" in low_t)):
            outcome, to = CARD, []
        elif names:
            outcome, to = "routed", []
            for n in names:
                n = aliases.get(n.casefold(), n)
                if n not in to:
                    to.append(n)
        elif "not sent" in low_t or low_t.startswith("covered by") or "already handled" in low_t:
            outcome, to = NOT_SENT, []
        else:
            outcome, to = UNCLEAR, []
        out.append(Logged(day=day, start=start, end=end, authors=authors, kind=kind,
                          channels=chans, outcome=outcome, to=to, text=line[2:].strip(),
                          later=later))
    return out


def _place_fits(s: Seen, lg: Logged) -> bool:
    if not lg.kind:
        return True
    if lg.kind != s.message.place.kind:
        return False
    if lg.kind == "channel" and lg.channels:
        name = s.message.place.name.casefold()
        return any(name == c or name.startswith(c) for c in lg.channels)
    return True


def match(seen: list[Seen], logged: list[Logged], slack_min: int = 2) -> dict[str, Logged]:
    """{message key: its log line}. A line fits a message on the same day,
    by the same author (first name), in the same kind of place (and channel,
    when the line names one), within the line's minutes or ``slack_min``
    minutes of them; the closest line wins."""
    out: dict[str, Logged] = {}
    used: dict[int, int] = {}
    for s in seen:
        author = _first_name(s.message.author.name)
        best, best_d = None, (slack_min + 1, False, 0)
        for lg in logged:
            if lg.day != s.day or author not in lg.authors or not _place_fits(s, lg):
                continue
            d = 0 if lg.start <= s.minute <= lg.end else min(abs(s.minute - lg.start),
                                                               abs(s.minute - lg.end))
            # Two lines as close: the one not used yet (two lines at one minute
            # are two messages written in that minute).
            score = (d, not lg.later, used.get(id(lg), 0))
            if score < best_d:
                best, best_d = lg, score
        if best is not None:
            out[s.message.key] = best
            used[id(best)] = used.get(id(best), 0) + 1
    return out


# ---------------------------------------------------------------------------
# The routing run
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    return "i-" + (re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "x")


def load_table(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [r for r in d.get("initiatives") or [] if isinstance(r, dict) and r.get("name")]
    return {"initiatives": rows, "aliases": dict(d.get("aliases") or {})}


def _a_list(answer: str) -> bool:
    """The answer holds a JSON list of objects (whatever it says)."""
    text = (answer or "").strip()
    start, end = text.find("["), text.rfind("]")
    try:
        got = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return False
    return isinstance(got, list) and all(isinstance(x, dict) for x in got)


class Cache:
    """Haiku's answers by question, kept in a file: a second run of the
    same replay asks nothing again."""

    def __init__(self, path: Path, run):
        self.path, self.run = path, run
        try:
            self.answers = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.answers = {}
        self.asked = self.reused = 0

    def __call__(self, prompt: str, timeout: float) -> str:
        k = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if k in self.answers:
            self.reused += 1
            return self.answers[k]
        self.asked += 1
        answer = self.run(prompt, timeout)
        if _a_list(answer):                             # keep only answers that are a list
            self.answers[k] = answer
            self.path.write_text(json.dumps(self.answers, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
        return answer


def replay(batches: list[list[Seen]], table: dict, work: Path, run=None,
           say=print, items: dict[str, list[str]] | None = None) -> dict[str, dict]:
    """Route every batch in order through the routing tables and the model,
    in the scratch folder ``work``. Returns {message key: decision}, each
    ``{to: initiative name or "", dropped, how, why}``; ``dropped`` is small
    talk, no match or FAILED (the model gave no answer in TRIES rounds)."""
    work.mkdir(parents=True, exist_ok=True)
    state = work / "hub"
    if state.exists():
        shutil.rmtree(state)            # each run starts with empty thread memory
    state.mkdir()
    names = {_slug(r["name"]): r["name"] for r in table["initiatives"]}
    hub = types.SimpleNamespace(DASHBOARD_DIR=state,
                                load_projects=lambda: [{"id": k, "name": v} for k, v in names.items()],
                                _claude_bin=lambda: shutil.which("claude"))
    outside.bind(hub)
    route.bind(hub)
    model.bind(hub)
    outside.set_source(SOURCE_ID, type="slack", mode="decide", settings={})
    for r in table["initiatives"]:
        route.set_row(SOURCE_ID, _slug(r["name"]),
                      {"channels": list(r.get("channels") or []), "topic": r.get("topic") or ""},
                      CEO)
    # Work items the rows start with ({initiative name: [items]}); the rows
    # also fill themselves from the messages routed, as on the hub.
    for name, keys in (items or {}).items():
        if _slug(name) not in names:
            say(f"items: no initiative {name!r} in the table, left out")
            continue
        for key in keys:
            try:
                route.set_initiative(_slug(name), {"addItems": [key]}, CEO, now=0.0)
            except route.Refused as e:
                say(f"items: {key} not added to {name}: {e}")
    cache = Cache(work / "answers.json", run or model.run_claude)
    model.RUN = cache
    out: dict[str, dict] = {}

    def keep(e, d):
        out[e["key"]] = {"to": names.get(d.get("to") or "", ""), "dropped": d.get("dropped", ""),
                         "how": d.get("how", ""), "why": d.get("why", "")}

    for n, batch in enumerate(batches, 1):
        entries = [{"key": s.message.key, "message": s.message.to_dict(), "readAt": s.message.at,
                    "decideOnly": True} for s in batch]
        now = max(s.message.at for s in batch) + 30
        route.learn_channels(SOURCE_ID, entries, now)
        decided = route.decide(SOURCE_ID, entries)
        fixed = [(e, d) for e, d in decided if d.get("how") != route.NEEDS_MODEL]
        route.record(SOURCE_ID, fixed, now)
        for e, d in fixed:
            keep(e, d)
        need = [e for e, d in decided if d.get("how") == route.NEEDS_MODEL]
        for i in range(0, len(need), model.BATCH_MAX):
            chunk = need[i:i + model.BATCH_MAX]
            msgs = [Message.from_dict(e["message"]) for e in chunk]
            decisions = None
            for _round in range(TRIES):
                decisions, how = model.ask(msgs, route.initiatives(), model.topics(SOURCE_ID),
                                           earlier=route.recent(SOURCE_ID, msgs))
                if decisions is not None:
                    break
            if decisions is None:
                for e in chunk:
                    out[e["key"]] = {"to": "", "dropped": FAILED, "how": route.BY_MODEL,
                                     "why": f"the model's answer was {how} every time"}
                continue
            route.record(SOURCE_ID, list(zip(chunk, decisions)), now)
            for e, d in zip(chunk, decisions):
                keep(e, d)
        if n % 20 == 0:
            say(f"{n} of {len(batches)} notices routed ({cache.asked} asked, {cache.reused} reused)")
    say(f"done: {cache.asked} Haiku calls made, {cache.reused} answers reused")
    return out


# ---------------------------------------------------------------------------
# The comparison and the report
# ---------------------------------------------------------------------------

def compare(seen: list[Seen], logged: list[Logged], hub: dict[str, dict]) -> list[dict]:
    """One row per message: the log's decision, the hub's, and whether they
    agree: "same", "partly" (the hub chose the log's second initiative),
    "differ", or "unclear" (the log's line could not be read)."""
    lines = match(seen, logged)
    rows = []
    for s in seen:
        k = s.message.key
        lg = lines.get(k)
        log_out, log_to = (lg.outcome, list(lg.to)) if lg else (NOT_LOGGED, [])
        h = hub.get(k) or {"to": "", "dropped": FAILED, "how": "", "why": "not routed"}
        if log_out == UNCLEAR:
            verdict = "unclear"
        elif h["to"]:
            verdict = ("same" if log_to and h["to"] == log_to[0] else
                       "partly" if h["to"] in log_to else "differ")
        elif h["dropped"] in (route.SMALL_TALK, route.NO_MATCH):
            verdict = "same" if log_out != "routed" else "differ"
        else:
            verdict = "differ"
        rows.append({"seen": s, "log": lg, "logOutcome": log_out, "logTo": log_to, "hub": h,
                     "verdict": verdict})
    return rows


_REF = re.compile(r"(?<![\w-])(?:[A-Za-z][\w.-]*[A-Za-z] \d{4,}|[A-Z][A-Z0-9]+-\d+|"
                  r"[\w.-]+\.[a-z]{2,} link|Slack message)(?![\w-])")
BARE_WORDS = 3          # "mostly": at most this many other words


def bare_ref(text: str) -> bool:
    """The message is only, or mostly, a pull request, a ticket key or a
    link: at least one of them (as ``outside_route.short_links`` writes
    them: "example-repo 1234", "ABC-123", "example.com link") and at most
    BARE_WORDS other words, mentions and emoji not counted."""
    t = route.short_links(text or "")
    if not _REF.search(t):
        return False
    t = _REF.sub(" ", t)
    t = re.sub(r"<[@!#][^>]*>|@[\w.-]+|:[a-z0-9_+-]+:", " ", t)
    words = [w for w in re.split(r"[^\w']+", t) if w]
    return len(words) <= BARE_WORDS


def bare_rows(rows: list[dict]) -> list[dict]:
    """The bare-number messages the log sent to an initiative and the hub
    sends nowhere (small talk or no match)."""
    return [r for r in rows if bare_ref(r["seen"].message.text) and r["logOutcome"] == "routed"
            and not r["hub"]["to"]]


def _hub_says(h: dict) -> str:
    if h["to"]:
        return f"**{h['to']}**"
    return h["dropped"] or "nothing"


def _log_said(r: dict) -> str:
    if r["logOutcome"] == "routed":
        return " + ".join(f"**{t}**" if i == 0 else t for i, t in enumerate(r["logTo"]))
    return r["logOutcome"]


def _how(h: dict) -> str:
    return {"channel": "channel table", "thread": "thread memory", "model": "Haiku",
            "correction": "correction"}.get(h.get("how", ""), h.get("how", "") or "-")


def _cell(text: str, limit: int) -> str:
    text = " ".join(route.short_links(text or "").split())
    text = re.sub(r"<@[A-Z0-9]+\|([^>]*)>", r"@\1", text)
    text = re.sub(r"<[!@#][^>]*>", "@…", text)
    text = text.replace("|", "/")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def report(rows: list[dict], logged: list[Logged], table: dict, title: str = "Replay report",
           notes: list[str] | None = None) -> str:
    """The comparison as Markdown."""
    n = len(rows)
    count = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("same", "partly", "differ", "unclear")}
    pct = (lambda x: f"{100 * x / n:.0f}%") if n else (lambda x: "-")
    hub_kinds: dict[str, int] = {}
    for r in rows:
        k = "routed" if r["hub"]["to"] else r["hub"]["dropped"]
        hub_kinds[k] = hub_kinds.get(k, 0) + 1
    log_kinds: dict[str, int] = {}
    for r in rows:
        log_kinds[r["logOutcome"]] = log_kinds.get(r["logOutcome"], 0) + 1
    how_kinds: dict[str, int] = {}
    for r in rows:
        if r["hub"]["to"] or r["hub"]["dropped"] in (route.SMALL_TALK, route.NO_MATCH):
            k = _how(r["hub"])
            how_kinds[k] = how_kinds.get(k, 0) + 1
    used = {id(r["log"]) for r in rows if r["log"] is not None}
    # A line a later "(re HH:MM)" line overrides is that message's line too.
    later = {(lg.day, lg.start, a) for r in rows if (lg := r["log"]) is not None and lg.later
             for a in lg.authors}
    unmatched = [lg for lg in logged if id(lg) not in used
                 and not any((lg.day, m, a) in later for a in lg.authors
                             for m in range(lg.start, lg.end + 1))]
    out = [f"# {title}", ""]
    out += notes or []
    out += ["", "## The result", "",
            "| | Messages | Share |", "|---|---:|---:|",
            f"| **Routed the same** | **{count['same']}** | **{pct(count['same'])}** |",
            f"| Partly the same (the hub chose the log's second initiative) | {count['partly']} | {pct(count['partly'])} |",
            f"| Different | {count['differ']} | {pct(count['differ'])} |",
            f"| The log's line could not be read | {count['unclear']} | {pct(count['unclear'])} |",
            f"| All messages replayed | {n} | |", "",
            "**What the hub did:** " + ", ".join(f"{v} {k}" for k, v in sorted(hub_kinds.items())) + ".  ",
            "**How it decided:** " + ", ".join(f"{v} by {k}" for k, v in sorted(how_kinds.items())) + ".  ",
            "**What the log says:** " + ", ".join(f"{v} {k}" for k, v in sorted(log_kinds.items())) + ".", ""]
    pairs: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["verdict"] in ("differ", "partly"):
            k = (_log_said(r).replace("**", ""), _hub_says(r["hub"]).replace("**", ""))
            pairs[k] = pairs.get(k, 0) + 1
    if pairs:
        out += ["## The differences by kind", "", "| The log | The hub | Messages |", "|---|---|---:|"]
        out += [f"| {a} | {b} | {c} |" for (a, b), c in sorted(pairs.items(), key=lambda x: -x[1])]
        out.append("")
    for verdict, heading in (("differ", "Every difference"),
                             ("partly", "Partly the same"),
                             ("unclear", "Log lines that could not be read")):
        sel = [r for r in rows if r["verdict"] == verdict]
        if not sel:
            continue
        out += [f"## {heading} ({len(sel)})", "",
                "| # | When | Where, who | The message | The log | The hub | The hub's reason | The log's line |",
                "|---:|---|---|---|---|---|---|---|"]
        for i, r in enumerate(sel, 1):
            s = r["seen"]
            where = s.place if s.place != "Group DM" else "group DM"
            msg_text = s.message.text
            if s.message.thread and s.message.thread.head and not s.message.thread.first:
                msg_text = f"{msg_text} (thread: {s.message.thread.head})"
            how, why = _how(r["hub"]), r["hub"]["why"]
            reason = (why if why.casefold().startswith(how.casefold()) else
                      f"{how}: {why}" if why else how)
            out.append(f"| {i} | {s.day[5:]} {s.hhmm} | {_cell(where, 40)}, {_cell(s.message.author.name, 30)} "
                       f"| {_cell(msg_text, 110)} | {_log_said(r)} | {_hub_says(r['hub'])} "
                       f"| {_cell(reason, 110)} | {_cell(r['log'].text if r['log'] else '', 110) or '-'} |")
        out.append("")
    if unmatched:
        out += [f"## Log lines with no message found ({len(unmatched)})", "",
                "Lines of the dispatch log that no replayed message was matched to (their time, "
                "author or place does not fit any message in the notices file).", ""]
        out += [f"- {lg.day[5:]} · {_cell(lg.text, 160)}" for lg in unmatched]
        out.append("")
    bare = [r for r in rows if bare_ref(r["seen"].message.text)]
    if bare:
        sent = [r for r in bare if r["logOutcome"] == "routed"]
        out += ["## Bare numbers", "",
                f"Messages that are only, or mostly, a pull request number, a ticket key or a "
                f"link (at most {BARE_WORDS} other words): {len(bare)}. The log sent {len(sent)} "
                f"of them to an initiative; of those, the hub sends "
                f"{sum(1 for r in sent if r['verdict'] == 'same')} to the same one, "
                f"{sum(1 for r in sent if r['hub']['to'] and r['verdict'] != 'same')} to another "
                f"one and **{len(bare_rows(rows))} nowhere** (small talk or no match).", ""]
    out += ["## The routing table used", "", "| Initiative | Channels | Topic |", "|---|---|---|"]
    for r in table["initiatives"]:
        out.append(f"| {r['name']} | {', '.join(r.get('channels') or []) or '-'} | {_cell(r.get('topic') or '', 300)} |")
    if table["aliases"]:
        out += ["", "Names in the log read as: " + "; ".join(f"{a} → {b}" for a, b in table["aliases"].items()) + "."]
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inbox", required=True, type=Path, help="the watcher's notices file")
    ap.add_argument("--log", required=True, type=Path, help="the dispatch log")
    ap.add_argument("--table", required=True, type=Path, help="the routing table (JSON)")
    ap.add_argument("--work", required=True, type=Path,
                    help="a scratch folder: the replay's routing state and Haiku's saved answers")
    ap.add_argument("--out", required=True, type=Path, help="the report to write (Markdown)")
    ap.add_argument("--from", dest="since", default="",
                    help='leave out messages before this time, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--until", default="",
                    help='leave out messages from this time on, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--rows", type=Path, default=None,
                    help="also write each message's two decisions here (JSON), for comparing runs")
    ap.add_argument("--items", type=Path, default=None,
                    help='work items the rows start with (JSON: {"rows": {initiative name: ["ABC-1", "repo#2"]}})')
    ap.add_argument("--title", default="Replay report")
    a = ap.parse_args(argv)
    table = load_table(a.table)
    batches = parse_inbox(a.inbox.read_text(encoding="utf-8"))
    def when(text):
        day, hhmm = text.split()
        hh, mm = hhmm.split(":")
        return (day, int(hh) * 60 + int(mm))
    if a.since:
        cut = when(a.since)
        batches = [[s for s in b if (s.day, s.minute) >= cut] for b in batches]
    if a.until:
        cut = when(a.until)
        batches = [[s for s in b if (s.day, s.minute) < cut] for b in batches]
    batches = [b for b in batches if b]
    logged = parse_log(a.log.read_text(encoding="utf-8"), table["aliases"])
    import agent_launch     # starting Claude on purpose, outside a hub
    t0 = time.monotonic()
    with agent_launch.opted_in():
        items = json.loads(a.items.read_text(encoding="utf-8")).get("rows") if a.items else None
        hub = replay(batches, table, a.work, items=items)
    seen = [s for b in batches for s in b]
    rows = compare(seen, logged, hub)
    notes = [f"_Made by `outside_replay.py` on {time.strftime('%Y-%m-%d %H:%M')}, "
             f"{len(batches)} notices, {len(seen)} messages, "
             f"{len(logged)} log lines read, {time.monotonic() - t0:.0f} s._"]
    a.out.write_text(report(rows, logged, table, a.title, notes), encoding="utf-8")
    if a.rows:
        a.rows.write_text(json.dumps([{
            "key": r["seen"].message.key, "verdict": r["verdict"], "logOutcome": r["logOutcome"],
            "logTo": r["logTo"], "hub": r["hub"], "bare": bare_ref(r["seen"].message.text)}
            for r in rows], indent=1, ensure_ascii=False), encoding="utf-8")
    same = sum(1 for r in rows if r["verdict"] == "same")
    print(f"{same} of {len(rows)} routed the same; report: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
