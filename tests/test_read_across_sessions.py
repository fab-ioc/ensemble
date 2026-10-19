"""A message read by any session of a seat shows as read (#245).

What was seen (P200, 2026-10-19): the person's point P199 to the Ensemble
Dashboard PO was typed in at 08:29:33, read by the busy PO and confirmed by
the hub at 08:29:46 (``mid`` the old session's turn), a minute before the PO
was asked for its handover and two before it was rotated. Their chat still
said "Delivered, not yet read by the agent." at 08:35: that page's room poll
had stopped at 08:22 (the hub's log has none of its polls after that, though
its send, its transcript check and its channel came in). One request of the
poll had never settled, and the poll runs one request at a time, so the
page never heard that the send was read, and showed its own copy of the
send's reply ("delivered") for good.

So the hub's read state is checked here across every way a seat changes
session (a PO rotation, a PO switch from Claude to Codex and back, a task
owner's handover, a resume), with what the balloon then says; a message sent
in the handover window that neither session read keeps #192's path; and a
poll request that never settles is given up, so the next poll asks again.

Skipped parts need Node.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import unittest
from unittest import mock

import chatroom
import rotation
import sends
from tests import test_send_receipts as base
from tests.test_never_dropped import Harness

SID = base.SID
iso = base.iso
NODE = base.NODE
SESSION = base.SESSION
S1 = "019a0000-0000-7000-8000-0000000000a1"
S2 = "019a0000-0000-7000-8000-0000000000a2"
S3 = "019a0000-0000-7000-8000-0000000000a3"

_BALLOONS = r"""
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = {
  esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  whoHtml: m => 'You', stripPointLines: t => String(t).replace(/\n*^\[point P\d+\]$/gm, ''),
  renderBubbles: () => {}, LAST_ITEMS: [], ROOM: 'room-1', pointItems: () => null,
  SOLO_MODE: input.solo !== false,
};
if (input.threaded) Object.assign(ctx, { threadedView: () => true, THREAD_MODE: false, THREAD: '', THREAD_MID: '' });
vm.createContext(ctx);
vm.runInContext(input.code, ctx);
vm.runInContext('ROOM_SENDS = ' + JSON.stringify(input.sends), ctx);
const out = ctx.sendsToShow(input.items).map(s => {
  const html = ctx.sendHtml(s, t => t);
  const m = html.match(/<div class="pend-state[^"]*" role="status">([\s\S]*?)<\/div>/);
  return { key: s.key, pending: / pending/.test(html.slice(0, html.indexOf('>'))),
           line: m ? m[1].replace(/<button[\s\S]*?<\/button>/g, '').replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').trim() : '' };
});
console.log(JSON.stringify(out));
"""


def balloons(sends_rows, items, threaded=False, solo=True):
    """What the chat draws for the hub's sends beside ``items`` (the
    conversation this page holds): one {key, line, pending} per balloon of
    its own; a send whose turn is drawn has none. ``solo``: a one-agent chat
    (False: a team's room)."""
    code = SESSION[SESSION.index("// ---- What you sent, until the conversation shows it: begin"):
                   SESSION.index("// ---- What you sent, until the conversation shows it: end")]
    r = subprocess.run([NODE, "-e", _BALLOONS], capture_output=True, text=True, encoding="utf-8", timeout=60,
                       input=json.dumps({"code": code, "sends": sends_rows, "items": items, "threaded": threaded,
                                         "solo": solo}))
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def items_of(sid, turns, agent="claude"):
    """A session's turns as the page draws them (soloItems' ids)."""
    return [{"id": f"{sid}:{i}", "from": "user" if t["role"] == "user" else agent, "text": t["text"],
             "ts": base.dashboard._turn_epoch(t["timestamp"])} for i, t in enumerate(turns)]


@unittest.skipUnless(NODE, "node is not installed")
class ReadInAnEarlierSession(Harness):
    """The hub confirms a send by the turn of whichever session of the seat
    read it, and the chat says so, whatever session it is in now."""

    TEXT = "how can we improve the threads, I found myself lost\n\n[point P9]"

    def seat(self, rid):
        full = chatroom.get_room(rid, public=False)
        return full, next(p for p in full["participants"] if p.get("kind") == "agent")

    def move_seat(self, rid, frm, to, **rec):
        """The seat's next session, as a rotation, a switch or a handover records it."""
        full, part = self.seat(rid)
        part["rotations"] = list(part.get("rotations") or []) + [
            {"n": len(part.get("rotations") or []) + 1, "at": time.time(), "fromSessionId": frm,
             "toSessionId": to, **rec}]
        part["sessionId"] = to
        if rec.get("agent"):
            part["agent"] = rec["agent"]
        chatroom.update_room(full)

    def fresh(self, sid, at, agent="claude"):
        """A fresh session: its first prompt (the handover) and its answer."""
        self.turns[sid] = [{"role": "user", "timestamp": iso(at), "text": "[rotation] carry on from the handover"},
                           {"role": "assistant", "timestamp": iso(at + 20), "text": "Taken over."}]
        return items_of(sid, self.turns[sid], agent)

    def read_by(self, sid, at):
        self.turns.setdefault(sid, []).append({"role": "user", "timestamp": iso(at), "queued": True,
                                               "text": self.TEXT})

    def assert_read(self, rid, key, by_sid, items):
        [s] = [s for s in self.payload(rid)["sends"] if s["key"] == key]
        self.assertEqual(s["state"], "confirmed")
        self.assertTrue(s["mid"].startswith(by_sid + ":"), s["mid"])
        # The chat that holds only the current session: "Read by the agent.", solid.
        [b] = balloons([s], items)
        self.assertEqual(b["line"].split("· ", 1)[1], "Read by the agent.")
        self.assertFalse(b["pending"])
        # The channel of a PO chat draws the turn itself: no balloon of its own.
        self.assertEqual(balloons([s], items, threaded=True), [])
        # Once the hub stops listing it (its turn is in the conversation): none.
        later = time.time() + sends.SHOW_CONFIRMED_S + 5
        self.assertNotIn(key, [x["key"] for x in self.payload(rid, now=later)["sends"]])

    def test_po_rotation_read_before_the_handover(self):
        """P199 as it happened: read mid-turn by the old session, then the
        handover and the fresh session."""
        rid = self.room(agents=("claude",))
        at = time.time() - 400
        self.delivered(rid, "send:p", self.TEXT, at)
        full, part = self.seat(rid)
        part["sessionId"] = S1
        chatroom.update_room(full)
        self.turns[S1] = [{"role": "user", "timestamp": iso(at - 300), "text": "earlier"}]
        self.read_by(S1, at + 2)
        self.turns[S1].append({"role": "user", "timestamp": iso(at + 70), "text": "[handover] update PO-HANDOVER.md"})
        self.move_seat(rid, S1, S2, tokens=153000, threshold=150000)
        items = self.fresh(S2, at + 145)
        self.assert_read(rid, "send:p", S1, items)

    def test_po_switch_claude_to_codex_and_back(self):
        rid = self.room(agents=("claude",))
        at = time.time() - 400
        self.delivered(rid, "send:s", self.TEXT, at)
        full, part = self.seat(rid)
        part["sessionId"] = S1
        chatroom.update_room(full)
        self.move_seat(rid, S1, S2, cause="manual", fromAgent="claude", agent="codex")
        self.read_by(S2, at + 30)            # the Codex session read it
        self.fresh(S2, at + 5, agent="codex")
        self.turns[S2].append({"role": "user", "timestamp": iso(at + 30), "text": self.TEXT})
        self.move_seat(rid, S2, S3, cause="manual", fromAgent="codex", agent="claude")
        items = self.fresh(S3, at + 200)
        self.assert_read(rid, "send:s", S2, items)

    def test_task_owner_handover(self):
        rid = self.room(agents=("claude",))
        at = time.time() - 400
        self.delivered(rid, "send:o", self.TEXT, at)
        self.read_by(SID, at + 10)
        self.move_seat(rid, SID, S2, tokens=204000, threshold=200000, agent="claude", fromAgent="claude",
                       startedAt=at + 100)
        items = self.fresh(S2, at + 120)
        self.assert_read(rid, "send:o", SID, items)

    def test_resume_keeps_the_session(self):
        rid = self.room(agents=("claude",))
        at = time.time() - 400
        self.delivered(rid, "send:r", self.TEXT, at)
        self.read_by(SID, at + 10)
        # Resumed: the same conversation, a note typed in after it.
        self.turns[SID].append({"role": "user", "timestamp": iso(at + 200), "text": "[resumed] carry on"})
        self.assert_read(rid, "send:r", SID, items_of(SID, self.turns[SID][-1:]))


@unittest.skipUnless(NODE, "node is not installed")
class SentInTheHandoverWindow(Harness):
    """Sent after the old session was asked for its handover, read by
    neither: held while the seat changes, typed in again once, then Not
    delivered with Retry (#192), and never taken for read."""

    def test_neither_session_read_it(self):
        rid = self.room(agents=("claude",))
        pty = self.ptys["pty-live-0"]
        full = chatroom.get_room(rid, public=False)
        part = next(p for p in full["participants"] if p.get("kind") == "agent")
        part["sessionId"] = S1
        chatroom.update_room(full)
        at = time.time() - 300
        self.turns[S1] = [{"role": "user", "timestamp": iso(at - 10), "text": "[handover] update PO-HANDOVER.md"},
                          {"role": "assistant", "timestamp": iso(at + 20), "text": "Handover updated."}]
        self.delivered(rid, "send:w", "a message in the window\n\n[point P3]", at)

        def line(now=None):
            [s] = self.payload(rid, now=now)["sends"]
            [b] = balloons([s], items_of(S2, self.turns.get(S2, [])))
            return s["state"], b["line"].split("· ", 1)[1]

        # The seat is changing: held, said as delivered.
        key = (rid, "claude")
        rotation._ROTATING[key] = {"stopped": False}
        try:
            self.ptys["pty-live-0"]._alive = False
            self.assertEqual(line(time.time() + 600)[0], "delivered")
        finally:
            rotation._ROTATING.pop(key, None)
            self.ptys["pty-live-0"]._alive = True
        full = chatroom.get_room(rid, public=False)
        part = next(p for p in full["participants"] if p.get("kind") == "agent")
        part["rotations"] = [{"n": 1, "at": at + 60, "fromSessionId": S1, "toSessionId": S2}]
        part["sessionId"] = S2
        chatroom.update_room(full)
        self.turns[S2] = [{"role": "user", "timestamp": iso(at + 70), "text": "[rotation] carry on from the handover"},
                          {"role": "assistant", "timestamp": iso(at + 90), "text": "Taken over."}]
        self.assertEqual(line(), ("delivered", "Delivered, not yet read by the agent."))
        # Typed in again once, the fresh session idle.
        sends.redeliver_tick(now=time.time())
        self.assertEqual(len(pty.typed), 1)
        state, said = line()
        self.assertEqual(state, "delivered")
        self.assertRegex(said, r"^Typed in again at \d\d:\d\d; not yet read by the agent\.$")
        # Still not read at the next idle: Not delivered, with Retry.
        sends.redeliver_tick(now=time.time() + sends.REDELIVER_AFTER_S + 5)
        self.assertEqual(line(), ("failed", f"Not delivered: {sends.NOT_TAKEN}."))


@unittest.skipUnless(NODE, "node is not installed")
class ConfirmedIsNotAlwaysRead(unittest.TestCase):
    """Only a one-agent chat's transcript confirms a read (review 1): a
    team's room confirms a post as it arrives, a /command once typed."""

    def row(self, **kw):
        return {"key": "send:t", "text": "for the team", "at": time.time() - 30, "state": "confirmed",
                "stateAt": time.time() - 29, **kw}

    def test_a_team_post_outside_the_window_is_not_read(self):
        [b] = balloons([self.row(mid="a1b2c3d4e5f6")], [], solo=False)
        self.assertNotIn("Read by the agent", b["line"])
        self.assertTrue(b["pending"])

    def test_a_team_post_still_unread_after_the_room_became_one_agent(self):
        """Review 2: the team was handed to one agent within the 120 s the hub
        still lists the post; the room is solo now, the post still unread."""
        [b] = balloons([self.row(mid="a1b2c3d4e5f6")], [], solo=True)
        self.assertNotIn("Read by the agent", b["line"])
        self.assertTrue(b["pending"])

    def test_the_same_send_in_a_one_agent_chat_is_read(self):
        [b] = balloons([self.row(mid=S1 + ":4")], [])
        self.assertEqual(b["line"].split("· ", 1)[1], "Read by the agent.")

    def test_a_command_is_never_shown_as_read(self):
        self.assertEqual(balloons([self.row(text="/compact")], []), [])


@unittest.skipUnless(NODE, "node is not installed")
class TeamHandedToOneAgent(Harness):
    """Review 2, through the hub: a team's post (confirmed on arrival, by
    its room message's id), then the room is one agent's within the 120 s
    the hub still lists it. Its balloon does not say it was read."""

    def test_the_post_is_not_read(self):
        rid = self.room(agents=("claude",))
        at = time.time() - 30
        sends.accept(rid, "send:p", "for the team", now=at)
        sends.mark(rid, ["send:p"], "confirmed", mid="a1b2c3d4e5f6", now=at + 1)
        [s] = [s for s in self.payload(rid)["sends"] if s["key"] == "send:p"]
        [b] = balloons([s], [], solo=True)
        self.assertNotIn("Read by the agent", b["line"])
        self.assertTrue(b["pending"])


@unittest.skipUnless(NODE, "node is not installed")
class APollThatNeverAnswers(unittest.TestCase):
    """The page's room poll gives up a request that never settles, and the
    next poll asks again (P200: one stuck request had ended the polls)."""

    def test_the_poll_is_free_again_and_the_next_one_lands(self):
        block = SESSION[SESSION.index("// The room poll: one request at a time."):
                        SESSION.index("function refreshShow(")]
        js = r"""
const vm = require('vm');
const code = require('fs').readFileSync(0, 'utf8');
const calls = [], shown = [];
let answer = null;
const ctx = {
  ROOM: 'room-1', ROOM_N: 50, SENDS_GEN: 0, ROOM_OBJ: null, structuredClone,
  ptTicket: () => 1, setText: () => {}, THR_DRAWN: false, thrPrefetch: () => {}, $: () => ({}), FileLinks: { recheck() {} },
  refreshShow: room => shown.push(room.id), setTimeout, clearTimeout, Object, AbortController,
  AbortSignal: { timeout: ms => AbortSignal.timeout(Math.min(ms, 50)) },
  fetch: (url, init) => {
    calls.push({ url, signal: !!(init && init.signal) });
    if (answer) return Promise.resolve({ ok: true, status: 200, headers: { get: () => '' }, json: async () => answer });
    return new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason)));
  },
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
(async () => {
  const out = {};
  ctx.refresh();
  out.busyAtOnce = vm.runInContext('REFRESH_BUSY', ctx);
  ctx.refresh();          // a tick while it hangs: asked for again, not beside it
  await new Promise(r => setTimeout(r, 300));
  out.busyAfter = vm.runInContext('REFRESH_BUSY', ctx);
  answer = { id: 'room-1', revision: 'r1', statusSig: 's1', messages: [] };
  await ctx.refresh();
  await new Promise(r => setTimeout(r, 50));
  out.calls = calls; out.shown = shown;
  console.log(JSON.stringify(out));
})();
"""
        r = subprocess.run([NODE, "-e", js], input=block, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["busyAtOnce"])
        self.assertFalse(out["busyAfter"], "a request that never settles held the poll for good")
        self.assertTrue(all(c["signal"] for c in out["calls"]))
        self.assertGreaterEqual(len(out["calls"]), 3)
        self.assertEqual(out["shown"], ["room-1"])

    def test_every_poller_gives_up_a_request_that_never_settles(self):
        for call in ("pollFetch(ROOM_LAST?.revision", "pollFetch('/api/session/' + encodeURIComponent(SOLO_SID) + '?stat=1')",
                     "const url = thrUrl(force ? '' : THR_SIG)", "pre.p.catch(() => pollFetch(url, null, 90000)) : await pollFetch(url, null, 90000)",
                     "THR_PRE = { url, p: pollFetch(url, null, 90000) }"):
            self.assertIn(call, SESSION)
        # The transcript reads (#247: the last balloons, the page above them,
        # a session rotated out of) give up too.
        self.assertEqual(len(re.findall(r"pollFetch\((?:'/api/session/' \+ encodeURIComponent\(sid\) \+ '\?full=1'|soloUrl\()"
                                        r"[^;]*?null, 90000\)", SESSION)), 3)
        start = SESSION.index("async function refreshRoom()")
        self.assertNotIn("await fetch(", SESSION[start:SESSION.index("function refreshShow(", start)])


if __name__ == "__main__":
    unittest.main()
