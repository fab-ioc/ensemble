"""The Inbox says when its cards are older questions (#360, P333 option A), in
headless Chrome over CDP against a hub in a thread.

Two projects whose PO asked and the person wrote past every question
(Motors 4, opTen 10), then three new questions (Motors 1, opTen 2) and a
project with only a new one (Plain 1). Checked, at 1440x900 and 390x844:

* nothing waiting, 14 older: the Inbox heading reads 0, "Older questions · 14"
  and "You wrote past them in their chat" come first under it, the older
  cards are quiet (grey bar, sunken ground, regular title); All, opTen and
  Motors show an outlined grey 14, 10, 4; the left strip has no badge with
  the Inbox put away; a selected row's grey count stays outlined;
* a closed older card waiting on its Undo leaves both the heading's N and
  the grey counts (13, opTen 9); Undo brings back 14 and 10;
* 5 waiting and 16 older: heading 5, the filled chips All 5, opTen 2,
  Motors 1, Plain 1, Again 1, the grey 1 of Cleared (its question from before
  Clear all), the older heading after the 5 waiting cards, the strip badge 5;
  Plain alone lists no older heading; Again (a question asked twice) and
  Cleared say their own reason, All names the three;
* the phone's Inbox pane: "Older questions 14" and the why line under it;
* every count on screen agrees, nothing is wider than the screen, the page
  threw nothing.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import inbox  # noqa: E402
import points  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_left_strip_cdp import CDP_JS as _BASE, NODE  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

_HEAD = _BASE.split("async function main()")[0]
# A desk page reports (hover: none) in headless Chrome without these.
_FLAG = "'--disable-gpu', "
assert _FLAG in _HEAD
_HEAD = _HEAD.replace(_FLAG, _FLAG + "'--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4', ")

CDP_JS = _HEAD + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId); await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: w - 5, y: h - 5 }, sessionId);
    };
    const hover = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const s = { 'cd-list-dock': null, 'cd-list-dock-a7': null, 'cd-ld-sel': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-inbox-scope': 'all' };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length >= 2', 30000);
    await until('window.ensBooted === true && !!IBX.data', 30000);
    return { evalIn, until, click, hover, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const HIDE = `document.querySelector('#list-dock .dk-stack:has([data-dk-tab="inbox"]) .dk-head [data-dk-act="hide"]')`;
  const settled = box => `!IBX.data.pending && document.querySelectorAll("${box} .ibx-row").length === ibxRows(IBX.data, IBX.scope, IBX.ldPid).length + ibxOlder(IBX.data, IBX.scope, IBX.ldPid).length`;
  const geo = p => p.evalIn(`(() => {
    const B = document.getElementById('ld-ibx-body'), cards = B ? [...B.querySelector('.ibx-cards') ? B.querySelector('.ibx-cards').children : []] : [];
    const head = B && B.querySelector('.ibx-older-head');
    const old = B && B.querySelector('.ibx-row[data-stage="older"]');
    const css = (e, k) => e ? getComputedStyle(e)[k] : '';
    const chip = b => { const n = b.querySelector('.ldp-n'); return [(b.querySelector('.ldp-name') || {}).textContent, n ? n.textContent : '', !!b.querySelector('.ldp-old'),
      n ? css(n, 'backgroundColor') : '', n ? css(n, 'boxShadow') : '', b.getAttribute('aria-current') === 'true', n ? (n.title || '') : '']; };
    const ph = document.getElementById('ph-inbox-body');
    return {
      title: ((B && B.querySelector('.ibx-title')) || {}).textContent || '',
      older: head ? [head.querySelector('.ibx-older-t').textContent, head.querySelector('.ibx-older-why').textContent] : null,
      headAt: head ? cards.indexOf(head) : -1,
      headTop: head ? css(head, 'borderTopWidth') : '',
      stages: cards.map(e => e.classList.contains('ibx-older-head') ? 'HEAD' : (e.dataset.stage || '')),
      oldCard: old ? { bar: css(old, 'borderLeftColor'), bg: css(old, 'backgroundColor'), w: css(old.querySelector('.ibx-line'), 'fontWeight') } : null,
      newCard: (n => n ? { bar: css(n, 'borderLeftColor'), bg: css(n, 'backgroundColor'), w: css(n.querySelector('.ibx-line'), 'fontWeight') } : null)(B && B.querySelector('.ibx-row[data-stage="answer"]')),
      tokens: (s => ({ strong: s.getPropertyValue('--border-strong').trim(), sunken: s.getPropertyValue('--surface-sunken').trim() }))(getComputedStyle(document.documentElement)),
      chips: [...document.querySelectorAll('#ld-projs .ldp-row')].map(chip),
      badge: ((document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="inbox"] .dk-strip-badge') || {}).textContent || '').trim(),
      phone: ph ? [...ph.querySelectorAll('.pdp-head, .ibx-older-why')].map(e => e.textContent.trim()) : [],
      W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth };
  })()`);
  // A colour as the page resolves it, for comparing a token with a computed style.
  const rgb = (p, v) => p.evalIn(`(() => { const e = document.createElement('i'); e.style.color = ${JSON.stringify(v)}; document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c; })()`);
  try {
    const p = await page(1440, 900, false);
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.ids.Motors)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock', 30000);
    await p.click(`document.querySelector('#ld-projs [data-ldp="all"]')`); await sleep(400);
    await p.until(settled('#ld-ibx-body') + ' && document.querySelectorAll("#ld-ibx-body .ibx-row").length > 2', 20000);
    await sleep(300);
    out.all = await geo(p); await p.shot(A.scene + '-all');
    out.colours = { strong: await rgb(p, 'var(--border-strong)'), sunken: await rgb(p, 'var(--surface-sunken)'), muted: await rgb(p, 'var(--fg-muted)') };
    // A project row selected: its count keeps its own look.
    await p.click(`document.querySelector('#ld-projs [data-ldp-proj="${A.ids.opTen}"]')`); await sleep(400);
    await p.until(settled('#ld-ibx-body'), 20000);
    out.opTen = await geo(p); await p.shot(A.scene + '-opten');
    for (const name of ['Plain', 'Again', 'Cleared']) {
      if (!A.ids[name]) continue;
      await p.click(`document.querySelector('#ld-projs [data-ldp-proj="${A.ids[name]}"]')`); await sleep(400);
      await p.until(settled('#ld-ibx-body'), 20000);
      out[name] = await geo(p); await p.shot(A.scene + '-' + name);
    }
    await p.click(`document.querySelector('#ld-projs [data-ldp="all"]')`); await sleep(400);
    await p.until(settled('#ld-ibx-body'), 20000);
    if (A.scene === 'older') {
      // Close the first older card; while its Undo shows it is not counted, then Undo.
      const row = `document.querySelector('#ld-ibx-body .ibx-row[data-stage="older"]')`;
      await p.hover(row); await sleep(150);
      await p.click(`${row}.querySelector('[data-ibx-close]')`); await sleep(300);
      out.closing = await geo(p); await p.shot(A.scene + '-closing');
      await p.click(`document.querySelector('#ld-ibx-body [data-ibx-undo]')`); await sleep(300);
      out.undone = await geo(p);
    }
    await p.click(HIDE); await sleep(600);
    out.min = await geo(p); await p.shot(A.scene + '-min');
    await p.close();
    const q = await page(390, 844, true);
    await q.evalIn(`(() => { IBX.scope = 'all'; phPane('inbox'); return 0; })()`);
    await q.until('!IBX.data.pending && document.querySelectorAll("#ph-inbox-body .ibx-row").length === ibxRows(IBX.data, "all", "").length + ibxOlder(IBX.data, "all", "").length', 20000);
    await sleep(300);
    out.phone = await geo(q); await q.shot(A.scene + '-phone');
    await q.close();
  } finally { try { ch.kill(); } catch (e) {} }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

MOTORS = ["Which colour for done cards?", "Ship the brake pads change today?", "Keep the old gearbox screen?",
          "Move the service log to the left?"]
OPTEN = ["Which font for the reports?", "Merge the two price lists?", "Send the weekly mail on Mondays?",
         "Show the stock count on the cards?", "Keep the archive for a year?", "Drop the CSV export?",
         "Ask before deleting a supplier?", "Sort suppliers by name?", "Show prices with VAT?",
         "Open the reports in a new tab?"]
NEW = {"Motors": ["Use the new tyre photos?"], "opTen": ["Which day for the stock check?", "Show the discount on invoices?"],
       "Plain": ["Rename the plain board?"], "Again": ["Keep the dark logo?"], "Cleared": ["Archive last year's tasks?"]}


def ask_msg(asks):
    return "A few things.\n\n" + "".join(f"{i + 1}. **Ask:** {a}\n   - Yes\n   - No\n" for i, a in enumerate(asks))


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class OlderQuestionsInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibo-", ignore_cleanup_errors=True)
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
            mock.patch.object(points, "mark_seen", lambda *a, **k: []),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)       # tests/test_inbox.py leaves it bound to its fake hub
        cls.ids, pos = {}, {}

        def project(name, asks, past, twice=False):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                     {"identity": "codex", "agent": "codex", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            for k in range(2 if twice else 1):
                if k:       # asked again after the person wrote: the earlier card is a repeat, not past
                    chatroom.post_message(po["id"], "user", "Busy today.", to="claude")
                for i in range(0, len(asks), 2):
                    chatroom.post_message(po["id"], "claude", ask_msg(asks[i:i + 2]), to="user")
            if past:
                chatroom.post_message(po["id"], "user", "Carry on with the release first.", to="claude")
            cls.ids[name], pos[name] = proj["id"], po["id"]

        project("Motors", MOTORS, True)
        project("opTen", OPTEN, True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        script = base / "inbox_older_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        cls.got = {}
        for scene in ("older", "mixed"):
            if scene == "mixed":
                for name in ("Motors", "opTen"):
                    chatroom.post_message(pos[name], "claude", ask_msg(NEW[name]), to="user")
                project("Plain", NEW["Plain"], False)
                project("Again", NEW["Again"], False, twice=True)     # the earlier card: why "repeat"
                project("Cleared", NEW["Cleared"], False)
                # Clear all, as the Inbox's: the questions before now (why "cleared") and the room's Needs you item.
                points.clear_decisions(pos["Cleared"], now=time.time() + 1)
                dashboard.attention_done_many([pos["Cleared"]])
            args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{server.server_address[1]}",
                    "ids": cls.ids, "shots": shots, "scene": scene}
            out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
            assert out.returncode == 0, out.stderr[-4000:]
            cls.got[scene] = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def chips(g):
        return {c[0]: (c[1], c[2]) for c in g["chips"] if c[1]}

    def test_nothing_waiting_heading_0_and_the_older_heading_right_under_it(self):
        a = self.got["older"]["all"]
        self.assertRegex(a["title"], r"·\s*0$", a["title"])
        self.assertEqual(a["older"], ["Older questions · 14", "You wrote past them in their chat"])
        self.assertEqual(a["headAt"], 0, "first under the Inbox heading")
        self.assertEqual(a["headTop"], "0px", "no hairline above it when it comes first")
        self.assertEqual(a["stages"], ["HEAD"] + ["older"] * 14)

    def test_older_cards_are_quiet(self):
        g = self.got["older"]
        o, col = g["all"]["oldCard"], g["colours"]
        self.assertEqual(o["bar"], col["strong"])
        self.assertEqual(o["bg"], col["sunken"])
        self.assertEqual(o["w"], "500")
        n = self.got["mixed"]["all"]["newCard"]
        self.assertEqual(n["w"], "600")
        self.assertNotEqual(n["bar"], col["strong"])

    def test_grey_counts_for_projects_with_only_older_questions(self):
        g = self.got["older"]
        self.assertEqual(self.chips(g["all"]), {"All": ("14", True), "opTen": ("10", True), "Motors": ("4", True)})
        for c in g["all"]["chips"]:
            if c[2]:
                self.assertEqual(c[3], "rgba(0, 0, 0, 0)", c)
                self.assertIn("inset", c[4], c)
                self.assertRegex(c[6], r"^\d+ older questions?, not counted\. You wrote past them in their chat$", c)
        self.assertEqual(self.chips(g["min"]), self.chips(g["all"]))
        self.assertEqual(g["min"]["badge"], "", "the strip counts only what waits")

    def test_a_selected_rows_grey_count_stays_outlined(self):
        sel = [c for c in self.got["older"]["opTen"]["chips"] if c[5]]
        self.assertEqual([c[0] for c in sel], ["opTen"])
        self.assertTrue(sel[0][2])
        self.assertEqual(sel[0][3], "rgba(0, 0, 0, 0)")
        self.assertIn("inset", sel[0][4])
        o = self.got["older"]["opTen"]
        self.assertRegex(o["title"], r"·\s*0$")
        self.assertEqual(o["older"][0], "Older questions · 10")

    def test_a_closed_older_card_waiting_on_undo_is_not_counted(self):
        g = self.got["older"]
        c, u = g["closing"], g["undone"]
        self.assertEqual(c["older"][0], "Older questions · 13")
        self.assertEqual(self.chips(c), {"All": ("13", True), "opTen": ("9", True), "Motors": ("4", True)})
        self.assertIn("HEAD", c["stages"], "the heading stays above the card waiting on its Undo")
        self.assertEqual(u["older"][0], "Older questions · 14")
        self.assertEqual(self.chips(u), self.chips(g["all"]))

    def test_waiting_and_older_filled_chips_and_the_older_heading_after_the_waiting_cards(self):
        a = self.got["mixed"]["all"]
        self.assertRegex(a["title"], r"·\s*5$", a["title"])
        self.assertEqual(self.chips(a), {"All": ("5", False), "opTen": ("2", False), "Motors": ("1", False), "Plain": ("1", False),
                                         "Again": ("1", False), "Cleared": ("1", True)})
        self.assertEqual(a["older"], ["Older questions · 16",
                                      "Not counted: written past in their chat, asked again later or from before Clear all"])
        self.assertEqual(a["stages"], ["answer"] * 5 + ["HEAD"] + ["older"] * 16)
        self.assertEqual(a["headTop"], "1px", "a hairline parts it from the waiting cards")
        self.assertEqual(self.got["mixed"]["min"]["badge"], "5")

    def test_waiting_only_lists_no_older_heading(self):
        p = self.got["mixed"]["Plain"]
        self.assertRegex(p["title"], r"·\s*1$", p["title"])
        self.assertIsNone(p["older"])
        self.assertEqual(p["stages"], ["answer"])

    def test_a_repeated_or_cleared_question_says_its_own_reason(self):
        g = self.got["mixed"]
        self.assertEqual(g["Again"]["older"], ["Older questions · 1", "Asked again later: only the newest counts"])
        self.assertEqual(g["Again"]["stages"], ["answer", "HEAD", "older"])
        cl = g["Cleared"]
        self.assertRegex(cl["title"], r"·\s*0$")
        self.assertEqual(cl["older"], ["Older questions · 1", "From before Clear all"])
        tip = [c[6] for c in cl["chips"] if c[0] == "Cleared"]
        self.assertEqual(tip, ["1 older question, not counted. From before Clear all"])

    def test_phone_pane_says_why_under_older_questions(self):
        for scene, lead, older in (("older", [], ["Older questions 14", "You wrote past them in their chat"]),
                                   ("mixed", ["Needs your answer 5"], ["Older questions 16",
                                    "Not counted: written past in their chat, asked again later or from before Clear all"])):
            self.assertEqual(self.got[scene]["phone"]["phone"], lead + older, scene)

    def test_nothing_wider_than_the_screen_and_no_errors(self):
        for scene in ("older", "mixed"):
            for k in ("all", "phone"):
                g = self.got[scene][k]
                self.assertLessEqual(g["scrollW"], g["W"], (scene, k))
            self.assertEqual(self.got[scene]["errors"], [], scene)


if __name__ == "__main__":
    unittest.main()
