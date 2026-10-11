import { createDock, oneColumn } from './dock.js';
import { layoutFile, layoutText, parseLayoutFile, dropUnknown, copyLayout, splitFractions, layoutIds } from './layout-file.js';
import { hidePanel, showPanel } from './layout.js';
import { ADOPT_TIMEOUT, markWindow, marksOf, windowWaits, windowState, adoptHook, callWindows, writeWaiting, takeWaiting, awaitWindows } from './popadopt.js';

const frameId = (id) => `dock:${id}`;
const idsIn = (layout) => layoutIds(layout);
const inside = (r, x, y) => r.width > 0 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;

/** Several connected Dock instances with one owner for every application panel. */
export function createDocks({ root, panels, storageKey = 'dock.docks', storage = globalThis.localStorage,
  app = storageKey, migrateFrom = null, dockWindowClose = 'dock', ...dockOptions }) {
  const doc = root.ownerDocument;
  const registry = new Map(panels.map((p) => [p.id, p]));
  if (registry.size !== panels.length || registry.has(frameId('main'))) throw new Error('Panel ids must be unique');
  const entries = new Map();
  const order = [];
  const memory = new Map();
  const dimensions = new Map();
  const pendingWindows = new Set();
  let windowNotice = null;
  let noticeObserver = null;
  const dragType = 'application/x-dock-panel';
  const dragKey = Math.random().toString(36).slice(2);
  let nativeDrag = null;
  let leaving = false;
  // A dock window outlives a reload of the page as a panel's does (popAdoptTimeout, popadopt.js): its windows are marked
  // as this group's (groupKey), and the page that loads next takes each back.
  const groupKey = `docks:${storageKey}`;
  const adoptMs = Number.isFinite(dockOptions.popAdoptTimeout ?? ADOPT_TIMEOUT) && (dockOptions.popAdoptTimeout ?? ADOPT_TIMEOUT) > 0
    ? (dockOptions.popAdoptTimeout ?? ADOPT_TIMEOUT) : 0;
  const transferBin = doc.createElement('div');
  transferBin.className = 'dk-transfer-bin'; transferBin.hidden = true;
  doc.body.appendChild(transferBin);
  let busy = true;
  let savedAt = new Date().toISOString();
  let latest = null;
  let groupZoom = 1;
  const initiallyNarrow = !!dockOptions.narrow;
  let narrow = false;
  let narrowWide = null;
  let presentationSync = false;
  let transferring = false;
  const emit = (name, detail) => {
    if (!presentationSync) root.dispatchEvent(new doc.defaultView.CustomEvent(name, { bubbles: true, detail }));
  };
  const notify = () => { if (!busy) persist(); };
  const storeFor = () => ({
    getItem: (key) => memory.get(key) || null,
    setItem: (key, value) => { memory.set(key, value); notify(); },
    removeItem: (key) => { memory.delete(key); notify(); },
  });
  function measuredRoot(id, element) {
    const live = element.getBoundingClientRect();
    if (live.width > 1 && live.height > 1) dimensions.set(id, { width: live.width, height: live.height });
    const remembered = dimensions.get(id);
    const main = root.getBoundingClientRect();
    const width = remembered?.width || Math.max(320, main.width * (id === 'main' ? 1 : 0.4));
    const height = remembered?.height || Math.max(240, main.height * (id === 'main' ? 1 : 0.7));
    return { getBoundingClientRect: () => ({ width, height }) };
  }
  function snapshot() {
    return layoutFile(order.map((id) => {
      const e = entries.get(id);
      const wide = e.dock.narrow() ? JSON.parse(memory.get(`${storageKey}.${id}`) || 'null') : null;
      return { id, title: e.title, window: e.window ? { ...e.window } : null,
        layout: splitFractions(wide || e.dock.layout(), measuredRoot(id, e.root)) };
    }), app, savedAt, groupZoom);
  }
  function persist() {
    if (narrowWide) {
      if (!presentationSync && narrow) syncNarrowVisibility();
      return;
    }
    savedAt = new Date().toISOString();
    latest = snapshot();
    try { storage?.setItem(storageKey, layoutText(latest)); } catch { /* current page still works */ }
  }
  function saveNarrowWide() {
    narrowWide.savedAt = new Date().toISOString();
    latest = copyLayout(narrowWide);
    try { storage?.setItem(storageKey, layoutText(narrowWide)); } catch { /* current page still works */ }
  }
  function syncNarrowVisibility() {
    const narrowLayout = entries.get('main')?.dock.layout();
    if (!narrowLayout) return;
    const hidden = new Set((narrowLayout.hidden || []).map((entry) => entry.id));
    let changed = false;
    for (const d of narrowWide.docks) for (const id of idsIn(d.layout)) {
      if (!registry.has(id)) continue;
      const wasHidden = (d.layout.hidden || []).some((entry) => entry.id === id);
      if (hidden.has(id) && !wasHidden) changed = hidePanel(d.layout, id) || changed;
      else if (!hidden.has(id) && wasHidden) changed = showPanel(d.layout, id) || changed;
    }
    if (changed) saveNarrowWide();
  }
  function editWide(run) {
    if (!narrow || presentationSync) return run();
    group.setNarrow(false);
    try { return run(); } finally { group.setNarrow(true); }
  }
  root.addEventListener('dock-removed', (event) => {
    if (!narrow || presentationSync || event.target !== root) return;
    const id = event.detail?.id;
    if (!registry.has(id)) return;
    const home = narrowWide.docks.find((d) => idsIn(d.layout).includes(id));
    if (home && hidePanel(home.layout, id)) saveNarrowWide();
  });
  root.addEventListener('zoom', (event) => {
    if (!narrowWide || presentationSync || !event.detail?.id) return;
    const { id, zoom } = event.detail;
    const home = narrowWide.docks.find((d) => idsIn(d.layout).includes(id));
    if (!home) return;
    if (zoom === 1) { if (home.layout.zoom) { delete home.layout.zoom[id]; if (!Object.keys(home.layout.zoom).length) delete home.layout.zoom; } }
    else (home.layout.zoom || (home.layout.zoom = {}))[id] = zoom;
    saveNarrowWide();
  });
  function frame(id, title) {
    const el = doc.createElement('div');
    el.className = 'dk-dock-frame';
    el.dataset.dockFrame = id;
    const inner = doc.createElement('div');
    inner.className = 'dk-dock-region';
    inner.dataset.dockRegion = id;
    el.appendChild(inner);
    return { panel: { id: frameId(id), title, el, closable: false, menuItems: () => [
      { id: 'dock-view-mode', label: dockOptions.text?.viewMode || 'View Mode', sub: [
        { id: 'dock-view-main', label: 'Dock', checked: !entries.get(id)?.window, run: () => group.dockBack(id) },
        { id: 'dock-view-window', label: 'Window', checked: !!entries.get(id)?.window, run: () => group.popDock(id) },
      ] },
      { id: 'rename-dock', label: 'Rename…', run: () => renameInput(id) },
      { id: 'remove-dock', label: 'Remove dock', run: () => group.removeDock(id) },
    ] }, root: inner };
  }
  function renameInput(id) {
    const e = entries.get(id);
    if (!e) return;
    const input = doc.createElement('input');
    input.className = 'dk-dock-rename'; input.value = e.title; input.setAttribute('aria-label', 'Dock name');
    e.frame.prepend(input); input.focus(); input.select();
    let finished = false;
    const done = (apply) => {
      if (finished) return;
      finished = true;
      const title = input.value.trim();
      if (input.isConnected) input.remove();
      if (apply && title) group.renameDock(id, title);
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.stopPropagation(); done(true); }
      if (event.key === 'Escape') { event.stopPropagation(); done(false); }
    });
    input.addEventListener('blur', () => done(true), { once: true });
  }
  function make(id, title, allocated, raw = null, host = null) {
    const home = host || (id === 'main' ? root : frame(id, title).root);
    let f = null;
    if (id !== 'main') {
      f = host ? { root: home, panel: null } : { root: home, panel: null };
    }
    const bridge = {
      others: () => order.filter((other) => other !== id).map((other) => ({ id: other, title: entries.get(other).title })),
      move: (panelId, to) => group.movePanel(panelId, { dock: to }),
      preview: (panelId, x, y) => crossPreview(id, panelId, x, y),
      clearPreview,
      drop: (panelId, x, y) => crossDrop(id, panelId, x, y),
      nativeDrag: () => [...entries.values()].some((entry) => !!entry.pop),
    };
    if (raw) memory.set(`${storageKey}.${id}`, JSON.stringify(splitFractions(raw, measuredRoot(id, home), true)));
    const own = id === 'main' ? { ...dockOptions,
      can: (pid, action) => (pid.startsWith('dock:') && action === 'pop' ? false : dockOptions.can?.(pid, action) !== false),
      narrowLayout: dockOptions.narrowLayout || (() =>
      oneColumn(entries.get(id)?.dock.layout() || { v: 1, root: allocated.length
        ? { t: 'stack', panels: allocated.map((p) => p.id), active: allocated[0].id } : null,
      floats: [], auto: [], hidden: [], out: {} })), menuItems: (pid, ctx) => {
      const e = [...entries.values()].find((it) => it.framePanel?.id === pid);
      if (e) return e.framePanel.menuItems();
      const item = registry.get(pid);
      return item?.menuItems?.(pid, ctx) || dockOptions.menuItems?.(pid, ctx) || [];
    } } : { ...dockOptions, defaultLayout: undefined, fill: undefined };
    const dock = createDock({ ...own, root: home, panels: allocated, storageKey: `${storageKey}.${id}`,
      zoom: groupZoom,
      storage: storeFor(), narrowKey: dockOptions.narrowKey ? `${dockOptions.narrowKey}.${id}` : undefined,
      groupBridge: bridge,
      text: id === 'main' ? dockOptions.text : { ...dockOptions.text, empty: 'Drag panels here' }, narrow });
    // A panel added to this dock directly is the group's too, and the place another dock kept for it goes.
    const addPanel = dock.addPanel;
    dock.addPanel = (p, where) => {
      const result = addPanel(p, where);
      if (result !== 'added' || (p.id.startsWith('dock:') && entries.has(p.id.slice(5)))) return result;
      if (!registry.has(p.id)) registry.set(p.id, p);
      if (!transferring) for (const other of order) if (other !== id) entries.get(other).dock.forget(p.id);
      return result;
    };
    if (id === 'main') {
      const setVisible = dock.setVisible;
      dock.setVisible = (panelId, show) => narrow && show && panelId.startsWith('dock:')
        ? false : setVisible(panelId, show);
    }
    entries.set(id, { id, title, dock, root: home, frame: id === 'main' ? null : home.parentElement,
      framePanel: null, window: null, pop: null });
    order.push(id);
    return dock;
  }
  function targetAt(sourceId, x, y) {
    const source = entries.get(sourceId);
    if (source && inside(source.root.getBoundingClientRect(), x, y) && sourceId !== 'main') return null;
    // A child region wins over the main root that contains it.
    for (const id of [...order].reverse()) {
      if (id === sourceId) continue;
      const e = entries.get(id);
      if (inside(e.root.getBoundingClientRect(), x, y)) {
        const drop = e.dock._dropAt(x, y, '');
        if (drop) return { id, drop };
      }
    }
    return null;
  }
  function clearPreview() { for (const e of entries.values()) e.dock._preview(null); }
  function crossPreview(sourceId, panelId, x, y) {
    clearPreview();
    if (panelId.startsWith('dock:')) return false;
    const target = targetAt(sourceId, x, y);
    if (target) entries.get(target.id).dock._preview(target.drop);
    return !!target;
  }
  function crossDrop(sourceId, panelId, x, y) {
    if (panelId.startsWith('dock:')) return false;
    const target = targetAt(sourceId, x, y);
    if (!target) return false;
    return group.movePanel(panelId, { dock: target.id, where: target.drop });
  }
  function owner(id) { return order.find((dockId) => entries.get(dockId).dock.panels().some((p) => p.id === id)); }
  // The dock keeping the place of a panel the app has not given (or has removed): the one a saved set showed it in,
  // else the first whose layout parks it.
  const savedHome = new Map();
  function parkedIn(id) {
    const saved = savedHome.get(id);
    if (saved && entries.get(saved)?.dock.slots().includes(id)) return saved;
    return order.find((dockId) => entries.get(dockId).dock.slots().includes(id));
  }
  /**
   * Which dock of a saved set keeps each panel id: the first that shows it, else the first that parks it. An id the app
   * has not given yet stays in that dock's layout, for createDock to park as it would alone (keepSlots). Only a frame
   * that names no dock of the set is dropped. An id a later dock shows too is reported and left out of that dock; a
   * place another dock keeps for it (`parked`, as a strip panel moved away leaves) stays.
   */
  function settle(docks) {
    const frames = new Set(docks.filter((d) => d.id !== 'main').map((d) => frameId(d.id)));
    const home = new Map();
    const dropped = [];
    const duplicates = [];
    const valid = (id, dockId) => registry.has(id) || !id.startsWith('dock:') || (dockId === 'main' && frames.has(id));
    docks.forEach((d, i) => {
      for (const id of new Set(idsIn(d.layout))) {
        if (!valid(id, d.id)) { if (!dropped.includes(id)) dropped.push(id); }
        else if (!home.has(id)) home.set(id, d.id);
        else duplicates.push({ path: `docks[${i}].layout`, message: `Panel ${id} is in dock ${home.get(id)} already; kept there` });
      }
    });
    for (const d of docks) for (const e of d.layout.parked || [])
      if (typeof e?.id === 'string' && valid(e.id, d.id) && !home.has(e.id)) home.set(e.id, d.id);
    const parked = [...home.keys()].filter((id) => !registry.has(id) && !frames.has(id));
    savedHome.clear();
    for (const id of parked) savedHome.set(id, home.get(id));
    const keep = (d) => ({ has: (id) => home.get(id) === d.id
      || (valid(id, d.id) && (d.layout.parked || []).some((e) => e?.id === id) && !idsIn(d.layout).includes(id)) });
    return { docks: docks.map((d) => ({ ...d, layout: dropUnknown(d.layout, keep(d)) })), home, dropped, duplicates, parked };
  }

  function dockGeometry(e, requested = {}) {
    const base = e.window || {};
    const view = doc.defaultView;
    const position = root.getBoundingClientRect();
    const value = (key, fallback) => Number.isFinite(requested[key]) ? requested[key]
      : Number.isFinite(base[key]) ? base[key] : fallback;
    return {
      left: Math.round(value('left', (view?.screenX || 0) + position.left + 60)),
      top: Math.round(value('top', (view?.screenY || 0) + position.top + 60)),
      width: Math.max(320, Math.round(value('width', 760))),
      height: Math.max(240, Math.round(value('height', 560))),
    };
  }
  function showWindowNotice() {
    if (!pendingWindows.size) {
      windowNotice?.remove();
      noticeObserver?.disconnect(); noticeObserver = null;
      return;
    }
    if (!windowNotice) {
      windowNotice = doc.createElement('div');
      windowNotice.className = 'dk-dock-window-note';
      windowNotice.setAttribute('role', 'status');
      const words = doc.createElement('span');
      words.textContent = 'Some dock windows are closed. Reopen them here.';
      const button = doc.createElement('button');
      button.type = 'button';
      button.addEventListener('click', () => {
        const id = pendingWindows.values().next().value;
        if (id) group.popDock(id);
      });
      windowNotice.append(words, button);
    }
    windowNotice.querySelector('button').textContent = pendingWindows.size > 1
      ? `Reopen windows (${pendingWindows.size} remaining)` : 'Reopen windows';
    if (windowNotice.parentNode !== root) root.appendChild(windowNotice);
    if (!noticeObserver) {
      noticeObserver = new doc.defaultView.MutationObserver(() => {
        if (pendingWindows.size && windowNotice.parentNode !== root) root.appendChild(windowNotice);
      });
      noticeObserver.observe(root, { childList: true });
    }
  }
  function rememberDockWindow(e) {
    const pop = e.pop;
    if (!pop || !e.window) return;
    try {
      const w = pop.win;
      if (w.closed || Math.abs(w.screenX) >= 30000 || Math.abs(w.screenY) >= 30000 ||
        w.innerWidth < 160 || w.innerHeight < 120) return;
      const actual = { left: w.screenX, top: w.screenY, width: w.innerWidth, height: w.innerHeight };
      if (!pop.offset) pop.offset = Object.fromEntries(Object.keys(actual).map((key) => [key, e.window[key] - actual[key]]));
      const next = Object.fromEntries(Object.keys(actual).map((key) => [key, Math.round(actual[key] + pop.offset[key])]));
      if (Object.keys(next).some((key) => next[key] !== e.window[key])) { e.window = next; persist(); }
    } catch { /* window has closed */ }
  }
  function releaseDockWindow(e, { clear = true, close = true, hide = false, save = true, announce = true } = {}) {
    const pop = e.pop;
    if (pop) {
      rememberDockWindow(e);
      e.pop = null;
      clearInterval(pop.watch);
      pop.cleanup?.();
      transferBin.appendChild(e.frame);
    }
    if (clear) e.window = null;
    const main = entries.get('main').dock;
    if (e.framePanel && !main.panels().some((p) => p.id === e.framePanel.id)) main.addPanel(e.framePanel);
    if (e.framePanel && (hide || clear)) main.setVisible(e.framePanel.id, !hide);
    if (!e.framePanel && e.originalParent && e.frame.parentNode !== e.originalParent)
      e.originalParent.insertBefore(e.frame, e.originalNext?.parentNode === e.originalParent ? e.originalNext : null);
    if (!e.framePanel && (hide || clear)) e.frame.hidden = hide;
    if (pop && close) { try { pop.win.close(); } catch { /* already closing */ } }
    if (pop || clear) { if (save) persist(); if (announce) emit('dock-back', { id: e.id }); }
    for (const entry of entries.values()) entry.dock.render();
  }
  function windowGone(e, pop, { failed = false } = {}) {
    if (leaving || e.pop !== pop) return;
    releaseDockWindow(e, { clear: !failed && dockWindowClose !== 'hide', close: failed,
      hide: !failed && dockWindowClose === 'hide' });
    if (failed || dockWindowClose === 'hide') pendingWindows.add(e.id);
    showWindowNotice();
  }
  function attachDrag(docInWindow) {
    const started = (event) => {
      const target = event.target?.closest?.('[data-dk-tab], .dk-head');
      if (!target) return;
      const head = target.closest('.dk-head');
      const tab = event.target.closest('[data-dk-tab]');
      if (!head || head.closest('.dk-flyout') || (head.closest('.dk-float') && !tab) ||
        event.target.closest(`.dk-ctl, ${dockOptions.help?.selector || '[data-help]'}, .dk-tab-extra, [data-dk-close], [data-dk-tabs]`)) {
        event.preventDefault(); return;
      }
      const dockRoot = target.closest('.dock');
      const source = [...entries.values()].find((entry) => entry.root === dockRoot);
      if (!source || !source.dock.panels().length) return;
      const id = tab?.dataset.dkTab || head.querySelector('[data-dk-tab][aria-selected="true"]')?.dataset.dkTab;
      if (!id || id.startsWith('dock:') || !registry.has(id) || !source.dock.can(id, 'move')) {
        event.preventDefault(); return;
      }
      nativeDrag = { id, sourceId: source.id, key: dragKey };
      source.dock._cancelDrag();
      event.dataTransfer.setData(dragType, `${dragKey}:${id}`);
      event.dataTransfer.effectAllowed = 'move';
    };
    const target = (event) => {
      if (!nativeDrag || event.dataTransfer?.types && ![...event.dataTransfer.types].includes(dragType)) return null;
      for (const id of [...order].reverse()) {
        const e = entries.get(id);
        if (e.root.ownerDocument !== docInWindow || !inside(e.root.getBoundingClientRect(), event.clientX, event.clientY)) continue;
        const drop = e.dock._dropAt(event.clientX, event.clientY, nativeDrag.id);
        if (drop) return { id, drop };
      }
      return null;
    };
    const over = (event) => {
      clearPreview();
      const hit = target(event);
      if (!hit) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      entries.get(hit.id).dock._preview(hit.drop);
    };
    const dropped = (event) => {
      const hit = target(event);
      if (!hit || event.dataTransfer.getData(dragType) !== `${dragKey}:${nativeDrag.id}`) return;
      event.preventDefault();
      clearPreview();
      group.movePanel(nativeDrag.id, { dock: hit.id, where: hit.drop });
      nativeDrag = null;
    };
    const ended = () => { nativeDrag = null; clearPreview(); };
    const escaped = (event) => { if (event.key === 'Escape') ended(); };
    for (const [kind, fn] of [['dragstart', started], ['dragover', over], ['drop', dropped], ['dragend', ended], ['keydown', escaped]])
      docInWindow.addEventListener(kind, fn);
    return () => { for (const [kind, fn] of [['dragstart', started], ['dragover', over], ['drop', dropped], ['dragend', ended], ['keydown', escaped]])
      docInWindow.removeEventListener(kind, fn); };
  }
  const detachMainDrag = attachDrag(doc);
  // The page going: a dock window that can wait for the next page (popout.html, popAdoptTimeout) stays open, inert,
  // with nothing of this page's on it; any other closes.
  const beforeLeave = () => {
    leaving = true;
    const kept = [];
    for (const e of entries.values()) if (e.pop && !e.pop.waiting) {
      rememberDockWindow(e);
      const pop = e.pop;
      if (adoptMs && pop.cleanup && windowWaits(pop.win)) {
        pop.waiting = true;
        clearInterval(pop.watch);
        pop.cleanup();
        pop.cleanup = null;
        windowState(pop.win, true);
        kept.push(e.id);
      } else { try { pop.win.close(); } catch { /* old page is leaving */ } }
    }
    writeWaiting(doc.defaultView, groupKey, kept, adoptMs);
  };
  doc.defaultView?.addEventListener('pagehide', beforeLeave);
  const afterReturn = (event) => {
    if (!event.persisted) return;
    leaving = false;
    takeWaiting(doc.defaultView, groupKey);
    for (const e of entries.values()) {
      const pop = e.pop;
      if (!pop?.waiting) { if (pop?.win.closed) windowGone(e, pop, { failed: true }); continue; }
      e.pop = null;
      let open = false;
      try { open = !pop.win.closed; } catch { open = false; }
      if (open && group.popDock(e.id, e.window || {}, pop.win)) continue;
      e.pop = pop; // gone meanwhile: its place offers to open it again
      windowGone(e, pop, { failed: true });
    }
  };
  doc.defaultView?.addEventListener('pageshow', afterReturn);

  let prior = null;
  let migrated = false;
  try { const raw = storage?.getItem(storageKey); if (raw) {
    const parsed = parseLayoutFile(raw); if (!parsed.errors.length) prior = parsed.value;
  } } catch { /* no saved set */ }
  if (!prior && migrateFrom) try {
    const old = JSON.parse(storage?.getItem(migrateFrom) || 'null');
    if (old && old.v === 1 && 'root' in old) {
      prior = layoutFile([{ id: 'main', title: 'Main', layout: splitFractions(old, root) }], app);
      migrated = true;
    }
  } catch { /* leave an invalid old save alone */ }
  if (prior) savedAt = prior.savedAt;
  if (prior?.zoom > 0) groupZoom = prior.zoom;
  const used = new Set();
  const settled = prior ? settle(prior.docks) : null;
  const savedDocks = settled?.docks || [{ id: 'main', title: 'Main', layout: null }];
  const allocations = new Map();
  for (const d of savedDocks) {
    // Its panels in the order it names them, then the ones it parks that the app gives now (they go back there).
    const list = (d.layout ? layoutIds(d.layout, true) : []).filter((id) => registry.has(id) && settled.home.get(id) === d.id && !used.has(id));
    list.forEach((id) => used.add(id));
    allocations.set(d.id, { panels: list.map((id) => registry.get(id)), layout: d.layout });
  }
  for (const p of panels) if (!used.has(p.id)) {
    const dockId = allocations.has(p.dock) ? p.dock : 'main';
    allocations.get(dockId).panels.push(p);
  }
  const frames = new Map();
  for (const d of savedDocks) if (d.id !== 'main') {
    const f = frame(d.id, d.title); frames.set(d.id, f);
    root.appendChild(f.panel.el);
  }
  const mainSave = savedDocks.find((d) => d.id === 'main');
  const mainAllocation = allocations.get('main');
  make('main', 'Main', [...mainAllocation.panels, ...[...frames.values()].map((f) => f.panel)], mainAllocation.layout, root);
  for (const d of savedDocks) if (d.id !== 'main') {
    const a = allocations.get(d.id);
    make(d.id, d.title, a.panels, a.layout, frames.get(d.id).root);
    entries.get(d.id).framePanel = frames.get(d.id).panel;
    entries.get(d.id).window = d.window || null;
  }
  for (const d of savedDocks) for (const entry of d.layout?.hidden || [])
    if (!settled.parked.includes(entry.id)) entries.get(d.id)?.dock.setVisible(entry.id, false);
  entries.get('main').dock.render();
  busy = false;
  latest = snapshot();
  if (!prior || migrated) persist();

  // The dock windows the last page left waiting (awaitWindows, set below).
  let waiting = { has: () => false, take() {}, stop() {} };
  const group = {
    popDock(id, requested = {}, taking = null) {
      if (id === 'main' || narrow) return false;
      const e = entries.get(id);
      if (!e || !e.frame) return false;
      if (e.pop) { try { e.pop.win.focus(); } catch { /* window closed */ } return true; }
      const geo = dockGeometry(e, requested);
      let child = taking; // its window from before a reload, taken back: no window.open
      // One that may still wait is found by its name with no URL (nothing loaded in it) and taken back, never a page
      // loaded over it; one gone leaves a blank window of that name, which the open below loads.
      if (!child && waiting.has(id)) {
        let found = null;
        try { found = entries.get('main').dock._findHost(`dock-region-${id}`, { x: geo.left, y: geo.top, w: geo.width, h: geo.height }); } catch { found = null; }
        const m = found && marksOf(found);
        if (m && m.dock === groupKey && m.id === id && windowWaits(found)) { child = found; taking = found; waiting.take(id); }
      }
      if (!child) try { child = entries.get('main').dock._openHost(`dock-region-${id}`, {
        x: geo.left, y: geo.top, w: geo.width, h: geo.height,
      }); } catch { child = null; }
      if (!child) {
        if (e.window) { pendingWindows.add(id); showWindowNotice(); }
        return false;
      }
      e.window = geo;
      pendingWindows.delete(id);
      showWindowNotice();
      const pop = { win: child, watch: null, cleanup: null, offset: null };
      e.pop = pop;
      const main = entries.get('main').dock;
      if (e.framePanel && main.panels().some((p) => p.id === e.framePanel.id)) main._transferPanel(e.framePanel.id, transferBin);
      persist();
      const mount = () => {
        if (e.pop !== pop) return;
        let cd;
        try { cd = child.document; if (child.closed) { windowGone(e, pop); return; }
          if (cd.readyState === 'loading' || !cd.getElementById('dk-pop-root')) return;
        } catch { return; }
        const host = cd.getElementById('dk-pop-root');
        try { markWindow(child, { key: doc.defaultView.__dockPopKey, dock: groupKey, id, wait: adoptMs }); } catch { /* page is closing */ }
        windowState(child, false);
        cd.title = e.title;
        if (!cd.querySelector('base')) {
          const base = cd.createElement('base'); base.href = doc.baseURI; cd.head.prepend(base);
        }
        main._syncHost(cd);
        const bar = cd.createElement('div');
        bar.className = 'dk-dock-window-bar dk-mono';
        const title = cd.createElement('span'); title.textContent = e.title;
        const options = cd.createElement('button');
        options.type = 'button'; options.textContent = '⋯'; options.setAttribute('aria-label', `${e.title}: Options`);
        const menu = cd.createElement('div');
        menu.className = 'dk-dock-window-menu'; menu.setAttribute('role', 'menu'); menu.hidden = true;
        const mode = cd.createElement('button'); mode.type = 'button'; mode.textContent = 'View Mode → Window';
        mode.setAttribute('role', 'menuitemcheckbox'); mode.setAttribute('aria-checked', 'true');
        const back = cd.createElement('button'); back.type = 'button'; back.textContent = 'Dock back';
        back.setAttribute('role', 'menuitem');
        const rename = cd.createElement('button'); rename.type = 'button'; rename.textContent = 'Rename…';
        rename.setAttribute('role', 'menuitem');
        const remove = cd.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove dock';
        remove.setAttribute('role', 'menuitem');
        menu.append(mode, back, rename, remove); bar.append(title, options, menu);
        pop.titleEl = title;
        options.setAttribute('aria-expanded', 'false');
        options.addEventListener('click', () => {
          menu.hidden = !menu.hidden; options.setAttribute('aria-expanded', String(!menu.hidden));
          if (!menu.hidden) back.focus();
        });
        mode.addEventListener('click', () => { menu.hidden = true; });
        back.addEventListener('click', () => group.dockBack(id));
        rename.addEventListener('click', () => { menu.hidden = true; renameInput(id); });
        remove.addEventListener('click', () => group.removeDock(id));
        menu.addEventListener('keydown', (event) => {
          if (event.key === 'Escape') { event.preventDefault(); menu.hidden = true; options.focus(); }
        });
        e.frame.hidden = false;
        host.replaceChildren(bar, e.frame);
        const unhost = e.dock._attachHostDoc(cd);
        const undrag = attachDrag(cd);
        const helpHandle = typeof dockOptions.help?.mount === 'function' ? dockOptions.help.mount(cd) : null;
        const unhelp = typeof helpHandle === 'function' ? helpHandle : helpHandle?.destroy;
        const sync = () => { if (e.pop === pop) main._syncHost(cd, false); };
        const observer = new doc.defaultView.MutationObserver(sync);
        observer.observe(doc.documentElement, { attributes: true });
        if (Array.isArray(dockOptions.bodyAttrs) && dockOptions.bodyAttrs.length)
          observer.observe(doc.body, { attributes: true, attributeFilter: dockOptions.bodyAttrs });
        const resized = () => { rememberDockWindow(e); e.dock.render(); };
        const gone = () => windowGone(e, pop);
        child.addEventListener('pagehide', gone);
        child.addEventListener('resize', resized);
        doc.defaultView.addEventListener(dockOptions.themeEvent || 'dock-theme', sync);
        pop.cleanup = () => {
          unhost(); undrag(); if (typeof unhelp === 'function') unhelp.call(helpHandle); observer.disconnect();
          child.removeEventListener('pagehide', gone);
          child.removeEventListener('resize', resized);
          doc.defaultView.removeEventListener(dockOptions.themeEvent || 'dock-theme', sync);
        };
        for (const entry of entries.values()) entry.dock.render();
        rememberDockWindow(e);
        emit('dock-popped', { id });
        if (taking) emit('pop-adopt', { id: frameId(id), dock: id, window: child });
      };
      const started = Date.now();
      pop.watch = setInterval(() => {
        if (e.pop !== pop) return;
        if (child.closed) { windowGone(e, pop); return; }
        if (!pop.cleanup) {
          mount();
          if (Date.now() - started > 6000 && !pop.cleanup) windowGone(e, pop, { failed: true });
        } else rememberDockWindow(e);
      }, 100);
      mount();
      return true;
    },
    dockBack(id) {
      const e = entries.get(id);
      if (!e || id === 'main' || (!e.window && !e.pop)) return false;
      pendingWindows.delete(id);
      releaseDockWindow(e);
      showWindowNotice();
      return true;
    },
    createDock({ id, title = id, root: explicitRoot, where } = {}) {
      if (narrow) return editWide(() => group.createDock({ id, title, root: explicitRoot, where }));
      if (typeof id !== 'string' || !id || id === 'main' || entries.has(id) || registry.has(frameId(id))) throw new Error('Dock id must be new');
      busy = true;
      const f = explicitRoot ? { root: explicitRoot, panel: null } : frame(id, title);
      if (f.panel) root.appendChild(f.panel.el);
      const dock = make(id, String(title), [], null, f.root);
      entries.get(id).framePanel = f.panel;
      if (explicitRoot) {
        const e = entries.get(id);
        e.frame = explicitRoot;
        e.originalParent = explicitRoot.parentNode || doc.body;
        e.originalNext = explicitRoot.nextSibling;
      }
      if (f.panel) entries.get('main').dock.addPanel(f.panel, where ?? 'right');
      busy = false; persist();
      emit('dock-created', { id, title });
      return dock;
    },
    removeDock(id, { panelsTo = 'main' } = {}) {
      if (narrow) return editWide(() => group.removeDock(id, { panelsTo }));
      const e = entries.get(id);
      if (!e || id === 'main') return false;
      if (e.window || e.pop) group.dockBack(id);
      for (const p of [...e.dock.panels()]) {
        if (panelsTo === 'hide') { group.movePanel(p.id, { dock: 'main' }); entries.get('main').dock.setVisible(p.id, false); }
        else group.movePanel(p.id, { dock: 'main' });
      }
      busy = true;
      // The places it keeps for panels not given (yet) go to main, beside the panels that just moved there.
      entries.get('main').dock._adoptParked(e.dock.layout().parked);
      e.dock.destroy();
      if (e.framePanel) { entries.get('main').dock._removeQuiet(e.framePanel.id); entries.get('main').dock.forget(e.framePanel.id); e.frame.remove(); }
      entries.delete(id); order.splice(order.indexOf(id), 1);
      busy = false; persist(); emit('dock-removed', { id });
      return true;
    },
    addPanel(panel, { dock: hint, where } = {}) {
      if (narrow) return editWide(() => group.addPanel(panel, { dock: hint, where }));
      const p = typeof panel === 'string' ? registry.get(panel) : panel;
      if (!p) return null;
      if (!registry.has(p.id)) registry.set(p.id, p);
      const from = owner(p.id);
      // A dock given here wins; else the dock keeping its place, else the panel's own dock hint, else main.
      const dock = hint || (from ? null : parkedIn(p.id)) || p.dock || 'main';
      if (from) return group.movePanel(p.id, { dock, where });
      const to = entries.get(dock); if (!to) return null;
      const result = to.dock.addPanel(p, where);
      persist(); return result;
    },
    movePanel(id, { dock: toId, where } = {}) {
      if (narrow) return editWide(() => group.movePanel(id, { dock: toId, where }));
      const fromId = owner(id);
      const target = entries.get(toId);
      if (!target || !registry.has(id)) return false;
      if (!fromId) return group.addPanel(id, { dock: toId, where });
      if (fromId === toId) { if (where?.target) target.dock._place(id, where.target, where.side); return true; }
      const source = entries.get(fromId).dock;
      const current = source.panels().find((p) => p.id === id);
      const badge = source._badge(id);
      const panelZoom = source.panelZoom(id);
      const mode = source.viewMode(id);
      const side = source.side(id);
      busy = true;
      source._transferPanel(id, transferBin);
      if (!['unpinned', 'undock'].includes(mode)) source.forget(id);
      transferring = true;
      try { target.dock.addPanel(current, where?.target ? undefined : where); } finally { transferring = false; }
      if (where?.target) target.dock._place(id, where.target, where.side);
      else if (!where && ['unpinned', 'undock'].includes(mode)) {
        target.dock.setViewMode(id, mode);
        if (side) target.dock.moveSide(id, side);
      }
      if (badge) target.dock.setBadge(id, badge.text, badge.title);
      // addPanel has connected the original element in its new document before observers hear the event.
      if (panelZoom !== 1) target.dock.setPanelZoom(id, panelZoom);
      busy = false; persist(); emit('dock-moved', { id, from: fromId, to: toId });
      return true;
    },
    docks: () => order.map((id) => ({ id, title: entries.get(id).title,
      window: entries.get(id).window ? { ...entries.get(id).window } : null,
      panels: narrowWide ? idsIn(narrowWide.docks.find((d) => d.id === id)?.layout || { root: null }).filter((pid) => registry.has(pid))
        : entries.get(id).dock.panels().map((p) => p.id).filter((pid) => !pid.startsWith('dock:')) })),
    dock: (id) => entries.get(id)?.dock || null,
    layout: () => copyLayout(narrowWide || latest || snapshot()),
    zoom: () => groupZoom,
    /** Every dock of the group asks tabExtra again for each tab, as dock.refreshTabExtras() does. */
    refreshTabExtras() { for (const e of entries.values()) e.dock.refreshTabExtras(); },
    setZoom(value) {
      if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0 || value === groupZoom) return false;
      groupZoom = value;
      for (const e of entries.values()) e.dock.setZoom(value);
      if (narrowWide) {
        if (value === 1) delete narrowWide.zoom;
        else narrowWide.zoom = value;
        saveNarrowWide();
      }
      else persist();
      return true;
    },
    renameDock(id, title) {
      if (narrow) return editWide(() => group.renameDock(id, title));
      const e = entries.get(id);
      if (!e || id === 'main' || !String(title).trim()) return false;
      e.title = String(title).trim();
      if (e.framePanel) { e.framePanel.title = e.title; entries.get('main').dock.setTitle(e.framePanel.id, e.title); }
      if (e.pop?.titleEl) { e.pop.titleEl.textContent = e.title; e.pop.win.document.title = e.title; }
      persist(); return true;
    },
    exportLayout: () => layoutText(group.layout()),
    importLayout(text, { onUnknown } = {}) {
      let input = text;
      try {
        const old = JSON.parse(text);
        if (old && old.v === 1 && 'root' in old && !('format' in old)) input = layoutText(layoutFile([
          { id: 'main', title: 'Main', layout: splitFractions(old, root) },
        ], app));
      } catch { /* parseLayoutFile reports the JSON error */ }
      const parsed = parseLayoutFile(input);
      if (parsed.errors.length) return { ok: false, errors: parsed.errors, dropped: [], parked: [] };
      const incoming = parsed.value;
      const { docks: clean, home, dropped, duplicates, parked } = settle(incoming.docks);
      if (onUnknown) for (const id of [...parked, ...dropped]) onUnknown(id);
      busy = true;
      const wasNarrow = narrow;
      if (wasNarrow) group.setNarrow(false);
      for (const id of [...order]) if (id !== 'main' && entries.get(id).pop) group.dockBack(id);
      pendingWindows.clear(); showWindowNotice();
      groupZoom = incoming.zoom || 1;
      for (const d of clean) if (d.id !== 'main' && !entries.has(d.id)) group.createDock({ id: d.id, title: d.title });
      for (const id of [...order]) if (id !== 'main' && !clean.some((d) => d.id === id)) group.removeDock(id);
      for (const d of clean) {
        if (d.id !== 'main') group.renameDock(d.id, d.title);
        for (const id of layoutIds(d.layout, true)) if (registry.has(id) && home.get(id) === d.id && owner(id) !== d.id)
          group.movePanel(id, { dock: d.id });
      }
      for (const p of registry.values()) if (!home.has(p.id)) {
        const destination = p.dock && entries.has(p.dock) ? p.dock : 'main';
        if (owner(p.id) !== destination) group.movePanel(p.id, { dock: destination });
      }
      for (const d of clean) entries.get(d.id).dock._replaceLayout(splitFractions(d.layout,
        measuredRoot(d.id, entries.get(d.id).root), true));
      for (const e of entries.values()) e.dock.setZoom(groupZoom);
      for (const d of clean) for (const entry of d.layout.hidden || [])
        if (!parked.includes(entry.id)) entries.get(d.id).dock.setVisible(entry.id, false);
      for (const d of clean) if (d.id !== 'main') entries.get(d.id).window = d.window || null;
      order.splice(0, order.length, ...clean.map((d) => d.id));
      if (wasNarrow) group.setNarrow(true);
      savedAt = incoming.savedAt;
      busy = false;
      latest = snapshot();
      try { storage?.setItem(storageKey, layoutText(latest)); } catch { /* current page still works */ }
      if (!wasNarrow) for (const d of clean) if (d.id !== 'main' && d.window)
        group.popDock(d.id, d.window);
      emit('layout-imported', { dropped, parked });
      return { ok: true, errors: duplicates, dropped, parked };
    },
    setNarrow(on) {
      on = !!on;
      if (on === narrow) return;
      if (on) {
        narrowWide = group.layout();
        presentationSync = true;
        for (const id of order) if (id !== 'main' && entries.get(id).pop)
          releaseDockWindow(entries.get(id), { clear: false });
        const hidden = new Set(narrowWide.docks.flatMap((d) => (d.layout.hidden || []).map((entry) => entry.id)));
        for (const id of order) if (id !== 'main')
          for (const panel of [...entries.get(id).dock.panels()]) group.movePanel(panel.id, { dock: 'main' });
        for (const id of hidden) entries.get('main').dock.setVisible(id, false);
        for (const id of order) if (id !== 'main' && entries.get(id).framePanel)
          entries.get('main').dock.setVisible(frameId(id), false);
        for (const e of entries.values()) e.dock.setNarrow(true);
        for (const id of registry.keys()) entries.get('main').dock.setVisible(id, !hidden.has(id));
        for (const id of order) if (id !== 'main' && entries.get(id).framePanel)
          entries.get('main').dock.setVisible(frameId(id), false);
        narrow = true;
        presentationSync = false;
      } else {
        const wide = narrowWide;
        presentationSync = true;
        narrow = false;
        for (const e of entries.values()) e.dock.setNarrow(false);
        if (wide) group.importLayout(layoutText(wide));
        narrowWide = null;
        presentationSync = false;
        persist();
      }
    },
    narrow: () => narrow,
    reset() {
      if (narrow) return editWide(() => group.reset());
      busy = true;
      for (const id of [...order]) if (id !== 'main') group.removeDock(id);
      for (const p of registry.values()) if (owner(p.id) !== 'main') group.movePanel(p.id, { dock: 'main' });
      entries.get('main').dock.reset();
      busy = false; persist();
    },
    destroy() {
      doc.defaultView?.removeEventListener('pagehide', beforeLeave);
      doc.defaultView?.removeEventListener('pageshow', afterReturn);
      unhook();
      waiting.stop();
      gone = true;
      noticeObserver?.disconnect(); windowNotice?.remove();
      for (const id of order) if (id !== 'main' && entries.get(id).pop)
        releaseDockWindow(entries.get(id), { clear: false, save: false, announce: false });
      for (const e of entries.values()) e.dock.destroy();
      for (const e of entries.values()) if (e.framePanel) e.frame.remove();
      detachMainDrag();
      transferBin.remove();
    },
  };
  // A dock window this page's last load left waiting comes back by itself (it asks: adoptAsked); one lost (closed, or
  // out of time: awaitWindows) is opened again as before a reload (the browser may block that: then the notice offers
  // it). Never while it may still come back: a named window.open would load a new page in that very window.
  function adoptAsked(w) {
    const m = marksOf(w);
    if (!m || m.dock !== groupKey) return null;
    const e = entries.get(m.id);
    if (!e || m.id === 'main' || !e.frame) return null;
    if (e.pop && e.pop.win === w && !e.pop.waiting) return 'yes';
    if (narrow || !e.window || (e.pop && e.pop.win !== w)) return 'no';
    if (e.pop) e.pop = null;
    pendingWindows.delete(m.id);
    const ok = group.popDock(m.id, e.window, w) && !!e.pop?.cleanup;
    waiting.take(m.id);
    showWindowNotice();
    return ok ? 'yes' : 'no';
  }
  const unhook = adoptHook(doc.defaultView, adoptAsked);
  let gone = false; // destroyed
  waiting = awaitWindows(doc.defaultView, groupKey, takeWaiting(doc.defaultView, groupKey), {
    done: (id) => { const e = entries.get(id); return gone || !e || !!e.pop || !e.window || narrow; },
    lost: (ids) => {
      for (const id of ids) {
        const e = entries.get(id);
        if (!gone && e && !e.pop && e.window && !narrow) group.popDock(id, e.window);
      }
    },
  });
  if (initiallyNarrow) group.setNarrow(true);
  if (!initiallyNarrow) for (const d of savedDocks) if (d.id !== 'main' && d.window && !waiting.has(d.id)) group.popDock(d.id, d.window);
  callWindows(doc.defaultView);
  return group;
}
