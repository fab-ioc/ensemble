// The layout model: plain JSON, so it is stored as it is. No DOM here; dock.js draws it.
//
//   { v, root, floats: [{ stack, x, y, w, h, home, strip?, side? }], auto: [{ id, edge, size, home, open? }],
//     hidden: [{ id, was }], out: { id: { x, y, w, h, was } }, pinned?: { id: strip }, depth?: { id: { w?, h? } },
//     parked?: [{ id, was, window?, pinned? }] }
//
// A node is a stack { t: 'stack', panels: [id], active: id, min?, size? } or a split { t: 'split', dir: 'row'|'col',
// kids: [node], size? }. `size` is a node's px along its parent split; one child of each split (the one holding the
// `fill` panel, else the largest) takes what is left. `home` is where a panel was docked: the panels it shared a stack
// with, or the panel beside it and on which side, so it goes back there. `out` names the panels popped out into a
// browser window of their own, with that window's screen position and size ({ x, y, w, h }). A panel that is out has
// no place in the layout (its tab goes, or its whole stack, and the neighbours take the room); `was` says where it
// was, as a hidden panel's does, and it goes back there.
//
// A strip panel's place is its edge, its size and where it stood among that edge's other panels: a `strip`
// ({ id, edge, size, peers, index }). A panel taken off its strip (floated, popped out, hidden) keeps it and goes back
// there, into the same place in the strip. A float made from a strip panel keeps it as `strip`, so Dock back takes the
// panel back to its strip, not docked; `pinned` keeps it for a panel pinned from its strip, so Unpin puts it back there.
//
// A panel's side (v0.5.0, as an IntelliJ tool window's): the edge it belongs to, whatever its view mode. A strip panel's
// is its strip's edge; a docked panel's is where it stands against the middle (sideOf); a floating one's, or one in its
// own window, is the side it goes back to. Changing the view mode keeps it: a strip panel pinned docks on its strip's
// side (pinPanel), a docked one unpinned goes to the strip on its side. Only a move changes it (moveSide, moveStrip, or a drag).
// A strip entry's `open` ('beside' | 'over') is how it slides out when it differs from the dock's stripOpen: IntelliJ's
// Dock Unpinned and Undock. A float's `side`, or a `was`'s, is a side given by moveSide while the panel was there.
//
// Panels added and removed at runtime (v0.10.0): `parked` keeps the place of a panel the app removed (removePanel), and
// of one a stored layout names that the app has not added yet (after a reload), as a hidden panel's `was` does, so
// adding it again puts it back there. A parked panel is drawn nowhere and is no tab. Beyond cfg.keepSlots the oldest go.
//
// Everything that depends on the app (its panels, their minimum sizes, their default edges and layout) comes from a
// config made by makeConfig(); every function that needs it takes it as `opts.cfg` (or `cfg`).

export const LAYOUT_VERSION = 1;
/** How many places of removed panels a layout keeps by default (makeConfig's keepSlots); the oldest go first. */
export const PARK_MAX = 50;
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
 *   unpinSize      { id: px } or (id) => px: how far that panel slides out of its strip when nothing else says (at
 *                  least its minSize); default sizes.unpinSize. cfg.unpinSizeOf(id, edge) is it, or null when not given
 *   sizes          overrides of SIZES
 *   keepSlots      how many places of removed (or not yet added) panels a layout keeps in `parked`; default PARK_MAX
 *
 * cfg.add(id) and cfg.drop(id) add a panel at runtime and take one away (cfg.ids follows them). The default layout puts
 * a panel added so as a tab in the middle (placeNew), not on its edge.
 */
export function makeConfig(o = {}) {
  const sizes = { ...SIZES, ...(o.sizes || {}) };
  const ids = Array.isArray(o.ids) ? o.ids.slice() : [];
  const table = (v, fallback) => (typeof v === 'function' ? (id) => v(id) || fallback(id) : (id) => (v && v[id]) || fallback(id));
  const minSize = table(o.minSize, () => sizes.panelMin);
  const edgeOf = table(o.edgeOf, () => 'right');
  const cfg = { ids, sizes, minSize, edgeOf, fill: o.fill || null, defaultSize: typeof o.defaultSize === 'function' ? o.defaultSize : null,
    keepSlots: finite(o.keepSlots) && o.keepSlots >= 0 ? Math.floor(o.keepSlots) : PARK_MAX, added: new Set() };
  cfg.add = (id) => { if (!ids.includes(id)) { ids.push(id); cfg.added.add(id); } };
  cfg.drop = (id) => { const i = ids.indexOf(id); if (i >= 0) ids.splice(i, 1); cfg.added.delete(id); };
  const unpinOf = table(o.unpinSize, () => null);
  cfg.unpinSizeOf = (id, edge) => {
    const v = unpinOf(id);
    if (!finite(v) || v <= 0) return null;
    const m = minSize(id);
    return Math.round(Math.max(edge === 'top' || edge === 'bottom' ? m.h : m.w, v));
  };
  const declared = o.defaultLayout;
  cfg.defaultLayout = (ctx = {}) => {
    const c = { viewportPx: 1600, purpose: 'load', ...ctx };
    let d = typeof declared === 'function' ? declared(c) : declared ? clone(declared) : null;
    if (d && d.t) d = { root: d };
    const layout = { v: LAYOUT_VERSION, root: null, floats: [], auto: [], hidden: [], out: {} };
    if (d) {
      layout.root = d.root ? clone(d.root) : null;
      for (const k of ['floats', 'auto', 'hidden']) if (Array.isArray(d[k])) layout[k] = clone(d[k]);
      // With panel unpinSizes given, a strip panel declared without a size slides out as far as its own says, else
      // sizes.unpinSize. Without them the declared entries stay as declared (v0.3.6).
      if (o.unpinSize && (typeof o.unpinSize === 'function' || Object.keys(o.unpinSize).length)) for (const a of layout.auto) if (a && !finite(a.size)) a.size = unpinFallback(cfg, a.id, a.edge);
    } else if (ids.length) {
      layout.root = ids.length === 1 ? stackNode([ids[0]]) : splitNode('row', ids.map((id) => stackNode([id])));
    }
    // A panel the default does not mention goes on its edge; one added at runtime, as a tab in the middle.
    for (const id of ids) if (!whereIs(layout, id) && !cfg.added.has(id)) dockAtEdge(layout, id, cfg.edgeOf(id), undefined, null, cfg);
    for (const id of ids) if (!whereIs(layout, id)) placeNew(layout, id, null, cfg);
    return layout;
  };
  return cfg;
}

const cfgOf = (opts) => (opts && opts.cfg) || makeConfig();
// How far an unpinned panel slides out when nothing else says: its own unpinSize, else sizes.unpinSize.
function unpinFallback(cfg, id, edge) {
  const own = typeof cfg.unpinSizeOf === 'function' ? cfg.unpinSizeOf(id, edge) : null;
  return own !== null ? own : cfg.sizes.unpinSize;
}

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
  if (finite(at.stack.size)) home.size = at.stack.size; else home.free = true; // free: it had no size, it shared the space
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
    const was = { kind: 'float', home: w.float.strip ? homeIn(w.float) : w.float.home || null, float: { x, y, w: fw, h } };
    if (EDGES.includes(w.float.side)) was.side = w.float.side;
    if (s.panels.length) { was.peers = s.panels.slice(); was.index = at; } // back into this floating stack while it lasts
    const strip = w.float.strip;
    if (strip && strip.id === id) { was.strip = { ...strip }; if (s.panels.length) delete w.float.strip; }
    return was;
  }
  if (w.kind === 'auto') {
    const peers = layout.auto.filter((a) => a.edge === w.entry.edge).map((a) => a.id);
    const index = peers.indexOf(id);
    peers.splice(index, 1);
    layout.auto.splice(layout.auto.indexOf(w.entry), 1);
    const entry = withOpen({ edge: w.entry.edge, size: w.entry.size }, w.entry.open);
    return { kind: 'auto', home: homeIn(w.entry), entry, peers, index };
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
  const keep = { kind: was.kind, home: orNone(was.home) };
  if (was.float) keep.float = was.float;
  if (was.peers) { keep.peers = was.peers.slice(); keep.index = was.index; }
  if (was.entry) { keep.edge = was.entry.edge; keep.size = was.entry.size; withOpen(keep, was.entry.open); }
  if (was.strip) keep.strip = { ...was.strip };
  if (was.side) keep.side = was.side;
  return keep;
}

// A home kept as it was given: an object, null, or none at all (undefined, which JSON leaves out): a strip entry in an
// app's defaultLayout has no `home`, and one back on its strip has none either, so the stored layout is as it was.
const orNone = (h) => (h === undefined ? undefined : h || null);
const homeIn = (x) => ('home' in x ? orNone(x.home) : undefined);
// Sets `home` on `x` unless there is none.
const putHome = (x, h) => { if (h !== undefined) x.home = h || null; return x; };
// Two homes the same, whatever their keys' order.
const sameHome = (a, b) => { const k = (h) => JSON.stringify(h, Object.keys(h || {}).sort()); return !!a && !!b && k(a) === k(b); };

// A strip panel's place, from what detach answered for it: { id, edge, size, peers, index }.
function stripOf(id, was) {
  return withOpen({ id, edge: was.entry.edge, size: was.entry.size, peers: (was.peers || []).slice(), index: was.index }, was.entry.open);
}
export const OPENS = ['beside', 'over'];
// `entry` with the slide-out style `open` when it has one.
function withOpen(entry, open) { if (OPENS.includes(open)) entry.open = open; return entry; }

// Puts strip entry `entry` into layout.auto where it stood among `peers` (that edge's other panels then, in order; it
// stood before peers[index]): after the nearest of the ones before it still on that edge, else before the nearest
// after it, else last. The strip draws an edge's panels in layout.auto's order.
function insertAuto(layout, entry, peers, index) {
  const list = Array.isArray(peers) ? peers : [];
  const at = finite(index) ? Math.max(0, Math.min(list.length, index)) : list.length;
  const on = (p) => layout.auto.findIndex((a) => a.id === p && a.edge === entry.edge);
  let i = -1;
  for (let k = at - 1; k >= 0 && i < 0; k--) { const j = on(list[k]); if (j >= 0) i = j + 1; }
  for (let k = at; k < list.length && i < 0; k++) { const j = on(list[k]); if (j >= 0) i = j; }
  layout.auto.splice(i < 0 ? layout.auto.length : i, 0, entry);
  forgetPinned(layout, entry.id);
  return entry;
}
function forgetPinned(layout, id) {
  if (!layout.pinned) return;
  delete layout.pinned[id];
  if (!Object.keys(layout.pinned).length) delete layout.pinned;
}

// Puts panel `id` into stack `s` where it was among `peers` (the stack's other panels then; it stood before
// peers[index]): after the nearest of the panels before it that is still there, else before the nearest after it.
function insertAmong(s, id, peers, index) {
  insertNear(s.panels, id, peers, index);
  s.active = id;
}
// The same for a plain list: `id` into `list` where it stood among `peers`.
function insertNear(list, id, peers, index) {
  const at = finite(index) ? Math.max(0, Math.min(peers.length, index)) : peers.length;
  let i = -1;
  for (let k = at - 1; k >= 0 && i < 0; k--) { const j = list.indexOf(peers[k]); if (j >= 0) i = j + 1; }
  for (let k = at; k < peers.length && i < 0; k++) { const j = list.indexOf(peers[k]); if (j >= 0) i = j; }
  list.splice(i < 0 ? list.length : i, 0, id);
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
  if (peerFloat) {
    insertAmong(peerFloat.stack, id, was.peers, was.index);
    if (was.strip && !peerFloat.strip) peerFloat.strip = { ...was.strip };
  } else if (was.kind === 'float' && was.float) {
    const f = putHome({ stack: stackNode([id]), ...was.float }, orNone(was.home));
    if (was.strip) f.strip = { ...was.strip };
    if (EDGES.includes(was.side)) f.side = was.side;
    layout.floats.push(f);
  } else if (was.kind === 'auto') {
    const edge = EDGES.includes(was.edge) ? was.edge : 'right';
    const entry = putHome({ id, edge, size: finite(was.size) ? was.size : unpinFallback(cfgOf(opts), id, edge) }, orNone(was.home));
    insertAuto(layout, withOpen(entry, was.open), was.peers, was.index);
  } else placeDocked(layout, id, was.home, { ...opts, side: was.side });
}

const FREE = 'free';
/** Docks panel `id` beside the stack found at `at`, on `side`. `dims(node)` measures a node ({ w, h }) when it can.
 * `size` FREE: back without a size of its own, sharing the space as it did before (in a split along `side`). */
function dockBeside(layout, at, id, side, size, dims) {
  const node = stackNode([id]);
  if (size === FREE) {
    const up = at.chain.length ? at.chain[at.chain.length - 1] : null;
    if (up && up.split.dir === dirOf(side)) { up.split.kids.splice(up.i + (isBefore(side) ? 0 : 1), 0, node); return node; }
    size = null;
  }
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
function placeAtHome(layout, id, home, dims, size) {
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
    if (at) { dockBeside(layout, at, id, home.side, home.free ? FREE : finite(home.size) ? home.size : size, dims); return true; }
  }
  return false;
}

/**
 * Docks a panel that is not docked: at its home, else where the default layout has it, else on its default edge.
 * `opts.side`: the side it must dock on (a strip panel pinned, a panel moved to a side): a home that would put it
 * elsewhere is passed over, and with none left it docks along that edge of the dock, `opts.size` px (its strip's depth).
 * `opts.size` is also its size beside a home that kept none.
 */
export function placeDocked(layout, id, home, opts = {}) {
  const cfg = cfgOf(opts);
  const want = EDGES.includes(opts.side) ? opts.side : null;
  const size = finite(opts.size) ? Math.round(opts.size) : undefined;
  const at = (h) => {
    if (!h) return false;
    if (want) {
      const t = clone(layout);
      if (!placeAtHome(t, id, h, null, size) || sideOf(t, id, cfg) !== want) return false;
    }
    return placeAtHome(layout, id, h, opts.dims, size);
  };
  if (at(home)) return;
  if (at(homeOf(cfg.defaultLayout({ viewportPx: opts.viewportPx || 1600, purpose: 'home' }), id))) return;
  dockAtEdge(layout, id, want || cfg.edgeOf(id), want ? size : undefined, opts.extent, cfg);
}

// The child of split `sp` that takes what is left (dock.js draws it so): the one holding the fill panel, else the
// largest (one without a size is), never a minimised stack.
function fillOf(sp, cfg) {
  if (cfg.fill) { const i = sp.kids.findIndex((k) => contains(k, cfg.fill)); if (i >= 0) return i; }
  let best = -1, bestSize = -1;
  sp.kids.forEach((k, i) => {
    if (k.t === 'stack' && k.min) return;
    const s = finite(k.size) ? k.size : Infinity;
    if (s > bestSize) { best = i; bestSize = s; }
  });
  return best;
}

/**
 * The side of the dock a docked panel stands on: from the top of the tree down, the first split where it is not in the
 * child that takes what is left (the middle) puts it before that child (left, top) or after it (right, bottom). null
 * for a panel in the middle itself, or alone, or not docked.
 */
export function sideOf(layout, id, cfg = makeConfig()) {
  const at = locate(layout.root, id);
  if (!at) return null;
  for (const { split: sp, i } of at.chain) {
    const f = fillOf(sp, cfg);
    if (f < 0 || i === f) continue;
    return sp.dir === 'row' ? (i < f ? 'left' : 'right') : (i < f ? 'top' : 'bottom');
  }
  return null;
}

// The side a panel that is away (`was`, as kept by `out` or `hidden`) goes back to.
function sideOfWas(layout, id, was, cfg) {
  if (!was) return cfg.edgeOf(id);
  if (was.kind === 'auto') return EDGES.includes(was.edge) ? was.edge : 'right';
  if (EDGES.includes(was.side)) return was.side;
  if (was.strip && EDGES.includes(was.strip.edge)) return was.strip.edge;
  const t = clone(layout);
  placeDocked(t, id, was.home, { cfg });
  return sideOf(t, id, cfg) || cfg.edgeOf(id);
}

/**
 * A panel's side, whatever its view mode: its strip's edge; where it is docked (sideOf; in the middle, the strip it was
 * pinned from, else its default edge); for a float or a window, the side it goes back to.
 */
export function panelSide(layout, id, opts = {}) {
  const cfg = cfgOf(opts);
  const w = whereIs(layout, id);
  if (!w) return null;
  if (w.kind === 'auto') return w.entry.edge;
  if (w.kind === 'dock') return sideOf(layout, id, cfg) || (layout.pinned && layout.pinned[id] ? layout.pinned[id].edge : cfg.edgeOf(id));
  if (w.kind === 'float') {
    const f = w.float;
    if (EDGES.includes(f.side)) return f.side;
    if (f.strip && f.strip.id === id) return f.strip.edge;
    return sideOfWas(layout, id, { kind: 'dock', home: f.home || null }, cfg);
  }
  return sideOfWas(layout, id, w.entry.was, cfg);
}

/**
 * A panel's view mode, by IntelliJ's names: 'pinned' (Dock Pinned: docked), 'unpinned' (Dock Unpinned: on a strip,
 * sliding out beside the middle), 'undock' (Undock: on a strip, sliding out over it), 'float', 'window' (in a window of
 * its own), or 'hidden'. `open` is the dock's stripOpen, for a strip entry without its own.
 */
export function viewModeOf(layout, id, open = 'over') {
  const w = whereIs(layout, id);
  if (!w) return null;
  if (w.kind === 'dock') return 'pinned';
  if (w.kind === 'auto') return (w.entry.open || open) === 'beside' ? 'unpinned' : 'undock';
  if (w.kind === 'float') return 'float';
  if (w.kind === 'out' || (w.kind === 'hidden' && w.entry.window)) return 'window';
  return 'hidden';
}

// A panel's depth on each axis (IntelliJ keeps a tool window's size): `layout.depth[id]` is { w?, h? }, its px
// across a left/right side (w) and a top/bottom one (h), as it was when Move To took it off that axis. Moved back onto
// an axis it has been on, it is that deep again; the first time, as deep as its default says (depthDefault).
const axisOf = (side) => (side === 'left' || side === 'right' ? 'w' : 'h');
function keepDepth(layout, id, axis, px) {
  if (!finite(px) || px <= 0) return;
  const all = layout.depth || (layout.depth = {});
  (all[id] || (all[id] = {}))[axis] = Math.round(px);
}
function depthOn(layout, id, axis) {
  const d = layout.depth && layout.depth[id];
  return d && finite(d[axis]) && d[axis] > 0 ? d[axis] : null;
}
// A docked panel's depth on `side` the first time it goes there: its stack's size in the default layout when it stands
// along that axis there, else the app's defaultSize, else none (dockAtEdge's own).
function depthDefault(id, side, opts) {
  const cfg = cfgOf(opts);
  const dir = dirOf(side);
  const viewportPx = opts.viewportPx || 1600;
  const def = cfg.defaultLayout({ viewportPx, purpose: 'home' });
  const at = locate(def.root, id);
  const up = at && at.chain.length ? at.chain[at.chain.length - 1] : null;
  if (up && up.split.dir === dir && finite(at.stack.size) && at.stack.size > 0) return at.stack.size;
  const px = cfg.defaultSize ? cfg.defaultSize([id], dir, { viewportPx, extent: opts.extent }) : null;
  return finite(px) && px > 0 ? px : undefined;
}

// A strip place moved to edge `side`: last there, as deep as before along the same direction, else as deep as it was
// last on that axis (layout.depth), else as its own says; the home that pointed at the old side goes.
function stripTo(layout, x, side, id, cfg) {
  if (axisOf(x.edge) !== axisOf(side)) {
    keepDepth(layout, id, axisOf(x.edge), x.size);
    x.size = depthOn(layout, id, axisOf(side)) || unpinFallback(cfg, id, side);
  }
  x.edge = side;
  x.peers = [];
  delete x.index;
  delete x.home;
  return x;
}

/**
 * Moves a panel to another side of the dock and keeps its view mode (IntelliJ's Move To): docked along that edge
 * (`opts.size` px deep when given, else as deep as it last was on that axis: layout.depth), last on that edge's strip,
 * or, floating or in its own window, going back to that side. The strip place a pin kept is forgotten. Returns false
 * when it is on that side already.
 */
export function moveSide(layout, id, side, opts = {}) {
  const cfg = cfgOf(opts);
  if (!EDGES.includes(side)) return false;
  const w = whereIs(layout, id);
  if (!w || w.kind === 'hidden' || panelSide(layout, id, opts) === side) return false;
  forgetPinned(layout, id);
  if (w.kind === 'dock') {
    // Its depth on the axis it leaves is kept (measured by opts.dims, else its stack's size along that split); on
    // the new side it is as deep as it was last on that axis, else as its default says.
    const from = sideOf(layout, id, cfg);
    const at = locate(layout.root, id);
    if (from && !at.stack.min) {
      const d = typeof opts.dims === 'function' ? opts.dims(at.stack) : null;
      const up = at.chain.length ? at.chain[at.chain.length - 1] : null;
      keepDepth(layout, id, axisOf(from), d ? d[axisOf(from)] : up && up.split.dir === dirOf(from) ? at.stack.size : null);
    }
    const size = finite(opts.size) ? opts.size : depthOn(layout, id, axisOf(side)) || depthDefault(id, side, { ...opts, cfg });
    detach(layout, id);
    dockAtEdge(layout, id, side, finite(size) ? Math.round(size) : undefined, opts.extent, cfg);
    return true;
  }
  if (w.kind === 'auto') {
    const e = w.entry;
    layout.auto.splice(layout.auto.indexOf(e), 1);
    const moved = stripTo(layout, e, side, id, cfg);
    delete moved.peers;
    layout.auto.push(moved);
    return true;
  }
  if (w.kind === 'float') {
    const f = w.float;
    if (f.strip && f.strip.id === id) { stripTo(layout, f.strip, side, id, cfg); delete f.home; delete f.side; } else f.side = side;
    return true;
  }
  const was = w.entry.was || (w.entry.was = { kind: 'dock', home: null });
  if (was.kind === 'auto') stripTo(layout, was, side, id, cfg);
  else if (was.strip) { stripTo(layout, was.strip, side, id, cfg); delete was.home; } else was.side = side;
  return true;
}

/**
 * Moves a strip panel to place `index` (from 0) among the panels of the strip on `side` (default: its own), as a drag of
 * its strip button does (IntelliJ's stripe). On another side it keeps its view mode, as moveSide does. The strip draws
 * an edge's panels in layout.auto's order, so the order is that; a panel taken off its strip later (pinned, floated,
 * popped out, hidden) keeps its new place there. Returns false when it is not on a strip, or already there.
 */
export function moveStrip(layout, id, index, side, opts = {}) {
  const cfg = cfgOf(opts);
  const a = layout.auto.find((x) => x.id === id);
  if (!a) return false;
  const edge = EDGES.includes(side) ? side : a.edge;
  if (typeof index === 'string' && index.trim() !== '') index = Number(index);
  if (index != null && !finite(index)) return false;
  // Each strip's order, as drawn: layout.auto interleaves the edges, and its order across them draws nothing.
  const strips = () => EDGES.map((e) => layout.auto.filter((x) => x.edge === e).map((x) => x.id).join()).join('|');
  const was = strips();
  const before = layout.auto.slice();
  layout.auto.splice(layout.auto.indexOf(a), 1);
  if (edge !== a.edge) { stripTo(layout, a, edge, id, cfg); delete a.peers; }
  const peers = layout.auto.filter((x) => x.edge === edge);
  const at = finite(index) ? Math.max(0, Math.min(peers.length, Math.round(index))) : peers.length;
  const i = at < peers.length ? layout.auto.indexOf(peers[at]) : peers.length ? layout.auto.indexOf(peers[peers.length - 1]) + 1 : layout.auto.length;
  layout.auto.splice(i, 0, a);
  if (strips() !== was) return true;
  layout.auto.splice(0, layout.auto.length, ...before);
  return false;
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
  forgetPinned(layout, id); // moved by the person: where it lives now is their choice, not the strip it was pinned from
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
  const f = putHome({ stack: stackNode([id]), x: rect.x, y: rect.y, w: rect.w, h: rect.h }, was.kind === 'auto' ? orNone(was.home) : was.home || null);
  // From a strip: Dock back takes it back there.
  if (was.kind === 'auto') f.strip = stripOf(id, was);
  layout.floats.push(f);
  return f;
}

// Back to its strip, where `strip` says it was ({ id, edge, size, peers, index }), with `home` (where Pin docks it).
function backToStrip(layout, id, strip, home, opts) {
  const edge = EDGES.includes(strip.edge) ? strip.edge : 'right';
  const size = finite(strip.size) ? strip.size : unpinFallback(cfgOf(opts), id, edge);
  return insertAuto(layout, withOpen(putHome({ id, edge, size }, orNone(home)), strip.open), strip.peers, strip.index);
}

/** Docks a floating window's panels back where they came from, in one stack as they were. */
export function dockBack(layout, f, opts = {}) {
  const i = layout.floats.indexOf(f);
  if (i < 0) return false;
  layout.floats.splice(i, 1);
  let panels = f.stack.panels;
  // The panel it was made from a strip for goes back to its strip; any others, docked in one stack as before.
  const strip = f.strip && panels.includes(f.strip.id) ? f.strip : null;
  if (strip) { backToStrip(layout, strip.id, strip, f.home, opts); panels = panels.filter((p) => p !== strip.id); }
  if (!panels.length) return true;
  const [first, ...rest] = panels;
  placeDocked(layout, first, f.home, { ...opts, side: EDGES.includes(f.side) ? f.side : undefined });
  const at = locate(layout.root, first);
  for (const p of rest) at.stack.panels.push(p);
  at.stack.active = panels.includes(f.stack.active) ? f.stack.active : first;
  return true;
}

/**
 * Docks one panel of a floating window, on its side (View Mode › Dock Pinned from Float): one floated from its strip
 * goes back there and is pinned (so Unpin finds its strip place); the only panel of a window docks back where it was; a
 * tab of several docks where it was, the others floating on.
 */
export function dockFromFloat(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w || w.kind !== 'float') return false;
  const f = w.float;
  if (f.strip && f.strip.id === id) {
    if (f.stack.panels.length === 1) dockBack(layout, f, opts);
    else { const was = detach(layout, id); backToStrip(layout, id, was.strip, was.home, opts); }
    return pinPanel(layout, id, opts);
  }
  if (f.stack.panels.length === 1) return dockBack(layout, f, opts);
  const side = panelSide(layout, id, opts);
  const was = detach(layout, id);
  placeDocked(layout, id, was.home, { ...opts, side });
  return true;
}

/** Unpins a panel: it leaves the layout for a strip on `edge`, `size` px deep when it slides out. */
export function unpinPanel(layout, id, edge, size, opts = {}, open) {
  // One that came from a strip (pinned from it, or floated from it) goes back to its place there.
  const pinned = layout.pinned && layout.pinned[id];
  const w = whereIs(layout, id);
  const fromFloat = w && w.kind === 'float' && w.float.strip && w.float.strip.id === id ? w.float.strip : null;
  const was = detach(layout, id);
  if (!was) return null;
  const back = was.strip || fromFloat || ((was.kind === 'dock' || was.kind === 'float') && pinned && (!EDGES.includes(edge) || pinned.edge === edge) ? pinned : null);
  forgetPinned(layout, id);
  // Pinned from its strip: back with the home it had there, so a pin and an unpin leave the layout as it was.
  // `open` given ('beside', 'over', or null for the dock's own): how it slides out from now on.
  const style = (entry) => { if (open !== undefined) { delete entry.open; withOpen(entry, open); } return entry; };
  if (back) return style(backToStrip(layout, id, back, back === pinned ? homeIn(pinned) : was.home, opts));
  const e = EDGES.includes(edge) ? edge : 'right';
  const entry = style({ id, edge: e, size: finite(size) ? Math.round(size) : unpinFallback(cfgOf(opts), id, e), home: was.home || null });
  layout.auto.push(entry);
  return entry;
}

/** Pins an unpinned panel: docked again where it was. Its place in the strip is kept, for Unpin. */
export function pinPanel(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w || w.kind !== 'auto') return false;
  const was = detach(layout, id);
  const place = stripOf(id, was);
  // On its strip's side, as deep as it slid out when its home kept no size.
  placeDocked(layout, id, was.home, { ...opts, side: place.edge, size: place.size });
  // Its strip place is kept unless a plain unpin gives it back anyway: a panel unpinned from where it is docked again,
  // last on its strip (so pin and unpin of a docked panel's strip twin leave nothing behind).
  if (!(sameHome(was.home, homeOf(layout, id)) && place.index === place.peers.length)) {
    if (!layout.pinned) layout.pinned = {};
    delete place.open; // how it slid out is chosen again when it goes back to a strip (View Mode), or the dock's own
    layout.pinned[id] = putHome(place, orNone(was.home));
  }
  return true;
}

/** Hides a panel; `showPanel` restores its mode. A Window keeps its geometry separately from its main-page place. */
export function hidePanel(layout, id) {
  const w = whereIs(layout, id);
  if (!w || w.kind === 'hidden') return false;
  const window = w.kind === 'out' ? pickGeo(w.entry) : null;
  layout.hidden.push({ id, was: wasOf(detach(layout, id)), ...(window ? { window } : {}) });
  return true;
}

export function showPanel(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w || w.kind !== 'hidden') return false;
  const window = w.entry.window;
  const was = detach(layout, id).was || { kind: 'dock' };
  if (window) (layout.out || (layout.out = {}))[id] = { ...window, was };
  else restore(layout, id, was, opts);
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

// ---------- panels added and removed at runtime (v0.10.0) ----------

// A new tab right of the front one (IntelliJ's editor tabs), in front.
function tabAfterActive(s, id) {
  const at = s.panels.indexOf(s.active);
  s.panels.splice(at < 0 ? s.panels.length : at + 1, 0, id);
  s.active = id;
}

/**
 * Puts a panel that is not in the layout where a new one goes: as a tab of panel `near`'s stack when that one is docked
 * or floating, else as a tab in the middle (the stack holding cfg.fill, else the one that takes what is left at each
 * split from the top), else the whole layout when it is empty.
 */
export function placeNew(layout, id, near, cfg = makeConfig()) {
  const w = typeof near === 'string' && near !== id ? whereIs(layout, near) : null;
  if (w && (w.kind === 'dock' || w.kind === 'float')) { tabAfterActive(w.stack, id); return; }
  if (!layout.root) { layout.root = stackNode([id]); return; }
  const at = cfg.fill && locate(layout.root, cfg.fill);
  let node = at ? at.stack : layout.root;
  while (node.t === 'split') { const f = fillOf(node, cfg); node = node.kids[f < 0 ? 0 : f]; }
  tabAfterActive(node, id);
}

// The list a panel's place counts its neighbours in: its home's peers (docked), its floating stack's or its strip's.
function peersRef(was) {
  if (!was) return null;
  if (was.kind === 'dock') return was.home && Array.isArray(was.home.peers) ? was.home : null;
  return (was.kind === 'float' || was.kind === 'auto') && Array.isArray(was.peers) ? was : null;
}
// The panels of its stack (or strip) parked before it are its peers too, where they stood: whichever of them is added
// back first, the others find it and join it.
function withParkedPeers(layout, id, was) {
  const r = peersRef(was);
  if (!r || !layout.parked) return;
  const order = r.peers.slice();
  order.splice(finite(r.index) ? Math.max(0, Math.min(order.length, r.index)) : order.length, 0, id);
  for (const z of layout.parked) {
    const rz = peersRef(z.was);
    if (!rz || z.was.kind !== was.kind || !rz.peers.includes(id) || order.includes(z.id)) continue;
    insertNear(order, z.id, rz.peers, rz.index);
  }
  r.peers = order.filter((p) => p !== id);
  r.index = order.indexOf(id);
}

/** The panels whose places the layout keeps (parked), oldest first. */
export const parkedOf = (layout) => (Array.isArray(layout.parked) ? layout.parked.map((p) => p.id) : []);

/**
 * Takes a panel out of the layout and keeps its place (its stack and neighbours, its float, its strip place, its
 * window's geometry, its view mode) in `parked`, newest last, at most `opts.max` (default cfg.keepSlots) of them: the
 * oldest go first. Wherever it was (docked, floating, on a strip, in its own window, hidden), its neighbours take the
 * room; the next tab of its stack is the one on its left (the first tab's right). False when the layout does not hold it.
 */
export function parkPanel(layout, id, opts = {}) {
  const w = whereIs(layout, id);
  if (!w) return false;
  const window = w.kind === 'out' ? pickGeo(w.entry) : w.kind === 'hidden' && w.entry.window ? pickGeo(w.entry.window) : null;
  const pinned = layout.pinned && layout.pinned[id];
  const was = wasOf(detach(layout, id));
  forgetPinned(layout, id);
  const entry = { id, was };
  if (window) entry.window = window;
  if (pinned) entry.pinned = pinned;
  withParkedPeers(layout, id, was);
  forgetPanel(layout, id);
  (layout.parked || (layout.parked = [])).push(entry);
  capParked(layout, finite(opts.max) ? opts.max : cfgOf(opts).keepSlots);
  return true;
}

function capParked(layout, max) {
  if (!layout.parked) return;
  const n = Math.max(0, Math.floor(max));
  const gone = layout.parked.length > n ? layout.parked.splice(0, layout.parked.length - n) : [];
  if (layout.depth) for (const p of gone) delete layout.depth[p.id];
  if (!layout.parked.length) delete layout.parked;
}

/** Drops the place kept for a panel (`id`), or for every one (no `id`). Returns how many it dropped. */
export function forgetPanel(layout, id) {
  if (!layout.parked) return 0;
  const before = layout.parked.length;
  const gone = layout.parked.filter((p) => id === undefined || p.id === id);
  layout.parked = id === undefined ? [] : layout.parked.filter((p) => p.id !== id);
  if (layout.depth) for (const p of gone) delete layout.depth[p.id];
  const n = before - layout.parked.length;
  if (!layout.parked.length) delete layout.parked;
  return n;
}

/**
 * Puts a parked panel back where it was: its stack, beside its neighbour, its float, its strip place, or (it was in its
 * own window) out, with that window's geometry, as one remembered after a reload. False when none is kept for it.
 */
export function unparkPanel(layout, id, opts = {}) {
  const i = layout.parked ? layout.parked.findIndex((p) => p.id === id) : -1;
  if (i < 0 || whereIs(layout, id)) return false;
  const [e] = layout.parked.splice(i, 1);
  if (!layout.parked.length) delete layout.parked;
  const was = e.was || { kind: 'dock', home: null };
  if (e.window) (layout.out || (layout.out = {}))[id] = { ...e.window, was };
  else restore(layout, id, was, opts);
  if (e.pinned) (layout.pinned || (layout.pinned = {}))[id] = e.pinned;
  return true;
}

/**
 * Puts panel `id`, not in the layout yet, where `where` says:
 *   none (null)                        back in its kept place (unparkPanel), else placeNew beside `opts.near`
 *   { kind: 'stack', stack: panelId }  a tab of that panel's stack, right of its front tab (docked or floating)
 *   { beside: panelId, side }          docked beside that panel, on `side` (as moveTo's split)
 *   'left' | 'right' | 'top' | 'bottom'  docked along that edge of the dock
 *   a view mode ('pinned', 'unpinned', 'undock', 'float', 'window') or 'hidden': placed new, then so (`opts.rect` the
 *     float's, `opts.geo` the window's, `opts.open` a strip panel's slide-out style)
 * A `where` given wins over the kept place, which is dropped. A target that is not there: as with none, placeNew.
 * Returns false when the layout holds the panel already.
 */
export function insertPanel(layout, id, where, opts = {}) {
  const cfg = cfgOf(opts);
  if (whereIs(layout, id)) return false;
  if (where == null) {
    if (!unparkPanel(layout, id, opts)) placeNew(layout, id, opts.near, cfg);
    return true;
  }
  forgetPanel(layout, id);
  const target = (pid) => (typeof pid === 'string' && pid !== id ? whereIs(layout, pid) : null);
  if (typeof where === 'object' && where.beside !== undefined) {
    const w = target(where.beside);
    if (w && w.kind === 'dock') dockBeside(layout, { stack: w.stack, chain: w.chain }, id, EDGES.includes(where.side) ? where.side : 'right', undefined, opts.dims);
    else if (w && w.kind === 'float') tabAfterActive(w.stack, id);
    else placeNew(layout, id, opts.near, cfg);
    return true;
  }
  if (typeof where === 'object') {
    const w = target(where.stack);
    if (w && (w.kind === 'dock' || w.kind === 'float')) tabAfterActive(w.stack, id);
    else placeNew(layout, id, opts.near, cfg);
    return true;
  }
  if (EDGES.includes(where)) { dockAtEdge(layout, id, where, undefined, opts.extent, cfg); return true; }
  placeNew(layout, id, opts.near, cfg);
  if (where === 'unpinned' || where === 'undock') unpinPanel(layout, id, panelSide(layout, id, opts), undefined, opts, opts.open);
  else if (where === 'float') floatPanel(layout, id, opts.rect || { x: 80, y: 60, w: 480, h: 360 });
  else if (where === 'window') popOutPanel(layout, id, opts.geo || {}, opts);
  else if (where === 'hidden') hidePanel(layout, id);
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
  if (h.free === true && !('size' in out)) out.free = true;
  if (Array.isArray(h.nearAll)) { const all = h.nearAll.filter((p) => typeof p === 'string'); if (all.length) out.nearAll = all; }
  return out;
}
// A stored strip place ({ id, edge, size, peers, index }) made safe, or null. Like normHome it adds no field.
function normStrip(x, id) {
  if (!x || typeof x !== 'object' || (typeof id === 'string' && x.id !== id) || typeof x.id !== 'string') return null;
  const out = { id: x.id, edge: EDGES.includes(x.edge) ? x.edge : 'right' };
  if (finite(x.size) && x.size > 0) out.size = x.size;
  out.peers = Array.isArray(x.peers) ? x.peers.filter((p) => typeof p === 'string') : [];
  if (finite(x.index)) out.index = x.index;
  withOpen(out, x.open);
  return withHome(out, x);
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
  const unpin = (id, edge) => unpinFallback(cfg, id, edge);
  const fallback = () => cfg.defaultLayout({ viewportPx: opts.viewportPx || 1600, purpose: opts.purpose || 'load' });
  if (raw && typeof raw === 'object' && raw.v !== LAYOUT_VERSION && typeof opts.migrate === 'function') {
    try { raw = opts.migrate(raw); } catch { raw = null; }
  }
  if (!raw || typeof raw !== 'object' || raw.v !== LAYOUT_VERSION) return fallback();
  const known = new Set(ids);
  const seen = new Set();
  // A panel the app has not added (yet) keeps its place: it is read as any other, then parked (v0.10.0).
  const ghosts = new Set();
  const take = (p) => typeof p === 'string' && p !== '' && !seen.has(p) && (seen.add(p), known.has(p) || ghosts.add(p), true);
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
    const nf = withHome({ stack: s, x: finite(f.x) ? f.x : 80, y: finite(f.y) ? f.y : 60, w: finite(f.w) && f.w > 0 ? f.w : 480,
      h: finite(f.h) && f.h > 0 ? f.h : 360 }, f);
    const strip = normStrip(f.strip);
    if (strip && s.panels.includes(strip.id)) nf.strip = strip;
    if (EDGES.includes(f.side)) nf.side = f.side;
    layout.floats.push(nf);
  }
  for (const a of Array.isArray(raw.auto) ? raw.auto : []) {
    if (!a || !take(a.id)) continue;
    const edge = EDGES.includes(a.edge) ? a.edge : 'right';
    layout.auto.push(withOpen(withHome({ id: a.id, edge, size: finite(a.size) && a.size > 0 ? a.size : unpin(a.id, edge) }, a), a.open));
  }
  const normWas = (w, id) => {
    const was = w && typeof w === 'object' ? w : {};
    const keep = withHome({ kind: ['dock', 'float', 'auto'].includes(was.kind) ? was.kind : 'dock' }, was);
    if (keep.kind === 'float' && was.float && ['x', 'y', 'w', 'h'].every((k) => finite(was.float[k]))) keep.float = { ...was.float };
    else if (keep.kind === 'float') keep.kind = 'dock';
    if (keep.kind === 'float' && Array.isArray(was.peers)) {
      const peers = was.peers.filter((p) => typeof p === 'string');
      if (peers.length) { keep.peers = peers; keep.index = finite(was.index) ? was.index : peers.length; }
    }
    if (keep.kind === 'auto') {
      keep.edge = EDGES.includes(was.edge) ? was.edge : 'right';
      keep.size = finite(was.size) ? was.size : unpin(id, keep.edge);
      // Where it stood in its strip (v0.4.2).
      if (Array.isArray(was.peers)) { keep.peers = was.peers.filter((p) => typeof p === 'string'); if (finite(was.index)) keep.index = was.index; }
      withOpen(keep, was.open);
    }
    if (keep.kind !== 'auto' && EDGES.includes(was.side)) keep.side = was.side; // a side given while it was away (v0.5.0)
    const strip = keep.kind === 'float' ? normStrip(was.strip, id) : null;
    if (strip) keep.strip = strip;
    return keep;
  };
  for (const h of Array.isArray(raw.hidden) ? raw.hidden : []) {
    if (!h || !take(h.id)) continue;
    const entry = h.was === undefined ? { id: h.id } : { id: h.id, was: normWas(h.was, h.id) };
    if (h.window && typeof h.window === 'object') entry.window = pickGeo(h.window);
    layout.hidden.push(entry);
  }
  // The panels in windows of their own, with where those windows were. One stored with `was` has no place in the
  // layout; one stored before (no `was`) still has its place there, and leaves it below.
  const out = raw.out && typeof raw.out === 'object' && !Array.isArray(raw.out) ? raw.out : {};
  const older = [];
  for (const id of Object.keys(out)) {
    const g = out[id] && typeof out[id] === 'object' ? out[id] : {};
    if (g.was && typeof g.was === 'object') { if (take(id)) layout.out[id] = { ...pickGeo(g), was: normWas(g.was, id) }; }
    else if (known.has(id)) older.push([id, g]);
  }
  // Where each panel pinned from a strip stood in it, for Unpin (v0.4.2).
  if (raw.pinned && typeof raw.pinned === 'object' && !Array.isArray(raw.pinned)) {
    for (const id of Object.keys(raw.pinned)) {
      const strip = known.has(id) || ghosts.has(id) ? normStrip(raw.pinned[id], id) : null;
      if (strip) (layout.pinned || (layout.pinned = {}))[id] = strip;
    }
  }
  // The places kept for panels removed, or not added yet (v0.10.0). One the app has now (it declares it again) goes back
  // to its place; the others stay kept, and so do the places of the panels above that it has not added.
  const kept = [];
  for (const p of Array.isArray(raw.parked) ? raw.parked : []) {
    if (!p || typeof p.id !== 'string' || p.id === '' || seen.has(p.id) || kept.some((k) => k.id === p.id)) continue;
    const e = { id: p.id, was: normWas(p.was, p.id) };
    if (p.window && typeof p.window === 'object') e.window = pickGeo(p.window);
    const strip = normStrip(p.pinned, p.id);
    if (strip) e.pinned = strip;
    kept.push(e);
  }
  if (kept.length) layout.parked = kept.filter((e) => !known.has(e.id));
  for (const e of kept) {
    if (!known.has(e.id)) continue;
    seen.add(e.id);
    (layout.parked || (layout.parked = [])).push(e);
    unparkPanel(layout, e.id, opts);
  }
  for (const id of ghosts) parkPanel(layout, id, { ...opts, max: Infinity });
  capParked(layout, cfg.keepSlots);
  const parked = layout.parked;
  // The default, with the parked panels' places and their depths (layout.depth then holds only theirs and today's).
  const orDefault = () => {
    const d = fallback();
    if (parked) d.parked = parked;
    if (parked && layout.depth) {
      for (const e of parked) if (layout.depth[e.id]) (d.depth || (d.depth = {}))[e.id] = layout.depth[e.id];
    }
    return d;
  };
  // Each panel's depth per axis, from Move To (a layout stored before has none: each panel's default the first time);
  // a panel removed keeps its own with its place.
  if (raw.depth && typeof raw.depth === 'object' && !Array.isArray(raw.depth)) {
    for (const id of Object.keys(raw.depth)) {
      const keep = known.has(id) || (parked || []).some((e) => e.id === id);
      const d = keep && raw.depth[id] && typeof raw.depth[id] === 'object' ? raw.depth[id] : null;
      if (d) for (const k of ['w', 'h']) keepDepth(layout, id, k, d[k]);
    }
  }
  // One that names none of today's panels says nothing about them: the default, not each panel placed one by one.
  if (!ids.some((id) => seen.has(id))) return orDefault();
  // A panel the stored layout does not know (added since it was stored) goes where the default has it.
  for (const id of ids) if (!seen.has(id)) placeDocked(layout, id, null, opts);
  const visible = ids.filter((id) => { const w = whereIs(layout, id); return w && w.kind !== 'hidden'; });
  if (!visible.length && !layout.hidden.some((h) => h.window)) return orDefault();
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
