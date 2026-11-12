"""P310: a task reference clicked in a PO conversation opens the task as a
chat panel of its own (#150), and the PO conversation stays in the middle,
where it was.

In headless Chrome over CDP, against a throwaway hub in a thread, with a
project whose PO room names two tasks (by number, and as a Markdown link) and a task
whose chat names the other:

* at 1440x900 and 1728x1117: a click on the plain link in the PO chat opens
  ``chat:<room>``, the PO frame is still the middle's and its scrollTop did not
  change; a second click focuses that panel (no second one); a double click on
  the #N chip, and the TaskCard's title, do the same for the other task; an
  agent-addressed chip (@codex@N) opens the panel at that agent; a reference in a task
  chat still opens in the middle;
* at 390x844 (a phone): the reference opens in the middle, as before.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
from tests import chrome_profile
from tests.test_task_numbers import Hub

NODE = shutil.which("node")
CHROME = chrome_profile.CHROME
LOAD_SESSIONS = dashboard.load_sessions   # Hub stubs it; the page's list needs the rooms

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
// The page's chat panels, the middle and the PO frame.
const STATE = `(() => {
  const d = PD.dock;
  const chats = [...PD.rt.entries()].filter(([, e]) => e.kind === 'chat').map(([id, e]) => {
    const f = e.el.querySelector('iframe.dp-session');
    return { id, room: e.room, front: d.frontOf(id) === id, visible: d.isVisible(id), src: f ? f.getAttribute('src') : '' };
  });
  const po = pdChatFrame();
  return { chats, sid: SELECTED_SID || null, detailOpen: document.body.classList.contains('detail-open'),
    middleRoom: (document.querySelector('#detail-panel iframe.dp-session') || {dataset: {}}).dataset.room || null,
    poMiddle: !!po && PO_PANEL.parentNode === PD.els['po-chat'] && !PO_PANEL.classList.contains('pd-off'),
    poScroll: po ? po.contentDocument.querySelector('#msgs').scrollTop : null, panels: pdPanelsOn() };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile: !!mobile }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until0 = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr.slice(0, 300)); };
    const until = async (expr, ms) => { try { return await until0(expr, ms); } catch (e) { let st = ''; try { st = JSON.stringify(await evalIn(STATE)); } catch (x) {} throw new Error(e.message + ' @ ' + st); } };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: "try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {}" }, sessionId);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensListDocked = true;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.taskA) + ')', 30000);
    // A real click (or double click) at an element inside a frame: its box
    // in the frame plus the frame's in the page.
    const at = async (frameExpr, sel) => evalIn(`(() => { const f = ${frameExpr}; const e = f.contentDocument.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)});
      e.scrollIntoView({ block: 'nearest' }); const fr = f.getBoundingClientRect(), r = e.getBoundingClientRect(); return [fr.left + r.left + Math.min(r.width / 2, 12), fr.top + r.top + r.height / 2]; })()`);
    const press = async ([x, y], n = 1) => {
      for (let i = 1; i <= n; i++) for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: i }, sessionId);
    };
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1, y: 1 }, sessionId);
    return { evalIn, until, shot, at, press, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const PO = 'pdChatFrame()';
  const MID = `document.querySelector('#detail-panel iframe.dp-session')`;
  const drawn = (f, sel) => `(() => { const f = ${f}; try { return !!f && f.contentWindow.eval('typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN') === true && !!f.contentDocument.querySelector(${JSON.stringify(sel)}); } catch (e) { return false; } })()`;
  const LINK = `a.task-link[data-task="${A.taskA}"]:not([data-agent])`, AGENT = `a.task-chip[data-task="${A.taskA}"][data-agent]`, CHIP = `a.task-chip[data-task="${A.taskB}"]`;
  const home = async (p) => {
    await p.evalIn(`(() => { try { ['cd-list-dock', 'cd-list-dock-axis', 'cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
      if (SELECTED_SID) closeTask(); SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    try { await p.until(drawn(PO, LINK), 30000); } catch (e) {
      throw new Error(e.message + ' :: ' + await p.evalIn(`(() => { const f = ${PO}; if (!f) return 'no PO frame'; const d = f.contentDocument;
        return [...d.querySelectorAll('.msg a')].slice(0, 12).map(a => a.outerHTML).join('\\n') + ' | ' + (d.querySelector('.msg .text') || {}).innerHTML; })()`));
    }
    await sleep(600);
  };
  // The PO chat scrolled off its end, so a kept scrollTop means something.
  const scrollUp = (p) => p.until(`(() => { const f = ${PO}, d = f.contentDocument, b = d.querySelector('#msgs'), a = d.querySelector(${JSON.stringify(LINK)});
    if (!b || !a) return 0; b.scrollTop = Math.max(0, a.getBoundingClientRect().top + b.scrollTop - b.clientHeight / 2); return b.scrollTop; })()`, 10000);
  try {
    for (const [w, h] of [[1440, 900], [1728, 1117]]) {
      const k = w + 'x' + h, r = out[k] = {};
      const p = await page(w, h);
      await home(p);
      await p.until('!!PD.dock && pdPanelsOn() && PD.restored', 30000);
      await scrollUp(p); await sleep(400);
      r.before = await p.evalIn(STATE);
      if (w === 1440) await p.shot('p310-before-1440');
      // The plain link: a panel of its own; the PO stays, at its place.
      await p.press(await p.at(PO, LINK));
      await p.until(`PD.rt.has('chat:${A.taskA}')`, 8000); await sleep(1200);
      r.link = await p.evalIn(STATE);
      if (w === 1440) await p.shot('p310-after-1440');
      // Again: the same panel, in front.
      await p.evalIn(`PD.dock.activate('po-chat'); 0`).catch(() => 0);
      await p.press(await p.at(PO, LINK)); await sleep(800);
      r.again = await p.evalIn(STATE);
      // The agent-addressed link: that panel, at the agent.
      await p.press(await p.at(PO, AGENT), 2); await sleep(800);
      r.agent = await p.evalIn(STATE);
      // A double click on B's chip: B's panel.
      await p.press(await p.at(PO, CHIP), 2);
      await p.until(`PD.rt.has('chat:${A.taskB}')`, 8000); await sleep(800);
      r.chip = await p.evalIn(STATE);
      // The card's open action (its title): close B's panel first, then one click on the chip, then the title.
      await p.evalIn(`PD.dock.removePanel('chat:${A.taskB}'); 0`); await sleep(500);
      await p.press(await p.at(PO, CHIP)); await p.until(`!!${PO}.contentDocument.querySelector('.task-card a.tc-title')`, 5000); await sleep(300);
      await p.press(await p.at(PO, '.task-card a.tc-title'));
      await p.until(`PD.rt.has('chat:${A.taskB}')`, 8000); await sleep(800);
      r.card = await p.evalIn(STATE);
      // A task chat's reference: the middle, as before. Task C in the middle, its link to A.
      await p.evalIn(`PD.dock.removePanel('chat:${A.taskA}'); PD.dock.removePanel('chat:${A.taskB}'); openDetail(${JSON.stringify(A.taskC)}); 0`);
      await p.until(drawn(MID, `a.task-link[data-task="${A.taskA}"]`), 30000); await sleep(400);
      await p.press(await p.at(MID, `a.task-link[data-task="${A.taskA}"]`));
      await p.until(`SELECTED_SID && rowBySid(SELECTED_SID).roomId === ${JSON.stringify(A.taskA)}`, 8000); await sleep(600);
      r.task = await p.evalIn(STATE);
      await p.close();
    }
    {
      const r = out.phone = {};
      const p = await page(390, 844, true);
      await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.proj)}; openPoOf(registeredProjects().find(x => x.id === ${JSON.stringify(A.proj)})); return 0; })()`);
      await p.until(drawn(PO, LINK), 30000); await sleep(600);
      r.before = await p.evalIn(STATE);
      await p.evalIn(`(() => { ${PO}.contentDocument.querySelector(${JSON.stringify(LINK)}).click(); return 0; })()`);
      await p.until(`SELECTED_SID && rowBySid(SELECTED_SID).roomId === ${JSON.stringify(A.taskA)}`, 8000); await sleep(600);
      r.after = await p.evalIn(STATE);
      r.after.vw = await p.evalIn('innerWidth');
      await p.shot('p310-phone-390');
      await p.close();
    }
  } finally {
    console.log(JSON.stringify(out));
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "Node and Chrome are required")
class PoTaskRefPanel(Hub):
    @classmethod
    def setUpClass(cls):
        cls.got = None

    def setUp(self):
        super().setUp()
        base = Path(self.tmp.name)
        self.tmp._ignore_cleanup_errors = True
        p = mock.patch.object(dashboard, "load_sessions", LOAD_SESSIONS)
        p.start(); self.addCleanup(p.stop)
        for attr, val in (("load_live", lambda: []), ("_read_agent_session_files", lambda *a, **k: [])):
            p = mock.patch.object(dashboard, attr, val)
            p.start(); self.addCleanup(p.stop)
        for attr, val in (("_agent_peer", lambda h: ""), ("log_message", lambda *a, **k: None)):
            p = mock.patch.object(dashboard.Handler, attr, val)
            p.start(); self.addCleanup(p.stop)
        for attr, val in (("SETTINGS_FILE", base / "settings.json"), ("PROJ_DIR", base / "transcripts"), ("CS_ROOT", base / "cs")):
            p = mock.patch.object(dashboard, attr, val)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")})
        p.start(); self.addCleanup(p.stop)

    def run_page(self):
        pid = self.ot
        team = [{"identity": "claude", "agent": "claude", "role": "Product owner"}, {"identity": "codex", "agent": "codex", "role": "reviewer"}]
        po = chatroom.create_room("PO talk", team)["id"]
        dashboard.assign_session_project(po, pid)
        ok, why = dashboard.set_project_po(pid, po)
        self.assertTrue(ok, why)
        a = self.room("Brakes that squeal", pid, numbered=True)
        b = self.room("Wipers that smear", pid, numbered=True)
        c = self.room("Lights that flicker", pid, numbered=True)
        for rid in (a, b, c):
            chatroom.post_message(rid, "user", "About this task.")
        for n in range(20):
            chatroom.post_message(po, "claude", f"Earlier note {n}.\n\nWith a second paragraph, to make the chat long.", to="user")
        chatroom.post_message(po, "claude", f"Please look at [the brakes](/?task={a}), at #{self.no(b)}, and @codex@{self.no(a)}.", to="user")
        for n in range(8):
            chatroom.post_message(po, "claude", f"Later note {n}.\n\nWith a second paragraph, to make the chat long.", to="user")
        chatroom.post_message(c, "user", f"This depends on [the brakes](/?task={a}).")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *x: None
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": self.tmp.name, "base": f"http://127.0.0.1:{server.server_port}",
                "proj": pid, "po": po, "taskA": a, "taskB": b, "taskC": c, "shots": shots}
        script = Path(self.tmp.name) / "p310_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        self.assertEqual(run.returncode, 0, run.stderr[-5000:] + "\n" + run.stdout[-3000:])
        return json.loads(run.stdout.strip().splitlines()[-1]), a, b, c

    def test_a_reference_in_a_po_chat_opens_a_panel(self):
        got, a, b, c = self.run_page()
        rooms = lambda s: sorted(x["room"] for x in s["chats"])
        for size in ("1440x900", "1728x1117"):
            with self.subTest(size=size):
                r = got[size]
                self.assertTrue(r["before"]["poMiddle"] and r["before"]["panels"], r["before"])
                self.assertGreater(r["before"]["poScroll"], 0)
                # The plain link.
                g = r["link"]
                self.assertEqual(rooms(g), [a], g)
                self.assertTrue(g["chats"][0]["front"] and g["chats"][0]["visible"], g)
                self.assertTrue(g["poMiddle"], "the PO is still the middle's")
                self.assertIsNone(g["sid"], "no task took the middle")
                self.assertLessEqual(abs(g["poScroll"] - r["before"]["poScroll"]), 1, "the PO chat kept its place")
                # Again: the one panel, in front.
                g = r["again"]
                self.assertEqual(rooms(g), [a], "no second panel")
                self.assertTrue(g["chats"][0]["front"])
                self.assertTrue(g["poMiddle"])
                # At the agent.
                g = r["agent"]
                self.assertEqual(rooms(g), [a])
                self.assertIn("&agent=codex", g["chats"][0]["src"])
                # The chip's double click, and the card's title.
                for key in ("chip", "card"):
                    g = r[key]
                    self.assertEqual(rooms(g), sorted([a, b]), (key, g))
                    self.assertTrue(next(x for x in g["chats"] if x["room"] == b)["front"], key)
                    self.assertTrue(g["poMiddle"], key)
                    self.assertIsNone(g["sid"], key)
                # A task chat's reference: the middle.
                g = r["task"]
                self.assertEqual(g["middleRoom"], a, g)
                self.assertEqual(g["chats"], [], "no panel")
        ph = got["phone"]
        self.assertEqual(ph["after"]["chats"], [], ph)
        self.assertEqual(ph["after"]["middleRoom"], a, ph)
        self.assertTrue(ph["after"]["detailOpen"])


if __name__ == "__main__":
    unittest.main()
