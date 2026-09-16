// ---- Images pasted or dropped into a chat box: begin ------------------------
// session.html (the chat box, a comment on a passage) and index.html (a
// comment on a diff's lines, the Files panel) share this. A pasted or dropped
// image is uploaded at once to the hub (POST /api/room/attachment, stored in
// the room's attachments folder) and shows as a chip with a remove ×; the
// message then carries the stored names, and the hub ends what it types into
// the agent with one "[image] <absolute path>" line per image
// (message_refs.py). A balloon shows those lines as thumbnails.
// tests/test_attach_page.py runs this file in Node.
const ATT_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const ATT_MAX = 20 * 1024 * 1024;
const ATT_PREFIX = '[image] ';

// The images in a paste or a drop (a ClipboardEvent's clipboardData or a
// DragEvent's dataTransfer), as Files. Text, and files of other kinds, are not.
function attImages(dt) {
  if (!dt) return [];
  const out = [];
  for (const it of Array.from(dt.items || [])) {
    if (it && it.kind === 'file' && ATT_TYPES.includes(it.type)) {
      const f = it.getAsFile && it.getAsFile();
      if (f) out.push(f);
    }
  }
  if (!out.length) for (const f of Array.from(dt.files || [])) if (f && ATT_TYPES.includes(f.type)) out.push(f);
  return out;
}

// "2026-09-16 14.03.27 screenshot.png": what a pasted image is called.
function attStampName(d, type) {
  d = d || new Date();
  const p = n => String(n).padStart(2, '0');
  const ext = { 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp' }[type] || '.png';
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}.${p(d.getMinutes())}.${p(d.getSeconds())} screenshot${ext}`;
}

// A dropped file keeps its name; a pasted one (the browser calls it image.png,
// or nothing) is named by the hub.
const attGivenName = (f, pasted) => (pasted || !f || !f.name || /^image\.\w+$/i.test(f.name)) ? '' : f.name;

const attUrl = (room, name) => '/api/room/attachment?room=' + encodeURIComponent(room) + '&name=' + encodeURIComponent(name);

// Upload one image for a room: resolves {name, path, url, room}, rejects with
// the hub's words.
async function attUpload(room, file, name) {
  if (!room) throw new Error('this chat has not loaded yet');
  if (file.size > ATT_MAX) throw new Error(`the image is ${(file.size / 1048576).toFixed(1)} MB; an image can be at most 20 MB`);
  const r = await fetch('/api/room/attachment?room=' + encodeURIComponent(room) + (name ? '&name=' + encodeURIComponent(name) : ''),
    { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file });
  let d = null;
  try { d = await r.json(); } catch (e) {}
  if (!r.ok || !d || !d.ok) throw new Error((d && (d.message || d.error)) || ('error ' + r.status));
  return { name: d.name, path: d.path, url: d.url || attUrl(room, d.name), room };
}

// A text's "[image] <path>" lines at its end: { words, paths }.
function attSplit(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\s+$/, '').split('\n');
  let n = lines.length;
  while (n && lines[n - 1].startsWith(ATT_PREFIX) && lines[n - 1].slice(ATT_PREFIX.length).trim()) n--;
  if (n === lines.length) return { words: String(text || ''), paths: [] };
  return { words: lines.slice(0, n).join('\n').replace(/\s+$/, ''), paths: lines.slice(n).map(l => l.slice(ATT_PREFIX.length).trim()) };
}
const attBase = p => String(p || '').split(/[\\/]/).pop();

// Thumbnails for a balloon's images: each opens full size in a new tab.
function attThumbsHtml(room, paths, esc) {
  if (!paths || !paths.length || !room) return '';
  return '<div class="att-thumbs">' + paths.map(p => {
    const name = attBase(p), u = attUrl(room, name);
    return `<a class="att-thumb" href="${esc(u)}" target="_blank" rel="noopener" title="${esc(name)}"><img src="${esc(u)}" alt="${esc(name)}" loading="lazy"></a>`;
  }).join('') + '</div>';
}

// The images of one box being written: a chip each, uploading, stored or
// refused. room() says where they go; changed() is called whenever the list
// or a chip's state changes (to redraw and to turn Send on or off).
function attBox(room, changed) {
  const list = [];
  let seq = 0;
  const box = {
    list,
    add(files, pasted) {
      for (const f of files || []) {
        const it = { id: 'att' + (++seq), label: attGivenName(f, pasted) || 'screenshot', state: 'up', err: '', name: '', url: '', room: '' };
        try { it.preview = URL.createObjectURL(f); } catch (e) { it.preview = ''; }
        list.push(it);
        attUpload(room(), f, attGivenName(f, pasted)).then(res => {
          Object.assign(it, res, { state: 'ok', label: res.name });
        }, e => {
          it.state = 'err'; it.err = (e && e.message) || 'not uploaded';
        }).then(() => { if (list.includes(it)) changed(box); });
      }
      if (files && files.length) changed(box);
    },
    // Images already stored ({room, name}), as a comment being edited has them.
    keep(images) {
      for (const x of images || []) {
        if (!x || !x.room || !x.name) continue;
        list.push({ id: 'att' + (++seq), label: x.name, state: 'ok', err: '', name: x.name, room: x.room, url: attUrl(x.room, x.name), preview: '' });
      }
    },
    remove(id) {
      const i = list.findIndex(x => x.id === id);
      if (i < 0) return;
      const [it] = list.splice(i, 1);
      if (it.preview) { try { URL.revokeObjectURL(it.preview); } catch (e) {} }
      changed(box);
    },
    clear() { for (const it of list.splice(0)) if (it.preview) { try { URL.revokeObjectURL(it.preview); } catch (e) {} } changed(box); },
    busy: () => list.some(x => x.state === 'up'),
    failed: () => list.filter(x => x.state === 'err'),
    // What a message carries: {room, name} of each stored image.
    stored: () => list.filter(x => x.state === 'ok').map(x => ({ room: x.room, name: x.name })),
    html(esc) {
      if (!list.length) return '';
      return list.map(x => `<span class="att-chip${x.state === 'err' ? ' err' : ''}" data-att="${esc(x.id)}" title="${esc(x.state === 'err' ? 'Not uploaded: ' + x.err : x.state === 'up' ? 'Uploading…' : x.label)}">`
        + (x.state !== 'err' && (x.url || x.preview) ? `<img src="${esc(x.url || x.preview)}" alt="">` : '')
        + `<span class="att-name">${esc(x.state === 'up' ? 'Uploading…' : x.state === 'err' ? 'Not uploaded' : x.label)}</span>`
        + `<button type="button" class="att-x" data-att-x="${esc(x.id)}" title="Remove this image" aria-label="Remove this image">×</button></span>`).join('');
    },
  };
  return box;
}

// Paste and drop on a box: images go to it (and nothing else happens); text
// pastes as ever. `zone` takes drops, `field` pastes; either may be the same.
function attWire(field, zone, box) {
  if (field) field.addEventListener('paste', e => {
    const files = attImages(e.clipboardData);
    if (!files.length) return;
    e.preventDefault();
    box.add(files, true);
  });
  if (zone) {
    zone.addEventListener('dragover', e => {
      const dt = e.dataTransfer;
      if (dt && Array.from(dt.types || []).includes('Files')) { e.preventDefault(); dt.dropEffect = 'copy'; }
    });
    zone.addEventListener('drop', e => {
      const files = attImages(e.dataTransfer);
      if (!files.length) return;
      e.preventDefault();
      e.stopPropagation();
      box.add(files, false);
    });
  }
}
// ---- Images pasted or dropped into a chat box: end --------------------------
