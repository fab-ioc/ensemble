"""CDP coverage for #253: the command list and the agent's menu in the chat.

At 1728x1117 and 390x844 (touch), light and dark: typing "/" opens the
agent's commands over the box (inside the page, above the words), ↓ moves
the choice and Enter puts it in the box; the menu panel shows the agent's
menu text without a sideways scroll, its keys are a finger's size on a
phone, and the panel's arrow keys go to the agent's terminal. A command
and its output show as balloons; the transcript's "/compact" and its
summary do not. Screenshots go to $ENSEMBLE_SHOTS when set. Skipped
without Node and Chrome.
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
SIZES = [(1728, 1117, False), (390, 844, True)]
MENU = """Manage MCP servers
5 servers

  claude.ai
❯ ✔ claude.ai Claude Docs    8 tools
  ✔ claude.ai Gmail          30 tools
  → Show unused connectors   2 hidden

  Built-in MCPs (always available)
  ✔ claude-in-chrome         22 tools

https://code.claude.com/docs/en/mcp for help

↑/↓ to navigate · Enter to confirm · Esc to cancel"""

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
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
const KEYS = { ArrowDown: 40, ArrowUp: 38, Enter: 13, Escape: 27 };
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const res = {};
  try {
    for (const [w, h, mob] of A.sizes) for (const theme of ['light', 'dark']) {
      const o = res[w + '-' + theme] = {};
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      await c.send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('cd-theme', '${theme}'); } catch (e) {}` }, sessionId);
      // The hub keeps the person's theme, and a page adopts it: set it there too.
      o.themeSet = (await fetch(A.base + '/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json', Origin: A.base }, body: JSON.stringify({ theme }) })).status;
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `slash-${w}-${theme}-${name}.png`), Buffer.from(r.data, 'base64')); };
      const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: KEYS[k] }, sessionId); };
      await c.send('Page.navigate', { url: A.base + '/session?id=' + A.room }, sessionId);
      await until('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && document.querySelectorAll("#msgs .msg").length > 0');
      await sleep(600);
      o.theme = await evalIn('document.documentElement.dataset.theme || "light"');
      o.balloons = await evalIn(`[...document.querySelectorAll('#msgs .msg')].map(m => ({ who: (m.querySelector('.who') || {}).textContent || '', text: m.innerText.replace(/\\s+/g, ' ').trim().slice(0, 120), pre: !!m.querySelector('pre.cb'), needs: m.classList.contains('needs-answer') }))`);
      // 1. "/" lists the commands.
      await evalIn('document.getElementById("input").focus(); 0');
      await c.send('Input.insertText', { text: '/m' }, sessionId);
      await until('!document.getElementById("slashpop").hidden && document.querySelectorAll("#slashpop .sl-opt").length > 0');
      const box = `(() => { const R = e => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width, h: b.height }; };
        const p = document.getElementById('slashpop'), i = document.getElementById('input');
        return { pop: R(p), input: R(i), vw: innerWidth, names: [...p.querySelectorAll('.sl-name')].map(e => e.textContent),
          sel: (p.querySelector('.sl-opt.sel .sl-name') || {}).textContent, optH: Math.min(...[...p.querySelectorAll('.sl-opt')].map(e => e.getBoundingClientRect().height)),
          descCut: [...p.querySelectorAll('.sl-opt')].every(e => e.scrollWidth <= e.clientWidth + 1) }; })()`;
      o.pop = await evalIn(box);
      await shot('list');
      await key('ArrowDown');
      o.afterDown = await evalIn(box);
      await key('Enter');
      o.picked = await evalIn('({ value: document.getElementById("input").value, hidden: document.getElementById("slashpop").hidden })');
      await key('Escape');
      await evalIn('(() => { const i = document.getElementById("input"); i.value = ""; i.dispatchEvent(new Event("input")); return 0; })()');
      // 2. The agent's menu as a panel; its keys go to the terminal.
      await evalIn(`(() => { window.__sent = []; const f0 = window.fetch; window.fetch = (u, o) => { if (String(u).includes('/api/pty/input')) { window.__sent.push([String(u), JSON.parse(o.body)]); return Promise.resolve(new Response('{}')); } return f0(u, o); };
        cmdPanelShow({ key: 'k1', text: '/mcp', identity: 'claude', pty: 'pty-x', agent: 'claude' }, ${JSON.stringify(A.menu)}); return 0; })()`);
      await sleep(300);
      o.panel = await evalIn(`(() => { const p = document.getElementById('cmdpanel'), b = p.getBoundingClientRect(), pre = p.querySelector('.cp-body');
        return { shown: !p.hidden && b.width > 0, l: b.left, r: b.right, vw: innerWidth, focused: document.activeElement === p,
          text: pre.textContent, sideways: pre.scrollWidth > pre.clientWidth + 1, pageSideways: document.documentElement.scrollWidth > innerWidth + 1,
          keyH: Math.min(...[...p.querySelectorAll('.cp-key')].map(e => e.getBoundingClientRect().height)),
          keys: [...p.querySelectorAll('.cp-key[data-k]')].map(e => e.dataset.k),
          bg: getComputedStyle(p).backgroundColor, fg: getComputedStyle(pre).color }; })()`);
      await shot('panel');
      {
        await evalIn('CMD = { key: "k1", pty: "pty-x" }; document.getElementById("cmdpanel").focus(); 0');
        await key('ArrowDown');
        await key('Escape');
        o.sent = await evalIn('window.__sent');
        await evalIn('CMD = null; cmdPanelHide(); 0');
        o.closed = await evalIn('document.getElementById("cmdpanel").hidden');
      }
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(res));
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _turn(sid: str, role: str, text: str, at: float, **extra) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    if role == "user":
        return {"type": "user", "timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}, **extra}
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class SlashCommandsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-slash-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        home = base / "home"
        (home / ".claude" / "skills" / "tidy").mkdir(parents=True)
        (home / ".claude" / "skills" / "tidy" / "SKILL.md").write_text(
            "---\nname: tidy\ndescription: Tidy the working tree before a commit, a long line to see it cut short on a phone\n---\n",
            encoding="utf-8")
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
            mock.patch.object(dashboard.slash, "catalogue", lambda agent, cwd="", home_=None, _c=dashboard.slash.catalogue: _c(agent, cwd, home)),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        (base / "cs").mkdir()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        sid = "slash-1"
        room = chatroom.create_room("Chart", [{"identity": "claude", "agent": "claude", "model": "opus",
                                               "role": "engineer", "sessionId": sid}])
        cls.room = room["id"]
        full = chatroom.get_room(cls.room, public=False)
        full["mode"] = "solo"
        chatroom.update_room(full)
        dashboard.assign_session_project(cls.room, proj["id"])
        t0 = time.time() - 600
        lines = [_turn(sid, "user", "Make the speed chart wider", t0),
                 _turn(sid, "assistant", "Done: it takes the whole width now.", t0 + 10),
                 _turn(sid, "user", "/compact", t0 + 20),
                 _turn(sid, "user", "This session is being continued from a previous conversation.", t0 + 25, isCompactSummary=True),
                 _turn(sid, "assistant", "Back after the compaction.", t0 + 60)]
        tdir = base / "transcripts" / "C--x"
        tdir.mkdir(parents=True)
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        chatroom.post_notice(cls.room, "user", "/cost", {"noticeKind": "command", "to": "claude", "key": "k0"})
        chatroom.post_notice(cls.room, "claude", "Total cost:            $0.0034\nTotal duration (API):  2s",
                             {"noticeKind": "commandOutput", "command": "/cost", "key": "k0"})
        full = chatroom.get_room(cls.room, public=False)
        full["messages"][-2] = {**full["messages"][-2], "ts": t0 + 30}
        full["messages"][-1] = {**full["messages"][-1], "ts": t0 + 31}
        chatroom.update_room(full)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": cls.room, "sizes": SIZES, "shots": cls.shots, "menu": MENU}
        (base / "args.json").write_text(json.dumps(args), encoding="utf-8")
        script = base / "slash_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), str(base / "args.json")], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def each(self):
        for w, _h, mob in SIZES:
            for theme in ("light", "dark"):
                yield w, mob, theme, self.got[f"{w}-{theme}"]

    def test_the_theme_is_the_one_asked_for(self):
        for w, _m, theme, o in self.each():
            self.assertEqual(o["theme"], theme, w)

    def test_slash_lists_the_commands_over_the_box(self):
        for w, mob, theme, o in self.each():
            p = o["pop"]
            self.assertIn("/mcp", p["names"], (w, theme))
            self.assertIn("/model", p["names"])
            self.assertTrue(all(n.startswith("/m") for n in p["names"][:3]), p["names"])
            self.assertLessEqual(p["pop"]["b"], p["input"]["t"] + 1, "above the words")
            self.assertGreaterEqual(p["pop"]["l"], 0)
            self.assertLessEqual(p["pop"]["r"], p["vw"])
            self.assertTrue(p["descCut"], "a long description is cut, never wider than the list")
            if mob:
                self.assertGreaterEqual(p["optH"], 44, "a finger's size")
            self.assertEqual(p["sel"], p["names"][0])
            self.assertEqual(o["afterDown"]["sel"], p["names"][1], "↓ moves the choice")
            self.assertEqual(o["picked"]["value"], p["names"][1] + " ", "Enter puts it in the box")
            self.assertTrue(o["picked"]["hidden"])

    def test_the_menu_panel(self):
        for w, mob, theme, o in self.each():
            p = o["panel"]
            self.assertTrue(p["shown"], (w, theme))
            self.assertTrue(p["focused"], "the panel takes the keys")
            self.assertIn("claude-in-chrome", p["text"])
            self.assertFalse(p["sideways"], "the menu wraps, it does not scroll sideways")
            self.assertFalse(p["pageSideways"])
            self.assertGreaterEqual(p["l"], 0)
            self.assertLessEqual(p["r"], p["vw"])
            self.assertEqual(p["keys"], ["up", "down", "left", "right", "enter", "esc", "tab", "space"])
            if mob:
                self.assertGreaterEqual(p["keyH"], 44)
            self.assertNotEqual(p["bg"], p["fg"])
            self.assertEqual(o["sent"], [["/api/pty/input", {"id": "pty-x", "data": "\x1b[B"}],
                                         ["/api/pty/input", {"id": "pty-x", "data": "\x1b"}]])
            self.assertTrue(o["closed"])

    def test_command_balloons_and_hidden_turns(self):
        for w, _m, theme, o in self.each():
            texts = [b["text"] for b in o["balloons"]]
            self.assertTrue(any(t.startswith("Copy link sam → claude") and t.endswith("/cost") for t in texts), texts)
            out = [b for b in o["balloons"] if "Total cost" in b["text"]]
            self.assertEqual(len(out), 1, texts)
            self.assertTrue(out[0]["pre"], "the terminal's text, as such")
            self.assertFalse(out[0]["needs"], "a command's output asks nothing")
            self.assertFalse(any("/compact" in t for t in texts), texts)
            self.assertFalse(any("being continued" in t for t in texts), texts)
            self.assertTrue(any("Back after the compaction" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()
