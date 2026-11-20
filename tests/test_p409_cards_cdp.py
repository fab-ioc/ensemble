"""P409 (#398) in Chrome: the box answers the open card, and a pick-any card.

On a throwaway hub (the real handler on a spare port, state in a temp
folder) headless Chrome opens a PO chat with four open cards: the three of
tests.test_asks.THREE and, newest, a pick-any card.

1. #408: plain typing sends a plain message; every card stays open, each
   with its ↩ Reply (no chip, menu or card comment field).
2. Reply on a card aims the box: the To line says "· reply to “…”", the card
   is marked, the caret is in the box; Send types the agent
   ``Re “<question>”: <words>``, the card reads "You answered in your
   message", the person's balloon says "Replies to <question> ↑", the card
   leaves the open cards and the To line drops the reply.
3. × drops the link and keeps the words, sent as a plain message; Undo of
   an aimed send gives the words back aimed at its card; an option clicked
   while aimed answers the card and the aim goes.
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
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
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
    // #408: the box's aim, as its To line says it ("reply to “…”"), else null.
    const aimLine = `(() => { const c = document.querySelector('#compose-reply'); return c && !c.hidden ? c.querySelector('.cr-re').textContent : null; })()`;
    const aimedCards = `[...document.querySelectorAll('#msgs .qa.aimed')].map(c => c.querySelector('.qa-qt').textContent.trim())`;
    const typeAndSend = async words => {
      await evalIn(`(() => { const i = document.querySelector('#input'); i.focus(); return 1; })()`);
      await send('Input.insertText', { text: words }, sessionId);
      await sleep(200);
      await click('#send');
    };
    const replyOn = async re => {
      await evalIn(`(() => { const c = [...document.querySelectorAll('#msgs .qa')].find(q => ${re}.test(q.textContent)); c.querySelector('.qa-reply').dataset.p408 = 'reply'; return 1; })()`);
      await click('[data-p408="reply"]');
      await evalIn(`(() => { document.querySelectorAll('[data-p408]').forEach(e => delete e.dataset.p408); return 1; })()`);
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
    out.inbox = await evalIn(`[...document.querySelectorAll('#ld-ibx-body .ibx-row')].map(r => ({ q: (r.querySelector('.ibx-line') || r).textContent.trim(), multi: !!r.querySelector('.ibx-multi'), meta: ((r.querySelector('.ibx-meta') || {}).textContent || '').trim(), reply: !!r.querySelector('[data-ibx-reply]') }))`);
    await shot('0 inbox');
    await send('Page.navigate', { url: A.base + '/session?room=' + A.po }, sessionId);
    await until('document.querySelectorAll("#msgs .qa").length >= 4');
    await sleep(300);
    // 1. Plain typing: the box answers no card; every open card has its Reply.
    out.aimStart = await evalIn(aimLine);
    out.replies = await evalIn(`[...document.querySelectorAll('#msgs .qa:not(.done)')].map(c => !!c.querySelector('.qa-reply'))`);
    out.noComment = await evalIn(`!document.querySelector('#msgs .qa-cm, #compose-answers, #answers-menu')`);
    out.toLine = await evalIn(`document.querySelector('#compose-destination-link').textContent.trim()`);
    await typeAndSend('A plain note');
    await until(`!document.querySelector('#input').value`, 10000);
    await sleep(300);
    out.openAfterPlain = await evalIn(`document.querySelectorAll('#msgs .qa:not(.done)').length`);
    // 2. Reply on "What else should go into this release?": the To line and the card say so; Send answers it.
    await replyOn('/What else should go/');
    out.aim = await evalIn(aimLine);
    out.aimTitle = await evalIn(`document.querySelector('#compose-reply .cr-re').title`);
    out.toLineAimed = await evalIn(`document.querySelector('#compose-destination-link').textContent.trim()`);
    out.aimed = await evalIn(aimedCards);
    out.replyOn = await evalIn(`(() => { const b = document.querySelector('#msgs .qa.aimed .qa-reply'); return b && { text: b.textContent.trim(), pressed: b.getAttribute('aria-pressed') }; })()`);
    out.caret = await evalIn(`document.activeElement === document.querySelector('#input')`);
    out.placeholder = await evalIn(`document.querySelector('#input').placeholder`);
    await send('Input.insertText', { text: 'Also the docs' }, sessionId);
    await sleep(200);
    await shot('1 aimed');
    await click('#send');
    const card = `[...document.querySelectorAll('#msgs .qa')].find(q => /What else should go/.test(q.textContent))`;
    await until(`(() => { const c = ${card}; return c && c.classList.contains('done') && !c.classList.contains('busy'); })()`, 10000);
    out.answeredCard = await evalIn(`${card}.querySelector('.qa-done').textContent`);
    out.aimAfter = await evalIn(aimLine);
    out.aimedAfter = await evalIn(aimedCards);
    out.inputAfter = await evalIn(`document.querySelector('#input').value`);
    // The agent's terminal has it: the person's balloon names the card.
    userTurn('Re “What else should go into this release?”: Also the docs');
    await evalIn('refresh()');
    const turnStrip = `[...document.querySelectorAll('#msgs .msg .via-card')].find(v => !v.closest('[data-key^="send-"]'))`;
    await until(`!!(${turnStrip}) && !!(${card}.querySelector('.qa-done a.msg-link'))`, 15000);
    out.balloon = await evalIn(`(() => { const v = ${turnStrip}; const m = v.closest('.msg'); return { strip: v.textContent, text: m.querySelector('.text').textContent }; })()`);
    out.cardLink = await evalIn(`!!(${card}.querySelector('.qa-done a.msg-link'))`);
    out.rawHeads = await evalIn(`[...document.querySelectorAll('#msgs .msg.user .text')].map(t => t.textContent).filter(t => t.includes('Re “What else'))`);
    out.strips = await evalIn(`document.querySelectorAll('#msgs .msg.user .via-card').length`);
    await evalIn(`(() => { const c = ${card}; c.scrollIntoView({ block: 'center' }); return 1; })()`);
    await shot('3 answered');
    // 3. ×: the link goes, the words stay, sent as a plain message.
    await replyOn('/Which retention/');
    await send('Input.insertText', { text: 'Keep a year' }, sessionId);
    await sleep(150);
    await click('#compose-reply-x');
    out.aimAfterX = await evalIn(aimLine);
    out.aimedAfterX = await evalIn(aimedCards);
    out.inputAfterX = await evalIn(`document.querySelector('#input').value`);
    await click('#send');
    await until(`!document.querySelector('#input').value`, 10000);
    await sleep(300);
    out.retentionOpen = await evalIn(`!![...document.querySelectorAll('#msgs .qa:not(.done)')].find(q => /Which retention/.test(q.textContent))`);
    // 3c. An older card answered from the box, then Undo before it is read:
    // the card is open again where it stood, its words back in the box, aimed at it.
    const BQ = `[...document.querySelectorAll('#msgs .qa')].find(q => /nightly backup/.test(q.textContent))`;
    await replyOn('/nightly backup/');
    out.holdAim = await evalIn(aimLine);
    // A poll overtakes the send: the page has the card closed before the hub's answer.
    await evalIn(`(() => { const f = window.fetch; window.fetch = async (u, o) => { const r = await f(u, o);
      if (String(u).includes('/api/room/resume') && String(o && o.body).includes('Hold on, not yet') && !window.__polled) {
        const open = () => (ROOM_OBJ.openCards || []).some(c => /nightly backup/.test(c.question));
        for (let t = Date.now(); Date.now() - t < 5000; await new Promise(z => setTimeout(z, 150))) { await refresh(); if (!open()) break; }
        window.__polled = open() ? 'still open' : 'closed'; }
      return r; }; return 1; })()`);
    await send('Input.insertText', { text: 'Hold on, not yet' }, sessionId);
    await sleep(200);
    await click('#send');
    await until(`(() => { const c = ${BQ}; return c && c.classList.contains('done') && !!c.querySelector('.qa-undo'); })()`, 10000);
    out.polled = await evalIn('window.__polled || null');
    out.holdAimAfter = await evalIn(aimLine);
    // A new card comes in before Undo.
    fs.appendFileSync(A.transcript, JSON.stringify({ type: 'assistant', timestamp: new Date().toISOString(), sessionId: 'po-sid',
      message: { role: 'assistant', stop_reason: 'end_turn', content: [{ type: 'text', text: 'Later.\n\n**Ask:** Which colour for the report header?\n- Blue\n- Grey' }] } }) + '\n');
    await evalIn('refresh()');
    await until(`(ROOM_OBJ.openCards || []).some(c => /report header/.test(c.question))`, 10000);
    await evalIn(`(() => { ${BQ}.querySelector('.qa-undo').dataset.p409 = 'undo'; return 1; })()`);
    await click('[data-p409="undo"]');
    await until(`document.querySelector('#input').value === 'Hold on, not yet'`, 10000);
    await sleep(300);
    out.undoAim = await evalIn(aimLine);
    out.undoAimed = await evalIn(aimedCards);
    out.undoInput = await evalIn(`document.querySelector('#input').value`);
    out.undoCardOpen = await evalIn(`!${BQ}.classList.contains('done')`);
    await click('#send');
    await until(`!document.querySelector('#input').value`, 10000);
    await until(`(() => { const c = ${BQ}; return c && c.classList.contains('done') && !c.classList.contains('busy'); })()`, 10000);
    // 3d. An option clicked while the box is aimed at that card answers it; the aim goes.
    const HQ = `[...document.querySelectorAll('#msgs .qa')].find(q => /report header/.test(q.textContent))`;
    await until(`!!(${HQ})`, 10000);
    await replyOn('/report header/');
    out.headerAim = await evalIn(aimLine);
    await evalIn(`(() => { const b = [...${HQ}.querySelectorAll('.qa-opt')].find(x => x.dataset.opt === 'Blue'); b.dataset.p409 = 'blue'; return 1; })()`);
    await click('[data-p409="blue"]');
    await until(`(() => { const c = ${HQ}; return c && c.classList.contains('done') && !c.classList.contains('busy'); })()`, 10000);
    await sleep(300);
    out.headerDone = await evalIn(`${HQ}.querySelector('.qa-done').textContent`);
    out.headerAimAfter = await evalIn(aimLine);
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

    def test_plain_typing_answers_no_card(self):
        g = self.got
        self.assertIsNone(g["aimStart"])
        self.assertTrue(g["replies"] and all(g["replies"]), g["replies"])
        self.assertTrue(g["noComment"], "no chip, menu or card comment field")
        self.assertTrue(g["toLine"].startswith("Cards PO"), g["toLine"])
        self.assertEqual(g["toLineAimed"], g["toLine"], "aimed, the To line keeps its label")
        plain = [s for s in self.sent if s[0].startswith("A plain note")]
        self.assertEqual(len(plain), 1, self.sent)
        self.assertEqual(plain[0][0].split("\n\n[point")[0], "A plain note")
        self.assertEqual(g["openAfterPlain"], 4)
        rows = [r for r in self.got["inbox"]]
        self.assertTrue(all(r["reply"] for r in rows), rows)

    def test_reply_aims_the_box_and_send_answers_the_card(self):
        g = self.got
        self.assertEqual(g["aim"], "reply to “What else should go into…”")
        self.assertEqual(g["aimTitle"], "What else should go into this release?")
        self.assertEqual(g["aimed"], ["What else should go into this release?"])
        self.assertEqual(g["replyOn"], {"text": "Replying in the box ↓", "pressed": "true"})
        self.assertTrue(g["caret"])
        self.assertEqual(g["placeholder"], "Your reply…")
        box = [s for s in self.sent if "Also the docs" in s[0]]
        self.assertEqual(len(box), 1, self.sent)
        self.assertTrue(box[0][0].startswith("Re “What else should go into this release?”: Also the docs"), box)
        self.assertIn("You answered in your message", g["answeredCard"])
        self.assertIn("Also the docs", g["answeredCard"])
        self.assertTrue(g["cardLink"], "the card links to the message")
        self.assertEqual(g["inputAfter"], "")
        self.assertIsNone(g["aimAfter"], "sent: the box goes back to the conversation")
        self.assertEqual(g["aimedAfter"], [])
        self.assertEqual(g["balloon"]["strip"], "Replies to What else should go into this release? ↑")
        self.assertNotIn("Re “", g["balloon"]["text"])
        self.assertEqual(g["rawHeads"], [], "no balloon keeps the Re head")
        self.assertGreaterEqual(g["strips"], 1)
        self.assertIn("Also the docs", g["balloon"]["text"])
        self.assertNotIn("What else should go into this release?", self.open, "answered: no row in the Inbox")

    def test_x_keeps_the_words_and_sends_a_plain_message(self):
        g = self.got
        self.assertIsNone(g["aimAfterX"])
        self.assertEqual(g["aimedAfterX"], [])
        self.assertEqual(g["inputAfterX"], "Keep a year")
        plain = [s for s in self.sent if "Keep a year" in s[0]]
        self.assertEqual([s[0].split("\n\n[point")[0] for s in plain], ["Keep a year"], self.sent)
        self.assertTrue(g["retentionOpen"])

    def test_an_option_clicked_while_aimed_answers_the_card(self):
        g = self.got
        self.assertEqual(g["headerAim"], "reply to “Which colour for the report…”")
        self.assertIn("Blue", g["headerDone"])
        self.assertIsNone(g["headerAimAfter"])

    def test_undo_of_an_unread_box_answer_gives_back_its_own_card(self):
        g = self.got
        self.assertEqual(g["holdAim"], "reply to “Turn on the nightly backup?”")
        self.assertEqual(g["polled"], "closed", "a poll before the hub's answer has the card closed")
        self.assertIsNone(g["holdAimAfter"])
        self.assertEqual(g["undoAim"], "reply to “Turn on the nightly backup?”", "the words go back aimed at the card they answered")
        self.assertEqual(g["undoAimed"], ["Turn on the nightly backup?"])
        self.assertEqual(g["undoInput"], "Hold on, not yet")
        self.assertTrue(g["undoCardOpen"])
        holds = [s for s in self.sent if "Hold on, not yet" in s[0]]
        self.assertEqual(len(holds), 2, self.sent)
        self.assertNotEqual(holds[0][2], holds[1][2], "the second send is a new message")
        self.assertTrue(holds[1][0].startswith("Re “Turn on the nightly backup?”: Hold on, not yet"), holds)

    def test_two_ticks_go_together(self):
        g = self.got
        self.assertEqual(g["pickHead"], "Ask 1 of 1 · pick any" if "of" in g["pickHead"] else "Ask · pick any")
        self.assertEqual(g["sendOff"], {"text": "Send", "disabled": True})
        self.assertEqual(g["ticked"], {"send": "Send (2)", "primary": True, "checked": ["CSV", "XML"], "done": False},
                         "a click only ticks")
        picks = [s for s in self.sent if s[2].startswith("ask:") and "export formats" in s[0]]
        self.assertEqual(len(picks), 1, self.sent)
        self.assertTrue(picks[0][0].startswith("Re “Which export formats should the report offer?”: CSV; XML"), picks)
        self.assertIn("You picked: CSV · XML", g["pickDone"])
        self.assertNotIn("Which export formats should the report offer?", self.open)
        self.assertEqual(sorted(self.open), ["Which retention for old transcripts?"])


if __name__ == "__main__":
    unittest.main()
