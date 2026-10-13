/* Keep an open page and its pending chat writes through a hub restart. */
(() => {
  const raw = window.fetch.bind(window);
  const pending = [];
  let offline = false, timer = 0, delay = 500, probing = false, flushing = false;
  const retryable = new Set(['/api/room/resume', '/api/room/points',
                             '/api/room/ask', '/api/send']);
  const unavailable = res => [502, 503, 504].includes(res.status);

  async function flush() {
    if (flushing) return;
    flushing = true;
    try {
    while (pending.length && !offline) {
      const p = pending[0];
      try {
        const reply = await raw(p.input, p.init);
        if (unavailable(reply)) {
          markOffline();
          break;
        }
        pending.shift();
        p.resolve(reply);
      } catch {
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
      const res = await raw('/api/platform', { cache: 'no-store' });
      if (!res.ok) throw new Error('hub unavailable');
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

  window.fetch = async (input, init) => {
    const path = typeof input === 'string' ? input.split('?')[0] : '';
    const api = path.startsWith('/api/');
    const queued = retryable.has(path) && (init?.method || 'GET').toUpperCase() === 'POST';
    if (queued && offline) return new Promise((resolve, reject) => pending.push({input, init, resolve, reject}));
    try {
      const res = await raw(input, init);
      if (api && unavailable(res)) {
        markOffline();
        if (queued) return new Promise((resolve, reject) => pending.push({input, init, resolve, reject}));
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
      if (queued) return new Promise((resolve, reject) => pending.push({input, init, resolve, reject}));
      throw e;
    }
  };
  window.HubResilience = { get offline() { return offline; }, get queued() { return pending.length; }, probe };
  document.addEventListener('DOMContentLoaded', line);
})();
