"""Turn the Slack connection's text answers into message records.

Ported from Slack Man's watcher (`slackwatch/slackparse.py`). The connection
answers in a readable text layout, not in fields. These functions read that
layout. When the layout cannot be read (for example because Slack changed it),
they raise ParseError instead of guessing, so the check fails and says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse, parse_qs

SLACKBOT = "USLACK"  # Slackbot's fixed user id in every workspace


class ParseError(Exception):
    """An answer did not have the expected layout."""


@dataclass
class SlackMessage:
    channel_id: str
    channel_name: str
    sender_id: str
    sender_name: str
    is_bot: bool
    ts: str
    time_text: str
    thread_ts: str
    permalink: str
    text: str
    reply_count: int = 0
    source: str = ""  # the label of the read that found it

    @property
    def key(self) -> str:
        return f"{self.channel_id}:{self.ts}"

    def to_dict(self):
        return asdict(self)


def tool_result_text(content) -> str:
    """The text of a tool result as it appears in Claude's stream output."""
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "".join(parts)


def _unwrap(raw: str, key: str):
    """The connection wraps its text in a small JSON object; return (text, pagination_info)."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise ParseError(f"answer is not JSON: {raw[:200]!r}")
    if not isinstance(data, dict) or key not in data:
        raise ParseError(f"answer has no {key!r}: {raw[:200]!r}")
    return data[key], data.get("pagination_info", "")


_RESULT_SPLIT = re.compile(r"^### Result \d+ of \d+[ \t]*\n", re.M)
_COUNT = re.compile(r"^## Messages \((\d+) results?\)", re.M)


def _field(header: str, name: str, required=True):
    m = re.search(rf"^{name}:[ \t]*(.*)$", header, re.M)
    if not m:
        if required:
            raise ParseError(f"search result without {name!r}: {header[:200]!r}")
        return ""
    return m.group(1).strip()


def _thread_ts(permalink: str) -> str:
    try:
        q = parse_qs(urlparse(permalink).query)
    except ValueError:
        return ""
    return (q.get("thread_ts") or [""])[0]


def parse_search(raw: str, source: str = ""):
    """Read a slack_search_public_and_private answer (response_format detailed).

    Returns (messages, count): count is the number of results the answer reports,
    used to tell whether more may be waiting.
    """
    text, _pagination = _unwrap(raw, "results")
    if "No results found" in text and "### Result" not in text:
        return [], 0
    m = _COUNT.search(text)
    if not m:
        raise ParseError(f"search answer without a message count: {text[:200]!r}")
    expected = int(m.group(1))
    blocks = _RESULT_SPLIT.split(text)[1:]
    if len(blocks) != expected:
        raise ParseError(f"search answer says {expected} results but {len(blocks)} were found")
    messages = []
    for block in blocks:
        if "\nText:" not in "\n" + block:
            raise ParseError(f"search result without text: {block[:200]!r}")
        header, body = re.split(r"^Text:[ \t]*\n?", block, maxsplit=1, flags=re.M)
        body = re.sub(r"\n*---\s*$", "", body).strip("\n")
        channel = _field(header, "Channel")
        cm = re.fullmatch(r"(.*?)\s*\(ID: ([A-Z0-9]+)\)", channel)
        if not cm:
            raise ParseError(f"cannot read channel line {channel!r}")
        sender = _field(header, "From")
        sm = re.match(r"(.*?)\s*(?:<[^>]*>)?\s*\(ID: ([A-Z0-9]+)\)\s*(\[BOT\])?", sender)
        if not sm:
            raise ParseError(f"cannot read sender line {sender!r}")
        ts = _field(header, "Message_ts")
        if not re.fullmatch(r"\d+\.\d+", ts):
            raise ParseError(f"cannot read message time {ts!r}")
        link = _field(header, "Permalink", required=False)
        lm = re.search(r"\((https?://[^)\s]+)\)", link)
        permalink = lm.group(1) if lm else link
        replies = _field(header, "Reply count", required=False)
        messages.append(SlackMessage(
            channel_id=cm.group(2),
            channel_name=cm.group(1).strip(),
            sender_id=sm.group(2),
            sender_name=sm.group(1).strip(),
            is_bot=bool(sm.group(3)),
            ts=ts,
            time_text=_field(header, "Time", required=False),
            thread_ts=_thread_ts(permalink),
            permalink=permalink,
            text=body,
            reply_count=int(replies) if replies.isdigit() else 0,
            source=source,
        ))
    return messages, expected


_CHANNEL_MSG = re.compile(
    r"^=== Message from (?P<name>.*?) \((?P<uid>[A-Z0-9]+)\) at (?P<time>.*?) ===[ \t]*\n"
    r"Message TS: (?P<ts>\d+\.\d+)[ \t]*\n",
    re.M,
)
_CHANNEL_HEADER = re.compile(r"^Channel: (?P<name>.*?) \((?:ID: )?(?P<id>[A-Z0-9]+)\)[ \t]*$", re.M)


def _channel_messages(text: str, channel_id: str, source: str):
    matches = list(_CHANNEL_MSG.finditer(text))
    if not matches:
        if "=== Message" in text:
            raise ParseError(f"cannot read the messages in {text[:200]!r}")
        return []
    messages = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip("\n")
        messages.append(SlackMessage(
            channel_id=channel_id,
            channel_name="",
            sender_id=m.group("uid"),
            sender_name=m.group("name"),
            is_bot=m.group("uid") == SLACKBOT,
            ts=m.group("ts"),
            time_text=m.group("time"),
            thread_ts="",
            permalink="",
            text=body,
            source=source,
        ))
    return messages


def parse_channel(raw: str, channel_id: str, source: str = ""):
    """Read a slack_read_channel answer (response_format detailed).

    Returns (messages, header): header is {"name", "id"} from the answer's first
    line when it has one. Reading the DM with a user id (for example Slackbot's,
    `USLACK`) answers with the DM's own id there.
    """
    text, _pagination = _unwrap(raw, "messages")
    header = {}
    hm = _CHANNEL_HEADER.search(text)
    if hm:
        header = {"name": hm.group("name").strip(), "id": hm.group("id")}
    real_id = header.get("id") or channel_id
    return _channel_messages(text, real_id, source), header


_THREAD_SPLIT = re.compile(r"^(?:=== THREAD PARENT MESSAGE ===|--- Reply \d+ of \d+ ---)[ \t]*\n", re.M)
_THREAD_REPLIES = re.compile(r"\n*^=== THREAD REPLIES \(\d+ total\) ===[ \t]*$", re.M)
_THREAD_FROM = re.compile(r"^From: (?P<name>.*?)\s*(?:<[^>]*>)?\s*\((?:ID: )?(?P<uid>[A-Z0-9]+)\)\s*(?P<bot>\[BOT\])?[ \t]*$", re.M)
_THREAD_TS = re.compile(r"^Message TS: (?P<ts>\d+\.\d+)[ \t]*$", re.M)
_THREAD_TIME = re.compile(r"^Time: (?P<time>.*?)[ \t]*$", re.M)
# Lines Slack adds after a message's text: reactions, files, a thread's summary.
TRAILER = re.compile(r"(?:\n(?:Thread|Reactions|Files?):[^\n]*)+\s*$")
_CURSOR = re.compile(r"cursor: `([^`]+)`")


def parse_thread(raw: str, channel_id: str, source: str = ""):
    """Read a slack_read_thread answer (response_format detailed): the first message
    and a page of its replies, oldest first. Each message carries the thread's ts.

    Returns (messages, cursor): cursor is "" when the answer says there is no more,
    else what to pass as `cursor` to read the next page of replies. The first
    message comes on every page; the caller keeps one copy of each message.
    """
    text, pagination = _unwrap(raw, "messages")
    blocks = _THREAD_SPLIT.split(text)
    if len(blocks) < 2:
        if text.strip():
            raise ParseError(f"cannot read the thread in {text[:200]!r}")
        return [], ""
    messages = []
    for block in blocks[1:]:
        block = _THREAD_REPLIES.split(block)[0]
        fm, tm = _THREAD_FROM.search(block), _THREAD_TS.search(block)
        if not fm or not tm:
            raise ParseError(f"thread message without sender or time: {block[:200]!r}")
        when = _THREAD_TIME.search(block)
        body_start = max(fm.end(), tm.end(), when.end() if when else 0)
        body = TRAILER.sub("", block[body_start:].strip("\n")).strip("\n")
        messages.append(SlackMessage(
            channel_id=channel_id,
            channel_name="",
            sender_id=fm.group("uid"),
            sender_name=fm.group("name").strip(),
            is_bot=bool(fm.group("bot")) or fm.group("uid") == SLACKBOT or fm.group("uid").startswith("B"),
            ts=tm.group("ts"),
            time_text=when.group("time") if when else "",
            thread_ts="",
            permalink="",
            text=body,
            source=source,
        ))
    messages.sort(key=lambda m: float(m.ts))
    first = messages[0].ts
    for m in messages:
        m.thread_ts = first
    cm = _CURSOR.search(pagination or "")
    more = cm and "no more" not in (pagination or "").lower()
    return messages, (cm.group(1) if more else "")


_PROFILE_ID = re.compile(r"\bUser ID:[ \t]*(?P<id>[UW][A-Z0-9]+)", re.I)
_PROFILE_NAME = re.compile(r"^(?:Display Name|Real Name|Name):[ \t]*(?P<name>\S.*?)[ \t]*$", re.M | re.I)
_PROFILE_TEAM = re.compile(r"\b(?:Team|Workspace) ID:[ \t]*(?P<id>[TE][A-Z0-9]+)", re.I)


def parse_profile(raw: str) -> dict:
    """Read a slack_read_user_profile answer: {"id", "name", "team"} (team may be "")."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    text = raw
    if isinstance(data, dict):
        text = next((v for v in data.values() if isinstance(v, str) and _PROFILE_ID.search(v)), json.dumps(data))
    m = _PROFILE_ID.search(text)
    if not m:
        raise ParseError(f"profile answer without a user id: {text[:200]!r}")
    name = _PROFILE_NAME.search(text)
    team = _PROFILE_TEAM.search(text)
    return {"id": m.group("id"), "name": name.group("name") if name else "",
            "team": team.group("id") if team else ""}


_CHANNELS_COUNT = re.compile(r"^## Channels \((\d+) results?\)", re.M)
_ARCHIVES_ID = re.compile(r"/archives/([CG][A-Z0-9]+)")


def parse_channels(raw: str) -> list:
    """Read a slack_search_channels answer (response_format detailed):
    [{"name": "made-up-channel", "id": "C...", "archived": False}], in the
    answer's order. The name is without its '#'."""
    text, _pagination = _unwrap(raw, "results")
    if "No results found" in text and "### Result" not in text:
        return []
    m = _CHANNELS_COUNT.search(text)
    if not m:
        raise ParseError(f"channel search answer without a channel count: {text[:200]!r}")
    blocks = _RESULT_SPLIT.split(text)[1:]
    if len(blocks) != int(m.group(1)):
        raise ParseError(f"channel search answer says {m.group(1)} results but {len(blocks)} were found")
    out = []
    for block in blocks:
        name = _field(block, "Name").lstrip("#").strip()
        im = _ARCHIVES_ID.search(_field(block, "Permalink"))
        if not name or not im:
            raise ParseError(f"channel search result without a name or an id: {block[:200]!r}")
        out.append({"name": name, "id": im.group(1),
                    "archived": _field(block, "Is Archived", required=False).lower() == "true"})
    return out


_ADDED = re.compile(r"You have been added to .*?<!subteam\^(S[A-Z0-9]+)")
_REMOVED = re.compile(r"You have been removed from .*?<!subteam\^(S[A-Z0-9]+)")


def membership_events(messages):
    """Slackbot's 'added to' / 'removed from' user group messages, as (ts, group id, is member)."""
    events = []
    for msg in messages:
        if msg.sender_id != SLACKBOT:
            continue
        m = _ADDED.search(msg.text)
        if m:
            events.append((float(msg.ts), m.group(1), True))
            continue
        m = _REMOVED.search(msg.text)
        if m:
            events.append((float(msg.ts), m.group(1), False))
    events.sort()
    return events


def workspace_from_link(link: str) -> str:
    """"example.slack.com" from a message link, or ""."""
    host = urlparse(link or "").hostname or ""
    return host if host.endswith(".slack.com") else ""


def link_for(workspace: str, channel_id: str, ts: str, thread_ts: str = "") -> str:
    """A message link built the way Slack builds them, for answers that carry none."""
    if not workspace:
        return ""
    url = f"https://{workspace}/archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        url += f"?thread_ts={thread_ts}&cid={channel_id}"
    return url
