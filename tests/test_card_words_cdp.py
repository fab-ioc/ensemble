"""#430 (GitHub issue 73): a question card and its explanation are one
balloon on the page. In the PO chat the card shows with its context while
the turn runs, and the reply written after the call joins that balloon above
the card; in a task's one-agent chat the line written just before the call
joins too; in a team chat the agent's next message after its card does; a
card with no words around it shows on its own.

ENSEMBLE_SHOTS=<dir> saves the PO chat's screenshots there."""
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

CONTEXT = ("The CSV export is done and tested. JSON still needs a day: dates come out in two formats "
           "and the importer rejects one of them.\n\nShipping CSV alone now is safe; holding both to "
           "Monday keeps one release note.")
REPLY = "I hold the release until you pick."
PRE = "Before I build the task view, one choice is yours."
TASK_CONTEXT = "The list can open on the board or on the task's chat."
TASK_REPLY = "I build the rest meanwhile."
TEAM_WORDS = "The review found two ways to fix the retry; both pass the tests."

JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const out = {};
  const open = async (room, w = 1200, h = 700) => {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1.5, mobile: false }, sessionId);
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 40000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr + ' :: ' + JSON.stringify(await look().catch(() => null)) + ' :: ' + JSON.stringify(await evalIn(DEBUG).catch(e => String(e)))); };
    const DEBUG = `(() => { const c = (LAST_ITEMS || []).find(x => Array.isArray(x.asks)); return (LAST_ITEMS || []).map(m => ({ id: m.id, ts: m.ts, kind: m.kind, from: m.from, card: c && c.from, cts: c && c.ts, j: c ? askJoinable(m, c, false) : null })); })()`;
    const look = () => evalIn(`[...document.querySelectorAll('#msgs .msg')].map(b => ({ mid: b.dataset.mid, cards: b.querySelectorAll('.qa').length,
      text: (b.querySelector('.text') || b).innerText }))`);
    const shot = async name => { if (!A.shots) return; await evalIn(`(document.querySelector('#msgs .qa') || document.body).scrollIntoView({ block: 'end' }); 0`); await sleep(300);
      const r = await send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await send('Page.navigate', { url: A.base + '/session?room=' + room }, sessionId);
    await until('document.querySelectorAll("#msgs .qa").length >= 1');
    await sleep(500);
    return { targetId, evalIn, until, look, shot };
  };
  try {
    // The PO chat: the card while the turn runs, then the reply joins it.
    let p = await open(A.po);
    out.poBefore = await p.look();
    await p.shot('po-while-the-turn-runs');
    fs.appendFileSync(A.poFile, A.poReply + '\n');
    await p.until(`[...document.querySelectorAll('#msgs .msg')].some(b => b.querySelector('.qa') && b.innerText.includes(${JSON.stringify(A.reply)}))`);
    await sleep(500);
    out.poAfter = await p.look();
    // A link to the reply lands on the card's balloon.
    out.landed = await p.evalIn(`(() => { const r = (LAST_ITEMS || []).find(m => m.joined && m.joined.length); return r ? { card: r.id, joined: r.joined, into: JOINED_INTO.get(r.joined[r.joined.length - 1]) } : null; })()`);
    await p.shot('po-reply-joins');
    await send('Target.closeTarget', { targetId: p.targetId });
    // A task's one-agent chat: the line before the call, the context, the reply.
    p = await open(A.task);
    await p.until(`[...document.querySelectorAll('#msgs .msg')].some(b => b.querySelector('.qa') && b.innerText.includes(${JSON.stringify(A.taskReply)}))`);
    out.task = await p.look();
    await send('Target.closeTarget', { targetId: p.targetId });
    // A team chat: the agent's next message after its card; a bare card alone.
    p = await open(A.team);
    await p.until('document.querySelectorAll("#msgs .qa").length >= 2');
    await sleep(300);
    out.team = await p.look();
    await send('Target.closeTarget', { targetId: p.targetId });
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class CardWordsInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-cardwords-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "EnsembleProjects").mkdir()
        tdir = base / "transcripts" / "C--po"
        tdir.mkdir(parents=True)
        (base / "cs").mkdir()

        def resume(h, room, text="", to="", key="", quiet=False):
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
            mock.patch.object(dashboard, "operator_name", return_value=os.environ.get("ENSEMBLE_SHOT_NAME", "sam")),
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
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler._ring_recipients = lambda *a: []
        now = time.time()

        ok, other, _ = dashboard.register_project("Solo")      # no PO: a task's card is the person's
        assert ok, other

        def solo(title, sid, role, project=project):
            r = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "role": role, "sessionId": sid}])
            room = chatroom.get_room(r["id"], public=False)
            room["mode"] = "solo"
            chatroom.update_room(room)
            dashboard.assign_session_project(r["id"], project["id"])
            return r["id"]

        def ask(rid, context, question, options, project=project):
            room = chatroom.get_room(rid, public=False)
            ctx = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": project["id"]}
            return ensemble_tools._ask(ctx, {"context": context, "questions": [
                {"question": question, "options": options}]}, handler)

        # The PO chat, as drawn: fab's question, the card; the reply comes later.
        cls.po = solo("Cards PO", "po-sid", "Product owner")
        ok, why = dashboard.set_project_po(project["id"], cls.po)
        assert ok, why
        cls.po_file = tdir / "po-sid.jsonl"
        cls.po_file.write_text("".join(json.dumps(t) + "\n" for t in [
            _turn("user", "Can the export ship this week?", now - 4)]), encoding="utf-8")
        ask(cls.po, CONTEXT, "Which export goes out this week?",
            [{"label": "CSV now, JSON Monday", "recommended": True}, {"label": "Both on Monday"}])
        cls.po_reply = json.dumps(_turn("assistant", REPLY, time.time() + 2))

        # A task's one-agent chat: a line, the call, the reply.
        cls.task = solo("Task view", "task-sid", "Engineer", other)
        task_file = tdir / "task-sid.jsonl"
        task_file.write_text("".join(json.dumps(t | {"sessionId": "task-sid"}) + "\n" for t in [
            _turn("user", "Build the task view.", now - 6),
            _turn("assistant", PRE, now - 2, stop="tool_use")]), encoding="utf-8")
        ask(cls.task, TASK_CONTEXT, "Where does the list open?", [{"label": "Board"}, {"label": "Chat"}], other)
        with task_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_turn("assistant", TASK_REPLY, time.time() + 1) | {"sessionId": "task-sid"}) + "\n")

        # A team chat: a card then the agent's next message; an older bare card.
        team = chatroom.create_room("Retry fix", [
            {"identity": "claude", "agent": "claude", "role": "Engineer", "sessionId": "eng-sid"},
            {"identity": "codex", "agent": "codex", "role": "Reviewer", "sessionId": "rev-sid"}])
        cls.team = team["id"]
        dashboard.assign_session_project(cls.team, other["id"])
        cls.bare = chatroom.post_message(cls.team, "claude", "Decision needed: Keep the old retry flag?", to="user",
                                         structured_asks=asks.safety("Decision needed: Keep the old retry flag?"))["message"]["id"]
        time.sleep(1.1)
        chatroom.post_message(cls.team, "codex", "Reviewed: two ways pass.", to="claude")
        time.sleep(1.1)
        cls.stock = chatroom.post_message(cls.team, "claude", "Questions for you.", to="user", ask_audience="user",
                                          structured_asks=[{"question": "Which retry fix ships?", "n": 0,
                                                            "options": [{"label": "Backoff"}, {"label": "Queue"}]}])["message"]["id"]
        cls.words = chatroom.post_message(cls.team, "claude", TEAM_WORDS, to="user")["message"]["id"]

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
                "base": f"http://127.0.0.1:{server.server_address[1]}", "shots": shots,
                "po": cls.po, "poFile": str(cls.po_file), "poReply": cls.po_reply, "reply": REPLY,
                "task": cls.task, "taskReply": TASK_REPLY, "team": cls.team}
        path = base / "cardwords_cdp.js"
        path.write_text(JS, encoding="utf-8")
        run = subprocess.run([NODE, str(path), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    @staticmethod
    def holding(balloons, words):
        return [b for b in balloons if words in b["text"]]

    def test_the_po_card_shows_with_its_explanation_while_the_turn_runs(self):
        before = self.got["poBefore"]
        cards = [b for b in before if b["cards"]]
        self.assertEqual(len(cards), 1, before)
        self.assertIn("The CSV export is done and tested.", cards[0]["text"])
        self.assertNotIn("Questions for you.", cards[0]["text"])
        self.assertEqual(len(self.holding(before, "The CSV export is done")), 1, before)

    def test_the_po_reply_joins_the_card_balloon_above_the_card(self):
        after = self.got["poAfter"]
        cards = [b for b in after if b["cards"]]
        self.assertEqual(len(cards), 1, after)
        text = cards[0]["text"]
        self.assertLess(text.index("The CSV export is done"), text.index(REPLY))
        self.assertLess(text.index(REPLY), text.index("Which export goes out this week?"))
        self.assertEqual(len(self.holding(after, REPLY)), 1, "no second balloon for the reply")
        landed = self.got["landed"]
        self.assertEqual(landed["into"], landed["card"], "a link to the reply finds the card's balloon")

    def test_a_task_chat_joins_the_line_before_the_call_and_the_reply(self):
        task = self.got["task"]
        cards = [b for b in task if b["cards"]]
        self.assertEqual(len(cards), 1, task)
        text = cards[0]["text"]
        order = [text.index(PRE), text.index(TASK_CONTEXT), text.index(TASK_REPLY), text.index("Where does the list open?")]
        self.assertEqual(order, sorted(order), text)
        for words in (PRE, TASK_REPLY):
            self.assertEqual(len(self.holding(task, words)), 1, words)

    def test_a_team_chat_joins_the_next_message_and_leaves_a_bare_card_alone(self):
        team = self.got["team"]
        by_mid = {b["mid"]: b for b in team}
        joined = by_mid[self.stock]
        self.assertIn(TEAM_WORDS, joined["text"])
        self.assertNotIn("Questions for you.", joined["text"], "the stock line goes once words join it")
        self.assertNotIn(self.words, by_mid, "the next message is in the card's balloon")
        self.assertEqual(by_mid[self.bare]["cards"], 1)
        self.assertNotIn("two ways", by_mid[self.bare]["text"], "the reviewer's line between keeps it alone")
        self.assertIn("Reviewed: two ways pass.", "\n".join(b["text"] for b in team))


if __name__ == "__main__":
    unittest.main()
