"""#408 in headless Chrome: Reply on a question card aims a send box at it.

On a throwaway hub (the real handler on a spare port, state in a temp
folder, /api/room/resume's delivery mocked) with one project: its PO chat
holds the three cards of tests.test_asks.THREE, a task chat one card.

* The workspace at 1440: ↩ Reply on a card in the docked PO chat aims the
  workspace's Send box at it: its To line says "· reply to “…”" (cut at 28
  letters on a word), the card is marked and its Reply reads "Replying in
  the box ↓", the caret is in the box; Send answers that card
  (``Re “<question>”: <words>``) and the To line drops the reply. × drops the
  link and keeps the words (sent as a plain message). The same from a docked
  task chat aims the box at that chat.
* The Inbox's Reply on a question's row (and its copy in the hover panel)
  opens the message with the box aimed at the card, in the workspace and on
  the chat's own page (part ``inbox+reply,ask:N``: the caret in the box).
* A phone at 390x844: Reply keeps the card in view above the box, and the
  To line's × is a 44px target.

Not covered here: a card in a thread (the fixture has no thread with a card).
Screenshots go to $ENSEMBLE_SHOTS when it is set."""
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
import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
import sends  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_asks import THREE  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
TASK_ASK = "Speeds are in.\n\n1. **Ask:** Which motor profile should ship by default?\n   - Smooth\n   - Sport\n"

CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const errs = [], chromes = [];
  // A computer's Chrome has desktop hover and a fine pointer (the Inbox's hover panel); a phone's, none.
  async function browser(desk) {
    const ch = spawn(A.chrome, ['--headless=new', ...(desk ? ['--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4'] : []),
      '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A), '--no-first-run',
      '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
    chromes.push(ch);
    const wsUrl = await new Promise((res, rej) => {
      let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
      ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
    });
    const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') errs.push(String(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).slice(0, 300));
      if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
    return (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  }
  const desk = await browser(true), handheld = await browser(false);
  const out = {};
  const seed = { 'cd-list-dock': null, 'cd-ld-sel': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-inbox-scope': 'all' };
  async function view(key, w, h, phone, url, ready, steps) {
    const send = phone ? handheld : desk;
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, s); await send('Runtime.enable', {}, s);
    await send('Emulation.setFocusEmulationEnabled', { enabled: true }, s);
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: phone }, s);
    if (phone) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
    await send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(seed) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, s);
    const ev = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, s);
      if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, label, ms = 25000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + (label || expr)); };
    const press = async (x, y) => {
      if (phone) {
        await send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }, s);
        await send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, s);
      } else {
        await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, buttons: 0 }, s);
        await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 }, s);
        await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 }, s);
      }
      await sleep(450);
    };
    const hover = async (x, y) => { await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, buttons: 0 }, s); await sleep(250); };
    // `el` (an expression for an element, in any frame of the page): its centre on the top page, and whether a press there reaches it.
    const at = el => ev(`(() => { const e = ${el}; if (!e) return null; e.scrollIntoView({ block: 'nearest' });
      let x = 0, y = 0, w = e.ownerDocument.defaultView; const r = e.getBoundingClientRect();
      x = r.left + r.width / 2; y = r.top + r.height / 2;
      const chain = []; for (let f = w.frameElement; f; f = f.ownerDocument.defaultView.frameElement) chain.push(f);
      for (const f of chain) { const b = f.getBoundingClientRect(); x += b.left + f.clientLeft; y += b.top + f.clientTop; }
      let hit = document.elementFromPoint(x, y), lx = x, ly = y;
      for (const f of chain.reverse()) { if (hit !== f) break; const b = f.getBoundingClientRect(); lx -= b.left + f.clientLeft; ly -= b.top + f.clientTop; hit = f.contentDocument.elementFromPoint(lx, ly); }
      return { x, y, hits: !!hit && (hit === e || e.contains(hit)) }; })()`);
    const pressOn = async (el, label) => { const p = await at(el); if (!p) throw new Error('missing: ' + label); await press(p.x, p.y); return p; };
    const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const P = { ev, until, press, pressOn, at, hover, shot, phone, key, type: text => send('Input.insertText', { text }, s) };
    errs.length = 0;
    await send('Page.navigate', { url: A.base + url }, s);
    await until(ready, key + ' ready', 40000);
    await sleep(1000);
    out[key] = {};
    for (const [name, fn] of steps) {
      try { out[key][name] = await fn(P); } catch (e) { out[key][name] = { error: String(e && e.message || e).slice(0, 600) }; }
    }
    out[key].errors = errs.slice();
    await send('Target.closeTarget', { targetId });
  }
  const sentList = `fetch('/test/sent').then(x => x.json())`;
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const TASK = `document.querySelector('iframe.dp-session[data-room=${JSON.stringify(A.task)}]')`;
  const WCF = `document.getElementById('workspace-compose-frame')`;
  const cardIn = (doc, re) => `[...(${doc}).querySelectorAll('#msgs .qa')].find(q => ${re}.test(q.textContent))`;
  const aimedIn = doc => `[...(${doc}).querySelectorAll('#msgs .qa.aimed')].map(c => c.querySelector('.qa-qt').textContent.trim())`;
  const wcLine = `(() => { const s = document.getElementById('workspace-compose-reply'); return s.hidden ? null : s.querySelector('.wc-re').textContent; })()`;
  const wcState = doc => `({ line: ${wcLine}, aimed: ${aimedIn(doc)}, on: [...(${doc}).querySelectorAll('#msgs .qa-reply.on')].length,
    target: WC.target && { room: WC.target.room, kind: WC.target.kind }, reply: WC.reply && { room: WC.reply.room, n: WC.reply.n, question: WC.reply.question },
    caret: document.activeElement === ${WCF} && ${WCF}.contentDocument.activeElement === ${WCF}.contentDocument.getElementById('input'),
    input: ${WCF}.contentDocument.getElementById('input').value })`;
  // Reply on a card in a docked chat, words typed, Send in the workspace box.
  async function wsReply(P, frame, re, words, room, shotName) {
    const r = {};
    const doc = `${frame}.contentDocument`;
    const p = await P.pressOn(`${cardIn(doc, re)}.querySelector('.qa-reply')`, 'Reply');
    r.hit = p.hits;
    await P.until(`!document.getElementById('workspace-compose-reply').hidden`, 'aimed', 5000).catch(() => null);
    await sleep(300);
    r.aimed = await P.ev(wcState(doc));
    await P.type(words);
    await sleep(200);
    await P.shot(shotName);
    const n0 = (await P.ev(sentList)).length;
    const s = await P.pressOn(`${WCF}.contentDocument.getElementById('send')`, 'Send');
    r.sendHit = s.hits;
    r.sent = await P.until(`${sentList}.then(l => l.slice(${n0}).find(x => x.text.includes(${JSON.stringify(words)})) || null)`, 'sent', 8000).catch(() => null);
    r.done = await P.until(`(() => { const c = ${cardIn(doc, re)}; return c && c.classList.contains('done') && !c.classList.contains('busy') ? c.querySelector('.qa-done').textContent : ''; })()`, 'card done', 10000).catch(() => '');
    await sleep(400);
    r.after = await P.ev(wcState(doc));
    return r;
  }
  const WS_READY = `typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r => r.roomId === ${JSON.stringify(A.po)})`;
  const wsOpen = async P => {
    await P.ev(`SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); 0`);
    await P.until(`${PO}?.contentDocument?.querySelectorAll('#msgs .qa').length >= 3`, 'PO chat');
    await P.until(`!document.getElementById('workspace-compose').hidden && !!${WCF}.contentWindow?.eval('typeof ED === "undefined" ? null : ED')`, 'workspace box');
    await sleep(800);
    return P.ev(`({ guest: ${PO}.contentDocument.body.classList.contains('workspace-guest'), line: ${wcLine}, hover: matchMedia('(hover: hover)').matches })`);
  };
  // The task opens in a tab of its own (#386), its chat docked there.
  const taskOpen = async P => {
    await P.ev(`pdChatOpen(${JSON.stringify(A.task)}); 0`);
    await P.until(`${TASK}?.contentDocument?.querySelectorAll('#msgs .qa').length >= 1`, 'task chat').catch(async e => { throw new Error(e.message + ' :: ' + await P.ev(`(() => { const f = ${TASK}; return f ? f.src + ' | ' + f.contentDocument.body.innerText.slice(0, 600) : 'no frame: ' + [...document.querySelectorAll('iframe')].map(x => x.className + ' ' + x.dataset.room).join(',') })()`)); });
    await P.until(`!document.getElementById('workspace-compose').hidden && !!${WCF}.contentWindow?.eval('typeof ED === "undefined" ? null : ED')`, 'workspace box');
    await sleep(800);
    return P.ev(`${TASK}.contentDocument.body.classList.contains('workspace-guest')`);
  };
  try {
    await view('ws1440', 1440, 900, false, '/', WS_READY, [
      ['open', wsOpen],
      ['po', P => wsReply(P, PO, '/What else should go/', 'Also the docs', A.po, 'ws1440-po-aimed')],
      ['x', async P => {
        const r = {}, doc = `${PO}.contentDocument`;
        await P.pressOn(`${cardIn(doc, '/Which retention/')}.querySelector('.qa-reply')`, 'Reply');
        await P.until(`!document.getElementById('workspace-compose-reply').hidden`, 'aimed', 5000);
        await P.type('Keep a year');
        await sleep(200);
        const x = await P.at(`document.getElementById('workspace-compose-reply-x')`);
        r.xSize = await P.ev(`(() => { const b = document.getElementById('workspace-compose-reply-x').getBoundingClientRect(); return [b.width, b.height]; })()`);
        r.xHit = x.hits;
        await P.press(x.x, x.y);
        await sleep(300);
        r.after = await P.ev(wcState(doc));
        const n0 = (await P.ev(sentList)).length;
        await P.pressOn(`${WCF}.contentDocument.getElementById('send')`, 'Send');
        r.sent = await P.until(`${sentList}.then(l => l.slice(${n0}).find(x => x.text.includes('Keep a year')) || null)`, 'sent', 8000).catch(() => null);
        await sleep(300);
        r.open = await P.ev(`!(${cardIn(doc, '/Which retention/')}).classList.contains('done')`);
        return r;
      }],
      ['taskOpen', taskOpen],
      ['task', P => wsReply(P, TASK, '/motor profile/', 'Smooth, but tuned', A.task, 'ws1440-task-aimed')],
      // The Inbox's Reply on the nightly backup's row: the box aims the PO chat's card.
      ['inbox', async P => {
        const r = {};
        await P.until(`(() => { const a = document.querySelector('#ld-projs [data-ldp="all"]'); if (a && a.getAttribute('aria-current') !== 'true') a.click();
          return !!document.querySelector('#ld-ibx-body .ibx-row [data-ibx-reply]'); })()`, 'inbox', 20000);
        await sleep(300);
        r.rows = await P.ev(`[...document.querySelectorAll('#ld-ibx-body .ibx-row')].map(li => ({ q: (li.querySelector('.ibx-line') || li).textContent.trim(), reply: !!li.querySelector('[data-ibx-reply]') }))`);
        const row = `[...document.querySelectorAll('#ld-ibx-body .ibx-row')].find(li => /nightly backup/.test(li.textContent))`;
        // Its own button, as a keyboard reaches it (the mouse gets the hover panel's copy, below).
        await P.ev(`(() => { ${row}.querySelector('[data-ibx-reply]').click(); return 1; })()`);
        r.line = await P.until(`${wcLine}`, 'aimed from the inbox', 10000).catch(() => null);
        await sleep(500);
        r.state = await P.ev(wcState(`${PO}.contentDocument`));
        await P.shot('ws1440-inbox-reply');
        // × drops it, then the hover panel's Reply on the retention row.
        await P.ev(`(() => { document.getElementById('workspace-compose-reply-x').click(); return 1; })()`);
        await sleep(300);
        const rrow = `[...document.querySelectorAll('#ld-ibx-body .ibx-row')].find(li => /Which retention/.test(li.textContent))`;
        const p = await P.at(`(${rrow}).querySelector('.ibx-link')`);
        await P.hover(p.x, p.y);
        r.side = await P.until(`(() => { const b = document.querySelector('.ibx-side .ibx-side-reply'); return b ? b.textContent.trim() : ''; })()`, 'side panel', 4000).catch(() => '');
        if (r.side) {
          await P.shot('ws1440-inbox-side');
          const sb = await P.at(`document.querySelector('.ibx-side .ibx-side-reply')`);
          r.sideHit = sb.hits;
          await P.press(sb.x, sb.y);
          r.sideLine = await P.until(`${wcLine}`, 'aimed from the side panel', 10000).catch(() => null);
          await sleep(300);
          r.sideState = await P.ev(wcState(`${PO}.contentDocument`));
        }
        return r;
      }],
    ]);
    // The chat's own page, opened from the Inbox's Reply (its link in a new tab).
    await view('pg1440', 1440, 900, false, `/session?room=${encodeURIComponent(A.po)}&msg=${encodeURIComponent(A.backup.mid)}&part=${encodeURIComponent('inbox+reply,ask:' + A.backup.n)}`,
      `document.querySelectorAll('#msgs .qa').length >= 3`, [
      ['landed', async P => {
        await P.until(`!document.getElementById('compose-reply').hidden`, 'aimed', 8000).catch(() => null);
        await sleep(400);
        await P.shot('pg1440-inbox-reply');
        return P.ev(`({ line: (() => { const s = document.getElementById('compose-reply'); return s.hidden ? null : s.querySelector('.cr-re').textContent; })(),
          aimed: ${aimedIn('document')}, caret: document.activeElement === document.getElementById('input') })`);
      }],
    ]);
    // A phone: Reply keeps the card in view above the box; × is a 44px target.
    await view('ph390', 390, 844, true, `/session?room=${encodeURIComponent(A.po)}`, `document.querySelectorAll('#msgs .qa').length >= 3`, [
      ['reply', async P => {
        await P.ev(`(() => { document.getElementById('msgs').scrollTop = 0; return 1; })()`);
        await sleep(300);
        const card = cardIn('document', '/Which retention/');
        // The card scrolled off under the box first: the Reply is on screen, its card mostly below.
        await P.ev(`(() => { const m = document.getElementById('msgs'), c = ${card}, b = c.querySelector('.qa-reply');
          m.scrollTop += b.getBoundingClientRect().bottom - m.getBoundingClientRect().bottom + 4; return 1; })()`);
        await sleep(300);
        const p = await P.pressOn(`${card}.querySelector('.qa-reply')`, 'Reply');
        await sleep(700);
        await P.shot('ph390-aimed');
        return P.ev(`(() => { const c = (${card}).getBoundingClientRect(), m = document.getElementById('msgs').getBoundingClientRect(),
          box = document.getElementById('compose').getBoundingClientRect(), x = document.getElementById('compose-reply-x').getBoundingClientRect();
          return { hit: ${JSON.stringify(p.hits)}, aimed: ${aimedIn('document')}, inView: c.top >= m.top - 1 && c.bottom <= Math.min(m.bottom, box.top) + 1,
            card: [c.top, c.bottom], msgs: [m.top, m.bottom], box: box.top, x: [x.width, x.height],
            line: document.getElementById('compose-reply').hidden ? null : document.querySelector('#compose-reply .cr-re').textContent }; })()`);
      }],
    ]);
  } finally {
    for (const b of [desk, handheld]) try { await b('Browser.close'); } catch (e) {}
    chromes.forEach(c => c.kill());
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


def _plain(text: str) -> str:
    return text.split("\n\n[point")[0]


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class ReplyOnCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-reply-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts" / "C--po").mkdir(parents=True)
        (base / "cs").mkdir()
        sent = cls.sent = []

        def resume(h, room, text="", to="", key="", quiet=False):
            sent.append({"roomId": room["id"], "text": text, "to": to, "key": key})
            sends.mark(room["id"], [key], "delivered")
            return {"delivered": 1}

        for patch in [
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
            patch.start()
            cls.addClassCleanup(patch.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        asks.forget()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        code = Path(proj["path"])
        po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": "po-sid"}])
        cls.po = po["id"]
        room = chatroom.get_room(cls.po, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        t0 = time.time() - 600
        (base / "transcripts" / "C--po" / "po-sid.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [
            _turn("po-sid", "user", "[rotation] You are the PO of Motors. Read PO-HANDOVER.md.", t0),
            _turn("po-sid", "assistant", THREE, t0 + 5)]), encoding="utf-8")
        members = [{"identity": "claude", "agent": "claude", "role": "engineer", "cwd": str(code)},
                   {"identity": "claude-2", "agent": "claude", "role": "reviewer", "cwd": str(code)}]
        task = chatroom.create_room("Faster motors", members)
        cls.task = task["id"]
        chatroom.update_room({**chatroom.get_room(cls.task, public=False), "cwd": str(code)})
        dashboard.assign_session_project(cls.task, cls.proj)
        dashboard.assign_task_number(cls.task, cls.proj)
        qs = asks.validated([{"question": "Which motor profile should ship by default?", "options": [{"label": "Smooth"}, {"label": "Sport"}]}], [])
        chatroom.post_message(cls.task, "claude", TASK_ASK, to="user", ask_audience="user", structured_asks=[{**q, "n": i} for i, q in enumerate(qs)])
        cards = asks.open_in(chatroom.get_room(cls.po))
        backup = next(c for c in cards if "nightly backup" in c["question"])

        class Handler(dashboard.Handler):
            def do_GET(self):
                if self.path == "/test/sent":
                    body = json.dumps(sent).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": f"http://127.0.0.1:{server.server_address[1]}",
                "shots": shots, "proj": cls.proj, "po": cls.po, "task": cls.task,
                "backup": {"mid": backup["mid"], "n": backup["n"]}}
        run = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        dump = os.environ.get("REPLY_CDP_DUMP", "")
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1, ensure_ascii=False), encoding="utf-8")

    def step(self, view, name):
        r = self.got[view][name]
        self.assertFalse(isinstance(r, dict) and "error" in r, r)
        return r

    def test_reply_in_a_docked_po_chat_aims_the_workspace_box(self):
        self.assertEqual(self.step("ws1440", "open"), {"guest": True, "line": None, "hover": True})
        r = self.step("ws1440", "po")
        self.assertTrue(r["hit"], r)
        a = r["aimed"]
        self.assertEqual(a["line"], "reply to “What else should go into…”", r)
        self.assertEqual(a["aimed"], ["What else should go into this release?"], r)
        self.assertEqual(a["on"], 1, r)
        self.assertEqual(a["target"], {"room": self.po, "kind": "po"}, r)
        self.assertEqual(a["reply"]["question"], "What else should go into this release?", r)
        self.assertTrue(a["caret"], ("the caret is in the workspace box", r))
        self.assertTrue(r["sendHit"], r)
        self.assertIsNotNone(r["sent"], r)
        self.assertEqual(r["sent"]["roomId"], self.po)
        self.assertTrue(r["sent"]["text"].startswith("Re “What else should go into this release?”: Also the docs"), r)
        self.assertIn("You answered in your message", r["done"])
        self.assertIsNone(r["after"]["line"], r)
        self.assertEqual(r["after"]["aimed"], [], r)
        self.assertIsNone(r["after"]["reply"], r)

    def test_x_keeps_the_words_and_drops_the_link(self):
        r = self.step("ws1440", "x")
        self.assertTrue(r["xHit"], r)
        self.assertIsNone(r["after"]["line"], r)
        self.assertEqual(r["after"]["aimed"], [], r)
        self.assertEqual(r["after"]["input"], "Keep a year", r)
        self.assertIsNotNone(r["sent"], r)
        self.assertEqual(_plain(r["sent"]["text"]), "Keep a year", r)
        self.assertTrue(r["open"], "a plain message leaves the card open")

    def test_reply_in_a_docked_task_chat_aims_the_box_there(self):
        self.assertTrue(self.step("ws1440", "taskOpen"))
        r = self.step("ws1440", "task")
        self.assertTrue(r["hit"], r)
        a = r["aimed"]
        self.assertEqual(a["line"], "reply to “Which motor profile should…”", r)
        self.assertEqual(a["aimed"], ["Which motor profile should ship by default?"], r)
        self.assertEqual(a["target"], {"room": self.task, "kind": "task"}, r)
        self.assertTrue(a["caret"], r)
        self.assertEqual(r["sent"]["roomId"], self.task, r)
        self.assertTrue(r["sent"]["text"].startswith("Re “Which motor profile should ship by default?”: Smooth, but tuned"), r)
        self.assertIn("You answered in your message", r["done"])
        self.assertIsNone(r["after"]["line"], r)

    def test_the_inbox_reply_opens_it_with_the_box_aimed(self):
        r = self.step("ws1440", "inbox")
        self.assertTrue(r["rows"] and all(x["reply"] for x in r["rows"]), r["rows"])
        self.assertEqual(r["line"], "reply to “Turn on the nightly backup?”", r)
        self.assertEqual(r["state"]["aimed"], ["Turn on the nightly backup?"], r)
        self.assertEqual(r["state"]["target"], {"room": self.po, "kind": "po"}, r)
        self.assertEqual(r["side"], "↩ Reply", r)
        self.assertTrue(r["sideHit"], r)
        self.assertEqual(r["sideLine"], "reply to “Which retention for old…”", r)
        self.assertEqual(r["sideState"]["aimed"], ["Which retention for old transcripts?"], r)

    def test_the_inbox_reply_on_the_chats_own_page(self):
        r = self.step("pg1440", "landed")
        self.assertEqual(r["line"], "reply to “Turn on the nightly backup?”", r)
        self.assertEqual(r["aimed"], ["Turn on the nightly backup?"], r)
        self.assertTrue(r["caret"], r)

    def test_the_phone_keeps_the_card_in_view_and_a_44px_x(self):
        r = self.step("ph390", "reply")
        self.assertTrue(r["hit"], r)
        self.assertEqual(r["aimed"], ["Which retention for old transcripts?"], r)
        self.assertEqual(r["line"], "reply to “Which retention for old…”", r)
        self.assertTrue(r["inView"], r)
        self.assertGreaterEqual(min(r["x"]), 44, r)

    def test_no_script_errors(self):
        for key, v in self.got.items():
            with self.subTest(view=key):
                self.assertEqual(v["errors"], [], v["errors"])


if __name__ == "__main__":
    unittest.main()
