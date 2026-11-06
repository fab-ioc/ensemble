"""Layout A step 6 (#308): old links still open what they name, and the
Inbox's rows and count move as things are read, answered and dealt with, and
hold over a reload, another browser and a hub restart.

In headless Chrome over CDP at 1440x900 and 390x844 (touch), each size against
its own hub: a child process with its own home folder (USERPROFILE), so a
restart is a real one (a new process reading the same state files). The node
script drives both the browser and the hub (it types Python lines to the hub
for what agents do: a report, a stop).

Two projects: Motors, with a PO whose chat holds an answered point (P1) and an
Ask card, and a task (M1); Alpha, with no PO, and tasks whose agents asked the
person a question or reported. Their agents' terminals are stand-ins the hub
takes as alive.

Links (each at both sizes):

* ``/?room=<PO>&msg=<answer>`` opens the PO's conversation at that balloon;
  ``/?room=<task>&msg=<card>&part=ask:0`` the task's, at its card;
  ``/?room=<PO>&thread=P1`` the P1 thread; a room the app does not list goes
  to the chat's own page (``&stay=1``), which offers no way back;
* ``/#needs`` (the old Needs you page) opens the Inbox;
* a chat opened in a tab of its own has the Ensemble icon (``#to-app``),
  whose link opens the same message (and the thread open beside it) in the
  app; on a phone that thread covers the chat, and its head (``#ts-to-app``)
  carries the icon a tap reaches.

Transitions, each checked on the hub's /api/inbox as the page polls it and
on the bar's Inbox count:

* reading P1's answer (a real click on its Inbox row) takes the check row
  off; answering the PO's card with its button takes the answer row off;
* a typed reply in A1's chat, a thumbs-up on A2's card, Done with this on
  A3 take their rows off;
* A4 reports it finished (a check row), and opening it takes it off; A5's
  finished report leaves when A5 is stopped; A8 asks a question in a report
  (an answer row), and its finished report after takes it off;
* M1's question goes to Motors' PO: the person's Inbox never lists it;
* Clear all on A6 and A7 takes both off, Undo brings both back;
* then a reload, another browser (a new profile) and a hub restart show the
  same rows and the same count;
* nothing is wider than the screen, the page threw nothing.

Measures go to $ENSEMBLE_SHOTS/links-states-<w>.json when set. Skipped
without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1440, 900, False), (390, 844, True)]
if os.environ.get("LS_ONLY"):
    SIZES = [s for s in SIZES if str(s[0]) == os.environ["LS_ONLY"]]

# The hub, in a process of its own. argv: its state folder, the repo. It seeds
# the fixtures on its first start (ids.json), prints {"port", "ids"}, then runs
# each line typed to it as Python and answers {"id", "ok", "out"}.
HUB_PY = r'''
import json, os, sys, threading, time, traceback, types
from pathlib import Path
base, repo = Path(sys.argv[1]), sys.argv[2]
# The lines typed to this process come on a private copy of its stdin, and
# its stdin becomes NUL. On Windows a program started while the main thread
# waits in a read of that pipe inherits it as its own stdin and hangs in its
# start-up until the read ends (git: no CPU, one thread): the hub's request
# threads then wait for git for ever (operator_name, git_root), Chrome's six
# connections to the host fill, and every later page waits or, through the
# service worker, shows the start-up page.
CMDS = os.fdopen(os.dup(0), "r", encoding="utf-8")
_nul = os.open(os.devnull, os.O_RDONLY); os.dup2(_nul, 0); os.close(_nul)
sys.path.insert(0, repo)
from http.server import ThreadingHTTPServer
from unittest import mock
import chatroom, dashboard, attention, points, ensemble_tools
LIVE = base / "live.json"
def live():
    try: return set(json.loads(LIVE.read_text(encoding="utf-8")))
    except (OSError, ValueError): return set()
def set_live(s): LIVE.write_text(json.dumps(sorted(s)), encoding="utf-8")
class Term:     # a stand-in agent terminal: alive, idle at its prompt
    def __init__(self, pid): self.id = pid; self.last_output = 1.0
    def alive(self): return self.id in live()
    def tail(self): return ""
    def info(self): return {"idleSeconds": 5}
    def last_submit(self): return 0.0
def kill(pid):
    s = live(); s.discard(pid); set_live(s)
def resume(h, room, text="", to="", key="", quiet=False, **kw):
    # As the hub does with a running chat: a team's room posts the line, a
    # one-agent chat's line is typed (its transcript shows it).
    if text and sum(1 for p in room["participants"] if p.get("kind") == "agent") > 1:
        dashboard._posted_send(room["id"], {"key": key, "text": text},
                               chatroom.post_message(room["id"], "user", text, to=to or "claude"))
    else:
        dashboard._typed_sends(room["id"], [{"key": key, "text": text}])
    return {"delivered": 1}
for p in [mock.patch.object(dashboard, "load_live", lambda: []),
          mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
          mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
          mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
          mock.patch.object(dashboard.Handler, "_resume_room", resume),
          mock.patch.object(dashboard.ptyrun, "get", lambda pid: Term(pid) if pid in live() else None),
          mock.patch.object(dashboard.ptyrun, "kill", kill),
          mock.patch.object(dashboard.ptyrun, "list_sessions", lambda *a, **k: []),
          mock.patch.object(attention, "_stall_seconds", lambda: 10 ** 9)]:
    p.start()

def team(home, rid=None):
    return [{"identity": "claude", "agent": "claude", "role": "engineer", "cwd": str(home)},
            {"identity": "codex", "agent": "codex", "role": "reviewer", "cwd": str(home)}]
def launched(rid):
    full = chatroom.get_room(rid, public=False)
    full.update(launched=True, mode="collab")
    s = live()
    for part in full["participants"]:
        if part.get("kind") == "agent":
            part["ptyId"] = f"fake-{rid}-{part['identity']}"
            s.add(part["ptyId"])
    chatroom.update_room(full)
    set_live(s)
def report(rid, kind, text, project):
    room = chatroom.get_room(rid, public=False)
    ctx = {"room": room, "identity": "claude", "part": chatroom.participant(room, "claude"), "projectId": project}
    return ensemble_tools._report(ctx, {"kind": kind, "text": text}, types.SimpleNamespace(_ring_report=lambda *a, **k: []))

ids_file = base / "ids.json"
if not ids_file.exists():
    ids = {}
    ok, mo, _ = dashboard.register_project("Motors"); assert ok, mo
    ok, al, _ = dashboard.register_project("Alpha"); assert ok, al
    ids["Motors"], ids["Alpha"] = mo["id"], al["id"]
    mh, ah = Path(mo.get("home") or mo["path"]), Path(al.get("home") or al["path"])
    # The PO chat: one agent, so a solo, threaded chat whose balloons are its
    # transcript's (the person's line with P1, the agent's "Re P1:" answer).
    po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "role": "Product owner",
                                             "sessionId": "po-sid", "cwd": str(mh)}])
    full = chatroom.get_room(po["id"], public=False); full["mode"] = "solo"; chatroom.update_room(full)
    dashboard.assign_session_project(po["id"], mo["id"])
    ok, why = dashboard.set_project_po(mo["id"], po["id"]); assert ok, why
    launched(po["id"])
    full = chatroom.get_room(po["id"], public=False); full["mode"] = "solo"; chatroom.update_room(full)
    ids["po"] = po["id"]
    text, got = points.take(chatroom.get_room(po["id"], public=False), "Make the board wider", to="claude", key="k1")
    assert got == ["P1"], got
    tdir = dashboard.PROJ_DIR / "C--po"; tdir.mkdir(parents=True, exist_ok=True)
    def turn(role, words, at):
        msg = {"role": role, "content": words if role == "user" else [{"type": "text", "text": words}]}
        if role == "assistant": msg["stop_reason"] = "end_turn"
        return {"type": role, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z", "sessionId": "po-sid", "message": msg}
    now = time.time() - 60       # before the card: a chat draws no hub row above its first turn
    (tdir / "po-sid.jsonl").write_text("".join(json.dumps(x) + chr(10) for x in [turn("user", text, now), turn("assistant", "Re P1: it is wider now.", now + 1)]), encoding="utf-8")
    points.sync(po["id"], force=True)
    led = points.load(po["id"], shared=True)
    ids["answer"] = points.last_answer(led["points"][0])["mid"]
    time.sleep(0.02)
    # Its card, as ensemble_ask posts one (a solo chat draws the hub's rows, not an agent's room lines).
    qs = dashboard.asks.validated([{"question": "Ship the brakes this week?", "options": [{"label": "Ship"}, {"label": "Wait"}]}], [])
    ids["poAsk"] = chatroom.post_message(po["id"], "claude", "Questions for you.", to="user", structured_asks=qs, ask_audience="user")["message"]["id"]
    m1 = chatroom.create_room("Gearbox noise", team(mh))
    dashboard.assign_session_project(m1["id"], mo["id"]); launched(m1["id"]); ids["M1"] = m1["id"]
    chatroom.post_message(m1["id"], "user", "Find the gearbox noise.", to="claude")
    for k, words in [("A1", "the pads"), ("A2", "the discs"), ("A3", "the chain"), ("A6", "the bell"), ("A7", "the lamp")]:
        t = chatroom.create_room(f"Brakes: {words}", team(ah))
        dashboard.assign_session_project(t["id"], al["id"]); launched(t["id"]); ids[k] = t["id"]
        chatroom.post_message(t["id"], "user", f"Look at {words}, please.", to="claude")
        time.sleep(0.02)
        ids[k + "ask"] = chatroom.post_message(t["id"], "claude", f"Ask: Which way for {words}?\n- Replace them\n- Wait a month", to="user")["message"]["id"]
    for k, title in [("A4", "Saddle height"), ("A5", "Bell volume"), ("A8", "Spoke count")]:
        t = chatroom.create_room(title, team(ah))
        dashboard.assign_session_project(t["id"], al["id"]); launched(t["id"]); ids[k] = t["id"]
        chatroom.post_message(t["id"], "user", f"Do the {title.lower()}.", to="claude")
    ids_file.write_text(json.dumps(ids), encoding="utf-8")
ids = json.loads(ids_file.read_text(encoding="utf-8"))
srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
srv.daemon_threads = True
srv.handle_error = lambda *a: None
threading.Thread(target=srv.serve_forever, daemon=True).start()
print(json.dumps({"port": srv.server_address[1], "ids": ids}), flush=True)
# The state files as a person's looking must leave them: read marks dropped
# (seenAt, seen, _revision), the cache left out.
def snapshot():
    import hashlib
    root = Path.home() / ".ensemble"
    def strip(v):
        if isinstance(v, dict): return {k: strip(x) for k, x in v.items() if k not in ("seenAt", "seen", "_revision")}
        if isinstance(v, list): return [strip(x) for x in v]
        return v
    out = {}
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root).as_posix()
        if not f.is_file() or rel.startswith("cache/") or rel.endswith((".lock", ".tmp")): continue
        b = f.read_bytes()
        if f.suffix == ".json":
            try: b = json.dumps(strip(json.loads(b.decode("utf-8"))), sort_keys=True).encode("utf-8")
            except ValueError: pass
        out[rel] = hashlib.sha1(b).hexdigest()
    return out
ns = {"dashboard": dashboard, "chatroom": chatroom, "snapshot": snapshot, "Path": Path, "attention": attention, "points": points,
      "report": report, "ids": ids, "live": live, "time": time}
for line in CMDS:
    if not line.strip():
        continue
    cmd = json.loads(line)
    ns["out"] = None
    try:
        exec(cmd["code"], ns)
        print(json.dumps({"id": cmd["id"], "ok": True, "out": ns["out"]}, default=str), flush=True)
    except Exception:
        print(json.dumps({"id": cmd["id"], "ok": False, "out": traceback.format_exc()[-1500:]}), flush=True)
'''

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const [W, H, MOB] = A.size;
// ---- the hub, a child process
let hub = null;
function startHub() {
  return new Promise((res, rej) => {
    const env = { ...process.env, USERPROFILE: A.home, HOME: A.home, CODEX_HOME: path.join(A.home, 'codex'), PYTHONIOENCODING: 'utf-8' };
    const p = spawn(A.py, [A.hubPy, A.state, A.repo], { env, stdio: ['pipe', 'pipe', 'pipe'] });
    const h = { p, buf: '', waits: new Map(), n: 0, err: '' };
    p.stderr.on('data', d => { h.err = (h.err + d).slice(-4000); });
    p.stdout.on('data', d => {
      h.buf += d; let i;
      while ((i = h.buf.indexOf('\n')) >= 0) {
        const line = h.buf.slice(0, i).trim(); h.buf = h.buf.slice(i + 1);
        if (!line) continue;
        let m; try { m = JSON.parse(line); } catch (e) { continue; }
        if (m.port) { h.port = m.port; h.ids = m.ids; res(h); continue; }
        const w = h.waits.get(m.id); if (w) { h.waits.delete(m.id); m.ok ? w.res(m.out) : w.rej(new Error(m.out)); }
      }
    });
    p.on('exit', c => { if (!h.port) rej(new Error('hub exited ' + c + ' ' + h.err)); });
    setTimeout(() => { if (!h.port) rej(new Error('hub did not start ' + h.err)); }, 60000);
  });
}
const py = code => new Promise((res, rej) => { const id = ++hub.n; hub.waits.set(id, { res, rej }); hub.p.stdin.write(JSON.stringify({ id, code }) + '\n'); });
async function stopHub() { const p = hub.p; p.stdin.end(); p.kill(); await new Promise(r => { if (p.exitCode !== null) r(); else p.on('exit', r); setTimeout(r, 5000); }); }
// ---- Chrome
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const c = new Cdp(ws); await c.open();
  return { ch, c, close: async () => { try { await c.send('Browser.close'); } catch (e) {} ch.kill(); } };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function tab(c, url, boot) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, s); await c.send('Runtime.enable', {}, s);
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, s);
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: MOB }, s);
  if (MOB) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
  if (boot === false) await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, s);
  const ev = async (expression) => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(expression.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (ex, ms = 20000) => { const t = Date.now(); let last = null; while (Date.now() - t < ms) { let v = null; try { v = await ev(ex); } catch (e) { last = e; } if (v) return v; await sleep(150); } throw new Error('timeout: ' + ex.slice(0, 300) + (last ? ' :: ' + last.message.slice(0, 300) : '')); };
  const click = async (expr) => {
    const [x, y] = await ev(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
    if (MOB) { for (const type of ['touchStart', 'touchEnd']) await c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] }, s); }
    else for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, s);
  };
  const go = async (u) => { await c.send('Page.navigate', { url: A.base() + u }, s); };
  await go(url);
  return { ev, until, click, go, s, close: () => c.send('Target.closeTarget', { targetId }) };
}
const app = async (c, url = '/', boot = false) => { const p = await tab(c, url, boot); await p.until('window.ensBooted === true', 40000); await sleep(400); return p; };
// The chat the app shows for a room: { room, landed, thread } of its frame(s).
// What a chat frame holds (its page-level constants are not window properties).
const FV = `(f => { try { return f.contentWindow.eval("({ room: typeof ROOM === 'string' ? ROOM : '', landed: typeof LANDED !== 'undefined' && LANDED ? LANDED.mid + (LANDED.part ? '|' + LANDED.part : '') : '', threadMode: typeof THREAD_MODE !== 'undefined' && THREAD_MODE, thread: typeof THREAD === 'string' ? THREAD : '', side: typeof TS !== 'undefined' && TS ? TS.tid : '', sideMid: typeof TS !== 'undefined' && TS ? TS.mid || '' : '', tmid: typeof THREAD_MID === 'string' ? THREAD_MID : '', drawn: typeof LAST_ITEMS !== 'undefined' && !!LAST_ITEMS })"); } catch (e) { return { err: String(e) }; } })`;
// Visible frames, and the thread frame a chat holds inside it (a phone's thread is there).
const FRAMES = room => `(() => { const vis = f => { const r = f.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const top = [...document.querySelectorAll('iframe')].filter(vis);
  const inner = top.flatMap(f => { try { return [...f.contentDocument.querySelectorAll('iframe')].filter(vis); } catch (e) { return []; } });
  return [...top, ...inner].map(${FV}).filter(x => x.room === ${JSON.stringify(room)}); })()`;
const OVER = `document.documentElement.scrollWidth - innerWidth`;
// The Inbox as the page has it after a fresh poll: its row ids and the bar's count.
const INBOX = `(async () => { const g = IBX.data && IBX.data.generatedAt; const t = Date.now(); while (IBX.busy && Date.now() - t < 8000) await new Promise(r => setTimeout(r, 50)); ibxPoll();
  while (Date.now() - t < 9000) { await new Promise(r => setTimeout(r, 80)); if (IBX.data && IBX.data.generatedAt !== g && !IBX.busy) break; }
  return { ids: ((IBX.data && IBX.data.rows) || []).map(r => r.id).sort(), pending: !!(IBX.data && IBX.data.pending), count: document.getElementById('inbox-btn').lastChild.textContent.trim(), over: ${OVER} }; })()`;
async function inboxUntil(p, pred, ms = 25000) {
  const t = Date.now(); let got = null;
  while (Date.now() - t < ms) { got = await p.ev(INBOX); if (!got.pending && pred(got.ids)) return got; await sleep(300); }
  got.timeout = true; return got;
}
const has = (ids, room, kind) => ids.some(i => i.includes(room) && (!kind || i.startsWith(kind + ':')));
const o = { links: {}, steps: {}, errors: [] };
async function main() {
  hub = await startHub();
  A.base = () => 'http://127.0.0.1:' + hub.port;
  const I = hub.ids;
  o.ids = I;
  let b = await launch();
  try {
    // ================= transitions
    const S = o.steps;
    const main = await app(b.c, '/');
    S.start = await inboxUntil(main, ids => has(ids, I.po, 'check') && has(ids, I.po, 'answer') && ['A1', 'A2', 'A3', 'A6', 'A7'].every(k => has(ids, I[k])));
    // Reading P1's answer: a real click on its Inbox row.
    // A phone (#311): on a PO's screen the Inbox is the tab beside Chat; elsewhere the bar's Inbox opens #307's pane.
    const IN_TAB = `document.body.classList.contains('po-dock') && !!PD.dock && !pdTask()`;
    const phoneInbox = async () => {
      if (await main.ev(IN_TAB)) { await main.click(`document.querySelector('#po-dock .dk-tab[data-dk-tab="inbox"]')`); await main.until(`PD.dock.isShown('inbox')`); }
      else { await main.click(`document.getElementById('inbox-btn')`); await main.until(`PH_PANE === 'inbox'`); }
    };
    if (MOB) await phoneInbox();
    else { await main.click(`document.getElementById('inbox-btn')`); await main.until(`!!PD.dock && PD.dock.isShown('inbox')`); }
    await sleep(500);
    const box = MOB ? `(PH_PANE === 'inbox' ? document.getElementById('ph-inbox-body') : PD.els.inbox)` : `PD.els.inbox`;
    // All projects' rows (Alpha's beside Motors'): its own control.
    // (No project open yet, as on a phone at first: no control, every row shown.)
    if (await main.ev(`!!${box}.querySelector('[data-ibx-scope="all"]')`)) {
      await main.click(`${box}.querySelector('[data-ibx-scope="all"]')`);
      S.scope = await main.until(`IBX.scope === 'all' ? 'all' : ''`, 5000).catch(e => ({ fail: e.message }));
    } else S.scope = await main.ev(`SELECTED_PROJECT ? 'scoped without a control' : 'all'`);
    await main.until(`!!${box}.querySelector('a.ibx-link[data-mid="${I.answer}"]')`).catch(async e => { S.inboxLinks = await main.ev(`[...${box}.querySelectorAll('a')].map(a => a.className + ' ' + JSON.stringify(a.dataset))`); throw e; });
    await main.click(`${box}.querySelector('a.ibx-link[data-mid="${I.answer}"]')`);
    S.readLanded = await main.until(`(() => { const f = ${FRAMES(I.po)}.find(x => x.landed); return f ? f.landed : ''; })()`, 30000).catch(e => ({ fail: e.message }));
    S.read = await inboxUntil(main, ids => !has(ids, I.po, 'check'));
    // The PO's card: its button.
    await main.ev(`openMsgLink(${JSON.stringify(I.po)}, ${JSON.stringify(I.poAsk)}, 'ask:0'); 0`);
    const card = `(() => { const f = [...document.querySelectorAll('iframe')].find(f => { const v = (${FV})(f); return v.room === ${JSON.stringify(I.po)} && !v.threadMode; }); return f && f.contentDocument.querySelector('.qa-opt[data-ask="${I.poAsk}:0"][data-opt="Ship"]'); })()`;
    await main.until(`!!${card} && !${card}.disabled`, 30000);
    await main.ev(`${card}.click(); 0`);
    S.card = await inboxUntil(main, ids => !has(ids, I.po, 'answer'));
    S.cardOver = await main.ev(OVER);
    // A1: a typed reply in its chat (a tab of its own).
    let t = await tab(b.c, `/session?room=${I.A1}&stay=1`);
    await t.until(`!!document.querySelector('#input') && !document.querySelector('#input').disabled && !!LAST_ITEMS`, 30000);
    await t.ev(`(() => { const i = document.querySelector('#input'); i.focus(); i.value = 'Replace them, thanks.'; i.dispatchEvent(new Event('input', { bubbles: true })); return 0; })()`);
    await t.until(`!document.getElementById('send').disabled`);
    await t.click(`document.getElementById('send')`);
    S.typedSent = await t.until(`(LAST_ITEMS || []).some(m => m && m.from === 'user' && /Replace them, thanks/.test(m.text || ''))`, 20000).catch(e => ({ fail: e.message }));
    await t.close();
    S.typed = await inboxUntil(main, ids => !has(ids, I.A1));
    // A2: a thumbs-up on its card.
    t = await tab(b.c, `/session?room=${I.A2}&stay=1`);
    await t.until(`!!LAST_ITEMS && LAST_ITEMS.some(m => m && m.id === ${JSON.stringify(I.A2ask)})`, 30000);
    await t.ev(`selReact(SelBar.REACTIONS[0], { mid: ${JSON.stringify(I.A2ask)}, from: 'claude', start: 5, end: 14, quote: 'Which way' }); 0`);
    S.thumbSent = await t.until(`(LAST_ITEMS || []).some(m => m && m.from === 'user' && /👍/.test(m.text || ''))`, 20000).catch(e => ({ fail: e.message }));
    await t.close();
    S.thumb = await inboxUntil(main, ids => !has(ids, I.A2));
    // A3: Done with this.
    await main.ev(`swDone({ dataset: { room: ${JSON.stringify(I.A3)} }, disabled: false })`);
    S.done = await inboxUntil(main, ids => !has(ids, I.A3));
    // A4 reports it finished; opening it reads it.
    await py(`out = report(ids["A4"], "completed", "The saddle is at 74 cm.", ids["Alpha"])`);
    S.reported = await inboxUntil(main, ids => has(ids, I.A4, 'check'));
    if (MOB) { if (await main.ev(`PH_PANE !== 'inbox' && !(${IN_TAB} && PD.dock.isShown('inbox'))`)) await phoneInbox(); }
    else await main.ev(`pdReveal('inbox'); 0`);
    // On a phone the open project now scopes the Inbox: back to All.
    if (await main.ev(`IBX.scope !== 'all' && !!${box}.querySelector('[data-ibx-scope="all"]')`)) {
      await main.click(`${box}.querySelector('[data-ibx-scope="all"]')`);
      await main.until(`IBX.scope === 'all'`, 5000).catch(async e => { throw new Error(e.message + ' :: ' + await main.ev(`(() => { const b = ${box}.querySelector('[data-ibx-scope="all"]'), r = b.getBoundingClientRect(), t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return JSON.stringify({ r: [r.left, r.top, r.width, r.height], hit: t && (t.tagName + '.' + t.className), H: innerHeight }); })()`)); });
    }
    await main.until(`!!${box}.querySelector('a.ibx-link[data-room="${I.A4}"]')`, 15000).catch(async e => { throw new Error(e.message + ' :: ' + await main.ev(`JSON.stringify({ scope: IBX.scope, pane: PH_PANE, tab: ${IN_TAB}, vis: !!PD.dock && PD.dock.isShown('inbox'), proj: SELECTED_PROJECT, links: [...${box}.querySelectorAll('a.ibx-link')].map(a => a.dataset.room), html: ${box}.innerHTML.slice(0, 600) })`)); });
    await main.click(`${box}.querySelector('a.ibx-link[data-room="${I.A4}"]')`);
    S.reportOpened = await main.until(`${FRAMES(I.A4)}.some(x => x.drawn)`, 30000).catch(e => ({ fail: e.message }));
    S.reportRead = await inboxUntil(main, ids => !has(ids, I.A4));
    // A5's finished report leaves when A5 is stopped.
    await py(`out = report(ids["A5"], "completed", "The bell is quieter.", ids["Alpha"])`);
    S.reported5 = await inboxUntil(main, ids => has(ids, I.A5, 'check'));
    await py(`out = dashboard.stop_task(ids["A5"])`);
    S.stopped = await inboxUntil(main, ids => !has(ids, I.A5));
    // A8 asks in a report, then reports it finished.
    await py(`out = report(ids["A8"], "question", "Thirty-two spokes or thirty-six?", ids["Alpha"])`);
    S.question = await inboxUntil(main, ids => has(ids, I.A8, 'answer'));
    await py(`out = report(ids["A8"], "completed", "Thirty-six spokes, as the rim wants.", ids["Alpha"])`);
    S.questionOver = await inboxUntil(main, ids => !has(ids, I.A8, 'answer') && has(ids, I.A8, 'check'));
    // M1's question goes to its PO, not to the person.
    await py(`out = report(ids["M1"], "question", "Is the noise the second gear?", ids["Motors"])`);
    await sleep(1200);
    S.toPo = await inboxUntil(main, () => true);
    S.toPoReport = await py(`r = chatroom.get_room(ids["po"], public=False); out = [m["text"][:60] for m in r["messages"] if m.get("kind") == "report" and m.get("taskId") == ids["M1"]]`);
    // Clear all on A6 and A7, and Undo.
    await main.ev(`(async () => { SW_CLEAR_KEYS = [${JSON.stringify(I.A6)}, ${JSON.stringify(I.A7)}]; await swClearAll(); return 0; })()`);
    S.cleared = await inboxUntil(main, ids => !has(ids, I.A6) && !has(ids, I.A7));
    S.clearPhase = await main.ev(`SW_CLEAR && SW_CLEAR.phase`);
    await main.ev(`swClearUndo()`);
    S.undone = await inboxUntil(main, ids => has(ids, I.A6) && has(ids, I.A7));
    S.over = await main.ev(OVER);
    await main.close();
    // ================= links (after the steps: opening P1's answer reads it)
    await sleep(1500);
    // Finish the hub's deferred point bookkeeping before the view-only
    // snapshot. A person's earlier typed reply can acquire its transcript
    // message id when the room is first read; that is not a link-side write.
    await py(`out = [points.sync(ids[k], force=True) for k in ["po", "A1", "A2", "A3"]]`);
    const snap0 = await py(`out = snapshot()`);
    await sleep(500);
    const snapPre = await py(`out = snapshot()`);
    o.links.preWrote = [...new Set([...Object.keys(snap0), ...Object.keys(snapPre)])].filter(k => snap0[k] !== snapPre[k]).sort();
    let p = await app(b.c, `/?room=${I.po}&msg=${I.answer}`, true);
    o.links.poMsg = await p.until(`(() => { const f = ${FRAMES(I.po)}.find(x => x.landed); return f ? f : null; })()`, 30000).catch(async e => ({ fail: e.message, frames: await p.ev(FRAMES(I.po)) }));
    o.links.poMsgUrl = await p.ev('location.pathname + location.search');
    // In the app's frames the chat is the app's: no icon back to it.
    o.links.framedIcons = await p.ev(`[...document.querySelectorAll('iframe')].flatMap(f => { try { return ['to-app', 'ts-to-app'].map(id => f.contentDocument.getElementById(id)).filter(a => a && (!a.hidden || a.getClientRects().length)).map(a => a.id); } catch (e) { return []; } })`);
    await p.close();
    p = await app(b.c, `/?room=${I.A1}&msg=${I.A1ask}&part=ask:0`, true);
    o.links.taskCard = await p.until(`(() => { const f = ${FRAMES(I.A1)}.find(x => x.landed); return f ? f : null; })()`, 30000).catch(async e => ({ fail: e.message, frames: await p.ev(FRAMES(I.A1)) }));
    await p.close();
    p = await app(b.c, `/?room=${I.po}&thread=P1`, true);
    o.links.thread = await p.until(`${FRAMES(I.po)}.some(x => (x.threadMode && x.thread === 'P1') || x.side === 'P1') ? 'P1' : ''`, 30000).catch(async e => ({ fail: e.message, frames: await p.ev(FRAMES(I.po)) }));
    await p.close();
    // A thread started on a balloon (tmid), with another message to land on (msg).
    p = await app(b.c, `/?room=${I.po}&msg=${I.answer}&thread=&tmid=po-sid:0`, true);
    o.links.tmid = await p.until(`(() => { const x = ${FRAMES(I.po)}.find(x => ((x.threadMode && x.tmid === 'po-sid:0') || x.sideMid === 'po-sid:0') && x.landed === '${I.answer}'); return x || null; })()`, 30000).catch(async e => ({ fail: e.message, frames: await p.ev(FRAMES(I.po)) }));
    await p.close();
    p = await tab(b.c, '/?room=room-0000dead&msg=x1');
    o.links.unknown = await p.until(`location.pathname === '/session' ? location.pathname + location.search : ''`, 30000).catch(e => ({ fail: e.message }));
    await p.until('typeof ROOM !== "undefined"', 20000).catch(() => null);
    o.links.unknownToApp = await p.ev(`(() => { const a = document.getElementById('to-app'); return a ? !a.hidden : null; })()`);
    await p.close();
    p = await app(b.c, '/#needs', true);
    o.links.needs = await p.until(MOB ? `PH_PANE === 'inbox' && !document.getElementById('ph-inbox').hidden ? { pane: PH_PANE }
        : document.body.classList.contains('po-dock') && PD.dock && PD.dock.isShown('inbox') ? { tab: 'inbox' } : null`
      : `document.body.classList.contains('po-dock') && PD.dock && PD.dock.isShown('inbox') ? { po: PD.pid } : null`, 20000).catch(async e => ({ fail: e.message, body: await p.ev(`document.body.className`) }));
    o.links.needsOver = await p.ev(OVER);
    await p.close();
    p = await tab(b.c, `/session?room=${I.po}&msg=${I.answer}`);
    await p.until(`(() => { const a = document.getElementById('to-app'); return a && !a.hidden; })()`, 20000).catch(() => null);
    // Once it has landed (on a phone the answer's thread opens over the chat,
    // its own head carrying the icon): the icon a tap reaches.
    await p.until(`!!LANDED || (!document.getElementById('thr-side').hidden && !!TS)`, 30000).catch(() => null);
    await sleep(1500);
    const ICON = `[document.getElementById('ts-to-app'), document.getElementById('to-app')].find(a => { if (!a || a.hidden || !a.getClientRects().length) return false; const r = a.getBoundingClientRect(); const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!h && a.contains(h); })`;
    o.links.toApp = await p.ev(`(() => { const a = ${ICON}; if (!a) return { shown: false, over: (h => h ? h.tagName + '#' + h.id : '')(document.elementFromPoint(30, 20)) }; const r = a.getBoundingClientRect(); a.dispatchEvent(new Event('focus')); return { shown: true, id: a.id, href: a.getAttribute('href'), w: Math.round(r.width), h: Math.round(r.height), label: a.getAttribute('aria-label') }; })()`);
    o.links.toAppOver = await p.ev(OVER);
    if (o.links.toApp && o.links.toApp.shown) {
      await p.click(ICON);
      await p.until(`location.pathname === '/' && window.ensBooted === true`, 30000).catch(() => null);
      o.links.toAppLands = await p.until(`(() => { const f = ${FRAMES(I.po)}.find(x => x.landed); return f ? f.landed : ''; })()`, 30000).catch(async e => ({ fail: e.message, at: await p.ev(`location.href`), frames: await p.ev(`[...document.querySelectorAll('iframe')].map(f => (f.getAttribute('src') || '') + ' ' + Math.round(f.getBoundingClientRect().width))`), po: await p.ev(FRAMES(I.po)) }));
    }
    await p.close();

    p = await tab(b.c, `/session?id=${I.po}&embed=1`);
    await p.until('typeof ROOM !== "undefined" && !!LAST_ITEMS', 20000).catch(() => null);
    o.links.embedIcons = await p.ev(`['to-app', 'ts-to-app'].map(id => document.getElementById(id)).filter(a => a && (!a.hidden || a.getClientRects().length)).map(a => a.id)`);
    await p.close();
    // Nothing the links opened wrote anything but read marks.
    await sleep(1500);
    const snap1 = await py(`out = snapshot()`);
    o.links.wrote = [...new Set([...Object.keys(snap0), ...Object.keys(snap1)])].filter(k => snap0[k] !== snap1[k]).sort();
    o.links.files = Object.keys(snap1).length;

    // A reload.
    const main2 = await app(b.c, '/');
    const want = (await inboxUntil(main2, () => true)).ids;
    await main2.ev(`location.reload(); 0`); await sleep(800);
    await main2.until('window.ensBooted === true', 40000);
    S.reload = await inboxUntil(main2, ids => ids.length === want.length);
    await main2.close();
    // Another browser.
    await b.close();
    o.errors.push(...b.c.errors);
    b = await launch();
    let q = await app(b.c, '/');
    S.otherBrowser = await inboxUntil(q, ids => ids.length === want.length);
    await q.close();
    // A hub restart: a new process on the same state.
    await stopHub();
    hub = await startHub();
    q = await app(b.c, '/');
    S.restarted = await inboxUntil(q, ids => ids.length === want.length);
    S.restartedOver = await q.ev(OVER);
    await q.close();
    S.want = want;
  } finally {
    o.errors.push(...b.c.errors);
    await b.close();
    try { await stopHub(); } catch (e) {}
  }
  if (A.shots) fs.writeFileSync(path.join(A.shots, `links-states-${W}.json`), JSON.stringify(o, null, 1));
  console.log(JSON.stringify(o));
}
main().catch(e => { console.error(JSON.stringify(o).slice(0, 6000)); console.error(e && e.stack || e); try { hub && hub.p.kill(); } catch (x) {} process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class LinksAndStatesInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.got = {}
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        for size in SIZES:
            # A short home folder: the hub's state paths stay under Windows' 260.
            tmp = tempfile.mkdtemp(prefix="e3")
            try:
                home = Path(tmp) / "h"
                state = Path(tmp) / "s"
                home.mkdir()
                state.mkdir()
                hub_py = Path(tmp) / "hub.py"
                hub_py.write_text(HUB_PY, encoding="utf-8")
                script = Path(tmp) / "links_states_cdp.js"
                script.write_text(CDP_JS, encoding="utf-8")
                args = {**chrome_profile.node_args(), "tmp": tmp, "home": str(home), "state": str(state),
                        "hubPy": str(hub_py), "repo": str(ROOT), "py": sys.executable, "size": size, "shots": shots}
                out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
                assert out.returncode == 0, out.stderr[-5000:]
                cls.got[size[0]] = json.loads(out.stdout.strip().splitlines()[-1])
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def each(self):
        for w, _h, mob in SIZES:
            yield w, mob, self.got[w]

    def test_old_and_new_links_land(self):
        for w, mob, g in self.each():
            L, I = g["links"], g["ids"]
            with self.subTest(w=w):
                self.assertEqual((L["poMsg"] or {}).get("landed"), I["answer"], L["poMsg"])
                self.assertEqual(L["poMsgUrl"], "/", "the app's own address once it opened")
                self.assertEqual((L["taskCard"] or {}).get("landed"), I["A1ask"] + "|ask:0", L["taskCard"])
                self.assertEqual(L["thread"], "P1")
                self.assertIsNotNone(L["tmid"], "tmid link did not land")
                self.assertEqual(L["tmid"].get("tmid") or L["tmid"].get("sideMid"), "po-sid:0", L["tmid"])
                self.assertEqual(L["tmid"].get("landed"), I["answer"], "the msg lands inside the tmid thread")
                self.assertEqual(L["unknown"], "/session?room=room-0000dead&msg=x1&stay=1")
                self.assertFalse(L["unknownToApp"], "no way back to an app that does not list it")
                self.assertNotIn("fail", L["needs"] or {"fail": "none"}, L["needs"])
                self.assertLessEqual(L["needsOver"], 1)
                self.assertEqual(L["framedIcons"], [], "a chat in the app's frame has no icon back to it")
                self.assertEqual(L["embedIcons"], [])

    def test_opening_links_writes_nothing_but_read_marks(self):
        for w, mob, g in self.each():
            L = g["links"]
            with self.subTest(w=w):
                self.assertGreater(L["files"], 10)
                self.assertEqual(L["preWrote"], [])
                self.assertEqual(L["wrote"], [])

    def test_a_chat_in_its_own_tab_opens_in_the_app(self):
        for w, mob, g in self.each():
            L, I = g["links"], g["ids"]
            with self.subTest(w=w):
                self.assertTrue(L["toApp"]["shown"], L["toApp"])
                # The answer's thread is open beside the chat (over it on a phone): the link names it too.
                want = f"/?room={I['po']}&msg={I['answer']}&thread=P1"
                self.assertEqual(L["toApp"]["href"], want, L["toApp"])
                self.assertEqual(L["toApp"]["id"], "ts-to-app" if mob else "to-app")
                self.assertEqual(L["toApp"]["label"], "Open in Ensemble")
                self.assertGreaterEqual(min(L["toApp"]["w"], L["toApp"]["h"]), 44 if mob else 32)
                self.assertLessEqual(L["toAppOver"], 1)
                self.assertEqual(L["toAppLands"], I["answer"])

    def test_reading_and_answering_take_rows_off(self):
        for w, mob, g in self.each():
            S, I = g["steps"], g["ids"]
            with self.subTest(w=w):
                self.assertNotIn("timeout", S["start"], S["start"])
                self.assertEqual(S["scope"], "all")
                self.assertEqual(S["readLanded"], I["answer"])
                self.assertNotIn("timeout", S["read"], S["read"])
                self.assertNotIn("timeout", S["card"], S["card"])
                self.assertIs(S["typedSent"], True)
                self.assertNotIn("timeout", S["typed"], S["typed"])
                self.assertIs(S["thumbSent"], True)
                self.assertNotIn("timeout", S["thumb"], S["thumb"])
                self.assertNotIn("timeout", S["done"], S["done"])
                # The count is the rows'.
                for k in ("start", "read", "card", "typed", "thumb", "done"):
                    self.assertEqual(S[k]["count"], str(len(S[k]["ids"])) if S[k]["ids"] else "", (k, S[k]))
                self.assertEqual(len(S["start"]["ids"]) - len(S["done"]["ids"]), 5, "one row each, nothing else moved")

    def test_reports_and_stops_move_rows(self):
        for w, mob, g in self.each():
            S, I = g["steps"], g["ids"]
            with self.subTest(w=w):
                for k in ("reported", "reportRead", "reported5", "stopped", "question", "questionOver"):
                    self.assertNotIn("timeout", S[k], (k, S[k]))
                self.assertIs(S["reportOpened"], True)
                self.assertFalse(any(I["M1"] in i for i in S["toPo"]["ids"]), "a question for the PO is not the person's")
                self.assertEqual(len(S["toPoReport"]), 1, "it is in the PO's chat")

    def test_clear_all_and_undo_round_trip(self):
        for w, mob, g in self.each():
            S = g["steps"]
            with self.subTest(w=w):
                self.assertNotIn("timeout", S["cleared"], S["cleared"])
                self.assertEqual(S["clearPhase"], "undo")
                self.assertNotIn("timeout", S["undone"], S["undone"])
                self.assertEqual(S["undone"]["count"], str(len(S["undone"]["ids"])))

    def test_reload_other_browser_and_restart_hold(self):
        for w, mob, g in self.each():
            S = g["steps"]
            with self.subTest(w=w):
                for k in ("reload", "otherBrowser", "restarted"):
                    self.assertEqual(S[k]["ids"], S["want"], k)
                    self.assertEqual(S[k]["count"], str(len(S["want"])) if S["want"] else "", k)

    def test_nothing_wider_than_the_screen_and_no_page_errors(self):
        for w, mob, g in self.each():
            S = g["steps"]
            with self.subTest(w=w):
                for k in ("cardOver", "over", "restartedOver"):
                    self.assertLessEqual(S[k], 1, k)
                self.assertEqual(g["errors"], [])


if __name__ == "__main__":
    unittest.main()
