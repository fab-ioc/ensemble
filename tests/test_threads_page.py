"""CDP coverage for PO thread channels, deep links, and panel geometry.

Each viewport has a solo PO room with a transcript file. The channel is the
whole conversation but a thread's replies, each thread's first message with
its line under it (#234); a line opens the thread beside the chat (over it on
a phone), and unread threads are listed in Threads.
Screenshots go to $ENSEMBLE_SHOTS when set. Skipped without Node and Chrome.
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
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1728, 1117, False), (625, 2000, False), (390, 844, True), (4096, 2160, False)]

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
// What the chat shows: each balloon's id, each thread line (and the balloon
// over it), each "replied to a thread" mark (and the balloon under it), the
// Threads list's cards, and the thread open beside the chat.
const VIEW = `(() => {
  const sib = (e, k) => { const s = e[k]; return s && s.dataset ? s.dataset.mid || '' : ''; };
  const words = e => e.textContent.replace(/\\s+/g, ' ').trim();
  const side = document.getElementById('thr-side'), f = document.getElementById('ts-frame');
  const sr = side.getBoundingClientRect(), cr = document.getElementById('chatcol').getBoundingClientRect();
  return {
    mids: [...document.querySelectorAll('#msgs .msg[data-mid]')].map(e => e.dataset.mid),
    lines: [...document.querySelectorAll('#msgs .thr-line')].map(e => ({ thr: e.dataset.thrLine, text: words(e),
      unread: e.classList.contains('unread'), dot: !!e.querySelector('.thr-dot'), on: e.classList.contains('on'), after: sib(e, 'previousElementSibling') })),
    cards: [...document.querySelectorAll('#msgs .thr-card')].map(e => ({ thr: e.dataset.thr, unread: e.classList.contains('unread') })),
    ins: [...document.querySelectorAll('#msgs .thr-in')].map(e => ({ text: words(e), before: sib(e, 'nextElementSibling') })),
    threads: document.getElementById('threads-view').getAttribute('aria-pressed'),
    whole: !!document.getElementById('whole-chat') || !!document.querySelector('[data-phone-action="whole-chat"]'),
    folded: document.querySelectorAll('#msgs .msg-fold, #msgs .fold-note, #msgs .gap-row').length,
    scrollX: document.documentElement.scrollWidth - innerWidth,
    endGap: (b => Math.round(b.scrollHeight - b.scrollTop - b.clientHeight))(document.getElementById('msgs')),
    side: { open: !side.hidden, layer: side.classList.contains('layer'), title: document.getElementById('ts-title').textContent,
      src: f.getAttribute('src') || '', left: sr.left, width: sr.width, height: sr.height, chatWidth: cr.width,
      stored: (() => { try { return localStorage.getItem('cd-thread-open:' + ROOM); } catch (e) { return null; } })() },
  };
})()`;
// The same, inside the thread open beside the chat (its frame, same origin).
const fe = js => `(() => { const f = document.getElementById('ts-frame'); try { return f.contentWindow.eval(${JSON.stringify(js)}); } catch (e) { return null; } })()`;
const FDRAWN = fe('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0');
const FVIEW = fe(`(() => {
  const list = document.getElementById('msgs').getBoundingClientRect();
  const seen = mid => { const e = document.querySelector('#msgs .msg[data-mid="' + mid + '"]'); if (!e) return false;
    const r = e.getBoundingClientRect(); return r.top < list.bottom && r.bottom > list.top; };
  return { thread: THREAD, tmid: THREAD_MID, side: document.body.classList.contains('side'),
    mids: [...document.querySelectorAll('#msgs .msg[data-mid]')].map(e => e.dataset.mid),
    headHidden: getComputedStyle(document.querySelector('body > header')).display === 'none',
    also: (e => !!e && !e.hidden && e.getBoundingClientRect().width > 0)(document.getElementById('thr-also-l')),
    seen: Object.fromEntries([...document.querySelectorAll('#msgs .msg[data-mid]')].map(e => [e.dataset.mid, seen(e.dataset.mid)])) };
})()`);
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of A.sizes) {
      const room = A.rooms[String(w)], o = {};
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `threads-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
      const go = async (q) => {
        await c.send('Page.navigate', { url: A.base + '/session?' + q }, sessionId);
        await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && (document.querySelectorAll("#msgs .thr-line").length > 0 || document.querySelectorAll("#msgs .msg[data-mid]").length > 0)', 30000);
        await sleep(400);
      };
      // A deliberately slow first build: the fixed notice appears, gives the
      // current phase, then leaves without shifting the chat.
      await c.send('Page.navigate', { url: A.base + '/session?room=' + room }, sessionId);
      await until('document.getElementById("ens-progress") && !document.getElementById("ens-progress").hidden', 15000);
      const progressShownAt = Date.now();
      o.progressDuring = await evalIn(`(() => {
        const e = document.getElementById('ens-progress');
        const r = e.getBoundingClientRect();
        return { text: e.textContent, width: r.width, right: r.right, scrollX: document.documentElement.scrollWidth - innerWidth,
          mode: e.classList.contains('indeterminate') ? 'indeterminate' : 'determinate' };
      })()`);
      o.progressThemes = await evalIn(`(() => {
        const root = document.documentElement, before = root.dataset.theme, e = document.getElementById('ens-progress');
        const out = {};
        for (const t of ['light','dark','dim','paper','contrast','fjord','intellij-dark']) {
          root.dataset.theme = t;
          const s = getComputedStyle(e);
          out[t] = { foreground: s.color, background: s.backgroundColor };
        }
        root.dataset.theme = before;
        return out;
      })()`);
      await shot('0-progress-during');
      await until('!document.getElementById("ens-progress").hidden && document.getElementById("ens-progress").textContent.includes("Sorting messages into threads") && !document.getElementById("ens-progress").classList.contains("indeterminate")', 15000);
      o.progressCount = await evalIn('document.getElementById("ens-progress").textContent');
      await shot('0-progress-count');
      await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .thr-line").length > 0', 30000);
      await until('document.getElementById("ens-progress").hidden', 15000);
      o.progressVisibleMs = Date.now() - progressShownAt;
      o.progressAfter = await evalIn('document.getElementById("ens-progress").hidden');
      await shot('0-progress-after');
      await sleep(400);
      o.channel = await evalIn(VIEW);
      await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
      await until('!!document.querySelector("#points-list .pt-thr")', 10000);
      o.sum = await evalIn('(e => e ? e.textContent : "")(document.querySelector("#points-line .pt-sum-t"))');
      o.needsThreads = await evalIn('[...document.querySelectorAll("#points-list .pt-thr")].map(e => e.dataset.thr)');
      await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
      await shot('1-channel');
      // Threads with new replies, newest first, then the channel again.
      await evalIn('document.getElementById("threads-view").click(); 0');
      await until('document.getElementById("threads-view").getAttribute("aria-pressed") === "true"', 15000);
      await sleep(400);
      o.unread = await evalIn(VIEW);
      await evalIn('document.getElementById("threads-view").click(); 0');
      await until('document.querySelectorAll("#msgs .thr-line").length > 0', 15000);
      const sid = 'po' + w;
      const closeSide = async () => {
        await evalIn('(document.querySelector("#thr-side.layer") ? document.getElementById("ts-back") : document.getElementById("ts-close")).click(); 0');
        await until('document.getElementById("thr-side").hidden', 5000);
      };
      // A thread's line opens it beside the chat (over it, with Back, on a phone).
      await evalIn('document.querySelector(\'#msgs .thr-line[data-thr-line="P1"] .thr-open\').click(); 0');
      await until(FDRAWN, 20000);
      await sleep(600);
      o.side = await evalIn(VIEW);
      o.sideThread = await evalIn(FVIEW);
      await shot('2-side');
      // A reload keeps it open.
      await go('room=' + room);
      await until(FDRAWN, 20000);
      await sleep(400);
      o.reloaded = await evalIn(VIEW);
      o.reloadedThread = await evalIn(FVIEW);
      // A reply in it, also sent to the chat.
      const n = o.reloadedThread.mids.length;
      await evalIn(fe('document.getElementById("input").value = "A bit wider still, please."; document.getElementById("input").dispatchEvent(new Event("input")); document.getElementById("thr-also").checked = true; document.getElementById("send").click(); 0'));
      o.warmProgress = await evalIn(`(async () => {
        let shown = 0;
        const fp = () => { try { return !document.getElementById('ts-frame').contentDocument.getElementById('ens-progress').hidden; } catch (e) { return false; } };
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 150));
          if (!document.getElementById('ens-progress').hidden || fp()) shown++;
        }
        return {shown, text: document.getElementById('ens-progress').textContent};
      })()`);
      await until(fe(`document.querySelectorAll('#msgs .msg[data-mid]').length >= ${n + 2}`), 20000);
      await until(`!!document.querySelector('#msgs .msg[data-mid="${sid}:8"]')`, 20000);
      await sleep(600);
      o.threadAfter = await evalIn(FVIEW);
      o.channelAfter = await evalIn(VIEW);
      await shot('3-replied');
      // Closed: the chat alone, and a reload keeps it closed.
      await closeSide();
      await go('room=' + room);
      await sleep(600);
      o.closed = await evalIn(VIEW);
      // A thread started on a message in the chat: the PO's note on a digest.
      await evalIn('document.getElementById("team-all").click(); 0');
      await until(`!!document.querySelector('#msgs .msg[data-mid="${sid}:5"] .thr-reply')`, 10000);
      await evalIn(`document.querySelector('#msgs .msg[data-mid="${sid}:5"] .thr-reply').click(); 0`);
      await until(FDRAWN, 20000);
      await sleep(400);
      o.started = await evalIn(VIEW);
      o.startedThread = await evalIn(FVIEW);
      await shot('4-started');
      await closeSide();
      await evalIn('document.getElementById("team-all").click(); 0');
      // A link to a reply: its thread opens beside the chat, on that reply.
      const linked = sid + ':3';
      await go('room=' + room + '&msg=' + encodeURIComponent(linked));
      await until(FDRAWN, 20000);
      await until(fe(`(e => !!e)(document.querySelector('#msgs .msg[data-mid="${linked}"]'))`), 20000);
      await sleep(1200);
      o.deepLink = await evalIn(VIEW);
      o.deepLinkThread = await evalIn(FVIEW);
      await shot('5-link');
      await closeSide();
      // A link to a message the chat shows lands in the chat.
      const inChat = sid + ':6';
      await go('room=' + room + '&msg=' + encodeURIComponent(inChat));
      await sleep(1500);
      o.chatLink = await evalIn(`(() => { const e = document.querySelector('#msgs .msg[data-mid="${inChat}"]'), list = document.getElementById('msgs').getBoundingClientRect();
        const r = e ? e.getBoundingClientRect() : null;
        return { side: !document.getElementById('thr-side').hidden, visible: !!r && r.top < list.bottom && r.bottom > list.top }; })()`);
      // A thread in a window of its own: all its messages, its box at the foot.
      await go('id=' + room + '&thread=P1');
      o.thread = await evalIn(VIEW);
      o.threadGeometry = await evalIn(`(() => {
        const pane = document.getElementById('msgpane').getBoundingClientRect();
        const list = document.getElementById('msgs').getBoundingClientRect();
        const box = document.getElementById('compose').getBoundingClientRect();
        return { gapBelowBox: Math.max(0, innerHeight - box.bottom),
          gapBetweenListAndBox: Math.max(0, box.top - pane.bottom),
          listHeight: list.height, paneHeight: pane.height };
      })()`);
      o.title = await evalIn('document.getElementById("title").textContent');
      o.also = await evalIn('(e => e && !e.hidden && e.getBoundingClientRect().width > 0)(document.getElementById("thr-also-l"))');
      // A thread drawn while it is not on screen (a Dock tab behind another) is not
      // read; shown again, the next poll reads it even when the hub has nothing new
      // to send ("the same", review 1).
      await go('id=' + room + '&thread=P2');
      // The reply above starts a rebuild in the background, and until it lands the
      // hub serves the last threads, "the same" too. So: wait for three polls in a
      // row that are the same (off screen, so waiting reads nothing), draw the
      // thread off screen, then poll on screen; again if that poll was not the same.
      o.offScreen = await evalIn(`(async () => {
        const f0 = window.fetch, pf = pageInFront, nap = ms => new Promise(r => setTimeout(r, ms));
        let same = false, res = null;
        window.fetch = async (u, ...a) => { const r = await f0(u, ...a); if (String(u).includes('/api/room/thread?')) same = !!(await r.clone().json()).same; return r; };
        const poll = async force => { same = false; while (THR_FETCHING) await nap(20); await renderThreaded(force); return same; };
        try {
          for (let tries = 1; tries <= 5 && !(res && res.same); tries++) {
            pageInFront = () => false;
            const end = Date.now() + 15000;
            for (let run = 0; run < 3 && Date.now() < end; await nap(400)) run = (await poll(false)) ? run + 1 : 0;
            THR_SEEN_TS = 0;
            await poll(true);
            const hidden = THR_SEEN_TS;
            pageInFront = pf;
            res = { tries, hidden, same: await poll(false), ts: THR_SEEN_TS };
          }
        } finally { window.fetch = f0; pageInFront = pf; }
        return res; })()`);
      await sleep(500);
      await go('room=' + room);
      o.channelLast = await evalIn(VIEW);
      out[w] = o;
      await c.send('Target.closeTarget', { targetId });
    }
    // A cold copied-balloon link must wait for thread indexing. While it
    // builds, the channel must never fall back to a wall of balloons.
    const coldMid = 'coldlink:3';
    const { targetId: coldTarget } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: coldSession } = await c.send('Target.attachToTarget', { targetId: coldTarget, flatten: true });
    await c.send('Page.enable', {}, coldSession);
    // The thread opens beside the chat (#234), never in a second window (#235).
    await c.send('Page.addScriptToEvaluateOnNewDocument', {
      source: 'window.open = url => { sessionStorage.setItem("__secondWindow", String(url)); return null; };'
    }, coldSession);
    const coldEval = async expr => { const r = await c.send('Runtime.evaluate',
      { expression: expr, returnByValue: true }, coldSession); return r.result.value; };
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.coldRoom + '&msg=' + encodeURIComponent(coldMid) }, coldSession);
    for (let i = 0; i < 100 && !(await coldEval('!!document.getElementById("ens-progress") && !document.getElementById("ens-progress").hidden')); i++) await sleep(100);
    const coldRowsDuring = await coldEval('document.querySelectorAll("#msgs .msg, #msgs .msg-fold").length');
    let opened = '';
    for (let i = 0; i < 100 && !opened; i++) { opened = await coldEval('(f => f ? f.getAttribute("src") || "" : "")(document.getElementById("ts-frame"))'); if (!opened) await sleep(150); }
    const second = await coldEval('sessionStorage.getItem("__secondWindow") || ""');
    out.coldLink = { coldRowsDuring, opened, second };
    await c.send('Target.closeTarget', { targetId: coldTarget });
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
class ThreadsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-thr-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        cls.tdir = base / "transcripts" / "C--po"
        cls.tdir.mkdir(parents=True)
        (base / "cs").mkdir()
        cls.sent = {}
        cls.lines = {}
        cls.sids = {}

        def resume(h, room, text="", to="", key="", quiet=False):
            # The PO: the reply goes into its transcript, and it answers.
            rid = room["id"]
            sid = cls.sids[rid]
            cls.sent.setdefault(rid, []).append(text)
            now = time.time()
            cls.lines[rid] += [_turn(sid, "user", text, now), _turn(sid, "assistant", "Re P1: wider still, done.", now + 1)]
            cls._write(sid, cls.lines[rid])
            return {"delivered": 1}

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
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard, "_send_left_behind", return_value=""),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        threads.forget()
        cls.rooms = {}
        for w, _h, _m in SIZES:
            cls.rooms[str(w)] = cls._po_room(f"Motors {w}", f"po{w}")
        cls.cold_link_room = cls._po_room("Link room", "coldlink")
        original_balloons = threads.balloons
        slowed_rooms = set()
        def slow_balloons(*args, **kwargs):
            rid = args[0].get("id", "")
            if rid not in slowed_rooms:
                slowed_rooms.add(rid)
                time.sleep(2.2)
            return original_balloons(*args, **kwargs)
        slow_patch = mock.patch.object(threads, "balloons", side_effect=slow_balloons)
        slow_patch.start()
        cls.addClassCleanup(slow_patch.stop)
        original_assign = threads.assign
        slowed_assigns = set()
        def slow_assign(*args, **kwargs):
            rid = threading.current_thread().name
            if rid in slowed_assigns:
                return original_assign(*args, **kwargs)
            slowed_assigns.add(rid)
            callback = args[4]
            waited = [False]
            def report(done, total):
                callback(done, total)
                if not waited[0]:
                    waited[0] = True
                    time.sleep(2.2)
            return original_assign(*args[:4], report, **kwargs)
        assign_patch = mock.patch.object(threads, "assign", side_effect=slow_assign)
        assign_patch.start()
        cls.addClassCleanup(assign_patch.stop)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        cls.got = cls._chrome()

    @classmethod
    def _write(cls, sid, lines):
        (cls.tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")

    @classmethod
    def _po_room(cls, name: str, sid: str) -> str:
        ok, proj, _ = dashboard.register_project(name)
        assert ok, proj
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid}])
        rid = po["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(rid, proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], rid)
        assert ok, why
        cls.sids[rid] = sid
        t0 = time.time() - 3600
        lines = [_turn(sid, "assistant", "I'm the PO for Motors.", t0)]
        talk = [("P1", "Make the speed chart wider", ["Done: it takes the whole width now.", "Re P1: and the axis labels no longer overlap."], 10),
                ("P2", "Why is the brake light red?", ["Re P2: the pads are worn; #4 replaces them."], 400)]
        for pid, text, answers, dt in talk:
            q, ids = points.take(chatroom.get_room(rid, public=False), text, key=pid, now=t0 + dt, sid=sid)
            assert ids == [pid], ids
            lines.append(_turn(sid, "user", q, t0 + dt + 1))
            lines += [_turn(sid, "assistant", a, t0 + dt + 5 + i) for i, a in enumerate(answers)]
            if pid == "P1":
                lines += [_turn(sid, "user", "[digest] Motors: all quiet.", t0 + dt + 100),
                          _turn(sid, "assistant", "Noted, nothing to do.", t0 + dt + 101)]
            cls._write(sid, lines)
            points.sync(rid, force=True)
        cls.lines[rid] = lines
        # Threaded since before any of it: every reply is unread.
        threads._ensure_since(rid, t0 - 60)
        return rid

    @classmethod
    def _chrome(cls):
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "rooms": cls.rooms, "coldRoom": cls.cold_link_room, "sizes": SIZES, "shots": cls.shots}
        script = base / "threads_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        return json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _each(self):
        return [(str(w), self.got[str(w)]) for w, _h, _m in SIZES]

    def test_progress_notice_during_and_after_build_at_desktop_and_phone(self):
        def luminance(rgb):
            parts = [int(x) / 255 for x in __import__('re').findall(r"\d+", rgb)[:3]]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in parts]
            return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
        for width in ("1728", "390"):
            with self.subTest(width=width):
                got = self.got[width]
                bar = got["progressDuring"]
                self.assertIn("Reading conversations", bar["text"])
                self.assertIn("Sorting messages into threads", got["progressCount"])
                self.assertIn("done", got["progressCount"])
                self.assertEqual(bar["scrollX"], 0)
                self.assertLessEqual(bar["right"], int(width))
                self.assertTrue(got["progressAfter"])
                self.assertGreater(got["progressVisibleMs"], 0)
                for theme, colors in got["progressThemes"].items():
                    with self.subTest(theme=theme):
                        a, b = luminance(colors["foreground"]), luminance(colors["background"])
                        self.assertGreaterEqual((max(a, b) + .05) / (min(a, b) + .05), 4.5)

    def test_warm_po_chat_shows_no_notice_as_transcript_grows(self):
        for width in ("1728", "390"):
            with self.subTest(width=width):
                self.assertEqual(self.got[width]["warmProgress"]["shown"], 0,
                                 self.got[width]["warmProgress"])

    def test_the_channel_is_the_whole_conversation_but_replies(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                ch = o["channel"]
                # Every message in time order, in full, but a thread's replies
                # (:2, :3, :7); the digest and the PO's note on it (:4, :5)
                # are Team activity, folded behind its row as ever.
                self.assertEqual(ch["mids"], [f"{sid}:0", f"{sid}:1", f"{sid}:6"])
                self.assertEqual(ch["folded"], 1)
                self.assertEqual(ch["cards"], [])
                self.assertFalse(ch["whole"], "Whole chat is gone")
                lines = {x["thr"]: x for x in ch["lines"]}
                self.assertEqual(sorted(lines), ["P1", "P2"])
                self.assertEqual(lines["P1"]["after"], f"{sid}:1", "the line is under its first message")
                self.assertEqual(lines["P2"]["after"], f"{sid}:6")
                self.assertIn("2 replies", lines["P1"]["text"])
                self.assertIn("Last reply", lines["P1"]["text"])
                self.assertIn("1 reply", lines["P2"]["text"])
                self.assertTrue(lines["P1"]["unread"] and lines["P1"]["dot"], "replies not seen: the dot")
                self.assertEqual(o["needsThreads"], ["P2", "P1"], "threads with new replies, newest first; Team activity is in the chat")
                self.assertIn("2 threads with new replies", o["sum"])
                self.assertEqual(ch["threads"], "false")
                self.assertFalse(ch["side"]["open"])
                self.assertLessEqual(ch["scrollX"], 1)

    def test_threads_view_lists_unread_topics(self):
        for w, o in self._each():
            with self.subTest(width=w):
                self.assertEqual(o["unread"]["mids"], [])
                self.assertEqual([x["thr"] for x in o["unread"]["cards"]], ["P2", "P1"], "Team activity is in the chat, not a thread")
                self.assertEqual(o["unread"]["threads"], "true")

    def test_a_line_opens_the_thread_beside_the_chat_or_over_it_on_a_phone(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid, side = f"po{w}", o["side"]["side"]
                self.assertTrue(side["open"])
                self.assertEqual(side["title"], "Make the speed chart wider")
                self.assertIn("&side=1&thread=P1", side["src"])
                self.assertEqual([x["thr"] for x in o["side"]["lines"] if x["on"]], ["P1"], "its line is marked")
                self.assertEqual(o["side"]["mids"], o["channel"]["mids"], "the chat stays as it was")
                if o["channel"]["endGap"] <= 50:
                    self.assertLessEqual(o["side"]["endGap"], 50, "a chat at its end stays there")
                t = o["sideThread"]
                self.assertEqual(t["mids"], [f"{sid}:1", f"{sid}:2", f"{sid}:3"])
                self.assertTrue(t["side"] and t["headHidden"] and t["also"], t)
                if w in ("1728", "4096"):
                    self.assertFalse(side["layer"])
                    self.assertEqual(side["width"], 520)
                    self.assertGreater(side["chatWidth"], 1000, "the chat is still in view")
                    self.assertAlmostEqual(side["left"], side["chatWidth"], delta=1)
                else:
                    self.assertTrue(side["layer"], "narrow or touch: the thread over the chat, with Back")
                    self.assertEqual(side["width"], int(w))
                self.assertLessEqual(o["side"]["scrollX"], 1)

    def test_a_reload_keeps_the_open_thread_and_close_forgets_it(self):
        for w, o in self._each():
            with self.subTest(width=w):
                self.assertTrue(o["reloaded"]["side"]["open"])
                self.assertEqual(o["reloadedThread"]["thread"], "P1")
                self.assertEqual(o["reloaded"]["side"]["stored"], '{"tid":"P1","mid":""}')
                self.assertFalse(o["closed"]["side"]["open"], "closed, a reload keeps it closed")
                self.assertIsNone(o["closed"]["side"]["stored"])

    def test_a_reply_lands_in_its_thread_and_also_in_the_chat_when_ticked(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                sent = self.sent[self.rooms[w]]
                self.assertEqual(len(sent), 1)
                self.assertTrue(sent[0].startswith("Re P1: A bit wider still, please."), sent[0])
                self.assertEqual(o["threadAfter"]["mids"][-2:], [f"{sid}:8", f"{sid}:9"], "the reply and the PO's answer, in the thread")
                after = o["channelAfter"]
                # Also send to chat: the reply is in the chat too, under a line
                # naming its thread; the PO's answer in the thread is not.
                self.assertEqual(after["mids"], [f"{sid}:0", f"{sid}:1", f"{sid}:6", f"{sid}:8"])
                self.assertEqual(after["ins"], [{"text": "replied to a thread: Make the speed chart wider", "before": f"{sid}:8"}])
                lines = {x["thr"]: x for x in after["lines"]}
                self.assertIn("4 replies", lines["P1"]["text"])
                self.assertFalse(lines["P1"]["unread"], "opened: read")
                self.assertTrue(lines["P2"]["unread"], "never opened: still unread")

    def test_a_thread_starts_from_any_message(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                self.assertIn(f"{sid}:5", o["started"]["mids"])
                self.assertTrue(o["started"]["side"]["open"])
                t = o["startedThread"]
                self.assertEqual((t["tmid"], t["mids"]), (f"{sid}:5", [f"{sid}:5"]))
                self.assertEqual(t["thread"], f"m:{sid}:5", "Team activity's note starts a thread of its own")

    def test_a_link_to_a_reply_opens_its_thread_beside_the_chat_on_it(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                self.assertTrue(o["deepLink"]["side"]["open"])
                self.assertEqual(o["deepLinkThread"]["thread"], "P1")
                self.assertTrue(o["deepLinkThread"]["seen"].get(f"{sid}:3"), o["deepLinkThread"])
                self.assertEqual(o["chatLink"], {"side": False, "visible": True}, "a message in the chat lands there")

    def test_cold_balloon_link_waits_for_the_thread_without_balloon_rows(self):
        self.assertEqual(self.got["coldLink"]["coldRowsDuring"], 0)
        self.assertIn("tmid=coldlink%3A3", self.got["coldLink"]["opened"])
        self.assertIn("msg=coldlink%3A3", self.got["coldLink"]["opened"])
        self.assertEqual(self.got["coldLink"]["second"], "")

    def test_a_thread_in_a_window_of_its_own(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                self.assertEqual(o["thread"]["mids"], [f"{sid}:1", f"{sid}:2", f"{sid}:3", f"{sid}:8", f"{sid}:9"])
                self.assertEqual(o["title"], "Make the speed chart wider")
                self.assertTrue(o["also"], "Also send to chat is under the box")

    def test_thread_list_fills_panel_and_box_stays_at_bottom(self):
        for width in ("625", "1728"):
            with self.subTest(width=width):
                geo = self.got[width]["threadGeometry"]
                self.assertGreater(geo["listHeight"], 100, geo)
                self.assertLessEqual(geo["gapBelowBox"], 16, geo)
                self.assertLessEqual(geo["gapBetweenListAndBox"], 16, geo)

    def test_a_thread_drawn_off_screen_is_read_once_it_shows(self):
        for w, o in self._each():
            with self.subTest(width=w):
                seen = o["offScreen"]
                self.assertEqual(seen["hidden"], 0, "not on screen: not read")
                self.assertEqual(seen["same"], True, f"the hub had nothing new ({seen['tries']} tries)")
                self.assertGreater(seen["ts"], 0, "on screen: read by that poll")
                lines = {x["thr"]: x for x in o["channelLast"]["lines"]}
                self.assertFalse(lines["P2"]["unread"])


if __name__ == "__main__":
    unittest.main()
