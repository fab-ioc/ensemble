"""Threads the user is in: which ones to follow, and the first message of each.

Ported from Slack Man's watcher (`slackwatch/threads.py`), with the user's id
in place of a fixed person and the channel limit as a setting.

The user is in a thread when they are mentioned in it (in the first message or
in any reply) or have written in it. A mention of a watched user group, written
while the user was in the group, counts as a mention of them: the thread is
followed the same way, and its entry keeps the group id under "group".

Two searches find new replies in such threads:

- "my-threads": Slack's `with:<@me> is:thread` filter. It returns the messages
  of threads the user has written in. It does not return threads where the user
  is only mentioned (seen on a real workspace, 2026-11-11).
- "mentioned-threads": every thread message in the channels of the threads where
  the user is mentioned (`is:thread in:<#C1> in:<#C2> ...`; repeated `in:` means
  "any of these channels"). Only the messages of those threads are kept.

The threads where the user is mentioned are kept in the source's memory under
"threads". They come from the mention search on every check, and once, when the
list is new, from a look back over the last `days` days ("thread-seed"). A
thread with no new message for `days` days is dropped.

The same list keeps the first message of each thread (shortened), so it is read
from Slack only once.
"""
from __future__ import annotations

import re

from .slack_connector import Query
from .slack_parse import TRAILER

MY_THREADS = "my-threads"
MENTIONED_THREADS = "mentioned-threads"
SEED = "thread-seed"
FIRST_PREFIX = "first:"  # labels of the reads of a thread's first message

MAX_THREADS = 300  # the threads with the oldest activity are dropped first
MAX_CHANNELS = 40  # default for the setting maxThreadChannels
MAX_FIRST_READS = 10  # first messages read in one check; the rest wait for the next reply
FIRST_KEEP = 400  # characters of a first message kept in memory


def key(msg) -> str:
    """The thread a message belongs to: "channel:ts of the first message".

    A message that is not in a thread is the first message of its own (possible) thread.
    """
    return f"{msg.channel_id}:{msg.thread_ts or msg.ts}"


def is_reply(msg) -> bool:
    return bool(msg.thread_ts) and msg.thread_ts != msg.ts


def mentions(text: str, user_id: str) -> bool:
    return bool(user_id) and re.search(rf"<@{re.escape(user_id)}(?:\||>)", text or "") is not None


def _new_entry(channel, ts):
    return {"channel": channel, "ts": ts, "mentioned": False, "written": False, "last": 0.0}


def _set_first(entry, msg):
    entry["first"] = (msg.text or "").strip()[:FIRST_KEEP]
    # A channel read gives "Name <email>"; keep the name only.
    entry["first_by"] = re.sub(r"\s*<[^>]*>", "", msg.sender_name or "").strip()


def learn(threads: dict, fetched, me: str, now: float, days: int, group_hits=None) -> dict:
    """The thread list after this check's answers.

    - Every message from the mention search (and the one-time look back) adds its
      thread as one where the user is mentioned.
    - Every message from a group search in group_hits ({message key: group id}, the
      group mentions that count, see slack.group_mentions) does the same, and notes
      the group.
    - Every message from the "my-threads" search adds its thread as one the user has
      written in.
    - Every message in a thread already on the list counts as activity in it.
    - Threads with no activity for `days` days are dropped.
    """
    out = {k: dict(v) for k, v in (threads or {}).items()}
    mention_labels = {f"mention:{me}", SEED}
    group_hits = group_hits or {}
    for label, messages in fetched.messages.items():
        if label == "slackbot" or label.startswith(FIRST_PREFIX):
            continue
        for msg in messages:
            k = key(msg)
            group = group_hits.get(msg.key) if label.startswith("group:") else None
            if label not in mention_labels and label != MY_THREADS and not group and k not in out:
                continue
            entry = out.get(k)
            if entry is None:
                entry = out[k] = _new_entry(msg.channel_id, msg.thread_ts or msg.ts)
            if label in mention_labels:
                entry["mentioned"] = True
                entry.pop("group", None)  # mentioned in person: that is the reason now
            elif group and not entry.get("mentioned"):
                entry["mentioned"] = True
                entry["group"] = group
            if label == MY_THREADS:
                entry["written"] = True
            if not is_reply(msg) and "first" not in entry:
                _set_first(entry, msg)
            entry["last"] = max(float(entry.get("last") or 0), float(msg.ts))
    return expire(out, now, days)


def expire(threads: dict, now: float, days: int) -> dict:
    oldest = now - days * 86400
    kept = {k: v for k, v in threads.items() if float(v.get("last") or 0) >= oldest}
    if len(kept) > MAX_THREADS:
        newest = sorted(kept.items(), key=lambda kv: float(kv[1].get("last") or 0), reverse=True)[:MAX_THREADS]
        kept = dict(newest)
    return kept


def _mentioned_channels(threads: dict) -> dict:
    """{channel: latest activity} of the threads where the user is mentioned.

    DMs are left out: every DM message is found by the DM search already.
    """
    latest = {}
    for entry in (threads or {}).values():
        if not entry.get("mentioned") or entry["channel"].startswith("D"):
            continue
        latest[entry["channel"]] = max(latest.get(entry["channel"], 0.0), float(entry.get("last") or 0))
    return latest


def follow_channels(threads: dict, max_channels: int = MAX_CHANNELS) -> list:
    """Channels of the threads where the user is mentioned, most recently active first."""
    latest = _mentioned_channels(threads)
    ordered = sorted(latest, key=lambda c: (-latest[c], c))
    return ordered[:max(0, int(max_channels))]


def channels_left_out(threads: dict, max_channels: int = MAX_CHANNELS) -> int:
    """How many channels of threads where the user is mentioned are over the limit and
    so not searched (replies there are missed until the user is mentioned again)."""
    return max(0, len(_mentioned_channels(threads)) - max(0, int(max_channels)))


def through_group(threads: dict) -> int:
    """How many followed threads are followed only because a group the user was in is mentioned."""
    return sum(1 for e in (threads or {}).values() if e.get("mentioned") and e.get("group"))


def followed(msg, threads: dict) -> bool:
    """Is the message in a thread where the user is mentioned?"""
    return bool((threads or {}).get(key(msg), {}).get("mentioned"))


# ---------- the first message of a thread ----------

def around(ts: str):
    """A one-microsecond window on each side of a Slack ts, done on the digits so no
    precision is lost: reading the channel in that window returns exactly that
    message. (Reading with oldest = latest = ts returns nothing: both ends are
    left out. Seen on a real workspace, 2026-11-16.)"""
    secs, _, micros = ts.partition(".")
    n = int(secs) * 1_000_000 + int((micros + "000000")[:6])

    def fmt(v):
        return f"{v // 1_000_000}.{v % 1_000_000:06d}"

    return fmt(n - 1), fmt(n + 1)


def first_queries(messages, threads: dict, fetched) -> list:
    """Reads of the first message for the replies whose thread's first message is not known yet."""
    found = {key(m) for msgs in fetched.messages.values() for m in msgs if not is_reply(m)}
    queries, planned = [], set()
    for msg in messages:
        if not is_reply(msg):
            continue
        k = key(msg)
        if k in planned or k in found or "first" in (threads or {}).get(k, {}):
            continue
        planned.add(k)
        oldest, latest = around(msg.thread_ts)
        queries.append(Query(FIRST_PREFIX + k, "channel", {
            "channel_id": msg.channel_id,
            "oldest": oldest,
            "latest": latest,
            "limit": 1,
            "response_format": "detailed",
        }))
        if len(queries) >= MAX_FIRST_READS:
            break
    return queries


def add_firsts(threads: dict, fetched, me: str, now: float) -> dict:
    """Keep the first messages read for this check. One that mentions the user puts
    its thread on the list of threads where they are mentioned."""
    out = {k: dict(v) for k, v in (threads or {}).items()}
    for label, messages in fetched.messages.items():
        if not label.startswith(FIRST_PREFIX):
            continue
        k = label[len(FIRST_PREFIX):]
        channel, _, ts = k.partition(":")
        for msg in messages:
            if msg.ts != ts:
                continue
            msg.text = TRAILER.sub("", msg.text or "").strip()
            entry = out.get(k)
            if entry is None:
                entry = out[k] = _new_entry(channel, ts)
            _set_first(entry, msg)
            if msg.sender_id != me and mentions(msg.text, me):
                entry["mentioned"] = True
                entry.pop("group", None)
            entry["last"] = max(float(entry.get("last") or 0), now)
    return out


def first_of(msg, threads: dict, fetched) -> tuple[str, str]:
    """(text, author) of the first message of a reply's thread, or ("", "") when not known."""
    entry = (threads or {}).get(key(msg), {})
    if "first" in entry:
        return entry["first"], entry.get("first_by", "")
    for msgs in fetched.messages.values():
        for m in msgs:
            if not is_reply(m) and key(m) == key(msg):
                tmp = {}
                _set_first(tmp, m)
                return tmp["first"], tmp["first_by"]
    return "", ""
