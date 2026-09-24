// The layout model: plain JSON, so it is stored as it is. No DOM here; dock.js draws it.
//
//   { v, root, floats: [{ stack, x, y, w, h, home }], auto: [{ id, edge, size, home }], hidden: [{ id, was }],
//     out: { id: { x, y, w, h, was } } }
//
// A node is a stack { t: 'stack', panels: [id], active: id, min?, size? } or a split { t: 'split', dir: 'row'|'col',
// kids: [node], size? }. `size` is a node's px along its parent split; one child of each split (the one holding the
// `fill` panel, else the largest) takes what is left. `home` is where a panel was docked: the panels it shared a stack
// with, or the panel beside it and on which side, so it goes back there. `out` names the panels popped out into a
// browser window of their own, with that window's screen position and size ({ x, y, w, h }). A panel that is out has
// no place in the layout (its tab goes, or its whole stack, and the neighbours take the room); `was` says where it
// was, as a hidden panel's does, and it goes back there.
//
// Everything that depends on the app (its panels, their minimum sizes, their default edges and layout) comes from a
// config made by makeConfig(); every function that needs it takes it as `opts.cfg` (or `cfg`).

export const LAYOUT_VERSION = 1;
export const EDGES = ['left', 'right', 'top', 'bottom'];

/** The layout defaults, in px. Each can be overridden through createDock's `sizes` option. */
export const SIZES = Object.freeze({
  head: 24, // a title bar (also --dk-head in CSS)
  bar: 5, // a splitter (also --dk-bar)
  strip: 22, // an edge strip holding unpinned panels (also --dk-strip)
  edgeBand: 24, // how near the dock's edge a drag docks at the edge
  keyStep: 16, // an arrow key on a splitter
  panelMin: Object.freeze({ w: 240, h: 90 }), // a panel's minimum size unless `minSize` says otherwise
  floatMin: Object.freeze({ w: 220, h: 120 }), // a floating window's minimum size
  popMin: Object.freeze({ w: 360, h: 240 }), // a popped-out browser window's minimum size
  unpinSize: 420, // how far an unpinned panel slides out when nothing else says
  splitSize: 300, // a split child with no size of its own
});

export const stackNode = (panels, active) => ({ t: 'stack', panels, active: active || panels[0] });
export const splitNode = (dir, kids) => ({ t: 'split', dir, kids });
/** A stack for a declared default layout: stack(['a', 'b'], { size: 320, active: 'b' }). */
export function stack(panels, o = {}) {
  const s = stackNode(panels.slice(), o.active);
  if (finite(o.size)) s.size = o.size;
  if (o.min === true) s.min = true;
  return s;
}
/** A split for a declared default layout: split('row', [stack(['a']), stack(['b'])], { size }). */
export function split(dir, kids, o = {}) {
  const s = splitNode(dir, kids);
  if (finite(o.size)) s.size = o.size;
  return s;
}

const dirOf = (side) => (side === 'left' || side === 'right' ? 'row' : 'col');
const isBefore = (side) => side === 'left' || side === 'top';
const finite = (n) => typeof n === 'number' && Number.isFinite(n);
const clone = (x) => JSON.parse(JSON.stringify(x));

// ---------- the app's config ----------

/**
 * The app-specific part of a dock, with defaults:
 *   ids            the panels' ids, in order
 *   defaultLayout  a node (built with stack()/split()), { root, floats?, auto?, hidden? }, or a function (ctx) returning
 *                  either; ctx is { viewportPx, purpose: 'load' | 'reset' | 'home' }. Default: every panel side by side.
 *   minSize        { id: { w, h } } or (id) => { w, h }; default sizes.panelMin
 *   edgeOf         { id: edge } or (id) => edge: where a panel goes when nothing else says; default 'right'
 *   fill           the panel whose place takes what is left in its split (e.g. the main view); default none
 *   defaultSize    (panelIds, dir, ctx) => px | null: a splitter's double-click size for the child holding panelIds
 *   sizes          overrides of SIZES
 */
export function makeConfig(o = {}) {
  const sizes = { ...SIZES, ...(o.sizes || {}) };
  const ids = Array.isArray(o.ids) ? o.ids.slice() : [];
  const table = (v, fallback) => (typeof v === 'function' ? (id) => v(id) || fallback(id) : (id) => (v && v[id]) || fallback(id));
  const minSize = table(o.minSize, () => sizes.panelMin);
  const edgeOf = table(o.edgeOf, () => 'right');
  const cfg = { ids, sizes, minSize, edgeOf, fill: o.fill || null, defaultSize: typeof o.defaultSize === 'function' ? o.defaultSize : null };
  const declared = o.defaultLayout;
  cfg.defaultLayout = (ctx = {}) => {
    const c = { viewportPx: 1600, purpose: 'load', ...ctx };
    let d = typeof declared === 'function' ? declared(c) : declared ? clone(declared) : null;
    if (d && d.t) d = { root: d };
    const layout = { v: LAYOUT_VERSION, root: null, floats: [], auto: [], hidden: [], out: {} };
    if (d) {
      layout.root = d.root ? clone(d.root) : null;
      for (const k of ['floats', 'auto', 'hidden']) if (Array.isArray(d[k])) layout[k] = clone(d[k]);
    } else if (ids.length) {
      layout.root = ids.length === 1 ? stackNode([ids[0]]) : splitNode('row', ids.map((id) => stackNode([id])));
    }
    // A panel the default does not mention goes on its edge.
    for (const id of ids) if (!whereIs(layout, id)) dockAtEdge(layout, id, cfg.edgeOf(id), undefined, null, cfg);
    return layout;
  };
  return cfg;
}

const cfgOf = (opts) => (opts && opts.cfg) || makeConfig();

// ---------- finding things ----------

/** The stack holding `id` under `node`, with the splits above it: { stack, chain: [{ split, i }] }; null if none. */
export function locate(node, id, chain = []) {
  if (!node) return null;
  if (node.t === 'stack') return node.panels.includes(id) ? { stack: node, chain } : null;
  for (let i = 0; i < node.kids.length; i++) {
    const at = locate(node.kids[i], id, [...chain, { split: node, i }]);
    if (at) return at;
  }
  return null;
}

export function panelsUnder(node, out = []) {
  if (!node) return out;
  if (node.t === 'stack') out.push(...node.panels);
  else for (const k of node.kids) panelsUnder(k, out);
  return out;
}

export function contains(node, id) { return panelsUnder(node).includes(id); }

export function findStack(node, s, chain = []) {
  if (!node) return null;
  if (node === s) return { stack: s, chain };
  if (node.t !== 'split') return null;
  for (let i = 0; i < node.kids.length; i++) {
    const at = findStack(node.kids[i], s, [...chain, { split: node, i }]);
    if (at) return at;
  }
  return null;
}

/** Where a panel is: { kind: 'dock' | 'float' | 'auto' | 'hidden' | 'out', ... }; null when the layout does not hold it. */
export function whereIs(layout, id) {
  const at = locate(layout.root, id);
  if (at) return { kind: 'dock', stack: at.stack, chain: at.chain };
  for (const f of layout.floats) if (f.stack.panels.includes(id)) return { kind: 'float', float: f, stack: f.stack };
  const a = layout.auto.find((x) => x.id === id);
  if (a) return { kind: 'auto', entry: a };
  const h = layout.hidden.find((x) => x.id === id);
  if (h) return { kind: 'hidden', entry: h };
  if (layout.out && Object.prototype.hasOwnProperty.call(layout.out, id)) return { kind: 'out', entry: layout.out[id] };
  return null;
}

/** Whether a panel is drawn: the front tab of its stack (docked or floating), or unpinned. A hidden panel is not. */
export function isShownIn(layout, id) {
  const w = whereIs(layout, id);
  if (!w) return false;
  if (w.kind === 'dock' || w.kind === 'float') return w.stack.active === id;
  return w.kind === 'auto';
}

/** Where a docked panel would go back to: the panels of its stack, else the panel (or panels) beside it and on which
 * side. */
export function homeOf(layout, id) {
  const at = locate(layout.root, id);
  if (!at) return null;
  const home = { peers: at.stack.panels.filter((p) => p !== id), index: at.stack.panels.indexOf(id), near: null, side: null };
  if (finite(at.stack.size)) home.size = at.stack.size;
  for (let k = at.chain.length - 1; k >= 0; k--) {
    const { split: sp, i } = at.chain[k];
    const j = i > 0 ? i - 1 : i + 1;
    if (!sp.kids[j]) continue;
    const near = panelsUnder(sp.kids[j]);
    if (!near.length) continue;
    home.near = near[0];
    if (near.length > 1) home.nearAll = near; // the neighbour was a split: go back beside all of it, not into it
    home.side = sp.dir === 'row' ? (i > j ? 'right' : 'left') : (i > j ? 'bottom' : 'top');
    break;
  }
  return home;
}

// ---------- changing the tree ----------

function replaceIn(layout, at, next) {
  const parent = at.chain.length ? at.chain[at.chain.length - 1] : null;
  if (!parent) { layout.root = next; return; }
  parent.split.kids[parent.i] = next;
}

// After a stack or split lost a child: an empty split goes, a split of one child becomes that child, and a split inside
// a split of the same direction gives its children to its parent.
export function tidy(node) {
  if (!node) return null;
  if (node.t === 'stack') return node.panels.length ? node : null;
  const kids = [];
  for (const k of node.kids) {
    const t = tidy(k);
    if (!t) continue;
    if (t.t === 'split' && t.dir === node.dir) kids.push(...t.kids);
    else kids.push(t);
  }
  if (!kids.length) return null;
  if (kids.length === 1) {
    const only = kids[0];
    if (finite(node.size)) only.size = node.size; else delete only.size;
    return only;
  }
  node.kids = kids;
  return node;
}

/** Takes a panel out of wherever it is. Returns what it was: { kind, home?, float?, entry?, was? }, or null. */
export function detach(layout, id) {
  const w = whereIs(layout, id);
  if (!w) return null;
  if (w.kind === 'dock') {
    const home = homeOf(layout, id);
    const s = w.stack;
    const at = s.panels.indexOf(id);
    s.panels.splice(at, 1);
    if (s.active === id) s.active = s.panels[Math.max(0, at - 1)];
    layout.root = tidy(layout.root);
    return { kind: 'dock', home };
  }
  if (w.kind === 'float') {
    const s = w.stack;
    const at = s.panels.indexOf(id);
    s.panels.splice(at, 1);
    if (s.active === id) s.active = s.panels[Math.max(0, at - 1)];
    if (!s.panels.length) layout.floats.splice(layout.floats.indexOf(w.float), 1);
    const { x, y, w: fw, h } = w.float;
    const was = { kind: 'float', home: w.float.home || null, float: { x, y, w: fw, h } };
    if (s.panels.length) { was.peers = s.panels.slice(); was.index = at; } // back into this floating stack while it lasts
    return was;
  }
  if (w.kind === 'auto') {
    layout.auto.splice(layout.auto.indexOf(w.entry), 1);
    return { kind: 'auto', home: w.entry.home || null, entry: { edge: w.entry.edge, size: w.entry.size } };
  }
  if (w.kind === 'out') {
    delete layout.out[id];
    const was = w.entry.was || { kind: 'dock', home: null };
    return { kind: 'out', home: was.home || null, was };
  }
  layout.hidden.splice(layout.hidden.indexOf(w.entry), 1);
  return { kind: 'hidden', was: w.entry.was || null };
}

// What a panel taken from its place (detach's answer) needs to go back there: { kind: 'dock' | 'float' | 'auto', home,
// float?, edge?, size? }. A panel that was out or hidden keeps what it had.
function wasOf(was) {
  if (was.kind === 'out' || was.kind === 'hidden') return was.was ? { ...was.was } : { kind: 'dock', home: null };
  const keep = { kind: was.kind, home: was.home || null };
  if (was.float) keep.float = was.float;
  if (was.peers) { keep.peers = was.peers.slice(); keep.index = was.index; }
  if (was.entry) { keep.edge = was.entry.edge; keep.size = was.entry.size; }
  return keep;
}

// Puts panel `id` into stack `s` where it was among `peers` (the stack's other panels then; it stood before
// peers[index]): after the nearest of the panels before it that is still there, else before the nearest after it.
function insertAmong(s, id, peers, index) {
  const at = finite(index) ? Math.max(0, Math.min(peers.length, index)) : peers.length;
  let i = -1;
  for (let k = at - 1; k >= 0 && i < 0; k--) { const j = s.panels.indexOf(peers[k]); if (j >= 0) i = j + 1; }
  for (let k = at; k < peers.length && i < 0; k++) { const j = s.panels.indexOf(peers[k]); if (j >= 0) i = j; }
  s.panels.splice(i < 0 ? s.panels.length : i, 0, id);
  s.active = id;
}

// The floating stack a panel that was in one goes back to: the one holding most of its peers, and of those the one
// still at its rectangle, or null.
function peerFloatOf(layout, was) {
  const peers = was.peers || [];
  let best = null, most = 0;
  for (const f of layout.floats) {
    const n = f.stack.panels.filter((p) => peers.includes(p)).length;
    if (!n) continue;
    const there = was.float && ['x', 'y', 'w', 'h'].every((k) => f[k] === was.float[k]);
    if (n > most || (n === most && there)) { best = f; most = n; }
  }
  return best;
}

// Puts a panel back as `was` says: docked, floating or unpinned.
function restore(layout, id, was, opts) {
  const peerFloat = was.kind === 'float' ? peerFloatOf(layout, was) : null;
  if (peerFloat) insertAmong(peerFloat.stack, id, was.peers, was.index);
  else if (was.kind === 'float' && was.float) layout.floats.push({ stack: stackNode([id]), ...was.float, home: was.home || null });
  else if (was.kind === 'auto') {
    const unpin = cfgOf(opts).sizes.unpinSize;
    layout.auto.push({ id, edge: EDGES.includes(was.edge) ? was.edge : 'right', size: finite(was.size) ? was.size : unpin, home: was.home || null });
  } else placeDocked(layout, id, was.home, opts);
}

/** Docks panel `id` beside the stack found at `at`, on `side`. `dims(node)` measures a node ({ w, h }) when it can. */
function dockBeside(layout, at, id, side, size, dims) {
  const node = stackNode([id]);
  const dir = dirOf(side);
  const target = at.stack;
  const parent = at.chain.length ? at.chain[at.chain.length - 1] : null;
  const measured = dims && dims(target);
  const along = measured ? (dir === 'row' ? measured.w : measured.h) : 0;
  const half = Math.round((along || (parent && parent.split.dir === dir && finite(target.size) ? target.size : 600)) / 2);
  if (parent && parent.split.dir === dir) {
    parent.split.kids.splice(parent.i + (isBefore(side) ? 0 : 1), 0, node);
    node.size = finite(size) ? size : half;
    if (!finite(size) && finite(target.size)) target.size = Math.max(half, target.size - node.size);
    return node;
  }
  const sp = splitNode(dir, isBefore(side) ? [node, target] : [target, node]);
  if (finite(target.size)) sp.size = target.size;
  delete target.size;
  node.size = finite(size) ? size : half;
  replaceIn(layout, at, sp);
  return node;
}

/** Docks panel `id` along an edge of the whole dock. `extent` is the dock's { w, h } when known. */
export function dockAtEdge(layout, id, side, size, extent, cfg = makeConfig()) {
  const node = stackNode([id]);
  const dir = dirOf(side);
  const span = extent ? (dir === 'row' ? extent.w : extent.h) : 0;
  const min = cfg.minSize(id);
  node.size = finite(size) ? size : Math.max(dir === 'row' ? min.w : min.h, Math.round((span || 1200) * 0.25));
  if (!layout.root) { delete node.size; layout.root = node; return node; }
  const root = layout.root;
  if (root.t === 'split' && root.dir === dir) {
    if (isBefore(side)) root.kids.unshift(node); else root.kids.push(node);
    return node;
  }
  delete root.size;
  layout.root = splitNode(dir, isBefore(side) ? [node, root] : [root, node]);
  return node;
}

// The smallest node holding every panel of `ids` still in the tree, as { stack: node, chain } (the node may be a split).
function enclosing(root, ids) {
  const here = ids.filter((p) => contains(root, p));
  if (!here.length) return null;
  let node = root;
  const chain = [];
  for (;;) {
    if (node.t !== 'split') break;
    const i = node.kids.findIndex((k) => here.every((p) => contains(k, p)));
    if (i < 0) break;
    chain.push({ split: node, i });
    node = node.kids[i];
  }
  return { stack: node, chain };
}

// Back where `home` says: into the stack holding most of the panels it shared one with, else beside the panel it was next to.
function placeAtHome(layout, id, home, dims) {
  if (!home) return false;
  let best = null, most = 0;
  for (const p of home.peers || []) {
    const at = locate(layout.root, p);
    const n = at ? at.stack.panels.filter((q) => home.peers.includes(q)).length : 0;
    if (n > most) { best = at; most = n; }
  }
  if (best) { insertAmong(best.stack, id, home.peers, home.index); return true; }
  if (home.near && EDGES.includes(home.side)) {
    const at = home.nearAll ? enclosing(layout.root, home.nearAll) : locate(layout.root, home.near);
    if (at) { dockBeside(layout, at, id, home.side, home.size, dims); return true; }
  }
  return false;
}

/** Docks a panel that is not docked: at its home, else where the default layout has it, else on its default edge. */
export function placeDocked(layout, id, home, opts = {}) {
  const cfg = cfgOf(opts);
  if (placeAtHome(layout, id, home, opts.dims)) return;
  if (placeAtHome(layout, id, homeOf(cfg.defaultLayout({ viewportPx: opts.viewportPx || 1600, purpose: 'home' }), id), opts.dims)) return;
  dockAtEdge(layout, id, cfg.edgeOf(id), undefined, opts.extent, cfg);
}

/**
 * Moves panel `id` to `target` ({ kind: 'root' } or { kind: 'stack', stack }) on `side` (an edge, or 'center' for a tab).
 * Returns false when the move changes nothing.
 */
export function moveTo(layout, id, target, side, opts = {}) {
  const cfg = cfgOf(opts);
  if (target.kind === 'stack') {
    const s = target.stack;
    if (s.panels.includes(id) && (side === 'center' || s.panels.length === 1)) return false;
  }
  const was = detach(layout, id);
  if (!was) return false;
  if (target.kind === 'root') {
    if (side === 'center' || !layout.root) {
      if (!layout.root) { layout.root = stackNode([id]); return true; }
      side = cfg.edgeOf(id);
    }
    dockAtEdge(layout, id, side, undefined, opts.extent, cfg);
    return true;
  }
  const s = target.stack;
  if (side === 'center') {
    s.panels.push(id);
    s.active = id;
    return true;
  }
  const at = layout.root && findStack(layout.root, s);
  if (at) { dockBeside(layout, at, id, side, undefined, opts.dims); return true; }
  // A floating stack takes tabs only; anything else puts the panel back.
  placeDocked(layout, id, was.home, opts);
  return true;
}

/** Floats a panel at `rect` ({ x, y, w, h } inside the dock). A docked panel remembers where it was. */
export function floatPanel(layout, id, rect) {
  const was = detach(layout, id);
  if (!was) return null;
  const f = { stack: stackNode([id]), x: rect.x, y: rect.y, w: rect.w, h: rect.h, home: was.home || null };
  layout.floats.push(f);
  return f;
}

/** Docks a floating window's panels back where they came from, in one stack as they were. */
export function dockBack(layout, f, opts = {}) {
  const i = layout.floats.indexOf(f);
  if (i < 0) return false;
  layout.floats.splice(i, 1);
  const [first, ...rest] = f.stack.panels;
  placeDocked(layout, first, f.home, opts);
  const at = locate(layout.root, first);
  for (const p of rest) at.stack.panels.push(p);
  at.stack.active = f.stack.active;
  return true;
}

/** Unpins a panel: it leaves the layout for a strip on `edge`, `size` px deep when it slides out. */
export function unpinPanel(layout, id, edge, size, opts = {}) {
  const was = detach(layout, id);
  if (!was) return null;
  const entry = { id, edge: EDGES.includes(edge) ? edge : 'right', size: finite(size) ? Math.round(size) : cfgOf(opts).sizes.unpinSize, home: was.home || null };
  layout.auto.push(entry);
  return entry;
}

/** Pins an unpinned panel: docked again where it was. */
export function pinPanel(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w || w.kind !== 'auto') return false;
  const was = detach(layout, id);
  placeDocked(layout, id, was.home, opts);
  return true;
}

/** Hides a panel; `showPanel` puts it back as it was (docked, floating or unpinned). One that was out comes back where
 * it was before it went out. */
export function hidePanel(layout, id) {
  const w = whereIs(layout, id);
  if (!w || w.kind === 'hidden') return false;
  layout.hidden.push({ id, was: wasOf(detach(layout, id)) });
  return true;
}

export function showPanel(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w || w.kind !== 'hidden') return false;
  restore(layout, id, detach(layout, id).was || { kind: 'dock' }, opts);
  return true;
}

/**
 * Pops a panel out into a window of its own: it leaves the layout (its tab, if it shared a stack; else its stack, and the
 * neighbours take the room), and `out` remembers where it was and `geo` ({ x, y, w, h }), where its window is. A
 * hidden panel is shown first. Returns the entry, or null.
 */
export function popOutPanel(layout, id, geo = {}, opts = {}) {
  const w = whereIs(layout, id);
  if (!w) return null;
  if (w.kind === 'out') { Object.assign(w.entry, pickGeo(geo)); return w.entry; }
  if (w.kind === 'hidden') showPanel(layout, id, opts);
  const entry = { ...pickGeo(geo), was: wasOf(detach(layout, id)) };
  if (!layout.out) layout.out = {};
  layout.out[id] = entry;
  return entry;
}

/**
 * Back from its own window, where it was: the same stack, the same side of the same neighbour, its float or its strip.
 * If that is gone, where the default layout has it, else on its default edge.
 */
export function popInPanel(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w || w.kind !== 'out') return false;
  restore(layout, id, detach(layout, id).was, opts);
  return true;
}

function pickGeo(g) {
  const out = {};
  for (const k of ['x', 'y', 'w', 'h']) if (g && finite(g[k])) out[k] = Math.round(g[k]);
  return out;
}

export function activate(layout, id) {
  const w = whereIs(layout, id);
  if (!w || (w.kind !== 'dock' && w.kind !== 'float')) return false;
  if (w.stack.active === id) return false;
  w.stack.active = id;
  return true;
}

// ---------- a stored layout ----------

// A stored `home`, made safe. It keeps the fields it has and adds none, so a layout that comes through here again (a
// reload, or narrow and back) comes out the same: an `index` it lacks stays absent (the panel goes after its peers).
function normHome(h) {
  if (!h || typeof h !== 'object') return null;
  const out = {};
  if ('peers' in h) out.peers = Array.isArray(h.peers) ? h.peers.filter((p) => typeof p === 'string') : [];
  if (finite(h.index)) out.index = h.index;
  if ('near' in h) out.near = typeof h.near === 'string' ? h.near : null;
  if ('side' in h) out.side = EDGES.includes(h.side) ? h.side : null;
  if (finite(h.size) && h.size > 0) out.size = h.size;
  if (Array.isArray(h.nearAll)) { const all = h.nearAll.filter((p) => typeof p === 'string'); if (all.length) out.nearAll = all; }
  return out;
}
// `out` with `from`'s home made safe, when `from` has one (null included); with none when it has none.
function withHome(out, from) {
  if ('home' in from) out.home = normHome(from.home);
  return out;
}

/**
 * A stored layout, made safe for today's panels (cfg.ids): unknown panels and repeats are dropped, a panel it does not
 * mention goes where the default has it, and a layout that shows nothing, or is not a layout (or of another version,
 * unless `opts.migrate` turns it into this one), is the default.
 */
export function normalizeLayout(raw, opts = {}) {
  const cfg = cfgOf(opts);
  const ids = cfg.ids;
  const unpin = cfg.sizes.unpinSize;
  const fallback = () => cfg.defaultLayout({ viewportPx: opts.viewportPx || 1600, purpose: opts.purpose || 'load' });
  if (raw && typeof raw === 'object' && raw.v !== LAYOUT_VERSION && typeof opts.migrate === 'function') {
    try { raw = opts.migrate(raw); } catch { raw = null; }
  }
  if (!raw || typeof raw !== 'object' || raw.v !== LAYOUT_VERSION) return fallback();
  const known = new Set(ids);
  const seen = new Set();
  const take = (p) => typeof p === 'string' && known.has(p) && !seen.has(p) && (seen.add(p), true);
  const size = (n, out) => { if (finite(n.size) && n.size > 0) out.size = Math.round(n.size); return out; };
  const normStack = (n) => {
    if (!n || n.t !== 'stack' || !Array.isArray(n.panels)) return null;
    const panels = n.panels.filter(take);
    if (!panels.length) return null;
    const s = stackNode(panels, panels.includes(n.active) ? n.active : panels[0]);
    if (n.min === true) s.min = true;
    return size(n, s);
  };
  const norm = (n, depth) => {
    if (!n || typeof n !== 'object' || depth > 12) return null;
    if (n.t === 'stack') return normStack(n);
    if (n.t !== 'split' || (n.dir !== 'row' && n.dir !== 'col') || !Array.isArray(n.kids)) return null;
    return size(n, splitNode(n.dir, n.kids.map((k) => norm(k, depth + 1)).filter(Boolean)));
  };
  const layout = { v: LAYOUT_VERSION, root: tidy(norm(raw.root, 0)), floats: [], auto: [], hidden: [], out: {} };
  for (const f of Array.isArray(raw.floats) ? raw.floats : []) {
    const s = f && normStack(f.stack);
    if (!s) continue;
    delete s.size;
    layout.floats.push(withHome({ stack: s, x: finite(f.x) ? f.x : 80, y: finite(f.y) ? f.y : 60, w: finite(f.w) && f.w > 0 ? f.w : 480,
      h: finite(f.h) && f.h > 0 ? f.h : 360 }, f));
  }
  for (const a of Array.isArray(raw.auto) ? raw.auto : []) {
    if (!a || !take(a.id)) continue;
    layout.auto.push(withHome({ id: a.id, edge: EDGES.includes(a.edge) ? a.edge : 'right', size: finite(a.size) && a.size > 0 ? a.size : unpin }, a));
  }
  const normWas = (w) => {
    const was = w && typeof w === 'object' ? w : {};
    const keep = withHome({ kind: ['dock', 'float', 'auto'].includes(was.kind) ? was.kind : 'dock' }, was);
    if (keep.kind === 'float' && was.float && ['x', 'y', 'w', 'h'].every((k) => finite(was.float[k]))) keep.float = { ...was.float };
    else if (keep.kind === 'float') keep.kind = 'dock';
    if (keep.kind === 'float' && Array.isArray(was.peers)) {
      const peers = was.peers.filter((p) => typeof p === 'string');
      if (peers.length) { keep.peers = peers; keep.index = finite(was.index) ? was.index : peers.length; }
    }
    if (keep.kind === 'auto') { keep.edge = EDGES.includes(was.edge) ? was.edge : 'right'; keep.size = finite(was.size) ? was.size : unpin; }
    return keep;
  };
  for (const h of Array.isArray(raw.hidden) ? raw.hidden : []) {
    if (!h || !take(h.id)) continue;
    layout.hidden.push(h.was === undefined ? { id: h.id } : { id: h.id, was: normWas(h.was) });
  }
  // The panels in windows of their own, with where those windows were. One stored with `was` has no place in the
  // layout; one stored before (no `was`) still has its place there, and leaves it below.
  const out = raw.out && typeof raw.out === 'object' && !Array.isArray(raw.out) ? raw.out : {};
  const older = [];
  for (const id of Object.keys(out)) {
    const g = out[id] && typeof out[id] === 'object' ? out[id] : {};
    if (g.was && typeof g.was === 'object') { if (take(id)) layout.out[id] = { ...pickGeo(g), was: normWas(g.was) }; }
    else if (known.has(id)) older.push([id, g]);
  }
  // One that names none of today's panels says nothing about them: the default, not each panel placed one by one.
  if (!seen.size) return fallback();
  // A panel the stored layout does not know (added since it was stored) goes where the default has it.
  for (const id of ids) if (!seen.has(id)) placeDocked(layout, id, null, opts);
  const visible = ids.filter((id) => { const w = whereIs(layout, id); return w && w.kind !== 'hidden'; });
  if (!visible.length) return fallback();
  // A panel a layout stored before was out and still in its place (a hidden one is not out): it leaves its place now.
  for (const [id, g] of older) {
    const w = whereIs(layout, id);
    if (w && w.kind !== 'hidden' && w.kind !== 'out') popOutPanel(layout, id, g, opts);
  }
  return layout;
}

/** Keeps a floating window inside a dock `W` x `H`: no bigger than it, and wholly on it. */
export function clampFloat(f, W, H, floatMin = SIZES.floatMin) {
  if (!(W > 0) || !(H > 0)) return f;
  f.w = Math.round(Math.max(Math.min(floatMin.w, W), Math.min(f.w, W)));
  f.h = Math.round(Math.max(Math.min(floatMin.h, H), Math.min(f.h, H)));
  f.x = Math.round(Math.max(0, Math.min(f.x, W - f.w)));
  f.y = Math.round(Math.max(0, Math.min(f.y, H - f.h)));
  return f;
}
