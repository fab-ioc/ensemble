"""Task numbers and project keys: a task is #18 in its project, ED-18 anywhere.

A room id (``room-e2f2b509``) means nothing to a person, so every task of a
project gets a number when it is created, monotonic in its project and never
reused or changed; a task moved to another project takes that project's next
number and keeps the old one in ``previousNos``, so an old reference still
lands. A project gets a short key (``ED`` for Ensemble Dashboard) for the full
form, used where two projects could be confused.

Pure functions: the hub (dashboard.py) owns the files and the lock, and hands
these the rooms and projects it read.
"""
from __future__ import annotations

import re
from collections import Counter

KEY_MAX = 6
_KEY = re.compile(r"^[A-Z][A-Z0-9]{0,%d}$" % (KEY_MAX - 1))
_WORD = re.compile(r"[A-Za-z0-9]+")
TITLE_NO_MAX = 9999
# How far above a project's task count a title's number may be and still be
# the task's number.
TITLE_NO_SLACK = 50
# A title a person numbered by hand: "18. 0DTE management", "18 Fixes", "#18 …".
_TITLE_NO = re.compile(r"^\s*(?:#(\d{1,4})(?!\w)|(\d{1,4})(?:\.|\s))")
# What names a task: its id, or its number with or without the project's key.
_REF = re.compile(r"^#?(?:([A-Za-z][A-Za-z0-9]{0,%d})-)?(\d{1,6})$" % (KEY_MAX - 1))
# A task named in chat text: #18, #ED-18, @codex@18, @reviewer@ED-18, and
# ED-18 on its own (its key in capitals, as the hub writes it: #156). Not in
# a word, a path or a URL fragment (page#18), and not an HTML entity (&#18;).
# Groups: the agent, the key after # or @, the key of the bare form, the number.
TEXT_REF = re.compile(r"(?<![\w&/#@.\\-])(?:(?:@([A-Za-z][\w-]*)@|#)"
                      r"(?:([A-Za-z][A-Za-z0-9]{0,%d})-)?|([A-Z][A-Z0-9]{0,%d})-)(\d{1,6})(?![\w-])" % (KEY_MAX - 1, KEY_MAX - 1))
# A bare #12 right after one of these words is someone else's number (a pull
# request, an issue, a review finding), not a task: "PR #12", "fixes #34".
# ED-12 and @codex@12 are always tasks.
NOT_TASK_BEFORE = re.compile(
    r"(?:^|[^\w])(?:pr|mr|pull request|issue|bug|ticket|resolve|resolved"
    r"|finding|step|item|point|round|option|question|comment|commit|line|page|part"
    r"|phase|rule|case|image)s?\.?[ \t]*\Z", re.I)   # \Z: Python's $ also matches before a final newline
_FENCE = re.compile(r"```[\s\S]*?(?:```|$)")
_CODE = re.compile(r"`[^`\n]*`")
# Where a sentence ends, for the project a bare number is read in (#156): a
# full stop, ! or ? before a space or the end (v0.11.0 keeps its dots), a
# blank line, a heading line (whole), a line that starts a list item, a
# quote or a table row, and a table's cell bar.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)|\n[ \t]*\n|^[ \t]*#{1,6}[ \t][^\n]*|^[ \t]*(?:[-*+]|\d{1,3}[.)]|>|\|)(?=\s|$)|\|", re.M)
# What may stand between a project's name and its number (read_project):
# a possessive ("Dock's #27"), the board noun ("the Dock project's #27"), a
# version, emphasised or not ("Dock v0.11.0 (#27)", "Dock **v0.11.0** (#27)"),
# one word that is a verb in the past ("Dock released #27") or "with", "as"
# or "task" ("Dock v0.11.0 with #27", "Dock task #27"), then a colon, comma
# or dash and an opening bracket.
_POSS = r"(?:['’]s)?"
_VERSION = r"(?:\s+[*_]*v?\d+(?:\.\d+)+[*_]*)?"
_LINK = r"(?:\s+(?:\w{2,}ed|with|as|task))?"
NOUNS = ("project", "board")


def aliases(name: str, po_title: str = "") -> list[str]:
    """What a chat calls a project, besides its full name: the first word of
    a name of several ("Ensemble" for Ensemble Dashboard; four letters or
    more), and the title of its PO's chat ("opten" for OPtionTradingENgine)
    when that is one or two words — a trailing "PO" dropped — and not a
    sentence. Each once, the name first."""
    out: list[str] = []
    name = " ".join(str(name or "").split())
    if name:
        out.append(name)
    words = name.split(" ")
    if len(words) > 1 and len(words[0]) >= 4 and words[0].isalpha():
        out.append(words[0])
    t = " ".join(str(po_title or "").split())
    t = re.sub(r"\s*(?:\bPO|\bproduct owner)\s*$", "", t, flags=re.I).strip(" -:")
    tw = t.split(" ")
    if t and len(tw) <= 2 and all(len(w) >= 3 and re.match(r"^[A-Za-z][\w-]*$", w) for w in tw) and len(t) <= 24:
        out.append(t)
    seen: set[str] = set()
    uniq = []
    for a in out:
        if a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq


def project_names(projects: list[dict], po_titles: dict[str, str] | None = None) -> list[dict]:
    """``[{id, key, name, aliases}]`` for the name rules: each project's name
    and :func:`aliases` (``po_titles`` is {project id: its PO chat's title}),
    a word two projects both answer to dropped from both. ``projects`` are
    ``{id, name, key}`` with the key as :func:`project_keys` gives it."""
    po_titles = po_titles or {}
    rows = [{"id": p["id"], "key": p.get("key") or "", "name": p.get("name") or "",
             "aliases": aliases(p.get("name") or "", po_titles.get(p["id"], ""))} for p in projects]
    counts = Counter(a.lower() for r in rows for a in r["aliases"])
    for r in rows:
        r["aliases"] = [a for a in r["aliases"] if counts[a.lower()] == 1]
    return rows


def _name_res(ctx: dict) -> tuple[re.Pattern | None, re.Pattern | None, dict[str, str]]:
    """(the regex of every alias, the regex of an alias right before a
    number, {alias lower: project id}) for a context; None with no names.
    (re's own cache keeps the compiled patterns between calls.)"""
    by: dict[str, str] = {}
    for p in ctx.get("projects") or []:
        for a in p.get("aliases") or []:
            by[a.lower()] = p["id"]
    if not by:
        return None, None, by
    alts = "|".join(re.escape(a) for a in sorted(by, key=len, reverse=True))
    nouns = "|".join(re.escape(n) for n in sorted({*NOUNS, *(ctx.get("nouns") or ())}, key=len, reverse=True) if n)
    names = re.compile(r"(?<![\w-])(%s)(?![\w-])" % alts, re.I)
    before = re.compile(r"(?<![\w-])(%s)%s(?:\s+(?:%s))?%s%s%s(?:\s*[:,–—-])?\s*\(?\s*\Z"
                        % (alts, _POSS, nouns, _POSS, _VERSION, _LINK), re.I)
    return names, before, by


def _sentence_before(plain: str, start: int) -> str:
    """What ``plain``'s sentence says before ``start``."""
    a = 0
    for m in _SENTENCE_END.finditer(plain, 0, start):
        if m.end() <= start:
            a = m.end()
    return plain[a:start]


def read_project(plain: str, start: int, end: int, ctx: dict) -> tuple[str, str, list[str]]:
    """``(project id, how, others)`` for the bare number at [start, end) of
    ``plain``: the project named right before it — "Dock #27", "Dock's #27",
    "the Dock project's #27", "in Dock: #27", "Dock task #27", "Dock released
    #27", "Dock v0.11.0 (#27)", "Dock v0.11.0 with #27" — with how "name";
    else the context's own project (how ""). ``others`` are the other
    projects its sentence names before it ("Answered Ensemble about #11",
    "Ensemble needs 9–12 (#20)"): too weak to read the number there (measured
    on real chats, such a name is the task's title or whom it was told far
    more often than its project), but a reader that finds the number in one
    of them too treats it as ambiguous (dashboard.TaskLookup, the pages:
    no line, and the card says which project was assumed)."""
    names, before, by = _name_res(ctx)
    own = ctx.get("own") or ""
    if names is None:
        return own, "", []
    said = _sentence_before(plain, start)
    m = before.search(said[-80:])      # a slice, as the page's copy reads it
    if m:
        return by[m.group(1).lower()], "name", []
    others: list[str] = []
    for n in names.finditer(said):
        pid = by[n.group(1).lower()]
        if pid != own and pid not in others:
            others.append(pid)
    return own, "", others


def normalize_key(key) -> str:
    """A project key as stored (upper case), or "" when it is not one: a
    letter, then up to five letters or digits."""
    k = str(key or "").strip().upper()
    return k if _KEY.match(k) else ""


def derive_key(name: str) -> str:
    """The initials of a project name's first three words, upper case:
    Ensemble Dashboard → ED, Motors → M. A word is a run of letters and
    digits; a key starts with a letter, so leading digits are dropped."""
    initials = "".join(w[0] for w in _WORD.findall(name or "")[:3]).upper().lstrip("0123456789")
    return initials or "P"


def project_keys(projects: list[dict]) -> dict[str, str]:
    """{project id: key} for every project, each key unique. A stored key is
    kept (the older project keeps it when two claim the same one); every other
    project gets its derived key, or that key followed by 2, 3, … when it is
    taken. Deterministic: projects are taken oldest first."""
    order = sorted(projects, key=lambda p: (p.get("createdAt") or 0, p.get("id") or ""))
    out: dict[str, str] = {}
    taken: set[str] = set()
    for p in order:
        k = normalize_key(p.get("key"))
        if k and k not in taken:
            out[p["id"]] = k
            taken.add(k)
    for p in order:
        if p["id"] in out:
            continue
        base = derive_key(p.get("name") or "")
        k, n = base, 2
        while k in taken:
            k = f"{base[:KEY_MAX - len(str(n))]}{n}"
            n += 1
        out[p["id"]] = k
        taken.add(k)
    return out


def title_no(title: str) -> int | None:
    """The number a person gave a task in its title, or None."""
    m = _TITLE_NO.match(title or "")
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 1 <= n <= TITLE_NO_MAX else None


def plan_numbers(tasks: list[dict], next_no: int = 0) -> tuple[dict[str, int], int]:
    """Numbers for a project's tasks that have none, and the counter after.

    ``tasks`` is every task of the project: ``{id, title, createdAt, no}``, with
    ``no`` None for a task without a number here. ``next_no`` is the project's
    counter (0 when it has none yet). On a project's first numbering (no
    counter), a task whose title starts with a number (``18.``, ``18 ``,
    ``#18``) gets it when no other task of the project has that number in its
    title or as its number, and it is not far above the project's task count
    (``2026 roadmap`` is a year, not #2026); the rest take the numbers after
    the highest one, oldest first. Once a project has a counter, titles are
    not read. Returns ``({id: no}, next counter)``: with nothing to number,
    nothing changes. A number two tasks share is the older one's; the
    others are numbered as if they had none."""
    order = lambda t: (t.get("createdAt") or 0, t.get("id") or "")
    keeper: dict[int, dict] = {}
    for t in sorted((t for t in tasks if t.get("no")), key=order):
        keeper.setdefault(int(t["no"]), t)
    used = set(keeper)
    todo = sorted((t for t in tasks if not t.get("no") or keeper[int(t["no"])] is not t), key=order)
    in_titles = Counter(n for n in (title_no(t.get("title") or "") for t in tasks) if n)
    counter = int(next_no or 0)
    out: dict[str, int] = {}
    for t in todo if counter <= 0 else ():
        n = title_no(t.get("title") or "")
        if n and in_titles[n] == 1 and n not in used and n <= len(tasks) + TITLE_NO_SLACK:
            out[t["id"]] = n
            used.add(n)
    after = max([counter - 1, 0, *used])
    for t in todo:
        if t["id"] not in out:
            after += 1
            out[t["id"]] = after
            used.add(after)
    return out, max(counter, max(used, default=0) + 1)


def parse_ref(ref) -> dict | None:
    """What a task address says: ``{"room": id}``, ``{"key": "", "no": 18}``
    (#18, 18: in a project) or ``{"key": "ED", "no": 18}`` (ED-18), else None."""
    if isinstance(ref, bool):
        return None
    s = str(ref if ref is not None else "").strip()
    if s.startswith("room-"):
        return {"room": s}
    m = _REF.match(s)
    if not m:
        return None
    return {"key": (m.group(1) or "").upper(), "no": int(m.group(2))}


def label(no, key: str = "") -> str:
    """#18, or ED-18 with a key; "" for a task without a number."""
    if not no:
        return ""
    return f"{key}-{no}" if key else f"#{no}"


def find_task(rooms: list[dict], ref, project_id: str, keys: dict[str, str],
              names: dict[str, str] | None = None, any_project: bool = False) -> tuple[str, str]:
    """(room id, "") for the task ``ref`` names, else ("", why).

    ``rooms`` are ``{id, no, noProjectId, previousNos}``; ``keys`` is
    :func:`project_keys`. A number without a key is looked up in
    ``project_id``; with no project it is found only when ``any_project`` and
    exactly one project has it. A task's old number in a project it was moved
    from still finds it."""
    names = names or {}
    parsed = parse_ref(ref)
    shown = str(ref if ref is not None else "").strip()
    if parsed is None:
        return "", f"“{shown}” is not a task: give its id (room-…) or its number (#18, or ED-18 in another project)"
    if "room" in parsed:
        rid = parsed["room"]
        return (rid, "") if any(r.get("id") == rid for r in rooms) else ("", f"no such task: {rid}")
    no = parsed["no"]
    if parsed["key"]:
        pids = [pid for pid, k in keys.items() if k == parsed["key"]]
        if not pids:
            return "", f"no project has the key {parsed['key']}: no task {parsed['key']}-{no}"
        project_id = pids[0]
    elif not project_id:
        if not any_project:
            return "", f"#{no} names a task in a project, and there is none here: give its key too (e.g. ED-{no})"
        hits = sorted({r["id"] for r in rooms if r.get("no") == no and r.get("noProjectId")})
        if len(hits) == 1:
            return hits[0], ""
        if not hits:
            return "", f"no task has the number #{no}"
        where = ", ".join(sorted(label(no, keys.get(r.get("noProjectId"), "?"))
                                 for r in rooms if r["id"] in hits))
        return "", f"#{no} is a number in several projects ({where}): give the project's key"
    for r in rooms:
        if r.get("no") == no and r.get("noProjectId") == project_id:
            return r["id"], ""
    for r in rooms:
        for prev in r.get("previousNos") or []:
            if isinstance(prev, dict) and prev.get("no") == no and prev.get("projectId") == project_id:
                return r["id"], ""
    where = names.get(project_id) or keys.get(project_id) or project_id
    return "", f"no task #{no} in {where}"


def _without_code(text: str) -> str:
    """``text`` with code blocks and code spans blanked out, same length."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    return _CODE.sub(blank, _FENCE.sub(blank, text or ""))


def all_text_refs(text: str, ctx: dict | None = None) -> list[dict]:
    """Every task named in chat text, in order, each mention once:
    ``[{token, who, key, no, start, end, project, how, others}]`` — ``who``
    is the identity or role before the number (``@codex@18``), else "". Code
    is not read, nor a bare number after a word that says it is not a task
    (:data:`NOT_TASK_BEFORE`). With ``ctx`` (``{projects: project_names(),
    own: the project the text belongs to, nouns: [the board noun]}``) a bare
    number's ``project``, ``how`` and ``others`` are :func:`read_project`'s
    and a key's project is the one with that key ("" for a key nobody has,
    how "key"); without one every ``project`` is ""."""
    out: list[dict] = []
    plain = _without_code(text)
    by_key = {p["key"]: p["id"] for p in (ctx or {}).get("projects") or [] if p.get("key")}
    for m in TEXT_REF.finditer(plain):
        key, no = (m.group(2) or m.group(3) or "").upper(), int(m.group(4))
        if not m.group(1) and not key and NOT_TASK_BEFORE.search(plain[max(0, m.start() - 40):m.start()]):
            continue
        ref = {"token": m.group(0), "who": m.group(1) or "", "key": key, "no": no,
               "start": m.start(), "end": m.end(), "project": "", "how": "", "others": []}
        if key:
            ref["project"], ref["how"] = by_key.get(key, ""), "key"
        elif ctx:
            ref["project"], ref["how"], ref["others"] = read_project(plain, m.start(), m.end(), ctx)
        out.append(ref)
    return out


def find_text_refs(text: str, ctx: dict | None = None) -> list[dict]:
    """The tasks named in chat text, in order and each task once (a number
    read in two projects is two tasks): :func:`all_text_refs` without its
    repeats."""
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for ref in all_text_refs(text, ctx):
        k = (ref["key"] or ref["project"], ref["no"])
        if k in seen:
            continue
        seen.add(k)
        out.append(ref)
    return out


def qualify_text(text: str, ctx: dict, exists) -> str:
    """``text`` with each bare ``#18`` that is the own project's task written
    in full, ``ED-18``, so the text says which project's task it means
    wherever it is read next (a PO's message to another project's PO, #156).
    A bare number is the own project's unless another project's name right
    before it says otherwise (:func:`read_project`) and that project has the
    task (``exists(project id, no)``); the own project must have it too. A
    keyed number, an agent's (``@codex@18``) and anything in code stay as
    written; without a key for the own project nothing changes."""
    own = ctx.get("own") or ""
    key = next((p.get("key") or "" for p in ctx.get("projects") or [] if p["id"] == own), "")
    if not own or not key:
        return text
    out = text
    for ref in reversed(all_text_refs(text, ctx)):
        if ref["who"] or ref["key"]:
            continue
        if ref["project"] != own and exists(ref["project"], ref["no"]):
            continue
        if not exists(own, ref["no"]):
            continue
        out = out[:ref["start"]] + f"{key}-{ref['no']}" + out[ref["end"]:]
    return out
