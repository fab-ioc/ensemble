"""The session action bar in a real browser: the task panel (index.html) and the
popped-out window (session.html), served by a hub in a thread, driven by
headless Chrome over CDP at 1280x800 and 360x640.

* the More menu opens by keyboard and by click, is moved to <body> while it is
  open and put back after; Arrow/Home/End move through its items, Escape and
  Tab close it and give focus back to More, and a click outside closes it;
* an item that is off takes a click without anything else hearing it;
* at the bottom edge the menu opens above its button, always inside the window;
* Terminal colours opens beside its item, stays inside the window when the
  window shrinks, stays open against More once the menu closes, follows More
  when the page scrolls, and Escape gives focus back.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402

SCHEMES = [f"Scheme {i:02d}" for i in range(40)]

CDP_JS = r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = fs.mkdtempSync(path.join(A.tmp, 'chrome-'));
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1400,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c + ' ' + buf))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
const KEYS = { ArrowDown: 40, ArrowUp: 38, Home: 36, End: 35, Escape: 27, Tab: 9 };

// Everything one view is checked for. `more`: the selector of its More button.
async function drive(c, p, more, W, H) {
  const out = {};
  const ev = p.evalIn, Q = JSON.stringify(more);
  const key = async k => { for (const type of ['keyDown', 'keyUp'])
    await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: KEYS[k] }, p.sessionId); await sleep(60); };
  const click = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased'])
    await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, p.sessionId); await sleep(120); };
  const centre = async sel => ev(`(() => { const r = (${sel}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  const rect = 'r => r && ({ l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height })';
  const state = () => ev(`(() => {
    const box = ${rect};
    const more = document.querySelector(${Q});
    const m = document.querySelector('.am-menu:not([hidden])');
    const items = m ? [...m.querySelectorAll('.am-item')].filter(b => !b.hidden) : [];
    const a = document.activeElement;
    return { open: !!m, inBody: !!m && m.parentElement === document.body, expanded: more.getAttribute('aria-expanded'),
             focus: items.indexOf(a), n: items.length, onMore: a === more, placement: m ? m.dataset.placement : '',
             menu: box(m && m.getBoundingClientRect()), more: box(more.getBoundingClientRect()),
             home: !!more.parentElement.querySelector(':scope > .am-menu, .am-menu'), vw: innerWidth, vh: innerHeight };
  })()`);
  const focusMore = () => ev(`document.querySelector(${Q}).focus(); 0`);

  // Keyboard: open on ArrowDown at the first item, move, close with Escape.
  await focusMore(); await key('ArrowDown');
  out.kbOpen = await state();
  await key('ArrowDown'); out.down = (await state()).focus;
  await key('End'); out.end = (await state()).focus;
  await key('Home'); out.home = (await state()).focus;
  await key('ArrowUp'); out.wrap = (await state()).focus;
  await key('Escape'); out.escaped = await state();
  await focusMore(); await key('ArrowUp'); out.upOpen = (await state()).focus;
  await key('Tab'); out.tabbed = await state();

  // Mouse: open, an off item takes the click alone, a click outside closes.
  const [mx, my] = await centre(`document.querySelector(${Q})`);
  await click(mx, my); out.clickOpen = await state();
  await ev('window.__heard = 0; document.addEventListener("click", () => { window.__heard++; }); 0');
  const offSel = 'document.querySelector(".am-menu:not([hidden]) .am-item[aria-disabled=\\"true\\"]")';
  out.hasOff = await ev('!!' + offSel);
  if (out.hasOff) {
    await ev(offSel + '.scrollIntoView({ block: "nearest" }); 0');
    const [ox, oy] = await centre(offSel);
    await click(ox, oy);
    out.offClick = { heard: await ev('window.__heard'), open: (await state()).open };
  }
  await ev('document.body.dispatchEvent(new MouseEvent("click", { bubbles: true })); 0');
  out.outside = await state();

  // The bottom edge: its button moved to the foot of the window opens it above.
  await ev(`(() => { const b = document.querySelector(${Q}).closest('.am-bar'); const r = b.getBoundingClientRect();
    b.style.transform = 'translateY(' + (innerHeight - 12 - r.bottom) + 'px)'; })(); 0`);
  const [bx, by] = await centre(`document.querySelector(${Q})`);
  await click(bx, by); out.bottom = await state();
  await key('Escape');
  await ev(`document.querySelector(${Q}).closest('.am-bar').style.transform = ''; 0`);

  // Terminal colours: beside its item, inside the window.
  const [cx, cy] = await centre(`document.querySelector(${Q})`);
  await click(cx, cy);
  const item = 'document.querySelector(".am-menu:not([hidden]) .theme-dd-trigger")';
  await ev(item + '.scrollIntoView({ block: "nearest" }); 0');
  const [ix, iy] = await centre(item);
  await click(ix, iy);
  await p.until('!!document.querySelector(".theme-dd-menu") && document.querySelectorAll(".theme-dd-menu .name").length > 0');
  await sleep(150);
  const picker = () => ev(`(() => { const box = ${rect}; const pk = document.querySelector('.theme-dd-menu');
    const it = document.querySelector('.am-menu:not([hidden]) .theme-dd-trigger'); const more = document.querySelector(${Q});
    return { picker: box(pk && pk.getBoundingClientRect()), placement: pk && pk.dataset.placement, item: box(it && it.getBoundingClientRect()),
             more: box(more.getBoundingClientRect()), menuOpen: !!document.querySelector('.am-menu:not([hidden])'),
             vw: innerWidth, vh: innerHeight, focusIn: !!(pk && pk.contains(document.activeElement)), onMore: document.activeElement === more }; })()`);
  out.picker = await picker();
  // The window shrinks: still inside it.
  await c.send('Emulation.setDeviceMetricsOverride', { width: W - 60, height: H - 120, deviceScaleFactor: 1, mobile: false }, p.sessionId);
  await sleep(300);
  out.pickerResized = await picker();
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false }, p.sessionId);
  await sleep(300);
  // The menu closes first: the picker stays, against More.
  await ev('SessionActions.closeMenu(); 0'); await sleep(100);
  out.pickerAlone = await picker();
  // The page scrolls under it (More moves): it follows.
  await ev(`document.querySelector(${Q}).closest('.am-bar').style.transform = 'translate(-24px, 30px)'; document.dispatchEvent(new Event('scroll')); 0`);
  await sleep(150);
  out.pickerScrolled = await picker();
  await ev(`document.querySelector(${Q}).closest('.am-bar').style.transform = 'translate(-24px, 60px)'; window.dispatchEvent(new Event('scroll')); 0`);
  await sleep(150);
  out.pickerWindowScrolled = await picker();
  await ev(`document.querySelector(${Q}).closest('.am-bar').style.transform = ''; 0`);
  // Escape in the picker: gone, and focus back on More.
  await ev('document.querySelector(".theme-dd-menu .theme-dd-search").focus(); 0');
  await key('Escape');
  out.pickerClosed = { gone: await ev('!document.querySelector(".theme-dd-menu")'), onMore: await ev(`document.activeElement === document.querySelector(${Q})`) };
  return out;
}

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [W, H] of [[1280, 800], [360, 640]]) {
      const page = async (url, ready) => {
        const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
        const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
        await c.send('Page.enable', {}, sessionId);
        await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false }, sessionId);
        const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
        const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
        await c.send('Page.navigate', { url }, sessionId);
        const p = { evalIn, until, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
        await until(ready);
        return p;
      };
      {
        const p = await page(A.base + '/', 'typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.room) + ')');
        await p.evalIn('openDetail(ALL_ROWS.find(r => r.roomId === ' + JSON.stringify(A.room) + ').sessionId); 0');
        await p.until('(() => { const b = document.querySelector("#detail-panel .am-more"); return !!b && b.getClientRects().length > 0; })()');
        await sleep(600);
        out['docked/' + W] = await drive(c, p, '#detail-panel .am-more', W, H);
        await p.close();
      }
      {
        const p = await page(A.base + '/session?room=' + encodeURIComponent(A.room),
                             '(() => { const b = document.querySelector("#actions .am-more"); return !!b && b.getClientRects().length > 0; })()');
        await sleep(300);
        out['popout/' + W] = await drive(c, p, '#actions .am-more', W, H);
        await p.close();
      }
    }
  } finally {
    try { c.ws.close(); } catch (e) {}
    const gone = new Promise(r => ch.on('exit', r));
    ch.kill();
    await Promise.race([gone, sleep(5000)]);
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    """A hub in a thread with one stopped one-agent task; its colour schemes
    are a fixed list, so the picker has something to show on any machine."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-act-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "list_presets", lambda: list(SCHEMES)),
            mock.patch.object(dashboard, "load_favorite_themes", lambda: []),
            mock.patch.object(dashboard, "current_theme_for_cwd", lambda cwd: ""),
            mock.patch.dict(dashboard.BACKEND.features, {"themes": True}),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        work = root / "Motors"
        work.mkdir()
        room = chatroom.create_room("Actions", [{"identity": "claude", "agent": "claude", "cwd": str(work)}])
        cls.room = room["id"]
        chatroom.patch_room(cls.room, cwd=str(work))
        chatroom.post_message(cls.room, "user", "hello")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {"chrome": CHROME, "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": cls.room}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        cls.err = out.stderr
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    VIEWS = ("docked/1280", "popout/1280", "docked/360", "popout/360")

    def inside(self, r, vw, vh, margin=8):
        self.assertGreaterEqual(r["l"], margin - 0.5, r)
        self.assertGreaterEqual(r["t"], margin - 0.5, r)
        self.assertLessEqual(r["r"], vw - margin + 0.5, (r, vw))
        self.assertLessEqual(r["b"], vh - margin + 0.5, (r, vh))

    def test_the_keyboard_opens_moves_and_closes_the_menu(self):
        for v in self.VIEWS:
            with self.subTest(v):
                g = self.got[v]
                k = g["kbOpen"]
                self.assertTrue(k["open"] and k["inBody"], k)
                self.assertEqual((k["expanded"], k["focus"]), ("true", 0))
                self.assertGreater(k["n"], 4)
                self.assertEqual((g["down"], g["end"], g["home"], g["wrap"]), (1, k["n"] - 1, 0, k["n"] - 1))
                for closed in ("escaped", "tabbed"):
                    e = g[closed]
                    self.assertFalse(e["open"], closed)
                    self.assertTrue(e["onMore"], f"{closed}: focus back on More")
                    self.assertEqual(e["expanded"], "false")
                    self.assertTrue(e["home"], f"{closed}: the menu is back in its bar")
                self.assertEqual(g["upOpen"], k["n"] - 1, "ArrowUp on More opens at the last item")
                self.inside(k["menu"], k["vw"], k["vh"])

    def test_clicks_open_it_off_items_do_nothing_and_outside_closes(self):
        for v in self.VIEWS:
            with self.subTest(v):
                g = self.got[v]
                self.assertTrue(g["clickOpen"]["open"])
                self.assertEqual(g["clickOpen"]["focus"], -1, "a mouse open leaves focus on More")
                self.assertTrue(g["hasOff"], "Make PO is off: the hub has not said")
                self.assertEqual(g["offClick"], {"heard": 0, "open": True})
                self.assertFalse(g["outside"]["open"])

    def test_at_the_bottom_edge_it_opens_above(self):
        for v in self.VIEWS:
            with self.subTest(v):
                b = self.got[v]["bottom"]
                self.assertTrue(b["open"])
                self.assertEqual(b["placement"], "above", b)
                self.assertLessEqual(b["menu"]["b"], b["more"]["t"], b)
                self.inside(b["menu"], b["vw"], b["vh"])

    def test_terminal_colours_opens_beside_its_item_and_stays_in_the_window(self):
        for v in self.VIEWS:
            with self.subTest(v):
                p = self.got[v]["picker"]
                pk, it = p["picker"], p["item"]
                self.assertTrue(p["menuOpen"] and p["focusIn"], p)
                near = {"right": abs(pk["l"] - (it["r"] + 4)), "left": abs(pk["r"] - (it["l"] - 4)),
                        "below": abs(pk["t"] - (it["b"] + 4)), "above": abs(pk["b"] - (it["t"] - 4))}[p["placement"]]
                self.assertLessEqual(near, 1, p)
                if v.endswith("/1280"):
                    self.assertIn(p["placement"], ("right", "left"), "room beside it on a desktop")
                self.inside(pk, p["vw"], p["vh"])
                r = self.got[v]["pickerResized"]
                self.inside(r["picker"], r["vw"], r["vh"])

    def test_the_picker_outlives_the_menu_and_follows_more(self):
        for v in self.VIEWS:
            with self.subTest(v):
                a, s = self.got[v]["pickerAlone"], self.got[v]["pickerScrolled"]
                for p in (a, s):
                    self.assertFalse(p["menuOpen"])
                    pk, mo = p["picker"], p["more"]
                    self.assertTrue(abs(pk["t"] - (mo["b"] + 4)) <= 1 or abs(pk["b"] - (mo["t"] - 4)) <= 1, p)
                    self.inside(pk, p["vw"], p["vh"])
                self.assertAlmostEqual(s["more"]["t"] - a["more"]["t"], 30, delta=1)
                self.assertAlmostEqual(s["picker"]["t"] - a["picker"]["t"], 30, delta=1, msg="it moved with More")
                w = self.got[v]["pickerWindowScrolled"]
                self.assertAlmostEqual(w["picker"]["t"] - a["picker"]["t"], 60, delta=1, msg="a scroll of the window too")
                self.assertEqual(self.got[v]["pickerClosed"], {"gone": True, "onMore": True})


if __name__ == "__main__":
    unittest.main()
