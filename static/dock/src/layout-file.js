// The portable layout file is also the group's storage value.
export const LAYOUT_FORMAT = 'dock-layout';
const clone = (value) => JSON.parse(JSON.stringify(value));
const object = (x) => !!x && typeof x === 'object' && !Array.isArray(x);

export function layoutFile(docks, app = 'dock', savedAt = new Date().toISOString(), zoom = 1) {
  return { format: LAYOUT_FORMAT, v: 1, app, savedAt, ...(zoom !== 1 ? { zoom } : {}), docks: docks.map((d) =>
    ({ id: d.id, title: d.title, host: 'main', ...(d.id === 'main' ? {} : { window: d.window || null }), layout: d.layout })) };
}

export function validateLayoutFile(value) {
  const errors = [];
  const err = (path, message) => errors.push({ path, message });
  if (!object(value)) { err('$', 'Expected an object'); return errors; }
  if (value.format !== LAYOUT_FORMAT) err('format', 'Expected dock-layout');
  if (value.v !== 1) err('v', value.v > 1 ? 'This layout version is newer than supported' : 'Expected version 1');
  if (typeof value.app !== 'string') err('app', 'Expected a string');
  if (typeof value.savedAt !== 'string') err('savedAt', 'Expected a string');
  if (value.zoom !== undefined && (typeof value.zoom !== 'number' || !Number.isFinite(value.zoom) || value.zoom <= 0)) err('zoom', 'Expected a positive zoom');
  if (!Array.isArray(value.docks) || !value.docks.length) { err('docks', 'Expected at least the main dock'); return errors; }
  const seenDocks = new Set();
  // An id repeated inside one dock is an error; one named by two docks is the group's to settle (the first wins).
  let seenPanels = new Set();
  function node(n, path, allowEmpty = false) {
    if (n === null && allowEmpty) return;
    if (!object(n)) { err(path, 'Expected a stack or split'); return; }
    if (n.t === 'stack') {
      if (!Array.isArray(n.panels) || !n.panels.length || n.panels.some((p) => typeof p !== 'string')) err(`${path}.panels`, 'Expected panel ids');
      else {
        for (const p of n.panels) { if (seenPanels.has(p)) err(`${path}.panels`, `Duplicate panel ${p}`); seenPanels.add(p); }
        if (!n.panels.includes(n.active)) err(`${path}.active`, 'Expected a panel in this stack');
      }
    } else if (n.t === 'split') {
      if (!['row', 'col'].includes(n.dir)) err(`${path}.dir`, 'Expected row or col');
      if (!Array.isArray(n.kids) || !n.kids.length) err(`${path}.kids`, 'Expected child nodes');
      else n.kids.forEach((kid, i) => node(kid, `${path}.kids[${i}]`));
    } else err(path, 'Unknown node type');
    if (n.size !== undefined && (typeof n.size !== 'number' || !Number.isFinite(n.size) || n.size <= 0)) err(`${path}.size`, 'Expected a positive size');
  }
  value.docks.forEach((d, i) => {
    const path = `docks[${i}]`;
    seenPanels = new Set();
    if (!object(d)) { err(path, 'Expected a dock'); return; }
    if (typeof d.id !== 'string' || !d.id) err(`${path}.id`, 'Expected a dock id');
    else if (seenDocks.has(d.id)) err(`${path}.id`, 'Duplicate dock id');
    seenDocks.add(d.id);
    if (typeof d.title !== 'string') err(`${path}.title`, 'Expected a title');
    if (d.host !== undefined && d.host !== 'main') err(`${path}.host`, 'This host is not supported yet');
    if (d.window !== undefined && d.window !== null) {
      if (d.id === 'main' || !object(d.window) || ['left', 'top', 'width', 'height'].some((key) =>
        typeof d.window[key] !== 'number' || !Number.isFinite(d.window[key]) ||
        (['width', 'height'].includes(key) && d.window[key] <= 0)))
        err(`${path}.window`, 'Expected finite left, top, width and positive height and width on a user dock');
    }
    if (!object(d.layout) || d.layout.v !== 1) { err(`${path}.layout`, 'Expected a version 1 layout'); return; }
    if (d.layout.zoom !== undefined) {
      if (!object(d.layout.zoom)) err(`${path}.layout.zoom`, 'Expected panel zooms by id');
      else for (const [id, zoom] of Object.entries(d.layout.zoom))
        if (typeof zoom !== 'number' || !Number.isFinite(zoom) || zoom < 0.5 || zoom > 2)
          err(`${path}.layout.zoom.${id}`, 'Expected zoom from 0.5 to 2');
    }
    node(d.layout.root, `${path}.layout.root`, true);
    for (const [field, key] of [['floats', 'stack'], ['auto', 'id'], ['hidden', 'id'], ['parked', 'id']]) {
      if (d.layout[field] !== undefined && !Array.isArray(d.layout[field])) err(`${path}.layout.${field}`, 'Expected an array');
      else if (field === 'floats') (d.layout.floats || []).forEach((f, j) => {
        const floatPath = `${path}.layout.floats[${j}]`;
        if (!object(f)) { err(floatPath, 'Expected a floating stack'); return; }
        node(f.stack, `${floatPath}.stack`);
        for (const key of ['x', 'y', 'w', 'h'])
          if (typeof f[key] !== 'number' || !Number.isFinite(f[key])) err(`${floatPath}.${key}`, 'Expected a finite number');
      });
      else (d.layout[field] || []).forEach((entry, j) => {
        if (!object(entry) || typeof entry[key] !== 'string') err(`${path}.layout.${field}[${j}]`, 'Expected a panel id');
        else if (field !== 'parked') { if (seenPanels.has(entry.id)) err(`${path}.layout.${field}[${j}]`, `Duplicate panel ${entry.id}`); seenPanels.add(entry.id); }
      });
    }
    if (d.layout.out !== undefined && !object(d.layout.out)) err(`${path}.layout.out`, 'Expected an object');
    else for (const id of Object.keys(d.layout.out || {})) { if (seenPanels.has(id)) err(`${path}.layout.out.${id}`, `Duplicate panel ${id}`); seenPanels.add(id); }
  });
  if (value.docks[0]?.id !== 'main') err('docks[0].id', 'The first dock must be main');
  return errors;
}

export function parseLayoutFile(text) {
  let value;
  try { value = JSON.parse(text); } catch (e) { return { value: null, errors: [{ path: '$', message: `Invalid JSON: ${e.message}` }] }; }
  return { value, errors: validateLayoutFile(value) };
}

export const layoutText = (value) => JSON.stringify(value, null, 2) + '\n';

export function downloadLayout(source, filename = 'dock-layout.json') {
  const doc = globalThis.document;
  const blob = new Blob([source.exportLayout()], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = doc.createElement('a');
  a.href = url; a.download = filename;
  doc.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function pickLayoutFile(source) {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.json,application/json'; input.hidden = true;
    document.body.appendChild(input);
    input.addEventListener('change', async () => {
      const file = input.files?.[0]; input.remove();
      if (!file) return resolve(null);
      try { resolve(source.importLayout(await file.text())); }
      catch (e) { resolve({ ok: false, errors: [{ path: '$', message: e.message }], dropped: [], parked: [] }); }
    }, { once: true });
    input.click();
  });
}

/** The panel ids a layout names: docked, floating, on a strip, hidden or out, and its parked ones when `parked`. */
export function layoutIds(layout, parked = false) {
  const ids = [];
  const walk = (node) => {
    if (!node) return;
    if (node.t === 'stack') ids.push(...node.panels);
    else for (const kid of node.kids || []) walk(kid);
  };
  walk(layout?.root);
  for (const f of layout?.floats || []) walk(f.stack);
  for (const field of parked ? ['auto', 'hidden', 'parked'] : ['auto', 'hidden'])
    for (const entry of layout?.[field] || []) if (typeof entry?.id === 'string') ids.push(entry.id);
  ids.push(...Object.keys(layout?.out || {}));
  return ids;
}

export function copyLayout(value) { return clone(value); }

/** Only split child sizes are portable fractions. Float and strip dimensions remain their restore geometry. */
export function splitFractions(raw, root, decode = false) {
  const layout = clone(raw);
  const r = root.getBoundingClientRect();
  function walk(node, width, height, parentAxis = null) {
    if (!node) return;
    if (!parentAxis && !decode) delete node.size;
    if (parentAxis && typeof node.size === 'number') node.size = decode
      ? Math.max(1, Math.round(node.size * parentAxis))
      : Math.max(0.001, Math.min(1, Math.round(node.size / parentAxis * 1000) / 1000));
    if (node.t !== 'split') return;
    const axis = node.dir === 'row' ? width : height;
    const known = node.kids.reduce((sum, kid) => sum + (typeof kid.size === 'number'
      ? decode ? kid.size * axis : kid.size : 0), 0);
    const free = node.kids.filter((kid) => typeof kid.size !== 'number').length;
    for (const kid of node.kids) {
      const along = typeof kid.size === 'number' ? (decode ? kid.size * axis : kid.size)
        : Math.max(1, (axis - known) / Math.max(1, free));
      walk(kid, node.dir === 'row' ? along : width, node.dir === 'col' ? along : height, axis);
    }
  }
  walk(layout.root, Math.max(1, r.width), Math.max(1, r.height));
  return layout;
}

/** Remove unavailable panel ids before Dock's normalizer can reserve their old slots. */
export function dropUnknown(layout, known, dropped = []) {
  const l = clone(layout);
  const has = (id) => known.has(id);
  const take = (id) => { if (has(id)) return true; if (!dropped.includes(id)) dropped.push(id); return false; };
  const walk = (n) => {
    if (!n) return null;
    if (n.t === 'stack') { n.panels = n.panels.filter(take); if (!n.panels.length) return null;
      if (!n.panels.includes(n.active)) n.active = n.panels[0]; return n; }
    n.kids = n.kids.map(walk).filter(Boolean);
    return n.kids.length ? n : null;
  };
  l.root = walk(l.root);
  l.floats = (l.floats || []).filter((f) => !!walk(f.stack));
  for (const field of ['auto', 'hidden', 'parked']) l[field] = (l[field] || []).filter((e) => take(e.id));
  l.out = Object.fromEntries(Object.entries(l.out || {}).filter(([id]) => take(id)));
  if (l.pinned) l.pinned = Object.fromEntries(Object.entries(l.pinned).filter(([id]) => take(id)));
  if (l.depth) l.depth = Object.fromEntries(Object.entries(l.depth).filter(([id]) => take(id)));
  if (l.zoom) { l.zoom = Object.fromEntries(Object.entries(l.zoom).filter(([id]) => take(id))); if (!Object.keys(l.zoom).length) delete l.zoom; }
  return l;
}
