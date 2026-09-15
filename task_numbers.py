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
# A title a person numbered by hand: "18. 0DTE management", "18 Fixes", "#18 …".
_TITLE_NO = re.compile(r"^\s*(?:#(\d{1,4})(?!\w)|(\d{1,4})(?:\.|\s))")
# What names a task: its id, or its number with or without the project's key.
_REF = re.compile(r"^#?(?:([A-Za-z][A-Za-z0-9]{0,%d})-)?(\d{1,6})$" % (KEY_MAX - 1))
# A task named in chat text: #18, #ED-18, @codex@18, @reviewer@ED-18. Not in a
# word, a path or a URL fragment (page#18), and not an HTML entity (&#18;).
TEXT_REF = re.compile(r"(?<![\w&/#@.\\-])(?:@([A-Za-z][\w-]*)@|#)"
                      r"(?:([A-Za-z][A-Za-z0-9]{0,%d})-)?(\d{1,6})(?![\w-])" % (KEY_MAX - 1))
_FENCE = re.compile(r"```[\s\S]*?(?:```|$)")
_CODE = re.compile(r"`[^`\n]*`")


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
    counter (0 when it has none yet). A task whose title starts with a number
    (``18.``, ``18 ``, ``#18``) gets it when no other task of the project has
    that number in its title or as its number, and the counter has not passed
    it (a number is never reused); the rest take the numbers after the highest
    one, oldest first. Returns ``({id: no}, next counter)``: with nothing to
    number, nothing changes."""
    used = {int(t["no"]) for t in tasks if t.get("no")}
    todo = sorted((t for t in tasks if not t.get("no")),
                  key=lambda t: (t.get("createdAt") or 0, t.get("id") or ""))
    in_titles = Counter(n for n in (title_no(t.get("title") or "") for t in tasks) if n)
    counter = int(next_no or 0)
    out: dict[str, int] = {}
    for t in todo:
        n = title_no(t.get("title") or "")
        if n and in_titles[n] == 1 and n not in used and (counter <= 0 or n >= counter):
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


def find_text_refs(text: str) -> list[dict]:
    """The tasks named in chat text, in order and each task once:
    ``[{token, who, key, no}]`` — ``who`` is the identity or role before the
    number (``@codex@18``), else "". Code is not read."""
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in TEXT_REF.finditer(_without_code(text)):
        key, no = (m.group(2) or "").upper(), int(m.group(3))
        if (key, no) in seen:
            continue
        seen.add((key, no))
        out.append({"token": m.group(0), "who": m.group(1) or "", "key": key, "no": no})
    return out
