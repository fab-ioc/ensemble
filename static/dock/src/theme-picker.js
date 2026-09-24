// A theme picker for the app's top bar: "System" and every colour set, each with a small swatch of its own colours,
// grouped by where the set comes from. `theme` is createTheme()'s handle; `button` opens it; `menu` is an empty element
// the app places (class "dk-menu" styles it). The swatches are drawn with the set's own tokens (css/theme.css applies
// each set to any element with data-dk-theme), so they are true to the set whatever theme the page has on.

import { THEME_LIST } from './theme.js';

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const GROUPS = { base: 'Themes', ensemble: 'Ensemble', extra: 'More' };

/** What a swatch shows: the panel ground with a text line, the chrome, and the accent. */
export function swatchHtml(name) {
  return `<span class="dk-swatch" data-dk-theme="${esc(name)}" aria-hidden="true"><i class="g"></i><i class="c"></i><i class="a"></i></span>`;
}

/**
 * Options: list (default THEME_LIST, or only those of `theme.themes` it knows), label(entry) for an entry's text,
 * groups ({ origin: heading }), systemLabel, win.
 */
export function mountThemePicker(theme, button, menu, { list = null, groups = GROUPS, systemLabel = 'System (light or dark)',
  label = (t) => t.label, win = typeof window !== 'undefined' ? window : null } = {}) {
  const doc = button.ownerDocument;
  const entries = list || THEME_LIST.filter((t) => !theme.themes || Object.prototype.hasOwnProperty.call(theme.themes, t.name));
  if (!menu.classList.contains('dk-menu')) menu.classList.add('dk-menu');
  menu.classList.add('dk-theme-menu');
  menu.setAttribute('role', 'menu');
  const item = (value, text, sw, title) => {
    const on = theme.pref() === value;
    return `<button type="button" role="menuitemradio" data-dk-theme-pick="${esc(value)}" aria-checked="${on}"${on ? ' class="on"' : ''}`
      + `${title ? ` title="${esc(title)}"` : ''}>${sw}<span>${esc(text)}</span></button>`;
  };
  function draw() {
    const sys = `<span class="dk-swatch dk-swatch-sys" aria-hidden="true">${swatchHtml('light')}${swatchHtml('dark')}</span>`;
    let html = item('system', systemLabel, sys, 'follows the operating system: Light or Dark');
    let at = null;
    for (const t of entries) {
      if (t.origin !== at) { at = t.origin; html += `<div class="dk-menu-head" role="presentation">${esc(groups[at] || at)}</div>`; }
      html += item(t.name, label(t), swatchHtml(t.name), t.scheme);
    }
    menu.innerHTML = html;
  }
  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (refocus) button.focus({ preventScroll: true });
  }
  // The given item in view, scrolling the menu only (scrollIntoView would scroll the page and the dock too).
  function reveal(item) {
    if (!item) return;
    const below = item.offsetTop + item.offsetHeight - (menu.scrollTop + menu.clientHeight);
    if (item.offsetTop < menu.scrollTop) menu.scrollTop = item.offsetTop; else if (below > 0) menu.scrollTop += below;
  }
  function open() {
    draw();
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    const r = button.getBoundingClientRect();
    const vw = (win && win.innerWidth) || 1600;
    const vh = (win && win.innerHeight) || 900;
    menu.style.top = (r.bottom + 4) + 'px';
    menu.style.maxHeight = Math.max(160, vh - r.bottom - 12) + 'px';
    menu.style.left = Math.max(4, Math.min(r.right - menu.offsetWidth, vw - menu.offsetWidth - 4)) + 'px';
    const cur = menu.querySelector('[aria-checked="true"]') || menu.querySelector('button');
    if (cur) {
      cur.focus({ preventScroll: true });
      reveal(cur);
    }
  }
  const onButton = () => { if (menu.hidden) open(); else close(false); };
  const onPick = (e) => {
    const b = e.target.closest('[data-dk-theme-pick]');
    if (!b) return;
    theme.set(b.dataset.dkThemePick);
    close(true);
  };
  const onKey = (e) => {
    const items = [...menu.querySelectorAll('button')];
    const at = items.indexOf(doc.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = items[(at + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length];
      next.focus({ preventScroll: true });
      reveal(next);
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      const next = items[e.key === 'Home' ? 0 : items.length - 1];
      next.focus({ preventScroll: true });
      reveal(next);
    }
  };
  const onOutside = (e) => {
    if (!menu.hidden && e.target.isConnected && !menu.contains(e.target) && !button.contains(e.target)) close(false);
  };
  const onResize = () => close(false);
  button.setAttribute('aria-haspopup', 'true');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', onButton);
  menu.addEventListener('click', onPick);
  menu.addEventListener('keydown', onKey);
  doc.addEventListener('click', onOutside);
  if (win && win.addEventListener) win.addEventListener('resize', onResize);
  theme.onChange(() => { if (!menu.hidden) draw(); });
  return {
    open, close, draw, entries,
    destroy() {
      button.removeEventListener('click', onButton);
      menu.removeEventListener('click', onPick);
      menu.removeEventListener('keydown', onKey);
      doc.removeEventListener('click', onOutside);
      if (win && win.removeEventListener) win.removeEventListener('resize', onResize);
    },
  };
}
