"""CDP coverage for a quieter PO chat (#402; issues 61 and 60, P413, P422).

A PO chat whose transcript has a turn of four mid-turn lines and a reply
(one of the lines a "Re P2:" answer, so a message of its own), team activity
(a digest and the PO's note on it), and a turn still running (two mid-turn
lines, no reply). Checked at a computer's and a phone's width:

- the reply shows "4 steps ▸", which opens them in place, in order, with
  their times, and folds them again; the "Re P2:" line is a balloon with its
  Ack, and no step is a balloon of its own;
- while the PO works, its last line is the one live line above the box (no
  terminal lines) and not a balloon; once it stops, it is a balloon again;
- Hide team activity, set in another tab (a storage event): no Team activity
  row, no state line, no "answers Pn", the Ack kept, the phone's Team
  activity filter gone; Shown again brings them back;
- set on the hub, a reload is Hidden; a link to a hidden message lands on it
  under "Team activity shown to open this message", and "Hide it again"
  hides it; a link to a step opens its reply's steps on it.

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
SIZES = [(1280, 900, False), (390, 844, True)]


def _interim(sid: str, text: str, at: float) -> dict:
    """A line the PO writes before a tool call (stop reason tool_use)."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at)) + ".000Z"
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "text", "text": text},
                                    {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}}


STEPS = ["Carrying on with merging #395.", "Merging #395 Part 1 now.",
         "Checking every page and the hub log.", "Now update the handover."]

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
  const words = e => e ? e.textContent.replace(/\\s+/g, ' ').trim() : '';
  const msgs = [...document.querySelectorAll('#msgs .msg[data-mid]')];
  const lt = document.getElementById('livetail'), pf = document.querySelector('[data-phone-action="team-all"]');
  const note = document.querySelector('#msgs .team-shown-note');
  return {
    texts: msgs.map(e => words(e.querySelector('.text.md'))),
    mids: msgs.map(e => e.dataset.mid),
    steps: [...document.querySelectorAll('#msgs .steps-btn')].map(b => ({ mid: b.dataset.steps, text: words(b), open: b.getAttribute('aria-expanded'),
      list: [...(b.closest('.msg').querySelectorAll('.steps-list li'))].map(li => ({ at: words(li.querySelector('.at')), text: words(li.querySelector('.step-text')),
        mid: li.dataset.step, landed: li.classList.contains('landed') })) })),
    gaps: document.querySelectorAll('#msgs .msg-fold.gap').length,
    states: document.querySelectorAll('#msgs .pt-st').length,
    chips: [...document.querySelectorAll('#msgs .pt-chip')].map(words),
    acks: [...document.querySelectorAll('#msgs [data-pt-act="ack"]')].map(b => b.dataset.pt),
    live: { shown: !!lt && !lt.hidden, po: words(lt && lt.querySelector('.po-live')), who: words(lt && lt.querySelector('.po-live .lt-who')),
      text: words(lt && lt.querySelector('.po-live .lt-text')), at: words(lt && lt.querySelector('.po-live .at')),
      terminal: !!(lt && lt.querySelector('.lt-lines')),
      oneLine: (() => { const t = lt && lt.querySelector('.po-live .lt-text'); return !!t && t.getBoundingClientRect().height <= 20; })() },
    phoneFilter: pf ? !pf.hidden : null,
    note: note ? { text: words(note), next: (note.nextElementSibling && note.nextElementSibling.dataset.mid) || '' } : null,
    landed: [...document.querySelectorAll('#msgs .msg.landed')].map(e => e.dataset.mid),
    scrollX: document.documentElement.scrollWidth - innerWidth,
  };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of A.sizes) {
      const room = A.rooms[String(w)], o = {};
      const attach = async () => {
        const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
        const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
        await c.send('Page.enable', {}, sessionId);
        await c.send('Runtime.enable', {}, sessionId);
        await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
        return { targetId, sessionId };
      };
      const { targetId, sessionId } = await attach();
      const evalOn = async (sid, expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sid); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const evalIn = expr => evalOn(sessionId, expr);
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(120); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `quiet-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
      const settled = async () => { await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && !THR_FETCHING', 30000); await sleep(500); };
      const go = async (q) => { await c.send('Page.navigate', { url: A.base + '/session?' + q }, sessionId); await settled(); };
      await go('room=' + room);
      await until('document.querySelectorAll("#msgs .steps-btn").length >= 3', 20000);
      // Who works: set by the test, not by the hub's poll.
      await evalIn('renderActivity = function () { renderLiveTail(); }; 0');
      o.ids = await evalIn(`(() => { const f = re => (LAST_ITEMS.find(m => re.test(m.text || '')) || {}); const own = f(/^Re P1:/);
        return { noted: f(/^Noted/).id, owner: own.id, step: ((own.steps || [])[2] || {}).mid }; })()`);
      o.first = await evalIn(VIEW);
      await shot('1-folded');
      // "4 steps ▸" opens them in place, and folds them again.
      await evalIn(`document.querySelector('#msgs .steps-btn[data-steps="${o.ids.owner}"]').click(); 0`);
      await until(`document.querySelectorAll('#msgs .msg[data-mid="${o.ids.owner}"] .steps-list li').length === 4`, 5000);
      o.opened = await evalIn(VIEW);
      await shot('2-steps-open');
      await evalIn(`document.querySelector('#msgs .steps-btn[data-steps="${o.ids.owner}"]').click(); 0`);
      await until(`!document.querySelector('#msgs .msg[data-mid="${o.ids.owner}"] .steps-list')`, 5000);
      o.closed = await evalIn(VIEW);
      // The PO works: its last line is the live line, not a balloon.
      await evalIn(`ROOM_LIVE = true; TAIL_WORKING = [{ identity: 'claude', ptyId: '' }]; renderBubbles(LAST_ITEMS, { force: true }); renderLiveTail(); 0`);
      await until('!!document.querySelector("#livetail .po-live")', 5000);
      o.working = await evalIn(VIEW);
      await shot('3-live-line');
      await evalIn(`TAIL_WORKING = []; renderBubbles(LAST_ITEMS, { force: true }); renderLiveTail(); 0`);
      o.stopped = await evalIn(VIEW);
      // Hidden, set in another tab: a storage event here.
      const other = await attach();
      await c.send('Page.navigate', { url: A.base + '/api/health' }, other.sessionId);
      await sleep(600);
      await evalOn(other.sessionId, `localStorage.setItem('cd-po-team', 'hidden'); 0`);
      await until('document.querySelectorAll("#msgs .msg-fold.gap").length === 0', 5000);
      o.hidden = await evalIn(VIEW);
      await shot('4-hidden');
      await evalOn(other.sessionId, `localStorage.setItem('cd-po-team', 'shown'); 0`);
      await until('document.querySelectorAll("#msgs .msg-fold.gap").length > 0', 5000);
      o.shownAgain = await evalIn(VIEW);
      await c.send('Target.closeTarget', { targetId: other.targetId });
      // Hidden on the hub (the Preferences row's save): a reload is Hidden.
      await evalIn(`fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poTeamActivity: 'hidden' }) }).then(r => r.status)`);
      await evalIn(`localStorage.removeItem('cd-po-team'); 0`);
      await go('room=' + room);
      await until('PO_TEAM === "hidden" && document.querySelectorAll("#msgs .msg-fold.gap").length === 0', 10000);
      o.reloaded = await evalIn(VIEW);
      // A link to a hidden message lands on it, under the note.
      await go('room=' + room + '&msg=' + encodeURIComponent(o.ids.noted));
      await until(`!!document.querySelector('#msgs .msg[data-mid="${o.ids.noted}"]') && !!document.querySelector('#msgs .team-shown-note')`, 20000);
      await sleep(300);
      o.linked = await evalIn(VIEW);
      await shot('5-linked-hidden');
      await evalIn('document.querySelector("#msgs .team-hide-btn").click(); 0');
      await until(`!document.querySelector('#msgs .msg[data-mid="${o.ids.noted}"]')`, 5000);
      o.hiddenAgain = await evalIn(VIEW);
      // A link to a step: its reply's steps open on it.
      await go('room=' + room + '&msg=' + encodeURIComponent(o.ids.step));
      await until(`!!document.querySelector('#msgs li[data-step="${o.ids.step}"].landed')`, 20000);
      o.stepLink = await evalIn(VIEW);
      await shot('6-step-link');
      await evalIn(`fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poTeamActivity: 'shown' }) }).then(r => r.status)`);
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
class QuietPoChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-qp-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        cls.tdir = base / "transcripts" / "C--po"
        cls.tdir.mkdir(parents=True)
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
            mock.patch.object(dashboard, "_send_left_behind", return_value=""),
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
            rid = cls._po_room(f"Motors {w}", f"qp{w}")
            cls.rooms[str(w)] = rid
            threads.build(rid, show_progress=False)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "rooms": cls.rooms, "sizes": SIZES, "shots": cls.shots}
        script = base / "quiet_cdp.js"
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

        def ask(pid, text, at):
            q, ids = points.take(chatroom.get_room(rid, public=False), text, key=pid, now=at, sid=sid)
            assert ids == [pid], ids
            lines.append(_turn(sid, "user", q, at + 1))

        def write():
            (cls.tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
            points.sync(rid, force=True)

        ask("P1", "Merge #395 please", t0 + 10)
        write()
        ask("P2", "Why is the brake light red?", t0 + 20)
        write()
        # One turn: a line, a "Re P2:" answer (a message of its own), four
        # lines (the reply's steps), the reply.
        lines.append(_interim(sid, "Looking at both now.", t0 + 30))
        lines.append(_interim(sid, "Re P2: the pads are worn; #4 replaces them.", t0 + 40))
        lines += [_interim(sid, s, t0 + 60 + 60 * i) for i, s in enumerate(STEPS)]
        lines.append(_turn(sid, "assistant", "Re P1: I merged #395 Part 1. Nothing waits on you.", t0 + 400))
        write()
        # Team activity.
        lines += [_turn(sid, "user", "[digest] Motors: all quiet.", t0 + 500),
                  _turn(sid, "assistant", "Noted, nothing to do.", t0 + 501)]
        write()
        # A turn still running: two lines, no reply yet.
        ask("P3", "And #397?", t0 + 600)
        lines += [_interim(sid, "Merging #397 now.", t0 + 610), _interim(sid, "Checking the Inbox rows.", t0 + 620)]
        write()
        threads._ensure_since(rid, t0 - 60)
        return rid

    def _each(self):
        return [(str(w), self.got[str(w)]) for w, _h, _m in SIZES]

    def test_a_reply_shows_its_steps_folded_and_opens_them(self):
        for w, o in self._each():
            with self.subTest(width=w):
                f = o["first"]
                for s in STEPS:
                    self.assertNotIn(s, f["texts"], "a step is no balloon of its own")
                own = next(s for s in f["steps"] if s["mid"] == o["ids"]["owner"])
                self.assertEqual((own["text"], own["open"], own["list"]), ("4 steps ▸", "false", []))
                op = next(s for s in o["opened"]["steps"] if s["mid"] == o["ids"]["owner"])
                self.assertEqual(op["text"], "4 steps ▾")
                self.assertEqual(op["open"], "true")
                self.assertEqual([x["text"] for x in op["list"]], STEPS, "in order")
                self.assertTrue(all(len(x["at"]) == 5 and ":" in x["at"] for x in op["list"]), op["list"])
                self.assertEqual(o["opened"]["texts"], f["texts"], "opened in place: the same balloons")
                cl = next(s for s in o["closed"]["steps"] if s["mid"] == o["ids"]["owner"])
                self.assertEqual((cl["text"], cl["list"]), ("4 steps ▸", []))

    def test_a_re_answer_in_a_step_is_a_message_with_its_ack(self):
        for w, o in self._each():
            with self.subTest(width=w):
                f = o["first"]
                self.assertTrue(any(t.startswith("Re P2: the pads are worn") for t in f["texts"]), f["texts"])
                self.assertIn("P2", f["acks"])
                self.assertIn("P1", f["acks"])
                re2 = [s for s in f["steps"] if s["text"] == "1 step ▸"]
                self.assertGreaterEqual(len(re2), 1, "the line before it is its step")

    def test_the_live_line_while_the_po_works(self):
        for w, o in self._each():
            with self.subTest(width=w):
                f, k, s = o["first"], o["working"], o["stopped"]
                self.assertIn("Checking the Inbox rows.", f["texts"], "not working: the last line is a message")
                self.assertFalse(f["live"]["shown"])
                self.assertTrue(k["live"]["shown"])
                self.assertEqual(k["live"]["who"], "PO")
                self.assertEqual(k["live"]["text"], "Checking the Inbox rows.")
                self.assertRegex(k["live"]["at"], r"^\d\d:\d\d$")
                self.assertFalse(k["live"]["terminal"], "no terminal lines in a PO chat")
                self.assertTrue(k["live"]["oneLine"])
                self.assertNotIn("Checking the Inbox rows.", k["texts"], "the live line is no balloon")
                self.assertIn("Checking the Inbox rows.", s["texts"], "stopped: a message again")
                self.assertFalse(s["live"]["shown"])

    def test_hidden_hides_team_activity_and_keeps_the_ack(self):
        for w, o in self._each():
            with self.subTest(width=w):
                f, hd, sa = o["first"], o["hidden"], o["shownAgain"]
                self.assertGreater(f["gaps"], 0)
                self.assertGreater(f["states"], 0)
                self.assertTrue(any(c.startswith("answers P") for c in f["chips"]), f["chips"])
                self.assertEqual(hd["gaps"], 0)
                self.assertEqual(hd["states"], 0, "no state line under the person's message")
                self.assertFalse(any(c.startswith(("answers P", "plans P")) for c in hd["chips"]), hd["chips"])
                self.assertIn("P1", hd["acks"], "Ack stays")
                self.assertNotIn("Noted, nothing to do.", hd["texts"])
                if w != "1280":
                    self.assertTrue(f["phoneFilter"])
                    self.assertFalse(hd["phoneFilter"], "no Team activity filter while Hidden")
                self.assertGreater(sa["gaps"], 0, "Shown again at once")
                self.assertGreater(sa["states"], 0)

    def test_hidden_on_the_hub_applies_on_reload(self):
        for w, o in self._each():
            with self.subTest(width=w):
                r = o["reloaded"]
                self.assertEqual(r["gaps"], 0)
                self.assertEqual(r["states"], 0)

    def test_a_link_to_a_hidden_message_lands_under_the_note(self):
        for w, o in self._each():
            with self.subTest(width=w):
                k = o["linked"]
                self.assertIn(o["ids"]["noted"], k["mids"])
                self.assertIsNotNone(k["note"])
                self.assertEqual(k["note"]["text"], "Team activity shown to open this message.Hide it again")
                self.assertEqual(k["note"]["next"], o["ids"]["noted"], "the note is right above it")
                a = o["hiddenAgain"]
                self.assertIsNone(a["note"])
                self.assertNotIn(o["ids"]["noted"], a["mids"])
                self.assertEqual(a["gaps"], 0)

    def test_a_link_to_a_step_opens_its_reply_on_it(self):
        for w, o in self._each():
            with self.subTest(width=w):
                own = next(s for s in o["stepLink"]["steps"] if s["mid"] == o["ids"]["owner"])
                self.assertEqual(own["open"], "true")
                hit = [x for x in own["list"] if x["mid"] == o["ids"]["step"]]
                self.assertEqual(len(hit), 1)
                self.assertEqual(hit[0]["text"], STEPS[2])

    def test_no_sideways_scroll_and_no_page_errors(self):
        for w, o in self._each():
            with self.subTest(width=w):
                for step in ("first", "opened", "working", "hidden", "linked", "stepLink"):
                    self.assertLessEqual(o[step]["scrollX"], 0, step)
                self.assertEqual(o["errors"], [])


if __name__ == "__main__":
    unittest.main()
