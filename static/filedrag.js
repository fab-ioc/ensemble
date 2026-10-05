// A file shown by Ensemble can leave the browser as a real file (Chromium),
// or as its URL/text (every browser). DragEvent's data store is writable only
// during dragstart, so small-file text is prefetched briefly and the handler
// immediately falls back to the URL whenever fresh metadata is unavailable.
(function (global) {
  'use strict';

  const TIP = 'Drag outside Ensemble — Chrome and Edge copy the file; Safari and Firefox receive its link or text.';
  const INTERNAL = 'application/x-ensemble-file';
  const META_TTL_MS = 2000;
  const META_PENDING_MS = 5000;
  const metaCache = new Map();
  let ghost = null;

  const fileName = path => String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || 'file';
  const pageToken = () => {
    try { return new URL(global.location.href).searchParams.get('token') || ''; } catch (e) { return ''; }
  };
  const addToken = url => {
    const token = pageToken();
    if (token) url.searchParams.set('token', token);
    return url;
  };
  const dragToken = () => {
    const meta = global.document && global.document.querySelector('meta[name="ensemble-file-drag"]');
    return meta && meta.getAttribute('content') || '';
  };
  const contextUrl = (route, info) => {
    const url = new URL(route, global.location.href);
    url.searchParams.set('path', info.path);
    if (info.room) url.searchParams.set('room', info.room);
    if (info.cwd) url.searchParams.set('cwd', info.cwd);
    const token = route === '/api/file/download' && dragToken();
    if (token) url.searchParams.set('drag', token);
    else addToken(url);
    return url;
  };
  const joinPath = (root, rel) => {
    if (!root || /^(?:[A-Za-z]:[\\/]|\\\\|~\/|\/)/.test(rel || '')) return rel || '';
    const sep = root.includes('\\') ? '\\' : '/';
    return root.replace(/[\\/]+$/, '') + sep + String(rel || '').replace(/^[\\/]+/, '').replace(/[\\/]/g, sep);
  };
  const chromiumDownload = () => {
    const ua = (global.navigator && global.navigator.userAgent) || '';
    return /(?:Chrome|Chromium)\//.test(ua) || /Edg\//.test(ua);
  };

  function infoOf(el) {
    if (!el) return null;
    if (el.matches && el.matches('a.file-link')) {
      try {
        const view = new URL(el.href, global.location.href);
        const path = view.searchParams.get('path') || el.dataset.filePath || '';
        return path ? { path, room: view.searchParams.get('room') || '', cwd: view.searchParams.get('cwd') || '', view: view.href } : null;
      } catch (e) { return null; }
    }
    let path = el.dataset && el.dataset.filePath || '';
    if (!path && el.dataset && el.dataset.file) {
      const host = el.closest('[data-file-root], [data-root]');
      path = joinPath(host && (host.dataset.fileRoot || host.dataset.root), el.dataset.file);
    }
    if (!path && el.dataset && el.dataset.path) path = el.dataset.path;
    if (!path) return null;
    return {
      path,
      room: el.dataset.fileRoom || '',
      cwd: el.dataset.fileCwd || '',
      view: el.dataset.fileView || contextUrl('/fileview', { path, room: el.dataset.fileRoom || '', cwd: el.dataset.fileCwd || '' }).href,
    };
  }

  function metadata(download) {
    const key = String(download);
    const now = Date.now();
    const hit = metaCache.get(key);
    if (hit && hit.value && now - hit.readyAt < META_TTL_MS) return hit.promise;
    if (hit && !hit.value && now - hit.startedAt < META_PENDING_MS) return hit.promise;
    if (hit) metaCache.delete(key);
    const meta = new URL(download);
    meta.searchParams.set('meta', '1');
    const entry = { value: null, promise: null, startedAt: now, readyAt: 0 };
    entry.promise = (global.fetch
      ? global.fetch(meta.href, { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null)
      : Promise.resolve(null))
      .then(value => {
        if (!value || typeof value !== 'object') {
          if (metaCache.get(key) === entry) metaCache.delete(key);
          return null;
        }
        entry.value = value;
        entry.readyAt = Date.now();
        return value;
      }, () => {
        if (metaCache.get(key) === entry) metaCache.delete(key);
        return null;
      });
    metaCache.set(key, entry);
    return entry.promise;
  }

  function cachedMetadata(download) {
    const entry = metaCache.get(String(download));
    if (entry && entry.value && Date.now() - entry.readyAt < META_TTL_MS) return entry.value;
    // Start or retry the refresh for the next drag, but never await it here or
    // use stale text in this drag's synchronous dataTransfer write window.
    metadata(download);
    return {};
  }

  function prepare(el) {
    const info = infoOf(el);
    if (!info) return Promise.resolve(null);
    return metadata(contextUrl('/api/file/download', info).href);
  }

  function dragImage(dt, name, doc) {
    if (ghost && ghost.remove) ghost.remove();
    const box = doc.createElement('div');
    box.setAttribute('aria-hidden', 'true');
    box.style.cssText = 'position:fixed;left:-10000px;top:-10000px;z-index:2147483647;display:flex;align-items:center;gap:8px;max-width:220px;height:32px;padding:0 10px;background:var(--surface-overlay,var(--surface,#fff));color:var(--fg,#172b4d);border:1px solid var(--border,#dfe1e6);border-radius:var(--r-200,6px);box-shadow:var(--e-200,0 4px 12px rgba(9,30,66,.2));font:500 var(--fs-200,12px)/16px system-ui,sans-serif;white-space:nowrap;overflow:hidden';
    box.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 1.5h6l4 4v9H3z"/><path d="M9 1.5v4h4"/></svg><span></span>';
    const label = box.querySelector('span');
    label.textContent = name;
    label.style.cssText = 'overflow:hidden;text-overflow:ellipsis';
    doc.body.appendChild(box);
    ghost = box;
    try { dt.setDragImage(box, 14, 16); } catch (e) {}
    global.setTimeout(() => { if (ghost === box) ghost = null; box.remove(); }, 0);
  }

  function start(ev, el) {
    const dt = ev.dataTransfer, info = infoOf(el);
    if (!dt || !info) return false;
    const download = contextUrl('/api/file/download', info).href;
    const meta = cachedMetadata(download);
    const name = meta.name || fileName(info.path);
    const mime = meta.mime || 'application/octet-stream';
    const plain = typeof meta.text === 'string' ? meta.text : download;
    try { dt.effectAllowed = 'copy'; } catch (e) {}
    if (chromiumDownload()) {
      try { dt.setData('DownloadURL', `${mime}:${name}:${download}`); } catch (e) {}
    }
    try { dt.setData('text/uri-list', download); } catch (e) {}
    try { dt.setData('text/plain', plain); } catch (e) {}
    try { dt.setData(INTERNAL, JSON.stringify({ path: info.path, view: info.view, download, name })); } catch (e) {}
    dragImage(dt, name, el.ownerDocument || document);
    return true;
  }

  function insertLink(ev) {
    const target = ev.target && ev.target.closest && ev.target.closest('textarea, [contenteditable="true"]');
    if (!target || !ev.dataTransfer || !Array.from(ev.dataTransfer.types || []).includes(INTERNAL)) return;
    let data = null;
    try { data = JSON.parse(ev.dataTransfer.getData(INTERNAL)); } catch (e) {}
    if (!data || !data.view) return;
    ev.preventDefault();
    if (target.matches('textarea')) {
      const a = target.selectionStart == null ? target.value.length : target.selectionStart;
      const b = target.selectionEnd == null ? a : target.selectionEnd;
      target.setRangeText(data.view, a, b, 'end');
    } else {
      const sel = target.ownerDocument.getSelection();
      if (sel && sel.rangeCount) {
        const range = sel.getRangeAt(0);
        range.deleteContents();
        const node = target.ownerDocument.createTextNode(data.view);
        range.insertNode(node); range.setStartAfter(node); range.collapse(true);
        sel.removeAllRanges(); sel.addRange(range);
      } else target.appendChild(target.ownerDocument.createTextNode(data.view));
    }
    const EventCtor = target.ownerDocument.defaultView.Event;
    target.dispatchEvent(new EventCtor('input', { bubbles: true }));
  }

  function addTip(el) {
    if (!el.title) el.title = TIP;
    else if (!el.title.includes('Chrome and Edge')) el.title += ' · ' + TIP;
  }

  function removeTip(el) {
    const suffix = ' · ' + TIP;
    if (el.title === TIP) el.removeAttribute('title');
    else if (el.title && el.title.endsWith(suffix)) el.title = el.title.slice(0, -suffix.length);
  }

  function decorateLinks(root) {
    if (!root) return;
    const links = [];
    if (root.matches && root.matches('a.file-link')) links.push(root);
    if (root.querySelectorAll) links.push(...root.querySelectorAll('a.file-link'));
    links.forEach(el => {
      el.draggable = true;
      el.dataset.fileDrag = 'link';
      addTip(el);
    });
  }

  function attach(doc) {
    if (!doc || doc._fileDragAttached) return;
    doc._fileDragAttached = true;
    decorateLinks(doc);
    const Observer = doc.defaultView && doc.defaultView.MutationObserver;
    if (Observer && doc.documentElement) {
      doc._fileDragObserver = new Observer(records => records.forEach(record =>
        record.addedNodes.forEach(node => { if (node.nodeType === 1) decorateLinks(node); })));
      doc._fileDragObserver.observe(doc.documentElement, { childList: true, subtree: true });
    }
    doc.addEventListener('dragstart', ev => {
      const el = ev.target && ev.target.closest && ev.target.closest('[data-file-drag], a.file-link');
      if (el) start(ev, el);
    }, true);
    const warm = ev => {
      const el = ev.target && ev.target.closest && ev.target.closest('[data-file-drag], a.file-link');
      if (el) prepare(el);
    };
    doc.addEventListener('pointerover', warm, true);
    doc.addEventListener('focusin', warm, true);
    doc.addEventListener('pointerdown', warm, true);
    doc.addEventListener('drop', insertLink, true);
  }

  function mark(el, path, opts) {
    if (!el || !path) return el;
    opts = opts || {};
    el.draggable = true;
    el.dataset.fileDrag = opts.kind || 'file';
    el.dataset.filePath = path;
    if (opts.room) el.dataset.fileRoom = opts.room;
    if (opts.cwd) el.dataset.fileCwd = opts.cwd;
    if (opts.view) el.dataset.fileView = opts.view;
    addTip(el);
    return el;
  }

  function unmark(el) {
    if (!el) return el;
    el.draggable = false;
    for (const key of ['fileDrag', 'filePath', 'fileRoom', 'fileCwd', 'fileView']) delete el.dataset[key];
    removeTip(el);
    return el;
  }

  global.FileDrag = { TIP, INTERNAL, attach, mark, unmark, prepare, start, infoOf, chromiumDownload, decorateLinks };
})(window);
