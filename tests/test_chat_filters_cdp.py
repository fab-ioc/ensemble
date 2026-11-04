"""CDP coverage for the chat's filter (#304): All, Threads, Topics, Unread.

Topics stopped being a panel of its own on the PO page; the conversation got a
row of filter pills over it. Each viewport has a solo PO chat with two threads
(P1 with two replies, P2 with one) and Team activity; the project's topics are
mocked (topics.view) as one topic holding P2. Checked at desktop and phone
widths: the pills and their counts, each filter's balloons, Show all, a link
to a balloon outside the filter landing, no sideways scroll, no page errors.
Screenshots go to $ENSEMBLE_SHOTS when set. Skipped without Node and Chrome.
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
from tests.test_threads_page import _turn  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1728, 1117, False), (430, 932, True), (390, 844, True), (360, 740, True)]

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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 400));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const VIEW = `(() => {
  const row = document.getElementById('chat-filters'), pop = document.getElementById('topics-pop'), note = document.getElementById('topic-note');
  const words = e => e ? e.textContent.replace(/\\s+/g, ' ').trim() : '';
  const r = row.getBoundingClientRect(), pr = pop.getBoundingClientRect();
  return {
    mids: [...document.querySelectorAll('#msgs .msg[data-mid]')].map(e => e.dataset.mid),
    lines: [...document.querySelectorAll('#msgs .thr-line')].map(e => e.dataset.thrLine),
    cards: [...document.querySelectorAll('#msgs .thr-card')].map(e => e.dataset.thr),
    rowShown: !row.hidden && r.width > 0,
    pills: [...row.querySelectorAll('[data-filter]')].map(b => ({ f: b.dataset.filter, text: words(b), pressed: b.getAttribute('aria-pressed'),
      h: Math.round(b.getBoundingClientRect().height), right: Math.round(b.getBoundingClientRect().right) })),
    rowRight: Math.round(r.right),
    pop: { open: !pop.hidden, left: Math.round(pr.left), right: Math.round(pr.right), rows: [...pop.querySelectorAll('[data-tp-open]')].map(b => b.dataset.tpOpen) },
    note: { shown: !!note && !note.hidden, text: words(note) },
    stored: (() => { try { return localStorage.getItem('ens-chat-filter:' + ROOM); } catch (e) { return null; } })(),
    scrollX: document.documentElement.scrollWidth - innerWidth,
    width: innerWidth,
  };
})()`;
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
      await c.send('Runtime.enable', {}, sessionId);
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `filters-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
      const pill = f => `document.querySelector('#chat-filters [data-filter=${f}]')`;
      const settled = async () => { await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && !THR_FETCHING', 30000); await sleep(500); };
      const go = async (q) => { await c.send('Page.navigate', { url: A.base + '/session?' + q }, sessionId); await settled(); };
      await go('room=' + room);
      await until(pill('threads') + '.textContent.match(/\\d/) && ' + pill('topics') + '.textContent.match(/\\d/)', 20000);
      o.all = await evalIn(VIEW);
      await shot('1-all');
      // Threads: only the balloons of a thread.
      await evalIn(pill('threads') + '.click(); 0');
      await until(pill('threads') + '.getAttribute("aria-pressed") === "true"', 5000); await settled();
      o.threads = await evalIn(VIEW);
      await shot('2-threads');
      // Topics: the list, one picked, the chat shows that topic only.
      await evalIn(pill('topics') + '.click(); 0');
      await until('!document.getElementById("topics-pop").hidden && !!document.querySelector("#topics-pop [data-tp-open]")', 10000);
      o.popOpen = await evalIn(VIEW);
      await shot('3-topics');
      await evalIn('document.querySelector("#topics-pop [data-tp-open]").click(); 0');
      await until(pill('topics') + '.getAttribute("aria-pressed") === "true"', 5000); await settled();
      o.topic = await evalIn(VIEW);
      await shot('4-topic');
      // A reload keeps the filter.
      await go('room=' + room);
      o.reloaded = await evalIn(VIEW);
      // Show all, under the topic's note.
      await evalIn('document.querySelector("#topic-note button").click(); 0');
      await until(pill('all') + '.getAttribute("aria-pressed") === "true"', 5000); await settled();
      o.shownAll = await evalIn(VIEW);
      // Unread: the threads with new replies.
      await evalIn(pill('unread') + '.click(); 0');
      await until(pill('unread') + '.getAttribute("aria-pressed") === "true"', 5000); await settled();
      o.unread = await evalIn(VIEW);
      await shot('5-unread');
      // A link to a balloon the filter hides: the filter steps aside, the balloon lands.
      await evalIn(`localStorage.setItem('ens-chat-filter:' + ROOM, JSON.stringify({kind:'topic', topic:{id:'subject:P2', name:'Brake light'}})); 0`);
      await c.send('Page.navigate', { url: A.base + '/session?room=' + room + '&msg=' + encodeURIComponent(A.linkMid[String(w)]) }, sessionId);
      await until(`!!document.querySelector('#msgs .msg[data-mid="${A.linkMid[String(w)]}"]')`, 30000);
      await sleep(800);
      o.linked = await evalIn(VIEW);
      o.errors = c.errors.splice(0);
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


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class ChatFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-cf-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        cls.tdir = base / "transcripts" / "C--po"
        cls.tdir.mkdir(parents=True)
        (base / "cs").mkdir()
        now = time.time()

        def view(project, room, rooms_dir, **_):
            return {"topics": [{"id": "subject:P2", "name": "Brake light", "threads": ["P2"], "threadCount": 1,
                                "unread": 1, "decisions": 0, "latest": {"ts": now - 60, "text": "the pads are worn"}}],
                    "archived": [], "suggestions": {}}

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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.topics, "view", side_effect=view),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        threads.forget()
        cls.rooms, cls.link = {}, {}
        for w, _h, _m in SIZES:
            rid = cls._po_room(f"Motors {w}", f"cf{w}")
            cls.rooms[str(w)] = rid
            cls.link[str(w)] = f"cf{w}:0"          # the PO's first note: in no topic, in no thread
            threads.build(rid, show_progress=False)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "rooms": cls.rooms, "linkMid": cls.link, "sizes": SIZES, "shots": cls.shots}
        script = base / "filters_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

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
            (cls.tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
            points.sync(rid, force=True)
        threads._ensure_since(rid, t0 - 60)    # threaded since before any of it: every reply unread
        return rid

    def _each(self):
        return [(str(w), self.got[str(w)]) for w, _h, _m in SIZES]

    def test_the_pills_show_with_their_counts(self):
        for w, o in self._each():
            with self.subTest(width=w):
                a = o["all"]
                self.assertTrue(a["rowShown"])
                self.assertEqual([p["f"] for p in a["pills"]], ["all", "threads", "topics", "unread"])
                text = {p["f"]: p["text"] for p in a["pills"]}
                self.assertEqual(text["threads"], "Threads 2", "P1 and P2 have replies; Team activity is no thread")
                self.assertTrue(text["topics"].startswith("Topics 1"), text)
                self.assertEqual(text["unread"], "Unread 2")
                self.assertEqual([p["pressed"] for p in a["pills"]], ["true", "false", "false", "false"])
                self.assertEqual(sorted(a["lines"]), ["P1", "P2"])
                if w != "1728":
                    self.assertTrue(all(p["h"] >= 44 for p in a["pills"]), "a finger's size on a phone")

    def test_threads_shows_only_the_balloons_of_a_thread(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"cf{w}"
                t = o["threads"]
                self.assertEqual(t["mids"], [f"{sid}:1", f"{sid}:6"], "each thread's first message; the PO's note and Team activity hidden")
                self.assertEqual(sorted(t["lines"]), ["P1", "P2"])
                self.assertTrue(t["note"]["shown"] and t["note"]["text"].startswith("Showing threads only"), t["note"])
                self.assertEqual(json.loads(t["stored"]), {"kind": "threads"})

    def test_a_topic_picked_shows_that_topic_only_and_show_all_restores(self):
        for w, o in self._each():
            with self.subTest(width=w):
                sid = f"cf{w}"
                self.assertTrue(o["popOpen"]["pop"]["open"])
                self.assertEqual(o["popOpen"]["pop"]["rows"], ["subject:P2"])
                p = o["popOpen"]["pop"]
                self.assertGreaterEqual(p["left"], 0)
                self.assertLessEqual(p["right"], o["popOpen"]["width"], "the list stays on screen")
                t = o["topic"]
                self.assertFalse(t["pop"]["open"], "picking a topic closes the list")
                self.assertEqual(t["mids"], [f"{sid}:6"])
                self.assertIn("Brake light", t["note"]["text"])
                self.assertEqual(o["reloaded"]["mids"], [f"{sid}:6"], "a reload keeps the filter")
                s = o["shownAll"]
                self.assertIsNone(s["stored"])
                self.assertFalse(s["note"]["shown"])
                self.assertEqual(s["mids"], o["all"]["mids"])

    def test_unread_lists_the_threads_with_new_replies(self):
        for w, o in self._each():
            with self.subTest(width=w):
                u = o["unread"]
                self.assertEqual(u["cards"], ["P2", "P1"], "newest first")
                self.assertEqual(u["mids"], [])

    def test_a_link_to_a_balloon_the_filter_hides_lands(self):
        for w, o in self._each():
            with self.subTest(width=w):
                k = o["linked"]
                self.assertIn(f"cf{w}:0", k["mids"])
                self.assertEqual([p["pressed"] for p in k["pills"]][0], "true", "the filter stepped aside to All")

    def test_no_sideways_scroll_and_no_page_errors(self):
        for w, o in self._each():
            with self.subTest(width=w):
                for step in ("all", "threads", "popOpen", "topic", "unread", "linked"):
                    v = o[step]
                    self.assertLessEqual(v["scrollX"], 0, step)
                    self.assertLessEqual(v["rowRight"], v["width"], step)
                    self.assertTrue(all(p["right"] <= v["width"] for p in v["pills"]), step)
                self.assertEqual(o["errors"], [])


if __name__ == "__main__":
    unittest.main()
