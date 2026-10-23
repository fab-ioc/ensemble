// The PO chat replay's browser (#265): tools/perf_po_chat.py runs it with
// chrome_profile.JS in front and one JSON argument:
//   {chrome, chromeLocalState, tmp, base, out, project, room, minutes, seed}
// It opens the board at `base` with `project` selected (the PO chat docked
// beside it, as the CEO keeps it), lets the page's own polls run, and on a
// fixed script (the same `seed`, the same session): scrolls the chat up and
// back, opens a point chip's card and follows its links, sends a message,
// opens a thread, and reloads the page once at the middle. Every request the
// page makes is written to out/page.jsonl; every step and what it led to
// (where a link landed, how long a send took) to out/steps.jsonl.
const {spawn} = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const T0 = Date.now(), now = () => (Date.now() - T0) / 1000;
const pageLog = fs.createWriteStream(path.join(A.out, 'page.jsonl'), {flags: 'a'});
const stepLog = fs.createWriteStream(path.join(A.out, 'steps.jsonl'), {flags: 'a'});
const step = o => { stepLog.write(JSON.stringify({at: +now().toFixed(2), ...o}) + '\n'); console.log(JSON.stringify(o).slice(0, 300)); };
let rnd = A.seed || 1;
const rand = n => { rnd = (rnd * 1103515245 + 12345) % 2147483648; return rnd % n; };

async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', '--window-size=1600,1000', 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
  const ws = await new Promise((res, rej) => { let buf = ''; ch.stderr.on('data', d => {
    buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no CDP ' + buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq = 0;
  await new Promise((res, rej) => { c.onopen = res; c.onerror = rej; });
  const reqs = new Map();
  c.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id) { const w = waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w[1](Error(m.error.message)) : w[0](m.result); } return; }
    const p = m.params || {};
    if (m.method === 'Network.requestWillBeSent') {
      const u = new URL(p.request.url);
      if (u.pathname.startsWith('/api/')) reqs.set(p.requestId, {at: now(), t: p.timestamp, m: p.request.method, p: u.pathname, q: u.search.slice(1, 300)});
    } else if (m.method === 'Network.responseReceived') {
      const r = reqs.get(p.requestId); if (r) r.status = p.response.status;
    } else if (m.method === 'Network.loadingFinished' || m.method === 'Network.loadingFailed') {
      const r = reqs.get(p.requestId); if (!r) return;
      reqs.delete(p.requestId);
      r.ms = +((p.timestamp - r.t) * 1000).toFixed(1); delete r.t;
      if (m.method === 'Network.loadingFailed') { r.failed = p.errorText; if (p.canceled) r.canceled = true; }
      pageLog.write(JSON.stringify(r) + '\n');
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const id = ++seq; waits.set(id, [res, rej]); c.send(JSON.stringify({id, method, params, sessionId})); });
  const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
  const {sessionId: s} = await send('Target.attachToTarget', {targetId, flatten: true});
  await send('Page.enable', {}, s);
  await send('Network.enable', {}, s);
  await send('Network.setCacheDisabled', {cacheDisabled: false}, s);
  await send('Emulation.setFocusEmulationEnabled', {enabled: true}, s);
  await send('Emulation.setDeviceMetricsOverride', {width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false}, s);
  const ev = async (expression, timeout = 30000) => {
    const r = await Promise.race([send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, s),
      sleep(timeout).then(() => ({timedOut: true}))]);
    if (r.timedOut) throw Error('evaluate timed out: ' + expression.slice(0, 80));
    if (r.exceptionDetails) throw Error(expression.slice(0, 100) + ' ' + JSON.stringify(r.exceptionDetails).slice(0, 400));
    return r.result.value;
  };
  const until = async (expression, ms = 30000, every = 100) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { try { const v = await ev(expression, 10000); if (v) return v; } catch (e) {} await sleep(every); }
    return null;
  };
  // The chat frame of the dock (its window), evaluated in: F`expr`.
  const FR = `(() => { const f = [...document.querySelectorAll('iframe')].find(f => { try { const l = f.contentWindow.location;
    const q = new URLSearchParams(l.search); return l.pathname === '/session' && (q.get('id') || q.get('room')) === ${JSON.stringify(A.room)}; } catch (e) { return false; } });
    return f ? f.contentWindow : null; })()`;
  const inChat = (body) => ev(`(() => { const w = ${FR}; if (!w) return null; return w.eval(${JSON.stringify('(() => {' + body + '})()')}); })()`);

  async function open() {
    const t = now();
    await send('Page.navigate', {url: A.base + '/'}, s);
    await until(`!!document.querySelector('[data-proj="${A.project}"]')`, 90000, 250);
    await ev(`(() => { const c = document.querySelector('[data-proj="${A.project}"]'); if (c) { c.click(); return 'click'; }
      SELECTED_PROJECT = ${JSON.stringify(A.project)}; PROJECT_TAB = 'tasks'; renderRows(); return 'set'; })()`);
    const drawn = await until(`(() => { const w = ${FR}; try { return !!(w && w.eval('CHAT_DRAWN')); } catch (e) { return false; } })()`, 120000, 200);
    const why = drawn ? undefined : await ev(`({ project: typeof SELECTED_PROJECT !== 'undefined' ? SELECTED_PROJECT : '?', body: document.body.className,
      frames: [...document.querySelectorAll('iframe')].map(f => f.getAttribute('src') || (f.contentWindow && f.contentWindow.location.href) || '').map(s => s.slice(0, 120)),
      projs: [...document.querySelectorAll('[data-proj]')].map(e => e.dataset.proj).slice(0, 20) })`).catch(e => String(e));
    step({do: 'open', ok: !!drawn, s: +(now() - t).toFixed(2), ...(why ? {why} : {})});
  }

  // What the chat shows now: where a link took it.
  const look = `const box = document.getElementById('msgs');
    const note = document.getElementById('goto-note');
    const thr = typeof THREAD_MODE !== 'undefined' && THREAD_MODE;
    return { room: ROOM, thread: thr ? (THREAD || ('m:' + THREAD_MID)) : '', msgs: box ? box.querySelectorAll('.msg[data-mid]').length : -1,
      landed: [...document.querySelectorAll('.landed')].map(e => (e.closest('[data-mid]') || {dataset: {}}).dataset.mid || '?').slice(0, 3),
      note: note && !note.hidden ? note.textContent.trim().slice(0, 120) : '',
      pane: [...document.querySelectorAll('iframe')].map(f => { try { const w = f.contentWindow; return { src: w.location.search.slice(0, 120),
        thread: w.eval('typeof THREAD_MODE !== "undefined" && THREAD_MODE ? (THREAD || ("m:" + THREAD_MID)) : ""'),
        msgs: w.document.querySelectorAll('#msgs .msg[data-mid]').length,
        landed: [...w.document.querySelectorAll('.landed')].map(e => (e.closest('[data-mid]') || {dataset: {}}).dataset.mid || '?').slice(0, 3),
        note: (n => n && !n.hidden ? n.textContent.trim().slice(0, 120) : '')(w.document.getElementById('goto-note')) }; } catch (e) { return null; } }).filter(Boolean) };`;

  async function scroll() {
    const t = now();
    const r = await inChat(`const box = document.getElementById('msgs'); if (!box) return null;
      const before = box.querySelectorAll('.msg[data-mid]').length; box.scrollTop = 0; box.dispatchEvent(new Event('scroll'));
      return before;`);
    await sleep(2500);
    const after = await inChat(`const box = document.getElementById('msgs'); const n = box.querySelectorAll('.msg[data-mid]').length;
      box.scrollTop = box.scrollHeight; box.dispatchEvent(new Event('scroll')); return n;`);
    step({do: 'scroll', before: r, after, s: +(now() - t).toFixed(2)});
  }

  async function point() {
    // A point chip in the chat, as the CEO opens one: its card, then each of its links.
    const chips = await inChat(`return [...document.querySelectorAll('#msgs a.point-chip')].map((a, i) => ({ i,
      ref: a.textContent.trim(), room: a.dataset.refRoom, msg: a.dataset.refMsg, part: a.dataset.refPart }));`);
    if (!chips || !chips.length) { step({do: 'point', chips: 0}); return; }
    const pick = chips[rand(chips.length)];
    const card = await inChat(`const a = [...document.querySelectorAll('#msgs a.point-chip')][${pick.i}]; if (!a) return null;
      a.scrollIntoView({block: 'center'}); a.click();
      const c = document.querySelector('.task-card'); if (!c) return {card: false};
      return {card: true, links: [...c.querySelectorAll('a.tc-title, a.tc-link')].map(l => ({ text: l.textContent.trim().slice(0, 40),
        room: l.dataset.refRoom, msg: l.dataset.refMsg, part: l.dataset.refPart }))};`);
    const links = (card && card.links) || [];
    for (let k = 0; k < Math.max(1, links.length); k++) {
      const l = links[k] || {text: '(chip)', room: pick.room, msg: pick.msg, part: pick.part};
      const t = now();
      // Each link from the chip's card, opened again for the next one.
      await inChat(`const a = [...document.querySelectorAll('#msgs a.point-chip')].find(a => a.textContent.trim() === ${JSON.stringify(pick.ref)});
        if (!a) return false; if (!document.querySelector('.task-card')) a.click();
        const l = [...document.querySelectorAll('.task-card a.tc-title, .task-card a.tc-link')][${k}];
        (l || a).dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, button: 0, detail: 1})); return true;`);
      // Where it went: a landed mark on the message (here or in the chat it opened),
      // a thread with its messages, or a note: within 8 s.
      let seen = null;
      const end = Date.now() + 8000;
      while (Date.now() < end) {
        await sleep(250);
        seen = await ev(`(() => { const w = ${FR}; if (!w) return null; return w.eval(${JSON.stringify('(() => {' + look + '})()')}); })()`).catch(() => null);
        const all = seen ? [seen, ...(seen.pane || [])] : [];
        if (all.some(x => (x.landed || []).includes(l.msg)) || all.some(x => x.note)) break;
      }
      const all = seen ? [seen, ...(seen.pane || [])] : [];
      const landed = all.some(x => (x.landed || []).includes(l.msg));
      const empty = all.some(x => x.thread && x.msgs === 0);
      step({do: 'point-link', ref: pick.ref, link: l.text, room: l.room, msg: l.msg, part: l.part,
        landed, empty, note: all.map(x => x.note).filter(Boolean)[0] || '', s: +(now() - t).toFixed(2),
        where: seen ? {thread: seen.thread, msgs: seen.msgs, pane: (seen.pane || []).filter(p => p.thread || (p.landed || []).length || p.note)} : null});
      // Back to the channel for the next step.
      await inChat(`if (typeof THREAD_MODE !== 'undefined' && THREAD_MODE && typeof closeThread === 'function') closeThread(); return true;`).catch(() => {});
      await sleep(500);
    }
  }

  async function sendMsg(n) {
    const token = 'perf-' + A.seed + '-' + n + '-' + Date.now().toString(36);
    const ready = await until(`(() => { const w = ${FR}; return !!w && !w.document.getElementById('send').disabled
      || (!!w && (w.document.getElementById('input').value = ' ', w.document.getElementById('input').dispatchEvent(new w.Event('input')), false)); })()`, 20000, 200);
    const t = now();
    const press = await inChat(`const i = document.getElementById('input'); i.value = ${JSON.stringify('Replay check ' + token + ', please ignore.')};
      i.dispatchEvent(new Event('input', {bubbles: true}));
      window.__perfT0 = performance.now(); document.getElementById('send').click(); return !!i;`);
    // Shown: the balloon with the words; sent: the hub holds it (its state says so).
    let shown = null, sent = null, state = '';
    const end = Date.now() + 30000;
    while (Date.now() < end && sent === null) {
      const r = await inChat(`const b = [...document.querySelectorAll('#msgs .msg.user')].find(m => (m.textContent || '').includes(${JSON.stringify(token)}));
        if (!b) return null; const st = b.querySelector('.pend-state'); return { ms: performance.now() - window.__perfT0, st: st ? st.textContent.trim() : '', pending: b.classList.contains('pending') };`).catch(() => null);
      if (r && shown === null) shown = +r.ms.toFixed(0);
      if (r && /Delivered|read|Sent into|Queued/i.test(r.st)) { sent = +r.ms.toFixed(0); state = r.st.slice(0, 80); }
      else if (r && !r.st && !r.pending) { sent = +r.ms.toFixed(0); state = '(posted)'; }
      else if (r) state = r.st.slice(0, 80);
      await sleep(20);
    }
    step({do: 'send', ok: !!press && ready !== null, shownMs: shown, sentMs: sent, state, s: +(now() - t).toFixed(2)});
  }

  async function thread() {
    const t = now();
    // One of the chat's threads, as its row in the channel opens it (openThread).
    const r = await inChat(`if (typeof THREADED === 'undefined' || !THREADED) return {threaded: false};
      const ids = Object.values(THR_META || {}).filter(t => t && t.id && t.id !== 'team:activity').map(t => t.id);
      if (!ids.length) return {threaded: true, none: true};
      const id = ids[${rand(100000)} % ids.length]; openThread(id, ''); return {threaded: true, id};`).catch(e => ({error: String(e).slice(0, 100)}));
    await sleep(3000);
    const seen = await inChat(look).catch(() => null);
    step({do: 'thread', ...r, thread: seen && seen.thread, msgs: seen && seen.msgs, empty: !!(seen && seen.thread && seen.msgs === 0), s: +(now() - t).toFixed(2)});
    await inChat(`if (typeof THREAD_MODE !== 'undefined' && THREAD_MODE && typeof closeThread === 'function') closeThread(); return true;`).catch(() => {});
  }

  await open();
  const minutes = A.minutes || 30, end = T0 + minutes * 60000;
  let n = 0, reloaded = false;
  // The script: every 20 s one step, in a fixed rotation.
  const plan = ['point', 'scroll', 'point', 'send', 'point', 'thread'];
  while (Date.now() < end) {
    const what = plan[n % plan.length];
    try {
      if (!reloaded && Date.now() > T0 + minutes * 30000) { reloaded = true; step({do: 'reload'}); await open(); }
      else if (what === 'point') await point();
      else if (what === 'scroll') await scroll();
      else if (what === 'send') await sendMsg(n);
      else if (what === 'thread') await thread();
    } catch (e) { step({do: what, error: String(e).slice(0, 300)}); }
    n++;
    const left = T0 + n * 20000 - Date.now();
    if (left > 0) await sleep(Math.min(left, Math.max(0, end - Date.now())));
  }
  // Requests still out at the end are written as such.
  for (const r of reqs.values()) { r.ms = null; r.open = true; delete r.t; pageLog.write(JSON.stringify(r) + '\n'); }
  await new Promise(r => pageLog.end(r)); await new Promise(r => stepLog.end(r));
  try { await send('Browser.close'); } catch (e) {}
  ch.kill();
}
main().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
