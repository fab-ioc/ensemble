"""Quick reactions in the selection bar (#258, static/selbar.js): in a real
browser, the chat (session.html) and the file view (fileview.html), served by
a hub in a thread, driven by headless Chrome over CDP.

* the bar offers the six reactions beside Comment, each with its word as a
  tooltip; a selection across two balloons has none (Copy only);
* one click sends exactly one comment, through the hub's resume-or-deliver:
  the quote and the reaction's words ("👍 Agree"), to the balloon's author;
  a double click still sends one;
* Tab goes into the bar and on to a reaction, Enter sends it;
* the reacted balloon shows a chip, still there after a reload (it is read
  from the conversation, not from the browser);
* the file view sends a reaction as its own comment on the file; a file no
  task chat owns has no reactions;
* the bar stays inside the window on a laptop and on a phone, where the
  reactions take a row of their own and every target is finger sized.

The parsing of a "## Review comments" message (SelBar.reactionsIn) is tested
in Node. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
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
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402

UNIT_JS = r"""
global.window = global; global.document = { addEventListener() {} };
require(process.argv[1]);
const S = window.SelBar;
const out = {};
out.notes = S.REACTIONS.map(S.reactNote);
out.two = S.reactionsIn('## Review comments (2)\n\n**1.** > Alpha  words\n\n👍 Agree\n\n**2.** > Bravo\n\n❓ Question: explain this');
out.typed = S.reactionsIn('## Review comments (1)\n\n**1.** > Alpha\n\n👍 Agree, and more besides');
out.mixed = S.reactionsIn('## Review comments (2)\n\n**1.** > Alpha\n\nA typed note\n\n**2.** > Bravo\n\n✅ Done / go ahead');
out.plain = S.reactionsIn('👍 Agree');
console.log(JSON.stringify({ ...out, two: out.two.map(x => [x.r.word, x.quote]), typed: out.typed.length,
  mixed: out.mixed.map(x => [x.r.word, x.quote]), plain: out.plain.length }));
"""

CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1400,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c + ' ' + buf))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}

async function page(c, url, ready, W, H, phone) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: !!phone }, sessionId);
  if (phone) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
  const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
  const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const go = async () => { await c.send('Page.navigate', { url }, sessionId); await until(ready); await sleep(600); };
  await go();
  const mouse = (type, x, y, clickCount = 1) => c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount }, sessionId);
  const key = async (k, code, vk, text) => {
    await c.send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, windowsVirtualKeyCode: vk, ...(text ? { text } : {}) }, sessionId);
    await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk }, sessionId);
    await sleep(250);
  };
  return {
    // With REACT_SHOTS set: a picture of the page as it is, for the task's note.
    shot: async name => { if (!A.shots) return; const { data } = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
      require('fs').writeFileSync(require('path').join(A.shots, name + '.png'), Buffer.from(data, 'base64')); },
    ev, until, go, key, close: () => c.send('Target.closeTarget', { targetId }),
    centre: sel => ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`),
    click: async (pt, n = 1) => { for (let k = 1; k <= n; k++) { await mouse('mousePressed', pt[0], pt[1], k); await mouse('mouseReleased', pt[0], pt[1], k); } await sleep(400); },
    reset: async () => { await ev('getSelection().removeAllRanges(); document.activeElement && document.activeElement.blur && document.activeElement.blur(); 0'); await sleep(350); },
    // The first `n` letters of the balloon whose words include `needle`, selected.
    pick: async (scope, needle, n) => { await ev(`(() => { const t = [...document.querySelectorAll(${JSON.stringify(scope)})].find(e => e.textContent.includes(${JSON.stringify(needle)}));
      t.scrollIntoView({ block: 'center' });
      const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT); let x; while ((x = w.nextNode())) { const i = x.nodeValue.indexOf(${JSON.stringify(needle)}); if (i >= 0) { getSelection().setBaseAndExtent(x, i, x, i + ${n}); return; } } })(); 0`); await sleep(500); },
    bar: () => ev(`(() => { const b = document.querySelector('.sel-bar'); const box = r => ({ l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height });
      const rx = b && b.querySelector('.sel-reacts');
      return { shown: !!b && !b.hidden, reacts: !!rx && !rx.hidden && rx.getClientRects().length > 0,
        buttons: rx ? [...rx.querySelectorAll('.sel-react')].map(x => ({ e: x.textContent, title: x.title, box: box(x.getBoundingClientRect()) })) : [],
        cmt: b ? box(b.querySelector('.sel-cmt').getBoundingClientRect()) : null, copy: b ? box(b.querySelector('.sel-copy').getBoundingClientRect()) : null,
        bar: b && !b.hidden ? box(b.getBoundingClientRect()) : null, vw: innerWidth, vh: innerHeight,
        coarse: matchMedia('(pointer: coarse)').matches, active: document.activeElement ? document.activeElement.className : '' }; })()`),
    chips: () => ev(`[...document.querySelectorAll('#msgs .msg')].filter(m => m.querySelector('.react-chip')).map(m => ({
        text: (m.querySelector('.text') || m).textContent.slice(0, 30), chips: [...m.querySelectorAll('.react-chip')].map(x => ({ t: x.textContent, link: x.tagName === 'A' && !!x.dataset.refMsg, sending: x.classList.contains('sending'), label: x.getAttribute('aria-label') })) }))`),
  };
}

async function chat(c) {
  const out = {};
  const ready = 'document.querySelectorAll("#msgs .msg .text.md").length > 4 && typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ';
  const p = await page(c, A.base + '/session?room=' + encodeURIComponent(A.room), ready, 1728, 1117);
  const M = '#msgs .msg .text.md';
  await p.reset(); await p.pick(M, 'Charlie reply', 13);
  out.offered = await p.bar();
  await p.shot('laptop-bar');
  await p.click(await p.centre('.sel-bar .sel-react[data-r="0"]'));
  out.afterClick = { bar: await p.bar(), sel: await p.ev('getSelection().toString()') };
  await p.until('[...document.querySelectorAll("#msgs .msg")].some(m => m.textContent.includes("Charlie reply") && m.querySelector("a.react-chip"))', 20000);

  // A double click: one comment.
  await p.reset(); await p.pick(M, 'Bravo paragraph', 15);
  await p.click(await p.centre('.sel-bar .sel-react[data-r="2"]'), 2);
  await p.until('[...document.querySelectorAll("#msgs .msg")].some(m => m.textContent.includes("Bravo paragraph") && m.querySelector("a.react-chip"))', 20000);

  // The keyboard: Tab into the bar (Comment), Tab to the first reaction, Enter.
  await p.reset(); await p.pick(M, 'Last balloon', 12);
  await p.key('Tab', 'Tab', 9);
  out.tab1 = await p.bar();
  await p.key('Tab', 'Tab', 9);
  out.tab2 = await p.bar();
  await p.key('Enter', 'Enter', 13, '\r');
  await p.until('[...document.querySelectorAll("#msgs .msg")].some(m => m.textContent.includes("Last balloon") && m.querySelector("a.react-chip"))', 20000);

  // Two balloons: Copy only.
  await p.reset();
  await p.ev(`(() => { const ts = [...document.querySelectorAll(${JSON.stringify(M)})]; const a = ts.find(e => e.textContent.includes('Filler message number 0')), b = ts.find(e => e.textContent.includes('Last balloon'));
    const fa = document.createTreeWalker(a, NodeFilter.SHOW_TEXT).nextNode(), fb = document.createTreeWalker(b, NodeFilter.SHOW_TEXT).nextNode();
    getSelection().setBaseAndExtent(fa, 0, fb, 4); })(); 0`);
  await sleep(500);
  out.twoBalloons = await p.bar();
  out.chips = await p.chips();
  await p.reset();
  await p.go();
  await p.until('document.querySelectorAll("#msgs .react-chip").length >= 3', 20000);
  out.chipsReloaded = await p.chips();
  await p.ev('document.querySelector("#msgs .react-chip").closest(".msg").scrollIntoView({ block: "center" }); 0'); await sleep(300);
  await p.shot('laptop-chips');
  out.chipStyle = await p.ev(`(() => { const x = document.querySelector('#msgs .react-chip'), s = getComputedStyle(x); return { h: x.getBoundingClientRect().height, fg: s.color, bg: s.backgroundColor }; })()`);
  await p.close();

  // A phone.
  const f = await page(c, A.base + '/session?room=' + encodeURIComponent(A.room), ready, 390, 844, true);
  await f.reset(); await f.pick(M, 'Charlie reply', 13);
  out.phone = await f.bar();
  await f.shot('phone-bar');
  await f.close();
  return out;
}

async function fileview(c) {
  const out = {};
  const ready = 'typeof _cRoot !== "undefined" && !!_cRoot && _cRoot.textContent.includes("line 3 of the notes")';
  const p = await page(c, A.base + '/fileview?path=' + encodeURIComponent(A.file) + '&room=' + encodeURIComponent(A.room), ready, 1000, 700);
  await p.reset(); await p.pick('body', 'line 3 of the notes', 19);
  out.offered = await p.bar();
  await p.click(await p.centre('.sel-bar .sel-react[data-r="1"]'));
  await sleep(1500);
  out.tray = await p.ev('document.querySelectorAll("#cmt-tray .cmt-item").length');
  await p.close();
  const q = await page(c, A.base + '/fileview?path=' + encodeURIComponent(A.file), ready, 1000, 700);
  await q.reset(); await q.pick('body', 'line 3 of the notes', 19);
  out.noRoom = await q.bar();
  await q.close();
  return out;
}

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    out.chat = await chat(c);
    out.file = await fileview(c);
  } finally {
    try { c.ws.close(); } catch (e) {}
    const gone = new Promise(r => ch.on('exit', r));
    ch.kill();
    await Promise.race([gone, sleep(5000)]);
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is needed")
class TheMessage(unittest.TestCase):
    """SelBar.reactionsIn reads the reactions back from a sent message."""

    def test_reactions_are_read_back(self):
        out = subprocess.run([NODE, "-e", UNIT_JS, str(ROOT / "static" / "selbar.js")], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got["notes"], ["👍 Agree", "👎 Disagree", "❓ Question: explain this", "🤔 Not sure: think again",
                                        "✅ Done / go ahead", "❤️ Love it: keep this"])
        self.assertEqual(got["two"], [["Agree", "Alpha  words"], ["Question: explain this", "Bravo"]])
        self.assertEqual(got["typed"], 0, "a typed note that starts like one is no reaction")
        self.assertEqual(got["mixed"], [["Done / go ahead", "Bravo"]])
        self.assertEqual(got["plain"], 0, "not a review-comments message")


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    """A hub in a thread with a two-agent chat and a text file in its folder;
    the resume-or-deliver endpoint posts what it is given as the person's
    message (no agent is started) and keeps a copy for the test."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-react-", ignore_cleanup_errors=True)
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
        work = root / "Motors"
        work.mkdir()
        notes = work / "notes.txt"
        notes.write_text("".join(f"This is line {i} of the notes, with some words.\n" for i in range(1, 40)), encoding="utf-8")
        room = chatroom.create_room("Reactions", [{"identity": "claude", "agent": "claude", "cwd": str(work)},
                                                  {"identity": "codex", "agent": "codex", "cwd": str(work)}])
        rid = cls.room = room["id"]
        chatroom.patch_room(rid, cwd=str(work))
        chatroom.post_message(rid, "user", "First question about the motor gearbox.")
        chatroom.post_message(rid, "claude", "Alpha paragraph one has words.\n\nBravo paragraph two follows it.", to="user")
        chatroom.post_message(rid, "codex", "Charlie reply from the reviewer with some words.", to="claude")
        for i in range(3):
            chatroom.post_message(rid, "claude", f"Filler message number {i} with text.", to="user")
        chatroom.post_message(rid, "claude", "Last balloon: delta echo foxtrot golf hotel.", to="user")
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

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": rid, "file": str(notes), "shots": os.environ.get("REACT_SHOTS", "")}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        cls.err = out.stderr
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def inside(self, g, margin=8):
        r = g["bar"]
        self.assertIsNotNone(r, g)
        self.assertGreaterEqual(r["l"], margin - 0.5, g)
        self.assertLessEqual(r["r"], g["vw"] - margin + 0.5, g)
        self.assertGreaterEqual(r["t"], -0.5, g)
        self.assertLessEqual(r["b"], g["vh"] + 0.5, g)

    def chat_sends(self):
        return [s for s in self.sent if s["text"].startswith("## Review comments (")]

    def test_the_bar_offers_six_reactions(self):
        g = self.got["chat"]["offered"]
        self.assertTrue(g["reacts"], g)
        self.assertEqual([b["e"] for b in g["buttons"]], ["👍", "👎", "❓", "🤔", "✅", "❤️"])
        self.assertEqual(g["buttons"][0]["title"], "Agree: sent at once")
        self.inside(g)
        # On the bar's row, after Comment and before Copy.
        self.assertLess(g["cmt"]["r"], g["buttons"][0]["box"]["l"], g)
        self.assertLess(g["buttons"][-1]["box"]["r"], g["copy"]["l"], g)

    def test_a_click_sends_one_comment_and_clears_the_selection(self):
        a = self.got["chat"]["afterClick"]
        self.assertFalse(a["bar"]["shown"], a)
        self.assertEqual(a["sel"], "")
        ones = [s for s in self.chat_sends() if "Charlie reply" in s["text"]]
        self.assertEqual(len(ones), 1, self.sent)
        self.assertEqual(ones[0]["text"], "## Review comments (1)\n\n**1.** > Charlie reply\n\n👍 Agree")
        self.assertEqual(ones[0]["to"], "codex", "to the balloon's author")

    def test_a_double_click_sends_one(self):
        ones = [s for s in self.chat_sends() if "Bravo paragraph" in s["text"]]
        self.assertEqual(len(ones), 1, self.sent)
        self.assertTrue(ones[0]["text"].endswith("❓ Question: explain this"), ones[0])

    def test_tab_and_enter(self):
        g = self.got["chat"]
        self.assertEqual(g["tab1"]["active"], "sel-cmt", g["tab1"])
        self.assertEqual(g["tab2"]["active"], "sel-react", g["tab2"])
        ones = [s for s in self.chat_sends() if "Last balloon" in s["text"]]
        self.assertEqual(len(ones), 1, self.sent)
        self.assertTrue(ones[0]["text"].endswith("\n\n👍 Agree"), ones[0])

    def test_two_balloons_have_no_reactions(self):
        g = self.got["chat"]["twoBalloons"]
        self.assertTrue(g["shown"], g)
        self.assertFalse(g["reacts"], g)

    def test_the_chip_shows_and_survives_a_reload(self):
        for k in ("chips", "chipsReloaded"):
            with self.subTest(k):
                got = {x["text"][:12]: x["chips"] for x in self.got["chat"][k]}
                self.assertEqual(set(got), {"Alpha paragr", "Charlie repl", "Last balloon"}, got)
                self.assertEqual([c["t"] for c in got["Charlie repl"]], ["👍"])
                self.assertEqual([c["t"] for c in got["Alpha paragr"]], ["❓"])
                self.assertTrue(all(c["link"] and not c["sending"] for v in got.values() for c in v), got)
                self.assertEqual(got["Charlie repl"][0]["label"], "Your reaction: Agree")

    def test_the_phone(self):
        g = self.got["chat"]["phone"]
        self.assertTrue(g["coarse"], g)
        self.assertTrue(g["reacts"], g)
        self.inside(g, margin=8)
        tops = {round(b["box"]["t"]) for b in g["buttons"]}
        self.assertEqual(len(tops), 1, "one row")
        self.assertGreater(min(tops), g["cmt"]["b"] - 1, "under Comment and Copy")
        for b in g["buttons"]:
            self.assertGreaterEqual(b["box"]["w"], 44, b)
            self.assertGreaterEqual(b["box"]["h"], 44, b)

    def test_the_file_view(self):
        g = self.got["file"]
        self.assertTrue(g["offered"]["reacts"], g["offered"])
        ones = [s for s in self.sent if s["text"].startswith("## Review comments on ")]
        self.assertEqual(len(ones), 1, self.sent)
        self.assertIn("(1)\nFile: ", ones[0]["text"])
        self.assertTrue(ones[0]["text"].endswith("**1.** > line 3 of the notes\n\n👎 Disagree"), ones[0]["text"])
        self.assertEqual(g["tray"], 0, "sent, so gone from the tray")
        self.assertTrue(g["noRoom"]["shown"], g["noRoom"])
        self.assertFalse(g["noRoom"]["reacts"], "no task chat: no one to react to")


class TheWiring(unittest.TestCase):
    """Every surface with selection comments passes the bar a reaction handler."""

    def test_every_surface_reacts(self):
        for page, mount in (("session.html", "react: selReact"),
                            ("fileview.html", "react: selReact, canReact: () => !!ROOM"),
                            ("index.html", "react: drSelReact, canReact: drCanReact")):
            src = (ROOT / page).read_text(encoding="utf-8")
            with self.subTest(page):
                self.assertIn(mount, src)
                self.assertIn(".sel-bar .sel-reacts { order: 3; flex-basis: 100%;", src)


if __name__ == "__main__":
    unittest.main()
