/* Ensemble's service worker (ED-227): the app opens at once, whatever the hub
   is doing. Served by the hub at /sw.js so its scope is the whole site; the hub
   puts the start-up page's stamp below, so a changed page installs again.

   It answers only page loads of the board, a chat and a file view. Each goes
   to the hub as before. When the hub cannot be reached at all the cached
   start-up page shows saying so ("down"). When the page has not come in
   NAV_WAIT_MS, /api/health is asked: the start-up page shows only if the hub
   says it is starting, or does not answer that either ("slow"); a hub that is
   merely busy (warming, a large page, a file view) is waited for as without
   this file. The start-up page polls the hub and reloads once it is ready.
   Nothing else is cached or intercepted: every API call, script and picture
   goes to the hub as if this file did not exist. */
const STAMP = '__STARTUP_STAMP__';
const CACHE = 'ensemble-startup-' + STAMP;
const PAGE = '/startup';
const FILES = [PAGE, '/static/icons/icon-192.png'];
const PAGES = new Set(['/', '/index.html', '/session', '/fileview']);
const NAV_WAIT_MS = 700;
const HEALTH_WAIT_MS = 1500;

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

// What the hub says of itself: 'starting', 'up' (warming, ready, or a hub
// from before /api/health), or 'silent' when it does not answer in time.
async function hubState() {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), HEALTH_WAIT_MS);
  try {
    const res = await fetch('/api/health', { cache: 'no-store', signal: ctl.signal });
    if (res.status === 404 || res.status === 401) return 'up';
    if (!res.ok) return 'silent';
    return (await res.json()).phase === 'starting' ? 'starting' : 'up';
  } catch (e) {
    return 'silent';
  } finally {
    clearTimeout(timer);
  }
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
    let timer;
    const slow = new Promise(resolve => { timer = setTimeout(() => resolve('slow'), NAV_WAIT_MS); });
    try {
      const first = await Promise.race([network, slow]);
      if (first !== 'slow') return first;
      // The page is slow: ask the hub why, while the page keeps coming.
      const state = await Promise.race([hubState(), network.then(() => 'page')]);
      if (state === 'page' || state === 'up') return await network;
      return startupPage(state === 'starting' ? 'starting' : 'slow');
    } catch (e) {
      return startupPage('down');
    } finally {
      clearTimeout(timer);
    }
  })());
});

/* Notifications (ED-359): the hub's pushes (notify.py) shown on this device,
   and the card, chat or Inbox they name opened when one is tapped. */

// Only a path on this hub is opened: a notification never sends you elsewhere.
// The parsed address must still be this origin: "/\evil.example/x" starts
// with one slash, yet a URL parser reads it as another host.
function hubUrl(path) {
  const home = new URL('/', self.location.origin).href;
  if (typeof path !== 'string' || !path.startsWith('/')) return home;
  try {
    const u = new URL(path, self.location.origin);
    return u.origin === self.location.origin ? u.href : home;
  } catch (e) { return home; }
}

self.addEventListener('push', event => {
  let a = {};
  try { a = event.data ? event.data.json() : {}; } catch (e) { a = {}; }
  // Every push shows something: Safari ends a subscription that stays silent.
  event.waitUntil(self.registration.showNotification(a.title || 'Ensemble', {
    body: a.body || '',
    tag: a.tag || undefined,
    renotify: !!a.tag,
    icon: '/static/icons/icon-192.png',
    data: { url: hubUrl(a.path) },
  }));
});

// The dashboard's own pages: a notification opens there (P368).
const DASH = new Set(['/', '/index.html']);
// How long an open dashboard has to say it opened the chat in place.
const OPEN_WAIT_MS = 3000;

// An address of a chat's own page (/session?room=…, what every notification
// sent before P368 holds) as the dashboard's deep link to that chat (#308):
// the dashboard opens it on that message, with its bar, Inbox and list.
function deepUrl(href) {
  let u;
  try { u = new URL(href); } catch (e) { return href; }
  if (u.origin !== self.location.origin || u.pathname !== '/session') return u.href;
  const room = u.searchParams.get('room') || u.searchParams.get('id') || '';
  if (!room) return u.href;
  const out = new URL('/', self.location.origin);
  out.searchParams.set('room', room);
  for (const k of ['msg', 'part', 'thread', 'tmid']) {
    if (u.searchParams.has(k)) out.searchParams.set(k, u.searchParams.get(k));
  }
  return out.href;
}

function pathOf(client) {
  try {
    const u = new URL(client.url);
    return u.origin === self.location.origin ? u.pathname : '';
  } catch (e) { return ''; }
}

// Asks an open dashboard to open the address in place (index.html's
// 'notify-open' listener). Its answer: {ok}, {fallback: path} for a chat it
// does not list, or null (an older page, no answer in time).
function askDashboard(client, url) {
  return new Promise(resolve => {
    const ch = new MessageChannel();
    const timer = setTimeout(() => resolve(null), OPEN_WAIT_MS);
    ch.port1.onmessage = ev => { clearTimeout(timer); resolve(ev.data || null); };
    try { client.postMessage({ ensemble: 'notify-open', url }, [ch.port2]); }
    catch (e) { clearTimeout(timer); resolve(null); }
  });
}

async function openFromNotification(url) {
  const wins = (await self.clients.matchAll({ type: 'window', includeUncontrolled: true }))
    .filter(c => pathOf(c));
  if (DASH.has(new URL(url).pathname)) {
    // An open dashboard, the one in front first: brought forward, then told.
    const dash = wins.filter(c => DASH.has(pathOf(c)))
      .sort((a, b) => (b.focused - a.focused)
        || ((b.visibilityState === 'visible') - (a.visibilityState === 'visible')));
    if (dash.length) {
      let win = dash[0];
      try { win = (await win.focus()) || win; } catch (e) { /* not allowed: told anyway */ }
      const said = await askDashboard(win, url);
      if (said && said.ok) return win;
      if (said && said.fallback) {
        if (self.clients.openWindow) return self.clients.openWindow(hubUrl(said.fallback));
        return win;
      }
      // It did not answer: the dashboard loads the address itself.
      if ('navigate' in win) {
        try {
          const went = await win.navigate(url);
          if (went) return went;
        } catch (e) { /* not controlled by this worker: open a new window */ }
      }
    }
  } else {
    const same = wins.find(c => c.url === url);
    if (same) return same.focus();
  }
  // No dashboard open: a new one, on that chat. Another hub window (a chat's
  // own page, a file view) is never turned into it.
  if (self.clients.openWindow) return self.clients.openWindow(url);
}

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = deepUrl((event.notification.data && event.notification.data.url) || hubUrl('/'));
  event.waitUntil(openFromNotification(url));
});
