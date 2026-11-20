"""P425: a hovered Inbox card stays still, in headless Chrome over CDP (desktop
hover and a fine pointer), against a hub in a thread.

The hover test's PO chat (long questions, cut at their lines, and short ones),
and two cards added to the hub's answer page-side (an answers card to check and
a waiting card), its answer changing at every poll as a live hub's does. On a
long question, a short one, the answers card and the waiting card:

* the pointer resting on the card's text for 3 s, across real Inbox polls and
  the task list's redraws, changes its raised state (its hover copy, its side
  panel, the card itself) at most once: recorded by a MutationObserver;
* the same with the pointer moving slowly over the text;
* a card whose text shows whole keeps its height and place on hover (within
  1 px) and gets no raised copy, only the side panel; a card whose text is cut
  gets a copy showing all of it.

Set ENSEMBLE_SHOTS=<dir> for a screenshot of a hovered short and long card.
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
  // The hub's answer, as a live one: an answers card and a waiting card added, and a card whose words change at every
  // poll (another conversation's news), so each poll draws the list anew.
  await evalIn(`(() => {
    const po = ${JSON.stringify(A.po)}, pid = ${JSON.stringify(A.proj)}, real = window.fetch.bind(window), now = Date.now() / 1000;
    window.__polls = 0;
    window.fetch = (u, o) => !String(u).split('?')[0].endsWith('/api/inbox') ? real(u, o) : real(u, o).then(r => r.json()).then(d => {
      const n = ++window.__polls, rows = d.rows || [];
      rows.push({ id: 'p425-check', kind: 'check', roomId: po, projectId: pid, project: 'Motors', isPo: true, mid: '', line: '2 answers to check',
        reason: '', since: now - 600, items: [{ mid: 'm-a', line: 'Wider columns: yes', since: now - 600 }, { mid: 'm-b', line: 'Colour: green', since: now - 500 }] });
      rows.push({ id: 'p425-wait', kind: 'waiting', roomId: po, projectId: pid, project: 'Motors', isPo: true, mid: '', line: 'Waits on you to start it',
        reason: 'Its folder is gone', since: now - 300 });
      rows.push({ id: 'p425-reason', kind: 'waiting', roomId: po, projectId: pid, project: 'Motors', isPo: true, mid: '', line: 'Needs a decision',
        reason: 'This reason is long enough to fill more than two lines in the narrow Inbox card. It explains the work that is waiting and why a person must read the whole note before choosing what to do next.', since: now - 250 });
      rows.push({ id: 'p425-news', kind: 'waiting', roomId: po, projectId: pid, project: 'Motors', isPo: true, mid: '', line: 'News ' + n,
        reason: '', since: now - 200 });
      d.rows = rows;
      return new Response(JSON.stringify(d), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    return 0;
  })()`);
  await evalIn('ibxPoll(), 0');
  await until(`!!${D}.querySelector('li.ibx-row[data-ibx="p425-check"]') && !!${D}.querySelector('li.ibx-row[data-ibx="p425-wait"]')`);
  out.hoverMedia = await evalIn(`matchMedia('(hover: hover) and (pointer: fine)').matches`);
  // Every change of what the person sees raised. An uncut card may be redrawn
  // in place by a poll; its side panel follows the card by id.
  await evalIn(`(() => {
    window.__rec = null;
    const kind = n => n.nodeType !== 1 ? '' : n.classList.contains('row-expanded') ? 'copy' : n.classList.contains('ibx-side') ? 'side'
      : n.matches('li.ibx-row') ? 'row' : '';
    new MutationObserver(ms => { if (!window.__rec) return; const t = Math.round(performance.now() - window.__rec.t0);
      for (const m of ms) {
        if (m.type === 'childList') {
          for (const n of m.addedNodes) { const k = kind(n); if (k) window.__rec.ev.push([t, k + '+']); }
          for (const n of m.removedNodes) { const k = kind(n); if (k) window.__rec.ev.push([t, k + '-']); }
        } else if (m.target.matches && m.target.matches('li.ibx-row') && m.target.dataset.ibx === window.__rec.id) {
          window.__rec.ev.push([t, 'class:' + m.target.className]);
        }
      } }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    return 0;
  })()`);
  const li = id => `${D}.querySelector('li.ibx-row[data-ibx=' + JSON.stringify(${JSON.stringify(id)}) + ']')`;
  // The card's measures: its box, whether its words are cut (a clamped part longer than it shows), and the copy's box.
  const look = id => `(() => {
    const li = ${li(id)}; if (!li) return null;
    const r = li.getBoundingClientRect(), cp = document.querySelector('.row-expanded'), cr = cp && cp.getBoundingClientRect();
    const content = cp && [...cp.querySelectorAll('.ibx-line, .ibx-reason, .ibx-meta')];
    const neededH = cr && content.length ? Math.max(...content.map(e => e.getBoundingClientRect().bottom))
      - cr.top + parseFloat(getComputedStyle(cp).paddingBottom) : 0;
    const cut = [...li.querySelectorAll('.ibx-line, .ibx-reason')].some(e => e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1);
    return { top: r.top, h: r.height, cut, copy: !!cp, copyH: cr ? cr.height : 0, copyBottom: cr ? cr.bottom : 0, bottom: r.bottom,
      keepGaps: cp ? cp.querySelectorAll('.row-expand-keep-gap').length : 0, neededH,
      side: !!document.querySelector('.ibx-side') && IBX_SIDE.id === li.dataset.ibx, actions: !!li.querySelector(':scope > .ibx-acts'),
      text: li.querySelector('.ibx-line').textContent.slice(0, 40),
      copyWhole: !!cp && [...cp.querySelectorAll('.ibx-line, .ibx-reason')].every(e => e.scrollHeight <= e.clientHeight + 1) && cr.bottom <= innerHeight };
  })()`;
  const at = id => evalIn(`(() => { const li = ${li(id)}; li.scrollIntoView({ block: 'nearest' });
    const b = li.querySelector('.ibx-line').getBoundingClientRect(); return [Math.round(b.left + 30), Math.round(b.top + 6), Math.round(b.width)]; })()`);
  const ids = await evalIn(`(() => { const qs = [...${D}.querySelectorAll('li.ibx-row[data-q]')];
    const cut = l => { const e = l.querySelector('.ibx-line'); return e.scrollHeight > e.clientHeight + 1; };
    return { long: (qs.find(cut) || {}).dataset?.ibx, short: (qs.find(l => !cut(l)) || {}).dataset?.ibx,
      check: 'p425-check', waiting: 'p425-wait', reason: 'p425-reason' }; })()`);
  out.ids = ids;
  out.cards = {};
  try {
    for (const [name, id] of Object.entries(ids)) {
      const o = out.cards[name] = {};
      await move(5, 5); await sleep(500); await frame();
      const [x, y, w] = await at(id);
      o.before = await evalIn(look(id));
      await move(x, y); await sleep(700); await frame();
      o.hover = await evalIn(look(id));
      if (name === 'short' || name === 'long') {
        await shot(`p425-${name}`, await evalIn(`(() => { const b = ${li(id)}.getBoundingClientRect(), cp = document.querySelector('.row-expanded'), sd = document.querySelector('.ibx-side');
          const bot = Math.max(b.bottom, cp ? cp.getBoundingClientRect().bottom : 0, sd ? sd.getBoundingClientRect().bottom : 0);
          const right = Math.max(b.right, sd ? sd.getBoundingClientRect().right : 0);
          return { x: Math.max(0, b.left - 8), y: Math.max(0, b.top - 8), width: right - b.left + 16, height: bot - b.top + 16 }; })()`));
      }
      // Still for 3 s: two real polls (the hub's answer changes at each) and the task list's redraws between.
      await evalIn(`(() => { window.__rec = { id: ${JSON.stringify(id)}, t0: performance.now(), ev: [] }; return 0; })()`);
      const p0 = await evalIn('window.__polls');
      for (let k = 0; k < 6; k++) {
        await sleep(250);
        if (k === 1 || k === 4) await evalIn('ibxPoll(), 0');
        else await evalIn('(renderRows(), 0)');
        await sleep(250);
      }
      o.polls = (await evalIn('window.__polls')) - p0;
      o.still = await evalIn('window.__rec.ev.splice(0)');
      o.stillEnd = await evalIn(look(id));
      // Slowly over its text: 2px every 40ms for 3 s, a poll on the way.
      const t0 = Date.now(); let k = 0;
      while (Date.now() - t0 < 3000) {
        await move(x + (k % Math.max(10, Math.min(w - 40, 150))), y + (k % 3)); k++;
        if (k === 30) await evalIn('ibxPoll(), 0');
        if (k % 15 === 0) await evalIn('(renderRows(), 0)');
        await sleep(40);
      }
      o.slow = await evalIn('window.__rec.ev.splice(0)');
      await evalIn('window.__rec = null, 0');
      o.slowEnd = await evalIn(look(id));
    }
    // A background paint for a different project must not be held under the old copy.
    await move(5, 5); await sleep(500);
    const [sx, sy] = await at(ids.long);
    await move(sx, sy); await sleep(700); await frame();
    out.switch = await evalIn(`(() => {
      const body = ldPart('ld-ibx-body');
      const hadCopy = !!document.querySelector('.row-expanded'), oldPid = body._ibxPid;
      IBX.scope = 'project'; IBX.polled = true;
      try { ibxPaintIn(body, 'p425-other-project', 'ldSig', false, { listed: true }); }
      finally { IBX.polled = false; }
      return { hadCopy, oldPid, newPid: body._ibxPid,
        copyGone: !document.querySelector('.row-expanded'), rows: LD_INBOX.querySelectorAll('li.ibx-row').length };
    })()`);
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class HoverSteadyInChrome(close.CloseInChrome):
    """The close test's hub and the hover test's PO chat, with this test's script."""

    @classmethod
    def setUpClass(cls):
        real, older = close.CDP_JS, close._older
        close.CDP_JS, close._older = CDP_JS, hover._mixed
        try:
            super().setUpClass()
        finally:
            close.CDP_JS, close._older = real, older

    def test_pointer_still_and_slow_moves_keep_raised_state(self):
        self.assertTrue(self.got['hoverMedia'])
        for name, card in self.got['cards'].items():
            with self.subTest(name=name):
                self.assertGreaterEqual(card['polls'], 2)
                for phase in ('still', 'slow'):
                    self.assertFalse([e for e in card[phase] if e[1] in ('copy-', 'copy+', 'side-', 'side+')],
                                     (name, phase, card[phase]))
                self.assertEqual(card['stillEnd']['side'], card['hover']['side'], name)
                self.assertEqual(card['slowEnd']['side'], card['hover']['side'], name)

    def test_only_cut_text_grows(self):
        for name, card in self.got['cards'].items():
            with self.subTest(name=name):
                before, hover = card['before'], card['hover']
                if hover['actions'] and name != 'check':
                    self.assertTrue(hover['side'])
                if name in ('long', 'reason'):
                    self.assertTrue(before['cut'])
                    self.assertTrue(hover['copy'])
                    self.assertTrue(hover['copyWhole'])
                    self.assertEqual(hover['keepGaps'], 0, 'hidden actions must not add an empty band')
                    self.assertLessEqual(hover['copyH'], hover['neededH'] + 1, 'copy must fit its text without spare height')
                else:
                    self.assertFalse(before['cut'])
                    self.assertFalse(hover['copy'])
                    self.assertLessEqual(abs(hover['h'] - before['h']), 1)
                    self.assertLessEqual(abs(hover['top'] - before['top']), 1)

    def test_no_page_errors(self):
        self.assertEqual(self.got['errors'], [])

    def test_project_switch_draws_while_old_copy_is_open(self):
        s = self.got['switch']
        self.assertTrue(s['hadCopy'])
        self.assertNotEqual(s['oldPid'], s['newPid'])
        self.assertEqual(s['newPid'], 'p425-other-project')
        self.assertTrue(s['copyGone'])
        self.assertEqual(s['rows'], 0)


for _n in [n for n in dir(close.CloseInChrome) if n.startswith("test_") and n not in HoverSteadyInChrome.__dict__]:
    setattr(HoverSteadyInChrome, _n, None)
del _n

if __name__ == "__main__":
    unittest.main()
