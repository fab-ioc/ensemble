// The dock on the page. Each panel has a title bar; it can be dragged to an edge or into another panel as a tab,
// floated, unpinned to a strip on its edge, minimised or maximised, or popped out into a browser window of its own.
// The layout (layout.js) is remembered in the storage the app gives.
//
// Its controls follow IntelliJ's tool windows (v0.5.0): a title bar holds its tabs, ⋯ (Options) and − (Hide). Options
// has View Mode (Dock Pinned: docked; Dock Unpinned: on its strip, sliding out beside the middle; Undock: on its strip,
// sliding out over it; Float; Window: a browser window of its own), Move To (the side), Maximise and Hide. A panel's
// side is kept through every change of mode (layout.js panelSide). headButtons: 'classic' gives the buttons of v0.4.
//
// The panels' own elements are moved between frames, never rebuilt: what they show, their listeners and their state stay
// theirs. Scroll positions are carried across a move.
//
// A popped-out panel's element moves into that window's document (same origin), so it is still the one panel with the
// one set of listeners and state: nothing is drawn twice and an action goes out once. It leaves the layout while it is
// out (its tab goes, or its stack, and the neighbours take the room); `out` remembers where it was, and its window's
// screen position and size, and it goes back there. The Panels menu reaches it meanwhile. After a reload a small notice
// offers to open its window again (a browser opens one only on a click) or to bring it back.

import { addHostDoc, removeHostDoc, whenGone } from './host.js';
import {
  EDGES, LAYOUT_VERSION, makeConfig, stackNode, locate, panelsUnder, contains, findStack, whereIs, isShownIn,
  moveTo, floatPanel, dockBack, unpinPanel, pinPanel, hidePanel, showPanel, activate, normalizeLayout, clampFloat,
  popOutPanel, popInPanel, panelSide, viewModeOf, moveSide, dockFromFloat,
} from './layout.js';
import { POP_HTML } from './popout-page.js';

export const LAYOUT_KEY = 'dock.layout';
export const POP_URL = 'popout.html'; // beside the app's page: same origin, nothing in the URL
export const POP_ROOT_ID = 'dk-pop-root';
export const THEME_EVENT = 'dock-theme';
export const THEME_ATTRS = ['data-theme', 'data-scheme', 'class', 'style'];
const POP_WAIT_MS = 15000;

/** The words the dock shows. Any of them can be replaced through createDock's `text` option. */
export const TEXT = {
  empty: 'Every panel is floating, unpinned or hidden: the Panels menu shows them.',
  // the Panels menu's, for a panel that is out (panels-menu.js reads them)
  popTag: 'in its own window',
  popShow: 'Show window',
  popOpen: 'Open window',
  popBack: 'Bring back',
  popWas: (t) => `${t} was in its own window before this page was reloaded.`,
  popReopen: 'Open its window again',
  popReopenTitle: 'open it in its own window where it was',
  popKeep: 'Bring it back here',
  popKeepTitle: 'put it back where it was in this window',
  popNoteClose: 'close this note (the Panels menu still has these panels)',
  popBlocked: (t) => `The browser did not open a window for ${t}: allow pop-ups for this page, then pop it out again.`,
  popFailed: (t) => `The window for ${t} did not load; it is back here.`,
  backToMain: 'Back to main window',
  backToMainTitle: 'put this panel back where it was in the main window and close this one',
  // the title bar and its Options menu (IntelliJ's words)
  options: 'Options',
  hide: 'Hide',
  viewMode: 'View Mode',
  moveTo: 'Move To',
  maximise: 'Maximise',
  restore: 'Restore',
  modes: { pinned: 'Dock Pinned', unpinned: 'Dock Unpinned', undock: 'Undock', float: 'Float', window: 'Window' },
  // what each view mode does: the tooltip of its menu item
  modeHints: {
    pinned: 'Docked; stays open',
    unpinned: 'Docked on its edge; hides when you click elsewhere',
    undock: 'Over the content; hides when you click elsewhere',
    float: 'A free window in the page; stays open',
    window: 'Its own browser window',
  },
  sides: { left: 'Left', right: 'Right', top: 'Top', bottom: 'Bottom' },
};
/** The view modes, in the order the Options menu lists them (IntelliJ's). */
export const VIEW_MODES = ['pinned', 'unpinned', 'undock', 'float', 'window'];

const ICON = {
  menu: '<circle cx="3" cy="6" r="1"/><circle cx="6" cy="6" r="1"/><circle cx="9" cy="6" r="1"/>',
  min: '<path d="M2.5 9.5h7"/>',
  hide: '<path d="M2.5 6h7"/>', // IntelliJ's −
  unmin: '<path d="M2.5 7.5 6 4l3.5 3.5"/>',
  max: '<rect x="2" y="2" width="8" height="8"/>',
  restore: '<rect x="2" y="4" width="6" height="6"/><path d="M4 4V2h6v6H8"/>',
  float: '<rect x="1.5" y="4.5" width="6" height="6"/><path d="M5.5 1.5h5v5M10.5 1.5 6 6"/>',
  dock: '<rect x="1.5" y="1.5" width="9" height="9"/><path d="M1.5 7.5h9"/>',
  pin: '<path d="M4 1.5h4M5 1.5v4L3 7.5h6L7 5.5v-4M6 7.5V11"/>',
  unpin: '<path d="M4 1.5h4M5 1.5v4L3 7.5h6L7 5.5v-4M6 7.5V11" transform="rotate(45 6 6)"/>',
  pop: '<rect x="1.5" y="1.5" width="9" height="6.5"/><path d="M4 10.5h4M6 8v2.5"/>', // a monitor: a window of its own
  back: '<path d="M5 2.5 1.5 6 5 9.5M1.5 6h9"/>', // an arrow home
  close: '<path d="M3 3l6 6M9 3l-6 6"/>',
};
const icon = (name) => `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">${ICON[name]}</svg>`;
export const escText = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const finite = (n) => typeof n === 'number' && Number.isFinite(n);
let docks = 0;
const report = (e) => { try { (globalThis.reportError || console.error)(e); } catch { /* ignore */ } };
const CSS_ESC = (s) => String(s).replace(/["\\]/g, '\\$&');
// The dock never scrolls anything to show what it focuses: a panel sliding out is still off to the side when it is
// focused, and scrolling the dock (or the page) to it would leave the whole dock shifted.
function focusQuiet(el) { try { el.focus({ preventScroll: true }); } catch { /* ignore */ } }
const idPart = (s) => String(s).replace(/[^\w-]/g, '_');

// Puts `el` into `parent` before `before` (null: last), unless it is there already. Where the browser has
// Element.moveBefore (Chrome 133+), an element that stays in the document moves with it: its iframes do not reload, and
// its focus, selection and animations stay. Otherwise, and between documents, it is taken out and put back.
function put(parent, el, before) {
  if (el.parentNode === parent && el.nextSibling === before) return;
  if (typeof parent.moveBefore === 'function' && el.isConnected && parent.isConnected && el.ownerDocument === parent.ownerDocument) {
    try { parent.moveBefore(el, before); return; } catch { /* taken out and put back, below */ }
  }
  parent.insertBefore(el, before);
}
// Puts `kids` into `parent` in this order, moving as few as it can: the longest run of them already in order stays
// where it is (a splitter counts for little, a box holding panels for much), and the others move in around it. Other
// children of `parent` are stale; the caller takes them away once everything has moved (they may still hold something
// that moves elsewhere).
function arrange(parent, kids) {
  const cur = new Map([...parent.children].map((c, i) => [c, i]));
  const n = kids.length;
  const pos = kids.map((k) => (cur.has(k) ? cur.get(k) : -1));
  const weight = (k) => (k.classList && k.classList.contains('dk-bar') ? 0.01 : 1);
  const best = new Array(n).fill(0);
  const prev = new Array(n).fill(-1);
  let end = -1;
  for (let i = 0; i < n; i++) {
    if (pos[i] < 0) continue;
    best[i] = weight(kids[i]);
    for (let j = 0; j < i; j++) {
      if (pos[j] >= 0 && pos[j] < pos[i] && best[j] + weight(kids[i]) > best[i]) { best[i] = best[j] + weight(kids[i]); prev[i] = j; }
    }
    if (end < 0 || best[i] > best[end]) end = i;
  }
  const keep = new Set();
  for (let i = end; i >= 0; i = prev[i]) keep.add(kids[i]);
  let next = null;
  for (let i = n - 1; i >= 0; i--) {
    if (!keep.has(kids[i])) put(parent, kids[i], next);
    next = kids[i];
  }
}

/**
 * A page's text with `<base href="base">` first in its head, so its relative URLs resolve against `base`, as the
 * browser's own parser reads the page (`Parser`: a DOMParser). A page with a `<base href>` of its own that works (an
 * HTML one, in the document: not in a <template>, svg or math) is returned as it is, and so is any page where there is
 * no DOMParser (Node). The page is written back from its parsed document with its own doctype, so it keeps its mode;
 * what lies outside <html> (a comment before it) is not kept.
 */
export function withBase(html, base, Parser = globalThis.DOMParser) {
  if (typeof Parser !== 'function') return html;
  let d;
  try { d = new Parser().parseFromString(html, 'text/html'); } catch { return html; }
  if (!d || !d.head || !d.documentElement) return html;
  if ([...d.querySelectorAll('base[href]')].some((b) => b.namespaceURI === 'http://www.w3.org/1999/xhtml')) return html;
  const tag = d.createElement('base');
  tag.setAttribute('href', base);
  d.head.insertBefore(tag, d.head.firstChild);
  return doctypeOf(d.doctype) + d.documentElement.outerHTML;
}
// A doctype as markup: `<!DOCTYPE html>`, with its public and system ids if it has them (they decide the mode).
function doctypeOf(t) {
  if (!t) return '';
  const q = (s) => `"${String(s).replace(/"/g, '')}"`;
  const ids = t.publicId ? ` PUBLIC ${q(t.publicId)}${t.systemId ? ' ' + q(t.systemId) : ''}` : t.systemId ? ` SYSTEM ${q(t.systemId)}` : '';
  return `<!DOCTYPE ${t.name || 'html'}${ids}>`;
}

function defaultStorage() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
}

/**
 * A layout as one column: every panel it shows (docked, floating, unpinned or out) becomes a tab of one stack, in that
 * order; hidden panels stay hidden. The front tab is the one that was in front of a one-stack layout, else `prefer`
 * (the fill panel), else the first. Changes `l` in place and returns it; one stack already is left as it is.
 */
export function oneColumn(l, prefer) {
  const out = Object.keys(l.out || {});
  const one = l.root && l.root.t === 'stack';
  if ((one || !l.root) && !l.floats.length && !l.auto.length && !out.length) return l;
  const order = [...panelsUnder(l.root), ...l.floats.flatMap((f) => f.stack.panels), ...l.auto.map((a) => a.id), ...out];
  const front = one ? l.root.active : prefer && order.includes(prefer) ? prefer : order[0];
  l.root = order.length ? stackNode(order, front) : null;
  l.floats = [];
  l.auto = [];
  l.out = {};
  return l;
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
 * Mounts the dock in `root`. `panels` are [{ id, title, help?, el, icon?, unpinSize? }] (each `el` is the panel's own
 * element; `icon`, an SVG string or element, is its strip button; `unpinSize`, px, how far it slides out of its strip).
 *
 * Options (see the README for each):
 *   storage, storageKey, migrate             where the layout is kept (any { getItem, setItem, removeItem })
 *   defaultLayout, minSize, edgeOf, fill, defaultSize, sizes    the layout's defaults (layout.js makeConfig)
 *   popUrl, popName, popTitle, copyStyles, openWindow           pop-out windows
 *   popHtml, popBase                        a pop-out page without a served file, and the base of its relative URLs
 *   popBackButton                           the pop-out's "Back to main window" button (default true)
 *   themeAttrs, themeEvent                  what of the main page's <html> a pop-out window copies, and when
 *   help: { icon(key, panel), mount(doc), selector }            a panel's help control, and its popovers in a window
 *   minClickRestores                        a click on a minimised panel's title bar restores it (default true)
 *   stripHover, stripOpen                   hovering a strip button slides its panel out (default true); a panel slid
 *                                           out lies 'over' the layout (default) or 'beside' it, the middle narrowing
 *                                           (a panel's own View Mode, Dock Unpinned or Undock, overrides it)
 *   stripAutoHide                           a panel slid out slides back when focus or a click goes elsewhere in the
 *                                           page (default true, IntelliJ's); false: v0.5.0's, one opened by a click
 *                                           stays (click-only, or beside) until closed
 *   headButtons                             'menu' (default): a title bar has ⋯ and −; 'classic': v0.4's buttons
 *   modalSelector, badgeClass, text, onReset, win
 *
 * Returns the dock's handle: layout(), isShown(id), isVisible(id), isAuto(id), frontOf(id), activate(id), reveal(id),
 * setBadge(id, text, title), onShown(fn), onChange(fn), setVisible(id, on), reset(), float(id), dockBack(id), unpin(id),
 * pin(id), toggleMin(id), toggleMax(id), restoreMax(), moveTo(id, target, side), dockEdge(id, side), popOut(id),
 * popIn(id), isOut(id), popWindow(id), openFly(id), closeFly(), flyOpen(), maximised(), viewMode(id),
 * setViewMode(id, mode), side(id), moveSide(id, side), render(), destroy().
 */
export function createDock({ root, panels, storageKey = LAYOUT_KEY, key, storage = defaultStorage(),
  win = typeof window !== 'undefined' ? window : null, popUrl = POP_URL, popName = 'dock-panel-', popTitle = (p) => p.title,
  copyStyles = true, themeAttrs = THEME_ATTRS, themeEvent = THEME_EVENT, help = {}, modalSelector = '[role="dialog"], .dk-help-pop',
  badgeClass = 'dk-badge', text = {}, onReset = null, migrate = null,
  defaultLayout, minSize, edgeOf, fill, defaultSize, sizes,
  narrow = false, narrowLayout, narrowKey, can = null, popHtml = null, popBase, minClickRestores = true, popBackButton = true,
  stripHover = true, stripOpen = 'over', stripAutoHide = true, headButtons = 'menu',
  openWindow = (url, name, features) => (win && typeof win.open === 'function' ? win.open(url, name, features) : null) }) {
  const doc = root.ownerDocument;
  const T = { ...TEXT, ...text };
  const layoutKey = key || storageKey;
  const ids = panels.map((p) => p.id);
  // A panel's own unpinSize: how far it slides out of its strip when nothing else says (layout.js cfg.unpinSizeOf).
  const unpinSizes = Object.fromEntries(panels.filter((p) => Number.isFinite(p.unpinSize) && p.unpinSize > 0).map((p) => [p.id, p.unpinSize]));
  const ownUnpin = Object.keys(unpinSizes).length ? unpinSizes : undefined;
  const wideCfg = makeConfig({ ids, defaultLayout, minSize, edgeOf, fill, defaultSize, sizes, unpinSize: ownUnpin });
  // Narrow (a phone): one column, every panel a tab of one stack, and no control or gesture that moves a panel. It has a
  // layout of its own, kept under its own key, so the wide one is there again when the window is wide again.
  const narrowCfg = makeConfig({ ids, minSize, edgeOf, fill, defaultSize, sizes, unpinSize: ownUnpin,
    defaultLayout: narrowLayout || ((ctx) => oneColumn(wideCfg.defaultLayout(ctx), fill)) });
  const narrowStoreKey = narrowKey || layoutKey + '.narrow';
  let narrowOn = !!narrow;
  let cfg = narrowOn ? narrowCfg : wideCfg;
  const S = cfg.sizes;
  // A strip panel slid out beside the layout (stripOpen: 'beside', or its own View Mode Dock Unpinned) takes room from
  // it. Click-only (stripHover: false): no hover opens one, nor does the pointer leaving close one.
  const openStyle = stripOpen === 'beside' ? 'beside' : 'over';
  const besideOf = (a) => !!a && (a.open || openStyle) === 'beside';
  const classic = headButtons === 'classic';
  const popPage = popHtml === true ? POP_HTML : typeof popHtml === 'string' ? popHtml : null;
  /** Whether a person may do `action` to panel `id`: move, float, unpin, pop, max, min, hide. Narrow allows only hide. */
  function allowed(id, action) {
    if (narrowOn && action !== 'hide') return false;
    if (typeof can !== 'function') return true;
    try { return can(id, action) !== false; } catch { return true; }
  }
  const helpSel = help.selector || '[data-help]';
  const byId = new Map(panels.map((p) => [p.id, p]));
  const panelEls = new Set(panels.map((p) => p.el));
  const badges = new Map();
  const shownFns = [];
  const changeFns = [];
  const popInFns = [];
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
  const empty = doc.createElement('div');
  empty.className = 'dk-empty dk-mono';
  const uid = 'dk' + (++docks); // ids of tabs, unique in the page
  root.classList.add('dock');
  root.classList.toggle('dk-narrow', narrowOn);
  // Click-only: the strip lies above a panel sliding in or out, so a quick second click on its button reaches it.
  root.classList.toggle('dk-strip-click', !stripHover);
  for (const n of [...root.childNodes]) if (n.nodeType !== 1) n.remove(); // the drawing keeps elements only
  // The sizes the stylesheet draws with are the ones the layout counts with: on the dock's root, and on a popped-out
  // window's (#dk-pop-root, not its <html>, whose style attribute the theme copies over). They are fixed for the dock's life.
  function putSizes(el) {
    el.style.setProperty('--dk-head', S.head + 'px');
    el.style.setProperty('--dk-bar', S.bar + 'px');
    el.style.setProperty('--dk-strip', S.strip + 'px');
  }
  putSizes(root);

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

  function storeKey() { return narrowOn ? narrowStoreKey : layoutKey; }
  function load() {
    let raw = null;
    try { const s = storage && storage.getItem(storeKey()); raw = s ? JSON.parse(s) : null; } catch { raw = null; }
    const l = normalizeLayout(raw, { cfg, viewportPx: viewport(), migrate });
    return narrowOn ? oneColumn(l) : l;
  }
  function save() {
    try { if (storage) storage.setItem(storeKey(), JSON.stringify(layout)); } catch { /* not remembered; still applied */ }
  }
  function commit() {
    if (narrowOn) oneColumn(layout);
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
  const tabId = (id) => `${uid}-tab-${idPart(id)}`;
  // A stack's tabs are a tablist with a roving tabindex: the front tab is the one Tab reaches, the arrow keys, Home and
  // End move along them. Its panel's element is the tabpanel. `iconic` (a minimised stack's bar): a panel with an icon
  // shows it, as its strip button does, with its title as tooltip and name.
  function tabHtml(id, active, draggable, iconic = false) {
    const p = byId.get(id);
    const b = badges.get(id);
    const ic = iconic ? iconHtml(p) : '';
    return `<span class="dk-tab-wrap${active ? ' on' : ''}" role="presentation"><button type="button" class="dk-tab${active ? ' on' : ''}${ic ? ' dk-tab-iconic' : ''}" role="tab" id="${tabId(id)}" data-dk-tab="${escText(id)}"`
      + ` aria-selected="${active}" tabindex="${active ? 0 : -1}"${p.el.id ? ` aria-controls="${escText(p.el.id)}"` : ''}`
      + `${ic ? ` aria-label="${escText(p.title)}"` : ''}`
      + ` title="${escText(p.title)}${draggable ? ': drag to dock it at an edge or into another panel as a tab' : ''}">`
      + `${ic ? `<span class="dk-tab-icon" aria-hidden="true">${ic}</span>` : escText(p.title)}${b ? `<span class="${badgeClass}" title="${escText(b.title || '')}">${escText(b.text)}</span>` : ''}</button>`
      + `${helpHtml(p)}</span>`;
  }
  // A panel's icon as markup (the app's own SVG string, or an element's), '' for none.
  function iconHtml(p) {
    if (!p.icon) return '';
    if (typeof p.icon === 'string') return p.icon;
    return p.icon.nodeType === 1 ? p.icon.outerHTML : '';
  }
  const ctl = (act, name, label, title) => `<button type="button" class="dk-btn" data-dk-act="${act}" aria-label="${escText(label)}" title="${escText(title)}">${icon(name)}</button>`;
  function asTabPanel(id, front) {
    const el = byId.get(id).el;
    el.classList.add('dk-panel');
    el.classList.toggle('dk-off', !front);
    if (!front) el.setAttribute('aria-hidden', 'true'); else el.removeAttribute('aria-hidden');
    if (!el.hasAttribute('role') || el.dataset.dkRole !== undefined) { el.setAttribute('role', 'tabpanel'); el.dataset.dkRole = ''; }
    if (el.dataset.dkRole !== undefined) el.setAttribute('aria-labelledby', tabId(id));
    return el;
  }

  // Whether a floating panel was floated from a strip: Dock back takes it back to its strip.
  function fromStrip(id) {
    const w = whereIs(layout, id);
    return !!(w && w.kind === 'float' && w.float.strip && w.float.strip.id === id);
  }
  // The per-panel menu's items, as far as `can` (and narrow) allow them.
  function menuItems(id, where) {
    const items = allowed(id, 'move') ? EDGES.map((e) => [`edge:${e}`, `Dock at the ${e} edge`]) : [];
    if (allowed(id, 'float')) items.push(where === 'float' ? ['dock', fromStrip(id) ? 'Back to its strip' : 'Dock back where it was'] : ['float', 'Float']);
    if (allowed(id, 'pop')) items.push(['pop', 'Pop out into its own window']);
    if (allowed(id, 'unpin')) items.push(['unpin', 'Unpin (auto-hide)']);
    if (allowed(id, 'hide') && !narrowOn) items.push(['hide', 'Hide (the Panels menu shows it again)']);
    return items;
  }

  // ---- IntelliJ's model: view modes and sides ----
  const modeOf = (id) => viewModeOf(layout, id, openStyle);
  // What a change of view mode needs `can` to allow: moving onto or off a strip 'unpin', into or out of a float
  // 'float', into or out of a window 'pop'.
  const NEEDS = { unpinned: 'unpin', undock: 'unpin', float: 'float', window: 'pop' };
  function modeOk(id, from, to) {
    if (narrowOn || !from || from === 'hidden') return false;
    if (from === to) return true;
    return [NEEDS[from], NEEDS[to]].filter(Boolean).every((a) => allowed(id, a));
  }
  // The Options menu of panel `id` (where: 'dock', 'float' or 'fly'): the view modes it may go to (its own checked),
  // the sides (Move To), Maximise and Hide, as far as `can` and narrow allow. Empty parts are left out.
  function optionsOf(id, where) {
    const now = modeOf(id);
    const modes = VIEW_MODES.filter((m) => modeOk(id, now, m));
    const o = { modes: modes.length > 1 ? modes : [], now, sides: [], side: null, max: null, hide: null };
    const w = whereIs(layout, id);
    // The middle (the stack holding cfg.fill) is on no side: Move To offers the four, none checked.
    const middle = w && w.kind === 'dock' && cfg.fill && w.stack.panels.includes(cfg.fill);
    if (allowed(id, 'move')) { o.sides = EDGES.slice(); o.side = middle ? null : panelSide(layout, id, opts()); }
    if (where !== 'fly' && w && w.stack && allowed(id, 'max')) o.max = maxed === w.stack ? T.restore : T.maximise;
    if (hideOk(id, where)) o.hide = where !== 'fly' && w && w.stack && w.stack.min ? T.restore : T.hide;
    o.any = !!(o.modes.length || o.sides.length || o.max);
    return o;
  }
  // − hides a panel as IntelliJ's does, to what stands for its stripe icon: a strip panel slides back in; a docked or
  // floating one is minimised to its title bar (which shows its icon, and a click brings it back).
  function hideOk(id, where) { return where === 'fly' || allowed(id, 'min'); }

  function headHtml(node, where) {
    const id = node.active;
    const t = byId.get(id).title;
    const tabs = node.panels.map((pid) => tabHtml(pid, pid === id, where === 'dock' && allowed(pid, 'move'), !!node.min)).join('');
    const isMax = maxed === node;
    const c = [];
    if (!classic) {
      if (optionsOf(id, where).any) c.push(ctl('menu', 'menu', `${t}: ${T.options}`, `${T.viewMode}, ${T.moveTo}, ${T.maximise}, ${T.hide}`));
      if (hideOk(id, where)) c.push(node.min ? ctl('hide', 'unmin', `${t}: ${T.restore}`, 'restore from its title bar')
        : ctl('hide', 'hide', `${t}: ${T.hide}`, 'hide it to its title bar (a click on it brings it back)'));
      return `<div class="dk-tabs" role="tablist" aria-label="${escText(t)}">${tabs}</div><span class="dk-ctl">${c.join('')}</span>`;
    }
    if (menuItems(id, where).length) c.push(ctl('menu', 'menu', `${t}: move or hide`, 'dock at an edge, float, unpin or hide'));
    if (allowed(id, 'min')) c.push(node.min ? ctl('min', 'unmin', `${t}: restore`, 'restore from its title bar') : ctl('min', 'min', `${t}: minimise`, 'minimise to its title bar'));
    if (allowed(id, 'max')) c.push(isMax ? ctl('max', 'restore', `${t}: restore size`, 'restore (Esc)') : ctl('max', 'max', `${t}: maximise`, 'maximise (Esc restores)'));
    if (allowed(id, 'pop')) c.push(ctl('pop', 'pop', `${t}: pop out`, 'pop out into a browser window of its own (drag it to another monitor)'));
    if (allowed(id, 'float')) {
      if (where === 'float' && fromStrip(id)) c.push(ctl('dock', 'dock', `${t}: back to its strip`, 'back to its strip, where it was (or double-click the title bar)'));
      else if (where === 'float') c.push(ctl('dock', 'dock', `${t}: dock back`, 'dock back where it was (or double-click the title bar)'));
      else c.push(ctl('float', 'float', `${t}: float`, 'float in its own window'));
    }
    if (allowed(id, 'unpin')) c.push(ctl('unpin', 'pin', `${t}: unpin`, 'pinned: unpin to a strip on its edge (auto-hide)'));
    return `<div class="dk-tabs" role="tablist" aria-label="${escText(t)}">${tabs}</div><span class="dk-ctl">${c.join('')}</span>`;
  }

  // ---- the elements kept from one drawing to the next ----
  // A drawing reuses the elements of the last one: a stack's section (its title bar and body), a split's box, a
  // floating window, an unpinned panel's flyout. A panel's element moves only when the element it is in changes, and
  // then with moveBefore where the browser has it (see put). An element is reused for the same layout node, else for
  // the node that holds the panels it held (layout changes keep most nodes, reset and a reload make new ones).
  const secOf = new WeakMap(); // stack node -> its kept section's parts
  const splitOf = new WeakMap(); // split node -> its box
  const floatOf = new WeakMap(); // float -> its window
  let secParts = new Map(); // section -> { sec, head, body, html } of the last drawing
  let bodyParts = new Map(); // a section's body -> the same
  let splitBoxes = new Set(); // the split boxes of the last drawing
  let floatBoxes = new Set(); // the floating windows of the last drawing
  const flyKept = new Map(); // unpinned panel id -> { el, head, body, grip, html }

  function stackFor(node, where, claimed) {
    let parts = secOf.get(node);
    if (!parts || claimed.has(parts.sec) || !secParts.has(parts.sec)) parts = null;
    if (!parts) {
      for (const id of [node.active, ...node.panels]) {
        const p = bodyParts.get(byId.get(id).el.parentNode);
        if (p && !claimed.has(p.sec)) { parts = p; break; }
      }
    }
    if (!parts) {
      const sec = doc.createElement('section');
      const head = doc.createElement('div');
      head.className = 'dk-head dk-mono';
      const body = doc.createElement('div');
      body.className = 'dk-body';
      parts = { sec, head, body, html: null };
    }
    claimed.add(parts.sec);
    secOf.set(node, parts);
    const { sec, head } = parts;
    sec.className = 'dk-stack' + (node.min ? ' dk-min' : '') + (maxed === node ? ' dk-max' : '');
    sec.setAttribute('aria-label', byId.get(node.active).title);
    for (const k of ['minWidth', 'minHeight', 'flex']) sec.style[k] = '';
    const html = headHtml(node, where);
    if (parts.html !== html) { head.innerHTML = html; parts.html = html; }
    parts.node = node;
    stackEls.set(node, sec);
    return parts;
  }
  function arrangeStack(parts) {
    settle(parts.sec, [parts.head, parts.body]);
    settle(parts.body, parts.node.panels.map((id) => asTabPanel(id, id === parts.node.active)));
    return [parts.sec, parts.body];
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

  // The first pass, from the leaves up: which element each node of the tree gets (nothing moves yet).
  const plan = new Map(); // node -> { el, parts?, bars? } for this drawing
  function claimNode(node, claimed) {
    if (node.t === 'stack') {
      const parts = stackFor(node, 'dock', claimed);
      plan.set(node, { el: parts.sec, parts });
      return parts.sec;
    }
    const kids = node.kids.map((k) => claimNode(k, claimed));
    let el = splitOf.get(node);
    if (!el || claimed.has(el) || !splitBoxes.has(el)) el = null;
    if (!el) el = kids.map((k) => k.parentNode).find((p) => p && splitBoxes.has(p) && !claimed.has(p) && p.classList.contains('dk-' + node.dir)) || null;
    if (!el) { el = doc.createElement('div'); el._dkBars = []; }
    claimed.add(el);
    splitOf.set(node, el);
    el.className = 'dk-split dk-' + node.dir;
    for (const k of ['minWidth', 'minHeight', 'flex']) el.style[k] = '';
    const bars = node.kids.slice(1).map((_, i) => buildBar(node, i, el._dkBars[i]));
    el._dkBars = bars;
    plan.set(node, { el, bars });
    return el;
  }
  // The second pass, from the root down, so each element is in the document before its children move into it.
  function arrangeNode(node) {
    const p = plan.get(node);
    if (node.t === 'stack') { arrangeStack(p.parts); return; }
    const fillAt = fillIndex(node);
    const kids = [];
    node.kids.forEach((k, i) => {
      if (i > 0) kids.push(p.bars[i - 1]);
      const kid = plan.get(k).el;
      sizeKid(kid, k, node, fillAt, i);
      nodeEls.set(k, kid);
      kids.push(kid);
    });
    settle(p.el, kids);
    for (const k of node.kids) arrangeNode(k);
  }
  let stale = []; // [element, the children it keeps]: the others go once everything has moved
  function settle(parent, kids) { arrange(parent, kids); stale.push([parent, kids]); }

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

  function buildBar(sp, i, kept) {
    const bar = kept || doc.createElement('div');
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

  function claimFloat(f, z, claimed) {
    const parts = stackFor(f.stack, 'float', claimed);
    let el = floatOf.get(f);
    if (!el || claimed.has(el) || !floatBoxes.has(el)) el = null;
    if (!el && floatBoxes.has(parts.sec.parentNode) && !claimed.has(parts.sec.parentNode)) el = parts.sec.parentNode;
    if (!el) {
      el = doc.createElement('div');
      el._dkRz = ['e', 's', 'se', 'w', 'n'].map((d) => {
        const h = doc.createElement('div');
        h.className = 'dk-rz dk-rz-' + d;
        h.dataset.dkRz = d;
        h.setAttribute('aria-hidden', 'true');
        return h;
      });
    }
    claimed.add(el);
    floatOf.set(f, el);
    el.className = 'dk-float' + (f.stack.min ? ' dk-min' : '') + (maxed === f.stack ? ' dk-max' : '');
    el.style.zIndex = String(20 + z);
    floatEls.set(f, el);
    placeFloat(f, el);
    plan.set(f, { el, parts });
    return el;
  }
  function arrangeFloat(f) {
    const { el, parts } = plan.get(f);
    settle(el, [parts.sec, ...el._dkRz]);
    arrangeStack(parts);
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
      const p = byId.get(a.id);
      const b = doc.createElement('button');
      b.type = 'button';
      b.className = 'dk-strip-btn dk-mono';
      b.dataset.dkAuto = a.id;
      const ic = iconOf(p);
      if (ic) {
        // An icon: the title is its tooltip and its name.
        b.classList.add('dk-strip-icon-btn');
        const span = doc.createElement('span');
        span.className = 'dk-strip-icon';
        span.setAttribute('aria-hidden', 'true');
        span.appendChild(ic);
        b.appendChild(span);
        b.title = p.title;
        b.setAttribute('aria-label', p.title);
      } else {
        b.textContent = p.title;
        b.title = stripHover ? `${p.title}: unpinned. Click or hover to slide it out; pin it from its title bar.`
          : `${p.title}: unpinned. Click to slide it out; pin it from its title bar.`;
      }
      const badge = badges.get(a.id);
      if (badge) b.appendChild(stripBadge(badge));
      b.setAttribute('aria-expanded', 'false');
      stripBtns.set(a.id, b);
      strip.appendChild(b);
    }
    return strip;
  }

  // A panel's icon as a node for its strip button: from an SVG (or any HTML) string, or a copy of an element.
  function iconOf(p) {
    if (!p.icon) return null;
    if (typeof p.icon === 'string') {
      const t = doc.createElement('template');
      t.innerHTML = p.icon;
      return t.content.childNodes.length ? t.content : null;
    }
    return p.icon.nodeType ? doc.importNode(p.icon, true) : null;
  }
  function stripBadge(badge) {
    const s = doc.createElement('span');
    s.className = badgeClass + ' dk-strip-badge';
    s.textContent = badge.text;
    s.title = badge.title || '';
    return s;
  }

  function claimFlyout(a) {
    const p = byId.get(a.id);
    let k = flyKept.get(a.id);
    if (!k) {
      const el = doc.createElement('div');
      const head = doc.createElement('div');
      head.className = 'dk-head dk-mono';
      const body = doc.createElement('div');
      body.className = 'dk-body';
      const grip = doc.createElement('div');
      grip.className = 'dk-fly-grip';
      grip.setAttribute('aria-hidden', 'true');
      k = { el, head, body, grip, html: null };
      flyKept.set(a.id, k);
    }
    const { el, head } = k;
    el.className = 'dk-flyout dk-fly-' + a.edge + (besideOf(a) ? ' dk-fly-beside' : '');
    el.dataset.dkFly = a.id;
    el.setAttribute('role', 'region');
    el.setAttribute('aria-label', p.title);
    const tabs = `<div class="dk-tabs" role="tablist" aria-label="${escText(p.title)}">${tabHtml(a.id, true, false)}</div>`;
    const html = !classic ? tabs + '<span class="dk-ctl">'
      + (optionsOf(a.id, 'fly').any ? ctl('menu', 'menu', `${p.title}: ${T.options}`, `${T.viewMode}, ${T.moveTo}, ${T.hide}`) : '')
      + ctl('hide', 'hide', `${p.title}: ${T.hide}`, 'slide it back in (Esc)') + '</span>'
      : tabs + '<span class="dk-ctl">'
      + (allowed(a.id, 'pop') ? ctl('pop', 'pop', `${p.title}: pop out`, 'pop out into a browser window of its own (drag it to another monitor)') : '')
      + (allowed(a.id, 'float') ? ctl('float', 'float', `${p.title}: float`, 'float in its own window') : '')
      + ctl('hide-fly', 'min', `${p.title}: slide in`, 'slide it back in (Esc)')
      + (allowed(a.id, 'unpin') ? ctl('pin', 'unpin', `${p.title}: pin`, 'unpinned: pin it back where it was') : '') + '</span>';
    if (k.html !== html) { head.innerHTML = html; k.html = html; }
    flyEls.set(a.id, el);
    return el;
  }
  function arrangeFlyout(a) {
    const k = flyKept.get(a.id);
    settle(k.el, [k.head, k.body, k.grip]);
    settle(k.body, [asTabPanel(a.id, true)]);
  }

  function placeFlyout(a) {
    const el = flyEls.get(a.id);
    if (!el) return;
    const ext = extent();
    const has = (edge) => layout.auto.some((x) => x.edge === edge);
    const across = a.edge === 'left' || a.edge === 'right' ? ext.w : ext.h;
    const size = Math.round(Math.min(a.size, across * 0.85));
    el._dkSize = size;
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

  // What identifies a control of the dock across a drawing that replaced it: a tab, a strip button, a title bar button.
  function focusKey(el) {
    if (!el || !el.closest || !root.contains(el)) return null;
    const tab = el.closest('[data-dk-tab]');
    if (tab) return `[data-dk-tab="${CSS_ESC(tab.dataset.dkTab)}"]`;
    const strip = el.closest('[data-dk-auto]');
    if (strip) return `[data-dk-auto="${CSS_ESC(strip.dataset.dkAuto)}"]`;
    const act = el.closest('[data-dk-act]');
    const tabs = act && act.closest('.dk-head') && act.closest('.dk-head').querySelector('[data-dk-tab].on');
    if (tabs) return { act: act.dataset.dkAct, tab: tabs.dataset.dkTab };
    return null;
  }
  function focusAgain(key) {
    let el = null;
    if (typeof key === 'string') el = root.querySelector(key);
    else if (key) {
      const tab = root.querySelector(`[data-dk-tab="${CSS_ESC(key.tab)}"]`);
      el = tab && tab.closest('.dk-head').querySelector(`[data-dk-act="${key.act}"]`);
    }
    if (el) focusQuiet(el);
  }

  function render() {
    snapshotScrolls();
    const focused = doc.activeElement;
    const fkey = focusKey(focused);
    stackEls = new Map();
    nodeEls = new Map();
    floatEls = new Map();
    flyEls = new Map();
    stripBtns = new Map();
    plan.clear();
    stale = [];
    if (maxed && !stillHas(maxed)) maxed = null;
    if (flyOpen && !layout.auto.some((a) => a.id === flyOpen)) flyOpen = null;
    main.className = 'dk-main';
    // Which element each thing gets, before anything moves: the kept ones are found where they are now.
    const claimed = new Set();
    const top = layout.root ? claimNode(layout.root, claimed) : empty;
    if (!layout.root) empty.textContent = T.empty;
    const ext = extent();
    const floatBox = layout.floats.map((f, z) => { clampFloat(f, ext.w, ext.h, S.floatMin); return claimFloat(f, z, claimed); });
    const flyBox = layout.auto.map((a) => claimFlyout(a));
    const strips = EDGES.map((edge) => layout.auto.filter((a) => a.edge === edge)).filter((e) => e.length).map((e) => buildStrip(e[0].edge, e));
    const note = outNote();
    // Then from the root down.
    const rootKids = [main, ...strips, ...floatBox, ...flyBox, parking, preview, ...(note ? [note] : [])];
    settle(root, rootKids);
    settle(main, [top]);
    if (layout.root) { top.style.minWidth = top.style.minHeight = top.style.flex = ''; nodeEls.set(layout.root, top); arrangeNode(layout.root); }
    layout.floats.forEach(arrangeFloat);
    for (const a of layout.auto) { arrangeFlyout(a); placeFlyout(a); }
    const parked = layout.hidden.map((h) => byId.get(h.id).el);
    // Out, but not drawn in a window of its own (still opening, or remembered from before a reload): kept here.
    for (const id of ids) if (outOf(id) && !live(id)) parked.push(byId.get(id).el);
    for (const el of parked) el.classList.add('dk-panel');
    settle(parking, parked);
    // Everything is in its place: what is left over goes (a panel's element never is: it has a place, above).
    // (A panel's element is never taken away: were one left over, it would wait in the parking.)
    for (const [el, keep] of stale) { const k = new Set(keep); for (const c of [...el.children]) if (!k.has(c)) { if (panelEls.has(c)) parking.appendChild(c); else c.remove(); } }
    secParts = new Map([...plan.values()].filter((p) => p.parts).map((p) => [p.parts.sec, p.parts]));
    bodyParts = new Map([...secParts.values()].map((p) => [p.body, p]));
    splitBoxes = new Set([...plan.entries()].filter(([n]) => n.t === 'split').map(([, p]) => p.el));
    floatBoxes = new Set(floatBox);
    for (const id of [...flyKept.keys()]) if (!layout.auto.some((a) => a.id === id)) flyKept.delete(id);
    root.classList.toggle('dk-has-max', !!maxed);
    markFlyouts();
    restoreScrolls();
    if (focused && focused !== doc.body && focused.isConnected && root.contains(focused)) {
      if (doc.activeElement !== focused) focusQuiet(focused);
    } else if (fkey) focusAgain(fkey);
    fitAll();
    announceShown();
  }

  function stillHas(node) {
    return (layout.root && (layout.root === node || findStack(layout.root, node))) || layout.floats.some((f) => f.stack === node);
  }

  function dockEvent(el, type, detail) {
    const W = el.ownerDocument.defaultView;
    const CE = (W && W.CustomEvent) || (doc.defaultView && doc.defaultView.CustomEvent) || CustomEvent;
    try { el.dispatchEvent(new CE(type, { detail, bubbles: type !== 'dock-shown' })); } catch { /* no CustomEvent */ }
  }
  function shownEvent(id) { dockEvent(byId.get(id).el, 'dock-shown', { id }); }
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
    besideSpace();
  }
  // Beside: the dock's middle leaves the open panel its room on that panel's edge (a margin of .dk-main, where the
  // flyout lies), so the layout narrows instead of being covered. None of it is in the layout, or saved.
  function besideSpace() {
    const f = flyOpen && layout.auto.find((x) => x.id === flyOpen);
    const a = besideOf(f) ? f : null;
    const el = a && flyEls.get(a.id);
    for (const e of EDGES) main.style['margin' + e[0].toUpperCase() + e.slice(1)] = el && a.edge === e ? el._dkSize + 'px' : '';
    root.classList.toggle('dk-beside-open', !!el);
  }

  // ---- a panel in a window of its own ----

  // Panels remembered as out whose windows are not open (after a reload): a small notice over the dock offers, for
  // each, to open its window again or to bring it back. It is not a place in the layout; closing it leaves them in the
  // Panels menu.
  let outNoteOff = false;
  function outNote() {
    const waiting = ids.filter((id) => outOf(id) && !pops.has(id));
    if (!waiting.length || outNoteOff) return null;
    const el = doc.createElement('div');
    el.className = 'dk-outnote dk-mono';
    el.setAttribute('role', 'status');
    const btn = (k, label, title, cls = '') => `<button type="button" class="dk-textbtn${cls}" data-dk-pop="${k}" title="${escText(title)}">${escText(label)}</button>`;
    el.innerHTML = waiting.map((id) => `<div class="dk-outnote-row" data-dk-out="${escText(id)}"><span>${escText(T.popWas(byId.get(id).title))}</span>`
      + `<span class="dk-outnote-acts">${btn('reopen', T.popReopen, T.popReopenTitle, ' dk-primary')}${btn('back', T.popKeep, T.popKeepTitle)}</span></div>`).join('')
      + `<button type="button" class="dk-btn dk-outnote-x" data-dk-pop="dismiss" aria-label="${escText(T.popNoteClose)}" title="${escText(T.popNoteClose)}">${icon('close')}</button>`;
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
  const windowAt = new Map(); // where a panel's window was when it closed, while this page lasts (IntelliJ keeps it)
  function popGeometry(id) {
    const g = outOf(id) || windowAt.get(id);
    if (g && ['x', 'y', 'w', 'h'].every((k) => finite(g[k]))) return { x: g.x, y: g.y, w: g.w, h: g.h };
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

  // The pop-out page without a served file (popHtml): the page's text as a Blob, opened by its blob: URL. That URL has
  // this page's origin, and the window loads it as it would popout.html: a standards-mode document whose scripts run as
  // the browser runs any page's. Its own base would be the blob: URL, against which a relative URL finds nothing, so the
  // page gets a <base href> of this page's base (popBase), unless it has a <base> of its own. One URL for each base
  // (a page's base changes only with history.pushState and no <base>); destroy() lets them go (so does this page unloading).
  const popBlobUrls = new Map(); // the page's text -> its blob: URL
  const urlEnv = () => {
    const view = doc.defaultView;
    return view && view.URL && typeof view.URL.createObjectURL === 'function' ? view : globalThis;
  };
  function popPageText() {
    const base = popBase === false ? null : typeof popBase === 'string' ? popBase : doc.baseURI;
    const view = doc.defaultView;
    return base ? withBase(popPage, base, view && view.DOMParser) : popPage;
  }
  function popPageUrl() {
    const text = popPageText();
    if (!popBlobUrls.has(text)) {
      const env = urlEnv();
      popBlobUrls.set(text, env.URL.createObjectURL(new env.Blob([text], { type: 'text/html;charset=utf-8' })));
    }
    return popBlobUrls.get(text);
  }

  function popOut(id) {
    const had = pops.get(id);
    if (had) { try { had.win.focus(); } catch { /* ignore */ } return true; }
    const w = whereIs(layout, id);
    if (!w) return false;
    const geo = popGeometry(id);
    let child = null;
    try { child = openWindow(popPage ? popPageUrl() : popUrl, popName + id, features(geo)); } catch { child = null; }
    if (!child) {
      notify(T.popBlocked(byId.get(id).title));
      return false;
    }
    if (flyOpen === id) flyOpen = null;
    const el = byId.get(id).el;
    if (el.isConnected && !el.closest('.dk-parking')) scrolls.set(id, scrollsOf(el));
    const pop = { win: child, doc: null, timer: null, started: Date.now() };
    pops.set(id, pop);
    const at = whereIs(layout, id);
    // Maximised alone in its stack: it comes back maximised (while this page lasts; maximised is not stored).
    if (maxed && at && at.stack === maxed && maxed.panels.length === 1) { maxed = null; pop.wasMax = true; }
    popOutPanel(layout, id, geo, opts());
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
    putSizes(host);
    const sec = cd.createElement('section');
    sec.className = 'dk-stack dk-pop';
    sec.setAttribute('aria-label', p.title);
    const head = cd.createElement('div');
    head.className = 'dk-head dk-mono';
    const backBtn = popBackButton
      ? `<button type="button" class="dk-pop-back" data-dk-pop="back" title="${escText(T.backToMainTitle)}">${icon('back')}<span>${escText(T.backToMain)}</span></button>`
      : '';
    head.innerHTML = `<div class="dk-tabs" role="tablist">${tabHtml(id, true, false)}</div><span class="dk-ctl">${backBtn}</span>`;
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
    popOutPanel(layout, id, g, opts()); // only its window's place changes
    save();
  }

  // Its window is done with: the panel comes back into this document at once, and the window closes if asked.
  function release(id, pop, closeIt) {
    const g = outOf(id);
    if (g && ['x', 'y', 'w', 'h'].every((k) => finite(g[k]))) windowAt.set(id, { x: g.x, y: g.y, w: g.w, h: g.h });
    pops.delete(id);
    clearTimeout(pop.timer);
    clearInterval(pop.timer);
    const el = byId.get(id).el;
    if (pop.doc && el.ownerDocument === pop.doc) {
      try { scrolls.set(id, scrollsOf(el)); } catch { /* the window is gone */ }
      // Before it moves back: the app takes out what it put in the panel for that window (dock-popin, onPopIn).
      dockEvent(el, 'dock-popin', { id, window: pop.win });
      for (const fn of popInFns) { try { fn(id); } catch (e) { report(e); } }
    }
    parking.appendChild(el);
    if (pop.doc) removeHostDoc(pop.doc);
    if (closeIt) { try { pop.win.close(); } catch { /* ignore */ } }
  }

  // The person closed the window (or it went on its own): the panel is back in its place, and no longer out.
  function windowGone(id, pop) {
    if (leaving || pops.get(id) !== pop) return;
    release(id, pop, false);
    popInPanel(layout, id, opts());
    maxAgain(id, pop);
    showBack(id);
    commit();
  }
  // Back from its window in the view mode it had, and seen (IntelliJ's Window mode left): a strip panel slides out, as
  // it was before it went, rather than going back to its strip button alone. (Not after a reload: its note's "Bring it
  // back here" leaves it on its strip, as before.)
  function showBack(id) {
    if (modeOf(id) !== 'unpinned' && modeOf(id) !== 'undock') return;
    flyOpen = id;
    flyByHover = false;
  }
  // Back from its window, maximised again if it went out maximised.
  function maxAgain(id, pop) {
    const w = pop && pop.wasMax ? whereIs(layout, id) : null;
    if (w && w.kind === 'dock' && !maxed) { maxed = w.stack; delete w.stack.min; }
  }

  /** Back into the main window where it was; its own window closes. Also "Bring it back here" for one remembered as out. */
  function popIn(id) {
    const pop = pops.get(id);
    if (pop) release(id, pop, true);
    if (!outOf(id)) { if (pop) commit(); return false; }
    popInPanel(layout, id, opts());
    maxAgain(id, pop);
    if (pop) showBack(id); // from its open window (its Back button): seen, as when its window is closed
    commit();
    return true;
  }
  // Before a panel that is out is moved some other way (floated, unpinned, docked, hidden): its window closes.
  function closeOut(id) { const pop = pops.get(id); if (pop) release(id, pop, true); }

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
    fitAll();
  }
  function closeFly(refocus) {
    if (!flyOpen) return;
    const id = flyOpen;
    flyOpen = null;
    markFlyouts();
    fitAll();
    const b = stripBtns.get(id);
    if (refocus && b) focusQuiet(b);
  }
  function insideFly(el) {
    if (!el || !flyOpen) return false;
    const fly = flyEls.get(flyOpen);
    const b = stripBtns.get(flyOpen);
    // Its Options menu counts as the panel: working in it keeps the panel out.
    return !!((fly && fly.contains(el)) || (b && b.contains(el)) || (menu && menu._id === flyOpen && inMenu(el)));
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

  // Where a panel floated last, while this page lasts: Float again puts it there (as IntelliJ keeps a window's bounds).
  const floatedAt = new Map();
  function leavingFloat(id) {
    const w = whereIs(layout, id);
    if (w && w.kind === 'float') floatedAt.set(id, { x: w.float.x, y: w.float.y, w: w.float.w, h: w.float.h });
  }
  function floatRectFor(id) {
    if (floatedAt.has(id)) return { ...floatedAt.get(id) };
    const node = locate(layout.root, id);
    const d = node && dims(node.stack);
    const ext = extent();
    const w = Math.round(Math.min(ext.w * 0.6, Math.max(S.floatMin.w * 2, d ? d.w : 520)));
    const h = Math.round(Math.min(ext.h * 0.7, Math.max(S.floatMin.h * 2, d ? d.h : 420)));
    const n = layout.floats.length;
    return { x: Math.round((ext.w - w) / 2) + 24 * n, y: Math.round((ext.h - h) / 3) + 24 * n, w, h };
  }

  // A panel's depth where it is docked, for a strip on `edge`.
  function unpinSize(id, edge) {
    const at = locate(layout.root, id);
    const d = at && dims(at.stack);
    if (d) return edge === 'left' || edge === 'right' ? d.w : d.h;
    if (at && finite(at.stack.size)) return at.stack.size;
    return Math.max(edge === 'left' || edge === 'right' ? cfg.minSize(id).w : cfg.minSize(id).h, S.unpinSize);
  }

  // Unpins a docked or floating panel to the strip on its side (the strip it was pinned from, else the side it stands
  // on: layout.js panelSide), `open` its slide-out style when given. Not committed.
  function unpinNow(id, open) {
    const w = whereIs(layout, id);
    if (narrowOn || !w || w.kind === 'auto' || w.kind === 'hidden') return false;
    closeOut(id);
    const edge = panelSide(layout, id, opts());
    // A panel's own unpinSize, when it has one, is the depth it slides out at; else its size where it was.
    const own = cfg.unpinSizeOf(id, edge);
    const size = own !== null ? own : w.kind === 'dock' ? unpinSize(id, edge) : w.kind === 'float' ? (edge === 'left' || edge === 'right' ? w.float.w : w.float.h) : undefined;
    if (maxed === w.stack && w.stack.panels.length === 1) maxed = null;
    unpinPanel(layout, id, edge, size, opts(), open);
    return true;
  }

  // IntelliJ's View Mode: panel `id` to `to` ('pinned', 'unpinned', 'undock', 'float', 'window'), on the side it has.
  function setViewMode(id, to) {
    const from = modeOf(id);
    if (!VIEW_MODES.includes(to) || from === to || !modeOk(id, from, to)) return false;
    if (to === 'window') { closeFly(false); return popOut(id); }
    let now = from;
    if (from === 'float') leavingFloat(id);
    if (from === 'window') {
      // Back from its window first (as its Back button does), then on to the mode asked for.
      const pop = pops.get(id);
      if (pop) release(id, pop, true);
      popInPanel(layout, id, opts());
      maxAgain(id, pop);
      now = modeOf(id);
    }
    const strip = (m) => m === 'unpinned' || m === 'undock';
    const style = (m) => (m === 'unpinned' ? 'beside' : 'over');
    if (to === 'float') {
      if (now !== 'float') {
        const rect = floatRectFor(id);
        const w = whereIs(layout, id);
        if (w && w.stack && maxed === w.stack && w.stack.panels.length === 1) maxed = null;
        floatPanel(layout, id, rect);
      }
      if (flyOpen === id) flyOpen = null;
    } else if (to === 'pinned') {
      if (strip(now)) pinPanel(layout, id, opts());
      else if (now === 'float') dockFromFloat(layout, id, opts());
      if (flyOpen === id) flyOpen = null;
    } else if (strip(to)) {
      if (strip(now)) {
        const a = layout.auto.find((x) => x.id === id);
        delete a.open;
        if (style(to) !== openStyle) a.open = style(to);
      } else unpinNow(id, style(to) === openStyle ? null : style(to));
      // Seen in its new mode, as IntelliJ keeps a window it switched: slid out until it is left.
      flyOpen = id;
      flyByHover = false;
    }
    commit();
    return true;
  }
  // IntelliJ's Move To: panel `id` to another side, in the view mode it has.
  function moveSideNow(id, side) {
    if (narrowOn || !allowed(id, 'move') || !EDGES.includes(side)) return false;
    const w = whereIs(layout, id);
    let size;
    if (w && w.kind === 'dock') { const d = dims(w.stack); if (d && w.stack.panels.length === 1) size = side === 'left' || side === 'right' ? d.w : d.h; }
    if (w && w.kind === 'dock' && maxed === w.stack && w.stack.panels.length === 1) maxed = null;
    if (!moveSide(layout, id, side, { ...opts(), size })) return false;
    commit();
    return true;
  }

  const api = {
    layout: () => layout,
    /** A panel's view mode (IntelliJ's): 'pinned', 'unpinned', 'undock', 'float', 'window' or 'hidden'. */
    viewMode: modeOf,
    /** Changes a panel's view mode; its side stays. False when nothing changed (or `can` does not allow it). */
    setViewMode: (id, mode) => setViewMode(id, mode),
    /** A panel's side: 'left', 'right', 'top' or 'bottom', whatever its view mode. */
    side: (id) => panelSide(layout, id, opts()),
    /** Moves a panel to another side in the view mode it has (IntelliJ's Move To). */
    moveSide: (id, side) => moveSideNow(id, side),
    config: () => cfg,
    isShown: shownNow,
    isVisible: (id) => { const w = whereIs(layout, id); return !!w && w.kind !== 'hidden'; },
    isAuto: (id) => { const w = whereIs(layout, id); return !!w && w.kind === 'auto'; },
    frontOf: (id) => { const w = whereIs(layout, id); return w && (w.kind === 'dock' || w.kind === 'float') ? w.stack.active : null; },
    onShown: (fn) => { shownFns.push(fn); },
    onChange: (fn) => { changeFns.push(fn); },
    /** `fn(id)` just before a popped-out panel's element moves back from its window (also the `dock-popin` event). */
    onPopIn: (fn) => { popInFns.push(fn); },
    /** Whether the dock is narrow (one column of tabs; see the `narrow` option). */
    narrow: () => narrowOn,
    /** Narrow on or off. Each has a layout of its own, kept apart. Going narrow closes pop-out windows (the wide layout
     * keeps them as out, and its notice offers to open them again). */
    setNarrow(on) {
      on = !!on;
      if (on === narrowOn || destroyed) return;
      save();
      for (const [id, pop] of [...pops]) { rememberWindow(id, pop); release(id, pop, true); }
      closeMenu(false);
      maxed = null;
      flyOpen = null;
      narrowOn = on;
      cfg = on ? narrowCfg : wideCfg;
      root.classList.toggle('dk-narrow', on);
      layout = load();
      commit();
    },
    /** Whether a person may do `action` to panel `id` here (the `can` option, and narrow). */
    can: allowed,
    render,
    activate(id) { if (activate(layout, id)) commit(); },
    /** Makes a panel seen: shown if hidden, brought to the front of its stack, slid out if unpinned. */
    reveal(id) {
      if (pops.has(id)) { try { pops.get(id).win.focus(); } catch { /* ignore */ } return; }
      if (outOf(id)) { popIn(id); return; } // remembered as out, its window not open: back here, where it can be seen
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
      // Its strip button's too, while it is unpinned (an icon's or a title's).
      const sb = stripBtns.get(id);
      if (sb) {
        const old = sb.querySelector('.dk-strip-badge');
        if (old) old.remove();
        if (txt) sb.appendChild(stripBadge({ text: txt, title }));
      }
      for (const tab of tabs) {
        let b = tab.querySelector('.' + badgeClass);
        if (!txt) { if (b) b.remove(); continue; }
        if (!b) { b = tab.ownerDocument.createElement('span'); b.className = badgeClass; tab.appendChild(b); }
        b.textContent = txt;
        b.title = title || '';
      }
    },
    setVisible(id, show) {
      if (!show) closeOut(id);
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
      if (narrowOn) return;
      closeOut(id);
      const rect = floatRectFor(id);
      if (floatPanel(layout, id, rect)) { flyOpen = flyOpen === id ? null : flyOpen; commit(); }
    },
    dockBack(id) {
      if (narrowOn) return;
      const w = whereIs(layout, id);
      if (w && w.kind === 'float') { leavingFloat(id); dockBack(layout, w.float, opts()); commit(); }
    },
    unpin(id) { if (unpinNow(id)) commit(); },
    pin(id) { if (pinPanel(layout, id, opts())) { flyOpen = null; commit(); } },
    toggleMin(id) {
      const w = whereIs(layout, id);
      if (narrowOn || !w || !w.stack) return;
      if (w.stack.min) delete w.stack.min; else { w.stack.min = true; if (maxed === w.stack) maxed = null; }
      commit();
    },
    toggleMax(id) {
      const w = whereIs(layout, id);
      if (narrowOn || !w || !w.stack) return;
      maxed = maxed === w.stack ? null : w.stack;
      if (maxed) delete w.stack.min;
      save();
      render();
    },
    restoreMax() { if (maxed) { maxed = null; render(); return true; } return false; },
    moveTo(id, target, side) { if (narrowOn) return; closeOut(id); if (moveTo(layout, id, target, side, opts())) commit(); },
    dockEdge(id, side) { if (narrowOn) return; closeOut(id); if (moveTo(layout, id, { kind: 'root' }, side, opts())) commit(); },
    popOut(id) { if (narrowOn) return false; closeFly(false); return popOut(id); },
    popIn,
    /** Whether a panel is out (in its own window, or remembered as out before a reload). */
    isOut: (id) => !!outOf(id),
    /** Whether a panel that is out has its window open now (false after a reload, until it is opened again). */
    isOpenOut: (id) => pops.has(id),
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
      for (const u of popBlobUrls.values()) { try { urlEnv().URL.revokeObjectURL(u); } catch { /* ignore */ } }
      popBlobUrls.clear();
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
    else if (name === 'hide') hideNow(id, !!btn.closest('.dk-flyout'));
  }
  // − : a strip panel slides back in; a docked or floating one is minimised to its title bar, or restored from it.
  function hideNow(id, fly) {
    if (fly || (flyOpen === id && modeOf(id) !== 'pinned' && modeOf(id) !== 'float')) { closeFly(true); return; }
    if (allowed(id, 'min')) api.toggleMin(id);
  }

  // ---- the per-panel menu: dock at an edge, float, unpin, hide ----

  let menu = null;
  const inMenu = (el) => !!(menu && el && (menu.contains(el) || (menu._sub && menu._sub.contains(el))));
  function closeMenu(refocus) {
    if (!menu) return;
    const back = menu._from;
    if (menu._sub) menu._sub.remove();
    menu.remove();
    menu = null;
    if (refocus && back && back.isConnected) focusQuiet(back);
  }
  // A menu at (x, y), kept inside the window: moved left or up, or to the other side of `alt` (the x it may open
  // leftwards from, for a submenu), when it would go past an edge.
  function placeMenu(el, x, y, alt) {
    const vw = viewport();
    const vh = (win && win.innerHeight) || 900;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let left = x;
    if (left + w > vw - 4) left = alt !== undefined ? alt - w : vw - 4 - w;
    let top = y;
    if (top + h > vh - 4) top = vh - 4 - h;
    el.style.left = Math.max(4, left) + 'px';
    el.style.top = Math.max(4, top) + 'px';
  }
  const menuButtons = (el) => [...el.querySelectorAll(':scope > button:not([disabled])')];
  function moveFocus(el, key) {
    const list = menuButtons(el);
    if (!list.length) return;
    const at = list.indexOf(doc.activeElement);
    const down = key === 'ArrowDown';
    let to;
    if (key === 'Home') to = 0;
    else if (key === 'End') to = list.length - 1;
    else if (at < 0) to = down ? 0 : list.length - 1;
    else to = (at + (down ? 1 : list.length - 1)) % list.length;
    focusQuiet(list[to]);
  }

  // The Options menu (⋯): View Mode ▸, Move To ▸, Maximise or Restore, Hide.
  function openOptions(btn, id) {
    if (menu && menu._from === btn) { closeMenu(true); return; }
    closeMenu(false);
    const fly = !!btn.closest('.dk-flyout');
    const w = whereIs(layout, id);
    const o = optionsOf(id, fly ? 'fly' : w && w.kind === 'float' ? 'float' : 'dock');
    const title = byId.get(id).title;
    menu = doc.createElement('div');
    menu.className = 'dk-menu dk-mono dk-options';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', `${title}: ${T.options}`);
    const arrow = '<span class="dk-menu-arrow" aria-hidden="true">▸</span>';
    const parts = [];
    if (o.modes.length) parts.push(`<button type="button" role="menuitem" aria-haspopup="menu" aria-expanded="false" data-dk-sub="mode">${escText(T.viewMode)}${arrow}</button>`);
    if (o.sides.length) parts.push(`<button type="button" role="menuitem" aria-haspopup="menu" aria-expanded="false" data-dk-sub="side">${escText(T.moveTo)}${arrow}</button>`);
    const tail = [];
    if (o.max) tail.push(`<button type="button" role="menuitem" data-dk-menu="max">${escText(o.max)}</button>`);
    if (o.hide) tail.push(`<button type="button" role="menuitem" data-dk-menu="hide">${escText(o.hide)}</button>`);
    if (parts.length && tail.length) parts.push('<div class="dk-menu-sep" role="separator"></div>');
    menu.innerHTML = parts.join('') + tail.join('');
    menu._from = btn;
    menu._id = id;
    menu._fly = fly;
    menu._o = o;
    doc.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    placeMenu(menu, r.right - menu.offsetWidth, r.bottom + 2);
    if (r.bottom + 2 + menu.offsetHeight > ((win && win.innerHeight) || 900) - 4) placeMenu(menu, r.right - menu.offsetWidth, r.top - 2 - menu.offsetHeight);
    focusQuiet(menuButtons(menu)[0]);
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.dkSub) { openSub(b, true); return; }
      pick(b.dataset.dkMenu);
    });
    // With a submenu open, the pointer switches it only after resting 150 ms, so a diagonal move towards the open
    // submenu across another item does not swap it (entering the submenu cancels the switch).
    const m = menu;
    m.addEventListener('pointerover', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      clearTimeout(m._swap);
      const go = () => {
        if (menu !== m) return;
        if (b.dataset.dkSub) { if (!m._sub || m._sub._kind !== b.dataset.dkSub) openSub(b, false); }
        else closeSub(false);
      };
      if (m._sub && m._sub._kind !== b.dataset.dkSub) m._swap = later(go, 150);
      else go();
    });
    menu.addEventListener('keydown', (e) => {
      const b = e.target.closest && e.target.closest('button');
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) { e.preventDefault(); moveFocus(menu, e.key); }
      else if (b && b.dataset.dkSub && (e.key === 'ArrowRight' || e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openSub(b, true); }
      else if (e.key === 'Tab') closeMenu(false);
    });
  }
  function closeSub(refocus) {
    if (!menu || !menu._sub) return;
    const parent = menu.querySelector(`[data-dk-sub="${menu._sub._kind}"]`);
    menu._sub.remove();
    menu._sub = null;
    if (parent) parent.setAttribute('aria-expanded', 'false');
    if (refocus && parent) focusQuiet(parent);
  }
  function openSub(parent, focus) {
    const kind = parent.dataset.dkSub;
    if (menu._sub && menu._sub._kind === kind) { if (focus) focusQuiet(menu._sub.querySelector('[aria-checked="true"]') || menuButtons(menu._sub)[0]); return; }
    closeSub(false);
    const o = menu._o;
    const sub = doc.createElement('div');
    sub.className = 'dk-menu dk-mono dk-submenu';
    sub.setAttribute('role', 'menu');
    sub.setAttribute('aria-label', kind === 'mode' ? T.viewMode : T.moveTo);
    const item = (k, label, on, hint) => `<button type="button" role="menuitemradio" aria-checked="${on}" data-dk-menu="${k}"${hint ? ` title="${escText(hint)}"` : ''}>${escText(label)}</button>`;
    const hints = T.modeHints || {};
    sub.innerHTML = kind === 'mode' ? o.modes.map((m) => item(`mode:${m}`, T.modes[m], m === o.now, hints[m])).join('')
      : o.sides.map((s) => item(`side:${s}`, T.sides[s], s === o.side)).join('');
    sub._kind = kind;
    menu._sub = sub;
    parent.setAttribute('aria-expanded', 'true');
    doc.body.appendChild(sub);
    const r = parent.getBoundingClientRect();
    const m = menu.getBoundingClientRect();
    placeMenu(sub, m.right - 2, r.top - 4, m.left + 2);
    sub.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) pick(b.dataset.dkMenu); });
    sub.addEventListener('pointerover', () => clearTimeout(menu && menu._swap));
    sub.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' || e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); closeSub(true); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) { e.preventDefault(); moveFocus(sub, e.key); }
      else if (e.key === 'Tab') closeMenu(false);
    });
    if (focus) focusQuiet(sub.querySelector('[aria-checked="true"]') || menuButtons(sub)[0]);
  }
  // An item chosen: the menu closes, then it acts.
  function pick(k) {
    if (!k || !menu) return;
    const id = menu._id;
    const fly = menu._fly;
    closeMenu(false);
    if (k.startsWith('mode:')) setViewMode(id, k.slice(5));
    else if (k.startsWith('side:')) moveSideNow(id, k.slice(5));
    else if (k === 'max') api.toggleMax(id);
    else if (k === 'hide') hideNow(id, fly);
    // Focus back in the panel's title bar where it still is, else on its strip button or tab.
    const q = CSS_ESC(id);
    const again = root.querySelector(`[data-dk-fly="${q}"].open [data-dk-tab]`)
      || [...root.querySelectorAll(`[data-dk-tab="${q}"]`)].find((t) => !t.closest('.dk-flyout'))
      || root.querySelector(`[data-dk-auto="${q}"]`);
    if (again && !pops.has(id)) focusQuiet(again);
  }

  function openMenu(btn, id) {
    if (!classic) { openOptions(btn, id); return; }
    if (menu && menu._from === btn) { closeMenu(true); return; }
    closeMenu(false);
    const title = byId.get(id).title;
    const w = whereIs(layout, id);
    const items = menuItems(id, w && w.kind === 'float' ? 'float' : 'dock');
    if (!items.length) return;
    menu = doc.createElement('div');
    menu.className = 'dk-menu dk-mono';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', `${title}: move or hide`);
    menu.innerHTML = items.map(([k, label]) => `<button type="button" role="menuitem" data-dk-menu="${k}">${escText(label)}</button>`).join('');
    menu._from = btn;
    menu._id = id;
    doc.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    menu.style.top = (r.bottom + 2) + 'px';
    menu.style.left = Math.max(4, Math.min(r.right - menu.offsetWidth, viewport() - menu.offsetWidth - 4)) + 'px';
    focusQuiet(menu.querySelector('button'));
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
        list[(at + (e.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length].focus({ preventScroll: true });
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
    const id = tab ? tab.dataset.dkTab : node.active;
    if (!allowed(id, 'move')) return;
    gesture = { kind: 'drag', id, x0: e.clientX, y0: e.clientY, on: false, drop: null };
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
    if (pb && pb.closest('.dk-outnote')) {
      const row = pb.closest('[data-dk-out]');
      const k = pb.dataset.dkPop;
      if (k === 'dismiss') { outNoteOff = true; render(); }
      else if (row && k === 'back') popIn(row.dataset.dkOut);
      else if (row && k === 'reopen') popOut(row.dataset.dkOut);
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
    if (minClickRestores && restoreFromHead(e)) return;
    const tab = t.closest('[data-dk-tab]');
    if (tab && !t.closest(helpSel)) { api.activate(tab.dataset.dkTab); return; }
    const strip = t.closest('[data-dk-auto]');
    if (strip) {
      const id = strip.dataset.dkAuto;
      if (flyOpen === id && !flyByHover) closeFly(false);
      else {
        openFly(id, false);
        const fly = flyEls.get(id);
        // Its first control (the panel is still sliding in from the side: focusQuiet scrolls nothing), else its tab.
        const first = fly && (fly.querySelector('.dk-body :is(button, input, select, textarea, [tabindex]):not([tabindex="-1"]):not([disabled])')
          || fly.querySelector('.dk-head [data-dk-tab]'));
        if (first) focusQuiet(first);
      }
    }
  });

  // A click on a minimised stack's title bar (a tab, or the bar beside the tabs; not a control or help, and not the
  // click that ends a drag or a move, which never gets here) restores it at once, as its restore control does, and a
  // tab clicked comes to the front. Enter and Space on a focused tab click it, so they restore too.
  let restored = null; // { id, x, y, t }: the last restore by a mouse click, whose second click may follow
  function restoreFromHead(e) {
    const t = e.target;
    const head = t.closest('.dk-head');
    if (!head || t.closest(`.dk-ctl, ${helpSel}`) || head.closest('.dk-flyout')) return false;
    const node = stackAround(head);
    if (!node || !node.min || !allowed(node.active, 'min')) return false;
    const tab = t.closest('[data-dk-tab]');
    if (tab) activate(layout, tab.dataset.dkTab);
    delete node.min;
    commit();
    restored = e.detail === 1 ? { id: node.active, x: e.clientX, y: e.clientY, t: e.timeStamp } : null;
    return true;
  }
  // The second click of a double click that restored a stack. Restoring can move the title bar away from the pointer (a
  // stack at the bottom grows upwards), leaving its body or a neighbour there: that click is still the title bar's. It
  // reaches nothing else, and its dblclick maximises or docks back as one on the title bar does. On the title bar
  // itself it goes on as any click there (the stack is restored: a tab switches, nothing minimises again).
  const DBL_MS = 500;
  const DBL_PX = 6;
  function secondClick(e) {
    const r = restored;
    if (!r || e.button !== 0 || e.timeStamp - r.t > DBL_MS || Math.abs(e.clientX - r.x) > DBL_PX || Math.abs(e.clientY - r.y) > DBL_PX) return false;
    const head = e.target.closest && e.target.closest('.dk-head');
    return !(head && head.querySelector(`[data-dk-tab="${CSS_ESC(r.id)}"]`));
  }
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    on(root, type, (e) => { if (secondClick(e)) { e.preventDefault(); e.stopPropagation(); } }, true);
  }
  on(root, 'dblclick', (e) => {
    if (!secondClick(e)) return;
    e.preventDefault();
    e.stopPropagation();
    const id = restored.id;
    restored = null;
    dblHead(id);
  }, true);

  on(root, 'dblclick', (e) => {
    const head = e.target.closest('.dk-head');
    if (!head || e.target.closest(`.dk-ctl, ${helpSel}`) || head.closest('.dk-flyout')) return;
    // By the panel's name, not its element: the first click of the two may have redrawn the title bar.
    const tabEl = e.target.closest('[data-dk-tab]');
    const node = stackAround(head);
    dblHead(tabEl ? tabEl.dataset.dkTab : node && node.active);
  });
  function dblHead(id) {
    const w = id && whereIs(layout, id);
    // IntelliJ: a double click maximises, and again restores, docked or floating. Classic: a float's docks it back.
    if (classic && w && w.kind === 'float' && allowed(id, 'float')) { dockBack(layout, w.float, opts()); commit(); }
    else if (w && (w.kind === 'dock' || w.kind === 'float') && allowed(id, 'max')) api.toggleMax(id);
  }
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

  // Keys. A stack's tabs are a roving tablist: Left and Right move along them (and bring that panel to the front), Home
  // and End go to the ends. F6 and Shift+F6, with focus in the dock, go to the next or previous stack's front tab.
  function showTab(tab) {
    const list = tab.closest('.dk-tabs');
    if (!list || !tab.getBoundingClientRect) return;
    const a = tab.getBoundingClientRect();
    const b = list.getBoundingClientRect();
    if (a.left < b.left) list.scrollLeft -= b.left - a.left;
    else if (a.right > b.right) list.scrollLeft += a.right - b.right;
  }
  function focusTab(id) {
    const tab = root.querySelector(`[data-dk-tab="${CSS_ESC(id)}"]`);
    if (tab) { focusQuiet(tab); showTab(tab); }
    return tab;
  }
  // The places F6 goes through, in order: the docked stacks, the floating windows (back to front), the panel slid out.
  function stackStops() {
    if (maxed && stackEls.get(maxed)) return [stackEls.get(maxed)];
    const docked = [...main.querySelectorAll('.dk-stack')];
    const floats = layout.floats.map((f) => stackEls.get(f.stack)).filter(Boolean);
    const fly = flyOpen && flyEls.get(flyOpen) ? [flyEls.get(flyOpen)] : [];
    return [...docked, ...floats, ...fly];
  }
  function cycleStacks(back) {
    const stops = stackStops();
    if (!stops.length) return false;
    const now = doc.activeElement;
    const at = stops.findIndex((s) => s.contains(now));
    const next = at < 0 ? (back ? stops.length - 1 : 0) : (at + (back ? stops.length - 1 : 1)) % stops.length;
    const tab = stops[next].querySelector('.dk-head [data-dk-tab].on') || stops[next].querySelector('.dk-head [data-dk-tab]');
    if (!tab) return false;
    focusQuiet(tab);
    showTab(tab);
    return true;
  }
  function onF6(e) {
    if (e.key !== 'F6' || e.altKey || e.ctrlKey || e.metaKey || e.defaultPrevented) return;
    if (!root.contains(doc.activeElement)) return; // outside the dock F6 is the browser's
    if (cycleStacks(e.shiftKey)) e.preventDefault();
  }
  on(doc, 'keydown', onF6);
  // With focus in an iframe in a panel, the keys go to the iframe's document, not this one (whose focus is then the
  // iframe). So each same-origin iframe in the dock gets the same listener, again each time it loads a page, and
  // iframes put in later get it too. A cross-origin one cannot be reached: F6 there stays the browser's. Not the
  // iframes inside those, nor those of a panel in its own window.
  const frames = new Map(); // iframe -> [the document its listener is on, the listener]
  const watched = new WeakSet(); // iframes with a load listener
  function unhookFrame(f) {
    const had = frames.get(f);
    if (!had) return;
    frames.delete(f);
    try { had[0].removeEventListener('keydown', had[1]); } catch { /* gone */ }
  }
  function hookFrame(f) {
    if (destroyed) return;
    let d = null;
    try { d = f.contentDocument; } catch { d = null; } // null when cross-origin
    const had = frames.get(f);
    if (had && had[0] === d) return;
    unhookFrame(f);
    if (!d) return;
    const fn = (e) => { if (root.contains(f)) onF6(e); };
    d.addEventListener('keydown', fn);
    frames.set(f, [d, fn]);
  }
  function watchFrames(under) {
    if (under.nodeType !== 1) return;
    const list = under.tagName === 'IFRAME' ? [under] : [...under.querySelectorAll('iframe')];
    for (const f of list) {
      if (!watched.has(f)) { watched.add(f); on(f, 'load', () => hookFrame(f)); }
      hookFrame(f);
    }
  }
  watchFrames(root);
  const FMO = doc.defaultView && doc.defaultView.MutationObserver;
  if (FMO) {
    const fmo = new FMO((recs) => {
      for (const r of recs) for (const n of r.addedNodes) watchFrames(n);
      for (const f of [...frames.keys()]) if (!f.isConnected) unhookFrame(f); // removed, not moved: let it go
    });
    fmo.observe(root, { childList: true, subtree: true });
    undo.push(() => fmo.disconnect());
  }
  undo.push(() => { for (const f of [...frames.keys()]) unhookFrame(f); });
  on(root, 'keydown', (e) => {
    const tab = e.target.closest && e.target.closest('[data-dk-tab]');
    if (tab && !e.altKey && !e.ctrlKey && !e.metaKey && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      const list = [...tab.closest('.dk-tabs').querySelectorAll('[data-dk-tab]')];
      const at = list.indexOf(tab);
      const n = list.length;
      const to = e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : (at + (e.key === 'ArrowRight' ? 1 : n - 1)) % n;
      e.preventDefault();
      const id = list[to].dataset.dkTab;
      api.activate(id);
      focusTab(id);
      return;
    }
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

  // Hover slides an unpinned panel out, and the pointer leaving it slides it back, unless it was opened otherwise
  // (click-only, or beside and opened by a click): sticky. Focus or a click going elsewhere in the page slides back any
  // panel slid out (IntelliJ's Dock Unpinned and Undock), sticky or not; with stripAutoHide: false (v0.5.0) a sticky one
  // goes back only by its button, its slide-in control, Esc, or another one opening. Not while a gesture runs, nor while
  // its Options menu is open, nor when the whole window loses focus (alt-tab, a popped-out window).
  const sticky = () => !stripHover || (besideOf(layout.auto.find((a) => a.id === flyOpen)) && !flyByHover);
  const stays = () => !stripAutoHide && sticky();
  // A modal (modalSelector, or an open <dialog>, whose role is implicit) counts as the panel that opened it.
  const inModal = (el) => !!(el && el.closest && el.closest(`${modalSelector}, dialog[open]`));
  const menuOfFly = () => !!(menu && flyOpen && menu._id === flyOpen);
  on(root, 'pointerover', (e) => {
    const strip = e.target.closest && e.target.closest('[data-dk-auto]');
    if (strip && !gesture && stripHover) {
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
    if (sticky()) return;
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
      if (!flyOpen || gesture || menuOfFly()) return;
      if (typeof doc.hasFocus === 'function' && !doc.hasFocus()) return; // the window lost focus, not the panel
      const now = doc.activeElement;
      // Focus let go (a click on something that takes none, an element removed): a click elsewhere is pointerdown's.
      if (!now || now === doc.body) return;
      if (insideFly(now) || inModal(now)) return;
      if (stays()) return;
      closeFly(false);
    }, 0);
  });
  on(doc, 'pointerdown', (e) => {
    if (flyOpen && !gesture && !stays() && !menuOfFly() && !insideFly(e.target) && !inMenu(e.target) && !inModal(e.target)) closeFly(false);
    if (menu && !inMenu(e.target) && e.target !== menu._from && !(menu._from && menu._from.contains(e.target))) closeMenu(false);
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
      besideSpace();
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

export { LAYOUT_VERSION, POP_HTML };
