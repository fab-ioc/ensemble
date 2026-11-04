"""The inbox on the page (#302, Layout A step 1), in headless Chrome over CDP,
against a hub in a thread serving the pages (inbox.py is tests/test_inbox.py).

Two projects, each with a PO whose chat holds a point answered and not seen
yet (a "check" row of the inbox). Checked:

* at 1728x1117 the Inbox is a column left of the PO's conversation, about
  280px wide, its rows the hub's /api/inbox rows for this project, each with
  its group heading, task (PO) and age; the bar's "Inbox N" counts every
  project; "All" lists the other project's row with its project's name and
  "This project" filters it out again;
* a real click on a row opens its message: the PO chat lands on that balloon;
* the bar's Inbox from another screen (the task list, no project chosen)
  opens a PO screen that has rows, with the Inbox column shown;
* on a phone (390x844, and 360 and 430) Inbox is a tab of the one column, its
  rows on screen, the bar's Inbox button gone, and nothing is wider than the
  screen;
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
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
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
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = p => p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const ready = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!IBX.data && !!PD.els.inbox.querySelector(".ibx-row")', 30000);
  const R = 'const R = el => { const b = el.getBoundingClientRect(); return { x: Math.round(b.left), r: Math.round(b.right), w: Math.round(b.width), h: Math.round(b.height) }; };';
  const fits = `(() => { const W = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll('#po-dock .ibx, #po-dock .ibx *')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > W + 1; }).map(e => e.className || e.tagName);
    return { W, scroll: document.documentElement.scrollWidth, body: document.body.scrollWidth, wide: wide.slice(0, 5) }; })()`;
  try {
    // ---- 1728 x 1117: the column beside the conversation
    const p = await page(1728, 1117);
    await toPo(p); await ready(p); await sleep(400);
    out.desk = await p.evalIn(`(async () => { ${R}
      const api = await (await fetch('/api/inbox', { cache: 'no-store' })).json();
      const rows = [...PD.els.inbox.querySelectorAll('.ibx-row')];
      return { col: R(PD.els.inbox), chat: R(PD.els['po-chat']), shown: PD.dock.isVisible('inbox') && PD.dock.frontOf('inbox') === 'inbox',
        auto: PD.dock.isAuto('inbox'),
        rows: rows.map(r => r.dataset.ibx), apiRows: api.rows.filter(r => r.projectId === ${JSON.stringify(A.proj)}).map(r => r.id),
        apiAll: api.rows.length, counts: api.counts || null,
        heads: [...PD.els.inbox.querySelectorAll('.pdp-head')].map(h => h.textContent.trim()),
        where: rows.map(r => r.querySelector('.ibx-where').textContent), ages: rows.map(r => r.querySelector('.pdp-age').textContent),
        lines: rows.map(r => r.querySelector('.ibx-line').textContent),
        scope: [...PD.els.inbox.querySelectorAll('[data-ibx-scope]')].map(b => b.dataset.ibxScope + ':' + b.getAttribute('aria-pressed')),
        btn: document.getElementById('inbox-btn').textContent.trim(), btnShown: document.getElementById('inbox-btn').getBoundingClientRect().width > 0,
        tool: toolIcon('inbox').includes('ti-inbox') && getComputedStyle(document.body).getPropertyValue('--tool-inbox').trim() !== '' };
    })()`);
    out.deskFits = await p.evalIn(fits);
    await p.shot('inbox-1728x1117');
    // All projects, then this project again.
    await p.click('PD.els.inbox.querySelector(\'[data-ibx-scope="all"]\')'); await sleep(200);
    out.all = await p.evalIn(`({ rows: [...PD.els.inbox.querySelectorAll('.ibx-row')].map(r => r.dataset.ibx),
      where: [...PD.els.inbox.querySelectorAll('.ibx-where')].map(e => e.textContent), saved: localStorage.getItem('cd-inbox-scope') })`);
    await p.click('PD.els.inbox.querySelector(\'[data-ibx-scope="project"]\')'); await sleep(200);
    out.back = await p.evalIn(`[...PD.els.inbox.querySelectorAll('.ibx-row')].map(r => r.dataset.ibx)`);
    // A row opens its message: the chat lands on that balloon.
    await p.until('(() => { const f = pdChatFrame(); return !!(f && f.contentWindow && f.contentWindow.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN")); })()', 30000);
    const mid = await p.evalIn('PD.els.inbox.querySelector(".ibx-row a.ibx-link[data-mid]").dataset.mid');
    await p.evalIn('(() => { const b = pdChatFrame().contentDocument.getElementById("msgs"); b.scrollTop = 0; })(); 0');
    await p.click('PD.els.inbox.querySelector(".ibx-row a.ibx-link[data-mid]")');
    await p.until(`(() => { const d = pdChatFrame().contentDocument, l = d.querySelector('.landed'); const m = l && (l.closest('.msg[data-mid]') || l); return !!m && m.dataset.mid === ${JSON.stringify(mid)}; })()`, 10000).catch(() => null);
    out.open = await p.evalIn(`(() => { const d = pdChatFrame().contentDocument, box = d.getElementById('msgs'), l = d.querySelector('.landed');
      const m = l && (l.closest('.msg[data-mid]') || l), el = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
      const er = el && el.getBoundingClientRect(), br = box.getBoundingClientRect();
      return { mid: ${JSON.stringify(mid)}, landed: m ? m.dataset.mid : '', inView: !!el && er.bottom > br.top && er.top < br.bottom, here: location.pathname }; })()`);
    // The bar's Inbox from the task list: the PO screen with its Inbox.
    await p.evalIn(`(() => { midLeave(); VIEW_MODE = 'board'; SELECTED_PROJECT = ''; renderRows(); return 0; })()`);
    await p.until('!document.body.classList.contains("po-dock")', 10000).catch(() => null);
    out.offPo = await p.evalIn('document.body.classList.contains("po-dock")');
    await p.click('document.getElementById("inbox-btn")');
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.isVisible("inbox")', 15000).catch(() => null);
    out.fromBar = await p.evalIn('({ po: document.body.classList.contains("po-dock"), inbox: !!PD.dock && PD.dock.isVisible("inbox"), pid: PD.pid })');
    await p.close();
    // ---- phones: a tab of the one column
    out.phone = {};
    for (const [w, h] of [[390, 844], [360, 780], [430, 932]]) {
      const q = await page(w, h, true);
      await toPo(q);
      await q.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.narrow() && !!IBX.data', 30000);
      await q.click('document.querySelector(\'#po-dock .dk-tab[data-dk-tab="inbox"]\')'); await sleep(400);
      await q.until('!!PD.els.inbox.querySelector(".ibx-row") && PD.els.inbox.getBoundingClientRect().width > 0', 10000).catch(() => null);
      out.phone[w] = await q.evalIn(`(() => { ${R} const f = ${fits};
        return { ...f, narrow: PD.dock.narrow(), front: PD.dock.frontOf('inbox'), col: R(PD.els.inbox),
          rows: [...PD.els.inbox.querySelectorAll('.ibx-row')].map(r => R(r)),
          tabs: [...document.querySelectorAll('#po-dock .dk-tab')].map(t => t.dataset.dkTab),
          btn: document.getElementById('inbox-btn').getBoundingClientRect().width }; })()`);
      await q.shot(`inbox-phone-${w}x${h}`);
      await q.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class InboxInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ibx-", ignore_cleanup_errors=True)
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
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]

        def project(name, ask, answer):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", members(home))
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            text, ids = points.take(chatroom.get_room(po["id"], public=False), ask, to="claude", key="k1")
            assert ids == ["P1"], ids
            chatroom.post_message(po["id"], "user", text, to="claude")
            m = chatroom.post_message(po["id"], "claude", f"Re P1: {answer}", to="user")
            for i in range(25):   # enough talk that the answer is far up
                chatroom.post_message(po["id"], "codex", f"note {i}\n\n" + "words " * 60, to="claude")
            points.sync(po["id"], force=True)
            return proj["id"], po["id"], ((m or {}).get("message") or {}).get("id", "")

        cls.proj, cls.po, cls.mid = project("Motors", "Please make the board wider", "it is wider now.")
        cls.other, cls.other_po, _ = project("Wheels", "Rounder wheels", "they are round.")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "po": cls.po, "shots": shots}
        script = base / "inbox_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_column_is_left_of_the_conversation_with_the_hubs_rows(self):
        d = self.got["desk"]
        self.assertTrue(d["shown"] and not d["auto"], d)
        self.assertLessEqual(d["col"]["r"], d["chat"]["x"] + 1, "left of the conversation, covering nothing")
        self.assertTrue(240 <= d["col"]["w"] <= 320, d["col"])
        self.assertGreaterEqual(d["chat"]["w"], 360)
        self.assertTrue(d["rows"], d)
        self.assertEqual(d["rows"], d["apiRows"], "the hub's rows for this project, in its order")
        self.assertEqual(d["heads"], [f"Ready to check {len(d['rows'])}"], d["heads"])
        self.assertTrue(all(w == "PO" for w in d["where"]), d["where"])
        self.assertTrue(all(a for a in d["ages"]), d["ages"])
        self.assertTrue(any("wider" in x or "board" in x for x in d["lines"]), d["lines"])
        self.assertEqual(d["scope"], ["project:true", "all:false"])
        self.assertTrue(d["tool"], "the Inbox's icon in its colour")

    def test_the_bar_counts_every_project(self):
        d = self.got["desk"]
        self.assertTrue(d["btnShown"])
        self.assertGreater(d["apiAll"], len(d["rows"]), "the other project's rows too")
        self.assertEqual(d["btn"], f"Inbox {d['apiAll']}")

    def test_all_lists_the_other_project_with_its_name_and_this_project_filters_it(self):
        a, d = self.got["all"], self.got["desk"]
        self.assertEqual(len(a["rows"]), d["apiAll"])
        self.assertTrue(any(w.startswith("Wheels · PO") for w in a["where"]), a["where"])
        self.assertTrue(any(w.startswith("Motors · PO") for w in a["where"]), a["where"])
        self.assertEqual(a["saved"], "all", "remembered")
        self.assertEqual(self.got["back"], d["rows"])

    def test_a_row_opens_its_message(self):
        o = self.got["open"]
        self.assertEqual(o["mid"], self.mid, "the row names the answer's message")
        self.assertEqual(o["landed"], o["mid"], o)
        self.assertTrue(o["inView"], o)
        self.assertEqual(o["here"], "/", "in the page, not a new tab")

    def test_the_bar_button_opens_the_po_screen_with_the_inbox(self):
        self.assertFalse(self.got["offPo"], "left the PO screen first")
        f = self.got["fromBar"]
        self.assertEqual((f["po"], f["inbox"]), (True, True), f)
        self.assertIn(f["pid"], (self.proj, self.other), "no project selected: the first one with a row")

    def test_nothing_is_wider_than_the_screen(self):
        f = self.got["deskFits"]
        self.assertLessEqual(f["scroll"], f["W"], f)
        self.assertEqual(f["wide"], [], f)
        for w, p in self.got["phone"].items():
            with self.subTest(w=w):
                self.assertLessEqual(p["scroll"], p["W"], p)
                self.assertLessEqual(p["body"], p["W"], p)
                self.assertEqual(p["wide"], [], p)

    def test_on_a_phone_the_inbox_is_a_tab(self):
        for w, p in self.got["phone"].items():
            with self.subTest(w=w):
                self.assertTrue(p["narrow"], p)
                self.assertIn("inbox", p["tabs"])
                self.assertEqual(p["front"], "inbox")
                self.assertTrue(p["rows"], p)
                self.assertTrue(all(r["r"] <= int(w) and r["x"] >= 0 for r in p["rows"]), p["rows"])
                self.assertEqual(p["btn"], 0, "the bar's Inbox gives way on a phone")

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
