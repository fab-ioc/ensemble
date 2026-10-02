// Draw a panel frame from its DOM, without the browser's sharing prompt (v0.12.0): a copy of the frame with every
// element's computed style written into it, in an SVG <foreignObject>, painted on a canvas at devicePixelRatio.
//
// What it keeps: computed styles (theme colours, :hover/:focus as they are now), ::before/::after/::placeholder, the web
// fonts in use (embedded as data: URLs, since an SVG image loads nothing), form state (values, checks, the <select>
// choice, <textarea> text), the scroll position of every scrolled area, <img> (inlined), <canvas> and <video> (their
// current pixels), inline SVG (and <use> of a symbol on the page), CSS url() images, and same-origin iframes (drawn
// from their documents, recursively). What it cannot: a cross-origin iframe, a tainted canvas or an image that cannot be
// read. Those throw NeedsCapture (the caller falls back to Region Capture) unless `blank` is set: then a cross-origin
// iframe's area is filled with `--dk-bg` and an unreadable image or canvas is left empty. Not drawn either: shadow DOM
// content, closed <details> internals beyond what is shown, and content outside the frame drawn over it.

const XHTML = 'http://www.w3.org/1999/xhtml';
const SVGNS = 'http://www.w3.org/2000/svg';
const XLINK = 'http://www.w3.org/1999/xlink';
const SKIP = new Set(['script', 'style', 'link', 'template', 'noscript', 'meta', 'title', 'head', 'base']);
// Never inlined: they do nothing in an image, or are set for the whole copy.
const DROP = new Set(['cursor', 'pointer-events', 'user-select', '-webkit-user-select', 'will-change', 'animation',
  'animation-name', 'animation-duration', 'animation-delay', 'animation-play-state', 'transition', 'transition-property',
  'transition-duration', 'transition-delay', 'caret-color', 'view-transition-name']);
const ACCENTED = new Set(['checkbox', 'radio', 'range']);
// A fall-back to Region Capture needs the click's activation, which Chrome keeps ~5 s: when reading the images and
// fonts takes longer than this, the drawing gives up while it lasts.
const SETTLE_MS = 3000;
// A font-weight as a FontFace and an @font-face rule give it ('normal', 'bold', '400', '100 900'), in numbers.
const weightOf = (w) => (w || 'normal').trim().split(/\s+/).map((x) => ({ normal: '400', bold: '700' })[x] || x).join(' ');

export class NeedsCapture extends Error {
  constructor(message) { super(message); this.name = 'NeedsCapture'; }
}

// A 2D canvas is what paints the drawing (jsdom, for one, has none).
export const canDraw = (view) => !!(view && view.XMLSerializer && view.Image && view.HTMLCanvasElement
  && view.CanvasRenderingContext2D && view.HTMLCanvasElement.prototype.toBlob && view.document && view.document.implementation);

// Whether an element with computed style `s` is the containing block of an absolutely placed (or, `fixed`, a fixed)
// descendant: positioned (not for fixed), transformed, filtered, contained or about to be.
const holds = (s, fixed) => (!fixed && s.position !== 'static')
  || ['transform', 'translate', 'rotate', 'scale', 'perspective', 'filter', 'backdropFilter'].some((p) => s[p] && s[p] !== 'none')
  || /paint|layout|strict|content/.test(s.contain) || /transform|perspective|filter/.test(s.willChange);
const visible = (el) => el.getClientRects().length > 0 && el.ownerDocument.defaultView.getComputedStyle(el).visibility !== 'hidden';
function frameDoc(iframe) {
  try { const d = iframe.contentDocument; return d && d.documentElement ? d : null; } catch { return null; }
}
function canvasData(c) {
  try { return c.width && c.height ? c.toDataURL('image/png') : ''; } catch { return null; }
}

/**
 * Throws NeedsCapture when the frame shows something drawing cannot read: a visible cross-origin iframe or a tainted
 * canvas (also inside same-origin iframes). Synchronous, so a fall-back to Region Capture still has the click.
 */
export function preflight(el) {
  for (const f of el.querySelectorAll('iframe, frame')) {
    if (!visible(f)) continue;
    const d = frameDoc(f);
    if (!d) throw new NeedsCapture('The panel shows another site\'s page.');
    if (d.body) preflight(d.body);
  }
  for (const c of el.querySelectorAll('canvas')) if (visible(c) && canvasData(c) === null) throw new NeedsCapture('A canvas cannot be read.');
}

/** Renders `el` (its border box, as on screen) to a PNG Blob at the window's devicePixelRatio. */
export async function drawPanel(el, { signal, blank = false } = {}) {
  const doc = el.ownerDocument;
  const view = doc.defaultView;
  if (!canDraw(view)) throw new Error('Drawing the panel is not supported by this browser.');
  if (!blank) preflight(el);
  const rect = el.getBoundingClientRect();
  if (!el.isConnected || !rect.width || !rect.height) throw new Error('The panel is no longer visible.');
  const dpr = view.devicePixelRatio || 1;
  const copier = new Copier(doc, blank);
  let svg;
  try {
    const root = copier.copy(el, null);
    // The frame at the top left, its size, whatever placed it on the page.
    root.style.setProperty('position', 'relative');
    for (const p of [...root.style]) if (/^(inset|margin)(-|$)/.test(p)) root.style.removeProperty(p);
    for (const p of ['left', 'top', 'right', 'bottom', 'translate', 'transform', 'rotate', 'scale']) root.style.removeProperty(p);
    root.style.setProperty('margin', '0');
    root.style.setProperty('width', `${rect.width}px`); root.style.setProperty('height', `${rect.height}px`);
    root.style.setProperty('box-sizing', 'border-box');
    let timer;
    const late = blank ? null : new Promise((_, reject) => {
      timer = view.setTimeout(() => reject(new NeedsCapture('Reading the panel\'s images and fonts took too long.')), SETTLE_MS);
    });
    const settling = copier.settle(signal);
    settling.catch(() => {}); // given up on (late): its own failure no longer matters
    try { await (late ? Promise.race([settling, late]) : settling); } finally { view.clearTimeout(timer); }
    signal?.throwIfAborted();
    svg = copier.svg(root, rect.width, rect.height, dpr);
  } finally { copier.done(); }
  const img = new view.Image();
  img.decoding = 'sync';
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  try { await img.decode(); } catch { throw new Error('The panel could not be drawn.'); }
  signal?.throwIfAborted();
  const canvas = doc.createElement('canvas');
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not encode the screenshot.'))), 'image/png');
    } catch (error) { reject(new NeedsCapture(error?.message || 'The drawing could not be read.')); }
  });
}

class Copier {
  constructor(doc, blank) {
    this.doc = doc;
    this.blank = blank;
    this.out = doc.implementation.createHTMLDocument(''); // no custom elements upgrade here, nothing runs
    this.tasks = [];
    this.rules = [];
    this.families = new Set();
    this.docs = new Set([doc]);
    this.uses = new Map(); // a <use>'s target, by id: copied once into the drawing
    this.n = 0;
    this.source = new Map(); // each copied element's original
    this.urls = new Map();
    // Each tag's style with no author CSS: a property equal to it (and to the parent's) need not be written.
    this.sandbox = doc.createElement('iframe');
    this.sandbox.setAttribute('aria-hidden', 'true');
    this.sandbox.tabIndex = -1;
    this.sandbox.style.cssText = 'position:fixed;left:-20000px;top:0;width:200px;height:200px;border:0;visibility:hidden;pointer-events:none';
    doc.documentElement.appendChild(this.sandbox);
    this.sdoc = this.sandbox.contentDocument;
    // In standards mode, as a page is (about:blank is in quirks mode). Under Trusted Types write() throws: about:blank
    // stays then (its <html>, <head> and <body> are there already).
    try { this.sdoc.open(); this.sdoc.write('<!doctype html><html><head></head><body></body></html>'); this.sdoc.close(); } catch { /* Trusted Types */ }
    this.base = new Map();
    const cs = this.sdoc.defaultView.getComputedStyle(this.sdoc.body);
    this.names = [];
    for (let i = 0; i < cs.length; i++) if (!cs[i].startsWith('--') && !DROP.has(cs[i])) this.names.push(cs[i]);
  }
  done() { this.sandbox.remove(); }

  // Chromium on Windows paints a checkbox, radio, range or progress with no accent-color of its own in the system accent
  // on screen, but in its built-in blue in an image (light blue in a dark scheme). The system accent is what `Highlight`
  // gives in a light scheme there; it is written into such controls so the image shows what the screen does.
  accent(view) {
    if (this.sysAccent !== undefined) return this.sysAccent;
    this.sysAccent = null;
    if (view.navigator.userAgentData?.platform !== 'Windows') return null;
    const probe = this.sdoc.createElement('i');
    probe.style.cssText = 'color-scheme:light;color:Highlight';
    this.sdoc.body.appendChild(probe);
    this.sysAccent = this.sdoc.defaultView.getComputedStyle(probe).color;
    probe.remove();
    return this.sysAccent;
  }

  baseline(ns, tag) {
    const k = ns + ' ' + tag;
    let b = this.base.get(k);
    if (b) return b;
    const e = this.sdoc.createElementNS(ns, tag);
    let host = this.sdoc.body;
    if (ns === SVGNS && tag !== 'svg') { host = this.sdoc.createElementNS(SVGNS, 'svg'); this.sdoc.body.appendChild(host); }
    host.appendChild(e);
    const cs = this.sdoc.defaultView.getComputedStyle(e);
    b = {};
    for (const n of this.names) b[n] = cs.getPropertyValue(n);
    (host === this.sdoc.body ? e : host).remove();
    this.base.set(k, b);
    return b;
  }

  // Writes `cs` into `to`'s style: what differs from the tag's own default or from the parent's (inherited) value.
  style(to, cs, ns, tag, parent, all = false) {
    const b = this.baseline(ns, tag);
    const vals = {};
    let text = '';
    for (const n of this.names) {
      let v = cs.getPropertyValue(n);
      vals[n] = v;
      if (!all && v === b[n] && (!parent || parent[n] === v)) continue;
      if (v.includes('url(')) v = this.inlineUrls(v, (data) => to.style.setProperty(n, data));
      text += `${n}:${v};`;
    }
    const ff = cs.getPropertyValue('font-family');
    if (ff) this.families.add(ff);
    to.setAttribute('style', text);
    return vals;
  }

  // url(...) values become data: URLs (later: `set` gets the value with them when they arrive). One the server does not
  // have (an HTTP error) shows nothing on the page either: the value becomes `none`. One that cannot be read (another
  // site's, without CORS) needs Region Capture, or becomes `none` in blank mode.
  inlineUrls(value, set) {
    const found = [...value.matchAll(/url\(\s*(["']?)([^"')]+)\1\s*\)/g)].map((m) => m[2]).filter((u) => !u.startsWith('data:'));
    if (!found.length) return value;
    this.tasks.push(Promise.all(found.map((u) => this.dataUrl(u).catch((error) => {
      if (error?.status || this.blank) return null;
      throw new NeedsCapture('An image cannot be read.');
    }))).then((datas) => {
      let v = value;
      found.forEach((u, i) => { v = v.split(u).join(datas[i] || ''); });
      set(datas.includes(null) ? 'none' : v);
    }));
    return value;
  }

  dataUrl(url) {
    if (url.startsWith('data:')) return Promise.resolve(url);
    if (!this.urls.has(url)) {
      const view = this.doc.defaultView;
      this.urls.set(url, view.fetch(url, { mode: 'cors', credentials: 'same-origin' }).then((r) => {
        if (!r.ok) throw Object.assign(new Error(`${r.status} ${url}`), { status: r.status });
        return r.blob();
      }).then((blob) => new Promise((resolve, reject) => {
        const fr = new view.FileReader();
        fr.onload = () => resolve(fr.result); fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(blob);
      })));
    }
    return this.urls.get(url);
  }

  pseudo(from, to, cs) {
    const view = from.ownerDocument.defaultView;
    let cls = null;
    const klass = () => { if (!cls) { cls = `dkshot-${++this.n}`; to.setAttribute('class', `${to.getAttribute('class') || ''} ${cls}`.trim()); } return cls; };
    for (const which of ['::before', '::after']) {
      const ps = view.getComputedStyle(from, which);
      const content = ps.getPropertyValue('content');
      if (!content || content === 'none' || content === 'normal' || ps.getPropertyValue('display') === 'none') continue;
      // What differs from the element's (inherited) value or from an inline box's default (its own size, say).
      const b = this.baseline(XHTML, 'span');
      const decls = {};
      for (const n of this.names) {
        const v = ps.getPropertyValue(n);
        if (v !== cs.getPropertyValue(n) || v !== b[n] || n === 'content' || n === 'display') decls[n] = v;
      }
      const sel = `.${klass()}${which}`;
      const i = this.rules.length;
      const rule = () => `${sel}{${Object.entries(decls).map(([n, v]) => `${n}:${v} !important;`).join('')}}`;
      this.rules.push(rule());
      for (const [n, v] of Object.entries(decls)) {
        if (v.includes('url(')) this.inlineUrls(v, (data) => { decls[n] = data; this.rules[i] = rule(); });
      }
    }
    if ((from.localName === 'input' || from.localName === 'textarea') && from.placeholder && !from.value) {
      const ps = view.getComputedStyle(from, '::placeholder');
      this.rules.push(`.${klass()}::placeholder{color:${ps.color} !important;opacity:${ps.opacity} !important;}`);
    }
  }

  // An element and what it shows, written into `this.out`. `parent` is the parent's computed values (or null).
  copy(from, parent, opts = {}) {
    const view = from.ownerDocument.defaultView;
    const cs = view.getComputedStyle(from);
    const ns = from.namespaceURI || XHTML;
    let tag = from.localName;
    if (SKIP.has(tag) && ns === XHTML) return null;
    if (cs.getPropertyValue('display') === 'none') return null;
    if (ns === XHTML && (tag === 'iframe' || tag === 'frame')) return this.frame(from, cs, parent);
    let outTag = tag;
    let src = null;
    let scrolledText = false;
    if (ns === XHTML) {
      if (tag === 'html' || tag === 'body') outTag = 'div';
      else if (tag === 'canvas') { src = canvasData(from); outTag = 'img'; if (src === null) { if (!this.blank) throw new NeedsCapture('A canvas cannot be read.'); src = ''; } }
      else if (tag === 'video') {
        outTag = 'img';
        src = '';
        try {
          if (from.videoWidth) {
            const c = from.ownerDocument.createElement('canvas'); c.width = from.videoWidth; c.height = from.videoHeight;
            c.getContext('2d').drawImage(from, 0, 0); src = c.toDataURL('image/png');
          }
        } catch { if (!this.blank) throw new NeedsCapture('A video cannot be read.'); }
      } else if (tag === 'textarea' && (from.scrollTop || from.scrollLeft)) { outTag = 'div'; scrolledText = true; }
    }
    const to = this.out.createElementNS(ns, outTag);
    if (outTag === tag) for (const a of from.attributes) {
      if (a.name === 'style' || a.name.startsWith('on')) continue;
      try { to.setAttributeNS(a.namespaceURI, a.name, a.value); } catch { /* an attribute name XML refuses */ }
    }
    this.source.set(to, from);
    const vals = this.style(to, cs, ns, outTag, parent, !parent);
    if (outTag === 'div' && (tag === 'html' || tag === 'body') && cs.getPropertyValue('display') === 'inline') to.style.setProperty('display', 'block');
    if (ns === XHTML) this.pseudo(from, to, cs);
    if (ns === XHTML && cs.accentColor === 'auto' && (tag === 'progress' || (tag === 'input' && ACCENTED.has(from.type)))) {
      const a = this.accent(view);
      if (a) to.style.setProperty('accent-color', a);
    }
    if (src !== null) {
      to.setAttribute('src', src || 'data:,');
      if (!src) to.style.setProperty('visibility', 'hidden');
      if (to.style.getPropertyValue('display') === '' && cs.display === 'inline') to.style.setProperty('display', 'inline-block');
      to.style.setProperty('width', cs.width); to.style.setProperty('height', cs.height);
      return to;
    }
    if (ns === XHTML) {
      if (tag === 'img') { this.image(from, to); return to; }
      if (tag === 'input') {
        to.removeAttribute('value'); to.removeAttribute('checked');
        if (from.type === 'checkbox' || from.type === 'radio') { if (from.checked) to.setAttribute('checked', ''); }
        else if (from.type !== 'file' && from.type !== 'password') to.setAttribute('value', from.value);
        else if (from.type === 'password') to.setAttribute('value', from.value.replace(/./gu, '•'));
        return to;
      }
      if (tag === 'textarea' && !scrolledText) { to.textContent = from.value; return to; }
      if (tag === 'select') {
        for (const child of from.children) to.appendChild(this.out.importNode(child, true));
        [...to.querySelectorAll('option')].forEach((o, i) => { if (from.options[i]?.selected) o.setAttribute('selected', ''); else o.removeAttribute('selected'); });
        return to;
      }
    }
    if (ns === SVGNS && tag === 'use') this.useTarget(from);
    const inner = scrolledText ? this.out.createElementNS(XHTML, 'div') : null;
    if (inner) {
      inner.textContent = from.value;
      to.style.setProperty('overflow', 'hidden'); to.style.setProperty('white-space', cs.whiteSpace);
      inner.setAttribute('style', `translate:${-from.scrollLeft}px ${-from.scrollTop}px`);
      to.appendChild(inner);
      return to;
    }
    for (const child of from.childNodes) {
      if (child.nodeType === 3) to.appendChild(this.out.createTextNode(child.data));
      else if (child.nodeType === 1) { const c = this.copy(child, vals); if (c) to.appendChild(c); }
    }
    if (ns === XHTML) this.scroll(from, to, cs);
    return to;
  }

  // A scrolling area: no scrollbar of its own in the image (it would show the top), its room kept (a blank gutter where
  // the page has a classic scrollbar), and what it shows. Its content goes into one box moved up and left by the scroll
  // with margins (a layout offset: it snaps to device pixels as the page does, text directly in the area moves with
  // it, and a sticky child sticks where the page shows it).
  scroll(from, to, cs) {
    const scrolls = (o) => o === 'auto' || o === 'scroll';
    const sx = from.scrollLeft;
    const sy = from.scrollTop;
    // overflow: hidden scrolls too, by script.
    if (!scrolls(cs.overflowX) && !scrolls(cs.overflowY) && !((sx || sy) && /hidden/.test(cs.overflow))) return;
    const bl = parseFloat(cs.borderLeftWidth) || 0;
    const br = parseFloat(cs.borderRightWidth) || 0;
    const bt = parseFloat(cs.borderTopWidth) || 0;
    const bb = parseFloat(cs.borderBottomWidth) || 0;
    const gutterY = Math.max(0, from.offsetWidth - from.clientWidth - bl - br);
    const gutterX = Math.max(0, from.offsetHeight - from.clientHeight - bt - bb);
    to.style.setProperty('overflow', 'hidden');
    if (gutterY) to.style.setProperty('scrollbar-gutter', 'stable');
    if (gutterX && !bb) to.style.setProperty('border-bottom', `${gutterX}px solid transparent`);
    if (!sx && !sy) return;
    const view = from.ownerDocument.defaultView;
    const box = this.out.createElementNS(XHTML, 'div');
    // Right and bottom margins as large keep its width, and its height for an area as tall as its content.
    let style = `margin:${-sy}px ${sx}px ${sy}px ${-sx}px;`;
    const display = cs.display;
    if (/flex|grid/.test(display)) {
      // The area's flex or grid layout moves to the box, the area's only child; the box fills the area's content box.
      const props = ['flex-direction', 'flex-wrap', 'justify-content', 'justify-items', 'align-items', 'align-content',
        'row-gap', 'column-gap', 'grid-template-columns', 'grid-template-rows', 'grid-template-areas', 'grid-auto-flow',
        'grid-auto-columns', 'grid-auto-rows'];
      style += `display:${display.includes('grid') ? 'grid' : 'flex'};${props.map((p) => `${p}:${cs.getPropertyValue(p)};`).join('')}`;
      style += `min-height:${from.clientHeight - (parseFloat(cs.paddingTop) || 0) - (parseFloat(cs.paddingBottom) || 0)}px;`;
      to.style.setProperty('display', display.startsWith('inline') ? 'inline-block' : 'block');
    } else style += 'display:flow-root;';
    box.setAttribute('style', style);
    for (const c of [...to.childNodes]) box.appendChild(c);
    to.appendChild(box);
    // An absolutely placed (or fixed) element, at any depth, whose containing block is the area scrolls with it, but the
    // box's margins do not move it: it is translated, on each axis where it is not at its static position (there the
    // box moved it). One whose containing block is outside the area does not scroll.
    if (!holds(cs, false) && !holds(cs, true)) return;
    for (const c of box.querySelectorAll('*')) {
      const kid = this.source.get(c);
      if (!kid) continue;
      const k = view.getComputedStyle(kid);
      const fixed = k.position === 'fixed';
      if (!fixed && k.position !== 'absolute') continue;
      let a = kid.parentElement;
      while (a && a !== from && !holds(view.getComputedStyle(a), fixed)) a = a.parentElement;
      if (a !== from || !holds(cs, fixed)) continue;
      const dx = k.left === 'auto' && k.right === 'auto' ? 0 : sx;
      const dy = k.top === 'auto' && k.bottom === 'auto' ? 0 : sy;
      if (!dx && !dy) continue;
      const t = c.style.getPropertyValue('translate');
      const [tx = '0px', ty = '0px'] = !t || t === 'none' ? [] : t.split(/\s+(?![^(]*\))/);
      c.style.setProperty('translate', `calc(${tx} - ${dx}px) calc(${ty} - ${dy}px)`);
    }
  }

  image(from, to) {
    to.removeAttribute('srcset'); to.removeAttribute('sizes'); to.removeAttribute('loading');
    const url = from.currentSrc || from.src;
    if (!url) return;
    to.setAttribute('src', 'data:,');
    this.tasks.push(this.dataUrl(url).catch(() => {
      // Not fetchable (no CORS): the pixels, if the image is readable as it is.
      const c = from.ownerDocument.createElement('canvas');
      c.width = from.naturalWidth; c.height = from.naturalHeight;
      try { c.getContext('2d').drawImage(from, 0, 0); return c.toDataURL('image/png'); } catch { return null; }
    }).then((data) => {
      if (data) { to.setAttribute('src', data); return; }
      if (!this.blank) throw new NeedsCapture('An image cannot be read.');
      to.style.setProperty('visibility', 'hidden');
    }));
  }

  useTarget(from) {
    const href = from.getAttribute('href') || from.getAttributeNS(XLINK, 'href') || '';
    if (!href.startsWith('#') || this.uses.has(href)) return;
    const target = from.ownerDocument.getElementById(href.slice(1));
    if (target) this.uses.set(href, this.out.importNode(target, true));
  }

  // An iframe: its box, and in it what its document shows (same origin), or `--dk-bg` (another origin, blank mode).
  frame(from, cs, parent) {
    const to = this.out.createElementNS(XHTML, 'div');
    this.style(to, cs, XHTML, 'div', parent);
    if (cs.display === 'inline') to.style.setProperty('display', 'inline-block');
    to.style.setProperty('overflow', 'hidden');
    // A hidden iframe hides its page too, but that page's own styles do not inherit the hiding: it is left out.
    if (cs.visibility !== 'visible') return to;
    const d = frameDoc(from);
    if (!d) {
      if (!this.blank && visible(from)) throw new NeedsCapture('The panel shows another site\'s page.');
      to.style.setProperty('background-color', this.doc.defaultView.getComputedStyle(this.doc.documentElement).getPropertyValue('--dk-bg').trim()
        || this.doc.defaultView.getComputedStyle(from).getPropertyValue('--dk-bg').trim() || 'transparent');
      return to;
    }
    this.docs.add(d);
    const fv = d.defaultView;
    const port = this.out.createElementNS(XHTML, 'div');
    const w = from.clientWidth;
    const h = from.clientHeight;
    // The frame's canvas: the root's background, else the body's (as a browser propagates it).
    const bg = [d.documentElement, d.body].map((e) => e && fv.getComputedStyle(e).backgroundColor).find((c) => c && c !== 'rgba(0, 0, 0, 0)') || 'transparent';
    port.setAttribute('style', `position:relative;overflow:hidden;width:${w}px;height:${h}px;background-color:${bg};color-scheme:${fv.getComputedStyle(d.documentElement).colorScheme}`);
    const html = this.copy(d.documentElement, null);
    if (html) {
      html.style.setProperty('position', 'relative');
      const se = d.scrollingElement || d.documentElement;
      if (se.scrollLeft || se.scrollTop) html.style.setProperty('translate', `${-se.scrollLeft}px ${-se.scrollTop}px`);
      port.appendChild(html);
    }
    to.appendChild(port);
    return to;
  }

  // Every task done (images, url() values; a failure that needs Region Capture is thrown), then the fonts gathered:
  // the families the copy uses are known by then.
  async settle(signal) {
    while (this.tasks.length) {
      await Promise.all(this.tasks.splice(0));
      signal?.throwIfAborted();
    }
    this.fontCss = await this.fonts();
    signal?.throwIfAborted();
  }

  // The @font-face rules of the families in use, their files embedded.
  async fonts() {
    const used = new Set();
    for (const list of this.families) for (const f of list.split(',')) used.add(f.trim().replace(/^["']|["']$/g, '').toLowerCase());
    const faces = [];
    for (const d of this.docs) {
      const loaded = new Set();
      try { for (const f of d.fonts) if (f.status === 'loaded') loaded.add(`${f.family.replace(/^["']|["']$/g, '').toLowerCase()}|${f.style}|${weightOf(f.weight)}|`); } catch { /* no FontFaceSet */ }
      const sheets = [...d.styleSheets];
      while (sheets.length) {
        const sheet = sheets.shift();
        let rules;
        try { rules = sheet.cssRules; } catch {
          if (sheet.href) faces.push(...await this.foreignFaces(sheet.href, used));
          continue;
        }
        for (const r of rules) {
          if (r.styleSheet) sheets.push(r.styleSheet);
          else if (r.cssRules && !r.style) sheets.push({ cssRules: r.cssRules, href: sheet.href });
          else if (r.constructor.name === 'CSSFontFaceRule' || r.type === 5) {
            const fam = r.style.getPropertyValue('font-family').trim().replace(/^["']|["']$/g, '').toLowerCase();
            if (!used.has(fam)) continue;
            // Of a family with faces loaded, only the style and weight loaded (a font service lists many).
            const style = r.style.getPropertyValue('font-style') || 'normal';
            const weight = weightOf(r.style.getPropertyValue('font-weight'));
            const mine = [...loaded].filter((l) => l.startsWith(fam + '|'));
            if (mine.length && !mine.some((l) => l.startsWith(`${fam}|${style}|${weight}|`))) continue;
            faces.push(this.face(r.cssText, sheet.href || d.baseURI));
          }
        }
      }
    }
    return (await Promise.all(faces.map((f) => Promise.resolve(f).catch(() => '')))).join('\n');
  }

  async face(css, base) {
    const urls = [...css.matchAll(/url\(\s*(["']?)([^"')]+)\1\s*\)/g)].map((m) => m[2]);
    let out = css;
    for (const u of urls) {
      if (u.startsWith('data:')) continue;
      const abs = new URL(u, base).href;
      const data = await this.dataUrl(abs).catch(() => null);
      out = out.split(u).join(data || 'about:invalid');
    }
    return out;
  }

  async foreignFaces(href, used) {
    try {
      const css = await (await this.doc.defaultView.fetch(href, { mode: 'cors' })).text();
      return (css.match(/@font-face\s*{[^}]*}/g) || []).filter((r) => {
        const m = /font-family\s*:\s*([^;]+);/.exec(r);
        return m && used.has(m[1].trim().replace(/^["']|["']$/g, '').toLowerCase());
      }).map((r) => this.face(r, href));
    } catch { return []; }
  }

  svg(root, w, h, dpr) {
    const pw = Math.round(w * dpr);
    const ph = Math.round(h * dpr);
    const s = new XMLSerializer();
    const defs = this.uses.size ? `<svg xmlns="${SVGNS}" width="0" height="0" style="position:absolute">${[...this.uses.values()].map((n) => s.serializeToString(n)).join('')}</svg>` : '';
    const style = `<style>*,*::before,*::after{animation:none !important;transition:none !important;caret-color:transparent !important}${this.rules.join('')}${this.fontCss || ''}</style>`;
    // Laid out at device pixels (zoom), so boxes and text snap to the pixels they have on screen.
    return `<svg xmlns="${SVGNS}" width="${pw}" height="${ph}"><foreignObject x="0" y="0" width="${pw}" height="${ph}">`
      + `<div xmlns="${XHTML}" style="width:${w}px;height:${h}px;overflow:hidden;zoom:${dpr}">${style}${defs}${s.serializeToString(root)}</div></foreignObject></svg>`;
  }
}
