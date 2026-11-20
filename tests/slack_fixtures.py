"""Made-up Slack answers in the exact text layout Claude's Slack connection uses,
and a stand-in for `claude -p`, for the Slack source's tests.

The layouts were copied from real answers (2026-11-10 and 2026-11-16). Every
id, name, address and text here is made up: the repo is public
(tests/test_slack_public.py checks it).
"""
from __future__ import annotations

import json
import os
import stat
import textwrap

ME = "U000TEST01"
OTHER = "U000TEST02"
THIRD = "U000TEST03"
GROUP = "S000TEST01"
GROUP2 = "S000TEST02"
CHANNEL = "C000TEST01"
CHANNEL2 = "C000TEST02"
CHANNEL3 = "C000TEST03"
DM = "D000TEST01"
SLACKBOT_DM = "D000TEST09"
WORKSPACE = "example.slack.com"
PREFIX = "mcp__claude_ai_Slack__"

ALL_TOOLS = [
    "slack_add_reaction", "slack_create_canvas", "slack_get_reactions", "slack_list_channel_members",
    "slack_list_user_channels", "slack_read_canvas", "slack_read_channel", "slack_read_file",
    "slack_read_thread", "slack_read_user_profile", "slack_schedule_message", "slack_search_channels",
    "slack_search_emojis", "slack_search_public", "slack_search_public_and_private", "slack_search_users",
    "slack_send_message", "slack_send_message_draft", "slack_update_canvas",
]


def link(channel_id, ts, thread_ts=None):
    url = f"https://{WORKSPACE}/archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts:
        url += f"?thread_ts={thread_ts}&cid={channel_id}"
    return url


def search_result(channel_id, channel_name, sender_id, sender_name, ts, text, bot=False, thread_ts=None):
    return {
        "channel": f"{channel_name} (ID: {channel_id})",
        "from": f"{sender_name} <{sender_name.lower().replace(' ', '.')}@example.com> (ID: {sender_id})"
        + (" [BOT]" if bot else ""),
        "ts": ts,
        "link": link(channel_id, ts, thread_ts),
        "text": text,
    }


def msg(channel_id, ts, text="hello", sender=OTHER, name="Ann Example", thread_ts=None, bot=False,
        channel_name="#made-up"):
    """A search result with sensible defaults."""
    return search_result(channel_id, channel_name, sender, name, ts, text, bot=bot, thread_ts=thread_ts)


def search_answer(results):
    """A slack_search_public_and_private answer as the tool returns it (a JSON string)."""
    if not results:
        return json.dumps({"results": "# Search Results for: \n\nNo results found."})
    parts = [f"# Search Results for: \n\n## Messages ({len(results)} result{'s' if len(results) != 1 else ''})\n"]
    for i, r in enumerate(results, 1):
        parts.append(
            f"### Result {i} of {len(results)}\n"
            f"Channel: {r['channel']}\n"
            f"From: {r['from']} \n"
            f"Time: 2026-11-10 10:00:00 CET\n"
            f"Message_ts: {r['ts']}\n"
            f"Permalink: [link]({r['link']})\n"
            f"Text: \n{r['text']}\n\n---\n\n"
        )
    return json.dumps({"results": "".join(parts)})


def channel_answer(messages, dm_id=SLACKBOT_DM, header="DM"):
    """A slack_read_channel answer. messages: [(sender_id, sender_name, ts, text)], newest first."""
    parts = [f"Channel: {header} ({dm_id})\n\n"]
    for uid, name, ts, text in messages:
        parts.append(f"=== Message from {name} ({uid}) at 2026-11-10 07:00:02 CET === \nMessage TS: {ts}\n{text}\n\n")
    return json.dumps({"messages": "".join(parts), "pagination_info": "There are no more messages available.\n"})


def thread_answer(first, replies, cursor=""):
    """A slack_read_thread answer. first and each reply: (sender_id, sender_name, ts, text)."""
    def block(m):
        uid, name, ts, text = m
        return (f"From: {name} <{name.lower().replace(' ', '.')}@example.com> ({uid})\n"
                f"Time: 2026-11-11 11:03:16 CET\nMessage TS: {ts}\n{text}\n")
    parts = ["=== THREAD PARENT MESSAGE ===\n", block(first), "Reactions: tada (2)\n\n",
             f"=== THREAD REPLIES ({len(replies)} total) ===\n\n"]
    for i, r in enumerate(replies, 1):
        parts.append(f"--- Reply {i} of {len(replies)} ---\n" + block(r) + "\n")
    more = (f"There are more messages in this thread. To view the next page, use cursor: `{cursor}`\n"
            if cursor else "There are no more messages in this thread.\n")
    return json.dumps({"messages": "".join(parts), "pagination_info": more})


def profile_answer(uid=ME, name="Me Example"):
    return json.dumps({"result": f"User ID: {uid}\nUsername: me.example\nDisplay Name: {name}\n"
                                 f"Real Name: {name}\nTitle: Made-up\nEmail: me@example.com\n"
                                 "Timezone: Europe/Amsterdam\nBot: No\n"})


def added(ts, group=GROUP):
    return ("USLACK", "Slack", ts,
            f"You have been added to the *made-up-on-call* (<!subteam^{group}>) user group by "
            f"<@{THIRD}|incident>. From now on, you will be notified when someone uses <!subteam^{group}>.")


def removed(ts, group=GROUP):
    return ("USLACK", "Slack", ts,
            f"You have been removed from the <!subteam^{group}> user group by <@{THIRD}|incident>. "
            f"You will no longer be notified when someone uses <!subteam^{group}>.")


def stream(queries, answers, tools_available=None, extra_calls=(), result_subtype="error_max_turns",
           prefix=PREFIX, errors=()):
    """Claude's stream-json output for a run that made the planned calls.

    answers: {label: answer text}. A label missing from answers gets no tool result.
    errors: labels whose tool result is an error. Returns a list of lines.
    """
    from sources.slack_connector import TOOL_FOR

    if tools_available is None:
        tools_available = sorted({prefix + TOOL_FOR[q.kind] for q in queries})
    events = [{"type": "system", "subtype": "init", "tools": tools_available,
               "mcp_servers": [{"name": "claude.ai Slack", "status": "connected"}]}]
    content = []
    for i, q in enumerate(queries):
        content.append({"type": "tool_use", "id": f"toolu_{i}", "name": prefix + TOOL_FOR[q.kind],
                        "input": dict(q.args)})
    for j, (name, args) in enumerate(extra_calls):
        content.append({"type": "tool_use", "id": f"toolu_x{j}", "name": name, "input": args})
    events.append({"type": "assistant", "message": {"content": content}})
    results = []
    for i, q in enumerate(queries):
        if q.label in errors:
            results.append({"type": "tool_result", "tool_use_id": f"toolu_{i}", "is_error": True,
                            "content": [{"type": "text", "text": "execution_failed: parameter_validation_failed"}]})
        elif q.label in answers:
            results.append({"type": "tool_result", "tool_use_id": f"toolu_{i}",
                            "content": [{"type": "text", "text": answers[q.label]}]})
    events.append({"type": "user", "message": {"content": results}})
    events.append({"type": "result", "subtype": result_subtype, "is_error": True, "total_cost_usd": 0.0009,
                   "duration_ms": 2000,
                   "usage": {"input_tokens": 2, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 3720,
                             "output_tokens": 300}})
    return [json.dumps(e) for e in events]


class FakeReader:
    """A reader for SlackSource(reader_factory=...): answers each read from
    `answer(query)` (a function returning the answer text, or None for no answer)
    and keeps every batch of reads in `batches`. Goes through the real
    read_stream, so the answers are read exactly as from a real run."""

    def __init__(self, answer, prefix=PREFIX):
        self.answer = answer
        self.prefix = prefix
        self.batches = []
        self.usage = {}
        self.runs = 0

    def read(self, queries):
        from sources.slack_connector import read_stream, add_usage
        self.batches.append(list(queries))
        self.runs += 1
        answers = {}
        for q in queries:
            a = self.answer(q)
            if a is not None:
                answers[q.label] = a
        result = read_stream(stream(queries, answers, prefix=self.prefix), queries, self.prefix)
        self.usage = add_usage(self.usage, result.usage)
        return result


# ---------- a stand-in for `claude -p`, run as a real program ----------

FAKE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stands in for `claude -p` in tests. A run with --allowedTools makes the
    calls listed in the prompt and answers them from answers.json next to this
    file: "by_match": [[text, answer], ...] (a call whose arguments, as JSON,
    contain the text gets that answer; the first match wins), else
    "by_tool": {tool short name: answer}. A run without --allowedTools is the
    probe: it lists every Slack tool and makes no call. Each command line is
    added to argv-all.jsonl."""
    import json, os, re, sys
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    with open(os.path.join(here, "argv-all.jsonl"), "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    answers = json.load(open(os.path.join(here, "answers.json")))
    prefix = answers.get("prefix", "mcp__claude_ai_Slack__")
    final = {"type": "result", "subtype": "error_max_turns", "is_error": True, "total_cost_usd": 0.0001,
             "duration_ms": 10, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 3700, "output_tokens": 200}}
    servers = [{"name": "claude.ai Slack", "status": answers.get("status", "connected")}]
    if "--allowedTools" not in sys.argv:
        tools = [prefix + t for t in answers.get("all_tools", [])]
        print(json.dumps({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": servers}))
        print(json.dumps(dict(final, subtype="success", is_error=False, result="ok")))
        sys.exit(0)
    calls = []
    for line in sys.argv[-1].splitlines():
        m = re.match(r"\\d+\\. (\\S+) (\\{.*\\})$", line)
        if m:
            calls.append((m.group(1), json.loads(m.group(2))))
    def answer(name, args):
        text = json.dumps(args)
        for needle, ans in answers.get("by_match", []):
            if needle in text:
                return ans
        return answers["by_tool"][name]
    # The run has the allowed tools under the connection's own name.
    allowed = [prefix + t.rsplit("__", 1)[-1] for t in sys.argv[sys.argv.index("--allowedTools") + 1].split(",")]
    allowed += [prefix + t for t in answers.get("extra_tools", [])]
    out = [{"type": "system", "subtype": "init", "tools": allowed, "mcp_servers": servers},
           {"type": "assistant", "message": {"content": [
               {"type": "tool_use", "id": f"t{i}", "name": prefix + name, "input": args}
               for i, (name, args) in enumerate(calls)]}},
           {"type": "user", "message": {"content": [
               {"type": "tool_result", "tool_use_id": f"t{i}",
                "content": [{"type": "text", "text": answer(name, args)}]}
               for i, (name, args) in enumerate(calls)]}},
           final]
    for e in out:
        print(json.dumps(e))
    sys.exit(1)
''')


def write_fake_claude(folder, answers: dict) -> str:
    """Write the stand-in into folder as `fake-claude` (not `claude`: the hub's
    guard refuses to start a program named claude under tests) with its answers.
    Returns its path."""
    path = os.path.join(folder, "fake-claude")
    with open(path, "w") as f:
        f.write(FAKE_CLAUDE)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    answers = dict(answers)
    answers.setdefault("all_tools", ALL_TOOLS)
    with open(os.path.join(folder, "answers.json"), "w") as f:
        json.dump(answers, f)
    return path


def runs(folder) -> list:
    """Every command line the stand-in got, one list per run."""
    try:
        with open(os.path.join(folder, "argv-all.jsonl")) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []
