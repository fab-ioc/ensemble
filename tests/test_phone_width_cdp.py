"""Nothing on a phone is wider than the screen (#295).

iOS zooms a page out to fit content wider than the screen, which looks like a
desktop page, so a page that holds nothing wider gives it no reason to. In
headless Chrome over CDP, as a touch phone at 440x956 and 390x844, against
test_phone_layout's isolated hub, on the list, an open PO conversation, an open
task, the plan tray, /session and /session?embed=1, with chats holding folded
rows drawn by the page's own renderers with long chips and many asks:

* no visible element's right edge is past the screen unless it sits inside a
  box that scrolls or clips within the screen;
* the messages do not scroll sideways, a row keeps its time and how it was
  sent inside its sender block and the screen (on a desktop with the longest
  sender too), and the closed task panel waits on the
  screen, not past its edge;
* the session page clips its root on a touch screen;
* all three pages ask iOS not to shrink to fit;
* the notice stays away on a normal phone, in landscape and on an iPad, and
  says the widths when a phone draws the page wider than its screen.

ENSEMBLE_FOCUS_BASE_REF=<ref> serves that ref's pages (the before numbers).
Skipped without Node or Chrome.
"""
from __future__ import annotations

import unittest

from tests import test_phone_focus as focus
from tests import test_phone_layout as phone

JS = phone.HEAD + r"""
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15';
// Rows through the page's own renderers, with what they are given at its
// longest: a PO's kind, superseded and unanswered; a gap naming many asks.
const WIDE_ROWS = `(() => {
  const now = Date.now() / 1000, box = document.getElementById('msgs'), N = CHAT_NAMES;
  tick = () => {};
  const line = 'A first line that runs on and on, with more words than any phone can hold on one row. '.repeat(4);
  const m = { id: 'wide-1', from: ROOM_OBJ.participants.find(p => p.kind === 'agent').identity, to: 'user', ts: now, text: line,
    kind: 'pomsg', poKind: 'a kind with a very long name '.repeat(6) };
  box.insertAdjacentHTML('beforeend', foldBalloonHtml(m, 0, 'row', { un: 'past', superseded: true }));
  gapAsks = () => ({ n: 14, ids: Array.from({ length: 14 }, (_, i) => 'P' + (1000 + i)) });
  const items = [0, 1].map(i => ({ id: 'gap-' + i, from: 'ensemble', to: m.from, ts: now - 60 + i, text: 'progress check ' + line }));
  box.insertAdjacentHTML('beforeend', gapRowHtml({ key: 'gap-w', seen: 1, first: 0, last: 1, idx: [0, 1] }, items, false, N));
  ROOM_OBJ.sendHows = { ...(ROOM_OBJ.sendHows || {}), 'wide-3': 'into-turn', 'wide-4': 'interrupted' };
  for (const id of ['wide-3', 'wide-4'])
    box.insertAdjacentHTML('beforeend', foldBalloonHtml({ id, from: 'user', to: m.from, ts: now, text: line }, 0, 'row', { }));
  return box.querySelectorAll('.msg-fold').length;
})()`;
// A sender too long for the share a phone's row keeps, as a desktop draws it.
const LONG_SENDER = `(() => {
  const box = document.getElementById('msgs');
  const m = { id: 'wide-2', from: 'a-sender-with-a-very-long-name-that-goes-on-and-on-and-on-and-on-and-on-and-on-and-on', to: 'user', ts: Date.now() / 1000, text: 'A line.' };
  box.insertAdjacentHTML('beforeend', foldBalloonHtml(m, 1, 'row', { }));
  return 0;
})()`;
const FRAME = `(document.body.classList.contains('detail-open') ? document.querySelector('#detail-panel iframe.dp-session') : document.querySelector('#po-panel iframe.po-session:not([hidden])'))`;
// What runs past the screen, with no box between it and the page to hold it.
const OVER = `(doc => {
  const win = doc.defaultView, W = win.innerWidth;
  const held = e => { for (let a = e.parentElement; a; a = a.parentElement) {
    if (a === doc.documentElement || a === doc.body) return false;
    const st = win.getComputedStyle(a);
    if (st.overflowX !== 'visible' && a.getBoundingClientRect().right <= W + 0.5) return true; } return false; };
  const vis = e => { const b = e.getBoundingClientRect(), st = win.getComputedStyle(e); return b.width > 0 && b.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  return [...doc.querySelectorAll('body *')].filter(e => vis(e) && e.getBoundingClientRect().right > W + 0.5 && !held(e))
    .map(e => (e.id || e.tagName + '.' + String(e.className).slice(0, 40)) + ':' + Math.round(e.getBoundingClientRect().right)).slice(0, 10);
})`;
const WIDTHS = doc => `(d => ({ inner: d.defaultView.innerWidth, root: d.documentElement.scrollWidth, over: ${OVER}(d),
  msgs: d.getElementById('msgs') ? [d.getElementById('msgs').scrollWidth, d.getElementById('msgs').clientWidth] : null,
  rows: d.querySelectorAll('.msg-fold').length,
  real: { chips: d.querySelectorAll('.msg-fold .chips .hub-kind').length, lozenges: d.querySelectorAll('.msg-fold .chips .lz').length, asks: d.querySelectorAll('.msg-fold.gap .gasks').length, sendHows: d.querySelectorAll('.msg-fold .from .send-how').length },
  timeCut: [...d.querySelectorAll('.msg-fold .from .at, .msg-fold .from .send-how')].filter(e => e.getBoundingClientRect().right > Math.min(e.closest('.from').getBoundingClientRect().right, d.defaultView.innerWidth) + 0.5).length, rootClip: d.defaultView.getComputedStyle(d.documentElement).overflowX }))(${doc})`;
const VIEWPORT = doc => `${doc}.querySelector('meta[name=viewport]').content`;
async function main() {
  const { ch, ws } = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (metrics, ua) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s);
    await c.send('Emulation.setDeviceMetricsOverride', { deviceScaleFactor: 3, mobile: true, ...metrics }, s);
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: metrics.mobile !== false, maxTouchPoints: 5 }, s);
    if (ua) await c.send('Emulation.setUserAgentOverride', { userAgent: ua }, s);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: "try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {}" }, s);
    const ev = async expression => { const r = await c.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw Error(expression.slice(0, 120) + ' ' + JSON.stringify(r.exceptionDetails).slice(0, 800)); return r.result.value; };
    const until = async (ex, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await ev(ex)) return; } catch (e) {} await sleep(150); } throw Error('timeout ' + ex); };
    const go = async url => { await c.send('Page.navigate', { url: A.base + url }, s); };
    return { ev, until, go, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const home = async p => { await p.go('/'); await p.until(`typeof PROJECTS !== "undefined" && !!PROJECTS && ALL_ROWS.some(r => r.roomId === ${JSON.stringify(A.task)})`); await sleep(400); };
  const inFrame = (p, js) => p.ev(`${FRAME}.contentWindow.eval(${JSON.stringify(js)})`);
  const chatReady = p => p.until(`!!${FRAME}?.contentWindow?.document.getElementById('input') && ${FRAME}.contentWindow.eval('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && !!ROOM_OBJ.participants')`);
  const banner = `(b => ({ shown: !b.hidden, text: b.textContent.trim() }))(document.getElementById('wide-banner') || { hidden: true, textContent: '' })`;
  try {
    for (const [w, h] of [[440, 956], [390, 844]]) {
      const o = out[w] = {}, p = await page({ width: w, height: h });
      await home(p);
      o.viewport = { index: await p.ev(VIEWPORT('document')) };
      o.list = await p.ev(WIDTHS('document'));
      o.closedPanel = await p.ev(`(b => [Math.round(b.left), Math.round(b.right)])(document.getElementById('detail-panel').getBoundingClientRect())`);
      o.banner = await p.ev(banner);
      await p.ev(`document.getElementById('me-btn').click(); 0`); await sleep(300);
      await p.ev(`document.querySelector('[data-me="usage"]').click(); 0`); await sleep(500);
      o.tray = await p.ev(WIDTHS('document'));
      o.trayShown = await p.ev(`!document.getElementById('usage-tray').hidden`);
      await home(p);
      await p.ev(`document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]').click(); 0`);
      await p.until(`document.body.classList.contains('po-dock') && !!document.querySelector('#po-panel iframe.po-session:not([hidden])')`);
      await chatReady(p); await sleep(600);
      await inFrame(p, WIDE_ROWS); await sleep(300);
      o.po = await p.ev(WIDTHS('document')); o.poChat = await p.ev(WIDTHS(`${FRAME}.contentDocument`));
      await home(p);
      await p.ev(`document.querySelector('#sw-list .sw-row[data-room="${A.task}"]').click(); 0`);
      await p.until(`document.body.classList.contains('detail-open') && !!document.querySelector('#detail-panel iframe.dp-session')`);
      await chatReady(p); await sleep(600);
      await inFrame(p, WIDE_ROWS); await sleep(300);
      o.task = await p.ev(WIDTHS('document')); o.taskChat = await p.ev(WIDTHS(`${FRAME}.contentDocument`));
      for (const [key, url] of [['session', `/session?id=${A.po}`], ['embed', `/session?id=${A.po}&embed=1`]]) {
        await p.go(url);
        await p.until(`typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && !!ROOM_OBJ.participants`); await sleep(500);
        await p.ev(WIDE_ROWS); await sleep(300);
        o[key] = await p.ev(WIDTHS('document'));
        o.viewport.session = await p.ev(VIEWPORT('document'));
      }
      await p.close();
    }
    // A desktop keeps the whole sender and its time.
    const d = await page({ width: 1728, height: 1117, mobile: false, deviceScaleFactor: 1 });
    await d.go(`/session?id=${A.po}`);
    await d.until(`typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && !!ROOM_OBJ.participants`); await sleep(500);
    await d.ev(WIDE_ROWS); await d.ev(LONG_SENDER); await sleep(300);
    out.desktop = await d.ev(WIDTHS('document'));
    await d.close();
    // The notice: what each screen says.
    const said = async (metrics, ua) => { const p = await page(metrics, ua); await home(p); const b = await p.ev(banner); await p.close(); return b; };
    out.desktopSite = await said({ width: 980, height: 2128, screenWidth: 440, screenHeight: 956 }, MAC);
    out.zoomedOut = await said({ width: 880, height: 1912, screenWidth: 440, screenHeight: 956 });
    out.landscape = await said({ width: 956, height: 440, screenWidth: 440, screenHeight: 956 });
    out.iPad = await said({ width: 820, height: 1180, screenWidth: 820, screenHeight: 1180 }, MAC);
  } finally { try { ch.kill(); } catch (e) {} }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(phone.NODE and phone.CHROME, 'needs Node and Chrome')
class PhoneWidth(unittest.TestCase):
    SCRIPT = JS

    @classmethod
    def setUpClass(cls):
        focus.PhoneFocus.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        phone.ThePhone.tearDownClass.__func__(cls)

    PHONES = ('440', '390')
    SCREENS = ('list', 'tray', 'po', 'poChat', 'task', 'taskChat', 'session', 'embed')

    def test_nothing_is_wider_than_the_screen(self):
        for w in self.PHONES:
            for name in self.SCREENS:
                with self.subTest(width=w, screen=name):
                    g = self.got[w][name]
                    self.assertEqual(g['inner'], int(w))
                    self.assertLessEqual(g['root'], int(w))
                    self.assertEqual(g['over'], [])

    def test_the_tray_was_open_when_measured(self):
        for w in self.PHONES:
            self.assertTrue(self.got[w]['trayShown'])

    def test_the_messages_do_not_scroll_sideways(self):
        for w in self.PHONES:
            for name in ('poChat', 'taskChat', 'session', 'embed'):
                with self.subTest(width=w, screen=name):
                    g = self.got[w][name]
                    self.assertGreaterEqual(g['rows'], 2)   # the wide rows are there
                    self.assertGreaterEqual(g['real']['chips'], 1)
                    self.assertGreaterEqual(g['real']['lozenges'], 1)
                    self.assertGreaterEqual(g['real']['asks'], 1)
                    self.assertEqual(g['real']['sendHows'], 2)
                    self.assertEqual(g['timeCut'], 0)
                    self.assertLessEqual(g['msgs'][0], g['msgs'][1])

    def test_a_desktop_keeps_a_long_sender_and_its_time(self):
        g = self.got['desktop']
        self.assertGreaterEqual(g['rows'], 3)
        self.assertEqual(g['timeCut'], 0)

    def test_the_closed_panel_waits_on_the_screen(self):
        for w in self.PHONES:
            with self.subTest(width=w):
                self.assertLessEqual(self.got[w]['closedPanel'][1], int(w))

    def test_the_session_page_clips_its_root(self):
        for w in self.PHONES:
            with self.subTest(width=w):
                self.assertEqual(self.got[w]['session']['rootClip'], 'hidden')

    def test_no_page_may_shrink_to_fit(self):
        for w in self.PHONES:
            for name, content in self.got[w]['viewport'].items():
                with self.subTest(width=w, page=name):
                    self.assertIn('shrink-to-fit=no', content)
        for name in ('fileview.html',):
            self.assertIn('shrink-to-fit=no', (phone.ROOT / name).read_text(encoding='utf-8'))

    def test_the_notice_stays_away_from_a_normal_screen(self):
        for what in (self.got['440']['banner'], self.got['390']['banner'], self.got['landscape'], self.got['iPad']):
            self.assertFalse(what['shown'], what)

    def test_the_notice_says_the_widths(self):
        self.assertTrue(self.got['desktopSite']['shown'])
        self.assertIn('980px wide on a 440px screen', self.got['desktopSite']['text'])
        self.assertIn('Request Mobile Website', self.got['desktopSite']['text'])
        self.assertTrue(self.got['zoomedOut']['shown'])
        self.assertIn('880px wide on a 440px screen', self.got['zoomedOut']['text'])


if __name__ == '__main__':
    unittest.main()
