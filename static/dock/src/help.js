// A panel's help: a small circled ? in its title bar that opens a quick popover saying what the panel shows. The app
// gives the words; createDock gets { icon, mount, open } through its `help` option, and mounts it again in each pop-out
// window so the ? and the opt-in menu Help item work there too.
//
//   const help = createHelp({ log: { title: 'Log', body: ['What happened, newest first.'] } });
//   help.mount(document);
//   createDock({ ..., help });   // and a panel { id: 'log', help: 'log', ... }

import { escText } from './dock.js';

export function createHelp(content = {}) {
  const mounted = new WeakMap();
  const icon = (key) => {
    const h = content[key];
    if (!h) return '';
    return `<button type="button" class="dk-help-q" data-help="${escText(key)}" aria-label="help: ${escText(h.title)}" title="${escText(h.title)}: what it shows">?</button>`;
  };
  const panelHtml = (key) => {
    const h = content[key];
    if (!h) return '';
    const body = Array.isArray(h.body) ? h.body : [h.body || ''];
    return `<div class="dk-help-t">${escText(h.title)}</div>` + body.map((p) => `<p>${escText(p)}</p>`).join('');
  };

  // One popover per document. Clicking a ? never also presses what it sits beside (a tab).
  function mount(doc = document) {
    const pop = doc.createElement('div');
    pop.className = 'dk-help-pop';
    pop.setAttribute('role', 'dialog');
    pop.hidden = true;
    doc.body.appendChild(pop);
    let openFor = null;
    const keys = new Map();
    function close() { pop.hidden = true; openFor = null; }
    function open(btn) {
      const key = btn.dataset.help;
      if (openFor === btn && !pop.hidden) { close(); return; }
      pop.innerHTML = panelHtml(key);
      pop.setAttribute('aria-label', 'help: ' + ((content[key] || {}).title || key));
      pop.hidden = false;
      openFor = btn;
      const r = btn.getBoundingClientRect();
      const view = doc.defaultView || { innerWidth: 1200, innerHeight: 800 };
      pop.style.top = Math.max(4, Math.min(r.bottom + 4, view.innerHeight - pop.offsetHeight - 4)) + 'px';
      pop.style.left = Math.max(4, Math.min(r.left, view.innerWidth - pop.offsetWidth - 4)) + 'px';
    }
    function openKey(key, anchor) {
      if (!content[key]) return false;
      let btn = keys.get(key);
      if (!btn) {
        btn = doc.createElement('button');
        btn.dataset.help = key;
        btn.getBoundingClientRect = () => btn._anchor?.getBoundingClientRect() || { left: 4, bottom: 4 };
        keys.set(key, btn);
      }
      btn._anchor = anchor;
      open(btn);
      return true;
    }
    const onClick = (e) => {
      const q = e.target.closest && e.target.closest('.dk-help-q[data-help]');
      if (q) { e.preventDefault(); e.stopPropagation(); open(q); return; }
      if (!pop.hidden && !pop.contains(e.target)) close();
    };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    doc.addEventListener('click', onClick, true);
    doc.addEventListener('keydown', onKey);
    function destroy() {
      doc.removeEventListener('click', onClick, true);
      doc.removeEventListener('keydown', onKey);
      pop.remove();
    }
    const instance = { open, openKey, close, destroy, pop };
    mounted.set(doc, instance);
    return instance;
  }

  return { icon, mount, panelHtml, selector: '.dk-help-q', open: (key, anchor) => {
    const doc = anchor?.ownerDocument || document;
    return (mounted.get(doc) || mount(doc)).openKey(key, anchor);
  } };
}
