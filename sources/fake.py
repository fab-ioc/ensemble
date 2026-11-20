"""A made-up source for tests: no service, no network.

Tests put messages in ``FakeSource.messages`` as if people wrote them, and the
source hands them out the way a real reader does: each read returns what was
written since the position it is given, oldest first, at most ``page`` at a
time. ``fail`` makes the next checks fail with that reason; ``broken`` makes
them raise. Every call is written down in ``calls``.

It is not listed in ``sources.TYPES``: a test registers it. Its ids are made
up (``U000TEST01``, ``C000TEST01``), as the public repo requires.
"""
from __future__ import annotations

from .base import (OK, FAILED, NOT_SET_UP, WHY_CHANNEL, Author, Check, Field, Message, Place,
                   Source, Thread, Watch)


class FakeSource(Source):
    kind = "fake"
    label = "Made-up source"
    can_send = False

    def __init__(self):
        self.messages: list[Message] = []
        self.page = 20                      # messages per read per check
        self.reads = ("inbox",)             # the reads a check makes, each over every message
        self.fail = ""                      # a reason: the next checks fail with it
        self.broken = ""                    # the next checks raise this
        self.not_set_up = ""                # check() answers "not set up" with this
        self.memory_out: dict | None = None
        self.positions_out: dict = {}       # positions given back instead of the real ones
        self.calls: list[dict] = []

    # -- what the page draws --------------------------------------------
    def fields(self) -> list[Field]:
        return [Field("account", "Account", "text", "", help="The made-up account to read."),
                Field("dms", "DMs", "switch", True),
                Field("me", "Your id", "found", "", help="Found by the first check.")]

    def route_fields(self) -> list[Field]:
        return [Field("channels", "Channels", "list", [],
                      help="Every message in these goes to the initiative.")]

    def problem(self, settings: dict) -> str:
        return "" if (settings or {}).get("account") else "No account is set."

    # -- reading -----------------------------------------------------------
    def plan(self, watch: Watch, positions: dict[str, float]) -> list[str]:
        return list(self.reads)

    def check(self, watch: Watch, positions: dict[str, float], now: float) -> Check:
        self.calls.append({"positions": dict(positions), "now": now,
                           "memory": dict(watch.memory), "places": list(watch.places)})
        if self.broken:
            raise RuntimeError(self.broken)
        if self.fail:
            return Check(outcome=FAILED, reason=self.fail)
        if self.not_set_up:
            return Check(outcome=NOT_SET_UP, reason=self.not_set_up)
        out = Check(outcome=OK, memory=self.memory_out)
        got: dict[str, Message] = {}
        for name in self.plan(watch, positions):
            since = positions.get(name, now)
            new = sorted((m for m in self.messages if since <= m.at <= now), key=lambda m: m.at)
            page = new[:self.page]
            for m in page:
                got[m.key] = m
            if len(new) > self.page:
                out.positions[name] = page[-1].at
                out.full.add(name)
            else:
                out.positions[name] = now
        out.positions.update(self.positions_out)
        out.messages = list(got.values())
        return out

    def thread(self, ref: str) -> list[Message]:
        return sorted((m for m in self.messages if m.thread and m.thread.id == ref),
                      key=lambda m: m.at)


def message(n: int, at: float, text: str = "", channel: str = "C000TEST01",
            author: str = "U000TEST02", thread: str = "") -> Message:
    """A made-up message number ``n`` written at ``at``."""
    return Message(key=f"fake:example:{channel}:{n}", source="fake",
                   place=Place(id=channel, name="#made-up", kind="channel"),
                   author=Author(id=author, name="Made-up Person"), at=float(at),
                   text=text or f"message {n}",
                   link=f"https://example.slack.com/archives/{channel}/p{n}",
                   thread=Thread(id=f"{channel}:{thread}") if thread else None,
                   why_seen=WHY_CHANNEL,
                   reply_to={"channel": channel, "thread": thread or str(n)})
