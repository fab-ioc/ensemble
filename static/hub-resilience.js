/* Keep an open page and its pending chat writes through a hub restart. */
(() => {
  const raw = window.fetch.bind(window);
  const pending = [];
  let offline = false, timer = 0, delay = 500, probing = false, flushing = false;
  const retryable = new Set(['/api/room/resume', '/api/room/points',
                             '/api/room/ask', '/api/send']);
  // A terminal injection has no durable receipt. A lost response can mean
  // that the terminal already received it, so only queue it before the send.
  const uncertainOnFailure = new Set(['/api/send']);
  const unavailable = res => [502, 503, 504].includes(res.status);
  const MAX_REPLAYS = 3;
  function deadline(ms) {
    if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(ms);
    const ctl = new AbortController();
    setTimeout(() => ctl.abort(new DOMException('Timed out', 'TimeoutError')), ms);
    return ctl.signal;
  }

  function rejectUncertain(p) {
    const path = typeof p.input === 'string' ? p.input.split('?')[0] : '';
    if (!uncertainOnFailure.has(path)) return false;
    pending.shift();
    p.reject(new Error('May not have been delivered - check the chat, then Retry.'));
    return true;
  }

  async function flush() {
    if (flushing) return;
    flushing = true;
    try {
    while (pending.length && !offline) {
      const p = pending[0];
      try {
        const reply = await raw(p.input, p.init);
        if (unavailable(reply)) {
          rejectUncertain(p);
          markOffline();
          break;
        }
        const i = pending.indexOf(p);
        if (i >= 0) pending.splice(i, 1);
        p.resolve(reply);
      } catch (e) {
        if (p.init?.signal?.aborted) {      // its caller gave up meanwhile (hold took it out)
          const i = pending.indexOf(p);
          if (i >= 0) pending.splice(i, 1);
          p.reject(e);
          continue;
        }
        // Lost again while the hub answers other requests: this request, not
        // the hub, is what fails (#251 measured a send replayed on every
        // answered poll, 140 a second, its button busy for good). Given up
        // after MAX_REPLAYS, so its caller says it was not sent.
        if (!rejectUncertain(p) && ++p.replays >= MAX_REPLAYS) {
          const i = pending.indexOf(p);
          if (i >= 0) pending.splice(i, 1);
          p.reject(e);
        }
        markOffline();
        break;
      }
    }
    } finally { flushing = false; }
  }

  function line() {
    let el = document.getElementById('hub-connection-line');
    if (!el && document.body) {
      el = document.createElement('div');
      el.id = 'hub-connection-line';
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:fixed;z-index:99999;bottom:12px;left:50%;transform:translateX(-50%);padding:5px 12px;border:1px solid var(--border);border-radius:var(--radius-sm,4px);background:var(--surface);color:var(--fg-subtle);font-size:12px;box-shadow:var(--shadow-sm,none)';
      el.textContent = 'Updating · back in a moment';
      document.body.appendChild(el);
    }
    if (el) el.hidden = !offline;
  }

  async function probe() {
    timer = 0;
    if (!offline || probing) return;
    probing = true;
    try {
      // Back once the hub is past its first steps (/api/health, ED-227);
      // a hub from before that answers /api/platform only.
      // Each look gives up after 10 s: one that never answered (a connection
      // lost on the way) kept the page offline, and every send held, for good.
      let res = await raw('/api/health', { cache: 'no-store', signal: deadline(10000) });
      if (res.status === 404) res = await raw('/api/platform', { cache: 'no-store', signal: deadline(10000) });
      if (!res.ok) throw new Error('hub unavailable');
      if (res.url.endsWith('/api/health')) {
        const h = await res.json();
        if (h.phase === 'starting') throw new Error('hub starting');
      }
      offline = false;
      delay = 500;
      line();
      window.dispatchEvent(new Event('hub:online'));
      await flush();
    } catch {
      offline = true;
    } finally {
      probing = false;
      if (offline) schedule();
    }
  }

  function schedule() {
    if (timer || probing) return;
    timer = setTimeout(probe, delay);
    delay = Math.min(8000, delay * 2);
  }

  function markOffline() {
    if (!offline) {
      offline = true;
      line();
      window.dispatchEvent(new Event('hub:offline'));
    }
    schedule();
  }

  // A send held for the hub's return gives up with its caller (sendJson's
  // deadline): it leaves the queue, so it is not sent behind the caller's back.
  function hold(input, init) {
    return new Promise((resolve, reject) => {
      const p = {input, init, resolve, reject, replays: 0};
      const signal = init?.signal;
      if (signal?.aborted) return reject(signal.reason || new DOMException('Aborted', 'AbortError'));
      signal?.addEventListener('abort', () => {
        const i = pending.indexOf(p);
        if (i >= 0) pending.splice(i, 1);
        reject(signal.reason || new DOMException('Aborted', 'AbortError'));
      }, {once: true});
      pending.push(p);
    });
  }

  window.fetch = async (input, init) => {
    const path = typeof input === 'string' ? input.split('?')[0] : '';
    const api = path.startsWith('/api/');
    const queued = retryable.has(path) && (init?.method || 'GET').toUpperCase() === 'POST';
    if (queued && offline) return hold(input, init);
    try {
      const res = await raw(input, init);
      if (api && unavailable(res)) {
        markOffline();
        if (queued && !uncertainOnFailure.has(path))
          return hold(input, init);
        throw new Error('hub unavailable');
      }
      if (api && offline && res.ok) {
        offline = false;
        delay = 500;
        line();
        window.dispatchEvent(new Event('hub:online'));
        if (pending.length) flush();
      }
      return res;
    } catch (e) {
      if (!api || e?.name === 'AbortError' || e?.name === 'TimeoutError') throw e;
      markOffline();
      if (queued && !uncertainOnFailure.has(path))
        return hold(input, init);
      throw e;
    }
  };
  window.HubResilience = { get offline() { return offline; }, get queued() { return pending.length; }, probe };

  // Every send-like button's request (#251: Send, a quick answer, Retry, a
  // batch of comments, feedback): POST JSON, its answer read, given up after
  // ``ms`` (20 s), so the button's busy state always ends. A request that
  // never answered (a connection lost on the way, a send held above while
  // the hub never came back) kept "Sending…" until the page was reloaded.
  // A page the browser put to sleep (a hidden tab, a phone's locked screen)
  // checks its deadline again when it is shown. Resolves {ok, status, data};
  // throws an Error with ``timeout`` (no answer in time) or ``network`` (no
  // connection) set and ``status`` 0. What a send reached is the hub's to
  // say: a send that carries a key is taken once, so sending it again is safe.
  const inFlight = new Set();
  function sendJson(path, body, opts) {
    const ms = (opts && opts.ms) || 20000;
    const ctl = new AbortController();
    const it = {at: Date.now(), ms, stop: () => ctl.abort(new DOMException('Timed out', 'TimeoutError'))};
    const timer = setTimeout(it.stop, ms);
    inFlight.add(it);
    // Settles at the deadline even when the request under it does not.
    const gaveUp = new Promise((_, reject) => ctl.signal.addEventListener('abort', () => reject(ctl.signal.reason), {once: true}));
    const asked = (async () => {
      const r = await window.fetch(path, {method: (opts && opts.method) || 'POST', headers: {'Content-Type': 'application/json'},
                                          body: JSON.stringify(body || {}), signal: ctl.signal});
      let data = null;
      try { data = await r.json(); } catch {}
      return {ok: r.ok, status: r.status, data};
    })();
    return Promise.race([asked, gaveUp]).catch(e => {
      const timeout = ctl.signal.aborted;
      throw Object.assign(new Error(timeout ? 'the hub did not answer in time' : 'the hub could not be reached'),
                          {status: 0, timeout, network: !timeout, cause: e});
    }).finally(() => { clearTimeout(timer); inFlight.delete(it); });
  }
  const recheck = () => { for (const it of [...inFlight]) if (Date.now() - it.at >= it.ms) it.stop(); };
  document.addEventListener('visibilitychange', recheck);
  window.addEventListener('pageshow', recheck);
  window.addEventListener('focus', recheck);
  window.sendJson = sendJson;
  document.addEventListener('DOMContentLoaded', line);
  // The service worker that shows the start-up page while the hub starts or
  // is not running (static/sw.js, served at /sw.js for the whole site). A
  // browser without one (plain http over the tailnet) has the hub's own
  // start-up page while it starts, and the browser's error page while it is down.
  try {
    if ('serviceWorker' in navigator && window.isSecureContext && window.top === window) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
      });
    }
  } catch {}
})();
