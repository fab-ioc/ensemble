"""Dock v0.14.1 (#196): a click into the conversation's frame puts a tool back.

In headless Chrome over CDP at 1728x1117 and 1280x800, against the hub of
test_tool_strip.py, with real mouse clicks:

* a tool opened from the strip slides back on a click into the conversation's
  frame, and Dock does it (the page's own relay, pdChatClicked, is not called);
* a click on the tool's own content keeps it out;
* with the tool's ⋯ menu open, the first click into the frame closes only the
  menu, a second puts the tool back (through the relay: the first left focus
  in the frame);
* the press Dock cannot see (the frame already had focus when the tool opened)
  still puts it back, through the relay and Dock's closeFly();
* Send in the conversation sends on the first click while a tool is out, and
  the tool goes back.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tests import chrome_profile
from tests.test_tool_strip import CDP_JS as STRIP_JS, CHROME, NODE, start_hub

SIZES = [[1728, 1117], [1280, 800]]

CDP_JS = STRIP_JS[:STRIP_JS.index("async function main()")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h] of """ + json.dumps(SIZES) + r""") {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Runtime.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const press = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId); };
      const click = async (sel) => {
        const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
        await press(x, y);
      };
      const CHAT = '#po-panel iframe.po-session:not([hidden])';
      // A point in the conversation's messages (not its composer), or one of its own elements.
      const inChat = async (inner) => {
        const [x, y] = await evalIn(`(() => { const f = document.querySelector(${JSON.stringify(CHAT)}), a = f.getBoundingClientRect();
          const e = ${inner ? `f.contentDocument.querySelector(${JSON.stringify(inner)})` : 'null'};
          if (!e) return [a.left + a.width / 2, a.top + Math.min(160, a.height / 3)];
          const b = e.getBoundingClientRect(); return [a.left + b.left + b.width / 2, a.top + b.top + b.height / 2]; })()`);
        await press(x, y);
      };
      const tool = id => `#po-dock .dk-strip-btn[data-dk-auto="${id}"]`;
      const fly = () => evalIn('PD.dock.flyOpen()');
      const relays = () => evalIn('window.__relays');
      const state = async () => ({ fly: await fly(), relays: await relays(), menu: await evalIn('!!document.querySelector(".dk-menu")'),
        chatFocused: await evalIn(`document.activeElement === document.querySelector(${JSON.stringify(CHAT)})`) });
      await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
      await until('window.ensBooted === true', 30000);
      await evalIn(`(() => { try { ['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
        SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
      await until(`document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#po-dock .dk-strip-btn") && !!document.querySelector(${JSON.stringify(CHAT)})?.contentWindow?.eval("ROOM_OBJ")`, 30000);
      await sleep(500);
      // Count the page's relay (the presses Dock cannot see); it still works.
      await evalIn('window.__relays = 0; { const f = window.pdChatClicked; window.pdChatClicked = (win) => { window.__relays++; return f(win); }; } 0');
      const r = out[w] = {};
      const open = async (id) => { await click(tool(id)); await until(`PD.dock.flyOpen() === ${JSON.stringify(id)}`, 5000); await sleep(300); };
      // A tool opened from the strip, then a click into the conversation.
      await open('changes'); r.opened = await state();
      await inChat(); await sleep(400); r.chat = await state();
      // A click on the tool's own content keeps it; then the conversation.
      await click('#po-dock .dk-strip-btn[data-dk-auto="points"]'); await until('PD.dock.flyOpen() === "points"', 5000); await sleep(300);
      await click('#po-dock .dk-flyout.open .dk-body'); await sleep(400); r.own = await state();
      await inChat(); await sleep(400); r.chatAfterOwn = await state();
      // The ⋯ menu: the first click closes only the menu, the second puts the tool back.
      await open('board');
      await click('#po-dock .dk-flyout.open [data-dk-act="menu"]'); await sleep(300); r.menuOpen = await state();
      await inChat(); await sleep(400); r.menuFirst = await state();
      await inChat(); await sleep(400); r.menuSecond = await state();
      // The frame already has focus when a tool opens (a restored tool): Dock hears
      // nothing of the press, the relay does it.
      await inChat(); await sleep(300);
      await evalIn('PD.dock.openFly("spec"); 0'); await sleep(400); r.focusedOpen = await state();
      await inChat(); await sleep(400); r.focusedChat = await state();
      // Send on the first click while a tool is out: a draft typed in, the tool opened, Send.
      await evalIn(`(() => { const w = document.querySelector(${JSON.stringify(CHAT)}).contentWindow;
        w.eval(\`tick=()=>{}; refresh=async()=>{}; window.__sent=[]; const old=window.fetch;
          window.fetch=(url,opt)=>{if(String(url)==='/api/room/resume' && opt?.method==='POST'){
            window.__sent.push(JSON.parse(opt.body));return Promise.resolve(new Response(JSON.stringify({send:null}),{status:200,headers:{'Content-Type':'application/json'}}));}
            return old(url,opt);};\`); return 0; })()`);
      await inChat('#input'); await sleep(200);
      await c.send('Input.insertText', { text: 'First click with a tool out' }, sessionId); await sleep(300);
      await open('changes'); r.sendOpen = await state();
      await inChat('#send'); await sleep(500);
      r.send = { ...(await state()), sent: await evalIn(`document.querySelector(${JSON.stringify(CHAT)}).contentWindow.__sent.map(s => s.text)`) };
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.consoleErrors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class FrameClick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls, prefix="ens-frameclick-")
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task}
        script = base / "frame_click_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def sizes(self):
        for w, _ in SIZES:
            with self.subTest(w=w):
                yield self.got[str(w)]

    def test_a_click_into_the_conversation_puts_the_tool_back_by_dock(self):
        for g in self.sizes():
            self.assertEqual(g["opened"]["fly"], "changes", g)
            self.assertIsNone(g["chat"]["fly"], g)
            self.assertTrue(g["chat"]["chatFocused"], g)
            self.assertEqual(g["chat"]["relays"], 0, "Dock, not the page's relay")
            self.assertIsNone(g["chatAfterOwn"]["fly"], g)
            self.assertEqual(g["chatAfterOwn"]["relays"], 0, g)

    def test_a_click_on_the_tool_s_own_content_keeps_it(self):
        for g in self.sizes():
            self.assertEqual(g["own"]["fly"], "points", g)

    def test_the_first_click_closes_only_the_tool_s_menu(self):
        for g in self.sizes():
            self.assertEqual((g["menuOpen"]["fly"], g["menuOpen"]["menu"]), ("board", True), g)
            self.assertEqual((g["menuFirst"]["fly"], g["menuFirst"]["menu"]), ("board", False), g)
            self.assertIsNone(g["menuSecond"]["fly"], g)
            # The first click left focus in the frame, so Dock hears nothing of
            # the second: the page's relay puts the tool back.
            self.assertEqual(g["menuSecond"]["relays"] - g["menuFirst"]["relays"], 1, g)

    def test_a_press_dock_cannot_see_goes_through_close_fly(self):
        for g in self.sizes():
            self.assertEqual(g["focusedOpen"]["fly"], "spec", g)
            self.assertTrue(g["focusedOpen"]["chatFocused"], "the frame kept focus while the tool opened")
            self.assertIsNone(g["focusedChat"]["fly"], g)
            self.assertEqual(g["focusedChat"]["relays"] - g["focusedOpen"]["relays"], 1, g)

    def test_send_works_on_the_first_click_with_a_tool_out(self):
        for g in self.sizes():
            self.assertEqual(g["sendOpen"]["fly"], "changes", g)
            self.assertEqual(g["send"]["sent"], ["First click with a tool out"], g)
            self.assertIsNone(g["send"]["fly"], g)

    def test_no_console_errors(self):
        self.assertEqual(self.got["consoleErrors"], [])


if __name__ == "__main__":
    unittest.main()
