"""A clean PO chat (#146, the CEO's P107 and issue #3): the person reads only
what is theirs, each ask shows with its answer, a comment on an answer closes
the ask, and a link lands on the relevant passage.

The page's own functions run in Node:

* what is for the person (forCeo): their messages, the answers to them (a
  reply to them, a "Re Pn:" paragraph anywhere, a paragraph addressed to them),
  a decision, a PO-to-PO message that names them; hub traffic, the PO's notes
  on it and session notes are team activity;
* every run of team activity is one gap, keyed by its first message, ended by
  a held balloon; its row counts what it holds by kind;
* the bar says what is behind the lines, or that everything is shown;
* a followed-up ask reads "followed up by P2" and links the thread, a
  delivered one shows its answer's words with 👍 and Comment, and every link
  names the passage it lands on.

In headless Chrome over CDP, against a hub in a thread serving a solo PO room
whose transcript is a file, at 1280 and at 390 px: the default view is the
person's messages, the answers and the decision with one row per gap and no
hub row between them; a gap opens in place; Team activity shows everything;
the answer link marks only the "Re P2" paragraph, not the balloon; Comment
puts a linked ask in the box; the hub itself read the transcript, linked the
answers and closed P1 as followed up by the comment.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node (and
Chrome for the page).
"""
from __future__ import annotations

import json
import os
import shutil
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

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

SRC = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NOUN = (ROOT / "static" / "noun.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def fold_block(src: str) -> str:
    return src[src.index("// ---- Folding a long conversation -"):src.index("// ---- Folding a long conversation: end")]


def js_function(name: str) -> str:
    i = SRC.index(f"\nfunction {name}(") + 1
    return SRC[i:SRC.index("\n}\n", i) + 3]


NODE_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  attSplit: t => ({ paths: [], words: t }), stripRefBlocks: t => t, ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, forCeo, gapsOf, gapWords, gapRowHtml, chatBarNote, hasReMarks, pointMaps, pointsListHtml, pointBarHtml, heldBy, PT_WORD };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: () => null });
const out = {};
const his = (id, text, ts) => ({ id, from: 'user', kind: 'human', text, ts });
const hub = (id, kind, ts, extra) => Object.assign({ id, from: 'user', kind, text: '[' + kind + '] …', ts }, extra || {});
const po = (id, text, ts, answers) => Object.assign({ id, from: 'claude', text, ts }, answers ? { answers } : {});
const items = [
  his('u1', 'Why is the build red?', 100),
  po('a1', 'Re P1: a flaky test.', 101, { kind: 'human' }),
  hub('d1', 'digest', 102),
  po('n1', 'Nothing new: #9 is merged.', 103, { kind: 'digest' }),
  hub('r1', 'report', 104, { reportKind: 'completed', taskId: 'room-t1', taskTitle: 'Fix login', reporter: 'claude' }),
  po('n2', "#9's report came in; all good.", 105, { kind: 'report', taskId: 'room-t1' }),
  po('n3', 'Re P1: and it is live now.', 106, { kind: 'report', taskId: 'room-t1' }),
  hub('h1', 'handover', 107),
  po('n4', 'The handover is written.', 108, { kind: 'handover' }),
  { id: 'rot1', divider: { n: 1, at: 109 }, ts: 109 },
  hub('ro1', 'rotation', 110),
  po('n5', 'I am the new PO.\n\nTo sam: the restart is done, reload.', 111, { kind: 'rotation' }),
  po('n6', 'For you, sam: nothing changed.', 112, { kind: 'digest' }),
  hub('d2', 'digest', 113),
  po('dec', 'Decision needed: ship Friday?\n\nI recommend yes.', 114, { kind: 'digest' }),
  { id: 'pm1', kind: 'pomsg', from: 'claude@room-dock', to: 'claude', fromProjectName: 'Dock', toProjectName: 'Motors', poKind: 'info', text: 'Dock v0.6 is tagged.', ts: 115 },
  { id: 'pm2', kind: 'pomsg', from: 'claude@room-dock', to: 'claude', fromProjectName: 'Dock', toProjectName: 'Motors', poKind: 'question', text: 'Sam asked for it (his P202).', ts: 116 },
  { id: 'rs', kind: 'notice', noticeKind: 'restart', from: 'ensemble', text: 'Hub restarted', ts: 117 },
  his('u2', 'Which test?', 118),
  po('a2', 'test_login.', 119, { kind: 'human' }),
  po('x1', 'Reading the log…', 120, { kind: 'human' }),
  { id: 'tm', from: 'claude', to: 'user', text: 'A room message to you.', ts: 121 },
  { id: 'tt', from: 'claude', to: 'codex', text: 'A room message to a teammate.', ts: 122 },
];
out.forCeo = Object.fromEntries(items.map(m => [m.id, T.forCeo(m)]));
const gaps = T.gapsOf(items, null);
out.gaps = gaps.map(g => ({ key: g.key, idx: g.idx.map(i => items[i].id), seen: g.seen }));
out.words = gaps.map(g => T.gapWords(g, items));
out.row = T.gapRowHtml(gaps[0], items, false);
out.rowOpen = T.gapRowHtml(gaps[0], items, true);
// A held balloon (an open point's, a comment's passage) ends a run.
out.heldGaps = T.gapsOf(items, (m, i) => m.id === 'n2').map(g => g.idx.map(i => items[i].id));
out.bar = [T.chatBarNote(false, 0, 3, 14), T.chatBarNote(false, 0, 0, 0), T.chatBarNote(true, 5, 0, 0), T.chatBarNote(true, 0, 0, 0), T.chatBarNote(false, 0, 1, 1)];
out.re = [T.hasReMarks('Re P12: yes'), T.hasReMarks('**Re P3, P4:** no'), T.hasReMarks('I said Re P1: inline'), T.hasReMarks('- re p8 (planned #9): later')];
// Without a name for the person, "To sam:" is not known to be theirs; "For you:" is.
Object.assign(T.CHAT_NAMES, { operator: 'you' });
out.noName = [T.forCeo(items[11]), T.forCeo(items[12]), T.forCeo(items[16])];
Object.assign(T.CHAT_NAMES, { operator: 'sam' });

// The points: P1 followed up by P2 (a comment quoting its answer), P2 delivered.
const href = mid => '/session?room=room-po&msg=' + mid;
const pv = { open: 0, planned: 0, delivered: 1, approvals: [], items: [
  { id: 'P1', state: 'followed', text: 'Why is the build red?', createdAt: 100, mid: 'u1', followedBy: 'P2',
    answers: [{ mid: 'a1', at: 101, how: 're', said: 'Re P1: a flaky test.' }] },
  { id: 'P2', state: 'delivered', text: '**1.** > a flaky test\n\nWhich test?', createdAt: 118, mid: 'u2', replyTo: 'P1', comment: true,
    answers: [{ mid: 'a2', at: 119, how: 'implicit', said: 'test_login.' }] },
] };
const P = T.pointMaps(pv);
out.list = T.pointsListHtml(P, href, 2000);
out.barHis = T.pointBarHtml({ id: 'u1', from: 'user' }, P, href);
out.barAnswer = T.pointBarHtml({ id: 'a1', from: 'claude', text: 'Re P1: a flaky test.' }, P, href);
out.barChild = T.pointBarHtml({ id: 'u2', from: 'user' }, P, href);
out.barChildAnswer = T.pointBarHtml({ id: 'a2', from: 'claude', text: 'test_login.' }, P, href);
out.keep = [...P.keep].sort();
out.word = T.PT_WORD.followed;
// A follow-up of an ask whose answer has no "Re Pn:" paragraph (a first reply)
// leads to the words it quoted; one whose answer has the paragraph leads there.
const pv2 = { ...pv, items: [...pv.items.map(p => p.id === 'P2' ? { ...p, state: 'followed', followedBy: 'P3' } : p),
  { id: 'P3', state: 'open', text: '**1.** > test_login\n\nWhich file?', createdAt: 130, mid: 'u3', replyTo: 'P2', comment: true, quote: 'test_login', answers: [] }] };
out.listQ = T.pointsListHtml(T.pointMaps(pv2), href, 2000);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class InNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = NOUN + "\n" + fold_block(SRC) + "\n" + js_function("chatBarNote")
        r = subprocess.run([NODE, "-e", NODE_JS], input=json.dumps({"code": code}), capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.o = json.loads(r.stdout.strip().splitlines()[-1])

    def test_what_is_for_the_person(self):
        f = self.o["forCeo"]
        mine = {k for k, v in f.items() if v}
        self.assertEqual(mine, {"u1", "a1", "n3", "n5", "n6", "dec", "pm2", "u2", "a2", "x1", "tm"})
        # Hub lines, the PO's notes on them, the session note and the restart row, a PO-to-PO
        # message that does not name them, a message to a teammate: team activity.
        for k in ("d1", "n1", "r1", "n2", "h1", "n4", "rot1", "ro1", "d2", "pm1", "rs", "tt"):
            self.assertFalse(f[k], k)
        self.assertEqual(self.o["noName"], [False, True, False], "'To sam:' and 'Sam asked' need the name; 'For you:' does not")
        self.assertEqual(self.o["re"], [True, True, False, True])

    def test_each_run_of_team_activity_is_one_gap(self):
        g = self.o["gaps"]
        self.assertEqual([x["idx"] for x in g], [["d1", "n1", "r1", "n2"], ["h1", "n4", "rot1", "ro1"], ["d2"], ["pm1"], ["rs"], ["tt"]])
        self.assertEqual(g[0]["key"], "gap:d1")
        self.assertEqual(g[0]["seen"], "n2")
        self.assertIn("1 progress check", self.o["words"][0])
        self.assertIn("2 PO notes", self.o["words"][0])
        self.assertIn("1 report", self.o["words"][0])
        self.assertEqual(self.o["words"][1], "1 handover note, 1 PO note, 1 new session, 1 session note")
        self.assertEqual(self.o["words"][3:5], ["1 PO to PO", "1 restart note"])
        # A held balloon ends a run: the gap before it and the one after.
        self.assertEqual(self.o["heldGaps"][:2], [["d1", "n1", "r1"], ["h1", "n4", "rot1", "ro1"]])

    def test_the_gap_row_and_the_bar(self):
        row = self.o["row"]
        self.assertIn('class="msg-fold gap user hub"', row)
        self.assertIn('data-group="gap:d1"', row)
        self.assertIn('aria-expanded="false"', row)
        self.assertIn(">Team activity<", row)
        self.assertIn("4 messages", row)
        self.assertIn('title="Show these 4 messages in place"', row)
        self.assertIn('aria-expanded="true"', self.o["rowOpen"])
        self.assertIn("Fold the team activity again", self.o["rowOpen"])
        self.assertEqual(self.o["bar"], ["Your messages and the answers to you · 14 team messages behind 3 lines",
                                         "Your messages and the answers to you · no team activity hidden",
                                         "Everything shown · 5 messages from the hub folded to a row", "Everything shown",
                                         "Your messages and the answers to you · 1 team message behind 1 line"])

    def test_an_ask_shows_its_answer_its_thread_and_comment(self):
        lst = self.o["list"]
        self.assertNotIn('data-pt="P1"', lst, "a followed-up ask is closed: not listed")
        self.assertIn('<span class="pt-said">test_login.</span>', lst)
        self.assertIn('follow-up to <a class="pt-link" href="/session?room=room-po&amp;msg=a1&amp;part=re:P1" data-ref-msg="a1" data-ref-part="re:P1"', lst)
        self.assertIn('data-pt-act="ack"', lst)
        self.assertIn('data-pt-act="comment"', lst)
        self.assertIn('data-ref-part="re:P2" title="Go to the answer">answer ↓</a>', lst)
        self.assertIn('data-ref-part="pt:P2" title="Go to your message">your message ↑</a>', lst)
        self.assertEqual(self.o["word"], "followed up")
        self.assertEqual(self.o["keep"], ["a2"], "a followed-up ask may fold; the unacknowledged answer never")

    def test_a_follow_up_of_a_first_reply_leads_to_the_words_it_quoted(self):
        lst = self.o["listQ"]
        p3 = lst[lst.index('data-pt="P3"'):]
        self.assertIn('follow-up to <a class="pt-link" href="/session?room=room-po&amp;msg=a2&amp;part=q:test_login" data-ref-msg="a2" data-ref-part="q:test_login"', p3,
                      "P2's answer has no 'Re P2' paragraph: the link lands on the quoted words")
        self.assertIn('data-ref-part="re:P1"', self.o["list"], "P1's answer has one: the link lands on it")

    def test_the_bars_read_as_one_thread(self):
        self.assertIn("P1 · followed up ↓", self.o["barHis"])
        self.assertIn('followed up by <a class="pt-link" href="/session?room=room-po&amp;msg=u2&amp;part=pt:P2"', self.o["barHis"])
        self.assertIn('data-pt-act="reopen"', self.o["barHis"])
        self.assertNotIn('data-pt-act="ack"', self.o["barAnswer"], "no thumbs up owed on a followed-up ask")
        self.assertIn("answers P1 ↑", self.o["barAnswer"])
        self.assertIn("followed up by", self.o["barAnswer"])
        self.assertIn("follow-up to", self.o["barChild"])
        self.assertIn('data-pt-act="ack"', self.o["barChildAnswer"])
        self.assertIn('data-pt-act="comment"', self.o["barChildAnswer"])


# ---- the page in headless Chrome -----------------------------------------------

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.po }, sessionId);
    await until('typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0 && typeof POINTS !== "undefined" && POINTS.items.length >= 2 && !document.getElementById("chatbar").hidden', 30000);
    await sleep(500);
    return { evalIn, until, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  // What #msgs shows: each child as kind:id.
  const SHOWN = `[...document.querderySelectorAll('#msgs > *')]`.replace('querdery', 'query') + `.map(e => (e.classList.contains('gap') ? 'gap' : e.classList.contains('msg-fold') ? 'row' : e.classList.contains('msg') ? 'msg' : e.className.split(' ')[0]) + ':' + (e.dataset.mid || e.dataset.group || ''))`;
  const view = async p => p.evalIn(`(() => ({ shown: ${SHOWN}, gaps: document.querySelectorAll('#msgs .msg-fold.gap').length,
    hubRows: document.querySelectorAll('#msgs .msg-fold.user.hub:not(.gap)').length, note: document.getElementById('chatbar-note').textContent,
    pressed: document.getElementById('team-all').getAttribute('aria-pressed'), scrollX: document.documentElement.scrollWidth - innerWidth,
    coarse: matchMedia('(pointer: coarse)').matches }))()`);
  try {
    for (const [w, h, mob] of [[1280, 800, false], [390, 844, true]]) {
      const p = await page(w, h, mob);
      const o = {};
      o.clean = await view(p);
      o.bars = await p.evalIn(`[...document.querySelectorAll('#msgs .msg .pt-bar')].map(b => [b.closest('.msg').dataset.mid, b.textContent])`);
      await p.shot(`po-chat-${w}-clean`);
      // The asks line: P2 with its answer's words, its thread, 👍 and Comment.
      await p.evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
      await sleep(200);
      o.asks = await p.evalIn(`(() => { const rows = [...document.querySelectorAll('#points-list .pt-row')]; return { ids: rows.map(r => r.dataset.pt), said: rows.map(r => (r.querySelector('.pt-said') || {}).textContent || ''),
        thread: rows.map(r => (r.querySelector('.pt-thread') || {}).textContent || ''), btns: rows.map(r => [...r.querySelectorAll('.pt-btn')].map(b => b.dataset.ptAct)),
        heights: [...document.querySelectorAll('#points-list .pt-btn, #points-list a.pt-link')].map(e => Math.round(e.getBoundingClientRect().height)) }; })()`);
      await p.shot(`po-chat-${w}-asks`);
      // The answer link lands on the "Re P2" paragraph alone.
      o.land = await p.evalIn(`(async () => {
        const a = document.querySelector('#points-list .pt-row[data-pt="P2"] a.pt-link[data-ref-part="re:P2"]'); a.click();
        for (let i = 0; i < 40 && GOTO; i++) await new Promise(r => setTimeout(r, 100));
        await new Promise(r => setTimeout(r, 150));
        const el = document.querySelector('#msgs .landed');
        return { tag: el ? el.tagName : '', text: el ? el.textContent.slice(0, 40) : '', inMsg: !!(el && el.closest('.msg') && el !== el.closest('.msg')),
          msgMarked: !!document.querySelector('#msgs .msg.landed'), mid: el ? el.closest('.msg').dataset.mid : '' };
      })()`);
      // "your message ↑" on P2 lands on its numbered item, not the whole balloon.
      o.landItem = await p.evalIn(`(async () => {
        const a = document.querySelector('#points-list .pt-row[data-pt="P2"] a.pt-link[data-ref-part="pt:P2"]'); a.click();
        for (let i = 0; i < 40 && GOTO; i++) await new Promise(r => setTimeout(r, 100));
        await new Promise(r => setTimeout(r, 150));
        const el = document.querySelector('#msgs .landed');
        return { tag: el ? el.tagName : '', cls: el ? el.className : '', msgMarked: !!document.querySelector('#msgs .msg.landed') };
      })()`);
      // Comment: a new ask in the box, linked to the answer.
      await p.evalIn('document.querySelector(\'#points-list .pt-row[data-pt="P2"] .pt-btn[data-pt-act="comment"]\').click(); 0');
      await sleep(300);
      o.comment = await p.evalIn(`(() => { const tas = [...document.querySelectorAll('#ed-points textarea')]; return { n: tas.length, text: tas.map(t => t.value).join('|'), focused: tas.includes(document.activeElement), chips: document.querySelectorAll('#ed-points .ed-refs a.ref-chip, #ed-points .ed-refs').length }; })()`);
      await p.shot(`po-chat-${w}-comment`);
      await p.evalIn('edClear(); 0');
      // A gap opens in place: its hub rows and the PO's notes show under its row.
      await p.evalIn('document.querySelector("#msgs .msg-fold.gap").click(); 0');
      await sleep(300);
      o.opened = await view(p);
      o.openedRow = await p.evalIn('document.querySelector("#msgs .msg-fold.gap").getAttribute("aria-expanded")');
      await p.shot(`po-chat-${w}-gap-open`);
      await p.evalIn('document.querySelector("#msgs .msg-fold.gap").click(); 0');
      await sleep(300);
      o.closed = await view(p);
      // Team activity: everything, as before.
      await p.evalIn('document.getElementById("team-all").click(); 0');
      await sleep(300);
      o.team = await view(p);
      await p.shot(`po-chat-${w}-team`);
      o.teamKept = await p.evalIn('localStorage.getItem("cd-chat-team")');
      await p.evalIn('document.getElementById("team-all").click(); 0');
      await sleep(300);
      o.back = await view(p);
      out[w] = o;
      await p.close();
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _turn(role: str, text: str, at: float, stop: str = "end_turn") -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    if role == "user":
        return {"type": "user", "timestamp": ts, "sessionId": "po-sid", "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts, "sessionId": "po-sid",
            "message": {"role": "assistant", "stop_reason": stop, "content": [{"type": "text", "text": text}]}}


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-clean-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts" / "C--po").mkdir(parents=True)
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", cls.root),
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
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": "po-sid"}])
        cls.po = po["id"]
        room = chatroom.get_room(cls.po, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        # The PO's transcript: the CEO's ask, its answer, hub traffic and the
        # PO's notes on it, a decision, then a comment quoting the answer.
        t0 = time.time() - 3600
        room = chatroom.get_room(cls.po, public=False)
        q1, ids = points.take(room, "Why is the build red?", key="k1", now=t0 + 10, sid="po-sid")
        assert ids == ["P1"], ids
        lines = [
            _turn("user", "[rotation] You are the PO of Motors. Read PO-HANDOVER.md.", t0),
            _turn("assistant", "I'm the new PO for Motors. Nothing needs doing right now.", t0 + 5),
            _turn("user", q1, t0 + 11),
            _turn("assistant", "Looking at the build.", t0 + 12, stop="tool_use"),
            _turn("assistant", "Re P1: a flaky test, test_login; fixed in #9 and merged.\n\nMore on it: the CI cache was stale, so the first run after a merge failed.", t0 + 20),
            _turn("user", "[digest] Progress digest\n\n#9 Fix login merged to main.", t0 + 30),
            _turn("assistant", "Nothing new: #9 is merged, nothing else moved.", t0 + 35),
            _turn("user", "[report] completed from task 'Fix login' (room-task1, claude): ## Done\n\nMerged.", t0 + 40),
            _turn("assistant", "#9's report came in; all good, its card is on Done.", t0 + 45),
            _turn("user", "[digest] Progress digest\n\nNo change.", t0 + 50),
            _turn("assistant", "Decision needed: ship on Friday?\n\n- yes: the tests are green\n- no: wait for #10\n\nI recommend yes.", t0 + 55),
        ]
        room = chatroom.get_room(cls.po, public=False)
        (base / "transcripts" / "C--po" / "po-sid.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        points.sync(cls.po, force=True)
        q2, ids = points.take(room, "## Review comments (1)\n\n**1.** > a flaky test, test_login\n\nWhich test file?", key="k2", now=t0 + 60, sid="po-sid")
        assert ids == ["P2"], ids
        lines += [_turn("user", q2, t0 + 61), _turn("assistant", "Re P2: tests/test_login.py, the retry on a cold cache.", t0 + 70)]
        (base / "transcripts" / "C--po" / "po-sid.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        led = points.sync(cls.po, force=True)
        cls.states = {p["id"]: p["state"] for p in led["points"]}
        cls.led = led
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.port}", "po": cls.po, "shots": shots}
        script = base / "po_chat_clean_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_hub_read_the_transcript_and_the_comment_closed_p1(self):
        self.assertEqual(self.states, {"P1": "followed", "P2": "delivered"})
        p1 = next(p for p in self.led["points"] if p["id"] == "P1")
        self.assertEqual(p1["followedBy"], "P2")
        self.assertEqual(p1["answers"][0]["mid"], "po-sid:4", "the reply that ends the turn, not the interim line")
        self.assertTrue(p1["answers"][0]["said"].startswith("Re P1: a flaky test, test_login; fixed in #9 and merged. More on it:"))

    def _both(self):
        return [(w, self.got[str(w)]) for w in (1280, 390)]

    def test_the_default_view_is_the_persons_with_one_row_per_gap(self):
        for w, o in self._both():
            with self.subTest(width=w):
                v = o["clean"]
                kinds = [s.split(":")[0] for s in v["shown"]]
                self.assertEqual(v["pressed"], "false")
                self.assertEqual(v["hubRows"], 0, "no hub row between the person's balloons")
                self.assertEqual(v["gaps"], 2, v["shown"])
                # A gap (the session note and the PO's first words), the ask, the PO's
                # interim line and its answer, a gap (digest, note, report, note, digest),
                # the decision, the comment, its answer.
                msgs = [s for s in v["shown"] if s.startswith("msg:")]
                self.assertEqual([m.split(":", 1)[1] for m in msgs], ["po-sid:2", "po-sid:3", "po-sid:4", "po-sid:10", "po-sid:11", "po-sid:12"], v["shown"])
                self.assertEqual(kinds.count("gap"), 2)
                self.assertIn("behind 2 lines", v["note"])
                self.assertIn("7 team messages", v["note"])
                self.assertLessEqual(v["scrollX"], 1, "nothing wider than the screen")
                bars = dict(o["bars"])
                self.assertIn("followed up by P2", bars["po-sid:2"])
                self.assertIn("answers P1", bars["po-sid:4"])
                self.assertNotIn("Ack", bars["po-sid:4"], "no thumbs up owed on a followed-up ask")
                self.assertIn("follow-up to P1", bars["po-sid:11"])
                self.assertIn("Ack", bars["po-sid:12"])
                self.assertIn("Comment", bars["po-sid:12"])

    def test_the_asks_line_shows_the_answer_with_thumbs_up_and_comment(self):
        for w, o in self._both():
            with self.subTest(width=w):
                a = o["asks"]
                self.assertEqual(a["ids"], ["P2"])
                self.assertEqual(a["said"][0], "Re P2: tests/test_login.py, the retry on a cold cache.")
                self.assertEqual(a["thread"][0], "follow-up to P1 ↑")
                self.assertEqual(a["btns"][0], ["ack", "comment"])
                if w == 390 and o["clean"]["coarse"]:
                    self.assertTrue(all(h >= 44 for h in a["heights"]), a["heights"])

    def test_a_link_lands_on_the_passage_not_the_balloon(self):
        for w, o in self._both():
            with self.subTest(width=w):
                # The page's renderer draws a paragraph as a div.ln; it, not the balloon, takes the mark.
                self.assertIn(o["land"]["tag"], ("P", "DIV"))
                self.assertTrue(o["land"]["text"].startswith("Re P2:"), o["land"])
                self.assertTrue(o["land"]["inMsg"])
                self.assertFalse(o["land"]["msgMarked"])
                self.assertEqual(o["land"]["mid"], "po-sid:12")
                self.assertEqual(o["landItem"]["tag"], "LI")
                self.assertIn("pt-item", o["landItem"]["cls"])
                self.assertFalse(o["landItem"]["msgMarked"])

    def test_comment_puts_a_linked_ask_in_the_box(self):
        for w, o in self._both():
            with self.subTest(width=w):
                c = o["comment"]
                self.assertEqual(c["n"], 1)
                self.assertIn(f"/session?room={self.po}&msg=po-sid:12", c["text"])
                self.assertTrue(c["focused"])

    def test_a_gap_opens_in_place_and_team_activity_shows_everything(self):
        for w, o in self._both():
            with self.subTest(width=w):
                self.assertEqual(o["openedRow"], "true")
                self.assertGreater(o["opened"]["hubRows"], 0, "the hub's rows show under the gap's row")
                self.assertEqual(o["opened"]["gaps"], 2, "the row stays, to fold it again")
                self.assertIn("behind 1 line", o["opened"]["note"])
                self.assertEqual(o["closed"]["hubRows"], 0)
                self.assertEqual(o["team"]["pressed"], "true")
                self.assertEqual(o["team"]["gaps"], 0)
                self.assertGreater(o["team"]["hubRows"], 0)
                self.assertTrue(o["team"]["note"].startswith("Everything shown"))
                self.assertEqual(o["teamKept"], "1")
                self.assertEqual(o["back"]["gaps"], 2)


if __name__ == "__main__":
    unittest.main()
