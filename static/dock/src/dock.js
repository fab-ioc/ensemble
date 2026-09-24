// The dock on the page. Each panel has a title bar; it can be dragged to an edge or into another panel as a tab,
// floated, unpinned to a strip on its edge, minimised or maximised, or popped out into a browser window of its own.
// The layout (layout.js) is remembered in the storage the app gives.
//
// The panels' own elements are moved between frames, never rebuilt: what they show, their listeners and their state stay
// theirs. Scroll positions are carried across a move.
//
// A popped-out panel's element moves into that window's document (same origin), so it is still the one panel with the
// one set of listeners and state: nothing is drawn twice and an action goes out once. Its place in the layout stays
// where it was and shows "shown in its own window · Bring back". `out` remembers which panels are out and their
// window's screen position and size; after a reload their places offer to open the window again, as a browser opens one
// only on a click.

import { addHostDoc, removeHostDoc, whenGone } from './host.js';
import {
  EDGES, LAYOUT_VERSION, makeConfig, stackNode, locate, panelsUnder, contains, findStack, whereIs, isShownIn,
  moveTo, floatPanel, dockBack, unpinPanel, pinPanel, hidePanel, showPanel, activate, normalizeLayout, clampFloat,
} from './layout.js';

export const LAYOUT_KEY = 'dock.layout';
export const POP_URL = 'popout.html'; // beside the app's page: same origin, nothing in the URL
export const POP_ROOT_ID = 'dk-pop-root';
export const THEME_EVENT = 'dock-theme';
export const THEME_ATTRS = ['data-theme', 'data-scheme', 'class', 'style'];
const POP_WAIT_MS = 15000;

/** The words the dock shows. Any of them can be replaced through createDock's `text` option. */
export const TEXT = {
  empty: 'Every panel is floating, unpinned or hidden: the Panels menu shows them.',
  popShown: (t) => `${t}: shown in its own window`,
  popShow: 'Show that window',
  popShowTitle: 'bring its window to the front',
  popBack: 'Bring back',
  popBackTitle: 'put it back here and close its window',
  popWas: (t) => `${t} was in its own window before this page was reloaded`,
  popReopen: 'Open its window again',
  popReopenTitle: 'open it in its own window where it was',
  popKeep: 'Keep it here',
  popKeepTitle: 'show it here again',
  popBlocked: (t) => `The browser did not open a window for ${t}: allow pop-ups for this page, then pop it out again.`,
  popFailed: (t) => `The window for ${t} did not load; it is back here.`,
  backToMain: 'Back to main window',
  backToMainTitle: 'put this panel back where it was in the main window and close this one',
};

const ICON = {
  menu: '<circle cx="3" cy="6" r="1"/><circle cx="6" cy="6" r="1"/><circle cx="9" cy="6" r="1"/>',
  min: '<path d="M2.5 9.5h7"/>',
  unmin: '<path d="M2.5 7.5 6 4l3.5 3.5"/>',
  max: '<rect x="2" y="2" width="8" height="8"/>',
  restore: '<rect x="2" y="4" width="6" height="6"/><path d="M4 4V2h6v6H8"/>',
  float: '<rect x="1.5" y="4.5" width="6" height="6"/><path d="M5.5 1.5h5v5M10.5 1.5 6 6"/>',
  dock: '<rect x="1.5" y="1.5" width="9" height="9"/><path d="M1.5 7.5h9"/>',
  pin: '<path d="M4 1.5h4M5 1.5v4L3 7.5h6L7 5.5v-4M6 7.5V11"/>',
  unpin: '<path d="M4 1.5h4M5 1.5v4L3 7.5h6L7 5.5v-4M6 7.5V11" transform="rotate(45 6 6)"/>',
  pop: '<rect x="1.5" y="1.5" width="9" height="6.5"/><path d="M4 10.5h4M6 8v2.5"/>', // a monitor: a window of its own
};
const icon = (name) => `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">${ICON[name]}</svg>`;
export const escText = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const finite = (n) => typeof n === 'number' && Number.isFinite(n);

function defaultStorage() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
}

/**
 * The panels a container declares: each child with data-panel="id", data-title and (optionally) data-panel-help.
 * <div data-panel="log" data-title="Log" data-panel-help="log">…</div>
 */
export function panelsFrom(container) {
  return [...container.querySelectorAll(':scope > [data-panel]')].map((el) => ({
    id: el.dataset.panel, title: el.dataset.title || el.dataset.panel, help: el.dataset.panelHelp || null, el }));
}

/**
 * Mounts the dock in `root`. `panels` are [{ id, title, help?, el }] (each `el` is the panel's own element).
 *
 * Options (see the README for each):
 *   storage, storageKey, migrate             where the layout is kept (any { getItem, setItem, removeItem })
 *   defaultLayout, minSize, edgeOf, fill, defaultSize, sizes    the layout's defaults (layout.js makeConfig)
 *   popUrl, popName, popTitle, copyStyles, openWindow           pop-out windows
 *   themeAttrs, themeEvent                  what of the main page's <html> a pop-out window copies, and when
 *   help: { icon(key, panel), mount(doc), selector }            a panel's help control, and its popovers in a window
 *   modalSelector, badgeClass, text, onReset, win
 *
 * Returns the dock's handle: layout(), isShown(id), isVisible(id), isAuto(id), frontOf(id), activate(id), reveal(id),
 * setBadge(id, text, title), onShown(fn), onChange(fn), setVisible(id, on), reset(), float(id), dockBack(id), unpin(id),
 * pin(id), toggleMin(id), toggleMax(id), restoreMax(), moveTo(id, target, side), dockEdge(id, side), popOut(id),
 * popIn(id), isOut(id), popWindow(id), openFly(id), closeFly(), flyOpen(), maximised(), render(), destroy().
 */
export function createDock({ root, panels, storageKey = LAYOUT_KEY, key, storage = defaultStorage(),
  win = typeof window !== 'undefined' ? window : null, popUrl = POP_URL, popName = 'dock-panel-', popTitle = (p) => p.title,
  copyStyles = true, themeAttrs = THEME_ATTRS, themeEvent = THEME_EVENT, help = {}, modalSelector = '[role="dialog"], .dk-help-pop',
  badgeClass = 'dk-badge', text = {}, onReset = null, migrate = null,
  defaultLayout, minSize, edgeOf, fill, defaultSize, sizes,
  openWindow = (url, name, features) => (win && typeof win.open === 'function' ? win.open(url, name, features) : null) }) {
  const doc = root.ownerDocument;
  const T = { ...TEXT, ...text };
  const layoutKey = key || storageKey;
  const ids = panels.map((p) => p.id);
  const cfg = makeConfig({ ids, defaultLayout, minSize, edgeOf, fill, defaultSize, sizes });
  const S = cfg.sizes;
  const helpSel = help.selector || '[data-help]';
  const byId = new Map(panels.map((p) => [p.id, p]));
  const badges = new Map();
  const shownFns = [];
  const changeFns = [];
  const undo = []; // what destroy() takes off
  const scrolls = new Map(); // panel id -> [[element, left, top]] from before its last move
  let stackEls = new Map(); // stack node -> its element
  let nodeEls = new Map(); // split child node -> its element
  let floatEls = new Map(); // float -> its element
  let flyEls = new Map(); // unpinned panel id -> its flyout
  let stripBtns = new Map(); // unpinned panel id -> its strip button
  let maxed = null; // the maximised stack node (not stored)
  let flyOpen = null; // the unpinned panel slid out
  let flyByHover = false;
  let wasShown = new Set();
  const pops = new Map(); // panel id -> { win, doc, timer, started } while its own window is open (or opening)
  let leaving = false; // the page is going away: its windows close, and stay remembered
  let destroyed = false;
  // The dock's own delayed work (hover, notes, focus): destroy() cancels it, and none runs after.
  const timers = new Set();
  function later(fn, ms) {
    const t = setTimeout(() => { timers.delete(t); if (!destroyed) fn(); }, ms);
    timers.add(t);
    return t;
  }
  const popKey = Math.random().toString(36).slice(2); // a popped-out window checks it still belongs to this page
  if (win) { try { win.__dockPopKey = popKey; } catch { /* a stub */ } }
  let layout = load();

  const main = doc.createElement('div');
  const parking = doc.createElement('div');
  parking.className = 'dk-parking';
  parking.hidden = true;
  const preview = doc.createElement('div');
  preview.className = 'dk-preview';
  preview.hidden = true;
  root.classList.add('dock');
  // The sizes the stylesheet draws with are the ones the layout counts with.
  root.style.setProperty('--dk-head', S.head + 'px');
  root.style.setProperty('--dk-bar', S.bar + 'px');
  root.style.setProperty('--dk-strip', S.strip + 'px');

  function on(target, type, fn, o) { target.addEventListener(type, fn, o); undo.push(() => target.removeEventListener(type, fn, o)); }
  function viewport() { return (win && win.innerWidth) || 1600; }
  function extent() {
    const r = root.getBoundingClientRect ? root.getBoundingClientRect() : null;
    const w = (r && r.width) || viewport();
    const h = (r && r.height) || (win && win.innerHeight) || 900;
    return { w, h };
  }
  function dims(node) {
    const el = stackEls.get(node) || nodeEls.get(node);
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    return r && r.width ? { w: r.width, h: r.height } : null;
  }
  const opts = () => ({ cfg, viewportPx: viewport(), dims, extent: extent() });

  // ---- panels in windows of their own ----
  const outOf = (id) => (layout.out ? layout.out[id] : null);
  const live = (id) => !!(pops.get(id) && pops.get(id).doc); // in its own window, and drawn there
  // A panel is on screen when its own window shows it, or in the main window when it is not out.
  const shownNow = (id) => live(id) || (!outOf(id) && isShownIn(layout, id));

  function load() {
    let raw = null;
    try { const s = storage && storage.getItem(layoutKey); raw = s ? JSON.parse(s) : null; } catch { raw = null; }
    return normalizeLayout(raw, { cfg, viewportPx: viewport(), migrate });
  }
  function save() {
    try { if (storage) storage.setItem(layoutKey, JSON.stringify(layout)); } catch { /* not remembered; still applied */ }
  }
  function commit() {
    save();
    render();
    for (const fn of changeFns) fn(layout);
  }

  // ---- drawing ----

  function scrollsOf(el) {
    return [el, ...el.querySelectorAll('*')].filter((x) => x.scrollLeft || x.scrollTop).map((x) => [x, x.scrollLeft, x.scrollTop]);
  }
  function putScrolls(id) {
    const el = byId.get(id).el;
    for (const [x, left, top] of scrolls.get(id) || []) {
      if (!x.isConnected || !el.contains(x)) continue;
      if (x.scrollLeft !== left) x.scrollLeft = left;
      if (x.scrollTop !== top) x.scrollTop = top;
    }
  }
  function snapshotScrolls() {
    for (const id of ids) {
      if (outOf(id)) continue;
      const el = byId.get(id).el;
      if (!el.isConnected || el.closest('.dk-parking')) continue;
      scrolls.set(id, scrollsOf(el));
    }
  }
  function restoreScrolls() {
    for (const id of ids) {
      if (outOf(id)) continue;
      if (byId.get(id).el.closest('.dk-parking')) continue;
      putScrolls(id);
    }
  }

  const helpHtml = (p) => (p.help && typeof help.icon === 'function' ? help.icon(p.help, p) || '' : '');
  function tabHtml(id, active, draggable) {
    const p = byId.get(id);
    const b = badges.get(id);
    return `<span class="dk-tab-wrap${active ? ' on' : ''}"><button type="button" class="dk-tab${active ? ' on' : ''}" role="tab" data-dk-tab="${escText(id)}"`
      + ` aria-selected="${active}" title="${escText(p.title)}${draggable ? ': drag to dock it at an edge or into another panel as a tab' : ''}">`
      + `${escText(p.title)}${b ? `<span class="${badgeClass}" title="${escText(b.title || '')}">${escText(b.text)}</span>` : ''}</button>`
      + `${helpHtml(p)}</span>`;
  }
  const ctl = (act, name, label, title) => `<button type="button" class="dk-btn" data-dk-act="${act}" aria-label="${escText(label)}" title="${escText(title)}">${icon(name)}</button>`;

  function headHtml(node, where) {
    const t = byId.get(node.active).title;
    const tabs = node.panels.map((id) => tabHtml(id, id === node.active, where === 'dock')).join('');
    const isMax = maxed === node;
    const c = [ctl('menu', 'menu', `${t}: move or hide`, 'dock at an edge, float, unpin or hide')];
    c.push(node.min ? ctl('min', 'unmin', `${t}: restore`, 'restore from its title bar') : ctl('min', 'min', `${t}: minimise`, 'minimise to its title bar'));
    c.push(isMax ? ctl('max', 'restore', `${t}: restore size`, 'restore (Esc)') : ctl('max', 'max', `${t}: maximise`, 'maximise (Esc restores)'));
    c.push(ctl('pop', 'pop', `${t}: pop out`, 'pop out into a browser window of its own (drag it to another monitor)'));
    if (where === 'float') c.push(ctl('dock', 'dock', `${t}: dock back`, 'dock back where it was (or double-click the title bar)'));
    else c.push(ctl('float', 'float', `${t}: float`, 'float in its own window'));
    c.push(ctl('unpin', 'pin', `${t}: unpin`, 'pinned: unpin to a strip on its edge (auto-hide)'));
    return `<div class="dk-tabs" role="tablist">${tabs}</div><span class="dk-ctl">${c.join('')}</span>`;
  }

  function buildStack(node, where) {
    const sec = doc.createElement('section');
    sec.className = 'dk-stack' + (node.min ? ' dk-min' : '') + (maxed === node ? ' dk-max' : '');
    sec.setAttribute('aria-label', byId.get(node.active).title);
    const head = doc.createElement('div');
    head.className = 'dk-head dk-mono';
    head.innerHTML = headHtml(node, where);
    const body = doc.createElement('div');
    body.className = 'dk-body';
    for (const id of node.panels) {
      const el = slot(id);
      el.classList.add('dk-panel');
      el.classList.toggle('dk-off', id !== node.active);
      if (id !== node.active) el.setAttribute('aria-hidden', 'true'); else el.removeAttribute('aria-hidden');
      body.appendChild(el);
    }
    sec.append(head, body);
    stackEls.set(node, sec);
    return sec;
  }

  function fillIndex(sp) {
    let best = -1;
    let bestSize = -1;
    for (let i = 0; i < sp.kids.length; i++) {
      const k = sp.kids[i];
      if (k.t === 'stack' && k.min) continue;
      if (cfg.fill && contains(k, cfg.fill)) return i;
      const s = finite(k.size) ? k.size : Infinity;
      if (s > bestSize) { best = i; bestSize = s; }
    }
    return best;
  }

  function minAlong(node, dir) {
    if (node.t === 'stack') {
      if (node.min) return S.head + 1;
      return Math.max(...node.panels.map((id) => (dir === 'row' ? cfg.minSize(id).w : cfg.minSize(id).h)));
    }
    const mins = node.kids.map((k) => minAlong(k, dir));
    return node.dir === dir ? mins.reduce((a, b) => a + b, 0) + S.bar * (mins.length - 1) : Math.max(...mins);
  }

  function sizeKid(el, node, sp, fillAt, i) {
    const dir = sp.dir;
    el.style[dir === 'row' ? 'minWidth' : 'minHeight'] = minAlong(node, dir) + 'px';
    if (node.t === 'stack' && node.min) el.style.flex = `0 0 ${S.head + 1}px`;
    else if (i === fillAt) el.style.flex = '1 1 0px';
    else el.style.flex = `0 1 ${finite(node.size) ? node.size : S.splitSize}px`;
  }

  function buildNode(node) {
    if (node.t === 'stack') return buildStack(node, 'dock');
    const el = doc.createElement('div');
    el.className = 'dk-split dk-' + node.dir;
    const fillAt = fillIndex(node);
    node.kids.forEach((k, i) => {
      if (i > 0) el.appendChild(buildBar(node, i - 1));
      const kid = buildNode(k);
      sizeKid(kid, k, node, fillAt, i);
      nodeEls.set(k, kid);
      el.appendChild(kid);
    });
    return el;
  }

  // Sizes that do not fit the window (a layout kept from a wider one) are narrowed for now, the widest first and never
  // below its minimum, so the one that takes what is left keeps its own minimum. Nothing is remembered: a wider window
  // gets the kept sizes back.
  function fitAll() {
    for (const [node, el] of nodeEls) {
      if (node.t !== 'split') continue;
      const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
      const avail = r ? (node.dir === 'row' ? r.width : r.height) - S.bar * (node.kids.length - 1) : 0;
      if (!(avail > 0)) continue;
      const fillAt = fillIndex(node);
      const kids = node.kids.map((k, i) => ({ k, i, min: minAlong(k, node.dir),
        px: k.t === 'stack' && k.min ? S.head + 1 : i === fillAt ? minAlong(k, node.dir) : (finite(k.size) ? k.size : S.splitSize) }));
      let need = kids.reduce((a, x) => a + x.px, 0) - avail;
      while (need > 0) {
        const give = kids.filter((x) => x.i !== fillAt && !(x.k.t === 'stack' && x.k.min) && x.px > x.min).sort((a, b) => b.px - a.px)[0];
        if (!give) break;
        const next = kids.filter((x) => x !== give && x.i !== fillAt && x.px > x.min && x.px < give.px).reduce((a, x) => Math.max(a, x.px), 0);
        const cut = Math.min(need, give.px - Math.max(give.min, next));
        give.px -= cut;
        need -= cut;
      }
      for (const x of kids) {
        if (x.i === fillAt || (x.k.t === 'stack' && x.k.min)) continue;
        const kid = nodeEls.get(x.k);
        if (kid) kid.style.flex = `0 1 ${Math.round(x.px)}px`;
      }
    }
  }

  function buildBar(sp, i) {
    const bar = doc.createElement('div');
    bar.className = 'dk-bar';
    bar.setAttribute('role', 'separator');
    bar.setAttribute('aria-orientation', sp.dir === 'row' ? 'vertical' : 'horizontal');
    const names = (n) => panelsUnder(n).map((id) => byId.get(id).title).join(', ');
    bar.setAttribute('aria-label', `resize ${names(sp.kids[i])} | ${names(sp.kids[i + 1])}`);
    bar.title = 'drag to resize; double-click for the default size; arrow keys move it';
    bar.tabIndex = 0;
    bar._dk = { split: sp, i };
    return bar;
  }

  function buildFloat(f, z) {
    const el = doc.createElement('div');
    el.className = 'dk-float' + (f.stack.min ? ' dk-min' : '') + (maxed === f.stack ? ' dk-max' : '');
    el.style.zIndex = String(20 + z);
    el.appendChild(buildStack(f.stack, 'float'));
    for (const d of ['e', 's', 'se', 'w', 'n']) {
      const h = doc.createElement('div');
      h.className = 'dk-rz dk-rz-' + d;
      h.dataset.dkRz = d;
      h.setAttribute('aria-hidden', 'true');
      el.appendChild(h);
    }
    floatEls.set(f, el);
    placeFloat(f, el);
    return el;
  }

  function placeFloat(f, el = floatEls.get(f)) {
    if (!el) return;
    el.style.left = f.x + 'px';
    el.style.top = f.y + 'px';
    el.style.width = f.w + 'px';
    el.style.height = (f.stack.min ? S.head + 2 : f.h) + 'px';
  }

  function buildStrip(edge, entries) {
    const strip = doc.createElement('div');
    strip.className = 'dk-strip dk-strip-' + edge;
    strip.setAttribute('role', 'toolbar');
    strip.setAttribute('aria-label', `unpinned panels, ${edge} edge`);
    for (const a of entries) {
      const b = doc.createElement('button');
      b.type = 'button';
      b.className = 'dk-strip-btn dk-mono';
      b.dataset.dkAuto = a.id;
      b.textContent = byId.get(a.id).title;
      b.title = `${byId.get(a.id).title}: unpinned. Click or hover to slide it out; pin it from its title bar.`;
      b.setAttribute('aria-expanded', 'false');
      stripBtns.set(a.id, b);
      strip.appendChild(b);
    }
    return strip;
  }

  function buildFlyout(a) {
    const p = byId.get(a.id);
    const el = doc.createElement('div');
    el.className = 'dk-flyout dk-fly-' + a.edge;
    el.dataset.dkFly = a.id;
    el.setAttribute('role', 'region');
    el.setAttribute('aria-label', p.title);
    const head = doc.createElement('div');
    head.className = 'dk-head dk-mono';
    head.innerHTML = `<div class="dk-tabs">${tabHtml(a.id, true, false)}</div><span class="dk-ctl">`
      + ctl('pop', 'pop', `${p.title}: pop out`, 'pop out into a browser window of its own (drag it to another monitor)')
      + ctl('float', 'float', `${p.title}: float`, 'float in its own window')
      + ctl('hide-fly', 'min', `${p.title}: slide in`, 'slide it back in (Esc)')
      + ctl('pin', 'unpin', `${p.title}: pin`, 'unpinned: pin it back where it was') + '</span>';
    const body = doc.createElement('div');
    body.className = 'dk-body';
    const pel = slot(a.id);
    pel.classList.add('dk-panel');
    pel.classList.remove('dk-off');
    pel.removeAttribute('aria-hidden');
    body.appendChild(pel);
    const grip = doc.createElement('div');
    grip.className = 'dk-fly-grip';
    grip.setAttribute('aria-hidden', 'true');
    el.append(head, body, grip);
    flyEls.set(a.id, el);
    return el;
  }

  function placeFlyout(a) {
    const el = flyEls.get(a.id);
    if (!el) return;
    const ext = extent();
    const has = (edge) => layout.auto.some((x) => x.edge === edge);
    const across = a.edge === 'left' || a.edge === 'right' ? ext.w : ext.h;
    const size = Math.round(Math.min(a.size, across * 0.85));
    const s = el.style;
    s.top = s.bottom = s.left = s.right = s.width = s.height = '';
    const off = (edge) => (has(edge) ? 'var(--dk-strip)' : '0px');
    if (a.edge === 'left' || a.edge === 'right') {
      s[a.edge] = off(a.edge);
      s.top = off('top'); s.bottom = off('bottom');
      s.width = size + 'px';
    } else {
      s[a.edge] = off(a.edge);
      s.left = off('left'); s.right = off('right');
      s.height = size + 'px';
    }
  }

  function render() {
    snapshotScrolls();
    const focused = doc.activeElement;
    stackEls = new Map();
    nodeEls = new Map();
    floatEls = new Map();
    flyEls = new Map();
    stripBtns = new Map();
    if (maxed && !stillHas(maxed)) maxed = null;
    if (flyOpen && !layout.auto.some((a) => a.id === flyOpen)) flyOpen = null;
    root.textContent = '';
    main.className = 'dk-main';
    main.textContent = '';
    if (layout.root) {
      const top = buildNode(layout.root);
      nodeEls.set(layout.root, top);
      main.appendChild(top);
    } else {
      main.innerHTML = `<div class="dk-empty dk-mono">${escText(T.empty)}</div>`;
    }
    root.appendChild(main);
    for (const edge of EDGES) {
      const entries = layout.auto.filter((a) => a.edge === edge);
      if (entries.length) root.appendChild(buildStrip(edge, entries));
    }
    const ext = extent();
    layout.floats.forEach((f, z) => { clampFloat(f, ext.w, ext.h, S.floatMin); root.appendChild(buildFloat(f, z)); });
    for (const a of layout.auto) {
      const el = buildFlyout(a);
      root.appendChild(el);
      placeFlyout(a);
    }
    parking.textContent = '';
    for (const h of layout.hidden) { const el = byId.get(h.id).el; el.classList.add('dk-panel'); parking.appendChild(el); }
    // Out, but not drawn in a window of its own (still opening, or remembered from before a reload): kept here.
    for (const id of ids) if (outOf(id) && !live(id)) parking.appendChild(byId.get(id).el);
    root.append(parking, preview);
    root.classList.toggle('dk-has-max', !!maxed);
    markFlyouts();
    restoreScrolls();
    if (focused && focused !== doc.body && focused.isConnected && root.contains(focused) && doc.activeElement !== focused) {
      try { focused.focus({ preventScroll: true }); } catch { /* ignore */ }
    }
    fitAll();
    announceShown();
  }

  function stillHas(node) {
    return (layout.root && (layout.root === node || findStack(layout.root, node))) || layout.floats.some((f) => f.stack === node);
  }

  function shownEvent(id) {
    const el = byId.get(id).el;
    const W = el.ownerDocument.defaultView;
    try { el.dispatchEvent(new ((W && W.CustomEvent) || CustomEvent)('dock-shown', { detail: { id } })); } catch { /* no CustomEvent */ }
  }
  function announceShown() {
    const now = new Set(ids.filter(shownNow));
    for (const id of now) {
      if (wasShown.has(id)) continue;
      for (const fn of shownFns) fn(id);
      shownEvent(id);
    }
    wasShown = now;
  }

  function markFlyouts() {
    for (const [id, el] of flyEls) {
      const open = id === flyOpen;
      el.classList.toggle('open', open);
      const b = stripBtns.get(id);
      if (b) { b.setAttribute('aria-expanded', String(open)); b.classList.toggle('on', open); }
      if (open) shownEvent(id);
    }
  }

  // ---- a panel in a window of its own ----

  // What a panel's place in the main window holds: the panel, or while it is out, a line saying where it is.
  function slot(id) {
    if (!outOf(id)) return byId.get(id).el;
    const el = doc.createElement('div');
    el.className = 'dk-popped dk-mono';
    el.dataset.dkPopped = id;
    el.setAttribute('role', 'status');
    const t = byId.get(id).title;
    const btn = (k, label, title) => `<button type="button" class="dk-textbtn" data-dk-pop="${k}" title="${escText(title)}">${escText(label)}</button>`;
    el.innerHTML = pops.has(id)
      ? `<p>${escText(T.popShown(t))}</p><div class="dk-popped-acts">${btn('show', T.popShow, T.popShowTitle)}${btn('back', T.popBack, T.popBackTitle)}</div>`
      : `<p>${escText(T.popWas(t))}</p><div class="dk-popped-acts">${btn('reopen', T.popReopen, T.popReopenTitle)}${btn('back', T.popKeep, T.popKeepTitle)}</div>`;
    return el;
  }

  let noteEl = null;
  let noteTimer = null;
  function notify(msg) {
    if (destroyed || !doc.body) return;
    if (!noteEl) { noteEl = doc.createElement('div'); noteEl.className = 'dk-note dk-mono'; noteEl.setAttribute('role', 'status'); }
    noteEl.textContent = msg;
    doc.body.appendChild(noteEl);
    clearTimeout(noteTimer);
    noteTimer = later(() => { if (noteEl) noteEl.remove(); }, 9000);
  }

  // Where the window opens: where it was last time, else over the panel's place, its size.
  function popGeometry(id) {
    const g = outOf(id);
    if (g && ['x', 'y', 'w', 'h'].every((k) => finite(g[k]))) return { ...g };
    const at = locate(layout.root, id);
    const d = at && dims(at.stack);
    const el = at && stackEls.get(at.stack);
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    const out = { w: Math.round(Math.max(S.popMin.w, d ? d.w : 560)), h: Math.round(Math.max(S.popMin.h, d ? d.h : 440)) };
    const sx = win && finite(win.screenX) ? win.screenX : null;
    const sy = win && finite(win.screenY) ? win.screenY : null;
    if (sx !== null && sy !== null) {
      const chrome = win && finite(win.outerHeight) && finite(win.innerHeight) ? Math.max(0, win.outerHeight - win.innerHeight) : 0;
      out.x = Math.round(sx + (r && r.width ? r.left : 80) + 24);
      out.y = Math.round(sy + chrome + (r && r.width ? r.top : 60) + 24);
    }
    return out;
  }
  function features(g) {
    let f = `popup=yes,width=${g.w},height=${g.h}`;
    if (finite(g.x) && finite(g.y)) f += `,left=${g.x},top=${g.y}`;
    return f;
  }

  // The window shows the main page's theme: the attributes of <html> that carry it.
  function syncTheme(cd) {
    try {
      const from = doc.documentElement;
      const to = cd.documentElement;
      for (const a of themeAttrs) {
        const v = from.getAttribute(a);
        if (v === null) to.removeAttribute(a); else if (to.getAttribute(a) !== v) to.setAttribute(a, v);
      }
    } catch { /* the window is going */ }
  }
  function syncThemes() { for (const pop of pops.values()) if (pop.doc) syncTheme(pop.doc); }

  // The window gets the main page's stylesheets, so the panel looks as it did (popout.html need not know them).
  function syncStyles(cd) {
    if (!copyStyles) return;
    for (const old of cd.querySelectorAll('[data-dk-copied]')) old.remove();
    for (const s of doc.querySelectorAll('link[rel~="stylesheet"], style')) {
      let c;
      if (s.tagName === 'LINK') { c = cd.createElement('link'); c.rel = 'stylesheet'; c.href = s.href; if (s.media) c.media = s.media; }
      else { c = cd.createElement('style'); c.textContent = s.textContent; if (s.media) c.media = s.media; }
      c.dataset.dkCopied = '';
      cd.head.appendChild(c);
    }
  }

  function popOut(id) {
    const had = pops.get(id);
    if (had) { try { had.win.focus(); } catch { /* ignore */ } return true; }
    const w = whereIs(layout, id);
    if (!w) return false;
    const geo = popGeometry(id);
    let child = null;
    try { child = openWindow(popUrl, popName + id, features(geo)); } catch { child = null; }
    if (!child) {
      notify(T.popBlocked(byId.get(id).title));
      return false;
    }
    if (w.kind === 'hidden') showPanel(layout, id, opts());
    if (flyOpen === id) flyOpen = null;
    const el = byId.get(id).el;
    if (el.isConnected && !el.closest('.dk-parking')) scrolls.set(id, scrollsOf(el));
    const pop = { win: child, doc: null, timer: null, started: Date.now() };
    pops.set(id, pop);
    if (!layout.out) layout.out = {};
    layout.out[id] = geo;
    commit();
    waitForWindow(id, pop);
    return true;
  }

  // The window's own page (popout.html) has loaded: the panel moves into it.
  function waitForWindow(id, pop) {
    const tick = () => {
      if (pops.get(id) !== pop) return;
      let ready = false;
      try {
        if (pop.win.closed) { windowGone(id, pop); return; }
        const cd = pop.win.document;
        ready = !!(cd && cd.readyState !== 'loading' && cd.getElementById(POP_ROOT_ID));
      } catch { ready = false; }
      if (ready) { mountWindow(id, pop); return; }
      if (Date.now() - pop.started > POP_WAIT_MS) {
        notify(T.popFailed(byId.get(id).title));
        popIn(id);
        return;
      }
      pop.timer = setTimeout(tick, 40);
    };
    tick();
  }

  function mountWindow(id, pop) {
    const cd = pop.win.document;
    const p = byId.get(id);
    pop.doc = cd;
    try { pop.win.__dockPopKey = popKey; } catch { /* ignore */ }
    cd.title = popTitle(p);
    syncTheme(cd);
    syncStyles(cd);
    const host = cd.getElementById(POP_ROOT_ID);
    host.textContent = '';
    const sec = cd.createElement('section');
    sec.className = 'dk-stack dk-pop';
    sec.setAttribute('aria-label', p.title);
    const head = cd.createElement('div');
    head.className = 'dk-head dk-mono';
    head.innerHTML = `<div class="dk-tabs" role="tablist">${tabHtml(id, true, false)}</div><span class="dk-ctl">`
      + `<button type="button" class="dk-pop-back" data-dk-pop="back" title="${escText(T.backToMainTitle)}">${escText(T.backToMain)}</button></span>`;
    const body = cd.createElement('div');
    body.className = 'dk-body';
    p.el.classList.add('dk-panel');
    p.el.classList.remove('dk-off');
    p.el.removeAttribute('aria-hidden');
    body.appendChild(p.el);
    sec.append(head, body);
    host.appendChild(sec);
    addHostDoc(cd);
    if (typeof help.mount === 'function') {
      const h = help.mount(cd);
      const stop = typeof h === 'function' ? h : h && typeof h.destroy === 'function' ? h.destroy : null;
      if (stop) whenGone(cd, stop);
    }
    const onBack = (e) => { if (e.target.closest('[data-dk-pop="back"]')) popIn(id); };
    const onReveal = () => { try { pop.win.focus(); } catch { /* ignore */ } };
    const gone = () => windowGone(id, pop);
    const moved = () => rememberWindow(id, pop);
    // The panel's scroll positions, once the window has laid it out (its stylesheets may still be loading).
    const rescroll = () => { if (pops.get(id) === pop) putScrolls(id); };
    head.addEventListener('click', onBack);
    sec.addEventListener('dock-reveal', onReveal);
    pop.win.addEventListener('pagehide', gone);
    pop.win.addEventListener('resize', moved);
    pop.win.addEventListener('load', rescroll);
    rescroll();
    const later = [setTimeout(rescroll, 100), setTimeout(rescroll, 400)];
    // When the window goes (or its panel comes back), nothing of the dock's stays on it. The panel has already left
    // `sec` for this document by then (release).
    whenGone(cd, () => {
      for (const t of later) clearTimeout(t);
      try {
        pop.win.removeEventListener('pagehide', gone);
        pop.win.removeEventListener('resize', moved);
        pop.win.removeEventListener('load', rescroll);
      } catch { /* closed */ }
      head.removeEventListener('click', onBack);
      sec.removeEventListener('dock-reveal', onReveal);
      sec.remove();
    });
    // A window closed without its page saying so (the browser killed it), and a window moved (no event says so).
    pop.timer = setInterval(() => {
      if (pops.get(id) !== pop) { clearInterval(pop.timer); return; }
      let closed = true;
      try { closed = pop.win.closed; } catch { closed = true; }
      if (closed) windowGone(id, pop); else rememberWindow(id, pop);
    }, 1000);
    render();
    shownEvent(id);
  }

  function rememberWindow(id, pop) {
    if (pops.get(id) !== pop || !layout.out || !layout.out[id]) return;
    let g = null;
    try { g = { x: pop.win.screenX, y: pop.win.screenY, w: pop.win.innerWidth, h: pop.win.innerHeight }; } catch { return; }
    if (!['x', 'y', 'w', 'h'].every((k) => finite(g[k])) || !(g.w > 0) || !(g.h > 0)) return;
    const was = layout.out[id];
    if (['x', 'y', 'w', 'h'].every((k) => was[k] === Math.round(g[k]))) return;
    layout.out[id] = { x: Math.round(g.x), y: Math.round(g.y), w: Math.round(g.w), h: Math.round(g.h) };
    save();
  }

  // Its window is done with: the panel comes back into this document at once, and the window closes if asked.
  function release(id, pop, closeIt) {
    pops.delete(id);
    clearTimeout(pop.timer);
    clearInterval(pop.timer);
    const el = byId.get(id).el;
    if (pop.doc && el.ownerDocument === pop.doc) { try { scrolls.set(id, scrollsOf(el)); } catch { /* the window is gone */ } }
    parking.appendChild(el);
    if (pop.doc) removeHostDoc(pop.doc);
    if (closeIt) { try { pop.win.close(); } catch { /* ignore */ } }
  }

  // The person closed the window (or it went on its own): the panel is back in its place, and no longer out.
  function windowGone(id, pop) {
    if (leaving || pops.get(id) !== pop) return;
    release(id, pop, false);
    if (layout.out) delete layout.out[id];
    commit();
  }

  /** Back into the main window where it was; its own window closes. Also "Keep it here" for one remembered as out. */
  function popIn(id) {
    const pop = pops.get(id);
    if (pop) release(id, pop, true);
    if (!layout.out || !(id in layout.out)) { if (pop) commit(); return false; }
    delete layout.out[id];
    commit();
    return true;
  }

  // ---- unpinned panels sliding out ----

  let hoverTimer = null;
  let leaveTimer = null;
  function openFly(id, byHover) {
    clearTimeout(leaveTimer);
    if (flyOpen === id) { if (!byHover) flyByHover = false; return; }
    flyOpen = id;
    flyByHover = !!byHover;
    const a = layout.auto.find((x) => x.id === id);
    if (a) placeFlyout(a);
    markFlyouts();
  }
  function closeFly(refocus) {
    if (!flyOpen) return;
    const id = flyOpen;
    flyOpen = null;
    markFlyouts();
    const b = stripBtns.get(id);
    if (refocus && b) b.focus();
  }
  function insideFly(el) {
    if (!el || !flyOpen) return false;
    const fly = flyEls.get(flyOpen);
    const b = stripBtns.get(flyOpen);
    return !!((fly && fly.contains(el)) || (b && b.contains(el)));
  }

  // ---- actions ----

  function stackAround(el) {
    const sec = el.closest('.dk-stack');
    if (!sec) return null;
    for (const [node, e] of stackEls) if (e === sec) return node;
    return null;
  }
  function floatAround(el) {
    const fe = el.closest('.dk-float');
    if (!fe) return null;
    for (const [f, e] of floatEls) if (e === fe) return f;
    return null;
  }

  function floatRectFor(id) {
    const node = locate(layout.root, id);
    const d = node && dims(node.stack);
    const ext = extent();
    const w = Math.round(Math.min(ext.w * 0.6, Math.max(S.floatMin.w * 2, d ? d.w : 520)));
    const h = Math.round(Math.min(ext.h * 0.7, Math.max(S.floatMin.h * 2, d ? d.h : 420)));
    const n = layout.floats.length;
    return { x: Math.round((ext.w - w) / 2) + 24 * n, y: Math.round((ext.h - h) / 3) + 24 * n, w, h };
  }

  // The edge an unpinned panel goes to: the dock's edge nearest its place, else its default edge.
  function edgeFor(id) {
    const at = locate(layout.root, id);
    const el = at && stackEls.get(at.stack);
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    const m = root.getBoundingClientRect ? root.getBoundingClientRect() : null;
    if (r && r.width && m && m.width) {
      const gaps = { left: r.left - m.left, right: m.right - r.right, top: r.top - m.top, bottom: m.bottom - r.bottom };
      return EDGES.reduce((best, e) => (gaps[e] < gaps[best] ? e : best), 'left');
    }
    if (at && at.chain.length) {
      const { split: sp, i } = at.chain[0];
      if (sp.dir === 'row') return i === 0 ? 'left' : 'right';
      return i === 0 ? 'top' : 'bottom';
    }
    return cfg.edgeOf(id);
  }
  function unpinSize(id, edge) {
    const at = locate(layout.root, id);
    const d = at && dims(at.stack);
    if (d) return edge === 'left' || edge === 'right' ? d.w : d.h;
    if (at && finite(at.stack.size)) return at.stack.size;
    return Math.max(edge === 'left' || edge === 'right' ? cfg.minSize(id).w : cfg.minSize(id).h, S.unpinSize);
  }

  const api = {
    layout: () => layout,
    config: () => cfg,
    isShown: shownNow,
    isVisible: (id) => { const w = whereIs(layout, id); return !!w && w.kind !== 'hidden'; },
    isAuto: (id) => { const w = whereIs(layout, id); return !!w && w.kind === 'auto'; },
    frontOf: (id) => { const w = whereIs(layout, id); return w && (w.kind === 'dock' || w.kind === 'float') ? w.stack.active : null; },
    onShown: (fn) => { shownFns.push(fn); },
    onChange: (fn) => { changeFns.push(fn); },
    render,
    activate(id) { if (activate(layout, id)) commit(); },
    /** Makes a panel seen: shown if hidden, brought to the front of its stack, slid out if unpinned. */
    reveal(id) {
      if (live(id)) { try { pops.get(id).win.focus(); } catch { /* ignore */ } return; }
      let changed = false;
      if (whereIs(layout, id) && whereIs(layout, id).kind === 'hidden') changed = showPanel(layout, id, opts()) || changed;
      changed = activate(layout, id) || changed;
      if (changed) commit();
      const w = whereIs(layout, id);
      if (w && w.kind === 'auto') openFly(id, false);
      if (w && w.stack && w.stack.min) { delete w.stack.min; commit(); }
    },
    setBadge(id, txt, title) {
      if (txt) badges.set(id, { text: txt, title }); else badges.delete(id);
      const tabs = [...root.querySelectorAll(`[data-dk-tab="${id}"]`)];
      if (live(id)) tabs.push(...pops.get(id).doc.querySelectorAll(`[data-dk-tab="${id}"]`));
      for (const tab of tabs) {
        let b = tab.querySelector('.' + badgeClass);
        if (!txt) { if (b) b.remove(); continue; }
        if (!b) { b = tab.ownerDocument.createElement('span'); b.className = badgeClass; tab.appendChild(b); }
        b.textContent = txt;
        b.title = title || '';
      }
    },
    setVisible(id, show) {
      if (!show && outOf(id)) { const pop = pops.get(id); if (pop) release(id, pop, true); delete layout.out[id]; }
      const changed = show ? showPanel(layout, id, opts()) : hidePanel(layout, id);
      if (changed) commit();
    },
    reset() {
      if (typeof onReset === 'function') { try { onReset(); } catch { /* the app's own */ } }
      for (const [id, pop] of [...pops]) release(id, pop, true);
      layout = cfg.defaultLayout({ viewportPx: viewport(), purpose: 'reset' });
      maxed = null;
      flyOpen = null;
      commit();
    },
    float(id) {
      const rect = floatRectFor(id);
      if (floatPanel(layout, id, rect)) { flyOpen = flyOpen === id ? null : flyOpen; commit(); }
    },
    dockBack(id) {
      const w = whereIs(layout, id);
      if (w && w.kind === 'float') { dockBack(layout, w.float, opts()); commit(); }
    },
    unpin(id) {
      const w = whereIs(layout, id);
      if (!w || w.kind === 'auto' || w.kind === 'hidden') return;
      const edge = w.kind === 'dock' ? edgeFor(id) : cfg.edgeOf(id);
      const size = w.kind === 'dock' ? unpinSize(id, edge) : (edge === 'left' || edge === 'right' ? w.float.w : w.float.h);
      if (maxed === w.stack && w.stack.panels.length === 1) maxed = null;
      unpinPanel(layout, id, edge, size, opts());
      commit();
    },
    pin(id) { if (pinPanel(layout, id, opts())) { flyOpen = null; commit(); } },
    toggleMin(id) {
      const w = whereIs(layout, id);
      if (!w || !w.stack) return;
      if (w.stack.min) delete w.stack.min; else { w.stack.min = true; if (maxed === w.stack) maxed = null; }
      commit();
    },
    toggleMax(id) {
      const w = whereIs(layout, id);
      if (!w || !w.stack) return;
      maxed = maxed === w.stack ? null : w.stack;
      if (maxed) delete w.stack.min;
      save();
      render();
    },
    restoreMax() { if (maxed) { maxed = null; render(); return true; } return false; },
    moveTo(id, target, side) { if (moveTo(layout, id, target, side, opts())) commit(); },
    dockEdge(id, side) { if (moveTo(layout, id, { kind: 'root' }, side, opts())) commit(); },
    popOut(id) { closeFly(false); return popOut(id); },
    popIn,
    /** Whether a panel is out (in its own window, or remembered as out before a reload). */
    isOut: (id) => !!outOf(id),
    /** The panel's own window while it is open; null otherwise. */
    popWindow: (id) => (pops.get(id) ? pops.get(id).win : null),
    openFly,
    closeFly,
    flyOpen: () => flyOpen,
    maximised: () => maxed,
    /** Takes the dock off the page: its windows close (and stay remembered as out), its listeners go. */
    destroy() {
      if (destroyed) return;
      leaving = true;
      destroyed = true;
      for (const t of timers) clearTimeout(t);
      timers.clear();
      if (noteEl) { noteEl.remove(); noteEl = null; }
      for (const [id, pop] of [...pops]) { rememberWindow(id, pop); release(id, pop, true); }
      for (const fn of undo.splice(0)) { try { fn(); } catch { /* ignore */ } }
      closeMenu(false);
      root.textContent = '';
    },
  };

  function act(name, id, btn) {
    const f = floatAround(btn);
    if (name === 'menu') openMenu(btn, id);
    else if (name === 'min') api.toggleMin(id);
    else if (name === 'max') api.toggleMax(id);
    else if (name === 'float') { closeFly(false); api.float(id); }
    else if (name === 'pop') api.popOut(id);
    else if (name === 'dock' && f) { dockBack(layout, f, opts()); commit(); }
    else if (name === 'unpin') api.unpin(id);
    else if (name === 'pin') api.pin(id);
    else if (name === 'hide-fly') closeFly(true);
  }

  // ---- the per-panel menu: dock at an edge, float, unpin, hide ----

  let menu = null;
  function closeMenu(refocus) {
    if (!menu) return;
    const back = menu._from;
    menu.remove();
    menu = null;
    if (refocus && back && back.isConnected) back.focus();
  }
  function openMenu(btn, id) {
    if (menu && menu._from === btn) { closeMenu(true); return; }
    closeMenu(false);
    const title = byId.get(id).title;
    const w = whereIs(layout, id);
    menu = doc.createElement('div');
    menu.className = 'dk-menu dk-mono';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', `${title}: move or hide`);
    const items = EDGES.map((e) => [`edge:${e}`, `Dock at the ${e} edge`]);
    if (w.kind === 'float') items.push(['dock', 'Dock back where it was']); else items.push(['float', 'Float']);
    items.push(['pop', 'Pop out into its own window']);
    items.push(['unpin', 'Unpin (auto-hide)'], ['hide', 'Hide (the Panels menu shows it again)']);
    menu.innerHTML = items.map(([k, label]) => `<button type="button" role="menuitem" data-dk-menu="${k}">${escText(label)}</button>`).join('');
    menu._from = btn;
    menu._id = id;
    doc.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    menu.style.top = (r.bottom + 2) + 'px';
    menu.style.left = Math.max(4, Math.min(r.right - menu.offsetWidth, viewport() - menu.offsetWidth - 4)) + 'px';
    menu.querySelector('button').focus();
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('[data-dk-menu]');
      if (!b) return;
      const k = b.dataset.dkMenu;
      const pid = menu._id;
      closeMenu(false);
      if (k.startsWith('edge:')) api.dockEdge(pid, k.slice(5));
      else if (k === 'float') api.float(pid);
      else if (k === 'pop') api.popOut(pid);
      else if (k === 'dock') api.dockBack(pid);
      else if (k === 'unpin') api.unpin(pid);
      else if (k === 'hide') api.setVisible(pid, false);
    });
    menu.addEventListener('keydown', (e) => {
      const list = [...menu.querySelectorAll('button')];
      const at = list.indexOf(doc.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        list[(at + (e.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length].focus();
      }
    });
  }

  // ---- pointer: dragging a panel, moving and sizing a float, the splitters ----

  let gesture = null; // { kind, ... } while a pointer is down on a title bar, a float's edge or a splitter
  let suppressClick = false;

  function rectOf(el) { return el.getBoundingClientRect(); }
  const inside = (r, x, y) => r && r.width > 0 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  const dirOf = (side) => (side === 'left' || side === 'right' ? 'row' : 'col');

  /** Where a panel dragged to (x, y) would dock, with the preview's rectangle; null for nowhere. */
  function dropAt(x, y, id) {
    const m = rectOf(main);
    if (!layout.root && inside(m, x, y)) return { target: { kind: 'root' }, side: 'center', rect: m };
    for (let i = layout.floats.length - 1; i >= 0; i--) {
      const f = layout.floats[i];
      const el = floatEls.get(f);
      const r = el && rectOf(el);
      if (inside(r, x, y)) return f.stack.panels.includes(id) ? null : { target: { kind: 'stack', stack: f.stack }, side: 'center', rect: r };
    }
    if (inside(m, x, y)) {
      const band = S.edgeBand;
      const quarter = (side) => ({ left: side === 'right' ? m.right - m.width / 4 : m.left, top: side === 'bottom' ? m.bottom - m.height / 4 : m.top,
        width: dirOf(side) === 'row' ? m.width / 4 : m.width, height: dirOf(side) === 'col' ? m.height / 4 : m.height });
      if (x - m.left < band) return { target: { kind: 'root' }, side: 'left', rect: quarter('left') };
      if (m.right - x < band) return { target: { kind: 'root' }, side: 'right', rect: quarter('right') };
      if (y - m.top < band) return { target: { kind: 'root' }, side: 'top', rect: quarter('top') };
      if (m.bottom - y < band) return { target: { kind: 'root' }, side: 'bottom', rect: quarter('bottom') };
    }
    for (const [node, el] of stackEls) {
      if (!layout.root || !findStack(layout.root, node)) continue;
      const r = rectOf(el);
      if (!inside(r, x, y)) continue;
      const fx = (x - r.left) / r.width;
      const fy = (y - r.top) / r.height;
      let side = 'center';
      if (fx < 0.25) side = 'left'; else if (fx > 0.75) side = 'right'; else if (fy < 0.25) side = 'top'; else if (fy > 0.75) side = 'bottom';
      if (node.panels.includes(id) && (side === 'center' || node.panels.length === 1)) return null;
      const rect = side === 'center' ? r : {
        left: side === 'right' ? r.left + r.width / 2 : r.left, top: side === 'bottom' ? r.top + r.height / 2 : r.top,
        width: dirOf(side) === 'row' ? r.width / 2 : r.width, height: dirOf(side) === 'col' ? r.height / 2 : r.height };
      return { target: { kind: 'stack', stack: node }, side, rect };
    }
    return null;
  }

  function showPreview(drop) {
    if (!drop) { preview.hidden = true; return; }
    const base = rectOf(root);
    preview.hidden = false;
    preview.dataset.side = drop.side;
    preview.style.left = (drop.rect.left - base.left) + 'px';
    preview.style.top = (drop.rect.top - base.top) + 'px';
    preview.style.width = drop.rect.width + 'px';
    preview.style.height = drop.rect.height + 'px';
  }

  function onDown(e) {
    if (e.button !== undefined && e.button !== 0) return;
    const t = e.target;
    const bar = t.closest && t.closest('.dk-bar');
    if (bar && bar._dk) { startBar(e, bar); return; }
    const rz = t.closest && t.closest('[data-dk-rz]');
    if (rz) { const f = floatAround(rz); if (f) startFloat(e, f, rz.dataset.dkRz); return; }
    const head = t.closest && t.closest('.dk-head');
    if (!head || t.closest(`.dk-ctl, ${helpSel}`)) return;
    const f = floatAround(head);
    const floatTab = f && t.closest('[data-dk-tab]');
    // A tab is the panel itself, floating or docked: dragging it takes that one panel to an edge or into another
    // stack and leaves the window's other tabs where they are. The rest of the title bar still moves the window.
    if (f && !floatTab) { startFloat(e, f, 'move'); return; }
    if (head.closest('.dk-flyout')) return;
    const node = f ? f.stack : stackAround(head);
    if (!node) return;
    const tab = t.closest('[data-dk-tab]');
    if (f) raiseFloat(f);
    gesture = { kind: 'drag', id: tab ? tab.dataset.dkTab : node.active, x0: e.clientX, y0: e.clientY, on: false, drop: null };
  }

  // The window being worked on comes to the front.
  function raiseFloat(f) {
    const i = layout.floats.indexOf(f);
    if (i < 0 || i === layout.floats.length - 1) return;
    layout.floats.splice(i, 1);
    layout.floats.push(f);
    layout.floats.forEach((x, z) => { const el = floatEls.get(x); if (el) el.style.zIndex = String(20 + z); });
  }

  function startFloat(e, f, mode) {
    const r = floatEls.get(f) ? rectOf(floatEls.get(f)) : null;
    gesture = { kind: 'float', f, mode, x0: e.clientX, y0: e.clientY, start: { x: f.x, y: f.y, w: f.w, h: f.h }, moved: false, r };
    raiseFloat(f);
    if (mode !== 'move') e.preventDefault();
  }

  function startBar(e, bar) {
    const { split: sp, i } = bar._dk;
    const a = sp.kids[i];
    const b = sp.kids[i + 1];
    const along = (node) => {
      const el = nodeEls.get(node);
      const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
      const px = r ? (sp.dir === 'row' ? r.width : r.height) : 0;
      return px || (finite(node.size) ? node.size : 1e6); // unmeasured: a child with no size of its own takes what is left
    };
    gesture = { kind: 'bar', bar, split: sp, i, a0: along(a), b0: along(b), pos0: sp.dir === 'row' ? e.clientX : e.clientY };
    bar.classList.add('on');
    if (bar.setPointerCapture && e.pointerId !== undefined) { try { bar.setPointerCapture(e.pointerId); } catch { /* ignore */ } }
    e.preventDefault();
  }

  // Resizes the two children either side of splitter `i` by `delta` px, keeping each at its minimum.
  function resizePair(sp, i, a0, b0, delta) {
    const a = sp.kids[i];
    const b = sp.kids[i + 1];
    const minA = minAlong(a, sp.dir);
    const minB = minAlong(b, sp.dir);
    const d = Math.max(minA - a0, Math.min(b0 - minB, delta));
    const fillAt = fillIndex(sp);
    if (i !== fillAt) a.size = Math.round(a0 + d);
    if (i + 1 !== fillAt) b.size = Math.round(b0 - d);
    for (const [k, node] of [[i, a], [i + 1, b]]) { const el = nodeEls.get(node); if (el) sizeKid(el, node, sp, fillAt, k); }
  }

  function onMove(e) {
    if (!gesture) return;
    if (gesture.kind === 'drag') {
      const far = Math.abs(e.clientX - gesture.x0) + Math.abs(e.clientY - gesture.y0) > 5;
      if (!gesture.on && !far) return;
      if (!gesture.on) { gesture.on = true; root.classList.add('dk-dragging'); closeFly(false); }
      gesture.drop = dropAt(e.clientX, e.clientY, gesture.id);
      showPreview(gesture.drop);
      return;
    }
    if (gesture.kind === 'bar') {
      const pos = gesture.split.dir === 'row' ? e.clientX : e.clientY;
      resizePair(gesture.split, gesture.i, gesture.a0, gesture.b0, pos - gesture.pos0);
      return;
    }
    if (gesture.kind === 'float') {
      const dx = e.clientX - gesture.x0;
      const dy = e.clientY - gesture.y0;
      if (!gesture.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      gesture.moved = true;
      const { f, mode, start } = gesture;
      const fm = S.floatMin;
      if (mode === 'move') { f.x = start.x + dx; f.y = start.y + dy; }
      if (mode === 'e' || mode === 'se') f.w = Math.max(fm.w, start.w + dx);
      if (mode === 's' || mode === 'se') f.h = Math.max(fm.h, start.h + dy);
      if (mode === 'w') { const w = Math.max(fm.w, start.w - dx); f.x = start.x + start.w - w; f.w = w; }
      if (mode === 'n') { const h = Math.max(fm.h, start.h - dy); f.y = start.y + start.h - h; f.h = h; }
      placeFloat(f);
    }
  }

  function onUp(e) {
    const g = gesture;
    gesture = null;
    if (!g) return;
    if (g.kind === 'drag') {
      root.classList.remove('dk-dragging');
      preview.hidden = true;
      if (!g.on) return;
      suppressClick = true;
      later(() => { suppressClick = false; }, 0);
      const drop = e.type === 'pointercancel' ? null : dropAt(e.clientX, e.clientY, g.id);
      if (drop && moveTo(layout, g.id, drop.target, drop.side, opts())) commit();
      return;
    }
    if (g.kind === 'bar') {
      g.bar.classList.remove('on');
      if (e.type !== 'pointercancel' && finite(e.clientX)) resizePair(g.split, g.i, g.a0, g.b0, (g.split.dir === 'row' ? e.clientX : e.clientY) - g.pos0);
      save();
      return;
    }
    if (g.kind === 'float' && g.moved) {
      suppressClick = true;
      later(() => { suppressClick = false; }, 0);
      // Dropped partly off the dock: at least the title bar stays in reach until the next reload or resize puts it
      // wholly back.
      const ext = extent();
      const f = g.f;
      f.x = Math.round(Math.max(-(f.w - 60), Math.min(f.x, ext.w - 60)));
      f.y = Math.round(Math.max(0, Math.min(f.y, ext.h - S.head)));
      f.w = Math.round(f.w); f.h = Math.round(f.h);
      placeFloat(f);
      save();
    }
  }

  // Something inside a panel asks to be seen (a dock-reveal event bubbling from it): its panel comes on screen.
  on(root, 'dock-reveal', (e) => {
    const el = e.target.closest && e.target.closest('.dk-panel');
    const p = el && panels.find((x) => x.el === el);
    if (p) api.reveal(p.id);
  });
  on(root, 'pointerdown', onDown);
  on(doc, 'pointermove', onMove);
  on(doc, 'pointerup', onUp);
  on(doc, 'pointercancel', onUp);

  on(root, 'click', (e) => {
    if (suppressClick) { e.preventDefault(); e.stopPropagation(); suppressClick = false; return; }
    const t = e.target;
    const pb = t.closest('[data-dk-pop]');
    const ph = pb && pb.closest('[data-dk-popped]');
    if (ph) {
      const id = ph.dataset.dkPopped;
      const k = pb.dataset.dkPop;
      if (k === 'show') api.reveal(id);
      else if (k === 'back') popIn(id);
      else if (k === 'reopen') popOut(id);
      return;
    }
    const b = t.closest('[data-dk-act]');
    if (b) {
      const fly = b.closest('.dk-flyout');
      const node = stackAround(b);
      const id = fly ? fly.dataset.dkFly : node && node.active;
      if (id) act(b.dataset.dkAct, id, b);
      return;
    }
    const tab = t.closest('[data-dk-tab]');
    if (tab && !t.closest(helpSel)) { api.activate(tab.dataset.dkTab); return; }
    const strip = t.closest('[data-dk-auto]');
    if (strip) {
      const id = strip.dataset.dkAuto;
      if (flyOpen === id && !flyByHover) closeFly(false);
      else {
        openFly(id, false);
        const fly = flyEls.get(id);
        const first = fly && fly.querySelector('.dk-body button, .dk-body [tabindex], .dk-body input, .dk-head button');
        if (first && first.focus) first.focus();
      }
    }
  });

  on(root, 'dblclick', (e) => {
    const head = e.target.closest('.dk-head');
    if (!head || e.target.closest(`.dk-ctl, ${helpSel}`) || head.closest('.dk-flyout')) return;
    // By the panel's name, not its element: the first click of the two may have redrawn the title bar.
    const tabEl = e.target.closest('[data-dk-tab]');
    const node = stackAround(head);
    const id = tabEl ? tabEl.dataset.dkTab : node && node.active;
    const w = id && whereIs(layout, id);
    if (w && w.kind === 'float') { dockBack(layout, w.float, opts()); commit(); }
    else if (w && w.kind === 'dock') api.toggleMax(id);
  });
  on(root, 'dblclick', (e) => {
    const bar = e.target.closest('.dk-bar');
    if (!bar || !bar._dk) return;
    const { split: sp, i } = bar._dk;
    const fillAt = fillIndex(sp);
    const ext = extent();
    for (const k of [i, i + 1]) {
      if (k === fillAt) continue;
      const node = sp.kids[k];
      const px = cfg.defaultSize ? cfg.defaultSize(panelsUnder(node), sp.dir, { viewportPx: viewport(), extent: ext }) : null;
      node.size = finite(px) ? px : Math.round((sp.dir === 'row' ? ext.w : ext.h) / (sp.kids.length));
    }
    commit();
  });

  on(root, 'keydown', (e) => {
    const bar = e.target.closest && e.target.closest('.dk-bar');
    if (!bar || !bar._dk) return;
    const { split: sp, i } = bar._dk;
    const row = sp.dir === 'row';
    const step = e.key === (row ? 'ArrowRight' : 'ArrowDown') ? S.keyStep : e.key === (row ? 'ArrowLeft' : 'ArrowUp') ? -S.keyStep : 0;
    if (!step) return;
    e.preventDefault();
    const along = (node) => { const d = dims(node); return d ? (row ? d.w : d.h) : (finite(node.size) ? node.size : 1e6); };
    resizePair(sp, i, along(sp.kids[i]), along(sp.kids[i + 1]), step);
    save();
  });

  // Hover slides an unpinned panel out; leaving it, or focus leaving it, slides it back.
  on(root, 'pointerover', (e) => {
    const strip = e.target.closest && e.target.closest('[data-dk-auto]');
    if (strip && !gesture) {
      clearTimeout(hoverTimer);
      const id = strip.dataset.dkAuto;
      hoverTimer = later(() => { if (flyOpen !== id) openFly(id, true); }, 250);
      return;
    }
    if (insideFly(e.target)) clearTimeout(leaveTimer);
  });
  on(root, 'pointerout', (e) => {
    const from = e.target.closest && e.target.closest('[data-dk-auto]');
    if (from) clearTimeout(hoverTimer);
    if (!flyOpen || !insideFly(e.target) || insideFly(e.relatedTarget)) return;
    clearTimeout(leaveTimer);
    leaveTimer = later(() => {
      if (!flyOpen) return;
      if (flyEls.get(flyOpen) && flyEls.get(flyOpen).contains(doc.activeElement)) return; // the person is working in it
      closeFly(false);
    }, 400);
  });
  on(root, 'focusout', (e) => {
    if (!flyOpen || !insideFly(e.target)) return;
    later(() => {
      if (!flyOpen || gesture) return;
      const now = doc.activeElement;
      if (now && now !== doc.body && insideFly(now)) return;
      if (flyByHover && (!now || now === doc.body)) return; // opened by hover: the pointer decides
      closeFly(false);
    }, 0);
  });
  on(doc, 'pointerdown', (e) => {
    if (flyOpen && !insideFly(e.target) && !(menu && menu.contains(e.target)) && !(e.target.closest && e.target.closest(modalSelector))) closeFly(false);
    if (menu && !menu.contains(e.target) && e.target !== menu._from && !(menu._from && menu._from.contains(e.target))) closeMenu(false);
  }, true);

  on(doc, 'keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (gesture && gesture.kind === 'drag') { gesture = null; preview.hidden = true; root.classList.remove('dk-dragging'); return; }
    if (e.target && e.target.closest && e.target.closest(`${modalSelector}, .dk-menu`)) return;
    if (flyOpen) { closeFly(true); return; }
    api.restoreMax();
  });

  if (win && win.addEventListener) {
    // The page going away (closed, reloaded, navigated): its windows close with it, so none is left showing stale
    // state or holding buttons. Which panels were out, and where, stays remembered for the next load.
    on(win, 'pagehide', () => {
      leaving = true;
      for (const [id, pop] of pops) rememberWindow(id, pop);
      for (const pop of pops.values()) { try { pop.win.close(); } catch { /* ignore */ } }
    });
    // Back from the browser's page cache: those windows are gone; their places offer to open them again.
    on(win, 'pageshow', (e) => {
      if (!e || !e.persisted) return;
      leaving = false;
      for (const [id, pop] of [...pops]) release(id, pop, true);
      render();
    });
    if (themeEvent) on(win, themeEvent, syncThemes);
    on(win, 'resize', () => {
      const ext = extent();
      for (const f of layout.floats) { clampFloat(f, ext.w, ext.h, S.floatMin); placeFloat(f); }
      for (const a of layout.auto) placeFlyout(a);
      fitAll();
      closeMenu(false);
    });
  }
  // A theme switched by any means (an attribute or class on <html>) reaches the windows, event or not.
  const MO = doc.defaultView && doc.defaultView.MutationObserver;
  if (MO && themeAttrs.length) {
    const mo = new MO(syncThemes);
    mo.observe(doc.documentElement, { attributes: true, attributeFilter: themeAttrs });
    undo.push(() => mo.disconnect());
  }

  render();
  save();
  return api;
}

export { LAYOUT_VERSION };
