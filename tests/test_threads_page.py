"""Threads in a PO chat, in the browser (#210).

In headless Chrome over CDP, against a hub in a thread serving a solo PO room
whose transcript is a file, at 1728x1117, 390x844 (a phone) and 4096x2160,
each size its own project and PO:

* the chat is a channel: each thread once, as its first message with a line
  under it ("2 replies · PO · last reply …", where it stands); the replies are
  not in it; a thread with replies not seen has the unread dot, and Needs you
  lists it under Threads;
* "Whole chat" shows every message in place again, and back;
* a thread's page has all its messages and its own box: a reply typed there
  goes to the PO as "Re P1: …", with "Also send to chat" it shows in the chat
  too, and the PO's answer lands in the thread;
* opened, a thread is read: its dot goes.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node and
Chrome.
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
import points  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1728, 1117, False), (390, 844, True), (4096, 2160, False)]

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
// What the chat shows: each balloon's id, and each thread line's words.
const VIEW = `(() => ({
  mids: [...document.querySelectorAll('#msgs .msg[data-mid]')].map(e => e.dataset.mid),
  lines: [...document.querySelectorAll('#msgs .thr-line')].map(e => ({ thr: e.querySelector('.thr-open').dataset.thr,
    text: e.textContent.replace(/\\s+/g, ' ').trim(), unread: e.classList.contains('unread'), dot: !!e.querySelector('.thr-dot') })),
  ins: [...document.querySelectorAll('#msgs .thr-in')].map(e => e.textContent.replace(/\\s+/g, ' ').trim()),
  // The bar's switch, or on a phone the composer menu's.
  whole: (b => b && !b.hidden && b.getBoundingClientRect().width > 0 ? b.getAttribute('aria-pressed') : null)(document.getElementById('whole-chat'))
    || (m => m && !m.hidden ? m.getAttribute('aria-pressed') : null)(document.querySelector('[data-phone-action="whole-chat"]')),
  scrollX: document.documentElement.scrollWidth - innerWidth,
}))()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of A.sizes) {
      const room = A.rooms[String(w)], o = {};
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `threads-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
      const go = async (q) => {
        await c.send('Page.navigate', { url: A.base + '/session?' + q }, sessionId);
        await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0', 30000);
        await sleep(400);
      };
      // A deliberately slow first build: the fixed notice appears, gives the
      // current phase, then leaves without shifting the chat.
      await c.send('Page.navigate', { url: A.base + '/session?room=' + room }, sessionId);
      await until('document.getElementById("ens-progress") && !document.getElementById("ens-progress").hidden', 15000);
      const progressShownAt = Date.now();
      o.progressDuring = await evalIn(`(() => {
        const e = document.getElementById('ens-progress');
        const r = e.getBoundingClientRect();
        return { text: e.textContent, width: r.width, right: r.right, scrollX: document.documentElement.scrollWidth - innerWidth,
          mode: e.classList.contains('indeterminate') ? 'indeterminate' : 'determinate' };
      })()`);
      o.progressThemes = await evalIn(`(() => {
        const root = document.documentElement, before = root.dataset.theme, e = document.getElementById('ens-progress');
        const out = {};
        for (const t of ['light','dark','dim','paper','contrast','fjord','intellij-dark']) {
          root.dataset.theme = t;
          const s = getComputedStyle(e);
          out[t] = { foreground: s.color, background: s.backgroundColor };
        }
        root.dataset.theme = before;
        return out;
      })()`);
      await shot('0-progress-during');
      await until('!document.getElementById("ens-progress").hidden && document.getElementById("ens-progress").textContent.includes("Sorting messages into threads") && !document.getElementById("ens-progress").classList.contains("indeterminate")', 15000);
      o.progressCount = await evalIn('document.getElementById("ens-progress").textContent');
      await shot('0-progress-count');
      await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0', 30000);
      await until('document.getElementById("ens-progress").hidden', 15000);
      o.progressVisibleMs = Date.now() - progressShownAt;
      o.progressAfter = await evalIn('document.getElementById("ens-progress").hidden');
      await shot('0-progress-after');
      await sleep(400);
      o.channel = await evalIn(VIEW);
      await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
      await until('!!document.querySelector("#points-list .pt-thr")', 10000);
      o.sum = await evalIn('(e => e ? e.textContent : "")(document.querySelector("#points-line .pt-sum-t"))');
      o.needsThreads = await evalIn('[...document.querySelectorAll("#points-list .pt-thr")].map(e => e.dataset.thr)');
      await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
      await shot('1-channel');
      // The whole chat, and back.
      await evalIn('document.getElementById("whole-chat").click(); 0');
      await until('document.getElementById("whole-chat").getAttribute("aria-pressed") === "true" && !THR_DRAWN || document.querySelectorAll("#msgs .msg[data-mid]").length > ' + o.channel.mids.length, 15000);
      await sleep(400);
      o.whole = await evalIn(VIEW);
      await evalIn('document.getElementById("whole-chat").click(); 0');
      await until('document.querySelectorAll("#msgs .thr-line").length > 0', 15000);
      // A thread: all its messages, and a reply in it, also sent to the chat.
      await go('id=' + room + '&thread=P1');
      o.thread = await evalIn(VIEW);
      o.title = await evalIn('document.getElementById("title").textContent');
      o.also = await evalIn('(e => e && !e.hidden && e.getBoundingClientRect().width > 0)(document.getElementById("thr-also-l"))');
      await shot('2-thread');
      await evalIn('document.getElementById("input").value = "A bit wider still, please."; document.getElementById("input").dispatchEvent(new Event("input")); document.getElementById("thr-also").checked = true; document.getElementById("send").click(); 0');
      o.warmProgress = await evalIn(`(async () => {
        let shown = 0;
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 150));
          if (!document.getElementById('ens-progress').hidden) shown++;
        }
        return {shown, text: document.getElementById('ens-progress').textContent};
      })()`);
      await until(`[...document.querySelectorAll('#msgs .msg[data-mid]')].length >= ${o.thread.mids.length + 2}`, 20000);
      await sleep(400);
      o.threadAfter = await evalIn(VIEW);
      await shot('3-replied');
      // Back in the chat: the reply is in it too, and the thread was read.
      await go('room=' + room);
      o.channelAfter = await evalIn(VIEW);
      await shot('4-channel-after');
      // A thread drawn while it is not on screen (a Dock tab behind another) is not
      // read; shown again, the next poll reads it even when the hub has nothing new
      // to send ("the same", review 1).
      await go('id=' + room + '&thread=P2');
      // The reply above starts a rebuild in the background, and until it lands the
      // hub serves the last threads, "the same" too. So: wait for three polls in a
      // row that are the same (off screen, so waiting reads nothing), draw the
      // thread off screen, then poll on screen; again if that poll was not the same.
      o.offScreen = await evalIn(`(async () => {
        const f0 = window.fetch, pf = pageInFront, nap = ms => new Promise(r => setTimeout(r, ms));
        let same = false, res = null;
        window.fetch = async (u, ...a) => { const r = await f0(u, ...a); if (String(u).includes('/api/room/thread?')) same = !!(await r.clone().json()).same; return r; };
        const poll = async force => { same = false; while (THR_FETCHING) await nap(20); await renderThreaded(force); return same; };
        try {
          for (let tries = 1; tries <= 5 && !(res && res.same); tries++) {
            pageInFront = () => false;
            const end = Date.now() + 15000;
            for (let run = 0; run < 3 && Date.now() < end; await nap(400)) run = (await poll(false)) ? run + 1 : 0;
            THR_SEEN_TS = 0;
            await poll(true);
            const hidden = THR_SEEN_TS;
            pageInFront = pf;
            res = { tries, hidden, same: await poll(false), ts: THR_SEEN_TS };
          }
        } finally { window.fetch = f0; pageInFront = pf; }
        return res; })()`);
      await sleep(500);
      await go('room=' + room);
      o.channelLast = await evalIn(VIEW);
      out[w] = o;
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
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


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class ThreadsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-thr-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        cls.tdir = base / "transcripts" / "C--po"
        cls.tdir.mkdir(parents=True)
        (base / "cs").mkdir()
        cls.sent = {}
        cls.lines = {}
        cls.sids = {}

        def resume(h, room, text="", to="", key="", quiet=False):
            # The PO: the reply goes into its transcript, and it answers.
            rid = room["id"]
            sid = cls.sids[rid]
            cls.sent.setdefault(rid, []).append(text)
            now = time.time()
            cls.lines[rid] += [_turn(sid, "user", text, now), _turn(sid, "assistant", "Re P1: wider still, done.", now + 1)]
            cls._write(sid, cls.lines[rid])
            return {"delivered": 1}

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
            mock.patch.object(dashboard, "_send_left_behind", return_value=""),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        threads.forget()
        cls.rooms = {}
        for w, _h, _m in SIZES:
            cls.rooms[str(w)] = cls._po_room(f"Motors {w}", f"po{w}")
        original_balloons = threads.balloons
        slowed_rooms = set()
        def slow_balloons(*args, **kwargs):
            rid = args[0].get("id", "")
            if rid not in slowed_rooms:
                slowed_rooms.add(rid)
                time.sleep(2.2)
            return original_balloons(*args, **kwargs)
        slow_patch = mock.patch.object(threads, "balloons", side_effect=slow_balloons)
        slow_patch.start()
        cls.addClassCleanup(slow_patch.stop)
        original_assign = threads.assign
        slowed_assigns = set()
        def slow_assign(*args, **kwargs):
            rid = threading.current_thread().name
            if rid in slowed_assigns:
                return original_assign(*args, **kwargs)
            slowed_assigns.add(rid)
            callback = args[4]
            waited = [False]
            def report(done, total):
                callback(done, total)
                if not waited[0]:
                    waited[0] = True
                    time.sleep(2.2)
            return original_assign(*args[:4], report, **kwargs)
        assign_patch = mock.patch.object(threads, "assign", side_effect=slow_assign)
        assign_patch.start()
        cls.addClassCleanup(assign_patch.stop)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        cls.got = cls._chrome()

    @classmethod
    def _write(cls, sid, lines):
        (cls.tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")

    @classmethod
    def _po_room(cls, name: str, sid: str) -> str:
        ok, proj, _ = dashboard.register_project(name)
        assert ok, proj
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid}])
        rid = po["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(rid, proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], rid)
        assert ok, why
        cls.sids[rid] = sid
        t0 = time.time() - 3600
        lines = [_turn(sid, "assistant", "I'm the PO for Motors.", t0)]
        talk = [("P1", "Make the speed chart wider", ["Done: it takes the whole width now.", "Re P1: and the axis labels no longer overlap."], 10),
                ("P2", "Why is the brake light red?", ["Re P2: the pads are worn; #4 replaces them."], 400)]
        for pid, text, answers, dt in talk:
            q, ids = points.take(chatroom.get_room(rid, public=False), text, key=pid, now=t0 + dt, sid=sid)
            assert ids == [pid], ids
            lines.append(_turn(sid, "user", q, t0 + dt + 1))
            lines += [_turn(sid, "assistant", a, t0 + dt + 5 + i) for i, a in enumerate(answers)]
            if pid == "P1":
                lines += [_turn(sid, "user", "[digest] Motors: all quiet.", t0 + dt + 100),
                          _turn(sid, "assistant", "Noted, nothing to do.", t0 + dt + 101)]
            cls._write(sid, lines)
            points.sync(rid, force=True)
        cls.lines[rid] = lines
        # Threaded since before any of it: every reply is unread.
        threads._ensure_since(rid, t0 - 60)
        return rid

    @classmethod
    def _chrome(cls):
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "rooms": cls.rooms, "sizes": SIZES, "shots": cls.shots}
        script = base / "threads_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        return json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _each(self):
        return [(str(w), self.got[str(w)]) for w, _h, _m in SIZES]

    def test_progress_notice_during_and_after_build_at_desktop_and_phone(self):
        def luminance(rgb):
            parts = [int(x) / 255 for x in __import__('re').findall(r"\d+", rgb)[:3]]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in parts]
            return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
        for width in ("1728", "390"):
            with self.subTest(width=width):
                got = self.got[width]
                bar = got["progressDuring"]
                self.assertIn("Reading conversations", bar["text"])
                self.assertIn("Sorting messages into threads", got["progressCount"])
                self.assertIn("done", got["progressCount"])
                self.assertEqual(bar["scrollX"], 0)
                self.assertLessEqual(bar["right"], int(width))
                self.assertTrue(got["progressAfter"])
                self.assertGreater(got["progressVisibleMs"], 0)
                for theme, colors in got["progressThemes"].items():
                    with self.subTest(theme=theme):
                        a, b = luminance(colors["foreground"]), luminance(colors["background"])
                        self.assertGreaterEqual((max(a, b) + .05) / (min(a, b) + .05), 4.5)

    def test_warm_po_chat_shows_no_notice_as_transcript_grows(self):
        for width in ("1728", "390"):
            with self.subTest(width=width):
                self.assertEqual(self.got[width]["warmProgress"]["shown"], 0,
                                 self.got[width]["warmProgress"])

    def test_the_channel_shows_each_thread_once_with_its_line(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                ch = o["channel"]
                # The two asks, each once; none of their replies.
                self.assertIn(f"{sid}:1", ch["mids"])
                self.assertIn(f"{sid}:6", ch["mids"])
                for reply in (2, 3, 7):
                    self.assertNotIn(f"{sid}:{reply}", ch["mids"])
                lines = {x["thr"]: x for x in ch["lines"]}
                self.assertEqual(sorted(lines), ["P1", "P2"])
                self.assertTrue(lines["P1"]["text"].startswith("2 replies"), lines["P1"])
                self.assertIn("PO", lines["P1"]["text"])
                self.assertTrue(lines["P1"]["unread"] and lines["P1"]["dot"], "replies not seen: bold, with the dot")
                self.assertEqual(o["needsThreads"], ["P2", "P1"], "Needs you lists them, newest first")
                self.assertIn("2 threads with new replies", o["sum"])
                self.assertEqual(ch["whole"], "false")
                self.assertLessEqual(ch["scrollX"], 1)

    def test_whole_chat_shows_every_message(self):
        for w, o in self._each():
            with self.subTest(width=w):
                self.assertGreater(len(o["whole"]["mids"]), len(o["channel"]["mids"]))
                self.assertIn(f"po{w}:3", o["whole"]["mids"])
                self.assertEqual(o["whole"]["lines"], [])

    def test_a_thread_has_all_its_messages_and_a_reply_lands_in_it(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                self.assertEqual(o["thread"]["mids"], [f"{sid}:1", f"{sid}:2", f"{sid}:3"])
                self.assertEqual(o["title"], "Make the speed chart wider")
                self.assertTrue(o["also"], "Also send to chat is under the box")
                sent = self.sent[self.rooms[w]]
                self.assertEqual(len(sent), 1)
                self.assertTrue(sent[0].startswith("Re P1: A bit wider still, please."), sent[0])
                self.assertEqual(o["threadAfter"]["mids"][-2:], [f"{sid}:8", f"{sid}:9"], "the reply and the PO's answer, in the thread")

    def test_back_in_the_chat_the_reply_shows_and_the_thread_is_read(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"po{w}"
                after = o["channelAfter"]
                self.assertIn(f"{sid}:8", after["mids"], "sent to the chat too")
                self.assertNotIn(f"{sid}:9", after["mids"], "the PO's answer stays in the thread")
                self.assertTrue(any(t.startswith("Replied in") for t in after["ins"]), after["ins"])
                lines = {x["thr"]: x for x in after["lines"]}
                self.assertTrue(lines["P1"]["text"].startswith("4 replies"), lines["P1"])
                self.assertFalse(lines["P1"]["unread"], "opened: read")
                self.assertTrue(lines["P2"]["unread"], "never opened: still unread")

    def test_a_thread_drawn_off_screen_is_read_once_it_shows(self):
        for w, o in self._each():
            with self.subTest(width=w):
                seen = o["offScreen"]
                self.assertEqual(seen["hidden"], 0, "not on screen: not read")
                self.assertEqual(seen["same"], True, f"the hub had nothing new ({seen['tries']} tries)")
                self.assertGreater(seen["ts"], 0, "on screen: read by that poll")
                lines = {x["thr"]: x for x in o["channelLast"]["lines"]}
                self.assertFalse(lines["P2"]["unread"])


if __name__ == "__main__":
    unittest.main()
