// The PO chat replay's browser (#265): tools/perf_po_chat.py runs it with
// chrome_profile.JS in front and one JSON argument:
//   {chrome, chromeLocalState, tmp, base, out, project, room, minutes, seed}
// It opens the board at `base` with `project` selected (the PO chat docked
// beside it, as the CEO keeps it), lets the page's own polls run, and on a
// fixed script (the same `seed`, the same session): scrolls the chat up and
// back, opens a point chip's card and follows its links, sends a message,
// opens a thread, and reloads the page once at the middle. At a quarter the
// PO rotates and the CEO's sequence of 10-24 is sent (#266), and again at
// three quarters, on the page reloaded since. Every request the
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
  // The chat frame of the dock (its window), evaluated in: F`expr`. The one
  // on screen first: a chat the board put away (another project's PO opened
  // by a cross-room link) keeps its frame, hidden, where nothing lands.
  // On screen: laid out, and not hidden by CSS (a Dock tab put away is
  // visibility: hidden and keeps its size).
  const VIS = `(f => f.getClientRects().length > 0 && f.clientHeight > 0 && (!f.checkVisibility || f.checkVisibility({visibilityProperty: true, opacityProperty: true})))`;
  const FR = `(() => { const fs = [...document.querySelectorAll('iframe')].filter(f => { try { const l = f.contentWindow.location;
    const q = new URLSearchParams(l.search); return l.pathname === '/session' && (q.get('id') || q.get('room')) === ${JSON.stringify(A.room)}; } catch (e) { return false; } });
    const f = fs.find(${VIS}) || fs[0];
    return f ? f.contentWindow : null; })()`;
  // Whether that chat is on screen.
  const SHOWN = `(() => { const w = ${FR}; const f = w && w.frameElement; return !!f && ${VIS}(f); })()`;
  // This chat is the one on screen: shown, and no other room's chat beside it.
  const ACTIVE = `(() => { if (!${SHOWN}) return 'this chat is not shown';
    const other = [...document.querySelectorAll('iframe')].find(f => { try { const l = f.contentWindow.location; const q = new URLSearchParams(l.search);
      return l.pathname === '/session' && (q.get('id') || q.get('room')) !== ${JSON.stringify(A.room)} && ${VIS}(f); } catch (e) { return false; } });
    return other ? 'another chat is on screen: ' + other.contentWindow.location.search.slice(0, 80) : ''; })()`;
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
  // Every chat the board shows (its Dock panels: this chat, another room's
  // a link opened, a thread's panel) and the frames in each (a side
  // thread): where a link or a thread went.
  const lookAll = () => ev(`(() => { const out = [];
    const shown = ${VIS};
    const see = (w, where, vis) => { try { if (w.location.pathname !== '/session') return;
      const v = w.eval(${JSON.stringify('(() => {' + look + '})()')}); delete v.pane; out.push({where, vis, ...v}); } catch (e) {} };
    for (const f of document.querySelectorAll('iframe')) { const w = f.contentWindow; const vis = shown(f); see(w, 'panel', vis);
      try { for (const g of w.document.querySelectorAll('iframe')) see(g.contentWindow, 'side', vis && shown(g)); } catch (e) {} }
    return out; })()`).catch(() => []);

  // Back to the channel, and a thread's Dock panel closed as its × does: a
  // run measures one open chat (each open view of it polls on its own).
  async function closeThreads() {
    await inChat(`if (typeof THREAD_MODE !== 'undefined' && THREAD_MODE && typeof closeThread === 'function') closeThread(); return true;`).catch(() => {});
    await ev(`(() => { try { if (PD.dock && PD.rt.has(PD_THREAD)) PD.dock.removePanel(PD_THREAD); } catch (e) {} return true; })()`).catch(() => {});
    await inChat(`if (typeof sideClose === 'function' && typeof TS !== 'undefined' && TS) sideClose(); return true;`).catch(() => {});
    // A link into another room opened that room's chat (another project's
    // PO): back to this PO, as its project on the board does.
    const why = await ev(ACTIVE).catch(e => String(e).slice(0, 80));
    if (why) {
      await ev(`(() => { const pj = registeredProjects().find(p => p.id === ${JSON.stringify(A.project)}); if (pj) openPoOf(pj); return !!pj; })()`).catch(() => {});
      const back = await until(`(() => { if (${ACTIVE}) return false; try { return !!${FR}.eval('CHAT_DRAWN'); } catch (e) { return false; } })()`, 30000, 200);
      step({do: 'back', ok: !!back, why});
    }
    // Every step ends in this chat, on screen.
    const left = await ev(ACTIVE).catch(e => String(e).slice(0, 80));
    step({do: 'active', ok: !left, ...(left ? {why: left} : {})});
  }

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

  // Whether a message id's conversation is gone and read from the hub's
  // kept text (Free disk space deleted it): asked once per session.
  const keptSid = new Map();
  async function isKept(msg) {
    const sid = (String(msg || '').match(/^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):/) || [])[1];
    if (!sid) return false;
    if (!keptSid.has(sid)) keptSid.set(sid, await inChat(`return fetch('/api/session/${sid}?stat=1').then(r => r.ok ? r.json() : {}).then(d => !!d.kept, () => false);`).catch(() => false));
    return keptSid.get(sid);
  }
  // Each point step looks for a chip of the next kind in turn: one into a
  // conversation that was deleted (kept), one of another room, a closed one,
  // any. The run fails when a kind was never found (analyse).
  const KINDS = ['old', 'cross', 'closed', 'any'];
  let pointN = 0;
  async function point() {
    // A point chip in the chat, as the CEO opens one: its card, then each of its links.
    const chips = await inChat(`return [...document.querySelectorAll('#msgs a.point-chip')].map((a, i) => ({ i,
      ref: a.textContent.trim(), room: a.dataset.refRoom, msg: a.dataset.refMsg, part: a.dataset.refPart,
      state: (() => { try { return JSON.parse(a.dataset.card || '{}').state || ''; } catch (e) { return ''; } })() }));`);
    if (!chips || !chips.length) { step({do: 'point', chips: 0}); return; }
    for (const ch of chips) {
      ch.cross = !!ch.room && ch.room !== A.room;
      ch.closed = /^acknowledged/.test(ch.state);
    }
    const want = KINDS[pointN++ % KINDS.length];
    let pool = chips;
    if (want === 'old') {
      pool = [];
      for (const ch of chips) if (await isKept(ch.msg)) pool.push(ch);
    } else if (want !== 'any') pool = chips.filter(ch => ch[want]);
    if (!pool.length && (want === 'old' || want === 'cross')) {
      // None on screen: a point of that kind the hub knows, followed the way
      // its chip is (refClickHandler), so the kind is covered in every run.
      const far = await farPoint(want, chips);
      step({do: 'point', chips: chips.length, want, found: 0, far: far ? far.ref : null});
      if (far) await follow(far, [{text: '(chip, ' + want + ')', room: far.room, msg: far.msg, part: far.part}], true);
      return;
    }
    const pick = (pool.length ? pool : chips)[rand(pool.length || chips.length)];
    pick.old = await isKept(pick.msg);
    step({do: 'point', chips: chips.length, want, found: pool.length, ref: pick.ref, kinds: {old: pick.old, cross: pick.cross, closed: pick.closed}});
    const card = await inChat(`const a = [...document.querySelectorAll('#msgs a.point-chip')][${pick.i}]; if (!a) return null;
      a.scrollIntoView({block: 'center'}); a.click();
      const c = document.querySelector('.task-card'); if (!c) return {card: false};
      return {card: true, links: [...c.querySelectorAll('a.tc-title, a.tc-link')].map(l => ({ text: l.textContent.trim().slice(0, 40),
        room: l.dataset.refRoom, msg: l.dataset.refMsg, part: l.dataset.refPart }))};`);
    await follow(pick, (card && card.links) || [], false);
  }

  // The points of this project the hub has, newest first (300 at most),
  // asked once: where farPoint finds one of a kind no chip on screen is.
  let farPoints = null, keptAsked = 0;
  async function farPoint(want, chips) {
    if (!farPoints) {
      const top = Math.max(1, ...chips.map(c => +((c.ref.match(/^P(\d+)/) || [0, 0])[1])));
      const ids = [];
      for (let n = top + 20; n > 0 && ids.length < 300; n--) ids.push('P' + n);
      farPoints = [];
      for (let i = 0; i < ids.length; i += 100) {
        const d = await inChat(`return fetch('/api/point/refs?ids=${ids.slice(i, i + 100).join(',')}&project=${encodeURIComponent(A.project)}&room=${encodeURIComponent(A.room)}')
          .then(r => r.json()).then(d => d.points || {}, () => ({}));`).catch(() => ({}));
        for (const p of Object.values(d || {})) if (p && p.mid) farPoints.push({ref: p.id, room: p.roomId, msg: p.mid, part: 'pt:' + p.id, closed: p.stage === 'acked'});
      }
    }
    const pool = [];
    for (const p of farPoints) {
      if (want === 'cross' ? p.room !== A.room : (p.room === A.room && keptAsked++ < 60 && await isKept(p.msg))) pool.push(p);
      if (pool.length >= 20) break;
    }
    return pool.length ? pool[rand(pool.length)] : null;
  }

  // Each link of a chip's card (or the chip itself), opened again for the
  // next one; ``synthetic``: a point no chip on screen shows, clicked
  // through the page's own handler.
  async function follow(pick, links, synthetic) {
    for (let k = 0; k < Math.max(1, links.length); k++) {
      const l = links[k] || {text: '(chip)', room: pick.room, msg: pick.msg, part: pick.part};
      // The thread panels already open (an earlier step's): not this link's.
      const was = new Set((await lookAll()).filter(x => x.thread).map(x => x.where + '|' + x.thread));
      // A landed mark stays a few seconds: an earlier link's mark is not this one's.
      await ev(`(() => { const clear = (d) => { try { d.querySelectorAll('.landed').forEach(e => e.classList.remove('landed'));
        d.querySelectorAll('iframe').forEach(f => clear(f.contentWindow.document)); } catch (e) {} }; clear(document); return true; })()`).catch(() => {});
      const want = l.room || A.room;
      const at = (x) => x.vis && x.room === want && (x.landed || []).includes(l.msg);
      const t = now();
      if (synthetic) await inChat(`const a = document.createElement('a'); a.dataset.point = 'true';
        a.dataset.refRoom = ${JSON.stringify(l.room)}; a.dataset.refMsg = ${JSON.stringify(l.msg)}; a.dataset.refPart = ${JSON.stringify(l.part)};
        a.href = '/session?room=' + encodeURIComponent(a.dataset.refRoom) + '&msg=' + encodeURIComponent(a.dataset.refMsg) + '&part=' + a.dataset.refPart;
        refClickHandler({target: a, button: 0, preventDefault() {}}); return true;`);
      else await inChat(`const a = [...document.querySelectorAll('#msgs a.point-chip')].find(a => a.textContent.trim() === ${JSON.stringify(pick.ref)});
        if (!a) return false; if (!document.querySelector('.task-card')) a.click();
        const l = [...document.querySelectorAll('.task-card a.tc-title, .task-card a.tc-link')][${k}];
        (l || a).dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, button: 0, detail: 1})); return true;`);
      // Where it went: a landed mark on the message (in this chat, the chat
      // it opened, or a thread's panel), or a note: within 8 s.
      let views = [];
      const end = Date.now() + 8000;
      while (Date.now() < end) {
        await sleep(250);
        views = await lookAll();
        if (views.some(at) || views.some(x => x.vis && x.note)) break;
      }
      // Landed: the mark on the message, in a chat of the room the link
      // names, on screen (a hidden frame or another room does not count).
      const landed = views.some(at);
      const hidden = !landed && views.some(x => (x.landed || []).includes(l.msg));
      const empty = views.some(x => x.vis && x.thread && x.msgs === 0 && !was.has(x.where + '|' + x.thread));
      const note = views.filter(x => x.vis).map(x => x.note).filter(Boolean)[0] || '';
      // "Not shown in this chat" for a room message filed as the CEO's that no
      // transcript holds (an agent posted it with the CEO's token, /api/room/say):
      // the chat has no balloon for it, and says so. Listed, not failed.
      const roomUser = !landed && /not shown in this chat/.test(note) && await inChat(`return fetch('/api/room/msg?room=' + encodeURIComponent(${JSON.stringify(l.room || A.room)}) + '&msg=' + encodeURIComponent(${JSON.stringify(l.msg)}))
        .then(r => r.ok ? r.json() : {}).then(d => d.from === 'user' && !String(d.id || '').includes(':'), () => false);`).catch(() => false);
      step({do: 'point-link', synthetic, ref: pick.ref, link: l.text, room: l.room, msg: l.msg, part: l.part, ...(roomUser ? {roomUser: true} : {}),
        kinds: {old: await isKept(l.msg), cross: !!l.room && l.room !== A.room, closed: !!pick.closed},
        landed, ...(hidden ? {landedHidden: true} : {}), empty, note, s: +(now() - t).toFixed(2),
        where: views.filter(v => v.thread || (v.landed || []).length || v.note)
          .map(v => ({where: v.where, vis: v.vis, room: v.room, thread: v.thread, msgs: v.msgs, landed: v.landed, note: v.note}))});
      // Back to the channel for the next step.
      await closeThreads();
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

  // The hub's replay controls (perf_hub): the PO's rotation, its answers.
  const replay = (what) => fetch(A.base + '/__replay/' + what, {method: 'POST', body: '{}'}).then(r => r.json());
  async function rotate() {
    const r = await replay('rotate').catch(e => ({ok: false, error: String(e).slice(0, 200)}));
    step({do: 'rotate', ok: !!r.ok, ...(r.ok ? {} : {why: r.error || ''})});
  }
  // The CEO's sequence of 10-24 (#266), after a rotation: a question and its
  // answer, the quick answer that was interrupted, a second question, a third
  // sent as that answer lands, a re-ask, "are you getting my messages". Each
  // balloon must never leave the chat, each answer must show without a reload.
  const SEQUENCE = [['How far is the backtest?', 1], ['Yes, start now', 0], ['actually walk me through the plan first', 0],
    ['and show me visuals', 1], ['Walk me through the plan first', 1], ['Are you getting my messages?', 1]];
  let seqN = 0;
  async function sequence() {
    const t = now(), run = seqN++;
    const sent = SEQUENCE.map(([words, wait], i) => ({tok: 'perf-' + A.seed + '-' + (900 + run * 10 + i) + '-' + Date.now().toString(36) + i,
      words, wait, answer: !words.startsWith('Yes,')}));
    const seen = {}, gone = [], shownAt = {};
    let sampling = true;
    const toks = JSON.stringify(sent.map(x => x.tok));
    const sampler = (async () => {
      while (sampling) {
        const v = await inChat(`const box = document.getElementById('msgs'); if (!box) return null;
          const out = {in: [], ans: []}, all = box.textContent || '';
          for (const tok of ${toks}) {
            const b = [...box.querySelectorAll('.msg')].find(m => (m.textContent || '').includes(tok) && !(m.textContent || '').includes('Replay answer ' + tok));
            if (b) out.in.push(tok);
            const l = b && b.nextElementSibling, n = l && l.classList.contains('thr-line') && l.querySelector('.thr-count');
            if (all.includes('Replay answer ' + tok) || (n && /[1-9]/.test(n.textContent))) out.ans.push(tok);
          }
          return out;`).catch(() => null);
        if (v) {
          for (const tok of v.in) seen[tok] = seen[tok] || Date.now();
          for (const tok of Object.keys(seen)) if (!v.in.includes(tok) && !gone.includes(tok)) gone.push(tok);
          for (const tok of v.ans) shownAt[tok] = shownAt[tok] || Date.now();
        }
        await sleep(250);
      }
    })();
    const press = async x => {
      await until(`(() => { const w = ${FR}; return !!w && !w.document.getElementById('send').disabled
        || (!!w && (w.document.getElementById('input').value = ' ', w.document.getElementById('input').dispatchEvent(new w.Event('input')), false)); })()`, 20000, 200);
      x.at = Date.now();
      await inChat(`const i = document.getElementById('input'); i.value = ${JSON.stringify('')} + ${JSON.stringify(x.tok)} + ' ' + ${JSON.stringify(x.words)};
        i.dispatchEvent(new Event('input', {bubbles: true})); document.getElementById('send').click(); return true;`);
    };
    const written = async (tok, ms) => { const end = Date.now() + ms; let w = {};
      while (Date.now() < end && !(w = await replay('written').catch(() => ({})))[tok]) await sleep(100);
      return w[tok] ? w[tok] * 1000 : 0; };
    for (let i = 0; i < sent.length; i++) {
      const x = sent[i];
      // "and show me visuals": the moment the answer before it is written.
      if (i > 0 && !sent[i - 1].wait && sent[i - 1].answer) await written(sent[i - 1].tok, 15000);
      await press(x);
      if (x.wait) await written(x.tok, 20000); else await sleep(x.answer ? 300 : 2500);
    }
    // Every answer written, then long enough to show (the budget) and for a
    // confirmed send to have left the hub's list if nothing held it (2 min).
    for (const x of sent) if (x.answer) x.writtenAt = await written(x.tok, 20000);
    const settle = Date.now() + Math.max(budgetsReplyMs(), 125000);
    while (Date.now() < settle) await sleep(500);
    sampling = false; await sampler;
    for (const x of sent) {
      x.shownMs = seen[x.tok] ? seen[x.tok] - x.at : null;
      x.answerMs = x.answer ? (shownAt[x.tok] && x.writtenAt ? Math.max(0, shownAt[x.tok] - x.writtenAt) : null) : undefined;
      x.gone = gone.includes(x.tok);
    }
    step({do: 'sequence', run, sends: sent.map(({tok, words, shownMs, answerMs, gone}) => ({tok, words, shownMs, answerMs, gone})),
      s: +(now() - t).toFixed(2)});
  }
  const budgetsReplyMs = () => A.replyShownMs || 5000;

  async function thread() {
    const t = now();
    // One of the chat's threads, as its row in the channel opens it (openThread).
    const r = await inChat(`if (typeof THREADED === 'undefined' || !THREADED) return {threaded: false};
      const ids = Object.values(THR_META || {}).filter(t => t && t.id && t.id !== 'team:activity').map(t => t.id);
      if (!ids.length) return {threaded: true, none: true};
      const id = ids[${rand(100000)} % ids.length]; openThread(id, ''); return {threaded: true, id};`).catch(e => ({error: String(e).slice(0, 100)}));
    // The thread shows in a Dock panel of its own, in the chat's side frame,
    // or in the chat: wait up to 8 s for its messages there.
    let shown = [];
    const end = Date.now() + 8000;
    while (r && r.id && Date.now() < end) {
      await sleep(250);
      shown = (await lookAll()).filter(x => x.vis && x.thread === r.id);
      if (shown.some(x => x.msgs > 0)) break;
    }
    step({do: 'thread', ...r, opened: shown.length > 0, where: shown.map(x => x.where),
      thread: shown.map(x => x.thread)[0] || '', msgs: shown.map(x => x.msgs)[0],
      empty: shown.length > 0 && !shown.some(x => x.msgs > 0), s: +(now() - t).toFixed(2)});
    await closeThreads();
  }

  await open();
  const minutes = A.minutes || 30, end = T0 + minutes * 60000;
  let n = 0, reloaded = false, rotated = false, again = false;
  // The script: every 20 s one step, in a fixed rotation.
  const plan = ['point', 'scroll', 'point', 'send', 'point', 'thread'];
  while (Date.now() < end) {
    const what = plan[n % plan.length];
    try {
      if (!rotated && Date.now() > T0 + minutes * 15000) { rotated = true; await rotate(); await sequence(); }
      else if (!reloaded && Date.now() > T0 + minutes * 30000) { reloaded = true; step({do: 'reload'}); await open(); }
      else if (!again && Date.now() > T0 + minutes * 45000) { again = true; await sequence(); }
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
  for (const r of reqs.values()) { r.ms = null; r.open = true; r.endAt = now(); delete r.t; pageLog.write(JSON.stringify(r) + '\n'); }
  await new Promise(r => pageLog.end(r)); await new Promise(r => stepLog.end(r));
  try { await send('Browser.close'); } catch (e) {}
  ch.kill();
}
main().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
