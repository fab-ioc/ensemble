"""CDP coverage for #266: a PO chat open across a PO rotation, on a busy hub.

The opTen PO chat (10-24) lost the person's sends and the PO's replies until
a reload: its thread index was rebuilt behind ~500 stands-aside for the
pages, each up to a second while any page request was being answered, so
the channel the page polled stayed minutes old; and a confirmed send left
the hub's list after SHOW_CONFIRMED_S whether or not that channel held its
turn yet, so its balloon went. This drives the person's sequence of that
afternoon on the real page, right after a rotation, with a request always
being answered: a question and its answer, a quick answer that interrupts
the turn, a second question, a third sent as that answer lands, a re-ask and
"are you getting my messages". Each sent balloon must never leave the page,
and each answer must show without a reload. Skipped without Node and Chrome.
"""
from __future__ import annotations

import json
import os
import re
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
import sends  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
OLD_SESSIONS = 40           # the PO rotated this often before: a build reads each
REPLY_WITHIN_MS = 8000      # an answer shows this soon after the PO wrote it

# The person's sends, in order: (token, words, what the PO does with it).
SEQUENCE = [
    ("ASK1", "How far is the backtest?", "answer"),
    ("ASK2", "Yes, start now", "interrupt"),          # the quick answer, its turn interrupted
    ("ASK3", "actually walk me through the plan first", "answer"),
    ("ASK4", "and show me visuals", "answer"),        # sent the moment ASK3's answer lands
    ("ASK5", "Walk me through the plan first", "answer"),
    ("ASK6", "Are you getting my messages?", "answer"),
    ("ASK7", "Sent while reading an old message", "answer"),   # from a link's window
]

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
async function hub(path, body) {
  const r = await fetch(A.base + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  return r.json();
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { samples: 0, gone: [], goneAt: {}, backAt: {}, seen: {}, answered: {}, late: [], errors: [] };
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
    await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0', 30000);
    await sleep(800);
    // The PO rotates to a fresh session while the chat is open; the hub is busy from now on.
    await hub('/__test/rotate', {});
    await sleep(1500);
    // The chat's words; an answer in the thread of a send counts by the
    // thread line under that send ("1 reply"): ans, by the send's token.
    const text = `(() => { const m = document.getElementById('msgs'), ans = [];
      for (const l of m.querySelectorAll('.thr-line')) {
        const p = l.previousElementSibling, k = p && (p.textContent.match(/ASK\\d+/) || [])[0];
        const n = l.querySelector('.thr-count');
        if (k && n && /[1-9]/.test(n.textContent)) ans.push(k);
      }
      return { t: m.textContent || '', ans }; })()`;
    // Every 250 ms: which sent tokens and which answers the chat shows.
    let sampling = true;
    const sampler = (async () => {
      while (sampling) {
        let v = { t: '', ans: [] };
        try { v = await evalIn(text); } catch (e) { out.errors.push(String(e).slice(0, 200)); }
        out.samples++;
        for (const [tok] of A.sequence) {
          const has = v.t.includes(tok + ' ');
          if (has && !out.seen[tok]) out.seen[tok] = Date.now();
          // The link's window holds none of the sends before it (they are below it).
          else if (!has && out.seen[tok] && !out.gone.includes(tok) && (!out.window || tok === 'ASK7')) { out.gone.push(tok); out.goneAt[tok] = [Date.now(), v.t.length]; }
          if (has && out.gone.includes(tok) && !out.backAt[tok]) out.backAt[tok] = Date.now();
          if (v.ans.includes(tok) && !out.answered[tok]) out.answered[tok] = Date.now();
        }
        await sleep(250);
      }
    })();
    const send = async words => {
      await evalIn(`(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(words)}; i.dispatchEvent(new Event('input')); document.getElementById('send').click(); return 0; })()`);
    };
    for (const [tok, words, what] of A.sequence) {
      if (tok === 'ASK1') { await hub('/__test/hold-channel', {}); await sleep(2500); }
      if (tok === 'ASK4') {
        // The same second the answer to the one before is written.
        const t0 = Date.now();
        while (Date.now() - t0 < 15000 && !(await hub('/__test/written', {}))['ANS-ASK3']) await sleep(50);
      }
      if (tok === 'ASK7') {
        await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
        // A link to an old message opened (a point's chip): the chat holds a
        // window around it, with later entries below it, and the person sends.
        const ch = await hub('/api/room/channel?id=' + A.room + '&n=40');
        const up = await hub('/api/room/channel?id=' + A.room + '&n=100&before=' + encodeURIComponent(ch.items[0].mid));
        const old = up.items[5].mid;
        await evalIn(`gotoMsg(${JSON.stringify(old)}); 0`);
        out.window = true;
        await until(`THR_AFTER > 0 && !!document.querySelector('#msgs .msg[data-mid="${old}"]')`, 20000);
        // Sent while one of the window's polls is still out (review 1): its
        // answer must not bring the window back.
        await hub('/__test/slow', {});
        // One the hub is holding (not one asked before /__test/slow).
        const t1 = Date.now();
        while (!((await hub('/__test/slowing', {})).n > 0 && await evalIn('THR_FETCHING && THR_AFTER > 0'))) {
          if (Date.now() - t1 > 20000) throw new Error('no window poll held');
          await sleep(50);
        }
      }
      await send(tok + ' ' + words);
      if (what === 'answer' && tok !== 'ASK3') {
        const t0 = Date.now();
        let at = 0;
        while (Date.now() - t0 < 30000 && !(at = (await hub('/__test/written', {}))['ANS-' + tok])) await sleep(100);
        if (tok === 'ASK1') {
          // The answer and then an unrelated event land before one channel
          // poll can return. A single room-wide latest id hides the answer.
          await sleep(1200); // transcript fixture uses whole-second timestamps
          await hub('/__test/report', {});
          await hub('/__test/release-channel', {});
          out.answerMaskedByLaterEvent = true;
        }
        // Time from the answer written to the answer shown.
        const shownBy = Date.now() + 0;
        while (!out.answered[tok] && Date.now() - shownBy < A.replyWithinMs) await sleep(100);
        if (!out.answered[tok]) out.late.push(tok);
        if (tok === 'ASK1') out.openAnswer = await until(`document.querySelector('#ts-frame')?.contentWindow?.document
          ?.querySelector('#msgs')?.textContent.includes('Here it is, answer 1.')`, 9000).then(() => true).catch(() => false);
        if (tok === 'ASK7') out.mobileAnswer = await until(`document.querySelector('#ts-frame')?.contentWindow?.document
          ?.querySelector('#msgs')?.textContent.includes('Here it is, answer 7.')`, 9000).then(() => true).catch(() => false);
      } else {
        await sleep(2500);
      }
    }
    if (!out.answered.ASK3) { await sleep(A.replyWithinMs); if (!out.answered.ASK3) out.late.push('ASK3'); }
    await hub('/__test/hold-channel', {});
    await sleep(2500);
    await send('BATCHA First pending reply');
    await until(`!SEND_IN_FLIGHT`, 5000);
    await send('BATCHB Second pending reply');
    await until(`!SEND_IN_FLIGHT`, 5000);
    const batchStart = Date.now();
    while (Date.now() - batchStart < 10000) {
      const written = await hub('/__test/written', {});
      if (written['ANS-BATCHA'] && written['ANS-BATCHB']) break;
      await sleep(100);
    }
    await sleep(1200);
    await hub('/__test/report', {});
    await hub('/__test/release-channel', {});
    out.batchBoth = await until(`document.querySelector('#msgs')?.textContent.includes('Here it is, answer BATCHA.') &&
      document.querySelector('#msgs')?.textContent.includes('Here it is, answer BATCHB.')`, 8000)
      .then(() => true).catch(() => false);
    out.batchExposed = await evalIn(`!document.querySelector('#thr-side')?.classList.contains('layer') ||
      document.querySelector('#thr-side').hidden`);
    out.batchThreadCount = await evalIn(`(() => { const mids = [...document.querySelectorAll('#msgs .msg[data-mid]')]
      .filter(e => /BATCH[AB] (First|Second) pending reply/.test(e.textContent)).map(e => e.dataset.mid);
      return new Set((THR_VIEW?.watchedReplies || []).filter(r => mids.includes(r.send)).map(r => r.thread)).size; })()`);
    out.batchDebug = await evalIn(`({watch:[...RECENT_SENDS.keys()].slice(-3),
      replies:(THR_VIEW?.watchedReplies||[]).slice(-4).map(r=>[r.send,r.mid,r.thread,!!r.turn]),
      inline:typeof THR_INLINE_TURNS !== 'undefined' ? [...THR_INLINE_TURNS.keys()] : [],
      shown:[...document.querySelectorAll('#msgs .msg[data-mid]')]
        .filter(e=>/BATCH|answer BATCH/.test(e.textContent)).map(e=>e.textContent.slice(0,90)),
      open:TS?.tid || ''})`);
    // Long enough for a confirmed send to have left the hub's list.
    await sleep(A.holdMs);
    sampling = false;
    await sampler;
    out.final = await evalIn(text);
    const ordinary = await hub('/__test/ordinary', {});
    const newestAfterOrdinary = await hub('/api/room/channel?id=' + A.room + '&n=40');
    out.ordinaryExcluded = newestAfterOrdinary.latestAnyMid !== ordinary.id;
    // New activity elsewhere must leave an intentionally selected view alone.
    await evalIn(`TOPIC_FILTER = {id:'nonexistent-topic', name:'A selected topic'};
      showTopicFilter(); thrRestart(); renderThreaded(true); true`);
    await until(`TOPIC_FILTER?.id === 'nonexistent-topic' && THR_DRAWN`, 8000);
    await hub('/__test/report', {});
    await sleep(4500);
    out.topicPreserved = await evalIn(`TOPIC_FILTER?.id === 'nonexistent-topic'`);
    await evalIn(`TOPIC_FILTER = null; showTopicFilter(); thrRestart(); renderThreaded(true); true`);
    await until(`!THR_FETCHING && THR_DRAWN && THR_AFTER === 0 && document.querySelectorAll('#msgs .msg[data-mid]').length > 0`, 8000);
    const newest = await hub('/api/room/channel?id=' + A.room + '&n=40');
    const older = await hub('/api/room/channel?id=' + A.room + '&n=100&before=' + encodeURIComponent(newest.items[0].mid));
    const oldMid = older.items[0].mid;
    await evalIn(`gotoMsg(${JSON.stringify(oldMid)}); true`);
    await until(`THR_AFTER > 0 && !!document.querySelector('#msgs .msg[data-mid="${oldMid}"]')`, 10000)
      .catch(async () => { out.windowDebug = await evalIn(`({after:THR_AFTER,goto:GOTO,mid:${JSON.stringify(oldMid)},fetching:THR_FETCHING,
        shown:document.querySelectorAll('#msgs .msg[data-mid]').length,topic:TOPIC_FILTER})`); });
    await hub('/__test/report', {});
    out.windowPreserved = await evalIn(`(async () => { const draw = renderThreaded;
      renderThreaded = () => Promise.resolve(); clearTimeout(THR_ANY_TIMER); THR_ANY_TIMER = 0; THR_ANY_MISSING = null;
      try { thrAnyWatch({latestAnyMid:'other-thread:unseen',latestAnyThread:'other-thread',latestAnyRole:'assistant'});
        await new Promise(r => setTimeout(r, 3700));
        return THR_AFTER > 0 && !!document.querySelector('#msgs .msg[data-mid="${oldMid}"]'); }
      finally { renderThreaded = draw; } })()`);
    // A task conversation uses the same session page in a pop-out and Dock.
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.task }, sessionId);
    await until(`document.querySelector('#msgs')?.textContent.includes('TASK SEED')`, 12000);
    const countRefresh = async event => {
      const before = await evalIn('TICK');
      await until(`TICK !== ${before}`, 3000); // just after the periodic tick
      return evalIn(`(async () => { let n = 0; const old = refresh; refresh = () => { n++; return old(); };
        try { ${event}; await new Promise(r => setTimeout(r, 350)); return n; }
        finally { refresh = old; } })()`);
    };
    out.focusRead = await countRefresh(`window.dispatchEvent(new Event('focus'))`);
    out.visibleRead = await countRefresh(`CHAT_WAS_HIDDEN = true; document.dispatchEvent(new Event('visibilitychange'))`);
    out.pageshowRead = await countRefresh(`window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))`);
    out.taskReply = await hub('/__test/task-reply', { words:'TASK REPLY 1' });
    out.taskVisible = await until(`document.querySelector('#msgs')?.textContent.includes('TASK REPLY 1')`, 5000).then(() => true).catch(() => false);
    // One request hangs until aborted. The next request must run within 10 s.
    out.hungPoll = await evalIn(`(async () => { const old = fetch; let n = 0;
      fetch = (url, opt) => { if (String(url).startsWith('/api/room?id=') && ++n === 1)
        return new Promise((_, reject) => opt.signal.addEventListener('abort', () => reject(new DOMException('aborted','AbortError'))));
        return old(url, opt); };
      try { tickNow(); await new Promise(r => setTimeout(r, 10200)); return n; }
      finally { fetch = old; } })()`);
    // A stale cursor from another hub process must request a full room view.
    out.keyChanged = await evalIn(`(async () => { ROOM_LAST.revision = 'old-hub-key';
      const old = ROOM_LAST; await fetch('/__test/task-reply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({words:'TASK REPLY 2'})});
      tickNow(); const end = Date.now()+5000; while (Date.now()<end) {
        if (document.querySelector('#msgs')?.textContent.includes('TASK REPLY 2')) return true;
        await new Promise(r=>setTimeout(r,100)); } return false; })()`);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.task + '&embed=1' }, sessionId);
    await until(`document.querySelector('#msgs')?.textContent.includes('TASK REPLY 2')`, 12000);
    await hub('/__test/task-reply', { words:'TASK DOCK REPLY' });
    out.dockedTask = await until(`document.querySelector('#msgs')?.textContent.includes('TASK DOCK REPLY')`, 5000).then(() => true).catch(() => false);
    out.taskRepairMid = await evalIn(`(() => { const n = [...document.querySelectorAll('#msgs [data-mid]')]
      .find(e => e.textContent.includes('TASK DOCK REPLY')); if (!n) return '';
      window.__drawBeforeDamage = renderBubbles;
      renderBubbles = (items, opt = {}) => { if (opt.force) return window.__drawBeforeDamage(items, opt); };
      n.remove(); return n.dataset.mid; })()`);
    out.taskRepaired = await until(`!!document.querySelector('#msgs [data-mid="${out.taskRepairMid}"]')`, 7500)
      .then(() => true).catch(() => false);
    await evalIn(`renderBubbles = window.__drawBeforeDamage; delete window.__drawBeforeDamage; true`);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until(`!!PROJECTS?.projects?.length`, 12000);
    await evalIn(`SELECTED_PROJECT = PROJECTS.projects.find(p => p.poRoomId === ${JSON.stringify(A.room)})?.id || '';
      PROJECT_TAB = 'tasks'; renderRows(); true`);
    out.dockReady = await until(`!!document.querySelector('iframe.po-session')?.contentWindow
      ?.document?.querySelector('#input')`, 16000).then(() => true).catch(() => false);
    if (out.dockReady) {
      await evalIn(`(() => { const d = document.querySelector('iframe.po-session').contentWindow.document;
        const i = d.querySelector('#input'); i.value = 'ASK1 Dock verification'; i.dispatchEvent(new Event('input'));
        d.querySelector('#send').click(); return true; })()`);
      out.dockedPoAnswer = await until(`document.querySelector('.pd-thread iframe')?.contentWindow
        ?.document?.querySelector('#msgs')?.textContent.includes('Here it is, answer 1.')`, 12000)
        .then(() => true).catch(() => false);
    }
    out.catchups = (await hub('/api/health')).chatCatchups || {};
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _turn(sid: str, role: str, text: str, at: float) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    if role == "user":
        return {"type": "user", "timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class PoChatRotationSequence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-rot-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        cls.tdir = base / "transcripts" / "C--po"
        cls.tdir.mkdir(parents=True)
        (base / "cs").mkdir()
        cls.lock = threading.Lock()
        cls.lines: dict[str, list] = {}
        cls.written: dict[str, float] = {}
        cls.busy = threading.Event()
        cls.stop = threading.Event()
        cls.slow = threading.Event()
        cls.slowing = []     # the &to= polls held now
        cls.hold_channel = threading.Event()
        cls.release_channel = threading.Event()
        cls.release_channel.set()

        def resume(h, room, text="", to="", key="", quiet=False):
            # The PO's seat: the send goes into its current conversation, and
            # it answers a moment later (or the person interrupts it).
            sid = cls.current
            # Typed into its terminal, as the hub's own delivery says.
            dashboard._typed_sends(room["id"], [{"key": key, "text": text}])

            def run():
                time.sleep(0.4)
                cls._append(sid, _turn(sid, "user", text, time.time()))
                tok = next((t for t, _w, _x in SEQUENCE if (t + " ") in text), "")
                if not tok:
                    batch = re.search(r"\b(BATCH[AB])\b", text)
                    tok = batch.group(1) if batch else ""
                what = next((x for t, _w, x in SEQUENCE if t == tok), "answer")
                time.sleep(1.2)
                if what == "interrupt":
                    cls._append(sid, _turn(sid, "user", "[Request interrupted by user]", time.time()))
                    return
                answer = f"Here it is, answer {tok[3:] if tok.startswith('ASK') else tok}."
                if tok.startswith("BATCH"):
                    led = points.sync(cls.room, force=True)
                    pid = next((p["id"] for p in led["points"] if tok in p.get("text", "")), "")
                    if pid:
                        answer = f"Re {pid}: {answer}"
                cls._append(sid, _turn(sid, "assistant", answer, time.time()))
                cls.written[f"ANS-{tok}"] = time.time()
            threading.Thread(target=run, daemon=True).start()
            return {"delivered": 1}

        orig_post = dashboard.Handler.do_POST

        def do_post(h):
            # The test's own controls, on loopback beside the page's hub.
            if h.path == "/__test/rotate":
                cls._rotate()
                return h._send_json(200, {"ok": True})
            if h.path == "/__test/report":
                cls.report_n = getattr(cls, "report_n", 0) + 1
                n = cls.report_n
                cls._append(cls.current, _turn(cls.current, "user", f"[report] Overnight report {n} from task #12.", time.time()))
                cls._append(cls.current, _turn(cls.current, "assistant", f"Overnight report {n} received.", time.time() + .01))
                return h._send_json(200, {"ok": True})
            if h.path == "/__test/hold-channel":
                cls.hold_channel.set(); cls.release_channel.clear()
                return h._send_json(200, {"ok": True})
            if h.path == "/__test/release-channel":
                cls.hold_channel.clear(); cls.release_channel.set()
                return h._send_json(200, {"ok": True})
            if h.path == "/__test/notice":
                msg = chatroom.post_notice(cls.room, "Ensemble", "Follow-up hub notice after the report.",
                                           {"noticeKind": "restart"})
                return h._send_json(200, {"id": msg["id"]})
            if h.path == "/__test/ordinary":
                posted = chatroom.post_message(cls.room, "claude", "Ordinary room record outside the channel.", to="user")
                return h._send_json(200, {"id": posted["message"]["id"]})
            if h.path == "/__test/written":
                return h._send_json(200, dict(cls.written))
            if h.path == "/__test/slow":
                cls.slow.set()
                return h._send_json(200, {"ok": True})
            if h.path == "/__test/slowing":
                return h._send_json(200, {"n": len(cls.slowing)})
            if h.path == "/__test/task-reply":
                words = json.loads(h.rfile.read(int(h.headers.get("Content-Length", "0"))) or b"{}").get("words", "")
                chatroom.post_message(cls.task, "claude", words, to="user")
                return h._send_json(200, {"ok": True})
            return orig_post(h)

        orig_get = dashboard.Handler.do_GET
        orig_send_file = dashboard.Handler._send_file

        def send_file(h, path, *args, **kwargs):
            baseline = os.environ.get("ENS_BASELINE_SESSION")
            if baseline and Path(path).name == "session.html":
                path = Path(baseline)
            return orig_send_file(h, path, *args, **kwargs)

        def do_get(h):
            if cls.hold_channel.is_set() and h.path.startswith("/api/room/channel"):
                cls.release_channel.wait(12)
            # A link window's poll answered slowly once asked (/__test/slow).
            if cls.slow.is_set() and h.path.startswith("/api/room/channel") and "&to=" in h.path:
                cls.slowing.append(h.path)
                try:
                    time.sleep(3)
                finally:
                    cls.slowing.remove(h.path)
            return orig_get(h)

        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(sends, "SHOW_CONFIRMED_S", 3),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard, "_send_left_behind", return_value=""),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.object(dashboard.Handler, "do_POST", do_post),
            mock.patch.object(dashboard.Handler, "do_GET", do_get),
            mock.patch.object(dashboard.Handler, "_send_file", send_file),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        threads.forget()
        cls.room = cls._po_room()
        cls.task = chatroom.create_room("Task", [
            {"identity": "claude", "agent": "claude", "role": "engineer"},
            {"identity": "codex", "agent": "codex", "role": "reviewer"},
        ])["id"]
        chatroom.post_message(cls.task, "claude", "TASK SEED", to="user")
        threads.build(cls.room, show_progress=False)

        def keep_busy():
            # A hub always answering something (the live one answers its
            # agents' tool calls and every open page's polls).
            while not cls.stop.is_set():
                if cls.busy.is_set():
                    dashboard._ANSWERING[-266] = time.monotonic()
                time.sleep(0.05)
            dashboard._ANSWERING.pop(-266, None)
        threading.Thread(target=keep_busy, daemon=True).start()
        cls.addClassCleanup(cls.stop.set)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        if not os.environ.get("ENS_DEBUG_SERVER"):
            cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        try:
            cls.got = cls._chrome()
        finally:
            cls.busy.clear()

    @classmethod
    def _append(cls, sid, line):
        with cls.lock:
            cls.lines.setdefault(sid, []).append(line)
            (cls.tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in cls.lines[sid]),
                                                   encoding="utf-8")

    @classmethod
    def _po_room(cls) -> str:
        ok, proj, _ = dashboard.register_project("Options")
        assert ok, proj
        t0 = time.time() - 6 * 3600
        sids = [f"old{i:02d}" for i in range(OLD_SESSIONS)] + ["cur"]
        for i, sid in enumerate(sids):
            at = t0 + i * 300
            cls._append(sid, _turn(sid, "assistant", f"I've taken over as PO ({i}).", at))
            cls._append(sid, _turn(sid, "user", f"[digest] Options: quiet hour {i}.", at + 10))
            cls._append(sid, _turn(sid, "assistant", f"Noted {i}, nothing to do.", at + 11))
        for j in range(100):
            # The person's talk with this PO: more than a page of the channel.
            cls._append("cur", _turn("cur", "user", f"Earlier question {j}?", at + 20 + j * 2))
            cls._append("cur", _turn("cur", "assistant", f"Earlier answer {j}.", at + 21 + j * 2))
        rots = [{"n": i + 1, "at": t0 + (i + 1) * 300, "fromSessionId": a, "toSessionId": b}
                for i, (a, b) in enumerate(zip(sids, sids[1:]))]
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner",
                                          "sessionId": "cur", "rotations": rots}])
        rid = po["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(rid, proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], rid)
        assert ok, why
        points.sync(rid, force=True)
        threads._ensure_since(rid, t0 - 60)
        cls.current = "cur"
        return rid

    @classmethod
    def _rotate(cls):
        new = "fresh"
        cls._append(new, _turn(new, "assistant", "I've read the handover and taken over as PO.", time.time()))
        rec = {"n": OLD_SESSIONS + 1, "at": time.time(), "fromSessionId": cls.current, "toSessionId": new}
        chatroom.patch_participant(cls.room, "claude", {"sessionId": new}, append={"rotations": rec})
        chatroom.post_notice(cls.room, "Ensemble", "**New PO session** — the hub started a fresh one.",
                             {"noticeKind": "rotation", "rotation": rec})
        cls.current = new
        cls.busy.set()

    @classmethod
    def _chrome(cls):
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "room": cls.room, "task": cls.task,
                "sequence": SEQUENCE, "replyWithinMs": REPLY_WITHIN_MS, "holdMs": 6000}
        script = Path(cls.tmp.name) / "rotation_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8",
                             timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENS_DEBUG"):
            Path(os.environ["ENS_DEBUG"]).write_text(json.dumps(got, indent=1), encoding="utf-8")
        return got

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_every_send_shows_at_once_and_never_leaves(self):
        self.assertGreater(self.got["samples"], 40)
        self.assertEqual([t for t, _w, _x in SEQUENCE if t not in self.got["seen"]], [],
                         "a send was never shown")
        self.assertEqual(self.got["gone"], [], "a sent balloon left the chat before a reload")

    def test_a_send_from_a_links_window_shows_with_its_answer(self):
        self.assertTrue(self.got.get("window"), "the link's window was not opened")
        self.assertIn("ASK7", self.got["seen"])
        self.assertNotIn("ASK7", self.got["gone"])
        self.assertNotIn("ASK7", self.got["late"])

    def test_every_answer_shows_without_a_reload(self):
        self.assertEqual(self.got["late"], [], f"answers not shown within {REPLY_WITHIN_MS} ms")
        for tok, _w, what in SEQUENCE:
            if what == "answer":
                self.assertIn(tok, self.got["final"]["ans"])

    def test_a_reply_to_a_new_send_opens_with_its_text(self):
        self.assertTrue(self.got.get("answerMaskedByLaterEvent"), self.got)
        self.assertTrue(self.got.get("openAnswer"), self.got)
        self.assertTrue(self.got.get("mobileAnswer"), self.got)
        self.assertTrue(self.got.get("batchBoth"), "both same-poll replies must be visible in the channel")
        self.assertTrue(self.got.get("batchExposed"), "mobile thread layer hid the replies")
        self.assertEqual(self.got.get("batchThreadCount"), 2, "the replies must be in different threads")

    def test_unrelated_activity_preserves_selected_topic_and_link_window(self):
        self.assertTrue(self.got.get("topicPreserved"), self.got)
        self.assertTrue(self.got.get("windowPreserved"), self.got)
        self.assertTrue(self.got.get("ordinaryExcluded"), self.got)

    def test_task_chat_recovers_after_visibility_timeout_and_stale_key(self):
        for event in ("focusRead", "visibleRead", "pageshowRead"):
            self.assertGreater(self.got.get(event, 0), 0, event)
        self.assertTrue(self.got.get("taskVisible"), self.got)
        self.assertGreaterEqual(self.got.get("hungPoll", 0), 2, self.got)
        self.assertTrue(self.got.get("keyChanged"), self.got)
        self.assertTrue(self.got.get("dockedTask"), self.got)
        self.assertTrue(self.got.get("dockReady"), self.got)
        self.assertTrue(self.got.get("dockedPoAnswer"), self.got)
        self.assertTrue(self.got.get("taskRepairMid"), self.got)
        self.assertTrue(self.got.get("taskRepaired"), self.got)
        self.assertGreater(self.got.get("catchups", {}).get("thread-reply", 0), 0, self.got)
        self.assertGreater(self.got.get("catchups", {}).get("poll-timeout", 0), 0, self.got)
        self.assertGreater(self.got.get("catchups", {}).get("cursor-refetch", 0), 0, self.got)
        self.assertGreater(self.got.get("catchups", {}).get("task-latest", 0), 0, self.got)
        self.assertGreater(self.got.get("catchups", {}).get("resume-read", 0), 0, self.got)


class HoldAndBudget(unittest.TestCase):
    """The two halves of the fix, without a browser."""

    def test_a_confirmed_send_stays_listed_while_the_channel_lacks_its_turn(self):
        rid = "room-266aaaa1"
        sent = {"key": "k1", "text": "hi", "to": "", "at": 100.0, "state": "confirmed", "stateAt": 101.0,
                "confirmedAt": 101.0, "mid": "s:4"}
        with mock.patch.object(sends, "reconcile", lambda *_a, **_k: [dict(sent)]):
            later = 101.0 + sends.SHOW_CONFIRMED_S + 5
            self.assertEqual(sends.view(rid, now=later), [], "no channel to ask: gone as before")
            self.assertEqual(sends.view(rid, now=later, held=lambda m: True), [])
            self.assertEqual(sends.view(rid, now=later, held=lambda m: None), [], "not known: gone as before")
            self.assertEqual([s["key"] for s in sends.view(rid, now=later, held=lambda m: False)], ["k1"])
            self.assertEqual(sends.view(rid, now=101.0 + sends.HOLD_UNSHOWN_S + 1, held=lambda m: False), [],
                             "held at most HOLD_UNSHOWN_S")

    def test_holds_mid_reads_the_index_without_building(self):
        with mock.patch.dict(threads._INDEX, {"room-266aaaa2": ("sig", {"by_mid": {"s:1": {}}})}, clear=False):
            self.assertIs(threads.holds_mid("room-266aaaa2", "s:1"), True)
            self.assertIs(threads.holds_mid("room-266aaaa2", "s:2"), False)
        self.assertIsNone(threads.holds_mid("room-266aaaa3", "s:1"))

    def test_a_build_stands_aside_at_most_its_budget(self):
        waits = []

        def yield_to_pages(most_s=1.0, quiet_s=0.0):
            waits.append(most_s)
            time.sleep(min(most_s, 0.05))
        room = {"id": "room-266aaaa4", "participants": []}
        many = [{"mid": f"s:{i}", "ts": float(i)} for i in range(5)]

        def balloons(_room, _led, on_session=None, on_turn=None):
            for i in range(200):
                on_session(i + 1, 200)
            return many
        with mock.patch.object(threads, "YIELD_BUDGET_S", 0.3),                 mock.patch.object(dashboard, "yield_to_pages", yield_to_pages),                 mock.patch.object(threads, "_ensure_since", lambda *_a: None),                 mock.patch.object(threads, "_signature", lambda *_a: "sig-x"),                 mock.patch.object(threads, "balloons", balloons),                 mock.patch.object(threads, "assign", lambda *a, **k: {"threads": {}, "of": {}, "parent": {}, "also": {},
                                                                      "order": [], "work": set(), "paired": {}}),                 mock.patch.object(points, "load", lambda _r: {"points": []}),                 mock.patch.object(points, "task_lookup", lambda *_a: (lambda _ref: None)),                 mock.patch.object(threads, "load_side", lambda _r: {}),                 mock.patch.dict(threads._INDEX, {}, clear=False):
            t0 = time.monotonic()
            threads.build("room-266aaaa4", room, show_progress=False)
            took = time.monotonic() - t0
        self.assertLess(len(waits), 12, "it stops standing aside once the budget is spent")
        self.assertLess(took, 1.0)


@unittest.skipUnless(NODE, "node is not installed")
class StalePageLoads(unittest.TestCase):
    """Review 2: a page above or below asked before the pages started again
    (a topic chosen) is not added to the new view, even when both views share
    the entry it was asked from."""

    @classmethod
    def setUpClass(cls):
        page = (ROOT / "session.html").read_text(encoding="utf-8")

        def fn(name, end="\n}\n"):
            at = page.index("function " + name + "(")
            return page[at:page.index(end, at) + len(end)]
        code = "".join([fn("thrRestart", "}\n"), page[page.index("const thrLast = "):page.index("\n", page.index("const thrLast = ")) + 1],
                        fn("thrLoadDown"), fn("thrKeepHub", "}\n"), fn("thrLoadUp")]).replace("\r\n", "\n")
        js = r"""
const vm = require('vm');
const code = require('fs').readFileSync(0, 'utf8');
let answer;
const box = { scrollTop: 0, scrollHeight: 0, children: [], querySelectorAll: () => [] };
const ctx = { pollFetch: () => new Promise(r => { answer = d => r({ json: () => d }); }), thrBase: () => '',
  thrDraw: () => {}, roomLatestTs: () => 0, ROOM_OBJ: null, $: () => box, readingPlace: () => null, showLatest: () => {},
  DRAW_HELD: false, STICK: true, Promise };
vm.createContext(ctx);
vm.runInContext('var THR_GEN = 0, THR_SIG = "", THR_N = 40, THR_FROM = "", THR_VIEW = null, THR_OLDER = [], THR_HUB = new Map(),'
  + ' THR_BEFORE = 0, THR_AFTER = 0, THR_UP = null, THR_DOWN = null, THR_META = {};\n' + code, ctx);
const run = s => vm.runInContext(s, ctx);
const view = mids => JSON.stringify({ items: mids.map(mid => ({ mid })) });
(async () => {
  const out = {};
  // Above: the unfiltered view's page arrives after a topic's view with the same first entry.
  run('THR_VIEW = ' + view(['a', 'b']) + '; THR_BEFORE = 1;');
  const up = ctx.thrLoadUp();
  run('thrRestart(); THR_VIEW = ' + view(['a', 'c']) + '; THR_BEFORE = 5;');
  answer({ items: [{ mid: 'old' }], before: 0 });
  out.upDrawn = await up;
  out.up = run('[THR_OLDER.map(t => t.mid), THR_BEFORE]');
  // Below: the same with the last entry.
  run('THR_VIEW = ' + view(['x', 'z']) + '; THR_AFTER = 1;');
  const down = ctx.thrLoadDown();
  run('thrRestart(); THR_VIEW = ' + view(['y', 'z']) + '; THR_AFTER = 7;');
  answer({ items: [{ mid: 'zz' }], after: 0 });
  out.downDrawn = await down;
  out.down = run('[THR_VIEW.items.map(t => t.mid), THR_AFTER]');
  // Not started again: the page is added.
  run('THR_VIEW = ' + view(['a', 'b']) + '; THR_BEFORE = 1; THR_OLDER = [];');
  const kept = ctx.thrLoadUp();
  answer({ items: [{ mid: 'older' }], before: 0 });
  out.keptDrawn = await kept;
  out.kept = run('THR_OLDER.map(t => t.mid)');
  console.log(JSON.stringify(out));
})();
"""
        res = subprocess.run([NODE, "-e", js], input=code, capture_output=True, encoding="utf-8", timeout=60)
        if res.returncode:
            raise AssertionError(res.stderr)
        cls.r = json.loads(res.stdout)

    def test_a_page_above_asked_before_a_topic_switch_is_not_added(self):
        self.assertFalse(self.r["upDrawn"])
        self.assertEqual(self.r["up"], [[], 5])

    def test_a_page_below_asked_before_a_topic_switch_is_not_added(self):
        self.assertFalse(self.r["downDrawn"])
        self.assertEqual(self.r["down"], [["y", "z"], 7])

    def test_a_page_asked_in_the_same_view_is_added(self):
        self.assertTrue(self.r["keptDrawn"])
        self.assertEqual(self.r["kept"], ["older"])


if __name__ == "__main__":
    unittest.main()
