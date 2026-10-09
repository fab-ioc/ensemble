"""A question an agent writes gets buttons, not a comment box alone
(GitHub issue 16, #195).

The forms are the ones found in the hub's real chats on 2026-10-09, names
left out: an ``Ask:`` with its options in the words ("Options: (1) …, (2) …,
or (3) …", "(a) … or (b) …", "A, or B?", "…: A, B, or C.", "… (recommended)?
Or do you want B, or C?", "Who? Just you, or your family?"), two questions
in one ask, a yes/no question with no options, and the fallback's plain final
questions ("Your options:" list before it, "Decision needed:" with a bulleted
list, "Decision for …: …? Also, …?"). The hub (``asks.parse`` /
``asks.safety``) and the page (``parseAsks`` / ``safetyAsk``) read each the
same way; a wh-question that names no options keeps a comment box alone.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asks  # noqa: E402
from tests.test_po_chat_clean import NODE, NOUN, SRC, fold_block  # noqa: E402

ASKS = {
    "options_colon": "Ask: Should I change the insurer draft? Options: keep it asking for the full offer "
                     "(my recommendation), or change it to ask for basic cover only.",
    "numbered_inline": "Ask: Do you want to move basic insurance for 2027? Options: (1) everyone at insurer K, about "
                       "CHF 490/yr saved, (2) K for the adults plus insurer A for the kids, about CHF 680/yr, or (3) "
                       "stay where we are. On option 1 or 2 I'll draft the notice.",
    "yesno_plain": "Ask: Should I check whether your four doctors are on the 2027 lists?",
    "or_comma": "Ask: will you add the tranches in the console now, or skip today's run?",
    "or_short": "Ask: Add tranche now, or skip today?",
    "bare_or": "Ask: buy or lease?",
    "yesno_with_or": "Ask: Has the buyer contacted a seller or bought a kit already?",
    "parens_list": "Ask: Should I move the remaining folders under one root `D:/x/` (backups, tasks, views) and "
                   "point the deploy scripts there? I would do it between runs.",
    "hash_list": "Ask: Should I deploy #214, #217 and #219 now (01:xx, the archive move in the same stop, services "
                 "back and checked well before the open), or keep it for Friday after 22:05?",
    "rec_then_or": "Ask: Should I build ranks 1–4 as one task (recommended)? It would go to an engineer. "
                   "Or do you want ranks 1–9, or a different selection?",
    "no_qmark": "Ask: Approve this design so #214 can build the screens, or tell me what to change, such as how "
                "a leg is moved.",
    "ab_and_second": "Ask: Should Trade detail (a) stay only as the detail of one open position, opened from "
                     "Positions, which I recommend, or (b) go completely, since Positions already shows legs? "
                     "And do you approve the rest of the design?",
    "two_alternatives": "Ask: Should the Trades panel show only today's trades, with past days in History (my "
                        "recommendation), or everything? And when you select an algo, should the filter start "
                        "off, or turn on by itself?",
    "or_what": "Ask: Do you approve this design, or what should change?",
    "colon_choices": "Ask: how should I check the console? The card offers three choices: allow me to select your "
                     "Chrome (recommended), check 7 Oct in History yourself, or accept the database rows as proof "
                     "this time.",
    "listed": "I won't start until I know what the project is for:\n\n"
              "1. **Ask:** What should the project do? For example, prepare tax returns, estimate or simulate "
              "taxes, keep track of deadlines and documents, or something else?\n"
              "2. **Ask:** Who will use it? Just you, your family, or outside users?\n"
              "3. **Ask:** Do you have a preference for how it runs?\n"
              "   - **Local app** — simplest (recommended)\n"
              "   - **Web app** — if other people need it\n",
    "wh_date": "1. **Ask:** On what date did the decisions reach you, by post or electronically?\n"
               "2. **Ask (yes/no):** Did the adviser send a worksheet?",
    "wh_open": "Ask: What is the project for, and who will read it?",
    "open_tag": "Ask (open): Should the empty list say something?",
    "rec_yes": "Ask: start #29 now? I recommend yes.",
    "rec_no": "Ask: Restart the hub tonight? I recommend against it.",
    "lead_label": "Ask: Routing: do you object to switching to CBOE for the paper proof, or would you rather keep SMART?",
    "choose_many": "Ask: Do you want both, only one, or a different cap, for example exactly 40?",
}

FALLBACK = {
    "decision_for": "Where things stand: the advice is done.\n\n"
                    "**Decision for the owner:** start task #1 now? Also, has the buyer contacted a seller already?",
    "options_lead": "Your options:\n- **V-Class:** about 1'119 all-in\n- **Vito 9 seats:** about 1'007\n"
                    "- **Cheapest:** the manual van at the airport, about 743\n\n"
                    "I'd book soon: the price went up 20% in three days. Which one do you want?",
    "decision_bullets": "Nothing is running now.\n\nDecision needed: which draft should start next?\n"
                        "- **#19 (recommended):** a click fix\n- **#23:** reorder buttons\n- **#20:** two docks\n\n"
                        "Usage is at 92%.",
    "wh_next": "Done and merged.\n\nWhat would you like next?",
    "your_decision": "Ready.\n\nYour decision: send it as it is, or should I have it checked further first?",
    "status_bullets": "- Merged the fix\n- Tests pass\n\nShall I deploy?",
    "so_what": "Re P4: nothing for now. P5 also arrived with no text, so what do you want done?",
    "when_or": "Two ways.\n\nWhen: early, meaning you ask for an offer now, or January?",
    "lettered": "Options:\nA. Keep it\nB. Drop it\n\nWhich should I use?",
}

NODE_JS = r"""
const vm = require('vm');
const { code, asks, fallback } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { esc: s => String(s ?? ''), attSplit: t => ({ paths: [], words: t }), stripRefBlocks: t => t,
  withPathAbbrevs: (t, f) => f(), ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
vm.createContext(ctx);
vm.runInContext(code + `\nglobalThis.t = { parseAsks, safetyAsk };`, ctx);
const out = { asks: {}, fallback: {} };
for (const [k, v] of Object.entries(asks)) out.asks[k] = JSON.parse(JSON.stringify(ctx.t.parseAsks(v)));
for (const [k, v] of Object.entries(fallback)) out.fallback[k] = JSON.parse(JSON.stringify(ctx.t.safetyAsk(v)));
console.log(JSON.stringify(out));
"""


def labels(a: dict) -> list[str]:
    return [o["label"] + ("*" if o["recommended"] else "") for o in a["options"]]


class TheWordsGiveTheOptions(unittest.TestCase):
    def one(self, key: str) -> dict:
        found = asks.parse(ASKS[key])
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_options_after_a_colon(self):
        a = self.one("options_colon")
        self.assertEqual((a["kind"], labels(a)), ("decision", ["Keep it asking for the full offer*",
                                                               "Change it to ask for basic cover only"]))

    def test_numbered_options_in_the_line(self):
        a = self.one("numbered_inline")
        self.assertEqual(labels(a), ["Everyone at insurer K", "K for the adults plus insurer A for the kids",
                                     "Stay where we are"])
        self.assertEqual(a["options"][0]["detail"], "about CHF 490/yr saved")

    def test_a_yes_no_question_gets_yes_and_no(self):
        for key in ("yesno_plain", "yesno_with_or", "parens_list"):
            with self.subTest(key=key):
                a = self.one(key)
                self.assertEqual((a["kind"], labels(a)), ("yesno", ["Yes", "No"]))

    def test_a_or_b(self):
        self.assertEqual(labels(self.one("or_comma")), ["Add the tranches in the console now", "Skip today's run"])
        self.assertEqual(labels(self.one("or_short")), ["Add tranche now", "Skip today"])
        self.assertEqual(labels(self.one("bare_or")), ["Buy", "Lease"])
        self.assertEqual(labels(self.one("or_what")), ["Approve this design", "What should change"])
        self.assertEqual(labels(self.one("lead_label")),
                         ["Object to switching to CBOE for the paper proof", "Keep SMART"])
        self.assertEqual(labels(self.one("choose_many")), ["Both", "Only one", "A different cap"])

    def test_a_list_of_task_numbers_stays_one_option(self):
        a = self.one("hash_list")
        self.assertEqual(labels(a), ["Deploy #214, #217 and #219 now", "Keep it for Friday after 22:05"])
        self.assertTrue(a["options"][0]["detail"].startswith("01:xx"))

    def test_a_recommended_proposal_then_or(self):
        self.assertEqual(labels(self.one("rec_then_or")), ["Build ranks 1–4 as one task*", "Ranks 1–9",
                                                           "A different selection"])

    def test_no_question_mark(self):
        a = self.one("no_qmark")
        self.assertEqual(labels(a), ["Approve this design so #214 can build the screens", "Tell me what to change"])
        self.assertEqual(a["options"][1]["detail"], "such as how a leg is moved")

    def test_two_questions_are_two_asks(self):
        a, b = asks.parse(ASKS["ab_and_second"])
        self.assertEqual(labels(a), ["Stay only as the detail of one open position*", "Go completely"])
        self.assertEqual((b["question"], labels(b)), ("And do you approve the rest of the design?", ["Yes", "No"]))
        self.assertEqual((a["line"], a["end"]), (b["line"], b["end"]))
        a, b = asks.parse(ASKS["two_alternatives"])
        self.assertEqual(labels(a), ["Should the Trades panel show only today's trades*", "Everything"])
        self.assertEqual(labels(b), ["Should the filter start off", "Turn on by itself"])

    def test_choices_in_the_next_sentence(self):
        self.assertEqual(labels(self.one("colon_choices")), ["Allow me to select your Chrome*",
                                                             "Check 7 Oct in History yourself",
                                                             "Accept the database rows as proof this time"])

    def test_a_listed_set_of_asks(self):
        a, b, c = asks.parse(ASKS["listed"])
        self.assertEqual(labels(a), ["Prepare tax returns", "Estimate or simulate taxes",
                                     "Keep track of deadlines and documents", "Something else"])
        self.assertEqual(labels(b), ["Just you", "Your family", "Outside users"])
        self.assertEqual(labels(c), ["Local app*", "Web app"])

    def test_a_wh_question_with_no_options_keeps_the_comment_box(self):
        a, b = asks.parse(ASKS["wh_date"])
        self.assertEqual((a["kind"], a["options"]), ("open", []))
        self.assertEqual(b["kind"], "yesno")
        self.assertEqual(self.one("wh_open")["kind"], "open")
        self.assertEqual(self.one("open_tag")["kind"], "open", "Ask (open): stays a comment box")

    def test_the_recommended_answer(self):
        self.assertEqual(labels(self.one("rec_yes")), ["Yes*", "No"])
        self.assertEqual(labels(self.one("rec_no")), ["Yes", "No*"])
        self.assertEqual(self.one("rec_yes")["recommended"], 0)


class TheFallback(unittest.TestCase):
    def test_two_questions_in_a_final_line(self):
        a, b = asks.safety(FALLBACK["decision_for"])
        self.assertEqual((a["question"], labels(a)), ("Decision for the owner: start task #1 now?", ["Yes", "No"]))
        self.assertEqual((b["n"], labels(b)), (1, ["Yes", "No"]))

    def test_a_list_named_options(self):
        a, = asks.safety(FALLBACK["options_lead"])
        self.assertEqual(labels(a), ["V-Class", "Vito 9 seats", "Cheapest"])

    def test_decision_needed_with_a_bulleted_list(self):
        a, = asks.safety(FALLBACK["decision_bullets"])
        self.assertEqual(labels(a), ["#19*", "#23", "#20"])
        self.assertEqual(a["recommended"], 0)

    def test_a_wh_question_names_no_options(self):
        for key in ("wh_next", "so_what"):
            with self.subTest(key=key):
                a, = asks.safety(FALLBACK[key])
                self.assertEqual((a["kind"], a["options"]), ("open", []))

    def test_alternatives_and_lists(self):
        self.assertEqual(labels(asks.safety(FALLBACK["your_decision"])[0]),
                         ["Send it as it is", "Have it checked further first"])
        self.assertEqual(labels(asks.safety(FALLBACK["status_bullets"])[0]), ["Yes", "No"],
                         "a status list is not the options")
        self.assertEqual(labels(asks.safety(FALLBACK["when_or"])[0]), ["Early", "January"])
        self.assertEqual(labels(asks.safety(FALLBACK["lettered"])[0]), ["Keep it", "Drop it"])


@unittest.skipUnless(NODE, "node is not installed")
class ThePageReadsTheSame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", NODE_JS],
                           input=json.dumps({"code": NOUN + "\n" + fold_block(SRC), "asks": ASKS, "fallback": FALLBACK}),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.o = json.loads(r.stdout.strip().splitlines()[-1])

    def test_asks(self):
        for k, text in ASKS.items():
            with self.subTest(fixture=k):
                self.assertEqual(self.o["asks"][k], asks.parse(text))

    def test_fallback(self):
        for k, text in FALLBACK.items():
            with self.subTest(fixture=k):
                self.assertEqual(self.o["fallback"][k], asks.safety(text))


if __name__ == "__main__":
    unittest.main()
