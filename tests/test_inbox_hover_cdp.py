"""The Inbox stays still under the mouse (#315), in headless Chrome over CDP,
against a hub in a thread serving the pages.

A PO chat with eight open questions, so the docked Inbox has eight rows. At
1728x1117 and at 1280x800 the pointer moves down the list in 4 px steps, once
over the rows' words (near their left edge) and once along their right edge
(through Close and Close this and earlier, which marks the rows below). At
every step each row's top and height are read; checked:

* no row moves or changes height at any step, against where it stood before
  the pointer came (the count of steps where one did is printed);
* the row under the pointer shows its actions, inside the row;
* the actions still show by keyboard (focus in the row) and in the confirm
  state, where the row grows to ask (a click, not a hover);
* the page threw nothing meanwhile.

Set ENSEMBLE_HOVER_LOG=1 to print the counts. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import tests.test_inbox_close_cdp as close

_CLOSE, NODE, CHROME = close.CDP_JS, close.NODE, close.CHROME

# The launcher and Cdp class of test_inbox_cdp (through test_inbox_close_cdp), with this test's main().
CDP_JS = _CLOSE[: _CLOSE.index("async function main()")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const move = (x, y) => c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
    const clickAt = async (x, y) => { await move(x, y); for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { ["cd-tool-strip", "cd-tool-open", "cd-phone-tabs", "cd-inbox-scope"].forEach(k => localStorage.removeItem(k)); } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, move, clickAt, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const D = 'LD_INBOX';
  // Every row's top and height, and the row under (x, y) with its actions.
  const read = (x, y) => `(() => { const rows = [...${D}.querySelectorAll('li.ibx-row[data-ibx]')];
    const r = rows.map(li => { const b = li.getBoundingClientRect(); return [Math.round(b.top * 10) / 10, Math.round(b.height * 10) / 10]; });
    const e = document.elementFromPoint(${x}, ${y}); const li = e && e.closest('li.ibx-row[data-ibx]');
    let acts = null;
    if (li) { const a = li.querySelector('.ibx-acts'); if (a) { const ab = a.getBoundingClientRect(), lb = li.getBoundingClientRect();
      acts = { shown: getComputedStyle(a).display !== 'none' && ab.height > 0, inRow: ab.top >= lb.top - 0.5 && ab.bottom <= lb.bottom + 0.5 && ab.right <= lb.right + 0.5 }; } }
    return { r, at: li ? rows.indexOf(li) : -1, acts }; })()`;
  const frame = p => p.evalIn('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(0))))');
  const sweep = async (p, side) => {
    const g = await p.evalIn(`(() => { const box = ${D}.getBoundingClientRect(); const rows = [...${D}.querySelectorAll('li.ibx-row[data-ibx]')];
      const f = rows[0].getBoundingClientRect(), l = rows[rows.length - 1].getBoundingClientRect();
      return { left: f.left, right: f.right, top: Math.max(f.top, box.top), bottom: Math.min(l.bottom, box.bottom) }; })()`);
    const x = side === 'right' ? Math.round(g.right - 12) : Math.round(g.left + 40);
    await p.move(5, 5); await frame(p);
    const base = (await p.evalIn(read(5, 5))).r;
    let changed = 0, steps = 0, hovered = 0, shown = 0, inRow = 0, prev = base, jumps = 0;
    const bad = [];
    for (let y = Math.ceil(g.top) + 1; y < g.bottom - 1; y += 4) {
      await p.move(x, y); await frame(p);
      const s = await p.evalIn(read(x, y));
      steps++;
      if (JSON.stringify(s.r) !== JSON.stringify(base)) { changed++; if (bad.length < 3) bad.push({ y, r: s.r }); }
      if (JSON.stringify(s.r) !== JSON.stringify(prev)) jumps++;
      prev = s.r;
      if (s.at === 2 && !sweep.shot) { sweep.shot = 1; await p.shot('hover-' + side + '-' + x); }
      if (s.at >= 0 && s.acts) { hovered++; if (s.acts.shown) shown++; if (s.acts.inRow) inRow++; }
    }
    await p.move(5, 5); await frame(p);
    return { x, steps, changed, jumps, hovered, shown, inRow, rows: base.length, bad };
  };
  try {
    for (const [w, h] of [[1728, 1117], [1280, 800]]) {
      const p = await page(w, h);
      sweep.shot = 0;
      await p.evalIn(`(() => { VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
      await p.until(`document.body.classList.contains("po-dock") && !!PD.dock && !!IBX.data && ${D}.querySelectorAll("li.ibx-row[data-q]").length >= 8 && ${D}.getBoundingClientRect().width > 0`, 30000);
      const k = `${w}x${h}`;
      out[k] = { left: await sweep(p, 'left'), right: await sweep(p, 'right') };
      // By keyboard: focus on a row's link shows its actions.
      out[k].focus = await p.evalIn(`(() => { const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[1]; li.querySelector('a.ibx-link').focus();
        const a = li.querySelector('.ibx-acts'); const s = getComputedStyle(a).display; document.activeElement.blur(); return s; })()`);
      // The confirm state: a click on Close this and earlier asks in the row, its actions shown.
      const [bx, by] = await p.evalIn(`(() => { const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[1]; li.querySelector('a.ibx-link').focus();
        const b = li.querySelector('[data-ibx-earlier]').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()`);
      await p.clickAt(bx, by); await sleep(250);
      out[k].confirm = await p.evalIn(`(() => { const li = ${D}.querySelector('li.ibx-row.ibx-confirming'); if (!li) return null;
        const a = li.querySelector('.ibx-acts'); return { acts: getComputedStyle(a).display, pos: getComputedStyle(a).position, conf: !!li.querySelector('.ibx-conf [data-ibx-go]') }; })()`);
      await p.shot('hover-' + k + '-confirm');
      await p.close();
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
class HoverInChrome(close.CloseInChrome):
    """The close test's hub and seeded PO chat, with this test's script."""

    @classmethod
    def setUpClass(cls):
        real = close.CDP_JS
        close.CDP_JS = CDP_JS
        try:
            super().setUpClass()
        finally:
            close.CDP_JS = real
        if os.environ.get("ENSEMBLE_HOVER_LOG"):
            for k, v in cls.got.items():
                if k != "errors":
                    sys.stderr.write(f"{k}: {json.dumps(v)}\n")

    def test_no_row_moves_or_resizes_under_the_pointer(self):
        for k in ("1728x1117", "1280x800"):
            for side in ("left", "right"):
                s = self.got[k][side]
                self.assertGreaterEqual(s["rows"], 6, s)
                self.assertGreater(s["steps"], 50, s)
                self.assertEqual(s["changed"], 0, f"{k} {side}: {s}")
                self.assertEqual(s["jumps"], 0, f"{k} {side}: {s}")

    def test_the_hovered_row_shows_its_actions_inside_it(self):
        for k in ("1728x1117", "1280x800"):
            for side in ("left", "right"):
                s = self.got[k][side]
                self.assertGreater(s["hovered"], 0, s)
                self.assertEqual(s["shown"], s["hovered"], f"{k} {side}: {s}")
                self.assertEqual(s["inRow"], s["hovered"], f"{k} {side}: {s}")

    def test_keyboard_focus_and_confirm_still_show_the_actions(self):
        for k in ("1728x1117", "1280x800"):
            self.assertEqual(self.got[k]["focus"], "flex")
            c = self.got[k]["confirm"]
            self.assertIsNotNone(c, k)
            self.assertEqual(c["acts"], "flex")
            self.assertEqual(c["pos"], "static", "the confirm row asks in its own space")
            self.assertTrue(c["conf"])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


# Only this file's tests: the base class's are run by its own module.
for _n in [n for n in dir(close.CloseInChrome) if n.startswith("test_") and n not in HoverInChrome.__dict__]:
    setattr(HoverInChrome, _n, None)
del _n

if __name__ == "__main__":
    unittest.main()
