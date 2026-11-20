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
* P410 (B): with a mouse the card shows no buttons: they are in one side panel
  8px right of the card, out of the Inbox, at the card's top, in full words
  (Close, Close this and earlier N, Done with this) at both widths; it is gone
  once the pointer has left;
* with the keyboard (the card's link focused), a cut question's copy is up and
  the card's buttons stay on top at their place (P414 item 5), the copy's text
  wrapped around them; a real click on each reaches that very button, and the
  focus stays on the link;
* the same after a repaint arriving while the pointer is on the row (held
  under a hover copy, drawn at once without one), and after the pointer leaves;
* a row whose message could not be opened, at both widths: its note and its
  ways (Open the conversation, Done) are never covered: with a mouse its
  actions are in the side panel; the row's height never changes. Outside the
  listed Inbox such a row is as it was.

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
  // Attention arrives separately from the Inbox; the Done action appears after both have drawn.
  await until(`${D}.querySelectorAll('li.ibx-row[data-q]')[0]?.querySelector('[data-ibx-dismiss]')`, 30000);
  out.hoverMedia = await evalIn(`matchMedia('(hover: hover) and (pointer: fine)').matches`);
  // Row i, as the pointer on its text sees it: is every point of its question
  // the question? (Its own .ibx-line, or the hover copy's.)
  const look = i => `(() => {
    const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}]; if (!li) return null;
    const line = li.querySelector('.ibx-line'), card = li.getBoundingClientRect(), box = line.getBoundingClientRect();
    const rg = document.createRange(); rg.selectNodeContents(line);
    const rects = [...rg.getClientRects()].filter(r => r.width > 2 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1);
    const copy = document.querySelector('.row-expanded'), acts = li.querySelector('.ibx-acts');
    let pts = 0, hid = 0; const bad = [];
    for (const r of rects) for (const f of [0.08, 0.3, 0.5, 0.7, 0.92]) {
      const x = r.left + r.width * f, y = r.top + r.height / 2; pts++;
      const e = document.elementFromPoint(x, y);
      if (!e || !e.closest('.ibx-line') || !(li.contains(e) || (copy && copy.contains(e)))) { hid++; if (bad.length < 3) bad.push([Math.round(x), Math.round(y), e ? e.tagName + '.' + e.className : null]); }
    }
    const cl = copy && copy.querySelector('.ibx-line');
    // The copy is painted: on top at every point of its own lines, and not see-through.
    let copyShown = false;
    if (cl) { const cr = document.createRange(); cr.selectNodeContents(cl); const cs = getComputedStyle(copy);
      const cps = [...cr.getClientRects()].filter(r => r.width > 2);
      const ls = getComputedStyle(cl);
      copyShown = cps.length > 0 && cs.visibility === 'visible' && Number(cs.opacity) > 0.95 && ls.visibility === 'visible' && Number(ls.opacity) > 0.95
        && ls.color !== 'rgba(0, 0, 0, 0)' && ls.webkitTextFillColor !== 'rgba(0, 0, 0, 0)' && cps.every(r => [0.1, 0.5, 0.9].every(f => {
        const e = document.elementFromPoint(r.left + r.width * f, r.top + r.height / 2); return !!e && copy.contains(e) && !!e.closest('.ibx-line'); })); }
    // P414 item 5: the row's buttons are on top while the copy is up: the button itself, or the copy's picture of
    // it at its place (the same label); never the copy's text.
    const keep = (acts && getComputedStyle(acts).display !== 'none' && +getComputedStyle(acts).opacity > 0.5 ? [...acts.querySelectorAll('.ibx-btn')] : []).map(b => {
      const r = b.getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (e && b.contains(e)) return 'orig';
      const pb = e && e.closest('.row-expand-keep .ibx-btn');
      return pb && copy && copy.contains(pb) && pb.textContent === b.textContent ? 'pic' : (e ? e.tagName + '.' + e.className : 'none');
    });
    const ab = acts && acts.getBoundingClientRect(), lk = li.querySelector('a.ibx-link').getBoundingClientRect();
    // P410 (B): the side panel, with a mouse: beside the card, out of the Inbox, its actions in full words.
    const sd = document.querySelector('.ibx-side'), ib = ${D}.getBoundingClientRect(), sr = sd && sd.getBoundingClientRect();
    const side = sd ? { n: document.querySelectorAll('.ibx-side').length, mine: IBX_SIDE.id === li.dataset.ibx,
      gap: Math.round(sr.left - card.right), outInbox: sr.left >= ib.right - 0.5, top: Math.round(sr.top - card.top),
      inWin: sr.left >= 0 && sr.right <= innerWidth && sr.top >= 0 && sr.bottom <= innerHeight, w: Math.round(sr.width),
      labels: [...sd.querySelectorAll('.ibx-side-btn')].map(b => [...b.children].map(e => e.textContent.trim()).join(' ')),
      oneLineEach: [...sd.querySelectorAll('.ibx-side-btn')].every(b => b.getBoundingClientRect().height <= 30) } : null;
    const btns = acts ? [...acts.querySelectorAll('.ibx-btn')] : [];
    return { id: li.dataset.ibx, text: line.textContent, pts, hid, bad, side,
      cut: line.scrollHeight > line.clientHeight + 1,
      copy: !!copy, keep, copyWhole: !!cl && cl.textContent === line.textContent && cl.scrollHeight <= cl.clientHeight + 1, copyShown,
      acts: acts && getComputedStyle(acts).display !== 'none' && +getComputedStyle(acts).opacity > 0.5 ? { n: btns.length, h: Math.round(ab.height), oneLine: new Set(btns.map(b => Math.round(b.getBoundingClientRect().top))).size === 1,
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
        await move(5, 5); await sleep(450); await frame();
        const left = await evalIn(look(i));
        o.rows.push({ i, before, hover, rp, during, after, left });
      }
      if (w) await evalIn(`(() => { ${D}.style.width = ''; ${D}.style.maxWidth = ''; return 0; })()`);
    }
    // P414 item 5, with the keyboard (P410: with a mouse the card's buttons are in the side panel): a cut question's
    // link given the focus, as Tab gives it (a focus ring): its copy is up and the card's buttons on top of it; a
    // real click on each reaches that very button, and the focus stays on the link.
    {
      const i = out.default.rows.find(r => r.hover.cut).i;
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      const tabTo = async () => {
        await move(5, 5); await sleep(450); await frame();
        await evalIn(`(() => { const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}]; li.scrollIntoView({ block: 'nearest' }); if (document.activeElement) document.activeElement.blur(); li.querySelector('a.ibx-link').focus(); return 0; })()`);
        await sleep(450); await frame();
        return evalIn(`(() => { const a = document.activeElement; const l = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].querySelector('a.ibx-link'); return a === l && a.matches(':focus-visible'); })()`);
      };
      await evalIn(`(() => { window.__p414 = []; window.__p414f = ev => { const b = ev.target.closest && ev.target.closest('.ibx-btn'); if (!b || !${D}.contains(b)) return;
        window.__p414.push({ inInbox: ${D}.contains(b), act: Object.keys(b.dataset).join(','), t: b.textContent.trim() }); ev.preventDefault(); ev.stopImmediatePropagation(); };
        window.addEventListener('click', window.__p414f, true); return 0; })()`);
      const clicks = [];
      const nb = await evalIn(`${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].querySelectorAll('.ibx-acts .ibx-btn').length`);
      for (let k = 0; k < nb; k++) {
        const kb = await tabTo();
        const st = await evalIn(look(i));
        if (k === 0) await shot('p414-keyboard-copy', await evalIn(`(() => { const b = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].getBoundingClientRect(), cp = document.querySelector('.row-expanded');
          const bot = Math.max(b.bottom, cp ? cp.getBoundingClientRect().bottom : 0); return { x: Math.max(0, b.left - 8), y: Math.max(0, b.top - 8), width: b.width + 16, height: bot - b.top + 16 }; })()`));
        const [bx, by] = await evalIn(`(() => { const b = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].querySelectorAll('.ibx-acts .ibx-btn')[${k}].getBoundingClientRect();
          return [Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2)]; })()`);
        await move(bx, by); await sleep(120); await frame();
        const onBtn = await evalIn(look(i));
        await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: bx, y: by, button: 'left', clickCount: 1 }, sessionId);
        await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: bx, y: by, button: 'left', clickCount: 1 }, sessionId);
        await sleep(80); await frame();
        const want = await evalIn(`(() => { const b = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].querySelectorAll('.ibx-acts .ibx-btn')[${k}]; return Object.keys(b.dataset).join(','); })()`);
        clicks.push({ k, kb, stays: await evalIn(`document.activeElement === ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].querySelector('a.ibx-link')`), copy: st.copy, keep: st.keep, side: st.side, sideOnBtn: onBtn.side, got: await evalIn(`window.__p414.splice(0)`), want });
      }
      await evalIn(`(() => { window.removeEventListener('click', window.__p414f, true); document.activeElement && document.activeElement.blur(); return 0; })()`);
      await move(5, 5); await sleep(450); await frame();
      out.keepClicks = clicks;
    }
    // Review 1: a row whose message could not be opened (its note and its ways under the meta line), a cut
    // question and one that fits: the pointer on its text, then on each of the note's ways.
    const failLook = i => `(() => {
      const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}], note = li.querySelector('.ibx-fail'), fa = li.querySelector('.ibx-fail-acts');
      const acts = li.querySelector('.ibx-acts'), ab = acts.getBoundingClientRect(), nb = note.getBoundingClientRect(), card = li.getBoundingClientRect();
      const ways = [...fa.querySelectorAll('.ibx-btn')].map(b => { const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { t: b.textContent.trim(), hit: !!e && b.contains(e), at: e ? e.tagName + '.' + e.className : null }; });
      const nr = document.createRange(); nr.selectNodeContents(note);
      const cov = [...nr.getClientRects()].filter(r => r.width > 2).map(r => document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)).filter(e => !e || !note.contains(e));
      const noteHid = cov.length, noteByCopy = cov.every(e => !!e && !!e.closest('.row-expanded'));
      return { h: Math.round(card.height), shown: getComputedStyle(acts).display !== 'none' && +getComputedStyle(acts).opacity > 0.5, below: ab.top >= fa.getBoundingClientRect().bottom - 0.5,
        inCard: ab.left >= card.left - 0.5 && ab.right <= card.right + 0.5 && ab.bottom <= card.bottom + 0.5, ways, noteHid, noteByCopy, noteH: Math.round(nb.height),
        side: !!document.querySelector('.ibx-side') && IBX_SIDE.id === li.dataset.ibx };
    })()`;
    out.failed = [];
    for (const [name, w] of [['default', 0], ['narrow', 240]]) {
    if (w) { await evalIn(`(() => { ${D}.style.width = '${w}px'; ${D}.style.maxWidth = '${w}px'; return 0; })()`); await frame(); }
    const dr = out[name].rows, picks = [dr.find(r => r.hover.cut).i, dr.find(r => !r.hover.cut).i];
    for (const i of picks) {
      await move(5, 5); await frame();
      await evalIn(`(() => { const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}]; IBX.failed.set(li.dataset.ibx, 'the message was removed'); ibxRepaint(); return 0; })()`);
      await frame();
      const before = await evalIn(failLook(i));
      const [x, y] = await at(i);
      await move(x, y); await sleep(450); await frame();
      const text = await evalIn(look(i)), onText = await evalIn(failLook(i));
      const wayAt = await evalIn(`(() => { const li = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}];
        return [...li.querySelectorAll('.ibx-fail-acts .ibx-btn')].map(b => { const r = b.getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; }); })()`);
      const onWays = [];
      for (const [wx, wy] of wayAt) { await move(wx, wy); await sleep(200); await frame(); onWays.push(await evalIn(failLook(i))); }
      await shot(`p408-failed-${name}-row${i}`, await evalIn(`(() => { const b = ${D}.querySelectorAll('li.ibx-row[data-q]')[${i}].getBoundingClientRect(); return { x: b.left - 8, y: b.top - 8, width: b.width + 16, height: b.height + 16 }; })()`));
      await move(5, 5); await frame();
      out.failed.push({ width: name, i, before, text, onText, onWays });
      await evalIn(`(() => { IBX.failed.clear(); ibxRepaint(); return 0; })()`); await frame();
    }
    if (w) await evalIn(`(() => { ${D}.style.width = ''; ${D}.style.maxWidth = ''; return 0; })()`);
    }
    // The other Inboxes keep their own rule: a failed-open row outside the listed Inbox shows its actions only on hover.
    out.unlisted = await evalIn(`(() => { const box = document.createElement('div'); box.className = 'ibx'; box.style.cssText = 'position:fixed;left:900px;top:20px;width:300px';
      box.innerHTML = '<ul class="ibx-cards"><li class="ibx-row" data-q="1"><a class="ibx-link"><span class="ibx-line">Q?</span></a><div class="ibx-meta">PO</div>'
        + '<div class="ibx-fail">Could not open</div><div class="ibx-fail-acts"><button class="ibx-btn">Done</button></div><div class="ibx-acts"><button class="ibx-btn">Close</button></div></li></ul>';
      document.body.appendChild(box); const d = getComputedStyle(box.querySelector('.ibx-acts')).display; box.remove(); return d; })()`);
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

    def test_with_a_mouse_the_actions_are_in_a_side_panel_beside_the_card(self):
        # P410 (B): the card shows no buttons; one panel, 8px right of the card, out of the Inbox, at its top.
        for k, r in self._rows():
            for phase in ("hover", "during", "after"):
                s, sd = r[phase], r[phase]["side"]
                self.assertIsNone(s["acts"], (k, r["i"], phase))
                self.assertIsNotNone(sd, (k, r["i"], phase))
                self.assertEqual(sd["n"], 1, (k, r["i"], phase, sd))
                self.assertTrue(sd["mine"], (k, r["i"], phase, "the hovered card's"))
                self.assertEqual(sd["gap"], 8, (k, r["i"], phase, sd))
                self.assertTrue(sd["outInbox"] and sd["inWin"] and sd["oneLineEach"], (k, r["i"], phase, sd))
                self.assertLessEqual(abs(sd["top"]), 1, (k, r["i"], phase, sd))
            self.assertIsNone(r["before"]["side"], (k, r["i"]))
            self.assertIsNone(r["left"]["side"], (k, r["i"], "gone once the pointer has left"))
            self.assertIsNone(r["left"]["acts"], (k, r["i"]))

    def test_the_side_panel_has_full_labels_at_both_widths(self):
        d = [r["hover"]["side"]["labels"] for r in self.got["default"]["rows"]]
        nr = [r["hover"]["side"]["labels"] for r in self.got["narrow"]["rows"]]
        self.assertEqual(d, nr, "the Inbox's width changes nothing")
        self.assertTrue(any(len(x) == 3 for x in d), d)
        for x in d:
            self.assertEqual(x[0], "Close", x)
            self.assertEqual(x[-1], "Done with this", x)
            if len(x) == 3:
                self.assertRegex(x[1], r"^Close this and earlier \d+$")

    def test_a_cut_question_s_copy_is_painted(self):
        for k, r in self._rows():
            for phase in ("hover", "after"):
                if r[phase]["cut"]:
                    self.assertTrue(r[phase]["copyShown"], (k, r["i"], phase, r[phase]))

    def test_the_buttons_stay_on_top_of_a_cut_question_s_copy(self):
        # P414 item 5, with the keyboard: a cut question's copy is up and its card's buttons show over it, at their place.
        cl = self.got["keepClicks"]
        self.assertEqual(len(cl), 3, cl)
        for x in cl:
            self.assertTrue(x["kb"] and x["copy"], x)
            self.assertEqual(len(x["keep"]), 3, x)
            self.assertTrue(all(k in ("orig", "pic") for k in x["keep"]), x)
            self.assertIn("pic", x["keep"], x)
            self.assertIsNone(x["side"], (x, "no side panel while the card shows its buttons"))

    def test_a_click_on_a_button_under_the_copy_reaches_it(self):
        for x in self.got["keepClicks"]:
            self.assertEqual(len(x["got"]), 1, x)
            self.assertTrue(x["got"][0]["inInbox"], x)
            self.assertEqual(x["got"][0]["act"], x["want"], x)
            self.assertTrue(x["stays"], (x, "the focus stays on the card's link"))
            self.assertIsNone(x["sideOnBtn"], x)

    def test_a_row_that_could_not_open_keeps_its_note_and_ways_usable(self):
        f = self.got["failed"]
        self.assertEqual([x["width"] for x in f], ["default", "default", "narrow", "narrow"])
        for a, b in (f[0:2], f[2:4]):
            self.assertTrue(a["text"]["cut"] and not b["text"]["cut"], "a cut question and one that fits, at each width")
        self.assertEqual(self.got["unlisted"], "none", "outside the listed Inbox the actions still wait for hover")
        for x in f:
            self.assertEqual(x["text"]["hid"], 0, x["text"])
            states = [x["before"], x["onText"]] + x["onWays"]
            self.assertGreaterEqual(len(x["onWays"]), 2, x)
            self.assertTrue(x["onText"]["side"], (x["i"], "its actions are in the side panel"))
            for s in states:
                self.assertFalse(s["shown"], (x["i"], "with a mouse the card shows no buttons", s))
                self.assertEqual(s["h"], x["before"]["h"], (x["i"], "nothing moves", s))
                if s is x["onText"] and x["text"]["cut"]:
                    # The pointer on a cut question: its hover copy (#337) is raised over what is under it; nothing else.
                    self.assertTrue(s["noteByCopy"], (x["i"], s))
                else:
                    self.assertEqual(s["noteHid"], 0, (x["i"], s))
            for s in [x["before"]] + x["onWays"]:
                self.assertTrue(all(w["hit"] for w in s["ways"]), (x["i"], s["ways"]))

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
