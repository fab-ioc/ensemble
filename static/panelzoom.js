/* Panel zoom (#423, GitHub issue 69): a file or a diff zoomed on its own,
   while the chat, the bars and the browser keep their size.

   One implementation for the two places that show a file's content: the file
   view (fileview.html: a page, Markdown, code and text, CSV, a picture) and a
   diff in Changes (index.html's drShow). Each page decides what it zooms and
   how; this file holds what they share:

   - the steps (the browser's own: 50 … 300%);
   - the level remembered per kind of file, on this device (localStorage
     'cd-zoom', one key for both pages: they are the same origin);
   - the control, − 100% + in a .seg: the % pressed while not at 100%, a click
     on it back to 100%; − and + disabled at the ends;
   - Ctrl/⌘ + wheel over the content, Ctrl/⌘ + = + − 0 with the focus in it;
   - a pinch with two fingers on a touch screen, on the content only.

   The browser's own zoom is untouched everywhere else. */
(function () {
  'use strict';
  const STEPS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300];
  const KEY = 'cd-zoom';
  const KINDS = ['page', 'md', 'code', 'img', 'diff'];
  const MIN = STEPS[0], MAX = STEPS[STEPS.length - 1];

  function readAll() {
    try { const o = JSON.parse(localStorage.getItem(KEY) || '{}'); return o && typeof o === 'object' ? o : {}; } catch (e) { return {}; }
  }
  function nearest(p) {
    let best = 100;
    for (const s of STEPS) if (Math.abs(s - p) < Math.abs(best - p)) best = s;
    return best;
  }
  function get(kind) {
    const v = +readAll()[kind];
    return v && STEPS.includes(v) ? v : (v ? nearest(v) : 100);
  }
  function set(kind, pct) {
    if (!KINDS.includes(kind)) return;
    const all = readAll();
    if (pct === 100) delete all[kind]; else all[kind] = pct;
    try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (e) {}
  }
  // The next step from pct in direction dir (+1 / -1); a level between steps
  // (a pinch) goes to the next step that way.
  function step(pct, dir) {
    if (dir > 0) { for (const s of STEPS) if (s > pct + 0.5) return s; return MAX; }
    for (let i = STEPS.length - 1; i >= 0; i--) if (STEPS[i] < pct - 0.5) return STEPS[i];
    return MIN;
  }
  // Ctrl/⌘ + = + − 0, as the browser reads them: 'in', 'out', 'reset' or ''.
  function keyOf(ev) {
    if (!(ev.ctrlKey || ev.metaKey) || ev.altKey) return '';
    if (ev.isComposing) return '';
    const k = ev.key, c = ev.code;
    if (k === '=' || k === '+' || c === 'NumpadAdd') return 'in';
    if (k === '-' || k === '_' || k === '−' || c === 'NumpadSubtract') return 'out';
    if ((k === '0' || c === 'Digit0' || c === 'Numpad0') && !ev.shiftKey) return 'reset';
    return '';
  }

  const TIP = 'Zoom this file · Ctrl/⌘ + wheel · click for 100%';
  const TIP_DIFF = 'Zoom this diff · Ctrl/⌘ + wheel · click for 100%';
  // The control's markup: three buttons in a .seg. `cls` is the class the
  // page's own buttons carry (fileview: btn; index: none, .seg button styles).
  function html(opts) {
    const o = opts || {}, cls = o.cls ? ` class="${o.cls}"` : '', tip = o.tip || TIP;
    return `<span class="seg pz" role="group" aria-label="Zoom"${o.hidden ? ' hidden' : ''}>`
      + `<button type="button"${cls} data-pz="out" aria-label="Zoom out" title="Zoom out · Ctrl/⌘ −">−</button>`
      + `<button type="button"${cls} data-pz="reset" aria-pressed="false" title="${tip}">100%</button>`
      + `<button type="button"${cls} data-pz="in" aria-label="Zoom in" title="Zoom in · Ctrl/⌘ +">+</button></span>`;
  }
  // What the control shows for a level.
  function show(seg, pct) {
    if (!seg) return;
    const p = Math.round(pct);
    const r = seg.querySelector('[data-pz="reset"]'), o = seg.querySelector('[data-pz="out"]'), i = seg.querySelector('[data-pz="in"]');
    if (r) {
      r.textContent = p + '%';
      r.setAttribute('aria-pressed', String(p !== 100));
      r.setAttribute('aria-label', `Zoom ${p}%${p !== 100 ? ', click for 100%' : ''}`);
      r.classList.toggle('active', p !== 100);
    }
    if (o) o.disabled = p <= MIN;
    if (i) i.disabled = p >= MAX;
  }
  // The control's clicks: act(what) with 'in', 'out' or 'reset'.
  function wire(seg, act) {
    if (!seg || seg._pz) return;
    seg._pz = true;
    seg.addEventListener('click', e => {
      const b = e.target.closest && e.target.closest('[data-pz]');
      if (!b || b.disabled) return;
      e.preventDefault();
      act(b.dataset.pz);
    });
  }

  // Ctrl/⌘ + wheel on `el` (an element or a document): one step a notch; a
  // trackpad's pinch (Chrome and Safari send it as Ctrl + wheel with small
  // deltas) collects its deltas and steps every so much. onStep(dir, ev).
  // The browser zooms the whole page only when the wheel is not over el.
  function wheel(el, onStep, ok) {
    if (!el || el._pzWheel) return;
    el._pzWheel = true;
    let acc = 0, last = 0;
    el.addEventListener('wheel', ev => {
      if (!(ev.ctrlKey || ev.metaKey)) return;
      if (ok && !ok(ev)) return;
      ev.preventDefault();
      const now = Date.now();
      if (now - last > 400) acc = 0;
      last = now;
      const k = ev.deltaMode === 1 ? 33 : ev.deltaMode === 2 ? 400 : 1;
      acc += ev.deltaY * k;
      // A mouse notch is about 100; a trackpad sends a few at a time.
      if (Math.abs(acc) >= 40) { onStep(acc < 0 ? 1 : -1, ev); acc = 0; }
    }, { passive: false });
  }

  // Two fingers on `el` zoom it alone: the page's own pinch is stopped there
  // (touch-action set by the page's CSS, and preventDefault on a two-finger
  // move), the level follows the fingers (live(pct, cx, cy)) and snaps to the
  // nearest step when they lift (done(pct, cx, cy)). base() is the level at
  // the start. iOS Safari also gets its gesture events stopped.
  function pinch(el, h) {
    if (!el || el._pzPinch) return;
    el._pzPinch = true;
    let d0 = 0, p0 = 100, cur = 100, cx = 0, cy = 0, on = false;
    const dist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    el.addEventListener('touchstart', ev => {
      if (ev.touches.length !== 2) return;
      d0 = dist(ev.touches); p0 = cur = h.base(); on = d0 > 0;
      cx = (ev.touches[0].clientX + ev.touches[1].clientX) / 2;
      cy = (ev.touches[0].clientY + ev.touches[1].clientY) / 2;
    }, { passive: true });
    el.addEventListener('touchmove', ev => {
      if (!on || ev.touches.length !== 2) return;
      ev.preventDefault();
      const p = Math.max(MIN, Math.min(MAX, p0 * dist(ev.touches) / d0));
      if (Math.abs(p - cur) < 1) return;
      cur = p;
      h.live(p, cx, cy);
    }, { passive: false });
    const end = ev => {
      if (!on || ev.touches.length >= 2) return;
      on = false;
      h.done(nearest(cur), cx, cy);
    };
    el.addEventListener('touchend', end, { passive: true });
    el.addEventListener('touchcancel', end, { passive: true });
    // Safari's own pinch events: stopped on the content, so the page keeps its size.
    const stop = e => { e.preventDefault(); };
    el.addEventListener('gesturestart', stop, { passive: false });
    el.addEventListener('gesturechange', stop, { passive: false });
  }

  // Keep what is under (x, y) where it is while the zoom changes: `apply()`
  // changes the zoom; the element at the point (in `doc`, viewport
  // coordinates) is measured before and after, and `scroller` scrolls by the
  // difference. `unit` is how many viewport pixels one scrollTop unit is
  // (a scroller that is itself zoomed counts in its own units). `xs`, when
  // given, is what scrolls sideways (a picture's pane).
  function keep(doc, scroller, x, y, apply, unit, xs) {
    let el = null;
    try { el = doc.elementFromPoint(x, y); } catch (e) {}
    const valid = el && scroller && el !== doc.documentElement && el !== doc.body && (scroller.contains ? scroller.contains(el) || scroller === doc.scrollingElement : true);
    let fy = 0, fx = 0;
    if (valid) {
      const r = el.getBoundingClientRect();
      fy = r.height ? (y - r.top) / r.height : 0;
      fx = r.width ? (x - r.left) / r.width : 0;
    }
    apply();
    if (!valid || !el.isConnected) return;
    for (let k = 0; k < 2; k++) {
      const r = el.getBoundingClientRect(), u = unit ? unit() : 1;
      const dy = r.top + fy * r.height - y, dx = r.left + fx * r.width - x;
      if (Math.abs(dy) < 1 && Math.abs(dx) < 1) break;
      scroller.scrollTop += dy / u;
      const h = xs || scroller;
      if (h.scrollWidth > h.clientWidth) h.scrollLeft += dx / u;
    }
  }

  window.PanelZoom = { STEPS, MIN, MAX, KINDS, TIP, TIP_DIFF, get, set, step, nearest, keyOf, html, show, wire, wheel, pinch, keep };
})();
