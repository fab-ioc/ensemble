"""End-to-end check of #253 on real agents: slash commands from the chat.

Not part of the suite: it starts real Claude Code (haiku) and, with --codex,
Codex agents, which costs plan allowance and needs both logged in. Run:

    py tests/slash_commands_e2e.py [--codex] [--port 8798] [--shots DIR]

A throwaway hub starts on --port with its own state: USERPROFILE, HOME and
CODEX_HOME point at %TEMP%\\e253 (a copy of the logins goes there and is
deleted afterwards). The hub's check that only the dashboard page may send a
command is switched off in that hub alone: this script itself runs under an
agent, and the check counts any agent ancestor of the browser as an agent.

Then headless Chrome drives the task's chat as a person would:

* Claude: type "/mc", pick /mcp from the list, send it; the menu panel shows
  the MCP server list from the agent's screen; ↓ moves the cursor there; Esc
  closes it; the panel goes, the chat is back to normal (box empty and
  usable, the agent's composer back), and the command and what it showed are
  balloons. Then /clear: the seat follows the new conversation id.
* Codex (--codex): /model opens the model picker as a panel; ↓ moves; Esc
  closes it; the chat is back to normal.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

LAUNCH = """
import sys
port = sys.argv[1]
sys.path.insert(0, %r)
sys.argv = ['dashboard.py', '--port', port]
import dashboard
dashboard.peer_process.from_agent = lambda *a, **k: ''   # this throwaway hub only
dashboard.main()
"""

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 60000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(250); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `e2e-${name}.png`), Buffer.from(r.data, 'base64')); };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: KEYS[k] }, sessionId); };
    const panel = 'document.getElementById("cmdpanel")';
    const panelText = `(${panel}.hidden ? null : (${panel}.querySelector(".cp-body") || {}).textContent)`;
    const composer = `(() => { const p = slTarget(''); if (!p || !ensureTermFor(p.ptyId)) return false; const l = readScreen(p.ptyId); return cmdScreen(p.agent, l, []).menu == null && cmdComposerAt(p.agent, l) >= 0; })()`;
    for (const t of A.tasks) {
      const o = res[t.name] = {};
      await c.send('Page.navigate', { url: A.base + '/session?id=' + t.room }, sessionId);
      await until('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && !!SOLO_PTY');
      // The agent has done its first turn and sits at its composer.
      await until(composer, 240000);
      await sleep(1500);
      await evalIn('document.getElementById("input").focus(); 0');
      await c.send('Input.insertText', { text: t.typed }, sessionId);
      await until('!document.getElementById("slashpop").hidden && document.querySelectorAll("#slashpop .sl-opt").length > 0');
      o.list = await evalIn('[...document.querySelectorAll("#slashpop .sl-name")].map(e => e.textContent)');
      await shot(t.name + '-list');
      await key('Enter');
      o.picked = await evalIn('document.getElementById("input").value');
      await evalIn('document.getElementById("send").click(); 0');
      o.menu = await until(panelText, 30000);
      await sleep(800);
      o.menu = await evalIn(panelText);
      await shot(t.name + '-menu');
      o.focused = await evalIn(`document.activeElement === ${panel}`);
      // Move in the menu: Claude's /mcp lists one server here (the hub's
      // own), so Enter opens it; Codex's picker moves down a row.
      await key(t.move);
      await sleep(1500);
      o.moved = await evalIn(panelText);
      await shot(t.name + '-moved');
      for (let i = 0; i < 3 && !(await evalIn(`${panel}.hidden`)); i++) { await key('Escape'); await sleep(1200); }
      await until(`${panel}.hidden`, 20000);
      o.closedAt = Date.now();
      await until(composer, 20000);
      o.screen = await evalIn('readScreen(SOLO_PTY).filter(l => l.trim())');
      // Its output (or the menu it showed) comes back as a balloon from the agent.
      o.out = await until(`(() => { const r = (ROOM_OBJ.messages || []).filter(m => m.kind === 'notice' && m.noticeKind === 'commandOutput'); return r.length ? r[r.length - 1].text : null; })()`, 30000).catch(() => null);
      await sleep(1500);
      o.after = await evalIn(`({ box: document.getElementById('input').value, sendOn: !document.getElementById('send').disabled, hint: document.getElementById('hint').textContent,
        balloons: [...document.querySelectorAll('#msgs .msg')].slice(-4).map(m => m.innerText.replace(/\\s+/g, ' ').trim().slice(0, 160)) })`);
      await shot(t.name + '-after');
      if (t.clear) {
        o.sidBefore = await evalIn('SOLO_SID');
        await evalIn('(() => { const i = document.getElementById("input"); i.value = "/clear"; i.dispatchEvent(new Event("input")); return 0; })()');
        await sleep(300);
        await evalIn('document.getElementById("send").click(); 0');
        o.sidAfter = await until(`(async () => { const d = await fetch('/api/room?id=' + ROOM).then(r => r.json()); const p = (d.participants || []).find(p => p.kind === 'agent'); return p && p.sessionId && p.sessionId !== ${JSON.stringify(o.sidBefore || '')} ? p.sessionId : null; })()`, 60000).catch(() => null);
      }
    }
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(res));
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def http(base, path, body=None, method=None):
    req = urllib.request.Request(base + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def scratch_home(base: Path, codex: bool) -> Path:
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    real = Path(os.path.expanduser("~"))
    shutil.copy2(real / ".claude" / ".credentials.json", home / ".claude" / ".credentials.json")
    cfg = json.loads((real / ".claude.json").read_text(encoding="utf-8"))
    keep = {k: cfg[k] for k in ("oauthAccount", "userID") if k in cfg}
    keep.update(hasCompletedOnboarding=True, bypassPermissionsModeAccepted=True)
    (home / ".claude.json").write_text(json.dumps(keep), encoding="utf-8")
    if codex:
        (home / ".codex").mkdir()
        shutil.copy2(real / ".codex" / "auth.json", home / ".codex" / "auth.json")
        (home / ".codex" / "config.toml").write_text(
            'model = "gpt-6-astra"\nmodel_reasoning_effort = "low"\n\n[windows]\nsandbox = "elevated"\n', encoding="utf-8")
    return home


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--codex", action="store_true")
    ap.add_argument("--shots", default="")
    ap.add_argument("--keep", action="store_true", help="leave the hub running and the folder in place")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    node = shutil.which("node")
    if not (node and CHROME):
        print("node and Chrome are needed")
        return 2
    if not port_free(a.port):
        print(f"port {a.port} is in use: pick another")
        return 2
    base = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "e253"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir()
    home = scratch_home(base, a.codex)
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ENSEMBLE") or k.startswith("CODEX"))}
    env.update(USERPROFILE=str(home), HOME=str(home), CODEX_HOME=str(home / ".codex"), PYTHONUTF8="1")
    log = open(base / "hub.log", "w", encoding="utf-8")
    hub = subprocess.Popen([sys.executable, "-c", LAUNCH % str(ROOT), str(a.port)], cwd=str(ROOT), env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{a.port}"
    ok = False
    try:
        for _ in range(120):
            time.sleep(1)
            try:
                cfg = http(url, "/api/config")
                break
            except Exception:
                if hub.poll() is not None:
                    raise SystemExit("the hub ended: see " + str(base / "hub.log"))
        state = str(cfg.get("stateDir") or "")
        assert str(home).lower() in state.lower(), f"not the throwaway hub: {state}"
        proj = http(url, "/api/projects/new", {"name": "Slash"})["project"]
        tasks = [("claude", {"agent": "claude", "model": "haiku", "role": "engineer"}, "/mc", True, "Enter")]
        if a.codex:
            tasks.append(("codex", {"agent": "codex", "role": "engineer"}, "/mod", False, "ArrowDown"))
        rooms = []
        for name, agent, typed, clear, move in tasks:
            r = http(url, "/api/room/new", {"title": f"Slash {name}", "projectId": proj["id"], "agents": [agent],
                                             "task": "Reply with the single word: ready. Do nothing else."})
            rooms.append({"name": name, "room": r["room"]["id"], "typed": typed, "clear": clear, "move": move})
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": url, "tasks": rooms, "shots": a.shots}
        if a.shots:
            Path(a.shots).mkdir(parents=True, exist_ok=True)
        (base / "args.json").write_text(json.dumps(args), encoding="utf-8")
        (base / "e2e.js").write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([node, str(base / "e2e.js"), str(base / "args.json")], capture_output=True,
                             encoding="utf-8", timeout=1200)
        if out.returncode:
            print(out.stderr[-4000:])
            return 1
        got = json.loads(out.stdout.strip().splitlines()[-1])
        print(json.dumps(got, indent=2, ensure_ascii=False))
        fails = []
        cl = got.get("claude", {})
        if "/mcp" not in cl.get("list", []):
            fails.append("claude: /mcp not listed")
        if not cl.get("picked", "").startswith("/mcp"):
            fails.append("claude: the pick did not fill the box")
        if "MCP" not in (cl.get("menu") or ""):
            fails.append("claude: no MCP menu in the panel")
        if cl.get("moved") == cl.get("menu"):
            fails.append("claude: Enter did not open the server")
        if (cl.get("after") or {}).get("box") or not (cl.get("after") or {}).get("sendOn"):
            fails.append("claude: the box is not back to normal")
        if not cl.get("out"):
            fails.append("claude: no output balloon")
        if not cl.get("sidAfter"):
            fails.append("claude: /clear was not followed")
        if a.codex:
            cx = got.get("codex", {})
            if "Model" not in (cx.get("menu") or ""):
                fails.append("codex: no model picker in the panel")
            if cx.get("moved") == cx.get("menu"):
                fails.append("codex: ↓ did not move the picker")
            if (cx.get("after") or {}).get("box") or not (cx.get("after") or {}).get("sendOn"):
                fails.append("codex: the box is not back to normal")
        print("FAIL:\n- " + "\n- ".join(fails) if fails else "PASS")
        ok = not fails
        return 0 if ok else 1
    finally:
        if not a.keep:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(hub.pid)], capture_output=True)
            log.close()
            # The terminal host and the agents outlive the hub: every process
            # started from the throwaway folder goes too.
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%s*' "
                  "-and $_.Name -ne 'powershell.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }") % base
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
            for _ in range(15):                          # files are let go a moment later
                time.sleep(1)
                # The logins go with it; git's objects are read-only on Windows.
                try:
                    shutil.rmtree(base, onexc=lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p)))
                except OSError:
                    pass
                if not base.exists():
                    break
            if home.exists():
                print(f"could not delete {home}: delete it by hand (it holds a copy of the logins)")


if __name__ == "__main__":
    sys.exit(main())
