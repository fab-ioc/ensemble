"""#247 review 4 in Chrome: a one-agent chat rotated once (two transcripts
of 300 turns). A link far back opens a window around its balloon, not the
turns between it and the latest; scrolling down the window joins it to the
latest, and Latest leaves it. Scrolled up from the latest, the chat goes on
into the session before, to its first turn.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_solo_window_page
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
from test_page_update import CHROME
from test_po_chat_clean import NODE, _turn
from tests import chrome_profile

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A), '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1000,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    const wsUrl = await new Promise((res, rej) => { let b = ''; ch.stderr.on('data', d => { b += d; const m = b.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
      setTimeout(() => rej(new Error('no devtools')), 20000); });
    const ws = new WebSocket(wsUrl); await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
    let id = 0; const waits = new Map(), EXC = [];
    ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown') EXC.push(JSON.stringify(m.params.exceptionDetails).slice(0, 500));
      const w = m.id && waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w.j(new Error(m.error.message)) : w.r(m.result); } };
    const send = (method, params = {}, sessionId) => new Promise((r, j) => { const i = ++id; waits.set(i, { r, j }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId); await send('Runtime.enable', {}, sessionId);
    const ev = async e => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true }, sessionId);
      if (r.exceptionDetails) throw new Error(e.slice(0, 100) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
    const until = async (e, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(e); } catch (x) {} if (v) return v; await sleep(100); }
      throw new Error('timeout: ' + e + ' :: ' + EXC.join(' | ')); };
    // The balloons drawn (older ones fold into a group: the page's list, not the nodes).
    const drawn = `LAST_ITEMS.map(m => String(m.id)).filter(m => /:[0-9]+$/.test(m))`;
    const sessionBytes = `performance.getEntriesByType('resource').filter(e => e.name.includes('/api/session/') && !e.name.includes('stat=1')).map(e => [e.name.split('/api/session/')[1], e.encodedBodySize])`;
    const toEnd = `(() => { const b = document.getElementById('msgs'); b.scrollTop = b.scrollHeight; b.dispatchEvent(new Event('scroll')); return 0; })()`;
    const toTop = `(() => { const b = document.getElementById('msgs'); b.scrollTop = 0; b.dispatchEvent(new Event('scroll')); return 0; })()`;
    const out = {};
    // A link to an early turn of the current session.
    await send('Page.navigate', { url: A.base + '/session?room=' + A.room + '&msg=' + encodeURIComponent('now-sid:5') }, sessionId);
    await until(`!!document.querySelector('#msgs .msg[data-mid="now-sid:5"].landed')`);
    out.link = await ev(`(d => ({ first: d.find(m => m.startsWith('now-sid:')), last: d[d.length - 1], n: d.length, win: !!SOLO_WIN, note: !!document.querySelector('#msgs .solo-newer-btn'),
      latest: !document.getElementById('to-latest').hidden, fetched: ${sessionBytes} }))(${drawn})`);
    out.downs = 0;
    for (let i = 0; i < 20 && await ev('!!SOLO_WIN'); i++) { await ev(toEnd); out.downs += await ev('SOLO_DOWN ? 1 : 0'); await until('!SOLO_DOWN && !SOLO_FETCHING'); await sleep(80); }
    // Each session's balloons in order, once (the reader at the window's top may have paged up into the one before).
    out.joined = await ev(`(d => ({ last: d[d.length - 1], win: !!SOLO_WIN, cut: soloCut(SOLO_HELD),
      now: d.filter(m => m.startsWith('now-sid:')).map(m => +m.split(':')[1]),
      old: d.filter(m => m.startsWith('old-sid:')).map(m => +m.split(':')[1]) }))(${drawn})`);
    // A link into the session before; Latest leaves its window.
    await send('Page.navigate', { url: A.base + '/session?room=' + A.room + '&msg=' + encodeURIComponent('old-sid:100') }, sessionId);
    await until(`!!document.querySelector('#msgs .msg[data-mid="old-sid:100"].landed')`);
    out.old = await ev(`(d => ({ first: d[0], last: d[d.length - 1], win: !!SOLO_WIN, n: d.length }))(${drawn})`);
    await ev(`document.getElementById('to-latest').click(), 0`);
    await until(`!SOLO_WIN && (d => d[d.length - 1] === 'now-sid:299')(${drawn})`);
    out.latest = await ev(`(d => ({ first: d[0], last: d[d.length - 1] }))(${drawn})`);
    // The latest, scrolled up to the very first turn, across the rotation.
    await send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
    await until(`typeof SOLO_HELD !== 'undefined' && !!SOLO_HELD && CHAT_DRAWN`);
    out.open = await ev(`(d => ({ first: d[0], last: d[d.length - 1], more: SOLO_MORE }))(${drawn})`);
    out.ups = 0;
    for (let i = 0; i < 30 && await ev('SOLO_MORE'); i++) { await ev(toTop); out.ups += await ev('SOLO_UP ? 1 : 0'); await until('!SOLO_UP && !SOLO_FETCHING'); await sleep(80); }
    out.top = await ev(`(d => ({ first: d[0], last: d[d.length - 1], n: d.length, more: SOLO_MORE,
      line: !!document.querySelector('#msgs .rot-line, #msgs [data-key^="rot"]') }))(${drawn})`);
    out.errors = EXC;
    console.log(JSON.stringify(out));
  } finally { ch.kill(); }
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class SoloWindowPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-sw-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("EnsembleProjects", "transcripts", "cs"):
            (base / d).mkdir()
        for patch in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
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
        rid = chatroom.create_room("Tidy the docs", [
            {"identity": "claude", "agent": "claude", "model": "", "role": "engineer", "sessionId": "now-sid"}])["id"]
        room = chatroom.get_room(rid, public=False)
        t0 = time.time() - 7200
        room["participants"][0]["rotations"] = [{"n": 1, "at": t0 + 3000, "fromSessionId": "old-sid",
                                                 "toSessionId": "now-sid", "tokens": 250000, "threshold": 250000}]
        chatroom.update_room(room)
        (base / "transcripts" / "C--eng").mkdir()
        for sid, start in (("old-sid", t0), ("now-sid", t0 + 3600)):
            turns = [_turn("user" if i % 2 == 0 else "assistant", f"{sid} turn {i} " + "words " * 40, start + i * 5)
                     for i in range(300)]
            (base / "transcripts" / "C--eng" / f"{sid}.jsonl").write_text(
                "".join(json.dumps({**t, "sessionId": sid}) + "\n" for t in turns), encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        args = {**chrome_profile.node_args(), "tmp": str(base), "room": rid,
                "base": f"http://127.0.0.1:{server.server_address[1]}"}
        path = base / "solo_window.js"
        path.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(path), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    def test_a_link_far_back_opens_a_window_around_it(self):
        o = self.got["link"]
        self.assertTrue(o["win"])
        self.assertEqual((o["first"], o["last"]), ("now-sid:0", "now-sid:45"), "its balloon and 40 after")
        self.assertTrue(o["note"], "the way to the later turns")
        self.assertTrue(o["latest"], "and the Latest button")
        window = [b for u, b in o["fetched"] if "around=5" in u]
        self.assertEqual(len(window), 1, o["fetched"])
        self.assertLess(window[0], 100_000)
        self.assertFalse([u for u, _ in o["fetched"] if "tail" not in u and "around" not in u and "from" not in u],
                         "never the whole transcript")

    def test_scrolling_down_the_window_joins_it_to_the_latest(self):
        j = self.got["joined"]
        self.assertFalse(j["win"])
        self.assertEqual((j["last"], j["cut"]), ("now-sid:299", 0))
        self.assertEqual(j["now"], list(range(300)), "no turn missing or twice where they met")
        self.assertEqual(j["old"], list(range(300 - len(j["old"]), 300)), "the session before, its last turns")

    def test_a_link_into_the_session_before_and_latest(self):
        o = self.got["old"]
        self.assertTrue(o["win"])
        self.assertEqual(o["last"], "old-sid:140", "its balloon and 40 after")
        self.assertLessEqual(int(o["first"].split(":")[1]), 80)
        self.assertEqual(self.got["latest"]["last"], "now-sid:299")

    def test_scrolling_up_goes_on_into_the_session_before(self):
        o, t = self.got["open"], self.got["top"]
        self.assertTrue(o["more"])
        self.assertEqual(o["last"], "now-sid:299")
        self.assertTrue(o["first"].startswith("now-sid:"), "the latest first, the session before not yet")
        self.assertEqual((t["first"], t["last"], t["n"], t["more"]), ("old-sid:0", "now-sid:299", 600, False))

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
