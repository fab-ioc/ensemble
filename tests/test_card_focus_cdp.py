"""Typing into a quick-answer card while the chat redraws (#191, GitHub issue 14).

On a throwaway hub (the real handler on a spare port, state in a temp folder)
headless Chrome opens a PO chat with three asks and types into the open ask's
comment box with real key input for 10 s. Every 500 ms something redraws the
chat, in turn: a plain redraw, a poll, a new message from the PO at the end,
the points changing so the card's own balloon is drawn anew (it says the ask
no longer waits), and the catch-up line above the card coming and going (the
read point, #188). After every redraw it checks the box still has focus, the
same caret or selection, and the whole text. Then Ctrl+Enter, pressed twice
and held (auto-repeat), sends exactly one answer.

It also checks a comment survives a reload (the per-card draft), and that the
Send buttons' tooltips name the shortcut.

ENSEMBLE_MEASURE=<file> writes the counts (focus losses, caret moves, text
lost, answers sent) for the before/after comparison in the task's report.
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
import points
import sends
from tests import chrome_profile
from tests.test_asks import THREE
from tests.test_page_update import CHROME
from tests.test_po_chat_clean import NODE, _turn

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
  const out = { ticks: [], losses: 0, caretMoves: 0, textLost: 0, nodeSwaps: 0 };
  try {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    await send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const key = (type, k, modifiers = 0, repeat = false) => send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Enter' ? 13 : 0, nativeVirtualKeyCode: k === 'Enter' ? 13 : 0, modifiers, autoRepeat: repeat }, sessionId);
    const open = async () => {
      await send('Page.navigate', { url: A.base + '/session?room=' + A.po }, sessionId);
      await until('document.querySelectorAll("#msgs .qa").length >= 3');
      await sleep(400);
    };
    await open();
    // The card typed into: the third ask, an open one ("Your answer", Send).
    const TA = `document.querySelectorAll('#msgs .qa')[2].querySelector('textarea')`;
    out.tips = await evalIn(`[...document.querySelectorAll('#msgs .qa-send')].map(b => b.title)`);
    await evalIn(`(() => { const ta = ${TA}; ta.scrollIntoView({ block: 'center' }); ta.focus(); window.__ta = ta; return 1; })()`);
    // What redraws the chat, one every 500 ms in turn.
    const sources = [
      ['redraw', `renderBubbles(LAST_ITEMS)`],
      ['poll', `refresh()`],
      ['new message', `null`],
      ['points', `(() => { const s = JSON.parse(POINTS_SIG || 'null') || { open: 0, planned: 0, delivered: 0, items: [], approvals: [] }; s.asksSettledAt = s.asksSettledAt ? 0 : Date.now() / 1000 + 5; pointsChanged(s, ptTicket()); })()`],
      ['catch-up', `(() => { if (CU_POINT) { CU_POINT = null; } else { const m = LAST_ITEMS.find(x => x && x.id && !x.divider); CU_POINT = { id: m.id, ts: m.ts, mine: true }; } CU_SNAP = null; renderBubbles(LAST_ITEMS); })()`],
    ];
    let typed = '', n = 0, src = 0, turn = 0;
    const t0 = Date.now();
    let nextRedraw = t0 + 500, selectAt = t0 + 5000, selected = null;
    while (Date.now() - t0 < 10000) {
      const word = 'w' + (n++) + ' ';
      // Typed where the caret is: at the end, or over a selection once.
      await send('Input.insertText', { text: word }, sessionId);
      if (selected) {
        typed = typed.slice(0, selected[0]) + word + typed.slice(selected[1]); selected = null;
        await evalIn(`(() => { const ta = document.activeElement; ta.setSelectionRange(ta.value.length, ta.value.length); return 1; })()`);   // back to the end
      }
      else typed += word;
      await sleep(60);
      if (Date.now() >= nextRedraw) {
        nextRedraw += 500;
        // Half the time a selection is made first, and must survive the redraw.
        const sel = (turn % 2) ? [3, 7] : null;
        const before = await evalIn(`(() => { const ta = document.activeElement; if (!ta || ta.tagName !== 'TEXTAREA') return null;
          ${sel ? `ta.setSelectionRange(${sel[0]}, ${sel[1]});` : ''} return [ta.selectionStart, ta.selectionEnd]; })()`);
        const [name, expr] = sources[src++ % sources.length];
        if (name === 'new message') {
          fs.appendFileSync(A.transcript, JSON.stringify({ type: 'assistant', timestamp: new Date().toISOString(), sessionId: 'po-sid',
            message: { role: 'assistant', stop_reason: 'end_turn', content: [{ type: 'text', text: 'Progress note ' + turn + '.' }] } }) + '\n');
          await evalIn(`refresh()`);
        } else await evalIn(expr);
        await sleep(120);
        const after = await evalIn(`(() => { const ta = document.activeElement, mine = ${TA};
          return { focused: !!ta && ta === mine, same: mine === window.__ta, value: mine ? mine.value : null,
            sel: mine ? [mine.selectionStart, mine.selectionEnd] : null }; })()`);
        const tick = { name, focused: after.focused, same: after.same, textOk: after.value === typed,
          caretOk: !!before && !!after.sel && after.sel[0] === before[0] && after.sel[1] === before[1] };
        out.ticks.push(tick);
        if (!tick.focused) out.losses++;
        if (!tick.same) out.nodeSwaps++;
        if (!tick.caretOk) out.caretMoves++;
        if (!tick.textOk) out.textLost++;
        turn++;
        // Carry on as a person would: back in the box with the whole text,
        // the caret at its end; or, all kept, over the selection just made.
        if (tick.focused && tick.textOk && tick.caretOk && sel) { selected = sel; continue; }
        await evalIn(`(() => { const ta = ${TA}; window.__ta = ta; ta.focus(); if (ta.value !== ${JSON.stringify(typed)}) { ta.value = ${JSON.stringify(typed)}; ta.dispatchEvent(new Event('input', { bubbles: true })); }
          ta.setSelectionRange(ta.value.length, ta.value.length); return 1; })()`);
      }
    }
    out.typed = typed;
    out.redraws = out.ticks.length;
    // Ctrl+Enter twice, then held: one answer.
    await evalIn(`(() => { const ta = ${TA}; ta.focus(); return 1; })()`);
    out.valueAtSend = await evalIn(`${TA}.value`);
    await key('rawKeyDown', 'Enter', 2);
    await key('rawKeyDown', 'Enter', 2, true);
    await key('rawKeyDown', 'Enter', 2, true);
    await key('keyUp', 'Enter', 2);
    await key('rawKeyDown', 'Enter', 2);
    await key('keyUp', 'Enter', 2);
    try { await until(`document.querySelectorAll('#msgs .qa')[2].classList.contains('done') && !document.querySelectorAll('#msgs .qa')[2].classList.contains('busy')`, 8000); out.sentDone = true; }
    catch (e) { out.sentDone = false; out.valueAfterKeys = await evalIn(`(${TA} || {}).value || null`); }
    // A send the hub refuses keeps the comment: in the box, and in storage
    // while it is on its way (review 1).
    const QA1 = `document.querySelectorAll('#msgs .qa')[1]`;
    await evalIn(`(() => { ${QA1}.querySelector('textarea').focus(); return 1; })()`);
    await send('Input.insertText', { text: 'not this time  ' }, sessionId);
    await sleep(200);
    out.failedSend = await evalIn(`(async () => {
      const key = ${QA1}.dataset.ask; let release;
      const p = askSend(key, '', () => new Promise((_, no) => { release = no; }));
      await new Promise(r => setTimeout(r, 300));
      const mid = { busy: ${QA1}.classList.contains('busy'), stored: JSON.parse(sessionStorage.getItem(ASK_DRAFT_KEY()) || '{}')[key] || null };
      release(new Error('the hub did not answer'));
      await p;
      const ta = ${QA1}.querySelector('textarea');
      return { mid, value: ta ? ta.value : null, stored: JSON.parse(sessionStorage.getItem(ASK_DRAFT_KEY()) || '{}')[key] || null };
    })()`);
    // A comment kept across a reload: the first card's.
    await evalIn(`(() => { const ta = document.querySelectorAll('#msgs .qa')[0].querySelector('textarea'); ta.focus(); return 1; })()`);
    await send('Input.insertText', { text: 'kept over a reload' }, sessionId);
    await sleep(200);
    await open();
    out.afterReload = await evalIn(`document.querySelectorAll('#msgs .qa')[0].querySelector('textarea').value`);
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
class TypingThroughRedraws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-cardfocus-", ignore_cleanup_errors=True)
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
            _turn("assistant", "Earlier: the build is green.", t0 + 2),
            _turn("assistant", THREE, t0 + 5)]), encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": f"http://127.0.0.1:{server.server_address[1]}",
                "po": cls.rid, "transcript": str(transcript)}
        script = base / "cardfocus_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        measure = os.environ.get("ENSEMBLE_MEASURE", "")
        if measure:
            Path(measure).write_text(json.dumps({**cls.got, "sent": cls.sent}, indent=1), encoding="utf-8")

    def test_typing_keeps_focus_caret_and_text_through_redraws(self):
        g = self.got
        self.assertGreaterEqual(g["redraws"], 18, "a redraw every 500 ms for 10 s")
        self.assertEqual({t["name"] for t in g["ticks"]}, {"redraw", "poll", "new message", "points", "catch-up"})
        self.assertEqual(g["losses"], 0, [t for t in g["ticks"] if not t["focused"]])
        self.assertEqual(g["caretMoves"], 0, [t for t in g["ticks"] if not t["caretOk"]])
        self.assertEqual(g["textLost"], 0, [t for t in g["ticks"] if not t["textOk"]])

    def test_ctrl_enter_sends_one_answer(self):
        self.assertTrue(self.got["sentDone"], self.got.get("valueAfterKeys"))
        answers = [t for t, k in self.sent if k.startswith("ask:")]
        self.assertEqual(len(answers), 1, self.sent)
        self.assertIn(self.got["valueAtSend"].strip(), answers[0])

    def test_a_comment_survives_a_reload(self):
        self.assertEqual(self.got["afterReload"], "kept over a reload")

    def test_a_refused_send_keeps_the_comment(self):
        f = self.got["failedSend"]
        self.assertTrue(f["mid"]["busy"], "the card shows it is being sent")
        self.assertEqual(f["mid"]["stored"], "not this time  ", "kept while on its way")
        self.assertEqual(f["value"], "not this time  ")
        self.assertEqual(f["stored"], "not this time  ")
        self.assertFalse([t for t, k in self.sent if "not this time" in t], "the refused answer was not delivered")

    def test_send_tooltips_name_the_shortcut(self):
        for tip in self.got["tips"]:
            self.assertRegex(tip, r"\((Ctrl|⌘)\+Enter\)")


if __name__ == "__main__":
    unittest.main()
