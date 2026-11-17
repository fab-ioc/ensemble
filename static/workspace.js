// Each project keeps its own workspace (#386, the CEO's P374, P376): the
// task chats and file panels open on a project's screen are that project's.
// Opening another project (or an Unassigned task, a context of its own) puts
// the open ones away, remembered, and brings back the ones that context had,
// each in its kept place (the page's dock keeps the places). A pinned one
// stays open in every context. At most PdWorkspace.CAP of a context's tabs
// stay open: one more closes the one least recently shown that is not pinned.
//
// This file is the state alone, with no page and no dock, so it is tested in
// Node (tests/test_project_workspace.py). The page (index.html, pdWs*) says
// what is open and whose it is, and does what this returns.
//
// State, kept in localStorage under KEY:
//   { v: 1, cur: <context key>, pins: [<panel id>],
//     here: [<panel id>],                          // opened on cur's screen though another's
//     seen: { <panel id>: <ms> },                  // last shown, for CAP
//     ctxs: { <context key>: { at: <ms>, items: [<item>] } } }
// An item is what brings a panel back: { id, kind: 'chat', room } or
// { id, kind: 'file', path, ctx } (ctx: the Workspace's, wsCtxKey's input),
// with out: true when it was in a window of its own, front: true when it was
// the front tab of its stack (one brought back behind stays behind).
// A context key is a project's id, or 'task:<sid>' for an Unassigned task.
(function (root) {
  const KEY = 'cd-pd-ctx';
  const CAP = 8;            // a context's tabs open at once (P376)
  const MAX_CTXS = 60;      // contexts remembered (the least recently left go)
  const empty = () => ({ v: 1, cur: '', pins: [], here: [], seen: {}, ctxs: {} });
  const isItem = x => !!x && typeof x === 'object' && typeof x.id === 'string' && !!x.id
    && ((x.kind === 'chat' && typeof x.room === 'string' && !!x.room)
      || (x.kind === 'file' && typeof x.path === 'string' && !!x.path && !!x.ctx && typeof x.ctx === 'object'));
  const clean = x => {
    const o = x.kind === 'chat' ? { id: x.id, kind: 'chat', room: x.room } : { id: x.id, kind: 'file', path: x.path, ctx: x.ctx };
    if (x.out) o.out = true;
    if (x.front) o.front = true;
    return o;
  };
  // The stored state, made whole: anything it cannot use is left out.
  function load(raw) {
    let s = raw;
    if (typeof raw === 'string') { try { s = JSON.parse(raw); } catch (e) { s = null; } }
    const out = empty();
    if (!s || typeof s !== 'object' || s.v !== 1) return out;
    out.cur = typeof s.cur === 'string' ? s.cur : '';
    out.pins = Array.isArray(s.pins) ? [...new Set(s.pins.filter(p => typeof p === 'string' && p))] : [];
    out.here = Array.isArray(s.here) ? [...new Set(s.here.filter(p => typeof p === 'string' && p))] : [];
    if (s.seen && typeof s.seen === 'object') for (const [k, t] of Object.entries(s.seen)) if (Number.isFinite(t)) out.seen[k] = t;
    if (s.ctxs && typeof s.ctxs === 'object') for (const [k, c] of Object.entries(s.ctxs)) {
      if (!k || !c || typeof c !== 'object') continue;
      const items = Array.isArray(c.items) ? c.items.filter(isItem).map(clean) : [];
      out.ctxs[k] = { at: Number.isFinite(c.at) ? c.at : 0, items: items.filter((x, i) => items.findIndex(y => y.id === x.id) === i) };
    }
    return out;
  }
  const isPinned = (s, id) => s.pins.includes(id);
  function pin(s, id, on) {
    s.pins = s.pins.filter(p => p !== id);
    if (on) s.pins.push(id);
    return s;
  }
  function touch(s, id, now) { s.seen[id] = now; return s; }
  // A panel the person opened on the current screen: it stays there, even
  // when it is another context's (a file link to another project's file), until
  // the screen goes to another context; then it goes away with its own.
  function adopt(s, id) { if (!s.here.includes(id)) s.here.push(id); return s; }
  // The open panels, as the page lists them: [{ item, owner, shown }], owner
  // the context the panel belongs to (null: not known yet, left alone).
  // Returns { away: [ids to take out of the dock], back: [items to add] } for
  // the screen of context `here`, and records in `s` what went away.
  // Idempotent: settled again with nothing changed, it returns nothing to do.
  // `here` empty (a screen of no context: Needs you, the Unassigned list)
  // changes nothing. On the same context's screen, a panel opened there
  // (adopt) stays.
  function settle(s, here, open, now) {
    const res = { away: [], back: [] };
    if (!here) return res;
    const openIds = new Set(open.map(o => o.item.id));
    const kept = here === s.cur ? new Set(s.here) : new Set();
    if (here !== s.cur) s.here = [];
    for (const o of open) {
      if (!o.owner || o.owner === here || isPinned(s, o.item.id) || kept.has(o.item.id) || !isItem(o.item)) continue;
      const c = s.ctxs[o.owner] || (s.ctxs[o.owner] = { at: 0, items: [] });
      c.items = c.items.filter(x => x.id !== o.item.id);
      c.items.push(clean(o.item));
      c.at = Math.max(c.at, now);
      res.away.push(o.item.id);
    }
    const c = s.ctxs[here];
    if (c) {
      for (const x of c.items) if (!openIds.has(x.id)) res.back.push(x);
      c.items = [];
      c.at = now;
    } else s.ctxs[here] = { at: now, items: [] };
    s.cur = here;
    prune(s, open.map(o => o.item.id));
    return res;
  }
  // Which open panel a new one closes (P376): with `adding` one more of
  // context `here`'s, more than CAP would be open, so the one of `here`'s
  // least recently shown that is not pinned goes (never `adding` itself, nor
  // `keep`'s). Pinned ones count but stay. Only tabs count: a panel in a
  // window of its own (item.out) is no tab, and is never closed this way
  // (the person put it out to keep it in sight), nor is a floating one
  // (item.float). `fixed`: the tabs always there that count but never go (the
  // PO's tab, review 2). Null: none has to go.
  function overflow(s, here, open, adding, keep, fixed) {
    const ownHere = o => o.owner === here || (s.cur === here && s.here.includes(o.item.id));
    const mine = open.filter(o => !o.item.out && !o.item.float && (ownHere(o) || isPinned(s, o.item.id)));
    const n = (fixed || 0) + mine.length + (adding && !mine.some(o => o.item.id === adding) ? 1 : 0);
    if (n <= CAP) return null;
    const keepSet = new Set([adding, ...(keep || [])]);
    const can = mine.filter(o => ownHere(o) && !isPinned(s, o.item.id) && !keepSet.has(o.item.id));
    if (!can.length) return null;
    can.sort((a, b) => (s.seen[a.item.id] || 0) - (s.seen[b.item.id] || 0));
    return can[0].item.id;
  }
  // A put-away item that can no longer come back (its task or file is gone).
  function drop(s, id) {
    for (const c of Object.values(s.ctxs)) c.items = c.items.filter(x => x.id !== id);
    s.pins = s.pins.filter(p => p !== id);
    s.here = s.here.filter(p => p !== id);
    delete s.seen[id];
    return s;
  }
  // Bounded: the least recently left contexts go past MAX_CTXS (their items
  // with them), an empty one other than the current goes, and `seen` keeps
  // only the panels open or remembered.
  function prune(s, openIds) {
    for (const [k, c] of Object.entries(s.ctxs)) if (!c.items.length && k !== s.cur) delete s.ctxs[k];
    const keys = Object.keys(s.ctxs).filter(k => k !== s.cur).sort((a, b) => s.ctxs[b].at - s.ctxs[a].at);
    keys.slice(MAX_CTXS - 1).forEach(k => { delete s.ctxs[k]; });
    const live = new Set(openIds || []);
    for (const c of Object.values(s.ctxs)) c.items.forEach(x => live.add(x.id));
    s.pins.forEach(p => live.add(p));
    if (openIds) s.here = s.here.filter(p => openIds.includes(p));
    for (const k of Object.keys(s.seen)) if (!live.has(k)) delete s.seen[k];
    return s;
  }
  // The items a context has put away (the ⋯'s list of what to reopen).
  const putAway = (s, key) => (s.ctxs[key] ? s.ctxs[key].items.slice() : []);
  const api = { KEY, CAP, MAX_CTXS, empty, load, pin, isPinned, touch, adopt, settle, overflow, drop, prune, putAway };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PdWorkspace = api;
})(typeof window !== 'undefined' ? window : globalThis);
