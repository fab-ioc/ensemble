"""A phone's chat fills the screen (#242).

In headless Chrome over CDP, as a touch phone at 440x956, 430x932, 390x844 and
360x780, against test_phone_layout's isolated hub (Motors with a PO and a task,
Plain without one), with the PO chat holding what the CEO's screenshot showed:
14 open question cards, a new answer, two threads with new replies, earlier
messages.

* the message box sits at the bottom of the screen (within 40px);
* the keyboard (a visual viewport 340px shorter, with focus in the box) puts
  it right above the keyboard, and closing the keyboard brings it back to the
  bottom even when the visual viewport's resize event never comes (iOS);
* the messages get at least 60% of the height at 440x956, in the PO chat and
  a task's;
* nothing runs past the right edge, every control of the bar inside its row;
* the threads with new replies are said once;
* a file opened in Plain is no tab of the Motors chat, and is back in Plain.

And the boxes grow with the text (at 1728x1117 and 440x956): the message box
and a card's comment for 1, 6 and 30 lines, capped near 40% of the visible
height, then scrolling; Send stays where it was; one row again after Send.

ENSEMBLE_IPHONE_BEFORE=1 records the measurements without assertions;
ENSEMBLE_FOCUS_BASE_REF=<ref> serves that ref's pages (the before numbers).
Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import test_phone_focus as focus
from tests import test_phone_layout as phone

JS = phone.HEAD + r"""
// A visual viewport the test can shrink (the keyboard) without telling anyone,
// as iOS sometimes does when the keyboard goes.
const FAKE_VV = `(() => { if (window.top !== window || !window.visualViewport) return;
  const real = window.visualViewport, fake = new EventTarget();
  window.__vv = { h: null, top: 0 };
  for (const k of ['width', 'offsetLeft', 'pageLeft', 'pageTop', 'scale']) Object.defineProperty(fake, k, { get: () => real[k] });
  Object.defineProperty(fake, 'height', { get: () => window.__vv.h == null ? real.height : window.__vv.h });
  Object.defineProperty(fake, 'offsetTop', { get: () => window.__vv.h == null ? real.offsetTop : window.__vv.top });
  real.addEventListener('resize', () => fake.dispatchEvent(new Event('resize')));
  real.addEventListener('scroll', () => fake.dispatchEvent(new Event('scroll')));
  Object.defineProperty(window, 'visualViewport', { get: () => fake, configurable: true });
})();`;
// The PO chat as the CEO's screenshot had it.
const PO_STATE = `(() => {
  const now = Date.now() / 1000;
  tick = () => {}; refresh = async () => {};
  const who = ROOM_OBJ.participants.find(p => p.kind === 'agent').identity;
  const items = Array.from({ length: 30 }, (_, i) => ({ id: 'po-' + i, from: i % 3 ? who : 'user', to: 'user', ts: now - 3600 + i * 60,
    text: 'Status of the listing ' + i + ': the draft is ready in your mail, and the buyer asked for a test drive on Saturday.' }));
  ROOM_OBJ = { ...ROOM_OBJ, openCards: Array.from({ length: 14 }, (_, i) => ({ mid: 'po-' + (i + 1), n: 0, question: 'Shall I send reply ' + (i + 1) + '?', ts: now - 86400 - i, who })) };
  const older = ROOM_OBJ.openCards.map(c => ({ ...c }));
  POINTS = pointMaps({ delivered: 1, open: 0, planned: 0,
    items: [{ id: 'P1', mid: 'po-3', text: 'Check the buyer', state: 'delivered', createdAt: now - 600, answers: [{ mid: 'po-4', said: 'Checked' }] }],
    threads: [{ id: 'po-5', title: 'Saturday test drive', unread: 2, lastAt: now - 60 }, { id: 'po-8', title: 'Lease takeover', unread: 1, lastAt: now - 120 }],
    needs: { decisions: [], older, olderCount: older.length, news: 1, totry: 0, closed: [], closedCount: 0 } });
  THREADED = true; THR_UNREAD = [{ id: 'po-5', lastMid: 'po-6', root: 'po-5' }, { id: 'po-8', lastMid: 'po-9', root: 'po-8' }];
  LAST_ITEMS = items; renderBubbles(items);
  showOpenQuestions(); showPointsLine(); pointsTell({ items: [], needs: { news: 1 }, delivered: 1, threads: [{}, {}] });
  document.body.classList.add('po-thread-channel');
  document.getElementById('chatbar').hidden = false;
  document.getElementById('earlier-threads').hidden = false;
  showThreadsBtn(); showThreadCatchup();
  return 0;
})()`;
const TASK_STATE = `(() => {
  const now = Date.now() / 1000;
  tick = () => {}; refresh = async () => {};
  const who = ROOM_OBJ.participants.find(p => p.kind === 'agent').identity;
  const items = Array.from({ length: 30 }, (_, i) => ({ id: 't-' + i, from: i % 2 ? who : 'user', to: 'user', ts: now - 3600 + i * 60, text: 'Brake check ' + i + ': pads, discs and the caliper slides.' }));
  LAST_ITEMS = items; renderBubbles(items); return 0;
})()`;
// The frame of the chat on screen.
const FRAME = `(document.body.classList.contains('detail-open') ? document.querySelector('#detail-panel iframe.dp-session') : document.querySelector('#po-panel iframe.po-session:not([hidden])'))`;
const GEO = `(() => {
  const f = ${FRAME}, d = f.contentDocument, fb = f.getBoundingClientRect();
  const vis = e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none'; };
  const m = d.getElementById('msgs').getBoundingClientRect(), c = d.getElementById('compose').getBoundingClientRect();
  // The messages' room: from their top to the box (an agent's live lines in between are messages too).
  // Cut off by a box that ends inside the screen (an ellipsis, a scroller): not past it.
  const clipped = e => { for (let a = e.parentElement; a; a = a.parentElement) { const st = a.ownerDocument.defaultView.getComputedStyle(a);
    if (st.overflowX !== 'visible' && a.getBoundingClientRect().right <= a.ownerDocument.defaultView.innerWidth + 0.5) return true; } return false; };
  const over = doc => [...doc.querySelectorAll('body *')].filter(e => vis(e) && e.getBoundingClientRect().right > doc.defaultView.innerWidth + 0.5 && e.getBoundingClientRect().left < doc.defaultView.innerWidth && e.getBoundingClientRect().bottom > 0
    && !clipped(e)
    && !e.closest('.dk-tabs, .dk-tabbar, .dk-head, #msgs, .phone-tools-menu, [hidden]')).map(e => (e.id || e.className || e.tagName) + (e.id ? '' : '@' + ((e.closest('[id]') || {}).id || '')) + ':' + Math.round(e.getBoundingClientRect().right) + (e.id ? '' : ' ' + [e.parentElement, e.parentElement && e.parentElement.parentElement].map(x => x ? x.tagName + '.' + x.className : '').join('<') + ' ' + e.outerHTML.slice(0, 80))).slice(0, 8);
  // Each control of the bar inside its row and the 16px gutter. (Above: what is
  // on screen and runs past its right edge; a panel parked off screen is not.)
  const hd = document.querySelector('header'), hr = hd.getBoundingClientRect();
  const barOut = [...hd.querySelectorAll('button, a, select, summary')].filter(vis).filter(e => {
    const b = e.getBoundingClientRect(); return b.right > innerWidth - 16 + 0.5 || b.left < 16 - 0.5 || b.bottom > hr.bottom + 0.5 || b.top < hr.top - 0.5;
  }).map(e => (e.id || e.className) + ' ' + JSON.stringify(e.getBoundingClientRect()));
  const said = [...d.querySelectorAll('#chatcol *, #msgpane *')].concat([...document.querySelectorAll('#po-panel .po-head *')])
    .filter(e => vis(e) && [...e.childNodes].some(n => n.nodeType === 3 && /threads? with new repl/i.test(n.textContent))).length;
  const strips = ['#open-questions', '#ask-line', '#points-line', '#chatbar', '#thread-catchup'].map(s => d.querySelector(s)).filter(e => e && vis(e)).map(e => [e.id, Math.round(e.getBoundingClientRect().height)]);
  return { vw: innerWidth, vh: innerHeight, frameTop: Math.round(fb.top), msgs: Math.round(c.top - m.top), share: (c.top - m.top) / innerHeight,
    boxBottom: Math.round(fb.top + c.bottom), below: Math.round(innerHeight - (fb.top + c.bottom)), strips, threadLines: said,
    tabs: [...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].filter(vis).map(e => e.textContent.trim()),
    over: over(document).concat(over(d)), barOut, scrollW: Math.max(document.documentElement.scrollWidth, d.documentElement.scrollWidth),
    vvh: getComputedStyle(document.documentElement).getPropertyValue('--vv-h') };
})()`;
// The box and a card's comment with 1, 6 and 30 lines.
const GROW = `(async () => {
  const f = ${FRAME}, w = f.contentWindow, d = f.contentDocument, g = k => w.eval(k);
  const pause = () => new Promise(r => setTimeout(r, 60));
  const who = g('ROOM_OBJ').participants.find(p => p.kind === 'agent').identity;
  const items = (g('LAST_ITEMS') || []).concat([{ id: 'grow-ask', from: who, to: 'user', text: 'Ask: Shall I go ahead?', asks: g('parseAsks')('Ask: Shall I go ahead?'), askAudience: 'user', ts: Date.now() / 1000 }]);
  w.__items = items; g('LAST_ITEMS = window.__items; renderBubbles(LAST_ITEMS)');
  const out = { cap: Math.round(w.innerHeight * 0.4), frame: Math.round(w.innerHeight) };
  const lines = n => Array.from({ length: n }, (_, i) => 'Line ' + (i + 1)).join('\\n');
  for (const [key, el] of [['box', () => d.getElementById('input')], ['card', () => d.querySelector('.msg[data-mid="grow-ask"] .qa-cm textarea')]]) {
    out[key] = {};
    for (const n of [1, 6, 30]) {
      const ta = el(); ta.focus(); ta.value = lines(n); ta.dispatchEvent(new w.Event('input', { bubbles: true })); await pause();
      out[key][n] = { h: Math.round(el().getBoundingClientRect().height), scrolls: el().scrollHeight > el().clientHeight + 1 };
    }
  }
  // Send stays put across a press, and the box is one row after Send.
  const ta = d.getElementById('input'); ta.focus(); ta.value = lines(6); ta.dispatchEvent(new w.Event('input', { bubbles: true })); await pause();
  const s0 = d.getElementById('send').getBoundingClientRect();
  d.getElementById('send').dispatchEvent(new w.PointerEvent('pointerdown', { bubbles: true }));
  ta.blur(); await pause();
  const s1 = d.getElementById('send').getBoundingClientRect();
  out.sendMoved = Math.round(Math.abs(s1.top - s0.top) + Math.abs(s1.left - s0.left));
  try { await g('sendNow()'); } catch (e) { out.sendError = String(e); }
  await pause(); out.leftAfterSend = d.getElementById('input').value.length;
  out.afterSend = Math.round(d.getElementById('input').getBoundingClientRect().height);
  out.row = Math.round(parseFloat(w.getComputedStyle(ta).minHeight) || 0);
  return out;
})()`;
// The installed app, its window as tall as the screen until the keyboard: the
// height the keyboard left comes back to the full one; any other height
// (a toolbar, a window made shorter with no keyboard) is the new height.
const STANDALONE = `(async () => {
  const pause = ms => new Promise(r => setTimeout(r, ms)), d = ${FRAME}.contentDocument;
  const mm = window.matchMedia, od = Object.getOwnPropertyDescriptor(window, 'innerHeight'), H = innerHeight;
  let ih = H;
  window.matchMedia = q => /standalone/.test(q) ? { matches: true, addEventListener() {}, removeEventListener() {} } : mm.call(window, q);
  Object.defineProperty(window, 'innerHeight', { get: () => ih, configurable: true });
  const vvh = () => getComputedStyle(document.documentElement).getPropertyValue('--vv-h').trim();
  const step = async () => { fitVisualViewport(); await pause(50); return vvh(); };
  const out = { H };
  try {
    out.start = await step();
    d.getElementById('input').focus(); ih = H - 340; out.typing = await step();
    d.getElementById('input').blur(); await pause(1000); out.stale = vvh();
    ih = H - 60; out.toolbar = await step();
    ih = H; await step(); ih = H - 60; out.shorter = await step();
  } finally {
    if (od) Object.defineProperty(window, 'innerHeight', od); else delete window.innerHeight;
    window.matchMedia = mm; fitVisualViewport();
  }
  return out;
})()`;
// A reload while the hub warms up: /api/health says 'starting' and the task
// list has no Plain task yet, so the Plain file panel is held, until __warm
// goes (sessionStorage 'warm' names the task left out).
const WARM = `(() => { let room = ''; try { room = sessionStorage.getItem('warm') || ''; sessionStorage.removeItem('warm'); } catch (e) {}
  if (!room) return; window.__warm = true;
  const drop = x => Array.isArray(x) ? x.filter(i => !(i && (i.roomId === room || i.sessionId === room))).map(drop)
    : x && typeof x === 'object' ? Object.fromEntries(Object.entries(x).map(([k, v]) => [k, drop(v)])) : x;
  const real = window.fetch;
  window.fetch = async (u, o) => {
    const url = String(u && u.url || u);
    if (!window.__warm || !/\\/api\\//.test(url)) return real(u, o);
    if (/\\/api\\/health/.test(url)) return new Response(JSON.stringify({ phase: 'starting' }), { headers: { 'Content-Type': 'application/json' } });
    const r = await real(String(url).replace(/([?&])since=[^&]*/, '$1since='), o);
    if (!/\\/api\\/(sessions|projects)\\b/.test(url) || !r.ok) return r;
    const j = drop(await r.json()); if (j && typeof j === 'object' && 'rev' in j) j.rev = '';
    return new Response(JSON.stringify(j), { headers: { 'Content-Type': 'application/json' } });
  };
})();`;
// What the page shows, for a wait that timed out.
const DIAG = `({ body: document.body.className, tabs: [...document.querySelectorAll('#po-dock .dk-tab')].map(e => e.textContent.trim()),
  views: [...WS_VIEWS.keys()], files: [...document.querySelectorAll('.wse')].slice(0, 12).map(e => e.className + ' ' + (e.dataset.path || '')),
  rt: [...PD.rt.entries()].map(([k, e]) => k + ':' + e.kind) })`;
async function main() {
  const { ch, ws } = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile }, s);
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: mobile, maxTouchPoints: 5 }, s);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: "try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {}\n" + FAKE_VV + WARM }, s);
    const ev = async expression => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw Error(expression.slice(0, 120) + ' ' + JSON.stringify(r.exceptionDetails).slice(0, 800)); return r.result.value; };
    const until = async (ex, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await ev(ex)) return; } catch (e) {} await sleep(150); }
      let seen = ''; try { seen = JSON.stringify(await ev(DIAG)); } catch (e) { seen = String(e); }
      throw Error('timeout ' + ex + '\n' + seen); };
    const shot = async name => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, s);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')');
    await sleep(300);
    return { ev, until, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  // The panels follow the project list, which a poll (5s) brings: a while for it, then the check.
  const settled = p => p.until(`![...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].some(e => e.getBoundingClientRect().width > 0 && /PLAIN-NOTES/.test(e.textContent))`, 8000).catch(() => {});
  const inFrame = (p, js) => p.ev(`${FRAME}.contentWindow.eval(${JSON.stringify(js)})`);
  const chatReady = p => p.until(`!!${FRAME}?.contentWindow?.document.getElementById('input') && ${FRAME}.contentWindow.eval('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && !!ROOM_OBJ.participants')`);
  const openPo = async p => {
    if (await p.ev(`document.body.classList.contains('detail-open') || !document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]')`)) await p.ev(`document.getElementById('bar-back')?.click(); 0`);
    await sleep(200);
    await p.ev(`document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]').click(); 0`);
    await p.until(`document.body.classList.contains('po-dock') && !document.body.classList.contains('detail-open') && !!document.querySelector('#po-panel iframe.po-session:not([hidden])')`);
    await chatReady(p); await sleep(700);
  };
  const openTask = async (p, room) => {
    if (await p.ev(`!document.querySelector('#sw-list .sw-row[data-room="${room}"]')`)) await p.ev(`document.getElementById('bar-back')?.click(); 0`);
    await sleep(200);
    await p.ev(`document.querySelector('#sw-list .sw-row[data-room="${room}"]').click(); 0`);
    await p.until(`document.body.classList.contains('detail-open') && !!document.querySelector('#detail-panel iframe.dp-session')`);
    await chatReady(p); await sleep(700);
  };
  // The keyboard: focus in the box and the visual viewport 340px shorter; then
  // the box blurred and the viewport back, with or without its resize event.
  const keyboard = async (p, h, told) => {
    await inFrame(p, `document.getElementById('input').focus(); 0`);
    await p.ev(`window.__vv.h = ${h - 340}; window.__vv.top = 0; visualViewport.dispatchEvent(new Event('resize')); 0`);
    await sleep(500);
    const open = await p.ev(GEO);
    await p.ev(`window.__vv.h = null; ${told ? "visualViewport.dispatchEvent(new Event('resize'));" : ''} 0`);
    await inFrame(p, `document.getElementById('input').blur(); 0`);
    await sleep(1200);
    return { open, closed: await p.ev(GEO) };
  };
  try {
    for (const [w, h] of [[440, 956], [430, 932], [390, 844], [360, 780]]) {
      const p = await page(w, h, true), k = w + 'x' + h, o = out[k] = {};
      // A file opened in Plain's task, before the Motors PO chat.
      await openTask(p, A.plainTask);
      await p.ev(`[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].find(e => /^Files/.test(e.textContent.trim())).click(); 0`);
      await p.until(`!!document.querySelector('.wse.file[data-path$="PLAIN-NOTES.md"]')`);
      await p.ev(`document.querySelector('.wse.file[data-path$="PLAIN-NOTES.md"]').click(); 0`);
      await p.until(`[...PD.rt.values()].some(e => e.kind === 'file')`);
      await sleep(300);
      o.plainTabs = await p.ev(`[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].map(e => e.textContent.trim())`);
      await openPo(p);
      await inFrame(p, PO_STATE);
      await sleep(400);
      await settled(p);
      o.po = await p.ev(GEO);
      await p.shot(`po-${k}`);
      o.poKbd = await keyboard(p, h, false);
      o.poKbdTold = await keyboard(p, h, true);
      await p.shot(`po-${k}-after-keyboard`);
      await openTask(p, A.task);
      await inFrame(p, TASK_STATE);
      await sleep(300);
      o.task = await p.ev(GEO);
      await p.shot(`task-${k}`);
      o.taskKbd = await keyboard(p, h, false);
      await openTask(p, A.plainTask);
      o.plainBack = await p.ev(`[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].map(e => e.textContent.trim())`);
      if (w === 440) {
        await openPo(p);
        await inFrame(p, PO_STATE);
        o.grow = await p.ev(GROW);
        await p.shot(`grow-${k}`);
        o.standalone = await p.ev(STANDALONE);
        // The Plain file panel held over a reload while the hub warms, on the Motors chat.
        await p.ev(`sessionStorage.setItem('warm', ${JSON.stringify(A.plainTask)}); location.reload(); 0`);
        await sleep(500);
        await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')');
        await openPo(p);
        await p.until(`PD.restored && !!PD_HOLD && PD_HOLD.ctxs.length > 0`);
        o.held = await p.ev(`({ project: (poDockProject() || {}).id, detail: document.body.classList.contains('detail-open') })`);
        await p.ev(`window.__warm = false; 0`);
        await p.until(`PD_HOLD === null && [...PD.rt.values()].some(e => e.kind === 'file')`);
        await sleep(600);
        await settled(p);
        o.held.tabs = await p.ev(`[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.textContent.trim())`);
        await openTask(p, A.plainTask);
        o.held.back = await p.ev(`[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].map(e => e.textContent.trim())`);
      }
      await p.close();
    }
    const p = await page(1728, 1117, false);
    await p.ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await p.until(`!!document.querySelector('#po-panel iframe.po-session:not([hidden])')?.contentWindow?.document.getElementById('input')`);
    await chatReady(p); await sleep(600);
    await inFrame(p, PO_STATE);
    out.desktop = { po: await p.ev(GEO), grow: await p.ev(GROW) };
    await p.shot('desktop-1728');
    await p.close();
  } finally { try { ch.kill(); } catch (e) {} }
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'measurements.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(phone.NODE and phone.CHROME, 'needs Node and Chrome')
class IphoneChat(unittest.TestCase):
    SCRIPT = JS

    @classmethod
    def setUpClass(cls):
        # Plain's task gets a folder of its own with a file to open as a panel.
        real = phone.chatroom.create_room

        def create(title, members, *a, **k):
            room = real(title, members, *a, **k)
            if title == 'Plain chore 1':
                folder = Path(members[0]['cwd']) / 'plain-chore-1'
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'PLAIN-NOTES.md').write_text('# Plain notes\n', encoding='utf-8')
                room = phone.chatroom.patch_room(room['id'], taskDir=str(folder)) or room
            return room
        with mock.patch.object(phone.chatroom, 'create_room', create):
            focus.PhoneFocus.setUpClass.__func__(cls)
        cls.before = bool(os.environ.get('ENSEMBLE_IPHONE_BEFORE'))

    @classmethod
    def tearDownClass(cls):
        phone.ThePhone.tearDownClass.__func__(cls)

    PHONES = ('440x956', '430x932', '390x844', '360x780')

    def test_the_box_is_at_the_bottom(self):
        if self.before:
            return
        for k in self.PHONES:
            g = self.got[k]
            for name in ('po', 'task'):
                with self.subTest(size=k, chat=name):
                    self.assertLessEqual(g[name]['below'], 40, g[name])
                    self.assertGreaterEqual(g[name]['below'], -1, g[name])

    def test_the_keyboard_comes_and_goes(self):
        if self.before:
            return
        for k in self.PHONES:
            g, h = self.got[k], int(k.split('x')[1])
            for name in ('poKbd', 'poKbdTold', 'taskKbd'):
                with self.subTest(size=k, case=name):
                    kb = g[name]
                    # Right above the keyboard while it is up...
                    self.assertLessEqual(kb['open']['boxBottom'], h - 340 + 1, kb['open'])
                    self.assertGreaterEqual(kb['open']['boxBottom'], h - 340 - 40, kb['open'])
                    # ...and at the bottom again once it went, told or not.
                    self.assertLessEqual(kb['closed']['below'], 40, kb['closed'])
                    self.assertGreaterEqual(kb['closed']['below'], -1, kb['closed'])

    def test_the_messages_get_the_room(self):
        if self.before:
            return
        g = self.got['440x956']
        for name in ('po', 'task'):
            with self.subTest(chat=name):
                self.assertGreaterEqual(g[name]['share'], .60, g[name])

    def test_nothing_past_the_edge(self):
        if self.before:
            return
        for k in self.PHONES:
            for name in ('po', 'task'):
                g = self.got[k][name]
                with self.subTest(size=k, chat=name):
                    self.assertLessEqual(g['scrollW'], g['vw'])
                    self.assertEqual(g['over'], [])
                    self.assertEqual(g['barOut'], [])

    def test_the_threads_are_said_once(self):
        if self.before:
            return
        for k in self.PHONES:
            with self.subTest(size=k):
                self.assertLessEqual(self.got[k]['po']['threadLines'], 1, self.got[k]['po']['strips'])
                # One row above the messages: the Needs you line.
                self.assertEqual([i for i, _ in self.got[k]['po']['strips']], ['points-line'])

    def test_a_file_stays_with_its_project(self):
        if self.before:
            return
        for k in self.PHONES:
            g = self.got[k]
            with self.subTest(size=k):
                self.assertTrue(any('PLAIN-NOTES.md' in t for t in g['plainTabs']), g['plainTabs'])
                self.assertFalse(any('PLAIN-NOTES.md' in t for t in g['po']['tabs']), g['po']['tabs'])
                self.assertFalse(any('PLAIN-NOTES.md' in t for t in g['task']['tabs']), g['task']['tabs'])
                self.assertTrue(any('PLAIN-NOTES.md' in t for t in g['plainBack']), g['plainBack'])

    def test_the_installed_app_height(self):
        if self.before:
            return
        g = self.got['440x956']['standalone']
        full = '%dpx' % g['H']
        # Back to the full height only while the window is as short as the keyboard left it.
        self.assertEqual((g['start'], g['typing'], g['stale'], g['toolbar'], g['shorter']), ('', '', full, '', ''), g)

    def test_a_late_panel_stays_with_its_project(self):
        if self.before:
            return
        g = self.got['440x956']['held']
        self.assertEqual(g['project'], self.proj, g)
        self.assertFalse(g['detail'], g)
        self.assertFalse(any('PLAIN-NOTES.md' in t for t in g['tabs']), g)
        self.assertTrue(any('PLAIN-NOTES.md' in t for t in g['back']), g)

    def test_the_boxes_grow_with_the_text(self):
        if self.before:
            return
        for where, g in (('phone', self.got['440x956']['grow']), ('desktop', self.got['desktop']['grow'])):
            for key in ('box', 'card'):
                with self.subTest(where=where, field=key):
                    one, six, thirty = g[key]['1'], g[key]['6'], g[key]['30']
                    self.assertLess(one['h'], 64, g)
                    self.assertGreater(six['h'], one['h'] + 60, g)
                    self.assertFalse(six['scrolls'], g)
                    self.assertLessEqual(thirty['h'], g['cap'] + 2, g)
                    self.assertGreaterEqual(thirty['h'], g['cap'] - 40, g)
                    self.assertTrue(thirty['scrolls'], g)
            with self.subTest(where=where, check='send'):
                self.assertEqual(g['sendMoved'], 0, g)
                self.assertLess(g['afterSend'], 64, g)


if __name__ == '__main__':
    unittest.main()
