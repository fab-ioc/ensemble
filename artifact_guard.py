"""PreToolUse hook for hub-launched Claude agents: no claude.ai artifact, page
or doc unless the CEO asked for one (rule P171, enforced by P469).

Claude Code's own system prompt tells Claude to publish Artifacts on its own
initiative, so the rule in the brief and the skill is not enough. The hub
registers this script in the ``--settings`` file of every PO room and every
task agent (owner and reviewers; not adopted sessions, not the person's own
terminals) for the tools that publish to claude.ai (``GUARDED_NAMES``,
``GUARDED_PREFIXES``). For each such call:

* A read-only call (``READ_ONLY``: ``Artifact`` with action ``read`` or
  ``list``, a Claude Docs ``read`` or ``query``) goes through. It publishes,
  shows and changes nothing, and the CEO who pastes an artifact link expects
  the agent to be able to read it, whoever relays the link.
* Anything else asks the hub (``POST /api/agent/artifact-check``) whether the
  CEO's own latest message in that conversation asks for a claude.ai
  artifact, page or doc. The hub reads the conversation's last user turn that
  its input journal (``input_provenance``) recorded as typed by the person,
  and applies ``asks_for_web_artifact`` to its text. Only an explicit yes
  lets the call through.
* Every other outcome denies the call with ``DENY_REASON``, which the agent
  reads: no answer from the hub, a hub that says no, a terminal it does not
  know, this script run without the hub's environment. A deny is the safe
  side: it only stops a publication, and the agent writes a file instead.

Input it cannot read (not JSON, no tool name, another event) is denied too:
the hook runs only for the guarded tools. Standard library only; prints one
JSON answer or nothing (a tool it does not guard), and always exits 0.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
from urllib.parse import urlsplit

DENY_REASON = (
    "Ensemble rule P171: no claude.ai artifact, page or doc unless the CEO asks for one "
    "in so many words. Write it as a file in the project's Documents folder (an .html page "
    "for what can be shown, .md for text) and link it by name.")

# The tools that publish to claude.ai or act on what is published there.
GUARDED_NAMES = frozenset({"Artifact", "ArtifactComments", "ArtifactData", "ArtifactCheck"})
GUARDED_PREFIXES = ("mcp__claude_ai_Claude_Docs__", "mcp__claude-artifacts__")
# Claude Code's matcher for them (a regex). The script checks the name again
# itself, so a matcher that catches more costs nothing.
MATCHER = "Artifact.*|mcp__claude_ai_Claude_Docs__.*|mcp__claude-artifacts__.*"

# Calls that only read: (tool, the actions that read) or a Docs tool's suffix.
READ_ONLY_ARTIFACT_ACTIONS = frozenset({"read", "list"})
READ_ONLY_DOCS = ("mcp__claude_ai_Claude_Docs__read", "mcp__claude_ai_Claude_Docs__query")

CHECK_PATH = "/api/agent/artifact-check"
# The hub reads the conversation's transcript to answer; a long one takes a
# moment. Claude Code's own limit on the hook is longer (dashboard).
TIMEOUT_S = 6.0

# What counts as asking: an instruction, not a mention. The CEO's latest
# typed message passes when one of its sentences is an instruction to put
# something on claude.ai, and nothing in the message takes it back:
#
# * quoted text ("...", “...”, '...', `...`) is not the CEO's own words and
#   is ignored;
# * the sentence opens with a VERBS verb (make, publish, put, ...), with
#   nothing before it but LEADS words ("please", "can you", "I want you to",
#   "then", ...);
# * at most eight words after that verb comes claude.ai, Claude Docs (or Doc)
#   or "Claude artifact(s)"; or the bare word artifact(s), but only after
#   PUBLISH_VERBS (publish, share, post, upload) and in a sentence that names
#   no LOCAL_PLACES word (folder, file, Documents, ...), since an artifact
#   can be a local one;
# * that sentence has no NEGATORS word (or a word ending in "n't") anywhere;
# * no later sentence withdraws it: one that cancels ("I changed my mind",
#   "Cancel that", "scratch that", "hold off", "on second thought"), or any
#   with a negator ("No thanks.", "I'd rather not.", "Actually, don't.")
#   except a content rule: a sentence opening with don't / do not / never
#   and a verb that is not WITHDRAW_VERBS (publish, make, do, mind, ...),
#   naming no claude.ai or artifact and not pointing back (it, that, this,
#   them). "Do not include secrets." is a rule; "Don't publish it." and
#   "Don't include it." withdraw. Known limit (a false deny, on the safe
#   side): "Don't use my name." withdraws, since "use" is a withdraw verb;
# * and no mention anywhere is negated (a NEGATORS word among the six words
#   before claude.ai, Claude Docs or artifact in its sentence).
#
# Passes: "Make it a claude.ai artifact", "please publish the plan as an
# artifact", "Can you put it in Claude Docs?", "Don't write a file. Publish it
# to claude.ai.", "Please publish the roadmap on claude.ai. Do not include
# secrets." Does not pass: the CEO's whole P469 message, "never create an
# artifact outside Ensemble", "don't publish to claude.ai", "These documents
# are known as artifacts", "The instructions say to create an artifact in the
# project folder", "Make a claude.ai artifact? No, save it in Documents",
# "make it an artifact" (bare artifact without a publish verb), "Publish the
# artifact in the project Documents folder" (a local place), 'Please share
# the phrase "publish it on claude.ai" in a local report' (quoted), "No
# artifacts last time. This time, publish it as an artifact", "publish it on
# claude.ai, not as a file", "Publish it on claude.ai. Actually, don't.",
# "Publish it on claude.ai. No thanks.", "Publish it on claude.ai. I changed
# my mind.", and
# any synonym ("a web page", "share it online").
# Deliberately literal and on the safe side: a real ask in other words is
# denied, and the CEO can say it again with claude.ai in it.
_KEYWORD = re.compile(r"claude\.ai\b|\bclaude\s+(?:docs?|artifacts?)\b|\b(?P<bare>artifacts?)\b",
                      re.I)
_QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\u2018[^\u2019\n]*\u2019(?![a-z])'
                     r"|(?<![\w'])'[^'\n]*'(?![\w'])|`[^`\n]*`", re.I)
_SENTENCE_END = re.compile(r"[.!?;\n](?=\s|$)")
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
NEGATORS = frozenset({"never", "not", "no", "dont", "without", "avoid", "stop", "nor",
                      "instead", "rather", "unless", "except"})
VERBS = frozenset({"make", "create", "publish", "put", "post", "share", "turn", "build",
                   "render", "upload", "save", "send", "move", "use", "do", "draw", "give"})
PUBLISH_VERBS = frozenset({"publish", "share", "post", "upload"})
LEADS = frozenset({"please", "kindly", "now", "then", "and", "also", "so", "ok", "okay", "yes",
                   "just", "can", "could", "would", "will", "you", "i", "we", "let's", "lets",
                   "us", "want", "need", "like", "i'd", "to", "go", "ahead", "finally", "next"})
LOCAL_PLACES = frozenset({"folder", "folders", "file", "files", "directory", "dir", "documents",
                          "repo", "repository", "disk", "local", "locally", "path", "worktree",
                          "branch", "here"})
BACK_REFERENCES = frozenset({"it", "that", "this", "them"})
WITHDRAW_VERBS = frozenset({"make", "create", "publish", "put", "post", "share", "turn", "build",
                            "render", "upload", "save", "send", "move", "do", "mind", "bother",
                            "go", "proceed", "worry", "use", "release", "show", "add"})
_CANCEL = re.compile(r"\b(?:chang(?:e|ed|ing)\s+(?:my|our)\s+minds?|cancel\w*|scratch\s+that"
                     r"|forget\s+(?:it|that|this|about)|hold\s+off|on\s+second\s+thoughts?"
                     r"|never\s*mind|nevermind|wait)\b", re.I)
_WINDOW = 6
_VERB_REACH = 8


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("\u2019", "'"))


def _negation(words) -> bool:
    return any(w in NEGATORS or w.endswith("n't") for w in words)


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        spans.append((start, m.end()))
        start = m.end()
    spans.append((start, len(text)))
    return spans


def _instruction(words: list[str], bare: bool) -> bool:
    """Whether the words of a sentence up to a keyword open with a request."""
    for i, w in enumerate(words):
        if w in VERBS:
            return (len(words) - i <= _VERB_REACH
                    and (not bare or w in PUBLISH_VERBS))
        if w not in LEADS:
            return False
    return False


def _withdraws(sentence: str) -> bool:
    """Whether a sentence after an ask takes it back: on the safe side, any
    negated sentence does unless it is a content rule (see the comment above)."""
    if _CANCEL.search(sentence):
        return True
    words = _words(sentence)
    if not _negation(words):
        return False
    if _KEYWORD.search(sentence):
        return True
    i = 0
    while i < len(words) and words[i] in LEADS:
        i += 1
    if words[i:i + 1] in (["don't"], ["dont"], ["never"]):
        i += 1
    elif words[i:i + 2] == ["do", "not"]:
        i += 2
    else:
        return True                         # "No thanks.", "I'd rather not."
    verb, rest = words[i:i + 1], words[i + 1:]
    return (not verb or verb[0] in WITHDRAW_VERBS or _negation(verb)
            or any(w in BACK_REFERENCES for w in rest))


def asks_for_web_artifact(text: str) -> bool:
    """Whether a message of the CEO's explicitly asks for a claude.ai artifact,
    page or doc (see the comment above for what passes)."""
    if not isinstance(text, str) or not text.strip():
        return False
    text = _QUOTED.sub(" ", text)
    spans = _sentences(text)
    ask = None                              # index of the first asking sentence
    for i, (start, end) in enumerate(spans):
        sentence = text[start:end]
        for m in _KEYWORD.finditer(sentence):
            before = _words(sentence[:m.start()])
            if _negation(before[-_WINDOW:]):
                return False                # a negated mention vetoes the message
            bare = bool(m.group("bare"))
            if (ask is None and _instruction(before, bare)
                    and not (bare and LOCAL_PLACES.intersection(_words(sentence)))):
                ask = i
    if ask is None:
        return False
    start, end = spans[ask]
    if _negation(_words(text[start:end])):
        return False
    return not any(_withdraws(text[s:e]) for s, e in spans[ask + 1:])


def guarded(tool: str) -> bool:
    return isinstance(tool, str) and (tool in GUARDED_NAMES or tool.startswith(GUARDED_PREFIXES))


def read_only(tool: str, tool_input) -> bool:
    if tool in READ_ONLY_DOCS:
        return True
    if tool == "Artifact" and isinstance(tool_input, dict):
        action = tool_input.get("action")
        return isinstance(action, str) and action.strip().lower() in READ_ONLY_ARTIFACT_ACTIONS
    return False


def deny() -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": DENY_REASON}}


def ask_hub(env: dict, data: dict, timeout: float = TIMEOUT_S) -> bool:
    """Whether the hub says the CEO asked for this publication. Never raises:
    anything but a clear yes is no."""
    conn = None
    try:
        url = env.get("ENSEMBLE_HOOK_URL", "")
        room, identity = env.get("ENSEMBLE_HOOK_ROOM", ""), env.get("ENSEMBLE_HOOK_IDENTITY", "")
        pty_id = env.get("ENSEMBLE_PTY_ID", "")
        u = urlsplit(url)
        if u.scheme != "http" or not u.hostname or not (room and identity and pty_id):
            return False
        tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        body = json.dumps({
            "room": room, "identity": identity, "ptyId": pty_id,
            "sessionId": str(data.get("session_id") or ""),
            "tool": str(data.get("tool_name") or ""),
            "action": str(tool_input.get("action") or "")[:40],
        }).encode("utf-8")
        # http.client, not urllib: no proxy from the environment for loopback.
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
        conn.request("POST", CHECK_PATH, body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            return False
        answer = json.loads(resp.read(4096).decode("utf-8") or "{}")
        return isinstance(answer, dict) and answer.get("allow") is True
    except Exception:                                        # noqa: BLE001 — no answer is no
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass


def handle(data, env: dict | None = None, ask=ask_hub) -> dict | None:
    """The hook's answer to one PreToolUse event: ``deny()`` or None (carry on)."""
    # It runs only for the guarded tools (MATCHER), so input it cannot use is
    # a guarded call it cannot read: denied, never let through.
    if not isinstance(data, dict) or data.get("hook_event_name") != "PreToolUse":
        return deny()
    tool = data.get("tool_name")
    if not isinstance(tool, str) or not tool:
        return deny()
    if not guarded(tool):
        return None
    if read_only(tool, data.get("tool_input")):
        return None
    return None if ask(dict(os.environ) if env is None else env, data) else deny()


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            data = None                     # unreadable: handle denies it
        out = handle(data)
    except BaseException:                                    # noqa: BLE001 — still the rule
        out = deny()
    if out is not None:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
