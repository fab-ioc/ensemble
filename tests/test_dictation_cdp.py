"""Dictation in the message box (P367) in a real Chrome, with a fake speech engine.

A ``FakeRec`` stands in for ``SpeechRecognition``/``webkitSpeechRecognition``
before the page loads; the test makes it say words (interim and final, cut the
way Chrome and Safari cut them), fail and end, while the keys and clicks are
real CDP input. Checked: the mic is live only with an engine on a secure page,
otherwise greyed, a press saying why in the box (P387); words land at the
caret, the ones still being heard selected; nothing is sent; a second press
and Esc (without stopping the agent) stop it, silence never does (P459): 20 s
of it leaves it listening, an engine that ends by itself is started again at
once with the words before and after kept once each (Chrome's and Safari's
cuts, grey words at the end), the button on throughout and no-speech not
shown, a press, Esc or a send after a restart stop it, and sessions that keep
failing (ending at once, or refusing to start) back off and stop with a note;
a refused
microphone is one line in the box (no dialog), cleared by typing; a typed key
ends listening and lands after the words; Send ends listening and sends the
words once; Esc leaves an open menu to close first; a press while stopping
listens again; only the mic's own hint line is cleared; on a phone the mic is
in the + menu and shows in the row only while listening. Skipped without Node
and Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
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
import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
NO_ENGINE = 'Dictation isn’t available in this browser: use Safari or Chrome.'
INSECURE = 'Dictation needs a secure page: open the hub at its https address.'
BLOCKED = 'Microphone blocked: allow it for this site in the browser’s settings.'
# Requests that would send a message or stop an agent.
SEND_PATHS = ('/api/room/resume', '/api/room/command', '/api/room/interrupt')

# Before any page script: the fake engine (or none, with ?nomic=1), dialogs
# recorded, and every POST recorded; a send and an agent stop are answered
# here, so nothing reaches the hub's terminals.
INIT = r"""(() => {
  window.__posts = []; window.__dialogs = []; window.__recs = []; window.__starts = [];
  for (const k of ['alert', 'confirm', 'prompt'])
    window[k] = (...a) => { window.__dialogs.push([k, String(a[0] || '')]); return k === 'confirm' ? false : null; };
  const realFetch = window.fetch.bind(window);
  window.fetch = (u, o) => {
    const url = new URL(String((u && u.url) || u), location.href);
    const method = String((o && o.method) || (u && u.method) || 'GET').toUpperCase();
    if (method === 'POST') { let body = null; try { body = JSON.parse(o.body); } catch (e) {} window.__posts.push({ path: url.pathname, body }); }
    if (url.pathname === '/api/room/interrupt' && window.__interruptFail)
      return Promise.resolve(new Response(JSON.stringify({ error: 'agent_not_working' }), { status: 409, headers: { 'Content-Type': 'application/json' } }));
    if (url.pathname === '/api/room/resume' || url.pathname === '/api/room/interrupt')
      return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    return realFetch(u, o);
  };
  // ?insecure=1: the page as a plain-http address on another machine sees it (P387).
  if (location.search.includes('insecure=1')) Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
  if (location.search.includes('nomic=1')) {
    for (const k of ['SpeechRecognition', 'webkitSpeechRecognition']) {
      try { delete window[k]; } catch (e) {}
      if (window[k]) try { Object.defineProperty(window, k, { value: undefined, configurable: true, writable: true }); } catch (e) {}
    }
    return;
  }
  class FakeRec {
    constructor() { this.lang = ''; this.continuous = false; this.interimResults = false; this.calls = [];
      this.onresult = null; this.onerror = null; this.onend = null; window.__recs.push(this); window.__rec = this; }
    start() { this.calls.push('start'); this.startLang = this.lang; window.__starts.push(Date.now());
      // ?: the test makes every start refuse (__startThrow) or end at once with a network error (__failFast).
      if (window.__startThrow) throw new DOMException('recognition has already started', 'InvalidStateError');
      // __failWords: each session hears a word, then fails the same way.
      if (window.__failFast) setTimeout(() => { if (window.__failWords) window.__emit([['w', false]], 0, this); if (this.onerror) this.onerror({ error: 'network', message: '' }); if (this.onend) this.onend(new Event('end')); }, 30); }
    stop() { this.calls.push('stop'); }
    abort() { this.calls.push('abort'); }
  }
  window.SpeechRecognition = FakeRec;
  window.webkitSpeechRecognition = FakeRec;
  // The test's hook: results as the real API gives them (all of the session's
  // results each time, each an array-like of alternatives with isFinal).
  window.__emit = (list, resultIndex = 0, rec = window.__rec) => {
    const results = list.map(([t, fin]) => { const r = [{ transcript: t, confidence: 0.9 }]; r.isFinal = !!fin; r.item = i => r[i]; return r; });
    results.item = i => results[i];
    if (rec.onresult) rec.onresult({ results, resultIndex });
  };
  window.__error = (error, rec = window.__rec) => { if (rec.onerror) rec.onerror({ error, message: '' }); };
  window.__end = (rec = window.__rec) => { if (rec.onend) rec.onend(new Event('end')); };
})();"""

CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A),
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
  const ws = await new Promise((res, rej) => { let buf = ''; ch.stderr.on('data', d => {
    buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no CDP ' + buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq = 0;
  const netPosts = [], jsDialogs = [];
  await new Promise((res, rej) => { c.onopen = res; c.onerror = rej; });
  c.onmessage = ev => { const m = JSON.parse(ev.data);
    if (m.method === 'Network.requestWillBeSent' && m.params.request.method === 'POST') netPosts.push(new URL(m.params.request.url).pathname);
    if (m.method === 'Page.javascriptDialogOpening') { jsDialogs.push(m.params.message); send('Page.handleJavaScriptDialog', {accept: false}, m.sessionId).catch(() => {}); }
    const w = waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w[1](new Error(m.error.message)) : w[0](m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const id = ++seq; waits.set(id, [res, rej]); c.send(JSON.stringify({id, method, params, sessionId})); });
  const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
  const {sessionId: s} = await send('Target.attachToTarget', {targetId, flatten: true});
  await send('Page.enable', {}, s);
  await send('Network.enable', {}, s);
  await send('Emulation.setFocusEmulationEnabled', {enabled: true}, s);
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 860, deviceScaleFactor: 1, mobile: false}, s);
  await send('Page.addScriptToEvaluateOnNewDocument', {source: A.init}, s);
  const ev = async expression => { const r = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, s);
    if (r.exceptionDetails) throw new Error(expression.slice(0, 140) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expression, label, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await ev(expression)) return; } catch (e) {} await sleep(120); } throw new Error('timeout ' + (label || expression)); };
  // Real input.
  const VK = {ArrowLeft: 37, Escape: 27, Enter: 13, ' ': 32};
  const key = async k => {
    const special = k.length > 1;
    const code = special ? k : k === ' ' ? 'Space' : /[a-z]/i.test(k) ? 'Key' + k.toUpperCase() : '';
    const vk = VK[k] || (/[a-z]/i.test(k) ? k.toUpperCase().charCodeAt(0) : k.charCodeAt(0));
    const down = {type: special ? 'rawKeyDown' : 'keyDown', key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk};
    if (!special) { down.text = k; down.unmodifiedText = k; }
    await send('Input.dispatchKeyEvent', down, s);
    await send('Input.dispatchKeyEvent', {type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk}, s);
  };
  const type = async text => { for (const ch of text) await key(ch); };
  // A special key with modifiers (2 = Ctrl).
  const keyMod = async (k, modifiers) => {
    const vk = VK[k];
    await send('Input.dispatchKeyEvent', {type: 'rawKeyDown', key: k, code: k, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers}, s);
    await send('Input.dispatchKeyEvent', {type: 'keyUp', key: k, code: k, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers}, s);
  };
  const click = async sel => {
    const r = await ev(`(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2}; })()`);
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: r.x, y: r.y}, s);
    await send('Input.dispatchMouseEvent', {type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1}, s);
    await send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1}, s);
    await sleep(60);
  };
  const emit = (list, idx = 0) => ev(`window.__emit(${JSON.stringify(list)}, ${idx}); 0`);
  const st = () => ev(`(() => { const i = document.getElementById('input'), m = document.getElementById('mic'), r = window.__rec;
    return {value: i.value, start: i.selectionStart, end: i.selectionEnd, sel: i.value.slice(i.selectionStart, i.selectionEnd),
      focus: document.activeElement === i, pressed: m.getAttribute('aria-pressed'), live: i.classList.contains('mic-live'),
      calls: r ? r.calls.slice() : [], recs: window.__recs.length, hint: document.getElementById('hint').textContent,
      sends: window.__posts.filter(p => ${JSON.stringify(A.sendPaths)}.includes(p.path)).map(p => ({path: p.path, text: p.body && p.body.text}))}; })()`);
  const open = async (q = '', room = A.room, keep = false) => {
    await send('Page.navigate', {url: A.base + '/session?id=' + room + '&embed=1' + q}, s);
    await until(`typeof ROOM_OBJ !== 'undefined' && !!ROOM_OBJ && !document.getElementById('send').disabled`, 'room ' + q);
    await sleep(300);
    if (!q.includes('nomic') && !keep) await ev(`(() => { edClear(); const i = document.getElementById('input'); i.focus(); window.__posts.length = 0; return 0; })()`);
    netPosts.length = 0;
  };
  const shown = `(() => { const m = document.getElementById('mic'); const b = m.getBoundingClientRect();
    return {hidden: m.hidden, display: getComputedStyle(m).display, w: b.width, h: b.height,
      beforeSend: !!(m.compareDocumentPosition(document.getElementById('send-split')) & Node.DOCUMENT_POSITION_FOLLOWING),
      inRow: !!m.closest('.ed-row'), label: m.getAttribute('aria-label'), title: m.title, disabled: m.getAttribute('aria-disabled'),
      color: getComputedStyle(m).color, cursor: getComputedStyle(m).cursor, secure: window.isSecureContext,
      greyColor: (() => { const p = document.createElement('span'); p.style.color = 'var(--fg-disabled)'; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; })(),
      itemDisabled: document.querySelector('[data-phone-action="mic"]').getAttribute('aria-disabled'),
      plusShown: getComputedStyle(document.querySelector('.phone-compose-tools')).display !== 'none',
      plusItem: !document.querySelector('[data-phone-action="mic"]').hidden,
      plusItemDrawn: getComputedStyle(document.querySelector('[data-phone-action="mic"]')).display !== 'none'}; })()`;
  const out = {};
  try {
    // 1. Shown with an engine; without one, or on a page that is not secure, drawn inactive and pressing it says why (P387).
    await open('&nomic=1');
    out.noEngine = await ev(shown);
    out.noEngine.engine = await ev(`!!(window.SpeechRecognition || window.webkitSpeechRecognition)`);
    await ev(`document.getElementById('input').focus(); 0`);
    await click('#mic');
    out.noEnginePress = {...await st(), dialogs: await ev('window.__dialogs.length')};
    await type('x');
    out.noEngineTyped = await st();
    await open('&insecure=1');
    out.insecure = await ev(shown);
    await click('#mic');
    out.insecurePress = await st();
    await open();
    out.engine = await ev(shown);
    out.engine.secure = await ev('window.isSecureContext');

    // 2. Words at the caret: real keys, caret between the two spaces.
    await type('Hello  world');
    for (let n = 0; n < 6; n++) await key('ArrowLeft');
    out.caret = await st();
    await click('#mic');
    out.started = await ev(`(() => { const r = window.__rec; return {calls: r.calls.slice(), lang: r.startLang, nav: navigator.language,
      continuous: r.continuous, interim: r.interimResults}; })()`);
    out.startedSt = await st();
    await emit([['move the', false]]);
    out.interim = await st();
    await emit([['move the task', true]]);
    out.final = await st();
    // Chrome: a second result for the second phrase.
    await emit([['move the task', true], [' to done', false]], 1);
    out.chrome1 = await st();
    await emit([['move the task', true], [' to done', true]], 1);
    out.chrome2 = await st();
    // 4. A second press stops: at once not pressed; the words stay after the end.
    await click('#mic');
    out.stopped = await st();
    await ev('window.__end(); 0');
    out.ended = await st();
    // Safari: one result that grows, in a second session at the caret.
    await click('#mic');
    await emit([['and', false]]);
    out.safari1 = await st();
    await emit([['and then', false]]);
    out.safari2 = await st();
    await emit([['and then some', true]]);
    out.safari3 = await st();
    await click('#mic'); await ev('window.__end(); 0');
    // A space is added when the caret follows a word.
    await ev(`(() => { edClear(); document.getElementById('input').focus(); return 0; })()`);
    await type('Hello  world');
    for (let n = 0; n < 7; n++) await key('ArrowLeft');
    await click('#mic');
    await emit([['there', true]]);
    await click('#mic'); await ev('window.__end(); 0');
    out.pad = await st();
    // 3. Nothing was sent during all of that.
    await sleep(800);
    out.quiet = await st();
    out.quietNet = netPosts.filter(p => A.sendPaths.includes(p));

    // 5. Esc stops listening, not the agent (which an Esc on the empty box would stop).
    await open();
    await until(`!document.getElementById('stop-wrap').hidden && STOP_TARGETS.length > 0`, 'a working agent to stop');
    out.escSetup = await ev(`({stopShown: !document.getElementById('stop-wrap').hidden, value: document.getElementById('input').value})`);
    await click('#mic');
    out.escListening = await st();
    await key('Escape');
    await sleep(500);
    out.esc = await st();
    out.escNet = netPosts.slice();
    await ev('window.__end(); 0');
    out.escEnded = await st();
    // The control: the same Esc, not listening, stops the agent.
    await key('Escape');
    await until(`window.__posts.some(p => p.path === '/api/room/interrupt')`, 'Esc stops the agent when not listening', 3000).catch(() => {});
    out.escControl = await st();

    // 6. Silence never stops it (P459): 20 s with no words, still listening.
    await open();
    await click('#mic');
    await emit([['hold on', true]]);
    await sleep(20000);
    out.silence20 = await st();
    // A press stops it; an engine that never says it ended is let go 1.5 s later.
    await click('#mic');
    out.silenceStop = await st();
    await sleep(1800);
    out.silenceLetGo = await st();

    // 6b. The engine ends by itself: a new session goes on at once after the words, the button on throughout.
    const NET = 'window.__starts.slice()';
    await open();
    await type('Note ');
    await click('#mic');
    await ev(`(() => { window.__pressLog = []; const m = document.getElementById('mic');
      new MutationObserver(() => window.__pressLog.push(m.getAttribute('aria-pressed'))).observe(m, {attributes: true, attributeFilter: ['aria-pressed']}); return 0; })()`);
    // Chrome: a result per phrase; the session (a long one) ends after a no-speech error.
    await emit([['one two', true], [' three', true]], 1);
    await sleep(1100);
    await ev(`window.__error('no-speech'); window.__end(); 0`);
    out.restart1 = await st();
    await sleep(150);
    out.restart1b = await st();
    // Safari: one growing result; the session ends (a network blip) while its words are still grey.
    await emit([['four', false]]);
    await emit([['four five', false]]);
    out.restart2Grey = await st();
    await ev(`window.__error('network'); window.__end(); 0`);
    out.restart2Wait = await st();
    await sleep(450);   // an error: the next session waits 250 ms
    out.restart2 = await st();
    await emit([['six', true]]);
    out.restart3Said = await st();
    // Its own time limit: an end with no error, then nothing more said.
    await sleep(1100);
    await ev(`window.__end(); 0`);
    await sleep(150);
    out.restart3 = {...await st(), log: await ev('window.__pressLog.slice()')};
    await click('#mic');
    await ev(`window.__end(); 0`);
    await sleep(300);
    out.restartStopped = {...await st(), log: await ev('window.__pressLog.slice()')};

    // 6c. After a restart, Esc and a send still stop it.
    await open();
    await click('#mic');
    await ev(`window.__end(); 0`);
    await sleep(400);
    await emit([['esc words', false]]);
    await key('Escape');
    await sleep(300);
    out.restartEsc = await st();
    await ev('window.__end(); 0');
    await sleep(200);
    out.restartEscEnded = await st();
    await open();
    await click('#mic');
    await ev(`window.__end(); 0`);
    await sleep(400);
    await emit([['send words', false]]);
    await click('#send');
    await until(`window.__posts.some(p => p.path === '/api/room/resume')`, 'the send', 5000).catch(() => {});
    await sleep(500);
    out.restartSend = await st();
    await ev('window.__end(); 0');
    await sleep(300);
    out.restartSendEnded = await st();

    // 6d. Sessions that keep failing at once (network): later and later, then it stops with a note.
    await open();
    await click('#mic');
    await sleep(1100);
    await ev('window.__starts = []; window.__failFast = true; window.__end(); 0');
    await until(`document.getElementById('mic').getAttribute('aria-pressed') === 'false'`, 'gives up on the network', 10000).catch(() => {});
    out.failNet = {...await st(), starts: await ev(NET)};
    await sleep(2500);
    out.failNetLater = {...await st(), starts: await ev(NET)};
    await ev('window.__failFast = false; 0');
    // A start refused every time (the same engine and a new one): it stops with a note, the words kept.
    await open();
    await click('#mic');
    await emit([['kept', true]]);
    await ev('window.__starts = []; window.__startThrow = true; window.__end(); 0');
    await until(`document.getElementById('mic').getAttribute('aria-pressed') === 'false'`, 'gives up on starts', 10000).catch(() => {});
    out.failThrow = {...await st(), starts: await ev(NET)};
    await sleep(2500);
    out.failThrowLater = {...await st(), starts: await ev(NET)};
    await ev('window.__startThrow = false; 0');
    // Sessions that each hear a word and then fail: the words stay, but it still backs off and stops.
    await open();
    await click('#mic');
    await sleep(1100);
    await ev('window.__starts = []; window.__failFast = true; window.__failWords = true; window.__end(); 0');
    await until(`document.getElementById('mic').getAttribute('aria-pressed') === 'false'`, 'gives up with words', 10000).catch(() => {});
    out.failWords = {...await st(), starts: await ev(NET)};
    await sleep(2500);
    out.failWordsLater = {...await st(), starts: await ev(NET)};
    await ev('window.__failFast = false; window.__failWords = false; 0');
    // The tab is hidden while it waits for the next session: none starts, listening ends.
    await open();
    await click('#mic');
    await sleep(1100);
    await ev('window.__starts = []; window.__startThrow = true; window.__end(); 0');
    await sleep(80);
    await ev(`(() => { window.__startThrow = false; Object.defineProperty(document, 'hidden', {value: true, configurable: true});
      document.dispatchEvent(new Event('visibilitychange')); return 0; })()`);
    out.hiddenWait = {...await st(), starts: await ev(NET)};
    await sleep(800);
    out.hiddenAfter = {...await st(), starts: await ev(NET)};
    await ev(`(() => { delete document.hidden; return 0; })()`);
    // A press while it waits between two failed sessions stops it at once.
    await open();
    await click('#mic');
    await sleep(1100);
    await ev('window.__starts = []; window.__failFast = true; window.__end(); 0');
    await sleep(550);
    out.failGap = {...await st(), starts: await ev(NET)};
    await click('#mic');
    out.failGapStop = await st();
    await sleep(2500);
    out.failGapLater = {...await st(), starts: await ev(NET)};
    await ev('window.__failFast = false; 0');

    // 7. A refused microphone: one line in the box, no dialog; typing clears it.
    await open();
    await click('#mic');
    await ev(`window.__error('not-allowed'); window.__end(); 0`);
    out.denied = await st();
    await ev('refresh().then(() => 0)');
    await sleep(300);
    out.deniedRefreshed = await st();
    out.deniedDialogs = await ev(`({page: window.__dialogs.slice(), open: document.querySelectorAll('dialog[open]').length})`);
    out.cdpDialogs = jsDialogs.slice();
    await type('a');
    out.deniedTyped = await st();

    // 8. A key typed while listening ends it and replaces the grey words, as any selection; heard words stay.
    await open();
    await type('Hello ');
    await click('#mic');
    await emit([['buy milk', false]]);
    out.keyBefore = await st();
    await key('x');
    out.keyAfter = await st();
    await open();
    await type('Hello ');
    await click('#mic');
    await emit([['buy', true], [' milk', false]], 1);
    out.keyFinalBefore = await st();
    await key('x');
    out.keyFinalAfter = await st();

    // 9. Send while listening: listening ends, the words are sent once.
    await open();
    await click('#mic');
    await emit([['ship it today', false]]);
    out.sendBefore = await st();
    await click('#send');
    await until(`window.__posts.some(p => p.path === '/api/room/resume')`, 'the send', 5000).catch(() => {});
    await sleep(1000);
    out.sendAfter = await st();
    // A late result from the ended session writes nothing.
    await emit([['ship it today please', true]]);
    out.sendLate = await st();
    out.sendNet = netPosts.filter(p => A.sendPaths.includes(p));

    // 10. Esc with a menu open while listening: the menu closes first, then Esc stops listening.
    await open();
    await click('#mic');
    await until(`!document.getElementById('send-more').hidden`, 'Send now offered');
    await click('#send-more');
    out.menuOpen = await ev(`!document.getElementById('send-menu').hidden`);
    await key('Escape');
    await sleep(200);
    out.menuEsc1 = {...await st(), menu: await ev(`!document.getElementById('send-menu').hidden`)};
    await key('Escape');
    await sleep(300);
    out.menuEsc2 = await st();
    await ev('window.__end(); 0');

    // 11. A press while it is still stopping: the words stay, it listens again.
    await open();
    await click('#mic');
    await emit([['first', true]]);
    await click('#mic');
    out.restop = await st();
    await click('#mic');
    out.again = {...await st(), first: await ev('window.__recs[0].calls.slice()')};
    await emit([['second', true]]);
    out.againSaid = await st();
    await ev('window.__end(window.__recs[0]); 0');   // the first session's late end changes nothing
    out.againOldEnd = await st();
    await click('#mic'); await ev('window.__end(); 0');
    out.againDone = await st();

    // 12. Another feature's line in the hint stays when listening starts; the mic's own line goes.
    await open();
    await ev('window.__interruptFail = true; 0');
    await key('Escape');
    await until(`document.getElementById('hint').textContent === 'It had already stopped.'`, 'stop refused', 5000).catch(() => {});
    out.otherHint = await st();
    await click('#mic');
    out.otherHintListening = await st();
    await emit([['words', false]]);
    out.otherHintWords = await st();
    await click('#mic'); await ev('window.__end(); 0');
    await ev('window.__interruptFail = false; 0');
    await click('#mic');
    await ev(`window.__error('not-allowed'); window.__end(); 0`);
    out.ownHint = await st();
    await click('#mic');
    out.ownHintCleared = await st();
    await click('#mic'); await ev('window.__end(); 0');

    // 14. A selection is kept until the first words come.
    const SEL = `(() => { const i = document.getElementById('input'); i.focus(); i.setSelectionRange(6, 9); return 0; })()`;
    await open();
    await type('Hello big world');
    await ev(SEL);
    await click('#mic');
    await ev(`window.__error('not-allowed'); window.__end(); 0`);
    out.selDenied = await st();
    await sleep(1200);   // the draft is saved by now
    await open('', A.room, true);
    out.selDeniedReload = await st();
    await ev(SEL);
    await click('#mic');
    await key('Escape');
    await ev('window.__end(); 0');
    out.selEsc = await st();
    await ev(SEL);
    await click('#mic');
    await ev(`window.__error('no-speech'); window.__end(); 0`);
    await sleep(600);
    out.selSilent = await st();
    await click('#mic'); await ev('window.__end(); 0');
    out.selSilentGone = await st();
    await ev(SEL);
    await click('#mic');
    await emit([['small', false]]);
    out.selFirst = await st();
    await click('#mic'); await ev('window.__end(); 0');
    out.selFirstDone = await st();
    await sleep(1200);
    await open('', A.room, true);
    out.selFirstReload = await st();

    // 15. A press in the box ends listening; the caret goes where pressed.
    await open();
    await type('Hello world');
    await click('#mic');
    await emit([['again', true]]);
    out.pressBefore = await st();
    const at = await ev(`(() => { const i = document.getElementById('input'), b = i.getBoundingClientRect(), cs = getComputedStyle(i);
      return {x: b.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth) + 1, y: b.top + parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth) + 6}; })()`);
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: at.x, y: at.y}, s);
    await send('Input.dispatchMouseEvent', {type: 'mousePressed', x: at.x, y: at.y, button: 'left', clickCount: 1}, s);
    await send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: at.x, y: at.y, button: 'left', clickCount: 1}, s);
    await sleep(100);
    out.pressed = await st();
    await key('y');
    out.pressTyped = await st();

    // 16. No point while dictating: "1. " said at the head's start becomes one point at the end.
    const PTS = `[...document.querySelectorAll('#ed-points textarea.ed-text')].map(t => t.value)`;
    await open();
    await click('#mic');
    await emit([['1. buy milk', false]]);
    out.ptInterim = {...await st(), points: await ev(PTS)};
    await emit([['1. buy milk and eggs', true]]);
    out.ptFinal = {...await st(), points: await ev(PTS)};
    await click('#mic'); await ev('window.__end(); 0');
    await sleep(200);
    out.ptEnded = {...await st(), points: await ev(PTS)};
    // A key typed over grey "1. buy" replaces it, as over any grey words: no point is made of them.
    await open();
    await click('#mic');
    await emit([['1. buy', false]]);
    await key('x');
    await sleep(200);
    out.ptKey = {...await st(), points: await ev(PTS)};

    // 17. The shared workspace box changes draft: the words stay with the old one, a late result writes nothing.
    await open('&workspace-compose=1', A.po, true);
    await ev(`(() => { edClear(); document.getElementById('input').focus(); window.__posts.length = 0; return 0; })()`);
    await type('Draft A ');
    await click('#mic');
    await emit([['said here', false]]);
    out.wsBefore = await st();
    await ev(`workspaceComposeThread({thread: 'P1', title: 'Some thread'}); 0`);
    await sleep(200);
    out.wsSwitched = {...await st(), key: await ev('ED_KEY()')};
    await emit([['said here later', true]]);
    out.wsLate = await st();
    await ev(`workspaceComposeThread(null); 0`);
    await sleep(200);
    out.wsBack = {...await st(), key: await ev('ED_KEY()')};

    // 18. Esc in the new thread's name field is that field's; listening goes on. Back in the box, Esc stops it.
    await open('', A.po);
    await until(`THREADED && !document.getElementById('new-thread').hidden`, 'the spool');
    await click('#new-thread');
    await until(`!document.getElementById('compose-thread-name').hidden`, 'the name field');
    await ev(`document.getElementById('input').focus(); 0`);
    await click('#mic');
    await click('#compose-thread-name');
    await type('Plans');
    out.nameListening = {...await st(), active: await ev('document.activeElement.id')};
    // A rename field has a use for Esc (it cancels): listening goes on.
    await ev(`(() => { const i = document.createElement('input'); i.id = 'rn'; i.value = 'Old'; document.body.append(i);
      window.__renamed = []; nameEdit(i, (save, v) => window.__renamed.push([save, v])); i.focus(); return 0; })()`);
    await key('Escape');
    await sleep(200);
    out.renameEsc = {...await st(), renamed: await ev('window.__renamed')};
    // The name field has none: Esc there stops listening, and the field keeps its words and the focus.
    await ev(`document.getElementById('compose-thread-name').focus(); 0`);
    await key('Escape');
    await sleep(200);
    out.nameEsc = {...await st(), active: await ev('document.activeElement.id'), name: await ev(`document.getElementById('compose-thread-name').value`)};
    await ev('window.__end(); 0');
    // A point's text has none either: Esc there stops listening, not the agent.
    await open();
    await ev(`(() => { edAddAt(0, 'milk'); document.getElementById('input').focus(); window.__posts.length = 0; return 0; })()`);
    await click('#mic');
    await click('#ed-points textarea.ed-text');
    out.ptFieldListening = {...await st(), inPoint: await ev(`document.activeElement.matches('#ed-points textarea.ed-text')`)};
    await key('Escape');
    await sleep(300);
    out.ptFieldEsc = {...await st(), inPoint: await ev(`document.activeElement.matches('#ed-points textarea.ed-text')`),
      posts: await ev('window.__posts.map(p => p.path)')};
    await ev('window.__end(); 0');

    // 20. Ctrl+Enter while "1. buy milk" is still grey: it is sent as a point.
    await open();
    await click('#mic');
    await emit([['1. buy milk', false]]);
    await keyMod('Enter', 2);
    await until(`window.__posts.some(p => p.path === '/api/room/resume')`, 'the send', 5000).catch(() => {});
    await sleep(500);
    out.ctrlEnter = await st();

    // 21. The PO chat's box changes thread: "1. buy milk" said in the chat stays there as a point.
    await open('', A.po);
    await click('#mic');
    await emit([['1. buy milk', false]]);
    await ev(`sharedThreadSelect({thread: 'P1', title: 'Some thread'}); 0`);
    await sleep(200);
    out.poSwitched = {...await st(), points: await ev(PTS)};
    await click('#mic');
    await emit([['1. eggs', false]]);
    await ev(`sharedThreadClear(); 0`);
    await sleep(200);
    out.poBack = {...await st(), points: await ev(PTS)};
    await ev(`sharedThreadSelect({thread: 'P1', title: 'Some thread'}); 0`);
    await sleep(200);
    out.poThread = {...await st(), points: await ev(PTS)};
    await ev(`sharedThreadClear(); 0`);
    // 22. Listening is the same button with its mic pulsing in the accent: no text, the box does not move (P369).
    const look = `(() => { const m = document.getElementById('mic'), b = m.getBoundingClientRect(), ico = m.querySelector('.mic-ico'),
      i = document.getElementById('input').getBoundingClientRect(), send = document.getElementById('send').getBoundingClientRect(), probe = document.createElement('span');
      probe.style.color = 'var(--accent)'; document.body.append(probe); const accent = getComputedStyle(probe).color; probe.remove();
      return {w: b.width, h: b.height, x: b.left, text: m.textContent.trim(), kids: m.children.length, label: m.getAttribute('aria-label'), title: m.title,
        color: getComputedStyle(m).color, accent, bg: getComputedStyle(m).backgroundColor, anim: getComputedStyle(ico).animationName,
        input: [i.left, i.width], send: send.left}; })()`;
    await open();
    out.lookIdle = await ev(look);
    await click('#mic');
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: 5, y: 5}, s);   // not hovered
    await sleep(400);
    out.lookOn = await ev(look);
    await click('#mic'); await ev('window.__end(); 0');
    await send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]}, s);
    await click('#mic');
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: 5, y: 5}, s);
    await sleep(400);
    out.lookReduced = await ev(look);
    await click('#mic'); await ev('window.__end(); 0');
    await send('Emulation.setEmulatedMedia', {features: []}, s);

    // 13. A phone: no mic in the row at rest; the + menu's Dictate listens, then the pressed mic shows.
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true}, s);
    await send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5}, s);
    await open();
    const tap = async sel => {
      const r = await ev(`(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2}; })()`);
      await send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: r.x, y: r.y}]}, s);
      await send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []}, s);
      await sleep(250);
    };
    const phone = () => ev(`(() => { const m = document.getElementById('mic'), b = m.getBoundingClientRect(), it = document.querySelector('[data-phone-action="mic"]'),
      ib = it.getBoundingClientRect(), menu = document.querySelector('.phone-compose-tools');
      return {coarse: matchMedia('(pointer: coarse)').matches, micHidden: m.hidden, display: getComputedStyle(m).display, w: b.width, h: b.height,
        item: !it.hidden, itemShown: ib.width > 0 && ib.height > 0, itemText: it.textContent.trim(), menuOpen: menu.open,
        input: document.getElementById('input').getBoundingClientRect().width}; })()`);
    out.phoneRest = {...await phone(), ...await st()};
    await tap('.phone-compose-tools > summary');
    out.phoneMenu = await phone();
    await tap('[data-phone-action="mic"]');
    out.phoneListening = {...await phone(), ...await st()};
    await emit([['from the phone', false]]);
    out.phoneWords = await st();
    await tap('.phone-compose-tools > summary');
    out.phoneMenu2 = await phone();
    await key('Escape');
    await sleep(200);
    out.phoneEsc1 = {...await phone(), ...await st()};
    await key('Escape');
    await sleep(300);
    out.phoneEsc2 = {...await phone(), ...await st()};
    await ev('window.__end(); 0');
    out.phoneEnded = {...await phone(), ...await st()};
    // A phone without an engine (an iPhone's browser without Dictation, P387): the + menu's Dictate is there, greyed; tapped, the menu closes and the line says why.
    await open('&nomic=1');
    await tap('.phone-compose-tools > summary');
    out.phoneNoEngineMenu = {...await phone(), itemDisabled: await ev(`document.querySelector('[data-phone-action="mic"]').getAttribute('aria-disabled')`),
      itemColor: await ev(`getComputedStyle(document.querySelector('[data-phone-action="mic"]')).color`)};
    await tap('[data-phone-action="mic"]');
    out.phoneNoEngineTap = {...await phone(), ...await st()};
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(() => {}); ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class Dictation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-dictation-', ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            # The agent works, so an Esc on the empty box would stop it.
            mock.patch.object(dashboard, '_room_is_live', lambda room: True),
            mock.patch.object(dashboard.attention, 'turn_state', lambda p, *a, **k: ('working', '')),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        part = {'identity': 'claude', 'agent': 'claude', 'ptyId': 'pty-claude', 'cwd': str(root)}
        r = chatroom.create_room('Dictation', [part])
        data = chatroom.get_room(r['id'], public=False)
        data['mode'] = 'solo'
        chatroom.update_room(data)
        chatroom.patch_participant(r['id'], 'claude', {'ptyId': 'pty-claude'})
        cls.room = r['id']
        # A PO chat (threaded): the spool, the thread name field, the shared workspace box.
        ok, proj, _ = dashboard.register_project('Motors')
        assert ok, proj
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner',
                                               'sessionId': 'dictation-po', 'cwd': str(root)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'
        chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], proj['id'])
        dashboard.set_project_po(proj['id'], po['id'])
        cls.po = po['id']
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name, 'init': INIT, 'sendPaths': list(SEND_PATHS),
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'room': cls.room, 'po': cls.po}
        script = base / 'dictation_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_1_shown_only_with_an_engine(self):
        g = self.got
        self.assertTrue(g['engine']['secure'])
        self.assertFalse(g['engine']['hidden'], g['engine'])
        self.assertNotEqual(g['engine']['display'], 'none')
        self.assertGreater(g['engine']['w'], 0)
        self.assertTrue(g['engine']['inRow'] and g['engine']['beforeSend'], g['engine'])
        self.assertEqual(g['engine']['label'], 'Dictate')
        self.assertIsNone(g['engine']['disabled'], 'an engine on a secure page: the mic is live')
        self.assertTrue(g['engine']['plusItem'], "the + menu's Dictate is there with the mic")
        self.assertFalse(g['engine']['plusItemDrawn'], "a wide box has the mic in its row, not twice (in the + menu too)")
        self.assertIsNone(g['engine']['itemDisabled'])

    def test_1b_greyed_with_its_reason_where_it_cannot_work(self):
        # P387: no engine (an iPhone's browser without Dictation), or a page that
        # is not secure (plain http from another machine): the mic is still in the
        # row, inactive, and a press says why in the box's line; nothing listens.
        g = self.got
        for case, press, why in (('noEngine', 'noEnginePress', NO_ENGINE), ('insecure', 'insecurePress', INSECURE)):
            with self.subTest(case):
                n = g[case]
                self.assertFalse(n['hidden'], n)
                self.assertNotEqual(n['display'], 'none')
                self.assertGreater(n['w'], 0)
                self.assertTrue(n['inRow'] and n['beforeSend'], n)
                self.assertEqual(n['disabled'], 'true')
                self.assertEqual(n['color'], n['greyColor'], 'drawn in --fg-disabled')
                self.assertEqual(n['cursor'], 'not-allowed')
                self.assertEqual(n['title'], why)
                self.assertIn(why, n['label'])
                self.assertEqual(n['itemDisabled'], 'true', "the + menu's Dictate is greyed with it")
                self.assertTrue(n['plusItem'])
                p = g[press]
                self.assertEqual(p['hint'], why)
                self.assertEqual(p['recs'], 0, 'no recognition started')
                self.assertEqual(p['pressed'], 'false')
                self.assertFalse(p['live'])
        self.assertFalse(g['noEngine']['engine'], 'both constructors gone')
        self.assertFalse(g['insecure']['secure'])
        self.assertEqual(g['noEnginePress']['dialogs'], 0, 'no dialog')
        self.assertEqual(g['noEngineTyped']['hint'], '', 'typing clears the line')
        self.assertEqual(g['noEngineTyped']['value'], 'x')

    def test_2_words_at_the_caret(self):
        g = self.got
        self.assertEqual((g['caret']['value'], g['caret']['start'], g['caret']['end']), ('Hello  world', 6, 6))
        self.assertEqual(g['started']['calls'], ['start'])
        self.assertEqual(g['started']['lang'], g['started']['nav'])
        self.assertTrue(g['started']['continuous'] and g['started']['interim'])
        self.assertEqual(g['startedSt']['pressed'], 'true')
        self.assertTrue(g['startedSt']['live'] and g['startedSt']['focus'], g['startedSt'])
        i = g['interim']
        self.assertEqual(i['value'], 'Hello move the world')
        self.assertEqual((i['start'], i['end'], i['sel']), (6, 14, 'move the'), 'the interim words are selected, exactly')
        self.assertEqual(i['pressed'], 'true')
        f = g['final']
        self.assertEqual(f['value'], 'Hello move the task world')
        self.assertEqual((f['start'], f['end']), (19, 19), 'caret after the heard words, nothing selected')
        self.assertEqual(g['chrome1']['value'], 'Hello move the task to done world')
        self.assertEqual(g['chrome1']['sel'], 'to done')
        self.assertEqual(g['chrome2']['value'], 'Hello move the task to done world')
        self.assertEqual((g['chrome2']['start'], g['chrome2']['end']), (27, 27))
        self.assertEqual(g['safari1']['value'], 'Hello move the task to done and world')
        self.assertEqual(g['safari1']['sel'], 'and')
        self.assertEqual(g['safari2']['value'], 'Hello move the task to done and then world')
        self.assertEqual(g['safari2']['sel'], 'and then')
        self.assertEqual(g['safari3']['value'], 'Hello move the task to done and then some world')
        self.assertEqual(g['safari3']['sel'], '')
        self.assertEqual(g['pad']['value'], 'Hello there  world', 'a space before the words after a word')
        self.assertEqual((g['pad']['start'], g['pad']['end']), (11, 11))

    def test_3_nothing_is_sent(self):
        g = self.got
        self.assertEqual(g['quiet']['sends'], [])
        self.assertEqual(g['quietNet'], [])
        self.assertEqual(g['quiet']['value'], 'Hello there  world', 'the words stay in the box')

    def test_4_second_press_stops(self):
        g = self.got
        self.assertEqual(g['stopped']['calls'], ['start', 'stop'])
        self.assertEqual(g['stopped']['pressed'], 'false', 'not pressed at once')
        self.assertFalse(g['stopped']['live'])
        self.assertEqual(g['ended']['value'], 'Hello move the task to done world')
        self.assertEqual(g['ended']['pressed'], 'false')
        self.assertEqual((g['ended']['start'], g['ended']['end']), (27, 27))

    def test_5_esc_stops_listening_not_the_agent(self):
        g = self.got
        self.assertTrue(g['escSetup']['stopShown'], 'an agent is working (Esc on the empty box would stop it)')
        self.assertEqual(g['escSetup']['value'], '')
        self.assertEqual(g['escListening']['pressed'], 'true')
        self.assertIn('stop', g['esc']['calls'])
        self.assertEqual(g['esc']['pressed'], 'false')
        self.assertEqual(g['esc']['sends'], [], 'no stop request')
        self.assertNotIn('/api/room/interrupt', g['escNet'])
        self.assertEqual(g['escEnded']['sends'], [])
        # The control: not listening, the same Esc does stop the agent.
        self.assertEqual([s['path'] for s in g['escControl']['sends']], ['/api/room/interrupt'])

    def test_6_silence_never_stops(self):
        g = self.got
        s = g['silence20']
        self.assertEqual((s['calls'], s['pressed'], s['live']), (['start'], 'true', True), '20 s of silence: still listening')
        self.assertEqual((s['value'], s['hint']), ('hold on', ''))
        self.assertEqual((g['silenceStop']['calls'], g['silenceStop']['pressed']), (['start', 'stop'], 'false'), 'a press stops it')
        self.assertEqual(g['silenceLetGo']['calls'], ['start', 'stop', 'abort'], 'an engine that never ends is let go')
        self.assertEqual(g['silenceLetGo']['value'], 'hold on')
        self.assertEqual((g['silenceLetGo']['start'], g['silenceLetGo']['end']), (7, 7))
        self.assertFalse(g['silenceLetGo']['live'])

    def test_6b_an_engine_end_starts_again_with_every_word_once(self):
        g = self.got
        r = g['restart1']
        self.assertEqual((r['value'], r['pressed'], r['live']), ('Note one two three', 'true', True), 'the button stays on at the end')
        self.assertEqual(r['hint'], '', 'no-speech is not shown')
        r = g['restart1b']
        self.assertEqual((r['calls'], r['recs'], r['pressed']), (['start', 'start'], 1, 'true'), 'a new session of the same engine')
        self.assertEqual(g['restart2Grey']['value'], 'Note one two three four five')
        self.assertEqual(g['restart2Grey']['sel'], 'four five')
        w = g['restart2Wait']
        self.assertEqual((w['calls'], w['pressed']), (['start', 'start'], 'true'), 'an error: it waits, still on')
        r = g['restart2']
        self.assertEqual(r['calls'], ['start', 'start', 'start'])
        self.assertEqual((r['value'], r['sel'], r['hint']), ('Note one two three four five', '', ''), 'grey words at the end are kept as heard')
        self.assertEqual((r['start'], r['end']), (len('Note one two three four five'),) * 2)
        self.assertEqual(g['restart3Said']['value'], 'Note one two three four five six', 'every word once')
        r = g['restart3']
        self.assertEqual((r['calls'], r['pressed'], r['value']), (['start'] * 4, 'true', 'Note one two three four five six'))
        self.assertNotIn('false', r['log'], 'no flicker: the button never went off')
        r = g['restartStopped']
        self.assertEqual((r['calls'], r['pressed'], r['live']), (['start'] * 4 + ['stop'], 'false', False), 'a press stops it, no new session')
        self.assertEqual((r['value'], r['hint'], r['sends']), ('Note one two three four five six', '', []))
        self.assertEqual(r['log'][-1], 'false')

    def test_6c_esc_and_send_stop_it_after_a_restart(self):
        g = self.got
        e = g['restartEsc']
        self.assertEqual((e['calls'], e['pressed'], e['sends']), (['start', 'start', 'stop'], 'false', []))
        e = g['restartEscEnded']
        self.assertEqual((e['calls'], e['pressed'], e['value']), (['start', 'start', 'stop'], 'false', 'esc words'))
        s = g['restartSend']
        self.assertEqual((s['calls'], s['pressed']), (['start', 'start', 'abort'], 'false'))
        self.assertEqual(s['sends'], [{'path': '/api/room/resume', 'text': 'send words'}])
        s = g['restartSendEnded']
        self.assertEqual((s['calls'], s['pressed'], s['value']), (['start', 'start', 'abort'], 'false', ''), 'no new session after the send')

    def test_6d_sessions_that_keep_failing_stop_with_a_note(self):
        g = self.got
        f = g['failNet']
        self.assertEqual((f['pressed'], f['live']), ('false', False))
        self.assertEqual(f['hint'], 'Dictation needs the network: the browser’s speech service did not answer.')
        self.assertEqual(len(f['starts']), 5, 'the first new session at once, then four that each failed: the fifth failure stops it')
        gaps = [b - a for a, b in zip(f['starts'], f['starts'][1:])]
        self.assertGreaterEqual(gaps[0], 200, gaps)
        self.assertTrue(all(b > a for a, b in zip(gaps, gaps[1:])), f'each wait is longer: {gaps}')
        self.assertLess(f['starts'][-1] - f['starts'][0], 10000)
        self.assertEqual(g['failNetLater']['starts'], f['starts'], 'and it does not try again')
        t = g['failThrow']
        self.assertEqual((t['pressed'], t['live'], t['value']), ('false', False, 'kept'))
        self.assertEqual(t['hint'], 'Dictation stopped: the browser’s speech service kept failing.')
        self.assertLessEqual(len(t['starts']), 10, 'five tries, each the same engine then a new one')
        self.assertEqual(len(g['failThrowLater']['starts']), len(t['starts']))
        w = g['failWords']
        self.assertEqual((w['pressed'], w['live']), ('false', False), 'words in each session do not make it spin')
        self.assertEqual(w['hint'], 'Dictation needs the network: the browser’s speech service did not answer.')
        self.assertEqual(len(w['starts']), 5)
        wgaps = [b - a for a, b in zip(w['starts'], w['starts'][1:])]
        self.assertTrue(wgaps[0] >= 200 and all(b > a for a, b in zip(wgaps, wgaps[1:])), wgaps)
        self.assertEqual(w['value'], 'w w w w w', 'one word per session, each kept once')
        self.assertEqual(g['failWordsLater']['starts'], w['starts'])
        h = g['hiddenWait']
        self.assertEqual((h['pressed'], len(h['starts'])), ('true', 2), 'it waits: the same engine and a new one refused')
        h = g['hiddenAfter']
        self.assertEqual(len(h['starts']), 2, 'no session starts in a hidden tab')
        self.assertEqual((h['pressed'], h['live'], h['hint']), ('false', False, ''))
        self.assertEqual(g['failGap']['pressed'], 'true', 'still on while it waits')
        self.assertEqual((g['failGapStop']['pressed'], g['failGapStop']['live']), ('false', False), 'a press in the wait stops it')
        self.assertEqual(g['failGapLater']['starts'], g['failGap']['starts'], 'no session after it')
        self.assertEqual(g['failGapLater']['hint'], '')

    def test_7_permission_denied_is_one_line(self):
        g = self.got
        self.assertEqual(g['denied']['hint'], BLOCKED)
        self.assertEqual(g['denied']['pressed'], 'false')
        self.assertEqual(g['deniedRefreshed']['hint'], BLOCKED, 'refresh keeps the line')
        self.assertEqual(g['deniedDialogs'], {'page': [], 'open': 0})
        self.assertEqual(g['cdpDialogs'], [])
        self.assertEqual(g['deniedTyped']['value'], 'a')
        self.assertEqual(g['deniedTyped']['hint'], '', 'typing clears the line')

    def test_8_a_typed_key_ends_listening(self):
        g = self.got
        self.assertEqual(g['keyBefore']['value'], 'Hello buy milk')
        self.assertEqual(g['keyBefore']['sel'], 'buy milk')
        a = g['keyAfter']
        self.assertEqual(a['value'], 'Hello x', 'the key replaces the grey words')
        self.assertEqual(a['calls'], ['start', 'abort'])
        self.assertEqual(a['pressed'], 'false')
        self.assertEqual((a['start'], a['end']), (7, 7))
        self.assertEqual(g['keyFinalBefore']['value'], 'Hello buy milk')
        self.assertEqual(g['keyFinalBefore']['sel'], 'milk')
        self.assertEqual(g['keyFinalAfter']['value'], 'Hello buy x', 'words already heard stay')
        self.assertEqual(g['keyFinalAfter']['calls'], ['start', 'abort'])

    def test_9_send_while_listening(self):
        g = self.got
        self.assertEqual(g['sendBefore']['value'], 'ship it today')
        self.assertEqual(g['sendBefore']['pressed'], 'true')
        a = g['sendAfter']
        self.assertEqual(a['calls'], ['start', 'abort'])
        self.assertEqual(a['pressed'], 'false')
        self.assertEqual(a['sends'], [{'path': '/api/room/resume', 'text': 'ship it today'}], 'sent once, with the words')
        self.assertEqual(a['value'], '', 'the box is cleared by the send')
        self.assertEqual(g['sendLate']['value'], '', 'a late result of the ended session writes nothing')
        self.assertEqual(g['sendLate']['sends'], a['sends'])


    def test_10_esc_closes_a_menu_first(self):
        g = self.got
        self.assertTrue(g['menuOpen'])
        self.assertFalse(g['menuEsc1']['menu'], 'the first Esc closes the send menu')
        self.assertEqual(g['menuEsc1']['calls'], ['start'], 'and listening goes on')
        self.assertEqual(g['menuEsc1']['pressed'], 'true')
        self.assertEqual(g['menuEsc2']['calls'], ['start', 'stop'], 'the next Esc stops listening')
        self.assertEqual(g['menuEsc2']['pressed'], 'false')
        self.assertEqual(g['menuEsc2']['sends'], [], 'and not the agent')

    def test_11_press_while_stopping_listens_again(self):
        g = self.got
        self.assertEqual(g['restop']['calls'], ['start', 'stop'])
        self.assertEqual(g['restop']['pressed'], 'false')
        a = g['again']
        self.assertEqual(a['first'], ['start', 'stop', 'abort'], 'the stopping session is let go')
        self.assertEqual((a['recs'], a['calls'], a['pressed']), (2, ['start'], 'true'), 'a new one listens')
        self.assertEqual(a['value'], 'first')
        self.assertEqual(g['againSaid']['value'], 'first second')
        self.assertEqual(g['againOldEnd']['pressed'], 'true', "the old session's end leaves the new one listening")
        self.assertEqual(g['againDone']['value'], 'first second')

    def test_12_only_the_mics_own_line_is_cleared(self):
        g = self.got
        self.assertEqual(g['otherHint']['hint'], 'It had already stopped.')
        self.assertEqual(g['otherHintListening']['pressed'], 'true')
        self.assertEqual(g['otherHintListening']['hint'], 'It had already stopped.', 'starting keeps another line')
        self.assertEqual(g['otherHintWords']['hint'], 'It had already stopped.')
        self.assertEqual(g['ownHint']['hint'], BLOCKED)
        self.assertEqual(g['ownHintCleared']['pressed'], 'true')
        self.assertEqual(g['ownHintCleared']['hint'], '', "starting again clears the mic's own line")

    def test_13_phone_dictates_from_the_plus_menu(self):
        g = self.got
        r = g['phoneRest']
        self.assertTrue(r['coarse'])
        self.assertFalse(r['micHidden'], 'available')
        self.assertEqual((r['display'], r['w']), ('none', 0), 'no mic in the row at rest')
        self.assertTrue(r['item'])
        m = g['phoneMenu']
        self.assertTrue(m['menuOpen'] and m['itemShown'], m)
        self.assertEqual(m['itemText'], 'Dictate')
        l = g['phoneListening']
        self.assertEqual((l['calls'], l['pressed']), (['start'], 'true'))
        self.assertFalse(l['menuOpen'])
        self.assertTrue(l['focus'], 'the box has the caret')
        self.assertNotEqual(l['display'], 'none')
        self.assertGreaterEqual(min(l['w'], l['h']), 44, 'the pressed mic shows in the row, finger-sized')
        self.assertEqual(g['phoneWords']['value'], 'from the phone')
        self.assertTrue(g['phoneMenu2']['menuOpen'])
        e1 = g['phoneEsc1']
        self.assertFalse(e1['menuOpen'], 'the first Esc closes the + menu')
        self.assertEqual((e1['calls'], e1['pressed']), (['start'], 'true'))
        e2 = g['phoneEsc2']
        self.assertEqual((e2['calls'], e2['pressed']), (['start', 'stop'], 'false'))
        self.assertEqual(e2['display'], 'none', 'the row gives the room back')
        self.assertEqual(e2['sends'], [])
        self.assertEqual(g['phoneEnded']['value'], 'from the phone')
        self.assertEqual(g['phoneEnded']['input'], r['input'], 'the words get their room back')
        n = g['phoneNoEngineMenu']
        self.assertTrue(n['menuOpen'] and n['item'] and n['itemShown'], n)
        self.assertEqual(n['itemDisabled'], 'true')
        self.assertEqual(n['itemColor'], g['noEngine']['greyColor'])
        self.assertEqual(n['display'], 'none', 'no mic in the row at rest')
        t = g['phoneNoEngineTap']
        self.assertFalse(t['menuOpen'], 'the menu closes')
        self.assertEqual(t['hint'], NO_ENGINE)
        self.assertEqual(t['recs'], 0)
        self.assertEqual(t['display'], 'none', 'and the row stays as it was')


    def test_14_a_selection_waits_for_the_first_words(self):
        g = self.got
        for k in ('selDenied', 'selDeniedReload', 'selEsc', 'selSilent', 'selSilentGone'):
            with self.subTest(k):
                self.assertEqual(g[k]['value'], 'Hello big world', 'the original text stays')
                self.assertEqual(g[k]['sends'], [])
        self.assertEqual(g['selDenied']['hint'], BLOCKED)
        self.assertEqual((g['selDenied']['start'], g['selDenied']['end']), (6, 9), 'still selected')
        self.assertEqual(g['selEsc']['calls'], ['start', 'stop'])
        self.assertEqual((g['selEsc']['start'], g['selEsc']['end']), (6, 9))
        self.assertEqual((g['selSilent']['calls'], g['selSilent']['pressed']), (['start', 'start'], 'true'), 'a silent session ended: another one')
        self.assertEqual((g['selSilent']['start'], g['selSilent']['end'], g['selSilent']['hint']), (6, 9, ''), 'still selected')
        self.assertEqual(g['selSilentGone']['calls'], ['start', 'start', 'stop'])
        self.assertEqual(g['selSilentGone']['pressed'], 'false')
        self.assertEqual((g['selSilentGone']['start'], g['selSilentGone']['end']), (6, 9))
        f = g['selFirst']
        self.assertEqual(f['value'], 'Hello small world', 'the first words replace the selection')
        self.assertEqual(f['sel'], 'small')
        self.assertEqual(g['selFirstDone']['value'], 'Hello small world')
        self.assertEqual(g['selFirstReload']['value'], 'Hello small world', 'and are saved')

    def test_15_a_press_in_the_box_ends_listening_where_pressed(self):
        g = self.got
        self.assertEqual(g['pressBefore']['value'], 'Hello world again')
        p = g['pressed']
        self.assertEqual(p['calls'], ['start', 'abort'])
        self.assertEqual(p['pressed'], 'false')
        self.assertEqual(p['start'], p['end'])
        self.assertLess(p['start'], len('Hello world'), 'the caret is where pressed, not after the words')
        at = p['start']
        self.assertEqual(g['pressTyped']['value'], 'Hello world again'[:at] + 'y' + 'Hello world again'[at:])

    def test_16_no_point_until_dictation_ends(self):
        g = self.got
        self.assertEqual((g['ptInterim']['value'], g['ptInterim']['points']), ('1. buy milk', []))
        self.assertEqual((g['ptFinal']['value'], g['ptFinal']['points']), ('1. buy milk and eggs', []))
        self.assertEqual(g['ptEnded']['points'], ['buy milk and eggs'], 'one point with all its words')
        self.assertEqual(g['ptEnded']['value'], '', 'the head is empty')

    def test_17_a_draft_switch_ends_listening(self):
        g = self.got
        self.assertEqual(g['wsBefore']['value'], 'Draft A said here')
        w = g['wsSwitched']
        self.assertTrue(w['key'].endswith(':thread:P1'), w['key'])
        self.assertEqual(w['calls'], ['start', 'abort'])
        self.assertEqual(w['pressed'], 'false')
        self.assertEqual(w['value'], '', "the thread's own draft")
        self.assertEqual(g['wsLate']['value'], '', 'a late result writes nothing')
        self.assertFalse(g['wsBack']['key'].endswith(':thread:P1'))
        self.assertEqual(g['wsBack']['value'], 'Draft A said here', 'the words stayed with the old draft')
        self.assertEqual(g['wsBack']['sends'], [])

    def test_18_esc_in_another_field_is_that_fields(self):
        g = self.got
        self.assertEqual(g['nameListening']['active'], 'compose-thread-name')
        self.assertEqual(g['nameListening']['pressed'], 'true', 'a click in another field leaves listening on')
        r = g['renameEsc']
        self.assertEqual(r['renamed'], [[False, 'Old']], 'the rename is cancelled')
        self.assertEqual((r['calls'], r['pressed']), (['start'], 'true'), 'and listening goes on')
        e = g['nameEsc']
        self.assertEqual((e['calls'], e['pressed']), (['start', 'stop'], 'false'), 'a field with no Esc of its own: Esc stops listening')
        self.assertEqual((e['active'], e['name']), ('compose-thread-name', 'Plans'))
        self.assertEqual(e['sends'], [])

    def test_18b_esc_in_a_points_text_stops_listening_not_the_agent(self):
        g = self.got
        self.assertTrue(g['ptFieldListening']['inPoint'])
        self.assertEqual(g['ptFieldListening']['pressed'], 'true')
        e = g['ptFieldEsc']
        self.assertEqual((e['calls'], e['pressed']), (['start', 'stop'], 'false'))
        self.assertTrue(e['inPoint'])
        self.assertNotIn('/api/room/interrupt', e['posts'], 'the agent is not stopped')
        self.assertEqual(e['sends'], [])

    def test_20_ctrl_enter_sends_grey_list_words_as_a_point(self):
        c = self.got['ctrlEnter']
        self.assertEqual(c['calls'], ['start', 'abort'])
        self.assertEqual(c['sends'], [{'path': '/api/room/resume', 'text': '## Points (1)\n\n**1.** buy milk'}])
        self.assertEqual(c['value'], '')

    def test_21_a_po_thread_switch_keeps_the_point(self):
        g = self.got
        self.assertEqual(g['poSwitched']['calls'], ['start', 'abort'])
        self.assertEqual((g['poSwitched']['value'], g['poSwitched']['points']), ('', []), "the thread's own draft")
        self.assertEqual((g['poBack']['value'], g['poBack']['points']), ('', ['buy milk']), 'the chat draft kept its point')
        self.assertEqual((g['poThread']['value'], g['poThread']['points']), ('', ['eggs']), "and the thread's, after a clear")
        self.assertEqual(g['poThread']['sends'], [])


    def test_19_a_key_over_grey_list_words_replaces_them(self):
        k = self.got['ptKey']
        self.assertEqual(k['calls'], ['start', 'abort'])
        self.assertEqual((k['value'], k['points']), ('x', []), 'the key replaces the grey "1. buy"; it is not made a point')

    def test_22_listening_is_a_pulsing_accent_mic(self):
        g = self.got
        idle, on, red = g['lookIdle'], g['lookOn'], g['lookReduced']
        self.assertEqual((on['w'], on['h'], on['x']), (idle['w'], idle['h'], idle['x']), 'the button keeps its size and place')
        self.assertEqual((on['input'], on['send']), (idle['input'], idle['send']), 'the box does not move')
        self.assertEqual((on['text'], on['kids']), ('', 1), 'just the mic: no Listening pill')
        self.assertEqual((on['label'], on['title']), ('Listening: press again or Esc to stop',) * 2)
        self.assertNotIn('silence', (on['title'] + idle['title']).lower())
        self.assertEqual(idle['label'], 'Dictate')
        self.assertEqual(on['color'], on['accent'], 'the mic is in the accent')
        self.assertNotEqual(idle['color'], idle['accent'])
        self.assertEqual((idle['anim'], on['anim']), ('none', 'mic-pulse'))
        self.assertEqual(on['bg'], idle['bg'], 'no pill behind it')
        self.assertEqual((red['anim'], red['color']), ('none', red['accent']), 'reduced motion: a steady accent mic')


if __name__ == '__main__':
    unittest.main()
