"""P409 (#398) in Chrome: the box answers the open card, and a pick-any card.

On a throwaway hub (the real handler on a spare port, state in a temp
folder) headless Chrome opens a PO chat with four open cards: the three of
tests.test_asks.THREE and, newest, a pick-any card.

1. The box's chip names the newest open card; ▾ lists the open cards, newest
   first, ✓ on the named one, then "No card: a plain message".
2. Another card picked in the menu, words typed and sent: the agent is typed
   ``Re “<question>”: <words>``, the card reads "You answered in your
   message", the person's balloon says "Answers <question> ↑", and the card
   leaves the open cards (so its Inbox row goes); the chip is back, on the
   newest open card.
3. × sends the next message as a plain one, once.
4. Two options of the pick-any card ticked: its Send says "Send (2)", and
   sends ``Re “<question>”: CSV; XML``; the card reads "You picked".

ENSEMBLE_SHOTS=<dir> saves a screenshot of each state.
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

import asks
import chatroom
import dashboard
import points
import sends
from tests import chrome_profile
from tests.test_asks import THREE
from tests.test_page_update import CHROME
from tests.test_po_chat_clean import NODE, _turn

PICK = "One more.\n\n**Ask (pick any):** Which export formats should the report offer?\n- CSV\n- JSON\n- XML"

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    await send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(A.shots + '/' + name + '.png', Buffer.from(r.data, 'base64')); };
    const click = async sel => {
      const r = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; e.scrollIntoView({ block: 'center' }); const b = e.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()`);
      if (!r) throw new Error('no ' + sel);
      for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: r[0], y: r[1], button: 'left', clickCount: 1 }, sessionId);
      await sleep(150);
    };
    const chip = `(() => { const c = document.querySelector('#compose-answers'); return c && !c.hidden ? c.querySelector('.ca-q').textContent : null; })()`;
    const typeAndSend = async words => {
      await evalIn(`(() => { const i = document.querySelector('#input'); i.focus(); return 1; })()`);
      await send('Input.insertText', { text: words }, sessionId);
      await sleep(200);
      await click('#send');
    };
    const userTurn = text => fs.appendFileSync(A.transcript, JSON.stringify({ type: 'user', timestamp: new Date().toISOString(), sessionId: 'po-sid',
      message: { role: 'user', content: text } }) + '\n');
    // 0. The Inbox: the pick-any card's row says "pick any".
    const seed = { 'cd-list-dock': null, 'cd-ld-sel': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-inbox-scope': 'all' };
    await send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(seed) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('window.ensBooted === true && typeof IBX !== "undefined" && !!IBX.data', 30000);
    await until(`(() => { const a = document.querySelector('#ld-projs [data-ldp="all"]'); if (a && a.getAttribute('aria-current') !== 'true') a.click(); return document.querySelectorAll('#ld-ibx-body .ibx-row').length >= 4; })()`, 20000);
    await sleep(300);
    out.inbox = await evalIn(`[...document.querySelectorAll('#ld-ibx-body .ibx-row')].map(r => ({ q: (r.querySelector('.ibx-line') || r).textContent.trim(), multi: !!r.querySelector('.ibx-multi'), meta: ((r.querySelector('.ibx-meta') || {}).textContent || '').trim() }))`);
    await shot('0 inbox');
    await send('Page.navigate', { url: A.base + '/session?room=' + A.po }, sessionId);
    await until('document.querySelectorAll("#msgs .qa").length >= 4');
    out.chip = await until(chip);
    await evalIn(`(() => { const i = document.querySelector('#input'); i.focus(); return 1; })()`);
    await send('Input.insertText', { text: 'Also the docs' }, sessionId);
    await sleep(200);
    await shot('1 chip');
    // 1. The menu: the open cards, newest first, ✓ on the named one.
    await click('#compose-answers-pick');
    await until(`!document.querySelector('#answers-menu').hidden`);
    out.menu = await evalIn(`[...document.querySelectorAll('#answers-menu button')].map(b => ({ q: b.querySelector('.am-q').textContent, on: b.getAttribute('aria-checked') === 'true' }))`);
    // In sight: the point at each item's middle is that item.
    out.menuSeen = await evalIn(`[...document.querySelectorAll('#answers-menu button')].map(b => { const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return { seen: !!e && b.contains(e), r: [r.left, r.top, r.width, r.height], at: e ? (e.id || e.className || e.tagName) : null }; })`);
    out.editor = await evalIn(`(() => { const out = []; for (let e = document.querySelector('#answers-menu'); e; e = e.parentElement) { const s = getComputedStyle(e); out.push([e.id || e.className, s.overflow, s.position, s.zIndex, s.contain]); } return out; })()`);
    await shot('2 menu');
    // 2. Another card: "What else should go into this release?".
    await evalIn(`(() => { const b = [...document.querySelectorAll('#answers-menu button')].find(x => /What else/.test(x.textContent)); b.click(); return 1; })()`);
    out.picked = await evalIn(chip);
    out.menuClosed = await evalIn(`document.querySelector('#answers-menu').hidden`);
    await click('#send');
    const card = `[...document.querySelectorAll('#msgs .qa')].find(q => /What else should go/.test(q.textContent))`;
    await until(`(() => { const c = ${card}; return c && c.classList.contains('done') && !c.classList.contains('busy'); })()`, 10000);
    out.answeredCard = await evalIn(`${card}.querySelector('.qa-done').textContent`);
    out.chipAfter = await evalIn(chip);
    out.inputAfter = await evalIn(`document.querySelector('#input').value`);
    // The agent's terminal has it: the person's balloon names the card.
    userTurn('Re “What else should go into this release?”: Also the docs');
    await evalIn('refresh()');
    // The pending send already reads "Answers … ↑"; wait for the turn's own balloon.
    const turnStrip = `[...document.querySelectorAll('#msgs .msg .via-card')].find(v => !v.closest('[data-key^="send-"]'))`;
    await until(`!!(${turnStrip}) && !!(${card}.querySelector('.qa-done a.msg-link'))`, 15000);
    out.balloon = await evalIn(`(() => { const v = ${turnStrip}; const m = v.closest('.msg'); return { strip: v.textContent, text: m.querySelector('.text').textContent }; })()`);
    out.cardLink = await evalIn(`!!(${card}.querySelector('.qa-done a.msg-link'))`);
    // No balloon of the person's (the pending send too) shows the "Re “…”:" head.
    out.rawHeads = await evalIn(`[...document.querySelectorAll('#msgs .msg.user .text')].map(t => t.textContent).filter(t => t.includes('Re “What else'))`);
    out.strips = await evalIn(`document.querySelectorAll('#msgs .msg.user .via-card').length`);
    await evalIn(`(() => { const c = ${card}; c.scrollIntoView({ block: 'center' }); return 1; })()`);
    await shot('3 answered');
    await evalIn(`(() => { (${turnStrip}).closest('.msg').scrollIntoView({ block: 'center' }); return 1; })()`);
    await shot('3b balloon');
    // 3. ×: this message is a plain one, and the chip comes back after it.
    await click('#compose-answers-x');
    out.chipAfterX = await evalIn(chip);
    await typeAndSend('A plain note');
    await until(`!document.querySelector('#input').value`, 10000);
    await sleep(300);
    out.chipBack = await until(chip);
    // 3c. An older card answered from the box, then Undo before it is read:
    // the card is open again where it stood, and its words go back under its chip.
    const BQ = `[...document.querySelectorAll('#msgs .qa')].find(q => /nightly backup/.test(q.textContent))`;
    await click('#compose-answers-pick');
    await until(`!document.querySelector('#answers-menu').hidden`);
    await evalIn(`(() => { const b = [...document.querySelectorAll('#answers-menu button')].find(x => /nightly backup/.test(x.textContent)); b.click(); return 1; })()`);
    out.holdChip = await evalIn(chip);
    await typeAndSend('Hold on, not yet');
    await until(`(() => { const c = ${BQ}; return c && c.classList.contains('done') && !!c.querySelector('.qa-undo'); })()`, 10000);
    out.holdChipAfter = await evalIn(chip);
    await evalIn(`(() => { ${BQ}.querySelector('.qa-undo').dataset.p409 = 'undo'; return 1; })()`);
    await click('[data-p409="undo"]');
    await until(`document.querySelector('#input').value === 'Hold on, not yet'`, 10000);
    await sleep(300);
    out.undoChip = await evalIn(chip);
    out.undoInput = await evalIn(`document.querySelector('#input').value`);
    out.undoCardOpen = await evalIn(`!${BQ}.classList.contains('done')`);
    await click('#compose-answers-pick');
    await until(`!document.querySelector('#answers-menu').hidden`);
    out.undoMenu = await evalIn(`[...document.querySelectorAll('#answers-menu button')].map(b => ({ q: b.querySelector('.am-q').textContent, on: b.getAttribute('aria-checked') === 'true' }))`);
    await evalIn(`(() => { document.querySelector('#answers-menu button[aria-checked="true"]').click(); return 1; })()`);
    await click('#send');
    await until(`!document.querySelector('#input').value`, 10000);
    await until(`(() => { const c = ${BQ}; return c && c.classList.contains('done') && !c.classList.contains('busy'); })()`, 10000);
    // 4. The pick-any card: two ticks, Send (2).
    const PQ = `[...document.querySelectorAll('#msgs .qa')].find(q => /export formats/.test(q.textContent))`;
    out.pickHead = await evalIn(`${PQ}.querySelector('.qa-no').textContent`);
    out.sendOff = await evalIn(`(() => { const b = ${PQ}.querySelector('.qa-send'); return { text: b.textContent, disabled: b.disabled }; })()`);
    for (const label of ['CSV', 'XML']) {
      await evalIn(`(() => { const b = [...${PQ}.querySelectorAll('.qa-opt')].find(x => x.dataset.opt === ${JSON.stringify(label)}); b.dataset.p409 = ${JSON.stringify(label)}; return 1; })()`);
      await click(`[data-p409="${label}"]`);
    }
    out.ticked = await evalIn(`(() => { const c = ${PQ}; return { send: c.querySelector('.qa-send').textContent, primary: c.querySelector('.qa-send').classList.contains('primary'),
      checked: [...c.querySelectorAll('.qa-opt[aria-checked="true"]')].map(b => b.dataset.opt), done: c.classList.contains('done') }; })()`);
    await shot('4 ticked');
    await evalIn(`(() => { ${PQ}.querySelector('.qa-send').dataset.p409 = 'send'; return 1; })()`);
    await click('[data-p409="send"]');
    await until(`(() => { const c = ${PQ}; return c && c.classList.contains('done') && !c.classList.contains('busy'); })()`, 10000);
    out.pickDone = await evalIn(`${PQ}.querySelector('.qa-done').textContent`);
    await evalIn(`(() => { ${PQ}.scrollIntoView({ block: 'center' }); return 1; })()`);
    await shot('5 picked');
    await send('Target.closeTarget', { targetId });
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class TheBoxAndPickAny(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-p409-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "EnsembleProjects").mkdir()
        (base / "transcripts" / "C--po").mkdir(parents=True)
        (base / "cs").mkdir()
        cls.sent = []

        cls.held = []

        def resume(h, room, text="", to="", key="", quiet=False):
            cls.sent.append((text, to, key))
            if "Hold on, not yet" in text and not cls.held:
                # Not read yet: held by a resume under way, so Undo withdraws it.
                cls.held.append(key)
                res = dashboard._RESUMES.setdefault(room["id"], dashboard._Resume(room["id"]))
                res.queue.append({"text": text, "to": to, "key": key, "at": time.time()})
                return {"queued": 1}
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
        asks.forget()
        ok, project, _ = dashboard.register_project("Cards")
        assert ok, project
        po = chatroom.create_room("Cards PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": "po-sid"}])
        cls.rid = po["id"]
        room = chatroom.get_room(cls.rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.rid, project["id"])
        ok, why = dashboard.set_project_po(project["id"], cls.rid)
        assert ok, why
        t0 = time.time() - 600
        transcript = base / "transcripts" / "C--po" / "po-sid.jsonl"
        transcript.write_text("".join(json.dumps(x) + "\n" for x in [
            _turn("user", "[rotation] You are the PO of Cards. Read PO-HANDOVER.md.", t0),
            _turn("assistant", THREE, t0 + 5),
            _turn("assistant", PICK, t0 + 60)]), encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": f"http://127.0.0.1:{server.server_address[1]}",
                "po": cls.rid, "transcript": str(transcript), "shots": shots}
        script = base / "p409_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        asks.forget()
        cls.open = [a["question"] for a in asks.open_in(chatroom.get_room(cls.rid))]

    def test_the_inbox_row_of_a_pick_any_card_says_so(self):
        rows = {r["q"]: r for r in self.got["inbox"]}
        pick = [r for q, r in rows.items() if "Which export formats" in q]
        self.assertEqual(len(pick), 1, self.got["inbox"])
        self.assertTrue(pick[0]["multi"])
        self.assertIn("pick any", pick[0]["meta"])
        self.assertFalse(any(r["multi"] for q, r in rows.items() if "Which export formats" not in q), self.got["inbox"])

    def test_the_chip_names_the_newest_open_card_and_the_menu_lists_them(self):
        g = self.got
        self.assertEqual(g["chip"], "Which export formats should the report offer?")
        qs = [m["q"] for m in g["menu"]]
        # The newest message's cards first; one message's cards in their order.
        self.assertEqual(qs, ["Which export formats should the report offer?", "Which retention for old transcripts?",
                              "Turn on the nightly backup?", "What else should go into this release?",
                              "No card: a plain message"])
        self.assertEqual([m["on"] for m in g["menu"]], [True, False, False, False, False])
        self.assertEqual(g["picked"], "What else should go into this release?")
        self.assertTrue(g["menuClosed"])
        self.assertTrue(all(m["seen"] for m in g["menuSeen"]), (g["menuSeen"], g.get("editor")))

    def test_the_message_answers_the_card_it_names(self):
        g = self.got
        box = [s for s in self.sent if not s[2].startswith("ask:")]
        self.assertTrue(box[0][0].startswith("Re “What else should go into this release?”: Also the docs"), box)
        self.assertIn("You answered in your message", g["answeredCard"])
        self.assertIn("Also the docs", g["answeredCard"])
        self.assertTrue(g["cardLink"], "the card links to the message")
        self.assertEqual(g["inputAfter"], "")
        self.assertEqual(g["chipAfter"], "Which export formats should the report offer?", "the chip is back, on the newest")
        self.assertEqual(g["balloon"]["strip"], "Answers What else should go into this release? ↑")
        self.assertNotIn("Re “", g["balloon"]["text"])
        self.assertEqual(g["rawHeads"], [], "no balloon keeps the Re head")
        self.assertGreaterEqual(g["strips"], 1)
        self.assertIn("Also the docs", g["balloon"]["text"])
        self.assertNotIn("What else should go into this release?", self.open, "answered: no row in the Inbox")

    def test_x_sends_one_plain_message(self):
        g = self.got
        self.assertIsNone(g["chipAfterX"])
        plain = [s for s in self.sent if s[0].startswith("A plain note")]
        self.assertEqual(len(plain), 1, self.sent)
        self.assertEqual(g["chipBack"], "Which export formats should the report offer?")

    def test_two_ticks_go_together(self):
        g = self.got
        self.assertEqual(g["pickHead"], "Ask 1 of 1 · pick any" if "of" in g["pickHead"] else "Ask · pick any")
        self.assertEqual(g["sendOff"], {"text": "Send", "disabled": True})
        self.assertEqual(g["ticked"], {"send": "Send (2)", "primary": True, "checked": ["CSV", "XML"], "done": False},
                         "a click only ticks")
        picks = [s for s in self.sent if s[2].startswith("ask:")]
        self.assertEqual(len(picks), 1, self.sent)
        self.assertTrue(picks[0][0].startswith("Re “Which export formats should the report offer?”: CSV; XML"), picks)
        self.assertIn("You picked: CSV · XML", g["pickDone"])
        self.assertNotIn("Which export formats should the report offer?", self.open)
        self.assertEqual(self.open, ["Which retention for old transcripts?"])

    def test_undo_of_an_unread_box_answer_gives_back_its_own_card(self):
        g = self.got
        self.assertEqual(g["holdChip"], "Turn on the nightly backup?")
        self.assertEqual(g["holdChipAfter"], "Which export formats should the report offer?")
        self.assertEqual(g["undoChip"], "Turn on the nightly backup?", "the words go back under the card they answered")
        self.assertEqual(g["undoInput"], "Hold on, not yet")
        self.assertTrue(g["undoCardOpen"])
        self.assertEqual([m["q"] for m in g["undoMenu"]][:3], ["Which export formats should the report offer?",
                                                               "Which retention for old transcripts?", "Turn on the nightly backup?"])
        self.assertEqual([m["q"] for m in g["undoMenu"] if m["on"]], ["Turn on the nightly backup?"])
        holds = [s for s in self.sent if "Hold on, not yet" in s[0]]
        self.assertEqual(len(holds), 2, self.sent)
        self.assertNotEqual(holds[0][2], holds[1][2], "the second send is a new message")
        self.assertTrue(holds[1][0].startswith("Re “Turn on the nightly backup?”: Hold on, not yet"), holds)


if __name__ == "__main__":
    unittest.main()
