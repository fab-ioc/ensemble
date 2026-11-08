// ---- Files pasted, dropped or picked into a chat box: begin -----------------
// session.html (the chat box, an ask being written, a comment on a passage)
// and index.html (a comment on a diff's lines, the Files panel) share this. A
// pasted, dropped or picked file is uploaded at once to the hub (POST
// /api/room/attachment, stored in the room's attachments folder) and shows as
// a chip with a remove ×; the message then carries the stored names, and the
// hub ends what it types into the agent with one "[image] <absolute path>"
// line per image and one "[file] <absolute path>" line per other file
// (message_refs.py). A balloon shows images as thumbnails, other files as a
// chip (type, name, size) that opens in the file view or downloads.
// tests/test_attach_page.py runs this file in Node.
const ATT_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const ATT_MAX = 20 * 1024 * 1024;
const ATT_PREFIX = '[image] ';
const ATT_FILE_PREFIX = '[file] ';
const ATT_IMAGE_EXT = /\.(png|jpe?g|gif|webp)$/i;
// What the file view can show (text, Markdown, code, a PDF, an image, a page
// sandboxed): a click on such a chip opens it there; anything else downloads.
const ATT_VIEW_EXT = /\.(md|markdown|txt|text|log|csv|tsv|json|jsonl|ya?ml|toml|ini|cfg|conf|xml|html?|css|js|mjs|cjs|ts|tsx|jsx|py|rb|go|rs|java|kt|c|h|cc|cpp|hpp|cs|swift|php|sh|bash|ps1|bat|sql|diff|patch|pdf|png|jpe?g|gif|webp|svg|bmp|ico)$/i;

// The files in a paste or a drop (a ClipboardEvent's clipboardData or a
// DragEvent's dataTransfer), as Files; `only` keeps those it accepts.
function attTake(dt, only) {
  if (!dt) return [];
  const out = [];
  for (const it of Array.from(dt.items || [])) {
    if (it && it.kind === 'file' && (!only || only(it))) {
      const f = it.getAsFile && it.getAsFile();
      if (f) out.push(f);
    }
  }
  if (!out.length) for (const f of Array.from(dt.files || [])) if (f && (!only || only(f))) out.push(f);
  return out;
}
// Images only (PNG, JPEG, GIF, WebP): the feedback form keeps to those.
const attImages = dt => attTake(dt, x => ATT_TYPES.includes(x.type));
// Every file, of any kind. Text is not a file.
const attFiles = dt => attTake(dt, null);
const attIsImage = f => !!f && (ATT_TYPES.includes(f.type) || (!f.type && ATT_IMAGE_EXT.test(f.name || '')));

// "2026-09-16 14.03.27 screenshot.png": what a pasted image is called.
function attStampName(d, type) {
  d = d || new Date();
  const p = n => String(n).padStart(2, '0');
  const ext = { 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp' }[type] || '.png';
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}.${p(d.getMinutes())}.${p(d.getSeconds())} screenshot${ext}`;
}

// A dropped or picked file keeps its name, and so does a pasted file that is
// not an image (a PDF copied in Explorer or Finder). A pasted image (the
// browser calls it image.png, or nothing) is named by the hub.
const attGivenName = (f, pasted) => (!f || !f.name || /^image\.\w+$/i.test(f.name) || (pasted && attIsImage(f))) ? '' : f.name;

const attUrl = (room, name) => '/api/room/attachment?room=' + encodeURIComponent(room) + '&name=' + encodeURIComponent(name);

// "12 KB", "3.4 MB": a file's size for a chip.
function attSize(n) {
  n = Number(n);
  if (!isFinite(n) || n < 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return Math.max(1, Math.round(n / 1024)) + ' KB';
  return (n / 1048576).toFixed(n < 10 * 1048576 ? 1 : 0) + ' MB';
}
const attExt = name => { const m = /\.([A-Za-z0-9]{1,5})$/.exec(String(name || '')); return m ? m[1].toUpperCase() : ''; };
// The small tile that says a file's kind: its extension, or a plain mark.
const attIcon = (name, esc) => `<span class="att-ico" aria-hidden="true">${esc(attExt(name).slice(0, 4) || 'FILE')}</span>`;

// Upload one file for a room: resolves {name, path, url, room, size, image},
// rejects with the hub's words.
async function attUpload(room, file, name) {
  if (!room) throw new Error('there is no chat to attach it to yet');
  if (file.size > ATT_MAX) throw new Error(`the file is ${(file.size / 1048576).toFixed(1)} MB; a file can be at most 20 MB`);
  const r = await fetch('/api/room/attachment?room=' + encodeURIComponent(room) + (name ? '&name=' + encodeURIComponent(name) : ''),
    { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file });
  let d = null;
  try { d = await r.json(); } catch (e) {}
  if (!r.ok || !d || !d.ok) throw new Error((d && (d.message || d.error)) || ('error ' + r.status));
  return { name: d.name, path: d.path, url: d.url || attUrl(room, d.name), room,
           size: d.size != null ? d.size : file.size, image: d.image != null ? !!d.image : ATT_IMAGE_EXT.test(d.name || '') };
}

// The line a stored file is named by in a message: "[image] " or "[file] ".
const attLinePrefix = name => ATT_IMAGE_EXT.test(String(name || '')) ? ATT_PREFIX : ATT_FILE_PREFIX;
const ATT_LINE = /^\[(image|file)\] (\S.*)$/;
// A text's "[image] <path>" and "[file] <path>" lines at its end: { words, paths }.
function attSplit(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\s+$/, '').split('\n');
  let n = lines.length;
  while (n && ATT_LINE.test(lines[n - 1])) n--;
  if (n === lines.length) return { words: String(text || ''), paths: [] };
  return { words: lines.slice(0, n).join('\n').replace(/\s+$/, ''), paths: lines.slice(n).map(l => l.match(ATT_LINE)[2].trim()) };
}
const attBase = p => String(p || '').split(/[\\/]/).pop();
// A path where this chat keeps its files (a task folder's attachments, or
// <state>/attachments/<room id>): only those can be shown from the hub.
function attOwn(room, p) {
  const parts = String(p || '').split(/[\\/]/);
  const dir = parts[parts.length - 2] || '', up = parts[parts.length - 3] || '';
  return !!room && (dir === 'attachments' || (dir === room && up === 'attachments'));
}

// The sizes of stored files a balloon shows, asked of the hub once each
// (GET ...&info=1) and written into every chip that waits for one.
const ATT_SIZES = new Map();
function attFillSizes(root) {
  if (!root || !root.querySelectorAll || typeof fetch !== 'function') return;
  for (const el of root.querySelectorAll('[data-att-size]')) {
    const u = el.getAttribute('data-att-size');
    if (!u || el.textContent) continue;
    let p = ATT_SIZES.get(u);
    if (!p) {
      p = fetch(u + '&info=1').then(r => r.ok ? r.json() : null).then(d => (d && d.size != null ? attSize(d.size) : ''), () => '');
      ATT_SIZES.set(u, p);
    }
    p.then(s => { if (s && !el.textContent) el.textContent = s; });
  }
}

// A balloon's files: images as thumbnails that open full size in a new tab;
// any other file as a chip that opens in the file view (viewHref(path), when
// the page gives one and the file view can show it) or else downloads.
function attThumbsHtml(room, paths, esc, viewHref) {
  if (!paths || !paths.length || !room) return '';
  const imgs = paths.filter(p => ATT_IMAGE_EXT.test(attBase(p))), files = paths.filter(p => !ATT_IMAGE_EXT.test(attBase(p)));
  return '<div class="att-thumbs">' + imgs.map(p => {
    const name = attBase(p), u = attUrl(room, name);
    return `<a class="att-thumb" href="${esc(u)}" target="_blank" rel="noopener" title="${esc(name)}"><img src="${esc(u)}" alt="${esc(name)}" loading="lazy"></a>`;
  }).join('') + files.map(p => {
    const name = attBase(p), u = attUrl(room, name);
    const view = viewHref && ATT_VIEW_EXT.test(name);
    const href = view ? viewHref(p) : u + '&download=1';
    return `<a class="att-file" href="${esc(href)}"${view ? ' target="_blank" rel="noopener"' : ` download="${esc(name)}"`} title="${esc((view ? 'Open ' : 'Download ') + name)}">`
      + attIcon(name, esc) + `<span class="att-name">${esc(name)}</span><span class="att-size" data-att-size="${esc(u)}"></span></a>`;
  }).join('') + '</div>';
}

// The files of one box being written: a chip each, uploading, stored or
// refused. room() says where they go; changed() is called whenever the list
// or a chip's state changes (to redraw and to turn Send on or off).
function attBox(room, changed) {
  const list = [];
  let seq = 0;
  const box = {
    list,
    add(files, pasted) {
      for (const f of files || []) {
        const image = attIsImage(f);
        const it = { id: 'att' + (++seq), label: attGivenName(f, pasted) || (image ? 'screenshot' : (f.name || 'file')), state: 'up', err: '', name: '', url: '', room: '',
                     size: f.size, image };
        it.preview = '';
        if (image) { try { it.preview = URL.createObjectURL(f); } catch (e) {} }
        list.push(it);
        attUpload(room(), f, attGivenName(f, pasted)).then(res => {
          Object.assign(it, res, { state: 'ok', label: res.name });
        }, e => {
          it.state = 'err'; it.err = (e && e.message) || 'not uploaded';
        }).then(() => { if (list.includes(it)) changed(box); });
      }
      if (files && files.length) changed(box);
    },
    // Files already stored ({room, name}), as a comment being edited has them.
    keep(images) {
      for (const x of images || []) {
        if (!x || !x.room || !x.name) continue;
        list.push({ id: 'att' + (++seq), label: x.name, state: 'ok', err: '', name: x.name, room: x.room, url: attUrl(x.room, x.name), preview: '',
                    image: ATT_IMAGE_EXT.test(x.name) });
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
    // What a message carries: {room, name} of each stored file.
    stored: () => list.filter(x => x.state === 'ok').map(x => ({ room: x.room, name: x.name })),
    html(esc) {
      if (!list.length) return '';
      return list.map(x => {
        const what = x.image ? 'image' : 'file';
        const shown = x.state === 'up' ? 'Uploading…' : x.state === 'err' ? 'Not uploaded' : x.label;
        const size = !x.image && x.state !== 'err' && x.size != null ? attSize(x.size) : '';
        return `<span class="att-chip${x.state === 'err' ? ' err' : ''}${x.image ? '' : ' file'}" data-att="${esc(x.id)}" title="${esc(x.state === 'err' ? 'Not uploaded: ' + x.err : x.state === 'up' ? 'Uploading ' + x.label + '…' : x.label + (size ? ', ' + size : ''))}">`
          + (x.image ? (x.state !== 'err' && (x.url || x.preview) ? `<img src="${esc(x.url || x.preview)}" alt="">` : '') : attIcon(x.label, esc))
          + `<span class="att-name">${esc(x.image || x.state === 'err' ? shown : x.label)}</span>`
          + (size ? `<span class="att-size">${esc(size)}</span>` : '')
          + `<button type="button" class="att-x" data-att-x="${esc(x.id)}" title="Remove this ${what}" aria-label="Remove ${esc(x.label)}">×</button></span>`;
      }).join('');
    },
  };
  return box;
}

// Paste and drop on a box: files go to it (and nothing else happens); text
// pastes as ever. `zone` takes drops, `field` pastes; either may be the same.
function attWire(field, zone, box) {
  if (field) field.addEventListener('paste', e => {
    const files = attFiles(e.clipboardData);
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
      const files = attFiles(e.dataTransfer);
      if (!files.length) return;
      e.preventDefault();
      e.stopPropagation();
      box.add(files, false);
    });
  }
}

// A picker for a box: opens the system's file chooser (several files, any
// kind) and adds what is chosen. One hidden input per page is enough.
function attPick(box) {
  if (typeof document === 'undefined' || !document.createElement) return;
  let input = document.getElementById('att-pick-any');
  if (!input) {
    input = document.createElement('input');
    input.type = 'file'; input.multiple = true; input.id = 'att-pick-any'; input.hidden = true;
    input.setAttribute('aria-hidden', 'true'); input.tabIndex = -1;
    document.body.appendChild(input);
  }
  input.onchange = () => {
    const files = Array.from(input.files || []);
    input.value = '';
    if (files.length && input._box) input._box.add(files, false);
  };
  input._box = box;
  input.click();
}
// ---- Files pasted, dropped or picked into a chat box: end -------------------
