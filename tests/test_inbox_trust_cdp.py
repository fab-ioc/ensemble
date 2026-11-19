"""An Inbox you can trust (#394, P394), in headless Chrome over CDP against a
hub in a thread serving the pages.

A long PO chat (90 messages: its first page holds only the newest), two old
messages near its start and a recent answer to check in the Inbox. Checked at
1440x900:

* the same old message opened from the Inbox four times in turn (the chat
  back on its newest page between), and another old one between: every open
  lands on it, marked from the Inbox; the chat never says it is not shown;
* after a landing, the chat shown again (the dashboard's `shown`) and its
  frame back from 0 high leave the landed message where it was, not at the
  latest;
* an open whose chat page starts again after it took the link (goto-taken),
  before it landed: the new page is asked again and lands; the dashboard
  keeps the link until the chat says it landed (goto-done), then drops it;
* a poll's news under a press on an Inbox row is not drawn (the row pressed
  is the row clicked), and is drawn once the click has run; without a press
  it is drawn at once;
* in a real PO chat (threaded, 300 turns after its card), the four rows of
  one card of four questions opened in turn, and the first again, the chat
  back on its newest page between: every open lands on the card;
* a message the PO filed in a thread (its reply to the person's new thread):
  opened from the Inbox, the thread's panel opens beside the chat and marks
  it "From the Inbox"; the chat says nothing is missing;
* the pages threw nothing.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
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

import tests.test_inbox_highlight_cdp as hl
import threads
from tests.test_threads_page import _turn

chatroom, dashboard, points, NODE, CHROME = hl.chatroom, hl.dashboard, hl.points, hl.NODE, hl.CHROME
_HL = hl.CDP_JS

# The launcher, Cdp class, DOC and MEASURE of test_inbox_highlight_cdp, with this test's main().
CDP_JS = _HL[: _HL.index("async function main()")] + r"""
const FRAME = room => `[...document.querySelectorAll('iframe.po-session, iframe.dp-session')].find(f => f.dataset.room === ${JSON.stringify(room)})`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(50); } throw new Error('timeout: ' + expr); };
  const mouse = async (type, x, y) => c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
  const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
  await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
  const F = FRAME(A.po);
  const inF = (expr, room = A.po) => `(() => { const f = ${FRAME(room)}; return f && f.contentWindow ? f.contentWindow.eval(${JSON.stringify(expr)}) : null; })()`;
  const chatReady = (room = A.po) => until(inF('typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN && IBX_HL_READ', room), 30000);
  const drawn = (mid, room = A.po) => inF(`[...document.querySelectorAll('.msg[data-mid]')].some(e => e.dataset.mid === ${JSON.stringify(mid)})`, room);
  // On screen in its chat: some of the message (or of its part) inside the box.
  const onScreen = (mid, room = A.po, part = '') => inF(`(() => { const box = document.getElementById('msgs'), m = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
    const el = m && (${JSON.stringify(part)} ? partOf(m, ${JSON.stringify(part)}) : m); if (!el) return false;
    const b = box.getBoundingClientRect(), r = el.getBoundingClientRect(); return r.bottom > b.top + 1 && r.top < b.bottom - 1 && r.height > 0; })()`, room);
  const note = (room = A.po) => inF(`(() => { const n = document.getElementById('goto-note'); return n && !n.hidden ? n.textContent.trim() : ''; })()`, room);
  // Landed: marked, measured once its scroll has settled (0.4 s on).
  const landedOn = async mid => {
    const m = await until(`(() => { const m = ${MEASURE(mid)}; return !!m && m.marked && m.age >= 0 ? m : null; })()`, 15000).catch(() => null);
    if (!m) return null;
    if (m.age < 400) await sleep(400 - m.age);
    return evalIn(MEASURE(mid));
  };
  // The chat back on its newest page (its pages started again, as the Latest
  // button does in a link's window): the old messages are not drawn.
  // A threaded chat whose newest page still holds them starts its pages again
  // (as the Latest button does there): they are no longer drawn. A room chat
  // keeps the older pages it fetched and goes to its end: they are off screen.
  const latest = async (room = A.po, mids = [A.old, A.old2]) => {
    const threaded = await evalIn(inF(`(() => { toLatest(); if (THREADED && [...document.querySelectorAll('.msg[data-mid]')].some(e => ${JSON.stringify(mids)}.includes(e.dataset.mid))) { thrRestart(); renderThreaded(true); } return THREADED; })()`, room));
    return until(mids.map(m => threaded ? `!(${drawn(m, room)})` : `!(${onScreen(m, room)})`).join(' && '), 10000).then(() => true, () => false);
  };
  const state = (mid, room = A.po) => evalIn(inF(`(() => { const b = document.getElementById('msgs'), mid = ${JSON.stringify(mid)}; return { stick: STICK, top: Math.round(b.scrollTop), height: b.scrollHeight, client: b.clientHeight, after: THR_AFTER, before: THR_BEFORE, asked: THR_ASKED, goto: GOTO, n: (LAST_ITEMS || []).length,
    threaded: THREADED, inItems: (LAST_ITEMS || []).some(m => m.id === mid), gap: GAP_OF_MID.get(mid) || '', group: GROUP_OF_MID.get(mid) || '', open: [...FOLD.groups].join(' '), drawnN: b.querySelectorAll('.msg[data-mid]').length, kids: b.children.length, win: !!ROOM_WIN }; })()`, room));
  const open = async (mid, room = A.po, part = '') => { await evalIn(`openMsgLink(${JSON.stringify(room)}, ${JSON.stringify(mid)}, ${JSON.stringify('inbox' + (part ? ',' + part : ''))}); 0`); const m = await landedOn(mid); return { m, partShown: part ? await evalIn(onScreen(mid, room, part)) : null, note: await evalIn(note(room)), pending: await evalIn(`GOTO_MSG.has(${JSON.stringify(room)})`), state: await state(mid, room) }; };
  try {
    await until('typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ') && window.ensBooted === true', 30000);
    await evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await chatReady();
    out.oldDrawnAtStart = await evalIn(drawn(A.old));
    // (1) the same old message, four times in turn, another one between
    out.opens = [];
    for (const [mid, back] of [[A.old, false], [A.old, true], [A.old2, true], [A.old, true], [A.old, false]]) {
      const r = { mid, back: back ? await latest() : null }; r.pre = await state(mid); Object.assign(r, await open(mid));
      await shot('open-' + out.opens.length);
      out.opens.push(r);
      await sleep(400);
    }
    // (2) shown again and back from 0 high: the landed message stays
    try {
    await latest();
    const o2 = await open(A.old);
    await sleep(2300);   // past the gesture's 2 s
    const at = () => evalIn(`(() => { const m = ${MEASURE(A.old)}; return m && { gap: m.gap, inside: m.inside }; })()`);
    out.keep = { landed: !!o2.m, before: await at() };
    await evalIn(`(() => { const f = ${F}; f.contentWindow.postMessage({ ensemble: 'shown' }, location.origin); return 0; })()`);
    await sleep(700);
    out.keep.shown = await at();
    out.keep.stick = await evalIn(inF('STICK'));
    await evalIn(`(() => { const f = ${F}; f.style.setProperty('height', '0px', 'important'); return 0; })()`);
    await sleep(500);
    await evalIn(`(() => { const f = ${F}; f.style.removeProperty('height'); return 0; })()`);
    await sleep(900);
    out.keep.resized = await at();
    await evalIn(inF('toLatest(), 0'));
    await sleep(300);
    out.keep.toLatestClears = await evalIn(inF('LAND_KEEP === null'));
    } catch (e) { out.keepErr = String(e).slice(0, 300); }
    // (6) the chat page starts again after it took the link, before it landed
    // (the chat started afresh first: the old message is not among those held)
    try {
    await evalIn(`(() => { const f = ${F}; f.contentWindow.location.reload(); return 0; })()`);
    await sleep(300);
    await chatReady();
    out.reloadHeldBefore = await evalIn(drawn(A.old2));
    // This page never gets the older messages it asks for: it cannot land before the page starts again.
    await evalIn(inF(`(() => { const f0 = window.fetch; window.fetch = (u, ...r) => String(u).includes('before=') ? new Promise(() => {}) : f0(u, ...r); return 0; })()`));
    out.reload = await evalIn(`new Promise(res => {
      const rid = ${JSON.stringify(A.po)}, f = ${F}, seen = [];
      const on = ev => { const d = ev.data || {};
        if (d.type === 'goto-taken' && d.roomId === rid && !seen.includes('taken')) { seen.push('taken'); seen.push(GOTO_MSG.has(rid) ? 'kept' : 'dropped'); f.contentWindow.location.reload(); }
        if (d.type === 'goto-done' && d.roomId === rid) { seen.push('done:' + d.ok); setTimeout(() => { seen.push(GOTO_MSG.has(rid) ? 'kept' : 'dropped'); window.removeEventListener('message', on); res(seen); }, 0); } };
      window.addEventListener('message', on);
      openMsgLink(rid, ${JSON.stringify(A.old2)}, 'inbox');
      setTimeout(() => { window.removeEventListener('message', on); res(seen.concat('timeout')); }, 25000);
    })`);
    out.reloadLanded = !!(await landedOn(A.old2));
    out.reloadNote = await evalIn(note());
    } catch (e) { out.reloadErr = String(e).slice(0, 300); }
    // (4) a poll's news under a press on an Inbox row
    try {
    await latest();
    const link = `[...LD_INBOX.querySelectorAll('a.ibx-link[data-mid]')].find(a => a.dataset.mid === ${JSON.stringify(A.answer)} && a.getBoundingClientRect().width > 0)`;
    await until(`!!(${link})`, 20000);
    const [x, y] = await evalIn(`(() => { const e = ${link}; e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
    const poll = `(() => { IBX.sig = IBX.phSig = IBX.ldSig = ''; ibxPolledPaint(); return 0; })()`;
    const same = `(${link}) === window.__ibxA`;
    await evalIn(`window.__ibxA = ${link}; 0`);
    await mouse('mouseMoved', x, y);
    await mouse('mousePressed', x, y);
    await evalIn(poll);
    out.press = { sameUnderPress: await evalIn(same), held: await evalIn('!!IBX.held') };
    await mouse('mouseReleased', x, y);
    out.press.landed = !!(await landedOn(A.answer));
    await sleep(200);
    out.press.heldAfter = await evalIn('!!IBX.held');
    out.press.drawnAfter = !(await evalIn(same)) && !!(await evalIn(`!!(${link})`));
    await evalIn(`window.__ibxA = ${link}; 0`);
    await mouse('mouseMoved', 5, 890);
    await sleep(300);
    await evalIn(poll);
    out.press.drawnWithoutPress = !(await evalIn(same));
    } catch (e) { out.pressErr = String(e).slice(0, 300); }
    // (7) a real PO chat (threaded, long): one card of four questions, a row
    // each, opened in turn with the chat back on its newest page between, and
    // its first question again
    try {
    out.threaded = [];
    for (const [i, part] of ['ask:0', 'ask:1', 'ask:2', 'ask:3', 'ask:0'].entries()) {
      let back = null;
      if (i) back = await latest(A.tpo, [A.card]);
      else { await evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.tproj)}; renderRows(); return 0; })()`); await chatReady(A.tpo); }
      const r = { part, back, pre: await state(A.card, A.tpo) };
      Object.assign(r, await open(A.card, A.tpo, part));
      await shot('threaded-' + i);
      out.threaded.push(r);
      await sleep(400);
    }
    } catch (e) { out.threadedErr = String(e).slice(0, 300); }
    // (8) a row whose message is filed in a thread: the thread's panel opens
    // beside the chat and marks it as opened from the Inbox
    try {
    const SIDE = `PD.rt.get('thread:po')?.el.querySelector('iframe')`;
    await latest(A.tpo, [A.card]);
    const done = evalIn(`new Promise(res => { const on = ev => { const d = ev.data || {}; if (d.type === 'goto-done' && d.roomId === ${JSON.stringify(A.tpo)}) { window.removeEventListener('message', on); res({ ok: d.ok, why: d.why }); } };
      window.addEventListener('message', on); setTimeout(() => res(null), 20000); })`);
    await evalIn(`openMsgLink(${JSON.stringify(A.tpo)}, ${JSON.stringify(A.tmid)}, 'inbox'); 0`);
    const mark = `(() => { const f = ${SIDE}; const d = f && f.contentDocument; const m = d && [...d.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(A.tmid)});
      if (!m) return null; const b = d.getElementById('msgs').getBoundingClientRect(), r = m.getBoundingClientRect();
      return { landed: m.classList.contains('ibx-landed'), label: ((m.querySelector('.lz-inbox') || {}).textContent || '').trim(), inside: r.bottom > b.top + 1 && r.top < b.bottom - 1 }; })()`;
    out.thread = { m: await until(`(() => { const v = ${mark}; return v && v.landed ? v : null; })()`, 20000).catch(() => null) };
    out.thread.last = await evalIn(mark);
    out.thread.done = await done;
    out.thread.inChannel = await evalIn(inF(`[...document.querySelectorAll('#msgs .msg[data-mid]')].some(e => e.dataset.mid === ${JSON.stringify(A.tmid)} && e.offsetParent)`, A.tpo));
    out.thread.note = await evalIn(note(A.tpo));
    await shot('thread-landed');
    } catch (e) { out.threadErr = String(e).slice(0, 300); }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _mid(m):
    return ((m or {}).get("message") or {}).get("id", "")


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class InboxTrustInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibxtrust-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        m = hl.mock
        cls.patches = [
            m.patch.object(dashboard, "PROJECTS_ROOT", root),
            m.patch.object(dashboard, "DASHBOARD_DIR", state),
            m.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            m.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            m.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            m.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            m.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            m.patch.object(dashboard, "CS_ROOT", base / "cs"),
            m.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            m.patch.object(dashboard, "load_live", lambda: []),
            m.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            m.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            m.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            m.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        ok, proj, _ = dashboard.register_project("Atlas")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("Atlas PO", members(home))
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        R = po["id"]
        text, _ = points.take(chatroom.get_room(R, public=False), "Make the board columns wider", to="claude", key="k1")
        chatroom.post_message(R, "user", text, to="claude")
        ids = {}
        for i in range(90):
            mm = chatroom.post_message(R, "claude" if i % 3 else "codex", f"Board note {i + 1}.\n\n{hl.LONG}",
                                       to="user" if i % 3 else "claude")
            if i == 4:
                ids["old"] = _mid(mm)
            if i == 9:
                ids["old2"] = _mid(mm)
        ids["answer"] = _mid(chatroom.post_message(R, "claude", "Re P1: the columns are wider now, please try it on the board.\n\n" + hl.LONG, to="user"))
        for i in range(3):
            chatroom.post_message(R, "claude", f"After the answer {i + 1}.\n\n{hl.LONG}", to="user")
        points.sync(R, force=True)
        ids.update(cls._threaded_po(base))
        cls.ids = {"proj": proj["id"], "po": R, **ids}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**hl.chrome_profile.node_args(), **cls.ids, "tmp": cls.tmp.name, "shots": shots,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        script = base / "inbox_trust_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_DUMP"):
            Path(os.environ["ENSEMBLE_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def _threaded_po(cls, base: Path) -> dict:
        """A PO chat as a real one is: one agent (a solo, threaded chat drawn from
        its transcript), a card of four questions near its start and 300 turns
        after it, so the card is far outside the newest page."""
        threads.forget()
        ok, proj, _ = dashboard.register_project("Brakes")
        assert ok, proj
        sid = "brakes-po"
        po = chatroom.create_room("Brakes PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid}])
        rid = po["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(rid, proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], rid)
        assert ok, why
        t0 = time.time() - 7200
        lines = [_turn(sid, "assistant", "I'm the PO for Brakes.", t0)]
        qs = dashboard.asks.validated([{"question": f"Brake question {i + 1}?", "options": [{"label": "Yes"}, {"label": "No"}]}
                                       for i in range(4)], [])
        with hl.mock.patch.object(chatroom, "_now", lambda: t0 + 10):
            card = _mid(chatroom.post_message(rid, "claude", "Questions for you.", to="user", structured_asks=qs, ask_audience="user"))
        for i in range(300):
            role = "user" if i % 2 else "assistant"
            lines.append(_turn(sid, role, f"Brake turn {i + 1}.\n\n" + ("" if role == "user" else hl.LONG), t0 + 20 + i * 20))
        tdir = base / "transcripts" / "C--po"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        # The person sent "Brake turn 2." as a new thread: the PO's reply to it
        # (turn 3) is filed in that thread, not in the channel (#367).
        threads.note_send(rid, "", "send:brk", [], False, "Brake turn 2.", now=t0 + 40, new=True)
        points.sync(rid, force=True)
        threads._ensure_since(rid, t0 - 60)
        threads.build(rid, show_progress=False)
        return {"tproj": proj["id"], "tpo": rid, "card": card, "tmid": f"{sid}:3"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_old_messages_are_not_on_the_first_page(self):
        self.assertFalse(self.got["oldDrawnAtStart"])

    def test_every_open_of_an_old_message_lands_on_it(self):
        for i, r in enumerate(self.got["opens"]):
            with self.subTest(i=i, mid=r["mid"]):
                if r["back"] is not None:
                    self.assertTrue(r["back"], "the chat was back on its newest page first")
                self.assertIsNotNone(r["m"], r)
                self.assertTrue(r["m"]["marked"], r)
                self.assertEqual(r["m"]["label"].lower(), "from the inbox", r)
                self.assertTrue(r["m"]["inside"], r)
                self.assertEqual(r["m"]["otherMarked"], 0, r)
                self.assertEqual(r["note"], "", r)
                self.assertFalse(r["pending"], "the dashboard dropped the link once it landed")

    def test_shown_again_or_resized_the_landed_message_stays(self):
        k = self.got["keep"]
        self.assertTrue(k["landed"], k)
        self.assertIsNotNone(k["before"], k)
        for when in ("shown", "resized"):
            with self.subTest(when):
                self.assertIsNotNone(k[when], k)
                self.assertTrue(k[when]["inside"], k)
                self.assertLessEqual(abs(k[when]["gap"] - k["before"]["gap"]), 6, k)
        self.assertFalse(k["stick"], k)
        self.assertTrue(k["toLatestClears"], k)

    def test_a_chat_page_starting_again_before_it_landed_is_asked_again(self):
        self.assertFalse(self.got["reloadHeldBefore"], "the old message is not among those held")
        seen = self.got["reload"]
        self.assertEqual(seen[:2], ["taken", "kept"], seen)
        self.assertIn("done:true", seen, seen)
        self.assertEqual(seen[-1], "dropped", seen)
        self.assertTrue(self.got["reloadLanded"])
        self.assertEqual(self.got["reloadNote"], "")

    def test_a_poll_is_not_drawn_under_a_press(self):
        p = self.got["press"]
        self.assertTrue(p["sameUnderPress"], p)
        self.assertTrue(p["held"], p)
        self.assertTrue(p["landed"], "the row pressed is the row clicked")
        self.assertFalse(p["heldAfter"], p)
        self.assertTrue(p["drawnAfter"], p)
        self.assertTrue(p["drawnWithoutPress"], p)

    def test_every_row_of_one_card_in_a_long_threaded_chat_lands(self):
        self.assertNotIn("threadedErr", self.got)
        opens = self.got["threaded"]
        self.assertEqual(len(opens), 5)
        for i, r in enumerate(opens):
            with self.subTest(i=i, part=r["part"]):
                self.assertTrue(r["pre"]["threaded"], "the PO chat is threaded")
                if r["back"] is not None:
                    self.assertTrue(r["back"], "the chat was back on its newest page first")
                self.assertIsNotNone(r["m"], r)
                self.assertTrue(r["m"]["marked"], r)
                self.assertTrue(r["partShown"], "its question is on screen (the card is taller than the chat)")
                self.assertEqual(r["note"], "", r)
                self.assertFalse(r["pending"], r)

    def test_a_message_filed_in_a_thread_lands_in_its_panel_marked(self):
        self.assertNotIn("threadErr", self.got)
        t = self.got["thread"]
        self.assertIsNotNone(t["m"], t)
        self.assertTrue(t["m"]["inside"], t)
        self.assertEqual(t["m"]["label"].lower(), "from the inbox", t)
        self.assertFalse(t["inChannel"], "filed in the thread, not drawn in the channel")
        self.assertEqual(t["note"], "", t)
        self.assertTrue(t["done"] and t["done"]["ok"], t)

    def test_no_section_failed(self):
        for k in ("keepErr", "reloadErr", "pressErr"):
            self.assertNotIn(k, self.got)

    def test_the_pages_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
