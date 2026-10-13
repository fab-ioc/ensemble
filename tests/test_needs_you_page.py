"""Needs you in a PO chat, in the browser (#209).

In headless Chrome over CDP, against a hub in a thread serving a solo PO room
whose transcript is a file, at 1728x1117, 390x844 (a phone) and 4096x2160,
each size its own project and PO:

* the list under the chat's line holds only what waits on the person: the
  PO's question card on top (Decisions), then the new answers, newest first;
  what waits on the PO is a closed fold;
* a click on the question lands on the card's ask (the landing mark, on screen);
* the answer on screen for a second is marked seen by the page (``screen``),
  the one opened from its "answer ↓" link too (``link``), and the one never on
  screen is not;
* the person writes again: the seen ones close as *seen*, the unseen one stays;
  reloaded, the list has it alone under New answers, the two under Closed.

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
// The list as the person reads it: group heads, fold buttons and rows, in order.
const LIST = `[...document.querySelectorAll('#points-list > li')].map(li => li.classList.contains('pt-fold') ? 'fold:' + li.textContent.trim().slice(1)
  : li.classList.contains('pt-group') ? 'head:' + li.textContent.trim()
  : li.classList.contains('pt-ask') ? 'ask:' + li.querySelector('.pt-words').textContent.trim()
  : li.dataset.pt ? li.dataset.pt + ':' + ((li.querySelector('.pt-state') || {}).textContent || '') : 'note:' + li.textContent.trim())`;
// Each answer's balloon: on screen in the chat or not.
const IN_VIEW = `(() => { const box = document.getElementById('msgs'), br = box.getBoundingClientRect(), o = {};
  for (const p of POINTS.items) { const a = lastAnswer(p); const el = a && a.mid && box.querySelector('.msg[data-mid="' + CSS.escape(a.mid) + '"]');
    if (!el) { o[p.id] = null; continue; } const r = el.getBoundingClientRect(); o[p.id] = r.bottom > br.top && r.top < br.bottom; }
  return o; })()`;
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
      await c.send('Network.enable', {}, sessionId);
      // The flat chat (#210's "Whole chat"): the answers on screen are what this is about;
      // in the threads view they are in their threads (tests/test_threads_page.py).
      await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'try { localStorage.setItem("cd-chat-whole", "1"); } catch (e) {}' }, sessionId);
      // The page is in front, as the person's window is.
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const block = on => c.send('Network.setBlockedURLs', { urls: on ? ['*/api/room/points/seen*'] : [] }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `needs-you-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
      const load = async () => {
        await c.send('Page.navigate', { url: A.base + '/session?room=' + room }, sessionId);
        await until('document.querySelector("#whole-chat") && !document.querySelector("#whole-chat").hidden');
        await evalIn('document.querySelector("#whole-chat").click()');
        await until('typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0 && typeof POINTS !== "undefined" && POINTS.items.length >= 3', 30000);
        await sleep(300);
        await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
        await until('!!document.querySelector("#points-list .pt-ask")', 20000);
      };
      if (A.stage === 1) {
        // What the person reads first, before anything is marked seen.
        await block(true);
        await load();
        o.list = await evalIn(LIST);
        o.sum = await evalIn('document.querySelector("#points-line .pt-sum-t").textContent');
        o.inView = await evalIn(IN_VIEW);
        await shot('1-list');
        // The list closed, the answer on screen is seen once the page may say so
        // (on a phone the open list covers it: not seen while it does).
        await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
        await block(false);
        // The fixed open-question line reduces the phone chat viewport. Bring
        // P3's answer fully into view before asserting that it counts as read.
        await evalIn(`(() => { const p = POINTS.items.find(x => x.id === 'P3'), a = lastAnswer(p);
          document.querySelector('#msgs .msg[data-mid="' + CSS.escape(a.mid) + '"]').scrollIntoView({ block: 'center' }); return 0; })()`);
        o.screenLeft = await until('POINTS.items.find(p => p.id === "P3").seen === true', 15000).then(() => true, () => false);
        o.inViewSeen = await evalIn(IN_VIEW);
        await evalIn('document.querySelector("#points-line .pt-sum").click(); 0');
        await until('!!document.querySelector("#points-list .pt-ask")', 20000);
        // P2's answer from its link: the chat lands there, and it is seen once the mark faded.
        await evalIn('document.querySelector("#points-list .pt-row[data-pt=\\"P2\\"] a.pt-link[data-ref-part=\\"re:P2\\"]").click(); 0');
        o.linkLeft = await until('!document.querySelector("#points-list .pt-row[data-pt=\\"P2\\"]")', 15000).then(() => true, () => false);
        o.inViewAfterLink = await evalIn(IN_VIEW);
        // The question: a click lands on the card's ask.
        o.cardMid = await evalIn('document.querySelector("#points-list .pt-ask").dataset.mid');
        await evalIn('document.querySelector("#points-list .pt-ask a.pt-link").click(); 0');
        await sleep(500);
        o.land = await evalIn(`(() => { const box = document.getElementById('msgs'), br = box.getBoundingClientRect();
          const el = box.querySelector('.msg[data-mid="' + CSS.escape(${JSON.stringify(o.cardMid)}) + '"] .landed, .msg.landed[data-mid="' + CSS.escape(${JSON.stringify(o.cardMid)}) + '"]'); if (!el) return null; const r = el.getBoundingClientRect(), m = el.closest('.msg[data-mid]');
          return { mid: m ? m.dataset.mid : '', text: el.textContent.slice(0, 80), inView: r.top >= br.top - 1 && r.top < br.bottom,
                   part: (new URLSearchParams(location.search)).get('part') || '' }; })()`);
        await shot('2-landed');
        o.listAfter = await evalIn(LIST);
        o.scrollX = await evalIn('document.documentElement.scrollWidth - innerWidth');
      } else {
        await block(true);
        await load();
        o.list = await evalIn(LIST);
        o.sum = await evalIn('document.querySelector("#points-line .pt-sum-t").textContent');
        await evalIn('document.querySelector("#points-list .pt-fold-sum[data-fold=\\"closed\\"]").click(); 0');
        await sleep(300);
        o.closed = await evalIn(LIST);
        await shot('3-closed');
      }
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


def _filler(sid: str, at: float, tag: str) -> list[dict]:
    """Messages for the person (not team activity), tall enough that what is
    above them is off screen even at 2160 px."""
    body = "\n".join(f"- line {j}: the brakes, the pads and the discs, measured on the long descent" for j in range(8))
    return [_turn(sid, "assistant", f"For you: {tag} note {i}, nothing to decide.\n\n{body}", at + i) for i in range(30)]


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class NeedsYouPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-needs-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        cls.rooms, cls.lines = {}, {}
        for w, _h, _m in SIZES:
            cls.rooms[str(w)] = cls._po_room(f"Motors {w}", f"po{w}")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        cls.got1 = cls._chrome(1)
        cls.seen = {w: cls._ledger(rid) for w, rid in cls.rooms.items()}
        # The person writes again in each chat.
        for w, rid in cls.rooms.items():
            sid = f"po{w}"
            q, ids = points.take(chatroom.get_room(rid, public=False), "Thanks. Next: the rear brakes.", key="k9", sid=sid)
            cls.lines[w].append(_turn(sid, "user", q, time.time()))
            cls._write(sid, cls.lines[w])
            points.sync(rid, force=True)
        cls.after = {w: cls._ledger(rid) for w, rid in cls.rooms.items()}
        cls.got2 = cls._chrome(2)

    @classmethod
    def _write(cls, sid, lines):
        (cls.tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")

    @classmethod
    def _po_room(cls, name: str, sid: str) -> str:
        ok, proj, _ = dashboard.register_project(name)
        assert ok, proj
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid}])
        room = chatroom.get_room(po["id"], public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        # P1 answered long ago, far up; P2 in the middle; P3 and the PO's
        # question at the end, where the chat opens.
        t0 = time.time() - 3600
        lines = [_turn(sid, "assistant", "I'm the PO for Motors.", t0)]
        asks = [("P1", "Why do the brakes squeal?", "Re P1: the pads glaze on a long descent; new pads are on the board as #4.", 10),
                ("P2", "How hot do the discs get?", "Re P2: 420 °C at the bottom of the pass, measured twice.", 400),
                ("P3", "Which pads did we order?", "Re P3: the sintered ones, delivered on Monday.", 800)]
        for pid, text, answer, dt in asks:
            room = chatroom.get_room(po["id"], public=False)
            q, ids = points.take(room, text, key=pid, now=t0 + dt, sid=sid)
            assert ids == [pid], ids
            lines += [_turn(sid, "user", q, t0 + dt + 1), _turn(sid, "assistant", answer, t0 + dt + 5)]
            if pid != "P3":
                lines += _filler(sid, t0 + dt + 10, pid)
            cls._write(sid, lines)
            points.sync(po["id"], force=True)
        lines.append(_turn(sid, "assistant", "Decision needed: replace the discs too?\n\n- yes: they are worn\n- no: wait a month\n\nI recommend yes.", t0 + 900))
        cls._write(sid, lines)
        points.sync(po["id"], force=True)
        cls.lines[name.split()[-1]] = lines
        return po["id"]

    @classmethod
    def _ledger(cls, rid):
        return {p["id"]: {k: p.get(k) for k in ("state", "seenHow", "ackedBy")} for p in points.load(rid)["points"]}

    @classmethod
    def _chrome(cls, stage):
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "rooms": cls.rooms, "sizes": SIZES, "stage": stage, "shots": cls.shots}
        script = base / "needs_you_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        return json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _each(self, got):
        return [(str(w), got[str(w)]) for w, _h, _m in SIZES]

    def test_the_list_holds_what_waits_on_the_person_the_question_on_top(self):
        for w, o in self._each(self.got1):
            with self.subTest(width=w):
                self.assertFalse(o["inView"]["P1"] or o["inView"]["P2"], "the chat opens at the end")
                self.assertTrue(o["inViewSeen"]["P3"], "the list closed, the last answer is on screen")
                self.assertEqual(o["list"], ["head:Decisions", "ask:replace the discs too?", "head:New answers",
                                             "P3:new answer", "P2:new answer", "P1:new answer"], o["list"])
                self.assertEqual(o["sum"], "Needs you: 1 decision · 3 new answers")
                self.assertLessEqual(o["scrollX"], 1)

    def test_on_screen_and_from_a_link_are_seen_the_rest_is_not(self):
        for w, o in self._each(self.got1):
            with self.subTest(width=w):
                self.assertTrue(o["screenLeft"], "P3's answer was on screen: seen, out of New answers")
                self.assertTrue(o["linkLeft"], "P2's answer was opened from its link: seen")
                self.assertFalse(o["inViewAfterLink"]["P1"], "P1 was never on screen")
                self.assertEqual(o["listAfter"][:3], ["head:Decisions", "ask:replace the discs too?", "head:New answers"])
                self.assertIn("P1:new answer", o["listAfter"])
                led = self.seen[w]
                self.assertEqual(led["P3"]["seenHow"], "screen")
                self.assertIn(led["P2"]["seenHow"], ("screen", "link"), "the link lands it on screen: whichever tells first")
                self.assertIsNone(led["P1"]["seenHow"])
                self.assertEqual({k: v["state"] for k, v in led.items()}, {"P1": "delivered", "P3": "delivered", "P2": "delivered"},
                                 "seen alone closes nothing")

    def test_a_click_on_the_question_lands_on_the_card(self):
        for w, o in self._each(self.got1):
            with self.subTest(width=w):
                land = o["land"]
                self.assertIsNotNone(land, "a passage carries the landing mark")
                self.assertEqual(land["mid"], o["cardMid"])
                self.assertTrue(land["inView"], land)

    def test_writing_again_closes_the_seen_answers_only(self):
        for w, _o in self._each(self.got2):
            with self.subTest(width=w):
                led = self.after[w]
                self.assertEqual((led["P3"]["state"], led["P3"]["ackedBy"]), ("acked", "seen"))
                self.assertEqual((led["P2"]["state"], led["P2"]["ackedBy"]), ("acked", "seen"))
                self.assertEqual(led["P1"]["state"], "delivered", "never seen: it stays")

    def test_reloaded_the_unseen_answer_is_alone_the_seen_ones_are_closed(self):
        for w, o in self._each(self.got2):
            with self.subTest(width=w):
                self.assertEqual(o["list"], ["head:Decisions", "ask:replace the discs too?", "head:New answers", "P1:new answer",
                                             "fold:With the PO (1)", "fold:Closed (2)"], o["list"])
                self.assertEqual(o["sum"], "Needs you: 1 decision · 1 new answer")
                closed = o["closed"][o["closed"].index("fold:Closed (2)") + 1:]
                self.assertEqual(sorted(closed), ["P2:seen", "P3:seen"])


if __name__ == "__main__":
    unittest.main()
