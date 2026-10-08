// A file shown by Ensemble can leave the browser as a real file (Chromium's
// DownloadURL), and a text file as its content (every browser). The hub's
// address never leaves: it means nothing to whoever receives the drop, so no
// text/uri-list, no link in text/plain, and the browser's own link data for an
// <a> is cleared. Drops inside Ensemble read the private INTERNAL type.
// DragEvent's data store is writable only during dragstart, so a text file's
// content is prefetched (hover, pointer moves, focus, press) and used only when
// fresh; otherwise the drag carries the file alone. Download, Copy content and
// Share… are the same hand-over without a drag (menus and the file view).
(function (global) {
  'use strict';

  const TIP = 'Drag out of Ensemble: Chrome and Edge hand over the file, every browser a text file’s content';
  const INTERNAL = 'application/x-ensemble-file';
  const META_TTL_MS = 2000;
  const META_PENDING_MS = 5000;
  const BLOB_TTL_MS = 30000;
  // Kept equal to dashboard._FILE_DRAG_TEXT_EXTS (tests/test_file_drag.py).
  const TEXT_EXTS = new Set([
    '.md', '.markdown', '.txt', '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.html', '.htm', '.css',
    '.scss', '.json', '.yaml', '.yml', '.toml', '.csv', '.tsv', '.sh', '.ps1', '.bat', '.cmd', '.sql', '.xml',
    '.log', '.ini', '.cfg', '.conf', '.rst', '.tex', '.java', '.kt', '.kts', '.go', '.rs', '.c', '.h', '.cpp',
    '.hpp', '.cs', '.swift', '.rb', '.php', '.lua', '.r', '.pl', '.vue', '.gradle', '.properties',
  ]);
  const metaCache = new Map();
  let shareFile = null;          // the one file Share… has read: { key, file, at, promise, timer }
  let ghost = null;

  const fileName = path => String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || 'file';
  const extOf = name => { const m = /(\.[^.\\/]+)$/.exec(fileName(name)); return m ? m[1].toLowerCase() : ''; };
  const isText = path => TEXT_EXTS.has(extOf(path));
  const isMarkdown = name => /\.(?:md|markdown)$/i.test(name || '');
  // DownloadURL is "mime:name:url": a colon in the name would end it early.
  const downloadName = name => String(name || 'file').replace(/[:\r\n]/g, '_');
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
    // A link's own data (its URL, an <a href> fragment) goes first: Chromium
    // fills it in before dragstart, and it is the hub's address.
    try { dt.clearData(); } catch (e) {}
    try { dt.effectAllowed = 'copy'; } catch (e) {}
    if (chromiumDownload()) {
      try { dt.setData('DownloadURL', `${mime}:${downloadName(name)}:${download}`); } catch (e) {}
    }
    if (typeof meta.text === 'string') {
      try { dt.setData('text/plain', meta.text); } catch (e) {}
      const html = isMarkdown(name) ? mdHtmlOrNull(meta.text) : null;
      if (html !== null) try { dt.setData('text/html', html); } catch (e) {}
    }
    try { dt.setData(INTERNAL, JSON.stringify({ path: info.path, view: info.view, name })); } catch (e) {}
    dragImage(dt, name, el.ownerDocument || document);
    return true;
  }

  // Markdown as plain, self-contained HTML for a mail or chat composer:
  // headings, paragraphs, lists, quotes, code, tables, emphasis. A link stays a
  // link only when it goes somewhere on the web; one to this hub, a local file
  // or a relative path keeps just its words, and images keep their alt text.
  function webUrl(raw) {
    let url;
    try { url = new URL(raw.replace(/&amp;/g, '&')); } catch (e) { return ''; }
    if (!/^https?:$/.test(url.protocol)) return '';
    // Host names compared without a trailing dot ("localhost." is localhost).
    const bare = h => String(h || '').toLowerCase().replace(/\.+$/, '');
    const host = bare(url.hostname);
    if (!host || host === bare(global.location && global.location.hostname)) return '';
    // An IPv6 literal is never the public web a colleague can open here
    // (loopback, link-local, unique-local and IPv4-mapped forms included).
    if (host.startsWith('[')) return '';
    const v4 = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(host);
    if (v4) {
      const [a, b] = [+v4[1], +v4[2]];
      if (a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254)
          || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)) return '';
    } else if (!host.includes('.') || /(?:^|\.)(?:localhost|local|internal|intranet|lan|home|corp|home\.arpa|ts\.net)$/.test(host)) return '';
    return url.href;
  }
  // Quotes nest at most this deep; deeper ">" stay text (a long run of them
  // must not recurse without end).
  const MD_QUOTE_DEPTH = 8;
  function mdHtml(src, depth) {
    depth = depth || 0;
    const escH = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const inline = t => {
      const keep = [];
      const hold = h => '\u0000' + (keep.push(h) - 1) + '\u0001';
      t = t.replace(/`([^`\n]+)`/g, (_, c) => hold('<code>' + escH(c) + '</code>'));
      t = escH(t)
        .replace(/!\[([^\]\n]*)\]\([^)\n]*\)/g, '$1')
        .replace(/\[([^\]\n]+)\]\(\s*(?:&lt;)?([^)\s]+?)(?:&gt;)?(?:\s+&quot;[^\n]*?&quot;)?\s*\)/g, (m, label, url) => {
          const href = webUrl(url);
          return href ? hold(`<a href="${escH(href)}">`) + label + hold('</a>') : label;
        })
        .replace(/\*\*(?=\S)([^*\n]*?\S)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^\w])__(?=\S)([^_\n]*?\S)__(?!\w)/g, '$1<strong>$2</strong>')
        .replace(/(^|[^\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])/g, '$1<em>$2</em>')
        .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, '$1<em>$2</em>')
        .replace(/~~(?=\S)([^~\n]*?\S)~~/g, '<del>$1</del>');
      return t.replace(/\u0000(\d+)\u0001/g, (_, i) => keep[+i]);
    };
    const cells = l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
    const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let para = [], list = null;
    const flush = () => {
      if (para.length) out.push('<p>' + inline(para.join(' ')) + '</p>');
      para = [];
      if (list) out.push(`<${list.tag}>` + list.items.map(i => '<li>' + inline(i) + '</li>').join('') + `</${list.tag}>`);
      list = null;
    };
    let i = 0;
    if (/^---\s*$/.test(lines[0] || '')) {          // front matter: kept, as code
      const end = lines.indexOf('---', 1);
      if (end > 0) { out.push('<pre><code>' + escH(lines.slice(1, end).join('\n')) + '</code></pre>'); i = end + 1; }
    }
    for (; i < lines.length; i++) {
      const l = lines[i];
      let m;
      if ((m = /^\s*(`{3,}|~{3,})/.exec(l))) {
        flush();
        const body = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(m[1]); i++) body.push(lines[i]);
        out.push('<pre style="font-family:monospace"><code>' + escH(body.join('\n')) + '</code></pre>');
      } else if (!l.trim()) flush();
      else if ((m = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); }
      else if (/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(l)) { flush(); out.push('<hr>'); }
      else if (l.includes('|') && /^[\s|:-]+$/.test(lines[i + 1] || '') && (lines[i + 1] || '').includes('-')) {
        flush();
        const head = cells(l), rows = [];
        for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) rows.push(cells(lines[i]));
        i--;
        const td = (tag, c) => `<${tag} style="border:1px solid #ccc;padding:4px 8px;text-align:left">${inline(c)}</${tag}>`;
        out.push('<table style="border-collapse:collapse"><thead><tr>' + head.map(c => td('th', c)).join('') + '</tr></thead><tbody>'
          + rows.map(r => '<tr>' + r.map(c => td('td', c)).join('') + '</tr>').join('') + '</tbody></table>');
      } else if (depth < MD_QUOTE_DEPTH && (m = /^\s*>\s?(.*)$/.exec(l))) {
        flush();
        const q = [m[1]];
        while (i + 1 < lines.length && (m = /^\s*>\s?(.*)$/.exec(lines[i + 1]))) { q.push(m[1]); i++; }
        out.push('<blockquote>' + mdHtml(q.join('\n'), depth + 1) + '</blockquote>');
      } else if ((m = /^\s*([-*+]|\d{1,9}[.)])\s+(.*)$/.exec(l))) {
        const tag = /\d/.test(m[1]) ? 'ol' : 'ul';
        if (para.length || (list && list.tag !== tag)) flush();
        if (!list) list = { tag, items: [] };
        list.items.push(m[2].replace(/^\[( |x|X)\]\s+/, (_, x) => (x === ' ' ? '☐ ' : '☑ ')));
      } else if (list && /^\s+\S/.test(l)) list.items[list.items.length - 1] += ' ' + l.trim();
      else { if (list) flush(); para.push(l.trim()); }
    }
    flush();
    return out.join('\n');
  }

  // The rendered HTML, or none: plain text still goes when rendering fails.
  function mdHtmlOrNull(text) {
    try { return mdHtml(text); } catch (e) { return null; }
  }

  // ---- The same hand-over without a drag: Download, Copy content, Share… --
  // Each takes the file's path and its context ({ room, cwd }), as mark() does.
  const actionInfo = (path, opts) => ({ path, room: (opts && opts.room) || '', cwd: (opts && opts.cwd) || '' });

  function download(path, opts) {
    const info = actionInfo(path, opts), doc = global.document;
    const a = doc.createElement('a');
    a.href = contextUrl('/api/file/download', info).href;
    a.download = fileName(path);
    a.rel = 'noopener';
    a.hidden = true;
    doc.body.appendChild(a);
    a.click();
    a.remove();
  }

  // Copies a text file's content (and, for Markdown, the rendered HTML). The
  // clipboard item is made at once with promised contents, so Safari keeps the
  // click's permission while the hub is read. Resolves to the file's name;
  // rejects with Error('not-text') for a binary or too large file.
  function copy(path, opts) {
    const info = actionInfo(path, opts);
    const read = metadata(contextUrl('/api/file/download', info).href).then(m => {
      if (!m) throw new Error('unreadable');
      if (typeof m.text !== 'string') throw new Error('not-text');
      return m;
    });
    const clip = global.navigator && global.navigator.clipboard;
    if (clip && clip.write && global.ClipboardItem) {
      const item = { 'text/plain': read.then(m => new Blob([m.text], { type: 'text/plain' })) };
      // Markdown also as rendered HTML; if that cannot be made, the escaped
      // text in its place, so the plain text is copied all the same.
      if (isMarkdown(fileName(path))) item['text/html'] = read.then(m => {
        const html = mdHtmlOrNull(m.text);
        return new Blob([html !== null ? html : '<pre>' + m.text.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) + '</pre>'], { type: 'text/html' });
      });
      let made;
      try { made = new global.ClipboardItem(item); } catch (e) { made = null; }
      if (made) return clip.write([made]).then(() => read).catch(e => read.then(() => { throw e; })).then(m => m.name);
    }
    if (clip && clip.writeText) return read.then(m => clip.writeText(m.text).then(() => m.name));
    return read.then(() => { throw new Error('no-clipboard'); });
  }

  function canShare() {
    const nav = global.navigator;
    if (!nav || typeof nav.share !== 'function' || typeof nav.canShare !== 'function' || typeof global.File !== 'function') return false;
    try { return !!nav.canShare({ files: [new global.File(['x'], 'x.txt', { type: 'text/plain' })] }); } catch (e) { return false; }
  }

  // The file as a File, read once and kept briefly, so a Share… pressed after
  // hovering it opens the share sheet without waiting. Only one file is kept,
  // and only for BLOB_TTL_MS after it arrived; a read that another one replaced
  // while it ran hands its file to whoever waits on it and keeps nothing.
  function fileOf(path, opts) {
    const info = actionInfo(path, opts), key = contextUrl('/api/file/download', info).href;
    const hit = shareFile;
    if (hit && hit.key === key && (!hit.at || Date.now() - hit.at < BLOB_TTL_MS)) return hit;
    if (hit) clearTimeout(hit.timer);
    const entry = { key, file: null, at: 0, promise: null, timer: 0 };
    const drop = () => { if (shareFile === entry) shareFile = null; };
    entry.promise = global.fetch(key, { credentials: 'same-origin' }).then(r => {
      if (!r.ok) throw new Error(r.status === 403 ? 'not-allowed' : 'unreadable');
      return r.blob();
    }).then(blob => {
      const file = new global.File([blob], fileName(path), { type: blob.type || 'application/octet-stream' });
      if (shareFile !== entry) return file;
      entry.file = file;
      entry.at = Date.now();
      entry.timer = global.setTimeout(drop, BLOB_TTL_MS);
      return entry.file;
    }, e => { drop(); throw e; });
    shareFile = entry;
    return entry;
  }

  function warmShare(path, opts) {
    if (canShare()) fileOf(path, opts).promise.catch(() => {});
  }

  // Resolves to the name once shared; rejects with an AbortError when the
  // person closed the sheet, or a NotAllowedError when the read took longer
  // than the click's permission (the file is ready for the next press).
  function share(path, opts) {
    const entry = fileOf(path, opts), name = fileName(path);
    const go = file => global.navigator.share({ files: [file], title: name }).then(() => name);
    return entry.file ? go(entry.file) : entry.promise.then(go);
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
    else if (!el.title.includes(TIP)) el.title += ' · ' + TIP;
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
    // Moving over a row keeps its content fresh for the drag that follows
    // (one read per META_TTL_MS at most: metadata() reuses a fresh one).
    doc.addEventListener('pointermove', warm, { capture: true, passive: true });
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

  global.FileDrag = {
    TIP, INTERNAL, TEXT_EXTS, attach, mark, unmark, prepare, start, infoOf, chromiumDownload, decorateLinks,
    isText, mdHtml, download, copy, canShare, warmShare, share,
  };
})(window);
