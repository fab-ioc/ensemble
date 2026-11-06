"""Closing questions from the Inbox (#310), in headless Chrome over CDP,
against a hub in a thread serving the pages (the endpoints are
tests/test_ask_close_many.py).

A PO chat with eight open questions: two still waiting (Needs your answer)
and six the person wrote past (Older questions). Checked:

* at 1440x900: Needs your answer, then Older questions, every question with
  Close and the last without "Close this and earlier"; pointing at the third
  question's "Close this and earlier N" marks it and every question below,
  N of them; a click asks in the row ("Close this and the N-1 earlier
  questions?"), Escape cancels; Close N takes them off the list, the hub's
  list too, and the Undo bar counts its seconds; the PO chat's Ask card of a
  closed question reads Closed; Undo brings every one back;
* a row's Close shows "Closed · Undo" in its place: Undo keeps it; let run,
  the hub closes it;
* at 390x844 (a phone's Inbox pane) the actions are on every row at 44 px; a
  tap on "Close this and earlier" marks the rows and asks in a bar along the
  bottom; Close N, then Undo; nothing wider than the screen;
* the page threw nothing meanwhile.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import inbox  # noqa: E402
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_inbox_cdp import CDP_JS as _BASE  # noqa: E402

NODE = shutil.which("node")

# The launcher and Cdp class of test_inbox_cdp, with this test's main().
CDP_JS = _BASE[: _BASE.index("async function main()")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const at = async (expr) => evalIn(`(() => { const e = (${expr}); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
    const hover = async (expr) => { const [x, y] = await at(expr); await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId); };
    const click = async (expr) => {
      const [x, y] = await at(expr);
      if (mobile) {
        for (const type of ['touchStart', 'touchEnd']) await c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] }, sessionId);
        return;
      }
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, hover, click, key, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = p => p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const api = `(async () => { const d = await (await fetch('/api/inbox', { cache: 'no-store' })).json(); return d.rows.concat(d.older || []).filter(r => Number.isInteger(r.n)).map(r => r.id); })()`;
  const fits = box => `(() => { const W = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll('${box} *')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > W + 1; }).map(e => e.className || e.tagName);
    return { W, scroll: document.documentElement.scrollWidth, wide: wide.slice(0, 5) }; })()`;
  const state = box => `(() => { const el = ${box};
    const qs = [...el.querySelectorAll('li.ibx-row[data-q]')];
    return { qs: qs.map(r => r.dataset.ibx), marked: [...el.querySelectorAll('li.ibx-row.ibx-mark')].map(r => r.dataset.ibx),
      heads: [...el.querySelectorAll('.pdp-head')].map(h => h.textContent.trim()),
      earlier: qs.map(r => { const b = r.querySelector('[data-ibx-earlier] .ibx-cn'); return b ? +b.textContent : 0; }),
      close: qs.map(r => !!r.querySelector('[data-ibx-close]')),
      meta: qs.map(r => r.querySelector('.ibx-meta').textContent),
      conf: (() => { const f = el.querySelector('.ibx-conf'); if (!f) return null; const b = f.getBoundingClientRect();
        return { q: f.querySelector('.ibx-conf-q').textContent, p: f.querySelector('.ibx-conf-p').textContent, go: f.querySelector('[data-ibx-go]').textContent,
          inRow: !!f.closest('li.ibx-row'), sheet: f.classList.contains('ibx-sheet'), top: Math.round(b.top), bottom: Math.round(b.bottom),
          btnH: [...f.querySelectorAll('.ibx-btn')].map(x => Math.round(x.getBoundingClientRect().height)) }; })(),
      bar: (() => { const u = el.querySelector('.ibx-undo'); if (!u) return null; const b = u.getBoundingClientRect(); return { text: u.textContent, h: Math.round(b.height), bottom: Math.round(b.bottom),
        btnH: Math.round(u.querySelector('[data-ibx-bulk-undo]').getBoundingClientRect().height) }; })(),
      acts: qs.map(r => { const a = r.querySelector('.ibx-acts'); return a ? getComputedStyle(a).display : ''; }),
      actH: [...el.querySelectorAll('.ibx-acts .ibx-btn')].map(b => Math.round(b.getBoundingClientRect().height)).filter(Boolean),
      gone: [...el.querySelectorAll('li.ibx-gone')].map(r => r.textContent) }; })()`;
  try {
    // ---- 1440 x 900: the Inbox column
    const p = await page(1440, 900);
    await toPo(p);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!IBX.data && PD.els.inbox.querySelectorAll("li.ibx-row[data-q]").length >= 8', 30000);
    const D = 'PD.els.inbox';
    out.before = await p.evalIn(state(D));
    out.apiBefore = await p.evalIn(api);
    out.restHidden = out.before.acts;
    const k = 2, target = out.before.qs[k];
    const sel = `${D}.querySelector('li.ibx-row[data-ibx="${target}"]')`;
    await p.hover(sel);
    await p.hover(`${sel}.querySelector('[data-ibx-earlier]')`); await sleep(150);
    out.hovered = await p.evalIn(state(D));
    await p.shot('close-1440-hover');
    await p.click(`${sel}.querySelector('[data-ibx-earlier]')`); await sleep(200);
    out.confirm = await p.evalIn(state(D));
    await p.shot('close-1440-confirm');
    await p.key('Escape'); await sleep(200);
    out.escaped = await p.evalIn(state(D));
    await p.hover(sel);
    await p.click(`${sel}.querySelector('[data-ibx-earlier]')`); await sleep(200);
    await p.click(`${D}.querySelector('[data-ibx-go]')`);
    await p.until(`!!${D}.querySelector('.ibx-undo')`, 15000);
    await sleep(300);
    out.closed = await p.evalIn(state(D));
    out.apiClosed = await p.evalIn(api);
    await p.shot('close-1440-undo');
    // The PO chat's card of a closed question.
    out.cardKey = await p.evalIn(`(() => { const r = ibxFind(${JSON.stringify(target)}); return r ? r.mid + ':' + r.n : ''; })()`);
    out.card = await p.until(`(() => {
      const f = pdChatFrame(); const d = f && f.contentDocument; if (!d) return '';
      const q = d.querySelector('.qa.done[data-ask="' + CSS.escape(${JSON.stringify(out.cardKey)}) + '"]');
      return q && /Closed/.test(q.textContent) ? q.textContent.trim() : ''; })()`, 20000).catch(e => 'none: ' + e.message);
    await p.click(`${D}.querySelector('[data-ibx-bulk-undo]')`);
    await p.until(`!${D}.querySelector('.ibx-undo') && ${D}.querySelectorAll('li.ibx-row[data-q]').length === ${out.before.qs.length}`, 20000).catch(() => null);
    out.undone = await p.evalIn(state(D));
    out.apiUndone = await p.evalIn(api);
    // One row's Close: Closed · Undo, Undo keeps it; again and let it run.
    const first = out.before.qs[0], fs1 = `${D}.querySelector('li.ibx-row[data-ibx="${first}"]')`;
    await p.hover(fs1);
    await p.click(`${fs1}.querySelector('[data-ibx-close]')`); await sleep(150);
    out.rowGone = await p.evalIn(state(D));
    await p.click(`${D}.querySelector('[data-ibx-undo]')`); await sleep(150);
    out.rowBack = await p.evalIn(state(D));
    await p.hover(fs1);
    await p.click(`${fs1}.querySelector('[data-ibx-close]')`); await sleep(150);
    await p.evalIn(`(() => { const g = IBX.gone.get(${JSON.stringify(first)}); g.fire(); return 0; })()`);
    await p.until(`(async () => !(await ${api}).includes(${JSON.stringify(first)}))()`, 15000).catch(() => null);
    out.apiRowClosed = await p.evalIn(api);
    out.rowClosed = await p.evalIn(state(D));
    out.deskFits = await p.evalIn(fits('#po-dock .ibx'));
    await p.close();
    // ---- 390 x 844: a phone's Inbox pane
    const q = await page(390, 844, true);
    await toPo(q);
    await q.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.narrow() && !!IBX.data', 30000);
    await q.click('document.getElementById("inbox-btn")');
    const P = 'document.getElementById("ph-inbox-body")';
    await q.until(`${P}.querySelectorAll('li.ibx-row[data-q]').length >= 7 && ${P}.getBoundingClientRect().width > 0`, 15000);
    out.phBefore = await q.evalIn(state(P));
    const pt = out.phBefore.qs[1], psel = `${P}.querySelector('li.ibx-row[data-ibx="${pt}"]')`;
    await q.click(`${psel}.querySelector('[data-ibx-earlier]')`); await sleep(250);
    out.phConfirm = await q.evalIn(state(P));
    out.phFits = await q.evalIn(fits('#ph-inbox-body'));
    await q.shot('close-390-confirm');
    await q.click(`${P}.querySelector('[data-ibx-go]')`);
    await q.until(`!!${P}.querySelector('.ibx-undo')`, 15000);
    await sleep(300);
    out.phClosed = await q.evalIn(state(P));
    await q.shot('close-390-undo');
    await q.click(`${P}.querySelector('[data-ibx-bulk-undo]')`);
    await q.until(`!${P}.querySelector('.ibx-undo') && ${P}.querySelectorAll('li.ibx-row[data-q]').length === ${out.phBefore.qs.length}`, 20000).catch(() => null);
    out.phUndone = await q.evalIn(state(P));
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

WAITING_1 = "Two things for the board.\n\n1. **Ask:** Wider columns?\n   - Yes\n   - No\n2. **Ask:** Which colour for done?\n   - Green\n   - Grey\n"


def _older(i: int) -> str:
    return (f"Round {i}.\n\n1. **Ask:** Old question {i}a?\n   - Yes\n   - No\n"
            f"2. **Ask:** Old question {i}b?\n   - Yes\n   - No\n")


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class CloseInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibc-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
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
            # A row's Close tells a running asker; nobody runs here, and nothing may start.
            mock.patch.object(dashboard.Handler, "_resume_room", lambda *a, **k: {"delivered": 0}),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)       # tests/test_inbox.py leaves it bound to its fake hub
        ok, proj, _ =dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                {"identity": "codex", "agent": "codex", "cwd": str(home)}])
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        for i in range(3):          # six questions the person wrote past
            chatroom.post_message(po["id"], "claude", _older(i), to="user")
        chatroom.post_message(po["id"], "user", "Carry on with the release first.", to="claude")
        chatroom.post_message(po["id"], "claude", WAITING_1, to="user")
        cls.proj, cls.po = proj["id"], po["id"]
        cls.cards = len(asks.open_cards(chatroom.get_room(po["id"])))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "po": cls.po, "shots": shots}
        script = base / "inbox_close_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_questions_newest_first_then_older_each_with_close(self):
        b = self.got["before"]
        self.assertEqual(self.cards, 8)
        self.assertEqual(b["heads"], ["Needs your answer 2", "Older questions 6"], b["heads"])
        self.assertEqual(len(b["qs"]), 8)
        self.assertEqual(b["qs"], self.got["apiBefore"], "the hub's questions, in its order")
        self.assertTrue(all(b["close"]), b)
        self.assertEqual(b["earlier"], [8, 7, 6, 5, 4, 3, 2, 0], "N counts itself and every one below; the last has none")
        self.assertTrue(all(m.startswith("PO") for m in b["meta"]), b["meta"])
        self.assertTrue(all(a == "none" for a in self.got["restHidden"]), "on a computer the actions wait for hover")

    def test_hover_marks_this_and_every_question_below(self):
        b, h = self.got["before"], self.got["hovered"]
        self.assertEqual(h["marked"], b["qs"][2:])
        self.assertEqual(h["earlier"][2], 6)

    def test_confirm_in_the_row_and_escape_cancels(self):
        f = self.got["confirm"]["conf"]
        self.assertIsNotNone(f)
        self.assertTrue(f["inRow"] and not f["sheet"], f)
        self.assertEqual(f["q"], "Close this and the 5 earlier questions?")
        self.assertIn("Nobody is answered and the PO is not woken. Undo for 30 s.", f["p"])
        self.assertEqual(f["go"], "Close 6")
        self.assertEqual(self.got["confirm"]["marked"], self.got["before"]["qs"][2:])
        self.assertIsNone(self.got["escaped"]["conf"])

    def test_close_takes_them_off_and_undo_brings_them_back(self):
        b, c = self.got["before"], self.got["closed"]
        self.assertEqual(c["qs"], b["qs"][:2])
        self.assertEqual(self.got["apiClosed"], b["qs"][:2], "the hub closed them")
        self.assertRegex(c["bar"]["text"], r"^Closed 6 questions\s*\d+ s\s*Undo$")
        self.assertIn("Closed", self.got["card"], "the chat's Ask card reads Closed")
        self.assertEqual(self.got["undone"]["qs"], b["qs"])
        self.assertEqual(self.got["apiUndone"], b["qs"])
        self.assertIsNone(self.got["undone"]["bar"])

    def test_a_rows_close_has_undo_then_closes(self):
        b = self.got["before"]
        self.assertTrue(any(g.startswith("Closed:") and "Undo" in g for g in self.got["rowGone"]["gone"]), self.got["rowGone"])
        self.assertEqual(self.got["rowBack"]["qs"], b["qs"])
        self.assertEqual(self.got["rowBack"]["gone"], [])
        self.assertNotIn(b["qs"][0], self.got["apiRowClosed"])
        self.assertEqual(self.got["rowClosed"]["qs"], b["qs"][1:])

    def test_phone_bottom_bar_and_44px(self):
        pb, pc = self.got["phBefore"], self.got["phConfirm"]
        self.assertTrue(all(a == "flex" for a in pb["acts"]), "always shown on touch")
        self.assertTrue(pb["actH"] and min(pb["actH"]) >= 44, pb["actH"])
        f = pc["conf"]
        self.assertTrue(f and f["sheet"] and not f["inRow"], f)
        self.assertEqual(f["bottom"], 844)
        self.assertTrue(min(f["btnH"]) >= 44, f)
        n = len(pb["qs"]) - 1
        self.assertEqual(f["q"], f"Close this and the {n - 1} earlier questions?")
        self.assertEqual(pc["marked"], pb["qs"][1:])
        self.assertEqual(self.got["phClosed"]["qs"], pb["qs"][:1])
        self.assertGreaterEqual(self.got["phClosed"]["bar"]["btnH"], 44)
        self.assertEqual(self.got["phUndone"]["qs"], pb["qs"])

    def test_nothing_is_wider_than_the_screen(self):
        for k in ("deskFits", "phFits"):
            f = self.got[k]
            self.assertLessEqual(f["scroll"], f["W"], f)
            self.assertEqual(f["wide"], [], f)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
