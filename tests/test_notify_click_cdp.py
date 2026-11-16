"""A tapped notification opens the dashboard on that chat (P368), in headless
Chrome over CDP against a hub in a thread, with the real service worker
(/sw.js, registered by the page at 127.0.0.1: a secure context over http).

The worker's own functions are run in it over CDP (its target, type
``service_worker``): ``openFromNotification(deepUrl(url))`` is what its
notificationclick does with the notification's address.

* a dashboard open (a computer, 1440x900): the PO chat and a task chat open in
  that same tab, in place (no reload: a mark set on the page is still there),
  on the message; the bar, the Inbox and the Projects list stay; the page
  answered (no 3 s wait);
* an old ``/session?room=…`` address: the same, by the dashboard's deep link;
* a page that does not answer (an older dashboard, simulated by making its
  MessagePort.postMessage a no-op): the worker navigates that tab to the deep
  link after its 3 s wait, and it lands there;
* a phone (390x844, touch): the phone's own view of that chat, on the message
  (the PO chat full width under the bar, its Inbox the tab beside Chat; a
  task's chat with the bar's Inbox);
* no page errors in the dashboard tabs.

``win.focus()`` outside a notificationclick is refused too; the worker tells
the page anyway, which is what is tested here.
* no dashboard open: a new window on the deep link, never the chat page alone
  (with only a chat's own page open, that page is left as it was), and that
  link lands the dashboard on the chat.

``clients.openWindow`` outside a real notificationclick: Chrome refuses it
("Not allowed to open a window", measured 2026-11-16 with Chrome headless);
the test tries it first and, when refused, stubs it in the worker to record
the address, then opens that address in a tab of its own. Either way the
address is checked.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import notify  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
// What a chat frame holds (its page-level constants are not window properties).
const FV = `(f => { try { return f.contentWindow.eval("({ room: typeof ROOM === 'string' ? ROOM : '', landed: typeof LANDED !== 'undefined' && LANDED ? LANDED.mid + (LANDED.part ? '|' + LANDED.part : '') : '', drawn: typeof LAST_ITEMS !== 'undefined' && !!LAST_ITEMS })"); } catch (e) { return { err: String(e) }; } })`;
// The visible chat frames of a room, and those inside them (a thread beside a chat).
const FRAMES = room => `(() => { const vis = f => { const r = f.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const top = [...document.querySelectorAll('iframe')].filter(vis);
  const inner = top.flatMap(f => { try { return [...f.contentDocument.querySelectorAll('iframe')].filter(vis); } catch (e) { return []; } });
  return [...top, ...inner].map(${FV}).filter(x => x.room === ${JSON.stringify(room)}); })()`;
const LANDED = (room, mid) => `(() => { const f = ${FRAMES(room)}.find(x => (x.landed || '').split('|')[0] === ${JSON.stringify(mid)}); return f ? f.landed : ''; })()`;
// The dashboard around the chat: its bar, its Inbox and its Projects list.
const AROUND = `(() => { const vis = e => !!e && e.getClientRects().length > 0 && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden';
  const phone = phoneNow();
  return { path: location.pathname, search: location.search, mark: window.__mark || 0, booted: window.ensBooted === true,
    bar: vis(document.querySelector('header')) && vis(document.getElementById('bar-home')),
    barItems: ['bar-home', 'proj-switch', 'inbox-btn', 'me-btn', 'new-btn'].filter(id => vis(document.getElementById(id))),
    // A phone's Inbox is the bar's button (its pane over the chat when tapped),
    // on a PO's screen the tab beside Chat (#311); a computer's is pinned open
    // on the left strip, with the Projects list (#322).
    inbox: phone ? (vis(document.getElementById('inbox-btn')) ? 'bar' : vis(document.querySelector('#po-dock .dk-tab[data-dk-tab="inbox"]')) ? 'tab' : '')
      : ldOnScreen('inbox') && vis(document.getElementById('ld-ibx-body')) ? 'strip' : '',
    list: phone ? !!document.getElementById('sw-list') : vis(document.getElementById('ld-projs')) && document.querySelectorAll('#ld-projs a, #ld-projs [data-pid], #ld-projs button').length > 0,
    phone, pane: typeof PH_PANE === 'string' ? PH_PANE : null, body: document.body.className,
    over: document.documentElement.scrollWidth - innerWidth }; })()`;
const OUT = { errors: [] };
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = OUT;
  const pageSessions = new Set();
  const onmsg = c.ws.onmessage;
  c.ws.onmessage = ev => {
    try { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown' && pageSessions.has(m.sessionId)) {
        const d = m.params.exceptionDetails; out.errors.push(((d.exception && d.exception.description) || d.text || '').slice(0, 300)); } } catch (e) {}
    onmsg(ev);
  };
  await c.send('Target.setDiscoverTargets', { discover: true });
  const pages = async () => (await c.send('Target.getTargets')).targetInfos.filter(t => t.type === 'page');
  const hubPages = async () => (await pages()).filter(t => t.url.startsWith(A.base));
  const attach = async (targetId, mobile) => {
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    pageSessions.add(s);
    await c.send('Page.enable', {}, s); await c.send('Runtime.enable', {}, s);
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, s);
    await c.send('Emulation.setDeviceMetricsOverride', mobile ? { width: 390, height: 844, deviceScaleFactor: 3, mobile: true }
      : { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
    // No PO opened by itself at the start: what opens is what the notification named.
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, s);
    const ev = async (expression) => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(expression.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (ex, ms = 30000) => { const t = Date.now(); let last = null; while (Date.now() - t < ms) { let v = null; try { v = await ev(ex); } catch (e) { last = e; } if (v) return v; await sleep(150); } throw new Error('timeout: ' + ex.slice(0, 300) + (last ? ' :: ' + last.message.slice(0, 300) : '')); };
    const close = async () => { await c.send('Target.closeTarget', { targetId }).catch(() => null);
      for (let i = 0; i < 50 && (await pages()).some(t => t.targetId === targetId); i++) await sleep(100); };
    return { targetId, s, ev, until, close };
  };
  const tab = async (url, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const p = await attach(targetId, mobile);
    await c.send('Page.navigate', { url }, p.s);
    return p;
  };
  const dash = async (url, mobile) => {
    const p = await tab(url, mobile);
    await p.until('window.ensBooted === true', 40000);
    // The worker took this page (it registers at load and claims its pages).
    await p.until('!!navigator.serviceWorker.controller', 20000);
    await sleep(500);
    return p;
  };
  // The service worker, over CDP.
  let swS = null, swId = '';
  const worker = async () => {
    const t0 = Date.now();
    while (Date.now() - t0 < 20000) {
      const w = (await c.send('Target.getTargets')).targetInfos.find(t => t.type === 'service_worker' && t.url.startsWith(A.base + '/sw.js'));
      if (w) {
        // A worker just installed can be replaced as it activates: try the next one.
        if (w.targetId !== swId) {
          try { swS = (await c.send('Target.attachToTarget', { targetId: w.targetId, flatten: true })).sessionId; swId = w.targetId; }
          catch (e) { await sleep(200); continue; }
        }
        return swS;
      }
      await sleep(200);
    }
    throw new Error('no service worker target');
  };
  const inSw = async (expression) => {
    const s = await worker();
    const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s);
    if (r.exceptionDetails) throw new Error('sw: ' + expression.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600));
    return r.result.value;
  };
  // What the worker's notificationclick does with this address.
  const tap = (href) => inSw(`(async () => { const t = Date.now(); const url = deepUrl(${JSON.stringify(href)});
    try { const w = await openFromNotification(url); return { url, ms: Date.now() - t, win: w ? { url: w.url, id: w.id } : null }; }
    catch (e) { return { url, ms: Date.now() - t, err: String(e) }; } })()`);
  const link = (path, room, mid) => A.base + path + '?room=' + room + '&msg=' + encodeURIComponent(mid) + '&part=inbox';
  // An open dashboard told what to open: the same tab, no reload, the chat on the message.
  const inPlace = async (p, href, room, mid) => {
    await p.ev('window.__mark = 1, 0');
    const before = (await hubPages()).map(t => t.targetId).sort();
    const r = await tap(href);
    r.landed = await p.until(LANDED(room, mid), 30000).catch(async e => ({ fail: e.message, frames: await p.ev(FRAMES(room)) }));
    await sleep(300);
    r.around = await p.ev(AROUND);
    r.sameTabs = JSON.stringify((await hubPages()).map(t => t.targetId).sort()) === JSON.stringify(before);
    r.sameTarget = (await hubPages()).some(t => t.targetId === p.targetId);
    return r;
  };
  try {
    // ---- a computer, a dashboard open
    let d = await dash(A.base + '/');
    out.stage = 'po';
    out.poMsg = await inPlace(d, link('/', A.po, A.poMid), A.po, A.poMid);
    out.stage = 'task';
    out.taskMsg = await inPlace(d, link('/', A.task, A.taskMid), A.task, A.taskMid);
    // An old address, of the chat's own page.
    out.stage = 'oldPath';
    out.oldPath = await inPlace(d, link('/session', A.po, A.poMid2), A.po, A.poMid2);
    // ---- an older dashboard: it does not answer. (A capturing 'message'
    // listener that stops the event did not keep the page's from it: the page
    // is kept from answering on the port instead.)
    await d.ev(`MessagePort.prototype.postMessage = function () { window.__blocked = (window.__blocked || 0) + 1; }, window.__mark = 1, 0`);
    const before = (await hubPages()).map(t => t.targetId).sort();
    out.stage = 'older';
    const r = await tap(link('/', A.task, A.taskMid2));
    await sleep(300);
    await d.until('window.ensBooted === true', 40000);
    r.landed = await d.until(LANDED(A.task, A.taskMid2), 30000).catch(async e => ({ fail: e.message, frames: await d.ev(FRAMES(A.task)) }));
    r.around = await d.ev(AROUND);
    r.sameTabs = JSON.stringify((await hubPages()).map(t => t.targetId).sort()) === JSON.stringify(before);
    out.older = r;
    await d.close();
    // ---- a phone
    out.stage = 'phone';
    d = await dash(A.base + '/', true);
    out.phone = await inPlace(d, link('/', A.po, A.poMid), A.po, A.poMid);
    out.phone.view = await d.ev(`(() => { const vis = e => !!e && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0;
      const f = [...document.querySelectorAll('iframe')].filter(vis).map(f => { const b = f.getBoundingClientRect(); return { cls: f.className, room: f.dataset.room || '', x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) }; });
      return { frames: f, inboxPane: !document.getElementById('ph-inbox').hidden, dock: document.body.classList.contains('po-dock'), detail: document.body.classList.contains('detail-open'),
        vw: innerWidth, vh: innerHeight }; })()`);
    out.phoneTask = await inPlace(d, link('/', A.task, A.taskMid), A.task, A.taskMid);
    await d.close();
    // ---- no window open at all
    for (const t of await hubPages()) await c.send('Target.closeTarget', { targetId: t.targetId }).catch(() => null);
    await sleep(500);
    out.stage = 'noWindow';
    const nb = (await pages()).length;
    const real = await tap(link('/', A.po, A.poMid));
    await sleep(1500);
    const opened = (await hubPages());
    out.noWindowReal = { r: real, opened: opened.map(t => t.url), pagesBefore: nb };
    let openedUrl = '';
    if (opened.length) {
      openedUrl = opened[0].url;
      out.noWindowWay = 'real';
      for (const t of opened) await c.send('Target.closeTarget', { targetId: t.targetId }).catch(() => null);
    } else {
      out.noWindowWay = 'stub';
      await inSw(`self.__opened = [], self.clients.openWindow = u => { self.__opened.push(u); return Promise.resolve(null); }, 0`);
      out.noWindowStub = await tap(link('/', A.po, A.poMid));
      out.noWindowStub.opened = await inSw('self.__opened');
      openedUrl = out.noWindowStub.opened[0] || '';
    }
    out.noWindowUrl = openedUrl;
    if (openedUrl) {
      out.stage = 'noWindowLands';
      d = await dash(openedUrl);
      out.noWindowLands = { landed: await d.until(LANDED(A.po, A.poMid), 30000).catch(async e => ({ fail: e.message, frames: await d.ev(FRAMES(A.po)) })) };
      await sleep(300);
      out.noWindowLands.around = await d.ev(AROUND);
      await d.close();
    }
    // ---- an old address, no dashboard, a chat's own page open: never turned into it
    if (out.noWindowWay === 'stub') {
      out.stage = 'oldNoDash';
      const s = await tab(A.base + '/session?room=' + A.po + '&stay=1');
      await s.until('typeof ROOM !== "undefined"', 20000);
      await sleep(500);
      await inSw('self.__opened = [], 0');
      out.oldNoDash = await tap(link('/session', A.task, A.taskMid));
      out.oldNoDash.opened = await inSw('self.__opened');
      out.oldNoDash.chatPage = await s.ev('location.pathname + location.search');
      await s.close();
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(JSON.stringify(OUT).slice(0, 3000)); console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class NotificationClickOpensTheDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-368-", ignore_cleanup_errors=True)
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
            mock.patch.object(notify, "_sender", lambda *a, **k: 201),
            mock.patch.object(notify, "_log"),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        sid = "notifypo"
        po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "role": "Product owner",
                                                 "sessionId": sid, "cwd": str(home)}])
        full = chatroom.get_room(po["id"], public=False)
        full["mode"] = "solo"
        chatroom.update_room(full)
        dashboard.assign_session_project(po["id"], proj["id"])
        dashboard.set_project_po(proj["id"], po["id"])
        task = chatroom.create_room("Gearbox noise", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                      {"identity": "claude-2", "agent": "claude", "role": "Reviewer",
                                                       "cwd": str(home)}])
        dashboard.assign_session_project(task["id"], proj["id"])
        chatroom.post_message(task["id"], "user", "Find the gearbox noise.", to="claude")
        tmids = []
        for i in range(14):
            chatroom.post_message(task["id"], "claude", f"Gearbox note {i}: " + "words " * 40, to="user")
            tmids.append(chatroom.get_room(task["id"], public=False)["messages"][-1]["id"])
        # The PO chat: a transcript, long enough that a balloon is not all on screen at once.
        at = time.time() - 3600

        def turn(role, text, off):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(at + off)) + ".000Z"
            msg = {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}
            if role == "assistant":
                msg["stop_reason"] = "end_turn"
            return {"type": role, "timestamp": stamp, "sessionId": sid, "message": msg}
        lines = [turn("user", "Start.", 0), turn("assistant", "The brakes are wider now.", 1)]
        lines += [turn("user" if i % 2 else "assistant", f"Filler {i} " + "words " * 30, i + 2) for i in range(2, 24)]
        tdir = base / "transcripts" / "C--po"
        tdir.mkdir()
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.ids = {"po": po["id"], "poMid": f"{sid}:1", "poMid2": f"{sid}:22",
                   "task": task["id"], "taskMid": tmids[1], "taskMid2": tmids[12]}
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": cls.base, **cls.ids}
        script = base / "notify_click_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8",
                             errors="replace", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_SHOTS"):
            Path(os.environ["ENSEMBLE_SHOTS"]).mkdir(parents=True, exist_ok=True)
            (Path(os.environ["ENSEMBLE_SHOTS"]) / "notify-click.json").write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def deep(self, room, mid):
        return f"{self.base}/?room={room}&msg={mid.replace(':', '%3A')}&part=inbox"

    def assert_dashboard(self, around, phone=False):
        self.assertEqual(around["path"], "/", around)
        self.assertTrue(around["booted"], around)
        self.assertTrue(around["bar"], around)
        self.assertTrue(around["inbox"], around)
        self.assertTrue(around["list"], around)
        self.assertEqual(around["phone"], phone, around)
        self.assertLessEqual(around["over"], 1, around)

    def assert_in_place(self, r, room, mid, phone=False):
        self.assertNotIn("err", r, r)
        self.assertEqual(r["url"], self.deep(room, mid), r)
        # The page answered: the worker did not wait out its 3 s and navigate.
        self.assertLess(r["ms"], 2900, r)
        self.assertIsNotNone(r["win"], r)
        self.assertTrue(isinstance(r["landed"], str) and r["landed"].startswith(mid), r)
        self.assertTrue(r["sameTabs"], r)
        self.assertTrue(r["sameTarget"], r)
        self.assertEqual(r["around"]["mark"], 1, "the page was not reloaded")
        self.assert_dashboard(r["around"], phone)

    def test_po_chat_opens_in_place_on_the_message(self):
        self.assert_in_place(self.got["poMsg"], self.ids["po"], self.ids["poMid"])

    def test_task_chat_opens_in_place_on_the_message(self):
        self.assert_in_place(self.got["taskMsg"], self.ids["task"], self.ids["taskMid"])

    def test_old_session_address_opens_the_dashboard_in_place(self):
        self.assert_in_place(self.got["oldPath"], self.ids["po"], self.ids["poMid2"])

    def test_a_page_that_does_not_answer_is_navigated_to_the_deep_link(self):
        r = self.got["older"]
        self.assertNotIn("err", r, r)
        self.assertGreaterEqual(r["ms"], 3000, r)
        self.assertIsNotNone(r["win"], r)
        self.assertTrue(r["landed"].startswith(self.ids["taskMid2"]), r)
        self.assertTrue(r["sameTabs"], "the same tab, navigated")
        self.assertEqual(r["around"]["mark"], 0, "a new page in that tab")
        self.assert_dashboard(r["around"])

    def test_phone_opens_its_own_view_of_the_chat(self):
        r = self.got["phone"]
        self.assert_in_place(r, self.ids["po"], self.ids["poMid"], phone=True)
        v = r["view"]
        self.assertFalse(v["inboxPane"], v)
        po = [f for f in v["frames"] if f["room"] == self.ids["po"]]
        self.assertTrue(po, v)
        self.assertEqual(po[0]["x"], 0, v)
        self.assertEqual(po[0]["w"], v["vw"], v)
        self.assert_in_place(self.got["phoneTask"], self.ids["task"], self.ids["taskMid"], phone=True)

    def test_no_window_opens_a_new_dashboard_on_the_chat(self):
        want = self.deep(self.ids["po"], self.ids["poMid"])
        self.assertEqual(self.got["noWindowUrl"], want, self.got.get("noWindowReal"))
        if self.got["noWindowWay"] == "stub":
            self.assertIn("Not allowed", self.got["noWindowReal"]["r"].get("err", ""), self.got["noWindowReal"])
            self.assertEqual(self.got["noWindowStub"]["opened"], [want])
        g = self.got["noWindowLands"]
        self.assertTrue(isinstance(g["landed"], str) and g["landed"].startswith(self.ids["poMid"]), g)
        self.assert_dashboard(g["around"])
        self.assertEqual(g["around"]["search"], "", "the address cleared once it opened")

    def test_old_address_with_only_a_chat_page_open_opens_the_dashboard(self):
        if self.got["noWindowWay"] != "stub":
            self.skipTest("openWindow was not stubbed")
        r = self.got["oldNoDash"]
        self.assertEqual(r["opened"], [self.deep(self.ids["task"], self.ids["taskMid"])], r)
        self.assertEqual(r["chatPage"], f"/session?room={self.ids['po']}&stay=1", "the chat's own page is left as it was")

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
