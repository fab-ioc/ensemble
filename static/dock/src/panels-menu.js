// A Panels menu for the app's top bar: every panel with show/hide, and Reset layout. The last panel on screen cannot be
// hidden, nor one the dock's can(id, 'hide') keeps. A panel in a window of its own is marked so, with Show window (Open
// window, after a reload) and Bring back.
// `button` opens it; `menu` is an empty element the app places (class "dk-menu" styles it).

import { escText, TEXT } from './dock.js';

export function mountPanelsMenu(dock, button, menu, panels, win = typeof window !== 'undefined' ? window : null, { resetLabel = 'Reset layout',
  outTag = TEXT.popTag, showLabel = TEXT.popShow, openLabel = TEXT.popOpen, backLabel = TEXT.popBack } = {}) {
  const doc = button.ownerDocument;
  if (!menu.classList.contains('dk-menu')) menu.classList.add('dk-menu');
  function draw() {
    const visible = panels.filter((p) => dock.isVisible(p.id));
    menu.innerHTML = panels.map((p) => {
      const on = dock.isVisible(p.id);
      const last = on && visible.length === 1;
      const kept = on && !last && dock.can && !dock.can(p.id, 'hide'); // the app's can(id, 'hide') says no
      const out = !!(dock.isOut && dock.isOut(p.id));
      const id = escText(p.id);
      let html = `<button type="button" role="menuitemcheckbox" data-panel-toggle="${id}" aria-checked="${on}"${on ? ' class="on"' : ''}`
        + `${last ? ' disabled title="the last panel on screen stays"' : kept ? ' disabled' : ''}>${escText(p.title)}`
        + `${out ? ` <span class="dk-menu-tag">${escText(outTag)}</span>` : ''}</button>`;
      if (out) {
        const open = dock.isOpenOut ? dock.isOpenOut(p.id) : true;
        html += `<div class="dk-menu-sub" role="group" aria-label="${escText(p.title)}: ${escText(outTag)}">`
          + `<button type="button" role="menuitem" data-panel-pop="show" data-panel-id="${id}">${escText(open ? showLabel : openLabel)}</button>`
          + `<button type="button" role="menuitem" data-panel-pop="back" data-panel-id="${id}">${escText(backLabel)}</button></div>`;
      }
      return html;
    }).join('') + `<div class="dk-menu-sep" role="separator"></div><button type="button" role="menuitem" data-layout-reset>${escText(resetLabel)}</button>`;
  }
  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (refocus) button.focus({ preventScroll: true });
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
    if (first) first.focus({ preventScroll: true });
  }
  button.addEventListener('click', () => { if (menu.hidden) open(); else close(false); });
  menu.addEventListener('click', (e) => {
    const pop = e.target.closest('[data-panel-pop]');
    if (pop) {
      const id = pop.dataset.panelId;
      close(false);
      if (pop.dataset.panelPop === 'back') dock.popIn(id);
      else if (dock.isOpenOut && !dock.isOpenOut(id)) dock.popOut(id); // from this click, so the browser lets it open
      else dock.reveal(id);
      return;
    }
    const t = e.target.closest('[data-panel-toggle]');
    if (t && !t.disabled) {
      const id = t.dataset.panelToggle;
      dock.setVisible(id, !dock.isVisible(id));
      draw();
      const again = menu.querySelector(`[data-panel-toggle="${id}"]`);
      if (again) again.focus({ preventScroll: true });
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
      items[(at + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus({ preventScroll: true });
    }
  });
  doc.addEventListener('click', (e) => {
    if (!menu.hidden && e.target.isConnected && !menu.contains(e.target) && !button.contains(e.target)) close(false);
  });
  if (win && win.addEventListener) win.addEventListener('resize', () => close(false));
  dock.onChange(() => { if (!menu.hidden) draw(); });
  return { open, close, draw };
}
