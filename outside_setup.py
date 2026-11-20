"""The outside sources setup file (#15): one plain Markdown file people edit
to set which outside sources are read and which initiative their messages
go to.

**The files**, in the hub's data folder (``outside/``):

- ``setup.md``: what people set. A ``## Sources`` section (each source and
  on / off / pause / decide only), then one section per initiative, headed
  with the initiative's name: ``Topic:``, ``Slack channels:``, ``Jira:``
  and ``Work items:`` lines. The work items the hub fills by itself are not
  listed there. At its top, between two marker comments, the hub writes the
  problems it found on the last save; that part is not counted as a save.
- ``routing-now.md``: the table exactly as the hub routes from it (channel
  ids and names, every work item with where it came from, the last change).
  Rewritten after each apply and whenever ``routing.json`` changes. Read
  only: it says so at its top.
- ``setup-state.json``: what the watcher knows: the file's modified time
  when last seen, the text last applied (a hash, the problems part left
  out), the problems, and whether the last try failed (Haiku did not
  answer: nothing applied; or only the Slack lookup: the rest applied) and
  when. ``applies``: the last 100 applies that changed something or found
  other problems, each with what it changed. The sources' changes are kept
  only there; the routing table's are also in ``routing.json``'s history.
- ``channel-ids.json``: ``{channel name: id}`` found by Slack lookups.

``routing.json`` (``outside_route``) stays the form the hub routes from,
with its history. The file is applied into it through
``outside_route.set_initiative``, so the table's rules hold: one channel or
work item in one row only, a clash is refused.

**Reading the file.** The sections are split in code by their ``## ``
headings. A section written in the plain form above (the form the hub
writes) is read in code. Any other section is read by one Haiku call
(``outside_model.run_claude``) that answers JSON; the code then checks
every value it returns against the section's text, so a value Haiku made up
is a problem, not a change.

**Applying it** (``apply_text``). The sources first, then each initiative's
section on its own: a section with a problem keeps the row it had, the rest
apply. A section that a clash refused is tried again after the others (so a
channel moved from one section to another in one save works). An
initiative whose row has something set but that has no section is cleared
of what people set, unless the file has a section with a name the hub does
not know (likely a renamed initiative), in which case nothing absent is
cleared. If Haiku fails, nothing changes; the watcher tries again in 10
minutes or on the next save. ``check_only`` returns what a text would do
and changes nothing (for the hub chat that comes next): it works on copies
of the sources' config and the routing table that only its own thread sees
(``outside.preview_config``, ``outside_route.preview_table``), writes no
file and logs nothing. It does ask Slack for channel ids it does not know.

**The watcher** (``tick``) runs from the hub loop: it writes the file from
today's table the first time, and applies it on a background thread after
each save and once after the hub starts when the text differs from what was
last applied or the last apply had problems.

**Changes made outside the file** (``section_change``): an agent's or a
PO's ``ensemble_routing`` set (``set_own``) and the message log's Make a
rule change one row through ``set_initiative``, then the hub rewrites that
section of the file. No apply runs meanwhile. A section in the plain form
keeps every line but the ones the change touched; a loosely written section
is rewritten whole in the plain form, and a change to a loose section that
holds words the hub has not applied (an unapplied save, a problem) is
refused, so those words are not lost; so is a change to a plain section's
line that a save changed since the last apply (``setup-state.json`` keeps
each section's lines as last applied, ``appliedLines``).
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import re
import threading
import time
from pathlib import Path

import durable
import outside
import outside_route as route
import sources

SETUP_NAME = "setup.md"
NOW_NAME = "routing-now.md"
STATE_NAME = "setup-state.json"
IDS_NAME = "channel-ids.json"

RETRY_S = 600                   # after Haiku or a Slack lookup failed, tried again this much later
HAIKU_TIMEOUT_S = 120
APPLIES_KEPT = 100              # setup-state.json keeps this many applies (what each changed)
APPLY_WAIT_S = 20               # how long a PO's change waits for an apply that is running
SETUP_BY = {"kind": "setup"}    # who made a change the file made
HUB_CHAT_BY = {"kind": "hubchat"}   # a new file the hub chat wrote, applied on the CEO's click (#16)
SOURCES_HEADING = "sources"

PROBLEMS_START = "<!-- problems: the hub writes this part after each save; what you type here is replaced -->"
PROBLEMS_END = "<!-- end of problems -->"
NO_PROBLEMS = "No problems on the last save."

# The source words a person writes, and (mode, paused) each means.
MODE_WORDS = {"on": ("on", False), "off": ("off", False), "pause": (None, True),
              "paused": (None, True), "decide only": ("decide", False),
              "decide": ("decide", False)}

_LOCK = threading.RLock()       # one write of the setup file at a time
_APPLYING = threading.Lock()    # one apply at a time
_STARTED = {"checked": False}   # the first tick after the hub started has looked

# The Haiku call: (prompt, timeout) -> the answer's text. Tests replace it.
RUN = None
# The Slack lookup: (names) -> {name: id, or "" when Slack does not know it}.
# Raises LookupFailed when it cannot ask. Tests replace it.
LOOKUP = None


class LookupFailed(Exception):
    """The Slack connection could not be asked (try again later)."""


class Refused(route.Refused):
    """A change an agent asked for that cannot be made."""


class Changed(Refused):
    """The setup file changed since a new text for it was made from it.
    ``carried``: what changed is an older per-source table carried into the
    routing table (``apply_new_first``), which is a write."""
    carried = False


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] outside setup file: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Paths and small files
# ---------------------------------------------------------------------------

def setup_path() -> Path:
    return outside.root() / SETUP_NAME


def now_path() -> Path:
    return outside.root() / NOW_NAME


def _state_path() -> Path:
    return outside.root() / STATE_NAME


def _ids_path() -> Path:
    return outside.root() / IDS_NAME


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def load_state() -> dict:
    d = _read_json(_state_path())
    probs = [p for p in d.get("problems") or []
             if isinstance(p, dict) and isinstance(p.get("text"), str)]
    return {"mtime": route._num(d.get("mtime")), "appliedHash": str(d.get("appliedHash") or ""),
            "problems": probs, "failed": str(d.get("failed") or ""),
            "failedAll": bool(d.get("failedAll")),
            "appliedLines": d.get("appliedLines") if isinstance(d.get("appliedLines"), dict) else {},
            "lastTry": route._num(d.get("lastTry")), "lastApply": route._num(d.get("lastApply")),
            "changes": [c for c in d.get("changes") or [] if isinstance(c, str)],
            "applies": [a for a in d.get("applies") or [] if isinstance(a, dict)][-APPLIES_KEPT:]}


def _save_state(st: dict) -> None:
    durable.write_text(_state_path(), json.dumps(st, indent=1, ensure_ascii=False))


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return None


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


# ---------------------------------------------------------------------------
# Channel names and ids
# ---------------------------------------------------------------------------

_CHANNEL_ID = re.compile(r"[CG][A-Z0-9]{8,}")
_CHANNEL_NAME = re.compile(r"[a-z0-9][a-z0-9._\-]{0,79}")


def channel_id(value: str) -> str:
    """A channel as an id (upper case), or "" when it is not one. As #9's
    ``sources/slack.channel_id``: a C or G and at least 8 letters and
    digits, a digit among them, in any case."""
    v = str(value or "").strip().lstrip("#").strip().upper()
    return v if _CHANNEL_ID.fullmatch(v) and re.search(r"\d", v) else ""


def channel_name(value: str) -> str:
    """A channel as a name ("#Made-Up" -> "made-up"), or "" when it is an id
    or not a channel name."""
    if channel_id(value):
        return ""
    v = str(value or "").strip().lstrip("#").strip().lower()
    return v if _CHANNEL_NAME.fullmatch(v) else ""


def _known_ids() -> dict[str, str]:
    """{channel name: id} the hub knows: found by lookups, learned from the
    messages routed, and #9's Slack memory when it has one."""
    out: dict[str, str] = {}
    for sid, cfg in outside.load_config().items():
        if (cfg.get("type") or "") != "slack":
            continue
        try:
            for cid, name in route._load_memory(sid)["channelNames"].items():
                if channel_id(cid) and channel_name(name):
                    out[channel_name(name)] = channel_id(cid)
            mem = outside._load_state(sid)["memory"]
            for name, cid in (mem.get("channelIds") or {}).items():
                if channel_id(cid) and channel_name(name):
                    out[channel_name(name)] = channel_id(cid)
        except Exception:
            continue
    for name, cid in (_read_json(_ids_path()).get("names") or {}).items():
        if isinstance(name, str) and isinstance(cid, str) and channel_id(cid) and channel_name(name):
            out[channel_name(name)] = channel_id(cid)
    return out


def _names_by_id() -> dict[str, str]:
    return {cid: name for name, cid in _known_ids().items()}


def _remember_ids(found: dict[str, str]) -> None:
    found = {n: channel_id(c) for n, c in found.items() if channel_name(n) and channel_id(c)}
    if not found:
        return
    with _LOCK:
        d = _read_json(_ids_path())
        names = d.get("names") if isinstance(d.get("names"), dict) else {}
        names.update(found)
        durable.write_text(_ids_path(), json.dumps({"names": names}, indent=1, ensure_ascii=False))


def _slack_lookup(names: list[str]) -> dict[str, str]:
    """{name: id or ""} through the first Slack source's reader (Claude's
    Slack connection, ``slack_search_channels``). Raises LookupFailed."""
    from sources.slack_connector import PAGE_SIZE, Query, ReadError
    sid = next((s for s, c in outside.load_config().items() if (c.get("type") or "") == "slack"), None)
    if sid is None:
        raise LookupFailed("the hub has no Slack source to look channels up with")
    cfg = outside.source_config(sid) or {}
    src = outside.instance(sid, cfg)
    if src is None or not hasattr(src, "_reader"):
        raise LookupFailed("the hub has no Slack reader")
    queries = [Query(f"find-channel:{n}", "channels", {
        "keywords": [n], "natural_language_query": "",
        "channel_types": "public_channel,private_channel",
        "limit": PAGE_SIZE, "response_format": "detailed"}) for n in names]
    try:
        reader = src._reader(dict(cfg.get("settings") or {}), outside._load_state(sid)["memory"])
        got = reader.read(queries)
    except ReadError as e:
        raise LookupFailed(f"the Slack connection did not answer: {str(e)[:200]}") from None
    except Exception as e:
        raise LookupFailed(f"the Slack lookup failed: {str(e)[:200]}") from None
    out = {}
    for n, q in zip(names, queries):
        found = (got.facts.get(q.label) or {}).get("channels") or []
        hit = next((c for c in found if str(c.get("name", "")).lower() == n and not c.get("archived")), None)
        hit = hit or next((c for c in found if str(c.get("name", "")).lower() == n), None)
        out[n] = channel_id(hit["id"]) if hit else ""
    return out


def resolve_channels(values: list[str], lookup: bool = True,
                     remember: bool = True) -> tuple[dict[str, str], list[str], str]:
    """``({value as written: id}, [names Slack does not know], failure)``
    for channels written by name or id. Names not known yet are looked up
    in Slack in one go; ``failure`` is why the lookup could not be made.
    With ``remember`` the ids found are kept in channel-ids.json."""
    known = _known_ids()
    out, unknown = {}, []
    for v in values:
        cid = channel_id(v)
        if cid:
            out[v] = cid
            continue
        name = channel_name(v)
        if name in known:
            out[v] = known[name]
        elif name and name not in unknown:
            unknown.append(name)
    missing, failure = [], ""
    if unknown and lookup:
        try:
            got = (LOOKUP or _slack_lookup)(unknown)
        except LookupFailed as e:
            failure = str(e)
            got = {}
        if remember:
            _remember_ids(got)
        if not failure:
            missing = [n for n in unknown if not channel_id(got.get(n) or "")]
        for v in values:
            n = channel_name(v)
            if n in got and channel_id(got[n]):
                out[v] = channel_id(got[n])
    elif unknown:
        missing = unknown
    return out, missing, failure


# ---------------------------------------------------------------------------
# The file's text: the problems part, sections, the plain form
# ---------------------------------------------------------------------------

_PROBLEMS = re.compile(r"[ \t]*<!--\s*problems\b.*?<!--\s*end of problems\s*-->[ \t]*\n?", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def without_problems(text: str) -> str:
    return _PROBLEMS.sub("", (text or "").replace("\r\n", "\n"), count=1)


def text_hash(text: str) -> str:
    """The hash of what people set: the problems part left out, so the
    hub's rewrite of it is not a new save."""
    lines = [line.rstrip() for line in without_problems(text).strip().split("\n")]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def with_problems(text: str, problems: list[str]) -> str:
    """``text`` with its problems part set to ``problems`` (put under the
    title when the part was taken out)."""
    lines = [f"- {p}" for p in problems] if problems else [NO_PROBLEMS]
    block = PROBLEMS_START + "\n" + "\n".join(lines) + "\n" + PROBLEMS_END + "\n"
    text = (text or "").replace("\r\n", "\n")
    if _PROBLEMS.search(text):
        return _PROBLEMS.sub(lambda _m: block, text, count=1)
    m = re.match(r"(#[^#\n][^\n]*\n)(\s*\n)?", text)
    if m:
        return text[:m.end(1)] + "\n" + block + "\n" + text[m.end():]
    return block + "\n" + text


def split_sections(text: str) -> tuple[str, list[dict]]:
    """``(the part before the first section, [{heading, lines, text}])``.
    The problems part and comments are left out."""
    body = _COMMENT.sub("", without_problems(text))
    head, sections, cur = [], [], None
    for line in body.split("\n"):
        m = re.match(r"^##(?!#)\s*(.*?)\s*#*\s*$", line)
        if m:
            cur = {"heading": m.group(1).strip(), "lines": []}
            sections.append(cur)
        elif cur is None:
            head.append(line)
        else:
            cur["lines"].append(line)
    for s in sections:
        while s["lines"] and not s["lines"][-1].strip():
            s["lines"].pop()
        s["text"] = "## " + s["heading"] + "\n" + "\n".join(s["lines"])
    return "\n".join(head), sections


def name_key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


def _values(text: str) -> list[str]:
    """A plain line's values: comma separated; "none" or "-" for none."""
    text = text.strip()
    if text.lower() in ("", "-", "none", "(none)"):
        return []
    return [v.strip() for v in text.split(",") if v.strip()]


_LABEL = re.compile(r"^\s*(?:[-*]\s+)?([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$")
_JIRA_PART = re.compile(r"^(epics?|components?|projects?)\s+(\S.*)$", re.I)
_JIRA_KEYS = {"epic": "epics", "component": "components", "project": "projects"}


def read_plain_section(lines: list[str]) -> dict | None:
    """A section in the plain form, read in code: ``{topic, channels,
    jira: {epics, components, projects}, items}``, or None when any line is
    not in that form (Haiku reads it then). A line left out is empty."""
    out = {"topic": "", "channels": [], "jira": {"epics": [], "components": [], "projects": []},
           "items": [], "not": []}
    seen = set()
    for line in lines:
        if not line.strip():
            continue
        m = _LABEL.match(line)
        if not m:
            return None
        label, value = m.group(1).strip().lower(), m.group(2)
        if label in seen:
            return None
        seen.add(label)
        if label == "topic":
            out["topic"] = value.strip()
        elif label in ("slack channels", "slack", "channels"):
            vals = [v for x in _values(value) for v in x.split()]
            if any(not (channel_id(v) or channel_name(v)) for v in vals):
                return None
            out["channels"] = vals
        elif label == "jira":
            for part in _values(value):
                pm = _JIRA_PART.match(part)
                if not pm:
                    return None
                out["jira"][_JIRA_KEYS[pm.group(1).lower().rstrip("s")]].append(pm.group(2).strip())
        elif label in ("jira epics", "jira components", "jira projects"):
            out["jira"][label.split()[1]] = _values(value)
        elif label in ("work items", "items"):
            out["items"] = _values(value)
        elif label == "not":
            out["not"] = _values(value)
        else:
            return None
    return out


# The Sources section's two lines that are not a source (#409), with
# today's behaviour as their defaults: how often every source is read, and
# whether a stopped PO is started for an outside message.
EVERY_LABEL = "Read every"
START_LABEL = "Start a stopped PO for an outside message"
EVERY_KEYS = ("read every", "check every")
START_KEYS = ("start a stopped po for an outside message", "start a stopped po",
              "start a stopped po for outside messages")
EVERY_DEFAULT = (outside.DEFAULTS["fastEvery"], outside.DEFAULTS["slowEvery"])
START_DEFAULT = outside.OPTION_DEFAULTS["startStoppedPo"]
_MIN = r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)"
_EVERY_TWO = re.compile(rf"^{_MIN}\s+in\s+working\s+hours\s*[,;]?\s*(?:and\s+)?{_MIN}\s+otherwise\.?$", re.I)
_EVERY_ONE = re.compile(rf"^{_MIN}\.?$", re.I)
YES_NO = {"yes": True, "no": False}


def _num_text(v: float) -> str:
    return str(int(v)) if float(v) == int(v) else str(v)


def _minutes_text(v: float) -> str:
    return f"{_num_text(v)} minute{'' if float(v) == 1 else 's'}"


def every_text(fast: float, slow: float) -> str:
    """``1 minute in working hours, 15 minutes otherwise``."""
    return f"{_minutes_text(fast)} in working hours, {_minutes_text(slow)} otherwise"


def read_every(value: str) -> tuple[float, float] | None:
    """``(working hours, otherwise)`` minutes from a Read every value, or None."""
    v = " ".join(str(value or "").split())
    m = _EVERY_TWO.match(v)
    if m:
        nums = (float(m.group(1)), float(m.group(2)))
    else:
        m = _EVERY_ONE.match(v)
        if not m:
            return None
        nums = (float(m.group(1)),) * 2
    return tuple(int(n) if n == int(n) else n for n in nums)


def _option_line(label: str, value: str) -> dict | None:
    """A Sources line that is one of the hub-wide settings: ``{option,
    name, value}`` (``value`` None when it cannot be read, with ``bad``),
    else None."""
    key = " ".join(label.split()).lower()
    if key in EVERY_KEYS:
        got = read_every(value)
        return {"option": "every", "name": EVERY_LABEL, "value": got,
                **({} if got else {"bad": f"Sources: {EVERY_LABEL}: \"{value.strip()[:80]}\" is not a "
                                          f"time such as \"{every_text(*EVERY_DEFAULT)}\""})}
    if key in START_KEYS:
        word = " ".join(value.split()).lower().rstrip(".")
        got = YES_NO.get(word)
        return {"option": "start", "name": START_LABEL, "value": got,
                **({} if got is not None else {"bad": f"Sources: {START_LABEL}: \"{value.strip()[:80]}\" "
                                                      f"is not yes or no"})}
    return None


def option_lines(cfgs: dict | None = None, opts: dict | None = None) -> list[str]:
    """The two hub-wide lines as the hub writes them, from today's config."""
    cfgs = outside.load_config() if cfgs is None else cfgs
    opts = outside.load_options() if opts is None else opts
    first = next(iter(cfgs.values()), None) or {}
    fast = first.get("fastEvery", EVERY_DEFAULT[0])
    slow = first.get("slowEvery", EVERY_DEFAULT[1])
    return [f"- {EVERY_LABEL}: {every_text(fast, slow)}",
            f"- {START_LABEL}: {'yes' if opts.get('startStoppedPo', START_DEFAULT) else 'no'}"]


def read_plain_sources(lines: list[str]) -> list[dict] | None:
    """The Sources section in the plain form: ``[{name, word}]`` (and the
    hub-wide lines as ``{option, name, value}``), or None."""
    out = []
    for line in lines:
        if not line.strip():
            continue
        m = _LABEL.match(line)
        if not m:
            return None
        opt = _option_line(m.group(1), m.group(2))
        if opt is not None:
            out.append(opt)
            continue
        word = " ".join(m.group(2).split()).lower()
        if word not in MODE_WORDS:
            return None
        out.append({"name": m.group(1).strip(), "word": word})
    return out


# ---------------------------------------------------------------------------
# Haiku
# ---------------------------------------------------------------------------

PROMPT = """You read part of a setup file a person wrote by hand. Answer with JSON only, no other words.

Each section starts with a line "## <heading>". For a section headed "Sources", list each source the person
names and the setting they chose: "on", "off", "pause" or "decide only"; also "readEvery": the minutes between
reads they wrote, [in working hours, otherwise] (null when they say nothing about it), and "startStoppedPo":
"yes" or "no", whether they want a stopped PO started for an outside message ("" when they say nothing).
For every other section give:
- "heading": the heading exactly as written, without the ##
- "topic": what the person says the initiative covers, copied from their words without adding any ("" when they say nothing)
- "slackChannels": the Slack channels named, each exactly as written, without the #
- "jiraEpics", "jiraComponents", "jiraProjects": the Jira epics, components and projects named, as written
- "workItems": the tickets (like ABC-12), epics (write "epic ABC-1") and pull requests (like repo#123 or
  "repo 123") named as work items, each as written
- "notItems": the work items the person says must not be routed to this initiative (to leave out), as written
Copy every name and key exactly as the person wrote it. Never add one they did not write. Leave a list
empty when the section names none.

Answer in this shape:
{"sources": [{"name": "Slack", "setting": "on"}], "readEvery": null, "startStoppedPo": "",
 "sections": [{"heading": "...", "topic": "...", "slackChannels": [], "jiraEpics": [], "jiraComponents": [],
   "jiraProjects": [], "workItems": [], "notItems": []}]}

The sections:
<<<
%s
>>>
"""


class HaikuFailed(Exception):
    pass


def _ask_haiku(sections: list[dict]) -> dict:
    """Haiku's reading of ``sections``. Raises HaikuFailed."""
    import outside_model
    run = RUN or outside_model.RUN
    prompt = PROMPT % "\n\n".join(s["text"] for s in sections)
    try:
        answer = run(prompt, HAIKU_TIMEOUT_S)
    except Exception as e:
        raise HaikuFailed(f"Haiku did not answer ({type(e).__name__}: {str(e)[:150]})") from None
    text = (answer or "").strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        got = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        got = None
    if not isinstance(got, dict):
        raise HaikuFailed("Haiku's answer was not the JSON asked for")
    return got


def _in_text(value: str, text: str) -> bool:
    """Whether ``value`` is written in ``text`` as a whole name: "kite" is
    not in "#kite-quotes", "ABC-1" is not in "ABC-12". Case and spaces do not
    count; a # before it may be there or not; a full stop after it ends a
    sentence, so it may follow."""
    v = " ".join(str(value or "").split()).casefold().lstrip("#")
    if not v:
        return False
    pat = r"(?<![\w.#-])#?" + re.escape(v).replace(r"\ ", r"\s+") + r"(?![\w#-]|\.\w)"
    return re.search(pat, " ".join(text.split()).casefold()) is not None


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


# The words a source's line must hold for each setting Haiku may read in it.
SETTING_WORDS = {"on": ("on",), "off": ("off",), "pause": ("pause", "paused"), "paused": ("pause", "paused"),
                 "decide only": ("decide",), "decide": ("decide",)}


def _setting_written(name: str, word: str, text: str) -> bool:
    """Whether a line of ``text`` that names the source ``name`` also holds
    the word of the setting Haiku read (on, off, pause, decide)."""
    lines = [ln for ln in text.splitlines() if _in_text(name, ln)]
    return any(_in_text(w, ln) for ln in lines for w in SETTING_WORDS.get(word, ()))


def _str_list(v) -> list[str]:
    return [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()] \
        if isinstance(v, list) else []


def _check_haiku_section(got: dict, section: dict) -> tuple[dict | None, list[str]]:
    """Haiku's reading of one section, every value checked against the
    section's text: ``(reading, [problems])``."""
    text = section["text"]
    out = {"topic": str(got.get("topic") or "").strip(), "channels": _str_list(got.get("slackChannels")),
           "jira": {"epics": _str_list(got.get("jiraEpics")),
                    "components": _str_list(got.get("jiraComponents")),
                    "projects": _str_list(got.get("jiraProjects"))},
           "items": _str_list(got.get("workItems")), "not": _str_list(got.get("notItems"))}
    made_up = [v for v in out["channels"] + out["items"] + out["not"] + sum(out["jira"].values(), [])
               if not _in_text(v, text) and not _in_text(re.sub(r"(?i)^epic\s+", "", v), text)]
    # The topic may leave words out, never add one: each of its words is in the section.
    if out["topic"] and not _words(out["topic"]) <= _words(text):
        made_up.insert(0, f"the topic \"{out['topic'][:80]}\"")
    if made_up:
        return None, [f"Haiku read {', '.join(made_up[:3])} in it, which the section does not say; "
                      f"nothing in it changed. Write it more plainly."]
    return out, []


# ---------------------------------------------------------------------------
# Writing the file from the table
# ---------------------------------------------------------------------------

def _mode_word(cfg: dict) -> str:
    if cfg.get("paused") and cfg.get("mode") != "off":
        return "pause"
    return {"on": "on", "off": "off", "decide": "decide only"}.get(cfg.get("mode"), "off")


def _row_lines(row: dict, kinds: set[str], names: dict[str, str]) -> list[str]:
    """A row in the plain form: topic, places, the work items people set."""
    lines = ["Topic: " + " ".join(str(row.get(route.TOPIC) or "").split())]
    places = row.get(route.PLACES) or {}
    if "slack" in kinds or (places.get("slack") or {}).get(route.CHANNELS):
        chans = []
        for c in (places.get("slack") or {}).get(route.CHANNELS) or []:
            cid = channel_id(c)
            chans.append("#" + (names.get(cid) or (cid if cid else route.norm(c))))
        lines.append("Slack channels: " + ", ".join(chans))
    jira = places.get("jira") or {}
    if "jira" in kinds or any(jira.values()):
        parts = [f"{w} {v}" for w, k in (("epic", "epics"), ("component", "components"),
                                          ("project", "projects"))
                 for v in jira.get(k) or []]
        lines.append("Jira: " + ", ".join(parts))
    items = [route.item_text(i) for i in row.get(route.ITEMS) or [] if i.get("from") in route.PEOPLE_SET]
    lines.append("Work items: " + ", ".join(items))
    if row.get(route.NOT):
        lines.append("Not: " + ", ".join(route.item_text(i) for i in row[route.NOT]))
    return lines


def _has_people_set(row: dict) -> bool:
    return bool(row.get(route.TOPIC) or any((v or {}) and any((v or {}).values())
                                           for v in (row.get(route.PLACES) or {}).values())
                or any(i.get("from") in route.PEOPLE_SET for i in row.get(route.ITEMS) or [])
                or bool(row.get(route.NOT)))


INTRO = """Change this file to set which outside messages the hub reads and which initiative each goes to.
The hub applies it each time you save. Each initiative has a section headed with its name (`## Name`);
`Work items:` lists the tickets and pull requests you set (the ones the hub adds by itself are not listed);
`Not:` lists the ones to take out of that initiative, which the hub then does not add back to it.
Under Sources, `Read every:` is how often each source is read and `Start a stopped PO for an outside message:`
(yes or no) whether a PO that is stopped is started for one; with no, its messages wait until it runs.
Write loosely if you like: Haiku reads what the hub cannot. What the hub routes from now: routing-now.md, next to this file."""


def first_text() -> str:
    """The file as today's table and sources stand: every source and its
    setting, a section per initiative that has something set."""
    names = route.initiatives()
    rows = route._load_one()["rows"]
    kinds = {t["kind"] for t in route.place_types()}
    by_id = _names_by_id()
    out = ["# Outside sources setup", "", PROBLEMS_START, NO_PROBLEMS, PROBLEMS_END, "", INTRO, ""]
    empty = [n for pid, n in names.items() if not _has_people_set(rows.get(pid) or {})]
    if empty:
        out += [f"<!-- Initiatives with nothing set yet (add a section to route to one): {', '.join(empty)} -->", ""]
    out += ["## Sources"]
    cfgs = outside.load_config()
    out += [f"- {outside.label_of(c)}: {_mode_word(c)}" for c in cfgs.values()] or \
        ["<!-- none yet: add a line such as \"- Slack: on\" -->"]
    out += option_lines(cfgs)
    for pid, name in names.items():
        row = rows.get(pid)
        if row and _has_people_set(row):
            out += ["", f"## {name}"] + _row_lines(row, kinds, by_id)
    return "\n".join(out) + "\n"


def section_text(project_id: str) -> str:
    """The plain form of one initiative's section, from its row now."""
    names = route.initiatives()
    row = route._load_one()["rows"].get(project_id) or route._empty_row()
    kinds = {t["kind"] for t in route.place_types()}
    return "\n".join([f"## {names.get(project_id, project_id)}"]
                     + _row_lines(row, kinds, _names_by_id())) + "\n"


def _section_span(text: str, heading_key: str) -> tuple[list[str], int | None, int | None]:
    """``(the text's lines, the heading's line, the line after the
    section)``; the two are None when there is no such section."""
    lines = text.replace("\r\n", "\n").split("\n")
    start = end = None
    in_comment = False
    for i, line in enumerate(lines):
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        m = re.match(r"^##(?!#)\s*(.*?)\s*#*\s*$", line)
        if m:
            if start is not None and end is None:
                end = i
            if start is None and name_key(m.group(1)) == heading_key:
                start = i
    if start is None:
        return lines, None, None
    end = len(lines) if end is None else end
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return lines, start, end


def _replace_section(text: str, heading_key: str, new: str) -> str:
    """``text`` with the section whose heading is ``heading_key`` replaced
    by ``new`` (added at the end when there is none)."""
    lines, start, end = _section_span(text, heading_key)
    if start is None:
        return text.rstrip("\n") + "\n\n" + new
    return "\n".join(lines[:start] + new.rstrip("\n").split("\n") + lines[end:]).rstrip("\n") + "\n"


# The lines of a section in the plain form, by what they set.
LINE_GROUPS = {"topic": ("topic",), "slack": ("slack channels", "slack", "channels"),
               "jira": ("jira", "jira epics", "jira components", "jira projects"),
               "items": ("work items", "items"), "not": ("not",)}


def _group_of(line: str) -> str | None:
    m = _LABEL.match(line)
    if not m:
        return None
    label = m.group(1).strip().lower()
    return next((g for g, labels in LINE_GROUPS.items() if label in labels), None)


def changed_groups(change: dict) -> set[str]:
    """Which lines of a section a row change touches."""
    out = set()
    if route.TOPIC in change:
        out.add("topic")
    out |= {k for k in (change.get(route.PLACES) or {}) if k in LINE_GROUPS}
    if any(k in change for k in (route.ITEMS, route.ADD_ITEMS, route.REMOVE_ITEMS)):
        out.add("items")
    # An item a person adds comes off the Not list, so that line may change too.
    if any(k in change for k in (route.ITEMS, route.ADD_ITEMS, route.NOT)):
        out.add("not")
    return out


def _merge_lines(old: list[str], new: list[str], groups: set[str]) -> list[str]:
    """A plain section's lines (without its heading) with only the lines of
    ``groups`` taken from ``new``; every other line stays as written."""
    fresh = {g: ln for ln in new if (g := _group_of(ln)) in groups}
    out, done = [], set()
    for ln in old:
        g = _group_of(ln)
        if g in groups:
            if g not in done and g in fresh:
                out.append(fresh[g])
            done.add(g)
            continue
        out.append(ln)
    last = max((i for i, ln in enumerate(out) if _group_of(ln)), default=len(out) - 1)
    missing = [fresh[g] for g in LINE_GROUPS if g in fresh and g not in done]
    return out[:last + 1] + missing + out[last + 1:]


# ---------------------------------------------------------------------------
# Applying a text
# ---------------------------------------------------------------------------

def _row_change(before: dict, after: dict, by_id: dict) -> list[str]:
    """What changed in a row, in a few words."""
    out = []
    if before.get(route.TOPIC) != after.get(route.TOPIC):
        out.append("topic changed")
    for kind in sorted(set(before.get(route.PLACES) or {}) | set(after.get(route.PLACES) or {})):
        b = (before.get(route.PLACES) or {}).get(kind) or {}
        a = (after.get(route.PLACES) or {}).get(kind) or {}
        for col in sorted(set(b) | set(a)):
            bs, as_ = list(b.get(col) or []), list(a.get(col) or [])
            show = (lambda c: "#" + by_id.get(channel_id(c), route.norm(c))) if col == route.CHANNELS else str
            added = [show(x) for x in as_ if x not in bs]
            gone = [show(x) for x in bs if x not in as_]
            label = f"{sources.TYPES[kind].label if kind in sources.TYPES else kind} {col}"
            if added:
                out.append(f"{label}: {', '.join(added)} added")
            if gone:
                out.append(f"{label}: {', '.join(gone)} taken out")
    bi = {i["key"] for i in before.get(route.ITEMS) or []}
    ai = {i["key"] for i in after.get(route.ITEMS) or []}
    if ai - bi:
        out.append("work items " + ", ".join(sorted(ai - bi)) + " added")
    if bi - ai:
        out.append("work items " + ", ".join(sorted(bi - ai)) + " taken out")
    bn, an = route.not_keys(before), route.not_keys(after)
    if an - bn:
        out.append("Not: " + ", ".join(sorted(an - bn)) + " added (the hub does not add them back)")
    if bn - an:
        out.append("Not: " + ", ".join(sorted(bn - an)) + " taken off")
    return out


def _source_for(name: str, cfgs: dict) -> tuple[str | None, str | None]:
    """``(source id, type)`` a Sources line names: a configured source (its
    id, its label), else a type the hub can read (not configured yet)."""
    k = name_key(name)
    for sid, c in cfgs.items():
        if k in (sid, name_key(outside.label_of(c))):
            return sid, c.get("type")
    for kind, make in sources.TYPES.items():
        if k in (kind, name_key(getattr(make, "label", "") or kind)):
            return None, kind
    return None, None


def _apply_options(opts: list[dict], plain: bool, check_only: bool, problems: list[str],
                   changes: list[str]) -> None:
    """The hub-wide Sources lines: the read interval set on every source,
    and whether a stopped PO is started. In a plain section a line left out
    means its default (today's behaviour); a loosely written section changes
    only what Haiku read in it."""
    got: dict = {}
    for x in opts:
        if x.get("bad"):
            problems.append(x["bad"])
        elif x["option"] in got:
            problems.append(f"Sources: two {x['name']} lines; the second was left out")
        else:
            got[x["option"]] = x["value"]
    bad = {x["option"] for x in opts if x.get("bad")}
    if plain:
        if "every" not in bad:
            got.setdefault("every", EVERY_DEFAULT)
        if "start" not in bad:
            got.setdefault("start", START_DEFAULT)
    if "every" in got:
        fast, slow = got["every"]
        try:
            fv = outside._frame_value("fastEvery", fast)
            sv = outside._frame_value("slowEvery", slow)
        except outside.Refused as e:
            problems.append(f"Sources: {EVERY_LABEL}: {e}")
        else:
            cfgs = outside.load_config()
            was = [(c.get("fastEvery"), c.get("slowEvery")) for c in cfgs.values()]
            if any(w != (fv, sv) for w in was):
                if check_only:
                    for c in cfgs.values():
                        c.update(fastEvery=fv, slowEvery=sv)
                    outside.preview_config(cfgs)
                else:
                    for sid in cfgs:
                        outside.update_source(sid, frame={"fastEvery": fv, "slowEvery": sv})
                olds = sorted({every_text(*w) for w in was})
                changes.append(f"Sources: {EVERY_LABEL.lower()} {' / '.join(olds)} → {every_text(fv, sv)}")
    if "start" in got:
        was = outside.load_options()["startStoppedPo"]
        if was != got["start"]:
            outside.set_options(startStoppedPo=got["start"])
            changes.append(f"Sources: {START_LABEL.lower()}: {'yes' if was else 'no'} → "
                           f"{'yes' if got['start'] else 'no'}")


def _apply_sources(lines: list[dict], check_only: bool, problems: list[str], changes: list[str],
                   plain: bool = True) -> None:
    opts = [x for x in lines if x.get("option")]
    lines = [x for x in lines if not x.get("option")]
    for x in lines:
        name, word = x["name"], " ".join(str(x.get("word") or "").split()).lower()
        if word not in MODE_WORDS:
            problems.append(f"Sources: {name}: \"{word}\" is not on, off, pause or decide only")
            continue
        cfgs = outside.load_config()
        sid, kind = _source_for(name, cfgs)
        if kind is None:
            problems.append(f"Sources: the hub has no source called {name} "
                            f"(it can read {', '.join(getattr(m, 'label', k) for k, m in sources.TYPES.items())})")
            continue
        try:
            # A preview changes only its own copy of the config (outside.preview_config).
            if sid is None:
                if check_only:
                    sid = kind
                    preview = outside.load_config()
                    preview[sid] = {**outside.DEFAULTS, "type": kind, "mode": "off", "paused": False,
                                    "settings": {}}
                    outside.preview_config(preview)
                else:
                    sid = outside.add_source(kind, built=True)["id"]
                changes.append(f"Sources: {name} added")
            cfg = outside.load_config()[sid]
            mode, paused = MODE_WORDS[word]
            if mode is None:
                mode = cfg["mode"] if cfg["mode"] != "off" else "on"
            if (cfg["mode"], bool(cfg.get("paused"))) == (mode, paused):
                continue
            if check_only:
                preview = outside.load_config()
                preview[sid].update(mode=mode, paused=paused)
                outside.preview_config(preview)
            else:
                outside.update_source(sid, mode=mode, paused=paused)
            changes.append(f"Sources: {outside.label_of(cfg)} {_mode_word(cfg)} → {word}")
        except (outside.Refused, ValueError) as e:
            problems.append(f"Sources: {name}: {e}")
    # After the sources, so a source added by this save is read as often too.
    _apply_options(opts, plain, check_only, problems, changes)


def _change_for(sec: dict, row: dict, kinds: set[str], ids: dict[str, str]) -> tuple[dict | None, list[str]]:
    """The ``set_initiative`` change a section asks for, or its problems."""
    probs = []
    change: dict = {route.TOPIC: sec["topic"]}
    if len(sec["topic"]) > route.TEXT_CHARS:
        probs.append(f"the topic is longer than {route.TEXT_CHARS} characters")
    places: dict = {}
    if sec["channels"]:
        if "slack" not in kinds:
            probs.append("it lists Slack channels, but the hub has no Slack source (add \"- Slack: on\" "
                         "under Sources)")
        else:
            places["slack"] = {route.CHANNELS: list(dict.fromkeys(ids[c] for c in sec["channels"] if c in ids))}
    elif "slack" in kinds:
        places["slack"] = {route.CHANNELS: []}
    jira = sec["jira"]
    if any(jira.values()):
        if "jira" not in kinds:
            probs.append("it lists Jira places, but the hub has no Jira source (add \"- Jira: on\" under Sources)")
        else:
            places["jira"] = {"epics": [v.upper() for v in jira["epics"]],
                              "components": list(jira["components"]),
                              "projects": [v.upper() for v in jira["projects"]]}
    elif "jira" in kinds:
        places["jira"] = {"epics": [], "components": [], "projects": []}
    if places:
        change[route.PLACES] = places
    items, nots = [], []
    for vals, out in ((sec["items"], items), (sec.get("not") or [], nots)):
        for v in vals:
            try:
                out.append(route.parse_item(v))
            except route.Refused as e:
                probs.append(str(e))
    listed = {k for _kind, k in items}
    not_listed = {k for _kind, k in nots}
    both = sorted(listed & not_listed)
    if both:
        probs.append(f"{', '.join(both)} {'is' if len(both) == 1 else 'are'} on both the Work items line and "
                     f"the Not line; take {'it' if len(both) == 1 else 'them'} off one of them")
    kept = [(i["kind"], i["key"]) for i in row.get(route.ITEMS) or []
            if i.get("from") not in route.PEOPLE_SET and i["key"] not in listed | not_listed]
    change[route.ITEMS] = [{"kind": k, "key": key} for k, key in items + kept]
    change[route.NOT] = [{"kind": k, "key": key} for k, key in nots]
    return (None if probs else change), probs


def _cleared(row: dict, kinds: set[str]) -> dict:
    change: dict = {route.TOPIC: "", route.NOT: [],
                    route.ITEMS: [{"kind": i["kind"], "key": i["key"]} for i in row.get(route.ITEMS) or []
                                  if i.get("from") not in route.PEOPLE_SET]}
    places = {}
    for t in route.place_types():
        places[t["kind"]] = {f.key: [] if f.kind == "list" else "" for f in t["fields"]}
    if places:
        change[route.PLACES] = places
    return change


def _haiku_options(got: dict, text: str, problems: list[str]) -> list[dict]:
    """The hub-wide settings Haiku read in a loosely written Sources
    section, each checked against its text: a number or a yes / no the
    section does not hold is a problem, and the setting is left as it is."""
    out = []
    every = got.get("readEvery")
    if isinstance(every, list) and len(every) == 2 and all(
            isinstance(n, (int, float)) and not isinstance(n, bool) for n in every):
        nums = tuple(int(n) if float(n) == int(n) else float(n) for n in every)
        if all(_num_text(n) in re.findall(r"\d+(?:\.\d+)?", text) for n in nums):
            out.append({"option": "every", "name": EVERY_LABEL, "value": nums})
        else:
            problems.append(f"Haiku read {EVERY_LABEL.lower()} as {every_text(*nums)}, which the section does "
                            f"not say; it was left as it is. Write it as \"- {EVERY_LABEL}: "
                            f"{every_text(*EVERY_DEFAULT)}\".")
    start = str(got.get("startStoppedPo") or "").strip().lower()
    if start in YES_NO:
        if start in _words(text):
            out.append({"option": "start", "name": START_LABEL, "value": YES_NO[start]})
        else:
            problems.append(f"Haiku read \"{start}\" for starting a stopped PO, which the section does not say; "
                            f"it was left as it is. Write it as \"- {START_LABEL}: yes\" (or no).")
    return out


def read_text(text: str) -> dict:
    """The file read, before anything is applied: ``{sources: [{name,
    word}] or None, sections: [{heading, reading or None, problems}],
    failed, sourceProblems}``. ``failed`` is why Haiku could not read it
    (then nothing may change); ``sourceProblems``, settings Haiku read in a
    loosely written Sources section that its lines do not say."""
    _head, secs = split_sections(text)
    src_lines, loose, out, src_problems, plain = None, [], [], [], True
    for s in secs:
        if name_key(s["heading"]) == SOURCES_HEADING:
            if src_lines is not None:
                out.append({"heading": s["heading"], "reading": None,
                            "problems": ["the file has two Sources sections; the second was left out"]})
                continue
            src_lines = read_plain_sources(s["lines"])
            if src_lines is None:
                loose.append(s)
                src_lines = []
                plain = False
                s["isSources"] = True
            continue
        reading = read_plain_section(s["lines"])
        entry = {"heading": s["heading"], "reading": reading, "problems": []}
        out.append(entry)
        if reading is None:
            loose.append(s)
            s["entry"] = entry
    failed = ""
    if loose:
        try:
            got = _ask_haiku(loose)
        except HaikuFailed as e:
            return {"sources": None, "sections": out, "failed": str(e), "sourceProblems": []}
        answers = {name_key(str(x.get("heading") or "")): x
                   for x in got.get("sections") or [] if isinstance(x, dict)}
        for s in loose:
            if s.get("isSources"):
                for x in got.get("sources") or []:
                    if not isinstance(x, dict):
                        continue
                    name, word = str(x.get("name") or "").strip(), str(x.get("setting") or "").strip()
                    word = " ".join(word.split()).lower()
                    if not name or not _in_text(name, s["text"]) or \
                            " ".join(name.split()).lower() in EVERY_KEYS + START_KEYS:
                        continue
                    if not _setting_written(name, word, s["text"]):
                        src_problems.append(f"Haiku read {name} as \"{word}\", which its line does not say; "
                                            f"{name} was left as it is. Write it as \"- {name}: on\" (or off, "
                                            f"pause, decide only).")
                        continue
                    src_lines.append({"name": name, "word": word})
                src_lines += _haiku_options(got, s["text"], src_problems)
                continue
            a = answers.get(name_key(s["heading"]))
            if a is None:
                s["entry"]["problems"].append("Haiku could not read this section; nothing in it changed. "
                                              "Write it more plainly.")
                continue
            s["entry"]["reading"], s["entry"]["problems"] = _check_haiku_section(a, s)
    return {"sources": src_lines, "sections": out, "failed": failed, "sourceProblems": src_problems,
            "sourcesPlain": plain}


def apply_text(text: str, by: dict | None = None, check_only: bool = False,
               now: float | None = None) -> dict:
    """Apply a whole setup file text (or, with ``check_only``, only say what
    it would do). Returns ``{problems: [{section, text}], changes: [str],
    failed: str, lookupFailed: str}``. ``failed``: Haiku could not read it,
    nothing changed. ``lookupFailed``: Slack could not be asked for some
    channels' ids; those sections kept their rows. With ``check_only``
    nothing is written: see the module's docstring."""
    now = time.time() if now is None else now
    by = by or SETUP_BY
    read = read_text(text)
    if read["failed"]:
        msg = (f"The hub could not read the file: {read['failed']}. Nothing changed; it tries again "
               f"on the next save or in 10 minutes.")
        return {"problems": [{"section": "", "text": msg}], "changes": [], "failed": read["failed"],
                "lookupFailed": ""}
    names = route.initiatives()
    by_key = {name_key(n): pid for pid, n in names.items()}
    problems: list[dict] = []
    changes: list[str] = []
    wanted: dict[str, dict] = {}         # project id -> its section
    unknown_heading = False
    for sec in read["sections"]:
        pid = by_key.get(name_key(sec["heading"]))
        if pid is None:
            unknown_heading = True
            problems.append({"section": sec["heading"],
                             "text": f"{sec['heading']}: there is no initiative of that name on this hub; "
                                     f"the section was left out"})
            continue
        if pid in wanted:
            problems.append({"section": sec["heading"],
                             "text": f"{sec['heading']}: the file has two sections for it; the second was left out"})
            continue
        sec["projectId"] = pid
        wanted[pid] = sec
        for p in sec["problems"]:
            problems.append({"section": sec["heading"], "text": f"{sec['heading']}: {p}"})
    # Channel ids, looked up before anything is locked (a lookup can take a while).
    every = [c for s in wanted.values() if s["reading"] for c in s["reading"]["channels"]]
    ids, missing, lookup_failed = resolve_channels(every, remember=not check_only)
    try:
        if check_only:
            # This thread works on copies of the sources' config and the table:
            # nothing is written and no other thread sees the preview.
            outside.preview_config(outside.load_config())
            outside.preview_options(outside.load_options())
            route.preview_table(True)
        with route._LOCK:
            src_problems: list[str] = list(read["sourceProblems"])
            if read["sources"] is not None:
                _apply_sources(read["sources"], check_only, src_problems, changes,
                               plain=read.get("sourcesPlain", True))
            problems += [{"section": "Sources", "text": p} for p in src_problems]
            kinds = {t["kind"] for t in route.place_types()}
            rows = route._load_one()["rows"]
            todo: list[tuple[str, dict]] = []
            for pid, sec in wanted.items():
                if sec["problems"] or sec["reading"] is None:
                    continue
                r = sec["reading"]
                bad = [c for c in r["channels"] if c not in ids]
                if bad:
                    if lookup_failed:
                        why = f"the channels' ids could not be looked up ({lookup_failed}); this section kept " \
                              f"its last routing and is tried again in 10 minutes"
                    else:
                        why = ", ".join("#" + channel_name(c) for c in bad) + \
                              (" is not a channel" if len(bad) == 1 else " are not channels") + \
                              " the Slack connection can see"
                    problems.append({"section": sec["heading"], "text": f"{sec['heading']}: {why}"})
                    continue
                change, probs = _change_for(r, rows.get(pid) or route._empty_row(), kinds, ids)
                if probs:
                    problems += [{"section": sec["heading"], "text": f"{sec['heading']}: {p}"} for p in probs]
                    continue
                todo.append((pid, change))
            if not unknown_heading:
                for pid in names:
                    if pid not in wanted and _has_people_set(rows.get(pid) or {}):
                        todo.append((pid, _cleared(rows[pid], kinds)))
            by_id = _names_by_id()
            refused: dict[str, str] = {}
            while todo:
                left = []
                for pid, change in todo:
                    try:
                        res = route.set_initiative(pid, change, by, now=now)
                    except route.Refused as e:
                        left.append((pid, change))
                        refused[pid] = str(e)
                        continue
                    refused.pop(pid, None)
                    if res["changed"]:
                        what = _row_change(res["before"], res["row"], by_id)
                        if pid not in wanted:
                            what = ["no section in the file, so what was set is cleared"] + what
                        changes += [f"{names[pid]}: {w}" for w in what]
                if len(left) == len(todo):
                    break
                todo = left
            for pid, why in refused.items():
                head = wanted[pid]["heading"] if pid in wanted else names[pid]
                problems.append({"section": head, "text": f"{head}: {why} This section kept its last routing."})
    finally:
        if check_only:
            outside.preview_config(None)
            outside.preview_options(None)
            route.preview_table(False)
    return {"problems": problems, "changes": changes, "failed": "", "lookupFailed": lookup_failed}


# ---------------------------------------------------------------------------
# The file: apply it, write its problems, the watcher
# ---------------------------------------------------------------------------

def write_first() -> str:
    """Write the file from today's table when there is none. Returns its text."""
    with _LOCK:
        p = setup_path()
        text = _read_text(p)
        if text is not None:
            return text
        text = first_text()
        _write_first_text(text)
    write_now()
    return text


def _write_first_text(text: str) -> None:
    """Write ``text`` (a ``first_text``) as the file, with ``_LOCK`` held."""
    p = setup_path()
    durable.write_text(p, text)
    st = load_state()
    # Written from the table, so it is what applies now.
    st.update(mtime=_mtime(p), appliedHash=text_hash(text), appliedLines=_lines_by_section(text),
              problems=[], failed="",
              lastApply=time.time())
    _save_state(st)
    _log("written from the routing table as it stands")


def apply_file(now: float | None = None, by: dict | None = None) -> dict | None:
    """Apply the file as it is now; write its problems part, routing-now.md
    and the state. None when an apply is running already."""
    if not _APPLYING.acquire(blocking=False):
        return None
    try:
        return _apply_file(now, by)
    finally:
        _APPLYING.release()


def apply_new_text(text: str, base_hash: str, by: dict | None = None,
                   now: float | None = None) -> dict:
    """Write ``text`` as the whole setup file and apply it at once, as
    ``by`` (the hub chat's Apply, #16). ``base_hash`` is the ``text_hash``
    of the file the text was made from: when the file changed since (a save,
    an agent's change), nothing is written and Changed is raised. Refused
    when an apply is running for longer than APPLY_WAIT_S. Returns what
    ``apply_file`` returns."""
    if not isinstance(text, str) or not text.strip() or len(text) > 200_000:
        raise Refused("the setup file must be text of at most 200,000 characters")
    if not _APPLYING.acquire(timeout=APPLY_WAIT_S):
        raise Refused("the setup file is being applied right now; try again in a minute")
    try:
        with _LOCK:
            cur = _read_text(setup_path())
            if cur is None or text_hash(cur) != base_hash:
                raise Changed("the setup file changed since this was made from it")
            # Keep the problems part where the hub writes it; the apply rewrites it.
            durable.write_text(setup_path(), with_problems(text.replace("\r\n", "\n"),
                                                           [x["text"] for x in load_state()["problems"]]))
        return _apply_file(now, by or HUB_CHAT_BY)
    finally:
        _APPLYING.release()


def apply_new_first(text: str, base_hash: str, by: dict | None = None,
                    now: float | None = None) -> dict:
    """``apply_new_text`` for a text made, when there was no file, from the
    text the file would be written with (Ask the hub's Apply, #410). With
    the apply lock held throughout (so no routing change made outside the
    file, which waits for it in ``section_change``, can land in between),
    and the file and table locks around the check and the writes: the text
    the file would be written with is made again without carrying a
    per-source table over; when it is not the one ``base_hash`` was made
    from, Changed is raised and nothing is written. When a per-source table
    would be carried into the table, that is done and Changed is raised
    (``carried``): a text made without those rows would clear them. Else
    the file is written from it, then as ``text``, and applied."""
    if not isinstance(text, str) or not text.strip() or len(text) > 200_000:
        raise Refused("the setup file must be text of at most 200,000 characters")
    if not _APPLYING.acquire(timeout=APPLY_WAIT_S):
        raise Refused("the setup file is being applied right now; try again in a minute")
    try:
        with _LOCK, route._LOCK:
            if _read_text(setup_path()) is None:
                with route.reading_only():
                    first = first_text()
                if text_hash(first) != base_hash:
                    raise Changed("the routing changed since this was made from it")
                first = first_text()        # carries a per-source table over, if one is there
                if text_hash(first) != base_hash:
                    e = Changed("an older per-source table was carried into the routing table")
                    e.carried = True
                    raise e
                _write_first_text(first)
            cur = _read_text(setup_path())
            if cur is None or text_hash(cur) != base_hash:
                raise Changed("the setup file changed since this was made from it")
            durable.write_text(setup_path(), with_problems(text.replace("\r\n", "\n"),
                                                           [x["text"] for x in load_state()["problems"]]))
        return _apply_file(now, by or HUB_CHAT_BY)
    finally:
        _APPLYING.release()


def _apply_file(now: float | None, by: dict | None) -> dict:
    """``apply_file``'s work, with the apply lock held."""
    now = time.time() if now is None else now
    p = setup_path()
    text = _read_text(p)
    if text is None:
        text = write_first()
    res = apply_text(text, by=by, now=now)
    h = text_hash(text)
    failed = res["failed"] or res["lookupFailed"]
    with _LOCK:
        st = load_state()
        before = st["problems"]
        st.update(lastTry=now, failed=failed, failedAll=bool(res["failed"]),
                  problems=res["problems"], changes=res["changes"][-50:])
        if res["changes"] or res["problems"] != before:
            # Kept here because the sources' changes are in no other history
            # (the routing table's changes are also in its own).
            st["applies"] = (st["applies"] + [{
                "at": now, "by": _who(by or SETUP_BY), "changes": res["changes"][-50:],
                "problems": [x["text"] for x in res["problems"]]}])[-APPLIES_KEPT:]
        if not res["failed"]:
            # A section with a problem kept its last good row as a whole:
            # none of its lines were applied, so its entry stays as it was.
            bad = {name_key(x.get("section") or "") for x in res["problems"]}
            lines = {k: (st["appliedLines"].get(k) or {}) if k in bad else v
                     for k, v in _lines_by_section(text).items()}
            st.update(appliedHash=h, appliedLines=lines, lastApply=now)
        cur = _read_text(p)
        if cur is not None and text_hash(cur) == h:
            new = with_problems(cur, [x["text"] for x in res["problems"]])
            if new != cur:
                durable.write_text(p, new)
            st["mtime"] = _mtime(p)
        _save_state(st)
    write_now(now)
    _log(f"applied by {_who(by or SETUP_BY)}: {len(res['changes'])} change(s), "
         f"{len(res['problems'])} problem(s)" + (f"; {failed}" if failed else ""))
    return res


def applying() -> bool:
    return _APPLYING.locked()


def save_text(text: str) -> None:
    """The panel's Save: the whole file, then an apply on a thread. A save
    that changes only the problems part (or nothing) applies nothing when
    the last apply went through with no problem."""
    if not isinstance(text, str) or len(text) > 200_000:
        raise Refused("the setup file must be text of at most 200,000 characters")
    text = text.replace("\r\n", "\n")
    with _LOCK:
        p = setup_path()
        durable.write_text(p, text)
        st = load_state()
        if text_hash(text) == st["appliedHash"] and not st["problems"] and not st["failed"]:
            st["mtime"] = _mtime(p)
            _save_state(st)
            return
    start_apply()


def start_apply() -> bool:
    if _APPLYING.locked():
        return False
    threading.Thread(target=_apply_quietly, daemon=True, name="outside-setup").start()
    return True


def _apply_quietly() -> None:
    try:
        apply_file()
    except Exception as e:
        _log(f"apply error: {str(e)[:200]}")


def tick(now: float | None = None, background: bool = True) -> None:
    """From the hub loop: write the file the first time; apply it after a
    save, once after the hub starts when needed, and again 10 minutes after
    a failed try; keep routing-now.md in step with routing.json."""
    now = time.time() if now is None else now
    p = setup_path()
    if not p.exists():
        # A hub with no source set up writes nothing (#409): the file comes
        # with the first source, or when the CEO opens it (panel()).
        if not outside.load_config():
            return
        write_first()
    if _mtime(route._one_file()) > _mtime(now_path()):
        write_now(now)
    if _APPLYING.locked():
        return
    st = load_state()
    m = _mtime(p)
    due = ""
    first = not _STARTED["checked"]
    _STARTED["checked"] = True
    if m != st["mtime"]:
        text = _read_text(p) or ""
        if text_hash(text) != st["appliedHash"]:
            due = "saved"
        else:
            with _LOCK:
                st2 = load_state()
                st2["mtime"] = m
                _save_state(st2)
    if not due and first:
        text = _read_text(p) or ""
        if text_hash(text) != st["appliedHash"]:
            due = "changed while the hub was stopped"
        elif st["problems"]:
            due = "the last apply had problems"
    if not due and st["failed"] and now - st["lastTry"] >= RETRY_S:
        due = "trying again after: " + st["failed"][:100]
    if not due:
        return
    _log(f"applying ({due})")
    if background:
        start_apply()
    else:
        apply_file(now)


def reset() -> None:
    """Forget that the hub has started (tests)."""
    _STARTED["checked"] = False


# ---------------------------------------------------------------------------
# Agents: their own section
# ---------------------------------------------------------------------------

def set_own(project_id: str, change: dict, by: dict, now: float | None = None) -> dict:
    """``ensemble_routing``'s set: the change to the caller's own row (Slack
    channels by name are looked up), then its section of the setup file is
    rewritten from the row. When the rest of the file was applied already,
    the new text counts as applied too, so no Haiku call follows."""
    change = dict(change or {})
    places = change.get(route.PLACES)
    if isinstance(places, dict) and isinstance(places.get("slack"), dict) \
            and isinstance(places["slack"].get(route.CHANNELS), list):
        vals = [str(v) for v in places["slack"][route.CHANNELS]]
        ids, missing, failure = resolve_channels(vals)
        if failure:
            raise Refused(f"the channels' ids could not be looked up: {failure}. Try again later, "
                          f"or give the channel ids (C…)")
        if missing:
            raise Refused(", ".join("#" + n for n in missing)
                          + (" is not a channel" if len(missing) == 1 else " are not channels")
                          + " the Slack connection can see")
        bad = [v for v in vals if v not in ids]
        if bad:
            raise Refused(f"{bad[0]!r} is not a Slack channel name or id")
        places = {**places, "slack": {**places["slack"],
                                      route.CHANNELS: list(dict.fromkeys(ids[v] for v in vals))}}
        change[route.PLACES] = places
    with section_change(project_id, changed_groups(change)) as done:
        res = route.set_initiative(project_id, change, by, now=now)
        done["changed"] = res["changed"]
        return res


@contextmanager
def section_change(project_id: str, groups: set[str]):
    """Around a change to one initiative's row made outside the file (a
    PO's ``ensemble_routing``, the message log's Make a rule): no apply runs
    meanwhile (one running is waited for, up to APPLY_WAIT_S), and when the
    change is made, the section's lines for ``groups`` are rewritten from
    the row (``write_section``). Refused, with nothing changed, when the
    section is written loosely and holds words the hub has not applied (a
    save not applied yet, or a problem in it), or when a plain section's
    lines for ``groups`` were changed in a save not applied yet: rewriting
    would lose those words. The body may set ``changed`` False in the dict
    it is given: then the file is left as it is."""
    if not _APPLYING.acquire(timeout=APPLY_WAIT_S):
        raise Refused("the setup file is being applied right now; try again in a minute")
    try:
        # The file lock too, so a panel Save cannot land between the check
        # and the rewrite.
        with _LOCK:
            why = _loose_unapplied(project_id) or _lines_unapplied(project_id, groups)
            if why:
                raise Refused(why)
            done = {"changed": True}
            yield done
            if done["changed"]:
                write_section(project_id, groups)
    finally:
        _APPLYING.release()


def _group_lines(lines: list[str]) -> dict[str, list[str]]:
    """``{group: [its non-empty lines, spaces made single]}`` of a section."""
    out: dict[str, list[str]] = {}
    for ln in lines:
        g = _group_of(ln)
        m = _LABEL.match(ln)
        if g and m and m.group(2).strip():
            out.setdefault(g, []).append(" ".join(ln.split()))
    return out


def _lines_by_section(text: str) -> dict[str, dict[str, list[str]]]:
    """Each section's labelled lines, by its name: kept as applied, so a
    change made outside the file can tell which lines a save changed since."""
    return {name_key(s["heading"]): _group_lines(s["lines"]) for s in split_sections(text)[1]}


def _lines_unapplied(project_id: str, groups: set[str]) -> str:
    """Why a change may not rewrite the lines ``groups`` of a plain section:
    a save changed them since the last apply that took the section (one not
    applied yet, or one whose section had a problem and so kept its row),
    in whatever form they are written."""
    name = route.initiatives().get(project_id)
    text = _read_text(setup_path())
    st = load_state()
    probs = problems_for(project_id)
    if not name or text is None or (text_hash(text) == st["appliedHash"] and not probs):
        return ""
    key = name_key(name)
    sec = next((s for s in split_sections(text)[1] if name_key(s["heading"]) == key), None)
    if sec is None or read_plain_section(sec["lines"]) is None:
        return ""
    now_lines, applied = _group_lines(sec["lines"]), st["appliedLines"].get(key) or {}
    if all(now_lines.get(g, []) == list(applied.get(g) or []) for g in groups):
        return ""
    if probs:
        return (f"{name}'s section of the setup file has a problem the hub could not apply ({probs[0]}), "
                f"and the line this changes was saved since the last apply without one; it is left as "
                f"written, so this change was not made. Fix that section in the setup file first (or wait "
                f"for the last save to be applied).")
    if st["failed"] and st["failedAll"]:
        when = f"the hub cannot apply the file right now ({st['failed'][:120]}); try again once it can"
    else:
        when = "try again in a minute"
    return (f"{name}'s section of the setup file was saved with a change to the line this changes, and "
            f"the hub has not applied it yet; it is left as written, so this change was not made: {when}.")


def _loose_unapplied(project_id: str) -> str:
    name = route.initiatives().get(project_id)
    text = _read_text(setup_path())
    if not name or text is None:
        return ""
    sec = next((s for s in split_sections(text)[1] if name_key(s["heading"]) == name_key(name)), None)
    if sec is None or read_plain_section(sec["lines"]) is not None:
        return ""
    st = load_state()
    probs = problems_for(project_id)
    if probs:
        what = f"a problem the hub could not apply ({probs[0]})"
    elif text_hash(text) != st["appliedHash"]:
        what = "words the hub has not applied yet"
    else:
        return ""
    return (f"{name}'s section of the setup file is written loosely and has {what}; it is left as "
            f"written, so this change was not made. Ask the CEO to fix that section, or try again "
            f"once the file is applied.")


def write_section(project_id: str, groups: set[str] | None = None) -> None:
    """Rewrite one initiative's section of the file from its row. With
    ``groups``, a section in the plain form keeps every line except those
    (the lines the change touched); otherwise, or for a loosely written
    section, the whole section is rewritten in the plain form. When the file
    was applied and the section had no problem, the new text counts as
    applied; otherwise the next tick applies the file again, so the
    section's other lines are checked once more."""
    names = route.initiatives()
    if project_id not in names:
        return
    with _LOCK:
        p = setup_path()
        text = _read_text(p)
        if text is None:
            write_first()
            return
        st = load_state()
        key = name_key(names[project_id])
        had_problem = any(name_key(x.get("section") or "") == key for x in st["problems"])
        was_applied = text_hash(text) == st["appliedHash"]
        fresh = section_text(project_id)
        lines, start, end = _section_span(text, key)
        whole = True
        if start is not None and groups is not None:
            sec = next((s for s in split_sections(text)[1] if name_key(s["heading"]) == key), None)
            if sec is not None and read_plain_section(sec["lines"]) is not None:
                body = _merge_lines(lines[start + 1:end], fresh.rstrip("\n").split("\n")[1:], groups)
                fresh = "\n".join([lines[start]] + body) + "\n"
                whole = False
        if whole:
            # The section is the row now: its problems are gone.
            st["problems"] = [x for x in st["problems"] if name_key(x.get("section") or "") != key]
        new = _replace_section(text, key, fresh)
        new = with_problems(new, [x["text"] for x in st["problems"]])
        durable.write_text(p, new)
        # The lines just written are the row: they count as applied.
        written = _lines_by_section(new).get(key) or {}
        mine = {} if whole else dict(st["appliedLines"].get(key) or {})
        for g in (LINE_GROUPS if whole else groups):
            if g in written:
                mine[g] = written[g]
            else:
                mine.pop(g, None)
        st["appliedLines"] = {**st["appliedLines"], key: mine}
        if was_applied and (whole or not had_problem):
            st["appliedHash"] = text_hash(new)
            st["mtime"] = _mtime(p)
        _save_state(st)
    write_now()


def problems_for(project_id: str) -> list[str]:
    """The problems the last apply found in one initiative's section."""
    name = route.initiatives().get(project_id)
    if not name:
        return []
    return [x["text"] for x in load_state()["problems"] if name_key(x.get("section") or "") == name_key(name)]


# ---------------------------------------------------------------------------
# routing-now.md
# ---------------------------------------------------------------------------

def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "never"


def _who(by) -> str:
    by = by if isinstance(by, dict) else {}
    k = by.get("kind")
    if k == "setup":
        return "the setup file"
    if k == "hubchat":
        return "the hub chat"
    if k == "ceo":
        return "the CEO"
    if k == "hub":
        return "the hub"
    if k == "agent":
        return f"{by.get('identity') or 'an agent'}" + (f" (PO of {by['projectName']})" if by.get("projectName") else "")
    return "someone"


def _from(item: dict) -> str:
    f = item.get("from") or ""
    return {"po": "the PO", "ceo": "the CEO", route.FROM_SETUP: "the setup file",
            route.FROM_ROUTED: "a routed message", route.FROM_MOVED: "Send elsewhere"}.get(f, f or "unknown")


def now_text(now: float | None = None) -> str:
    now = time.time() if now is None else now
    names = route.initiatives()
    t = route._load_one()
    by_id = _names_by_id()
    last = route._last_changes(t["history"], names)
    out = ["# Routing now", "",
           f"Written by the hub at {_when(now)}. **Do not edit this file**: the hub rewrites it. "
           f"To change the routing, edit {SETUP_NAME} next to it.", ""]
    st = load_state()
    if st["problems"]:
        out += [f"The last apply of {SETUP_NAME} ({_when(st['lastTry'])}) had problems; those sections "
                f"route as below until they are fixed:"] + [f"- {x['text']}" for x in st["problems"]] + [""]
    out += ["## Sources"]
    hs = outside.health_all(now)
    out += [f"- {h['label']} ({h['id']}): {_mode_word({'mode': h['mode'], 'paused': h['paused']})}. "
            f"{h['text']}. Last read {_when(h['lastGood'])}." for h in hs] or ["- none"]
    out += option_lines()
    nothing = []
    for pid, name in names.items():
        row = t["rows"].get(pid)
        if not row or route._row_empty(row):
            nothing.append(name)
            continue
        out += ["", f"## {name}", f"- Topic: {row[route.TOPIC] or '(none)'}"]
        for kind, vals in sorted((row.get(route.PLACES) or {}).items()):
            label = sources.TYPES[kind].label if kind in sources.TYPES else kind
            for col, v in vals.items():
                if not v:
                    continue
                if col == route.CHANNELS:
                    shown = ", ".join(f"#{by_id.get(channel_id(c), '?')} ({c})" if channel_id(c) else f"#{route.norm(c)}"
                                      for c in v)
                else:
                    shown = ", ".join(v) if isinstance(v, list) else str(v)
                out.append(f"- {label} {col}: {shown}")
        if row[route.ITEMS]:
            out.append("- Work items:")
            out += [f"  - {route.item_text(i)}: from {_from(i)}, {_when(i.get('at') or 0)}" for i in row[route.ITEMS]]
        if row.get(route.NOT):
            out.append("- Not (taken out; the hub does not add them back): "
                       + ", ".join(route.item_text(i) for i in row[route.NOT]))
        h = last.get(pid)
        out.append(f"- Last change: {_when(h.get('at') or 0)} by {_who(h.get('by'))}" if h else "- Last change: none")
    if nothing:
        out += ["", "## Initiatives with nothing set", ", ".join(nothing)]
    return "\n".join(out) + "\n"


def write_now(now: float | None = None) -> None:
    try:
        durable.write_text(now_path(), now_text(now))
    except Exception as e:
        _log(f"routing-now.md could not be written: {str(e)[:200]}")


def panel() -> dict:
    """What the panel's setup file box shows. The CEO opening it writes the
    file the first time, also on a hub with no source yet."""
    write_first()
    st = load_state()
    text = _read_text(setup_path()) or ""
    # body: the file without its problems part, so the panel can tell a
    # change someone made from the hub writing the problems.
    return {"text": text, "body": without_problems(text), "path": str(setup_path()),
            "nowPath": str(now_path()), "problems": [x["text"] for x in st["problems"]],
            "changes": st["changes"], "applying": applying(), "lastApply": st["lastApply"],
            "lastTry": st["lastTry"], "failed": st["failed"], "failedAll": st["failedAll"]}
