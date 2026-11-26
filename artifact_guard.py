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

Standard library only; prints one JSON answer or nothing, and always exits 0.
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

# What counts as asking. The CEO's latest typed message passes when it names
# claude.ai, Claude Docs (or Doc), or an artifact as something to make: a
# REQUEST_WORDS word (make, publish, as, ...) among the six words before that
# keyword in its sentence, and none of NEGATORS (or a word ending in "n't")
# there. A negated mention anywhere in the message vetoes it. So "make it a
# claude.ai artifact", "publish the plan as an Artifact", "put it in Claude
# Docs" pass; "never create an artifact outside Ensemble", "don't publish to
# claude.ai", "no artifacts", a complaint that only names them ("ensemble
# still creates artifacts on the web"), and any message without those words
# do not; nor does the CEO's whole P469 message. Deliberately literal and on
# the safe side: no synonyms ("a web page", "share it online" do not pass),
# "No artifacts last time; this time make one" does not pass, and a request
# word must be there ("an artifact, please" does not pass). A message that
# uses "artifact" in another sense ("save the CI artifacts") can pass; the
# agent still has to choose to publish, and the brief tells it not to unless
# asked.
_KEYWORD = re.compile(r"claude\.ai\b|\bclaude\s+docs?\b|\bartifacts?\b", re.I)
_SENTENCE_END = re.compile(r"[.!?;\n](?=\s|$)")
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
NEGATORS = frozenset({"never", "not", "no", "dont", "without", "avoid", "stop", "nor",
                      "instead", "rather", "unless", "except"})
REQUEST_WORDS = frozenset({"make", "publish", "create", "put", "post", "share", "turn", "build",
                           "render", "use", "upload", "save", "send", "write", "draw", "give",
                           "want", "need", "like", "as", "into", "via"})
_WINDOW = 6


def _window(before: str) -> list[str]:
    clause = _SENTENCE_END.split(before)[-1].lower().replace("\u2019", "'")
    return _WORD.findall(clause)[-_WINDOW:]


def _negated(words: list[str]) -> bool:
    return any(w in NEGATORS or w.endswith("n't") for w in words)


def asks_for_web_artifact(text: str) -> bool:
    """Whether a message of the CEO's explicitly asks for a claude.ai artifact,
    page or doc (see the comment above for what passes)."""
    if not isinstance(text, str) or not text.strip():
        return False
    windows = [_window(text[:m.start()]) for m in _KEYWORD.finditer(text)]
    if any(_negated(w) for w in windows):
        return False
    return any(REQUEST_WORDS.intersection(w) for w in windows)


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
    if not isinstance(data, dict) or data.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    tool = data.get("tool_name")
    if not guarded(tool):
        return None
    if read_only(tool, data.get("tool_input")):
        return None
    return None if ask(dict(os.environ) if env is None else env, data) else deny()


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        try:
            data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except ValueError:
            data = {}
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
