// A Panels menu for the app's top bar: every panel with show/hide, and Reset layout. The last panel on screen cannot be
// hidden. `button` opens it; `menu` is an empty element the app places (class "dk-menu" styles it).

import { escText } from './dock.js';

export function mountPanelsMenu(dock, button, menu, panels, win = typeof window !== 'undefined' ? window : null, { resetLabel = 'Reset layout' } = {}) {
  const doc = button.ownerDocument;
  if (!menu.classList.contains('dk-menu')) menu.classList.add('dk-menu');
  function draw() {
    const visible = panels.filter((p) => dock.isVisible(p.id));
    menu.innerHTML = panels.map((p) => {
      const on = dock.isVisible(p.id);
      const last = on && visible.length === 1;
      return `<button type="button" role="menuitemcheckbox" data-panel-toggle="${escText(p.id)}" aria-checked="${on}"${on ? ' class="on"' : ''}`
        + `${last ? ' disabled title="the last panel on screen stays"' : ''}>${escText(p.title)}</button>`;
    }).join('') + `<div class="dk-menu-sep" role="separator"></div><button type="button" role="menuitem" data-layout-reset>${escText(resetLabel)}</button>`;
  }
  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (refocus) button.focus();
  }
  function open() {
    draw();
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    const r = button.getBoundingClientRect();
    const vw = (win && win.innerWidth) || 1600;
    menu.style.top = (r.bottom + 4) + 'px';
    menu.style.left = Math.max(4, Math.min(r.right - menu.offsetWidth, vw - menu.offsetWidth - 4)) + 'px';
    const first = menu.querySelector('button:not([disabled])');
    if (first) first.focus();
  }
  button.addEventListener('click', () => { if (menu.hidden) open(); else close(false); });
  menu.addEventListener('click', (e) => {
    const t = e.target.closest('[data-panel-toggle]');
    if (t && !t.disabled) {
      const id = t.dataset.panelToggle;
      dock.setVisible(id, !dock.isVisible(id));
      draw();
      const again = menu.querySelector(`[data-panel-toggle="${id}"]`);
      if (again) again.focus();
      return;
    }
    if (e.target.closest('[data-layout-reset]')) { dock.reset(); close(true); }
  });
  menu.addEventListener('keydown', (e) => {
    const items = [...menu.querySelectorAll('button:not([disabled])')];
    const at = items.indexOf(doc.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      items[(at + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
    }
  });
  doc.addEventListener('click', (e) => {
    if (!menu.hidden && e.target.isConnected && !menu.contains(e.target) && !button.contains(e.target)) close(false);
  });
  if (win && win.addEventListener) win.addEventListener('resize', () => close(false));
  dock.onChange(() => { if (!menu.hidden) draw(); });
  return { open, close, draw };
}
