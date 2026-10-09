// File links of the Ensemble pages: the one place a link to a file is made,
// and where the hub is asked whether the files a page links to are there.
// index.html, session.html and fileview.html load it; every /fileview?path=
// link they draw comes from FileLinks.href, so the hub's one resolver
// (file_refs.py: absolute path, the room's folders, the project home, its
// Documents, its code folder, the task folders of the project, then the file
// by its name) answers every one of them the same way.
//
// FileLinks.watch() (once per page) marks every a.file-link the page draws,
// whatever draws it (a balloon, a card, a comment, a file's preview): a
// MutationObserver runs before the browser paints, so a redrawn link that was
// missing is drawn dimmed straight away. FileLinks.scan(root) is the same for
// one subtree, by hand. Each link: what the
// hub said before is painted at once (no flicker on a redraw), what it has
// not been asked yet goes into one batched GET /api/files/check, a single
// request in flight at a time. A path that names nothing is a quiet chip,
// "not written yet", still a link: the file view says what is (not) there.
// FileLinks.recheck(), on the page's own refresh, asks again about the ones
// that were missing (at most every RECHECK_MS), so a file a task writes later
// turns into a normal link by itself. A recheck asks with "tail": false: the
// hub only looks where the path is written and walks no folder for a file by
// its name, so a page left open costs a few stat calls, not a walk of every
// folder each RECHECK_MS. A hub older than /api/files/check
// answers 404: then nothing is marked and every link stays as it was.
const FileLinks = (() => {
  // ?path=&line=&room=&roomName=&cwd=, in that order (tests compare hrefs).
  function href(path, o) {
    o = o || {};
    const q = (k, v) => (v ? '&' + k + '=' + encodeURIComponent(v) : '');
    return '/fileview?path=' + encodeURIComponent(String(path || '').trim()) + (o.line ? '&line=' + o.line : '')
      + q('room', o.room) + q('roomName', o.roomName) + q('cwd', o.cwd);
  }
  const SEP = '\u0001', BATCH = 80, RECHECK_MS = 10000;
  const MISSING_TITLE = 'This file does not exist yet';
  const state = new Map();   // path SEP room SEP cwd -> true (there) | false (not there)
  const queue = new Map();   // the same keys, waiting for the next request -> true: a first ask
                             // (the hub may search by name), false: a recheck (it does not)
  const asking = new Set();  // keys in the request on its way
  const failed = new Map();  // keys a request did not answer, as queued: asked again by the next recheck
  let busy = false, off = false, lastRecheck = 0;
  function keyOf(a) {
    const h = a.getAttribute('href') || '';
    if (!h.startsWith('/fileview?')) return '';
    const q = new URLSearchParams(h.slice('/fileview?'.length));
    const p = q.get('path') || '';
    return p ? [p, q.get('room') || '', q.get('cwd') || ''].join(SEP) : '';
  }
  function paint(a, there) {
    const was = a.classList.contains('file-missing');
    if (there === false && !was) {
      a.classList.add('file-missing');
      a.dataset.titleWas = a.getAttribute('title') || '';
      a.title = MISSING_TITLE;
    } else if (there !== false && was) {
      a.classList.remove('file-missing');
      if (a.dataset.titleWas) a.title = a.dataset.titleWas; else a.removeAttribute('title');
      delete a.dataset.titleWas;
    }
  }
  function links(root) {
    return root && root.querySelectorAll ? root.querySelectorAll('a.file-link') : [];
  }
  // One link: painted from what the hub said, or queued to be asked. true when queued.
  function mark(a) {
    const k = keyOf(a);
    if (!k) return false;
    if (state.has(k)) { paint(a, state.get(k)); return false; }
    if (asking.has(k)) return false;
    queue.set(k, true);
    return true;
  }
  function scan(root) {
    if (off) return;
    for (const a of links(root)) mark(a);
    flush();
  }
  function recheck() {
    if (off || busy) return;
    const now = Date.now();
    if (now - lastRecheck < RECHECK_MS) return;
    lastRecheck = now;
    for (const [k, there] of state) if (there === false && !queue.has(k)) queue.set(k, false);
    for (const [k, tail] of failed) if (!queue.has(k) || tail) queue.set(k, tail);
    failed.clear();
    flush();
  }
  async function flush() {
    if (busy || off || !queue.size || typeof fetch !== 'function') return;
    busy = true;
    // one kind per request: first asks, or rechecks, whichever is first in the queue
    const tail = queue.values().next().value;
    const keys = [...queue].filter(([, t]) => t === tail).slice(0, BATCH).map(([k]) => k);
    keys.forEach(k => { queue.delete(k); asking.add(k); });
    const ctx = [], at = new Map();
    const items = keys.map(k => {
      const [p, room, cwd] = k.split(SEP), c = room + SEP + cwd;
      if (!at.has(c)) at.set(c, ctx.push([room, cwd]) - 1);
      return [p, at.get(c)];
    });
    let again = false;
    try {
      const q = tail ? { ctx, items } : { ctx, items, tail: false };
      const r = await fetch('/api/files/check?q=' + encodeURIComponent(JSON.stringify(q)), { cache: 'no-store' });
      if (r.status === 404) { off = true; return; }
      if (!r.ok) { keys.forEach(k => failed.set(k, tail)); return; }
      const there = ((await r.json()) || {}).there || [];
      const changed = new Set();
      keys.forEach((k, i) => {
        if (typeof there[i] !== 'boolean') return;
        if (state.get(k) !== there[i]) changed.add(k);
        state.set(k, there[i]);
      });
      if (changed.size && typeof document !== 'undefined') {
        for (const a of links(document)) { const k = keyOf(a); if (changed.has(k)) paint(a, state.get(k)); }
      }
      again = queue.size > 0;
    } catch (e) {
      keys.forEach(k => failed.set(k, tail));   // the hub is away: asked again by the next recheck
    } finally {
      keys.forEach(k => asking.delete(k));
      busy = false;
    }
    if (again) flush();
  }
  let watching = false;
  function watch() {
    if (watching || typeof MutationObserver !== 'function' || typeof document === 'undefined') return;
    if (!document.body) { document.addEventListener('DOMContentLoaded', watch, { once: true }); return; }
    watching = true;
    new MutationObserver(recs => {
      if (off) return;
      for (const r of recs) for (const n of r.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.matches('a.file-link')) mark(n);
        if (n.firstElementChild) for (const a of links(n)) mark(a);
      }
      if (queue.size) flush();
    }).observe(document.body, { childList: true, subtree: true });
    scan(document.body);
  }
  // What the hub said about one link, for a page that draws links itself:
  // true, false, or undefined when it has not been asked yet.
  const known = a => state.get(keyOf(a));
  return { href, watch, scan, recheck, known, MISSING_TITLE, _state: state };
})();
