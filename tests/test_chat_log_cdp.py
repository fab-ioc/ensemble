"""Chat as a log, with author colours (#401: issues 58, 63, 62; P412).

Settings › Appearance › Chat › Chat as a log, the hub's ``chatLog``, off by
default. In headless Chrome over CDP against a hub in a thread: a project
Motors with a PO chat and two tasks, #7 (engineer and reviewer) and #9. The
PO chat has a point the PO answered ("answers P1"), and reports from both
tasks; #7's chat has the person, its engineer and its reviewer.

Checked, on a computer (1440x900), with the setting on:

* every message starts at the same left edge, the person's too;
* between any two items of the chat the same 21px gap, with one 1px line
  in its middle across the column; nothing overlaps;
* "answers P1" is inside its message, as its last line;
* labels by author: the person's is the accent, the PO's is as before, a
  task's agents take the task's colour (#7: --c-task-3, #9: --c-task-5),
  the same in the PO chat and in the task's own chat, with white text over
  4.5:1, and none is a colour that means something;
* in Light and in Dark.

And: off, the chat is as it was (the person's on the right, no lines); the
switch in Settings (a real click in the dashboard) changes an open chat in
another tab at once, no reload, and the hub keeps it; on a phone the chat is
unchanged with the setting on; the page threw nothing. Screenshots go to
$ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
TEAM = [{'identity': 'claude', 'agent': 'claude', 'model': '', 'role': 'engineer'},
        {'identity': 'codex', 'agent': 'codex', 'model': '', 'role': 'reviewer'}]


def channels(color: str) -> tuple[float, float, float]:
    nums = [float(x) for x in re.findall(r'[\d.]+', color)]
    if color.startswith('color('):
        return tuple(nums[:3])
    return tuple(v / 255 for v in nums[:3])


def contrast(a: str, b: str) -> float:
    def lum(color: str) -> float:
        lin = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels(color)]
        return sum(x * y for x, y in zip(lin, (.2126, .7152, .0722)))
    x, y = lum(a), lum(b)
    return (max(x, y) + .05) / (min(x, y) + .05)


def oklab(color: str) -> tuple[float, float, float]:
    r, g, b = (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels(color))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def delta(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(oklab(a), oklab(b))) ** .5


CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A),
    '--no-first-run', '--disable-gpu', '--hide-scrollbars', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((resolve,reject) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) resolve(m[1]);
  }); ch.on('exit', c => reject(Error('chrome exited '+c))); setTimeout(() => reject(Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((resolve,reject) => { c.onopen=resolve; c.onerror=reject; });
  const errors = [];
  c.onmessage = ev => { const m=JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 400));
    const w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((resolve,reject) => { const id=++seq; waits.set(id,[resolve,reject]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const tab = async (w, h, mobile) => {
    const {targetId}=await send('Target.createTarget',{url:'about:blank'});
    const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
    await send('Page.enable',{},s); await send('Runtime.enable',{},s);
    await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:!!mobile},s);
    if (mobile) await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
      if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails).slice(0,500)); return r.result.value; };
    const until=async (expression,label) => { for(let i=0;i<200;i++){try{if(await ev(expression))return;}catch(e){} await sleep(120);}
      throw Error('timeout '+label+' '+await ev(`[...(document.getElementById('msgs')||{children:[]}).children].map(e => e.className + ': ' + e.textContent.replace(/\\s+/g,' ').slice(0,60)).join(' | ') + ' DRAWN=' + (typeof CHAT_DRAWN) + ' ' + document.body.innerText.slice(0, 300)`).catch(e => String(e))); };
    const click = async (expr) => {
      const [x, y] = await ev(`(() => { const e = (${expr}); e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      await send('Input.dispatchMouseEvent', {type:'mouseMoved', x, y}, s);
      for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button:'left', clickCount:1}, s);
    };
    const shot = async name => { if (!A.shots) return;
      const r = await send('Page.captureScreenshot', {format:'png'}, s); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const chat = async room => {
      await send('Page.navigate',{url:A.base+'/session?room='+room},s);
      await until(`typeof CHAT_DRAWN !== 'undefined' && CHAT_DRAWN && document.querySelectorAll('#msgs > :is(.msg, .msg-fold)').length >= ${A.counts[room]}`, 'chat ' + room);
      // The PO chat folds a task's reports into one row: open it (a real click), so its messages show.
      if (room === A.po) {
        await click(`document.querySelector('#msgs > .msg-fold.grp[aria-expanded="false"]')`);
        await until(`document.querySelectorAll('#msgs > .msg').length >= 6`, 'group opened');
      }
      await sleep(500);
    };
    return {ev, until, click, shot, chat, s, close: () => send('Target.closeTarget', {targetId})};
  };
  // The chat as drawn: each item of #msgs, its line, its labels, the points' bar.
  const VIEW = `(() => {
    const probe = document.body.appendChild(document.createElement('i'));
    const tok = n => { probe.style.color = ''; probe.style.color = 'var(' + n + ')'; return getComputedStyle(probe).color; };
    const box = document.getElementById('msgs'), br = box.getBoundingClientRect();
    const R = e => { const r = e.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; };
    const items = [...box.children].filter(e => e.getBoundingClientRect().height > 0).map(e => {
      const be = getComputedStyle(e, '::before');
      const bal = e.querySelector(':scope > .bal') || e;
      return {cls: String(e.className), rect: R(e), bal: bal ? R(bal) : null,
        line: be.content !== 'none' && be.content !== 'normal' ? {top: be.top, h: be.height, w: be.width, left: be.left, bg: be.backgroundColor} : null,
        text: e.textContent.replace(/\\s+/g, ' ').trim().slice(0, 80),
        bars: [...e.querySelectorAll('.pt-bar')].filter(b => b.getBoundingClientRect().height > 0).map(b => ({rect: R(b), text: b.textContent.replace(/\\s+/g, ' ').trim()}))};
    });
    const labels = [...box.querySelectorAll('.rl')].filter(e => e.getBoundingClientRect().width > 0).map(e => ({
      cls: String(e.className), tc: e.dataset.tc || '', text: e.textContent, bg: getComputedStyle(e).backgroundColor, ink: getComputedStyle(e).color,
      inThread: !!e.closest('.thr-line, .thr-in'), msg: (e.closest('.msg') || {}).textContent ? e.closest('.msg').textContent.replace(/\\s+/g, ' ').trim().slice(0, 60) : ''}));
    const names = ['--accent', '--border', '--c-task-1', '--c-task-2', '--c-task-3', '--c-task-4', '--c-task-5'];
    for (const k of ['success', 'warning', 'danger', 'progress', 'discovery', 'neutral']) for (const v of ['fg', 'bold']) names.push('--c-' + k + '-' + v);
    const tokens = Object.fromEntries(names.map(n => [n, tok(n)]));
    probe.remove();
    return {log: document.documentElement.dataset.chatLog, theme: document.documentElement.dataset.theme || '',
      coarse: matchMedia('(pointer: coarse)').matches, box: R(box), inner: box.clientWidth - parseFloat(getComputedStyle(box).paddingLeft) - parseFloat(getComputedStyle(box).paddingRight), items, labels, tokens,
      scrollX: document.documentElement.scrollWidth - innerWidth};
  })()`;
  const out = {errors};
  try {
    // The dashboard, where Settings is; and a chat in another tab.
    const dash = await tab(1440, 900);
    await send('Page.navigate', {url: A.base + '/'}, dash.s);
    await dash.until(`typeof PREFS !== 'undefined' && !!PREFS`, 'dashboard');
    await dash.ev(`localStorage.removeItem('cd-chat-log'); localStorage.setItem('cd-theme', 'light'); 0`);
    const p = await tab(1440, 900);
    out.fresh = {};
    for (const room of [A.po, A.task7]) { await p.chat(room); if (room === A.po) await p.until(`!!document.querySelector('.rl.po[data-po-fill]')`, 'po colour'); out.fresh[room] = await p.ev(VIEW); await p.shot(`chat-log-off-${room === A.po ? 'po' : 'task7'}`); }
    // Settings › Appearance › Chat as a log: On and Save, with real clicks. The open chat follows.
    await dash.ev(`openSettings('appearance'); 0`); await sleep(400);
    out.prefBefore = await dash.ev(`[...document.querySelectorAll('#pref-chat-log button')].map(b => [b.dataset.val, b.getAttribute('aria-pressed')])`);
    out.prefLabel = await dash.ev(`document.getElementById('pref-chat-log-label').textContent`);
    out.prefRect = await dash.ev(`(() => { const h = [...document.querySelectorAll('h5.sp-sub')].find(e => e.textContent.trim() === 'Chat'), seg = document.getElementById('pref-chat-log');
      const hint = seg.closest('.ap-pc'); const a = h.getBoundingClientRect(), b = (hint || seg).getBoundingClientRect(), c = seg.getBoundingClientRect();
      return {l: Math.min(a.left, b.left) - 16, t: a.top - 12, r: Math.max(c.right, b.right) + 16, b: b.bottom + 12, dpr: devicePixelRatio}; })()`);
    await dash.shot('settings-chat');
    if (A.shots) fs.writeFileSync(path.join(A.shots, 'settings-chat.json'), JSON.stringify(out.prefRect));
    await dash.click(`document.querySelector('#pref-chat-log button[data-val="on"]')`);
    await dash.click(`document.getElementById('ap-save')`);
    await dash.until(`fetch('/api/settings').then(r => r.json()).then(j => j.chatLog === 'on')`, 'saved on');
    await p.until(`document.documentElement.dataset.chatLog === 'on'`, 'chat took on');
    out.reloads = await p.ev(`performance.getEntriesByType('navigation').length + ':' + (window.__noReload = 1)`);
    await sleep(300);
    out.on = {light: {}, dark: {}};
    out.on.light[A.task7] = await p.ev(VIEW);
    out.sameDoc = await p.ev(`window.__noReload === 1`);
    await p.shot('chat-log-on-task7-light');
    for (const theme of ['light', 'dark']) {
      await p.ev(`localStorage.setItem('cd-theme', '${theme}'); cdTheme.apply(); 0`);
      for (const room of [A.po, A.task7]) {
        await p.chat(room);
        // The PO label takes its project's colour once /api/projects is back.
        if (room === A.po) await p.until(`!!document.querySelector('.rl.po[data-po-fill]')`, 'po colour');
        out.on[theme][room] = await p.ev(VIEW);
        if (room === A.po) out.on[theme].poFill = await p.ev(`document.querySelector('.rl.po').dataset.poFill`);
        await p.shot(`chat-log-on-${room === A.po ? 'po' : 'task7'}-${theme}`);
      }
    }
    // Every other kind of row the chat draws, with its markup's classes, put in the PO chat
    // for one measure: a "Show more" note, the catch-up note, a rotation and a checkpoint
    // line, a thread's line under a balloon and a "replied in a thread" line above one.
    out.mixed = {};
    for (const theme of ['light', 'dark']) {
      await p.chat(A.po);
      await p.ev(`localStorage.setItem('cd-theme', '${theme}'); cdTheme.apply(); 0`);
      await p.until(`document.documentElement.dataset.scheme === '${theme}'`, 'theme ${theme}');
      out.mixed[theme] = await p.ev(`(() => {
        const box = document.getElementById('msgs'), m = [...box.querySelectorAll(':scope > .msg')];
        const add = (html, before) => { const t = document.createElement('template'); t.innerHTML = html; const e = t.content.firstElementChild; e.dataset.fixture = '1'; box.insertBefore(e, before); };
        add('<div class="fold-note thr-more"><button type="button" class="thr-more-btn">Show more</button></div>', box.firstElementChild);
        add('<div class="catchup"><span class="cu-text">Since you last read: #7 done</span><button type="button" class="cu-read">Mark read</button></div>', m[1]);
        add('<div class="thr-line"><button type="button" class="thr-open"><span class="thr-count">2 replies</span></button></div>', m[1].nextElementSibling);
        add('<div class="rot-line" role="separator"><span>A fresh PO session</span></div>', m[2]);
        add('<div class="cp-line"><span class="cp-what">2 files changed</span><button type="button" class="cp-btn">Undo</button></div>', m[3]);
        add('<div class="thr-in"><button type="button" class="thr-open">replied in a thread</button></div>', m[4]);
        return ${VIEW};
      })()`);
      await p.shot(`chat-log-on-po-rows-${theme}`);
      await p.ev(`document.querySelectorAll('#msgs [data-fixture]').forEach(e => e.remove()); 0`);
    }
    // In the thread page, "Also in ..." is rendered after its own balloon.
    // Use the page's real marker builder and that placement, then measure both sides.
    await send('Page.navigate', {url: A.base + '/session?room=' + A.po + '&thread=P1'}, p.s);
    await p.until(`THREAD_MODE && CHAT_DRAWN && document.querySelectorAll('#msgs > .msg').length >= 2`, 'P1 thread');
    out.threadAfter = await p.ev(`(() => {
      const m = document.querySelector('#msgs > .msg');
      m.insertAdjacentHTML('afterend', threadMarkHtml({id:'also-fixture', from:'user', alsoIn:['P2']}));
      return ${VIEW};
    })()`);
    await p.ev(`localStorage.setItem('cd-theme', 'light'); cdTheme.apply(); 0`);
    // A fresh browser cache: the hub's choice still draws the log.
    await p.ev(`localStorage.removeItem('cd-chat-log'); 0`);
    await p.chat(A.po);
    await p.until(`document.documentElement.dataset.chatLog === 'on'`, 'hub choice');
    out.fromHub = await p.ev(`document.documentElement.dataset.chatLog`);
    // A phone with the setting on: its chat is unchanged.
    const ph = await tab(390, 844, true);
    await send('Page.navigate', {url: A.base + '/session?room=' + A.po}, ph.s); await sleep(800);
    await ph.ev(`localStorage.setItem('cd-chat-log', 'on'); 0`);
    for (const room of [A.po]) { await ph.chat(room); out.phoneOn = await ph.ev(VIEW); }
    await ph.ev(`localStorage.setItem('cd-chat-log', 'off'); 0`);
    await ph.chat(A.po); out.phoneOff = await ph.ev(VIEW);
    await ph.close();
    // Off again in Settings: the open chat goes back.
    await p.ev(`localStorage.setItem('cd-chat-log', 'on'); 0`); await p.chat(A.po);
    await dash.ev(`openSettings('appearance'); 0`); await sleep(300);
    out.prefOn = await dash.ev(`[...document.querySelectorAll('#pref-chat-log button')].map(b => [b.dataset.val, b.getAttribute('aria-pressed')])`);
    await dash.click(`document.querySelector('#pref-chat-log button[data-val="off"]')`);
    await dash.click(`document.getElementById('ap-save')`);
    await p.until(`document.documentElement.dataset.chatLog === 'off'`, 'chat took off');
    await dash.until(`fetch('/api/settings').then(r => r.json()).then(j => j.chatLog === 'off')`, 'saved off');
    await sleep(300);
    out.offAgain = await p.ev(VIEW);
    // A save that fails: no chat moves (the preview is this page's only), the cache and
    // the hub keep off, and Settings keeps previewing On with its bar so Save can be tried again.
    await dash.ev(`window.__api = api; api = (u, o) => o && o.method === 'PUT' && u === '/api/settings' ? Promise.reject(new Error('offline')) : window.__api(u, o); 0`);
    await dash.click(`document.querySelector('#pref-chat-log button[data-val="on"]')`);
    await dash.click(`document.getElementById('ap-save')`);
    await sleep(800);
    out.failed = {chat: await p.ev(`document.documentElement.dataset.chatLog`),
      local: await dash.ev(`localStorage.getItem('cd-chat-log')`),
      pressed: await dash.ev(`[...document.querySelectorAll('#pref-chat-log button')].map(b => [b.dataset.val, b.getAttribute('aria-pressed')])`),
      hub: await dash.ev(`window.__api('/api/settings').then(j => j.chatLog)`),
      bar: await dash.ev(`!document.getElementById('ap-bar').hidden`)};
    await dash.ev(`api = window.__api; apCancel(); 0`);
    // On is saving when Off is chosen: On is saved, and Off stays previewed, unsaved.
    await dash.ev(`window.__api = api; api = (u, o) => {
      if (u === '/api/settings' && o && o.method === 'PUT')
        return new Promise(r => { window.__releaseOn = () => r(window.__api(u, o)); });
      return window.__api(u, o);
    }; 0`);
    await dash.click(`document.querySelector('#pref-chat-log button[data-val="on"]')`);
    await dash.click(`document.getElementById('ap-save')`);
    await dash.click(`document.querySelector('#pref-chat-log button[data-val="off"]')`);
    await dash.ev(`window.__releaseOn(); 0`);
    await dash.until(`window.__api('/api/settings').then(j => j.chatLog === 'on')`, 'On saved');
    await dash.until(`!AP.saving`, 'save done');
    await sleep(700);
    out.whileSaving = {draft: await dash.ev(`Object.keys(AP.draft)`),
      bar: await dash.ev(`!document.getElementById('ap-bar').hidden`),
      chat: await p.ev(`document.documentElement.dataset.chatLog`),
      local: await dash.ev(`localStorage.getItem('cd-chat-log')`),
      pressed: await dash.ev(`[...document.querySelectorAll('#pref-chat-log button')].map(b => [b.dataset.val, b.getAttribute('aria-pressed')])`),
      hub: await dash.ev(`window.__api('/api/settings').then(j => j.chatLog)`)};
    await dash.ev(`api = window.__api; 0`);
    await dash.click(`document.getElementById('ap-save')`);
    await dash.until(`fetch('/api/settings').then(r => r.json()).then(j => j.chatLog === 'off')`, 'reset off');
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ChatLogInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-chatlog-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        root = base / 'EnsembleProjects'; root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        cls.patches = [
            mock.patch.object(dashboard, 'PROJECTS_ROOT', root),
            mock.patch.object(dashboard, 'DASHBOARD_DIR', state),
            mock.patch.object(dashboard, 'PROJECTS_FILE', state / 'projects.json'),
            mock.patch.object(dashboard, 'SESSION_PROJECTS_FILE', state / 'session_projects.json'),
            mock.patch.object(dashboard, 'SETTINGS_FILE', state / 'settings.json'),
            mock.patch.object(dashboard, 'LABELS_FILE', state / 'labels.json'),
            mock.patch.object(dashboard, 'PROJ_DIR', base / 'transcripts'),
            mock.patch.object(dashboard, 'CS_ROOT', base / 'cs'),
            mock.patch.object(chatroom, 'ROOMS_DIR', base / 'rooms'),
            mock.patch.object(dashboard, 'load_live', lambda: []),
            mock.patch.object(dashboard, '_read_agent_session_files', lambda *a, **k: []),
            mock.patch.object(dashboard, 'operator_name', return_value='sam'),
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        cls.addClassCleanup(points._CACHE.clear)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        # Another project, busier lately: the project list puts it first, so Motors' PO
        # takes the palette's second colour (green), not the blue of the person's accent.
        ok, brakes, _ = dashboard.register_project('Brakes'); assert ok, brakes
        pid = proj['id']
        cls.task7, cls.task9 = cls.task('Export the parts list', pid, 7), cls.task('Upload page', pid, 9)
        po = chatroom.create_room('PO', [dict(m) for m in TEAM])['id']
        full = chatroom.get_room(po, public=False); full['projectId'] = pid; chatroom.update_room(full)
        dashboard.assign_session_project(po, pid)
        ok, why = dashboard.set_project_po(pid, po); assert ok, why
        cls.po = po
        # The PO chat: a point the PO answered, then reports from #7 (engineer, reviewer) and #9.
        ask = chatroom.post_message(po, 'user', 'Make the export a day faster.')['message']
        led = points.load(po)
        points._new_point(led, ask['text'], 'claude', ask['ts'], '', '', n=1, mid=ask['id'])
        points._save(po, led)
        chatroom.post_message(po, 'claude', 'Re P1: The export now takes a day less. #7 does it.', to='user')
        for room, no, who, kind, body in [
                (cls.task7, 7, 'claude', 'completed', 'The export is a day faster.'),
                (cls.task9, 9, 'claude', 'update', 'The upload page is half done.'),
                (cls.task7, 7, 'codex', 'review', 'Approved: the export.')]:
            chatroom.post_report(po, f'{who}@{room}', 'claude', body,
                                 {'reportKind': kind, 'taskId': room, 'taskNo': no,
                                  'taskTitle': chatroom.get_room(room)['title']})
        chatroom.post_message(po, 'user', 'Thanks, ship #7.')
        # #7's own chat: the person, its engineer, its reviewer.
        for who, text, to in [('user', 'Start with the parts list.', 'claude'),
                              ('claude', 'Plan: one query instead of forty.', 'codex'),
                              ('codex', 'Looks right; add a test.', 'claude'),
                              ('claude', 'Test added. Done.', 'user'),
                              ('user', 'Good.', 'claude')]:
            chatroom.post_message(cls.task7, who, text, to=to)
        cls.counts = {po: 5, cls.task7: 5}
        later = chatroom.create_room('Brakes work', [dict(m) for m in TEAM])['id']
        dashboard.assign_session_project(later, brakes['id'])
        time.sleep(1.1); chatroom.post_message(later, 'user', 'Check the pads.', to='claude')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close); cls.addClassCleanup(cls.server.shutdown)
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name, 'shots': shots,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'po': po, 'task7': cls.task7, 'counts': cls.counts}
        script = base / 'chat_log_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        if os.environ.get('CHAT_LOG_DUMP'):
            Path(os.environ['CHAT_LOG_DUMP']).write_text(json.dumps(cls.got, indent=1), encoding='utf-8')
        cls.settings = json.loads((state / 'settings.json').read_text(encoding='utf-8'))
        cls.addClassCleanup(cls.tmp.cleanup)

    @classmethod
    def task(cls, title: str, pid: str, no: int) -> str:
        rid = chatroom.create_room(title, [dict(m) for m in TEAM])['id']
        full = chatroom.get_room(rid, public=False); full['projectId'] = pid; chatroom.update_room(full)
        dashboard.assign_session_project(rid, pid)
        chatroom.set_task_number(rid, pid, no)
        return rid

    def views_on(self):
        for theme in ('light', 'dark'):
            for room in (self.po, self.task7):
                yield theme, room, self.got['on'][theme][room]

    def msgs(self, view):
        return [i for i in view['items'] if re.search(r'(^| )msg( |$)', i['cls'])]

    def test_off_by_default_and_unchanged(self):
        for room, view in self.got['fresh'].items():
            with self.subTest(room=room):
                self.assertEqual(view['log'], 'off')
                self.assertTrue(all(i['line'] is None for i in view['items']), 'no lines when off')
                mine = [i for i in self.msgs(view) if ' user' in ' ' + i['cls'] and 'hub' not in i['cls']]
                self.assertTrue(mine, view['items'])
                for i in mine:
                    self.assertGreater(i['bal']['l'] - view['box']['l'], 100, ('the person on the right', i))
        self.assertEqual(self.got['prefBefore'], [['off', 'true'], ['on', 'false']])
        self.assertEqual(self.got['prefLabel'], 'Chat as a log')

    def test_every_message_starts_at_one_left_edge(self):
        for theme, room, view in self.views_on():
            with self.subTest(theme=theme, room=room):
                self.assertEqual(view['log'], 'on')
                lefts = {round(i['bal']['l']) for i in self.msgs(view) if i['bal']}
                self.assertEqual(len(lefts), 1, [(i['cls'], i['bal']) for i in self.msgs(view)])

    def test_one_line_in_the_middle_of_each_equal_gap(self):
        views = [*self.views_on(), *(('mixed ' + t, self.po, v) for t, v in self.got['mixed'].items())]
        for theme, room, view in views:
            with self.subTest(theme=theme, room=room):
                items = view['items']
                self.assertGreaterEqual(len(items), 5)
                if theme.startswith('mixed'):
                    kinds = {k for i in items for k in ('fold-note', 'catchup', 'thr-line', 'rot-line', 'cp-line', 'thr-in', 'msg-fold') if k in i['cls'].split()}
                    self.assertEqual(len(kinds), 7, kinds)
                for a, b in zip(items, items[1:]):
                    gap = b['rect']['t'] - a['rect']['b']
                    # A thread's line is part of its balloon: under the one above, over the one below.
                    if 'thr-line' in b['cls'].split() or ('thr-in' in a['cls'].split() and 'after' not in a['cls'].split()) or 'after' in b['cls'].split():
                        self.assertIsNone(b['line'], (a['cls'], b['cls']))
                        self.assertAlmostEqual(gap, 2, delta=0.6, msg=(a['cls'], b['cls'], gap))
                        continue
                    self.assertAlmostEqual(gap, 21, delta=0.6, msg=(a['cls'], b['cls'], gap))
                    self.assertEqual((b['line']['h'], b['line']['top']), ('1px', '-11px'), b)
                    self.assertAlmostEqual(float(b['line']['w'][:-2]), view['inner'], delta=1, msg=b)
                    self.assertAlmostEqual(b['rect']['l'] + float(b['line']['left'][:-2]), view['box']['l'] + (view['box']['w'] - view['inner']) / 2, delta=1, msg=b)
                    self.assertEqual(channels(b['line']['bg']), channels(view['tokens']['--border']))
                self.assertIsNone(items[0]['line'], 'none above the first')
                self.assertLessEqual(view['scrollX'], 0)

    def test_answers_p1_is_inside_its_message(self):
        for theme in ('light', 'dark'):
            view = self.got['on'][theme][self.po]
            with self.subTest(theme=theme):
                withbar = [i for i in self.msgs(view) if any('answers P1' in b['text'] for b in i['bars'])]
                self.assertEqual(len(withbar), 1, [(i['cls'], i['bars']) for i in self.msgs(view)])
                m = withbar[0]
                bar = next(b for b in m['bars'] if 'answers P1' in b['text'])
                self.assertIn('answers P1', bar['text'])
                self.assertGreaterEqual(bar['rect']['t'], m['rect']['t'])
                self.assertLessEqual(bar['rect']['b'], m['rect']['b'] + .5, 'inside its message')
                self.assertLess(abs(bar['rect']['l'] - m['bal']['l']), 40, 'on the left with its message')

    def label(self, view, cls, tc=None, thread=False):
        return [lb for lb in view['labels'] if re.search(rf'(^| ){cls}( |$)', lb['cls'])
                and (tc is None or lb['tc'] == tc) and lb['inThread'] == thread]

    def test_labels_by_author(self):
        for theme, room, view in self.views_on():
            with self.subTest(theme=theme, room=room):
                t = view['tokens']
                person = self.label(view, 'person')
                self.assertTrue(person)
                for lb in person:
                    self.assertEqual(channels(lb['bg']), channels(t['--accent']), lb)
                engineers = self.label(view, 'engineer')
                self.assertTrue(engineers, view['labels'])
                for lb in engineers + self.label(view, 'reviewer'):
                    want = {'7': '3', '9': '5'}['9' if 'Upload' in lb['msg'] or 'upload' in lb['msg'] else '7']
                    self.assertEqual(lb['tc'], want, lb)
                    self.assertEqual(channels(lb['bg']), channels(t['--c-task-' + want]), lb)
                    self.assertGreaterEqual(contrast(lb['ink'], lb['bg']), 4.5, lb)
                    for k in ('success', 'warning', 'danger', 'progress', 'discovery', 'neutral'):
                        for v in ('fg', 'bold'):
                            self.assertGreater(delta(lb['bg'], t[f'--c-{k}-{v}']), .06, (lb, k, v))
                    self.assertGreater(delta(lb['bg'], t['--accent']), .06, lb)
                if room == self.po:
                    self.assertTrue(self.label(view, 'reviewer'), 'the review report')
                    self.assertEqual({lb['tc'] for lb in engineers}, {'3', '5'})

    def test_a_tasks_colour_is_the_same_in_both_chats(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                po, own = self.got['on'][theme][self.po], self.got['on'][theme][self.task7]
                seen = {lb['bg'] for lb in self.label(po, 'engineer', '3')} | {lb['bg'] for lb in self.label(own, 'engineer')}
                self.assertEqual(len(seen), 1, seen)

    def test_the_po_label_is_as_before(self):
        off = self.got['fresh'][self.po]
        for theme in ('light',):
            on = self.got['on'][theme][self.po]
            self.assertEqual([lb['bg'] for lb in self.label(on, 'po')], [lb['bg'] for lb in self.label(off, 'po')])
            self.assertTrue(self.label(on, 'po'))
            self.assertEqual(self.got['on'][theme].get('poFill'), 'var(--c-success-bold)', 'the PO label is in its project colour')

    def test_a_failed_save_moves_no_chat_and_keeps_the_preview(self):
        self.assertEqual(self.got['failed'], {'chat': 'off', 'local': 'off', 'pressed': [['off', 'false'], ['on', 'true']], 'hub': 'off', 'bar': True})

    def test_a_choice_made_while_saving_stays_previewed(self):
        self.assertEqual(self.got['whileSaving'], {'draft': ['chatLog'], 'bar': True, 'chat': 'on', 'local': 'on', 'pressed': [['off', 'true'], ['on', 'false']], 'hub': 'on'})

    def test_thread_mode_also_in_belongs_to_its_own_balloon(self):
        items = self.got['threadAfter']['items']
        n = next(i for i, item in enumerate(items) if 'after' in item['cls'].split())
        self.assertGreater(n, 0)
        self.assertLess(n + 1, len(items))
        before, mark, after = items[n - 1:n + 2]
        self.assertIn('msg', before['cls'].split())
        self.assertIn('msg', after['cls'].split())
        self.assertIn('Also in P2', mark['text'])
        self.assertIsNone(mark['line'])
        self.assertAlmostEqual(mark['rect']['t'] - before['rect']['b'], 2, delta=.6)
        self.assertEqual((after['line']['h'], after['line']['top']), ('1px', '-11px'))
        self.assertAlmostEqual(after['rect']['t'] - mark['rect']['b'], 21, delta=.6)

    def test_settings_switches_an_open_chat_and_the_hub_keeps_it(self):
        self.assertTrue(self.got['sameDoc'], 'no reload')
        self.assertEqual(self.got['fromHub'], 'on')
        self.assertEqual(self.got['prefOn'], [['off', 'false'], ['on', 'true']])
        self.assertEqual(self.got['offAgain']['log'], 'off')
        self.assertTrue(all(i['line'] is None for i in self.got['offAgain']['items']))
        self.assertEqual(self.settings.get('chatLog'), 'off')

    def test_a_phone_is_unchanged(self):
        on, off = self.got['phoneOn'], self.got['phoneOff']
        self.assertTrue(on['coarse'])
        self.assertEqual([(i['cls'], i['rect']) for i in on['items']], [(i['cls'], i['rect']) for i in off['items']])
        self.assertEqual([lb['bg'] for lb in on['labels']], [lb['bg'] for lb in off['labels']])

    def test_no_page_errors(self):
        self.assertEqual(self.got['errors'], [])


class ThreadLabelTask(unittest.TestCase):
    """A thread line's labels carry a report's task number, so a task's agents take its colour there too."""

    def test_a_report_label_has_its_task_number(self):
        import threads
        r7 = {'role': 'user', 'kind': 'report', 'turn': {'kind': 'report', 'reportKind': 'review', 'taskNo': 7}}
        r9 = {'role': 'user', 'kind': 'report', 'turn': {'kind': 'report', 'reportKind': 'completed', 'taskNo': 9}}
        self.assertEqual(threads._reply_label(r7), {'cls': 'reviewer', 'text': 'reviewer', 'taskNo': 7})
        self.assertEqual(threads._reply_label(r9), {'cls': 'engineer', 'text': 'engineer', 'taskNo': 9})
        self.assertEqual(threads._reply_label({'role': 'user', 'kind': 'report', 'turn': {}}), {'cls': 'engineer', 'text': 'engineer'})


class ChatLogSetting(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='ens-chatlog-set-', ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', Path(tmp.name) / 'settings.json')
        p.start(); self.addCleanup(p.stop)

    def test_off_by_default(self):
        self.assertEqual(dashboard.load_settings()['chatLog'], 'off')

    def test_off_or_on_only(self):
        self.assertEqual(dashboard.save_settings({'chatLog': 'on'})['chatLog'], 'on')
        for bad in ('yes', '', 'On', 1, True, None, ['on']):
            self.assertEqual(dashboard.save_settings({'chatLog': bad})['chatLog'], 'on', bad)
        self.assertEqual(dashboard.save_settings({'chatLog': 'off'})['chatLog'], 'off')


if __name__ == '__main__':
    unittest.main()
