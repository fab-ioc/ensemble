"""P410 (the CEO's choice B): a hovered Inbox card's actions open in a side panel
just right of it, in headless Chrome over CDP (desktop hover and a fine
pointer), against a hub in a thread.

The hover test's PO chat (long and short questions). With real mouse moves and
clicks:

* the pointer on a card opens one panel 8px right of it, out of the Inbox, its
  actions in full words; the card shows no buttons;
* moving straight down onto the next card moves the panel to that card at once;
* moving from a card's text diagonally onto the panel's lowest button, across
  the next card, keeps the panel on its card (aimed at the panel), and so does
  a stop in the gap between the card and the panel;
* off both, it stays for the grace time, then goes; Esc closes it;
* pointing at the panel's Close this and earlier marks the rows it would close,
  as on the card; leaving it unmarks them;
* a real click on each panel button presses the hovered card's own button
  (Close, Close this and earlier, Done with this), and the real Close and Close
  this and earlier do what they do on the card (the row goes, with Undo; the
  card asks to confirm, its buttons in it, the panel gone);
* a repaint while the pointer is on the card or on the panel keeps the panel,
  on the same card;
* on a touch screen (no hover) the card shows its own buttons and no panel opens;
* the page threw nothing.

Set ENSEMBLE_SHOTS=<dir> for a screenshot of the panel beside its card at 260px.
"""
from __future__ import annotations

import unittest

import tests.test_inbox_close_cdp as close
import tests.test_inbox_hover_text_cdp as hover

NODE, CHROME = close.NODE, close.CHROME
_PRE = hover.CDP_JS[: hover.CDP_JS.index("async function main()")]

CDP_JS = _PRE + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const move = (x, y) => c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
  const click = async (x, y) => { await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, sessionId); };
  const key = async k => { for (const type of ['rawKeyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0 }, sessionId); };
  const shot = async (name, clip) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png', ...(clip ? { clip: { ...clip, scale: 1 } } : {}) }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  const frame = () => evalIn('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(0))))');
  await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
  await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
  await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
  await until('window.ensBooted === true', 30000);
  await evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const D = 'LD_INBOX';
  await until(`!!IBX.data && ${D}.querySelectorAll("li.ibx-row[data-q]").length >= 8 && ${D}.getBoundingClientRect().width > 0`, 30000);
  out.hoverMedia = await evalIn(`matchMedia('(hover: hover) and (pointer: fine)').matches`);
  out.width = await evalIn(`Math.round(${D}.getBoundingClientRect().width)`);
  const row = i => `${D}.querySelectorAll('li.ibx-row[data-q]')[${i}]`;
  const idOf = i => evalIn(`${row(i)}.dataset.ibx`);
  // The panel now: whose, where, its labels; and the card's own buttons.
  const st = `(() => { const sd = document.querySelectorAll('.ibx-side'), s = sd[0];
    const li = s && ${D}.querySelector('li.ibx-row[data-ibx="' + CSS.escape(IBX_SIDE.id) + '"]'), ib = ${D}.getBoundingClientRect();
    const r = s && s.getBoundingClientRect(), cr = li && li.getBoundingClientRect();
    return { n: sd.length, id: s ? IBX_SIDE.id : null, gap: s && li ? Math.round(r.left - cr.right) : null, outInbox: !!s && r.left >= ib.right - 0.5,
      labels: s ? [...s.querySelectorAll('.ibx-side-btn')].map(b => [...b.children].map(e => e.textContent.trim()).join(' ')) : [],
      marked: ${D}.querySelectorAll('li.ibx-row.ibx-mark').length,
      shown: [...${D}.querySelectorAll('.ibx-listed li.ibx-row[data-q] > .ibx-acts')].filter(a => getComputedStyle(a).display !== 'none').length }; })()`;
  const textAt = async i => evalIn(`(() => { const li = ${row(i)}; li.scrollIntoView({ block: 'nearest' });
    const b = li.querySelector('.ibx-line').getBoundingClientRect(); return [Math.round(b.left + 30), Math.round(b.top + 6)]; })()`);
  const btnAt = k => evalIn(`(() => { const b = document.querySelectorAll('.ibx-side .ibx-side-btn')[${k}].getBoundingClientRect(); return [Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2)]; })()`);
  const away = async () => { await move(5, 5); await sleep(450); await frame(); };
  const repaint = `(() => { for (const k of Object.keys(IBX)) if (/Sig$/.test(k)) IBX[k] = ''; ibxPolledPaint(); return 0; })()`;
  try {
    // A card under the pointer: its panel.
    const id0 = await idOf(0), id1 = await idOf(1);
    // Give the first question the same Done with this action as a clearable Needs you room.
    // The test can then exercise all three actions regardless of the fixture's derived clear list.
    await evalIn(`(() => { const room = IBX.data.rows.find(r => r.id === ${JSON.stringify(id0)}).roomId, old = ibxClearKeys;
      ibxClearKeys = (...a) => { const keys = old(...a); if (!keys.needs.includes(room)) keys.needs.push(room); return keys; };
      IBX.ldSig = ''; ibxRepaint(); return 0; })()`);
    let [x, y] = await textAt(0);
    await away();
    out.before = await evalIn(st);
    await move(x, y); await sleep(100); await frame();
    out.open = await evalIn(st);
    out.open.want = id0;
    await shot('p410-side-panel-260', await evalIn(`(() => { const b = ${row(0)}.getBoundingClientRect(), s = document.querySelector('.ibx-side').getBoundingClientRect(), ib = ${D}.getBoundingClientRect();
      const b2 = ${row(2)}.getBoundingClientRect(), top = Math.max(0, ib.top);
      return { x: Math.max(0, ib.left), y: top, width: s.right - ib.left + 160, height: Math.max(b2.bottom, s.bottom) - top + 12 }; })()`));
    // Straight down onto the next card: the panel moves there at once.
    const [x1, y1] = await textAt(1);
    for (let k = 1; k <= 4; k++) { await move(x, Math.round(y + (y1 - y) * k / 4)); await sleep(16); }
    await frame();
    out.down = await evalIn(st);
    out.down.want = id1;
    // Back on card 0 (its foot: the panel is taller than the card), then diagonally onto its panel's lowest
    // button, across card 1.
    await away();
    [x, y] = await textAt(0);
    const [fx, fy] = await evalIn(`(() => { const r = ${row(0)}.getBoundingClientRect(); return [Math.round(r.left + 30), Math.round(r.bottom - 4)]; })()`);
    await move(fx, fy); await sleep(100); await frame();
    const nb = await evalIn(`document.querySelectorAll('.ibx-side .ibx-side-btn').length`);
    const [tx, ty] = await btnAt(nb - 1);
    const path = [];
    for (let k = 1; k <= 12; k++) {
      const px = Math.round(fx + (tx - fx) * k / 12), py = Math.round(fy + (ty - fy) * k / 12);
      const on = await evalIn(`(() => { const e = document.elementFromPoint(${px}, ${py}), li = e && e.closest('li.ibx-row[data-ibx]'); return e && e.closest('.ibx-side') ? 'side' : li ? li.dataset.ibx : (e && e.closest('.row-expanded') ? 'copy' : 'other'); })()`);
      await move(px, py); await sleep(16);
      path.push({ on, side: (await evalIn(st)).id });
    }
    await sleep(400); await frame();
    out.aim = { path, end: await evalIn(st), want: id0, id1, geo: await evalIn(`[...${D}.querySelectorAll('li.ibx-row[data-q]')].slice(0, 8).map(l => { const r = l.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.bottom)]; }).concat([[0, Math.round(document.querySelector('.ibx-side') ? document.querySelector('.ibx-side').getBoundingClientRect().height : 0)]])`) };
    // From the card's right edge, a stop in the gap: the panel stays past the grace once the pointer is on it.
    await away();
    await move(x, y); await sleep(100); await frame();
    const [gx, gy] = await evalIn(`(() => { const r = ${row(0)}.getBoundingClientRect(); return [Math.round(r.right + 4), Math.round(r.top + 10)]; })()`);
    await move(gx, gy); await sleep(150); await frame();
    const inGap = await evalIn(st);
    const [p0x, p0y] = await btnAt(0);
    await move(p0x, p0y); await sleep(450); await frame();
    out.gap = { inGap, onPanel: await evalIn(st), want: id0 };
    // A repaint with the pointer on the panel, then on the card: the panel stays, on its card.
    await evalIn(repaint); await sleep(100); await frame();
    out.repaintPanel = await evalIn(st);
    await move(x, y); await sleep(100); await frame();
    await evalIn(repaint); await sleep(100); await frame();
    out.repaintCard = await evalIn(st);
    out.repaintCard.connected = await evalIn(`!!document.querySelector('.ibx-side') && document.querySelector('.ibx-side').isConnected && document.querySelectorAll('.ibx-side').length === 1`);
    // Off both: there for the grace, then gone.
    await move(5, 5); await sleep(120);
    out.leaveSoon = await evalIn(st);
    await sleep(400); await frame();
    out.leaveLate = await evalIn(st);
    // Esc closes it.
    await move(x, y); await sleep(100); await frame();
    const escBefore = await evalIn(st);
    await key('Escape'); await frame();
    out.esc = { before: escBefore, after: await evalIn(st) };
    // Pointing at Close this and earlier marks what it would close; leaving it unmarks.
    await away();
    await move(x, y); await sleep(100); await frame();
    const ei = (await evalIn(st)).labels.findIndex(l => /^Close this and earlier/.test(l));
    const [ex, ey] = await btnAt(ei);
    await move(ex, ey); await sleep(100); await frame();
    const onEarlier = await evalIn(st);
    const [cx, cy] = await btnAt(0);
    await move(cx, cy); await sleep(100); await frame();
    out.mark = { ei, onEarlier, offEarlier: await evalIn(st), rows: await evalIn(`${D}.querySelectorAll('li.ibx-row[data-q]').length`) };
    // A real click on each panel button presses the hovered card's own (held here, so nothing is sent).
    await evalIn(`(() => { window.__p410 = []; window.__p410f = ev => { const b = ev.target.closest && ev.target.closest('.ibx-btn'); if (!b) return;
      const li = b.closest('li.ibx-row[data-ibx]');
      window.__p410.push({ row: li ? li.dataset.ibx : null, inActs: !!b.closest('li.ibx-row > .ibx-acts'), key: ['ibxClose', 'ibxEarlier', 'ibxDone', 'ibxDismiss'].find(k => k in b.dataset) || '' });
      ev.preventDefault(); ev.stopImmediatePropagation(); };
      window.addEventListener('click', window.__p410f, true); return 0; })()`);
    out.clicks = [];
    for (let k = 0; k < nb; k++) {
      await away();
      await move(x, y); await sleep(100); await frame();
      const s0 = await evalIn(st);
      const [bx, by] = await btnAt(k);
      await move(bx, by); await sleep(60);
      await click(bx, by); await sleep(80); await frame();
      out.clicks.push({ k, label: s0.labels[k], got: await evalIn('window.__p410.splice(0)'), after: await evalIn(st), want: id0 });
    }
    await evalIn(`(() => { window.removeEventListener('click', window.__p410f, true); return 0; })()`);
    // The real Close this and earlier: the card asks to confirm, its buttons in it, the panel gone. Then Cancel.
    await away();
    [x, y] = await textAt(2);
    const id2 = await idOf(2);
    await move(x, y); await sleep(100); await frame();
    const ei2 = (await evalIn(st)).labels.findIndex(l => /^Close this and earlier/.test(l));
    let [bx, by] = await btnAt(ei2);
    await move(bx, by); await sleep(60); await click(bx, by); await sleep(150); await frame();
    out.earlier = { want: id2, confirm: await evalIn('IBX.confirm'), confirming: await evalIn(`${D}.querySelector('li.ibx-row[data-ibx="' + CSS.escape(${JSON.stringify(id2)}) + '"]').classList.contains('ibx-confirming')`),
      after: await evalIn(st) };
    await evalIn(`(() => { ${D}.querySelector('[data-ibx-cancel]').click(); return 0; })()`); await frame();
    out.earlier.cancelled = await evalIn('!IBX.confirm');
    // The real Close: the row goes, with Undo (the hub is told once the undo time is over).
    await away();
    [x, y] = await textAt(3);
    const id3 = await idOf(3);
    await move(x, y); await sleep(100); await frame();
    [bx, by] = await btnAt(0);
    await move(bx, by); await sleep(60); await click(bx, by); await sleep(150); await frame();
    out.close = { want: id3, gone: await evalIn(`IBX.gone.has(${JSON.stringify(id3)})`), after: await evalIn(st) };
    await evalIn(`(() => { const u = ${D}.querySelector('[data-ibx-undo="' + CSS.escape(${JSON.stringify(id3)}) + '"]'); if (u) u.click(); return !!u; })()`).then(v => { out.close.undo = v; });
    await frame();
    // A touch screen: no hover. The card shows its buttons; neither a touch nor the mouse opens a panel.
    await away();
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 }, sessionId);
    await c.send('Emulation.setEmitTouchEventsForMouse', { enabled: true, configuration: 'mobile' }, sessionId);
    await sleep(100); await frame();
    out.touch = { hover: await evalIn(`matchMedia('(hover: hover)').matches`) };
    [x, y] = await textAt(0);
    await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }, sessionId);
    await sleep(60); await frame();
    out.touch.duringTouch = await evalIn(st);
    await c.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] }, sessionId);
    await move(x, y); await sleep(150); await frame();
    out.touch.mouse = await evalIn(st);
    out.touch.cards = await evalIn(`${D}.querySelectorAll('.ibx-listed li.ibx-row[data-q]').length`);
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class SidePanelInChrome(close.CloseInChrome):
    """The hover test's hub and PO chat, with this test's script."""

    @classmethod
    def setUpClass(cls):
        real, older = close.CDP_JS, close._older
        close.CDP_JS, close._older = CDP_JS, hover._mixed
        try:
            super().setUpClass()
        finally:
            close.CDP_JS, close._older = real, older

    def test_the_page_is_what_is_measured(self):
        self.assertTrue(self.got["hoverMedia"], "desktop hover")
        self.assertEqual(self.got["width"], 260)

    def test_a_card_under_the_pointer_opens_its_panel_beside_it(self):
        b, o = self.got["before"], self.got["open"]
        self.assertEqual(b["n"], 0, b)
        self.assertEqual(b["shown"], 0, ("with a mouse the cards show no buttons", b))
        self.assertEqual(o["n"], 1, o)
        self.assertEqual(o["id"], o["want"], o)
        self.assertEqual(o["gap"], 8, o)
        self.assertTrue(o["outInbox"], o)
        self.assertEqual(o["shown"], 0, o)
        self.assertEqual(o["labels"][0], "Close", o)
        self.assertRegex(o["labels"][1], r"^Close this and earlier \d+$")
        self.assertEqual(o["labels"][-1], "Done with this", o)

    def test_straight_down_onto_the_next_card_moves_the_panel(self):
        d = self.got["down"]
        self.assertEqual((d["n"], d["id"]), (1, d["want"]), d)

    def test_a_move_aimed_at_the_panel_keeps_it_across_the_next_card(self):
        a = self.got["aim"]
        crossed = [p for p in a["path"] if p["on"] not in (a["want"], "side", "other", "copy")]
        self.assertTrue(crossed, ("the way crosses another card", a["geo"]))
        self.assertTrue(all(p["side"] == a["want"] for p in a["path"]), a["path"])
        self.assertEqual(a["path"][-1]["on"], "side", a["path"])
        self.assertEqual(a["end"]["id"], a["want"], a)

    def test_a_stop_in_the_gap_keeps_the_panel_and_on_it_it_stays(self):
        g = self.got["gap"]
        self.assertEqual(g["inGap"]["id"], g["want"], g)
        self.assertEqual(g["onPanel"]["id"], g["want"], g)

    def test_a_repaint_keeps_the_panel_on_its_card(self):
        for s in (self.got["repaintPanel"], self.got["repaintCard"]):
            self.assertEqual(s["n"], 1, s)
            self.assertEqual(s["id"], self.got["open"]["want"], s)
            self.assertEqual(s["gap"], 8, s)
        self.assertTrue(self.got["repaintCard"]["connected"])

    def test_off_both_it_stays_for_the_grace_then_goes(self):
        self.assertEqual(self.got["leaveSoon"]["n"], 1, self.got["leaveSoon"])
        self.assertEqual(self.got["leaveLate"]["n"], 0, self.got["leaveLate"])

    def test_esc_closes_it(self):
        e = self.got["esc"]
        self.assertEqual((e["before"]["n"], e["after"]["n"]), (1, 0), e)

    def test_pointing_at_close_this_and_earlier_marks_the_rows(self):
        m = self.got["mark"]
        self.assertGreaterEqual(m["ei"], 1, m)
        self.assertGreater(m["onEarlier"]["marked"], 1, m)
        self.assertEqual(m["offEarlier"]["marked"], 0, m)

    def test_each_panel_button_presses_the_card_s_own(self):
        cl = self.got["clicks"]
        self.assertEqual([x["label"].split(" ")[0] for x in cl], ["Close", "Close", "Done"], cl)
        want = ["ibxClose", "ibxEarlier", ("ibxDone", "ibxDismiss")]
        for x, w in zip(cl, want):
            self.assertEqual(len(x["got"]), 1, x)
            g = x["got"][0]
            self.assertEqual(g["row"], x["want"], x)
            self.assertTrue(g["inActs"], x)
            self.assertIn(g["key"], w if isinstance(w, tuple) else (w,), x)
            self.assertEqual(x["after"]["n"], 0, ("the panel goes with the press", x))

    def test_the_real_close_this_and_earlier_asks_to_confirm_on_the_card(self):
        e = self.got["earlier"]
        self.assertEqual(e["confirm"], e["want"], e)
        self.assertTrue(e["confirming"], e)
        self.assertEqual(e["after"]["n"], 0, e)
        self.assertEqual(e["after"]["shown"], 1, ("the confirming card shows its own buttons", e))
        self.assertTrue(e["cancelled"], e)

    def test_the_real_close_takes_the_row_with_undo(self):
        c = self.got["close"]
        self.assertTrue(c["gone"], c)
        self.assertEqual(c["after"]["n"], 0, c)
        self.assertTrue(c["undo"], c)

    def test_a_touch_screen_keeps_the_card_s_buttons_and_no_panel(self):
        t = self.got["touch"]
        self.assertFalse(t["hover"], t)
        for s in (t["duringTouch"], t["mouse"]):
            self.assertEqual(s["n"], 0, s)
            self.assertEqual(s["shown"], t["cards"], s)

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


# Only this file's tests: the base class's are run by its own module.
for _n in [n for n in dir(close.CloseInChrome) if n.startswith("test_") and n not in SidePanelInChrome.__dict__]:
    setattr(SidePanelInChrome, _n, None)
del _n

if __name__ == "__main__":
    unittest.main()
