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
import re
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
const ctx = { attSplit: t => ({ words: String(t || ''), paths: [] }), stripRefBlocks: t => t,
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
// The card's words (#238, tests/test_card_text.py): plain here.
ctx.cardTextHtml = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); ctx.hubUrlWords = s => String(s ?? ''); ctx.cardText = s => ({ html: ctx.cardTextHtml(s), links: [] }); ctx.cardWords = s => String(s ?? '');
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, pointMaps, pointBarHtml, heldBy, foldPlan,
    chatGroups, quietItem, catchUp, pointAct, stripPointLines, hasPointLines, stripImagePlaceholders, hasImagePlaceholders, refPreview, lastAnswer,
    setPoints: v => { POINTS = v; }, points: () => POINTS };`, ctx);
const T = ctx.t;
const out = {};
const href = mid => '/session?room=room-po&msg=' + mid;
// P2 "answered": a hub from before the stages (read as delivered).
const pv = { open: 1, planned: 2, delivered: 2, approvals: [], items: [
  { id: 'P3', state: 'open', text: 'Why is the build red?\n\n[point P3]', createdAt: 1000, mid: 's:4', answers: [] },
  { id: 'P2', state: 'answered', text: 'Is #26 merged?', createdAt: 900, mid: 's:0', answers: [{ mid: 's:2', at: 950, how: 'implicit' }] },
  { id: 'P1', state: 'acked', text: 'Old one', createdAt: 800, mid: 's:9', answers: [{ mid: 's:10', at: 810 }] },
  { id: 'P4', state: 'answered', text: 'Start the docs task', createdAt: 1100, mid: 's:5', answers: [{ at: 1200, how: 'tool', summary: 'claude: started as #75' }] },
  { id: 'P5', state: 'planned', text: 'Fix the gap', createdAt: 1300, mid: 's:11', task: { ref: '#104', workflow: 'inprogress', workflowName: 'In progress' },
    answers: [{ mid: 's:12', at: 1310, how: 're', kind: 'plan', task: '#104' }] },
  { id: 'P6', state: 'delivered', text: 'Ship the stages', createdAt: 1400, mid: 's:13', task: { ref: '#105' },
    answers: [{ mid: 's:14', at: 1410, how: 're', kind: 'plan', task: '#105' }, { mid: 's:15', at: 1500, how: 're' }] },
  { id: 'P7', state: 'planned', text: 'Merged one', createdAt: 1450, mid: 's:16', task: { ref: '#106', workflow: 'done', workflowName: 'Done', done: true },
    answers: [{ mid: 's:17', at: 1460, how: 're', kind: 'plan', task: '#106' }] },
] };
const P = T.pointMaps(pv);
out.his = T.pointBarHtml({ id: 's:4', from: 'user' }, P, href);
out.hisAnswered = T.pointBarHtml({ id: 's:0', from: 'user' }, P, href);
out.hisAcked = T.pointBarHtml({ id: 's:9', from: 'user' }, P, href);
out.agent = T.pointBarHtml({ id: 's:2', from: 'claude', text: 'Yes.' }, P, href);
out.agentAcked = T.pointBarHtml({ id: 's:10', from: 'claude', text: 'Yes.' }, P, href);
out.nothing = T.pointBarHtml({ id: 's:7', from: 'claude', text: 'x' }, P, href);
out.hisPlanned = T.pointBarHtml({ id: 's:11', from: 'user' }, P, href);
out.hisMerged = T.pointBarHtml({ id: 's:16', from: 'user' }, P, href);
out.agentPlan = T.pointBarHtml({ id: 's:12', from: 'claude', text: 'Re P5 (planned #104): on it.' }, P, href);
out.hisDelivered = T.pointBarHtml({ id: 's:13', from: 'user' }, P, href);
out.oldPlan = T.pointBarHtml({ id: 's:14', from: 'claude', text: 'Re P6 (planned #105): on it.' }, P, href);
out.delivery = T.pointBarHtml({ id: 's:15', from: 'claude', text: 'Re P6: live.' }, P, href);
out.keep = [...P.keep].sort(); out.unacked = [...P.unacked].sort();
out.images = [T.stripImagePlaceholders('[Image #2] [Image #3]\n\n## Points (2)\n**1.** a\n[image] C:\\t\\attachments\\a.png'),
  T.stripImagePlaceholders('[Image #3] a picture of my own'), T.stripImagePlaceholders('look at [Image #1] this one'),
  T.stripImagePlaceholders('see [image] x and [Image 2]'), T.stripImagePlaceholders('[Image]'),
  T.stripImagePlaceholders('[Image #1]**1.** a\n[image]\n\n**2.** b\n[image] C:\\t\\attachments\\b.png'),
  T.stripImagePlaceholders('Reproduce with this input:\n```text\n[image]\n```'),
  T.stripImagePlaceholders('[Image #1] see\n[image]\n~~~\n[Image #2]\n[image]\n~~~\nafter'),
  T.stripImagePlaceholders('Example:\n````markdown\n```text\n[image]\n[Image #2]\n```\n````\n[image]'),
  T.stripImagePlaceholders('~~~~\n~~~\n[image]\n~~~~ not a close\n[Image #1]\n~~~~~\n[image]')];
// A link's preview of the person's message reads as their balloon; an agent's is as written.
out.previews = [T.refPreview({ from: 'user', text: '[Image #2] [Image #3]\n\nPlease compare these screenshots' }),
  T.refPreview({ from: 'claude', text: 'Your [Image #2] shows it' })];
out.hasImages = [T.hasImagePlaceholders('[Image #12]x'), T.hasImagePlaceholders('[image] a.png')];
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

// A decision's balloon: no Done or Go ahead of its own (those are the
// selection bar's reactions, P228); one answered so before shows it.
(async () => {
  T.setPoints(T.pointMaps({ items: [], approvals: [] }));
  const dec = { id: 's:7', from: 'claude', text: 'Decision needed: ship on Friday?\n\n- yes\n- no\n\nI recommend yes: the tests are green.' };
  out.decBar = T.pointBarHtml(dec, T.points(), href);
  out.approvedBar = T.pointBarHtml(dec, T.pointMaps({ items: [], approvals: ['s:7'] }), href);
  out.doneBar = T.pointBarHtml(dec, T.pointMaps({ items: [], dones: ['s:7'] }), href);
  const post = (path, body) => { posts.push([path, body]); return new Promise(r => setTimeout(() => r({ ok: true }), 5)); };
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
        run = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": esc + "\n" + fold_block(SRC) + fn_src("refPreview")}),
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.r = json.loads(run.stdout)

    def test_the_persons_balloon_says_where_each_point_stands(self):
        r = self.r
        # #361: each point says plainly what became of it (received, done, acknowledged).
        self.assertIn('<span class="pt-chip pt-id" data-pt-id="P3" data-pt-state="open">P3</span>', r["his"])
        self.assertIn('<span class="pt-st received">received</span><span class="pt-chip">not started yet</span>', r["his"],
                      "no agent at work on this page: received, not started yet")
        self.assertIn('data-pt="P3" data-pt-act="drop"', r["his"])
        self.assertIn('class="pt-link" href="/session?room=room-po&amp;msg=s:2&amp;part=re:P2" data-ref-msg="s:2" data-ref-part="re:P2"', r["hisAnswered"])
        self.assertIn('data-pt-state="delivered"', r["hisAnswered"], "answered, from a hub before the stages, is delivered")
        self.assertIn('<span class="pt-st done">done — <a class="pt-link"', r["hisAnswered"])
        self.assertIn('title="Go to the answer">see the answer ↓</a></span>', r["hisAnswered"])
        self.assertNotIn("data-pt-act", r["hisAnswered"], "a delivered point is neither dropped nor reopened")
        self.assertIn('<span class="pt-st done">done — ', r["hisAcked"])
        self.assertIn('<span class="pt-chip">acknowledged</span>', r["hisAcked"])
        # Read again after the thumbs up, the question still leads to its answer.
        self.assertIn('data-ref-msg="s:10"', r["hisAcked"])
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

    def test_what_never_folds(self):
        # #311: the chat's Needs you line is gone (the Inbox lists what waits).
        r = self.r
        self.assertEqual((r["keep"], r["unacked"]), (["s:11", "s:12", "s:15", "s:16", "s:17", "s:2", "s:4"], ["s:15", "s:2"]),
                         "an open or planned point's balloon and its plan, an unacknowledged delivery")

    def test_a_planned_point_shows_its_task_and_takes_no_thumbs_up(self):
        r = self.r
        # #361: a task the page cannot open (no roomId) leads to the plan.
        self.assertIn('<span class="pt-st working"><span class="dot" aria-hidden="true"></span>in progress — <a class="pt-link"', r["hisPlanned"])
        self.assertIn('data-ref-msg="s:12" data-ref-part="re:P5" title="Go to the plan">#104 · in progress ↓</a>', r["hisPlanned"])
        self.assertNotIn("pt-task", r["hisPlanned"])
        self.assertIn('data-pt="P5" data-pt-act="drop"', r["hisPlanned"])
        # A merged task is no task to open: its words lead to the plan.
        self.assertIn('data-ref-msg="s:17" data-ref-part="re:P7" title="Go to the plan">#106 merged, awaiting go-live ↓</a>', r["hisMerged"])
        self.assertNotIn("pt-task", r["hisMerged"])
        self.assertIn("plans P5 ↑", r["agentPlan"])
        self.assertIn("#104 · in progress", r["agentPlan"])
        self.assertNotIn('data-pt-act="ack"', r["agentPlan"], "a plan takes no thumbs up")

    def test_a_delivered_point_links_its_delivery_and_keeps_its_plan(self):
        r = self.r
        self.assertIn('data-ref-msg="s:15"', r["hisDelivered"], "the person's balloon leads to the delivery, not the plan")
        self.assertIn('<span class="pt-st done">done — <a class="pt-link"', r["hisDelivered"])
        self.assertIn('data-ref-part="re:P6" title="Go to the answer">see the answer ↓</a>', r["hisDelivered"])
        self.assertIn("plans P6 ↑", r["oldPlan"])
        self.assertNotIn('data-pt-act="ack"', r["oldPlan"])
        self.assertIn("answers P6 ↑", r["delivery"])
        self.assertIn('data-pt="P6" data-pt-act="ack"', r["delivery"])

    def test_the_hubs_point_lines_are_not_shown(self):
        self.assertEqual(self.r["strip"], ["Why?", "## Review comments (2)\n\n**1.** a\n\n**2.** b", "plain  ", False])

    def test_an_agents_image_placeholders_are_not_shown(self):
        # Claude Code's line of placeholders above a points message (P47), one
        # in the words, Codex's, and a text without any, which is unchanged.
        self.assertEqual(self.r["images"], [
            "## Points (2)\n**1.** a\n[image] C:\\t\\attachments\\a.png",
            "a picture of my own",
            "look at this one",
            "see [image] x and [Image 2]",
            "",
            # An image the hub could not show keeps a bare "[image]" line: not shown.
            "**1.** a\n\n**2.** b\n[image] C:\\t\\attachments\\b.png",
            # A code sample is the person's words, kept as typed.
            "Reproduce with this input:\n```text\n[image]\n```",
            "see\n~~~\n[Image #2]\n[image]\n~~~\nafter",
            # A longer fence holds a shorter one; it closes only on its own.
            "Example:\n````markdown\n```text\n[image]\n[Image #2]\n```\n````",
            "~~~~\n~~~\n[image]\n~~~~ not a close\n[Image #1]\n~~~~~",
        ])
        self.assertEqual(self.r["hasImages"], [True, False])
        self.assertEqual(self.r["previews"], ["Please compare these screenshots", "Your [Image #2] shows it"])
        self.assertIn("text: stripImagePlaceholders(stripPointLines(m.text))", SRC, "the person's balloons are drawn without them")

    def test_an_open_point_or_an_unacknowledged_answer_never_folds(self):
        r = self.r
        self.assertEqual(r["planWithout"], ["row", "row"], "without the rule both would fold")
        self.assertEqual(r["planKept"], ["full", "full"])
        self.assertIn(2, r["groupsWithout"][0])
        self.assertTrue(all(2 not in g for g in r["groupsWith"]), "the answer stays out of its task's row")

    def test_the_catch_up_line_counts_unacknowledged_answers_first(self):
        self.assertEqual(self.r["cu"][0], "1 new answer to your asks")
        self.assertEqual(self.r["cu"][1], "1 decision waiting")
        self.assertNotIn("1 answer to your asks to acknowledge", self.r["cuPlain"])

    def test_a_decision_has_no_answer_buttons_and_an_old_answer_shows(self):
        r = self.r
        self.assertEqual(r["decBar"], "", "Done and Go ahead are the selection bar's reactions now (P228)")
        self.assertIn("approved: go ahead", r["approvedBar"])
        self.assertIn(">done<", r["doneBar"])
        self.assertNotIn("<button", r["approvedBar"] + r["doneBar"])
        self.assertEqual([p for p in r["posts"] if p[0] == "/api/room/approve"], [])
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
const ctx = { out, POINTS_SIG: '', LAST_ITEMS: null, pointMaps: v => v, showPointsLine: () => {}, pointsTell: () => {},
  pointsAges: () => {}, renderBubbles: () => {}, POINTS: null, resolves: [],
  POINT_REFS: { refresh() {} },       // static/pointrefs.js (#265), which the page loads
  setInterval: () => 0 };             // the page's own timers (the room watch); the test ticks by hand
// The card's words (#238, tests/test_card_text.py): plain here.
ctx.cardTextHtml = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); ctx.hubUrlWords = s => String(s ?? ''); ctx.cardText = s => ({ html: ctx.cardTextHtml(s), links: [] }); ctx.cardWords = s => String(s ?? '');
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
const ctx = { out, $: () => box, CHAT_DRAWN: true, _landing: false, _cmtComposerOpen: false, selShown: () => false,
  GOTO: '', GOTO_PART: '', THREAD_MODE: false, URL_GOTO: '', SOLO_WANT: null, SOLO_SID: 'now', SOLO_AGENT: 'claude', openGroupOf: () => {},
  ibxPart: () => false,
  soloOwns: sid => sid === 'now' || sid === 'old', renderSolo: () => { out.renders++; },
  showGotoNote: t => { if (t) out.notes.push(t); } };
// The card's words (#238, tests/test_card_text.py): plain here.
ctx.cardTextHtml = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); ctx.hubUrlWords = s => String(s ?? ''); ctx.cardText = s => ({ html: ctx.cardTextHtml(s), links: [] }); ctx.cardWords = s => String(s ?? '');
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
        code = ("let POINTS_OPEN = false;\n"
                + "const FileLinks = { recheck() {} };   // static/filelinks.js, which the page loads\n"
                + SRC[SRC.index("let PT_GEN = 0"):SRC.index("function ptTicket")]
                + fn_src("ptTicket") + fn_src("pointsChanged")
                + SRC[SRC.index("let REFRESH_BUSY"):SRC.index("async function refreshRoom")])
        r = self.node(POLL_JS, code)
        self.assertEqual(r["afterThree"], [1, 1], "three timer ticks, one request")
        self.assertEqual(r["most"], 1)
        self.assertEqual(r["afterSlow"], {"v": "acked"})
        self.assertEqual(r["rooms2"], 2, "the refresh asked for meanwhile runs once, after")
        self.assertEqual(r["afterFresh"], {"v": "fresh"})

    def test_a_link_to_a_queued_line_loads_its_window(self):
        r = self.node(LAND_JS, fn_src("landPending"))
        self.assertEqual(r["want"], {"sid": "old", "key": "q0", "mid": "old:q0"})
        self.assertEqual((r["renders"], r["renders2"]), (2, 1), "loaded once per link")
        self.assertEqual(r["want2"], {"sid": "old", "key": "7", "mid": "old:7"})
        self.assertEqual(r["notes"], [], "never 'not found' before the transcript is drawn")


STATES_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { attSplit: t => ({ words: String(t || ''), paths: [] }), stripRefBlocks: t => t, URL,
  fetch: () => new Promise(() => {}),       // a link the hub has not resolved yet stays pending
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {}, TAIL_WORKING: [], ACT_READS: 0,
  withPathAbbrevs: (t, fn) => fn(), ATT_PREFIX: '[image] ', ATT_FILE_PREFIX: '[file] ',
  // renderActivity's world: the room's agents, who works (BUSY), nothing else drawn.
  ROOM_AGENTS: [{ identity: 'claude', ptyId: 'pty-1' }, { identity: 'codex', ptyId: 'pty-2' }], ROOM_LIVE: true, BUSY: new Set(),
  LAST_ITEMS: null, renderLiveTail: () => {}, showState: () => {}, renderBubbles: () => {} };
ctx.agentState = a => ({ cls: ctx.BUSY.has(a.identity) ? 'working' : 'idle' });
ctx.cardTextHtml = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); ctx.hubUrlWords = s => String(s ?? ''); ctx.cardText = s => ({ html: ctx.cardTextHtml(s), links: [] }); ctx.cardWords = s => String(s ?? '');
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, pointMaps, pointBarHtml, ptStateHtml, ptStillWorking, ptAfter, ptWorker, ptWorkers, isAgentMsg,
    ptNowHtml, ptTaskHtml, PT_SETTLED, foldBalloonHtml, replyStrip, REPLY_HEAD_RE, REPLY_QUOTE_HEAD_RE, openAsks, asksOf, REFS,
    pointItems, pointItemsHtml, renderActivity, setPoints: v => { POINTS = v; } };`, ctx);
const T = ctx.t;
const out = {};
const href = mid => '/session?room=room-po&msg=' + mid;
T.CHAT_NAMES.po = 'claude';
const W = [{ identity: 'claude', ptyId: 'pty-1' }, { identity: 'codex', ptyId: 'pty-2' }];
const pv = { items: [
  { id: 'P1', state: 'open', text: 'Why is it red?', mid: 'u1', answers: [] },
  { id: 'P2', state: 'open', text: 'And this?', mid: 'u2', answers: [] },
  { id: 'P3', state: 'delivered', text: 'Widen the columns', mid: 'u3', answers: [{ mid: 'a3', at: 3, how: 'implicit' }] },
  { id: 'P4', state: 'delivered', text: 'Rename it', mid: 'u4', answers: [{ mid: 'a4', at: 4, how: 'implicit' }] },
  { id: 'P5', state: 'planned', text: 'Phones', mid: 'u5', task: { ref: '#104', title: 'Phone layout check', roomId: 'room-abc', workflowName: 'In progress' },
    answers: [{ mid: 'a5', at: 5, how: 're', kind: 'plan', task: '#104' }] },
  { id: 'P6', state: 'planned', text: 'Later', mid: 'u6', answers: [{ mid: 'a6', at: 6, how: 're', kind: 'plan' }] },
  { id: 'P7', state: 'delivered', text: 'Which one?', mid: 'u7', answers: [{ mid: 'a7', at: 7, how: 're' }] },
  { id: 'P8', state: 'acked', text: 'Old', mid: 'u8', answers: [{ mid: 'a8', at: 8, how: 're' }] },
  { id: 'P9', state: 'followed', text: 'First', mid: 'u9', followedBy: 'P10', answers: [{ mid: 'a9', at: 9, how: 're' }] },
  { id: 'P10', state: 'open', text: 'Second', mid: 'u10', replyTo: 'P9', answers: [] },
  { id: 'P11', state: 'dropped', text: 'Never mind', mid: 'u11', answers: [] },
  { id: 'P12', state: 'followed', text: 'Gone', mid: 'u12', followedBy: 'P99', answers: [] },
  // Answered by a message with a question card, which the Inbox files under
  // Older questions (not in needs.decisions): open, then answered.
  { id: 'P13', state: 'delivered', text: 'A or B?', mid: 'u13', answers: [{ mid: 'a13', at: 13, how: 're' }] },
  { id: 'P14', state: 'delivered', text: 'C or D?', mid: 'u14', answers: [{ mid: 'a14', at: 14, how: 're' }] },
  // A numbered request, one point per item.
  { id: 'P20', state: 'open', text: '**1.** a', mid: 'u20', createdAt: 1, answers: [] },
  { id: 'P21', state: 'delivered', text: '**2.** b', mid: 'u20', createdAt: 2, answers: [{ mid: 'a21', at: 21, how: 're' }] },
], needs: { decisions: [{ mid: 'a7' }] }, asks: { a14: { 0: { option: 'C' } } } };
const P = T.pointMaps(pv);
// The drawn chat: u1 has no agent's message after it, u2 has one; a3 is
// the latest; u4's answer has the person's next message after it.
const tail = [{ id: 'x0', from: 'claude', text: 'Earlier.' }, { id: 'u1', from: 'user', text: 'Why is it red?' }];
const card = (id, q) => ({ id, from: 'claude', to: 'user', text: q, asks: [{ n: 0, question: q, options: [{ label: 'A' }, { label: 'B' }], recommended: -1 }] });
P.drawn = [
  card('a13', 'A or B?'), card('a14', 'C or D?'),
  { id: 'u1', from: 'user', text: 'Why is it red?' },
  { id: 'u2', from: 'user', text: 'And this?' }, { id: 'x2', from: 'codex', text: 'Something else.' },
  { id: 'u4', from: 'user', text: 'Rename it' }, { id: 'a4', from: 'claude', text: 'On it.' }, { id: 'u4b', from: 'user', text: 'thanks' },
  { id: 'u3', from: 'user', text: 'Widen the columns' }, { id: 'a3', from: 'claude', text: 'On it, widening them.', ts: 30 },
];
const bar = mid => T.pointBarHtml({ id: mid, from: 'user' }, P, href);
const st = id => T.ptStateHtml(P.byId.get(id), P, href);
// Nobody at work.
out.idle = { open: st('P1'), planned: st('P6'), implicit: st('P3'), still: T.ptStillWorking(P.byId.get('P3'), P), agentA3: T.pointBarHtml({ id: 'a3', from: 'claude' }, P, href) };
// The agents at work.
ctx.TAIL_WORKING = W;
const drawn = P.drawn;
P.drawn = tail; const openWork = st('P1'); P.drawn = drawn;
out.work = { workers: T.ptWorkers().length, open: openWork, openAfterAgent: st('P2'), openAfterAny: st('P1'), implicit: st('P3'), still: T.ptStillWorking(P.byId.get('P3'), P),
  stillWorker: T.ptWorker(P, 'a3').ptyId, firstWorker: T.ptWorker(P, null).identity, codexWorker: T.ptWorker(P, 'x2').identity,
  inputAfter: st('P4'), stillAfter: T.ptStillWorking(P.byId.get('P4'), P), agentA3: T.pointBarHtml({ id: 'a3', from: 'claude' }, P, href),
  task: st('P5'), planned: st('P6'), waiting: st('P7'), acked: st('P8'), followed: st('P9'), followUp: st('P10'), dropped: st('P11'),
  followedGone: st('P12'), cardOpen: st('P13'), cardAnswered: st('P14'),
  after: [T.ptAfter(P, 'u2', T.isAgentMsg), T.ptAfter(P, 'u3', T.isAgentMsg), T.ptAfter(P, 'a3', T.isAgentMsg), T.ptAfter(P, 'nope', T.isAgentMsg)],
  agentMsg: [T.isAgentMsg({ from: 'claude' }), T.isAgentMsg({ from: 'user' }), T.isAgentMsg({ from: 'ensemble' }), T.isAgentMsg({ from: 'claude', kind: 'pomsg' }), T.isAgentMsg(null)],
  now: [T.ptNowHtml(W[0]), T.ptNowHtml({ identity: 'x' }), T.ptNowHtml(null)],
  taskLink: [T.ptTaskHtml(P.byId.get('P5')), T.ptTaskHtml({ task: { ref: '#9', roomId: 'room-9', done: true } }), T.ptTaskHtml({ task: { ref: '#9' } }), T.ptTaskHtml({})] };
// The page saw the agents stop after that "on it": done from then on.
T.PT_SETTLED.add('a3');
out.settled = { implicit: st('P3'), still: T.ptStillWorking(P.byId.get('P3'), P), agentA3: T.pointBarHtml({ id: 'a3', from: 'claude' }, P, href) };
T.PT_SETTLED.delete('a3');
// Q2 after a reload (PT_SETTLED empty): the hub's turnEnds say whether the
// author's turn ended after its "on it" answer.
const q2 = (ends, workers) => { P.turnEnds = ends; ctx.TAIL_WORKING = workers;
  const r = { still: T.ptStillWorking(P.byId.get('P3'), P), state: st('P3'), agent: T.pointBarHtml({ id: 'a3', from: 'claude' }, P, href) };
  P.turnEnds = {}; ctx.TAIL_WORKING = W; return r; };
out.q2 = {
  endedAfter: q2({ claude: 40 }, W),             // working again on something else: done
  endedBefore: q2({ claude: 20, codex: 99 }, W), // its turn ended before the answer: still working
  otherWorks: q2({}, [W[1]]),                    // another agent works, the author does not: done
  pointMapsKeeps: T.pointMaps({ items: [], turnEnds: { claude: 5 } }).turnEnds, pointMapsNone: T.pointMaps({ items: [] }).turnEnds,
};
// renderActivity settles an "on it" answer whose author is not at work, even
// while another agent works; one whose author works stays open.
T.setPoints(P);
// The page's first state read may come before the live states: it settles nothing.
ctx.BUSY = new Set(['codex']); T.renderActivity({}, {});
out.q2.settledFirst = T.PT_SETTLED.has('a3');
ctx.BUSY = new Set(['claude', 'codex']); T.renderActivity({}, {});
out.q2.settledBusy = T.PT_SETTLED.has('a3');
ctx.BUSY = new Set(['codex']); T.renderActivity({}, {});
out.q2.settledOther = T.PT_SETTLED.has('a3');
out.q2.workingAfter = ctx.TAIL_WORKING.map(a => a.identity);
ctx.BUSY = new Set(['claude', 'codex']); T.renderActivity({}, {});
out.q2.afterSettle = { settled: T.PT_SETTLED.has('a3'), still: T.ptStillWorking(P.byId.get('P3'), P) };
T.PT_SETTLED.delete('a3'); ctx.TAIL_WORKING = W;

// The person's balloon: its words, then the state under it; a reply's strip on top.
T.REFS.set('room-po|a9', { state: 'ok', from: 'claude', roomId: 'room-po', text: 'The columns are wider on a test board now.', ts: 1789552800 });
// A replied-to message with a head of its own: the quote goes without it.
T.REFS.set('room-po|h1', { state: 'ok', from: 'claude', roomId: 'room-po', text: 'Re P61: the columns are wider.', ts: 1789552800 });
T.REFS.set('room-po|h2', { state: 'ok', from: 'claude', roomId: 'room-po', text: 'Re P61 (planned #5): on it.', ts: 1789552800 });
T.REFS.set('room-po|h3', { state: 'ok', from: 'user', roomId: 'room-po', who: 'sam', text: 'Re http://h/session?room=room-po&msg=a9: and the rows?', ts: 1789552800 });
T.REFS.set('room-po|h4', { state: 'ok', from: 'claude', roomId: 'room-po', text: 'Rephrased: P61 is done.', ts: 1789552800 });
const md = t => '<p>' + String(t) + '</p>';
const opt = { md, pts: m => T.pointBarHtml(m, P, href) };
out.ball = {
  own: T.foldBalloonHtml({ id: 'u1', from: 'user', text: 'Why is it red?', ts: 0 }, 0, 'full', opt),
  plain: T.foldBalloonHtml({ id: 'z1', from: 'user', text: 'no points here' }, 1, 'full', opt),
  agent: T.foldBalloonHtml({ id: 'a3', from: 'claude', text: 'On it.' }, 2, 'full', opt),
  // The page's body for the person's balloon: their numbered points ({ html, under }).
  numbered: T.foldBalloonHtml({ id: 'u20', from: 'user', text: 'Re http://h/session?room=room-po&msg=a9:\n\n## Points (2)\n\n**1.** a\n\n**2.** b' }, 3, 'full',
    Object.assign({ body: m => { const it = m.id ? T.pointItems(m.text) : null; return it ? T.pointItemsHtml(m, it, P, href, md) : null; } }, opt)),
  agentBody: T.foldBalloonHtml({ id: 'a3', from: 'claude', text: 'On it.' }, 5, 'full', Object.assign({ body: () => '<div class="asks">card</div>' }, opt)),
  reply: T.foldBalloonHtml({ id: 'u10', from: 'user', text: 'Re http://127.0.0.1:8080/session?room=room-po&msg=a9: and the rows?' }, 4, 'full', opt),
};
out.strip = {
  ok: T.replyStrip({ text: 'Re http://h/session?room=room-po&msg=a9&part=re:P9: and the rows?' }),
  pending: T.replyStrip({ text: 'Re http://h/session?room=room-po&msg=zz: hm' }),
  quotes: ['h1', 'h2', 'h3', 'h4'].map(k => (T.replyStrip({ text: `Re http://h/session?room=room-po&msg=${k}: ok` }).html.match(/<span class="q">“(.*?)”<\/span>/) || [])[1] || null),
  notHead: T.replyStrip({ text: 'see http://h/session?room=room-po&msg=a9: hm' }),
  head: ['Re http://h/session?room=r&msg=m: x', 're https://h/session?room=r&msg=s:2:', 'Rex http://h/session?room=r&msg=m: x', 'Re http://h/x:y'].map(t => {
    const x = t.match(T.REPLY_HEAD_RE); return x ? x[1] : null; }),
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs Node")
class BalloonStates(unittest.TestCase):
    """#361: what became of each request, under the person's balloon: received,
    working on it (with the agent's step now), in progress in its task,
    waiting for you, done, you replied; an "on it" answer is working on it
    until the agents stop (Q2)."""

    @classmethod
    def setUpClass(cls):
        esc = next(ln for ln in SRC.split("\n") if ln.startswith("const esc = "))
        ref_href = SRC[SRC.index("const refHref = "):SRC.index("const REFS = ")]
        code = (esc + "\n" + fold_block(SRC) + fn_src("refOfUrl") + ref_href + "const REFS = new Map();\n"
                + fn_src("refInfo") + fn_src("refWho") + fn_src("refPreview") + fn_src("itemsHtml") + fn_src("renderActivity"))
        run = subprocess.run([NODE, "-e", STATES_JS], input=json.dumps({"code": code}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60, env={**__import__("os").environ, "TZ": "UTC"})
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.r = json.loads(run.stdout)

    DOT = '<span class="dot" aria-hidden="true"></span>'

    def test_an_open_request_is_received_until_an_agent_works(self):
        idle, work = self.r["idle"], self.r["work"]
        self.assertIn('<span class="pt-st received">received</span><span class="pt-chip">not started yet</span>', idle["open"])
        self.assertIn('data-pt="P1" data-pt-act="drop"', idle["open"])
        self.assertNotIn("pt-now", idle["open"])
        self.assertEqual(work["workers"], 2)
        self.assertIn(f'<span class="pt-st working">{self.DOT}working on it</span>', work["open"])
        self.assertNotIn("received", work["open"])
        self.assertIn('data-pt="P1" data-pt-act="drop"', work["open"])
        self.assertIn('<div class="pt-now" data-pty="pty-1">', work["open"], "the step of the first agent at work")
        # An agent wrote after it (about something else): still not started.
        for k in ("openAfterAgent", "openAfterAny"):
            self.assertIn('<span class="pt-st received">received</span><span class="pt-chip">not started yet</span>', work[k], k)
            self.assertNotIn("pt-now", work[k], k)

    def test_the_step_line_and_who_it_follows(self):
        w = self.r["work"]
        self.assertEqual(w["now"], ['<div class="pt-now" data-pty="pty-1"><span class="now">now:</span><span class="ln"></span><span class="ago"></span></div>', "", ""])
        self.assertEqual((w["stillWorker"], w["firstWorker"], w["codexWorker"]), ("pty-1", "claude", "codex"),
                         "the agent that wrote the answer, else the first at work")
        self.assertEqual(w["after"], [True, True, False, None])
        self.assertEqual(w["agentMsg"], [True, False, False, False, False])

    def test_an_on_it_answer_is_working_until_the_agents_stop(self):
        idle, work, done = self.r["idle"], self.r["work"], self.r["settled"]
        # Agents at work, nothing after the answer: working on it, no Ack yet.
        self.assertTrue(work["still"])
        self.assertIn(f'<span class="pt-st working">{self.DOT}working on it</span>', work["implicit"])
        self.assertIn('<div class="pt-now" data-pty="pty-1">', work["implicit"])
        self.assertNotIn("done", work["implicit"])
        self.assertIn('data-pt="P3" data-pt-act="drop"', work["implicit"], "still being worked on: it can be dropped")
        self.assertIn("answers P3 ↑", work["agentA3"])
        self.assertNotIn('data-pt-act="ack"', work["agentA3"])
        self.assertNotIn('data-pt-act="comment"', work["agentA3"])
        # Nobody at work: done, with its Ack.
        self.assertFalse(idle["still"])
        self.assertIn('<span class="pt-st done">done — <a class="pt-link" href="/session?room=room-po&amp;msg=a3&amp;part=re:P3"', idle["implicit"])
        self.assertIn('data-pt="P3" data-pt-act="ack"', idle["agentA3"])
        # The page saw the agents stop since (PT_SETTLED): done even while they work again.
        self.assertFalse(done["still"])
        self.assertIn('<span class="pt-st done">done — ', done["implicit"])
        self.assertNotIn("pt-now", done["implicit"])
        self.assertIn('data-pt="P3" data-pt-act="ack"', done["agentA3"])
        # The person wrote after the answer: done.
        self.assertFalse(work["stillAfter"])
        self.assertIn('<span class="pt-st done">done — ', work["inputAfter"])
        self.assertIn('title="Go to the answer">see the answer ↓</a>', work["inputAfter"])

    def test_a_planned_request_links_its_task_or_its_plan(self):
        w, idle = self.r["work"], self.r["idle"]
        link = ('<a class="pt-task" href="/session?id=room-abc" data-task="room-abc" title="Open task #104 (in progress)">'
                '#104 Phone layout check ↗</a>')
        self.assertEqual(w["taskLink"], [link, "", "", ""], "a task merged, without a room or none: no task link")
        self.assertIn(f'<span class="pt-st working">{self.DOT}in progress — {link}</span>', w["task"])
        self.assertNotIn("pt-now", w["task"], "a task's step is in the task, not here")
        self.assertIn('data-pt="P5" data-pt-act="drop"', w["task"])
        # No task: its plan, working on it while an agent works.
        self.assertIn('<span class="pt-st working">in progress — <a class="pt-link" href="/session?room=room-po&amp;msg=a6&amp;part=re:P6"', idle["planned"])
        self.assertIn('title="Go to the plan">see the plan ↓</a></span>', idle["planned"])
        self.assertIn(f'<span class="pt-st working">{self.DOT}working on it — <a class="pt-link"', w["planned"])
        self.assertIn('<div class="pt-now" data-pty="pty-1">', w["planned"])

    def test_waiting_acknowledged_replied_and_dropped(self):
        w = self.r["work"]
        self.assertIn('<span class="pt-st waiting">waiting for you — <a class="pt-link" href="/session?room=room-po&amp;msg=a7&amp;part=re:P7"', w["waiting"])
        self.assertIn('title="Go to the question">answer the question ↓</a></span>', w["waiting"])
        self.assertNotIn("data-pt-act", w["waiting"])
        # The answer still holds an open question card, though the Inbox filed it
        # elsewhere (not in needs.decisions): waiting for you; answered: done.
        self.assertIn('<span class="pt-st waiting">waiting for you — <a class="pt-link" href="/session?room=room-po&amp;msg=a13&amp;part=re:P13"', w["cardOpen"])
        self.assertIn('<span class="pt-st done">done — ', w["cardAnswered"])
        self.assertNotIn("waiting", w["cardAnswered"])
        self.assertIn('<span class="pt-st done">done — <a class="pt-link" href="/session?room=room-po&amp;msg=a8&amp;part=re:P8"', w["acked"])
        self.assertIn('<span class="pt-chip">acknowledged</span>', w["acked"])
        self.assertIn('data-pt="P8" data-pt-act="reopen"', w["acked"])
        self.assertIn('<span class="pt-chip"><a class="pt-link" href="/session?room=room-po&amp;msg=u10&amp;part=pt:P10" data-ref-msg="u10" data-ref-part="pt:P10"'
                      ' title="Go to P10, your follow-up">you replied ↓</a></span>', w["followed"])
        self.assertIn('data-pt="P9" data-pt-act="reopen"', w["followed"])
        self.assertNotIn('class="pt-st', w["followed"])
        self.assertIn('<span class="pt-chip">you replied</span>', w["followedGone"], "a follow-up not drawn: no link")
        self.assertIn('follow-up to <a class="pt-link"', w["followUp"], "the follow-up still leads up to what it follows")
        self.assertIn('data-pt-id="P11" data-pt-state="dropped">P11</span><span class="pt-chip">dropped</span>', w["dropped"])
        self.assertIn('data-pt="P11" data-pt-act="reopen"', w["dropped"])

    def test_the_state_is_a_caption_under_the_persons_balloon(self):
        b = self.r["ball"]
        own = b["own"]
        self.assertTrue(own.startswith('<div class="msg user has-under" data-mid="u1" data-key="u1" data-from="user"><div class="bal"><div class="from">'), own)
        self.assertIn('<div class="text md"><p>Why is it red?</p></div></div><div class="pt-under"><div class="pt-bar">', own)
        self.assertTrue(own.endswith("</div></div></div>"))
        self.assertEqual(own.count('class="pt-bar"'), 1, "the bar is under the balloon, not in it too")
        # Without points and an agent's: no caption; an agent's own body (its asks) as it is.
        self.assertTrue(b["plain"].startswith('<div class="msg user" data-mid="z1"'))
        self.assertNotIn("has-under", b["plain"] + b["agent"] + b["agentBody"])
        self.assertIn('<div class="text md"><p>On it.</p></div><div class="pt-bar">', b["agent"], "an agent's bar stays in its balloon")
        self.assertIn('<div class="asks">card</div>', b["agentBody"])

    def test_a_numbered_reply_has_its_strip_its_items_and_their_states_under_it(self):
        # Review 1: a numbered request is option B too, and a reply's head is a strip.
        h = self.r["ball"]["numbered"]
        self.assertTrue(h.startswith('<div class="msg user has-under" data-mid="u20"'), h)
        bal, under = h.split('<div class="pt-under">')
        self.assertIn('<div class="bal"><div class="from">', bal)
        self.assertIn('<a class="ref-chip reply-strip" href="/session?room=room-po&amp;msg=a9"', bal)
        self.assertLess(bal.index("reply-strip"), bal.index('<div class="text md">'), "the strip on top")
        self.assertNotIn("Re http", h)
        self.assertIn('<ol class="pt-items"><li class="pt-item"><p>a</p><span class="pt-at" data-pt-at="P20" hidden></span></li>'
                      '<li class="pt-item"><p>b</p><span class="pt-at" data-pt-at="P21" hidden></span></li></ol>', bal,
                      "each item keeps its point's id, so a link to it lands on the item")
        self.assertNotIn("pt-bar", bal, "no state inside the balloon")
        self.assertEqual(under.count('<div class="pt-bar">'), 2)
        self.assertLess(under.index('data-pt-id="P20"'), under.index('data-pt-id="P21"'), "in the items' order")
        self.assertIn('data-pt-id="P21" data-pt-state="delivered"', under)
        self.assertIn("see the answer ↓", under)

    def test_an_on_it_answer_is_settled_by_its_own_agents_turn(self):
        # Review 1: durable (the hub's turnEnds, every device and after a reload) and per agent.
        q = self.r["q2"]
        self.assertFalse(q["endedAfter"]["still"])
        self.assertIn('<span class="pt-st done">done — ', q["endedAfter"]["state"])
        self.assertIn('data-pt="P3" data-pt-act="ack"', q["endedAfter"]["agent"])
        self.assertTrue(q["endedBefore"]["still"], "a turn that ended before the answer says nothing")
        self.assertIn(f'{self.DOT}working on it', q["endedBefore"]["state"])
        self.assertNotIn('data-pt-act="ack"', q["endedBefore"]["agent"])
        self.assertFalse(q["otherWorks"]["still"], "another agent at work does not keep it open")
        self.assertIn('<span class="pt-st done">done — ', q["otherWorks"]["state"])
        self.assertIn('data-pt="P3" data-pt-act="ack"', q["otherWorks"]["agent"])
        self.assertEqual((q["pointMapsKeeps"], q["pointMapsNone"]), ({"claude": 5}, {}))
        self.assertFalse(q["settledFirst"], "the page's first state read settles nothing")
        self.assertFalse(q["settledBusy"], "its author at work: not settled")
        self.assertTrue(q["settledOther"], "its author stopped while another works: settled")
        self.assertEqual(q["workingAfter"], ["codex"])
        self.assertEqual(q["afterSettle"], {"settled": True, "still": False}, "settled stays done when the author works again")

    def test_a_reply_shows_what_it_answers_as_a_strip_on_top(self):
        s, b = self.r["strip"], self.r["ball"]
        self.assertEqual(s["ok"]["text"], "and the rows?")
        self.assertEqual(s["ok"]["html"],
                         '<a class="ref-chip reply-strip" href="/session?room=room-po&amp;msg=a9&amp;part=re:P9" data-ref-room="room-po" data-ref-msg="a9" data-ref-part="re:P9"'
                         ' title="Go to the message you replied to"><span class="rs-arrow" aria-hidden="true">↩</span><b>replying to PO · 10:00</b>'
                         '<span class="q">“The columns are wider on a test board now.”</span><span class="up" aria-hidden="true">↑</span></a>')
        self.assertIsNone(s["pending"], "a link the hub has not resolved stays in the words")
        self.assertEqual(s["quotes"], ["the columns are wider.", "on it.", "and the rows?", "Rephrased: P61 is done."],
                         "the quote drops the replied-to message's own Re head, and only that")
        self.assertIsNone(s["notHead"], "only a reply's head, not a link in the words")
        self.assertEqual(s["head"], ["http://h/session?room=r&msg=m", "https://h/session?room=r&msg=s:2", None, None])
        reply = b["reply"]
        self.assertIn('<div class="bal"><div class="from">', reply)
        self.assertIn('↑</span></a><div class="text md"><p>and the rows?</p></div>', reply, "the strip on top, the words without the link")
        self.assertNotIn("Re http", reply)
        self.assertIn('<div class="pt-under"><div class="pt-bar">', reply)


class Contrast(unittest.TestCase):
    def test_the_bar_on_the_persons_balloon_is_not_muted(self):
        # --fg-muted on --hover measured 4.25:1 in Light at 12px.
        self.assertIn(".msg.user .pt-bar, .msg.user .pt-chip:not(.delivered) { color:var(--fg-subtle); }", SRC)


if __name__ == "__main__":
    unittest.main()
