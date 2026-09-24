// The documents a dock draws in: the main page's, and each popped-out window's while it is open. A popped-out panel's
// element moves into its window's document and keeps its modules, listeners and state, so an app draws in more than one
// document. This module knows them.
//
// A dialog opened through hostDoc() opens in the document the person last pressed, typed or clicked in, so a
// confirmation asked from a popped-out panel is in that window, in front of them, and never behind it in the main one.

const extra = new Set(); // the popped-out windows' documents
const windowFns = [];
const docListeners = new Set(); // [type, fn]: on every document the screen draws in
const goneFns = new Map(); // a pop-out document -> what to undo when it goes
let last = null;

function mainDoc() { return typeof document !== 'undefined' ? document : null; }

function isOpen(doc) {
  if (!doc) return false;
  if (doc === mainDoc()) return true;
  const w = doc.defaultView;
  return extra.has(doc) && !(w && w.closed) && !!doc.body;
}

const TRACKED = ['pointerdown', 'keydown', 'click', 'focusin'];
function track(doc) {
  const mark = () => { last = doc; };
  for (const type of TRACKED) doc.addEventListener(type, mark, true);
  whenGone(doc, () => { for (const type of TRACKED) doc.removeEventListener(type, mark, true); });
}

/**
 * Runs `fn` when a pop-out window's document leaves the screen (closed, or its panel brought back): a dialog open in it
 * is cancelled, listeners put on it are taken off. Returns the way to stop. The main page's document never goes.
 */
export function whenGone(doc, fn) {
  if (!doc || doc === mainDoc()) return () => {};
  let fns = goneFns.get(doc);
  if (!fns) goneFns.set(doc, (fns = new Set()));
  fns.add(fn);
  return () => fns.delete(fn);
}

/** The document a dialog opens in: the one the person last used, while it is open; else the main page's. */
export function hostDoc() {
  return isOpen(last) ? last : mainDoc();
}

/** Says which document the person is using now (a test, or a window being focused on purpose). */
export function useDoc(doc) { last = doc; }

/** A popped-out window's document joins; `fns` given to onEveryWindow run for its window. */
export function addHostDoc(doc) {
  if (!doc || extra.has(doc)) return;
  extra.add(doc);
  track(doc);
  for (const [type, fn] of docListeners) doc.addEventListener(type, fn);
  const w = doc.defaultView;
  if (w) {
    for (const fn of windowFns) {
      try { const undo = fn(w); if (typeof undo === 'function') whenGone(doc, undo); } catch { /* one hook failing leaves the others */ }
    }
  }
}

export function removeHostDoc(doc) {
  extra.delete(doc);
  for (const [type, fn] of docListeners) doc.removeEventListener(type, fn);
  if (last === doc) last = mainDoc();
  const fns = goneFns.get(doc);
  goneFns.delete(doc);
  for (const fn of fns || []) { try { fn(); } catch { /* the window is going; the rest still run */ } }
}

export function hostDocs() {
  const out = [];
  if (mainDoc()) out.push(mainDoc());
  for (const d of extra) if (isOpen(d)) out.push(d);
  return out;
}

/** An element by id in whichever document holds it now: a panel's part may be in its own window. */
export function byId(id) {
  for (const d of hostDocs()) {
    const el = d.getElementById(id);
    if (el) return el;
  }
  return null;
}

/**
 * Runs `fn(win)` for the main window now and for each popped-out window as it opens (window-wide listeners). What
 * `fn` returns, a function, is run when that pop-out window goes.
 */
export function onEveryWindow(fn) {
  windowFns.push(fn);
  if (typeof window !== 'undefined') fn(window);
  for (const d of extra) {
    if (!d.defaultView) continue;
    const undo = fn(d.defaultView);
    if (typeof undo === 'function') whenGone(d, undo);
  }
}

/**
 * Listens for `type` on every document the screen draws in, now and as windows open (a key such as Escape, wherever
 * the person is). Returns the way to stop.
 */
export function listenEveryDoc(type, fn) {
  const entry = [type, fn];
  docListeners.add(entry);
  for (const d of [mainDoc(), ...extra]) if (d) d.addEventListener(type, fn);
  return () => {
    docListeners.delete(entry);
    for (const d of [mainDoc(), ...extra]) if (d) d.removeEventListener(type, fn);
  };
}

if (mainDoc()) track(mainDoc());
