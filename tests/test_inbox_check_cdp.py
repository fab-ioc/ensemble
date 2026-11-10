"""Checking answers from the Inbox (#325, P291), in headless Chrome over CDP,
against a hub in a thread serving the pages.

Two projects, each a PO chat with answers that ask the person to try
something (one Inbox card, "N answers to check") and one open question.
Checked:

* at 1440x900 (the list dock's Inbox): the card's head line has
  "Done · all 4", shown without hover, above its list of answers; the
  sub-line says "Open one to check it, or Done for all"; opening one answer
  from the card shows "Checked: … Undo" in its place and the card counts 3;
  Undo brings it back and sends nothing; opened again and let run, the hub
  acks that point (its answer stays in the chat) and a reload keeps it off;
  Done shows "Done · Undo", Undo keeps the card, Done let run acks them all
  and the card goes; the question is untouched all along;
* at 390x844 (a phone's Inbox tab): the same with taps, Done at 44 px;
  nothing wider than the screen;
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

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import inbox  # noqa: E402
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_inbox_cdp import CDP_JS as _BASE  # noqa: E402

NODE = shutil.which("node")

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
    const click = async (expr) => {
      const [x, y] = await at(expr);
      if (mobile) {
        for (const type of ['touchStart', 'touchEnd']) await c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] }, sessionId);
        return;
      }
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    // Off every row: nothing hovered.
    const away = async () => { if (!mobile) await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: w - 2, y: h - 2 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const load = async () => {
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.length > 0', 30000);
      await until('window.ensBooted === true', 30000);
    };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
    await load();
    return { evalIn, until, click, away, shot, load, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = (p, proj) => p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const api = po => `(async () => { const d = await (await fetch('/api/inbox', { cache: 'no-store' })).json();
    const rs = d.rows.concat(d.older || []).filter(r => r.roomId === ${JSON.stringify(po)});
    const card = rs.find(r => r.kind === 'check');
    return { card: card ? card.items.map(i => i.points.join(',')) : null, qs: rs.filter(r => Number.isInteger(r.n)).length }; })()`;
  const state = box => `(() => { const el = ${box};
    const card = el.querySelector('li.ibx-row[data-kind="check"]');
    const done = card && card.querySelector('.ibx-hd [data-ibx-done]');
    const sum = card && card.querySelector('.ibx-more > summary');
    return { card: !!card, gone: card && card.classList.contains('ibx-gone') ? card.textContent : '',
      line: card && card.querySelector('.ibx-hd .ibx-line') ? card.querySelector('.ibx-hd .ibx-line').textContent : '',
      reason: card ? [...card.querySelectorAll('.ibx-hd .ibx-reason')].map(s => s.textContent).join(' · ') : '',
      done: done ? { text: done.textContent, shown: getComputedStyle(done).display !== 'none' && done.getBoundingClientRect().height > 0,
        h: Math.round(done.getBoundingClientRect().height), aboveList: sum ? done.getBoundingClientRect().bottom <= sum.getBoundingClientRect().top + 1 : null } : null,
      summary: sum ? sum.textContent : '',
      items: card ? [...card.querySelectorAll('li.ibx-item')].map(i => (i.classList.contains('ibx-gone') ? 'gone:' : '') + i.textContent.slice(0, 60)) : [],
      qs: el.querySelectorAll('li.ibx-row[data-q]').length }; })()`;
  const fits = box => `(() => { const W = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll('${box} *')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > W + 1; }).map(e => e.className || e.tagName);
    return { W, scroll: document.documentElement.scrollWidth, wide: wide.slice(0, 5) }; })()`;
  const fire = key => `(() => { for (const [k, g] of IBX.gone) if (k.startsWith(${JSON.stringify(key)})) { clearTimeout(g.timer); g.fire(); } return 0; })()`;
  // One run of the checks on a page `p` with its Inbox in `B`; names prefixed `pre`.
  const run = async (p, B, po, pre, open) => {
    const cardSel = `${B}.querySelector('li.ibx-row[data-kind="check"]')`;
    await p.until(`!!${cardSel}`, 30000);
    await p.away(); await sleep(200);
    out[pre + 'Before'] = await p.evalIn(state(B));
    await p.shot(pre + '-before');
    await p.click(`${cardSel}.querySelector('.ibx-more > summary')`); await sleep(250);
    await p.away();
    await p.shot(pre + '-open');
    const item = `${cardSel}.querySelector('li.ibx-item:not(.ibx-gone) a.ibx-link')`;
    out[pre + 'Key'] = await p.evalIn(`${item}.dataset.ibxCheck`);
    await p.click(item); await sleep(300);
    await open();
    out[pre + 'Checked'] = await p.evalIn(state(B));
    out[pre + 'ApiPending'] = await p.evalIn(api(po));
    await p.shot(pre + '-checked');
    await p.click(`${B}.querySelector('li.ibx-item.ibx-gone [data-ibx-undo]')`); await sleep(250);
    out[pre + 'Undone'] = await p.evalIn(state(B));
    await p.click(item); await sleep(300);
    await open();
    await p.evalIn(fire(`check:${po}:points#`));
    await p.until(`(async () => ((await ${api(po)}).card || []).length === 3)()`, 15000).catch(() => null);
    out[pre + 'ApiAcked'] = await p.evalIn(api(po));
    await p.until(`!${B}.querySelector('li.ibx-item.ibx-gone')`, 15000).catch(() => null);
    out[pre + 'After'] = await p.evalIn(state(B));
    await p.away(); await sleep(150);
    await p.shot(pre + '-after');
  };
  try {
    // ---- 1440 x 900: the list dock's Inbox
    const p = await page(1440, 900);
    await toPo(p, A.proj);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!IBX.data', 30000);
    const D = 'LD_INBOX';
    await run(p, D, A.po, 'desk', async () => {});
    // The reload keeps the checked one off.
    await p.load();
    await toPo(p, A.proj);
    await p.until(`document.body.classList.contains("po-dock") && !!IBX.data && !!${D}.querySelector('li.ibx-row[data-kind="check"]')`, 30000);
    out.deskReload = await p.evalIn(state(D));
    // Done: Done · Undo, Undo keeps it; again and let it run: the card goes.
    await p.click(`${D}.querySelector('li.ibx-row[data-kind="check"] .ibx-hd [data-ibx-done]')`); await sleep(250);
    out.deskDoneGone = await p.evalIn(state(D));
    await p.shot('desk-done');
    await p.click(`${D}.querySelector('li.ibx-row.ibx-gone [data-ibx-undo]')`); await sleep(250);
    out.deskDoneBack = await p.evalIn(state(D));
    await p.click(`${D}.querySelector('li.ibx-row[data-kind="check"] .ibx-hd [data-ibx-done]')`); await sleep(250);
    await p.evalIn(fire(`check:${A.po}:points`));
    await p.until(`(async () => (await ${api(A.po)}).card === null)()`, 15000).catch(() => null);
    out.deskApiDone = await p.evalIn(api(A.po));
    await p.until(`!${D}.querySelector('li.ibx-row[data-kind="check"]')`, 15000).catch(() => null);
    out.deskDone = await p.evalIn(state(D));
    out.deskFits = await p.evalIn(fits('#list-dock .ibx'));
    await p.close();
    // ---- 390 x 844: a phone's Inbox tab, beside Chat (#311)
    const q = await page(390, 844, true);
    await toPo(q, A.proj2);
    await q.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.narrow() && !!IBX.data', 30000);
    const tab = 'document.querySelector("#po-dock .dk-tab[data-dk-tab=inbox]")';
    await q.click(tab);
    const P = 'PD.els.inbox';
    await q.until(`${P}.getBoundingClientRect().width > 0`, 15000);
    // Opening an answer goes to the chat: come back to the Inbox tab to look.
    await run(q, P, A.po2, 'phone', async () => {
      await sleep(300);
      out.phoneWentToChat = await q.evalIn(`${tab}.getAttribute('aria-selected') !== 'true'`);
      if (out.phoneWentToChat) { await q.click(tab); await sleep(300); }
    });
    out.phoneFits = await q.evalIn(fits('#po-dock .ibx'));
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

ASK = "One more thing.\n\n1. **Ask:** Wider columns?\n   - Yes\n   - No\n"


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class CheckInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibk-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard.Handler, "_resume_room", lambda *a, **k: {"delivered": 0}),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)       # tests/test_inbox.py leaves it bound to its fake hub

        def project(name):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                     {"identity": "codex", "agent": "codex", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            for k in range(1, 5):   # four answers that ask the person to try something
                text, ids = points.take(chatroom.get_room(po["id"], public=False), f"Change number {k} please",
                                        to="claude", key=f"k{k}")
                assert ids == [f"P{k}"], ids
                chatroom.post_message(po["id"], "user", text, to="claude")
                chatroom.post_message(po["id"], "claude", f"Re P{k}: it is live now, please try it.", to="user")
            chatroom.post_message(po["id"], "claude", ASK, to="user")
            points.sync(po["id"], force=True)
            return proj["id"], po["id"]

        cls.proj, cls.po = project("Motors")
        cls.proj2, cls.po2 = project("Wheels")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "po": cls.po, "proj2": cls.proj2, "po2": cls.po2, "shots": shots}
        script = base / "inbox_check_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _before(self, pre, done_h):
        b = self.got[pre + "Before"]
        self.assertTrue(b["card"], b)
        self.assertRegex(b["line"], r"PO: 4 answers to check$")
        self.assertEqual(b["reason"], "4 ask you to try something · Open one to check it, or Done for all")
        self.assertEqual(b["done"]["text"], "Done · all 4")
        self.assertTrue(b["done"]["shown"], "Done shows without hover")
        self.assertTrue(b["done"]["aboveList"], "Done is on the head line, above the answers")
        self.assertGreaterEqual(b["done"]["h"], done_h)
        self.assertEqual(b["qs"], 1, "the question is listed")

    def _check(self, pre):
        c, u, a, f = (self.got[pre + k] for k in ("Checked", "Undone", "ApiAcked", "After"))
        key = self.got[pre + "Key"]
        self.assertEqual([x for x in c["items"] if x.startswith("gone:")], [c["items"][0]], c)
        self.assertTrue(c["items"][0].startswith("gone:Checked:") and c["items"][0].rstrip().endswith("Undo"), c["items"][0])
        self.assertRegex(c["line"], r"PO: 3 answers to check$")
        self.assertEqual(c["done"]["text"], "Done · all 3")
        self.assertEqual(c["reason"], "3 ask you to try something · Open one to check it, or Done for all")
        self.assertEqual(len(self.got[pre + "ApiPending"]["card"]), 4, "nothing is sent while its Undo shows")
        self.assertEqual(len(u["items"]), 4, u)
        self.assertFalse(any(x.startswith("gone:") for x in u["items"]), u)
        self.assertEqual(u["done"]["text"], "Done · all 4")
        self.assertEqual(len(a["card"]), 3, a)
        self.assertNotIn(key.split("#", 1)[1], a["card"], "the hub acked the opened answer's point")
        self.assertEqual(a["qs"], 1, "the question is untouched")
        self.assertEqual(len(f["items"]), 3, f)
        self.assertEqual(f["done"]["text"], "Done · all 3")
        self.assertEqual(f["qs"], 1)

    def test_desktop_done_on_the_head_line_without_hover(self):
        self._before("desk", 20)

    def test_desktop_opening_an_answer_checks_it(self):
        self._check("desk")

    def test_desktop_reload_keeps_it_checked(self):
        r = self.got["deskReload"]
        self.assertEqual(len(r["items"]), 3, r)
        self.assertRegex(r["line"], r"PO: 3 answers to check$")
        self.assertEqual(r["done"]["text"], "Done · all 3")

    def test_desktop_done_acks_them_all_with_undo(self):
        g, b, d, a = (self.got[k] for k in ("deskDoneGone", "deskDoneBack", "deskDone", "deskApiDone"))
        self.assertTrue(g["gone"].startswith("Done:") and "Undo" in g["gone"], g)
        self.assertEqual(len(b["items"]), 3, b)
        self.assertFalse(d["card"], d)
        self.assertIsNone(a["card"], "the hub acked every one")
        self.assertEqual(a["qs"], 1, "the question is untouched")
        self.assertEqual(d["qs"], 1)

    def test_phone_done_and_opening_an_answer(self):
        self._before("phone", 44)
        self._check("phone")

    def test_nothing_is_wider_than_the_screen(self):
        for k in ("deskFits", "phoneFits"):
            f = self.got[k]
            self.assertLessEqual(f["scroll"], f["W"], f)
            self.assertEqual(f["wide"], [], f)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
