"""Team activity never folds away a message the person waits for (#160,
GitHub issue 5).

Since #146 the PO chat folds the hub's traffic and the PO's notes on it behind
one "Team activity" row per gap. The PO chats of 10-01 16:47 to 10-02 11:35
folded twelve messages meant for the person that way (the list and the causes
are in the task's report in Documents): the PO's words after a hub line were
team activity unless they carried "Re Pn:" at a paragraph's head or "To
<name>:".

The page's own functions run in Node, over each real case as it reached the
page (the hub line, then the PO's reply that answers it), its words cut to the
sentences that matter:

* every real case is the person's to read (forCeo), with the reason found;
* plain acknowledgements of reports, progress checks and the PO's narration
  of its own work still fold, as do messages to a teammate;
* an ask is named only while it is open (open, in progress, or answered and
  not acknowledged); a gap's row names every ask its messages mention.

Skipped without Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NOUN = (ROOT / "static" / "noun.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def fold_block(src: str) -> str:
    return src[src.index("// ---- Folding a long conversation -"):src.index("// ---- Folding a long conversation: end")]


# The real cases: (where, the hub line's kind, extra fields of what it
# answers, the PO's words). Kept to the sentences that carry the message.
REAL = [
    ("Dock 10-01 16:48, after the hub's restart note (the person had approved option A just before)", "handover", {},
     "I created the task for screenshots without the share prompt, your option A, as draft #28. "
     "It waits for you to start it."),
    ("Dock 10-02 08:25, on #28's review 3", "report", {"reportKind": "review 3 (approved)", "taskId": "room-d28"},
     "The reviewer approved #28 (screenshots without the share prompt) in its third review.\n\n"
     "When the engineer reports it done, I'll release it as v0.12.0:\n- merge it into `main`;\n"
     "- reopen the demo for you.\n\nI've noted this in the handover. #19 is still a draft waiting for you to start it."),
    ("Ensemble 10-01 16:48, after the restart helper's note", "helper", {},
     "Fab, everything we built today is now live. The hub restarted at 16:47 and all ten running sessions "
     "came back on their own. Reload the page to see the changes.\n\n**What you'll see now:**\n"
     "- **Clean PO chat (P107):** a comment or follow-up of yours closes the point it answers."),
    ("Ensemble 10-02 09:21, on the Dock PO's message", "pomsg", {},
     "The Dock PO says Dock v0.12.0 is out: Take Screenshot no longer asks to share the tab, because Dock "
     "now draws the panel picture itself (this was your Dock point P23).\n\nI've started #158 to bring that "
     "version into Ensemble. I'll tell you when it's in."),
    ("Ensemble 10-02 10:33, on the trading PO's bug", "pomsg", {},
     "The trading project's PO reported that three of its tasks sat silent for about 17 hours without being "
     "flagged.\n\nI've started #159 at high priority. I've answered the trading PO. The fix needs a hub "
     "restart once it's merged."),
    ("Ensemble 10-02 11:35, in the handover turn", "handover", {},
     "Fab, the five tasks I started this morning (#158–#162) didn't do any work at first. They had no model "
     "of their own, so they ran on Claude Code's default, Fable.\n\n**What I changed:**\n- Settings › Agent "
     "models › Claude is now Opus, so every agent the hub starts uses Opus unless you pick something else there."),
    ("Motors 10-02 06:59, in the handover turn", "handover", {},
     "The handover file is up to date, and the next PO session picks up from there. Re P77, here's what I "
     "found before I had to stop. I haven't changed anything on the ad."),
    ("opTen 10-01 17:07, on a due check", "due", {},
     "The 17:05 market-data check failed on paper-1, so I've added it to #159 (the paper-check fixes).\n\n"
     "- **Trading impact:** no orders are affected. The cost is wasted quote lines, which is part of why the "
     "chains you open get no quotes."),
    ("opTen 10-01 17:09, a new PO's first words", "rotation", {},
     "I've taken over as PO and read the handover and roadmap.\n\n**Waiting on Fab:** a yes on P232, which "
     "would replace the fixed product lists with anything TWS lists that has options."),
    ("opTen 10-02 00:16, on a due check", "due", {},
     "The first 2-hour watcher hit its time limit before the checkpoint runs finished.\n\nWhen it ends I'll "
     "check that every service is up. Then I'll answer P231, P224 and P220."),
    ("opTen 10-02 10:34, a new PO's first words", "rotation", {},
     "Taking over as the 193rd PO.\n\n- **The recap you asked for (P245)** went out in the previous session's "
     "last message.\n\n**Decisions that are yours, none urgent:**\n- P232: one task so the product list becomes "
     "anything TWS lists that has options."),
    ("opTen 10-02 11:26, on #161's question", "report", {"reportKind": "question", "taskId": "room-o161"},
     "I answered #161's question with a ruling at the top of its spec.\n\n1. **The 5-wide stress cap is now "
     "0.60, not the 0.40 approved yesterday.** That is the loosening you asked for in P243."),
]

# Still team activity: real acknowledgements and narration from the same chats,
# and the #146 test's plain ones.
PLAIN = [
    ("report", {"reportKind": "review 3 (approved)", "taskId": "room-o160"},
     "The reviewer approved #160 on its third round. The approval covers points 1 to 5. The reviewer raised "
     "two minor points, neither blocking.\n\nPoint 6 is not finished yet. I'll merge only when it reports completed."),
    ("report", {"reportKind": "review 4 (changes requested)", "taskId": "room-o159"},
     "The reviewer sent #159 back for changes on its fourth review. #159 fixes these itself, so nothing is "
     "needed from me now. I'll merge it when it reports completed. I've noted this in the handover."),
    ("report", {"reportKind": "completed", "taskId": "room-t1"}, "#9's report came in; all good."),
    ("digest", {}, "Nothing new: #9 is merged."),
    ("pomsg", {}, "I noted the Dock PO's info of 10-02 08:49 in the handover, and no reply is needed. "
     "Nothing in opTen has to change."),
    ("helper", {}, "Now the handover."),
    ("report", {"reportKind": "completed", "taskId": "room-d28"}, "Now the demo for the CEO."),
    ("due", {}, "Running the 22:05 deploy now: stream-bridge only, with the two users created before the restart."),
    ("rotation", {}, "Checking the AS24 edit form for whether the Version (title) field takes free text."),
    ("pomsg", {}, "Our own new tasks may be running on Fable too. Checking."),
    ("handover", {}, "The handover is up to date for the next PO session.\n\n- **Points:** P248 is answered: dropped."),
    # Review 1: acknowledgements that say "you", "decision" or a negated
    # "blocked", and an answer still to come, quoted.
    ("digest", {}, "Nothing needs a decision. #140 is under review, and #143 is fixing the second review's findings."),
    ("digest", {}, "None of the new tasks is blocked."),
    ("digest", {}, "#138 is not blocked. Its engineer is still working through the first review."),
    ("digest", {}, "Nothing needs you yet. #114 is now in review."),
    ("digest", {}, "Nothing new: #115 is waiting for you to look at the layout page again."),
    ("digest", {}, "No action needed. The progress check says #150 is blocked by 4 failing tests, but its agent is fixing them."),
    ("digest", {}, "Nothing new here. The screenshot decision was already made."),
    ("digest", {}, "#128 isn't actually waiting on you or me. The board still shows its first question."),
    ("due", {}, "Still due today: 15:40 the \"Re P197\" take-profit check. Once #141 is merged I answer \"Re P198\"."),
]

NODE_JS = r"""
const vm = require('vm');
const { code, real, plain } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  attSplit: t => ({ paths: [], words: t }), stripRefBlocks: t => t, ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, forCeo, ceoWhy, gapsOf, gapRowHtml, gapAsksWords, pointMaps, setPoints: pv => { POINTS = pointMaps(pv); } };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'fab', po: 'claude', taskNo: () => null });
// The asks as the ledgers held them: these open, in progress or answered; P248 dropped.
const st = { P23: 'delivered', P77: 'open', P107: 'acked', P220: 'planned', P224: 'planned', P231: 'planned',
  P232: 'open', P243: 'planned', P245: 'delivered', P248: 'dropped' };
T.setPoints({ items: Object.entries(st).map(([id, state]) => ({ id, state, text: id, createdAt: 1, answers: [] })) });
const out = {};
let n = 0;
const hub = (kind, extra) => Object.assign({ id: 'h' + (++n), from: 'user', kind, text: '[' + kind + '] …', ts: n }, extra);
const po = (text, kind, extra) => ({ id: 'p' + (++n), from: 'claude', to: '', text, ts: n, answers: Object.assign({ kind }, extra) });
const pair = ([kind, extra, text]) => [hub(kind, extra), po(text, kind, extra)];
out.real = real.map(([where, kind, extra, text]) => { const [h, m] = pair([kind, extra, text]); return { where, forCeo: T.forCeo(m), why: T.ceoWhy(m), hub: T.forCeo(h) }; });
out.plain = plain.map(p => { const [h, m] = pair(p); return { text: p[2].slice(0, 50), forCeo: T.forCeo(m), why: T.ceoWhy(m) }; });
// The same words as a message to a teammate are the team's.
out.toMate = T.forCeo({ id: 'tm', from: 'claude', to: 'codex', text: 'Can you check P232? It is blocked.', ts: 99 })
  || T.forCeo({ id: 'tm2', from: 'claude', to: 'codex', text: 'Fix it before I write Re P232, the ruling.', ts: 99 });
out.noMsg = T.ceoWhy(null);
// Review 2: a negation ends at a comma or "but"/"and"; "Nothing blocks…" and
// "Noted." do not open an acknowledgement; "Re P77 -" and "Re P77 (" answer.
out.clauses = ["#141 hasn't reported yet, but #142 is blocked on the TWS login.", "Not only is #141 merged, but #142 is now blocked.",
  "#144 is not merged yet and #145 failed its review.", "I chose one folder per project, with no spaces, which matches what you asked for.",
  "Nothing blocks the merge any more: #141 is live.", "Noted. #141 is live now.", "Re P77 - done.", "Re P77 (planned #9) the button is back."]
  .map(text => [text, T.ceoWhy(po(text, 'digest', {}))]);
// An ask that is closed no longer keeps a message in view.
const closed = po('The handover lists P232 for the next session.', 'handover', {});
out.openAsk = T.forCeo(closed);
T.setPoints({ items: [{ id: 'P232', state: 'acked', text: 'x', createdAt: 1, answers: [] }] });
out.closedAsk = T.forCeo(closed);
// "Fable" is not the person's name; "Fab," is.
out.fable = T.forCeo(po('All five tasks hit the Fable limit.', 'pomsg', {}));
out.fab = T.forCeo(po('Fab, the tasks are back.', 'pomsg', {}));
// A gap names the asks its messages mention: plain ones, in one run.
const items = plain.flatMap(pair).concat([hub('points', {}), Object.assign(hub('report', { reportKind: 'completed', taskId: 'room-o1' }), { text: "[report] completed from task 'x (sam P239)' (#1, claude): done" })]);
const gaps = T.gapsOf(items, null);
out.gaps = gaps.length;
out.gapN = gaps[0] ? gaps[0].idx.length : 0;
out.row = gaps[0] ? T.gapRowHtml(gaps[0], items, false) : '';
out.asks = gaps[0] ? T.gapAsksWords(gaps[0], items) : '';
out.none = T.gapAsksWords({ idx: [0, 1] }, [hub('digest', {}), po('Nothing new.', 'digest', {})]);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class RealCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = NOUN + "\n" + fold_block(SRC)
        r = subprocess.run([NODE, "-e", NODE_JS], input=json.dumps({"code": code, "real": REAL, "plain": PLAIN}),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.o = json.loads(r.stdout.strip().splitlines()[-1])

    def test_every_real_case_stays_in_view(self):
        self.assertEqual(len(self.o["real"]), 12)
        for c in self.o["real"]:
            self.assertTrue(c["forCeo"], c["where"])
            self.assertTrue(c["why"], c["where"])
            self.assertFalse(c["hub"], "the hub line itself is still team activity: " + c["where"])

    def test_the_reasons(self):
        why = {c["where"].split(",")[0]: c["why"] for c in self.o["real"]}
        self.assertEqual(why["Motors 10-02 06:59"], "answer", "'Re P77,' inside a sentence answers P77")
        self.assertEqual(why["opTen 10-01 17:09"], "ask", "P232 is open: naming it keeps the message")
        self.assertEqual(why["Ensemble 10-02 10:33"], "decision", "a hub restart is the person's to do")
        self.assertEqual(why["Dock 10-01 16:48"], "spoken")

    def test_plain_acknowledgements_still_fold(self):
        for c in self.o["plain"]:
            self.assertFalse(c["forCeo"], c)
        self.assertFalse(self.o["toMate"], "a message to a teammate is the team's, whatever it says")
        self.assertEqual(self.o["noMsg"], "")

    def test_a_negation_takes_back_only_its_own_clause(self):
        for text, why in self.o["clauses"]:
            self.assertTrue(why, text)

    def test_an_ask_keeps_a_message_only_while_open(self):
        self.assertTrue(self.o["openAsk"])
        self.assertFalse(self.o["closedAsk"])

    def test_the_name_is_a_word(self):
        self.assertFalse(self.o["fable"])
        self.assertTrue(self.o["fab"])

    def test_the_gap_names_the_asks_inside(self):
        self.assertEqual(self.o["gaps"], 1, "the plain ones and the hub lines are one run")
        self.assertEqual(self.o["asks"], "3 name your asks: P248, P197, P198, P239",
                         "the closed P248 in a handover note, the quoted P197/P198 still to come, P239 in a task's report; never the reminder")
        self.assertIn('<span class="gasks">3 name your asks: P248, P197, P198, P239</span>', self.o["row"])
        self.assertIn("(3 name your asks: P248, P197, P198, P239)", self.o["row"], "the tooltip says it too")
        self.assertEqual(self.o["none"], "")


if __name__ == "__main__":
    unittest.main()
