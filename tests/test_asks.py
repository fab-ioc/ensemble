"""A PO message with questions gives the person a quick-answer row per
question (#163, GitHub issue 7).

* the marker: ``asks.parse`` and the page's ``parseAsks`` read the same asks
  from the same messages — Claude's shape (numbered, bold, options indented),
  Codex's (plain ``Ask:`` and a list), ``(yes/no)``, Yes/No with a
  recommendation, an open ask, ``**Ask:**`` with the question on the next
  line, a sibling bullet, a quoted ask; nothing in a fenced block and nothing
  in an unmarked "?" message;
* the card: a button per option with the recommended one marked, a comment
  box, settled once answered; a balloon with an open ask is the person's (it
  never folds into Team activity);
* the endpoint: one POST sends one tracked answer quoting its ask, a point
  like any other; the same ask again is a duplicate and sends nothing; a
  refused send takes the answer back; answering one ask leaves the others
  open (the Needs you list);
* attention: a room with open asks is waiting for the person;
* the page in headless Chrome at 1280 and 390 px: a three-ask message draws
  three cards with no sideways scroll; a click sends the option with the
  comment typed, and the card settles; another device sees it answered.

Screenshots go to $ENSEMBLE_SHOTS when it is set.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asks  # noqa: E402
import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
import sends  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests.test_points import _World, http, turn  # noqa: E402
from tests.test_po_chat_clean import NODE, NOUN, SRC, _turn, fold_block  # noqa: E402

THREE = """Three things need you before I merge.

1. **Ask:** Which retention for old transcripts?
   - **30 days** — saves about 2 GB (recommended)
   - **90 days** — what we have now
2. **Ask (yes/no):** Turn on the nightly backup?
3. **Ask:** Anything else to fold into this release?

I carry on with the tests meanwhile."""

FIXTURES = {
    "claude": THREE,
    "codex": "Ask: Should the badge be amber?\n- Yes\n- No (recommended)\n\nAsk: Pick a name\n* Alpha: first\n* Beta - second\n\nDone.",
    "next_line": "**Ask:**\nWhich port should the test hub use?\n\n- 8798\n- 8799",
    "fenced": "Write it so:\n\n```\nAsk: example?\n- a\n```\n\nNo questions here?",
    "sibling": "- Ask: Ship it?\n- Another bullet\n  - nested",
    "quoted": "> Ask (yes/no): Restart tonight?",
    "rec_lead": "Ask: Which one?\n1. Recommended: Keep it — less churn\n2. [x] Drop it",
    "plain": "What do you think? Should we ship on Friday?",
    # Where Python and JS regexes differ (review 1): lone CRs and Unicode line
    # breaks, other spaces, non-ASCII digits, a long label with emoji.
    "breaks": "Ask: Q one?" + chr(13) + chr(13) + chr(10) + "- A" + chr(10) + "- B" + chr(0x2028) + "Ask: Q" + chr(0xa0) + "two?" + chr(0x2029) + "- C",
    "spaces": "Ask:" + chr(0x3000) + "Which" + chr(0x1f) + "one" + chr(0xfeff) + "?\n- **Keep" + chr(0x85) + "it** " + chr(0x2003) + "— less churn",
    "digits": "Ask: Which?\n" + chr(0x661) + ". Not an option\n1. Real",
    "emoji": "Ask: Which name?\n- " + ("Name " + chr(0x1f600)) * 16 + " — long\n- Short",
}

NODE_JS = r"""
const vm = require('vm');
const { code, fixtures } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ctx = { esc, attSplit: t => ({ paths: [], words: t }), stripRefBlocks: t => t, withPathAbbrevs: (t, f) => f(),
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { parseAsks, askCardHtml, askBodyHtml, openAsks, forCeo, pointMaps, CHAT_NAMES };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: () => null });
const out = { parsed: {} };
for (const [k, v] of Object.entries(fixtures)) out.parsed[k] = JSON.parse(JSON.stringify(T.parseAsks(v)));
const now = Date.now() / 1000;
const m = { id: 's:1', from: 'claude', kind: 'human', text: fixtures.claude, ts: now - 60 };
const plain = { id: 's:2', from: 'claude', kind: 'digest', text: fixtures.plain, ts: 11 };
const none = T.pointMaps(null);
out.body = T.askBodyHtml(m, none, t => '<p>' + esc(t) + '</p>', null);
out.plainBody = T.askBodyHtml(plain, none, t => t, null);
out.forCeo = [T.forCeo({ ...m, kind: 'digest' }), T.forCeo(plain)];
out.open = T.openAsks(m, none).map(a => a.n);
const P = T.pointMaps({ open: 0, planned: 0, delivered: 0, items: [], approvals: [],
  asks: { 's:1': { '0': { option: '30 days', comment: 'and tell me the size', at: 12 } } } });
out.openAfter = T.openAsks(m, P).map(a => a.n);
out.answered = T.askBodyHtml(m, P, t => t, null);
const A = T.pointMaps({ open: 0, planned: 0, delivered: 0, items: [], approvals: ['s:1'] });
out.approvedOpen = T.openAsks(m, A).map(a => a.n);
// Words of the person's in the chat after it, or a week gone: not waiting any more.
const S = T.pointMaps({ open: 0, planned: 0, delivered: 0, items: [], approvals: [], asksSettledAt: now - 30 });
out.settledOpen = T.openAsks(m, S).length;
out.settledBody = T.askBodyHtml(m, S, t => t, null);
out.ages = [T.openAsks({ ...m, ts: now - 6 * 86400 }, none).length, T.openAsks({ ...m, ts: now - 8 * 86400 }, none).length,
  T.openAsks({ ...m, ts: 0 }, S).length];
// A Done task waits for nobody, as on the hub.
ctx.ROOM_OBJ = { workflow: 'done' };
out.doneOpen = T.openAsks(m, none).length;
out.doneBody = T.askBodyHtml(m, none, t => t, null);
delete ctx.ROOM_OBJ;
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class TheMarker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", NODE_JS], input=json.dumps({"code": NOUN + "\n" + fold_block(SRC), "fixtures": FIXTURES}),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.o = json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_hub_and_the_page_read_the_same_asks(self):
        for k, text in FIXTURES.items():
            with self.subTest(fixture=k):
                self.assertEqual(self.o["parsed"][k], asks.parse(text))

    def test_what_each_shape_reads_as(self):
        c = asks.parse(FIXTURES["claude"])
        self.assertEqual([a["kind"] for a in c], ["decision", "yesno", "open"])
        self.assertEqual([a["question"] for a in c], ["Which retention for old transcripts?",
                                                      "Turn on the nightly backup?",
                                                      "Anything else to fold into this release?"])
        self.assertEqual(c[0]["options"], [{"label": "30 days", "detail": "saves about 2 GB", "recommended": True},
                                           {"label": "90 days", "detail": "what we have now", "recommended": False}])
        self.assertEqual([o["label"] for o in c[1]["options"]], ["Yes", "No"])
        self.assertEqual(c[2]["options"], [])
        x = asks.parse(FIXTURES["codex"])
        self.assertEqual((x[0]["kind"], x[0]["recommended"]), ("yesno", 1))
        self.assertEqual([(o["label"], o["detail"]) for o in x[1]["options"]], [("Alpha", "first"), ("Beta", "second")])
        n = asks.parse(FIXTURES["next_line"])
        self.assertEqual((n[0]["question"], [o["label"] for o in n[0]["options"]]),
                         ("Which port should the test hub use?", ["8798", "8799"]))
        self.assertEqual(asks.parse(FIXTURES["fenced"]), [])
        self.assertEqual(asks.parse(FIXTURES["plain"]), [])
        s = asks.parse(FIXTURES["sibling"])
        self.assertEqual((s[0]["kind"], s[0]["options"]), ("open", []), "a same-indent bullet is a sibling, not an option")
        self.assertEqual(asks.parse(FIXTURES["quoted"])[0]["kind"], "yesno")
        r = asks.parse(FIXTURES["rec_lead"])[0]
        self.assertEqual([(o["label"], o["recommended"]) for o in r["options"]], [("Keep it", True), ("Drop it", False)])
        b = asks.parse(FIXTURES["breaks"])
        self.assertEqual([(a["question"], [o["label"] for o in a["options"]]) for a in b],
                         [("Q one?", ["A", "B"]), ("Q two?", ["C"])])
        sp = asks.parse(FIXTURES["spaces"])[0]
        self.assertEqual((sp["question"], sp["options"][0]["label"]), ("Which one ?", "Keep it"))
        self.assertEqual(asks.parse(FIXTURES["digits"])[0]["kind"], "open", "only 0-9 lead a numbered list")
        e = asks.parse(FIXTURES["emoji"])[0]["options"][0]["label"]
        self.assertEqual(len(e), 80)
        self.assertTrue(e.endswith("…"))

    def test_the_answer_quotes_its_ask(self):
        a = asks.parse(THREE)[0]
        self.assertEqual(asks.answer_text(a, "30 days", "and the size"),
                         "Re “Which retention for old transcripts?”: 30 days\n\nand the size")
        self.assertEqual(asks.answer_text(a, "", "neither"), "Re “Which retention for old transcripts?”: neither")
        self.assertEqual(asks.check(a, "30 DAYS"), "30 days")
        self.assertIsNone(asks.check(a, "7 days"))

    def test_the_cards(self):
        b = self.o["body"]
        self.assertEqual(b.count('class="qa"'), 3)
        self.assertIn("Ask 1 of 3", b)
        self.assertEqual(len(re.findall(r'class="qa-opt[ "]', b)), 4, "two options, then Yes and No; the open ask has none")
        self.assertIn('class="qa-opt rec"', b)
        self.assertIn('<span class="lz lz-neutral">recommended</span>', b)
        self.assertEqual(b.count("<textarea"), 3)
        self.assertIn("Three things need you", b)
        self.assertIn("I carry on with the tests", b)
        self.assertNotIn("**Ask", b, "the ask's own lines are its card")
        self.assertIsNone(self.o["plainBody"], "an unmarked question gets no card")
        self.assertEqual(self.o["forCeo"], [True, False], "a balloon with asks is the person's; a plain one in a digest is not")

    def test_an_answer_settles_its_card_only(self):
        self.assertEqual(self.o["open"], [0, 1, 2])
        self.assertEqual(self.o["openAfter"], [1, 2])
        a = self.o["answered"]
        self.assertIn("You answered:", a)
        self.assertIn("<strong>30 days</strong>", a)
        self.assertIn("and tell me the size", a)
        self.assertEqual(a.count("<textarea"), 2)
        self.assertIn('aria-pressed="true"', a)
        self.assertEqual(self.o["approvedOpen"], [1, 2], "a thumbs up answers the ask that had a recommendation")

    def test_words_in_the_chat_or_a_week_end_the_wait(self):
        self.assertEqual(self.o["settledOpen"], 0)
        self.assertIn("You wrote in the chat after this", self.o["settledBody"])
        self.assertEqual(self.o["settledBody"].count("<textarea"), 3, "the cards still answer")
        self.assertEqual(self.o["ages"], [3, 0, 3], "6 days: open; 8 days: not; unknown time: until answered")
        self.assertEqual(self.o["doneOpen"], 0, "a Done task waits for nobody")
        self.assertIn("This task is done", self.o["doneBody"])


class TheEndpoint(_World):
    def _room(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "[rotation] You are the PO.", self.t0), turn("assistant", THREE, self.t0 + 2))
        return rid

    def _resume(self, sent):
        def resume(h, room, text="", to="", key="", quiet=False):
            sent.append((text, key))
            sends.mark(room["id"], [key], "delivered")
            return {"delivered": 1}
        return resume

    def test_one_answer_sent_once_and_the_others_stay_open(self):
        rid = self._room()
        sent = []
        body = {"roomId": rid, "mid": "sid-1:1", "n": 0, "option": "30 days", "comment": "and the size"}
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            status, r = http("/api/room/ask", body)
            self.assertEqual(status, 200, r)
            status, r2 = http("/api/room/ask", {**body, "option": "90 days"})
        self.assertEqual((status, r2.get("duplicate"), r2["answer"]["option"]), (200, True, "30 days"))
        self.assertEqual(len(sent), 1)
        text, key = sent[0]
        self.assertEqual(key, "ask:sid-1:1:0")
        self.assertIn("Re “Which retention for old transcripts?”: 30 days\n\nand the size", text)
        led = points.load(rid)
        self.assertEqual(led["asks"]["sid-1:1"]["0"]["option"], "30 days")
        self.assertTrue(any(p["text"].startswith("Re “Which retention") for p in led["points"]), "a point like any other")
        self.assertIn("sid-1:1", r["points"]["asks"])
        room = chatroom.get_room(rid)
        self.assertEqual([a["n"] for a in asks.open_in(room)], [1, 2])

    def test_a_wrong_answer_and_a_refused_send(self):
        rid = self._room()
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume([])):
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 0, "option": "7 days"})[0], 400)
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 2})[0], 400, "an open ask needs words")
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 5, "option": "Yes"})[0], 404)
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 1, "option": "yes"},
                                  origin="http://elsewhere.example")[0], 403)
        with mock.patch.object(dashboard.Handler, "_resume_room", side_effect=dashboard.StartRoomError("no")):
            status, r = http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 1, "option": "Yes"})
        self.assertEqual(status, 400)
        self.assertNotIn("1", (points.load(rid)["asks"].get("sid-1:1") or {}), "refused: it may be answered again")

    def test_a_kept_answer_discarded_opens_its_ask_again(self):
        rid = self._room()
        # Nothing takes it: the send fails, kept for Retry, and the ask stays answered.
        with mock.patch.object(dashboard.Handler, "_resume_room", lambda h, room, **k: {}):
            status, r = http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 1, "option": "Yes"})
        self.assertEqual((status, r.get("kept")), (400, True), r)
        self.assertIn("1", r["points"]["asks"]["sid-1:1"], "the reply carries the answer it keeps")
        status, r = http("/api/room/resume", {"roomId": rid, "key": "ask:sid-1:1:1", "discard": True})
        self.assertEqual((status, r.get("discarded")), (200, True), r)
        self.assertNotIn("sid-1:1", points.load(rid)["asks"], "discarded: it may be answered again")
        self.assertEqual([a["n"] for a in asks.open_in(chatroom.get_room(rid))], [0, 1, 2])

    def test_words_in_the_chat_end_the_wait_and_a_quick_answer_does_not(self):
        rid = self._room()
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume([])):
            http("/api/room/ask", {"roomId": rid, "mid": "sid-1:1", "n": 0, "option": "30 days"})
            self.assertEqual([a["n"] for a in asks.open_in(chatroom.get_room(rid))], [1, 2])
            self.assertEqual(http("/api/room/resume", {"roomId": rid, "text": "1. yes 2. nothing else", "key": "k9"})[0], 200)
        self.assertEqual(asks.open_in(chatroom.get_room(rid)), [])
        self.assertGreater(points.view(rid)["asksSettledAt"], self.t0 + 2)

    def test_a_command_or_a_refused_send_does_not_end_the_wait(self):
        rid = self._room()
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume([])):
            self.assertEqual(http("/api/room/resume", {"roomId": rid, "text": "/compact", "key": "k1"})[0], 200)
        self.assertEqual(len(asks.open_in(chatroom.get_room(rid))), 3, "a command is not words of theirs")
        with mock.patch.object(dashboard.Handler, "_resume_room", lambda h, room, **k: {}):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "not now", "key": "k2"})
        self.assertEqual(status, 400, r)
        self.assertEqual(len(asks.open_in(chatroom.get_room(rid))), 3, "a send that did not go settles nothing")
        self.assertFalse(points.view(rid).get("asksSettledAt"))

    def test_a_done_task_asks_nothing(self):
        rid = self._room()
        room = chatroom.get_room(rid)
        asks.forget()
        self.assertEqual(list(asks.open_by_room([room])), [rid])
        asks.forget()
        self.assertEqual(asks.open_by_room([{**room, "workflow": "done"}]), {})

    def test_a_room_with_open_asks_waits_for_the_person(self):
        rid = self._room()
        summary = chatroom.get_room(rid)
        asks.forget()
        quiet = lambda *a, **k: None   # noqa: E731
        with mock.patch.object(attention, "_room_summaries", return_value=[summary]), \
                mock.patch.object(attention, "_claude_status_by_session", return_value={}), \
                mock.patch.object(attention, "_stall_seconds", return_value=600), \
                mock.patch.object(attention, "_evidence", return_value={"alive": True}), \
                mock.patch.object(attention, "_classify_agent", quiet), \
                mock.patch.object(attention, "_duplicate_ptys", quiet), \
                mock.patch.object(attention, "_room_level", quiet), \
                mock.patch.object(dashboard, "load_session_projects", return_value={}), \
                mock.patch.object(dashboard, "load_projects", return_value=[]), \
                mock.patch.object(dashboard, "load_labels", return_value={}):
            items = attention._items()
        self.assertEqual(len(items), 1, items)
        it = items[0]
        self.assertEqual((it["state"], it["askKind"], it["openAsks"]), ("waiting_for_you", "asks", 3))
        self.assertEqual(it["reason"], "claude asked you 3 questions: “Which retention for old transcripts?” and more")


# ---- the page in headless Chrome -----------------------------------------------

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const out = {};
  try {
    for (const [w, h, mob, n, opt] of [[1280, 800, false, 0, '30 days'], [390, 844, true, 1, 'Yes']]) {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
      await send('Page.enable', {}, sessionId);
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
      await send('Page.navigate', { url: A.base + '/session?room=' + A.po }, sessionId);
      await until('document.querySelectorAll("#msgs .qa").length >= 3');
      await sleep(400);
      const look = () => evalIn(`(() => ({ cards: document.querySelectorAll('#msgs .qa').length, done: [...document.querySelectorAll('#msgs .qa')].map(c => c.classList.contains('done')),
        scrollX: document.documentElement.scrollWidth - innerWidth,
        inside: [...document.querySelectorAll('#msgs .qa, #msgs .qa-opt, #msgs .qa-cm textarea')].every(e => { const r = e.getBoundingClientRect(), b = e.closest('.msg').getBoundingClientRect(); return r.left >= b.left - 1 && r.right <= b.right + 1; }),
        heights: [...document.querySelectorAll('#msgs .qa:not(.done) .qa-opt, #msgs .qa:not(.done) .qa-send')].map(e => Math.round(e.getBoundingClientRect().height)),
        coarse: matchMedia('(pointer: coarse)').matches }))()`);
      const o = { before: await look() };
      await evalIn(`document.querySelector('#msgs .qa').scrollIntoView({ block: 'start' }); 0`);
      await sleep(200);
      await shot(`asks-${w}-open`);
      // A comment typed, a redraw, then the option: both go, once.
      await evalIn(`(() => { const ta = document.querySelectorAll('#msgs .qa')[${n}].querySelector('textarea'); ta.value = 'from the ${w} page'; ta.dispatchEvent(new Event('input', { bubbles: true })); renderBubbles(LAST_ITEMS); 0 })()`);
      o.kept = await evalIn(`document.querySelectorAll('#msgs .qa')[${n}].querySelector('textarea').value`);
      await evalIn(`[...document.querySelectorAll('#msgs .qa')[${n}].querySelectorAll('.qa-opt')].find(b => b.dataset.opt === ${JSON.stringify(opt)}).click(); 0`);
      await until(`document.querySelectorAll('#msgs .qa')[${n}].classList.contains('done') && !document.querySelectorAll('#msgs .qa')[${n}].classList.contains('busy')`);
      await sleep(300);
      o.after = await look();
      o.card = await evalIn(`(() => { const c = document.querySelectorAll('#msgs .qa')[${n}]; return { text: c.querySelector('.qa-done').textContent,
        disabled: [...c.querySelectorAll('.qa-opt')].every(b => b.disabled), pressed: [...c.querySelectorAll('.qa-opt[aria-pressed="true"]')].map(b => b.dataset.opt) }; })()`);
      await shot(`asks-${w}-answered`);
      out[w] = o;
      await send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-asks-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts" / "C--po").mkdir(parents=True)
        (base / "cs").mkdir()
        cls.sent = []

        def resume(h, room, text="", to="", key="", quiet=False):
            cls.sent.append((text, key))
            sends.mark(room["id"], [key], "delivered")
            return {"delivered": 1}

        for p in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": "po-sid"}])
        cls.po = po["id"]
        room = chatroom.get_room(cls.po, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], cls.po)
        assert ok, why
        t0 = time.time() - 600
        lines = [_turn("user", "[rotation] You are the PO of Motors. Read PO-HANDOVER.md.", t0),
                 _turn("assistant", THREE, t0 + 5)]
        (base / "transcripts" / "C--po" / "po-sid.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{server.server_address[1]}",
                "po": cls.po, "shots": shots}
        script = base / "asks_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.led = points.load(cls.po)
        cls.addClassCleanup(cls.tmp.cleanup)

    def test_three_cards_that_fit(self):
        for w in ("1280", "390"):
            with self.subTest(width=w):
                o = self.got[w]
                self.assertEqual(o["before"]["cards"], 3)
                self.assertLessEqual(o["before"]["scrollX"], 1, "nothing wider than the screen")
                self.assertTrue(o["before"]["inside"], "every card inside its balloon")
                self.assertLessEqual(o["after"]["scrollX"], 1)
                if w == "390" and o["before"]["coarse"]:
                    self.assertTrue(all(h >= 44 for h in o["before"]["heights"]), o["before"]["heights"])

    def test_a_click_sends_the_option_and_the_comment_once(self):
        self.assertEqual([k for _, k in self.sent], ["ask:po-sid:1:0", "ask:po-sid:1:1"])
        self.assertIn("Re “Which retention for old transcripts?”: 30 days\n\nfrom the 1280 page", self.sent[0][0])
        self.assertIn("Re “Turn on the nightly backup?”: Yes\n\nfrom the 390 page", self.sent[1][0])
        self.assertEqual(set(self.led["asks"]["po-sid:1"]), {"0", "1"})

    def test_the_card_settles_and_another_device_sees_it(self):
        o = self.got["1280"]
        self.assertEqual(o["kept"], "from the 1280 page", "a redraw keeps the comment")
        self.assertEqual(o["after"]["done"], [True, False, False])
        self.assertTrue(o["card"]["disabled"])
        self.assertEqual(o["card"]["pressed"], ["30 days"])
        self.assertIn("You answered: 30 days", o["card"]["text"])
        m = self.got["390"]
        self.assertEqual(m["before"]["done"], [True, False, False], "the second page sees the first answer")
        self.assertEqual(m["after"]["done"], [True, True, False])
        self.assertEqual(m["card"]["pressed"], ["Yes"])


if __name__ == "__main__":
    unittest.main()
