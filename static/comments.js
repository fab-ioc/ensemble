// ---- Review comments kept until they are delivered: begin -------------------
// session.html (comments on passages of a chat) and index.html (comments on
// lines of a diff) keep their review comments with this one store, so both
// behave alike: a reload, a hub restart or the page being made again never
// loses a comment, and only what the hub confirmed is taken off.
//
// One stored entry per comment, under prefix + cid. The same list can be open
// twice (a chat in the PO's drawer and in its own window, a task in two tabs):
// writing one comment touches only its own entry, so neither copy can
// overwrite the other's. What this browser will not store (storage full or
// blocked) stays in the page and is tried again on each sync; so is an entry
// that could not be deleted. `valid` says whether a stored object is one of
// this list's comments.
function cmtStore(prefix, valid) {
  const list = [], unstored = new Map(), gone = new Set();
  let lastAt = 0;
  // The stored comments, or null when storage cannot be read.
  function stored() {
    try {
      const out = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith(prefix)) continue;
        try {
          const c = JSON.parse(localStorage.getItem(k));
          if (c && typeof c === 'object' && c.cid && valid(c)) out.push(c);
        } catch (e) {}
      }
      return out;
    } catch (e) { return null; }
  }
  return {
    list, unstored,
    id: () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
    // A time no earlier than the last one given, so comments keep their order.
    now: () => (lastAt = Math.max(Date.now(), lastAt + 1)),
    // The list again from what is stored plus what only this page holds. When
    // storage cannot be read at all, the page's own list stands.
    sync() {
      for (const [cid, c] of unstored) {
        try { localStorage.setItem(prefix + cid, JSON.stringify(c)); unstored.delete(cid); } catch (e) {}
      }
      for (const cid of gone) {
        try { localStorage.removeItem(prefix + cid); gone.delete(cid); } catch (e) {}
      }
      const s = stored();
      if (!s) return;
      const next = s.filter(c => !gone.has(c.cid) && !unstored.has(c.cid)).concat([...unstored.values()]);
      next.sort((a, b) => (a.at || 0) - (b.at || 0) || (a.cid < b.cid ? -1 : a.cid > b.cid ? 1 : 0));
      list.splice(0, list.length, ...next);
    },
    // Add a comment, or replace the one with its cid.
    put(c) {
      const i = list.findIndex(z => z.cid === c.cid);
      if (i >= 0) list[i] = c; else list.push(c);
      gone.delete(c.cid);
      try { localStorage.setItem(prefix + c.cid, JSON.stringify(c)); unstored.delete(c.cid); } catch (e) { unstored.set(c.cid, c); }
    },
    remove(cids) {
      for (const cid of cids) {
        const i = list.findIndex(z => z.cid === cid); if (i >= 0) list.splice(i, 1);
        unstored.delete(cid);
        try { localStorage.removeItem(prefix + cid); } catch (e) { gone.add(cid); }
      }
    },
    // Whether a storage event is about this list.
    hears: key => key === null || String(key).startsWith(prefix),
  };
}
// ---- Review comments kept until they are delivered: end ---------------------
