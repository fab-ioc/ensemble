"""CDP coverage for #232: the message box gives the words the room.

At 1728x1117, 1440x900 and 390x844 (touch): the typed text takes at least 70%
of each box's width, the conversation's state is said once (the dashboard's
header for the PO, the pop-out's own header, or the chat's state line when no
header of its room is on screen), a PO room's seats are all called PO (also an
identity from before a switch), a task's seats keep their names, and Send does
not move when the box takes focus or the words wrap. Screenshots go to
$ENSEMBLE_SHOTS when set. Skipped without Node and Chrome.
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
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1728, 1117, False), (1440, 900, False), (390, 844, True)]
WORDS = "so now every option price comes from the new feed? do we still need the old one for anything at all, or can it go this week"

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
// In one document and its same-origin frames: the room's state indicators on
// screen (a header's [data-state-room], a chat's #status), and each box.
const LOOK = room => `(() => {
  const R = el => { const b = el.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) }; };
  const shown = (el, win) => { if (!el) return false; const b = el.getBoundingClientRect(); if (b.width < 1 || b.height < 1) return false;
    if (b.bottom <= 0 || b.right <= 0 || b.top >= win.innerHeight || b.left >= win.innerWidth) return false;
    for (let e = el; e; e = e.parentElement) { const s = win.getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden') return false; }
    const t = win.document.elementFromPoint(Math.min(Math.max(b.left + b.width / 2, 0), win.innerWidth - 1), Math.min(Math.max(b.top + b.height / 2, 0), win.innerHeight - 1));
    return !!t && (t === el || el.contains(t)); };
  const roomOf = win => { try { return win.eval('typeof ROOM === "undefined" ? "" : ROOM'); } catch (e) { return ''; } };
  const out = { states: [], boxes: [] };
  const walk = (win, where, frameOk) => {
    const doc = win.document;
    if (!frameOk) return;
    for (const el of doc.querySelectorAll('[data-state-room]')) if (el.dataset.stateRoom === ${JSON.stringify(room)} && shown(el, win)) out.states.push({ where, text: el.textContent.trim() });
    const st = doc.getElementById('status');
    if (st && roomOf(win) === ${JSON.stringify(room)} && st.classList.contains('on')) {
      const b = st.getBoundingClientRect(); let ok = b.width > 0 && b.height > 0;
      for (let e = st; ok && e; e = e.parentElement) { const s = win.getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden') ok = false; }
      if (ok) out.states.push({ where, text: st.textContent.trim(), title: st.title });
    }
    const c = doc.getElementById('compose');
    if (c && roomOf(win) === ${JSON.stringify(room)} && c.getBoundingClientRect().width > 0) {
      const inp = doc.getElementById('input');
      out.boxes.push({ where, box: R(c), input: R(inp), share: R(inp).w / R(c).w, hint: doc.getElementById('hint').textContent,
        activity: !!doc.getElementById('activity'), placeholder: inp.placeholder,
        whos: [...doc.querySelectorAll('#msgs .msg .who')].map(e => e.textContent.trim()),
        names: win.eval("[whoName('claude'), whoName('claude-2'), whoName('codex'), whoName('codex-2')]") });
    }
    for (const f of doc.querySelectorAll('iframe')) {
      const b = f.getBoundingClientRect(); let w = null;
      try { w = f.contentWindow; if (!w.document.body) w = null; } catch (e) { w = null; }
      if (w) walk(w, where + '>frame', b.width > 0 && b.height > 0 && win.getComputedStyle(f).display !== 'none');
    }
  };
  walk(window, 'top', true);
  return out;
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const res = {};
  try {
    for (const [w, h, mob] of A.sizes) {
      const o = res[String(w)] = {};
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `box-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
      // Send's place while the box takes focus and the words wrap.
      const steady = async (win) => {
        const rect = `(() => { const b = ${win}.document.getElementById('send').getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; })()`;
        const s = { before: await evalIn(rect) };
        await evalIn(`${win}.document.getElementById('input').focus(); 0`);
        await sleep(300);
        s.focused = await evalIn(rect);
        await c.send('Input.insertText', { text: A.words }, sessionId);
        await sleep(400);
        s.typed = await evalIn(rect);
        s.lines = await evalIn(`(() => { const i = ${win}.document.getElementById('input'); return Math.round(i.scrollHeight / parseFloat(${win}.getComputedStyle(i).lineHeight || 20)); })()`);
        s.value = await evalIn(`${win}.document.getElementById('input').value.length`);
        await shot(win.includes('pdChat') ? 'po-typed' : 'pop-typed');
        await evalIn(`(() => { const i = ${win}.document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); i.blur(); return 0; })()`);
        return s;
      };
      // 1. The PO in the dashboard: its header says the state; the chat does not.
      await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')');
      await until('window.ensBooted === true');
      await evalIn(`(() => { try { ['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
        VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
      // A PO's chat opens on its threads: the whole chat shows the balloons.
      await until('(() => { try { return pdChatFrame().contentWindow.eval("!!ROOM_OBJ && THR_DRAWN"); } catch (e) { return false; } })()', 60000);
      await evalIn('pdChatFrame().contentWindow.eval("WHOLE || toggleWhole()"); 0');
      await until('(() => { const f = pdChatFrame(); try { return f.contentDocument.querySelectorAll("#msgs .msg .who").length > 0 && f.contentDocument.getElementById("status").classList.contains("on"); } catch (e) { return false; } })()');
      await sleep(800);
      o.po = await evalIn(LOOK(A.po));
      o.poHead = await evalIn('(() => { const h = document.querySelector(".po-head, #po-panel .po-who"); return h ? h.textContent.replace(/\\s+/g, " ").trim() : ""; })()');
      o.poHeadTip = await evalIn('(() => { const n = document.querySelector(".po-name"); return n ? n.title : ""; })()');
      await shot('po');
      o.poSend = await steady('pdChatFrame().contentWindow');
      // 2. A task popped out: its own header says the state, its seats keep their names.
      await c.send('Page.navigate', { url: A.base + '/session?id=' + A.task }, sessionId);
      await until('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && document.querySelectorAll("#msgs .msg .who").length > 0 && document.getElementById("status").classList.contains("on")');
      await sleep(600);
      o.task = await evalIn(LOOK(A.task));
      o.taskHeaderHasState = await evalIn('!!document.querySelector("header #status.on")');
      await shot('pop-task');
      o.taskSend = await steady('window');
      // 3. The PO's chat in a frame with no header of its room: it says the
      // state in its own line; once a header shows it, it does not; a panel
      // laid over that header gives the line back.
      await evalIn(`(() => { document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'host-frame';
        f.style.cssText = 'position:fixed;left:0;top:60px;width:100vw;height:calc(100vh - 60px);border:0';
        f.src = '/session?id=' + ${JSON.stringify(A.po)} + '&embed=1'; document.body.append(f); return 0; })()`);
      const fr = 'document.getElementById("host-frame").contentWindow';
      await until(`(() => { try { const w = ${fr}; return w.eval('!!ROOM_OBJ') && w.document.getElementById('status').classList.contains('on'); } catch (e) { return false; } })()`);
      await sleep(400);
      const line = `(() => { const w = ${fr}; w.placeState(); const l = w.document.getElementById('state-line'); return { hidden: l.hidden, inLine: l.contains(w.document.getElementById('status')), text: l.textContent.trim() }; })()`;
      o.embedAlone = await evalIn(line);
      await shot('embed-alone');
      await evalIn(`(() => { const s = document.createElement('span'); s.id = 'host-state'; s.dataset.stateRoom = ${JSON.stringify(A.po)}; s.textContent = 'idle';
        s.style.cssText = 'position:fixed;left:8px;top:8px;width:80px;height:20px;z-index:5'; document.body.append(s); return 0; })()`);
      o.embedHosted = await evalIn(line);
      o.embedHostedLook = await evalIn(LOOK(A.po));
      await evalIn(`(() => { const d = document.createElement('div'); d.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:60px;z-index:9;background:#888'; document.body.append(d); return 0; })()`);
      o.embedCovered = await evalIn(line);
      await shot('embed-covered');
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(res));
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _turn(sid: str, role: str, text: str, at: float) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    if role == "user":
        return {"type": "user", "timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class MessageBoxRoom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-box-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        tdir = base / "transcripts" / "C--po"
        tdir.mkdir(parents=True)
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
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        # The PO's seat after a rotation is claude-2; its chat is its transcript.
        sid = "po-box-1"
        po = chatroom.create_room("PO", [{"identity": "claude-2", "agent": "claude", "model": "opus", "role": "Product owner", "sessionId": sid}])
        cls.po = po["id"]
        room = chatroom.get_room(cls.po, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        t0 = time.time() - 3600
        lines = [_turn(sid, "assistant", "I'm the PO for Motors.", t0),
                 _turn(sid, "user", "Make the speed chart wider", t0 + 10),
                 _turn(sid, "assistant", "Done: it takes the whole width now.", t0 + 20)]
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        # A task: an engineer and a reviewer, each called by its own name.
        task = chatroom.create_room("Wider board", [{"identity": "claude", "agent": "claude", "model": "opus", "role": "engineer"},
                                                    {"identity": "claude-2", "agent": "claude", "model": "opus", "role": "reviewer"}])
        cls.task = task["id"]
        dashboard.assign_session_project(cls.task, cls.proj)
        chatroom.post_message(cls.task, "user", "Make the board wider, please.")
        chatroom.post_message(cls.task, "claude", "On it: the board takes the whole width now.", to="user")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "po": cls.po, "task": cls.task, "proj": cls.proj, "sizes": SIZES, "shots": cls.shots, "words": WORDS}
        script = base / "box_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _each(self):
        for w, _h, _m in SIZES:
            yield w, self.got[str(w)]

    def test_the_words_get_the_box(self):
        for w, o in self._each():
            for name in ("po", "task"):
                with self.subTest(width=w, box=name):
                    boxes = o[name]["boxes"]
                    self.assertEqual(len(boxes), 1, boxes)
                    self.assertGreaterEqual(boxes[0]["share"], 0.70, boxes[0])
                    self.assertFalse(boxes[0]["activity"], "no seat chip in the box")
                    self.assertNotIn("resume", boxes[0]["hint"])
                    self.assertNotIn("waiting", boxes[0]["hint"])

    def test_the_state_is_said_once(self):
        for w, o in self._each():
            with self.subTest(width=w, where="po"):
                states = o["po"]["states"]
                self.assertEqual(len(states), 1, states)
                self.assertEqual(states[0]["where"], "top", "the dashboard's header says it, not the chat")
                self.assertIn("stopped", states[0]["text"])
            with self.subTest(width=w, where="task pop-out"):
                states = o["task"]["states"]
                self.assertEqual(len(states), 1, states)
                self.assertTrue(o["taskHeaderHasState"])
                self.assertIn("claude (engineer)", states[0]["title"])
                self.assertIn("claude-2 (reviewer)", states[0]["title"])

    def test_an_embedded_chat_says_it_only_without_a_header(self):
        for w, o in self._each():
            with self.subTest(width=w):
                self.assertEqual(o["embedAlone"], {"hidden": False, "inLine": True, "text": "stopped — reply resumes"})
                self.assertTrue(o["embedHosted"]["hidden"], o["embedHosted"])
                self.assertEqual(len(o["embedHostedLook"]["states"]), 1, o["embedHostedLook"]["states"])
                self.assertFalse(o["embedCovered"]["hidden"], "a panel over the header gives the line back")

    def test_the_po_is_called_po(self):
        for w, o in self._each():
            with self.subTest(width=w):
                box = o["po"]["boxes"][0]
                self.assertEqual(box["names"], ["PO", "PO", "PO", "PO"], "every seat, also before a switch")
                self.assertTrue(box["whos"] and set(box["whos"]) <= {"PO", "sam"}, box["whos"])
                self.assertIn("Message the PO", box["placeholder"])
                self.assertIn("Motors · PO", o["poHead"])
                self.assertNotIn("claude", o["poHead"].replace("Motors · PO", ""), "kind and model are in the tooltip")
                self.assertEqual(o["poHeadTip"], "PO · claude opus")

    def test_a_task_keeps_its_seat_names(self):
        for w, o in self._each():
            with self.subTest(width=w):
                box = o["task"]["boxes"][0]
                self.assertEqual(box["names"], ["claude", "claude-2", "codex", "codex-2"])
                self.assertIn("claude", box["whos"])
                self.assertIn("Message the agents", box["placeholder"])

    def test_send_stays_put_on_focus_and_typing(self):
        for w, o in self._each():
            for name in ("poSend", "taskSend"):
                with self.subTest(width=w, box=name):
                    s = o[name]
                    self.assertEqual(s["value"], len(WORDS), "the words went in")
                    self.assertEqual(s["before"], s["focused"], s)
                    self.assertEqual(s["before"], s["typed"], s)


if __name__ == "__main__":
    unittest.main()
