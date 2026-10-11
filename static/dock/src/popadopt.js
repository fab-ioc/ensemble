// A window a dock opened (a panel's, or a whole dock's) outlives a reload of the main page (popAdoptTimeout). The page
// going away leaves it open, inert, "reconnecting…"; the pop-out page (popout.html) then asks its opener, the page
// that loaded next, to take it back: window.__dockPopAdopt(itself), which every dock on that page answers through
// adoptHook. A dock knows its own windows by the marks it put on them (markWindow): its storage key and the panel's
// id. The window closes itself when no page takes it back in time, or when its opener is closed.
//
// The page that loads next also says it is there (a BroadcastChannel message), so a window whose timers the browser
// slows (hidden, minimised) asks at once instead of at its next tick.

export const ADOPT_TIMEOUT = 15000;
/** How long a reloaded page waits for a window it had before its notice (or quiet mark) says that window is lost. */
export const ADOPT_GRACE = 2500;
const CHANNEL = 'dock-pop';
const WAITING = 'dock.popWaiting:';

/** The marks a dock puts on a window it draws in: whose it is, and how long it waits for a page after a reload. */
export function markWindow(w, { key, dock, id, wait }) {
  try {
    w.__dockPopKey = key;
    w.__dockPopDock = dock;
    w.__dockPopId = id;
    w.__dockPopWait = wait;
  } catch { /* the window is going */ }
}

/** The dock and id a window was marked with (null for a window no dock marked). Only strings: the page that marked
 * it may be gone. */
export function marksOf(w) {
  try {
    const dock = w.__dockPopDock, id = w.__dockPopId;
    return typeof dock === 'string' && typeof id === 'string' ? { dock, id } : null;
  } catch { return null; }
}

/** Whether the window's own page waits for the next page (popout.html does; an app's page of its own may not). */
export function windowWaits(w) {
  try { return !w.closed && w.__dockPopWaits === true; } catch { return false; }
}

/** The window's page shows itself waiting (inert, "reconnecting…") or live again. */
export function windowState(w, waiting) {
  try { if (typeof w.__dockPopState === 'function') w.__dockPopState(!!waiting); } catch { /* the window is going */ }
}

/**
 * `fn(w)` answers a window asking the page to take it back: 'yes' (taken), 'no' (it should close) or null (not this
 * dock's, or not yet). Every dock on the page is asked in turn. Returns the way to stop.
 */
export function adoptHook(win, fn) {
  if (!win) return () => {};
  let list;
  try {
    list = win.__dockPopAdopters;
    if (!Array.isArray(list)) {
      list = win.__dockPopAdopters = [];
      win.__dockPopAdopt = (w) => {
        for (const f of list.slice()) {
          let r = null;
          try { r = f(w); } catch (e) { r = null; try { (win.reportError || console.error)(e); } catch { /* ignore */ } }
          if (r === 'yes' || r === 'no') return r;
        }
        return null;
      };
    }
  } catch { return () => {}; }
  list.push(fn);
  return () => { const i = list.indexOf(fn); if (i >= 0) list.splice(i, 1); };
}

/** Tells the windows waiting for a page that one is here (each asks it at once). */
export function callWindows(win) {
  try {
    const BC = win && win.BroadcastChannel;
    if (!BC) return;
    const c = new BC(CHANNEL);
    c.postMessage('adopt');
    c.close();
  } catch { /* no channel: they ask at their next tick */ }
}

/** The page is going: the ids of `dock`'s windows left waiting for the next page, and how long they wait (this tab's
 * sessionStorage). */
export function writeWaiting(win, dock, ids, wait = ADOPT_TIMEOUT) {
  try {
    const s = win.sessionStorage;
    if (ids.length) s.setItem(WAITING + dock, JSON.stringify({ at: Date.now(), wait, ids }));
    else s.removeItem(WAITING + dock);
  } catch { /* no sessionStorage: their notices come at once */ }
}

/** Read once, by the page that loads next: { at, wait, ids } (the ids left waiting, when, for how long), or null. */
export function takeWaiting(win, dock) {
  try {
    const s = win.sessionStorage;
    const raw = s.getItem(WAITING + dock);
    if (raw === null) return null;
    s.removeItem(WAITING + dock);
    const v = JSON.parse(raw);
    if (!v || !Array.isArray(v.ids) || !Number.isFinite(v.at)) return null;
    const wait = Number.isFinite(v.wait) && v.wait > 0 ? v.wait : ADOPT_TIMEOUT;
    return { at: v.at, wait, ids: v.ids.filter((x) => typeof x === 'string') };
  } catch { return null; }
}

// A waiting window stamps the time in localStorage (popout.html), each time it asks; it clears the stamp when it is
// taken back or closes.
const ALIVE = 'dock.popAlive:';
function aliveAt(win, dock, id) {
  try { const v = Number(win.localStorage.getItem(ALIVE + dock + ':' + id)); return v > 0 ? v : null; } catch { return null; }
}
function dropAlive(win, dock, id) {
  try { win.localStorage.removeItem(ALIVE + dock + ':' + id); } catch { /* no storage */ }
}

/**
 * The windows the last page left waiting (`waited`, from takeWaiting), followed until each is back or lost. One is
 * coming back while its time (`waited.wait` from when the page went) has not run out and either the page is younger
 * than ADOPT_GRACE (the time a window needs to ask) or the window's stamp is fresh (it asked within ADOPT_GRACE: a
 * window that waits longer, its panel not added yet or its asking slow, stays so). A window closed stops stamping and
 * is lost. Every `every` ms the page calls the windows (each asks, and stamps, at once however slow its timers are)
 * and `lost(ids)` hears of those that will not come back. `done(id)`: the panel no longer needs its window (back, or
 * a window of its own again).
 */
export function awaitWindows(win, dock, waited, { done, lost, every = 1000 }) {
  const pend = new Set();
  let timer = null;
  const none = { has: () => false, take() {}, stop() {} };
  if (!win || !waited) return none;
  const born = Date.now();
  const end = waited.at + waited.wait;
  for (const id of waited.ids) if (born < end) pend.add(id); else dropAlive(win, dock, id);
  const coming = (id) => {
    const now = Date.now();
    if (now >= end) return false;
    if (now < born + ADOPT_GRACE) return true;
    const a = aliveAt(win, dock, id);
    return a !== null && now - a < ADOPT_GRACE;
  };
  const tick = () => {
    timer = null;
    const gone = [];
    for (const id of [...pend]) {
      if (done(id)) { pend.delete(id); dropAlive(win, dock, id); } else if (!coming(id)) { pend.delete(id); dropAlive(win, dock, id); gone.push(id); }
    }
    if (gone.length) lost(gone);
    if (pend.size) { callWindows(win); timer = setTimeout(tick, every); }
  };
  if (!pend.size) return none;
  timer = setTimeout(tick, every);
  return {
    has: (id) => pend.has(id),
    /** `id`'s window is back: no longer followed. */
    take(id) { if (pend.delete(id)) dropAlive(win, dock, id); },
    stop() { clearTimeout(timer); timer = null; pend.clear(); },
  };
}
