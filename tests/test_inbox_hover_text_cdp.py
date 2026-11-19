"""P408: a hovered Inbox row keeps its question readable, in headless Chrome over
CDP (desktop hover and a fine pointer), against a hub in a thread.

#394 laid a row's hover actions over its card as a full-width band that wrapped
to two lines in the 260px Inbox: 52px of the card's ground over the bottom of an
80px row, so the question's text went blank under the mouse. Now they are one
line over the meta line only, inside the card, their labels shortened with the
Inbox's width.

The close test's PO chat (each message asks two questions, so rows share one
message), with long questions (cut at their lines: a hover copy) and short ones.
At the default Inbox width (260px) and the narrowest (240px), the pointer is put
on every row's text in turn; checked on each:

* every point of the question's text, at each of its lines, shows the question
  (the row's own text, or its hover copy's): nothing is drawn over it;
* a cut question shows whole in its hover copy; one that fits shows none;
* the actions are one line, inside the card, below the question;
* the same after a repaint arriving while the pointer is on the row (held
  under a hover copy, drawn at once without one), and after the pointer leaves.

Set ENSEMBLE_SHOTS=<dir> for a screenshot of a hovered row at each width.
"""
from __future__ import annotations

import os
import unittest

import tests.test_inbox_close_cdp as close

_CLOSE, NODE, CHROME = close.CDP_JS, close.NODE, close.CHROME
_HOVER = "'--headless=new', '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4',"
assert "'--headless=new'," in _CLOSE

CDP_JS = _CLOSE[: _CLOSE.index("async function main()")].replace("'--headless=new',", _HOVER, 1) + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const move = (x, y) => c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
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
  // Row i, as the pointer on its text sees it: is every point of its question
  // the question? (Its own .ibx-line, or the hover copy's.)
  const look = i => `(() => {
    const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}]; if (!li) return null;
    const line = li.querySelector('.ibx-line'), card = li.getBoundingClientRect(), box = line.getBoundingClientRect();
    const rg = document.createRange(); rg.selectNodeContents(line);
    const rects = [...rg.getClientRects()].filter(r => r.width > 2 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1);
    let pts = 0, hid = 0; const bad = [];
    for (const r of rects) for (const f of [0.08, 0.3, 0.5, 0.7, 0.92]) {
      const x = r.left + r.width * f, y = r.top + r.height / 2; pts++;
      const e = document.elementFromPoint(x, y);
      if (!e || !e.closest('.ibx-line')) { hid++; if (bad.length < 3) bad.push([Math.round(x), Math.round(y), e ? e.tagName + '.' + e.className : null]); }
    }
    const copy = document.querySelector('.row-expanded');
    const cl = copy && copy.querySelector('.ibx-line');
    const acts = li.querySelector('.ibx-acts'), ab = acts && acts.getBoundingClientRect(), lk = li.querySelector('a.ibx-link').getBoundingClientRect();
    const btns = acts ? [...acts.querySelectorAll('.ibx-btn')] : [];
    return { id: li.dataset.ibx, text: line.textContent, pts, hid, bad,
      cut: line.scrollHeight > line.clientHeight + 1,
      copy: !!copy, copyWhole: !!cl && cl.textContent === line.textContent && cl.scrollHeight <= cl.clientHeight + 1,
      acts: acts && getComputedStyle(acts).display !== 'none' ? { n: btns.length, h: Math.round(ab.height), oneLine: new Set(btns.map(b => Math.round(b.getBoundingClientRect().top))).size === 1,
        inCard: ab.left >= card.left - 0.5 && ab.right <= card.right + 0.5 && ab.bottom <= card.bottom + 0.5,
        belowText: ab.top >= lk.bottom - 0.5, labels: btns.map(b => { const sp = [...b.children].filter(e => { const cs = getComputedStyle(e); return cs.display !== 'none' && cs.clipPath === 'none'; });
          return (b.children.length ? sp.map(e => e.textContent.trim()).join(' ') : b.textContent.trim()); }),
        allIn: btns.every(b => { const x = b.getBoundingClientRect(); return x.left >= ab.left - 0.5 && x.right <= ab.right + 0.5; }) } : null };
  })()`;
  const at = async i => evalIn(`(() => { const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}]; li.scrollIntoView({ block: 'nearest' });
    const b = li.querySelector('.ibx-line').getBoundingClientRect(); return [Math.round(b.left + 30), Math.round(b.top + 6)]; })()`);
  // A poll's repaint: the list drawn anew (held under a hover copy).
  const repaint = `(() => { const li = ${D}.querySelector('li.ibx-row[data-q]'); for (const k of Object.keys(IBX)) if (/Sig$/.test(k)) IBX[k] = '';
    ibxPolledPaint(); return { held: !!IBX.held, same: ${D}.querySelector('li.ibx-row[data-q]') === li }; })()`;
  try {
    for (const [name, w] of [['default', 0], ['narrow', 240]]) {
      if (w) { await evalIn(`(() => { ${D}.style.width = '${w}px'; ${D}.style.maxWidth = '${w}px'; return 0; })()`); await frame(); }
      const o = out[name] = { width: await evalIn(`Math.round(${D}.getBoundingClientRect().width)`), cards: await evalIn(`Math.round(${D}.querySelector('.ibx-cards').getBoundingClientRect().width)`), rows: [] };
      const n = await evalIn(`${D}.querySelectorAll('li.ibx-row[data-q]').length`);
      for (let i = 0; i < n; i++) {
        const [x, y] = await at(i);
        await move(5, 5); await frame();
        const before = await evalIn(look(i));
        await move(x, y); await sleep(450); await frame();
        const hover = await evalIn(look(i));
        if (i === 0 || (hover.cut && !o.shotCut)) {
          if (hover.cut) o.shotCut = 1;
          const clip = await evalIn(`(() => { const b = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].getBoundingClientRect(), cp = document.querySelector('.row-expanded');
            const bot = Math.max(b.bottom, cp ? cp.getBoundingClientRect().bottom : 0); return { x: Math.max(0, b.left - 8), y: Math.max(0, b.top - 8), width: b.width + 16, height: bot - b.top + 16 }; })()`);
          await shot(`p408-${name}-row${i}`, clip);
        }
        const rp = await evalIn(repaint); await frame();
        const during = await evalIn(look(i));
        await move(x + 1, y); await frame();
        const after = await evalIn(look(i));
        await move(5, 5); await sleep(50); await frame();
        const left = await evalIn(look(i));
        o.rows.push({ i, before, hover, rp, during, after, left });
      }
      if (w) await evalIn(`(() => { ${D}.style.width = ''; ${D}.style.maxWidth = ''; return 0; })()`);
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

LONG = ("Which of the three layouts for the release notes page should ship first, given that the wide one needs the new "
        "table component and the narrow one drops the screenshots that the support team asked for last week")


def _mixed(i: int) -> str:
    """Two questions in one message: a long one (cut at its lines) and a short one."""
    return (f"Round {i}.\n\n1. **Ask:** {LONG} ({i})?\n   - Yes\n   - No\n"
            f"2. **Ask:** Old question {i}b?\n   - Yes\n   - No\n")


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class HoverTextInChrome(close.CloseInChrome):
    """The close test's hub and PO chat, its questions long and short, with this test's script."""

    @classmethod
    def setUpClass(cls):
        real, older = close.CDP_JS, close._older
        close.CDP_JS, close._older = CDP_JS, _mixed
        try:
            super().setUpClass()
        finally:
            close.CDP_JS, close._older = real, older

    def _rows(self):
        for k in ("default", "narrow"):
            for r in self.got[k]["rows"]:
                yield k, r

    def test_the_pages_are_what_is_measured(self):
        self.assertTrue(self.got["hoverMedia"], "desktop hover")
        self.assertEqual(self.got["default"]["width"], 260)
        self.assertEqual(self.got["narrow"]["width"], 240)
        self.assertLess(self.got["narrow"]["cards"], 240)
        self.assertGreaterEqual(len(self.got["default"]["rows"]), 8)
        self.assertTrue(any(r["hover"]["cut"] for _k, r in self._rows()), "some questions are cut at their lines")
        self.assertTrue(any(not r["hover"]["cut"] for _k, r in self._rows()), "and some fit")

    def test_the_question_stays_readable_under_the_pointer(self):
        for k, r in self._rows():
            for phase in ("before", "hover", "during", "after", "left"):
                s = r[phase]
                self.assertGreater(s["pts"], 0, (k, r["i"], phase))
                self.assertEqual(s["hid"], 0, f"{k} row {r['i']} {phase}: {s['bad']} ({s['text'][:40]})")

    def test_a_cut_question_shows_whole_and_one_that_fits_shows_no_copy(self):
        for k, r in self._rows():
            for phase in ("hover", "after"):
                s = r[phase]
                if s["cut"]:
                    self.assertTrue(s["copyWhole"], (k, r["i"], phase, s))
                else:
                    self.assertFalse(s["copy"], (k, r["i"], phase, s))
            self.assertFalse(r["left"]["copy"], (k, r["i"]))

    def test_the_actions_are_one_line_below_the_question_inside_the_card(self):
        for k, r in self._rows():
            for phase in ("hover", "after"):
                a = r[phase]["acts"]
                self.assertIsNotNone(a, (k, r["i"], phase))
                self.assertTrue(a["oneLine"], (k, r["i"], a))
                self.assertLessEqual(a["h"], 20, (k, r["i"], a))
                self.assertTrue(a["inCard"] and a["allIn"], (k, r["i"], a))
                self.assertTrue(a["belowText"], (k, r["i"], a))
            self.assertIsNone(r["left"]["acts"], (k, r["i"]))

    def test_labels_shorten_with_the_width(self):
        d = [r["hover"]["acts"]["labels"] for r in self.got["default"]["rows"]]
        nr = [r["hover"]["acts"]["labels"] for r in self.got["narrow"]["rows"]]
        self.assertTrue(any(len(x) == 3 for x in d), d)
        self.assertRegex(d[0][1], r"^Earlier \d+$")
        self.assertIn("Done with this", d[0])
        self.assertEqual(nr[0][1:], ["Earlier", "Done"])
        self.assertEqual(d[0][0], "Close")

    def test_a_repaint_while_hovering_is_held_only_under_a_copy(self):
        for k, r in self._rows():
            self.assertEqual(r["rp"]["held"], r["hover"]["copy"], (k, r["i"], r["rp"]))

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


# Only this file's tests: the base class's are run by its own module.
for _n in [n for n in dir(close.CloseInChrome) if n.startswith("test_") and n not in HoverTextInChrome.__dict__]:
    setattr(HoverTextInChrome, _n, None)
del _n

if __name__ == "__main__":
    unittest.main()
