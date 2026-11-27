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
import threads
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
PO_PRE = "The release waits on one choice of yours."
PO_PRE_CONTEXT = "Dark mode is ready; the print styles are not."
PO_PRE_REPLY = "I keep building the print styles meanwhile."
THR_TOPIC = "Make the speed chart wider"
THR_PRE = "The wider chart has one catch."
THR_CONTEXT = "At full width the legend no longer fits beside the chart on a phone."
THR_REPLY = "I keep the chart as it is until you pick."

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
    // Review 1: a line no longer joined (a thread started on it) is a
    // balloon again, and a link to it no longer goes to the card.
    out.stale = await p.evalIn(`(() => { const c = LAST_ITEMS.find(m => m.joined && m.joined.length); const rid = c.joined[c.joined.length - 1];
      const card = Object.assign({}, c, { joined: undefined, steps: undefined, text: 'Q' });
      const line = { id: rid, from: c.from, ts: c.ts + 1, text: 'words', thr: ['m:' + rid] };
      const g = GOTO; const first = joinAskWords([card, line]).length; const into = JOINED_INTO.get(rid);
      THR_META['m:' + rid] = { chosen: true };
      const second = joinAskWords([card, line]).length; GOTO = rid; joinAskWords([card, line]); const goto = GOTO;
      GOTO = g; delete THR_META['m:' + rid];
      return { first, into, second, goto, rid, card: c.id }; })()`);
    await send('Target.closeTarget', { targetId: p.targetId });
    // A link to the joined reply, opened: it lands on the card's balloon.
    p = await open(A.po + '&msg=' + encodeURIComponent(out.landed.joined[out.landed.joined.length - 1]));
    out.linkLanded = await p.until('typeof LANDED !== "undefined" && LANDED && LANDED.mid');
    await send('Target.closeTarget', { targetId: p.targetId });
    // A PO turn with a line before the call: that line (a step of the reply)
    // reads in the card's balloon, above the context, not under "N steps".
    p = await open(A.pre);
    await p.until(`[...document.querySelectorAll('#msgs .msg')].some(b => b.querySelector('.qa') && b.innerText.includes(${JSON.stringify(A.preReply)}))`);
    await sleep(300);
    out.pre = await p.look();
    // Review 2: a line naming another point is not a card's words, and the
    // line before the call is the latest step that can be (not a Re line).
    out.guards = await p.evalIn(`(() => { const c = LAST_ITEMS.find(m => Array.isArray(m.asks) && m.asks.length);
      const card = Object.assign({}, c, { joined: undefined, steps: undefined, text: 'About P1.', points: ['P1'] });
      const iso = t => new Date(t * 1000).toISOString();
      const other = joinAskWords([card, { id: 'x-p2', from: c.from, ts: c.ts + 1, text: 'For P2, the export ships Monday.' }]);
      const same = joinAskWords([card, { id: 'x-p1', from: c.from, ts: c.ts + 1, text: 'For P1, I hold it.' }]);
      const steps = [{ mid: 'st-a', text: 'Why I ask: the legend.', timestamp: iso(c.ts - 10) },
                     { mid: 'st-b', text: 'Re P1: noted.', timestamp: iso(c.ts - 5) }];
      const multi = joinAskWords([card, { id: 'x-st', from: c.from, ts: c.ts + 2, text: 'I wait.', steps }]);
      return { other: other.length, same: same.length, multiText: multi[0].text, multiSteps: (multi[0].steps || []).map(x => x.mid) }; })()`);
    await send('Target.closeTarget', { targetId: p.targetId });
    // A PO thread: the line before the call, the card and the reply are one
    // balloon in the thread the person started.
    p = await open(A.thr + '&thread=P1');
    await p.until(`[...document.querySelectorAll('#msgs .msg')].some(b => b.querySelector('.qa') && b.innerText.includes(${JSON.stringify(A.thrReply)}))`);
    await sleep(300);
    out.thread = await p.look();
    out.threadMode = await p.evalIn('THREAD_MODE');
    out.threadBar = await p.evalIn(`[...document.querySelectorAll('#msgs .msg')].filter(b => b.querySelector('.qa')).map(b => [...b.querySelectorAll('.pt-bar')].map(x => x.innerText).join(' | ')).join(' || ')`);
    await p.shot('po-thread-one-balloon');
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

        # A PO turn: a line, the call, the reply (the line is a step of the reply).
        ok, third, _ = dashboard.register_project("Print")
        assert ok, third
        cls.pre = solo("Print PO", "pre-sid", "Product owner", third)
        ok, why = dashboard.set_project_po(third["id"], cls.pre)
        assert ok, why
        pre_file = tdir / "pre-sid.jsonl"
        pre_file.write_text("".join(json.dumps(t | {"sessionId": "pre-sid"}) + "\n" for t in [
            _turn("user", "Is the theme work done?", now - 6),
            _turn("assistant", PO_PRE, now - 2, stop="tool_use")]), encoding="utf-8")
        ask(cls.pre, PO_PRE_CONTEXT, "Ship dark mode first?", [{"label": "Yes"}, {"label": "Wait"}], third)
        with pre_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_turn("assistant", PO_PRE_REPLY, time.time() + 1) | {"sessionId": "pre-sid"}) + "\n")

        # A PO thread: the person's P1 sent as a new thread; the PO's line,
        # its card about P1 and the reply.
        ok, fourth, _ = dashboard.register_project("Charts")
        assert ok, fourth
        cls.thr = solo("Charts PO", "thr-sid", "Product owner", fourth)
        ok, why = dashboard.set_project_po(fourth["id"], cls.thr)
        assert ok, why
        t0 = now - 120
        q, ids = points.take(chatroom.get_room(cls.thr, public=False), THR_TOPIC, key="P1", now=t0, sid="thr-sid")
        assert ids == ["P1"], ids
        threads.note_send(cls.thr, "", "P1", ids, False, q, now=t0, new=True)
        thr_file = tdir / "thr-sid.jsonl"
        thr_file.write_text("".join(json.dumps(t | {"sessionId": "thr-sid"}) + "\n" for t in [
            _turn("user", q, t0 + 1), _turn("assistant", THR_PRE, now - 2, stop="tool_use")]), encoding="utf-8")
        room = chatroom.get_room(cls.thr, public=False)
        ensemble_tools._ask({"room": room, "identity": "claude", "part": room["participants"][0], "projectId": fourth["id"]},
                            {"context": THR_CONTEXT, "points": ["P1"], "questions": [
                                {"question": "Wider chart, legend below it?", "options": [{"label": "Yes"}, {"label": "No"}]}]}, handler)
        with thr_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_turn("assistant", THR_REPLY, time.time() + 1) | {"sessionId": "thr-sid"}) + "\n")
        points.sync(cls.thr, force=True)

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
                "task": cls.task, "taskReply": TASK_REPLY, "team": cls.team, "pre": cls.pre, "preReply": PO_PRE_REPLY,
                "thr": cls.thr, "thrReply": THR_REPLY}
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

    def test_a_line_no_longer_joined_is_a_balloon_and_its_link_lands_on_it(self):
        st = self.got["stale"]
        self.assertEqual((st["first"], st["into"]), (1, st["card"]), st)
        self.assertEqual(st["second"], 2, "a thread on the line: a balloon again")
        self.assertEqual(st["goto"], st["rid"], "its link lands on the line, not the card")

    def test_a_link_to_the_joined_reply_lands_on_the_card(self):
        self.assertEqual(self.got["linkLanded"], self.got["landed"]["card"])

    def test_a_po_line_before_the_call_reads_above_the_card(self):
        pre = self.got["pre"]
        cards = [b for b in pre if b["cards"]]
        self.assertEqual(len(cards), 1, pre)
        text = cards[0]["text"]
        order = [text.index(PO_PRE), text.index(PO_PRE_CONTEXT), text.index(PO_PRE_REPLY),
                 text.index("Ship dark mode first?")]
        self.assertEqual(order, sorted(order), text)
        for words in (PO_PRE, PO_PRE_REPLY):
            self.assertEqual(len(self.holding(pre, words)), 1, words)

    def test_a_line_naming_another_point_is_not_the_cards_words(self):
        g = self.got["guards"]
        self.assertEqual(g["other"], 2, "a P1 card and a line about P2: two balloons")
        self.assertEqual(g["same"], 1, "a line about the card's own point joins it")

    def test_the_line_before_the_call_is_the_latest_step_that_can_be(self):
        g = self.got["guards"]
        self.assertTrue(g["multiText"].startswith("Why I ask: the legend."), g)
        self.assertEqual(g["multiSteps"], ["st-b"], "the Re step stays a step")

    def test_a_po_thread_draws_the_card_and_its_words_as_one_balloon(self):
        self.assertTrue(self.got["threadMode"])
        balloons = self.got["thread"]
        cards = [b for b in balloons if b["cards"]]
        self.assertEqual(len(cards), 1, balloons)
        text = cards[0]["text"]
        order = [text.index(THR_PRE), text.index(THR_CONTEXT), text.index(THR_REPLY),
                 text.index("Wider chart, legend below it?")]
        self.assertEqual(order, sorted(order), text)
        for words in (THR_PRE, THR_REPLY):
            self.assertEqual(len(self.holding(balloons, words)), 1, words)
        # The reply answers P1 (the thread's first reply): its bar stays on the balloon.
        self.assertIn("answers P1", self.got["threadBar"], "the reply's point bar rides with it")
        self.assertIn("Ack", self.got["threadBar"])

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
