"""A message opened from the Inbox is clearly highlighted (#343, P316/P317), in
headless Chrome over CDP, against a hub in a thread serving the pages.

A busy PO chat (an answer to check, an ask card, long answers with code) and a
busy task chat (a task's ask, folded team talk). Checked, at 1440x900 and on a
390x844 phone:

* a real Inbox click on the answer: at about 0.3 s the whole message has the
  Inbox highlight (the accent ring), the FROM THE INBOX label after its time,
  aria-current="location" and the focus; its top sits 72px (48 on the phone)
  under the chat's top, wholly inside the chat, nothing over it; still marked
  at about 2 s, gone after the setting's 3.5 s;
* a second Inbox click, on the ask card's row, moves the highlight: the first
  message is unmarked, the ask's whole message (its card in it) is marked;
* an Inbox link to a task's ask (the task panel) and to a folded task message;
* session.html?…&part=inbox opened by itself;
* Settings › Preferences › Inbox highlight set to 1.5 in its field: the hub
  keeps 1.5, the next landing is present at 0.3 s and gone after 1.5 s;
* the hub clamps the setting to 1–30 s;
* the pages threw nothing.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// The chat document holding balloon `mid` (a frame of the dashboard, or the page itself).
const DOC = mid => `[...document.querySelectorAll('iframe')].map(f => { try { return f.getBoundingClientRect().width > 0 && f.contentDocument; } catch (e) { return null; } })
  .concat([document]).find(d => d && d.getElementById('msgs') && [...d.querySelectorAll('.msg[data-mid]')].some(e => e.dataset.mid === ${JSON.stringify(mid)}))`;
// Where balloon `mid` is and how it is marked.
const MEASURE = mid => `(() => {
  const d = ${DOC(mid)};
  if (!d) return null;
  const w = d.defaultView, box = d.getElementById('msgs'), el = [...d.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
  const br = box.getBoundingClientRect(), b = el.getBoundingClientRect(), cs = w.getComputedStyle(el);
  const probe = d.createElement('i'); probe.style.color = 'var(--accent)'; d.body.appendChild(probe); const accent = w.getComputedStyle(probe).color; probe.remove();
  const hit = y => { const t = d.elementFromPoint(b.left + b.width / 2, y); return !!t && (t === el || el.contains(t)); };
  const L = w.eval('LANDED'), lab = el.querySelector('.from .lz-inbox'), at = el.querySelector('.from .at');
  return { marked: el.classList.contains('ibx-landed'), label: lab ? lab.textContent.trim() : '', labelAfterTime: !!lab && !!at && at.nextElementSibling === lab,
    current: el.getAttribute('aria-current') || '', focus: d.activeElement === el, active: d.activeElement ? d.activeElement.tagName + '#' + d.activeElement.id + '.' + String(d.activeElement.className).slice(0, 40) : '', accent, shadow: cs.boxShadow,
    ring: cs.boxShadow.includes(accent), gap: Math.round(b.top - br.top), inside: b.top >= br.top - 1 && b.bottom <= br.bottom + 1,
    hitTop: hit(b.top + 4), hitBottom: hit(b.bottom - 4), card: !!el.querySelector('.qa[data-ask]'),
    otherMarked: [...d.querySelectorAll('.msg.ibx-landed, .msg[aria-current], .lz-inbox')].filter(e => e !== el && !el.contains(e)).length,
    age: L && L.mid === ${JSON.stringify(mid)} ? Date.now() - L.at : -1, ms: L ? L.ms || 0 : 0, landedPart: L ? L.part : null, plainLanded: !!d.querySelector('.landed') };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile, url) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(50); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + (url || '/') }, sessionId);
    return { evalIn, until, click, shot, sid: async () => sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  // A landing on `mid`, measured at about 0.3 s, about 2 s (when the length allows) and after it.
  const series = async (p, name, mid, ms, go) => {
    const r = { mid };
    await go();
    await p.until(`(() => { const m = ${MEASURE(mid)}; return !!m && m.age >= 0; })()`, 15000).catch(() => null);
    const waitAge = async a => { const m = await p.evalIn(MEASURE(mid)); if (m && m.age >= 0 && m.age < a) await sleep(a - m.age); };
    await waitAge(300); r.early = await p.evalIn(MEASURE(mid)); await p.shot(name + '-0.3s');
    if (ms > 2600) { await waitAge(2000); r.mid2s = await p.evalIn(MEASURE(mid)); }
    await sleep(Math.max(0, ms + 400 - Math.max(r.early ? r.early.age : 0, r.mid2s ? r.mid2s.age : 0)));
    r.after = await p.evalIn(MEASURE(mid)); await p.shot(name + '-after');
    return r;
  };
  const link = mid => `[...document.querySelectorAll('a.ibx-link[data-mid]')].filter(a => a.getBoundingClientRect().width > 0).find(a => a.dataset.mid === ${JSON.stringify(mid)})`;
  const chatReady = (p, room) => p.until(`(() => { const f = [...document.querySelectorAll('iframe.po-session, iframe.dp-session')].find(f => f.dataset.room === ${JSON.stringify(room)}); return !!(f && f.contentWindow && f.contentWindow.eval('typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN && IBX_HL_READ')); })()`, 30000);
  try {
    for (const [w, h, mobile] of [[1440, 900, false], [390, 844, true]]) {
      const tag = mobile ? 'phone' : 'desk', o = out[tag] = {};
      const p = await page(w, h, mobile);
      await p.until('typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ') && window.ensBooted === true', 30000);
      await p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
      await chatReady(p, A.po);
      const inbox = async () => { if (mobile) { await p.evalIn('ibxOpen(); 0'); await sleep(400); } await p.until(`!!(${link(A.answer)})`, 20000); };
      await inbox();
      o.hrefs = await p.evalIn(`[${link(A.answer)}, ${link(A.ask)}].map(a => a && a.getAttribute('href'))`);
      o.inboxRowMarked = await p.evalIn(`[...document.querySelectorAll('.ibx-row')].some(r => /landed|current/.test(r.className) || r.hasAttribute('aria-current'))`);
      // (a) the answer, from a real Inbox click
      o.answer = await series(p, tag + '-answer', A.answer, 3500, () => p.click(link(A.answer)));
      o.inboxRowMarkedAfter = await p.evalIn(`[...document.querySelectorAll('.ibx-row')].some(r => /landed|current/.test(r.className) || r.hasAttribute('aria-current'))`);
      // (g) a second click while the first is marked moves it; (c) the ask card's row marks its whole message
      await inbox();
      await p.click(link(A.answer));
      await p.until(`(() => { const m = ${MEASURE(A.answer)}; return !!m && m.marked; })()`, 15000).catch(() => null);
      await sleep(600);
      if (mobile) await inbox();
      o.ask = await series(p, tag + '-ask', A.ask, 3500, () => p.click(link(A.ask)));
      o.askOld = await p.evalIn(MEASURE(A.answer));
      // (e) the task panel, as the Inbox opens a task's ask; (d) a folded task message
      o.task = await series(p, tag + '-task', A.taskAsk, 3500, () => p.evalIn(`openMsgLink(${JSON.stringify(A.task)}, ${JSON.stringify(A.taskAsk)}, 'inbox,ask:0'); 0`));
      o.foldedBefore = await p.evalIn(`(() => { const d = ${DOC(A.taskAsk)}; return !!d && !!d.querySelector('.msg-fold[data-mid="${A.folded}"]'); })()`);
      o.folded = await series(p, tag + '-folded', A.folded, 3500, () => p.evalIn(`openMsgLink(${JSON.stringify(A.task)}, ${JSON.stringify(A.folded)}, 'inbox'); 0`));
      if (!mobile) {
        // (h) the setting, typed in Settings › Preferences
        await p.evalIn(`openSettings('prefs'); 0`);
        await p.until(`document.getElementById('pref-inbox-hl').getClientRects().length > 0`, 10000);
        await p.click(`document.getElementById('pref-inbox-hl')`);
        await p.evalIn(`(() => { const i = document.getElementById('pref-inbox-hl'); i.select(); return 0; })()`);
        await c.send('Input.insertText', { text: '1.5' }, await p.sid());
        await p.evalIn(`document.getElementById('pref-inbox-hl').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); 0`);
        await p.until('PREFS && PREFS.inboxHighlightSec === 1.5', 10000).catch(() => null);
        await p.shot('desk-setting');
        o.setting = await p.evalIn(`(async () => ({ prefs: PREFS.inboxHighlightSec, field: document.getElementById('pref-inbox-hl').value,
          hub: (await (await fetch('/api/settings', { cache: 'no-store' })).json()).inboxHighlightSec,
          open: !document.getElementById('settings-panel').hidden,
          row: (() => { const i = document.getElementById('pref-inbox-hl'), r = i.closest('.pref-row'), h = r && r.previousElementSibling;
            return { label: r ? r.querySelector('label').textContent.trim() : '', unit: r ? r.textContent.includes('seconds') : false, head: h ? h.textContent.trim() : '', min: i.min, max: i.max }; })() }))()`);
        await p.evalIn(`closeSettings(); 0`);
        await p.evalIn(`(() => { const b = (${DOC(A.answer)}).getElementById('msgs'); b.scrollTop = b.scrollHeight; return 0; })()`);
        await p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
        await inbox();
        o.short = await series(p, tag + '-short', A.answer, 1500, () => p.click(link(A.answer)));
        await p.evalIn(`fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ inboxHighlightSec: 3.5 }) }).then(r => r.json()).then(s => { PREFS = s; return 0; })`);
      }
      await p.close();
      // (f) session.html by itself, as a new tab from the Inbox opens it
      const s = await page(w, h, mobile, `/session?room=${A.po}&msg=${A.answer}&part=inbox`);
      o.solo = await series(s, tag + '-solo', A.answer, 3500, async () => {});
      await s.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

CODE = """```python
def land(box, el, gap=72):
    top = el.offsetTop - gap
    box.scrollTop = max(0, top)
    return top
```"""
LONG = "\n\n".join(["The column now keeps its width while the board scrolls. The header stays put, "
                    "the cards under it move, and a resize keeps the place you were reading."] * 2)


def _mid(m):
    return ((m or {}).get("message") or {}).get("id", "")


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class InboxHighlightInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibxhl-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        ok, proj, _ = dashboard.register_project("Atlas")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("Atlas PO", members(home))
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        R = po["id"]
        text, _ = points.take(chatroom.get_room(R, public=False), "Make the board columns wider", to="claude", key="k1")
        chatroom.post_message(R, "user", text, to="claude")
        for i in range(5):
            chatroom.post_message(R, "claude", f"Step {i + 1} of the column work.\n\n{LONG}\n\n{CODE}", to="user")
        answer = _mid(chatroom.post_message(R, "claude", "Re P1: the columns are wider now, please try it on the board.\n\n" + LONG, to="user"))
        for i in range(8):
            chatroom.post_message(R, "codex", f"review note {i}\n\n" + "the diff reads fine " * 20, to="claude")
        for i in range(6):
            chatroom.post_message(R, "claude", f"Card height, part {i + 1}.\n\n{LONG}" + (f"\n\n{CODE}" if i % 2 else ""), to="user")
        ask = _mid(chatroom.post_message(R, "claude", "The cards can grow two ways.\n\n1. **Ask:** Which card height should ship?\n"
                                         "   - Fixed 96px\n   - Grow with the title\n   - Ask me later\n", to="user"))
        for i in range(12):
            chatroom.post_message(R, "claude" if i % 3 else "codex", f"Follow-up {i + 1}.\n\n{LONG}" + (f"\n\n{CODE}" if i % 4 == 0 else ""),
                                  to="user" if i % 3 else "claude")
        points.sync(R, force=True)
        tk = chatroom.create_room("Faster board", members(home))
        dashboard.assign_session_project(tk["id"], proj["id"])
        T = tk["id"]
        chatroom.post_message(T, "user", "Make the board load faster.", to="claude")
        folded = ""
        for i in range(14):
            m = chatroom.post_message(T, "claude" if i % 2 == 0 else "codex", f"Board load, step {i + 1}.\n\n{LONG}",
                                      to="codex" if i % 2 == 0 else "claude")
            if i == 5:
                folded = _mid(m)
        task_ask = _mid(chatroom.post_message(T, "claude", "1. **Ask:** Cache the board for how long?\n   - 10 seconds\n   - 1 minute\n", to="user"))
        for i in range(12):
            chatroom.post_message(T, "claude" if i % 2 == 0 else "codex", f"Board load, follow-up {i + 1}.\n\n{LONG}",
                                  to="codex" if i % 2 == 0 else "claude")
        cls.ids = {"proj": proj["id"], "po": R, "answer": answer, "ask": ask, "task": T, "taskAsk": task_ask, "folded": folded}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), **cls.ids, "tmp": cls.tmp.name, "shots": shots,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        script = base / "inbox_hl_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_DUMP"):
            Path(os.environ["ENSEMBLE_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def present(self, m, gap=None, what=""):
        self.assertIsNotNone(m, what)
        self.assertTrue(m["marked"], (what, m))
        self.assertEqual(m["label"].lower(), "from the inbox", (what, m))
        self.assertTrue(m["labelAfterTime"], (what, m))
        self.assertEqual(m["current"], "location", (what, m))
        self.assertTrue(m["focus"], (what, m))
        self.assertEqual(m["otherMarked"], 0, (what, m))
        self.assertFalse(m["plainLanded"], ("not the old flash", what, m))
        if gap is not None:
            self.assertTrue(m["ring"], ("the accent ring", what, m["shadow"], m["accent"]))
            self.assertLessEqual(abs(m["gap"] - gap), 6, (what, m))
            self.assertTrue(m["inside"] and m["hitTop"] and m["hitBottom"], ("on screen, nothing over it", what, m))

    def gone(self, m, what=""):
        self.assertIsNotNone(m, what)
        self.assertFalse(m["marked"], (what, m))
        self.assertEqual((m["label"], m["current"]), ("", ""), (what, m))
        self.assertEqual(m["otherMarked"], 0, (what, m))
        self.assertFalse(m["ring"], (what, m["shadow"]))

    def each(self):
        return [(self.got[tag], gap, tag) for tag, gap in (("desk", 72), ("phone", 48))]

    def test_an_inbox_click_marks_the_whole_message_near_the_top_then_it_goes(self):
        for o, gap, tag in self.each():
            with self.subTest(tag):
                a = o["answer"]
                self.present(a["early"], gap, "0.3 s")
                self.assertLess(a["early"]["age"], 900, a["early"])
                self.assertEqual(a["early"]["ms"], 3500, "the default")
                self.assertEqual(a["early"]["landedPart"], "", "LANDED keeps the bare part")
                self.present(a["mid2s"], None, "2 s")
                self.gone(a["after"], "after 3.5 s")
                self.assertGreaterEqual(a["after"]["age"], -1)

    def test_the_inbox_row_gets_no_mark(self):
        for o, gap, tag in self.each():
            with self.subTest(tag):
                self.assertFalse(o["inboxRowMarked"])
                self.assertFalse(o["inboxRowMarkedAfter"])

    def test_the_inbox_links_say_inbox_for_a_new_tab(self):
        for o, gap, tag in self.each():
            with self.subTest(tag):
                answer, ask = o["hrefs"]
                self.assertTrue(answer.endswith(f"msg={self.ids['answer']}&part=inbox"), answer)
                self.assertTrue(ask.endswith(f"msg={self.ids['ask']}&part=inbox,ask:0"), ask)

    def test_a_second_click_moves_it_and_an_ask_marks_its_whole_message(self):
        for o, gap, tag in self.each():
            with self.subTest(tag):
                a = o["ask"]
                self.present(a["early"], None, "ask 0.3 s")
                self.assertTrue(a["early"]["card"], "the whole message, its card in it")
                self.assertTrue(a["early"]["ring"], a["early"]["shadow"])
                self.assertTrue(a["early"]["inside"] and a["early"]["hitTop"], a["early"])
                self.gone(a["after"], "ask after")
                self.gone(o["askOld"], "the first one, unmarked")

    def test_the_task_panel_and_a_folded_message(self):
        for o, gap, tag in self.each():
            with self.subTest(tag):
                self.present(o["task"]["early"], None, "task ask")
                self.gone(o["task"]["after"], "task ask after")
                self.present(o["folded"]["early"], None, "folded")
                self.assertTrue(o["folded"]["early"]["ring"], o["folded"]["early"])
                self.gone(o["folded"]["after"], "folded after")

    def test_session_page_by_itself(self):
        for o, gap, tag in self.each():
            with self.subTest(tag):
                self.present(o["solo"]["early"], gap, "session.html")
                self.present(o["solo"]["mid2s"], None, "session.html 2 s")
                self.gone(o["solo"]["after"], "session.html after")

    def test_a_changed_setting_sets_the_length(self):
        s = self.got["desk"]["setting"]
        self.assertEqual((s["prefs"], s["hub"]), (1.5, 1.5), s)
        self.assertEqual(float(s["field"]), 1.5, s)
        self.assertEqual(s["row"]["label"], "Inbox highlight")
        self.assertTrue(s["row"]["unit"])
        self.assertEqual((s["row"]["min"], s["row"]["max"]), ("1", "30"))
        o = self.got["desk"]["short"]
        self.present(o["early"], 72, "1.5 s setting, 0.3 s")
        self.assertEqual(o["early"]["ms"], 1500)
        self.gone(o["after"], "after 1.5 s")
        self.assertLess(o["after"]["age"] if o["after"]["age"] >= 0 else 0, 2600)

    def test_the_pages_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


class InboxHighlightSetting(unittest.TestCase):
    def test_clamp(self):
        c = dashboard.clamp_inbox_highlight
        self.assertEqual(c(3.5), 3.5)
        self.assertEqual(c("2"), 2)
        self.assertEqual(c(0), 1)
        self.assertEqual(c(-4), 1)
        self.assertEqual(c(99), 30)
        self.assertEqual(c(2.26), 2.3)
        for bad in (None, "", "x", True, False, float("nan"), float("inf"), [], {}):
            with self.subTest(bad=bad):
                self.assertIsNone(c(bad))

    def test_saved_on_the_hub_clamped_and_defaulted(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "settings.json"
            with mock.patch.object(dashboard, "SETTINGS_FILE", f), mock.patch.object(dashboard, "DASHBOARD_DIR", Path(d)):
                self.assertEqual(dashboard.load_settings()["inboxHighlightSec"], 3.5)
                self.assertEqual(dashboard.save_settings({"inboxHighlightSec": 1.5})["inboxHighlightSec"], 1.5)
                self.assertEqual(dashboard.save_settings({"inboxHighlightSec": 120})["inboxHighlightSec"], 30)
                self.assertEqual(dashboard.save_settings({"inboxHighlightSec": "soon"})["inboxHighlightSec"], 30, "kept")
                self.assertEqual(dashboard.load_settings()["inboxHighlightSec"], 30)


if __name__ == "__main__":
    unittest.main()
