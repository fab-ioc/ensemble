// A write only the page may make (the read point, Done with this): sent with
// the page's key cookie. The hub draws a new key at every restart, and a page
// left open across one held the old key, so every such write was refused
// until the whole page was reloaded; the read point of a chat never reached
// the hub and its question stayed in Needs you for days (GitHub issue 13,
// point P157). Refused for that, the page loads /ui-key in a hidden frame of
// its own, which hands today's key over, and sends the write once more.
(function () {
  let refreshing = null;
  function pageKeyRefresh() {
    if (refreshing) return refreshing;
    refreshing = new Promise(done => {
      const f = document.createElement('iframe');
      f.hidden = true;
      f.setAttribute('aria-hidden', 'true');
      f.tabIndex = -1;
      const end = () => { clearTimeout(t); f.remove(); refreshing = null; done(); };
      const t = setTimeout(end, 10000);
      f.addEventListener('load', end, { once: true });
      f.src = '/ui-key?t=' + Date.now();
      (document.body || document.documentElement).appendChild(f);
    });
    return refreshing;
  }
  // POST JSON to a page-only route; resolves to the Response. A refusal for
  // an old key ("page_only") fetches today's key once and tries again.
  async function pageWrite(url, body, opts) {
    const send = () => fetch(url, Object.assign({
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    }, opts || {}));
    const r = await send();
    if (r.status !== 403) return r;
    let why = null;
    try { why = await r.clone().json(); } catch (e) {}
    if (!why || why.error !== 'page_only') return r;
    await pageKeyRefresh();
    return send();
  }
  window.pageKeyRefresh = pageKeyRefresh;
  window.pageWrite = pageWrite;
})();
