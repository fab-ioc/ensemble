"""CDP coverage for links to a PO chat's cards and room messages (#262).

A card (a message with asks) is a room message the PO chat draws among its
transcript's turns, never in a thread. Its link, with or without
``&part=ask:N``, lands on it in the chat, older than the channel's first page
too; a message the chat does not show, or does not have, says so instead of
opening an empty thread beside the chat. Skipped without Node and Chrome.
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
SIZES = [(1280, 800, False), (390, 844, True)]

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
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
// Where a link left the chat: the balloon (on screen, marked), the card the
// part names (marked), the thread beside the chat, the note under the chat.
const SEEN = (mid, part) => `(() => {
  const box = document.getElementById('msgs'), list = box.getBoundingClientRect();
  const el = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
  const r = el && el.getBoundingClientRect();
  const card = el && ${JSON.stringify(part)} ? [...el.querySelectorAll('.qa[data-ask]')].find(e => e.dataset.ask === ${JSON.stringify(mid)} + ':' + ${JSON.stringify(part)}.slice(4)) : null;
  const cr = card && card.getBoundingClientRect();
  const note = document.getElementById('goto-note'), side = document.getElementById('thr-side'), f = document.getElementById('ts-frame');
  return { drawn: !!el, visible: !!r && r.top < list.bottom && r.bottom > list.top,
    landed: !!el && el.classList.contains('landed'), card: !!card, cardLanded: !!card && card.classList.contains('landed'),
    cardVisible: !!cr && cr.top < list.bottom && cr.bottom > list.top,
    side: !side.hidden, frame: f.getAttribute('src') || '', note: note && !note.hidden ? note.textContent.trim() : '',
    opened: sessionStorage.getItem('__secondWindow') || '' };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of A.sizes) {
      const o = {};
      for (const link of A.links) {
        const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
        const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
        await c.send('Page.enable', {}, sessionId);
        await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
        if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
        await c.send('Page.addScriptToEvaluateOnNewDocument', {
          source: 'window.open = url => { sessionStorage.setItem("__secondWindow", String(url)); return null; };'
            + ' if (window.parent === window) try { localStorage.clear(); } catch (e) {}' }, sessionId);   // no thread kept open by the last link
        const evalIn = async expr => { const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId);
          if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
        // The link as an agent writes it: the hub's loopback address.
        await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room + '&msg=' + encodeURIComponent(link.mid).replace(/%3A/g, ':')
          + (link.part ? '&part=' + link.part : '') }, sessionId);
        let v = null;
        const t = Date.now();
        while (Date.now() - t < 30000) {
          try { v = await evalIn(SEEN(link.mid, link.part || '')); } catch (e) { v = null; }
          if (v && ((link.part ? v.cardLanded : v.landed) && v.visible || v.note || v.side)) break;
          await sleep(150);
        }
        await sleep(1500);   // a later poll must not take it away again
        o[link.key] = { first: v, after: await evalIn(SEEN(link.mid, link.part || '')) };
        await c.send('Target.closeTarget', { targetId });
      }
      out[w] = o;
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

ASKS = [
    {"n": 0, "question": "Sandbox: how far did you get?", "kind": "decision",
     "options": [{"label": "Sandbox file ready", "detail": "", "recommended": True},
                 {"label": "Not done yet", "detail": "", "recommended": False}]},
    {"n": 1, "question": "Data trials: start them?", "kind": "decision",
     "options": [{"label": "Start both", "detail": "", "recommended": True},
                 {"label": "Skip", "detail": "", "recommended": False}]},
]


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class CardLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-card-", ignore_cleanup_errors=True)
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
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        threads.forget()
        ok, proj, _ = dashboard.register_project("Trading")
        assert ok, proj
        sid = "potrade"
        po = chatroom.create_room("PO", [{"identity": "claude-2", "agent": "claude", "role": "Product owner", "sessionId": sid}])
        rid = cls.room = po["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(rid, proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], rid)
        assert ok, why
        t0 = time.time() - 7200
        threads._ensure_since(rid, t0 - 60)
        lines = [_turn(sid, "assistant", "I'm the PO for Trading.", t0)]
        lines += [_turn(sid, "user" if i % 2 else "assistant", f"Entry {i}.", t0 + 100 + i * 10) for i in range(160)]
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")

        def post(at, text, asks=None):
            with mock.patch.object(chatroom, "_now", return_value=at):
                return chatroom.post_message(rid, "claude-2", text, to="user", structured_asks=asks,
                                             ask_audience="user" if asks else "")["message"]["id"]
        # A card older than the channel's first page (P826's links), one in
        # it (P214's), and a plain room message the chat does not draw.
        cls.old_card = post(t0 + 50, "Two cards for you.", ASKS)
        cls.new_card = post(t0 + 100 + 150 * 10 + 5, "Two older cards' links didn't open, so here they are again.", ASKS)
        cls.plain = post(t0 + 100 + 140 * 10 + 5, "A note the chat does not show.")
        points.sync(rid, force=True)
        threads.build(rid, show_progress=False)
        cls.turn_old = f"{sid}:4"          # a transcript turn far above the first page
        cls.turn_new = f"{sid}:{len(lines) - 3}"
        cls.links = [
            {"key": "newCard", "mid": cls.new_card},
            {"key": "newCardAsk0", "mid": cls.new_card, "part": "ask:0"},
            {"key": "newCardAsk1", "mid": cls.new_card, "part": "ask:1"},
            {"key": "oldCardAsk0", "mid": cls.old_card, "part": "ask:0"},
            {"key": "oldCardAsk1", "mid": cls.old_card, "part": "ask:1"},
            {"key": "turnOld", "mid": cls.turn_old},
            {"key": "turnNew", "mid": cls.turn_new},
            {"key": "plain", "mid": cls.plain},
            {"key": "gone", "mid": "0123456789ab", "part": "ask:0"},
        ]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": rid, "links": cls.links, "sizes": SIZES}
        script = base / "card_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _each(self, key):
        for w, _h, _m in SIZES:
            yield str(w), self.got[str(w)][key]["after"]

    def test_a_card_link_lands_on_the_card_in_the_chat(self):
        for key in ("newCardAsk0", "newCardAsk1", "oldCardAsk0", "oldCardAsk1"):
            for w, v in self._each(key):
                with self.subTest(link=key, width=w):
                    self.assertFalse(v["side"], "no thread beside the chat")
                    self.assertEqual(v["opened"], "", "no window of its own")
                    self.assertTrue(v["drawn"] and v["card"], v)
                    self.assertTrue(v["cardVisible"], v)
                    first = self.got[w][key]["first"]
                    self.assertTrue(first["cardLanded"], first)
                    self.assertEqual(v["note"], "")

    def test_a_message_link_lands_on_its_balloon(self):
        for key in ("newCard", "turnOld", "turnNew"):
            for w, v in self._each(key):
                with self.subTest(link=key, width=w):
                    self.assertFalse(v["side"], v)
                    self.assertTrue(v["drawn"] and v["visible"], v)
                    self.assertTrue(self.got[w][key]["first"]["landed"], self.got[w][key]["first"])

    def test_a_message_the_chat_does_not_show_says_so(self):
        for key, words in (("plain", "not shown in this chat"), ("gone", "not found")):
            for w, v in self._each(key):
                with self.subTest(link=key, width=w):
                    self.assertFalse(v["side"], "never an empty thread")
                    self.assertEqual(v["opened"], "")
                    self.assertIn(words, v["note"])


class CardLinkWindow(unittest.TestCase):
    """The hub's half: a channel asked to reach a room message places its
    window by the message's time and says what it is (``uptoAt``)."""

    def test_upto_a_room_message_reaches_it_by_its_time(self):
        recs = {f"s:{i}": {"ts": 100.0 + i} for i in range(200)}
        idx = {"by_mid": recs}
        seq = list(recs)
        start, end = threads._span(idx, seq, 40, "card", upto_ts=150.5)
        self.assertLessEqual(start, 51)
        self.assertGreater(end, 51)
        self.assertEqual(threads._span(idx, seq, 40, "card"), (160, 200), "no time: the last page")


if __name__ == "__main__":
    unittest.main()
