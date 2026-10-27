"""Done and Go ahead as two reactions (GitHub issue 28, P228) in headless
Chrome: the selection bar offers ✅ Done and 🚀 Go ahead side by side, at
1440x900 and at 390x844 (one row, every reaction at least 44px, the bar
inside the window); each sends its own words; a balloon shows a chip for
each, and a ✅ sent before the split ("✅ Done / go ahead") still shows as
a ✅ chip. No balloon has Done or Go ahead buttons of its own.

Screenshots go to $ENSEMBLE_SHOTS when it is set."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
from test_page_update import CHROME, NODE
from tests import chrome_profile

CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const out = {};
  try {
    for (const [w, h, phone, click] of A.views) {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
      await send('Page.enable', {}, sessionId);
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: phone }, sessionId);
      if (phone) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const ev = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
      const mouse = (type, x, y) => send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1 }, sessionId);
      const click = async sel => { const [x, y] = await ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
        await mouse('mousePressed', x, y); await mouse('mouseReleased', x, y); await sleep(400); };
      // The first letters of the balloon words holding `needle`, selected.
      const pick = async needle => { await ev(`(() => { getSelection().removeAllRanges();
        const t = [...document.querySelectorAll('#msgs .msg .text.md')].find(e => e.textContent.includes(${JSON.stringify(needle)}));
        t.scrollIntoView({ block: 'center' });
        const wk = document.createTreeWalker(t, NodeFilter.SHOW_TEXT); let x; while ((x = wk.nextNode())) { const i = x.nodeValue.indexOf(${JSON.stringify(needle)}); if (i >= 0) { getSelection().setBaseAndExtent(x, i, x, i + ${JSON.stringify(needle)}.length); return; } } })(); 0`); await sleep(600); };
      const bar = () => ev(`(() => { const b = document.querySelector('.sel-bar'); const box = r => ({ l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height });
        const rx = b && b.querySelector('.sel-reacts');
        return { shown: !!b && !b.hidden, bar: b ? box(b.getBoundingClientRect()) : null, vw: innerWidth, vh: innerHeight,
          cmt: b ? box(b.querySelector('.sel-cmt').getBoundingClientRect()) : null, copy: b ? box(b.querySelector('.sel-copy').getBoundingClientRect()) : null,
          buttons: rx ? [...rx.querySelectorAll('.sel-react')].map(x => ({ e: x.textContent, title: x.title, label: x.getAttribute('aria-label'), box: box(x.getBoundingClientRect()) })) : [] }; })()`);
      // Done or Go ahead as a balloon's own button (#272's, gone): none anywhere.
      const buttons = () => ev(`[...document.querySelectorAll('#msgs .msg button')].filter(b => b.matches('.pt-approve, .pt-ans, [data-kind]') || /^(✓\s*)?(Done|Go ahead)$/.test(b.textContent.trim())).length`);
      const chips = () => ev(`[...document.querySelectorAll('#msgs .msg')].filter(m => m.querySelector('.react-chip')).map(m => ({
        text: (m.querySelector('.text') || m).textContent.trim().slice(0, 24), chips: [...m.querySelectorAll('.react-chip')].map(x => ({ t: x.textContent, label: x.getAttribute('aria-label') })) }))`);
      await send('Page.navigate', { url: A.base + '/session?room=' + encodeURIComponent(A.room) }, sessionId);
      await until(`document.querySelectorAll('#msgs .msg .text.md').length >= 3 && typeof ROOM_OBJ !== 'undefined' && !!ROOM_OBJ`);
      await until(`document.querySelectorAll('#msgs .react-chip').length > 0`);
      await sleep(500);
      const o = { buttons: await buttons(), chipsBefore: await chips(), scrollX: await ev('document.documentElement.scrollWidth - innerWidth') };
      await pick(A.decision);
      o.bar = await bar();
      await shot(`done-go-${w}-bar`);
      if (click) {
        await click('.sel-bar .sel-react[data-r="5"]');
        await until(`[...document.querySelectorAll('#msgs .msg')].some(m => m.textContent.includes(${JSON.stringify(A.decision)}) && m.querySelector('a.react-chip'))`);
        await pick(A.plain);
        await click('.sel-bar .sel-react[data-r="4"]');
        await until(`[...document.querySelectorAll('#msgs .msg')].some(m => m.textContent.includes(${JSON.stringify(A.plain)}) && m.querySelector('a.react-chip'))`);
        await sleep(500);
        o.chipsAfter = await chips();
        o.buttonsAfter = await buttons();
        await ev(`document.querySelector('#msgs a.react-chip').closest('.msg').scrollIntoView({ block: 'center' }); 0`);
        await sleep(200);
        await shot(`done-go-${w}-chips`);
      }
      out[w] = o;
      await send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

CARD_INTRO = "Release timing for the wider board"
DECISION = "Run the backup tonight"
PLAIN = "The board is wider now"
DONE, GO, OLD = "✅ Done: I did it, nothing to start", "🚀 Go ahead: do this", "✅ Done / go ahead"


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    """A hub in a thread with a chat holding a decision, a card and a plain
    answer, and one reaction sent before the split; the resume endpoint posts
    what it is given as the person's message (no agent starts)."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-donego-", ignore_cleanup_errors=True)
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
        work = root / "Board"
        work.mkdir()
        room = chatroom.create_room("Answers", [{"identity": "claude", "agent": "claude", "cwd": str(work)},
                                                {"identity": "codex", "agent": "codex", "cwd": str(work)}])
        rid = cls.room = room["id"]
        chatroom.patch_room(rid, cwd=str(work))
        chatroom.post_message(rid, "user", "Make the board wider, please.")
        chatroom.post_message(rid, "claude", f"{PLAIN}; it is live after a reload.", to="user")
        chatroom.post_message(rid, "claude", f"Decision needed: {DECISION}? I recommend yes: it takes ten minutes.", to="user")
        chatroom.post_message(rid, "claude", f"{CARD_INTRO}.\n\nAsk: When should this go live?\n- Now (recommended)\n- Later", to="user")
        # A ✅ sent before Done and Go ahead were two.
        chatroom.post_message(rid, "user", f"## Review comments (1)\n\n**1.** > {CARD_INTRO}\n\n{OLD}", to="claude")
        sent = cls.sent = []

        class Handler(dashboard.Handler):
            def do_POST(self):
                if self.path.split("?")[0] == "/api/room/resume":
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    if body.get("text"):
                        sent.append(body)
                        chatroom.post_message(rid, "user", body["text"], to=body.get("to") or "")
                    data = json.dumps({"ok": True}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                super().do_POST()

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
                "room": rid, "shots": shots, "decision": DECISION, "plain": PLAIN,
                "views": [[1440, 900, False, True], [390, 844, True, False]]}
        run = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    def inside(self, g, margin=8):
        r = g["bar"]
        self.assertIsNotNone(r, g)
        self.assertGreaterEqual(r["l"], margin - 0.5, g)
        self.assertLessEqual(r["r"], g["vw"] - margin + 0.5, g)
        self.assertGreaterEqual(r["t"], -0.5, g)
        self.assertLessEqual(r["b"], g["vh"] + 0.5, g)

    def test_the_bar_offers_done_and_go_ahead_side_by_side(self):
        for w in ("1440", "390"):
            with self.subTest(w):
                g = self.got[w]["bar"]
                self.assertTrue(g["shown"], g)
                es = [b["e"] for b in g["buttons"]]
                self.assertEqual(es, ["👍", "👎", "❓", "🤔", "✅", "🚀", "❤️"])
                self.assertEqual(g["buttons"][4]["title"], "Done: I did it, nothing to start: sent at once")
                self.assertEqual(g["buttons"][5]["title"], "Go ahead: do this: sent at once")
                self.assertNotIn("Done / go ahead", json.dumps(g, ensure_ascii=False), "the old reaction is never offered")
                self.inside(g)
                self.assertEqual(len({round(b["box"]["t"]) for b in g["buttons"]}), 1, "one row")
                self.assertLess(g["buttons"][4]["box"]["r"], g["buttons"][5]["box"]["l"] + 0.5, "next to each other")
                self.assertLessEqual(self.got[w]["scrollX"], 1)

    def test_on_the_laptop_the_row_sits_between_comment_and_copy(self):
        g = self.got["1440"]["bar"]
        self.assertLess(g["cmt"]["r"], g["buttons"][0]["box"]["l"], g)
        self.assertLess(g["buttons"][-1]["box"]["r"], g["copy"]["l"], g)

    def test_on_the_phone_every_reaction_is_a_44px_target(self):
        g = self.got["390"]["bar"]
        self.assertGreater(min(b["box"]["t"] for b in g["buttons"]), g["cmt"]["b"] - 1, "under Comment and Copy")
        for b in g["buttons"]:
            self.assertGreaterEqual(min(b["box"]["w"], b["box"]["h"]), 44, b)

    def test_each_sends_its_own_words(self):
        notes = {s["text"].split("\n\n")[1]: s["text"].split("\n\n")[-1].split(" on http")[0] for s in self.sent}
        self.assertEqual(notes, {f"**1.** > {DECISION}": GO, f"**1.** > {PLAIN}": DONE}, self.sent)
        self.assertTrue(all(s["text"].startswith("## Review comments (1)") for s in self.sent), self.sent)

    def test_chips_for_both_and_the_old_note(self):
        before = {x["text"]: x["chips"] for x in self.got["1440"]["chipsBefore"]}
        self.assertEqual(list(before), [CARD_INTRO[:24]], before)
        self.assertEqual(before[CARD_INTRO[:24]], [{"t": "✅", "label": "Your reaction: Done / go ahead"}])
        after = {x["text"][:12]: [c["t"] for c in x["chips"]] for x in self.got["1440"]["chipsAfter"]}
        self.assertEqual(after, {"The board is": ["✅"], "Decision nee": ["🚀"], CARD_INTRO[:12]: ["✅"]}, after)
        labels = [c["label"] for x in self.got["1440"]["chipsAfter"] for c in x["chips"]]
        self.assertIn("Your reaction: Done: I did it, nothing to start", labels)
        self.assertIn("Your reaction: Go ahead: do this", labels)
        self.assertEqual(self.got["390"]["chipsBefore"] and len(self.got["390"]["chipsBefore"]), 3, "a reload shows all three")

    def test_no_balloon_has_done_or_go_ahead_buttons(self):
        for w in ("1440", "390"):
            self.assertEqual(self.got[w]["buttons"], 0, w)
        self.assertEqual(self.got["1440"]["buttonsAfter"], 0)


if __name__ == "__main__":
    unittest.main()
