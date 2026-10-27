"""Done and Go ahead (#272, GitHub issue 28) in headless Chrome: a card
with a recommendation offers both, a yes/no decision both, a plain answer
only its point's Done; each sends its own words (or none) and lands in its
own ledger map. At 390x844 every answer button is at least 44px; at
1728x1117 the three are clicked.

Screenshots go to $ENSEMBLE_SHOTS when it is set."""
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

import asks
import chatroom
import dashboard
import ensemble_tools
import points
import sends
from test_page_update import CHROME
from test_po_chat_clean import NODE, _turn
from tests import chrome_profile

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  // The balloon whose text holds WORDS, and its answer buttons.
  const ballon = w => `[...document.querySelectorAll('.msg')].find(m => m.textContent.includes(${JSON.stringify(w)}))`;
  const out = {};
  try {
    for (const [w, h, mob, click] of A.views) {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
      await send('Page.enable', {}, sessionId);
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } let seen = ''; try { seen = await evalIn(`JSON.stringify({ msgs: [...document.querySelectorAll('.msg')].map(m => m.textContent.slice(0, 160)), points: typeof POINTS === 'object' ? POINTS.items : null })`); } catch (e) {}
        throw new Error('timeout: ' + expr + ' seen: ' + seen); };
      const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
      await send('Page.navigate', { url: A.base + '/session?room=' + A.po }, sessionId);
      // A PO's plain answer to a point is in that point's thread.
      await until(`typeof openThread === 'function' && ${ballon(A.words[0])}`);
      await evalIn(`openThread('P1'); 0`);
      await until(A.words.map(x => `${ballon(x)}`).join(' && ') + ` && ${ballon(A.words[2])}.querySelector('.pt-ans')`);
      await sleep(400);
      const look = () => evalIn(`(() => { const r = {}; for (const x of ${JSON.stringify(A.words)}) { const b = [...document.querySelectorAll('.msg')].find(m => m.textContent.includes(x));
        r[x] = { kinds: [...b.querySelectorAll('.pt-ans')].map(e => e.dataset.kind || e.dataset.ptAct),
          icons: [...b.querySelectorAll('.pt-ans')].map(e => !!e.querySelector('svg.ans-ic')),
          labels: [...b.querySelectorAll('.pt-ans')].map(e => e.getAttribute('aria-label') || ''),
          sizes: [...b.querySelectorAll('.pt-ans')].map(e => { const q = e.getBoundingClientRect(); return [Math.round(q.width), Math.round(q.height)]; }),
          chips: [...b.querySelectorAll('.pt-chip')].map(e => e.textContent.trim()) }; }
        r.scrollX = document.documentElement.scrollWidth - innerWidth; return r; })()`);
      const o = { before: await look() };
      await evalIn(`${ballon(A.words[0])}.scrollIntoView({ block: 'start' }); 0`);
      await sleep(200);
      await shot(`done-go-${w}-open`);
      if (click) {
        // Each theme: the word and its icon against the ground they sit on.
        o.themes = {};
        for (const th of A.themes) {
          await evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(th)}; 0`);
          await sleep(150);
          o.themes[th] = await evalIn(`(() => {
            const rgb = s => (s.match(/[\\d.]+/g) || []).map(Number);
            const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
            const ground = e => { for (; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c.length >= 3 && (c.length < 4 || c[3] > 0.5)) return c; } return [255, 255, 255]; };
            const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
            return [...document.querySelectorAll('.msg .pt-ans')].map(e => { const g = ground(e);
              const ic = getComputedStyle(e.querySelector('svg path')), paint = ic.stroke !== 'none' ? ic.stroke : ic.fill;
              return Math.min(ratio(rgb(getComputedStyle(e).color), g), ratio(rgb(paint), g)); });
          })()`);
          if (A.shotThemes.includes(th)) await shot(`done-go-${w}-${th}`);
        }
        await evalIn(`delete document.documentElement.dataset.theme; 0`);
        const hit = async (x, sel) => { await evalIn(`${ballon(x)}.querySelector(${JSON.stringify(sel)}).click(); 0`); };
        await hit(A.words[0], '.pt-ans[data-kind="go"]');
        await until(`!${ballon(A.words[0])}.querySelector('.pt-ans')`);
        await hit(A.words[1], '.pt-ans[data-kind="done"]');
        await until(`!${ballon(A.words[1])}.querySelector('.pt-ans')`);
        await hit(A.words[2], '.pt-ans');
        await until(`!${ballon(A.words[2])}.querySelector('.pt-ans')`);
        await sleep(500);
        o.after = await look();
        // Every copy of the answers (the chat's, the thread's) and the open-questions strip follow.
        const copies = `[...document.querySelectorAll('.msg')].filter(m => ${JSON.stringify(A.words)}.some(x => m.textContent.includes(x)))`;
        o.copies = await evalIn(`${copies}.length`);
        o.settled = await until(`!${copies}.some(m => m.querySelector('.pt-ans')) && 'yes'`, 15000).catch(() => 'no');
        o.strip = await until(`(() => { const q = document.querySelector('#open-questions'), t = q && !q.hidden ? q.textContent : ''; return t.includes('backup') || t.includes('go live') ? '' : 'settled'; })()`, 2000).catch(() => 'still open');
        await evalIn(`${ballon(A.words[0])}.scrollIntoView({ block: 'start' }); 0`);
        await sleep(200);
        await shot(`done-go-${w}-answered`);
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

THEMES = ["light", "dark", "dim", "paper", "contrast", "fjord", "intellij-dark"]
CARD, DECISION, PLAIN = "When should this go live?", "Run the backup tonight?", "The board is wider now"


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-donego-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "EnsembleProjects").mkdir()
        (base / "transcripts" / "C--po").mkdir(parents=True)
        (base / "cs").mkdir()
        cls.sent = []

        def resume(h, room, text="", to="", key="", quiet=False):
            cls.sent.append((text, key))
            sends.mark(room["id"], [key], "delivered")
            return {"delivered": 1}

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
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, project, _ = dashboard.register_project("Answers")
        assert ok, project
        po = chatroom.create_room("Answers PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": "po-sid"}])
        cls.rid = po["id"]
        room = chatroom.get_room(cls.rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.rid, project["id"])
        ok, why = dashboard.set_project_po(project["id"], cls.rid)
        assert ok, why
        # A point of the person's, and the PO's plain answer to it.
        t0 = time.time() - 300
        asked, pids = points.take(chatroom.get_room(cls.rid, public=False), "make the board wider", "claude", now=t0)
        assert pids == ["P1"], pids
        (base / "transcripts" / "C--po" / "po-sid.jsonl").write_text(
            "".join(json.dumps(t) + "\n" for t in [
                _turn("user", "[rotation] PO brief.", t0 - 10),
                _turn("user", asked, t0),
                _turn("assistant", f"Re P1: {PLAIN}; it is live.", t0 + 1)]), encoding="utf-8")
        room = chatroom.get_room(cls.rid, public=False)
        context = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": project["id"]}
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler._ring_recipients = lambda *a: []
        # A card with a recommended option: Done and Go ahead.
        cls.card = ensemble_tools._ask(context, {"context": "Release timing.", "questions": [
            {"question": CARD, "options": [{"label": "Now", "recommended": True}, {"label": "Later"}]}]}, handler)
        # A yes/no decision: Done and Go ahead (= Yes).
        cls.decision = chatroom.post_message(cls.rid, "claude", f"Decision needed: {DECISION}", to="user",
                                             structured_asks=asks.safety(f"Decision needed: {DECISION}"))["message"]["id"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base),
                "base": f"http://127.0.0.1:{server.server_address[1]}", "po": cls.rid, "shots": shots,
                "words": [CARD, DECISION, PLAIN], "themes": THEMES, "shotThemes": ["light", "dark", "fjord"], "views": [[390, 844, True, False], [1728, 1117, False, True]]}
        path = base / "donego_cdp.js"
        path.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(path), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        cls.ledger = points.load(cls.rid)

    def test_a_proposal_offers_both_and_a_plain_answer_only_done(self):
        for w in ("390", "1728"):
            b = self.got[w]["before"]
            self.assertEqual(b[CARD]["kinds"], ["done", "go"], w)
            self.assertEqual(b[DECISION]["kinds"], ["done", "go"], w)
            self.assertEqual(b[PLAIN]["kinds"], ["done"], f"{w}: a plain answer has no Go ahead")
            for x in (CARD, DECISION, PLAIN):
                self.assertTrue(all(b[x]["icons"]), f"{w} {x}: every answer button has its icon")
                self.assertTrue(all(lbl.startswith(("Done", "Go ahead")) for lbl in b[x]["labels"]), b[x]["labels"])
            self.assertLessEqual(b["scrollX"], 1)

    def test_phone_buttons_are_at_least_44px(self):
        b = self.got["390"]["before"]
        for x in (CARD, DECISION, PLAIN):
            for wd, ht in b[x]["sizes"]:
                self.assertGreaterEqual(min(wd, ht), 44, f"{x}: {b[x]['sizes']}")

    def test_each_answer_sends_its_own_words(self):
        texts = [t for t, _ in self.sent]
        go = [t for t in texts if "Go ahead" in t]
        done = [t for t in texts if "Done" in t]
        self.assertEqual(len(go), 1, texts)
        self.assertIn("“Now” on “When should this go live?”", go[0])
        self.assertEqual(len(done), 1, texts)
        self.assertIn(DECISION, done[0])
        self.assertFalse(any(PLAIN in t or "P1" in t for t in texts), "Done on a plain answer types nothing")

    def test_the_ledger_keeps_them_apart(self):
        led = self.ledger
        self.assertEqual(len(led["approvals"]), 1, "Go ahead on the card only")
        card = next(iter(led["approvals"]))
        self.assertEqual(led["asks"][card]["0"].get("option"), "Now")
        self.assertTrue(led["asks"][card]["0"].get("go"))
        self.assertIn(self.decision, led["dones"])
        self.assertNotIn(self.decision, led["approvals"])
        p1 = next(p for p in led["points"] if p["id"] == "P1")
        self.assertEqual((p1["state"], p1.get("ackKind")), ("acked", "done"))
        self.assertTrue(led["asks"][self.decision]["0"].get("done"))

    def test_every_theme_keeps_the_words_and_icons_readable(self):
        got = self.got["1728"]["themes"]
        self.assertEqual(sorted(got), sorted(THEMES))
        for th, ratios in got.items():
            self.assertEqual(len(ratios), 5, th)
            self.assertGreaterEqual(min(ratios), 4.5, f"{th}: {ratios}")

    def test_answers_settle_every_copy_and_the_open_questions_strip(self):
        a = self.got["1728"]
        self.assertEqual(a["settled"], "yes", "the thread's copy of the answer follows")
        self.assertEqual(a["strip"], "settled", "the open-questions strip drops the answered cards within 2 s")

    def test_answered_balloons_show_their_chip(self):
        a = self.got["1728"]["after"]
        self.assertIn("approved: go ahead", " ".join(a[CARD]["chips"]))
        self.assertIn("done", " ".join(a[DECISION]["chips"]))
        self.assertTrue(any("done" in c for c in a[PLAIN]["chips"]), a[PLAIN]["chips"])


if __name__ == "__main__":
    unittest.main()
