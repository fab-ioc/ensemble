/* One notice for overlapping page work and hub work, shared by every page. */
(function () {
  const work = new Map();
  let showTimer = 0, hideTimer = 0, node;
  function ensure() {
    if (node) return node;
    node = document.createElement('div');
    node.id = 'ens-progress'; node.hidden = true;
    node.setAttribute('role', 'status'); node.setAttribute('aria-live', 'polite');
    node.innerHTML = '<span id="ens-progress-label"></span><span id="ens-progress-track" role="progressbar"><span id="ens-progress-fill"></span></span>';
    document.body.appendChild(node);
    return node;
  }
  function paint() {
    clearTimeout(hideTimer);
    const box = ensure();
    const item = [...work.values()].at(-1);
    if (!item) {
      clearTimeout(showTimer);
      if (!box.hidden) {
        box.classList.add('ens-progress-leaving');
        hideTimer = setTimeout(() => { if (!work.size) box.hidden = true; box.classList.remove('ens-progress-leaving'); }, 320);
      }
      return;
    }
    const total = Number(item.total) || 0, done = Math.max(0, Number(item.done) || 0);
    const fraction = total ? Math.min(1, done / total) : 0;
    const words = item.label || item.phase || 'Working…';
    const count = total ? ` · ${done.toLocaleString()} of ${total.toLocaleString()} done` : '';
    box.querySelector('#ens-progress-label').textContent = words + count;
    const track = box.querySelector('#ens-progress-track');
    if (total) { track.setAttribute('aria-valuenow', String(Math.round(fraction * 100))); track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); }
    else { track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuemin'); track.removeAttribute('aria-valuemax'); }
    box.classList.toggle('indeterminate', !total);
    box.querySelector('#ens-progress-fill').style.width = total ? `${fraction * 100}%` : '';
    box.classList.remove('ens-progress-leaving');
    if (box.hidden && !showTimer) showTimer = setTimeout(() => { showTimer = 0; if (work.size) box.hidden = false; }, 350);
  }
  const api = {
    set(key, item) { work.delete(key); work.set(key, item || {}); paint(); },
    clear(key) { work.delete(key); paint(); },
    sync(prefix, items) {
      for (const key of [...work.keys()]) if (key.startsWith(prefix) && !(key.slice(prefix.length) in (items || {}))) work.delete(key);
      for (const [key, value] of Object.entries(items || {})) { work.delete(prefix + key); work.set(prefix + key, value); }
      paint();
    }
  };
  window.EnsembleProgress = api;
})();
