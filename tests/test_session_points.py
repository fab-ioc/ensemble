"""The person's points in the chat (session.html): the page's own functions run
in Node and these check that:

* the person's balloon says where each point stands (waiting, answered with a
  link down to the answer, acknowledged, dropped) with Drop or Reopen, and an
  agent's balloon says which points it answers, linked up, with a thumbs up
  while the answer is not acknowledged;
* the line above the chat counts what waits for an answer and what waits for
  an acknowledgement, and lists both ends of each;
* a balloon holding an open point, or an unacknowledged answer, never folds:
  not by age, not into its task's row, not by Just us;
* the catch-up line counts unacknowledged answers first;
* a thumbs up on a decision with a recommendation sends the approval exactly
  once, and is not offered without a recommendation; an Ack posts once;
* the hub's "[point Pn]" lines are not shown.

Skipped without Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def fold_block(src: str) -> str:
    return src[src.index("// ---- Folding a long conversation -"):src.index("// ---- Folding a long conversation: end")]


JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const posts = [];
const ctx = { attSplit: t => ({ words: String(t || ''), paths: [] }),
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, pointMaps, pointBarHtml, pointsSummary, pointsListHtml, pointsLineHtml, heldBy, foldPlan,
    chatGroups, quietItem, catchUp, canApprove, approveDecision, pointAct, stripPointLines, hasPointLines, lastAnswer,
    setPoints: v => { POINTS = v; }, points: () => POINTS };`, ctx);
const T = ctx.t;
const out = {};
const href = mid => '/session?room=room-po&msg=' + mid;
const pv = { open: 1, answered: 1, approvals: [], items: [
  { id: 'P3', state: 'open', text: 'Why is the build red?\n\n[point P3]', createdAt: 1000, mid: 's:4', answers: [] },
  { id: 'P2', state: 'answered', text: 'Is #26 merged?', createdAt: 900, mid: 's:0', answers: [{ mid: 's:2', at: 950, how: 'implicit' }] },
  { id: 'P1', state: 'acked', text: 'Old one', createdAt: 800, mid: 's:9', answers: [{ mid: 's:10', at: 810 }] },
  { id: 'P4', state: 'answered', text: 'Start the docs task', createdAt: 1100, mid: 's:5', answers: [{ at: 1200, how: 'tool', summary: 'claude: started as #75' }] },
] };
const P = T.pointMaps(pv);
out.his = T.pointBarHtml({ id: 's:4', from: 'user' }, P, href);
out.hisAnswered = T.pointBarHtml({ id: 's:0', from: 'user' }, P, href);
out.hisAcked = T.pointBarHtml({ id: 's:9', from: 'user' }, P, href);
out.agent = T.pointBarHtml({ id: 's:2', from: 'claude', text: 'Yes.' }, P, href);
out.agentAcked = T.pointBarHtml({ id: 's:10', from: 'claude', text: 'Yes.' }, P, href);
out.nothing = T.pointBarHtml({ id: 's:7', from: 'claude', text: 'x' }, P, href);
out.summary = [T.pointsSummary(P), T.pointsSummary(T.pointMaps({ open: 0, answered: 2, items: [] })), T.pointsSummary(T.pointMaps(null))];
out.list = T.pointsListHtml(P, href, 2000);
out.lineClosed = T.pointsLineHtml(P, false, href, 2000);
out.keep = [...P.keep].sort(); out.unacked = [...P.unacked].sort();
out.strip = [T.stripPointLines('Why?\n\n[point P3]'), T.stripPointLines('## Review comments (2)\n\n**1.** a\n\n[point P1]\n\n**2.** b\n\n[point P2]'),
  T.stripPointLines('plain  '), T.hasPointLines('say [point P1] inline')];

// Never folds: an old open point's balloon, an unacknowledged answer to a
// report inside a task's messages, in Just us too.
const items = [
  { id: 's:4', from: 'user', kind: 'human', text: 'Why is the build red?', ts: 1 },
  { id: 's:1', from: 'user', kind: 'report', reportKind: 'completed', taskId: '#26', taskTitle: 'X', reporter: 'claude', text: '[report] …', ts: 2 },
  { id: 's:2', from: 'claude', answers: { kind: 'report', taskId: '#26' }, text: 'Re P2: merged.', ts: 3 },
  { id: 's:3', from: 'user', kind: 'report', reportKind: 'update', taskId: '#26', taskTitle: 'X', reporter: 'claude', text: '[report] …', ts: 4 },
];
for (let i = 0; i < 12; i++) items.push({ id: 'z:' + i, from: i % 2 ? 'claude' : 'user', kind: 'human', text: 'later ' + i, ts: 10 + i });
const fold = { base: null, open: new Set() };
const held = T.heldBy(new Set(), P.keep);
const quiet = (m, i) => T.quietItem(m, true);
const plan = T.foldPlan(items, fold, true, held, quiet);
const planNo = T.foldPlan(items, { base: null, open: new Set() }, true, null, quiet);
out.planKept = [plan[0], plan[2]]; out.planWithout = [planNo[0], planNo[2]];
const kept = i => held(items[i], i);
out.groupsWith = T.chatGroups(items, kept).map(g => g.hidden);
out.groupsWithout = T.chatGroups(items).map(g => g.hidden);

// Catch-up: an unacknowledged answer first.
const cuItems = [
  { id: 'a', from: 'user', kind: 'human', text: 'q', ts: 1 },
  { id: 'b', from: 'claude', answers: { kind: 'human' }, text: 'Decision needed: A or B?', ts: 2 },
  { id: 'c', from: 'claude', answers: { kind: 'report', taskId: '#7' }, text: 'Re P2: yes', ts: 3 },
  { id: 'd', from: 'claude', answers: { kind: 'human' }, text: 'more', ts: 4 },
];
T.CHAT_NAMES.po = 'claude';
out.cu = T.catchUp(cuItems, { i: 0, ts: 1, mine: true }, false, T.CHAT_NAMES, new Set(['c'])).parts.map(p => p.text);
out.cuPlain = T.catchUp(cuItems, { i: 0, ts: 1, mine: true }, false).parts.map(p => p.text);

// A thumbs up on a decision: sent once, whatever the clicks.
(async () => {
  T.setPoints(T.pointMaps({ items: [], approvals: [] }));
  const dec = { id: 's:7', from: 'claude', text: 'Decision needed: ship on Friday?\n\n- yes\n- no\n\nI recommend yes: the tests are green.' };
  const noRec = { id: 's:8', from: 'claude', text: 'Decision needed: A or B?' };
  const q = { id: 'm1', from: 'claude', kind: 'report', reportKind: 'question', text: 'Which DB? My recommendation: Postgres.' };
  out.can = [T.canApprove(dec), T.canApprove(noRec), T.canApprove(q), T.canApprove({ id: 'm2', from: 'user', text: 'Decision needed: x, I recommend' })];
  out.decBar = T.pointBarHtml(dec, T.points(), href);
  out.noRecBar = T.pointBarHtml(noRec, T.points(), href);
  const post = (path, body) => { posts.push([path, body]); return new Promise(r => setTimeout(() => r({ ok: true }), 5)); };
  await Promise.all([T.approveDecision(dec, post), T.approveDecision(dec, post)]);
  await T.approveDecision(dec, post);
  out.approvedBar = T.pointBarHtml(dec, T.points(), href);
  await Promise.all([T.pointAct('P2', 'ack', post), T.pointAct('P2', 'ack', post)]);
  out.posts = posts;
  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(NODE, "needs Node")
class Points(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The page's own esc, which takes strings only: a number in the markup throws.
        esc = next(ln for ln in SRC.split("\n") if ln.startswith("const esc = "))
        run = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": esc + "\n" + fold_block(SRC)}),
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.r = json.loads(run.stdout)

    def test_the_persons_balloon_says_where_each_point_stands(self):
        r = self.r
        self.assertIn("P3 · waiting for an answer", r["his"])
        self.assertIn('data-pt="P3" data-pt-act="drop"', r["his"])
        self.assertIn('class="pt-link" href="/session?room=room-po&amp;msg=s:2" data-ref-msg="s:2"', r["hisAnswered"])
        self.assertIn("P2 · answered ↓", r["hisAnswered"])
        self.assertIn("P1 · acknowledged", r["hisAcked"])
        self.assertIn('data-pt-act="reopen"', r["hisAcked"])
        self.assertNotIn("drop", r["hisAcked"])

    def test_the_answer_says_what_it_answers_with_a_thumbs_up(self):
        r = self.r
        self.assertIn("answers P2 ↑", r["agent"])
        self.assertIn('data-ref-msg="s:0"', r["agent"])
        self.assertIn('data-pt="P2" data-pt-act="ack"', r["agent"])
        self.assertIn("👍 Ack", r["agent"])
        self.assertIn("acknowledged", r["agentAcked"])
        self.assertNotIn('data-pt-act="ack"', r["agentAcked"])
        self.assertEqual(r["nothing"], "")

    def test_the_line_counts_and_lists_both_ends(self):
        r = self.r
        self.assertEqual(r["summary"], ["Your points: 1 waiting for an answer · 1 answered, not yet acknowledged",
                                        "Your points: 2 answered, not yet acknowledged", ""])
        rows = r["list"].split("</li>")
        self.assertEqual(len(rows) - 1, 3, "open and answered, not the acknowledged one")
        self.assertIn('data-pt="P2"', rows[0], "an answer to acknowledge comes first")
        self.assertIn("your message ↑", rows[0])
        self.assertIn("answer ↓", rows[0])
        self.assertIn("claude: started as #75", r["list"], "an answer given by doing says what was done")
        self.assertIn('data-pt="P3" data-pt-act="drop"', r["list"])
        self.assertIn("Why is the build red?", r["list"])
        self.assertNotIn("[point P3]", r["list"])
        self.assertIn('aria-expanded="false"', r["lineClosed"])
        self.assertIn("<ul id=\"points-list\" hidden>", r["lineClosed"])
        self.assertEqual((r["keep"], r["unacked"]), (["s:2", "s:4"], ["s:2"]))

    def test_the_hubs_point_lines_are_not_shown(self):
        self.assertEqual(self.r["strip"], ["Why?", "## Review comments (2)\n\n**1.** a\n\n**2.** b", "plain  ", False])

    def test_an_open_point_or_an_unacknowledged_answer_never_folds(self):
        r = self.r
        self.assertEqual(r["planWithout"], ["row", "row"], "without the rule both would fold")
        self.assertEqual(r["planKept"], ["full", "full"])
        self.assertIn(2, r["groupsWithout"][0])
        self.assertTrue(all(2 not in g for g in r["groupsWith"]), "the answer stays out of its task's row")

    def test_the_catch_up_line_counts_unacknowledged_answers_first(self):
        self.assertEqual(self.r["cu"][0], "1 answer to your points to acknowledge")
        self.assertEqual(self.r["cu"][1], "1 decision waiting")
        self.assertNotIn("1 answer to your points to acknowledge", self.r["cuPlain"])

    def test_a_thumbs_up_on_a_decision_sends_the_approval_once(self):
        r = self.r
        self.assertEqual(r["can"], [True, False, True, False])
        self.assertIn("👍 Go with it", r["decBar"])
        self.assertNotIn("pt-approve", r["noRecBar"])
        self.assertIn("approved", r["approvedBar"])
        self.assertNotIn("pt-approve", r["approvedBar"])
        approvals = [p for p in r["posts"] if p[0] == "/api/room/approve"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0][1], {"roomId": "room-po", "mid": "s:7", "to": "", "question": "ship on Friday?"})
        acks = [p for p in r["posts"] if p[0] == "/api/room/points"]
        self.assertEqual(acks, [["/api/room/points", {"roomId": "room-po", "id": "P2", "action": "ack"}]])


def fn_src(name: str, prefix: str = "function ") -> str:
    import re
    m = re.search(rf"^(?:async )?{prefix}{name}\(", SRC, re.M)
    return SRC[m.start():SRC.index("\n}\n", m.start()) + 3]


POLL_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = { inFlight: 0, most: 0, rooms: 0, drawn: [], wants: [], notes: [] };
const ctx = { out, POINTS_SIG: '', LAST_ITEMS: null, pointMaps: v => v, showPointsLine: () => {},
  pointsAges: () => {}, renderBubbles: () => {}, POINTS: null, resolves: [] };
vm.createContext(ctx);
vm.runInContext(code + `
  // The room request, held open until the test lets it answer.
  async function refreshRoom() {
    out.rooms++; out.inFlight++; out.most = Math.max(out.most, out.inFlight);
    const ticket = ptTicket();
    const pv = await new Promise(r => resolves.push(r));
    out.inFlight--;
    pointsChanged(pv, ticket);
  }
  globalThis.T = { refresh, ptTicket, pointsChanged, drawn: () => POINTS };`, ctx);
(async () => {
  const T = ctx.T;
  const tick = () => new Promise(r => setImmediate(r));
  T.refresh(); T.refresh(); T.refresh();           // the timer fires while the first is out
  await tick();
  out.afterThree = [out.rooms, out.inFlight];
  // An Ack answers while that poll is still out: its newer state is drawn...
  T.pointsChanged({ v: 'acked' }, T.ptTicket());
  // ...and the slow poll, which asked before, does not draw over it.
  ctx.resolves.shift()({ v: 'stale' });
  await tick(); await tick();
  out.afterSlow = T.drawn();
  out.rooms2 = out.rooms;                          // the asked-again refresh ran once
  ctx.resolves.shift()({ v: 'fresh' });
  await tick(); await tick();
  out.afterFresh = T.drawn();
  process.stdout.write(JSON.stringify(out));
})();
"""


LAND_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = { renders: 0, notes: [] };
const box = { clientHeight: 400, querySelectorAll: () => [] };
const ctx = { out, $: () => box, CHAT_DRAWN: true, _landing: false, _cmtComposerOpen: false, _selBtn: null,
  GOTO: '', SOLO_WANT: null, SOLO_SID: 'now', SOLO_AGENT: 'claude', openGroupOf: () => {},
  soloOwns: sid => sid === 'now' || sid === 'old', renderSolo: () => { out.renders++; },
  showGotoNote: t => { if (t) out.notes.push(t); } };
vm.createContext(ctx);
vm.runInContext(code + `
  GOTO = 'old:q0'; landPending();
  out.want = SOLO_WANT;
  landPending();                                   // not loaded yet: waits, asks nothing new
  out.renders2 = out.renders;
  GOTO = 'old:7'; landPending();
  out.want2 = SOLO_WANT;`, ctx);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs Node")
class PollAndLinks(unittest.TestCase):
    """The room poll is one request at a time and an older answer never draws
    over a newer one; a link to a line read while the agent was busy loads
    the transcript it is in."""

    def node(self, js, code):
        r = subprocess.run([NODE, "-e", js], input=json.dumps({"code": code}), capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_room_poll_is_one_at_a_time_and_a_slow_answer_draws_nothing(self):
        code = ("let POINTS_OPEN = false;\n" + SRC[SRC.index("let PT_GEN = 0"):SRC.index("function ptTicket")]
                + fn_src("ptTicket") + fn_src("pointsChanged")
                + SRC[SRC.index("let REFRESH_BUSY"):SRC.index("async function refreshRoom")])
        r = self.node(POLL_JS, code)
        self.assertEqual(r["afterThree"], [1, 1], "three timer ticks, one request")
        self.assertEqual(r["most"], 1)
        self.assertEqual(r["afterSlow"], {"v": "acked"})
        self.assertEqual(r["rooms2"], 2, "the refresh asked for meanwhile runs once, after")
        self.assertEqual(r["afterFresh"], {"v": "fresh"})

    def test_a_link_to_a_queued_line_loads_its_whole_transcript(self):
        r = self.node(LAND_JS, fn_src("landPending"))
        self.assertEqual(r["want"], {"sid": "old", "n": 0, "mid": "old:q0"})
        self.assertEqual((r["renders"], r["renders2"]), (2, 1), "loaded once per link")
        self.assertEqual(r["want2"], {"sid": "old", "n": 7, "mid": "old:7"})
        self.assertEqual(r["notes"], [], "never 'not found' before the transcript is drawn")


if __name__ == "__main__":
    unittest.main()
