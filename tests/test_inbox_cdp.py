"""The inbox on the page (#302, Layout A step 1), in headless Chrome over CDP,
against a hub in a thread serving the pages (inbox.py is tests/test_inbox.py).

Two projects, each with a PO whose chat holds a point answered and not seen
yet (a "check" row of the inbox). Checked:

* at 1728x1117 the Inbox is a column left of the PO's conversation, about
  280px wide, its rows the hub's /api/inbox rows for this project, each with
  its group heading, task (PO) and age; the bar's "Inbox N" counts every
  project; "All" lists the other project's row with its project's name and
  "This project" filters it out again;
* a real click on a row opens its message: the PO chat lands on that balloon,
  marked as opened from the Inbox (#343);
* the bar's Inbox from another screen (the task list, no project chosen)
  opens a PO screen that has rows, with the Inbox column shown;
* on a phone (390x844, and 360 and 430) the bar's Inbox opens the Inbox pane
  over the conversation (#307), its rows on screen; the PO screen has no Inbox
  tab; nothing is wider than the screen;
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
  const toPo = (p, pid = A.proj) => p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const ready = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!IBX.data && !!LD_INBOX.querySelector(".ibx-row")', 30000);
  const R = 'const R = el => { const b = el.getBoundingClientRect(); return { x: Math.round(b.left), r: Math.round(b.right), w: Math.round(b.width), h: Math.round(b.height) }; };';
  const fits = `(() => { const W = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll('#po-dock .ibx, #po-dock .ibx *, #list-dock .ibx, #list-dock .ibx *')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > W + 1; }).map(e => e.className || e.tagName);
    return { W, scroll: document.documentElement.scrollWidth, body: document.body.scrollWidth, wide: wide.slice(0, 5) }; })()`;
  try {
    // ---- 1728 x 1117: the column beside the conversation
    const p = await page(1728, 1117);
    await toPo(p); await ready(p); await sleep(400);
    out.desk = await p.evalIn(`(async () => { ${R}
      const api = await (await fetch('/api/inbox', { cache: 'no-store' })).json();
      const rows = [...LD_INBOX.querySelectorAll('.ibx-row')];
      return { col: R(LD_INBOX), chat: R(PD.els['po-chat']), shown: ldOnScreen('inbox') && LD.dock.frontOf('inbox') === 'inbox',
        auto: LD.dock.isAuto('inbox'), inPd: !!PD.dock && PD.dock.isVisible('inbox'),
        rows: rows.map(r => r.dataset.ibx), apiRows: api.rows.filter(r => r.projectId === ${JSON.stringify(A.proj)}).map(r => r.id),
        apiAll: api.rows.length, counts: api.counts || null,
        heads: [...LD_INBOX.querySelectorAll('.pdp-head')].map(h => h.textContent.trim()), stages: rows.map(r => r.dataset.stage || ''),
        where: rows.map(r => r.querySelector('.ibx-where').textContent), ages: rows.map(r => r.querySelector('.pdp-age').textContent),
        lines: rows.map(r => r.querySelector('.ibx-line').textContent),
        scope: [...LD_INBOX.querySelectorAll('[data-ibx-scope]')].map(b => b.dataset.ibxScope + ':' + b.getAttribute('aria-pressed')),
        btn: document.getElementById('inbox-btn').textContent.trim(), btnShown: document.getElementById('inbox-btn').getBoundingClientRect().width > 0,
        tool: toolIcon('inbox').includes('ti-inbox') && getComputedStyle(document.body).getPropertyValue('--tool-inbox').trim() !== '' };
    })()`);
    out.deskFits = await p.evalIn(fits);
    await p.shot('inbox-1728x1117');
    // All projects, then this project again.
    await p.click('document.querySelector(\'#ld-projs [data-ldp="all"]\')'); await sleep(200);
    out.all = await p.evalIn(`({ rows: [...LD_INBOX.querySelectorAll('.ibx-row')].map(r => r.dataset.ibx),
      where: [...LD_INBOX.querySelectorAll('.ibx-where')].map(e => e.textContent), saved: localStorage.getItem('cd-inbox-scope') })`);
    await p.click('document.querySelector(\'#ld-projs [data-ldp-proj="' + A.proj + '"]\')'); await sleep(200);
    out.back = await p.evalIn(`[...LD_INBOX.querySelectorAll('.ibx-row')].map(r => r.dataset.ibx)`);
    // A row opens its message: the chat lands on that balloon.
    await p.until('(() => { const f = pdChatFrame(); return !!(f && f.contentWindow && f.contentWindow.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN")); })()', 30000);
    const mid = await p.evalIn('LD_INBOX.querySelector(".ibx-row a.ibx-link[data-mid]").dataset.mid');
    await p.evalIn('(() => { const b = pdChatFrame().contentDocument.getElementById("msgs"); b.scrollTop = 0; })(); 0');
    await p.click('LD_INBOX.querySelector(".ibx-row a.ibx-link[data-mid]")');
    await p.until(`(() => { const d = pdChatFrame().contentDocument, l = d.querySelector('.msg.ibx-landed'); const m = l && (l.closest('.msg[data-mid]') || l); return !!m && m.dataset.mid === ${JSON.stringify(mid)}; })()`, 10000).catch(() => null);
    out.open = await p.evalIn(`(() => { const d = pdChatFrame().contentDocument, box = d.getElementById('msgs'), l = d.querySelector('.msg.ibx-landed');
      const m = l && (l.closest('.msg[data-mid]') || l), el = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
      const er = el && el.getBoundingClientRect(), br = box.getBoundingClientRect();
      return { mid: ${JSON.stringify(mid)}, landed: m ? m.dataset.mid : '', current: m ? m.getAttribute('aria-current') : '', inView: !!el && er.bottom > br.top && er.top < br.bottom, here: location.pathname }; })()`);
    // The row's answers, listed under it: the second one opens its own answer.
    await p.click('LD_INBOX.querySelector(".ibx-more > summary")'); await sleep(200);
    out.more = await p.evalIn(`(() => { const d = LD_INBOX.querySelector('.ibx-more');
      return { open: d.open, line: d.closest('.ibx-row').querySelector('.ibx-line').textContent, summary: d.querySelector('summary').textContent,
        items: [...d.querySelectorAll('.ibx-item a.ibx-link')].map(a => a.dataset.mid) }; })()`);
    await p.evalIn('(() => { IBX.sig = ""; ibxPaint(); return 0; })()');
    out.moreKept = await p.evalIn('LD_INBOX.querySelector(".ibx-more").open');
    await p.click('LD_INBOX.querySelectorAll(".ibx-item a.ibx-link")[1]');
    await p.until(`(() => { const l = pdChatFrame().contentDocument.querySelector('.msg.ibx-landed'); const m = l && (l.closest('.msg[data-mid]') || l); return !!m && m.dataset.mid === ${JSON.stringify(A.mid2)}; })()`, 10000).catch(() => null);
    out.moreOpen = await p.evalIn(`(() => { const l = pdChatFrame().contentDocument.querySelector('.msg.ibx-landed'); const m = l && (l.closest('.msg[data-mid]') || l); return m ? m.dataset.mid : ''; })()`);
    // #322: off the PO screen the Inbox stays on the left strip; #328: with nothing open it keeps the last pick.
    out.prePid = await p.evalIn('IBX.ldPid');
    await p.evalIn(`(() => { midLeave(); VIEW_MODE = 'board'; SELECTED_PROJECT = ''; renderRows(); return 0; })()`);
    await p.until('!document.body.classList.contains("po-dock")', 10000).catch(() => null);
    await sleep(300);
    out.offPo = await p.evalIn(`({ po: document.body.classList.contains("po-dock"), inbox: ldOnScreen("inbox"), pid: IBX.ldPid, here: projHere() || '',
      rows: [...LD_INBOX.querySelectorAll('.ibx-row')].map(r => r.dataset.ibx), want: ibxRows(IBX.data, IBX.scope, IBX.ldPid).map(r => r.id) })`);
    await p.close();
    // ---- phones: the PO screen's Inbox tab, beside Chat (#311)
    out.phone = {};
    for (const [w, h] of [[390, 844], [360, 780], [430, 932]]) {
      const q = await page(w, h, true);
      await toPo(q, A.phones[w]);   // its own project: the desk page opened Motors' chat, so its answers are seen
      await q.until('document.body.classList.contains("po-dock") && !!PD.dock && PD.dock.narrow() && !!IBX.data', 30000);
      await q.click('document.querySelector("#po-dock .dk-tab[data-dk-tab=inbox]")'); await sleep(400);
      const pane = 'PD.els.inbox';
      await q.until(`!!${pane}.querySelector(".ibx-row") && ${pane}.getBoundingClientRect().width > 0`, 10000).catch(() => null);
      out.phone[w] = await q.evalIn(`(() => { ${R} const f = ${fits}; const el = ${pane};
        return { ...f, narrow: PD.dock.narrow(), pane: PH_PANE, shown: PD.dock.isShown('inbox'), col: R(el),
          rows: [...el.querySelectorAll('.ibx-row')].map(r => R(r)),
          tabs: [...document.querySelectorAll('#po-dock .dk-tab')].filter(t => t.getBoundingClientRect().width > 0).map(t => t.dataset.dkTab),
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

        def project(name, ask, answer, more=None):
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
            mid = lambda m: ((m or {}).get("message") or {}).get("id", "")
            first, second = mid(m), ""
            if more:    # a second answer in the same PO chat: still one row
                text, ids = points.take(chatroom.get_room(po["id"], public=False), more[0], to="claude", key="k2")
                assert ids == ["P2"], ids
                chatroom.post_message(po["id"], "user", text, to="claude")
                for i in range(12):
                    chatroom.post_message(po["id"], "codex", f"aside {i}\n\n" + "words " * 60, to="claude")
                second = mid(chatroom.post_message(po["id"], "claude", f"Re P2: {more[1]}", to="user"))
            for i in range(25):   # enough talk that the answer is far up
                chatroom.post_message(po["id"], "codex", f"note {i}\n\n" + "words " * 60, to="claude")
            points.sync(po["id"], force=True)
            return proj["id"], po["id"], first, second

        cls.proj, cls.po, cls.mid, cls.mid2 = project("Motors", "Please make the board wider", "it is wider now.",
                                                      ("And the cards taller", "they are taller now."))
        cls.other, cls.other_po, _, _ = project("Wheels", "Rounder wheels", "they are round.")
        # One project per phone width: opening a PO chat marks its answers seen, and the row leaves.
        cls.phones = {w: project(f"Phone{w}", "Bigger buttons", "they are bigger.")[0] for w in (390, 360, 430)}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "po": cls.po, "mid2": cls.mid2, "phones": cls.phones, "shots": shots}
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
        self.assertFalse(d["inPd"], "#322: not in the PO dock on a computer")
        self.assertLessEqual(d["col"]["r"], d["chat"]["x"] + 1, "left of the conversation, covering nothing")
        self.assertTrue(240 <= d["col"]["w"] <= 320, d["col"])
        self.assertGreaterEqual(d["chat"]["w"], 360)
        self.assertTrue(d["rows"], d)
        self.assertEqual(d["rows"], d["apiRows"], "the hub's rows for this project, in its order")
        # #322 (P287 drawing): cards, no group headings; each card carries its stage
        self.assertEqual(d["heads"], [], d["heads"])
        self.assertEqual(d["stages"], ["check"] * len(d["rows"]), d["stages"])
        self.assertTrue(all(w == "PO" for w in d["where"]), d["where"])
        self.assertTrue(all(a for a in d["ages"]), d["ages"])
        self.assertTrue(any(x.startswith("Motors PO: ") for x in d["lines"]), d["lines"])
        self.assertEqual(d["scope"], [], "the Projects panel owns the All/project choice")
        self.assertTrue(d["tool"], "the Inbox's icon in its colour")

    def test_the_bar_counts_every_project(self):
        # #322: the bar's Inbox gives way to the strip's on a computer; it still keeps the count.
        d = self.got["desk"]
        self.assertFalse(d["btnShown"])
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
        self.assertEqual(o["landed"], o["mid"], "#343: the whole message marked as opened from the Inbox")
        self.assertEqual(o["current"], "location", o)
        self.assertTrue(o["inView"], o)
        self.assertEqual(o["here"], "/", "in the page, not a new tab")

    def test_a_po_chats_answers_are_one_row_listed_under_it(self):
        # PO fix 6: two answers to check in the Motors PO chat are one row.
        d, m = self.got["desk"], self.got["more"]
        self.assertEqual(len(d["rows"]), 1, d["rows"])
        self.assertEqual(m["line"], "Motors PO: 2 answers to check")
        self.assertEqual((m["open"], m["summary"]), (True, "2 answers"))
        self.assertEqual(m["items"], [self.mid, self.mid2], "oldest first")
        self.assertTrue(self.got["moreKept"], "stays open when the inbox paints again")
        self.assertEqual(self.got["moreOpen"], self.mid2, "an answer opens its own message")

    def test_off_the_po_screen_the_inbox_keeps_the_pick(self):
        o = self.got["offPo"]
        self.assertFalse(o["po"], "left the PO screen first")
        self.assertTrue(o["inbox"], o)
        self.assertEqual(o["here"], "", "nothing open")
        self.assertEqual(o["pid"], self.got["prePid"], "the pick stays (#328)")
        self.assertEqual(o["rows"], o["want"])

    def test_nothing_is_wider_than_the_screen(self):
        f = self.got["deskFits"]
        self.assertLessEqual(f["scroll"], f["W"], f)
        self.assertEqual(f["wide"], [], f)
        for w, p in self.got["phone"].items():
            with self.subTest(w=w):
                self.assertLessEqual(p["scroll"], p["W"], p)
                self.assertLessEqual(p["body"], p["W"], p)
                self.assertEqual(p["wide"], [], p)

    def test_on_a_phone_the_inbox_is_a_tab_beside_chat(self):
        for w, p in self.got["phone"].items():
            with self.subTest(w=w):
                self.assertTrue(p["narrow"], p)
                self.assertEqual(p["tabs"][:2], ["po-chat", "inbox"], "Chat | Inbox (#311)")
                self.assertTrue(p["shown"], p)
                self.assertEqual(p["pane"], "", "the tab, not #307's pane")
                self.assertEqual(p["btn"], 0, "the bar has no Inbox of its own beside the tab")
                self.assertTrue(p["rows"], p)
                self.assertTrue(all(r["r"] <= int(w) and r["x"] >= 0 for r in p["rows"]), p["rows"])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
