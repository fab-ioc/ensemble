/* A frame that never loads its address is given it once more (ED-399, GitHub
   issue 65).

   Seen on a copy: a thread or chat panel beside the chat stayed blank. Its
   iframe had the right address, yet the page never asked the hub for it, and
   it was still blank 30 s later. It happened on the first load with a fresh
   browser profile, while the service worker installed and took control of the
   page (so after any hub update too), about 1 run in 15 with the CPU heavily
   throttled. Setting the same address again loaded it within half a second.

   So every iframe of this document that shows a page of this hub is watched,
   however its address was set (`.src =`, setAttribute, an innerHTML template,
   a frame put in or moved between documents): a MutationObserver sees each.
   A new panel needs nothing to be guarded. When WAIT_MS after its address was
   set the frame still has the document it had then (the empty first one, or
   the page before) and that document does not show the address, the address
   is set again: once per address set, never more.

   Never touched: a frame where a page came (even one still loading or slow
   to paint, even one that changed its own address since), a frame whose
   document cannot be read (another origin, an error page, a sandbox), a
   frame given another address or taken out meanwhile, srcdoc and lazy
   frames. A frame that never got its page holds no draft, so none is lost.

   window.frameGuard: watch(doc) adds a document (a pop-out window's; one an
   iframe moves into is added by itself); retries lists the frames given
   their address again. window.FRAME_GUARD_MS (a test sets it) changes the
   wait. */
(() => {
  'use strict';
  if (window.frameGuard) return;
  const waitMs = () => (typeof window.FRAME_GUARD_MS === 'number' ? window.FRAME_GUARD_MS : 8000);
  // iframe -> {url, doc0, owner, timer, done}: the address set last, the
  // document the frame had then, and whether it was judged (loaded, or given
  // its address again).
  const state = new WeakMap();
  // iframe -> the src this file set itself (its own mutation is not a new set).
  const own = new WeakMap();
  const watched = new WeakSet();
  const retries = [];
  const noHash = u => String(u).split('#')[0];
  const isFrame = n => n && n.nodeType === 1 && n.localName === 'iframe';
  const docOf = f => { try { return f.contentDocument || null; } catch (e) { return null; } };
  const urlOf = d => { try { return d.URL; } catch (e) { return ''; } };

  // The hub page the frame is to show, or '' when it is not one of ours.
  function target(f) {
    const raw = f.getAttribute('src');
    if (!raw || f.hasAttribute('srcdoc') || f.getAttribute('loading') === 'lazy') return '';
    let u;
    try { u = new URL(raw, f.ownerDocument.baseURI); } catch (e) { return ''; }
    if (u.origin !== location.origin || !/^https?:$/.test(u.protocol)) return '';
    return u.href;
  }

  // Still without its page: the document it had when the address was set,
  // or an empty one (it was put in again), and not showing that address.
  function stuck(f, e) {
    const d = docOf(f);
    if (!d) return false;
    const shown = urlOf(d);
    if (!shown || noHash(shown) === noHash(e.url)) return false;
    return d === e.doc0 || shown === 'about:blank';
  }

  function arm(f) {
    const old = state.get(f);
    if (old) clearTimeout(old.timer);
    const url = target(f);
    if (!url) { state.delete(f); return; }
    const e = { url, doc0: docOf(f), owner: f.ownerDocument, timer: 0, done: false };
    state.set(f, e);
    e.timer = setTimeout(() => check(f, e), waitMs());
  }

  function check(f, e) {
    if (state.get(f) !== e || e.done) return;
    e.done = true;
    if (!f.isConnected || target(f) !== e.url || !stuck(f, e)) return;
    const src = f.getAttribute('src');
    const d = docOf(f);
    retries.push({ url: e.url, shown: d ? urlOf(d) : '', at: Date.now() });
    try { console.info('[frame-guard] the frame did not load ' + src + ' in ' + waitMs() + ' ms: its address is set again'); } catch (x) {}
    own.set(f, src);
    f.setAttribute('src', src);
  }

  function srcSet(f) {
    if (own.has(f) && own.get(f) === f.getAttribute('src')) { own.delete(f); return; }
    own.delete(f);
    arm(f);
  }

  // Put in (or moved). A frame that kept its document (moved with
  // moveBefore) keeps its watch; one that got a new, empty document (taken
  // out and put back, or moved into another window) loads its address again,
  // and is watched again. One showing a page already is left alone.
  function inserted(f) {
    if (f.ownerDocument !== document && !watched.has(f.ownerDocument)) { watch(f.ownerDocument); return; }
    const url = target(f);
    if (!url) return;
    const e = state.get(f), d = docOf(f);
    const fresh = !!d && urlOf(d) === 'about:blank';
    if (e && e.url === url && e.owner === f.ownerDocument && !(fresh && d !== e.doc0)) return;
    if (!e && !fresh) return;
    arm(f);
  }

  const framesIn = n => (n.nodeType !== 1 ? [] : isFrame(n) ? [n] : n.getElementsByTagName ? [...n.getElementsByTagName('iframe')] : []);

  function onMutations(recs) {
    const left = [];
    for (const r of recs) {
      if (r.type === 'attributes') {
        if (isFrame(r.target) && r.target.isConnected) srcSet(r.target);
        continue;
      }
      for (const n of r.addedNodes) for (const f of framesIn(n)) inserted(f);
      for (const n of r.removedNodes) left.push(...framesIn(n));
    }
    // A frame that left this document for another one (a panel popped out
    // into its own window): that document is watched too, and the frame with it.
    for (const f of left) if (f.isConnected && f.ownerDocument !== document) inserted(f);
  }

  function watch(doc) {
    if (!doc || watched.has(doc)) return;
    watched.add(doc);
    const mo = new MutationObserver(onMutations);
    mo.observe(doc, { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });
    try { for (const f of doc.getElementsByTagName('iframe')) inserted(f); } catch (e) {}
  }

  window.frameGuard = { watch, retries, waitMs };
  watch(document);
})();
