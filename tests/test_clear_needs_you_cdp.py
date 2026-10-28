"""Clear Needs you in one action, in the browser (#279).

In headless Chrome over CDP (every launch through tests/chrome_profile.py),
at 1440x900 and 390x844 (a touch phone), against a hub in a thread with
throwaway state: a project with three team tasks (an engineer and a reviewer) whose engineer asked the person a
question (an ``Ask:`` card) and a fourth whose question the person wrote past.

* the written-past task is not on Needs you; the other three are;
* Done with this on the row itself (shown on focus; beside the row on a
  touch screen) takes that row off;
* Clear all in Needs you's head asks in place ("Clear 2 rows?"), clears every
  row, says "Cleared 2" with Undo; Undo brings the rows back;
* the recent list has the same Clear all in a head of its own;
* a row opens its chat at the card it stands for (the landing mark).

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
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

import asks  # noqa: E402
import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import test_middle  # noqa: E402
from tests.test_middle import CHROME, NODE  # noqa: E402

HEAD = test_middle.CDP_JS[:test_middle.CDP_JS.index("// Where the middle is")]
SIZES = [(1440, 900, False), (390, 844, True)]

CDP_JS = HEAD + r"""
const ROWS = `[...document.querySelectorAll('#sw-list section[data-group="needs"] .sw-row')].map(r => r.dataset.room)`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const [w, h, mob] = A.size, o = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, s);
    if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
    const ev = async (expression) => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(expression.slice(0, 100) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
    const until = async (ex, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(ex); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + ex); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, `clear-needs-you-${w}-${name}.png`), Buffer.from(r.data, 'base64')); };
    const click = async (sel) => { await ev(`(() => { const b = document.querySelector(${JSON.stringify(sel)}); if (!b) throw new Error('no ' + ${JSON.stringify(sel)}); b.click(); return 0; })()`); };
    // The recent list (everyone's default) first: Clear all in a head of its own.
    await c.send('Page.navigate', { url: A.base + '/' }, s);
    await until(`!!document.querySelector('#sw-list .sw-needs-head [data-clear="ask"]')`, 30000).catch(async e => {
      throw new Error(e.message + ' :: ' + await ev(`JSON.stringify({ list: document.querySelector('#sw-list').innerText.slice(0, 800), attn: ATTENTION.items.length })`)); });
    o.recentHead = await ev(`(() => { const hd = document.querySelector('#sw-list .sw-needs-head'); return hd ? hd.textContent.trim() : null; })()`);
    // Grouped by status: Needs you's own head.
    await ev(`localStorage.setItem('cd-switcher-view', 'status'); 0`);
    await c.send('Page.navigate', { url: A.base + '/' }, s);
    await until(`${ROWS}.length >= 3`, 30000);
    await sleep(300);
    o.before = await ev(ROWS);
    o.hoverNone = await ev(`matchMedia('(hover: none)').matches`);
    o.scrollX = await ev(`document.documentElement.scrollWidth - innerWidth`);
    await shot('1-before');
    // Done with this on the row: hidden until the row has the focus (or always, on touch).
    const first = A.rooms[0], sel = `.sw-row-done[data-room="${first}"]`;
    o.doneShown = await ev(`getComputedStyle(document.querySelector('${sel}')).display`);
    await ev(`document.querySelector('.sw-row[data-room="${first}"]').focus(); 0`);
    o.doneOnFocus = await ev(`(() => { const b = document.querySelector('${sel}').getBoundingClientRect(); return { display: getComputedStyle(document.querySelector('${sel}')).display, w: Math.round(b.width), h: Math.round(b.height) }; })()`);
    await shot('2-row-done');
    await click(sel);
    await until(`!${ROWS}.includes('${first}')`);
    o.afterDone = await ev(ROWS);
    // Clear all: the confirm in place, then the rows gone and Undo.
    await click('#sw-list [data-clear="ask"]');
    o.confirm = await ev(`document.querySelector('#sw-list .sw-clear-all').textContent.trim()`);
    o.confirmTargets = await ev(`[...document.querySelectorAll('#sw-list .sw-ca')].map(b => { const r = b.getBoundingClientRect(); return [b.textContent, Math.round(r.width), Math.round(r.height)]; })`);
    await shot('3-confirm');
    await click('#sw-list [data-clear="go"]');
    await until(`${ROWS}.length === 0 && !!document.querySelector('#sw-list [data-clear="undo"]')`);
    o.cleared = await ev(`document.querySelector('#sw-list .sw-clear-all').textContent.trim()`);
    o.hubAfterClear = await ev(`fetch('/api/attention').then(r => r.json()).then(j => j.items.map(i => i.roomId))`);
    await shot('4-cleared');
    await click('#sw-list [data-clear="undo"]');
    await until(`${ROWS}.length === 2`);
    o.afterUndo = await ev(ROWS);
    await until(`!document.querySelector('#sw-list .sw-clear-all [data-clear="undo"]')`);
    await shot('5-undone');
    if (!mob) {
      // A row opens its chat at its card.
      const room = A.rooms[1];
      o.rowMsg = await ev(`document.querySelector('.sw-row[data-room="${room}"]').dataset.msg || ''`);
      await click(`.sw-row[data-room="${room}"]`);
      o.land = await until(`(() => { const f = document.querySelector('iframe.dp-session[data-room="${room}"]'); const d = f && f.contentDocument; const el = d && d.querySelector('.landed'); const m = el && el.closest('.msg[data-mid]'); return m ? m.dataset.mid : ''; })()`, 30000).catch(() => '');
      if (!o.land) o.landWhy = await ev(`JSON.stringify({ frames: [...document.querySelectorAll('iframe')].map(f => [f.className, f.dataset.room, (f.contentWindow && f.contentWindow.location.href) || '', f.contentDocument ? f.contentDocument.querySelectorAll('.msg[data-mid]').length : -1, (() => { try { const W = f.contentWindow; return JSON.stringify({ n: W.ROOM_OBJ && W.ROOM_OBJ.messages.length, drawn: W.CHAT_DRAWN, solo: W.SOLO_MODE, threaded: W.THREADED, kids: W.document.getElementById('msgs').children.length, cls: W.document.getElementById('msgs').className, hidden: W.document.getElementById('msgs').hidden, body: W.document.body.className }); } catch (e) { return String(e); } })()]), goto: [...GOTO_MSG.entries()] })`);
      await shot('6-landed');
    }
    // The chat's own Needs you: Clear all moves its Decisions to Older questions, and Undo.
    const chat = A.rooms[2], LIST = `[...document.querySelectorAll('#points-list > li')].map(li => (li.classList.contains('pt-ask') ? 'ask:' : '') + li.firstChild.textContent.trim())`;
    await c.send('Page.navigate', { url: A.base + '/session?room=' + chat }, s);
    await until(`!!document.querySelector('#points-line .pt-sum')`, 30000);
    await click('#points-line .pt-sum');
    await until(`!!document.querySelector('#points-list [data-dec-clear="ask"]')`);
    o.chatBefore = await ev(LIST);
    await click('#points-list [data-dec-clear="ask"]');
    o.chatConfirm = await ev(`document.querySelector('#points-list .pt-clear').textContent.trim()`);
    await shot('7-chat-confirm');
    await click('#points-list [data-dec-clear="go"]');
    await until(`!!document.querySelector('#points-list [data-dec-clear="undo"]')`);
    o.chatCleared = await ev(LIST);
    await click('#points-list .pt-fold-sum[data-fold="older"]');
    o.chatOlder = await ev(LIST);
    await shot('8-chat-cleared');
    await click('#points-list [data-dec-clear="undo"]');
    await until(`${LIST}[0] === 'Decisions'`);
    o.chatUndone = await ev(LIST);
    o.chatScrollX = await ev(`document.documentElement.scrollWidth - innerWidth`);
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(o));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class ClearNeedsYou(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-clear-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        patches = [
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        cls.rooms, cls.past = [], ""
        for i, words in enumerate(["the pads", "the discs", "the chain", "the tyres"]):
            t = chatroom.create_room(f"Brakes {i + 1}: {words}", [{"identity": "claude", "agent": "claude", "role": "engineer", "cwd": str(home)},
                                                                   {"identity": "claude-2", "agent": "claude", "role": "reviewer", "cwd": str(home)}])
            chatroom.post_message(t["id"], "user", f"Look at {words}, please.")
            time.sleep(0.02)
            chatroom.post_message(t["id"], "claude", f"Ask: Which way for {words}?\n- Replace them\n- Wait a month", to="user")
            if i == 3:
                time.sleep(0.02)
                chatroom.post_message(t["id"], "user", "Never mind the tyres, go on with the rest.")
                cls.past = t["id"]
            else:
                cls.rooms.append(t["id"])
            dashboard.assign_session_project(t["id"], proj["id"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        cls.got = {}
        for size in SIZES:
            # Each size starts from the same marks: nothing dealt with.
            for rid in cls.rooms:
                chatroom.restore_dealt(rid, {"dealtAt": 0, "seenAt": 0})
            asks.forget()
            attention.snapshot(0)
            args = {**test_middle.chrome_profile.node_args(), "tmp": cls.tmp.name,
                    "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                    "size": size, "rooms": cls.rooms, "shots": shots}
            script = base / "clear_cdp.js"
            script.write_text(CDP_JS, encoding="utf-8")
            out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
            assert out.returncode == 0, out.stderr[-4000:]
            cls.got[size[0]] = json.loads(out.stdout.strip().splitlines()[-1])
        cls.addClassCleanup(cls.tmp.cleanup)

    def each(self):
        return [(self.got[w], mob, w) for w, _h, mob in SIZES]

    def test_written_past_is_absent(self):
        for o, _mob, w in self.each():
            with self.subTest(width=w):
                self.assertEqual(sorted(o["before"]), sorted(self.rooms), o["before"])
                self.assertNotIn(self.past, o["before"])
                self.assertLessEqual(o["scrollX"], 1)

    def test_row_done(self):
        for o, mob, w in self.each():
            with self.subTest(width=w):
                if o["hoverNone"]:
                    self.assertNotEqual(o["doneShown"], "none", "always there on a touch screen")
                    self.assertGreaterEqual(min(o["doneOnFocus"]["w"], o["doneOnFocus"]["h"]), 44)
                else:
                    self.assertEqual(o["doneShown"], "none", "hidden until hovered or focused")
                self.assertNotEqual(o["doneOnFocus"]["display"], "none")
                self.assertEqual(sorted(o["afterDone"]), sorted(self.rooms[1:]))

    def test_clear_all_confirm_and_undo(self):
        for o, mob, w in self.each():
            with self.subTest(width=w):
                self.assertEqual(o["confirm"], "Clear 2 rows?ClearCancel")
                self.assertEqual(o["cleared"], "Cleared 2Undo")
                self.assertFalse(set(self.rooms) & set(o["hubAfterClear"]), "the hub lists none of them")
                self.assertEqual(sorted(o["afterUndo"]), sorted(self.rooms[1:]), "Undo brings back what Clear all took")
                if mob:
                    self.assertTrue(all(wd >= 44 and ht >= 44 for _t, wd, ht in o["confirmTargets"]), o["confirmTargets"])

    def test_recent_list_has_clear_all(self):
        for o, _mob, w in self.each():
            with self.subTest(width=w):
                self.assertIsNotNone(o["recentHead"])
                self.assertIn("Clear all", o["recentHead"])

    def test_chat_decisions_clear_all_and_undo(self):
        for o, _mob, w in self.each():
            with self.subTest(width=w):
                self.assertEqual(o["chatBefore"][:2], ["Decisions", "ask:Which way for the chain?"], o["chatBefore"])
                self.assertEqual(o["chatConfirm"], "Move 1 to Older?ClearCancel")
                self.assertNotIn("ask:Which way for the chain?", o["chatCleared"], o["chatCleared"])
                self.assertIn("Cleared 1: it is under Older questions.", o["chatCleared"])
                self.assertIn("ask:Which way for the chain?", o["chatOlder"], "under Older questions, not answered")
                self.assertEqual(o["chatUndone"][:2], ["Decisions", "ask:Which way for the chain?"], o["chatUndone"])
                self.assertLessEqual(o["chatScrollX"], 1)

    def test_row_opens_at_its_card(self):
        o = self.got[1440]
        self.assertTrue(o["rowMsg"])
        self.assertEqual(o["land"], o["rowMsg"], o.get("landWhy"))


if __name__ == "__main__":
    unittest.main()
