"""A file link in a chat opens the file as a panel of the dock (#428, GitHub
issue 71), as the Files tool does.

In headless Chrome over CDP, against a hub in a thread (test_tool_strip's),
on a computer with the PO's dock:

* a plain click on a file link in the PO's chat opens a file:<path> panel at
  the right of the conversation, and no new tab;
* a second click on it reveals that panel: one panel per file;
* a relative link ("NOTES.md") opens at the path the hub resolved it to;
* a link with a line opens the file's source at that line;
* a link to a file that is not there, and a Ctrl-click, open no panel;
* a link the hub cannot show here (a folder) opens the browser's own new tab:
  the hub is asked before the click (pointer over, focus, button down), so
  the click decides at once and no window is opened after an await;
* this page's own file links (an Inbox card's) do the same, the Files tool's
  "open in its own tab" does not;
* on a phone, the dock opens no panel for a link (it stays a new tab).

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

import chatroom
import dashboard
from tests.test_page_update import CHROME
from tests.test_tool_strip import NODE, start_hub
from tests import chrome_profile

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown') this.errors.push(m.params); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const out = {};
const fs = require('fs');
// ENSEMBLE_CFL_SHOTS=<folder>: the page before and after the first click, as PNGs.
async function shot(S, name) { if (!A.shots) return; const r = await S('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(A.shots + '/' + name + '.png', Buffer.from(r.data, 'base64')); }
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    const S = (m, p) => c.send(m, p, sessionId);
    await S('Page.enable'); await S('Runtime.enable');
    await S('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    const evalIn = async (expr) => { const r = await S('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const pages = async () => (await c.send('Target.getTargets')).targetInfos.filter(t => t.type === 'page').length;
    const files = () => evalIn('[...PD.rt.keys()].filter(k => k.startsWith("file:")).sort()');
    // The PO chat's file link whose text ends so, in view, and where it is on the page.
    const FR = 'document.querySelector("#po-panel iframe.po-session:not([hidden])")';
    const linkAt = end => evalIn(`(() => { const f = ${FR}, d = f.contentDocument;
      const a = [...d.querySelectorAll('a.file-link')].find(x => x.textContent.trim().endsWith(${JSON.stringify(end)}));
      if (!a) throw new Error('no link ' + ${JSON.stringify(end)});
      a.scrollIntoView({ block: 'center' });
      const r = a.getBoundingClientRect(), o = f.getBoundingClientRect();
      return [o.left + r.left + Math.min(r.width / 2, 20), o.top + r.top + r.height / 2]; })()`);
    const click = async (end, modifiers = 0) => { const [x, y] = await linkAt(end);
      await S('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }); await sleep(400);   // the pointer over it: the hub asked
      for (const type of ['mousePressed', 'mouseReleased']) await S('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: 1, clickCount: 1, modifiers }); };
    await S('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' });
    await S('Page.navigate', { url: A.base + '/' });
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
    await until('window.ensBooted === true', 30000);
    await evalIn(`(() => { ['cd-tool-strip', 'cd-tool-open', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k));
      SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!PD.dock && !!' + FR, 30000);
    await until(`(() => { const d = ${FR}.contentDocument; return !!d && d.querySelectorAll('a.file-link').length >= 4; })()`, 30000);
    await until('[...WS_VIEWS.values()].some(v => v.el && v.el.isConnected)', 30000);
    await until(`!${FR}.contentDocument.querySelector('a.file-link:not(.file-missing)[href*="GONE"]')`, 10000);
    await sleep(800);
    await linkAt('README.md'); await sleep(300); await shot(S, 'before');
    out.pages0 = await pages();
    out.files0 = await files();

    // A plain click: a panel at the right of the conversation, no new tab.
    await click('README.md');
    await until('[...PD.rt.keys()].some(k => k.startsWith("file:"))', 15000);
    await sleep(600);
    out.files1 = await files();
    await sleep(900); await shot(S, 'after');
    out.pages1 = await pages();
    out.where1 = await evalIn(`(() => { const id = [...PD.rt.keys()].find(k => k.startsWith("file:"));
      const p = PD.rt.get(id).v.panels.get(id.slice(5)) || null, el = document.querySelector('.wfp');
      const conv = document.getElementById('po-panel').getBoundingClientRect(), r = el && el.getBoundingClientRect();
      return { visible: PD.dock.isVisible(id), right: !!r && r.left >= conv.right - 2, width: r ? Math.round(r.width) : 0 }; })()`);

    // Again: that panel, no second one.
    await click('README.md');
    await sleep(900);
    out.files2 = await files();

    // A relative link: at the path the hub resolved.
    await click('NOTES.md');
    await until('[...PD.rt.keys()].filter(k => k.startsWith("file:")).length === 2', 15000);
    out.files3 = await files();

    // A line: the source at that line.
    await click('LINES.md:3');
    await until('[...PD.rt.keys()].filter(k => k.startsWith("file:")).length === 3', 15000);
    out.line = await evalIn(`(() => { for (const v of WS_VIEWS.values()) { const t = v.tabs.find(x => /lines\\.md$/i.test(x.path)); if (t) return { view: t.st.view, mark: t.st.marks.source }; } return null; })()`);

    // Not there; with Ctrl: no panel.
    await click('GONE.md');
    await sleep(1200);
    await click('OTHER.md', 2);
    await sleep(1200);
    out.files4 = await files();

    // One the hub cannot show here (a folder of that name): the browser's own new tab, no panel.
    const before = await pages();
    await click('FOLDER.md');
    await sleep(1500);
    out.folder = { files: await files(), newTabs: (await pages()) - before };

    // This page's own file link (as an Inbox card draws it), and the Files tool's ⧉.
    out.own = await evalIn(`(() => { const a = document.createElement('a'); a.className = 'file-link'; a.target = '_blank';
      a.href = FileLinks.href(${JSON.stringify(A.own)}); a.textContent = 'OWN.md'; a.id = 't-own';
      document.body.appendChild(a); pdFileLinkAsk(a.href); return 0; })()`);
    await sleep(500);
    out.own = await evalIn(`(() => { const a = document.getElementById('t-own') || document.querySelector('#t-own'); a.click(); a.remove();
      // The Files tool's own tab: left to the browser (false: something kept it from opening).
      const w = document.querySelector('.wfp .wsp-own');
      return w.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })); })()`);
    await until('[...PD.rt.keys()].filter(k => k.startsWith("file:")).length === 4', 15000);
    out.files5 = await files();

    // A task's chat: its own Files (never shown, so mounted hidden) opens it.
    await evalIn(`(() => { const r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.task)}); openDetail(r.sessionId); return 0; })()`);
    await until(`pdTaskWanted() && !!pdById('detail-panel') && !!pdById('detail-panel')._panes && !!pdById('detail-panel')._panes.workspace`, 20000);
    out.taskWsBefore = await evalIn(`!!pdById('detail-panel')._panes.workspace.querySelector('.wsp')`);
    const th = `FileLinks.href(${JSON.stringify(A.task_file)}, { room: ${JSON.stringify(A.task)} })`;
    out.taskEarly = await evalIn(`pdOpenFileLink(${th})`);   // before the hub's answer: the browser's tab
    await sleep(600);
    out.taskOpened = await evalIn(`pdOpenFileLink(${th})`);
    await until(`[...PD.rt.keys()].some(k => k.endsWith('task.md'))`, 15000);
    out.taskCtx = await evalIn(`(() => { const e = [...PD.rt.entries()].find(([k]) => k.endsWith('task.md'))[1]; return e.v.ctx.kind; })()`);
    out.taskWsShown = await evalIn(`pdToolOn('workspace')`);

    // A phone: the dock opens no panel for a link.
    await S('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await until('isPhone()', 10000);
    out.phone = await evalIn(`(() => { const h = FileLinks.href(${JSON.stringify(A.own)}); pdFileLinkAsk(h); return pdOpenFileLink(h); })()`);
    if (A.shots) {   // before: the link opened a tab of its own, the file view
      await S('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
      const href = await evalIn(`[...${FR}.contentDocument.querySelectorAll('a.file-link')].find(x => x.textContent.trim().endsWith('README.md')).href`);
      await S('Page.navigate', { url: href }); await sleep(2500); await shot(S, 'before-tab');
    }
    out.exceptions = c.errors.map(e => (e.exceptionDetails && (e.exceptionDetails.exception && e.exceptionDetails.exception.description || e.exceptionDetails.text)) || '');
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e, JSON.stringify(out).slice(0, 3000)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class ChatFileLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls, prefix="ens-cfl-")
        home = next(f.parent for f in cls.root.rglob("README.md") if "Motors" in f.read_text(encoding="utf-8"))
        cls.home = home
        for n in ("NOTES.md", "OTHER.md", "OWN.md", "TASK.md"):
            (home / n).write_text(f"# {n}\n", encoding="utf-8")
        (home / "FOLDER.md").mkdir()
        (home / "LINES.md").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        pj = next(p for p in dashboard.load_projects() if p["id"] == cls.proj)
        po = dashboard.project_po_room(pj)
        chatroom.post_message(po["id"], "claude", "\n".join([
            f"- `{home / 'README.md'}`",
            "- `NOTES.md`",
            f"- `{home / 'LINES.md'}:3`",
            f"- `{home / 'GONE.md'}`",
            f"- `{home / 'OTHER.md'}`",
            f"- `{home / 'FOLDER.md'}`",
        ]), to="user")
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "own": str(home / "OWN.md"),
                "task": cls.task, "task_file": str(home / "TASK.md"), "shots": os.environ.get("ENSEMBLE_CFL_SHOTS", "")}
        script = base / "cfl_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_CFL_PRINT"):
            print(json.dumps(cls.got, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def pid(self, name):
        return "file:" + str(self.home / name).replace("\\", "/").lower()

    def test_a_plain_click_opens_a_panel_at_the_right_of_the_conversation(self):
        self.assertEqual(self.got["files0"], [])
        self.assertEqual(self.got["files1"], [self.pid("README.md")])
        self.assertEqual(self.got["where1"]["visible"], True, self.got["where1"])
        self.assertTrue(self.got["where1"]["right"], self.got["where1"])
        self.assertEqual(self.got["pages1"], self.got["pages0"], "no new tab")

    def test_a_second_click_reveals_the_same_panel(self):
        self.assertEqual(self.got["files2"], [self.pid("README.md")])

    def test_a_relative_link_opens_where_the_hub_resolved_it(self):
        self.assertEqual(self.got["files3"], sorted([self.pid("README.md"), self.pid("NOTES.md")]))

    def test_a_line_opens_the_source_there(self):
        self.assertEqual(self.got["line"], {"view": "source", "mark": 3})

    def test_a_missing_file_and_a_ctrl_click_open_no_panel(self):
        self.assertEqual(self.got["files4"], sorted([self.pid("README.md"), self.pid("NOTES.md"), self.pid("LINES.md")]))

    def test_one_the_hub_cannot_show_opens_the_browser_s_own_tab(self):
        # Review 1: decided at the click, so the tab is the link's own, never a late window.open.
        self.assertEqual(self.got["folder"]["files"], sorted([self.pid("README.md"), self.pid("NOTES.md"), self.pid("LINES.md")]))
        self.assertEqual(self.got["folder"]["newTabs"], 1, self.got["folder"])

    def test_this_page_s_own_links_too_but_not_the_files_tool_s_own_tab(self):
        self.assertEqual(self.got["files5"], sorted([self.pid(n) for n in ("README.md", "NOTES.md", "LINES.md", "OWN.md")]))
        self.assertIs(self.got["own"], True, "the Files tool's own-tab link stays a link")

    def test_a_task_s_link_opens_in_its_files_mounted_hidden(self):
        self.assertIs(self.got["taskWsBefore"], False, "its Files tool was never shown")
        self.assertIs(self.got["taskEarly"], False, "a click before the hub's answer keeps the link")
        self.assertIs(self.got["taskOpened"], True)
        self.assertEqual(self.got["taskCtx"], "task")
        self.assertIs(self.got["taskWsShown"], False, "the tool stays as it was")

    def test_a_phone_keeps_the_new_tab(self):
        self.assertIs(self.got["phone"], False)

    def test_no_exceptions(self):
        self.assertEqual(self.got["exceptions"], [])


if __name__ == "__main__":
    unittest.main()
