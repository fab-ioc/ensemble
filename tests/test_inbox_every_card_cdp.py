"""Every open question card is a row in Needs your answer (#394, P396): no
Older questions group, in headless Chrome over CDP against a hub in a thread.

Two projects whose PO asked and the person wrote past every question
(Motors 4, opTen 10), then three new questions (Motors 1, opTen 2), a
project with only a new one (Plain 1), one asked the same question twice
(Again: both cards) and one whose question came before Clear all (Cleared:
none). Checked, at 1440x900 and 390x844:

* written past, 14 open: the Inbox heading reads 14, 14 answer cards with the
  amber bar, no Older heading anywhere; All, opTen and Motors show filled
  14, 10, 4; the left strip's badge with the Inbox put away is 14;
* a closed card waiting on its Undo leaves the counts (13, opTen 9); Undo
  brings back 14 and 10;
* mixed: heading 20, chips All 20, opTen 12, Motors 5, Plain 1, Again 2, no
  chip for Cleared (its Inbox empty);
* the phone's Inbox pane: only "Needs your answer N";
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
  const settled = box => `!IBX.data.pending && document.querySelectorAll("${box} .ibx-row").length === ibxRows(IBX.data, IBX.scope, IBX.ldPid).length`;
  const geo = p => p.evalIn(`(() => {
    const B = document.getElementById('ld-ibx-body'), cards = B ? [...B.querySelector('.ibx-cards') ? B.querySelector('.ibx-cards').children : []] : [];
    const css = (e, k) => e ? getComputedStyle(e)[k] : '';
    const chip = b => { const n = b.querySelector('.ldp-n'); return [(b.querySelector('.ldp-name') || {}).textContent, n ? n.textContent : '', !!b.querySelector('.ldp-n:not([class="ldp-n"])'),
      n ? css(n, 'backgroundColor') : '', n ? css(n, 'boxShadow') : '', b.getAttribute('aria-current') === 'true', n ? (n.title || '') : '', n ? css(n, 'fontWeight') : '']; };
    const ph = document.getElementById('ph-inbox-body');
    return {
      title: ((B && B.querySelector('.ibx-title')) || {}).textContent || '',
      olderAnywhere: document.querySelectorAll('[class*="older"], [data-stage="older"]').length,
      stages: cards.map(e => e.dataset.stage || e.className),
      newCard: (n => n ? { bar: css(n, 'borderLeftColor'), bg: css(n, 'backgroundColor'), w: css(n.querySelector('.ibx-line'), 'fontWeight') } : null)(B && B.querySelector('.ibx-row[data-stage="answer"]')),
      chips: [...document.querySelectorAll('#ld-projs .ldp-row')].map(chip),
      badge: ((document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="inbox"] .dk-strip-badge') || {}).textContent || '').trim(),
      phone: ph ? [...ph.querySelectorAll('.pdp-head, [class*="older"]')].map(e => e.textContent.trim()) : [],
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
    out.colours = { warn: await rgb(p, 'var(--c-warning-bold)') };
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
    if (A.scene === 'past') {
      // Close the first card; while its Undo shows it is not counted, then Undo.
      const row = `document.querySelector('#ld-ibx-body .ibx-row[data-stage="answer"]')`;
      await p.hover(row); await sleep(150);
      // Its actions stay inside the card in this narrow column (#394: three of them wrap).
      out.acts = await p.evalIn(`(() => { const r = ${row}, c = r.getBoundingClientRect();
        return [...r.querySelectorAll('.ibx-acts .ibx-btn')].map(b => { const x = b.getBoundingClientRect(); return x.left >= c.left && x.right <= c.right && x.top >= c.top && x.bottom <= c.bottom; }); })()`);
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
    await q.until('!IBX.data.pending && document.querySelectorAll("#ph-inbox-body .ibx-row").length === ibxRows(IBX.data, "all", "").length', 20000);
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
class EveryOpenCardIsARowInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibe-", ignore_cleanup_errors=True)
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
                if k:       # asked again after the person wrote: both cards stay open (P396)
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
        script = base / "inbox_every_card_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        cls.got = {}
        for scene in ("past", "mixed"):
            if scene == "mixed":
                for name in ("Motors", "opTen"):
                    chatroom.post_message(pos[name], "claude", ask_msg(NEW[name]), to="user")
                project("Plain", NEW["Plain"], False)
                project("Again", NEW["Again"], False, twice=True)     # the earlier card: why "repeat"
                project("Cleared", NEW["Cleared"], False)
                # Clear all on the room's Needs you Decisions (#279) closes the questions before now; the Needs you item is dismissed too.
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

    def test_written_past_cards_are_rows_with_no_older_heading(self):
        a = self.got["past"]["all"]
        self.assertRegex(a["title"], r"·\s*14$", a["title"])
        self.assertEqual(a["stages"], ["answer"] * 14)
        self.assertEqual(a["olderAnywhere"], 0)
        self.assertEqual(a["newCard"]["bar"], self.got["past"]["colours"]["warn"])
        self.assertEqual(a["newCard"]["w"], "600")

    def test_project_chips_count_them_filled(self):
        g = self.got["past"]
        self.assertEqual(self.chips(g["all"]), {"All": ("14", False), "opTen": ("10", False), "Motors": ("4", False)})
        self.assertEqual(self.chips(g["min"]), self.chips(g["all"]))
        self.assertEqual(g["min"]["badge"], "14")
        o = g["opTen"]
        self.assertRegex(o["title"], r"·\s*10$")
        self.assertEqual(o["stages"], ["answer"] * 10)

    def test_a_closed_card_waiting_on_undo_is_not_counted(self):
        g = self.got["past"]
        self.assertEqual(g["acts"], [True] * 3, "Close, Close this and earlier and Done with this, all inside the card")
        c, u = g["closing"], g["undone"]
        self.assertEqual(self.chips(c), {"All": ("13", False), "opTen": ("9", False), "Motors": ("4", False)})
        self.assertEqual(self.chips(u), self.chips(g["all"]))

    def test_mixed_asked_again_counts_both_and_cleared_none(self):
        a = self.got["mixed"]["all"]
        self.assertRegex(a["title"], r"·\s*20$", a["title"])
        self.assertEqual(self.chips(a), {"All": ("20", False), "opTen": ("12", False), "Motors": ("5", False),
                                         "Plain": ("1", False), "Again": ("2", False)})
        self.assertEqual(a["stages"], ["answer"] * 20)
        self.assertEqual(self.got["mixed"]["Again"]["stages"], ["answer"] * 2)
        cl = self.got["mixed"]["Cleared"]
        self.assertRegex(cl["title"], r"·\s*0$")
        self.assertEqual(cl["stages"], [])
        self.assertEqual(self.got["mixed"]["min"]["badge"], "20")

    def test_phone_pane_lists_only_needs_your_answer(self):
        for scene, n in (("past", 14), ("mixed", 20)):
            self.assertEqual(self.got[scene]["phone"]["phone"], [f"Needs your answer {n}"], scene)

    def test_nothing_wider_than_the_screen_and_no_errors(self):
        for scene in ("past", "mixed"):
            for k in ("all", "phone"):
                g = self.got[scene][k]
                self.assertLessEqual(g["scrollW"], g["W"], (scene, k))
            self.assertEqual(self.got[scene]["errors"], [], scene)


if __name__ == "__main__":
    unittest.main()
