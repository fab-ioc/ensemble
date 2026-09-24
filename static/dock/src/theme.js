// The theme: which colour set is on. The tokens live in css/theme.css as custom properties on :root, light by default
// and dark under :root[data-theme="dark"]. An app switches by setting data-theme (and data-scheme, light or dark, for
// anything that only cares which kind it is) on <html>, which applyTheme() does and announces with a `dock-theme` event
// on the window. A dock copies those attributes into its pop-out windows and keeps them in step.
//
//   import { createTheme } from './dock/src/theme.js';
//   const theme = createTheme();          // remembers the choice in localStorage['dock.theme']; 'system' follows the OS
//   theme.set('dark'); theme.toggle(); theme.onChange(({ pref, theme }) => …);

export const THEMES = { light: 'light', dark: 'dark' }; // name -> scheme
export const THEME_KEY = 'dock.theme';
export const THEME_EVENT = 'dock-theme';

function safeStorage() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
}

/** Puts theme `name` on <html> (data-theme, data-scheme) and tells the window. */
export function applyTheme(name, { root = typeof document !== 'undefined' ? document.documentElement : null, themes = THEMES,
  win = typeof window !== 'undefined' ? window : null, event = THEME_EVENT, pref = name } = {}) {
  if (!root) return;
  root.dataset.theme = name;
  root.dataset.scheme = themes[name] || 'light';
  if (win && event) {
    try { win.dispatchEvent(new win.CustomEvent(event, { detail: { pref, theme: name } })); } catch { /* no CustomEvent */ }
  }
}

/**
 * A remembered theme choice: `pref` is a theme's name or 'system' (the OS's light or dark). Options: storage (any
 * { getItem, setItem }), key, themes ({ name: 'light'|'dark' }), fallback, root, win, event.
 */
export function createTheme({ storage = safeStorage(), key = THEME_KEY, themes = THEMES, fallback = 'light',
  root = typeof document !== 'undefined' ? document.documentElement : null, win = typeof window !== 'undefined' ? window : null,
  event = THEME_EVENT } = {}) {
  const fns = [];
  const mq = win && win.matchMedia ? win.matchMedia('(prefers-color-scheme: dark)') : null;
  const valid = (v) => v === 'system' || Object.prototype.hasOwnProperty.call(themes, v);
  let chosen = null; // this page's choice: it holds even when storage cannot keep it
  function pref() {
    if (chosen !== null) return chosen;
    let v = null;
    try { v = storage ? storage.getItem(key) : null; } catch { v = null; }
    return valid(v) ? v : fallback;
  }
  // The theme for a kind (light or dark): the one named so if there is one, else the first of that kind.
  const ofKind = (kind) => (themes[kind] === kind ? kind : Object.keys(themes).find((n) => themes[n] === kind));
  const resolve = (p) => (p === 'system' ? ofKind(mq && mq.matches ? 'dark' : 'light') || fallback : p);
  function apply() {
    const p = pref();
    const name = resolve(p);
    applyTheme(name, { root, themes, win, event, pref: p });
    for (const fn of fns) fn({ pref: p, theme: name });
    return name;
  }
  function set(p) {
    if (!valid(p)) return current();
    chosen = p;
    try { if (storage) storage.setItem(key, p); } catch { /* not remembered; still applied */ }
    return apply();
  }
  function current() { return resolve(pref()); }
  /** To the other kind (light or dark). */
  function toggle() {
    const to = ofKind(themes[current()] === 'dark' ? 'light' : 'dark');
    return to ? set(to) : current();
  }
  if (mq) {
    const follow = () => { if (pref() === 'system') apply(); };
    if (mq.addEventListener) mq.addEventListener('change', follow); else if (mq.addListener) mq.addListener(follow);
  }
  if (win && win.addEventListener) win.addEventListener('storage', (e) => { if (e.key === key) { chosen = null; apply(); } });
  apply();
  return { apply, set, toggle, pref, current, themes, onChange: (fn) => { fns.push(fn); } };
}
