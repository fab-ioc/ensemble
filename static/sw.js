/* Ensemble's service worker (ED-227): the app opens at once, whatever the hub
   is doing. Served by the hub at /sw.js so its scope is the whole site; the hub
   puts the start-up page's stamp below, so a changed page installs again.

   It answers only page loads of the board, a chat and a file view. Each goes
   to the hub as before; when the hub has not answered in NAV_WAIT_MS the
   cached start-up page shows instead ("slow"), and when the hub cannot be
   reached at all it shows saying so ("down"). The start-up page polls the hub
   and reloads once it is ready; it first posts {grace} so that reload waits
   for the hub instead of racing it. Nothing else is cached or intercepted:
   every API call, script and picture goes to the hub as if this file did not
   exist. */
const STAMP = '__STARTUP_STAMP__';
const CACHE = 'ensemble-startup-' + STAMP;
const PAGE = '/startup';
const FILES = [PAGE, '/static/icons/icon-192.png'];
const PAGES = new Set(['/', '/index.html', '/session', '/fileview']);
const NAV_WAIT_MS = 700;
let graceUntil = 0;

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(FILES.map(f => new Request(f, { cache: 'no-store' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('ensemble-startup-') && key !== CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('message', event => {
  const ms = Number(event.data && event.data.grace) || 0;
  if (ms > 0) graceUntil = Date.now() + Math.min(ms, 60000);
});

async function startupPage(state) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(PAGE);
  const html = hit ? await hit.text()
    : '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="3"><title>Ensemble</title><p>Ensemble is starting.</p>';
  return new Response(
    html.replace('<meta name="sw-state" content="starting">', '<meta name="sw-state" content="' + state + '">'),
    { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
                              'X-Ensemble-Startup': state } });
}

function unavailable(res) {
  return res.status === 502 || res.status === 503 || res.status === 504;
}

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.mode !== 'navigate') {
    // The start-up page's own picture, from the cache when the hub is down.
    if (FILES.includes(url.pathname) && url.pathname !== PAGE) {
      event.respondWith(fetch(req).catch(async () => (await caches.match(url.pathname)) || Response.error()));
    }
    return;
  }
  if (!PAGES.has(url.pathname)) return;
  event.respondWith((async () => {
    const network = fetch(req).then(res => (unavailable(res) ? Promise.reject(new Error('unavailable')) : res));
    // Keep the network answer from being reported as unhandled when the
    // start-up page wins the race.
    network.catch(() => {});
    if (Date.now() < graceUntil) {
      graceUntil = 0;     // for the one load the start-up page asked for
      try { return await network; } catch (e) { return startupPage('down'); }
    }
    let timer;
    const slow = new Promise(resolve => { timer = setTimeout(() => resolve('slow'), NAV_WAIT_MS); });
    try {
      const first = await Promise.race([network, slow]);
      if (first !== 'slow') return first;
      return startupPage('slow');
    } catch (e) {
      return startupPage('down');
    } finally {
      clearTimeout(timer);
    }
  })());
});
