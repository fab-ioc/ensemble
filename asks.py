"""Asks: the questions an agent marks for the person in a message, each
answered on its own with one click (GitHub issue 7, #163).

**The marker.** A line of its own that starts ``Ask:`` is one ask; the list
right under it, if any, is its options::

    Ask: Which colour should the badge be?
    - Amber (recommended): it is the colour that means "needs you"
    - Grey: quieter

    Ask (yes/no): Restart the hub tonight?

    Ask: What should the empty list say?

It reads as plain text wherever it is copied, folded or quoted, and it is the
shape both Claude and Codex already write: the line may carry list, quote or
heading marks and bold (``1. **Ask:** …``, ``- Ask: …``, ``**Ask (yes/no):**
…``), and the options may be any list (``-``, ``*``, ``1.``, ``a)``),
indented under a numbered ask or not. Nothing in a fenced code block is an
ask, so the rule can be quoted.

**Kinds.** An ask with options is a ``decision``; one whose options are just
Yes and No, or that says ``(yes/no)``, is ``yesno``; one with neither is
``open`` (a comment only). An ask with no list gets its options from its
words when they name them, and Yes and No when it is not a wh-question
(:func:`inline`, GitHub issue 16); ``Ask (open):`` keeps the comment alone. At most one option is recommended: the first one
marked ``(recommended)`` (or ``[recommended]``, ``— recommended``, a leading
``Recommended:``).

**An option's label** is what its button says and what the answer sends: the
bold words it starts with, else its words up to the first `` — ``, `` - `` or
``: ``. The rest is its detail.

The page parses the same way (``parseAsks`` in session.html); the tests hold
the two to the same answers. The hub parses only to check an answer against
the message it answers, to word the answer, and to find the asks still open
(the Needs you list).

**Answers** are kept in the room's points ledger (``asks``: message id → ask
number → the answer), once per ask from any device, and sent as the person's
message, ``Re “<ask>”: <option>`` with their comment under it — a point like
any other.
"""
from __future__ import annotations

import re
import threading
import time

_d = None   # the dashboard module (bind)


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


QUESTION_MAX = 200      # an ask's words kept and quoted
LABEL_MAX = 80          # an option's label
CACHE_S = 5.0           # the open asks of every room are worked out at most this often

_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_QUOTE = re.compile(r"^[ \t]*>[ \t]?")
_ASK = re.compile(
    r"^(?P<ind>[ \t]*)(?P<lead>(?:(?:#{1,6}|[-*+]|[0-9]{1,3}[.)])[ \t]+)*)"
    r"(?:\*\*|__)?[ \t]*ask(?:[ \t]*\((?P<kind>[^)\n]{1,24})\))?[ \t]*(?:\*\*|__)?[ \t]*:"
    r"[ \t]*(?:\*\*|__)?[ \t]*(?P<q>.*)$", re.I)
_ITEM = re.compile(r"^(?P<ind>[ \t]*)(?:[-*+]|[0-9]{1,3}[.)]|[A-Za-z][.)])[ \t]+(?P<t>\S.*)$")
_LIST_LEAD = re.compile(r"(?:[-*+]|[0-9]{1,3}[.)])[ \t]+")
# The page reads the same lines and spaces: every line break its "." stops at
# is a new line, and every other space either language trims is a plain one.
_LINE_BREAKS = re.compile(r"\r\n?|[\u2028\u2029]")
_OTHER_SPACE = re.compile(r"[\x0b\x0c\x1c-\x1f\x85\xa0\u1680\u2000-\u200a\u202f\u205f\u3000\ufeff]")
_CHECKBOX = re.compile(r"^\[[ xX]\][ \t]+")
_REC = re.compile(
    r"[ \t]*(?:\*\*|__|\*|_)?[(\[][ \t]*recommended[ \t]*[)\]](?:\*\*|__|\*|_)?"
    r"|[ \t]+[—–-][ \t]*(?:\*\*|__|\*|_)?recommended(?:\*\*|__|\*|_)?\.?[ \t]*$", re.I)
_REC_LEAD = re.compile(r"^(?:\*\*|__)?recommended(?:\*\*|__)?[ \t]*[:—–-][ \t]*(?:\*\*|__)?[ \t]*", re.I)
_BOLD_HEAD = re.compile(r"^(?:\*\*|__)(?P<l>[^*_\n]+?)(?:\*\*|__)[ \t]*(?P<rest>.*)$")
_SPLIT = re.compile(r"[ \t]+[—–][ \t]+|[ \t]+-{1,2}[ \t]+|:[ \t]+")
_MARKS = re.compile(r"\*\*|__|`")
_YESNO_TAG = re.compile(r"^(?:yes[ \t]*(?:/|-|or)[ \t]*no|y/n|yesno)$", re.I)
_OPEN_TAG = re.compile(r"^(?:open|free[ \t]*text|text)$", re.I)


def _indent(s: str) -> int:
    return len(s.replace("\t", "    "))


def _plain(s: str) -> str:
    return " ".join(_MARKS.sub("", s or "").split())


def _option(text: str) -> dict | None:
    t = _CHECKBOX.sub("", text.strip())
    rec = False
    if _REC_LEAD.match(t):
        t, rec = _REC_LEAD.sub("", t, count=1), True
    if _REC.search(t):
        t, rec = _REC.sub("", t, count=1).strip(), True
    m = _BOLD_HEAD.match(t)
    if m:
        label, detail = m.group("l"), re.sub(r"^[ \t]*[:—–-]+[ \t]*", "", m.group("rest"))
    else:
        s = _SPLIT.search(t)
        label, detail = (t[:s.start()], t[s.end():]) if s else (t, "")
    label = _plain(label).rstrip(".:;,").strip()
    if len(label) > LABEL_MAX:
        label = label[:LABEL_MAX - 1].rstrip() + "…"
    if not label:
        return None
    return {"label": label, "detail": _plain(detail), "recommended": rec}


# ---------------------------------------------------------------------------
# Options in the words (GitHub issue 16, #195): an ask with no list under it
# still gets buttons when its words give them. Read from the agents' real
# questions: "Options: (1) …, (2) …, or (3) …", "(a) … or (b) …", "A, or
# B?", "Who? Just you, your family, or outside users?", "The card offers
# three choices: A, B, or C.", "… (recommended)? Or do you want B, or C?";
# two questions in one ask ("…? And do you approve …?") are two asks; a
# question that is not a wh-question is answered Yes or No. Only a wh-question
# with no options in its words keeps a comment box alone. The page has the
# same rules (askInline in session.html).
# ---------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[?!.])[ \t]+(?=[\"'(*_]*[A-Z0-9])")
_WH = re.compile(r"^(?:(?:on|in|at|by|for|from|to|with|until|since)[ \t]+)?(?:what|which|who|whom|whose|where|when|why|how)\b", re.I)
_AUX = re.compile(r"^(?:should|shall|can|could|may|might|must|will|would|do|does|did|is|are|was|were|am|have|has|had)\b", re.I)
_JOIN = re.compile(r"^(?:and|also|plus|so|then)\b[ \t]*,?[ \t]*", re.I)
_OR = re.compile(r"^or\b[ \t]*,?[ \t]*", re.I)
_EG = re.compile(r"^(?:for example|for instance|e\.g\.|such as|like)[ \t]*[,:]?[ \t]+", re.I)
_TAIL = re.compile(r"^(?:for example|for instance|e\.g\.|such as|meaning|i\.e\.|that is|with|which|since|because|so that)\b", re.I)
_SUB = re.compile(r"^(?:when|if|once|after|before|while|as soon as)\b", re.I)
_LEAD_LABEL = re.compile(r"^([^:?]{1,40}):[ \t]+")
_ASKER = re.compile(r"^(?:should|shall|can|could|may|will|would|do|did)[ \t]+(?:i|we|you)[ \t]+"
                    r"(?:(?:rather|want|like|prefer)[ \t]+(?:me[ \t]+|us[ \t]+)?(?:to[ \t]+)?)?", re.I)
_CHOOSER = re.compile(r"^(?:do|would)[ \t]+you[ \t]+(?:want|prefer|rather)\b", re.I)
_PICKER = re.compile(r"^(?:(?:should|shall)[ \t]+(?:i|we)|(?:do|would)[ \t]+you[ \t]+(?:want|prefer|rather))\b", re.I)
_INLINE_REC = re.compile(
    r"[ \t]*\([ \t]*(?:my[ \t]+|i(?:'d|[ \t]+would)?[ \t]+)?recommend(?:ed|ation)?\b[^)]*\)"
    r"|,?[ \t]+which[ \t]+i(?:'d|[ \t]+would)?[ \t]+recommend\b"
    r"|[ \t]+[—–-][ \t]+(?:my[ \t]+)?recommend(?:ed|ation)\b", re.I)
_REC_NO = re.compile(r"\bI(?:'d|[ \t]+would)?[ \t]+(?:recommend|suggest)[ \t]+(?:no|not|against)\b", re.I)
_REC_YES = re.compile(r"\bI(?:'d|[ \t]+would)?[ \t]+(?:recommend|suggest)[ \t]+(?:yes|it|that|so|both|starting|doing|going)\b"
                      r"|\(recommended\)|\bmy recommendation is yes\b", re.I)
_ENUM = re.compile(r"(?:^|(?<=[ \t:,;]))\(?([1-9]|[a-hA-H])\)[ \t]+")
_LAST_OR = re.compile(r",[ \t]+or[ \t]+", re.I)
_BARE_OR = re.compile(r"[ \t]+or[ \t]+", re.I)
_COMMA = re.compile(r",[ \t]+")
_LABEL_CUT = re.compile(r",[ \t]+(?![#0-9])")      # not in "#214, #217"
_ENDS = re.compile(r"[ \t?.!;,:]+$")
_QMARK = re.compile(r"\?[ \t*_\"')]*$")


def _mask(s: str) -> str:
    """``s`` with every parenthesis and what it holds blanked out (same
    length), so a comma or an "or" in brackets splits nothing."""
    out, depth = [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        out.append("\0" if depth else ch)
        if ch == ")" and depth:
            depth -= 1
    return "".join(out)


def _top(s: str, rx) -> list:
    """The top-level matches of ``rx`` in ``s`` (none inside brackets)."""
    return list(rx.finditer(_mask(s)))


def _words(s: str) -> int:
    return len(_mask(s).split())


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _inline_option(item: str) -> dict | None:
    t, rec = item.strip(), False
    if _INLINE_REC.search(t):
        t, rec = _INLINE_REC.sub("", t, count=1), True
    t = _ENDS.sub("", _OR.sub("", _ASKER.sub("", t.strip(), count=1), count=1)).strip()
    cut = _top(t, _LABEL_CUT)
    label, detail = (t[:cut[0].start()], t[cut[0].end():]) if cut else (t, "")
    if len(label) > LABEL_MAX:
        m = _mask(label)
        bare = " ".join("".join(c for c, k in zip(label, m) if k != "\0").split())
        aside = "; ".join(x.strip("() ") for x in re.findall(r"\([^()]*\)", label))
        if bare and aside:
            label, detail = bare, aside + ("; " + detail if detail else "")
    label = _ENDS.sub("", " ".join(label.split()))
    if len(label) > LABEL_MAX:
        label = label[:LABEL_MAX - 1].rstrip() + "…"
    if not label:
        return None
    return {"label": _cap(label), "detail": " ".join(detail.split()).rstrip(".;,"), "recommended": rec}


def _options(items: list[str]) -> list[dict]:
    """Options from the items of a list in the words: an item that only goes
    on about the one before it ("for example …", "meaning …") is its detail.
    Fewer than two, or more than six, is no list."""
    merged: list[str] = []
    for it in items:
        it = it.strip()
        if merged and (not it or _TAIL.match(it)):
            merged[-1] += ", " + it
        elif it:
            merged.append(it)
    out, seen = [], set()
    for it in merged:
        o = _inline_option(it)
        if o and o["label"].lower() not in seen:
            seen.add(o["label"].lower())
            out.append(o)
    return out if 2 <= len(out) <= 6 else []


def _or_items(t: str, multi: bool) -> list[str] | None:
    """``A, B, or C`` / ``A or B``: the items, or None when the words hold no
    "or". ``multi`` false: the part before the last ", or" is one item."""
    t = _ENDS.sub("", t.strip())
    last = _top(t, _LAST_OR)
    if last:
        head, tail = t[:last[-1].start()], t[last[-1].end():]
        if multi:
            return [head[a:b] for a, b in _cuts(head, _COMMA)] + [tail]
        return [head, tail]
    bare = _top(t, _BARE_OR)
    if len(bare) == 1 and not _top(t, _COMMA):
        return [t[:bare[0].start()], t[bare[0].end():]]
    return None


def _cuts(s: str, rx) -> list[tuple[int, int]]:
    spans, at = [], 0
    for m in _top(s, rx):
        spans.append((at, m.start()))
        at = m.end()
    spans.append((at, len(s)))
    return spans


def _enum_options(s: str) -> tuple[str, list[dict]] | None:
    """``(1) … (2) …`` / ``(a) … or (b) …`` in a sentence: the words before
    the first, and the options."""
    want, marks = None, []
    for m in _ENUM.finditer(s):
        v = m.group(1).lower()
        if want is None:
            if v not in ("1", "a"):
                continue
            want = v
        if v != want:
            continue
        marks.append(m)
        want = chr(ord(want) + 1)
    if len(marks) < 2:
        return None
    items = [s[m.end():(marks[k + 1].start() if k + 1 < len(marks) else len(s))] for k, m in enumerate(marks)]
    items = [re.sub(r"(?:,?[ \t]+or|,)[ \t]*$", "", _ENDS.sub("", it), flags=re.I) for it in items]
    opts = _options(items)
    return (s[:marks[0].start()], opts) if opts else None


def _colon_options(s: str) -> list[dict]:
    """"…: A, B, or C." — a list after a colon, joined by "or"."""
    m = _top(s, re.compile(r":[ \t]+"))
    if not m:
        return []
    items = _or_items(s[m[0].end():], True)
    return _options(items) if items else []


def _core(s: str) -> str:
    """The question itself: without a lead label ("Decision:"), a lead "And",
    a clause before it ("…, so what …", "When you …, should …")."""
    t = _JOIN.sub("", s.strip(), count=1)
    m = _LEAD_LABEL.match(t)
    if m and len(m.group(1).split()) <= 5:
        t = t[m.end():]
    so = _top(t, re.compile(r"[,;][ \t]+(?:so|but|then)[ \t]+", re.I))
    if so:
        t = t[so[-1].end():]
    if _SUB.match(t):
        c = _top(t, _COMMA)
        if c:
            t = t[c[0].end():]
    return _ENDS.sub("", t.strip())


def _alternatives(core: str) -> list[dict]:
    """"Should I A, or B?" / "buy or lease?": the alternatives it names."""
    if not _top(core, _LAST_OR):
        if _words(core) > 6:
            return []
        if _AUX.match(core) and not _PICKER.match(core):
            # "Is it red or blue?" names two answers; "Did you read or
            # review it?" asks yes or no: one word after the "or" is a choice
            # between it and the word before.
            bare = _top(core, _BARE_OR)
            if len(bare) != 1 or _top(core, _COMMA):
                return []
            head, tail = core[:bare[0].start()].split(), core[bare[0].end():].split()
            return _options([head[-1], tail[0]]) if len(head) >= 2 and len(tail) == 1 else []
    items = _or_items(core, bool(_CHOOSER.match(core)))
    return _options(items) if items else []


def _yes_no(text: str) -> list[dict]:
    rec = "No" if _REC_NO.search(text) else "Yes" if _REC_YES.search(text) else ""
    return [{"label": x, "detail": "", "recommended": x == rec} for x in ("Yes", "No")]


def inline(text: str) -> list[dict]:
    """The asks the words of one question hold: ``[{question, options}]``,
    options [] for a wh-question that names none. [] when there is no
    question in it."""
    text = " ".join((text or "").split())
    if not text:
        return []
    sents = [s for s in _SENTENCE.split(text) if s.strip()]
    asked = any(_QMARK.search(s) for s in sents)
    groups: list[dict] = []
    pre: list[str] = []
    for s in sents:
        isq = bool(_QMARK.search(s)) or not asked
        if not groups:
            if not isq:
                pre.append(s)
                continue
            groups.append({"text": pre + [s], "core": s, "opts": None})
            pre = []
            continue
        g = groups[-1]
        if isq and not asked:
            g["text"].append(s)
        elif isq and _OR.match(s) and not g["opts"]:
            rest = _OR.sub("", s, count=1)
            items = _or_items(_ASKER.sub("", rest.strip(), count=1), True) or [rest]
            g["opts"] = _options([_core(g["core"])] + items) or None
            g["text"].append(s)
        elif isq and (_JOIN.match(s) or _AUX.match(s) or _WH.match(s)):
            groups.append({"text": [s], "core": s, "opts": None})
        elif isq and not g["opts"] and \
                (items := _or_items(_EG.sub("", s, count=1), True)) and \
                all(_words(x) <= 8 for x in items) and (opts := _options(items)):
            g["opts"] = opts
            g["text"].append(s)
        else:
            if not g["opts"]:
                found = _enum_options(s)
                g["opts"] = (found[1] if found else _colon_options(s)) or None
            g["text"].append(s)
    out = []
    for g in groups:
        opts = g["opts"]
        if not opts:
            found = _enum_options(g["core"])
            opts = found[1] if found else _colon_options(g["core"])
        if not opts:
            core = _core(g["core"])
            if _WH.match(core):
                opts = []
            else:
                opts = _alternatives(core) or _yes_no(" ".join(g["text"]))
        rec = [k for k, o in enumerate(opts) if o["recommended"]][:1]
        for k, o in enumerate(opts):
            o["recommended"] = [k] == rec
        out.append({"question": text if len(groups) == 1 else " ".join(g["text"]), "options": opts})
    return out


def _kind(opts: list[dict]) -> str:
    labels = sorted(o["label"].lower() for o in opts)
    return "yesno" if labels == ["no", "yes"] else "decision" if opts else "open"


def parse(text: str) -> list[dict]:
    """The asks a message marks, in order: ``{n, question, kind, options:
    [{label, detail, recommended}], recommended (index or -1), line, end}``
    (``line``..``end``: its lines, end exclusive). [] for an unmarked one."""
    lines = _OTHER_SPACE.sub(" ", _LINE_BREAKS.sub("\n", text or "")).split("\n")
    bare = [_QUOTE.sub("", ln, count=1) for ln in lines]
    fenced, out, i = False, [], 0
    while i < len(lines):
        ln = bare[i]
        if _FENCE.match(ln):
            fenced = not fenced
            i += 1
            continue
        m = None if fenced else _ASK.match(ln)
        if not m:
            i += 1
            continue
        start, nested = i, bool(_LIST_LEAD.search(m.group("lead") or ""))
        ind = _indent(m.group("ind"))
        q = re.sub(r"(?:\*\*|__)[ \t]*$", "", m.group("q")).strip()
        i += 1
        if not q:
            # "**Ask:**" alone: the question is the next line of words.
            j = i
            while j < len(lines) and not bare[j].strip():
                j += 1
            if j < len(lines) and not _ITEM.match(bare[j]) and not _ASK.match(bare[j]) and not _FENCE.match(bare[j]):
                q, i = bare[j].strip(), j + 1
        opts: list[dict] = []
        end = i
        j, gap = i, 0
        while j < len(lines):
            b = bare[j]
            if not b.strip():
                gap += 1
                if gap > 1:
                    break
                j += 1
                continue
            it = _ITEM.match(b)
            if not it or _ASK.match(b) or _FENCE.match(b):
                break
            if nested and _indent(it.group("ind")) <= ind:
                break       # a sibling of a listed ask, not an option of it
            o = _option(it.group("t"))
            if o:
                opts.append(o)
            gap = 0
            j += 1
            end = j
        i = end
        question = _plain(q)[:QUESTION_MAX]
        if not question:
            continue
        tag = " ".join((m.group("kind") or "").split())
        if opts or _OPEN_TAG.match(tag):
            found = [(question, opts)]
        elif _YESNO_TAG.match(tag):
            found = [(question, _yes_no(_plain(q)))]
        else:
            # No list: the options its words give, Yes and No for a yes/no
            # question, two asks for two questions.
            found = [(g["question"][:QUESTION_MAX], g["options"]) for g in inline(_plain(q))] or [(question, [])]
        for question, opts in found:
            rec = next((k for k, o in enumerate(opts) if o["recommended"]), -1)
            for k, o in enumerate(opts):
                o["recommended"] = k == rec
            out.append({"n": len(out), "question": question, "kind": _kind(opts), "options": opts,
                        "recommended": rec, "line": start, "end": max(end, start + 1)})
    return out


def answer_text(ask: dict, option: str, comment: str) -> str:
    """The person's message for an answer: ``Re “<ask>”: <option>`` and their
    comment under it (or after the colon, with no option)."""
    head = f"Re “{ask['question']}”: "
    option, comment = (option or "").strip(), (comment or "").strip()
    if option and comment:
        return f"{head}{option}\n\n{comment}"
    return head + (option or comment)


def check(ask: dict, option: str) -> str | None:
    """The option as the ask names it (case as written), or None when the ask
    has no such option. "" stays "" (a comment alone)."""
    option = (option or "").strip()
    if not option:
        return ""
    return next((o["label"] for o in ask["options"] if o["label"].lower() == option.lower()), None)


def validated(questions, warnings: list | None = None) -> list[dict]:
    """Normalize tool input, refusing any question that cannot make a card.
    A question with no options gets them from its words as an ``Ask:`` does
    (Yes and No for a yes/no question), and two questions in one become two
    cards (with ``yesno`` too). Every case without options but plain Yes/No,
    and two questions sharing one set of options, adds a line to
    ``warnings``."""
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a non-empty list")
    if len(questions) > 10:
        raise ValueError("questions may contain at most 10 questions")
    out = []
    for n, item in enumerate(questions):
        where = f"questions[{n}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        q = item.get("question")
        if not isinstance(q, str) or not q.strip() or len(q.strip()) > QUESTION_MAX:
            raise ValueError(f"{where}.question must be 1-{QUESTION_MAX} characters")
        yesno = item.get("yesno", False)
        if not isinstance(yesno, bool):
            raise ValueError(f"{where}.yesno must be true or false")
        raw = item.get("options", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise ValueError(f"{where}.options must be a list")
        if yesno and raw:
            raise ValueError(f"{where}: choose yesno or options, not both")
        if raw and not 1 <= len(raw) <= 6:
            raise ValueError(f"{where}.options must have 1-6 options")
        opts = []
        if yesno:
            opts = [{"label": label, "detail": "", "recommended": False} for label in ("Yes", "No")]
        else:
            for k, opt in enumerate(raw):
                loc = f"{where}.options[{k}]"
                if not isinstance(opt, dict):
                    raise ValueError(f"{loc} must be an object")
                label = opt.get("label")
                if not isinstance(label, str) or not label.strip() or len(label.strip()) > LABEL_MAX:
                    raise ValueError(f"{loc}.label must be 1-{LABEL_MAX} characters")
                detail = opt.get("detail", "")
                if not isinstance(detail, str):
                    raise ValueError(f"{loc}.detail must be text")
                rec = opt.get("recommended", False)
                if not isinstance(rec, bool):
                    raise ValueError(f"{loc}.recommended must be true or false")
                opts.append({"label": label.strip(), "detail": detail.strip(), "recommended": rec})
        parts = [(" ".join(q.split()), opts)]
        found = inline(q)
        if not opts:
            # Its words give the options; two questions are two cards.
            parts = [(g["question"], g["options"]) for g in found] or [(" ".join(q.split()), [])]
            if warnings is not None:
                said = "; ".join(" / ".join(o["label"] for o in o2) for _, o2 in parts if o2)
                if len(parts) > 1:
                    warnings.append(f"{where} holds {len(parts)} questions; each became its own card. "
                                    "Ask each as its own question, with its options.")
                if any(not o2 for _, o2 in parts):
                    warnings.append(f"{where} has no options and is not a yes/no question, so its card is a comment "
                                    "box only. Give it 2-6 options (or yesno: true) so it is answered with one click.")
                elif any(_kind(o2) != "yesno" for _, o2 in parts):
                    warnings.append(f"{where} has no options; its buttons were taken from its words ({said}). "
                                    "Pass the options explicitly.")
        elif yesno and len(found) > 1:
            parts = [(g["question"], [dict(o) for o in opts]) for g in found]
            if warnings is not None:
                warnings.append(f"{where} holds {len(parts)} questions; each became its own Yes/No card. "
                                "Ask each as its own question.")
        elif len(found) > 1:
            raise ValueError(f"{where} holds {len(found)} questions but one set of options; ask each as its own "
                             "question with its own options")
        for question, opts in parts:
            if len({o["label"].casefold() for o in opts}) != len(opts):
                raise ValueError(f"{where}.options have duplicate labels")
            recommended = [k for k, o in enumerate(opts) if o["recommended"]]
            if len(recommended) > 1:
                raise ValueError(f"{where}.options may have at most one recommended option")
            labels = sorted(o["label"].lower() for o in opts)
            kind = "yesno" if yesno or labels == ["no", "yes"] else "decision" if opts else "open"
            out.append({"n": len(out), "question": question[:QUESTION_MAX], "kind": kind,
                        "options": opts, "recommended": recommended[0] if recommended else -1})
    if len(out) > 10:
        raise ValueError("questions may contain at most 10 questions")
    return out


_DECISION = re.compile(r"^[ \t]*(?:\*\*)?Decision needed:(?:\*\*)?[ \t]*(.+)$", re.I | re.M)
_REPORTED_QUESTION = re.compile(r"^(?:(?:the )?(?:user|ceo)|you)\s+(?:asked|said|wrote|wondered)\b", re.I)
_TRAIL_OPTION = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?(?:[A-Ca-c]|[1-6])"
    r"(?:[.)]|[ \t]*\(recommended\):|:)(?:\*\*)?[ \t]+(.+)$", re.I)
_OPTIONS_LEAD = re.compile(r"\b(?:options?|choices?|alternatives?)\b[^:\n]*:[ \t*_]*$", re.I)


def _option_line(line: str, loose: bool):
    return _TRAIL_OPTION.match(line) or (_ITEM.match(line) if loose else None)


def safety(text: str) -> list[dict]:
    """Conservative fallback for a final, direct question in plain agent prose."""
    if parse(text):
        return []
    clean = []
    fenced = False
    for line in _LINE_BREAKS.sub("\n", text or "").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and not line.lstrip().startswith(">"):
            clean.append(line)
    body = "\n".join(clean).strip()
    if not body:
        return []
    parts = re.split(r"\n[ \t]*\n", body)
    tail = parts[-1].strip()
    decision = list(_DECISION.finditer(body))
    # A list counts as the options when its lines are lettered or numbered;
    # any list does after "Decision needed:" or under a line naming the
    # options ("Your options:"), unless it asks yes or no.
    loose = False
    if decision and len(body) - decision[-1].start() <= 1000:
        q = decision[-1].group(1).strip()
        following = body[decision[-1].end():].strip().splitlines()
        # A plain list under a yes/no question is its reasons, not its options.
        loose = not any(_kind(g["options"]) == "yesno" for g in inline(_plain(q)))
    elif tail.endswith("?"):
        tail_lines = tail.splitlines()
        q = tail_lines[-1].strip()
        if _REPORTED_QUESTION.match(q):
            return []
        following = []
        for preceding in (tail_lines[:-1], parts[-2].strip().splitlines() if len(parts) > 1 else []):
            lead = bool(preceding) and bool(_OPTIONS_LEAD.search(preceding[0]))
            opts = [line for line in preceding if _option_line(line, lead)]
            if len(opts) >= 2 and len(preceding) - len(opts) <= 1:
                following, loose = opts, lead
                break
    else:
        return []
    q = re.sub(r"^(?:[-*]|[1-6][.)])[ \t]+", "", q).strip("* ")
    if not q or len(q) > QUESTION_MAX:
        return []
    options = []
    for line in following:
        match = _option_line(line, loose)
        if match:
            opt = _option(match.group(1) if match.re is _TRAIL_OPTION else match.group("t"))
            if opt:
                options.append(opt)
        elif line.strip() and options:
            break
    if not 1 <= len(options) <= 6:
        options = []
    if options:
        rec = next((k for k, o in enumerate(options) if o["recommended"]), -1)
        for k, opt in enumerate(options):
            opt["recommended"] = k == rec
        return [{"n": 0, "question": _plain(q), "kind": _kind(options), "options": options, "recommended": rec}]
    out = []
    for g in inline(_plain(q)):
        rec = next((k for k, o in enumerate(g["options"]) if o["recommended"]), -1)
        out.append({"n": len(out), "question": g["question"][:QUESTION_MAX], "kind": _kind(g["options"]),
                    "options": g["options"], "recommended": rec})
    return out


def of_message(message: dict, eligible: bool = True) -> list[dict]:
    if not eligible:
        return []
    stored = message.get("asks")
    if isinstance(stored, list):
        return stored
    parsed = parse(message.get("text") or "")
    return parsed if parsed else safety(message.get("text") or "")


# ---------------------------------------------------------------------------
# Which asks are still open (the Needs you list)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_SID_CACHE: dict[str, tuple] = {}       # sid → (stat, [(mid, ts, [questions])])
_ROOM_CACHE: dict[str, tuple] = {}      # room id → (updatedAt, [(mid, ts, [questions])])
_RESULT: tuple[float, dict] = (0.0, {})


def _marked(mid: str, ts: float, text: str, who: str, message: dict | None = None) -> tuple | None:
    found = of_message(message or {"text": text})
    return (mid, ts, [a["question"] for a in found], who) if found else None


def _session_asks(room: dict, sid: str, who: str) -> list[tuple]:
    if _d.room_po_id(room):
        return []
    st = _d.points._session_stat(sid)
    hit = _SID_CACHE.get(sid)
    if hit is not None and st is not None and hit[0] == st:
        return hit[1]
    turns = _d.solo_turns(room, sid) or {}
    found = []
    for mid, t in turns.items():
        if t.get("role") == "user":
            continue
        x = _marked(mid, _d._turn_epoch(t.get("timestamp")), t.get("text") or "", who)
        if x:
            found.append(x)
    if st is not None:
        _SID_CACHE[sid] = (st, found)
    return found


def message_asks(room: dict) -> list[tuple]:
    """A room's agents' messages that mark asks: ``[(mid, ts, [questions],
    who)]``, from what the room holds (a team's messages)."""
    has_po = bool(_d.room_po_id(room))
    agents = {p.get("identity") for p in room.get("participants", [])
              if p.get("kind") == "agent" and not str(p.get("role") or "").lower().startswith("reviewer")}
    out = []
    for m in room.get("messages") or []:
        if m.get("from") in agents and (not has_po or m.get("askAudience") == "user") \
                and m.get("kind") not in ("report", "notice") \
                and m.get("askAudience") != "po" and not m.get("rang") \
                and (m.get("to") or "").lower() in ("", "all", "user"):
            x = _marked(m.get("id", ""), float(m.get("ts") or 0), m.get("text") or "",
                        m.get("from") or "", m)
            if x and x[0]:
                out.append(x)
    return out


def _room_asks(summary: dict) -> list[tuple]:
    rid = summary.get("id", "")
    agents = [p for p in summary.get("participants", []) if p.get("kind") == "agent"]
    if (summary.get("mode") or "") == "solo":
        room = _d.chatroom.get_room(rid)
        if room is None:
            return []
        out = message_asks(room)
        for p in agents:
            sid = (p.get("sessionId") or "").strip()
            if not sid:
                continue
            out += _session_asks(room, sid, p.get("identity") or "")
        return out
    hit = _ROOM_CACHE.get(rid)
    if hit is not None and hit[0] == summary.get("updatedAt"):
        return hit[1]
    room = _d.chatroom.get_room(rid)
    found = message_asks(room) if room else []
    _ROOM_CACHE[rid] = (summary.get("updatedAt"), found)
    return found


def answered(room_id: str) -> dict:
    """The asks answered in a room: ``{mid: {"<n>": answer}}``."""
    if not _d.points.exists(room_id):
        return {}
    return _d.points.load(room_id).get("asks") or {}


def open_in(summary: dict, now: float | None = None) -> list[dict]:
    """The asks of a room still waiting for the person, oldest first:
    ``[{mid, n, question, ts, who}]``. An ask counts until it is answered
    (an unread one never ages out: GitHub issue 11), and until the person
    writes in the chat after it (``asksSettledAt``: they answered in words,
    or set it aside); one whose message was approved with a thumbs up ("go
    with your recommendation") is answered if it has a recommendation. The
    page's ``openAsks`` keeps the same rules. Needs you also lets go of an
    ask the person has read once it is a day old (attention.read_and_old):
    it stays open in the chat, for them to come back to; and at once of one
    they said they are done with (attention.set_aside: Done with this)."""
    now = time.time() if now is None else now
    rid = summary.get("id", "")
    marked = _room_asks(summary)
    if not marked:
        return []
    led = _d.points.load(rid) if _d.points.exists(rid) else {}
    done, approved = led.get("asks") or {}, led.get("approvals") or {}
    settled = float(led.get("asksSettledAt") or 0)
    if not _d.room_po_id(summary):
        settled = max(settled, *(float(p.get("answeredAt") or 0)
                                  for p in summary.get("participants", []) if p.get("kind") == "agent"))
    out = []
    for mid, ts, qs, who in marked:
        if ts and ts < settled:
            continue
        if _d.attention.read_and_old(summary, ts, now) or _d.attention.set_aside(summary, ts):
            continue
        got = done.get(mid) or {}
        for n, q in enumerate(qs):
            if str(n) in got:
                continue
            if mid in approved and _approved_settles(rid, mid, n):
                continue
            out.append({"mid": mid, "n": n, "question": q, "ts": ts, "who": who})
    out.sort(key=lambda a: (a["ts"], a["n"]))
    return out


def _approved_settles(room_id: str, mid: str, n: int) -> bool:
    found = balloon_asks(room_id, mid)
    return n < len(found) and found[n]["recommended"] >= 0


def balloon_asks(room_id: str, mid: str) -> list[dict]:
    room = _d.chatroom.get_room(room_id)
    if room is None:
        return []
    has_po = bool(_d.room_po_id(room))
    for message in room.get("messages") or []:
        if message.get("id") == mid:
            agents = {p.get("identity") for p in room.get("participants", [])
                      if p.get("kind") == "agent" and not str(p.get("role") or "").lower().startswith("reviewer")}
            if message.get("from") not in agents or message.get("kind") in ("notice",) \
                    or message.get("rang") or (message.get("to") or "").lower() not in ("", "all", "user") \
                    or message.get("askAudience") == "po" or (has_po and message.get("askAudience") != "user"):
                return []
            return of_message(message)
    if has_po:
        return []
    try:
        text = _d.points._balloon_text(room_id, mid, {})
    except Exception:       # noqa: BLE001
        return []
    return of_message({"text": text or ""})


def _same(a, b) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def _place(found: list[dict], k: str, ans, taken: dict) -> str | None:
    """Where an answer recorded as ask ``k`` goes now: its own number when
    that ask quotes it, else the ask that quotes it (the first part of one
    split in two) nearest at or after ``k`` — a split only moves asks down —
    and not taken by another answer. None when none fits."""
    at = int(k) if str(k).isdigit() else -1
    q = " ".join(str((ans or {}).get("question") or "").split()).casefold()
    if not q:
        return None if str(k) in taken else str(k)
    def own(i: int) -> str:
        return " ".join(found[i]["question"].split()).casefold()
    for test in (lambda i: own(i) == q, lambda i: q.startswith(own(i))):
        fits = [i for i in range(len(found)) if test(i) and str(i) not in taken]
        if fits:
            return str(min(fits, key=lambda i: (i < at, abs(i - at))))
    return None


def realign(room_id: str) -> int:
    """A ledger from before #195 keyed its answers by the ask's number when an
    ask holding two questions was one ask: each answer moves to the ask whose
    question it quotes (the first part of a split one), once. An answer with
    no ask of its own leaves the ledger as it was (it is tried again at the
    next start). How many messages' answers moved."""
    P = _d.points
    with P._LOCK:
        led = P.load(room_id)
        if led.get("asksKeys", 1) >= P.ASKS_KEYS:
            return 0
        moved = 0
        for mid, got in list(led["asks"].items()):
            if not isinstance(got, dict) or not got:
                continue
            found = balloon_asks(room_id, mid)
            new: dict = {}
            for k, ans in sorted(got.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
                j = _place(found, k, ans, new)
                if j is None:
                    _d.points._log(f"{room_id}: answer {mid}:{k} has no ask of its own; the ledger stays as it was")
                    return 0
                new[j] = ans
            if new != got:
                led["asks"][mid] = new
                moved += 1
        led["asksKeys"] = P.ASKS_KEYS
        P._save(room_id, led)
    forget()
    return moved


def realign_all() -> None:
    """:func:`realign` every ledger on disk (once, at the hub's start)."""
    for f in sorted(_d.points._dir().glob("room-*.json")):
        try:
            n = realign(f.stem)
            if n:
                print(f"[asks] {f.stem}: answers of {n} message(s) moved to their asks", flush=True)
        except Exception as e:      # noqa: BLE001 — one room never stops the others
            print(f"[asks] {f.stem}: answers not realigned: {e!r}", flush=True)


def open_by_room(summaries: list[dict], now: float | None = None) -> dict[str, list[dict]]:
    """:func:`open_in` for every room, worked out at most every
    :data:`CACHE_S` (the attention poll asks every second and a half)."""
    global _RESULT
    now = time.time() if now is None else now
    with _LOCK:
        if now - _RESULT[0] < CACHE_S:
            return _RESULT[1]
        out = {}
        for s in summaries:
            if not s.get("launched", True) or _d.normalize_workflow(s.get("workflow")) == "done":
                continue        # a draft asks nothing yet; a Done task's asks are over
            try:
                got = open_in(s, now)
            except Exception as e:      # noqa: BLE001 — one room's trouble hides no other's
                print(f"[asks] {s.get('id')}: open asks not read: {e!r}", flush=True)
                continue
            if got:
                out[s["id"]] = got
        _RESULT = (now, out)
        return out


def forget() -> None:
    """Drop the worked-out open asks (an answer was just recorded)."""
    global _RESULT
    with _LOCK:
        _RESULT = (0.0, {})
