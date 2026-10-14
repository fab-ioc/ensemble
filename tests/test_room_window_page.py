"""#228 step 2 in Chrome: the chat page holds its room's latest messages,
loads the page before as it scrolls up, loads down to a linked balloon it does
not hold, and still takes new messages.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_room_window_page
"""
from __future__ import annotations

import json
import subprocess
import unittest

import chatroom
from tests import chrome_profile
from tests.test_hidden_chat_polling import NODE, page_hub

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[process.argv.length - 1]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A), '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1000,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    const wsUrl = await new Promise((res, rej) => { let b = ''; ch.stderr.on('data', d => { b += d; const m = b.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
      setTimeout(() => rej(new Error('no devtools')), 20000); });
    const ws = new WebSocket(wsUrl); await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
    let id = 0; const waits = new Map();
    ws.onmessage = ev => { const m = JSON.parse(ev.data); const w = m.id && waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w.j(new Error(m.error.message)) : w.r(m.result); } };
    const send = (method, params = {}, sessionId) => new Promise((r, j) => { const i = ++id; waits.set(i, { r, j }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    const ev = async e => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true }, sessionId);
      if (r.exceptionDetails) throw new Error(e.slice(0, 100) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
    const until = async (e, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(e); } catch (x) {} if (v) return v; await sleep(100); } throw new Error('timeout: ' + e); };
    const roomBytes = `performance.getEntriesByType('resource').filter(e => e.name.includes('/api/room?')).map(e => [e.name.replace(/^.*\?/, ''), e.encodedBodySize])`;
    await send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
    await until('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && CHAT_DRAWN');
    const held = 'ROOM_OBJ.messages.length';
    const out = {};
    out.open = await ev(`({ held: ${held}, before: ROOM_OBJ.window.before, header: document.getElementById('info').textContent,
      first: ROOM_OBJ.messages[0].id, firstFetch: ${roomBytes}[0] })`);
    // A window that does not fill the chat gets the pages before it until it does.
    await until(`(b => b.scrollHeight > b.clientHeight || !roomHasOlder())(document.getElementById('msgs')) && !OLDER_BUSY`);
    out.filled = await ev(`(b => ({ held: ${held}, before: ROOM_OBJ.window.before, overflow: b.scrollHeight > b.clientHeight }))(document.getElementById('msgs'))`);
    // Scrolled to the top: the page before comes in, the place is kept.
    await ev(`(() => { const b = document.getElementById('msgs'); b.scrollTop = 0; b.dispatchEvent(new Event('scroll')); return 0; })()`);
    await until(`${held} > ${out.open.held}`);
    out.scrolled = await ev(`({ held: ${held}, before: ROOM_OBJ.window.before, first: ROOM_OBJ.messages[0].id, top: document.getElementById('msgs').scrollTop })`);
    // A full fetch (the cursor unknown, as after a hub restart) keeps the pages loaded.
    await ev(`(ROOM_LAST.revision = 'gone', refreshRoom().then(() => 0))`);
    out.refetched = await ev(`({ held: ${held}, before: ROOM_OBJ.window.before, first: ROOM_OBJ.messages[0].id,
      lastHeld: ROOM_LAST.room.messages.length })`);
    // A link to a balloon not held: loaded down to it, landed on.
    await ev(`gotoMsg('m3'), 0`);
    await until(`!!document.querySelector('#msgs [data-key="m3"]')`);
    out.goto = await ev(`({ held: ${held}, before: ROOM_OBJ.window.before, first: ROOM_OBJ.messages[0].id })`);
    // A new message still arrives, after everything held.
    console.log('POST');   // the test posts it
    await until(`ROOM_OBJ.messages.some(m => m.id && m.text === 'the newest one')`, 15000);
    out.after = await ev(`({ held: ${held}, last: ROOM_OBJ.messages[ROOM_OBJ.messages.length - 1].text, header: document.getElementById('info').textContent })`);
    // A page opened on a link to a balloon older than its window lands on it too.
    await send('Page.navigate', { url: A.base + '/session?room=' + A.room + '&msg=m7' }, sessionId);
    await until('typeof ROOM_OBJ !== "undefined" && !!ROOM_OBJ && CHAT_DRAWN');
    await until(`!!document.querySelector('#msgs .msg[data-mid="m7"]') || /not found/.test(document.body.innerText)`);
    out.opened = await ev(`({ landed: !!document.querySelector('#msgs .msg[data-mid="m7"]'), notFound: /not found/.test(document.body.innerText),
      first: ROOM_OBJ.messages[0].id })`);
    console.log(JSON.stringify(out));
  } finally { ch.kill(); }
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


@unittest.skipUnless(NODE and chrome_profile.CHROME, "node and Chrome are needed")
class RoomWindowPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        args = page_hub(cls, 400)
        proc = subprocess.Popen([NODE, "-e", CDP_JS, json.dumps(args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8")
        last = ""
        for line in proc.stdout:
            line = line.strip()
            if line == "POST":
                chatroom.post_message(args["room"], "claude", "the newest one")
            elif line:
                last = line
        err = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        assert proc.wait(timeout=240) == 0, err[-3000:]
        cls.got = json.loads(last)

    def test_opens_with_the_latest_messages_only(self):
        o = self.got["open"]
        self.assertIn("n=50", o["firstFetch"][0])
        self.assertLess(o["firstFetch"][1], 30000, "50 short messages, not the room")
        self.assertGreaterEqual(o["held"], 50)
        self.assertEqual(o["held"] + o["before"], 400)
        self.assertIn("400 messages", o["header"])
        self.assertEqual(o["first"], "m%d" % o["before"])

    def test_a_window_that_does_not_fill_the_chat_loads_more(self):
        f = self.got["filled"]
        self.assertTrue(f["overflow"] or f["before"] == 0, f)
        self.assertEqual(f["held"] + f["before"], 400)

    def test_scrolling_up_loads_the_page_before(self):
        s = self.got["scrolled"]
        # Pages of 100 until a screen of it is above the reader (older balloons fold short).
        self.assertGreater(s["held"], self.got["filled"]["held"])
        self.assertEqual(s["held"] + s["before"], 400)
        self.assertEqual(s["first"], "m%d" % s["before"])
        self.assertGreater(s["top"], 0, "the place is kept, not left at the top")

    def test_a_full_fetch_keeps_the_pages_loaded(self):
        s, r = self.got["scrolled"], self.got["refetched"]
        self.assertEqual((r["held"], r["before"], r["first"]), (s["held"], s["before"], s["first"]))
        self.assertEqual(r["lastHeld"], s["held"], "the poll's copy keeps them too, for the next delta")

    def test_a_page_opened_on_an_old_link_lands_on_it(self):
        o = self.got["opened"]
        self.assertTrue(o["landed"])
        self.assertFalse(o["notFound"])
        self.assertEqual(o["first"], "m2", "the page from the link's balloon, with 5 before it")

    def test_a_link_loads_down_to_its_balloon(self):
        g = self.got["goto"]
        self.assertEqual(g["before"], 0)
        self.assertEqual(g["first"], "m0")

    def test_new_messages_still_arrive(self):
        a = self.got["after"]
        self.assertEqual(a["last"], "the newest one")
        self.assertIn("401 messages", a["header"])


if __name__ == "__main__":
    unittest.main()
