"""Which tasks need a human, and why — computed from evidence that already exists.

The dashboard has always known when an agent died, hit its usage limit, or sat
on a permission prompt; nothing joined those signals up, so the only way to find
out was to open a task and then its agent's terminal. This module is that join.

Four states, deliberately few, each with a plain-language reason. The state
answers *can it continue on its own?*; the reason answers *why not?*:

``agent_gone``
    The terminal died and nobody asked it to. Carries the exit status and the
    last screen — captured at death by :mod:`backends.ptyrun`, because the
    reaper drops the buffer moments later. If the screen says it ran out of
    usage, the reason says so; the state still says "gone", because a dead
    agent needs restarting and a live one does not.
``blocked``
    Still running, but its own output says it cannot get any further: a usage or
    credit limit, an expired login, an authentication failure. The line is quoted.
``waiting_for_you``
    The agent asked a question, is sitting on a permission prompt, reported
    that it finished (``ensemble_report``) or sent something to the user that
    nobody has answered, or the room is waiting on the human (including a
    collaboration paused at its hop limit).
``stalled``
    Alive, was asked to do something — woken by a message, or the owner of a
    team at launch — isn't working, and never answered anyone. A one-agent
    task idle at its prompt has only finished its turn, and is not stalled.

What an agent says, before what its screen looks like
------------------------------------------------------
A hub-launched Claude agent tells the hub, through its hooks, the moment it
takes a prompt, stops to ask, finishes its turn or ends (``agent_hooks.py``).
A Claude session also publishes its own status (``busy`` / ``idle`` /
``shell`` / ``waiting``) in ``~/.claude/sessions/<pid>.json``; ``waiting``
means exactly "blocked on a permission or plan prompt". Both are the agent's
own word, and the newer of the two is what it is doing — unless the screen has
clearly moved on since (``_hook_status`` has the rules). The screen is the
fallback: codex publishes nothing, so for codex the terminal *is* the evidence
and every rule here works on the screen text alone; a Claude agent started
before the hooks existed, or since a hub restart, has said nothing yet. And the
walls a hook never sees — a usage limit, an expired login, the CLI's own error
line — are always read off the screen.

Reading a terminal
------------------

Two things make that screen hostile to naive matching, both observed on live
agents rather than imagined:

* **It is wrapped.** A limit banner arrives split across three lines at whatever
  width the terminal happens to be — and the width changes the moment someone
  opens that terminal in the browser. A pattern anchored to a whole sentence
  simply never matches.
* **Stripping escape sequences loses spaces.** A TUI repaints by moving the
  cursor, not by printing spaces, so ``The attention task`` can come back as
  ``Theattentiontask``.

So every phrase here is compiled with :func:`_phrase`, which lets any amount of
whitespace — including none — sit between its words, and matching runs over the
whole tail rather than line by line.

Cost
----
Polled every few seconds, so it touches only cheap things: room files (cached on
mtime), the live-session json files, and the in-memory PTY registry (each
terminal's screen analysed once per burst of output, not once per poll). It
never reads a transcript, never shells out, and never calls ``load_live()`` or
``load_sessions()`` — the endpoints built on those are already slow and must not
get slower.
"""
from __future__ import annotations

import json
import re
import threading
import time

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

# Worst first — this both breaks ties within a task and orders the tray.
STATES = ("agent_gone", "blocked", "waiting_for_you", "stalled")
_SEVERITY = {s: i for i, s in enumerate(STATES)}

# How long an agent that was asked to do something may stay silent before the
# dashboard says so. A setting, not a magic number — see _SETTINGS_DEFAULTS.
STALL_SECONDS_DEFAULT = 900

# A floor on terminal silence before anything is called stalled, so an agent
# caught mid-turn (status file a second out of date) is never accused.
_MIN_QUIET = 60

# How long after being rung a one-agent task's terminal must still be printing
# to count as having answered there. Typing the doorbell prints at once; a turn
# spent on the ask keeps the screen moving for longer than this.
_ANSWER_AFTER = 5

# How long a death stays newsworthy. Stopping a task clears its record — that is
# the "I've dealt with it" gesture — but a task nobody ever touches would sit in
# the tray forever, and a list that only grows is exactly the uselessness this
# module exists to fix. After a week it is history, not news.
_DEATH_MAX_AGE = 7 * 86400


# ---------------------------------------------------------------------------
# Reading a terminal's last screen
# ---------------------------------------------------------------------------

def _phrase(pattern: str) -> re.Pattern:
    """Compile a phrase that survives a real terminal: every space becomes
    "any amount of whitespace, including none", so the phrase still matches when
    the screen wrapped it across lines or when stripping escape sequences glued
    its words together."""
    return re.compile(pattern.replace(" ", r"\s*"), re.I)


# Only the agent's own wall counts. Both CLIs draw a refusal as a status line
# of their own below the conversation: Claude as a `⎿` line that *starts* with
# the error ("⎿  API Error: 401 · OAuth token has expired…", "⎿  Credit balance
# is too low"), after which the turn ends and the session goes idle; codex as a
# `■` notice. The same words anywhere else are someone quoting them — a review
# finding read through chat_read (a tool result), a `[digest]` or `[report]`
# line the hub typed, a numbered finding, a quoted sentence, the agent's own
# `●` balloon discussing this module — and on 2026-09-14 two healthy rooms
# were shown as "log in again" for an hour because of exactly that. So a match
# counts only below the last `●` turn (a background-task notice is not one),
# only in a block whose first line is the CLI's own error line — never the
# first `⎿` under a tool call, which is that tool's output, whatever it says —
# and never while Claude itself says it is busy (see
# `_own_wall_lines` and `_classify_agent`). Phrases are never exempted for this:
# "run /login to renew" is a real wall when Claude prints it.
#
# "I cannot continue" — capacity and credentials. Each rule is
# (pattern, plain-language why, machine-readable cause). Kept deliberately
# specific: a bare "rate limit" is NOT here, because both CLIs retry those
# transparently and a healthy agent prints them all the time.
#
# Order matters: the first rule that matches a spot wins, so the specific
# credential causes come before the generic "run /login" hint — a real line
# often says both, and "invalid API key" is the useful half.
_BLOCK_RULES: list[tuple[re.Pattern, str, str]] = [
    (_phrase(r"you'?ve hit your usage limit"), "hit its usage limit", "usage_limit"),
    (_phrase(r"usage limit reached"), "hit its usage limit", "usage_limit"),
    (_phrase(r"\b\d+-hour limit reached"), "hit its usage limit", "usage_limit"),
    (_phrase(r"you have exceeded your (?:usage|monthly) limit"), "hit its usage limit", "usage_limit"),
    (_phrase(r"(?:credit balance|balance) is too low"), "is out of credit", "credit"),
    (_phrase(r"insufficient (?:credit|credits|quota|balance|funds)"), "is out of credit", "credit"),
    (_phrase(r"quota (?:exceeded|exhausted)"), "is out of quota", "credit"),
    (_phrase(r"out of (?:credits|tokens)\b"), "is out of credit", "credit"),
    (_phrase(r"purchase more credits"), "is out of credit", "credit"),
    (_phrase(r"invalid api key"), "has a bad API key", "auth"),
    (_phrase(r"(?:oauth )?token (?:has )?expired"), "needs you to log in again", "auth"),
    (_phrase(r"session expired"), "needs you to log in again", "auth"),
    (_phrase(r"authentication (?:failed|error)"), "failed to authenticate", "auth"),
    (_phrase(r"\bnot logged in\b"), "needs you to log in again", "auth"),
    (_phrase(r"(?:please )?run /login"), "needs you to log in again", "auth"),
]

# Real near-misses that must NOT fire. Codex prints these on a perfectly healthy
# session — they are an offer, not a refusal — and they contain the words "usage
# limit". Nothing broader belongs here: "your limit will reset at 3pm" is the
# tail of Claude's *real* limit message, so exempting that phrase would hide the
# very thing this module exists to show.
#
# Claude's notice that a login is about to expire ("Your login expires in 3
# days · run /login to renew") is advice, not a wall: the agent keeps working,
# and read as "needs you to log in again" it hid a finished task's report.
_BLOCK_EXEMPT = _phrase(r"usage limit reset available|/usage to use one")
# Only the words of that notice itself are exempt, never a match next to it:
# "OAuth token has expired · run /login to renew" ends with the same advice
# and is a real wall.
_LOGIN_NOTICE = _phrase(r"your login expires in \d+ \w+(?: [·•|\-] (?:please )?run ?/login to renew)?")

# An agent editing THIS file puts the patterns above on its own screen. Only
# *structural* evidence counts — regex source, a diff line — and only on the
# same screen line as the match.
#
# It used to include the words "regex" and "pattern", checked across a ±320
# character window. That is worse than the disease: an agent that says "next
# I'll tune the pattern list" anywhere near a real usage-limit banner would
# suppress it entirely, and on this board agents discuss regexes constantly.
# A guard against a cosmetic false positive must never hide a real one.
_CODE_LOOKING = re.compile(
    r"re\.compile|_phrase\(|\\s\*|\\b|re\.I\b|^\s*[+\-]\s*[(\"']",
    re.I,
)

# The agent is mid-thought: both CLIs paint an interruptible working indicator
# while a turn runs. Seeing it means "busy", never "waiting" or "stalled".
# ("esc to cancel" is not one: it is how an approval prompt ends — codex's MCP
# tool approval says "enter to submit | esc to cancel" — and counting it as
# busy hid a task frozen on that prompt for 13 hours.)
_BUSY_MARKERS = _phrase(r"esc to interrupt|ctrl\+c to (?:stop|interrupt)")

# An interactive prompt is on screen and it wants a human. The explicit phrases
# are what Claude Code and codex actually print; the structural fallback (a
# selection cursor together with numbered options) catches prompts whose wording
# we don't know — which is how a codex agent gets classified with no session
# status file to help.
_PROMPT_PHRASES = _phrase(
    r"do you want to proceed"
    r"|do you want to (?:allow|continue|create|make)"
    r"|would you like to"
    r"|requires approval"
    r"|allow codex to"
    r"|approve this plan"
    r"|ready to code\?"
    r"|and don'?t ask again"
    r"|trust the (?:files|folder)"
    r"|do you trust the contents"
    r"|no, and tell (?:claude|codex)"
    r"|allow the \S+ mcp server to run tool"
    r"|enter to submit"
)
_CURSOR_LINE = re.compile(r"^\s*[❯➤▶›>]\s*\S")
_NUMBERED_OPTION = re.compile(r"^\s*[❯➤▶›>]?\s*\d+[.)]\s+\S")

_LEADING_GLYPHS = re.compile(r"^[\s•■⏺⏵❯➤▶>*\-|]+")
_TRAILING_GLYPHS = re.compile(r"[\s•■⏺⏵❯➤▶>*|]+$")


def _tail_lines(tail: str) -> list[str]:
    return [ln for ln in (tail or "").split("\n") if ln.strip()]


# Where a quote must stop: the TUI chrome that surrounds a message (an input
# box, a bullet, the next widget) is not part of what the agent said.
_CHROME = re.compile(r"[›❯➤▶■⏺]|\s{3,}")


def _screen_line(text: str, start: int, end: int) -> str:
    """The screen line(s) a match sits on — nothing above or below it.

    Scope matters more than the pattern here: a diff line looks like code on
    its own line, whereas a neighbouring relay message from a teammate is just
    someone talking. Widening this to a character window is how a real alarm
    gets suppressed by unrelated text.
    """
    begin = text.rfind("\n", 0, start) + 1
    stop = text.find("\n", end)
    return text[begin:stop if stop != -1 else len(text)]


def _quote_at(text: str, start: int, end: int, limit: int = 260) -> str:
    """The agent's own words around a match, made readable.

    Reaches back to the start of the sentence — or failing that the start of the
    screen line, so "Claude usage limit reached" keeps its "Claude" — and
    forward over up to two sentences, stopping at any interface chrome. Then
    collapses the wrapping whitespace: the quote a human would read off the
    screen, not the 80-column fragment the terminal happened to draw.
    """
    # Back to the start of the screen line the match sits on (at most 90
    # characters), then forward again to the last sentence end inside it.
    window_start = max(text.rfind("\n", 0, start) + 1, start - 90)
    head = text[window_start:start]
    cut = max(head.rfind(". "), head.rfind("! "))
    begin = window_start + cut + 2 if cut >= 0 else window_start
    after = text[end:end + limit]
    chrome = _CHROME.search(after)
    if chrome:
        after = after[:chrome.start()]
    stop = end + len(after)
    # Extend over a second sentence only when the first is too short to be the
    # whole message — and never past the end of a screen line, because what
    # follows there is the agent talking, not more of the banner. (A wrapped
    # banner has no sentence end at its wrap points, so this never cuts one.)
    for m in re.finditer(r"[.!?](?=\s|$)", after):
        stop = end + m.end()
        if m.end() >= 60 or after[m.end():m.end() + 1] == "\n":
            break
    quote = " ".join(text[begin:stop].split())
    return _TRAILING_GLYPHS.sub("", _LEADING_GLYPHS.sub("", quote)[:limit])


# One refusal often trips several rules — "You've hit your usage limit. Upgrade
# to Pro …, visit … to purchase more credits or try again at 3:32 PM." matches
# both the limit rule and the credit rule. Matches this close together are one
# message, and the first of them names the actual cause.
_CLUSTER_SPAN = 400

# Screen structure, read off the first characters of a line (``ptyrun.tail``
# strips each line, so continuation lines of a wrapped block start bare):
# a Claude turn or tool call; the frame of a box, which says nothing itself; the
# CLI's own error line; and everything that marks someone else's words — an
# agent's balloon or tool output, a prompt, a quote, a hub-typed ``[tag]``, a
# numbered finding.
_TURN_LINE = re.compile(r"^[●⏺]")
_BOX_EDGE = re.compile(r"^[\s│┃║|╭╰╮╯─]+")
_RESULT_GLYPH = "⎿"
_OWN_ERROR = re.compile(r"^[■✗✘⚠]")
_QUOTED_OPENER = re.compile(r"^(?:[●⏺•└├>›❯“\"«]|\[[^\]\n]{1,60}\]|\d+[.)]\s)")
# A `●` line that calls a tool rather than one Claude wrote. Its first `⎿` is
# the tool's output, whatever that output says. Only Claude's own tool names
# count as "● Name(…)" — a sentence can start "● Note(…)" too — plus the shapes
# live screens show for grouped tool work: "● Running 1 shell command…",
# "●ReadingNfile…", "● Searching for 2 patterns, reading 1 file", "● Calling
# ensemble…", and anything marked "(MCP)" or "(ctrl+o to expand)". Spaces may
# be missing in every one of them.
_TOOL_NAMES = ("Bash|BashOutput|PowerShell|Read|Write|Edit|MultiEdit|Update|Create|Glob|Grep|LS|"
               "Task|Agent|Skill|WebFetch|WebSearch|Fetch|TodoWrite|NotebookEdit|NotebookRead|"
               "KillShell|KillBash|Monitor|ToolSearch|SlashCommand|AskUserQuestion|ExitPlanMode|"
               "EnterPlanMode|Workflow|SendMessage|ListAgents|Artifact")
_TOOL_CALL = re.compile(
    r"^[●⏺]\s*(?:"
    rf"(?:{_TOOL_NAMES})\s*\("
    r"|.*\((?:MCP|ctrl\+o\s*to\s*expand)\)"
    # Only the verbs and objects Claude's grouped-work headers use: "● Adding
    # 3 tests" is a sentence, and a real error under it must still count.
    r"|(?:Running|Ran|Reading|Read|Searching|Searched|Listing|Listed|Writing|Wrote|"
    r"Editing|Edited|Making|Made|Fetching|Fetched)\s*(?:for\s*)?\d+\s*"
    r"(?:shell\s*commands?|files?|patterns?|director(?:y|ies)|(?:scratchpad\s*)?edits?|"
    r"urls?|pages?|searche?s?)\b"
    r"|Call(?:ing|ed)\s*(?:ensemble|claude-in-chrome)\b"
    r")")
# A `●` line that only announces something finished in the background. It
# arrives whether or not the agent can reach the API, so it is no proof the
# agent got past a wall above it. (Stripping loses spaces: "●Backgroundcommand".)
_NOTICE_TURN = _phrase(r"^[●⏺] ?background (?:command|task|shell|agent)")
# A tool's progress frame, which the raw buffer keeps above the final output.
_PROGRESS_RESULT = _phrase(r"^⎿ ?(?:running|waiting)\b")
# Claude draws a tool's output and its own API error with the same `⎿`. Its
# error *starts* with the refusal, give or take a short prefix ("API Error:
# 401 · ", "Claude "), and it is never the first `⎿` under a tool call.
_API_ERROR = re.compile(r"^\s*API\s*Error\b", re.I)
_RESULT_WALL_AT = 30


def _result_is_wall(text: str, at: int) -> bool:
    """Whether the `⎿` block starting at ``at`` begins with a refusal.

    A block that starts with a shell echo ("$ type fixture.txt"), a hub-typed
    ``[tag]``, a quote or a numbered item is output, never Claude's error —
    which matters because Claude also heads tool work with a plain sentence
    ("● Checking where the new test runs execute" over "⎿ $ git …"), so the
    line above cannot always say it was a tool."""
    body = text[at:at + 300]
    lead = body.lstrip()
    if lead.startswith("$") or _QUOTED_OPENER.match(lead):
        return False
    if _API_ERROR.match(body):
        return True
    first = min((m.start() for pat, _, _ in _BLOCK_RULES for m in [pat.search(body)] if m),
                default=None)
    return first is not None and len(body[:first].strip()) <= _RESULT_WALL_AT


def _body(line: str) -> str:
    return _BOX_EDGE.sub("", line)


def _is_tool_output(lines: list[str], j: int) -> bool:
    """Whether the `⎿` on line ``j`` is the output of a tool call: the nearest
    structure above it, past bare continuation lines, spinner frames and the
    tool's own progress frames, is a tool-call line. After another `⎿`, a
    prompt, a hub-typed line or Claude's own words it is Claude speaking."""
    for k in range(j - 1, -1, -1):
        body = _body(lines[k])
        if body.startswith(_RESULT_GLYPH):
            if _PROGRESS_RESULT.match(body):
                continue
            return False
        if _TURN_LINE.match(body):
            return bool(_TOOL_CALL.match(body))
        if _OWN_ERROR.match(body) or _QUOTED_OPENER.match(body):
            return False
    return False


def _own_wall_lines(text: str) -> tuple[int, callable]:
    """Where the agent's own status area begins, and a test for one offset.

    Returns ``(floor, owns)``: a match before ``floor`` sits above the last
    Claude turn and is history — the agent spoke after it, so it got past it
    (a background-task notice is not a turn) — and ``owns(offset)`` says
    whether the block holding that offset is the CLI's own error line rather
    than quoted text. A block is identified by the nearest line above (or at)
    the offset that starts with structure; a screen with no structure at all
    keeps the old behaviour and counts.
    """
    lines = text.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    floor = 0
    for i, ln in enumerate(lines):
        body = _body(ln)
        if _TURN_LINE.match(body) and not _NOTICE_TURN.match(body):
            floor = starts[i]
    verdicts: dict[int, bool] = {}

    def owns(offset: int) -> bool:
        i = max(0, text.count("\n", 0, offset))
        if i in verdicts:
            return verdicts[i]
        verdict, j = True, i
        while j >= 0:
            edge = _BOX_EDGE.match(lines[j])
            body = lines[j][edge.end():] if edge else lines[j]
            if body.startswith(_RESULT_GLYPH):
                verdict = (not _is_tool_output(lines, j)
                           and _result_is_wall(text, starts[j] + (edge.end() if edge else 0) + 1))
                break
            if _OWN_ERROR.match(body):
                break
            if _QUOTED_OPENER.match(body):
                verdict = False
                break
            j -= 1
        verdicts[i] = verdict
        return verdict

    return floor, owns


def find_block(tail: str) -> tuple[str, str, str] | None:
    """Scan a terminal's screen for "I cannot continue".

    Returns ``(why, cause, quoted_sentence)``, or None. Only the agent's own
    wall counts — below its last turn, on the CLI's own error line, never text
    quoted inside a balloon, a tool result or a hub-typed line (see the note
    above ``_BLOCK_RULES``). The *latest* such message wins — an old warning
    can sit above the current one — but within that message the *first* rule
    to match names the cause, and the quote spans the whole thing, so the
    product owner reads what the agent actually said.
    """
    text = tail or ""
    if not text:
        return None
    hits: list[tuple[int, int, str, str]] = []
    notices = [(n.start(), n.end()) for n in _LOGIN_NOTICE.finditer(text)]
    floor, owns = _own_wall_lines(text)
    for pat, why, cause in _BLOCK_RULES:
        for m in pat.finditer(text):
            if m.start() < floor or not owns(m.start()):
                continue        # history, or someone quoting a wall
            if _BLOCK_EXEMPT.search(text[max(0, m.start() - 60):m.end() + 60]):
                continue
            if any(s <= m.start() and m.end() <= e for s, e in notices):
                continue
            if _CODE_LOOKING.search(_screen_line(text, m.start(), m.end())):
                continue        # the agent is looking at code, not hitting a wall
            hits.append((m.start(), m.end(), why, cause))
    if not hits:
        return None
    hits.sort()
    cluster = [hits[-1]]
    for h in reversed(hits[:-1]):
        if cluster[0][0] - h[0] <= _CLUSTER_SPAN:
            cluster.insert(0, h)
        else:
            break
    start, _, why, cause = cluster[0]
    end = max(h[1] for h in cluster)
    return (why, cause, _quote_at(text, start, end))


def _last_at(pat: re.Pattern, text: str) -> int:
    """Where the last match of ``pat`` starts in ``text``, or -1."""
    at = -1
    for m in pat.finditer(text):
        at = m.start()
    return at


def looks_busy(tail: str) -> bool:
    """The screen shows a running turn. Only the bottom of it counts — an
    interruptible indicator that scrolled off the top means nothing — and a
    prompt drawn after it means the turn stopped to ask."""
    text = "\n".join(_tail_lines(tail)[-6:])
    busy = _last_at(_BUSY_MARKERS, text)
    return busy >= 0 and busy > _last_at(_PROMPT_PHRASES, text)


def looks_like_prompt(tail: str) -> bool:
    """An interactive prompt is waiting for an answer. Bottom of the screen
    only: a prompt already answered has scrolled up.

    Whichever comes last wins, a prompt or a working indicator: codex redraws
    in place, so its "Working (esc to interrupt)" and the approval prompt that
    stopped that turn end up on the same stripped line, the prompt after it."""
    lines = _tail_lines(tail)[-14:]
    if not lines:
        return False
    text = "\n".join(lines)
    prompt, busy = _last_at(_PROMPT_PHRASES, text), _last_at(_BUSY_MARKERS, text)
    if prompt > busy:
        return True
    if busy >= 0:
        return False               # a working indicator outranks a stale prompt
    # Structural fallback: a selection cursor AND at least two numbered choices.
    # Requiring both keeps ordinary numbered prose out of the notifications.
    has_cursor = any(_CURSOR_LINE.match(ln) for ln in lines)
    options = sum(1 for ln in lines if _NUMBERED_OPTION.match(ln))
    return has_cursor and options >= 2


# Each terminal's screen is analysed once per burst of output, not once per
# poll: {ptyId: (last_output, analysis)}. Live agents carry a full 512 KB
# buffer, and there can be a dozen of them.
_ANALYSIS: dict[str, tuple[float, dict]] = {}
_ANALYSIS_LOCK = threading.Lock()


def analyse(tail: str) -> dict:
    return {"block": find_block(tail), "busy": looks_busy(tail),
            "prompt": looks_like_prompt(tail)}


def _analyse_live(sess) -> tuple[str, dict]:
    """Read and classify a live terminal's screen, reusing the last verdict
    while it has printed nothing new."""
    stamp = sess.last_output
    with _ANALYSIS_LOCK:
        hit = _ANALYSIS.get(sess.id)
    if hit and hit[0] == stamp:
        return hit[1]["tail"], hit[1]
    try:
        tail = sess.tail()
    except Exception:
        tail = ""
    out = analyse(tail)
    out["tail"] = tail
    with _ANALYSIS_LOCK:
        _ANALYSIS[sess.id] = (stamp, out)
        if len(_ANALYSIS) > 200:
            _ANALYSIS.pop(next(iter(_ANALYSIS)))
    return tail, out


# ---------------------------------------------------------------------------
# Room reading (mtime-cached — this endpoint is polled)
# ---------------------------------------------------------------------------

_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


_REPORT_HEADING = re.compile(r"\A\*\*Report — [^\n]*\n\n")


def _open_to_human(room: dict, msgs: list) -> dict | None:
    """What an agent put to the human that is still open: a report of
    completed / question / blocked, or a message sent to "user" —
    ``{from, ts, kind, text, id}``, or None.

    An ask (``blocked``, ``question``, a message to the person) stays open
    until the person speaks in the chat, the agent reports ``completed``, or a
    later report of the agent's says the ask is over (``clears``, see
    ``ensemble_report``). An ``update`` about something else does not close
    it: a task blocked on a login reported that a group had approved its post,
    and the block left the bell although nobody had logged in. A ``completed``
    is not an ask: a later ``update`` (it is working again) still ends it.

    The newest ``blocked`` / ``question`` wins over a plain message sent after
    it. Teammate chatter does not answer anything — the redesign pair sent
    "ready to merge" to the user, then a thank-you to the designer, and the
    merge was still waiting on a human.

    A one-agent task is answered in its terminal as often as in chat: what a
    person (or the PO, for them) submitted to it after the ask closes it too.
    That is kept on the participant (``answeredAt``, see
    ``dashboard.note_answer``), not on the terminal, so the ask stays closed
    across a hub restart and a rotation — and the bell, the chat's line and
    the progress check all read this one rule. Such an answer ends the scan
    where the person speaking in chat would: what the agent put to them after
    it is as open as ever, whatever it had asked before."""
    agents = {p.get("identity") for p in room.get("participants", [])
              if p.get("kind") == "agent"}
    answered: dict[str, float] = {}
    if room.get("mode") == "solo":
        for p in room.get("participants", []):
            try:
                answered[p.get("identity")] = float(p.get("answeredAt") or 0)
            except (TypeError, ValueError):
                pass
    reports = [r for r in (room.get("lastReport"), room.get("lastRealReport")) if isinstance(r, dict)]
    updated = False         # a later update: the agent is working again
    message = None          # the newest plain message to the person
    for m in reversed(msgs):
        frm = m.get("from", "")
        if frm == "user":
            break
        if frm not in agents:
            continue
        if answered.get(frm, 0) > float(m.get("ts") or 0):
            break               # answered in its terminal after this
        if m.get("kind") == "report":
            kind = m.get("reportKind", "")
            if kind == "update":
                if m.get("clears"):
                    break
                updated = True
                continue
            if kind == "completed" and (updated or message):
                break
            text = next((r.get("text", "") for r in reports if r.get("messageId") == m.get("id")),
                        None)
            if text is None:
                text = _REPORT_HEADING.sub("", m.get("text", "") or "")
            return {"from": frm, "ts": m.get("ts", 0), "kind": kind, "id": m.get("id", ""),
                    "text": " ".join((text or "").split())[:400], "line": _first_line(text)}
        if (m.get("to") or "") == "user" and message is None:
            message = {"from": frm, "ts": m.get("ts", 0), "kind": "message", "id": m.get("id", ""),
                       "text": " ".join((m.get("text") or "").split())[:400],
                       "line": _first_line(m.get("text"))}
    return message


def _first_line(text) -> str:
    """The first line of an ask that says something, without Markdown marks."""
    for ln in (text or "").splitlines():
        ln = " ".join(re.sub(r"^[#>\-*\s]+|[*`]+", "", ln).split())
        if ln:
            return ln[:200]
    return ""


def open_ask(room: dict) -> dict | None:
    """The room's open ask to the person (see ``_open_to_human``), from a room
    that carries its messages."""
    return _open_to_human(room, room.get("messages") or [])


def open_ask_now(room: dict) -> dict | None:
    """The ask the task's chat holds above the conversation: a ``blocked``, a
    ``question`` or a message to the person that is still open, by the rules
    the bell uses — so a one-agent task answered in its terminal has none. A
    ``completed`` is not an ask."""
    ask = open_ask(room)
    return ask if ask and ask["kind"] != "completed" else None


def _summarize(room: dict) -> dict:
    """The few room fields attention needs, without its whole message log."""
    msgs = room.get("messages") or []
    # A task owner's "new session" notice is for the human and asks nothing of
    # anyone: the rotation itself is the ask (the participant's rotatedAt). A
    # PO's rotation sets no rotatedAt, so its notice still ends any older ask.
    last = next((m for m in reversed(msgs)
                 if not (m.get("noticeKind") == "rotation"
                         and (m.get("rotation") or {}).get("startedAt"))), {})
    cr = _d.chatroom
    rang = last.get("rang")
    if last and not isinstance(rang, list):
        # A message from before the wake rules recorded who they woke.
        rang = cr.wake_targets(room, last.get("from", ""), last.get("to", ""),
                               last.get("text", ""))
    return {
        "id": room.get("id", ""),
        "title": room.get("title", ""),
        "no": room.get("no") or None,
        "noProjectId": room.get("noProjectId") or "",
        "status": room.get("status", "active"),
        "waitingFor": room.get("waitingFor", ""),
        "hopCount": room.get("hopCount", 0),
        "maxHops": room.get("maxHops", 0),
        "launched": room.get("launched", True),
        "cwd": room.get("cwd", ""),
        "projectId": room.get("projectId", ""),
        "createdAt": room.get("createdAt", 0),
        "updatedAt": room.get("updatedAt", 0),
        "mode": room.get("mode", ""),
        "owners": cr.owners(room),
        "participants": [
            {k: p.get(k) for k in ("identity", "kind", "agent", "role",
                                   "ptyId", "sessionId", "lastExit", "resumedAt",
                                   "rotatedAt", "answeredAt")}
            for p in room.get("participants", [])
        ],
        "lastMessage": {"from": last.get("from", ""), "to": last.get("to", ""),
                        "text": (last.get("text") or "")[:400], "ts": last.get("ts", 0),
                        "rang": rang or []},
        "openToHuman": _open_to_human(room, msgs),
    }


def _room_summaries() -> list[dict]:
    """Every room, summarised, re-reading only the files that changed since the
    last poll. A room's JSON carries its whole chat history, so re-parsing all
    of them every few seconds is the one cost worth avoiding here."""
    out: list[dict] = []
    live_ids: set[str] = set()
    try:
        paths = list(_d.chatroom.ROOMS_DIR.glob("room-*.json"))
    except OSError:
        return out
    for p in paths:
        rid = p.stem
        live_ids.add(rid)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        with _CACHE_LOCK:
            hit = _SUMMARY_CACHE.get(rid)
        if hit and hit[0] == mtime:
            out.append(hit[1])
            continue
        try:
            room = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = _summarize(room)
        with _CACHE_LOCK:
            _SUMMARY_CACHE[rid] = (mtime, summary)
        out.append(summary)
    with _CACHE_LOCK:                       # forget deleted rooms
        for gone in [k for k in _SUMMARY_CACHE if k not in live_ids]:
            _SUMMARY_CACHE.pop(gone, None)
    return out


# ---------------------------------------------------------------------------
# Per-agent evidence
# ---------------------------------------------------------------------------

def _claude_status_by_session() -> dict[str, tuple[str, float]]:
    """{sessionId: (status, since)} for every live Claude session. The status
    is Claude's own, and ``waiting`` means it is blocked on a permission or plan
    prompt; ``since`` is when it last changed (0 when the file does not say)."""
    out: dict[str, tuple[str, float]] = {}
    try:
        for d in _d._read_session_files():
            sid = d.get("sessionId", "")
            if sid:
                try:
                    since = float(d.get("statusUpdatedAt") or 0) / 1000.0
                except (TypeError, ValueError):
                    since = 0.0
                out[sid] = (d.get("status", "") or "", since)
    except Exception:
        pass
    return out


def _evidence(part: dict, statuses: dict[str, tuple[str, float]]) -> dict:
    """Everything known about one agent right now: its terminal (alive or dead),
    what that terminal says, and what Claude says about itself."""
    ptyrun = _d.ptyrun
    pty_id = (part.get("ptyId") or "").strip()
    sess = ptyrun.get(pty_id) if pty_id else None
    alive = bool(sess and sess.alive())
    tail, idle, death, submitted, printed, hook = "", None, None, 0.0, 0.0, None
    if alive:
        tail, scan = _analyse_live(sess)
        try:
            printed = float(sess.last_output or 0)
        except (TypeError, ValueError):
            printed = 0.0
        if (part.get("agent") or "") != "codex":
            # Held per terminal: what an earlier run of this agent said is
            # not about this one.
            hook = _d.agent_hooks.state_for(pty_id)
        try:
            idle = sess.info().get("idleSeconds")
        except Exception:
            idle = None
        try:
            submitted = float(sess.last_submit() or 0)
        except Exception:
            submitted = 0.0
    else:
        scan = None
    if not alive and pty_id:
        # The death record: from this process's memory, else the copy persisted
        # on the task (which is what survives a hub restart). Only a record for
        # THIS pty counts — a relaunched agent gets a new pty id, so a stale
        # record from a previous run can never re-raise an old alarm.
        death = (sess.death() if sess is not None else None) or ptyrun.death_for(pty_id)
        if death is None:
            saved = part.get("lastExit")
            if isinstance(saved, dict) and saved.get("ptyId") == pty_id:
                death = saved
        if death:
            tail = death.get("tail", "") or ""
            scan = analyse(tail)
    # The session the hub launched, else the one the agent's hooks name: a
    # /clear or a resume can continue under a new id.
    said = (statuses.get((part.get("sessionId") or "").strip())
            or statuses.get((hook or {}).get("sessionId") or "") or ("", 0.0))
    if isinstance(said, str):
        said = (said, 0.0)
    return {
        "ptyId": pty_id, "alive": alive, "tail": tail, "idleSeconds": idle,
        "lastSubmit": submitted, "death": death, "scan": scan or {"block": None, "busy": False, "prompt": False},
        "claudeStatus": said[0], "claudeStatusAt": said[1],
        "hook": hook, "lastOutput": printed,
    }


# How long after a hook the terminal must still be printing, with a working
# indicator and no prompt on it, for the screen to count as having moved on.
_HOOK_MOVED_ON = 5.0
_HOOK_STATUS = {"working": "busy", "waiting": "waiting", "idle": "idle"}


def _hook_status(ev: dict) -> str:
    """What the agent's last hook says it is doing, in the status file's words
    (``busy`` / ``waiting`` / ``idle``) — or "" when there is none or it can no
    longer be believed, and the status file and the screen decide as before.

    A hook is the agent's own word at one moment; what makes it wrong is only
    ever a later moment nobody reported (a hook lost while the hub was slow,
    a turn interrupted with Esc, which fires none). So it is dropped when
    something newer contradicts it, never because of its age — an agent that
    finished yesterday is still finished — and what was contradicted once
    stays dropped until the next hook (``agent_hooks.invalidate``):

    * **Another terminal's.** Held per terminal, so a relaunch starts clean.
    * **The session ended.** Nothing to say about a live terminal: the screen.
    * **The status file changed later, to something else.** It is Claude's
      own word too, and the newer one. Approving a permission prompt flips it
      to ``busy`` at once, where the tool's hook comes only when the tool is
      done; an interrupted turn flips it to ``idle``; and a prompt typed while
      a turn runs fires its hook then, not when its own turn starts — so that
      turn begins, moments after the ``Stop`` before it, with the file alone
      saying ``busy`` (measured: 40 ms apart, which is why there is no grace
      period here). A later change that says the same leaves the hook in
      charge: it knows more (see the prompt fallback in ``_classify_agent``).
      The file is the agent's own: ``idle`` ends what the agent asked, not
      what a subagent still running asked.
    * **"Working", on a terminal silent for ``_MIN_QUIET``.** A running turn
      repaints its indicator every second. Silence means the turn ended with
      no hook — or sits on a prompt whose hook was lost, which the screen
      then shows.
    * **"Waiting" or "idle", but the terminal has printed for more than
      ``_HOOK_MOVED_ON`` seconds since, still is, and shows a working
      indicator and no prompt.** It went back to work and the hook saying so
      was lost.
    """
    hook = ev.get("hook") or {}
    status = _HOOK_STATUS.get(hook.get("state") or "", "")
    if not status:
        return ""
    at = float(hook.get("at") or 0)
    idle = ev.get("idleSeconds")
    quiet = idle is not None and idle >= _MIN_QUIET
    scan = ev.get("scan") or {}
    turn_over = False
    if status != "busy" and scan.get("busy") and not scan.get("prompt") and not quiet \
            and float(ev.get("lastOutput") or 0) > at + _HOOK_MOVED_ON:
        # Before the status file: the screen is about every ask, the file's
        # ``idle`` only about the agent's own.
        outdated = True
    elif ev.get("claudeStatus") not in ("", status) and float(ev.get("claudeStatusAt") or 0) > at:
        outdated = True
        turn_over = ev.get("claudeStatus") == "idle"
    else:
        outdated = status == "busy" and quiet
    if outdated:
        # For good, not for this poll: the evidence against it passes (the
        # terminal goes quiet, or prints again), what it disproved does not
        # come back. The next hook starts afresh.
        left = None
        if _d is not None:
            left = _d.agent_hooks.invalidate(ev.get("ptyId") or "", at, own_only=turn_over)
        # The agent's own turn ending answers nothing a subagent asked.
        return "waiting" if turn_over and (left or {}).get("state") == "waiting" else ""
    return status


def turn_state(part: dict, statuses: dict | None = None) -> tuple[str, str]:
    """Whether an agent is in the middle of a turn right now, for the snapshot a
    planned restart takes just before the hub stops: ``(state, source)`` with
    state ``working`` | ``waiting`` (a prompt is up: its turn is not over) |
    ``idle`` | ``stopped`` (no live terminal), and source ``hook`` | ``status``
    | ``screen`` — what said so, in the order ``_classify_agent`` believes
    them: the agent's hooks, Claude's status file, then the screen (all codex
    and a hook-less agent have)."""
    ev = _evidence(part, _claude_status_by_session() if statuses is None else statuses)
    if not ev["alive"]:
        return "stopped", ""
    hooked = _hook_status(ev)
    said, source = (hooked, "hook") if hooked else (ev["claudeStatus"], "status")
    if said in ("busy", "shell"):
        return "working", source
    if said == "waiting":
        return "waiting", source
    scan, idle = ev["scan"], ev["idleSeconds"]
    if said == "idle":
        # Its hooks report every prompt; the status file does not (a question).
        return ("waiting", "screen") if scan["prompt"] and source != "hook" else ("idle", source)
    if scan["prompt"]:
        return "waiting", "screen"
    if scan["busy"] and (idle is None or idle < _MIN_QUIET):
        return "working", "screen"
    return "idle", "screen"


def _owed_since(room: dict, identity: str) -> tuple[float, str]:
    """When this agent was last asked for something it hasn't delivered.

    Returns ``(when, "launch" | "message")``, or ``(0, "")`` if it owes nothing.

    This is what keeps ``stalled`` honest, and it has to cover the commonest
    shape of task on the board: a **solo** agent with no teammate to hand off
    to, and often no messages at all. For that one the ask is the launch itself
    — the spec was its first prompt — so an agent that has never said anything
    since it started owes its first report. The caller needs to know which case
    it is, because only one of them can honestly be described as "asked N ago":
    a launch was a single event that may be days old.
    """
    last = room.get("lastMessage") or {}
    sender = last.get("from", "")
    part = next((p for p in room.get("participants") or []
                 if p.get("identity") == identity), {})
    resumed = max(float(part.get("resumedAt") or 0), float(part.get("rotatedAt") or 0))
    if resumed > float(last.get("ts") or 0):
        # Started again: the hub typed it a line to carry on (see the
        # dashboard's RESUME_NOTE), or handed it to a fresh session that
        # continues from its handover (rotation.py). That is an ask like a
        # message, dated from when it was made — so a fresh session reading
        # its handover is not stalled over an older ask — and an agent left
        # idle at an empty prompt after it is exactly what nobody noticed.
        return resumed, "message"
    if not sender:
        # Nothing has ever been said in this room. In a one-agent task the
        # human drives the agent through its terminal, and an agent idle at
        # its prompt has simply finished its turn — not an alarm. In a team
        # the spec was the ask, but only of the owner: a reviewer waits for
        # a deliverable by design.
        if room.get("mode") == "solo" or identity not in (room.get("owners") or []):
            return 0.0, ""
        return float(room.get("createdAt") or 0), "launch"
    if sender == identity:
        return 0.0, ""             # it spoke last — the ball is elsewhere
    if identity not in (last.get("rang") or []):
        return 0.0, ""             # it was never woken, so nothing was asked of it
    return float(last.get("ts") or 0), "message"


_ASK_NAMES = {"blocked": "its blocked report", "question": "its question",
              "message": "its message to you"}


def _classify_agent(room: dict, part: dict, ev: dict, stall_seconds: int,
                    now: float) -> tuple[str, str, dict] | None:
    """One agent's attention state, or None if it needs nothing.

    Liveness decides first. A dead terminal is never "waiting for you", and a
    live one is never "gone" — the state has to tell the truth about that,
    because a dead agent needs relaunching while a blocked one needs a wait or
    a login.
    """
    identity = part.get("identity", "")
    kind = part.get("agent", "") or "agent"
    who = f"{identity} ({kind})" if kind != identity else identity
    block = ev["scan"]["block"]
    # What Claude itself says it is doing: its last hook while that can be
    # believed, else its status file. Codex says nothing, by either route.
    hooked = _hook_status(ev) if ev["alive"] else ""
    status = hooked or ev["claudeStatus"]
    if block and ev["alive"] and status == "busy":
        # A Claude session that says it is working has not hit a wall: a real
        # refusal ends the turn. The screen verdict is kept, so a wall drawn
        # while the status file is a second behind is reported on the next
        # poll that reads it idle.
        block = None

    if not ev["alive"]:
        death = ev["death"]
        if death is None:
            return None            # never ran here, or orphaned by a restart
        if death.get("killed"):
            return None            # we stopped it on purpose
        if now - float(death.get("endedAt") or 0) > _DEATH_MAX_AGE:
            return None            # old enough to be history rather than news
        code = death.get("exitCode")
        exit_txt = "exit status unknown" if code is None else f"exit status {code}"
        extra = {"exitCode": code, "endedAt": death.get("endedAt"),
                 "lastLines": "\n".join(_tail_lines(ev["tail"])[-12:])}
        if block:
            why, cause, line = block
            extra["quote"] = line
            extra["cause"] = cause
            return ("agent_gone", f"{who} died — it {why}: “{line}” ({exit_txt})", extra)
        return ("agent_gone", f"{who} died on its own ({exit_txt})", extra)

    # What it put to a human that nobody has answered (``_open_to_human``). An
    # ask keeps its text and its time on the item whatever else the terminal
    # shows: a prompt or a wall on the screen is one more thing to see to, and
    # neither answers it. (A ``completed`` is not an ask: a prompt after it is
    # simply the newer thing.)
    put = room.get("openToHuman")
    put = put if put and put.get("from") == identity else None
    q = (put or {}).get("text", "")
    asked = {"quote": q, "since": float(put.get("ts") or 0), "askId": put.get("id", "")} if put else {}
    still, still_extra = "", {}
    if put and put["kind"] != "completed":
        still = f"; {_ASK_NAMES.get(put['kind'], 'its message to you')} is still open: “{q}”"
        still_extra = {**asked, **({"cause": "reported"} if put["kind"] == "blocked" else {})}

    if block:
        why, cause, line = block
        return ("blocked", f"{who} {why}: “{line}”{still}",
                {"quote": line, **still_extra, "cause": cause})

    if status == "waiting":
        return ("waiting_for_you", f"{who} is waiting on your answer to a prompt{still}", still_extra)
    if status != "busy" and ev["scan"]["prompt"] and hooked != "idle":
        # The screen is the fallback, and it is needed for two different
        # reasons: codex publishes no status at all, and Claude's status file
        # does not flip to "waiting" for every prompt (an AskUserQuestion
        # doesn't — see the same fallback in session.html's `looksLikePrompt`).
        # A working indicator already rules the screen out, so this can't
        # catch a thinking agent. An agent whose hook says it finished its turn
        # has no prompt up — its hooks report every one, the question too — so
        # there the words of a prompt are the end of its own last message
        # ("Would you like to…?").
        return ("waiting_for_you", f"{who} has a prompt on screen waiting for you{still}", still_extra)

    # A working indicator on a screen that has been still for a while is a
    # leftover: both CLIs repaint theirs every second while a turn runs, and
    # codex's in-place redraws leave its start-up "esc to interrupt" in the
    # stripped text of a session idle at its prompt.
    # It put something to a human — a report, a question, "ready to merge" —
    # and nobody has answered. That is the human's move, never a stall, and it
    # stays up whatever the terminal is doing: an agent that went back to
    # checking on its own (a doorbell, a progress check, a restart) has not
    # been answered, and "a busy Claude is never blocked" above is about walls
    # read off the screen, not about what the agent itself reported.
    if put:
        if put["kind"] == "blocked":
            return ("blocked", f"{who} reported it is blocked and needs help: “{q}”",
                    {**asked, "cause": "reported"})
        if put["kind"] == "completed":
            return ("waiting_for_you", f"{who} reported the work is finished: “{q}”", asked)
        if put["kind"] == "question":
            return ("waiting_for_you", f"{who} asked: “{q}”", asked)
        return ("waiting_for_you", f"{who} is waiting on your reply: “{q}”", asked)

    idle = ev["idleSeconds"]
    if status == "busy" or (ev["scan"]["busy"] and (idle is None or idle < _MIN_QUIET)):
        return None                # thinking is not a problem, however long

    if room.get("status") != "active":
        return None                # the room is waiting on the human, not on it
    asked, how = _owed_since(room, identity)
    if not asked:
        return None
    idle = ev["idleSeconds"]
    waited = now - asked
    if room.get("mode") == "solo" and idle is not None and waited - idle > _ANSWER_AFTER:
        # A one-agent task answers in its terminal: it kept printing well
        # after it was asked, so it worked on it and answered — just not in
        # chat. (The doorbell's own keystrokes print at the moment of asking,
        # which is why "printed since" needs a margin.)
        return None
    # What the threshold measures has to match what the reason claims, and the
    # two asks are not alike. A message has a timestamp, so the wait is real
    # elapsed time. A launch was a single event that may be days old: measuring
    # from it would make every solo task older than the threshold permanently
    # eligible, leaving only the 60-second floor to gate it — the setting would
    # do nothing and a minute at the prompt would raise an alarm. There, the
    # silence *is* the measurement.
    elapsed = waited if how == "message" else (idle if idle is not None else waited)
    quiet_enough = idle is None or idle >= min(_MIN_QUIET, stall_seconds)
    if elapsed < stall_seconds or not quiet_enough:
        return None
    extra = {"waitedSeconds": int(waited), "idleSeconds": idle}
    if how == "message":
        return ("stalled",
                f"{who} was asked to do something {_ago(waited)} ago, isn't "
                f"working, and hasn't reported back", extra)
    # The only ask was the launch. From outside there is no telling "finished
    # quietly" from "stuck", so say what is actually known — the terminal has
    # been silent this long, and it never reported anything.
    return ("stalled",
            f"{who} has been idle at its prompt for {_ago(elapsed)} and never "
            f"reported back since it started", extra)


def _ago(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "a while"
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{max(1, s // 60)} min"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------

def _room_level(room: dict, live_agents: list[str]) -> tuple[str, str, dict] | None:
    """Attention the room itself declares: it is waiting on the human, or the
    collaboration hit its hop limit and paused. Only meaningful while an agent
    is still alive to receive the answer."""
    if not live_agents:
        return None
    status = room.get("status", "active")
    if status == "paused" and room.get("hopCount", 0) >= (room.get("maxHops") or 0) > 0:
        return ("waiting_for_you",
                f"the agents handed off {room['hopCount']} times without you and "
                f"paused at their limit — they need your steer", {})
    if status in ("waiting_human", "paused"):
        last = room.get("lastMessage") or {}
        who = last.get("from", "") or room.get("waitingFor", "") or "the agents"
        text = " ".join((last.get("text") or "").split())[:200]
        reason = f"{who} is waiting on your reply"
        if text:
            reason += f": “{text}”"
        return ("waiting_for_you", reason, {"quote": text})
    return None


# When each (task, state) was first seen, so the tray can say how long a task
# has been in trouble rather than when its chat last moved. Reset the moment a
# state clears, so a state that comes back reads as new. An unguarded dict is
# safe here because `_items()` has exactly one caller, `snapshot()`, and it
# runs under `_COMPUTE_LOCK` — keep it that way.
_FIRST_SEEN: dict[tuple[str, str], float] = {}


def _first_seen(room_id: str, state: str, now: float) -> float:
    return _FIRST_SEEN.setdefault((room_id, state), now)


def _stall_seconds() -> int:
    try:
        v = int(_d.load_settings().get("attentionStallSeconds", STALL_SECONDS_DEFAULT))
    except (TypeError, ValueError, AttributeError):
        return STALL_SECONDS_DEFAULT
    return max(60, min(24 * 3600, v))


def _items() -> list[dict]:
    now = time.time()
    rooms = _room_summaries()
    statuses = _claude_status_by_session()
    stall = _stall_seconds()
    links = _d.load_session_projects()
    projects = {p["id"]: p for p in _d.load_projects()}
    labels = _d.load_labels()
    all_projects = list(projects.values())
    try:
        keys = _d.project_keys(all_projects)
    except Exception:
        keys = {}
    items: list[dict] = []
    seen_now: set[tuple[str, str]] = set()

    for room in rooms:
        if not room.get("launched", True):
            continue               # a draft has no agents to worry about
        agents = [p for p in room["participants"] if p.get("kind") == "agent"]
        if not agents:
            continue
        found: list[tuple[str, str, dict, dict]] = []   # (state, reason, extra, part)
        live_agents: list[str] = []
        for part in agents:
            ev = _evidence(part, statuses)
            if ev["alive"]:
                live_agents.append(part.get("identity", ""))
            hit = _classify_agent(room, part, ev, stall, now)
            if hit:
                found.append((*hit, part))
        room_hit = _room_level(room, live_agents)
        if room_hit and not any(f[0] == "waiting_for_you" for f in found):
            found.append((*room_hit, {}))
        if not found:
            continue
        # One item per task: the worst thing wrong with it.
        found.sort(key=lambda f: _SEVERITY.get(f[0], 99))
        state, reason, extra, part = found[0]
        rid = room["id"]
        pid = links.get(rid) or room.get("projectId") or \
            _d._project_for_cwd(room.get("cwd", ""), all_projects)
        no = room.get("no")
        item = {
            "roomId": rid,
            # Its number (#18), and the full form (ED-18) for lists of every project.
            "no": no,
            "ref": _d.task_numbers.label(no, keys.get(room.get("noProjectId"), "")) if no else "",
            "projectId": pid or "",
            "project": (projects.get(pid) or {}).get("name", "") if pid else "",
            "title": labels.get(rid) or room.get("title", "") or rid,
            "state": state,
            "reason": reason,
            "agentIdentity": part.get("identity", "") if part else "",
            "agent": part.get("agent", "") if part else "",
            # The model, so an entry can name WHO is stuck in the terms the
            # rest of the UI uses — "claude opus", not just "claude".
            "agentModel": part.get("model", "") if part else "",
            # An agent_gone item has no terminal left to open — its pty is out
            # of the registry — so the notification carries the last screen.
            "ptyId": (part.get("ptyId", "") if part and state != "agent_gone" else ""),
            "sessionId": part.get("sessionId", "") if part else "",
            # When this became true — a death knows exactly; for the rest it is
            # when we first saw the state, which is what "blocked · 4 min ago"
            # has to mean. Room `updatedAt` would be the last chat message, and
            # would read as 4 minutes for an agent blocked for an hour.
            # An ask the agent reported dates from the report.
            "since": float(extra.get("endedAt") or extra.get("since") or _first_seen(rid, state, now)),
            "otherStates": sorted({f[0] for f in found[1:]}),
        }
        seen_now.add((rid, state))
        if extra.get("since"):
            item["askedAt"] = float(extra["since"])     # the pages say "since 15:55"
        for k in ("quote", "cause", "exitCode", "lastLines", "waitedSeconds", "askId"):
            if k in extra and extra[k] not in (None, ""):
                item[k] = extra[k]
        items.append(item)
    # Forget states that have cleared, so the same trouble returning later
    # reads as new rather than inheriting an old start time.
    for gone in [k for k in _FIRST_SEEN if k not in seen_now]:
        _FIRST_SEEN.pop(gone, None)
    items.sort(key=lambda it: (_SEVERITY.get(it["state"], 99), -(it.get("since") or 0)))
    return items


# A short result cache so the page can poll every couple of seconds for free.
_RESULT_TTL = 1.5
_result: tuple[float, dict] = (0.0, {})
_RESULT_LOCK = threading.Lock()
_COMPUTE_LOCK = threading.Lock()


def snapshot(max_age: float = _RESULT_TTL) -> dict:
    """``{items, count, byState, generatedAt}`` — the answer to "what needs me?"."""
    global _result
    with _RESULT_LOCK:
        ts, cached = _result
    if cached and time.time() - ts <= max_age:
        return cached
    # One computation even when ten tabs ask at once: the losers of the race
    # take the fresh result the winner just published.
    with _COMPUTE_LOCK:
        with _RESULT_LOCK:
            ts, cached = _result
        if cached and time.time() - ts <= max_age:
            return cached
        items = _items()
        by_state: dict[str, int] = {}
        for it in items:
            by_state[it["state"]] = by_state.get(it["state"], 0) + 1
        payload = {"items": items, "count": len(items), "byState": by_state,
                   "generatedAt": time.time()}
        with _RESULT_LOCK:
            _result = (payload["generatedAt"], payload)
        return payload


def by_room(max_age: float = _RESULT_TTL) -> dict[str, dict]:
    """The same items keyed by room id, for stamping rows that are already being
    built — ``/api/sessions`` uses this instead of computing anything itself."""
    return {it["roomId"]: it for it in snapshot(max_age)["items"]}


# ---------------------------------------------------------------------------
# Death capture → the task record
# ---------------------------------------------------------------------------

_TAIL_KEEP = 4000


def on_pty_death(rec: dict) -> None:
    """Persist a terminal's dying screen onto its task.

    Registered with ``ptyrun.set_death_hook``. The in-memory record is enough
    for the running hub, but the evidence people come back looking for — "it
    died because it ran out of usage" — has to survive a restart, so it is
    written onto the room. Runs on the dead session's reader thread, through the
    narrow :func:`chatroom.record_exit` (a full room rewrite from here would
    race an incoming chat message).

    The room file lives under ``DASHBOARD_DIR``, never under the projects root.
    Raw terminal text stays out of the backed-up folder because
    ``dashboard._export_task_chats`` whitelists participant fields down to
    identity/agent/model/role — keep that whitelist a whitelist if you edit it.
    """
    meta = rec.get("meta") or {}
    rid, identity = meta.get("room", ""), meta.get("identity", "")
    if not rid or not identity:
        return
    try:
        _d.chatroom.record_exit(rid, identity, {
            "ptyId": rec.get("ptyId", ""),
            "exitCode": rec.get("exitCode"),
            "killed": bool(rec.get("killed")),
            "endedAt": rec.get("endedAt", time.time()),
            "tail": (rec.get("tail") or "")[-_TAIL_KEEP:],
        })
    except Exception:
        pass


def install() -> None:
    """Wire the death hook. Called once by the dashboard at start-up."""
    _d.ptyrun.set_death_hook(on_pty_death)
