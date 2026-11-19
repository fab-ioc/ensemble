"""A message that could not be opened says so on its Inbox row (#394, P394
drawing, part 2 option 1), in headless Chrome over CDP against a hub in a
thread serving the pages.

A long PO chat (Atlas, 90 messages: its first page holds only the newest)
with an answer to check in the Inbox, and a second project (Brakes) whose
screen is open. The Inbox lists All. Checked at 1440x900:

* a row whose message is not in its conversation, pressed: its chat cannot
  land on it, and that row (only that one) shows the amber note "Could not
  open this message: it was not found in its conversation ..." with Open the
  conversation and Done, shown without hover;
* the note survives a poll's repaint;
* Open the conversation, from the other project's screen, opens the chat;
* Done takes the note away (the row stays) and the focus goes to its link;
* pressed again and failing, then pressed with its real message: it lands and
  the note goes;
* a message whose conversation could not load it: the note says it could not
  be loaded and to reload;
* nothing is wider than the column, the pages threw nothing.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import tests.test_inbox_highlight_cdp as hl

chatroom, dashboard, points, NODE, CHROME = hl.chatroom, hl.dashboard, hl.points, hl.NODE, hl.CHROME
_HL = hl.CDP_JS

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
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(80); } throw new Error('timeout: ' + expr); };
  const click = async (expr) => {
    const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1435, y: 895 }, sessionId);
  };
  const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope", "cd-list-dock", "cd-list-dock-a7", "cd-ld-sel"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
  await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
  const inF = (expr, room = A.po) => `(() => { const f = ${FRAME(room)}; return f && f.contentWindow ? f.contentWindow.eval(${JSON.stringify(expr)}) : null; })()`;
  const chatReady = (room = A.po) => until(inF('typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN && IBX_HL_READ', room), 30000);
  const LINK = `[...LD_INBOX.querySelectorAll('a.ibx-link[data-mid]')].find(a => a.dataset.mid === ${JSON.stringify(A.answer)} && a.getBoundingClientRect().width > 0)`;
  // The answer's row, its id, and what the box shows of the notes.
  const view = () => evalIn(`(() => { const a = ${LINK}, li = a && a.closest('li.ibx-row[data-ibx]');
    const notes = [...LD_INBOX.querySelectorAll('.ibx-fail')], f = li && li.querySelector('.ibx-fail');
    const r = li && li.getBoundingClientRect(), b = (li && li.closest('.ibx')) || LD_INBOX, br = b.getBoundingClientRect();
    const acts = li ? [...li.querySelectorAll('.ibx-fail-acts .ibx-btn')] : [];
    return { id: li ? li.dataset.ibx : '', notes: notes.length, inRow: !!f, text: f ? f.textContent.trim() : '',
      bg: f ? getComputedStyle(f).backgroundColor : '', warnBg: (() => { const e = document.createElement('i'); e.style.color = 'var(--c-warning-bg)'; document.body.appendChild(e); const v = getComputedStyle(e).color; e.remove(); return v; })(),
      acts: acts.map(x => [x.textContent.trim(), getComputedStyle(x).display !== 'none' && x.getBoundingClientRect().width > 0]),
      inside: !r || acts.every(x => { const q = x.getBoundingClientRect(); return q.left >= r.left - 0.5 && q.right <= r.right + 0.5; }),
      failed: [...IBX.failed.keys()], pressed: !!IBX.pressed,
      W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth, boxW: Math.round(br.width), boxSW: b.scrollWidth }; })()`);
  // Press the answer's row with its link pointing at a message its chat does not have.
  const pressGone = async (mid) => {
    await until(`!!(${LINK})`, 20000);
    await evalIn(`(() => { const a = ${LINK}; a.dataset.mid = ${JSON.stringify(mid)}; window.__gone = a; return 0; })()`);
    await click('window.__gone');
  };
  try {
    await until('typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ') && window.ensBooted === true', 30000);
    await evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.other)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!LD.dock && !!IBX.data && !IBX.data.pending', 30000);
    await click(`document.querySelector('#ld-projs [data-ldp="all"]')`);
    await until(`!!(${LINK})`, 20000);
    out.before = await view();
    // (1) a message not in its conversation
    await pressGone('m-not-there-1');
    await chatReady();
    await until(`IBX.failed.size > 0 && !!LD_INBOX.querySelector('.ibx-fail')`, 40000);
    await sleep(200);
    out.failed = await view();
    out.chatNote = await evalIn(inF(`(() => { const n = document.getElementById('goto-note'); return n && !n.hidden ? n.textContent.trim() : ''; })()`));
    await shot('fail-note');
    // (2) a poll's repaint keeps it
    await evalIn(`(() => { IBX.sig = IBX.phSig = IBX.ldSig = ''; ibxPolledPaint(); return 0; })()`);
    await evalIn('Promise.resolve(ibxPoll()).then(() => 0, () => 0)');
    await sleep(400);
    out.afterPoll = await view();
    // (3) Open the conversation, from the other project's screen
    await evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.other)}; renderRows(); return 0; })()`);
    await sleep(300);
    out.awayFirst = await evalIn('SELECTED_PROJECT');
    out.awayView = await view();
    await click(`[...LD_INBOX.querySelectorAll('[data-ibx-fail-room]')].find(b => b.getBoundingClientRect().width > 0)`);
    await sleep(600);
    out.opened = { project: await evalIn('SELECTED_PROJECT'), frame: await evalIn(`(() => { const f = ${FRAME(A.po)}; return !!f && f.getBoundingClientRect().width > 0; })()`) };
    out.afterOpen = await view();
    // (4) Done: the note goes, the row stays, its link has the focus
    await click(`[...LD_INBOX.querySelectorAll('[data-ibx-fail-done]')].find(b => b.getBoundingClientRect().width > 0)`);
    await sleep(300);
    out.done = await view();
    out.done.focus = await evalIn(`(() => { const a = document.activeElement; return !!a && a.matches('a.ibx-link') && !!a.closest('li.ibx-row') && a.closest('li.ibx-row').dataset.ibx === ${JSON.stringify(out.failed.id)}; })()`);
    // (5) fails again, then its real message: lands, the note goes
    await pressGone('m-not-there-2');
    await until(`IBX.failed.size > 0 && !!LD_INBOX.querySelector('.ibx-fail')`, 40000);
    out.again = await view();
    await evalIn('ibxRepaint(), 0');
    await until(`!!(${LINK})`, 10000);
    await click(LINK);
    out.landed = !!(await until(`(() => { const m = ${MEASURE(A.answer)}; return !!m && m.marked; })()`, 20000).catch(() => false));
    await sleep(500);
    out.afterLanded = await view();
    // (6) its conversation could not load it (the chat started afresh, so it
    // holds only its newest messages and must ask for older ones)
    await evalIn(`(() => { const f = ${FRAME(A.po)}; f.contentWindow.location.reload(); return 0; })()`);
    await sleep(300);
    await chatReady();
    await evalIn(inF(`(() => { const f0 = window.fetch; window.fetch = (u, ...r) => String(u).includes('before=') ? Promise.reject(new TypeError('Failed to fetch')) : f0(u, ...r); return 0; })()`));
    await pressGone('m-not-there-3');
    await until(`IBX.failed.size > 0 && !!LD_INBOX.querySelector('.ibx-fail')`, 40000);
    out.unloaded = await view();
    await shot('fail-unloaded');
  } catch (e) { out.err = String(e && e.stack || e).slice(0, 600); }
  finally {
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
class InboxFailNoteInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibxfail-", ignore_cleanup_errors=True)
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
        ids = {}
        for name in ("Atlas", "Brakes"):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", members(home))
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            ids[name] = (proj["id"], po["id"])
        R = ids["Atlas"][1]
        text, _ = points.take(chatroom.get_room(R, public=False), "Make the board columns wider", to="claude", key="k1")
        chatroom.post_message(R, "user", text, to="claude")
        for i in range(90):   # more than a chat holds at first: older pages are fetched
            chatroom.post_message(R, "claude" if i % 3 else "codex", f"Board note {i + 1}.\n\n{hl.LONG}",
                                  to="user" if i % 3 else "claude")
        answer = _mid(chatroom.post_message(R, "claude", "Re P1: the columns are wider now, please try it on the board.\n\n" + hl.LONG, to="user"))
        chatroom.post_message(R, "claude", "After the answer.", to="user")
        points.sync(R, force=True)
        cls.ids = {"proj": ids["Atlas"][0], "po": R, "other": ids["Brakes"][0], "answer": answer}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**hl.chrome_profile.node_args(), **cls.ids, "tmp": cls.tmp.name, "shots": shots,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        script = base / "inbox_fail_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_DUMP"):
            Path(os.environ["ENSEMBLE_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_ran_through(self):
        self.assertNotIn("err", self.got, self.got.get("err"))

    def test_no_note_before_a_press(self):
        b = self.got["before"]
        self.assertTrue(b["id"], b)
        self.assertEqual(b["notes"], 0, b)

    def test_the_pressed_row_says_its_message_was_not_found(self):
        f = self.got["failed"]
        self.assertEqual(f["notes"], 1, "on the pressed row only")
        self.assertTrue(f["inRow"], f)
        self.assertEqual(f["failed"], [f["id"]], f)
        self.assertIn("Could not open this message:", f["text"])
        self.assertIn("not found in its conversation", f["text"])
        self.assertEqual(f["bg"], f["warnBg"], "amber")
        self.assertEqual(f["acts"], [["Open the conversation", True], ["Done", True]], "shown without hover")
        self.assertTrue(f["inside"], f)
        self.assertTrue(self.got["chatNote"], "its chat says so too")

    def test_a_poll_keeps_the_note(self):
        a = self.got["afterPoll"]
        self.assertEqual(a["notes"], 1, a)
        self.assertTrue(a["inRow"], a)

    def test_open_the_conversation_opens_its_chat(self):
        self.assertEqual(self.got["awayFirst"], self.ids["other"])
        self.assertTrue(self.got["awayView"]["inRow"], "the note is there on another project's screen")
        self.assertEqual(self.got["opened"]["project"], self.ids["proj"], self.got["opened"])
        self.assertTrue(self.got["opened"]["frame"], self.got["opened"])
        self.assertTrue(self.got["afterOpen"]["inRow"], "opening the conversation does not take the note away")

    def test_done_takes_the_note_away_not_the_row(self):
        d = self.got["done"]
        self.assertEqual(d["notes"], 0, d)
        self.assertEqual(d["id"], self.got["failed"]["id"], "the row stays")
        self.assertEqual(d["failed"], [], d)
        self.assertTrue(d["focus"], "the focus is on the row's link")

    def test_a_later_open_that_lands_clears_it(self):
        self.assertEqual(self.got["again"]["notes"], 1, self.got["again"])
        self.assertTrue(self.got["landed"])
        a = self.got["afterLanded"]
        self.assertEqual(a["notes"], 0, a)
        self.assertEqual(a["failed"], [], a)

    def test_a_message_that_could_not_load_says_reload(self):
        u = self.got["unloaded"]
        self.assertEqual(u["notes"], 1, u)
        self.assertIn("could not be loaded", u["text"])
        self.assertIn("Reload the page", u["text"])

    def test_nothing_wider_than_its_column_and_no_errors(self):
        for k in ("failed", "unloaded"):
            v = self.got[k]
            with self.subTest(k):
                self.assertLessEqual(v["scrollW"], v["W"], v)
                self.assertLessEqual(v["boxSW"], v["boxW"] + 1, v)
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
