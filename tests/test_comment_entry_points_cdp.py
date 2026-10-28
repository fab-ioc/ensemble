"""Every Comment works where the person reads (P233), in headless Chrome.

Each entry point is driven as a person does it, with real presses at the
place the top page hits (through the frames), at 1440x900 and on a phone at
390x844:

* the selection bar's 💬 Comment over a chat's balloon (the PO chat and a
  task chat, docked in the workspace and in a chat's own page), over a file's
  words (file view) and over a diff's lines (Changes): the comment box opens,
  takes the words typed, Add puts them in the "N comments" tray, and its
  Submit hands the hub one "## Review comments" message for that chat;
* Comment on an answered point, under the balloon and in the Dock's Needs
  you list: the link to the answer is a new point in the box that sends to
  that chat, with the cursor in it. In the workspace that is the shared Send
  box (#271): the chat's own box is hidden there, and before P233 the link
  went into it, so nothing showed.

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
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A), '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map(); const errs = [];
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') errs.push(String(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).slice(0, 300));
    if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const out = {};
  async function view(key, w, h, phone, url, ready, steps) {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, s); await send('Runtime.enable', {}, s);
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: phone }, s);
    if (phone) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
    const ev = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, s);
      if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, label, ms = 25000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + (label || expr)); };
    // A press where the top page hits, as the person makes it: mouse, or a tap on the phone.
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
    // `el` (an expression for an element, in any frame of the page): its centre
    // on the top page, and whether a press there reaches it.
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
    const P = { ev, until, press, pressOn, at, shot, phone, key, type: text => send('Input.insertText', { text }, s) };
    errs.length = 0;
    await send('Page.navigate', { url: A.base + url }, s);
    await until(ready, key + ' ready', 40000).catch(async e => { throw new Error(e.message + ' :: ' + await ev(`location.href + ' | ' + document.querySelectorAll('#msgs .msg').length + ' msgs | ' + [...document.querySelectorAll('#msgs .msg')].map(m => m.className + ':' + (m.querySelector('.text') || {}).className).join(',') + ' | ' + document.querySelectorAll('.pt-btn').length + ' pt | ' + document.body.innerText.slice(0, 400)`).catch(x => String(x))); });
    await sleep(1200);
    out[key] = {};
    for (const [name, fn] of steps) {
      try { out[key][name] = await fn(P); } catch (e) { out[key][name] = { error: String(e && e.message || e).slice(0, 600) }; }
    }
    out[key].errors = errs.slice();
    await send('Target.closeTarget', { targetId });
  }

  // Selected words in `doc` (an expression for a document): the first `n`
  // letters of the text node holding `needle` under `root`.
  const choose = (doc, root, needle) => `(() => { const d = ${doc}, w = d.defaultView; w.getSelection().removeAllRanges();
    const t = [...d.querySelectorAll(${JSON.stringify(root)})].find(e => e.textContent.includes(${JSON.stringify(needle)}));
    if (!t) return false; t.scrollIntoView({ block: 'center' });
    const wk = d.createTreeWalker(t, NodeFilter.SHOW_TEXT); let x;
    while ((x = wk.nextNode())) { const i = x.nodeValue.indexOf(${JSON.stringify(needle)}); if (i >= 0) { w.getSelection().setBaseAndExtent(x, i, x, i + ${JSON.stringify(needle)}.length); return true; } }
    return false; })()`;
  // The selection bar's Comment, the comment box, Add, the tray and its Submit.
  // box: the comment box's selector; add: its Add button; tray: where Submit is.
  async function selComment(P, o) {
    const r = {};
    r.selected = await P.ev(o.select);
    await P.until(`!!${o.doc}.querySelector('.sel-bar:not([hidden]) .sel-cmt:not([hidden])')`, 'selection bar');
    const c = await P.pressOn(`${o.doc}.querySelector('.sel-bar .sel-cmt')`, 'Comment');
    r.commentHit = c.hits;
    r.box = !!(await P.until(`!!${o.boxDoc || o.doc}.querySelector(${JSON.stringify(o.box)})`, 'comment box', 4000).catch(() => false));
    if (!r.box) return r;
    r.focused = await P.ev(`(() => { const d = ${o.boxDoc || o.doc}; const a = d.activeElement; return !!a && a.tagName === 'TEXTAREA' && !!a.closest(${JSON.stringify(o.box)}); })()`);
    await P.type(o.words);
    r.typed = await P.ev(`${o.boxDoc || o.doc}.querySelector(${JSON.stringify(o.box + ' textarea')}).value`);
    const a = await P.pressOn(`${o.boxDoc || o.doc}.querySelector(${JSON.stringify(o.add)})`, 'Add');
    r.addHit = a.hits;
    await P.shot(o.shot + '-tray');
    r.tray = await P.until(`(() => { const t = ${o.tray}; return t && t.textContent.includes(${JSON.stringify(o.words)}) ? t.textContent.replace(/\\s+/g, ' ').slice(0, 160) : ''; })()`, 'tray', 4000).catch(() => '');
    if (!r.tray) return r;
    const sent0 = await P.ev(`fetch('/test/sent').then(x => x.json()).then(l => l.length)`);
    const sb = await P.pressOn(`${o.tray}.querySelector('.cmt-submit')`, 'Submit');
    r.submitHit = sb.hits;
    r.sent = await P.until(`fetch('/test/sent').then(x => x.json()).then(l => l.slice(${sent0}).find(x => x.text.includes(${JSON.stringify(o.words)})) || null)`, 'sent', 8000).catch(() => null);
    r.trayAfter = await P.ev(`(() => { const t = ${o.tray}; return !!t && t.isConnected && t.textContent.includes(${JSON.stringify(o.words)}); })()`);
    return r;
  }
  // Comment on an answered point: the link lands in the box that sends to
  // that chat, the cursor in it. box: that box's window, as an expression.
  async function pointComment(P, o) {
    const r = {};
    await P.until(`!!${o.btn}`, 'point Comment');
    const p = await P.pressOn(o.btn, 'point Comment');
    r.hit = p.hits;
    r.box = await P.until(`(() => { const w = ${o.box}; const ed = w && w.eval('typeof ED === "undefined" ? null : ED'); if (!ed) return null; const pts = ed.points.map(x => x.text || '');
      const d = w.document, a = d.activeElement, li = a && a.closest && a.closest('li.ed-pt');
      const shown = !!d.getElementById('compose') && d.getElementById('compose').getClientRects().length > 0 && w.frameElement?.offsetParent !== null;
      return pts.some(t => t.includes(${JSON.stringify(o.mid)})) ? { points: pts, focused: !!li && d.hasFocus(), shown } : null; })()`, 'link in the box', 5000).catch(() => null);
    // Nothing went to the hidden box of the chat itself.
    if (o.own) r.own = await P.ev(`(() => { const w = ${o.own}; const ed = w && w.eval('typeof ED === "undefined" ? null : ED'); return ed ? ed.points.map(x => x.text || '') : null; })()`);
    await P.shot(o.shot);
    return r;
  }

  const ptBtn = doc => `((d) => { const b = d.querySelector('#msgs .pt-btn[data-pt-act=comment]'); if (b) return b;
    const s = d.querySelector('#points-line .pt-sum[aria-expanded=false]'); if (s) s.click();
    return d.querySelector('#points-list .pt-btn[data-pt-act=comment]'); })(${doc})`;
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const TASK = `document.querySelector('iframe.dp-session[data-room=${JSON.stringify(A.task)}]')`;
  const WCF = `document.getElementById('workspace-compose-frame')`;
  const WS_READY = `typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r => r.roomId === ${JSON.stringify(A.po)})`;
  const chatSteps = (frame, room, shot, words) => [
    ['sel', P => selComment(P, { doc: `${frame}.contentDocument`, select: choose(`${frame}.contentDocument`, '#msgs .msg .text.md', words),
      box: '.cmt-composer', add: '.cmt-composer .cmt-add', tray: `${frame}.contentDocument.getElementById('cmt-tray')`, words: 'Comment on ' + shot, shot })],
  ];
  try {
    // The workspace at 1440: the PO chat and a task chat docked, one Send box under them.
    await view('ws1440', 1440, 900, false, '/', WS_READY, [
      ['open', async P => { await P.ev(`SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); 0`);
        await P.until(`${PO}?.contentDocument?.querySelectorAll('#msgs .msg .text.md').length >= 2`, 'PO chat');
        await P.ev(`pdChatOpen(${JSON.stringify(A.task)}); 0`);
        await P.until(`!!${TASK}?.contentDocument?.querySelector('#msgs .pt-btn[data-pt-act=comment]')`, 'task chat');
        await P.until(`!document.getElementById('workspace-compose').hidden && !!${WCF}.contentWindow?.eval('typeof ED === "undefined" ? null : ED')`, 'workspace box');
        await sleep(800);
        return P.ev(`({ guests: [${PO}, ${TASK}].map(f => f.contentDocument.body.classList.contains('workspace-guest')) })`); }],
      ...chatSteps(PO, A.po, 'ws1440-po', A.words.po),
      ...chatSteps(TASK, A.task, 'ws1440-task', A.words.task),
      ['poPoint', P => pointComment(P, { btn: ptBtn(`${PO}.contentDocument`), box: `${WCF}.contentWindow`,
        own: `${PO}.contentWindow`, mid: A.poAnswer, shot: 'ws1440-po-point' })],
      ['wcAfterPo', P => P.ev(`({ room: WC.target && WC.target.room, kind: WC.target && WC.target.kind })`)],
      ['taskPoint', P => pointComment(P, { btn: ptBtn(`${TASK}.contentDocument`), box: `${WCF}.contentWindow`,
        own: `${TASK}.contentWindow`, mid: A.taskAnswer, shot: 'ws1440-task-point' })],
      ['wcAfterTask', P => P.ev(`({ room: WC.target && WC.target.room, kind: WC.target && WC.target.kind })`)],
      ['needsYou', async P => { await P.ev(`wcChoose({ kind: 'po', room: ${JSON.stringify(A.po)} }); 0`);
        await P.until(`(() => { const w = ${WCF}.contentWindow; return w.eval('typeof ED !== "undefined" && typeof ROOM !== "undefined" && ROOM') === ${JSON.stringify(A.po)}; })()`, 'PO draft');
        await P.ev(`${WCF}.contentWindow.eval('while (ED.points.length) edRemoveAt(0); ED.points.length')`);
        await P.ev(`PD.dock.reveal('points'); 0`);
        return pointComment(P, { btn: `PD.els.points.querySelector('.pdp-btn[data-pt-act=comment]')`, box: `${WCF}.contentWindow`,
          own: `${PO}.contentWindow`, mid: A.poAnswer, shot: 'ws1440-needs-you' }); }],
      ['changes', async P => { await P.ev(`PD.dock.reveal('changes'); 0`);
        await P.until(`!!PD.els.changes.querySelector('.chf[data-file="motor.py"]')`, 'changes list');
        await P.ev(`PD.els.changes.querySelector('.chf[data-file="motor.py"]').click(); 0`);
        await P.until(`PD.els.changes.querySelectorAll('.drv .dr .dg[data-n]').length > 2`, 'diff').catch(async e => { throw new Error(e.message + ' :: ' + await P.ev(`PD.els.changes.innerHTML.slice(1500, 5000)`)); });
        return selComment(P, { doc: 'document', select: `(() => { const rows = [...PD.els.changes.querySelectorAll('.drv .dr')].filter(x => x.querySelector('.dg[data-n]'));
            rows[1].scrollIntoView({ block: 'center' }); getSelection().setBaseAndExtent(rows[1].querySelector('.dt'), 0, rows[2].querySelector('.dt'), 0); return true; })()`,
          box: '.dcx', add: '.dcx .dcx-add', tray: `([...PD.els.changes.querySelectorAll('.cmt-tray-head')].find(h => h.getClientRects().length) || {}).parentElement`,
          words: 'Comment on the diff at 1440', shot: 'ws1440-changes' }); }],
    ]);
    // A chat's own page at 1440 and on the phone, the file view at both, and Changes on the phone.
    for (const [w, h, phone] of [[1440, 900, false], [390, 844, true]]) {
      const tag = (phone ? 'ph' : 'pg') + w;
      for (const [who, room, mid] of [['po', A.po, A.poAnswer], ['task', A.task, A.taskAnswer]]) {
        await view(`${tag}-${who}`, w, h, phone, '/session?id=' + encodeURIComponent(room),
          `document.querySelectorAll('#msgs .msg .text.md').length >= 2 && document.getElementById('points-line').getClientRects().length > 0`, [
          ['sel', P => selComment(P, { doc: 'document', select: choose('document', '#msgs .msg .text.md', A.words[who]),
            box: '.cmt-composer', add: '.cmt-composer .cmt-add', tray: `document.getElementById('cmt-tray')`, words: `Comment on ${tag} ${who}`, shot: `${tag}-${who}` })],
          ['point', P => pointComment(P, { btn: ptBtn('document'), box: 'window', mid, shot: `${tag}-${who}-point` })],
        ]);
      }
      await view(`${tag}-file`, w, h, phone, A.file, `!!document.querySelector('.md, #content, main') && document.body.textContent.includes(${JSON.stringify(A.fileWords)})`, [
        ['sel', P => selComment(P, { doc: 'document', select: choose('document', 'p, li', A.fileWords),
          box: '.cmt-composer', add: '.cmt-composer .cmt-add', tray: `document.getElementById('cmt-tray')`, words: `Comment on the file at ${w}`, shot: `${tag}-file` })],
      ]);
    }
    await view('ph390-changes', 390, 844, true, '/', WS_READY, [
      ['changes', async P => { await P.ev(`SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); 0`);
        await sleep(800);
        await P.ev(`PD.dock.reveal('changes'); 0`);
        await P.until(`!!PD.els.changes.querySelector('.chf[data-file="motor.py"]')`, 'changes list');
        await P.ev(`PD.els.changes.querySelector('.chf[data-file="motor.py"]').click(); 0`);
        await P.until(`PD.els.changes.querySelectorAll('.drv .dr .dg[data-n]').length > 2`, 'diff').catch(async e => { throw new Error(e.message + ' :: ' + await P.ev(`PD.els.changes.innerHTML.slice(1500, 5000)`)); });
        return selComment(P, { doc: 'document', select: `(() => { const rows = [...PD.els.changes.querySelectorAll('.drv .dr')].filter(x => x.querySelector('.dg[data-n]'));
            rows[1].scrollIntoView({ block: 'center' }); getSelection().setBaseAndExtent(rows[1].querySelector('.dt'), 0, rows[2].querySelector('.dt'), 0); return true; })()`,
          box: '.dcx', add: '.dcx .dcx-add', tray: `([...PD.els.changes.querySelectorAll('.cmt-tray-head')].find(h => h.getClientRects().length) || {}).parentElement`,
          words: 'Comment on the diff at 390', shot: 'ph390-changes' }); }],
    ]);
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

ANSWER = "Re P1: the board is wider now and the panels fit."
TASK_ANSWER = "Re P1: the motor speeds are doubled in motor.py."
FILE_WORDS = "The notes say what the motors do"


def _turn(sid: str, role: str, text: str, at: float) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    if role == "user":
        return {"type": "user", "timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class EveryComment(unittest.TestCase):
    """A hub in a thread with one project: its PO chat and a task chat, each
    with an answered point, a file in its Documents and an uncommitted change
    in its repository. /api/room/resume records what it is given and posts
    it as the person's message (no agent starts); /test/sent lists it."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-cmt-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        code = Path(proj["path"])

        def git(*a):
            subprocess.run(["git", "-C", str(code), *a], capture_output=True, check=True, encoding="utf-8")
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        (code / "motor.py").write_text("".join(f"speed_{i} = {i}\n" for i in range(12)), encoding="utf-8")
        git("add", "motor.py")
        git("commit", "-q", "-m", "motors")
        (code / "motor.py").write_text("".join(f"speed_{i} = {i * 2}\n" for i in range(12)), encoding="utf-8")
        docs = home / "Documents"
        docs.mkdir(exist_ok=True)
        note = docs / "#1 Notes.md"
        note.write_text(f"# Notes\n\n{FILE_WORDS}, and how fast.\n", encoding="utf-8")
        # The PO's chat is its transcript; the user's line is point P1, answered.
        sid = "po-cmt-1"
        po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid}])
        cls.po = po["id"]
        room = chatroom.get_room(cls.po, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        t0 = time.time() - 3600
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        threads.forget()
        q, ids = points.take(chatroom.get_room(cls.po, public=False), "Please make the board wider", key="P1", now=t0 + 10, sid=sid)
        assert ids == ["P1"], ids
        tdir = base / "transcripts" / "C--po"
        tdir.mkdir()
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [
            _turn(sid, "assistant", "I'm the PO for Motors.", t0),
            _turn(sid, "user", q, t0 + 11),
            _turn(sid, "assistant", ANSWER, t0 + 20)]), encoding="utf-8")
        points.sync(cls.po, force=True)
        threads._ensure_since(cls.po, t0 - 60)
        members = [{"identity": "claude", "agent": "claude", "role": "engineer", "cwd": str(code)},
                   {"identity": "claude-2", "agent": "claude", "role": "reviewer", "cwd": str(code)}]
        task = chatroom.create_room("Faster motors", members)
        cls.task = task["id"]
        chatroom.update_room({**chatroom.get_room(cls.task, public=False), "cwd": str(code)})
        dashboard.assign_session_project(cls.task, cls.proj)
        dashboard.assign_task_number(cls.task, cls.proj)
        text, ids = points.take(chatroom.get_room(cls.task, public=False), "Please double the motor speeds", to="claude", key="k-task")
        assert ids == ["P1"], ids
        chatroom.post_message(cls.task, "user", text, to="claude")
        chatroom.post_message(cls.task, "claude", TASK_ANSWER, to="user")
        points.sync(cls.task, force=True)
        sent = cls.sent = []

        class Handler(dashboard.Handler):
            def _json(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/test/sent":
                    return self._json(sent)
                super().do_GET()

            def do_POST(self):
                if self.path.split("?")[0] == "/api/room/resume":
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    if body.get("text"):
                        sent.append(body)
                        chatroom.post_message(body["roomId"], "user", body["text"], to=body.get("to") or "")
                    return self._json({"ok": True})
                super().do_POST()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        # Each answer's balloon, as the hub names it to the page.
        answers = {}
        for rid in (cls.po, cls.task):
            with urllib.request.urlopen(f"{base_url}/api/room?id={rid}&n=40") as r:
                item = json.load(r)["points"]["items"][0]
            assert item["answers"], item
            answers[rid] = item["answers"][0]["mid"]
        cls.mids = {"po": answers[cls.po], "task": answers[cls.task]}
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": base_url,
                "shots": shots, "proj": cls.proj, "po": cls.po, "task": cls.task,
                "poAnswer": answers[cls.po], "taskAnswer": answers[cls.task], "words": {"po": "I'm the PO for Motors", "task": TASK_ANSWER[:24]},
                "file": f"/fileview?path={quote(str(note))}&room={cls.task}&roomName=Faster%20motors", "fileWords": FILE_WORDS}
        run = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        dump = os.environ.get("COMMENT_CDP_DUMP", "")
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1, ensure_ascii=False), encoding="utf-8")

    def sel_ok(self, key, step, room):
        r = self.got[key][step]
        self.assertNotIn("error", r, r)
        self.assertTrue(r["selected"], r)
        self.assertTrue(r["commentHit"], ("a press on Comment reaches it", r))
        self.assertTrue(r["box"], ("Comment opens the comment box", r))
        self.assertTrue(r["focused"], ("the cursor is in the box", r))
        self.assertTrue(r["typed"], r)
        self.assertTrue(r["addHit"], r)
        self.assertTrue(r["tray"], ("Add puts it in the tray", r))
        self.assertTrue(r["submitHit"], r)
        self.assertIsNotNone(r["sent"], ("Submit hands it to the hub", r))
        self.assertTrue(r["sent"]["text"].startswith("## Review comments"), r)
        self.assertIn(r["typed"], r["sent"]["text"], r)
        if room:
            self.assertEqual(r["sent"]["roomId"], room, r)
        self.assertFalse(r["trayAfter"], ("the tray lets a sent comment go", r))

    def point_ok(self, key, step, mid, own=True):
        r = self.got[key][step]
        self.assertNotIn("error", r, r)
        self.assertTrue(r["hit"], ("a press on Comment reaches it", r))
        self.assertIsNotNone(r["box"], ("the link is in the box that sends to the chat", r))
        self.assertTrue(r["box"]["shown"], ("that box is on screen", r))
        self.assertTrue(r["box"]["focused"], ("the cursor is in the new point", r))
        self.assertEqual(sum(mid in t for t in r["box"]["points"]), 1, r)
        if own and "own" in r:
            self.assertFalse(any(mid in t for t in (r["own"] or [])), ("not in the chat's hidden box", r))

    def test_selection_comment_in_the_workspace(self):
        self.assertEqual(self.got["ws1440"]["open"], {"guests": [True, True]})
        self.sel_ok("ws1440", "sel", None)

    def test_selection_comment_in_a_chat_page_and_on_the_phone(self):
        for tag in ("pg1440", "ph390"):
            for who, room in (("po", self.po), ("task", self.task)):
                with self.subTest(at=tag, chat=who):
                    self.sel_ok(f"{tag}-{who}", "sel", room)

    def test_selection_comment_in_the_file_view(self):
        for tag in ("pg1440", "ph390"):
            with self.subTest(at=tag):
                self.sel_ok(f"{tag}-file", "sel", self.task)

    def test_selection_comment_on_a_diff(self):
        for key in ("ws1440", "ph390-changes"):
            with self.subTest(at=key):
                self.sel_ok(key, "changes", None)

    def test_point_comment_in_the_workspace_goes_in_the_send_box(self):
        self.point_ok("ws1440", "poPoint", self.got_mid("po"))
        self.assertEqual(self.got["ws1440"]["wcAfterPo"], {"room": self.po, "kind": "po"})
        self.point_ok("ws1440", "taskPoint", self.got_mid("task"))
        self.assertEqual(self.got["ws1440"]["wcAfterTask"], {"room": self.task, "kind": "task"})

    def test_needs_you_comment_in_the_workspace_goes_in_the_send_box(self):
        self.point_ok("ws1440", "needsYou", self.got_mid("po"))

    def test_point_comment_in_a_chat_page_and_on_the_phone(self):
        for tag in ("pg1440", "ph390"):
            for who in ("po", "task"):
                with self.subTest(at=tag, chat=who):
                    self.point_ok(f"{tag}-{who}", "point", self.got_mid(who), own=False)

    def test_no_script_errors(self):
        for key, v in self.got.items():
            with self.subTest(view=key):
                self.assertEqual(v["errors"], [], v["errors"])

    def got_mid(self, who):
        return self.mids[who]


if __name__ == "__main__":
    unittest.main()
