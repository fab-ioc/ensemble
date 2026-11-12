"""#329 (P297 drawing): a project's settings dialog, in headless Chrome over CDP
against a hub in a thread serving the pages.

Three projects: Motors and Wheels (a PO each), Alpha (none). Checked at
1140x1000:

* a project row in the left list has a ⋯ menu (and a right click): Open,
  Rename project…, Project settings…;
* Rename… opens the dialog with the name selected; another project's name
  (case aside) is refused on the page, Save off, a red border; an empty name
  turns Save off; the Key's Change… opens its dialog and comes back to the
  settings with what was typed; Save renames it in the left list, the bar and
  the PO dock's tab;
* the bar's switcher lists Project settings… and Rename project… first, under
  the project's icon; an SVG uploaded there replaces the square at exactly
  12x12 in the left list, the bar and the Inbox; a file over 1 MB is refused
  on the page; a PNG from a row's menu; the square brings the square back;
* the board word names the menu and the dialog; Escape closes it;
* a phone (390x844): the switcher offers only Project settings…; the dialog
  fits the width less 8px gutters, its targets 44px, its field 16px;
* the page threw nothing meanwhile.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
# A white wheel on white: the hairline keeps its edge on a light ground.
WHEEL_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" fill="#FFFFFF"/>'
             '<circle cx="24" cy="24" r="15" fill="none" stroke="#222" stroke-width="5"/><circle cx="24" cy="24" r="4" fill="#222"/>'
             '<path d="M24 9v30M9 24h30" stroke="#222" stroke-width="2"/></svg>')


def png(w: int, h: int, rgb=(27, 43, 68)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {}; global.OUT = out;
  // A page; seed: localStorage keys set (a string) or removed (null) before the page's first script, once.
  const page = async (w, h, mobile, seed) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const clickAt = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
      await clickAt(x, y);
    };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const s = Object.assign({ 'cd-list-dock': null, 'cd-list-dock-a4': null, 'cd-list-dock-a5': null, 'cd-list-dock-a6': null, 'cd-list-dock-a7': null, 'cd-ld-sel': null, 'cd-ld-split': null, 'cd-noun': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-phone-tabs': null, 'cd-last-conv': null }, seed || {});
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 2 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, clickAt, key, shot, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = async (p, pid) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock && PD.pid === ' + JSON.stringify(pid), 30000);
    await sleep(500);
  };
  const P = A.ids;
  const R = `(e => { if (!e) return null; const r = e.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10 }; })`;
  const DLG = `(() => { const d = document.getElementById('modal'); if (!d || !d.open || !d.classList.contains('ps-dlg')) return null;
    const q = s => d.querySelector(s), t = s => ((q(s) || {}).textContent || '').trim();
    const inp = q('#ps-name');
    return { title: t('.ps-title'), sub: t('.ps-sub'), name: inp && inp.value, focus: document.activeElement && (document.activeElement.id || document.activeElement.className),
      selAll: !!inp && inp.selectionStart === 0 && inp.selectionEnd === inp.value.length && inp.value.length > 0,
      note: t('#ps-name-note'), noteCls: (q('#ps-name-note') || {}).className, bad: !!inp && inp.classList.contains('bad'),
      save: !!q('#ps-save') && !q('#ps-save').disabled, choice: (q('input[name="ps-icon"]:checked') || {}).value,
      img: t('.ps-choice:nth-child(2) small'), up: t('#ps-up'), err: [...d.querySelectorAll('.ps-err')].map(e => e.textContent),
      sets: [...d.querySelectorAll('.ps-set')].map(e => e.innerText.replace(/\\s+/g, ' ').trim()),
      box: ${R}(d), big: ${R}(q('.ps-choice:nth-child(2) .ps-big')), sample: ${R}(q('.ps-srow .ldp-ico')),
      hint: [...d.querySelectorAll('.ps-hint')].map(e => e.textContent), W: document.documentElement.clientWidth, scrollW: d.scrollWidth, clientW: d.clientWidth };
  })()`;
  const ICONS = pid => `(() => { const pid = ${JSON.stringify(pid)};
    const one = e => { if (!e) return null; const r = e.getBoundingClientRect(); const img = e.querySelector('img');
      return { tag: e.tagName.toLowerCase(), w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10, img: !!img, loaded: !!img && img.complete && img.naturalWidth > 0, src: img ? img.getAttribute('src') : '', alt: img ? img.getAttribute('alt') : null }; };
    return { row: one(document.querySelector('#ld-projs .ldp-row[data-ldp-proj="' + pid + '"] .ldp-ico')),
      rowName: ((document.querySelector('#ld-projs .ldp-row[data-ldp-proj="' + pid + '"] .ldp-name') || {}).textContent || ''),
      bar: one(document.querySelector('#proj-switch-ico .ldp-ico')), barName: (document.getElementById('proj-switch-name') || {}).textContent,
      ibx: one(document.querySelector('#ld-ibx-body .ibx-where .ldp-ico')), ibxWhere: ((document.querySelector('#ld-ibx-body .ibx-where') || {}).textContent || ''),
      pj: (projectById(pid) || {}).name, icon: (projectById(pid) || {}).icon || null,
      tabs: [...document.querySelectorAll('.dk-tab')].map(t => t.textContent.trim()).filter(Boolean).slice(0, 8) }; })()`;
  const setName = (p, v) => p.evalIn(`(() => { const i = document.querySelector('#modal #ps-name'); i.focus(); i.value = ${JSON.stringify(v)}; i.dispatchEvent(new Event('input', { bubbles: true })); return 0; })()`);
  const upload = async (p, file) => {
    const r = await c.send('Runtime.evaluate', { expression: `document.querySelector('#modal #ps-file')`, returnByValue: false }, p.sessionId);
    await c.send('DOM.setFileInputFiles', { files: [file], objectId: r.result.objectId }, p.sessionId);
  };
  try {
    const q = await page(1140, 1000, false);
    await c.send('DOM.enable', {}, q.sessionId);
    await toPo(q, P.Motors);
    await q.until('document.querySelectorAll("#ld-inbox a.ibx-link[data-room]").length > 0', 15000).catch(() => null);
    await q.evalIn(`(() => { ldpScope('all'); ibxRepaint(); return 0; })()`);
    await sleep(300);
    out.before = await q.evalIn(ICONS(P.Motors));
    // ---- the row's ⋯
    await q.evalIn(`(() => { const b = document.querySelector('#ld-projs [data-ldp-pmenu="${P.Wheels}"]'); b.click(); return 0; })()`);
    await sleep(200);
    out.rowMenu = await q.evalIn(`[...document.querySelectorAll('.dcm .dcm-item, .dcm .dcm-head')].map(e => e.textContent.trim())`);
    out.rowMenuFocus = await q.evalIn(`document.activeElement && document.activeElement.textContent.trim()`);
    await q.shot('live-1140x1000-rowmenu-light');
    await q.key('Escape');
    out.rowMenuGone = await q.evalIn(`!document.querySelector('.dcm')`);
    // right click on a project row
    await q.evalIn(`(() => { const b = document.querySelector('#ld-projs .ldp-row[data-ldp-proj="${P.Motors}"]'); const r = b.getBoundingClientRect(); b.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 5 })); return 0; })()`);
    await sleep(200);
    out.ctxMenu = await q.evalIn(`[...document.querySelectorAll('.dcm .dcm-item')].map(e => e.textContent.trim())`);
    // Rename… from it
    await q.evalIn(`[...document.querySelectorAll('.dcm .dcm-item')].find(e => /Rename/.test(e.textContent)).click()`);
    await q.until(DLG, 5000);
    out.renameOpen = await q.evalIn(DLG);
    // a duplicate name
    await setName(q, 'wheels');
    out.dup = await q.evalIn(DLG);
    await q.shot('live-1140x1000-error-light');
    await setName(q, '   ');
    out.empty = await q.evalIn(DLG);
    // typed for real (no maxlength in the way): 61 emoji are 61 characters to the hub, 122 UTF-16 units
    await setName(q, '');
    await q.evalIn(`(document.querySelector('#modal #ps-name').focus(), 0)`);
    await c.send('Input.insertText', { text: '\u{1F680}'.repeat(61) + ' Ğüç Ωμέγα' }, q.sessionId);
    await sleep(100);
    out.longTyped = await q.evalIn(`(() => { const i = document.querySelector('#modal #ps-name'), b = document.querySelector('#modal #ps-save');
      return { chars: Array.from(i.value).length, max: i.getAttribute('maxlength'), invalid: i.getAttribute('aria-invalid'), save: !b.disabled }; })()`);
    await setName(q, 'Motors Two');
    out.typed = await q.evalIn(DLG);
    // the Key's Change… and back: what was typed is kept
    await q.evalIn(`document.querySelector('#modal [data-ps-act="key"]').click()`);
    await q.until(`!!document.querySelector('#modal #key-in')`, 5000);
    out.keyOpen = true;
    await q.evalIn(`document.querySelector('#modal #key-cancel').click()`);
    await q.until(DLG, 5000);
    out.back = await q.evalIn(DLG);
    // Save
    await q.evalIn(`document.querySelector('#modal #ps-save').click()`);
    await q.until(`!document.getElementById('modal').open`, 8000);
    await q.until(`(projectById(${JSON.stringify(P.Motors)}) || {}).name === 'Motors Two'`, 8000);
    // the Inbox is asked again at once, and the hub's kept build was forgotten: well before its ten-second poll
    out.ibxRenamedIn = await q.until(`/Motors Two/.test((document.querySelector('#ld-ibx-body .ibx-where') || {}).textContent || '') && 1`, 4000).catch(() => 0);
    out.fold = await q.evalIn(`[psFold('Straße') === psFold('STRASSE'), psFold('Motors') === psFold('MOTORS')]`);
    out.fold2 = await q.evalIn(`[psFold('\\u0131') === psFold('i'), psName(' A\\u2003 B\\x1f'), psName('A\\ufeffB')]`);
    // every character the page folds, with what it folds it to: the hub's casefold() must agree
    out.folded = await q.evalIn(`(() => { const o = []; for (let c = 0; c < 0x110000; c++) { if (c >= 0xD800 && c <= 0xDFFF) continue; const ch = String.fromCodePoint(c), k = psFold(ch); if (k !== ch) o.push([c, k]); } return o; })()`);
    await sleep(800);
    out.renamed = await q.evalIn(ICONS(P.Motors));
    // ---- the bar's switcher › Project settings…: an icon
    await q.evalIn(`document.getElementById('proj-switch').click()`);
    await sleep(300);
    out.switchMenu = await q.evalIn(`[...document.querySelectorAll('#proj-menu .pm-set, #proj-menu .pm-head')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.textContent.trim())`);
    await q.shot('live-1140x1000-switcher-light');
    await q.evalIn(`document.querySelector('#proj-menu .proj-settings-btn').click()`);
    await q.until(DLG, 5000);
    out.settingsOpen = await q.evalIn(DLG);
    await q.shot('live-1140x1000-square-light');
    await upload(q, A.svg);
    await q.until(`(() => { const d = document.getElementById('modal'); const i = d.querySelector('.ps-big-img img'); return !!i && i.complete && i.naturalWidth > 0; })()`, 5000);
    out.picked = await q.evalIn(DLG);
    await q.shot('live-1140x1000-dialog-light');
    await q.evalIn(`document.documentElement.setAttribute('data-theme', 'dark')`);
    await sleep(200);
    await q.shot('live-1140x1000-dialog-dark');
    await q.evalIn(`document.documentElement.removeAttribute('data-theme')`);
    // too big: refused on the page
    await upload(q, A.big);
    await sleep(500);
    out.tooBig = await q.evalIn(DLG);
    await q.evalIn(`document.querySelector('#modal #ps-save').click()`);
    await q.until(`!document.getElementById('modal').open`, 8000);
    await q.until(`!!(projectById(${JSON.stringify(P.Motors)}) || {}).icon`, 8000);
    await sleep(800);
    await q.until(`(() => { const i = document.querySelector('#ld-projs .ldp-row[data-ldp-proj="${P.Motors}"] .ldp-ico img'); return !!i && i.complete && i.naturalWidth > 0; })()`, 8000).catch(() => null);
    out.withIcon = await q.evalIn(ICONS(P.Motors));
    await q.shot('live-1140x1000-icon-light');
    // the chat under the dialog leaves its state to the header again once the dialog closes
    out.chatStateLine = await q.evalIn(`[...document.querySelectorAll('iframe.po-session')].filter(f => !f.hidden).map(f => { const l = f.contentDocument && f.contentDocument.getElementById('state-line'); return !!l && !l.hidden; })`);
    // a PNG on Wheels, from its row
    await q.evalIn(`(() => { document.querySelector('#ld-projs [data-ldp-pmenu="${P.Wheels}"]').click(); return 0; })()`);
    await sleep(200);
    await q.evalIn(`[...document.querySelectorAll('.dcm .dcm-item')].find(e => /settings/.test(e.textContent)).click()`);
    await q.until(DLG, 5000);
    await upload(q, A.png);
    await q.until(`!!document.querySelector('#modal .ps-big-img img')`, 5000);
    await q.evalIn(`document.querySelector('#modal #ps-save').click()`);
    await q.until(`!!(projectById(${JSON.stringify(P.Wheels)}) || {}).icon`, 8000);
    await sleep(800);
    out.wheels = await q.evalIn(ICONS(P.Wheels));
    // ---- back to the square
    await q.evalIn(`(projSettings(${JSON.stringify(P.Motors)}), 0)`).catch(() => null);
    await q.until(DLG, 5000).catch(() => null);
    out.reopen = await q.evalIn(DLG);
    await q.evalIn(`document.querySelector('#modal input[name="ps-icon"][value="square"]').click()`);
    out.squarePicked = await q.evalIn(DLG);
    await q.evalIn(`document.querySelector('#modal #ps-save').click()`);
    await q.until(`!(projectById(${JSON.stringify(P.Motors)}) || {}).icon`, 8000);
    await sleep(800);
    out.square = await q.evalIn(ICONS(P.Motors));
    // ---- the board word
    await q.evalIn(`(() => { setNoun({ one: 'Initiative', many: 'Initiatives' }); window.dispatchEvent(new CustomEvent('cd-noun')); return 0; })()`);
    await q.evalIn(`(() => { document.querySelector('#ld-projs [data-ldp-pmenu="${P.Wheels}"]').click(); return 0; })()`);
    await sleep(200);
    out.nounMenu = await q.evalIn(`[...document.querySelectorAll('.dcm .dcm-item')].map(e => e.textContent.trim())`);
    await q.key('Escape');
    q.evalIn(`(projSettings(${JSON.stringify(P.Wheels)}), 0)`).catch(() => null);
    await q.until(DLG, 5000);
    out.nounDlg = await q.evalIn(DLG);
    await setName(q, 'motors two');
    out.nounDup = await q.evalIn(DLG);
    await q.key('Escape');
    await sleep(200);
    out.escClosed = await q.evalIn(`!document.getElementById('modal').open`);
    await q.evalIn(`(() => { setNoun(null); window.dispatchEvent(new CustomEvent('cd-noun')); return 0; })()`);
    await q.close();
    // ---- a phone
    const ph = await page(390, 844, true);
    await toPo(ph, P.Wheels).catch(() => null);
    await sleep(600);
    await ph.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(P.Wheels)}; syncProjSwitch(); document.getElementById('proj-switch').click(); return 0; })()`);
    await sleep(400);
    out.phoneMenu = await ph.evalIn(`[...document.querySelectorAll('#proj-menu .pm-set')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.textContent.trim())`);
    await ph.shot('live-390x844-menu-light');
    await ph.evalIn(`document.querySelector('#proj-menu .proj-settings-btn').click()`);
    await ph.until(DLG, 5000);
    await sleep(300);
    out.phone = await ph.evalIn(DLG);
    out.phoneTargets = await ph.evalIn(`[...document.querySelectorAll('#modal button, #modal .ps-choice, #modal input[type=text]')].filter(e => e.getBoundingClientRect().width > 0).map(e => [e.id || e.className, Math.round(e.getBoundingClientRect().height)])`);
    out.phoneFont = await ph.evalIn(`getComputedStyle(document.querySelector('#modal #ps-name')).fontSize`);
    await ph.shot('live-390x844-dialog-light');
    await ph.evalIn(`document.documentElement.setAttribute('data-theme', 'dark')`);
    await sleep(200);
    await ph.shot('live-390x844-dialog-dark');
    await ph.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'project-settings.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); try { console.error(JSON.stringify(global.OUT || {}).slice(0, 3000)); } catch (x) {} process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class ProjectSettingsInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ps-", ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(points, "mark_seen", lambda *a, **k: []),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        cls.ids = {}
        for name in ("Motors", "Wheels", "Alpha"):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
            if name in ("Motors", "Wheels"):
                home = Path(proj.get("home") or proj["path"])
                po = chatroom.create_room(f"{name} PO", members(home))
                dashboard.assign_session_project(po["id"], proj["id"])
                ok, why = dashboard.set_project_po(proj["id"], po["id"])
                assert ok, why
                if name == "Motors":
                    cls.po = po["id"]
                    text, ids = points.take(chatroom.get_room(po["id"], public=False), "Wider board", to="claude", key="k1")
                    chatroom.post_message(po["id"], "user", text, to="claude")
                    chatroom.post_message(po["id"], "claude", "Re P1: it is wider now.", to="user")
                    points.sync(po["id"], force=True)
        files = base / "files"
        files.mkdir()
        (files / "wheels-logo.svg").write_text(WHEEL_SVG, encoding="utf-8")
        (files / "motors.png").write_bytes(png(64, 48))
        (files / "huge.png").write_bytes(png(8, 8) + b"\x00" * (1024 * 1024 + 10))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "po": cls.po, "shots": shots,
                "svg": str(files / "wheels-logo.svg"), "png": str(files / "motors.png"), "big": str(files / "huge.png")}
        script = base / "project_settings_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_PS_DUMP"):
            Path(os.environ["ENSEMBLE_PS_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def g(self, k):
        return self.got[k]

    def assertIcon(self, ico, img):
        self.assertIsNotNone(ico)
        self.assertEqual((ico["w"], ico["h"]), (12, 12), ico)
        self.assertEqual(ico["img"], img, ico)
        if img:
            self.assertTrue(ico["loaded"], ico)

    def test_the_row_menu(self):
        self.assertEqual(self.g("rowMenu"), ["Wheels", "Open", "Rename project…", "Project settings…"])
        self.assertEqual(self.g("rowMenuFocus"), "Open")
        self.assertTrue(self.g("rowMenuGone"))
        self.assertEqual(self.g("ctxMenu"), ["Open", "Rename project…", "Project settings…"])

    def test_rename_opens_with_the_name_selected(self):
        d = self.g("renameOpen")
        self.assertEqual(d["title"], "Project settings")
        self.assertEqual(d["name"], "Motors")
        self.assertTrue(d["selAll"])
        self.assertEqual(d["focus"], "ps-name")
        self.assertIn("Its folder, its id and its key", d["note"])
        self.assertTrue(d["save"])
        self.assertEqual(d["box"]["w"], 560)

    def test_a_taken_or_empty_name_cannot_be_saved(self):
        d = self.g("dup")
        self.assertEqual(d["note"], "Another project is already called wheels. Pick another name.")
        self.assertEqual(d["noteCls"], "ps-err")
        self.assertTrue(d["bad"])
        self.assertFalse(d["save"])
        self.assertFalse(self.g("empty")["save"])
        self.assertTrue(self.g("typed")["save"])
        self.assertFalse(self.g("typed")["bad"])

    def test_another_setting_comes_back_with_what_was_typed(self):
        self.assertTrue(self.g("keyOpen"))
        self.assertEqual(self.g("back")["name"], "Motors Two")
        self.assertEqual(self.g("back")["focus"], "ps-btn")

    def test_the_rename_shows_everywhere(self):
        r = self.g("renamed")
        self.assertEqual(r["pj"], "Motors Two")
        self.assertEqual(r["rowName"], "Motors Two")
        self.assertEqual(r["barName"], "Motors Two")
        self.assertIn("Motors Two", r["ibxWhere"])
        self.assertEqual(self.g("ibxRenamedIn"), 1, "the Inbox names it at once")
        self.assertEqual(self.g("fold"), [True, True], "names compared as the hub's casefold()")
        self.assertEqual(self.g("longTyped"), {"chars": 71, "max": None, "invalid": None, "save": True},
                         "a name the hub takes can be typed: no UTF-16 maxlength")
        self.assertEqual(self.g("fold2"), [False, "A B", "A\ufeffB"],
                         "dotless i is not i to the hub; its whitespace is Python's")
        folded = self.g("folded")
        self.assertGreater(len(folded), 100)
        wrong = [(hex(c), k) for c, k in folded if k.casefold() != chr(c).casefold()]
        self.assertEqual(wrong, [], "a name the page calls taken, the hub refuses too")

    def test_the_switcher_lists_the_settings_first(self):
        m = self.g("switchMenu")
        self.assertIn("Motors Two", m)
        i = m.index("Motors Two")
        self.assertEqual(m[i:i + 3], ["Motors Two", "Project settings…", "Rename project…"])
        self.assertTrue(any(x.startswith("Key:") for x in m), m)

    def test_the_chat_leaves_its_state_to_the_header_after_the_dialog(self):
        self.assertEqual(self.g("chatStateLine"), [False])

    def test_the_dialog(self):
        d = self.g("settingsOpen")
        self.assertEqual(d["choice"], "square")
        self.assertEqual(d["up"], "Upload image…")
        self.assertEqual(d["img"], "none yet")
        self.assertEqual([s.split(" ")[0] for s in d["sets"]], ["Kind", "Key", "Task", "PO"])
        self.assertIn("its tasks are", d["sets"][1])
        self.assertTrue(d["sets"][3].startswith("PO claude"), d["sets"])
        self.assertEqual((d["sample"]["w"], d["sample"]["h"]), (12, 12))
        p = self.g("picked")
        self.assertEqual(p["choice"], "image")
        self.assertEqual(p["up"], "Replace…")
        self.assertTrue(p["img"].startswith("wheels-logo.svg · "), p["img"])
        self.assertEqual((p["big"]["w"], p["big"]["h"]), (40, 40))

    def test_a_file_over_1_mb_is_refused(self):
        d = self.g("tooBig")
        self.assertTrue(any("up to 1 MB" in e for e in d["err"]), d["err"])
        self.assertTrue(d["img"].startswith("wheels-logo.svg"), "the picked one stays")

    def test_the_icon_replaces_the_square_at_its_size(self):
        b = self.g("before")
        self.assertEqual(b["row"]["tag"], "svg")
        w = self.g("withIcon")
        self.assertEqual(w["icon"]["file"], "project-icon.svg")
        self.assertEqual(w["icon"]["name"], "wheels-logo.svg")
        self.assertIcon(w["row"], True)
        self.assertIcon(w["bar"], True)
        self.assertIcon(w["ibx"], True)
        self.assertEqual([w[k]["alt"] for k in ("row", "bar", "ibx")], ["Motors Two"] * 3, "the image says the name")
        wh = self.g("wheels")
        self.assertEqual(wh["icon"]["file"], "project-icon.png")
        self.assertIcon(wh["row"], True)

    def test_the_square_comes_back(self):
        self.assertEqual(self.g("reopen")["choice"], "image")
        self.assertTrue(self.g("squarePicked")["save"])
        s = self.g("square")
        self.assertIsNone(s["icon"])
        self.assertEqual(s["row"]["tag"], "svg")
        self.assertEqual((s["row"]["w"], s["row"]["h"]), (12, 12))
        self.assertEqual(s["bar"]["tag"], "svg")

    def test_the_board_word(self):
        self.assertEqual(self.g("nounMenu"), ["Open", "Rename initiative…", "Initiative settings…"])
        self.assertEqual(self.g("nounDlg")["title"], "Initiative settings")
        self.assertEqual(self.g("nounDup")["note"], "Another initiative is already called motors two. Pick another name.")
        self.assertTrue(self.g("escClosed"))

    def test_a_phone(self):
        self.assertEqual(self.g("phoneMenu"), ["Project settings…"])
        d = self.g("phone")
        self.assertEqual(d["box"]["x"], 8)
        self.assertEqual(d["box"]["w"], 374)
        self.assertLessEqual(d["scrollW"], d["clientW"])
        self.assertEqual(self.g("phoneFont"), "16px")
        for what, h in self.g("phoneTargets"):
            self.assertGreaterEqual(h, 44, what)
        self.assertEqual(d["hint"][0], "Shown everywhere at once. Its folder, its id and its key " + d["hint"][0].split("its key ")[1])

    def test_no_errors(self):
        self.assertEqual(self.g("errors"), [])


if __name__ == "__main__":
    unittest.main()
