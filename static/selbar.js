// ---- The selection bar: 💬 Comment · Copy -----------------------------------
// session.html (a chat's balloons), fileview.html (a file) and index.html (a
// diff's lines) offer the same bar over a text selection, with this one code:
//
// * it follows the selection itself (selectionchange), however it was made: a
//   drag ending anywhere, a double or triple click, Shift+arrows, a phone's
//   handles. While a mouse button is held it waits for the release, so a drag
//   does not flash it;
// * the page says what the selection is (`resolve`): null when it is none of
//   this surface's business, else where it is and what a comment on it would
//   be (`comment: null` for Copy only: a selection across two balloons);
// * Comment and Copy read the selection when clicked, so a selection changed
//   by the keyboard after the bar showed comments on what is selected now;
// * the bar stays on screen: under the selection, above it when there is no
//   room, kept inside the window, and it follows a scroll (hidden while the
//   selection is scrolled out of view);
// * Copy writes the selected words as plain text (navigator.clipboard, or
//   execCommand over plain http), says "Copied" and keeps the selection.
//
// Its listeners go on `document`: index.html copies those to a panel popped out
// into a window of its own, so each handler works on the document its event
// came from. CSS stays in the pages (.sel-bar, .sel-cmt, .sel-copy).
window.SelBar = (function () {
  const COARSE = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  // A phone's selection handles hang under the words: the bar clears them.
  const GAP = COARSE ? 30 : 8, EDGE = 8;

  const docOf = e => {
    const t = e && e.target;
    if (!t) return document;
    if (t.nodeType === 9) return t;
    return t.ownerDocument || (t.document) || document;
  };

  // The visible part of a range: its line boxes cut to the window and to every
  // element around it that scrolls (the chat, a diff's pane), so words scrolled
  // out of a pane do not count. Null when none of it is in view.
  function visibleRect(range, win) {
    let L = 0, T = 0, R = win.innerWidth, B = win.innerHeight;
    let el = range.commonAncestorContainer;
    if (el && el.nodeType !== 1) el = el.parentElement;
    for (; el && el.nodeType === 1; el = el.parentElement) {
      const cs = win.getComputedStyle(el);
      if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
      const c = el.getBoundingClientRect();
      L = Math.max(L, c.left); T = Math.max(T, c.top); R = Math.min(R, c.right); B = Math.min(B, c.bottom);
    }
    let out = null;
    for (const r of range.getClientRects()) {
      if (!r.width && !r.height) continue;
      const l = Math.max(L, r.left), t = Math.max(T, r.top), rr = Math.min(R, r.right), b = Math.min(B, r.bottom);
      if (rr < l || b < t) continue;
      out = out ? { left: Math.min(out.left, l), top: Math.min(out.top, t), right: Math.max(out.right, rr), bottom: Math.max(out.bottom, b) }
                : { left: l, top: t, right: rr, bottom: b };
    }
    return out;
  }

  // Under the selection; above it when there is no room below; else at the
  // window's foot. Always inside the window, EDGE from its sides.
  function place(bar, rect, win) {
    const w = bar.offsetWidth, h = bar.offsetHeight, vw = win.innerWidth, vh = win.innerHeight;
    let top = rect.bottom + GAP, where = 'below';
    if (top + h > vh - EDGE) {
      if (rect.top - GAP - h >= EDGE) { top = rect.top - GAP - h; where = 'above'; }
      else { top = vh - EDGE - h; where = 'inside'; }
    }
    const left = Math.max(EDGE, Math.min(rect.left, vw - EDGE - w));
    bar.style.top = Math.round(Math.max(EDGE, top)) + 'px';
    bar.style.left = Math.round(left) + 'px';
    bar.dataset.placement = where;
  }

  // Plain text to the clipboard. navigator.clipboard needs a secure page (https,
  // localhost); over the tailnet's plain http the textarea way is the one there
  // is. Either way the selection is put back as it was.
  async function copyText(text, doc) {
    const win = doc.defaultView || window;
    try {
      if (win.navigator.clipboard && win.isSecureContext) { await win.navigator.clipboard.writeText(text); return true; }
    } catch (e) {}
    const sel = doc.getSelection();
    const kept = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
    const active = doc.activeElement;
    const ta = doc.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;font-size:16px;';
    doc.body.appendChild(ta);
    let ok = false;
    try { ta.select(); ta.setSelectionRange(0, text.length); ok = doc.execCommand('copy'); } catch (e) {}
    ta.remove();
    try { if (active && active.focus) active.focus({ preventScroll: true }); } catch (e) {}
    if (kept && sel) { try { sel.removeAllRanges(); sel.addRange(kept); } catch (e) {} }
    return ok;
  }

  // opts.resolve(sel, doc) → null | { range, comment }: `range` is what
  //   the bar stands by (the part a comment would take, or the whole selection),
  //   `comment` the page's data for a comment on it, or null for Copy only.
  // opts.comment(data, sel, doc): Comment was clicked.
  // opts.copyText(sel, info): the words Copy takes (default: the selection's).
  function mount(opts) {
    let bar = null, cur = null, down = false, timer = 0, raf = 0, copiedT = 0;

    function hide() {
      clearTimeout(copiedT);
      if (bar) { bar.remove(); bar = null; }
      cur = null;
    }
    // What the selection in `doc` is now, for this page.
    function read(doc) {
      const sel = doc.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount || !sel.toString().trim()) return null;
      const a = doc.activeElement;
      if (a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT' || a.isContentEditable)) return null;
      const info = opts.resolve(sel, doc);
      return info ? { sel, doc, info, text: sel.toString(), range: sel.getRangeAt(0).cloneRange() } : null;
    }
    function build(doc) {
      const b = doc.createElement('div');
      b.className = 'sel-bar';
      b.setAttribute('role', 'toolbar');
      b.setAttribute('aria-label', 'Selected text');
      b.innerHTML = '<button type="button" class="sel-cmt">💬 Comment</button>'
        + '<span class="sel-sep" aria-hidden="true">·</span>'
        + '<button type="button" class="sel-copy">Copy</button>'
        + '<span class="sel-said" role="status"></span>';
      // A press on the bar must not take the selection away.
      b.addEventListener('mousedown', e => e.preventDefault());
      b.querySelector('.sel-cmt').addEventListener('click', () => {
        const now = (cur && read(cur.doc)) || cur;         // as selected now; else as last seen (a tap may have cleared it)
        if (!now || !now.info.comment) return;
        const { info, sel, doc: d } = now;
        hide();
        opts.comment(info.comment, sel, d);
      });
      b.querySelector('.sel-copy').addEventListener('click', async () => {
        const now = (cur && read(cur.doc)) || cur;
        if (!now) return;
        const d = now.doc, sel = d.getSelection();
        // A tap on a phone can clear the selection before the click: put it back.
        if (sel && sel.isCollapsed && now.range) { try { sel.removeAllRanges(); sel.addRange(now.range); } catch (e) {} }
        const text = opts.copyText ? opts.copyText(sel, now.info) : now.text;
        const ok = await copyText(text, d);
        if (!bar) return;
        const btn = bar.querySelector('.sel-copy');
        btn.textContent = ok ? '✓ Copied' : 'Copy failed';
        bar.querySelector('.sel-said').textContent = ok ? 'Copied' : 'Not copied';
        clearTimeout(copiedT);
        copiedT = setTimeout(() => { if (bar) { btn.textContent = 'Copy'; bar.querySelector('.sel-said').textContent = ''; } }, 1500);
      });
      doc.body.appendChild(b);
      return b;
    }
    function update(doc) {
      const now = read(doc);
      if (!now) { if (!bar || bar.ownerDocument === doc) hide(); return; }
      if (bar && bar.ownerDocument !== doc) hide();
      if (!bar) bar = build(doc);
      const same = !!cur && cur.text === now.text;
      cur = now;
      bar.querySelector('.sel-cmt').hidden = !now.info.comment;
      bar.querySelector('.sel-sep').hidden = !now.info.comment;
      bar.classList.toggle('copy-only', !now.info.comment);
      if (!same) { clearTimeout(copiedT); bar.querySelector('.sel-copy').textContent = 'Copy'; }
      position();
    }
    function position() {
      if (!bar || !cur) return;
      const win = cur.doc.defaultView || window;
      let rect = null;
      try { rect = visibleRect(cur.info.range, win); } catch (e) {}
      bar.hidden = !rect;
      if (rect) place(bar, rect, win);
    }
    const soon = (doc, ms) => { clearTimeout(timer); timer = setTimeout(() => update(doc), ms); };

    document.addEventListener('selectionchange', e => {
      if (down) return;                                   // a drag: wait for the release
      soon(docOf(e), COARSE ? 300 : 200);
    });
    document.addEventListener('mousedown', e => {
      if (bar && bar.contains(e.target)) return;
      if (e.button === 0) down = true;
      if (bar) hide();
    });
    // Anywhere in the page, not only on the surface: a drag may end outside it.
    document.addEventListener('mouseup', e => { down = false; soon(docOf(e), 10); });
    // A release outside the window never reaches the page: the next move says so.
    document.addEventListener('mousemove', e => { if (down && !(e.buttons & 1)) { down = false; soon(docOf(e), 10); } });
    document.addEventListener('touchend', e => soon(docOf(e), 300));
    document.addEventListener('scroll', () => {
      if (!bar || raf) return;
      raf = requestAnimationFrame(() => { raf = 0; position(); });
    }, true);
    window.addEventListener('resize', () => position());
    if (window.visualViewport) window.visualViewport.addEventListener('resize', () => position());

    return {
      hide,
      shown: () => !!bar && bar.isConnected,
      // After the page redrew what the selection is in: read it again.
      refresh: () => { if (bar) update(bar.ownerDocument); },
    };
  }

  // The offset of (node, offset) in root's text, counted as root.textContent
  // counts it, whether the boundary is in a text node or between elements.
  function textOffset(root, node, offset) {
    const d = root.ownerDocument, r = d.createRange();
    r.selectNodeContents(root);
    try { r.setEnd(node, offset); } catch (e) { return -1; }
    return r.toString().length;
  }
  // The part of `range` inside `el`: [start, end] offsets in el's text, or
  // null when there is none.
  function clampTo(el, range) {
    const d = el.ownerDocument;
    if (!range.intersectsNode(el)) return null;
    const whole = d.createRange(); whole.selectNodeContents(el);
    const len = whole.toString().length;
    const start = range.compareBoundaryPoints(Range.START_TO_START, whole) <= 0 ? 0 : textOffset(el, range.startContainer, range.startOffset);
    const end = range.compareBoundaryPoints(Range.END_TO_END, whole) >= 0 ? len : textOffset(el, range.endContainer, range.endOffset);
    if (start < 0 || end < 0 || end <= start) return null;
    return { start, end, len };
  }
  // A Range over [start, end) of el's text.
  function rangeIn(el, start, end) {
    const d = el.ownerDocument, w = d.createTreeWalker(el, NodeFilter.SHOW_TEXT, null), r = d.createRange();
    let off = 0, n, s = false;
    r.selectNodeContents(el);
    while ((n = w.nextNode())) {
      const len = n.nodeValue.length;
      if (!s && start <= off + len) { r.setStart(n, start - off); s = true; }
      if (end <= off + len) { r.setEnd(n, end - off); break; }
      off += len;
    }
    return r;
  }

  return { mount, copyText, clampTo, rangeIn, textOffset, visibleRect, place };
})();
