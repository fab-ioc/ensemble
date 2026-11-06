"""Layout A step 5 (#307): on a phone the conversation is home, and the Inbox
and the project switcher open one pane at a time over it. In headless Chrome
over CDP (mobile + touch), against a hub in a thread serving the pages.

The fixtures of test_desktop_layout_cdp: Motors (a PO with an answer to check,
so one Inbox row), Wheels (a PO), Alpha (no PO), and a task in no project.
At 360x740, 390x844, 430x932 and 440x956, a fresh browser:

* opens on a PO's conversation, ← hidden; the PO screen's tabs start
  Chat | Inbox (with its count) and the bar has no Inbox of its own (#311);
* the Inbox tab shows the Inbox full width, Chat brings the conversation
  back; a row of it opens its conversation, the Chat tab in front;
* the switcher is a full-width pane; a project in it opens its PO;
  switcher › Task list shows the list pane; a task in it opens, and ← goes
  back to the list;
* a project with no PO opens its board, ← goes home (the last conversation:
  the task opened from the list); Unassigned lists the task in no project;
* with the keyboard up (--vv-h shrunk) on that task's conversation the
  composer stays in the frame, above the keyboard, and the last message above it;
* nothing is wider than the screen, the bar's targets are 44px, the filter
  pills (when shown) fit or scroll in their own row; the page threw nothing.

Measures go to $ENSEMBLE_SHOTS/phone-home.json (and screenshots) when set.
Skipped without Node or Chrome.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import test_desktop_layout_cdp as dl  # noqa: E402

SIZES = [(360, 740), (390, 844), (430, 932), (440, 956)]

HEAD = dl.CDP_JS[:dl.CDP_JS.index("// Where things are")]
CDP_JS = HEAD + r"""
const LOOK = `(() => {
  const B = e => { if (!e || !e.isConnected) return null; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { y: Math.round(r.top), b: Math.round(r.bottom), x: Math.round(r.left), r: Math.round(r.right), h: Math.round(r.height), w: Math.round(r.width) } : null; };
  const fr = [...document.querySelectorAll('#po-panel iframe.po-session, #detail-panel iframe.dp-session')].find(f => B(f));
  let inner = null;
  if (fr) { try { const d = fr.contentDocument, o = B(fr);
    const rel = e => { const b = B(e); return b && { y: b.y + o.y, b: b.b + o.y, h: b.h, w: b.w }; };
    const cf = d.getElementById('chat-filters');
    inner = { compose: rel(d.getElementById('compose')), lastMsg: rel([...d.querySelectorAll('#msgs .msg')].pop()),
      pills: rel(cf), pillsScrollW: cf ? cf.scrollWidth : 0, pillsClientW: cf ? cf.clientWidth : 0,
      pillsOverflow: cf ? getComputedStyle(cf).overflowX : '' }; } catch (e) { inner = String(e); } }
  const bar = [...document.querySelectorAll('header button, header a')].filter(e => B(e) && !e.closest('#proj-menu'));
  return { vw: innerWidth, vh: innerHeight, scrollW: document.documentElement.scrollWidth,
    header: B(document.querySelector('header')), po: document.body.classList.contains('po-dock'), pid: (PD && PD.pid) || '',
    proj: SELECTED_PROJECT, open: document.body.classList.contains('detail-open'), pane: PH_PANE, switchOpen: document.body.classList.contains('ph-switch'),
    back: B(document.getElementById('bar-back')), inboxBtn: B(document.getElementById('inbox-btn')),
    inboxText: document.getElementById('inbox-btn').textContent.trim(), inboxExpanded: document.getElementById('inbox-btn').getAttribute('aria-expanded'),
    tabs: [...document.querySelectorAll('#po-dock .dk-tab')].filter(e => B(e)).map(e => e.textContent.trim()),
    inboxPane: B(document.getElementById('ph-inbox')), inboxRows: document.querySelectorAll('#ph-inbox-body .ibx-row').length,
    inboxTab: !!PD.dock && PD.dock.isShown('inbox'), chatTab: !!PD.dock && PD.dock.isShown('po-chat'),
    inboxBox: PD.dock && PD.dock.isShown('inbox') ? B(PD.els.inbox) : null, tabRows: PD.els.inbox.querySelectorAll('.ibx-row').length,
    menu: B(document.getElementById('proj-menu')), list: B(document.getElementById('switcher')),
    small: bar.map(e => [e.id || e.className || e.textContent.trim().slice(0, 12), B(e).w, B(e).h]).filter(t => t[2] < 44),
    frame: fr ? B(fr) : null, inner,
    vvh: parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vv-h')) || 0,
    text: document.body.innerText.includes(A_LOOSE) };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const P = A.ids;
  const look = A.looseTitle;
  try {
    for (const [w, h] of A.widths) {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId); await c.send('Runtime.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: true }, sessionId);
      await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      // A fresh browser: no last conversation, the page boots as it would.
      await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.A_LOOSE = ' + JSON.stringify(look) + '; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); ["cd-last-conv", "cd-list-dock", "cd-list-dock-a4", "cd-phone-tabs"].forEach(k => localStorage.removeItem(k)); } } catch (e) {}' }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const tryUntil = (expr, ms) => until(expr, ms).catch(() => null);
      // A real tap where the element is.
      const tap = async (expr) => {
        const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to tap'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(20, r.width / 2), r.top + r.height / 2]; })()`);
        for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
      };
      const shot = async (n) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, `phone-${w}-${n}.png`), Buffer.from(r.data, 'base64')); };
      const o = {};
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('window.ensBooted === true && typeof ALL_ROWS !== "undefined" && ALL_ROWS.length > 0', 30000);
      await tryUntil('document.body.classList.contains("po-dock") && !!document.querySelector("#po-panel iframe.po-session") && !!IBX.data', 30000);
      await sleep(1500);
      o.home = await evalIn(LOOK); await shot('home');
      // The Inbox tab (#311); Chat brings the conversation back.
      const TAB = id => `document.querySelector('#po-dock .dk-tab[data-dk-tab="${id}"]')`;
      await tap(TAB('inbox'));
      await tryUntil('PD.dock.isShown("inbox") && !!PD.els.inbox.querySelector(".ibx-row, .ibx-others")', 8000);
      o.inbox = await evalIn(LOOK); await shot('inbox');
      // Home's project has nothing: its empty line offers the other projects'.
      o.others = await evalIn('(PD.els.inbox.querySelector(".ibx-others") || {}).textContent || ""');
      if (o.others) {
        await tap('PD.els.inbox.querySelector(".ibx-others")');
        await tryUntil('PD.els.inbox.querySelectorAll(".ibx-row").length > 0', 8000);
        o.inboxAll = await evalIn(LOOK);
      }
      await tap(TAB('po-chat')); await sleep(300);
      o.inboxClosed = await evalIn(LOOK);
      // A row of it: its conversation, Chat in front. Opening the answer
      // settles the only row, so at the last size only.
      if (w === A.widths[A.widths.length - 1][0]) {
        await tap(TAB('inbox'));
        await tryUntil('PD.dock.isShown("inbox") && PD.els.inbox.querySelectorAll(".ibx-link").length > 0', 8000);
        if (!(await evalIn('PD.els.inbox.querySelectorAll(".ibx-link").length'))) {   // home is Wheels: All
          await tap('PD.els.inbox.querySelector(".ibx-others")');
          await tryUntil('PD.els.inbox.querySelectorAll(".ibx-link").length > 0', 8000);
        }
        await tap('PD.els.inbox.querySelector(".ibx-link")');
        await tryUntil('document.body.classList.contains("po-dock") && PD.pid === ' + JSON.stringify(P.Motors) + ' && PD.dock.isShown("po-chat")', 15000);
        await sleep(300);
        o.inboxRow = await evalIn(LOOK);
      }
      // The switcher: a full-width pane; Wheels opens its PO.
      await tap('document.getElementById("proj-switch")'); await sleep(300);
      o.switcher = await evalIn(LOOK); await shot('switcher');
      await tap(`document.querySelector('#proj-menu .pm-any[data-proj="${P.Wheels}"]')`);
      await tryUntil('!document.body.classList.contains("ph-switch") && document.body.classList.contains("po-dock") && PD.pid === ' + JSON.stringify(P.Wheels), 15000);
      await sleep(300);
      o.wheels = await evalIn(LOOK);
      // Switcher › Task list: the list pane; a task opens; ← back to the list.
      await tap('document.getElementById("proj-switch")'); await sleep(300);
      await tap('document.querySelector("#proj-menu [data-ph-list]")'); await sleep(400);
      o.list = await evalIn(LOOK); await shot('list');
      o.listRooms = await evalIn(`[...document.querySelectorAll('#sw-list .sw-row[data-room]')].map(e => e.dataset.room)`);
      const roomSel = `[...document.querySelectorAll('#sw-list .sw-row[data-room]')].find(e => e.dataset.room === ${JSON.stringify(A.loose)}) || [...document.querySelectorAll('#sw-list .sw-row[data-room]')].find(e => e.offsetParent)`;
      o.listRow = await evalIn(`(() => { const e = ${roomSel}; return e ? e.dataset.room : null; })()`);
      if (o.listRow) {
        await tap(roomSel);
        await tryUntil('document.body.classList.contains("detail-open") && !!document.querySelector("#detail-panel iframe.dp-session")', 15000);
        await sleep(500);
        o.task = await evalIn(LOOK); await shot('task');
        await tap('document.getElementById("bar-back")'); await sleep(400);
        o.taskBack = await evalIn(LOOK);
        await tap('document.querySelector("#switcher [data-ph-close]")'); await sleep(300);
      }
      // A project with no PO: its board; ← home.
      await tap('document.getElementById("proj-switch")'); await sleep(300);
      await tap(`document.querySelector('#proj-menu .pm-any[data-proj="${P.Alpha}"]')`); await sleep(800);
      o.alpha = await evalIn(LOOK);
      await tap('document.getElementById("bar-back")');
      await tryUntil('document.body.classList.contains("po-dock")', 15000); await sleep(300);
      o.alphaBack = await evalIn(LOOK);
      // Unassigned: the task in no project.
      await tap('document.getElementById("proj-switch")'); await sleep(300);
      o.unassignedItem = await evalIn(`!!document.querySelector('#proj-menu .pm-any[data-proj="__unassigned__"]')`);
      if (o.unassignedItem) {
        await tap(`document.querySelector('#proj-menu .pm-any[data-proj="__unassigned__"]')`); await sleep(800);
        o.unassigned = await evalIn(LOOK);
      } else await evalIn('closeProjMenu(); 0');
      // The keyboard up: home, --vv-h shrunk.
      await evalIn('goHome(); 0');
      await tryUntil('document.body.classList.contains("po-dock") && !!document.querySelector("#po-panel iframe.po-session")', 15000); await sleep(800);
      await evalIn(`document.documentElement.style.setProperty('--vv-h', '${h - 340}px'); 0`); await sleep(500);
      o.kbd = await evalIn(LOOK); await shot('kbd');
      // And on a PO's conversation.
      await evalIn(`closeDetail(); openPoOf(projectById(${JSON.stringify(P.Wheels)})); 0`);
      await tryUntil('document.body.classList.contains("po-dock") && !document.body.classList.contains("detail-open") && !!document.querySelector("#po-panel iframe.po-session") && PD.pid === ' + JSON.stringify(P.Wheels), 15000); await sleep(800);
      o.kbdPo = await evalIn(LOOK); await shot('kbd-po');
      await evalIn(`document.documentElement.style.removeProperty('--vv-h'); 0`);
      out[w] = o;
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'phone-home.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(dl.NODE and dl.CHROME, "needs node and Chrome")
class PhoneHomeInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # test_desktop_layout_cdp's fixtures and hub, running this script.
        with mock.patch.object(dl, "CDP_JS", CDP_JS), mock.patch.object(dl, "WIDTHS", SIZES):
            dl.DesktopLayoutInChrome.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        dl.DesktopLayoutInChrome.tearDownClass.__func__(cls)

    def each(self):
        return [(w, h, self.got[str(w)]) for w, h in SIZES]

    def test_home_is_a_po_conversation(self):
        for w, _, o in self.each():
            g = o["home"]
            self.assertTrue(g["po"], g)
            self.assertIn(g["pid"], (self.ids["Motors"], self.ids["Wheels"]))
            self.assertIsNone(g["back"], "no ← at home")
            self.assertFalse(g["open"])
            self.assertEqual(g["pane"], "")

    def test_the_inbox_is_a_tab_beside_chat(self):
        for w, _, o in self.each():
            g = o["home"]
            self.assertIsNone(g["inboxBtn"], "the tab is the Inbox: none in the bar (#311)")
            self.assertEqual(g["tabs"][0], "Chat", g["tabs"])
            self.assertTrue(g["tabs"][1].startswith("Inbox"), g["tabs"])
            self.assertTrue(g["chatTab"] and not g["inboxTab"], g)

    def test_the_inbox_tab(self):
        for w, _, o in self.each():
            g = o["inbox"]
            self.assertTrue(g["inboxTab"], g)
            self.assertEqual(g["pane"], "", "the tab, not #307's pane")
            self.assertIsNone(g["inboxPane"])
            self.assertIsNotNone(g["inboxBox"], g)
            self.assertGreaterEqual(g["inboxBox"]["y"], g["header"]["b"] - 1, "under the bar")
            self.assertEqual(g["inboxBox"]["w"], w)
            if o["others"]:   # home's project has none: one tap to the other projects'
                self.assertEqual(g["tabRows"], 0)
                self.assertEqual(g["pid"], self.ids["Wheels"])
                self.assertIn("1 in other", o["others"])
                self.assertEqual(o["inboxAll"]["tabRows"], 1)
            else:
                self.assertEqual(g["tabRows"], 1)
            c = o["inboxClosed"]
            self.assertTrue(c["chatTab"] and not c["inboxTab"], c)
            self.assertEqual(c["pane"], "")

    def test_an_inbox_row_opens_its_conversation(self):
        g = self.got[str(SIZES[-1][0])]["inboxRow"]
        if True:
            self.assertTrue(g["po"])
            self.assertEqual(g["pid"], self.ids["Motors"])
            self.assertTrue(g["chatTab"] and not g["inboxTab"], g)
            self.assertIsNone(g["inboxPane"])
            self.assertEqual(g["pane"], "")

    def test_the_switcher_is_a_pane(self):
        for w, _, o in self.each():
            g = o["switcher"]
            self.assertTrue(g["switchOpen"])
            self.assertIsNotNone(g["menu"], g)
            self.assertEqual(g["menu"]["x"], 0)
            self.assertEqual(g["menu"]["w"], w)
            self.assertGreaterEqual(g["menu"]["y"], g["header"]["b"] - 1)
            p = o["wheels"]
            self.assertTrue(p["po"])
            self.assertEqual(p["pid"], self.ids["Wheels"])
            self.assertFalse(p["switchOpen"])

    def test_the_task_list_is_a_pane_and_back_returns_to_it(self):
        for w, _, o in self.each():
            g = o["list"]
            self.assertEqual(g["pane"], "list")
            self.assertIsNotNone(g["list"], g)
            self.assertEqual(g["list"]["w"], w)
            self.assertIsNotNone(o["listRow"], o["listRooms"])
            t = o["task"]
            self.assertTrue(t["open"], t)
            self.assertEqual(t["pane"], "")
            self.assertIsNone(t["list"], "the list steps aside")
            self.assertIsNotNone(t["back"])
            b = o["taskBack"]
            self.assertFalse(b["open"])
            self.assertEqual(b["pane"], "list", "← goes back to the list")
            self.assertIsNotNone(b["list"])

    def test_a_project_with_no_po_and_back_home(self):
        for w, _, o in self.each():
            g = o["alpha"]
            self.assertEqual(g["proj"], self.ids["Alpha"])
            self.assertFalse(g["po"])
            self.assertIsNotNone(g["back"], "← goes home")
            # home: the last conversation, here the task opened from the list
            hb = o["alphaBack"]
            self.assertTrue(hb["open"], hb)
            self.assertNotEqual(hb["proj"], self.ids["Alpha"])

    def test_tasks_in_no_project_are_reachable(self):
        for w, _, o in self.each():
            self.assertTrue(o["unassignedItem"])
            self.assertEqual(o["unassigned"]["proj"], "__unassigned__")
            self.assertTrue(o["unassigned"]["text"], "the task in no project is listed")

    def test_the_keyboard_keeps_the_composer_and_last_message(self):
        for (w, h, o), k in ((x, k) for x in self.each() for k in ("kbd", "kbdPo")):
            g = o[k]
            self.assertEqual(g["vvh"], h - 340)
            self.assertIsNotNone(g["frame"], g)
            self.assertLessEqual(g["frame"]["b"], h - 340 + 1)
            i = g["inner"]
            self.assertIsInstance(i, dict, i)
            self.assertIsNotNone(i["compose"], i)
            self.assertLessEqual(i["compose"]["b"], g["frame"]["b"] + 1)
            self.assertGreaterEqual(i["compose"]["y"], g["frame"]["y"])
            if i["lastMsg"]:
                self.assertLessEqual(i["lastMsg"]["b"], i["compose"]["y"] + 1)

    def test_nothing_wider_than_the_screen_and_targets_44(self):
        for w, _, o in self.each():
            for k in ("home", "inbox", "switcher", "list", "task", "alpha", "kbd", "kbdPo"):
                g = o.get(k)
                if not g:
                    continue
                self.assertLessEqual(g["scrollW"], w, k)
                self.assertEqual(g["small"], [], f"{k}: bar targets under 44px")

    def test_filter_pills_fit_or_scroll_in_their_row(self):
        for w, _, o in self.each():
            for k in ("home", "wheels", "kbdPo"):
                i = o[k]["inner"]
                if not isinstance(i, dict) or not i["pills"]:
                    continue
                self.assertLessEqual(i["pills"]["w"], w)
                if i["pillsScrollW"] > i["pillsClientW"]:
                    self.assertEqual(i["pillsOverflow"], "auto", k)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
