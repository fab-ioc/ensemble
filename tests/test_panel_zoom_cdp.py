"""A file or a diff zooms on its own (#423, GitHub issue 69).

* static/panelzoom.js (PanelZoom): the steps, the level kept per kind of file
  in localStorage 'cd-zoom', the keys it reads.
* In a real browser (a hub in a thread, headless Chrome over CDP), the file
  view of Markdown, code, an HTML page, a CSV and a picture: − 100% + in the
  header zooms the file alone (the header keeps its size, the browser's zoom
  is untouched), Ctrl + wheel over it, Ctrl + = and Ctrl + 0; the level is
  kept per kind, comes back after a load and a Reload; what is under the
  pointer stays there; a click in the code's gutter still picks its line; a
  PDF has no control. On a phone (touch, 360 px) the header fits and ⋯ holds
  Reload, Comment and Raw. In Changes, a diff zooms on its own, and keeps its
  level across Unified / Side by side.

The browser part is skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402


def _png(w: int, h: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = b"".join(b"\x00" + b"\x30\x60\x90" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1100,700', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
async function page(c, phone) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  if (phone) {
    await c.send('Emulation.setDeviceMetricsOverride', { width: 360, height: 740, deviceScaleFactor: 3, mobile: true }, sessionId);
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
  }
  const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
  const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  // Ctrl + wheel at (x, y), as a mouse sends it: one notch.
  const wheel = async (x, y, dy) => { await c.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY: dy, modifiers: 2 }, sessionId); await sleep(120); };
  // Ctrl + a key.
  const key = async (k, code, vk) => {
    await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk, modifiers: 2 }, sessionId);
    await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, modifiers: 2 }, sessionId);
    await sleep(80);
  };
  const click = async (x, y) => {
    await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, sessionId);
    await sleep(120);
  };
  // Two fingers from d0 apart to d1 apart, around (x, y).
  const pinch = async (x, y, d0, d1) => {
    const pts = d => [{ x: x - d / 2, y, id: 1 }, { x: x + d / 2, y, id: 2 }];
    await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pts(d0) }, sessionId);
    for (let k = 1; k <= 6; k++) { await c.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pts(d0 + (d1 - d0) * k / 6) }, sessionId); await sleep(40); }
    await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, sessionId);
    await sleep(200);
  };
  // With ENS_SHOTS set, a picture of the page there (to set beside the drawing).
  const shot = async name => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
    require('fs').writeFileSync(require('path').join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  return { ev, until, wheel, key, click, pinch, shot, go: async (url, ready) => { await c.send('Page.navigate', { url }, sessionId); await until(ready); }, close: () => c.send('Target.closeTarget', { targetId }) };
}
const READY = `typeof FVZ !== 'undefined' && document.readyState === 'complete'`;
// The control and the header, as they are drawn.
const CTRL = `(() => { const s = document.getElementById('fv-zoom'); const r = s.querySelector('[data-pz=reset]'); const h = document.querySelector('header');
  return { shown: !s.hidden && s.getBoundingClientRect().width > 0, text: r.textContent, pressed: r.getAttribute('aria-pressed'), next: s.nextElementSibling.id,
    out: s.querySelector('[data-pz=out]').disabled, in: s.querySelector('[data-pz=in]').disabled,
    headH: Math.round(h.getBoundingClientRect().height), headFont: getComputedStyle(h.querySelector('.name') || h).fontSize,
    dpr: devicePixelRatio, vv: visualViewport.scale, store: localStorage.getItem('cd-zoom') }; })()`;
const press = what => `(() => { document.querySelector('#fv-zoom [data-pz=${what}]').click(); return 1; })()`;

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const p = await page(c, false);
    // ---- Markdown -------------------------------------------------------
    await p.go(A.mdUrl, READY);
    await p.until(`document.querySelector('main .md h2')`);
    await p.ev(`localStorage.removeItem('cd-zoom'), 1`);
    await p.go(A.mdUrl, READY);
    await p.until(`document.querySelector('main .md h2')`);
    const md = out.md = {};
    md.start = await p.ev(CTRL);
    md.zoom0 = await p.ev(`document.querySelector('.md').style.zoom`);
    await p.ev(press('in'));
    md.plus = await p.ev(CTRL);
    md.zoomPlus = await p.ev(`document.querySelector('.md').style.zoom`);
    // Keys on the header (Reload focused) and a wheel over it are the browser's.
    await p.ev(`document.getElementById('fv-reload').focus(), 1`);
    await p.key('=', 'Equal', 187);
    const hb = await p.ev(`(() => { const r = document.querySelector('header').getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
    await p.wheel(hb.x, hb.y, -100);
    md.header = await p.ev(`document.querySelector('.md').style.zoom`);
    // A click in the text gives main the focus: the keys zoom the file.
    const tx = await p.ev(`(() => { const r = document.querySelector('.md p').getBoundingClientRect(); return { x: Math.round(r.left + 10), y: Math.round(r.top + 4) }; })()`);
    await p.click(tx.x, tx.y);
    md.focus = await p.ev(`document.activeElement && document.activeElement.id`);
    await p.key('=', 'Equal', 187);
    md.key = await p.ev(`document.querySelector('.md').style.zoom`);
    await p.key('0', 'Digit0', 48);
    md.reset = await p.ev(CTRL);
    await p.key('-', 'Minus', 189);
    md.keyOut = await p.ev(`document.querySelector('.md').style.zoom`);
    await p.key('0', 'Digit0', 48);
    // Ctrl + wheel over paragraph 30: it stays under the pointer.
    await p.ev(`(() => { const e = [...document.querySelectorAll('.md p')].find(x => /Paragraph 30 /.test(x.textContent)); e.scrollIntoView({ block: 'center' }); return 1; })()`);
    const at = await p.ev(`(() => { const e = [...document.querySelectorAll('.md p')].find(x => /Paragraph 30 /.test(x.textContent)); const r = e.getBoundingClientRect(); return { x: Math.round(r.left + 40), y: Math.round(r.top + r.height / 2) }; })()`);
    await p.wheel(at.x, at.y, -100);
    await p.wheel(at.x, at.y, -100);
    md.wheel = await p.ev(`(() => { const e = document.elementFromPoint(${at.x}, ${at.y}); return { zoom: document.querySelector('.md').style.zoom, under: e && e.textContent.slice(0, 13) }; })()`);
    md.wheelCtrl = await p.ev(CTRL);
    // Kept: 125% after a load, and after a Reload of the file.
    await p.go(A.mdUrl, READY);
    await p.until(`document.querySelector('main .md h2')`);
    md.loaded = { zoom: await p.ev(`document.querySelector('.md').style.zoom`), text: (await p.ev(CTRL)).text };
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await sleep(600);
    md.reloaded = await p.ev(`document.querySelector('.md').style.zoom`);
    // A comment on words, at 150%: the mark is on those words.
    await p.ev(press('in'));
    md.at150 = await p.ev(`document.querySelector('.md').style.zoom`);
    await p.ev(`(() => { scrollTo(0, 0); document.querySelector('#fv-zoom [data-pz=reset]').blur(); return 1; })()`);
    await p.shot('desktop-md-150');
    await p.ev(`(() => { const t = _cRoot.textContent; const q = 'The gearbox runs at seven to one.'; const s = t.indexOf(q);
      FVC.put({ cid: FVC.id(), at: FVC.now(), note: 'Right?', view: CUR_VIEW, start: s, end: s + q.length, quote: q }); clearHl(); remarkAll(); renderTray(); return 1; })()`);
    md.mark = await p.ev(`(() => { const m = document.querySelector('mark.cmt-hl'); return m ? m.textContent : null; })()`);
    await p.ev(`(() => { FVC.remove(CMTS.map(c => c.cid)); clearHl(); renderTray(); return 1; })()`);
    // The ends: − at 50%, + at 300% disabled.
    await p.ev(`(() => { for (let i = 0; i < 20; i++) document.querySelector('#fv-zoom [data-pz=in]').click(); return 1; })()`);
    md.max = await p.ev(CTRL);
    await p.ev(`(() => { for (let i = 0; i < 20; i++) document.querySelector('#fv-zoom [data-pz=out]').click(); return 1; })()`);
    md.min = await p.ev(CTRL);
    await p.ev(press('reset'));
    await p.ev(press('in')); await p.ev(press('in')); await p.ev(press('in'));   // 150% for Markdown

    // ---- Code -------------------------------------------------------------
    await p.go(A.codeUrl, READY);
    await p.until(`document.querySelector('.code .ln-row')`);
    const code = out.code = {};
    code.start = (await p.ev(CTRL)).text;
    await p.ev(press('in')); await p.ev(press('in')); await p.ev(press('in')); await p.ev(press('in'));   // 175%
    code.zoom = await p.ev(`document.querySelector('.code').style.zoom`);
    code.ctrl = await p.ev(CTRL);
    // The gutter of line 12, clicked: line 12 is marked.
    const g = await p.ev(`(() => { const r = document.querySelector('.code .ln-row[data-n="12"]').getBoundingClientRect(); return { x: Math.round(r.left + 6), y: Math.round(r.top + r.height / 2) }; })()`);
    await p.click(g.x, g.y);
    code.marked = await p.ev(`[...document.querySelectorAll('.code .ln-row.is-marked')].map(r => r.dataset.n)`);
    // Wheel over line 40 keeps line 40 under the pointer.
    const l40 = await p.ev(`(() => { const e = document.querySelector('.code .ln-row[data-n="40"]'); e.scrollIntoView({ block: 'center' }); const r = e.getBoundingClientRect(); return { x: Math.round(r.left + 120), y: Math.round(r.top + r.height / 2) }; })()`);
    await p.wheel(l40.x, l40.y, 100);
    code.wheel = await p.ev(`(() => { const e = document.elementFromPoint(${l40.x}, ${l40.y}); const row = e && e.closest('.ln-row'); return { zoom: document.querySelector('.code').style.zoom, row: row && row.dataset.n }; })()`);

    // ---- An HTML page -----------------------------------------------------
    await p.go(A.htmlUrl, READY);
    await p.until(`(() => { const f = document.querySelector('iframe.file'); return f && f.contentDocument && /Line 5 /.test(f.contentDocument.body.textContent) && FVZ.kind === 'page'; })()`);
    const html = out.html = {};
    html.start = (await p.ev(CTRL)).text;
    await p.ev(press('in')); await p.ev(press('in'));
    html.ctrl = await p.ev(CTRL);
    html.body = await p.ev(`document.querySelector('iframe.file').contentDocument.body.style.zoom`);
    html.root = await p.ev(`document.querySelector('iframe.file').contentDocument.documentElement.style.zoom`);
    // Ctrl + = with the focus in the page.
    await p.ev(`(() => { const f = document.querySelector('iframe.file'); f.focus(); f.contentWindow.focus(); return 1; })()`);
    await p.key('=', 'Equal', 187);
    html.key = await p.ev(`document.querySelector('iframe.file').contentDocument.body.style.zoom`);
    // Ctrl + wheel over the page's frame.
    const fr = await p.ev(`(() => { const r = document.querySelector('iframe.file').getBoundingClientRect(); return { x: Math.round(r.left + 100), y: Math.round(r.top + 100) }; })()`);
    await p.wheel(fr.x, fr.y, 100);
    html.wheel = await p.ev(`document.querySelector('iframe.file').contentDocument.body.style.zoom`);
    html.dpr = await p.ev(`devicePixelRatio`);

    // ---- A CSV --------------------------------------------------------------
    await p.go(A.csvUrl, READY);
    await p.until(`document.querySelector('table.csv')`);
    out.csv = { start: (await p.ev(CTRL)).text };
    await p.ev(press('out'));
    out.csv.zoom = await p.ev(`document.querySelector('table.csv').style.zoom`);
    out.csv.kind = await p.ev(`FVZ.kind`);

    // ---- A picture ----------------------------------------------------------
    await p.go(A.pngUrl, READY);
    await p.until(`(() => { const i = document.querySelector('img.file'); return i && i.complete && i.naturalWidth && FVZ.kind === 'img'; })()`);
    const img = out.img = { start: (await p.ev(CTRL)).text, w0: await p.ev(`document.querySelector('img.file').clientWidth`) };
    await p.ev(press('in')); await p.ev(press('in')); await p.ev(press('in')); await p.ev(press('in')); await p.ev(press('in'));   // 200%
    img.w = await p.ev(`document.querySelector('img.file').clientWidth`);
    img.scrolls = await p.ev(`(() => { const m = document.getElementById('main'); return m.scrollWidth > m.clientWidth; })()`);
    img.pageScroll = await p.ev(`document.scrollingElement.scrollWidth > document.scrollingElement.clientWidth`);
    await p.ev(press('reset'));
    img.back = await p.ev(`document.querySelector('img.file').clientWidth`);
    // A picture wider than the pane: every level is its 100% fit times the level, the pane in place.
    await p.go(A.bigUrl, READY);
    await p.until(`(() => { const i = document.querySelector('img.file'); return i && i.complete && i.naturalWidth && FVZ.kind === 'img'; })()`);
    const bigAt = `({ w: document.querySelector('img.file').getBoundingClientRect().width, left: Math.round(document.getElementById('main').getBoundingClientRect().left), mw: document.getElementById('main').clientWidth })`;
    img.big = { fit: await p.ev(bigAt) };
    await p.ev(press('out'));
    img.big.z90 = await p.ev(bigAt);
    await p.ev(press('in')); await p.ev(press('in'));
    img.big.z110 = await p.ev(bigAt);
    img.big.edge = await p.ev(`(() => { const c = getComputedStyle(document.querySelector('img.file')); return parseFloat(c.borderLeftWidth) + parseFloat(c.borderRightWidth) + parseFloat(c.paddingLeft) + parseFloat(c.paddingRight); })()`);
    await p.ev(press('reset'));

    // ---- A PDF --------------------------------------------------------------
    await p.go(A.pdfUrl, READY);
    await p.until(`document.querySelector('main iframe.file')`);
    out.pdf = await p.ev(`document.getElementById('fv-zoom').hidden`);

    // ---- Kept per kind --------------------------------------------------------
    out.store = await p.ev(`JSON.parse(localStorage.getItem('cd-zoom'))`);

    // ---- A diff in Changes ------------------------------------------------------
    await p.go(A.base + '/', `typeof drShow === 'function' && typeof PanelZoom !== 'undefined'`);
    out.diff = await p.ev(`(async () => {
      const box = document.createElement('div'); box.style.cssText = 'position:fixed;left:0;top:0;width:1000px;height:600px;overflow:auto;background:#fff;z-index:9999'; document.body.appendChild(box);
      const rv = drReview('zoom', { open: null, target: () => null });
      localStorage.setItem('cd-diff-mode', 'unified');
      const v = drShow(rv, box, 'root', 'src/app.py', ${JSON.stringify(A.diff)});
      const r = {}, z = () => box.querySelector('.drv').style.zoom, seg = () => box.querySelector('.drv-head .pz');
      r.seg = !!seg(); r.before = seg() && seg().nextElementSibling && seg().nextElementSibling.className;
      r.headFont = getComputedStyle(box.querySelector('.drv-head')).fontSize;
      seg().querySelector('[data-pz=in]').click(); seg().querySelector('[data-pz=in]').click();
      r.zoom = z(); r.text = seg().querySelector('[data-pz=reset]').textContent;
      r.headFontAfter = getComputedStyle(box.querySelector('.drv-head')).fontSize;
      r.headZoom = box.querySelector('.drv-head').style.zoom;
      box.querySelector('.drv-mode [data-mode=split]').click();
      r.split = !!box.querySelector('.drv.split'); r.zoomSplit = z(); r.textSplit = seg().querySelector('[data-pz=reset]').textContent;
      // A click on line 3's gutter still opens the comment box on line 3.
      const row = [...box.querySelectorAll('.drv .dr[data-i]')].find(e => e.querySelector('.dg'));
      r.store = JSON.parse(localStorage.getItem('cd-zoom')).diff;
      box.scrollTop = 0; window.__zbox = box;
      return r; })()`);
    await p.shot('desktop-diff-125');
    // Ctrl + wheel and Ctrl + 0 on the diff.
    // On the diff's bar (the % focused, a wheel over it) the keys and wheel are the browser's.
    await p.ev(`(() => { __zbox.querySelector('.drv-head .pz [data-pz=reset]').focus(); return 1; })()`);
    await p.key('=', 'Equal', 187);
    const dh = await p.ev(`(() => { const r = __zbox.querySelector('.drv-head .drv-path').getBoundingClientRect(); return { x: Math.round(r.left + 4), y: Math.round(r.top + r.height / 2) }; })()`);
    await p.wheel(dh.x, dh.y, -100);
    out.diff.header = await p.ev(`__zbox.querySelector('.drv').style.zoom`);
    await p.ev(`(() => { __zbox.querySelector('.drv').focus(); return 1; })()`);
    await p.wheel(300, 300, -100);
    out.diff.wheel = await p.ev(`__zbox.querySelector('.drv').style.zoom`);
    await p.key('0', 'Digit0', 48);
    out.diff.reset = await p.ev(`({ zoom: __zbox.querySelector('.drv').style.zoom, dpr: devicePixelRatio })`);
    await p.close();

    // ---- A phone --------------------------------------------------------------
    const q = await page(c, true);
    // Opened from a folder (Back shown), with Preview / Source: the worst case for room.
    await q.go(A.phoneUrl, READY);
    await q.until(`document.querySelector('main .md h2') && !document.getElementById('out-menu').hidden && !document.getElementById('back').hidden`);
    out.phone = await q.ev(`(() => { const h = document.querySelector('header'); const vis = [...h.children].filter(e => e.getBoundingClientRect().width > 0);
      const right = Math.max(...vis.map(e => e.getBoundingClientRect().right));
      const m = document.getElementById('out-menu');
      return { coarse: matchMedia('(pointer: coarse)').matches, w: innerWidth, right: Math.round(right), scroll: h.scrollWidth <= h.clientWidth,
        zoom: !document.getElementById('fv-zoom').hidden, reload: getComputedStyle(document.getElementById('fv-reload')).display,
        menu: m.textContent.trim(), name: Math.round(document.getElementById('name').getBoundingClientRect().width + (h.querySelector('.grow')?.getBoundingClientRect().width || 0)),
        nameCut: (n => n.scrollWidth > n.clientWidth)(document.getElementById('name')),
        back: document.getElementById('back').textContent.split(String.fromCharCode(160)).join(' ').trim() === '‹ Back' && getComputedStyle(document.querySelector('#back .lbl')).display, views: getComputedStyle(document.getElementById('views')).display, label: m.getAttribute('aria-label'), touch: getComputedStyle(document.getElementById('main')).touchAction,
        buttons: [...document.querySelectorAll('#fv-zoom button')].map(b => Math.round(b.getBoundingClientRect().height)) }; })()`);
    // Two fingers on the text: 150% × 1.2 = 180, snapped to 175; the page keeps its scale.
    out.phone.pinchFrom = await q.ev(`document.querySelector('.md').style.zoom`);
    await q.pinch(180, 400, 100, 120);
    await q.ev(`scrollTo(0, 0), 1`);
    await q.shot('phone-md-175');
    out.phone.pinch = await q.ev(`({ zoom: document.querySelector('.md').style.zoom, text: document.querySelector('#fv-zoom [data-pz=reset]').textContent, vv: visualViewport.scale })`);
    await q.ev(`document.getElementById('out-menu').click(), 1`);
    out.phone.rows = await q.ev(`[...document.querySelectorAll('.out-pop button')].map(b => b.textContent.trim())`);
    out.phone.checked = await q.ev(`[...document.querySelectorAll('.out-pop [role=menuitemradio]')].map(b => b.dataset.out + ':' + b.getAttribute('aria-checked'))`);
    out.phone.seps = await q.ev(`document.querySelectorAll('.out-pop .out-sep').length`);
    // Source from the menu: the view switches, and Markdown's level stays on the rendered text only.
    await q.ev(`document.querySelector('.out-pop [data-out=view-source]').click(), 1`);
    await q.until(`document.querySelector('.code .ln-row')`);
    out.phone.source = await q.ev(`({ view: CUR_VIEW, kind: FVZ.kind, menuGone: !document.querySelector('.out-pop') })`);
    await q.close();
  } finally {
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _md() -> str:
    body = "\n\n".join(f"Paragraph {i} of the plan, long enough to fill a line or two of the view at any zoom." for i in range(60))
    return f"# Plan\n\n## Gearbox\n\nThe gearbox runs at seven to one.\n\n{body}\n"


DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,6 +1,7 @@
 import sys
-def main(argv):
+def main(argv=None):
+    print('hello')
     return 0
 def helper(x):
-    return x + 1
+    return x + 2
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pz-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state, files = base / "state", base / "files"
        state.mkdir()
        files.mkdir()
        for p in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        (files / "plan.md").write_text(_md(), encoding="utf-8")
        (files / "run.py").write_text("".join(f"x{i} = {i}  # a line of code\n" for i in range(1, 201)), encoding="utf-8")
        (files / "page.html").write_text("<h1>A page</h1>" + "".join(f"<p>Line {i} of the page.</p>" for i in range(120)), encoding="utf-8")
        (files / "data.csv").write_text("name,count\n" + "".join(f"row{i},{i}\n" for i in range(40)), encoding="utf-8")
        (files / "pic.png").write_bytes(_png(300, 120))
        (files / "big.png").write_bytes(_png(2000, 200))
        (files / "doc.pdf").write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.server.server_address[1]}"
        url = lambda n: f"{b}/fileview?path={quote(str(files / n))}"  # noqa: E731
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": b, "shots": os.environ.get("ENS_SHOTS", ""), "diff": DIFF,
                "mdUrl": url("plan.md"), "codeUrl": url("run.py"), "htmlUrl": url("page.html"),
                "csvUrl": url("data.csv"), "phoneUrl": url("plan.md") + "&back=" + quote(f"/fileview?path={quote(str(files))}"), "pngUrl": url("pic.png"), "bigUrl": url("big.png"), "pdfUrl": url("doc.pdf")}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_control_sits_before_reload_at_100(self):
        s = self.got["md"]["start"]
        self.assertTrue(s["shown"], s)
        self.assertEqual((s["text"], s["pressed"], s["next"]), ("100%", "false", "fv-reload"))
        self.assertEqual(self.got["md"]["zoom0"], "")

    def test_plus_zooms_the_file_alone(self):
        md = self.got["md"]
        self.assertEqual(md["zoomPlus"], "1.1")
        p, s = md["plus"], md["start"]
        self.assertEqual((p["text"], p["pressed"]), ("110%", "true"))
        self.assertEqual((p["headH"], p["headFont"], p["dpr"], p["vv"]), (s["headH"], s["headFont"], s["dpr"], s["vv"]), "the header and the browser keep their size")
        self.assertEqual(json.loads(p["store"]), {"md": 110})

    def test_the_keys(self):
        md = self.got["md"]
        self.assertEqual(md["header"], "1.1", "keys on the header and a wheel over it leave the file alone")
        self.assertEqual(md["focus"], "main", "a click in the text focuses the file")
        self.assertEqual(md["key"], "1.25", "Ctrl + = one step in")
        self.assertEqual((md["reset"]["text"], md["reset"]["pressed"]), ("100%", "false"), "Ctrl + 0 back to 100%")
        self.assertIsNone(md["reset"]["store"] and json.loads(md["reset"]["store"]).get("md"), "100% is not stored")
        self.assertEqual(md["keyOut"], "0.9", "Ctrl + − one step out")

    def test_the_wheel_keeps_what_is_under_the_pointer(self):
        w = self.got["md"]["wheel"]
        self.assertEqual(w["zoom"], "1.25", "two notches: 110, 125")
        self.assertEqual(w["under"], "Paragraph 30 ")
        self.assertEqual(self.got["md"]["wheelCtrl"]["dpr"], self.got["md"]["start"]["dpr"], "the browser did not zoom")

    def test_the_level_is_kept_after_a_load_and_a_reload(self):
        self.assertEqual(self.got["md"]["loaded"], {"zoom": "1.25", "text": "125%"})
        self.assertEqual(self.got["md"]["reloaded"], "1.25")

    def test_a_comment_marks_its_words_when_zoomed(self):
        self.assertEqual(self.got["md"]["at150"], "1.5")
        self.assertEqual(self.got["md"]["mark"], "The gearbox runs at seven to one.")

    def test_the_ends(self):
        mx, mn = self.got["md"]["max"], self.got["md"]["min"]
        self.assertEqual((mx["text"], mx["in"], mx["out"]), ("300%", True, False))
        self.assertEqual((mn["text"], mn["in"], mn["out"]), ("50%", False, True))

    def test_code_zooms_and_its_gutter_still_picks_the_line(self):
        c = self.got["code"]
        self.assertEqual(c["start"], "100%", "code has its own level, not Markdown's")
        self.assertEqual(c["zoom"], "1.75")
        self.assertEqual(c["marked"], ["12"])
        self.assertEqual(c["wheel"], {"zoom": "1.5", "row": "40"})

    def test_a_page_zooms_its_body(self):
        h = self.got["html"]
        self.assertEqual(h["start"], "100%")
        self.assertEqual((h["body"], h["root"]), ("1.25", ""), "the body, so the comment layer on the root keeps its size")
        self.assertEqual(h["key"], "1.5", "Ctrl + = in the page")
        self.assertEqual(h["wheel"], "1.25", "Ctrl + wheel over the page")
        self.assertEqual(h["dpr"], self.got["md"]["start"]["dpr"])

    def test_a_csv_zooms_its_table_at_the_code_level(self):
        self.assertEqual(self.got["csv"]["start"], "150%", "a CSV is read as code: code's level, last set by the wheel")
        self.assertEqual((self.got["csv"]["kind"], self.got["csv"]["zoom"]), ("code", "1.25"))

    def test_a_picture_grows_and_scrolls_in_its_pane(self):
        i = self.got["img"]
        self.assertEqual(i["start"], "100%")
        self.assertEqual(i["w0"], 300)
        self.assertEqual(i["w"], 600)
        self.assertFalse(i["pageScroll"], "the page itself does not scroll sideways")
        self.assertEqual(i["back"], 300)

    def test_a_wide_picture_zooms_from_its_fit(self):
        b = self.got["img"]["big"]
        fit = b["fit"]["w"] - b["edge"]
        self.assertLess(fit, 2000, "fitted to the pane at 100%")
        for key, z in (("z90", 0.9), ("z110", 1.1)):
            self.assertAlmostEqual(b[key]["w"] - b["edge"], fit * z, delta=2, msg=(key, b))
            self.assertEqual((b[key]["left"], b[key]["mw"]), (b["fit"]["left"], b["fit"]["mw"]), "the pane stays where it was")

    def test_a_pdf_has_no_control(self):
        self.assertTrue(self.got["pdf"])

    def test_the_levels_are_kept_per_kind(self):
        self.assertEqual(self.got["store"], {"md": 150, "code": 125, "page": 125})

    def test_a_diff_zooms_on_its_own(self):
        d = self.got["diff"]
        self.assertTrue(d["seg"])
        self.assertIn("drv-mode", d["before"], "before Unified / Side by side")
        self.assertEqual((d["zoom"], d["text"], d["store"]), ("1.25", "125%", 125))
        self.assertEqual((d["headFontAfter"], d["headZoom"]), (d["headFont"], ""), "the bar keeps its size")
        self.assertEqual((d["split"], d["zoomSplit"], d["textSplit"]), (True, "1.25", "125%"), "kept across the mode switch")
        self.assertEqual(d["header"], "1.25", "keys on the bar and a wheel over it leave the diff alone")
        self.assertEqual(d["wheel"], "1.5")
        self.assertEqual(d["reset"]["zoom"], "")

    def test_a_phone_header_fits_and_the_menu_holds_the_rest(self):
        ph = self.got["phone"]
        self.assertTrue(ph["coarse"], ph)
        self.assertLessEqual(ph["right"], ph["w"], ph)
        self.assertTrue(ph["zoom"])
        self.assertEqual(ph["reload"], "none")
        self.assertEqual((ph["menu"], ph["label"]), ("⋯", "More"))
        self.assertEqual(ph["touch"], "pan-x pan-y")
        self.assertGreaterEqual(ph["name"], 100, "the name and the space after it keep room")
        self.assertFalse(ph["nameCut"], "a short name shows whole")
        self.assertEqual((ph["back"], ph["views"]), ("none", "none"), "Back is ‹ alone; Preview / Source are rows of ⋯")
        self.assertEqual(ph["checked"], ["view-preview:true", "view-source:false"])
        self.assertEqual(ph["seps"], 2)
        self.assertEqual(ph["source"], {"view": "source", "kind": "code", "menuGone": True})
        self.assertEqual(ph["rows"][:2], ["✓Preview", "Source"])
        for want in ("Reload this file", "Comment", "Raw file", "Download"):
            self.assertTrue(any(want in r for r in ph["rows"]), (want, ph["rows"]))
        self.assertTrue(all(h >= 44 for h in ph["buttons"]), ph["buttons"])

    def test_two_fingers_zoom_the_file_not_the_page(self):
        ph = self.got["phone"]
        self.assertEqual(ph["pinchFrom"], "1.5", "Markdown's level on this device")
        self.assertEqual(ph["pinch"], {"zoom": "1.75", "text": "175%", "vv": 1})


class Steps(unittest.TestCase):
    def test_the_script_is_a_page_file(self):
        self.assertIn("static/panelzoom.js", dashboard.PAGE_FILES)


if __name__ == "__main__":
    unittest.main()
