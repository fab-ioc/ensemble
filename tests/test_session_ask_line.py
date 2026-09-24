"""The chat holds the task's open ask in one line above the conversation
(session.html, which is also the task panel's chat), and shows the hub's
"restarted" row in a one-agent chat.

The page's own functions run in Node and these check that:

* the line reads "Waiting for you since HH:MM: <first line of the ask>";
* the ask's words link to its balloon: the message in a room, in a one-agent
  chat the agent's turn that made the report;
* the hub decides whether an ask is open (``room.openAsk``): the page has no
  rule of its own, and the line goes when the room carries none;
* it is rewritten only when what it says changes (nothing clickable is
  replaced on a timer), and its link lands like the catch-up line's;
* it has the catch-up line's look, from tokens alone, and a phone-sized target;
* the hub's restart row goes into a transcript by its time, as a line.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def js_function(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found in session.html"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def js_line(src: str, start: str) -> str:
    at = src.index(start)
    return src[at:src.index("\n", at) + 1]


JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const el = { hidden: true, html: '', writes: 0 };
Object.defineProperty(el, 'innerHTML', { get() { return this.html; }, set(v) { this.html = v; this.writes++; } });
const ctx = {
  esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  $: sel => sel === '#ask-line' ? el : null,
  Date, JSON, console,
};
vm.createContext(ctx);
vm.runInContext(code + `
  const ROOM = 'room-x5';
  let ROOM_OBJ = null, SOLO_MODE = false, LAST_ITEMS = null;
  const refHref = (room, mid) => '/session?id=' + room + '&msg=' + mid;
  const chatTime = ts => 'T' + ts;
  globalThis.t = { askBalloon, askLineHtml, showAskLine, withHubRows, isLandmark,
    set: (room, solo, items) => { ROOM_OBJ = room; SOLO_MODE = solo; LAST_ITEMS = items; } };`, ctx);
const T = ctx.t, out = {};
const now = Date.now() / 1000;
const ask = { from: 'claude', ts: now - 60, kind: 'blocked', id: 'm-blocked',
  line: 'AutoScout24 is logged out <b>', text: 'AutoScout24 is logged out <b> the operator must log in' };

// A room: the message itself.
const roomItems = [{ id: 'm1', from: 'user', ts: now - 90 }, { id: 'm-blocked', from: 'claude', ts: now - 60 },
  { id: 'm-update', from: 'claude', ts: now - 10 }];
out.roomMid = T.askBalloon(ask, roomItems, false);
out.roomMidGone = T.askBalloon(ask, [{ id: 'm9', from: 'claude', ts: now }], false);
// A one-agent chat: the agent's last turn at or before the report, else its first after.
const turns = [{ id: 's:0', from: 'user', ts: now - 200 }, { id: 's:1', from: 'claude', ts: now - 100 },
  { id: 's:2', from: 'claude', ts: now - 61 }, { id: 'rot1', divider: {}, ts: now - 60 },
  { id: 's:3', from: 'user', ts: now - 30 }, { id: 's:4', from: 'claude', ts: now - 5 }];
out.soloMid = T.askBalloon(ask, turns, true);
out.soloAfter = T.askBalloon({ ...ask, ts: now - 500 }, turns, true);
out.soloNone = T.askBalloon(ask, [{ id: 's:0', from: 'user', ts: now - 200 }], true);
out.noAsk = T.askBalloon(null, turns, true);

out.html = T.askLineHtml(ask, 'm-blocked', mid => '/session?id=room-x5&msg=' + mid);
out.plain = T.askLineHtml(ask, '', () => '');
out.noLine = T.askLineHtml({ ts: now, text: '' }, '', () => '');

// Drawn from room.openAsk alone; rewritten only on a change; gone when it closes.
T.set({ openAsk: ask }, false, roomItems);
T.showAskLine(null);
out.shown = { hidden: el.hidden, html: el.html, writes: el.writes };
T.showAskLine(roomItems); T.showAskLine(null);
out.writesAfterPolls = el.writes;
T.set({ openAsk: { ...ask, id: 'm-q', line: 'Which price?' } }, false, roomItems);
T.showAskLine(null);
out.changed = { html: el.html, writes: el.writes };
T.set({}, false, roomItems);
T.showAskLine(null);
out.closed = { hidden: el.hidden, html: el.html };

// The hub's row in a transcript: by its time, never above the first turn shown.
const row = ts => ({ id: 'n' + ts, from: 'ensemble', kind: 'notice', noticeKind: 'restart', text: 'The hub was restarted at 18:05.', ts });
const room = { messages: [row(now - 1000), row(now - 50), { id: 'x', kind: 'notice', noticeKind: 'due', ts: now - 40 }, row(now + 5)] };
out.rows = T.withHubRows(turns, room).map(m => m.id);
out.untouched = T.withHubRows(turns, { messages: [] }) === turns;
out.landmark = [T.isLandmark(row(1)), T.isLandmark({ kind: 'notice', noticeKind: 'due' })];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node not found")
class TheAskLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = "\n".join([
            js_line(SRC, "const isHubRow ="), js_line(SRC, "const isPoMsg ="), js_line(SRC, "const isLandmark ="),
            js_function(SRC, "chatWhen"),
            SRC[SRC.index("// ---- The open ask: what the task put"):SRC.index("// ---- The open ask: end")],
            js_line(SRC, "let ASK_SIG ="), js_function(SRC, "showAskLine"), js_function(SRC, "withHubRows"),
        ])
        r = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code}), capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, r.stderr
        cls.out = json.loads(r.stdout)

    def test_the_line_says_since_when_and_the_asks_first_line(self):
        html = self.out["html"]
        self.assertRegex(html, r'^<span class="ask-text">Waiting for you since T\d+(\.\d+)?: <a class="ask-link" ')
        self.assertIn('href="/session?id=room-x5&amp;msg=m-blocked" data-ref-msg="m-blocked"', html)
        self.assertIn(">AutoScout24 is logged out &lt;b&gt;</a></span>", html)       # text, never markup
        self.assertNotIn("<a ", self.out["plain"])
        self.assertIn("AutoScout24 is logged out", self.out["plain"])
        self.assertIn("see the report", self.out["noLine"])

    def test_it_links_to_the_asks_balloon(self):
        self.assertEqual(self.out["roomMid"], "m-blocked")
        self.assertEqual(self.out["roomMidGone"], "")            # not in this chat: plain words
        self.assertEqual(self.out["soloMid"], "s:2")             # the turn that made the report
        self.assertEqual(self.out["soloAfter"], "s:1")           # older than what is shown: the first after
        self.assertEqual((self.out["soloNone"], self.out["noAsk"]), ("", ""))

    def test_the_hub_decides_and_the_page_draws(self):
        self.assertFalse(self.out["shown"]["hidden"])
        self.assertIn("AutoScout24 is logged out", self.out["shown"]["html"])
        self.assertEqual(self.out["closed"], {"hidden": True, "html": ""})
        # No rule of its own about what closes an ask.
        block = SRC[SRC.index("// ---- The open ask: what the task put"):SRC.index("// ---- The open ask: end")]
        for word in ("reportKind", "clears", "'update'", "completed"):
            self.assertNotIn(word, block)

    def test_a_poll_does_not_replace_it(self):
        self.assertEqual(self.out["shown"]["writes"], 1)
        self.assertEqual(self.out["writesAfterPolls"], 1)
        self.assertEqual(self.out["changed"]["writes"], 2)
        self.assertIn("Which price?", self.out["changed"]["html"])

    def test_it_is_wired_into_the_page(self):
        self.assertLess(SRC.index('<div id="ask-line" role="status" hidden></div>'), SRC.index('<div id="msgs"></div>'))
        self.assertIn("showAskLine(items);", js_function(SRC, "renderBubbles"))
        self.assertIn("showAskLine(null);", js_function(SRC, "refreshRoom"))
        self.assertIn("ev.target.closest('a.ref-chip, a.sup-link, a.cu-link, a.ask-link, a.pt-link')", SRC)

    def test_it_looks_like_the_catch_up_line_from_tokens_alone(self):
        rule = SRC[SRC.index("  #ask-line {"):SRC.index("  /* Away from the end of the chat")]
        for decl in ("background:var(--surface)", "border:1px solid var(--border)", "border-radius:var(--r-200)",
                     "color:var(--fg-muted)", "font-size:var(--fs-200)", "line-height:16px"):
            self.assertIn(decl, rule)
            self.assertIn(decl, SRC[SRC.index("  #msgs > .catchup {"):SRC.index("  #msgs > .catchup .cu-text")])
        self.assertIn("#ask-line .ask-link { color:var(--link); }", rule)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|opacity", rule))
        self.assertIn("#ask-line[hidden] { display:none; }", rule)
        self.assertIn("#ask-line .ask-link::after { content: ''; position: absolute; left: 0; right: 0; top: -14px; bottom: -14px; }", SRC)

    def test_the_hubs_restart_row_goes_in_by_its_time(self):
        self.assertEqual(self.out["rows"], ["s:0", "s:1", "s:2", "rot1", "n" + self.out["rows"][4][1:], "s:3", "s:4",
                                            self.out["rows"][7]])
        self.assertTrue(self.out["rows"][4].startswith("n") and self.out["rows"][7].startswith("n"))
        self.assertEqual(len(self.out["rows"]), 8)               # the one older than the first turn is left out
        self.assertTrue(self.out["untouched"])
        self.assertEqual(self.out["landmark"], [True, False])
        self.assertIn("isHubRow(m) ? m.text : rotationText(", SRC)


if __name__ == "__main__":
    unittest.main()
